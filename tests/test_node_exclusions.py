"""노드 배치 제외 + 다시 포함 + k8s cordon 자동 반영(2026-10-02, repositories/node_exclusions.py 모듈 docstring).

층별로 고정한다: ① placement(후보 선정·사유·0대 거부 코드) ② planner(남은 노드로 계획·0대 즉시 거부) ③ stepper(제출
직전 재검사 -> 종단, 배치 항목 종료) ④ 컨펌(409 + 종단) ⑤ 관리자 API(제외·다시 포함·감사·검증) ⑥ 에이전트 k8s 프로브
⑦ nsync launcher 는 매니페스트 테스트(test_execution_manifests)가 고정한다."""
import json

import pytest

from dms.agent.probes import k8s_schedulability, probe_k8s_node
from dms.agent.runner import build_report
from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.identity import ResolvedIdentity, StubIdentityResolver
from dms.placement import PlacementError, select_tool_and_candidates
from dms.planner import Planner
from dms.repositories import Repositories
from dms.repositories.node_exclusions import blocked_nodes, k8s_unschedulable
from dms.stepper import JobStepper

NOW = "2026-08-02T10:00:00Z"
ALICE = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)
TOOLS = ("dscan", "dsync", "nsync", "drm")


def _report(node, storages=("s1",), *, schedulable=None, identities=("alice",)):
    r = {"node_name": node,
         "mounts": [{"storage_name": s, "mount_path": f"/mnt/{s}", "status": "Ready", "writable": True}
                    for s in storages],
         "tools": [{"name": t, "status": "Ready"} for t in TOOLS],
         "identities": [{"username": u, "status": "Ready"} for u in identities]}
    if schedulable is not None:
        r["k8s_node"] = {"schedulable": schedulable, "reason": None if schedulable else "cordoned"}
    return r


def _fresh(*reports):
    return [{"node_name": r["node_name"], "report": r} for r in reports]


def _scan(fresh, excluded=frozenset()):
    return select_tool_and_candidates("scan", fresh, storage_name="s1", owner="alice",
                                      privileged=False, excluded_nodes=excluded)


# ---- ① placement ----

def test_excluded_and_cordoned_nodes_leave_candidates_with_their_reason():
    out = _scan(_fresh(_report("n1"), _report("n2"), _report("n3", schedulable=False), _report("n4", schedulable=True)),
                excluded={"n2"})
    assert out["candidates"] == {"primary": ["n1", "n4"]}
    assert out["rejections"] == {"n2": "node_excluded", "n3": "node_unschedulable"}


def test_unknown_k8s_state_is_not_blocking():
    # null(조회 실패·클러스터 밖) = 모름 -- 막지 않는다(fail-open). 키 부재(옛 에이전트)도 같다.
    r = _report("n1")
    r["k8s_node"] = {"schedulable": None, "reason": "probe_failed:ApiException"}
    assert _scan(_fresh(r, _report("n2")))["candidates"] == {"primary": ["n1", "n2"]}
    assert k8s_unschedulable({"k8s_node": {"schedulable": None}}) is False
    assert k8s_unschedulable({}) is False and k8s_unschedulable(None) is False


def test_exclusion_is_checked_after_tool_and_before_identity():
    # 도구 뒤: 마운트가 없는 노드는 제외 여부와 무관하게 missing_target_mount(제외 사유는 "쓸 수 있었던 노드"만).
    # 신원 앞: 제외된 노드가 identity_not_ready_on_node 로 기록되면 planner 신원 유예가 쓰지도 않을 노드를 기다린다.
    out = _scan(_fresh(_report("n1"), _report("n2", storages=()), _report("n3", identities=())),
                excluded={"n2", "n3"})
    assert out["rejections"] == {"n2": "missing_target_mount", "n3": "node_excluded"}


@pytest.mark.parametrize("op, kwargs", [
    ("scan", {"storage_name": "s1"}),
    ("rm", {"storage_name": "s1"}),
    ("sync", {"source_storage": "s1", "destination_storage": "s1"}),
])
def test_all_blocked_raises_nodes_excluded(op, kwargs):
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates(op, _fresh(_report("n1"), _report("n2", schedulable=False)),
                                   owner="alice", privileged=False, excluded_nodes={"n1"}, **kwargs)
    assert e.value.reason_code == "nodes_excluded"


def test_identity_pending_keeps_the_grace_reason_code():
    # 막힌 노드와 신원 대기 노드가 섞여 0대면 기존 코드(no_eligible_nodes) -- planner 의 신원 유예가 걸려야 한다
    with pytest.raises(PlacementError) as e:
        _scan(_fresh(_report("n1"), _report("n2", identities=())), excluded={"n1"})
    assert e.value.reason_code == "no_eligible_nodes"


