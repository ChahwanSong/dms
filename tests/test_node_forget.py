"""노드 기록 정리(2026-11-25, dms.node_forget -- OA 사이트 「사라지지 않는 stale 노드」).

- DELETE /api/admin/nodes/{name}: 세션 관리자만, stale 행만, in_use(빌드 노드·k8s 호출 노드·진행 중 빌드·비종단 잡 후보)면
  409, 한 트랜잭션으로 agent_nodes + node_exclusions + agent_reports + 감사 1행.
- 자동 정리(컨트롤러 retention 루프 끝): 꺼짐이 기본, k8s 에 그 Node 가 없을 때만 -- 조회 실패·빈 목록은 「모름」이라 안 지운다.
- 같은 장비의 새 이름 힌트(renamed_to): machine_key 가 같은 fresh 행이 정확히 1개일 때만.
- 설정 DMS_AGENT_NODE_FORGET_SECONDS: 0 또는 ≥ STALE."""
import dataclasses
from pathlib import Path

import pytest

from dms.config import Settings, SettingsError
from dms.controller import build_loops
from dms.db import dump_json, iso_plus, load_json, utc_now_iso
from dms.node_forget import (AUTO_FORGET_ACTOR, BLOCKING_REQUESTS_SHOWN, FORGET_BATCH_LIMIT, MACHINE_KEY_RE,
                             REF_KINDS, forget_absent_nodes_once, machine_key_of, node_references, rename_hints)
from dms.repositories import Repositories
from dms.repositories.agents import NodeForgetRefused

ADMIN = {"Authorization": "Bearer tok-shared"}
OLD = "2026-01-01T00:00:00Z"   # 어떤 stale·forget 문턱보다도 오래됨
KEY_A = "0123456789abcdef"
KEY_B = "fedcba9876543210"
WEEK = 604800


@pytest.fixture
def session_admin(client):
    """노드 기록 삭제는 세션 관리자만(admin_session_required). Bearer 헤더가 세션 쿠키보다 우선하므로 변경 호출엔 헤더를
    싣지 않는다(test_api_control 의 session_admin 과 같은 모양)."""
    client.app.state.repos.accounts.create("opadm", "p", "admin", actor="t")
    assert client.post("/api/auth/login", json={"username": "opadm", "password": "p"}).status_code == 200
    return "opadm"


def _stale(repos, name, n=1, *, at=OLD, **report):
    for i in range(n):
        repos.agents.ingest(name, {"node_name": name, "seq": i, **report}, reported_at=at)


def _count(db, table, name):
    return db.query_one(f"SELECT COUNT(*) AS c FROM {table} WHERE node_name = :n", {"n": name})["c"]


def _audits(db):
    return db.query("SELECT * FROM audit_log WHERE mutation_class = 'agent_node' ORDER BY id")


def _events(db, event_type):
    return [{**r, "payload": load_json(r["payload"])}
            for r in db.query("SELECT * FROM events WHERE event_type = :t ORDER BY id", {"t": event_type})]


