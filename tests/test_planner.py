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
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
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
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
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
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
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
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
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
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
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
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
    _seed_policy(repos, "nsync")
    _seed_unmounted_report(repos, "n1", tool="nsync")
    _seed_unmounted_report(repos, "n2", tool="nsync")
    rid = _sync_request(repos)
    _backdate(db, rid, "2026-08-02T09:58:00Z")        # grace 안이어도
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_ready_sync_candidate"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_sync_identity_grace_expired_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")
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