def test_sync_excluding_the_only_colocated_node_falls_back_to_nsync():
    # n1 만 src·dst 를 함께 마운트 -- 그걸 빼면 dsync 공존 노드가 없어 nsync(src 전용 n2 + dst 전용 n3)로 간다
    fresh = _fresh(_report("n1", ("src", "dst")), _report("n2", ("src",)), _report("n3", ("dst",)))
    out = select_tool_and_candidates("sync", fresh, source_storage="src", destination_storage="dst",
                                     owner="alice", privileged=False, excluded_nodes={"n1"})
    assert out["tool"] == "nsync"
    assert out["candidates"] == {"source": ["n2"], "destination": ["n3"]}


# ---- ② planner ----

class _PSettings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    planner_identity_grace_seconds = 300


def _planner_world(db, nodes=("n1", "n2", "n3")):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/dms",
                          backend_type="cephfs", actor="admin")
    repos.storages.set_status("s1", "Ready", "ready_nodes=3")
    repos.control.upsert_policy("scan", max_nodes=3, procs_per_node=8, queue="dms-data",
                                default_priority="mid", max_priority="high", preview_timeout_seconds=3600,
                                execution_timeout_seconds=3600, enabled=True, actor="admin")
    for n in nodes:
        repos.agents.ingest(n, _report(n), reported_at="2026-08-02T09:59:00Z")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k",
                                payload={"storage": "s1", "target": "t"}, priority="mid")
    planner = Planner(repos, StubIdentityResolver({"alice": ALICE}), settings=_PSettings())
    return repos, rid, planner


def test_planner_plans_on_remaining_nodes(db):
    repos, rid, planner = _planner_world(db)
    repos.node_exclusions.exclude("n2", reason="disk errors", actor="admin")
    planner.run_once(now_iso=NOW)
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    assert job["worker_pool"]["candidates"] == {"primary": ["n1", "n3"]}
    assert job["worker_pool"]["node_count"] == 2


def test_planner_rejects_immediately_when_every_node_is_blocked(db):
    # 기다려도 풀리지 않는 사유 -- 유예 없이 즉시 거부(같은 resource_key 후속 요청이 Conflict 로 죽지 않게)
    repos, rid, planner = _planner_world(db, nodes=("n1", "n2"))
    repos.node_exclusions.exclude("n1", reason=None, actor="admin")
    repos.agents.ingest("n2", _report("n2", schedulable=False), reported_at="2026-08-02T09:59:30Z")
    planner.run_once(now_iso=NOW)
    assert repos.requests.get(rid)["state"] == RequestState.REJECTED.value
    assert repos.requests.last_reason_code(rid) == "nodes_excluded"


def test_including_back_restores_the_node_for_new_plans(db):
    repos, rid, planner = _planner_world(db)
    repos.node_exclusions.exclude("n2", reason=None, actor="admin")
    assert repos.node_exclusions.include("n2", actor="admin") is True
    planner.run_once(now_iso=NOW)
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    assert job["worker_pool"]["candidates"] == {"primary": ["n1", "n2", "n3"]}


# ---- ③ stepper: 제출 직전 재검사 ----

class _SSettings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _planned_scan(repos, nodes=("n1", "n2"), *, state=None, batch_id=None):
    if repos.storages.get("s1") is None:
        repos.storages.create(storage_name="s1", mount_path="/s1", managed_root="/s1/dms",
                              backend_type="cephfs", actor="test")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key=f"k-{len(repos.requests.list_pending())}",
                                payload={"storage": "s1", "target": "a"}, priority="mid", batch_id=batch_id)
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 10001, "gid": 10000, "username": "alice",
                     "groups": [], "privileged": False},
                     "candidates": {"primary": list(nodes)}, "process_count": 16,
                     "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    if state is not None:
        repos.data_jobs.set_job_state(jid, state, actor="stepper")
    return rid, jid


def test_job_planned_onto_a_node_excluded_later_ends_before_submission(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos)
    repos.node_exclusions.exclude("n2", reason="nic flapping", actor="admin")
    adapter = StubExecutionAdapter()
    result = JobStepper(repos, adapter, settings=_SSettings()).run_once()
    assert result[jid] == "Rejected"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_excluded_at_step"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert adapter.submitted_specs() == []                    # 막힌 노드로 아무것도 제출하지 않았다
    ev = [e for e in repos.observability.events_for_request(rid) if e["event_type"] == "node_excluded_at_step"]
    assert ev and "n2=node_excluded" in ev[0]["message"]


