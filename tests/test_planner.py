import pytest
from dms.domain import ROLE_USER, RequestState
from dms.identity import ResolvedIdentity, StubIdentityResolver
from dms.planner import Planner
from dms.repositories import Repositories

NOW = "2026-08-02T10:00:00Z"
ALICE = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)


class _Settings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    planner_identity_grace_seconds = 300


def _seed_storage(repos, name="s1", status="Ready"):
    repos.storages.create(storage_name=name, mount_path=f"/mnt/{name}",
                          managed_root=f"/mnt/{name}/dms", backend_type="cephfs",
                          actor="admin")
    repos.storages.set_status(name, status, "ready_nodes=1")


def _seed_sync_storages(repos):
    # alice(비관리자)의 src -> dst sync 가 계획되려면 그 쌍이 허용돼 있어야 한다(2026-09-30 사용자
    # sync 허용 쌍, 기본 전부 불가). 이 파일의 sync 테스트는 배치·신원 대기가 관심사라 허용해 둔다
    # -- 게이트 자체는 test_sync_pairs 가 고정한다.
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
    repos.sync_pairs.add("src", "dst", actor="admin")


def _seed_policy(repos, tool="scan"):
    repos.control.upsert_policy(tool, max_nodes=3, procs_per_node=8, queue="dms-data",
                                default_priority="mid", max_priority="high",
                                preview_timeout_seconds=3600,
                                execution_timeout_seconds=3600, enabled=True,
                                actor="admin")


