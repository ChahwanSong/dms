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