def test_executing_job_whose_node_got_cordoned_fails_before_execution_submit(db):
    # 컨펌 뒤(Executing) 실행 제출 직전에 cordon 이 보였다 -- 그대로 내면 gang 이 안 서서 영원히 Pending
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, state=DataJobState.EXECUTING)
    repos.agents.ingest("n1", _report("n1", schedulable=False), reported_at="2026-08-02T09:59:00Z")
    adapter = StubExecutionAdapter()
    result = JobStepper(repos, adapter, settings=_SSettings()).run_once()
    assert result[jid] == "Failed"                              # 실행 단계 관례(_fail_closed)
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_excluded_at_step"
    assert adapter.submitted_specs() == []


def test_unrelated_exclusion_does_not_touch_the_job(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, nodes=("n1",))
    repos.node_exclusions.exclude("n9", reason=None, actor="admin")
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"
    assert len(adapter.submitted_specs()) == 1


def test_blocked_nodes_reads_the_latest_report_even_if_stale_and_exclusion_wins():
    class _R:
        pass
    repos = _R()
    repos.node_exclusions = type("X", (), {"excluded_names": lambda self: {"a"}})()
    repos.agents = type("A", (), {"latest_reports": lambda self, names: [
        {"node_name": "a", "report": _report("a", schedulable=False)},
        {"node_name": "b", "report": _report("b", schedulable=False)},
        {"node_name": "c", "report": _report("c", schedulable=True)}]})()
    assert blocked_nodes(repos, {"source": ["a", "b"], "destination": ["c"]}) == {
        "a": "node_excluded", "b": "node_unschedulable"}
    assert blocked_nodes(repos, None) == {} and blocked_nodes(repos, {"primary": [1, None]}) == {}


# ---- ④ 컨펌: 409 + 종단 ----

def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def test_confirm_with_a_blocked_node_is_409_and_ends_the_job(client):
    repos = client.app.state.repos
    rid, jid = _planned_scan(repos, state=DataJobState.CONFIRM_PENDING)
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc", expires_at="2099-01-01T00:00:00Z",
                                artifact_uri="file:///art/j")
    repos.node_exclusions.exclude("n1", reason=None, actor="admin")
    _login(client)
    r = client.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": "sha256:abc"})
    assert r.status_code == 409 and r.json()["detail"] == "node_excluded"
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert repos.requests.last_reason_code(rid) == "node_excluded_at_step"


# ---- ⑤ 관리자 API ----

def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def test_admin_excludes_and_includes_a_node_with_audit(client, db):
    _admin(client)
    repos = client.app.state.repos
    repos.agents.ingest("n1", _report("n1"))
    r = client.put("/api/admin/nodes/n1/exclusion", json={"reason": "  디스크 오류  "})
    assert r.status_code == 200
    assert (r.json()["node_name"], r.json()["reason"], r.json()["created_by"]) == ("n1", "디스크 오류", "admin")
    # 멱등: 다시 제외해도 처음 사유가 남고 감사 행도 하나
    again = client.put("/api/admin/nodes/n1/exclusion", json={"reason": "other"}).json()
    assert again["reason"] == "디스크 오류"
    [node] = client.get("/api/admin/nodes").json()
    assert node["exclusion"]["reason"] == "디스크 오류"
    # 다시 포함
    assert client.delete("/api/admin/nodes/n1/exclusion").json() == {"included": "n1"}
    assert client.get("/api/admin/nodes").json()[0]["exclusion"] is None
    assert client.delete("/api/admin/nodes/n1/exclusion").status_code == 404
    audits = db.query("SELECT operation, target_key, actor, before_state, after_state FROM audit_log "
                      "WHERE mutation_class = 'node_exclusion' ORDER BY id")
    assert [(a["operation"], a["target_key"], a["actor"]) for a in audits] == [
        ("exclude", "n1", "admin"), ("include", "n1", "admin")]
    assert json.loads(audits[1]["before_state"])["reason"] == "디스크 오류"


