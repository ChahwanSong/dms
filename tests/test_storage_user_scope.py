"""스토리지 사용 범위(2026-09-30 사용자 요청): 전체 / 관리자 전용("사용자에게만 비활성") /
완전 비활성. 관리자 전용은 비관리자 피커에서 빠지는 것(표시)만이 아니라 제출 게이트·planner·
스캔 경로 등록이 모두 막는다(repositories.storages.storage_open_to_users 가 단일 정의).
"""
import pytest

from dms.db import Database
from dms.migrations import migrate
from dms.planner import Planner
from dms.repositories import Repositories
from dms.repositories.storages import storage_open_to_users
from dms.identity import ResolvedIdentity, StubIdentityResolver

ADMIN = {"Authorization": "Bearer tok-shared"}
BODY = {"storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}
PUT = {"mount_path": "/mnt/ceph", "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}


def _login_user(client, name="u1"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _admin_only(client):
    assert client.post("/api/admin/storages", json=BODY, headers=ADMIN).status_code == 201
    r = client.put("/api/admin/storages/ceph-a", json={**PUT, "enabled": True,
                                                       "user_enabled": False}, headers=ADMIN)
    assert r.status_code == 200 and (r.json()["enabled"], r.json()["user_enabled"]) == (1, 0)


# --- 정의 ------------------------------------------------------------------------

@pytest.mark.parametrize("row, expected", [
    ({"enabled": 1, "user_enabled": 1}, True),
    ({"enabled": 1, "user_enabled": 0}, False),      # 관리자 전용
    ({"enabled": 0, "user_enabled": 1}, False),      # 완전 비활성
    ({"enabled": 1, "user_enabled": None}, True),    # 컬럼 이전 행 = 종전 동작
    ({"enabled": 1}, True),
])
def test_open_to_users_definition(row, expected):
    assert storage_open_to_users(row) is expected


# --- 저장소·마이그레이션 ---------------------------------------------------------------

def test_update_without_user_enabled_keeps_the_admin_only_setting(db):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/m", managed_root="/m/d",
                          backend_type="cephfs", actor="a", user_enabled=False)
    assert repos.storages.get("s1")["user_enabled"] == 0
    # 옛 클라이언트의 PUT(필드 모름) = None → 유지. 조용히 사용자에게 다시 열리면 안 된다.
    repos.storages.update("s1", mount_path="/m", managed_root="/m/d2", backend_type="cephfs",
                          enabled=True, actor="a")
    assert repos.storages.get("s1")["user_enabled"] == 0
    repos.storages.update("s1", mount_path="/m", managed_root="/m/d2", backend_type="cephfs",
                          enabled=True, user_enabled=True, actor="a")
    assert repos.storages.get("s1")["user_enabled"] == 1


def test_migrate_backfills_existing_rows_to_open_and_never_touches_admin_only(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/old.db")
    # 기배포 DB 흉내: user_enabled 컬럼이 없는 storages.
    db.execute("""CREATE TABLE storages (storage_name TEXT PRIMARY KEY, mount_path TEXT NOT NULL,
                  managed_root TEXT NOT NULL, backend_type TEXT NOT NULL,
                  enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'Unknown',
                  status_detail TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                  updated_by TEXT NOT NULL)""")
    db.execute("""INSERT INTO storages (storage_name, mount_path, managed_root, backend_type,
                  created_at, updated_at, updated_by) VALUES ('old', '/m', '/m', 'cephfs',
                  't', 't', 'a')""")
    migrate(db)
    assert db.query_one("SELECT user_enabled FROM storages WHERE storage_name='old'")[
        "user_enabled"] == 1
    db.execute("UPDATE storages SET user_enabled = 0 WHERE storage_name = 'old'")
    migrate(db)                                    # 멱등: 관리자가 정한 0 은 그대로
    assert db.query_one("SELECT user_enabled FROM storages WHERE storage_name='old'")[
        "user_enabled"] == 0


# --- API ---------------------------------------------------------------------------

def test_create_can_start_admin_only(client):
    r = client.post("/api/admin/storages", json={**BODY, "user_enabled": False}, headers=ADMIN)
    assert r.status_code == 201 and (r.json()["enabled"], r.json()["user_enabled"]) == (1, 0)


def test_put_without_the_field_keeps_admin_only(client):
    _admin_only(client)
    r = client.put("/api/admin/storages/ceph-a", json={**PUT, "enabled": True}, headers=ADMIN)
    assert r.json()["user_enabled"] == 0


def test_user_picker_hides_admin_only_and_admin_sees_the_mark(client):
    _admin_only(client)
    admin_rows = client.get("/api/user/storages", headers=ADMIN).json()
    assert [(r["storage_name"], r["admin_only"]) for r in admin_rows] == [("ceph-a", True)]
    _login_user(client)
    assert client.get("/api/user/storages").json() == []


SYNC = {"operation": "sync", "source_storage": "ceph-a", "source": "a",
        "destination_storage": "ceph-a", "destination": "b"}


def test_user_submit_to_admin_only_storage_is_refused(client):
    _admin_only(client)
    _login_user(client)
    r = client.post("/api/user/requests", json=SYNC)
    assert r.status_code == 403 and r.json()["detail"] == "storage_admin_only"


def test_admin_submit_to_admin_only_storage_is_accepted(client):
    _admin_only(client)
    assert client.post("/api/user/requests", json={**SYNC, "run_as_root": False},
                       headers=ADMIN).status_code == 202


def test_fully_disabled_storage_keeps_the_planner_rejection_path(client):
    # 완전 비활성은 종전대로 제출은 받고 planner 가 storage_disabled 로 거부한다(제출 게이트는
    # 관리자 전용만 본다 -- 두 사유가 섞이지 않게).
    assert client.post("/api/admin/storages", json=BODY, headers=ADMIN).status_code == 201
    client.put("/api/admin/storages/ceph-a", json={**PUT, "enabled": False,
                                                   "user_enabled": False}, headers=ADMIN)
    client.app.state.repos.sync_pairs.add("ceph-a", "ceph-a", actor="test")   # 쌍 게이트는 통과
    _login_user(client)
    assert client.post("/api/user/requests", json=SYNC).status_code == 202


def test_user_cannot_register_a_scan_path_on_an_admin_only_storage(client):
    _admin_only(client)
    _login_user(client)
    r = client.post("/api/user/scan-paths", json={"storage_name": "ceph-a", "path": "x"})
    assert r.status_code == 422 and r.json()["detail"] == "storage_missing"


# --- planner(계획 시점 재확인) ---------------------------------------------------------

class _Settings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    # shared-token: _plan 의 요청은 실행 신원 alice 를 지정한다 -- 다른 실행 신원 지정은 planner 도 API 와 같은
    # 술어(owner_override_allowed, 2026-10-07)로 다시 보므로, 토큰 요청이 그렇게 할 수 있는 형상(목록에 있음)으로 둔다.
    privileged_requesters = frozenset({"ops", "shared-token"})
    planner_identity_grace_seconds = 300


ALICE = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)


def _plan(db, *, requester, role, auth="session", batch_id=None):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/dms",
                          backend_type="cephfs", actor="a", user_enabled=False)
    repos.storages.set_status("s1", "Ready", "ready_nodes=1")
    repos.control.upsert_policy("scan", max_nodes=3, procs_per_node=8, queue="dms-data",
                                default_priority="mid", max_priority="high",
                                preview_timeout_seconds=3600, execution_timeout_seconds=3600,
                                enabled=True, actor="admin")
    repos.agents.ingest("n1", {
        "node_name": "n1",
        "mounts": [{"storage_name": "s1", "mount_path": "/mnt/s1", "status": "Ready",
                    "writable": True}],
        "tools": [{"name": t, "status": "Ready"} for t in ("dscan", "dsync", "nsync", "drm")],
        "identities": [{"username": "alice", "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")
    if role is not None:
        repos.accounts.create(requester, "pw", role)
    kw = {"batch_id": batch_id} if batch_id else {}
    rid = repos.requests.create(operation="scan", requester_id=requester, actor=requester,
                                resource_key=f"k-{requester}",
                                payload={"storage": "s1", "target": "a", "options": {},
                                         "owner_username": "alice"},
                                priority="mid", auth_method=auth, **kw)
    Planner(repos, StubIdentityResolver({"alice": ALICE}),
            settings=_Settings()).run_once(now_iso="2026-08-02T10:00:00Z")
    return repos.requests.get(rid)["state"], repos.requests.last_reason_code(rid)


def test_planner_rejects_a_user_request_on_an_admin_only_storage(db):
    assert _plan(db, requester="alice", role="user") == ("Rejected", "storage_admin_only")


def test_planner_rejects_when_the_requester_has_no_account_row(db):
    assert _plan(db, requester="ghost", role=None) == ("Rejected", "storage_admin_only")


def test_planner_lets_an_admin_request_through(db):
    state, reason = _plan(db, requester="ops", role="admin")
    assert reason != "storage_admin_only" and state == "Planned"


def test_planner_token_admin_only_for_the_shapes_the_api_creates(db):
    # 토큰 요청의 실제 모양(shared-token)은 관리자 -- 통과. "token" 표식 + 사람 이름(API 가 만들
    # 수 없는 조합, DB 직접 삽입)은 표식만으로 통과시키지 않는다(리뷰 발견).
    state, reason = _plan(db, requester="shared-token", role=None, auth="token")
    assert reason != "storage_admin_only" and state == "Planned"


def test_planner_does_not_trust_the_token_mark_on_a_human_requester(db):
    assert _plan(db, requester="alice", role="user", auth="token") == ("Rejected", "storage_admin_only")


def test_batch_children_follow_the_creators_current_role_for_admin_only_storages(db):
    # 관리자 전용 판정은 계정 역할 기준이다 -- 배치 자식이라는 사실만으로 면제하지 않는다(생성자가
    # 강등된 배치는 거부). sync 허용 쌍의 배치 면제(planner pair_exempt)가 이 판정으로 번지면 안 된다.
    assert _plan(db, requester="demoted", role="user", batch_id="b-1") == ("Rejected", "storage_admin_only")


def test_batch_children_of_a_current_admin_pass_the_admin_only_check(db):
    state, reason = _plan(db, requester="ops", role="admin", batch_id="b-2")
    assert reason != "storage_admin_only" and state == "Planned"


def test_submit_gate_checks_the_destination_storage_too(client):
    # 소스는 공개, 목적지만 관리자 전용 -- 게이트가 목적지를 빠뜨리면 이 요청이 통과한다.
    assert client.post("/api/admin/storages", json=BODY, headers=ADMIN).status_code == 201
    assert client.post("/api/admin/storages", json={
        **BODY, "storage_name": "adm-dst", "user_enabled": False}, headers=ADMIN).status_code == 201
    _login_user(client)
    r = client.post("/api/user/requests", json={**SYNC, "destination_storage": "adm-dst"})
    assert r.status_code == 403 and r.json()["detail"] == "storage_admin_only"


def test_user_cannot_confirm_a_job_whose_storage_became_admin_only(client):
    # 미리보기까지 끝난(ConfirmPending) 사용자 잡 -- 그 사이 관리자 전용이 되면 컨펌(실행 시작)을
    # 막는다. 관리자는 컨펌할 수 있다(리뷰 발견: 제출·계획 게이트만으로는 TTL 동안 샌다).
    from dms.domain import DataJobState, RequestState
    assert client.post("/api/admin/storages", json=BODY, headers=ADMIN).status_code == 201
    _login_user(client, "alice")
    repos = client.app.state.repos
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k-conf", payload={"source_storage": "ceph-a", "source": "a",
        "destination_storage": "ceph-a", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="ceph-a", source="a", destination_storage="ceph-a", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
        expires_at="2099-01-01T00:00:00Z", artifact_uri="file:///art/j")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    client.put("/api/admin/storages/ceph-a", json={**PUT, "enabled": True, "user_enabled": False},
               headers=ADMIN)
    r = client.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": "sha256:abc"})
    assert r.status_code == 403 and r.json()["detail"] == "storage_admin_only"
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"      # 상태 불변
    r = client.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": "sha256:abc"},
                    headers=ADMIN)
    assert r.status_code == 200 and r.json()["state"] == "Executing"