def _seed_report(repos, node="n1", storage="s1", user="alice"):
    repos.agents.ingest(node, {
        "node_name": node,
        "mounts": [{"storage_name": storage, "mount_path": f"/mnt/{storage}",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": t, "status": "Ready"}
                  for t in ("dscan", "dsync", "nsync", "drm")],
        "identities": [{"username": user, "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")


def _seed_identity_pending_report(repos, node="n1", storage="s1"):
    # 마운트·도구는 전부 Ready, 신원만 미전파 -- 유예 대상의 정확한 형태(설계 §2.3).
    # identities 빈 목록 = 에이전트가 아직 alice 를 프로브 대상으로 못 받은 상태.
    repos.agents.ingest(node, {
        "node_name": node,
        "mounts": [{"storage_name": storage, "mount_path": f"/mnt/{storage}",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": t, "status": "Ready"}
                  for t in ("dscan", "dsync", "nsync", "drm")],
        "identities": []},
        reported_at="2026-08-02T09:59:00Z")


def _seed_sync_reports(repos, identities):
    # n1 은 src 만, n2 는 dst 만 마운트 -- 실 테스트베드처럼 노드별 사유가 섞인다.
    # 한쪽 role 에서 신원 대기인 노드가 반대쪽에서는 미마운트로 잡힌다.
    for node, storage in (("n1", "src"), ("n2", "dst")):
        repos.agents.ingest(node, {"node_name": node,
            "mounts": [{"storage_name": storage, "mount_path": f"/mnt/{storage}",
                        "status": "Ready", "writable": True}],
            "tools": [{"name": t, "status": "Ready"} for t in ("dsync", "nsync")],
            "identities": identities},
            reported_at="2026-08-02T09:59:00Z")


def _seed_sync_node(repos, node, storage, *, identity_ready):
    # 노드별로 신원 전파 여부를 따로 준다 -- 합집합의 한쪽 절반에만 신원 대기 노드를
    # 두는 비대칭 형상을 만들기 위한 것(아래 destination-only/source-only 테스트).
    repos.agents.ingest(node, {"node_name": node,
        "mounts": [{"storage_name": storage, "mount_path": f"/mnt/{storage}",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": t, "status": "Ready"} for t in ("dsync", "nsync")],
        "identities": ([{"username": "alice", "status": "Ready"}]
                       if identity_ready else [])},
        reported_at="2026-08-02T09:59:00Z")


def _seed_unmounted_report(repos, node, tool="dscan"):
    repos.agents.ingest(node, {"node_name": node, "mounts": [],
        "tools": [{"name": tool, "status": "Ready"}], "identities": []},
        reported_at="2026-08-02T09:59:00Z")


def _sync_request(repos):
    return repos.requests.create(
        operation="sync", requester_id="alice", actor="alice",
        resource_key="data.sync:src:a:dst:b:ff",
        payload={"source_storage": "src", "source": "a",
                 "destination_storage": "dst", "destination": "b",
                 "options": {}, "owner_username": None}, priority="mid")


def _backdate(db, rid, created_at):
    # repos.requests.create 는 created_at 을 벽시계로 넣는다 -- grace 판정을
    # 결정적으로 만들려면 NOW(고정 시각) 기준으로 나이를 직접 심어야 한다.
    db.execute("UPDATE requests SET created_at = :c WHERE request_id = :id",
               {"c": created_at, "id": rid})


def _scan_request(repos, requester="alice", key="data.scan:s1:a:ff"):
    return repos.requests.create(
        operation="scan", requester_id=requester, actor=requester,
        resource_key=key, payload={"storage": "s1", "target": "a",
                                   "options": {}, "owner_username": None},
        priority="mid")


def _planner(repos, resolver=None):
    return Planner(repos, resolver or StubIdentityResolver({"alice": ALICE}),
                   settings=_Settings())


def test_happy_path_plans_scan(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    assert repos.requests.get(rid)["state"] == "Planned"
    jobs = repos.data_jobs.list_jobs(request_id=rid)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["tool"] == "dscan" and job["state"] == "Pending"
    assert job["worker_pool"]["candidates"]["primary"] == ["n1"]
    assert job["worker_pool"]["identity"]["uid"] == 10001
    assert job["worker_pool"]["process_count"] == 8


def test_storage_missing_disabled_not_ready(db):
    repos = Repositories(db)
    _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:storage_missing"
    assert repos.requests.get(rid)["state"] == "Rejected"

    # storage가 존재하지만 status가 Unknown이면 not_ready
    _seed_storage(repos, status="Unknown")
    rid2 = _scan_request(repos, key="data.scan:s1:b:ff")
    assert _planner(repos).run_once(now_iso=NOW)[rid2] == "rejected:storage_not_ready"


def test_conflict_on_prior_active(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    first = _scan_request(repos, key="dup")
    second = _scan_request(repos, key="dup")
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[first] == "planned"
    assert result[second] == "conflict"
    assert repos.requests.get(second)["state"] == "Conflict"


def test_identity_rejection(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    planner = _planner(repos, resolver=StubIdentityResolver({}))  # alice 없음
    assert planner.run_once(now_iso=NOW)[rid] == "rejected:ldap_identity_not_found"


def test_missing_policy_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_report(repos)
    db.execute("DELETE FROM policies WHERE tool = 'scan'")  # 시드된 기본 정책 제거
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:missing_policy"


def test_no_candidates_when_no_fresh_report(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)  # 리포트 없음
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_eligible_nodes"


def test_sync_selects_nsync(db):
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")
    repos.agents.ingest("n1", {"node_name": "n1",
        "mounts": [{"storage_name": "src", "mount_path": "/mnt/src",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": "nsync", "status": "Ready"},
                  {"name": "dsync", "status": "Ready"}],
        "identities": [{"username": "alice", "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")
    repos.agents.ingest("n2", {"node_name": "n2",
        "mounts": [{"storage_name": "dst", "mount_path": "/mnt/dst",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": "nsync", "status": "Ready"},
                  {"name": "dsync", "status": "Ready"}],
        "identities": [{"username": "alice", "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="data.sync:src:a:dst:b:ff",
        payload={"source_storage": "src", "source": "a",
                 "destination_storage": "dst", "destination": "b",
                 "options": {}, "owner_username": None}, priority="mid")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    job = repos.data_jobs.list_jobs(request_id=rid)[0]
    assert job["tool"] == "nsync"
    assert job["worker_pool"]["candidates"]["source"] == ["n1"]
    assert job["worker_pool"]["candidates"]["destination"] == ["n2"]


def test_replan_is_idempotent(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    _planner(repos).run_once(now_iso=NOW)
    # 크래시 흉내: 상태만 Pending으로 되돌림(잡은 남음)
    repos.requests.set_state(rid, RequestState.PENDING, actor="test")
    _planner(repos).run_once(now_iso=NOW)
    assert len(repos.data_jobs.list_jobs(request_id=rid)) == 1
    assert repos.requests.get(rid)["state"] == "Planned"


def test_policy_max_nodes_trims_scan_candidates(db):
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)  # max_nodes=3, procs_per_node=8
    for node in ("n1", "n2", "n3", "n4", "n5"):
        _seed_report(repos, node=node)
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["candidates"]["primary"] == ["n1", "n2", "n3"]
    assert len(wp["candidates"]["primary"]) == wp["node_count"] == 3
    assert wp["process_count"] == 24


def test_policy_max_nodes_trims_sync_candidates(db):
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")  # max_nodes=3, procs_per_node=8
    for node in ("s1", "s2", "s3", "s4"):
        repos.agents.ingest(node, {"node_name": node,
            "mounts": [{"storage_name": "src", "mount_path": "/mnt/src",
                        "status": "Ready", "writable": True}],
            "tools": [{"name": "nsync", "status": "Ready"}],
            "identities": [{"username": "alice", "status": "Ready"}]},
            reported_at="2026-08-02T09:59:00Z")
    for node in ("d1", "d2"):
        repos.agents.ingest(node, {"node_name": node,
            "mounts": [{"storage_name": "dst", "mount_path": "/mnt/dst",
                        "status": "Ready", "writable": True}],
            "tools": [{"name": "nsync", "status": "Ready"}],
            "identities": [{"username": "alice", "status": "Ready"}]},
            reported_at="2026-08-02T09:59:00Z")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="data.sync:src:a:dst:b:ff",
        payload={"source_storage": "src", "source": "a",
                 "destination_storage": "dst", "destination": "b",
                 "options": {}, "owner_username": None}, priority="mid")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["candidates"]["source"] == ["s1", "s2", "s3"]
    assert wp["candidates"]["destination"] == ["d1", "d2"]
    assert len(wp["candidates"]["source"]) == wp["source_count"] == 3
    assert len(wp["candidates"]["destination"]) == wp["destination_count"] == 2


# --- 슬라이스 32: payload.node_count — min(정책, 요청) 캡 + fail-closed 방어 ---

def test_payload_node_count_caps_candidates(db):
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)  # max_nodes=3
    for node in ("n1", "n2", "n3", "n4", "n5"):
        _seed_report(repos, node=node)
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff",
        payload={"storage": "s1", "target": "a", "options": {},
                 "owner_username": None, "node_count": 2},
        priority="mid")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 2                      # min(정책 3, 요청 2)
    assert wp["candidates"]["primary"] == ["n1", "n2"]  # 슬라이스도 축소
    assert wp["process_count"] == 16


def test_payload_node_count_tampered_rejects(db):
    # DB 는 신뢰 경계 — payload 는 무검증 INSERT 로 변조될 수 있다. 값이 있는데
    # 비정상(str)이면 fail-closed 거부(stepper 층1 unknown_tool 관례).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff",
        payload={"storage": "s1", "target": "a", "options": {},
                 "owner_username": None, "node_count": "8"},
        priority="mid")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:invalid_node_count"
    assert repos.requests.get(rid)["state"] == "Rejected"
    # _reject 는 set_state_with_result 경로 — results 행이 함께 남는다.
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": rid})
    assert (row["terminal_state"], row["reason_code"]) == ("Rejected",
                                                           "invalid_node_count")


# --- payload.procs_per_node — node_count 와 같은 min-캡 + fail-closed 방어 미러 ---

def test_payload_procs_per_node_caps_process_count(db):
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)  # procs_per_node=8
    for node in ("n1", "n2", "n3", "n4", "n5"):
        _seed_report(repos, node=node)
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff",
        payload={"storage": "s1", "target": "a", "options": {},
                 "owner_username": None, "procs_per_node": 2},
        priority="mid")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 3                      # 노드 수는 불변(정책 max_nodes)
    assert wp["process_count"] == 6                   # 3노드 × min(정책 8, 요청 2)


def test_payload_procs_per_node_tampered_rejects(db):
    # DB 는 신뢰 경계 — payload 는 무검증 INSERT 로 변조될 수 있다. 값이 있는데
    # 비정상(str)이면 fail-closed 거부(stepper 층1 unknown_tool 관례).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="data.scan:s1:a:ff",
        payload={"storage": "s1", "target": "a", "options": {},
                 "owner_username": None, "procs_per_node": "8"},
        priority="mid")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:invalid_procs_per_node"
    assert repos.requests.get(rid)["state"] == "Rejected"
    # _reject 는 set_state_with_result 경로 — results 행이 함께 남는다.
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": rid})
    assert (row["terminal_state"], row["reason_code"]) == ("Rejected",
                                                           "invalid_procs_per_node")


def test_requester_disabled_account_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    repos.accounts.create("alice", "pw", ROLE_USER, actor="admin")
    repos.accounts.set_disabled("alice", True, actor="admin")
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:requester_disabled"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_requester_enabled_account_plans_normally(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    repos.accounts.create("alice", "pw", ROLE_USER, actor="admin")
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"


def test_requester_without_account_row_plans_normally(db):
    # 회귀 가드: 포탈 계정이 없는 요청자(대부분의 기존 테스트가 이 형태)는
    # "무판정"이어야 한다 — account row가 없다고 해서 거부되면 안 된다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    assert repos.accounts.get("alice") is None
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"


def test_unexpected_exception_records_plan_error_event(db, monkeypatch):
    # 배선 회귀 가드: run_once의 항목별 except Exception이 stderr만 찍고 끝나면
    # 이 실패는 전이도, 이벤트도 안 남는다 -- events 테이블을 직접 검사한다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(repos.accounts, "get", _boom)

    result = _planner(repos).run_once(now_iso=NOW)
    assert rid not in result  # 예외 분기는 results에 아무 것도 안 남긴다
    events = repos.observability.events_for_request(rid)
    assert len(events) == 1
    assert events[0]["component"] == "planner"
    assert events[0]["event_type"] == "plan_error"
    assert events[0]["severity"] == "error"
    assert "RuntimeError" in events[0]["message"] and "boom" in events[0]["message"]


def test_worker_pool_records_rejections(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_report(repos, node="n1")  # 적격
    # n2는 도구 없음 → 탈락 사유 기록돼야
    repos.agents.ingest("n2", {"node_name": "n2",
        "mounts": [{"storage_name": "s1", "mount_path": "/mnt/s1",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": "dsync", "status": "Ready"}],  # dscan 없음
        "identities": [{"username": "alice", "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")
    rid = _scan_request(repos)
    _planner(repos).run_once(now_iso=NOW)
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["rejections"]  # 비어있지 않음


def test_identity_only_rejection_defers_within_grace(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_identity_pending_report(repos)
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")        # 나이 120s < grace 300s
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "deferred:identity_propagating"
    # 아무 상태도 바꾸지 않는다(설계 §2.3) -- Pending 으로 남아 다음 틱의
    # list_pending 에 다시 걸린다. results 행(종단)도 물론 없다.
    assert repos.requests.get(rid)["state"] == "Pending"
    assert repos.data_jobs.list_jobs(request_id=rid) == []
    # 유예는 관측 가능해야 한다 -- 매 유예마다 이벤트(설계 §2.3)
    events = repos.observability.events_for_request(rid)
    assert [e["event_type"] for e in events] == ["identity_propagating"]
    assert events[0]["severity"] == "info"
    assert events[0]["payload"] == {
        "rejections": {"n1": "identity_not_ready_on_node"}}


def test_identity_grace_expired_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_identity_pending_report(repos)
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:54:00Z")        # 나이 360s > grace 300s
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_eligible_nodes"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_mixed_rejections_defer_within_grace(db):
    # 실 테스트베드 형상(설계 §2.3 정정): cephfs-third 처럼 일부 노드는 미마운트,
    # 일부는 신원만 대기다. "모든 노드가 신원 대기" 규칙이었다면 이 형상은 유예되지
    # 못했다 -- 고치겠다고 한 바로 그 케이스를 못 고치는 규칙이었다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_identity_pending_report(repos, node="n1")
    _seed_unmounted_report(repos, "n2")
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    assert repos.requests.get(rid)["state"] == "Pending"
    assert repos.data_jobs.list_jobs(request_id=rid) == []
    events = repos.observability.events_for_request(rid)
    assert [e["event_type"] for e in events] == ["identity_propagating"]
    # 페이로드는 섞인 사유를 그대로 남긴다 -- 운영자가 "왜 0대인가"를 봐야 한다.
    assert events[0]["payload"]["rejections"] == {
        "n1": "identity_not_ready_on_node", "n2": "missing_target_mount"}


def test_non_identity_rejections_reject_immediately(db):
    # 신원 사유 노드가 하나도 없으면(전 노드 미마운트) 전파돼도 적격이 될 노드가
    # 없다 -- grace 안이어도 즉시 거부. "증명되면 유예, 아니면 거부".
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_unmounted_report(repos, "n1")
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_eligible_nodes"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_deferred_request_plans_after_identity_propagates(db):
    # 슬라이스 15 실증에서 실패했던 바로 그 시나리오(설계 §6-4): 첫 요청이 전파를
    # 기다렸다가 자동 성공해야 한다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_identity_pending_report(repos)
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    _seed_report(repos)                                # 신원 전파 완료(alice Ready)
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    assert repos.requests.get(rid)["state"] == "Planned"


def test_sync_identity_pending_defers_and_plans_after_propagation(db):
    # 슬라이스 15 실증에서 실제로 났던 전이(Rejected / no_ready_sync_candidate)가
    # 이 형상이다 -- sync 도 유예 대상이어야 한다(설계 §2.3 정정).
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")
    _seed_sync_reports(repos, identities=[])          # 신원 미전파
    rid = _sync_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    assert repos.requests.get(rid)["state"] == "Pending"
    events = repos.observability.events_for_request(rid)
    assert [e["event_type"] for e in events] == ["identity_propagating"]
    # 합집합 판정의 증거: source 쪽 신원 대기는 n1, destination 쪽은 n2 이고 각각
    # 반대쪽에서는 미마운트다. 한쪽 dict 만 봤다면 이 형상도 놓쳤을 것이다.
    assert events[0]["payload"]["rejections"] == {
        "source": {"n1": "identity_not_ready_on_node", "n2": "missing_target_mount"},
        "destination": {"n1": "missing_target_mount",
                        "n2": "identity_not_ready_on_node"}}
    _seed_sync_reports(repos, identities=[{"username": "alice", "status": "Ready"}])
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    job = repos.data_jobs.list_jobs(request_id=rid)[0]
    assert job["tool"] == "nsync"
    assert job["worker_pool"]["candidates"] == {"source": ["n1"], "destination": ["n2"]}


def test_sync_defers_when_only_destination_has_identity_pending(db):
    # 합집합의 destination 절반을 고정한다. source 쪽 사유에는 identity 가 하나도
    # 없고(n1 은 이미 적격, n2 는 src 미마운트) destination 쪽 n2 만 신원 대기다 --
    # _identity_pending_nodes 가 source dict 만 훑도록 좁아지면 이 요청은 즉시
    # 거부되고, 그게 이 태스크가 없애려던 과잉 거부다.
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")
    _seed_sync_node(repos, "n1", "src", identity_ready=True)
    _seed_sync_node(repos, "n2", "dst", identity_ready=False)
    rid = _sync_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    assert repos.requests.get(rid)["state"] == "Pending"
    events = repos.observability.events_for_request(rid)
    assert events[0]["payload"]["rejections"] == {
        "source": {"n2": "missing_target_mount"},
        "destination": {"n1": "missing_target_mount",
                        "n2": "identity_not_ready_on_node"}}
    # 유예 사유가 destination 쪽에만 있어도 전파되면 정상 수렴한다.
    _seed_sync_node(repos, "n2", "dst", identity_ready=True)
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    job = repos.data_jobs.list_jobs(request_id=rid)[0]
    assert job["worker_pool"]["candidates"] == {"source": ["n1"], "destination": ["n2"]}


def test_sync_defers_when_only_source_has_identity_pending(db):
    # 위의 거울상 -- 합집합의 source 절반을 고정한다(destination 쪽만 훑는 회귀를 잡는다).
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")
    _seed_sync_node(repos, "n1", "src", identity_ready=False)
    _seed_sync_node(repos, "n2", "dst", identity_ready=True)
    rid = _sync_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    assert repos.requests.get(rid)["state"] == "Pending"
    events = repos.observability.events_for_request(rid)
    assert events[0]["payload"]["rejections"] == {
        "source": {"n1": "identity_not_ready_on_node",
                   "n2": "missing_target_mount"},
        "destination": {"n1": "missing_target_mount"}}
    _seed_sync_node(repos, "n1", "src", identity_ready=True)
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    job = repos.data_jobs.list_jobs(request_id=rid)[0]
    assert job["worker_pool"]["candidates"] == {"source": ["n1"], "destination": ["n2"]}


def test_sync_without_identity_pending_rejects_immediately(db):
    # 양쪽 합집합에 신원 사유 노드가 0 -- 전파돼도 적격이 될 노드가 없으므로 즉시 거부.
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")
    _seed_unmounted_report(repos, "n1", tool="nsync")
    _seed_unmounted_report(repos, "n2", tool="nsync")
    rid = _sync_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")        # grace 안이어도
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_ready_sync_candidate"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_sync_identity_grace_expired_rejects(db):
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")
    _seed_sync_reports(repos, identities=[])
    rid = _sync_request(repos)
    _backdate(db, rid, "2026-08-02T09:54:00Z")        # 나이 360s > grace 300s
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_ready_sync_candidate"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_repeated_defer_records_event_once_until_reason_changes(db):
    # 유예는 매 틱(기본 10s) 재평가된다 -- 틱마다 남기면 grace 300s 동안 요청 하나에
    # 30건이 쌓여 요청 상세의 이벤트 목록(limit 100)을 유예 잡음으로 덮는다.
    # 사유가 바뀔 때만(첫 유예 포함) 남긴다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_identity_pending_report(repos)
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = _planner(repos)
    for _ in range(3):
        assert planner.run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    assert len(repos.observability.events_for_request(rid)) == 1
    # 사유가 바뀌면 다시 남긴다 -- 억제는 "같은 사유의 연속"에만 적용된다.
    _seed_unmounted_report(repos, "n2")
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    events = repos.observability.events_for_request(rid)
    assert len(events) == 2
    assert events[1]["payload"]["rejections"]["n2"] == "missing_target_mount"


# --- 슬라이스 33(A'): node_count 명시 요청은 유예 창 안에서 요청 수를 기다린다 ---

def _scan_request_with_count(repos, count, key="data.scan:s1:a:ff"):
    return repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key=key, payload={"storage": "s1", "target": "a", "options": {},
                                   "owner_username": None, "node_count": count},
        priority="mid")


def test_requested_node_count_waits_within_grace(db):
    # 적격 1 < 목표 2 인데 부족 사유가 신원 전파 대기 -- 유예 창 안에서는 있는
    # 만큼으로 계획하지 않고 다음 틱을 기다린다(실측 사고: 전파 중 1대만 준비된
    # 틱에 계획돼 요청 2대가 1대로 박제).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_report(repos, node="n1")                     # 적격
    _seed_identity_pending_report(repos, node="n2")    # 신원만 대기
    rid = _scan_request_with_count(repos, 2)
    _backdate(db, rid, "2026-08-02T09:58:00Z")         # 나이 120s < grace 300s
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:awaiting_requested_nodes"
    assert repos.requests.get(rid)["state"] == "Pending"
    assert repos.data_jobs.list_jobs(request_id=rid) == []
    events = repos.observability.events_for_request(rid)
    assert [e["event_type"] for e in events] == ["awaiting_requested_nodes"]
    assert events[0]["severity"] == "info"
    assert events[0]["payload"] == {"eligible": 1, "target": 2,
                                    "not_ready_nodes": ["n2"]}
    # identity_propagating 과 같은 중복 억제 관례 -- 같은 사유의 연속 유예는 1건만.
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:awaiting_requested_nodes"
    assert len(repos.observability.events_for_request(rid)) == 1


def test_requested_node_count_grace_expired_plans_with_available(db):
    # 마감 있는 최선: 유예 만료 후 첫 틱엔 있는 만큼으로 계획한다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_report(repos, node="n1")
    _seed_identity_pending_report(repos, node="n2")
    rid = _scan_request_with_count(repos, 2)
    _backdate(db, rid, "2026-08-02T09:54:00Z")         # 나이 360s > grace 300s
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 1
    assert wp["candidates"]["primary"] == ["n1"]


def test_requested_node_count_non_identity_shortage_plans_immediately(db):
    # 부족하지만 신원 사유 노드가 0(미마운트만) -- 기다려도 늘어나지 않으므로
    # grace 안이어도 즉시 계획한다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_report(repos, node="n1")
    _seed_unmounted_report(repos, "n2")
    rid = _scan_request_with_count(repos, 2)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 1


def test_unspecified_node_count_plans_immediately_despite_identity_pending(db):
    # 회귀 금지: node_count 미지정이면 현행대로 적격 >= 1 에서 즉시 계획한다 --
    # A' 분기는 요청이 수를 명시했을 때만 발동한다(null != 0).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_report(repos, node="n1")
    _seed_identity_pending_report(repos, node="n2")
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 1


def test_requested_node_count_target_is_policy_capped(db):
    # 목표는 min(요청, 정책 max_nodes) -- 정책이 허용 안 하는 수(요청 8)를
    # 기다리지 않는다. 적격 4 = 정책 캡 4 이므로 신원 대기 노드가 있어도 즉시 계획.
    repos = Repositories(db)
    _seed_storage(repos)
    repos.control.upsert_policy("scan", max_nodes=4, procs_per_node=8,
                                queue="dms-data", default_priority="mid",
                                max_priority="high", preview_timeout_seconds=3600,
                                execution_timeout_seconds=3600, enabled=True,
                                actor="admin")
    for node in ("n1", "n2", "n3", "n4"):
        _seed_report(repos, node=node)
    _seed_identity_pending_report(repos, node="n5")
    rid = _scan_request_with_count(repos, 8)
    _backdate(db, rid, "2026-08-02T09:58:00Z")         # grace 안이어도
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 4
    assert wp["candidates"]["primary"] == ["n1", "n2", "n3", "n4"]


def test_requested_node_count_plans_when_target_reached(db):
    # 정상 종료 경로: 전파가 완료돼 적격이 목표에 도달하면 요청한 수로 계획한다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_report(repos, node="n1")
    _seed_identity_pending_report(repos, node="n2")
    rid = _scan_request_with_count(repos, 2)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:awaiting_requested_nodes"
    _seed_report(repos, node="n2")                     # 전파 완료
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 2
    assert wp["candidates"]["primary"] == ["n1", "n2"]


def test_sync_requested_node_count_waits_per_side(db):
    # sync 는 max_nodes 가 면당 상한이므로 목표도 면당 동일 규칙 -- source 는
    # 충족(2)이어도 destination 이 부족(1<2)이고 그 사유가 신원 대기면 기다린다.
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")
    _seed_sync_node(repos, "s1", "src", identity_ready=True)
    _seed_sync_node(repos, "s2", "src", identity_ready=True)
    _seed_sync_node(repos, "d1", "dst", identity_ready=True)
    _seed_sync_node(repos, "d2", "dst", identity_ready=False)
    rid = repos.requests.create(
        operation="sync", requester_id="alice", actor="alice",
        resource_key="data.sync:src:a:dst:b:ff",
        payload={"source_storage": "src", "source": "a",
                 "destination_storage": "dst", "destination": "b",
                 "options": {}, "owner_username": None, "node_count": 2},
        priority="mid")
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "deferred:awaiting_requested_nodes"
    assert repos.requests.get(rid)["state"] == "Pending"
    events = repos.observability.events_for_request(rid)
    assert [e["event_type"] for e in events] == ["awaiting_requested_nodes"]
    assert events[0]["payload"] == {
        "eligible": {"source": 2, "destination": 1}, "target": 2,
        "not_ready_nodes": ["d2"]}
    _seed_sync_node(repos, "d2", "dst", identity_ready=True)  # 전파 완료
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["source_count"] == 2 and wp["destination_count"] == 2


class _PrivSettings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"root"})
    planner_identity_grace_seconds = 300


def _root_request(repos, key, auth_method, run_as_root=False):
    payload = {"storage": "s1", "target": "a", "options": {}, "owner_username": None}
    if run_as_root:
        payload["run_as_root"] = True
    return repos.requests.create(
        operation="scan", requester_id="root", actor="root", resource_key=key,
        payload=payload,
        priority="mid", auth_method=auth_method)


def test_session_auth_root_request_runs_privileged(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos, user="root")
    rid = _root_request(repos, "k-session", "session", run_as_root=True)
    resolver = StubIdentityResolver(
        {"root": ResolvedIdentity("root", 5000, 5000, (), False)})
    Planner(repos, resolver, settings=_PrivSettings()).run_once(now_iso=NOW)
    ident = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]["identity"]
    assert ident["uid"] == 0 and ident["privileged"] is True


def test_token_auth_root_request_never_runs_privileged(db):
    # 설계 §2.2-2 심층 방어: 토큰 인증이면 requester_id 가 root 라도 uid 0 이 아니라
    # LDAP 로 해석된 실제 uid 로 돈다. (Task 1 이 애초에 token 으로 requester_id=root
    # 를 못 만들게 막지만, 다른 경로로 들어와도 여기서 다시 끊긴다.)
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos, user="root")
    rid = _root_request(repos, "k-token", "token")
    resolver = StubIdentityResolver(
        {"root": ResolvedIdentity("root", 5000, 5000, (), False)})
    Planner(repos, resolver, settings=_PrivSettings()).run_once(now_iso=NOW)
    ident = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]["identity"]
    assert ident["uid"] == 5000 and ident["privileged"] is False


def test_privileged_batch_child_plans_for_non_ldap_requester(db):
    """배치 특권 실행의 종단 통합: LDAP 밖 로컬 admin 이 만든 세션 배치의 자식이
    (a) 상속된 auth_method="session" + allowlist 로 특권을 얻어 root 로 계획되고
    (b) placement 신원 게이트를 건너뛴다(placement.py -- privileged 는 노드 신원
    전파와 무관). 이 성질이 없으면 실사고 그대로 ldap_identity_not_found 즉시
    Rejected 다(배치 자식 token 고정 시절의 그 경로)."""
    from dms.batch_orchestrator import BatchOrchestrator

    class _AdminPrivSettings(_PrivSettings):
        privileged_requesters = frozenset({"admin"})

    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    # 신원 미전파 리포트(identities: []) -- 특권이 아니면 후보 0 이 되는 형상.
    _seed_identity_pending_report(repos)
    # 배치는 관리자 전용 라우트에서만 생긴다 -- 생성자 admin 은 로컬 관리자 계정(LDAP 밖)이다. 자식의 실행 신원
    # 지정(owner alice)은 planner 가 API 와 같은 술어(계정 역할 + 목록)로 다시 본다(2026-10-07).
    repos.accounts.create("admin", "pw", "admin")
    bid = repos.batches.create(
        operation="scan", requester_id="admin", actor="admin", max_concurrency=1,
        options={}, note=None, items=[{"storage": "s1", "target": "a"}],
        status="Running", owner_username="alice", auth_method="session")
    BatchOrchestrator(repos, settings=_AdminPrivSettings()).run_once()
    rid = repos.batches.list_items(bid)[0]["request_id"]
    resolver = StubIdentityResolver({})       # LDAP 에 admin 도 alice 도 없다
    result = Planner(repos, resolver,
                     settings=_AdminPrivSettings()).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    ident = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]["identity"]
    assert (ident["username"], ident["uid"], ident["privileged"]) == ("alice", 0, True)


def test_reject_crash_between_writes_leaves_request_pending(db):
    """비원자 쌍(BACKLOG §2.4, 슬라이스 27 발견): set_state(REJECTED) 커밋 후
    record_result 전에 죽으면 요청은 Rejected 인데 results 가 없다 -- Rejected 는
    종단이라 고아 스윕 시야 밖, 결손이 영구다(finalize 의 슬라이스 24 실증과
    동일 계열). 원자화 후엔 둘 다 롤백 -> Pending 잔존, run_once 의 요청별 예외
    격리가 plan_error 로 남기고 다음 틱 재계획이 완주한다."""
    repos = Repositories(db)
    rid = _scan_request(repos)      # 스토리지 미시드 -> storage_missing 거부 경로

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom     # 인스턴스 속성이 메서드를 가린다
    _planner(repos).run_once(now_iso=NOW)    # 요청별 try/except 가 삼킨다(무전파)
    del repos.requests.record_result         # 원복 -- 클래스 메서드가 되살아난다
    # 전부-또는-전무: Pending 그대로, Rejected 전이도 results 도 없다.
    assert repos.requests.get(rid)["state"] == "Pending"
    assert all(t["to_state"] != "Rejected" for t in repos.requests.transitions(rid))
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": rid}) == []
    # 다음 틱: 정상 완주 -- 거부 상태와 results 행이 함께 남는다.
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:storage_missing"
    assert repos.requests.get(rid)["state"] == "Rejected"
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": rid})
    assert (row["terminal_state"], row["reason_code"]) == ("Rejected",
                                                           "storage_missing")


def test_conflict_crash_between_writes_leaves_request_pending(db):
    # 비원자 쌍의 두 번째(conflict 경로) -- _reject 와 같은 계약. 첫 요청은 정상
    # 계획(planned 경로는 record_result 를 안 부른다), 둘째가 conflict 에서 크래시.
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)
    _seed_report(repos)
    first = _scan_request(repos)
    second = _scan_request(repos)   # 같은 resource_key -- 뒤가 conflict

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom
    _planner(repos).run_once(now_iso=NOW)
    del repos.requests.record_result
    assert repos.requests.get(first)["state"] == "Planned"     # 격리: 옆은 무사
    assert repos.requests.get(second)["state"] == "Pending"
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": second}) == []
    assert _planner(repos).run_once(now_iso=NOW)[second] == "conflict"
    assert repos.requests.get(second)["state"] == "Conflict"
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": second})
    assert (row["terminal_state"], row["reason_code"]) == ("Conflict",
                                                           "resource_conflict")


class _CountingDownResolver:
    def __init__(self):
        self.calls = 0

    def resolve(self, username, *, deadline=None):
        from dms.identity import IdentityUnavailable
        self.calls += 1
        raise IdentityUnavailable("ldap down")


def test_ldap_outage_trips_the_tick_circuit_and_leaves_the_rest_pending(db):
    # LDAP 가 죽은 틱: 첫 요청만 LDAP 에 가서 ldap_unavailable 로 거부되고(기존 의미), 나머지는 LDAP 에 다시 가지
    # 않고 Pending 으로 남는다 -- 틱마다 대기 요청 수 × 타임아웃만큼 단일 스레드 컨트롤러가 멈추던 것을 막는다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rids = [_scan_request(repos, key=f"data.scan:s1:a{i}:ff") for i in range(3)]
    down = _CountingDownResolver()
    result = _planner(repos, resolver=down).run_once(now_iso=NOW)
    assert down.calls == 1
    assert result[rids[0]] == "rejected:ldap_unavailable"
    assert [result[r] for r in rids[1:]] == ["deferred:ldap_circuit_open"] * 2
    assert [repos.requests.get(r)["state"] for r in rids[1:]] == ["Pending", "Pending"]
    # 다음 틱은 다시 LDAP 를 시도한다(LDAP 가 돌아오면 바로 이어진다).
    result = _planner(repos).run_once(now_iso=NOW)
    assert [result[r] for r in rids[1:]] == ["planned", "planned"]


# --- 보조 그룹(2026-10-07): 스냅숏 4키·D5·D7·이벤트·owner 자격 재확인·틱 예산 ------------------------------

class _GroupSettings(_Settings):
    identity_supplementary_groups = True


def _alice_with(gids, gid=10000):
    return ResolvedIdentity("alice", 10001, gid, ("dmsusers", "proj"), False, group_gids=tuple(gids))


def _plan_scan_with(repos, ident, *, settings=None):
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    result = Planner(repos, StubIdentityResolver({"alice": ident}),
                     settings=settings or _GroupSettings()).run_once(now_iso=NOW)
    return rid, result[rid]


def _snapshot(repos, rid):
    return repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]["identity"]


def _events(repos, rid, event_type):
    return [e for e in repos.observability.events_for_request(rid) if e["event_type"] == event_type]


def test_snapshot_records_supplementary_groups(db):
    repos = Repositories(db)
    rid, outcome = _plan_scan_with(repos, _alice_with((10000, 20002, 20001)))
    assert outcome == "planned"
    ident = _snapshot(repos, rid)
    # 4키 값과 **타입**(JSON 그대로의 list·str·int) -- stepper·매니페스트·포탈이 이 모양을 읽는다.
    assert ident["supplementary_gids"] == [20001, 20002] and type(ident["supplementary_gids"]) is list
    assert ident["supplementary_gids_status"] == "applied" and type(ident["supplementary_gids_status"]) is str
    assert ident["supplementary_gids_excluded"] == [] and type(ident["supplementary_gids_excluded"]) is list
    assert ident["supplementary_gids_found"] == 2 and type(ident["supplementary_gids_found"]) is int
    assert "group_gids" not in ident                  # 리졸버 원시값은 싣지 않는다
    assert (ident["uid"], ident["gid"], ident["groups"]) == (10001, 10000, ["dmsusers", "proj"])
    assert _events(repos, rid, "identity_groups_filtered") == []


def test_snapshot_without_groups_is_none(db):
    repos = Repositories(db)
    rid, _ = _plan_scan_with(repos, ALICE)
    ident = _snapshot(repos, rid)
    assert (ident["supplementary_gids"], ident["supplementary_gids_status"],
            ident["supplementary_gids_excluded"], ident["supplementary_gids_found"]) == ([], "none", [], 0)


def test_switch_off_records_disabled(db):
    # 설정 스텁에 스위치가 없으면 꺼짐(fail-closed) -- 실 Settings 의 기본은 켬(D9).
    repos = Repositories(db)
    rid, _ = _plan_scan_with(repos, _alice_with((20001,)), settings=_Settings())
    ident = _snapshot(repos, rid)
    assert ident["supplementary_gids"] == [] and ident["supplementary_gids_status"] == "disabled"
    assert ident["supplementary_gids_found"] is None   # 모름(보지 않았다) ≠ 0


def test_over_limit_records_status_and_one_event(db):
    from dms.identity import MAX_SUPPLEMENTARY_GROUPS
    repos = Repositories(db)
    rid, outcome = _plan_scan_with(repos, _alice_with(range(30000, 30000 + MAX_SUPPLEMENTARY_GROUPS + 1)))
    assert outcome == "planned"                        # D4: 거부가 아니라 통째로 미적용(오늘 동작)
    ident = _snapshot(repos, rid)
    assert ident["supplementary_gids"] == [] and ident["supplementary_gids_status"] == "over_limit"
    assert ident["supplementary_gids_found"] == MAX_SUPPLEMENTARY_GROUPS + 1
    events = _events(repos, rid, "identity_groups_filtered")
    assert len(events) == 1 and events[0]["severity"] == "info"
    assert events[0]["payload"] == {"status": "over_limit", "found": MAX_SUPPLEMENTARY_GROUPS + 1,
                                    "limit": MAX_SUPPLEMENTARY_GROUPS, "excluded": []}


def test_excluded_gids_recorded_with_event(db):
    repos = Repositories(db)
    rid, _ = _plan_scan_with(repos, _alice_with((20001, -1, 2147483648)))
    ident = _snapshot(repos, rid)
    assert ident["supplementary_gids"] == [20001] and ident["supplementary_gids_status"] == "applied"
    assert ident["supplementary_gids_excluded"] == [-1, 2147483648]
    events = _events(repos, rid, "identity_groups_filtered")
    assert len(events) == 1
    assert events[0]["payload"]["excluded"] == [-1, 2147483648]
    assert events[0]["payload"]["status"] == "applied"


def test_gid_zero_group_emits_root_group_warning(db):
    repos = Repositories(db)
    rid, outcome = _plan_scan_with(repos, _alice_with((0, 20001)))
    assert outcome == "planned"                        # D3: 인정(거부 안 함) -- 가시성만
    assert _snapshot(repos, rid)["supplementary_gids"] == [0, 20001]
    events = _events(repos, rid, "identity_groups_root_group")
    assert len(events) == 1 and events[0]["severity"] == "warning"
    assert events[0]["payload"] == {"gids": [0, 20001]}


def test_filtered_event_not_emitted_on_grace_ticks(db):
    from dms.identity import MAX_SUPPLEMENTARY_GROUPS
    many = _alice_with(range(30000, 30000 + MAX_SUPPLEMENTARY_GROUPS + 1))
    # (a) 신원 전파 유예(identity_propagating)
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_identity_pending_report(repos)
    rid = _scan_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")
    planner = Planner(repos, StubIdentityResolver({"alice": many}), settings=_GroupSettings())
    for _ in range(2):
        assert planner.run_once(now_iso=NOW)[rid] == "deferred:identity_propagating"
    assert _events(repos, rid, "identity_groups_filtered") == []
    _seed_report(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    assert len(_events(repos, rid, "identity_groups_filtered")) == 1
    # (b) 요청 노드 수 대기(awaiting_requested_nodes)
    rid2 = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice", resource_key="data.scan:s1:b:ff",
        payload={"storage": "s1", "target": "b", "options": {}, "owner_username": None, "node_count": 2},
        priority="mid")
    _backdate(db, rid2, "2026-08-02T09:58:00Z")
    _seed_identity_pending_report(repos, node="n2")
    assert planner.run_once(now_iso=NOW)[rid2] == "deferred:awaiting_requested_nodes"
    assert _events(repos, rid2, "identity_groups_filtered") == []
    _seed_report(repos, node="n2")
    assert planner.run_once(now_iso=NOW)[rid2] == "planned"
    assert len(_events(repos, rid2, "identity_groups_filtered")) == 1


def test_primary_gid_zero_rejected_at_plan(db):
    repos = Repositories(db)
    rid, outcome = _plan_scan_with(repos, _alice_with((20001,), gid=0))
    assert outcome == "rejected:identity_root_group_without_privilege"
    assert repos.data_jobs.list_jobs(request_id=rid) == []


# owner_username 자격 재확인(API 와 같은 술어) -- DB 직접 쓰기 방어

def _owner_request(repos, *, requester, owner, key="data.scan:s1:a:ff"):
    return repos.requests.create(
        operation="scan", requester_id=requester, actor=requester, resource_key=key,
        payload={"storage": "s1", "target": "a", "options": {}, "owner_username": owner},
        priority="mid", auth_method="session")


class _OpsSettings(_Settings):
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"ops"})


def test_owner_override_by_non_privileged_requester_rejected(db):
    # DB 직접 쓰기: bob 의 요청 payload 만 owner=alice -- alice 의 uid(이제 보조 그룹까지)를 얻지 못한다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    repos.accounts.create("bob", "pw", ROLE_USER)
    rid = _owner_request(repos, requester="bob", owner="alice")
    result = Planner(repos, StubIdentityResolver({"alice": ALICE}),
                     settings=_OpsSettings()).run_once(now_iso=NOW)
    assert result[rid] == "rejected:privileged_not_authorized"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert repos.data_jobs.list_jobs(request_id=rid) == []


def test_owner_override_by_eligible_admin_plans(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    repos.accounts.create("ops", "pw", "admin")
    rid = _owner_request(repos, requester="ops", owner="alice")
    result = Planner(repos, StubIdentityResolver({"alice": ALICE}),
                     settings=_OpsSettings()).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    assert _snapshot(repos, rid)["username"] == "alice"


@pytest.mark.parametrize("owner", ["", " alice", 123, ["x"], "bad name"])
def test_malformed_owner_rejected_invalid_owner_username(db, owner):
    # 예전: "" 는 resolve 가 '본인'으로 읽었고, 비문자열은 .strip() AttributeError 로 매 틱 plan_error·영구 Pending.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _owner_request(repos, requester="alice", owner=owner)
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "rejected:invalid_owner_username"


# D7 chown gid 멤버십(계획 시점)

def _sync_chown_request(repos, chown, *, run_as_root=False, requester="alice"):
    payload = {"source_storage": "src", "source": "a", "destination_storage": "dst", "destination": "b",
               "options": {"chown": chown}, "owner_username": None}
    if run_as_root:
        payload["run_as_root"] = True
    return repos.requests.create(operation="sync", requester_id=requester, actor=requester,
                                 resource_key="data.sync:src:a:dst:b:ff", payload=payload,
                                 priority="mid", auth_method="session")


def _seed_sync_ready(repos, user="alice"):
    _seed_sync_storages(repos)
    _seed_policy(repos, "dsync")
    _seed_sync_reports(repos, [{"username": user, "status": "Ready"}])


def test_chown_gid_not_member_rejected(db):
    repos = Repositories(db)
    _seed_sync_ready(repos)
    rid = _sync_chown_request(repos, "10001:30000")
    result = Planner(repos, StubIdentityResolver({"alice": _alice_with((20001,))}),
                     settings=_GroupSettings()).run_once(now_iso=NOW)
    assert result[rid] == "rejected:chown_group_not_member"
    assert repos.data_jobs.list_jobs(request_id=rid) == []


def test_chown_supplementary_gid_member_plans(db):
    repos = Repositories(db)
    _seed_sync_ready(repos)
    rid = _sync_chown_request(repos, "10001:20001")
    result = Planner(repos, StubIdentityResolver({"alice": _alice_with((20001,))}),
                     settings=_GroupSettings()).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    # 스위치가 꺼져 있으면 적용 목록이 비어 주 gid 만 -- 같은 gid 가 거부된다.
    rid2 = repos.requests.create(
        operation="sync", requester_id="alice", actor="alice", resource_key="data.sync:src:c:dst:d:ff",
        payload={"source_storage": "src", "source": "c", "destination_storage": "dst", "destination": "d",
                 "options": {"chown": ":20001"}, "owner_username": None},
        priority="mid", auth_method="session")
    result = Planner(repos, StubIdentityResolver({"alice": _alice_with((20001,))}),
                     settings=_Settings()).run_once(now_iso=NOW)
    assert result[rid2] == "rejected:chown_group_not_member"


def test_chown_check_skipped_for_root(db):
    repos = Repositories(db)
    _seed_sync_ready(repos, user="root")
    rid = _sync_chown_request(repos, "0:30000", run_as_root=True, requester="root")
    result = Planner(repos, StubIdentityResolver({}), settings=_PrivSettings()).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    assert _snapshot(repos, rid)["privileged"] is True


# 틱 LDAP 예산·IdentityLookupInvalid(서킷 비개방)

class _ClockResolver:
    """resolve 마다 가짜 monotonic 을 step 초 전진시키고 받은 deadline 을 기록한다."""
    def __init__(self, users, clock, step):
        self._inner = StubIdentityResolver(users)
        self._clock, self._step = clock, step
        self.calls = []

    def resolve(self, username, *, deadline=None):
        self.calls.append((username, deadline, self._clock[0]))
        self._clock[0] += self._step
        return self._inner.resolve(username)


def test_tick_budget_defers_remaining_requests(db):
    from dms.identity import LDAP_TICK_BUDGET_SECONDS
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rids = [_scan_request(repos, key=f"data.scan:s1:a{i}:ff") for i in range(2)]
    clock = [1000.0]
    slow = _ClockResolver({"alice": ALICE}, clock, step=LDAP_TICK_BUDGET_SECONDS + 1)
    result = Planner(repos, slow, settings=_Settings(),
                     monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert result[rids[0]] == "planned"
    assert result[rids[1]] == "deferred:ldap_circuit_open"    # 예산 소진 -- LDAP 를 부르지 않고 Pending
    assert len(slow.calls) == 1
    _, deadline, started = slow.calls[0]
    # 틱 첫 resolve 는 남은 몫 = 예산 = 자체 마감이라 deadline 을 넘기지 않는다(identity.tick_resolve_deadline -- 그
    # 마감의 중단은 진짜 판정). 남은 몫이 더 짧아진 뒤의 전달은 test_tick_budget_passes_remaining_as_deadline.
    assert deadline is None
    assert repos.requests.get(rids[1])["state"] == "Pending"
    # 다음 틱은 새 예산으로 이어간다.
    result = Planner(repos, StubIdentityResolver({"alice": ALICE}), settings=_Settings()).run_once(now_iso=NOW)
    assert result[rids[1]] == "planned"


def test_lookup_invalid_does_not_open_circuit(db):
    from dms.identity import IdentityLookupInvalid

    class _DupResolver:
        def __init__(self):
            self.calls = []

        def resolve(self, username, *, deadline=None):
            self.calls.append(username)
            if username == "dup":
                raise IdentityLookupInvalid("duplicate user entries: 2")
            return StubIdentityResolver({"alice": ALICE}).resolve(username)
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid_dup = _scan_request(repos, requester="dup", key="data.scan:s1:d:ff")
    rid_ok = _scan_request(repos, key="data.scan:s1:a:ff")
    resolver = _DupResolver()
    result = _planner(repos, resolver=resolver).run_once(now_iso=NOW)
    assert result[rid_dup] == "rejected:ldap_unavailable"     # 그 요청만(계획 시점 의미는 그대로)
    assert result[rid_ok] == "planned"                        # 같은 틱의 다른 사용자는 계속
    assert resolver.calls == ["dup", "alice"]


def test_lookup_invalid_cause_goes_to_stderr_only(db, capsys):
    # G6(2026-10-08 리뷰): 같은 ldap_unavailable 로 접히는 원인(중복 엔트리·결과 코드·페이지 상한·URI 오류)을 운영자가
    # 가를 근거 -- stderr 한 줄. LDAP 원문은 요청 결과·이벤트에 싣지 않는다(비관리자 요청자에게도 반환된다).
    from dms.identity import IdentityLookupInvalid

    class _DupResolver:
        def resolve(self, username, *, deadline=None):
            raise IdentityLookupInvalid("duplicate user entries: 2")
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid_dup = _scan_request(repos, key="data.scan:s1:d:ff")
    assert _planner(repos, resolver=_DupResolver()).run_once(now_iso=NOW)[rid_dup] == "rejected:ldap_unavailable"
    err = capsys.readouterr().err
    assert f"planner: {rid_dup} rejected ldap_unavailable: duplicate user entries: 2" in err
    events = repos.observability.events_for_request(rid_dup)
    assert all("duplicate" not in (e["message"] or "") and "duplicate" not in str(e["payload"]) for e in events)


# ---- G0/G3/G4(2026-10-08 리뷰): 틱 예산 경계 -- 실 LdapIdentityResolver + 실 connect_first + 가짜 시계 ----
# 예전 _ClockResolver 는 deadline 을 무시하는 페이크라, 예산에 '걸쳐 넘는' resolve 가 plain IdentityUnavailable 로
# 종단 거부되던 경로를 보지 못했다.

class _LAttr:
    def __init__(self, v):
        self.value = v


class _LEntry:
    def __init__(self, attrs):
        self._a, self.entry_dn = attrs, "uid=alice,ou=p,dc=x"

    def __getitem__(self, k):
        return _LAttr(self._a[k])


class _HealthyConn:
    """건강한 LDAP: 검색마다 clock 을 search_cost 초 전진(사용자 alice 1건, posixGroup 1건)."""
    def __init__(self, clock, search_cost):
        self._clock, self._cost = clock, search_cost
        self.entries, self.result = [], {"result": 0}

    def search(self, base, flt, attributes=None, paged_size=None, paged_cookie=None):
        self._clock[0] += self._cost
        if flt.startswith("(uid="):
            self.entries = [_LEntry({"uidNumber": 10001, "gidNumber": 10000})]
        else:
            self.entries = [_LEntry({"cn": "dmsusers", "gidNumber": 20000, "objectClass": ["posixGroup"]})]
        self.result = {"result": 0}

    def unbind(self):
        pass


def _real_ldap(clock, uris, *, dead=(), connect_cost=0.05, search_cost=0.05, dead_cost=5.0, rotation=None,
               tried=None):
    from dms.identity_ldap import LdapIdentityResolver, connect_first
    tried = [] if tried is None else tried

    def open_one(uri):
        tried.append(uri)
        if uri in dead:
            clock[0] += dead_cost                 # 타임아웃형 장애(방화벽 drop): connect_timeout 만큼 기다린 뒤 실패
            raise OSError("timed out")
        clock[0] += connect_cost
        return _HealthyConn(clock, search_cost)
    return LdapIdentityResolver(
        connect=lambda deadline=None: connect_first(uris, open_one, deadline=deadline,
                                                    monotonic=lambda: clock[0], rotation=rotation),
        user_base="ou=p", group_base="ou=g", group_member_attr="memberUid", monotonic=lambda: clock[0])


def _states(repos, rids):
    return [repos.requests.get(r)["state"] for r in rids]


def test_budget_straddle_on_healthy_ldap_stays_pending(db):
    # G0: 건강하지만 느린 LDAP(resolve 1.1s) + 대기 12건 -- 9건 계획 뒤 10번째는 남은 몫 0.1s 로 시작해 그 마감에 걸린다.
    # 예전엔 rejected:ldap_unavailable(정상 요청의 종단 거부), 이제 Pending 으로 다음 틱.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rids = [_scan_request(repos, key=f"data.scan:s1:a{i}:ff") for i in range(12)]
    clock = [1000.0]
    res = _real_ldap(clock, ["ldap://b"], connect_cost=0.3, search_cost=0.4)
    result = Planner(repos, res, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert [result[r] for r in rids[:9]] == ["planned"] * 9
    assert [result[r] for r in rids[9:]] == ["deferred:ldap_circuit_open"] * 3
    assert "Rejected" not in _states(repos, rids)
    # 다음 틱은 온전한 예산으로 이어 간다.
    result = Planner(repos, res, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert [result[r] for r in rids[9:]] == ["planned"] * 3


def test_budget_straddle_many_requests_never_rejects(db):
    # G3: 40건 × 0.4s -- 예전엔 틱마다 정확히 한 건이 ldap_unavailable 로 종단 거부됐다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rids = [_scan_request(repos, key=f"data.scan:s1:m{i}:ff") for i in range(40)]
    clock = [1000.0]
    res = _real_ldap(clock, ["ldap://b"], connect_cost=0.3, search_cost=0.05)
    for _ in range(3):
        Planner(repos, res, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert set(_states(repos, rids)) == {"Planned"}


def test_dead_primary_uri_with_healthy_secondary_never_rejects(db):
    # G3/G4 페일오버 형상: p1 이 타임아웃형으로 죽고(5s) r3 는 정상, 대기 3건. 예전 ['Planned','Rejected','Pending'].
    # (a) 시작 URI 기억(운영 형상 -- build_ldap_resolver): 첫 resolve 가 r3 에 붙은 뒤로는 p1 을 다시 기다리지 않는다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rids = [_scan_request(repos, key=f"data.scan:s1:f{i}:ff") for i in range(3)]
    clock, tried = [1000.0], []
    res = _real_ldap(clock, ["ldap://p1", "ldap://r3"], dead={"ldap://p1"}, rotation={"start": 0}, tried=tried)
    result = Planner(repos, res, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert [result[r] for r in rids] == ["planned"] * 3
    assert tried == ["ldap://p1", "ldap://r3", "ldap://r3", "ldap://r3"]


def test_dead_primary_without_rotation_defers_instead_of_rejecting(db):
    # G3 (b) 기억 없이(매 resolve 가 p1 부터): 두 번째 요청은 남은 몫 ~4.8s 로 p1 의 5s 타임아웃에 걸려 r3 를 못 가
    # 본다 -- 호출자 마감의 중단이라 Pending(다음 틱 계획), 거부가 아니다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rids = [_scan_request(repos, key=f"data.scan:s1:g{i}:ff") for i in range(3)]
    clock = [1000.0]
    res = _real_ldap(clock, ["ldap://p1", "ldap://r3"], dead={"ldap://p1"})
    result = Planner(repos, res, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert [result[r] for r in rids] == ["planned", "deferred:ldap_circuit_open", "deferred:ldap_circuit_open"]
    result = Planner(repos, res, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert result[rids[1]] == "planned"
    for _ in range(2):
        Planner(repos, res, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert _states(repos, rids) == ["Planned"] * 3


def test_first_resolve_of_tick_hitting_own_deadline_is_still_rejected(db):
    # 진행 보장: 틱 첫 resolve 는 deadline 없이(자체 마감 10s) 돌아 그 마감의 중단은 진짜 판정 -- 느린 LDAP(검색 11s)
    # 에서 첫 요청은 ldap_unavailable 로 거부되고 나머지는 서킷으로 Pending(예산 소진으로 영구 Pending 이 되지 않는다).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rids = [_scan_request(repos, key=f"data.scan:s1:h{i}:ff") for i in range(3)]
    clock = [1000.0]
    res = _real_ldap(clock, ["ldap://b"], search_cost=11.0)
    result = Planner(repos, res, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert result[rids[0]] == "rejected:ldap_unavailable"
    assert [result[r] for r in rids[1:]] == ["deferred:ldap_circuit_open"] * 2


def test_tick_budget_passes_remaining_as_deadline(db):
    # 남은 몫이 자체 마감보다 짧아진 뒤의 resolve 는 그 몫을 deadline 으로 받는다(첫 resolve 는 None).
    from dms.identity import LDAP_TICK_BUDGET_SECONDS
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rids = [_scan_request(repos, key=f"data.scan:s1:k{i}:ff") for i in range(2)]
    clock = [1000.0]
    slow = _ClockResolver({"alice": ALICE}, clock, step=3.0)
    Planner(repos, slow, settings=_Settings(), monotonic=lambda: clock[0]).run_once(now_iso=NOW)
    assert [d for _u, d, _t in slow.calls] == [None, 1000.0 + LDAP_TICK_BUDGET_SECONDS]
    assert _states(repos, rids) == ["Planned", "Planned"]