def test_exclusion_input_is_validated(client):
    _admin(client)
    client.app.state.repos.agents.ingest("n1", _report("n1"))
    # 보고한 적 없는 이름(오타)은 404 -- 조용히 "아무것도 안 막음"이 되면 관리자는 막았다고 믿는다
    r = client.put("/api/admin/nodes/n-typo/exclusion", json={})
    assert r.status_code == 404 and r.json()["detail"] == "node_not_found"
    r = client.put("/api/admin/nodes/n1/exclusion", json={"reason": "x" * 501})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_exclusion_reason"
    assert client.put("/api/admin/nodes/n1/exclusion", json={"reason": "   "}).json()["reason"] is None
    # 제어문자(NUL 등)는 422 -- PostgreSQL TEXT 는 NUL 을 못 담아 500 이 됐다(리뷰)
    for bad in ("a\x00b", "line\nbreak"):
        r = client.put("/api/admin/nodes/n1/exclusion", json={"reason": bad})
        assert r.status_code == 422 and r.json()["detail"] == "invalid_exclusion_reason"


def test_exclusion_api_requires_admin(client):
    client.post("/api/auth/signup", json={"username": "bob", "password": "p"})
    client.post("/api/auth/login", json={"username": "bob", "password": "p"})
    assert client.put("/api/admin/nodes/n1/exclusion", json={}).status_code == 403
    assert client.delete("/api/admin/nodes/n1/exclusion").status_code == 403


# ---- ⑥ 에이전트 k8s 프로브 ----

def test_k8s_schedulability_cordon_and_blocking_taints():
    assert k8s_schedulability({"unschedulable": True, "taints": []}) == {
        "schedulable": False, "reason": "cordoned", "transient": False}
    t = k8s_schedulability({"unschedulable": False, "taints": [
        {"key": "dms.io/excluded", "value": "true", "effect": "NoSchedule"},
        {"key": "soft", "value": None, "effect": "PreferNoSchedule"}]})
    assert t == {"schedulable": False, "reason": "taint dms.io/excluded=true:NoSchedule", "transient": False}
    assert k8s_schedulability({"unschedulable": False, "taints": [
        {"key": "soft", "value": None, "effect": "PreferNoSchedule"}]})["schedulable"] is True
    assert k8s_schedulability({})["schedulable"] is True


def test_k8s_condition_taints_are_transient_but_a_mixed_or_cordon_is_hard():
    # kubelet 조건 taint 만이면 transient(저절로 풀린다) -- 관리자 taint·cordon 이 하나라도 섞이면 hard
    t = k8s_schedulability({"taints": [{"key": "node.kubernetes.io/disk-pressure", "effect": "NoSchedule"}]})
    assert t["schedulable"] is False and t["transient"] is True
    mixed = k8s_schedulability({"taints": [{"key": "node.kubernetes.io/disk-pressure", "effect": "NoSchedule"},
                                           {"key": "maint", "value": "x", "effect": "NoExecute"}]})
    assert mixed["transient"] is False
    # cordon 의 짝 taint 는 조건이 아니다(관리자 조치)
    assert k8s_schedulability({"taints": [{"key": "node.kubernetes.io/unschedulable",
                                           "effect": "NoSchedule"}]})["transient"] is False


def test_transient_condition_avoids_new_plans_but_does_not_end_planned_jobs():
    r = _report("n1")
    r["k8s_node"] = {"schedulable": False, "reason": "taint node.kubernetes.io/memory-pressure:NoSchedule",
                     "transient": True}
    out = _scan(_fresh(r, _report("n2")))
    assert out["candidates"] == {"primary": ["n2"]} and out["rejections"] == {"n1": "node_unschedulable"}

    class _R:
        pass
    repos = _R()
    repos.node_exclusions = type("X", (), {"excluded_names": lambda self: set()})()
    repos.agents = type("A", (), {"latest_reports": lambda self, names: [{"node_name": "n1", "report": r}]})()
    assert blocked_nodes(repos, {"primary": ["n1"]}) == {}              # 이미 계획된 잡은 기다린다


def test_probe_k8s_node_fails_open():
    assert probe_k8s_node("n1", environ={}) == {"schedulable": None, "reason": "not_in_cluster"}

    def boom(_name):
        raise PermissionError("403")
    assert probe_k8s_node("n1", read_node=boom) == {"schedulable": None, "reason": "probe_failed:PermissionError"}
    assert probe_k8s_node("n1", read_node=lambda n: {"unschedulable": True})["schedulable"] is False


def test_agent_report_carries_k8s_node():
    r = build_report("n1", [], [], mountinfo_text="", tools_fn=lambda names: [],
                     identities_fn=lambda t: [], os_fn=lambda *a, **k: {},
                     k8s_node_fn=lambda name: {"schedulable": False, "reason": f"cordoned:{name}"})
    assert r["k8s_node"] == {"schedulable": False, "reason": "cordoned:n1"}


# ---- 배치 항목: 종료(사용자 결정 2026-10-02) ----