def _active_job(db, job_id, worker_pool, *, state="Pending", request_id="r"):
    raw = worker_pool if isinstance(worker_pool, str) else dump_json(worker_pool)
    db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, options, priority, state, worker_pool,
                      created_at, updated_at)
                  VALUES (:j, :r, 'sync', '{}', 'mid', :s, :wp, :t, :t)""",
               {"j": job_id, "r": request_id, "s": state, "wp": raw, "t": OLD})


# --- 삭제 API -------------------------------------------------------------------------------------------------------

def test_forget_deletes_node_history_exclusion_and_audits(client, session_admin, db):
    repos = client.app.state.repos
    _stale(repos, "old", 3)
    _stale(repos, "keep", 2)
    repos.node_exclusions.exclude("old", reason="교체", actor="t")
    repos.node_exclusions.exclude("keep", reason=None, actor="t")

    r = client.delete("/api/admin/nodes/old")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": "old", "reported_at": OLD, "reports_deleted": 3, "exclusion_deleted": True}
    for table in ("agent_nodes", "agent_reports", "node_exclusions"):
        assert _count(db, table, "old") == 0, table
    # 다른 노드는 그대로
    assert _count(db, "agent_nodes", "keep") == 1
    assert _count(db, "agent_reports", "keep") == 2
    assert _count(db, "node_exclusions", "keep") == 1

    [audit] = _audits(db)
    assert (audit["operation"], audit["target_key"], audit["actor"]) == ("delete", "old", "opadm")
    before = load_json(audit["before_state"])
    assert before["reported_at"] == OLD and before["reports_deleted"] == 3
    assert before["exclusion"]["node_name"] == "old" and before["exclusion"]["reason"] == "교체"
    assert audit["after_state"] is None

    # 지운 뒤: 목록에서 빠지고 상세는 404(노드 자체가 없다)
    names = [n["node_name"] for n in client.get("/api/admin/nodes", headers=ADMIN).json()]
    assert names == ["keep"]
    assert client.get("/api/admin/nodes/old/reports", headers=ADMIN).status_code == 404


def test_forget_without_exclusion_or_history(client, session_admin, db):
    repos = client.app.state.repos
    _stale(repos, "old")
    repos.agents.prune_reports("9999-01-01T00:00:00Z")      # retention 이 이력만 지운 상태
    r = client.delete("/api/admin/nodes/old")
    assert r.status_code == 200
    assert r.json() == {"deleted": "old", "reported_at": OLD, "reports_deleted": 0, "exclusion_deleted": False}
    assert load_json(_audits(db)[0]["before_state"])["exclusion"] is None


def test_same_name_reporting_again_comes_back_as_a_new_row(client, session_admin):
    repos = client.app.state.repos
    _stale(repos, "old", 2)
    assert client.delete("/api/admin/nodes/old").status_code == 200
    repos.agents.ingest("old", {"node_name": "old"})
    [node] = client.get("/api/admin/nodes", headers=ADMIN).json()
    assert node["node_name"] == "old" and node["fresh"] is True
    assert len(client.get("/api/admin/nodes/old/reports", headers=ADMIN).json()) == 1


def test_forget_refuses_a_fresh_node(client, session_admin, db):
    repos = client.app.state.repos
    repos.agents.ingest("live", {"node_name": "live"})      # 방금 보고 -- fresh
    r = client.delete("/api/admin/nodes/live")
    assert r.status_code == 409 and r.json()["detail"] == "node_fresh"
    assert _count(db, "agent_nodes", "live") == 1 and _count(db, "agent_reports", "live") == 1
    assert _audits(db) == []


def test_forget_unknown_node_is_404(client, session_admin, db):
    r = client.delete("/api/admin/nodes/ghost")
    assert r.status_code == 404 and r.json()["detail"] == "node_not_found"
    assert _audits(db) == []


def test_forget_requires_a_session_admin(client, db):
    repos = client.app.state.repos
    _stale(repos, "old")
    assert client.delete("/api/admin/nodes/old").status_code == 401
    # 공유 토큰(모든 노드 에이전트가 가진 role admin 자격)은 403
    r = client.delete("/api/admin/nodes/old", headers=ADMIN)
    assert r.status_code == 403 and r.json()["detail"] == "admin_session_required"
    # 일반 사용자 세션은 라우터 수준 require_admin 에서 403
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    r = client.delete("/api/admin/nodes/old")
    assert r.status_code == 403 and r.json()["detail"] == "admin_required"
    assert _count(db, "agent_nodes", "old") == 1 and _audits(db) == []


def test_forget_is_allowed_during_maintenance(client, session_admin, db):
    db.execute("UPDATE control_state SET maintenance = 1 WHERE id = 1")
    _stale(client.app.state.repos, "old")
    assert client.delete("/api/admin/nodes/old").status_code == 200


# --- in_use --------------------------------------------------------------------------------------------------------

def _set_build_node(db, name):
    db.execute("UPDATE control_state SET build_node_name = :n WHERE id = 1", {"n": name})


@pytest.mark.parametrize("ref", REF_KINDS)
def test_forget_refuses_a_referenced_node(client, session_admin, db, ref):
    repos = client.app.state.repos
    _stale(repos, "old", 2)
    if ref == "build_node":
        _set_build_node(db, "old")
    elif ref == "k8s_primary":
        repos.k8s.update_settings(primary_node="old", secondary_node=None, interval_seconds=3600, actor="t")
    elif ref == "k8s_secondary":
        repos.k8s.update_settings(primary_node="other", secondary_node="old", interval_seconds=3600, actor="t")
    elif ref == "active_build":
        repos.builds.create(source_path="/src", images=["dms"], node_name="old", actor="t")
    elif ref == "active_jobs":
        _active_job(db, "j1", {"candidates": {"source": ["x"], "destination": ["old"]}}, state="ConfirmPending")
    r = client.delete("/api/admin/nodes/old")
    # active_jobs 면 막는 요청 id 가 따라온다(포탈 창의 요청 상세 링크)
    expected = f"node_in_use:{ref}" + (";requests=r" if ref == "active_jobs" else "")
    assert r.status_code == 409 and r.json()["detail"] == expected
    assert _count(db, "agent_nodes", "old") == 1 and _count(db, "agent_reports", "old") == 2
    assert _audits(db) == []


def test_in_use_refs_come_in_fixed_order(client, session_admin, db):
    repos = client.app.state.repos
    _stale(repos, "old")
    _active_job(db, "j1", {"candidates": {"primary": ["old"]}})
    _set_build_node(db, "old")
    repos.k8s.update_settings(primary_node=None, secondary_node="old", interval_seconds=3600, actor="t")
    r = client.delete("/api/admin/nodes/old")
    assert r.json()["detail"] == "node_in_use:build_node,k8s_secondary,active_jobs;requests=r"


def test_in_use_names_the_blocking_requests_capped_and_deduplicated(client, session_admin, db):
    """작업 목록엔 노드 필터가 없다 -- 거절이 막는 요청을 짚어 준다(잡 job_id 순, 같은 요청은 한 번, 최대
    BLOCKING_REQUESTS_SHOWN 개). 빌드 노드만 막는 거절엔 requests 가 없다."""
    repos = client.app.state.repos
    _stale(repos, "old")
    _active_job(db, "j0", {"candidates": {"primary": ["old"]}}, request_id="req-a")
    _active_job(db, "j1", {"candidates": {"primary": ["old"]}}, request_id="req-a", state="PreviewRunning")
    for i in range(2, 2 + BLOCKING_REQUESTS_SHOWN + 1):
        _active_job(db, f"j{i}", {"candidates": {"primary": ["old"]}}, request_id=f"req-{i}", state="Executing")
    detail = client.delete("/api/admin/nodes/old").json()["detail"]
    head, _, tail = detail.partition(";requests=")
    assert head == "node_in_use:active_jobs"
    assert tail.split(",") == ["req-a"] + [f"req-{i}" for i in range(2, 1 + BLOCKING_REQUESTS_SHOWN)]
    assert _count(db, "agent_nodes", "old") == 1

    _stale(repos, "other")
    _set_build_node(db, "other")
    assert client.delete("/api/admin/nodes/other").json()["detail"] == "node_in_use:build_node"


def test_in_use_omits_request_ids_that_would_break_the_detail(client, session_admin, db):
    """DB 는 신뢰 경계 -- 합성 상세를 깨는 모양의 요청 id 는 싣지 않는다(판정은 그대로 active_jobs)."""
    _stale(client.app.state.repos, "old")
    _active_job(db, "j1", {"candidates": {"primary": ["old"]}}, request_id="a;requests=x,y")
    _active_job(db, "j2", {"candidates": {"primary": ["old"]}}, request_id="ok-1")
    assert client.delete("/api/admin/nodes/old").json()["detail"] == "node_in_use:active_jobs;requests=ok-1"


def test_active_jobs_on_node_carries_request_ids(db):
    repos = Repositories(db)
    _active_job(db, "j1", {"candidates": {"primary": ["old"]}}, request_id="req-1")
    assert repos.data_jobs.active_jobs_on_node("old") == [{"job_id": "j1", "request_id": "req-1"}]


def test_finished_build_and_terminal_job_do_not_block(client, session_admin, db):
    repos = client.app.state.repos
    _stale(repos, "old")
    build_id = repos.builds.create(source_path="/src", images=["dms"], node_name="old", actor="t")
    repos.builds.finish(build_id, state="Succeeded")
    _active_job(db, "j1", {"candidates": {"primary": ["old"]}}, state="Succeeded")
    assert node_references(repos, "old") == ()
    assert client.delete("/api/admin/nodes/old").status_code == 200


def test_active_job_ids_on_node_reads_only_candidates(db):
    repos = Repositories(db)
    _active_job(db, "j-rej", {"candidates": {"primary": ["n2"]}, "rejections": {"old": "tool_missing"},
                              "node_cpu_caps": {"old": 4}, "identity": {"user": "old"}})
    _active_job(db, "j-cand", {"candidates": {"primary": ["n2", "old"]}})
    _active_job(db, "j-done", {"candidates": {"primary": ["old"]}}, state="Cancelled")
    assert repos.data_jobs.active_job_ids_on_node("old") == ["j-cand"]


def test_active_job_ids_on_node_is_fail_closed_on_broken_worker_pools(db):
    repos = Repositories(db)
    _active_job(db, "j-broken", '{"candidates": {"primary": ["old"')           # 잘린 JSON
    _active_job(db, "j-list", '["old"]')                                       # dict 가 아님
    _active_job(db, "j-other", '{"candidates": {"primary": ["older"]}}')      # LIKE 는 따옴표째 -- 안 걸린다
    assert repos.data_jobs.active_job_ids_on_node("old") == ["j-broken", "j-list"]


def test_active_job_ids_on_node_escapes_like_wildcards(db):
    repos = Repositories(db)
    _active_job(db, "j1", {"candidates": {"primary": ["a-b", "axc", "abc"]}})
    # '_'·'%' 가 와일드카드로 새면 위 후보에 걸린다 -- 이스케이프 + 파싱이 둘 다 거른다
    assert repos.data_jobs.active_job_ids_on_node("a_b") == []
    assert repos.data_jobs.active_job_ids_on_node("a%") == []
    _active_job(db, "j2", {"candidates": {"primary": ["a_b"]}})
    assert repos.data_jobs.active_job_ids_on_node("a_b") == ["j2"]


def test_active_job_ids_on_node_respects_limit(db):
    repos = Repositories(db)
    for i in range(5):
        _active_job(db, f"j{i}", {"candidates": {"primary": ["old"]}})
    assert repos.data_jobs.active_job_ids_on_node("old", limit=2) == ["j0", "j1"]


# --- 원자 가드 -------------------------------------------------------------------------------------------------------

def test_conditional_delete_loses_to_a_report_after_the_checks(db):
    """판정 뒤·삭제 전에 그 노드가 다시 보고한 것과 같은 상태 -- 조건부 DELETE 가 0행이라 node_fresh 로 거절하고, 아무것도
    지우지 않는다(트랜잭션 롤백 -- 콜백 안의 갱신도 되돌아간다)."""
    repos = Repositories(db)
    _stale(repos, "old", 2)
    stale_before = iso_plus(utc_now_iso(), -300)

    def race(name):
        db.execute("UPDATE agent_nodes SET reported_at = :t WHERE node_name = :n", {"t": utc_now_iso(), "n": name})
        return ()

    with pytest.raises(NodeForgetRefused) as e:
        repos.agents.forget_node("old", stale_before_iso=stale_before, actor="t", operation="delete", references=race)
    assert e.value.code == "node_fresh"
    assert db.query_one("SELECT reported_at FROM agent_nodes WHERE node_name = 'old'")["reported_at"] == OLD
    assert _count(db, "agent_reports", "old") == 2 and _audits(db) == []


def test_conditional_delete_reports_not_found_when_the_row_vanished(db):
    repos = Repositories(db)
    _stale(repos, "old")

    def vanish(name):
        db.execute("DELETE FROM agent_nodes WHERE node_name = :n", {"n": name})
        return ()

    with pytest.raises(NodeForgetRefused) as e:
        repos.agents.forget_node("old", stale_before_iso=iso_plus(utc_now_iso(), -300), actor="t",
                                 operation="delete", references=vanish)
    assert e.value.code == "node_not_found"
    assert _count(db, "agent_nodes", "old") == 1           # 롤백


# --- 새 이름 힌트 ----------------------------------------------------------------------------------------------------

def test_list_nodes_hints_the_new_name_of_the_same_machine(client):
    repos = client.app.state.repos
    _stale(repos, "h-aii-dms26001", machine_key=KEY_A)
    repos.agents.ingest("h-aii-dms-26001", {"node_name": "h-aii-dms-26001", "machine_key": KEY_A})
    repos.agents.ingest("other", {"node_name": "other", "machine_key": KEY_B})
    nodes = {n["node_name"]: n for n in client.get("/api/admin/nodes", headers=ADMIN).json()}
    assert nodes["h-aii-dms26001"]["renamed_to"] == "h-aii-dms-26001"
    assert nodes["h-aii-dms-26001"]["renamed_to"] is None      # fresh 행은 힌트를 받지 않는다
    assert nodes["other"]["renamed_to"] is None


def _node(name, fresh, key="<absent>"):
    report = {"node_name": name}
    if key != "<absent>":
        report["machine_key"] = key
    return {"node_name": name, "fresh": fresh, "report": report}


def test_rename_hints_need_exactly_one_fresh_twin():
    assert rename_hints([_node("old", False, KEY_A), _node("new", True, KEY_A)]) == {"old": "new"}
    # 복제 VM 템플릿처럼 같은 키의 fresh 행이 둘이면 힌트 없음
    assert rename_hints([_node("old", False, KEY_A), _node("n1", True, KEY_A), _node("n2", True, KEY_A)]) == {}
    # 같은 키가 stale 끼리만 -- 힌트 없음
    assert rename_hints([_node("old", False, KEY_A), _node("older", False, KEY_A)]) == {}
    # 키 없음(옛 에이전트) -- 힌트 없음
    assert rename_hints([_node("old", False), _node("new", True)]) == {}
    assert rename_hints([_node("old", False, None), _node("new", True, None)]) == {}


@pytest.mark.parametrize("bad", ["0123456789ABCDEF", "0123456789abcdef" * 2, "0123456789abcde", 123,
                                 {"k": KEY_A}, "0123456789abcdef\n", None])
def test_malformed_machine_keys_give_no_hint(bad):
    assert machine_key_of({"machine_key": bad}) is None
    assert rename_hints([_node("old", False, bad), _node("new", True, bad)]) == {}


def test_machine_key_of_ignores_non_dict_reports():
    assert machine_key_of(None) is None and machine_key_of("x") is None and machine_key_of([KEY_A]) is None
    assert machine_key_of({"machine_key": KEY_A}) == KEY_A


def test_server_key_shape_matches_the_agent_probe():
    from dms.agent.probes import MACHINE_KEY_HEX
    assert MACHINE_KEY_RE.pattern == f"[0-9a-f]{{{MACHINE_KEY_HEX}}}"


# --- 자동 정리 -------------------------------------------------------------------------------------------------------

class Lister:
    def __init__(self, result=None, exc=None):
        self.result = result if result is not None else []
        self.exc = exc
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.exc is not None:
            raise self.exc
        return self.result


def _on(settings, seconds=WEEK):
    return dataclasses.replace(settings, agent_node_forget_seconds=seconds)


def test_auto_forget_is_off_by_default(db, settings):
    repos = Repositories(db)
    _stale(repos, "gone")
    lister = Lister([{"name": "alive"}])
    assert settings.agent_node_forget_seconds == 0
    assert forget_absent_nodes_once(repos, settings, lister) == {"enabled": False}
    assert lister.calls == 0 and _count(db, "agent_nodes", "gone") == 1


def test_auto_forget_deletes_nodes_absent_from_kubernetes(db, settings):
    repos = Repositories(db)
    _stale(repos, "gone", 2)
    _stale(repos, "still-there")
    repos.agents.ingest("alive", {"node_name": "alive"})
    lister = Lister([{"name": "alive", "ip": "10.0.0.1", "control_plane": False},
                     {"name": "still-there", "ip": None, "control_plane": False}])
    out = forget_absent_nodes_once(repos, _on(settings), lister)
    assert out["deleted"] == ["gone"] and out["k8s"] == "ok" and out["candidates"] == 2
    assert _count(db, "agent_nodes", "gone") == 0 and _count(db, "agent_reports", "gone") == 0
    assert _count(db, "agent_nodes", "still-there") == 1 and _count(db, "agent_nodes", "alive") == 1
    [audit] = _audits(db)
    assert (audit["operation"], audit["actor"], audit["target_key"]) == ("auto_forget", AUTO_FORGET_ACTOR, "gone")
    [event] = _events(db, "agent_node_forgotten")
    assert event["component"] == "retention" and event["payload"] == {"nodes": ["gone"]}


def test_auto_forget_keeps_everything_when_the_k8s_list_fails(db, settings):
    repos = Repositories(db)
    _stale(repos, "gone")
    out = forget_absent_nodes_once(repos, _on(settings), Lister(exc=RuntimeError("403 Forbidden")))
    assert out["k8s"] == "list_failed" and out["deleted"] == []
    assert _count(db, "agent_nodes", "gone") == 1
    [event] = _events(db, "agent_node_forget_skipped")
    assert event["severity"] == "warning"
    assert event["payload"] == {"reason": "k8s_list_failed", "candidates": 1}
    assert "403 Forbidden" in event["message"]


@pytest.mark.parametrize("listed", [[], [{"name": None}, {"name": ""}, {"ip": "10.0.0.1"}, "node", 7], None, {"x": 1}])
def test_auto_forget_treats_an_empty_or_nameless_list_as_unknown(db, settings, listed):
    # 스텁 백엔드의 node_lister 는 lambda: [] -- 「노드 없음」으로 읽으면 로컬·CI 의 오래된 행이 전부 지워진다.
    repos = Repositories(db)
    _stale(repos, "gone")
    out = forget_absent_nodes_once(repos, _on(settings), lambda: listed)
    assert out["k8s"] == "no_nodes_listed" and out["deleted"] == []
    assert _count(db, "agent_nodes", "gone") == 1
    assert _events(db, "agent_node_forget_skipped")[0]["payload"]["reason"] == "no_nodes_listed"


def test_auto_forget_without_a_lister_is_unknown(db, settings):
    repos = Repositories(db)
    _stale(repos, "gone")
    out = forget_absent_nodes_once(repos, _on(settings), None)
    assert out["k8s"] == "unknown" and _count(db, "agent_nodes", "gone") == 1


def test_auto_forget_only_considers_nodes_past_the_forget_window(db, settings):
    repos = Repositories(db)
    # stale(> 300초)이지만 7일은 안 됐다 -- 후보가 아니고, 후보가 없으면 k8s 를 부르지 않는다
    _stale(repos, "recent", at=iso_plus(utc_now_iso(), -3600))
    lister = Lister([{"name": "alive"}])
    out = forget_absent_nodes_once(repos, _on(settings), lister)
    assert out["candidates"] == 0 and lister.calls == 0
    assert _count(db, "agent_nodes", "recent") == 1


def test_auto_forget_with_no_candidates_does_not_call_kubernetes(db, settings):
    repos = Repositories(db)
    repos.agents.ingest("alive", {"node_name": "alive"})
    lister = Lister([{"name": "alive"}])
    assert forget_absent_nodes_once(repos, _on(settings), lister)["candidates"] == 0
    assert lister.calls == 0


def test_auto_forget_honours_the_in_use_guard(db, settings):
    repos = Repositories(db)
    _stale(repos, "gone")
    _set_build_node(db, "gone")
    out = forget_absent_nodes_once(repos, _on(settings), Lister([{"name": "alive"}]))
    assert out["deleted"] == [] and out["in_use"] == {"gone": ["build_node"]}
    assert _count(db, "agent_nodes", "gone") == 1 and _audits(db) == []
    [event] = _events(db, "agent_node_forget_in_use")
    assert event["payload"] == {"nodes": {"gone": ["build_node"]}}
    assert _events(db, "agent_node_forgotten") == []


def test_auto_forget_in_use_event_names_the_blocking_requests(db, settings):
    repos = Repositories(db)
    _stale(repos, "gone")
    _active_job(db, "j1", {"candidates": {"primary": ["gone"]}}, request_id="req-1")
    out = forget_absent_nodes_once(repos, _on(settings), Lister([{"name": "alive"}]))
    assert out["in_use"] == {"gone": ["active_jobs"]} and out["in_use_requests"] == {"gone": ["req-1"]}
    [event] = _events(db, "agent_node_forget_in_use")
    assert event["payload"] == {"nodes": {"gone": ["active_jobs"]}, "requests": {"gone": ["req-1"]}}


def test_auto_forget_cap_counts_deletions_not_in_use_refusals(db, settings):
    """이름순 앞자리의 in_use 노드가 틱 예산을 다 쓰면 뒤의 지울 수 있는 노드가 영원히 남는다 -- 상한은 지운 수에 건다."""
    repos = Repositories(db)
    blocked = [f"a-{i:02d}" for i in range(FORGET_BATCH_LIMIT + 1)]
    for name in blocked:
        _stale(repos, name)
    _active_job(db, "j1", {"candidates": {"primary": blocked}})
    _stale(repos, "z-free")
    out = forget_absent_nodes_once(repos, _on(settings), Lister([{"name": "alive"}]))
    assert out["deleted"] == ["z-free"]
    assert sorted(out["in_use"]) == blocked
    assert _count(db, "agent_nodes", "z-free") == 0 and _count(db, "agent_nodes", "a-00") == 1


def test_auto_forget_is_capped_per_tick(db, settings):
    repos = Repositories(db)
    for i in range(FORGET_BATCH_LIMIT + 3):
        _stale(repos, f"gone-{i:02d}")
    lister = Lister([{"name": "alive"}])
    first = forget_absent_nodes_once(repos, _on(settings), lister)
    assert len(first["deleted"]) == FORGET_BATCH_LIMIT
    second = forget_absent_nodes_once(repos, _on(settings), lister)
    assert len(second["deleted"]) == 3
    assert db.query_one("SELECT COUNT(*) AS c FROM agent_nodes")["c"] == 0


def test_retention_loop_runs_auto_forget_after_the_prunes(db, settings):
    repos = Repositories(db)
    _stale(repos, "gone")
    db.execute("""INSERT INTO events (component, severity, event_type, at)
                  VALUES ('planner', 'error', 'x', :at)""", {"at": "2000-01-01T00:00:00Z"})
    [loop] = [l for l in build_loops(_on(settings), repos, node_lister=Lister([{"name": "alive"}]))
              if l.name == "retention"]
    loop.fn()
    assert _count(db, "agent_nodes", "gone") == 0
    assert db.query("SELECT id FROM events WHERE event_type = 'x'") == []     # prune 도 그대로 돈다


def test_retention_loop_failure_in_k8s_does_not_stop_the_prunes(db, settings):
    repos = Repositories(db)
    _stale(repos, "gone")
    db.execute("""INSERT INTO events (component, severity, event_type, at)
                  VALUES ('planner', 'error', 'x', :at)""", {"at": "2000-01-01T00:00:00Z"})
    [loop] = [l for l in build_loops(_on(settings), repos, node_lister=Lister(exc=OSError("down")))
              if l.name == "retention"]
    loop.fn()
    assert _count(db, "agent_nodes", "gone") == 1
    assert db.query("SELECT id FROM events WHERE event_type = 'x'") == []


def test_controller_cli_wires_the_node_lister(tmp_path, monkeypatch, capsys):
    """배선 가드: cli 가 wiring.build_node_lister(settings) 를 build_loops 에 넘기지 않으면 자동 정리를 켜도 늘 「모름」이다
    (test_cli 의 retention=ok 만으로는 못 잡는다)."""
    from dms import wiring
    from dms.cli import main
    from dms.db import Database

    url = f"sqlite:///{tmp_path}/c.db"
    for key, value in (("DMS_DATABASE_URL", url), ("DMS_SHARED_TOKEN", "t"), ("DMS_ADMIN_TOKEN", "a"),
                       ("DMS_SESSION_SECRET", "s"), ("DMS_AGENT_NODE_FORGET_SECONDS", str(WEEK))):
        monkeypatch.setenv(key, value)
    assert main(["migrate"]) == 0
    _stale(Repositories(Database.connect(url)), "gone")
    lister = Lister([{"name": "alive"}])
    monkeypatch.setattr(wiring, "build_node_lister", lambda settings: lister)
    assert main(["controller", "--once"]) == 0
    assert "retention=ok" in capsys.readouterr().out
    assert lister.calls == 1
    assert Database.connect(url).query("SELECT node_name FROM agent_nodes") == []


# --- 설정 -----------------------------------------------------------------------------------------------------------

VALID = {"DMS_DATABASE_URL": "sqlite:///tmp/dms.db", "DMS_SHARED_TOKEN": "tok",
         "DMS_ADMIN_TOKEN": "adm", "DMS_SESSION_SECRET": "sess"}


def test_forget_seconds_default_is_off():
    assert Settings.from_env(VALID).agent_node_forget_seconds == 0
    assert Settings(database_url="x", shared_token="t", admin_token="a",
                    session_secret="s").agent_node_forget_seconds == 0


def test_forget_seconds_bounds():
    assert Settings.from_env({**VALID, "DMS_AGENT_NODE_FORGET_SECONDS": "604800"}).agent_node_forget_seconds == WEEK
    assert Settings.from_env({**VALID, "DMS_AGENT_NODE_FORGET_SECONDS": "300"}).agent_node_forget_seconds == 300
    assert Settings.from_env({**VALID, "DMS_AGENT_NODE_FORGET_SECONDS": "0"}).agent_node_forget_seconds == 0
    for bad, extra in (("-1", {}), ("299", {}), ("1", {}), ("599", {"DMS_AGENT_REPORT_STALE_SECONDS": "600"}),
                       ("x", {})):
        with pytest.raises(SettingsError) as e:
            Settings.from_env({**VALID, **extra, "DMS_AGENT_NODE_FORGET_SECONDS": bad})
        assert "DMS_AGENT_NODE_FORGET_SECONDS" in str(e.value)


def test_forget_seconds_is_in_the_site_neutral_config():
    text = (Path(__file__).resolve().parent.parent / "deploy/k8s/20-config.yaml").read_text()
    assert 'DMS_AGENT_NODE_FORGET_SECONDS: "0"' in text           # 코드 기본과 같은 값(사이트 중립)
    assert str(Settings.from_env(VALID).agent_node_forget_seconds) == "0"