def test_batch_item_on_a_blocked_node_ends_rejected_with_the_reason(db):
    from dms.batch_orchestrator import BatchOrchestrator

    class _O:
        preview_ttl_seconds = 900
    repos = Repositories(db)
    bid = repos.batches.create(operation="scan", requester_id="admin", actor="admin", max_concurrency=1,
                               options={}, note=None, items=[{"storage": "s1", "target": "a"}],
                               status="Running")
    BatchOrchestrator(repos, settings=_O()).run_once()                  # 자식 요청 materialize
    [item] = repos.batches.list_items(bid)
    rid = item["request_id"]
    # planner 대신 자식 요청에 n1·n2 로 계획된 잡을 붙인다
    if repos.storages.get("s1") is None:
        repos.storages.create(storage_name="s1", mount_path="/s1", managed_root="/s1/dms",
                              backend_type="cephfs", actor="test")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 0, "gid": 0, "username": "admin",
                     "groups": [], "privileged": True},
                     "candidates": {"primary": ["n1", "n2"]}, "process_count": 16,
                     "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    repos.node_exclusions.exclude("n2", reason=None, actor="admin")
    JobStepper(repos, StubExecutionAdapter(), settings=_SSettings()).run_once()
    BatchOrchestrator(repos, settings=_O()).run_once()                  # 자식 종단 집계
    [item] = repos.batches.list_items(bid)
    assert item["status"] == "Rejected" and item["reason_code"] == "node_excluded_at_step"
    assert repos.batches.get(bid)["status"] == "Completed"



# ---- 적대적 리뷰 반영: 제출됐지만 아직 스케줄 전(PENDING) 단계도 재검사 ----

def test_submitted_but_pending_step_is_ended_when_its_node_gets_blocked(db):
    # Volcano 큐·gang 대기 중에 cordon/제외되면 -- 그대로 두면 cordon 은 영원히 Pending, 배치 제외만이면 큐가
    # 풀리는 순간 그 노드에 앉는다. 도구가 시작 전이라 종단하고 띄운 단계는 회수한다.
    repos = Repositories(db)
    rid, jid = _planned_scan(repos)
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_SSettings())
    stepper.run_once()                                               # preflight 제출
    ref = repos.data_jobs.get_job(jid)["phase_refs"]["preflight"]
    adapter.script(ref, [ExecStatus.PENDING, ExecStatus.PENDING])
    repos.node_exclusions.exclude("n2", reason=None, actor="admin")
    assert stepper.run_once()[jid] == "Rejected"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_excluded_at_step"
    assert adapter.poll(ref) == ExecStatus.FAILED                     # 대기 중이던 파드는 terminate 됐다


def test_running_step_is_left_alone_when_its_node_gets_blocked(db):
    # 노드에서 이미 도는 잡은 건드리지 않는다(maintenance·drain 관례)
    repos = Repositories(db)
    rid, jid = _planned_scan(repos)
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_SSettings())
    stepper.run_once()
    ref = repos.data_jobs.get_job(jid)["phase_refs"]["preflight"]
    adapter.script(ref, [ExecStatus.RUNNING])
    repos.node_exclusions.exclude("n2", reason=None, actor="admin")
    assert stepper.run_once()[jid] == "Preflight"


# ---- 적대적 리뷰 반영: sync 의 nodes_excluded 는 막힘이 진짜 원인일 때만 ----

def test_sync_reports_nodes_excluded_only_when_blocks_are_the_cause():
    # src 쪽 유일 노드가 막혔지만 dst 는 어디에도 없다 -- 막힘을 풀어도 배치가 안 서므로 원래 코드
    fresh = _fresh(_report("n1", ("src",)))
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("sync", fresh, source_storage="src", destination_storage="dst",
                                   owner="alice", privileged=False, excluded_nodes={"n1"})
    assert e.value.reason_code == "no_ready_sync_candidate"
    # 막힘만 풀면 배치가 서는 경우는 nodes_excluded
    fresh = _fresh(_report("n1", ("src",)), _report("n2", ("dst",)))
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates("sync", fresh, source_storage="src", destination_storage="dst",
                                   owner="alice", privileged=False, excluded_nodes={"n1"})
    assert e.value.reason_code == "nodes_excluded"


def test_scan_with_no_mount_anywhere_keeps_no_eligible_nodes_even_if_some_node_is_excluded():
    with pytest.raises(PlacementError) as e:
        _scan(_fresh(_report("n1", storages=()), _report("n2", storages=())), excluded={"n1"})
    assert e.value.reason_code == "no_eligible_nodes"
