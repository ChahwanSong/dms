"""사용자 sync 허용 스토리지 쌍(2026-09-30 사용자 결정: "기본 전부 불가에 허용 쌍을 추가").

방향이 있는 (소스 -> 목적지) 쌍만 비관리자 sync 를 허용한다. 관리자·배치는 제한 없음. 강제 지점은
제출·계획·컨펌 세 곳(repositories/sync_pairs.sync_pair_allowed 하나)이고, 포탈 후보 필터는 표시다.

k8s 신원 모드 전용(2026-10-26 LDAP 코드 삭제) 뒤로: 비면제 사용자는 쪽마다 PV 주소여야 하고(직접 경로는 raw_path_not_allowed
-- 쌍 게이트보다 앞) PV↔PV sync 는 쌍 면제(k8s_access.pair_gate_exempt, 설계 D14)라 사용자 제출이 쌍 게이트에 닿지 않는다.
저장소·관리 API·조회와 관리자·배치 면제는 그대로 고정하고, 게이트 순서는 아래 사용자 테스트가 고정한다.
"""
import pytest

from dms.domain import DomainValidationError
from dms.planner import Planner
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared"}


def _storage(client, name, **extra):
    body = {"storage_name": name, "mount_path": f"/mnt/{name}", "managed_root": f"/mnt/{name}/dms",
            "backend_type": "cephfs", **extra}
    assert client.post("/api/admin/storages", json=body, headers=ADMIN).status_code == 201


def _login_user(client, name="u1"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _login_ops(client):
    # 특권 세션 관리자(기본 특권 목록의 admin) -- 직접 경로 제출은 면제자만이다.
    client.app.state.repos.accounts.create("admin", "p", "admin")
    client.post("/api/auth/login", json={"username": "admin", "password": "p"})


def _sync(src, dst):
    return {"operation": "sync", "source_storage": src, "source": "a",
            "destination_storage": dst, "destination": "b"}


# --- 저장소 ----------------------------------------------------------------------

def _storages(repos, *names):
    for n in names:
        repos.storages.create(storage_name=n, mount_path=f"/m/{n}", managed_root=f"/m/{n}",
                              backend_type="cephfs", actor="ops")


def test_add_is_idempotent_and_audited_remove_reports_absence(db):
    repos = Repositories(db)
    _storages(repos, "a", "b")
    row, created = repos.sync_pairs.add("a", "b", actor="ops")
    assert created and (row["source_storage"], row["destination_storage"], row["created_by"]) == ("a", "b", "ops")
    _, created_again = repos.sync_pairs.add("a", "b", actor="ops")
    assert created_again is False                                   # 멱등(감사 기록 없음)
    assert repos.sync_pairs.remove("a", "b", actor="ops") is True
    assert repos.sync_pairs.remove("a", "b", actor="ops") is False
    ops = [(r["operation"], r["target_key"]) for r in db.query(
        "SELECT operation, target_key FROM audit_log WHERE mutation_class = 'sync_pair' ORDER BY id")]
    assert ops == [("add", "a->b"), ("remove", "a->b")]


def test_add_refuses_unregistered_storages_inside_its_transaction(db):
    # 존재 확인이 삽입과 같은 트랜잭션이다(리뷰 발견: 라우트에서 먼저 확인하면 그 사이 끝난 스토리지
    # 삭제의 쌍 정리를 비껴간 쌍이 남아, 같은 이름 재등록이 옛 허용을 물려받는다).
    repos = Repositories(db)
    _storages(repos, "a")
    for src, dst in (("a", "zz"), ("zz", "a")):
        with pytest.raises(DomainValidationError) as e:
            repos.sync_pairs.add(src, dst, actor="ops")
        assert e.value.reason_code == "storage_missing"
    assert repos.sync_pairs.list() == []
    assert db.query("SELECT 1 AS one FROM audit_log WHERE mutation_class = 'sync_pair'") == []


def test_deleting_a_storage_removes_its_pairs_in_the_same_transaction(db):
    # 같은 이름으로 다시 등록된 스토리지가 옛 허용을 물려받지 않는다.
    repos = Repositories(db)
    _storages(repos, "a", "b", "c")
    repos.sync_pairs.add("a", "b", actor="ops"); repos.sync_pairs.add("b", "a", actor="ops")
    repos.sync_pairs.add("c", "c", actor="ops")
    repos.storages.delete("a", actor="ops")
    assert [(p["source_storage"], p["destination_storage"]) for p in repos.sync_pairs.list()] == [("c", "c")]
    removed = db.query("SELECT target_key FROM audit_log WHERE mutation_class = 'sync_pair' "
                       "AND operation = 'remove' ORDER BY target_key")
    assert [r["target_key"] for r in removed] == ["a->b", "b->a"]


# --- 관리자 API ----------------------------------------------------------------------

def test_admin_crud_and_validation(client):
    _storage(client, "a"); _storage(client, "b")
    r = client.post("/api/admin/sync-pairs", json={"source_storage": "a", "destination_storage": "b"},
                    headers=ADMIN)
    assert r.status_code == 201 and r.json()["source_storage"] == "a"
    assert client.post("/api/admin/sync-pairs", json={"source_storage": "a", "destination_storage": "b"},
                       headers=ADMIN).status_code == 201                # 멱등
    r = client.post("/api/admin/sync-pairs", json={"source_storage": "a", "destination_storage": "zz"},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "storage_missing"
    rows = client.get("/api/admin/sync-pairs", headers=ADMIN).json()
    assert [(p["source_storage"], p["destination_storage"]) for p in rows] == [("a", "b")]
    assert client.delete("/api/admin/sync-pairs/a/b", headers=ADMIN).json()["deleted"] is True
    r = client.delete("/api/admin/sync-pairs/a/b", headers=ADMIN)
    assert r.status_code == 404 and r.json()["detail"] == "sync_pair_not_found"


def test_admin_api_requires_admin(client):
    assert client.get("/api/admin/sync-pairs").status_code == 401
    _login_user(client)
    assert client.get("/api/admin/sync-pairs").status_code == 403
    assert client.post("/api/admin/sync-pairs",
                       json={"source_storage": "a", "destination_storage": "b"}).status_code == 403


# --- 사용자 조회 ----------------------------------------------------------------------

def test_user_sees_only_pairs_between_storages_they_can_pick(client):
    _storage(client, "a"); _storage(client, "b"); _storage(client, "adm", user_enabled=False)
    _storage(client, "off")
    client.put("/api/admin/storages/off", json={"mount_path": "/mnt/off", "managed_root": "/mnt/off/dms",
                                                "backend_type": "cephfs", "enabled": False}, headers=ADMIN)
    for s, d in (("a", "b"), ("a", "adm"), ("off", "a"), ("b", "b")):
        client.post("/api/admin/sync-pairs", json={"source_storage": s, "destination_storage": d},
                    headers=ADMIN)
    assert client.get("/api/user/sync-pairs", headers=ADMIN).json() == {"restricted": False, "pairs": []}
    _login_user(client)
    body = client.get("/api/user/sync-pairs").json()
    assert body["restricted"] is True
    assert sorted((p["source_storage"], p["destination_storage"]) for p in body["pairs"]) == [
        ("a", "b"), ("b", "b")]                       # 관리자 전용·비활성이 낀 쌍은 없다


# --- 제출 게이트 ------------------------------------------------------------------------

def test_user_raw_sync_is_refused_before_the_pair_gate(client):
    # 허용 쌍이 있어도 사용자 직접 경로 sync 는 맵핑 게이트(raw_path_not_allowed)가 먼저 끊는다 -- 쌍은 사용자 제출을 열지 못한다.
    _storage(client, "a"); _storage(client, "b")
    _login_user(client)
    client.app.state.repos.sync_pairs.add("a", "b", actor="ops")
    r = client.post("/api/user/requests", json=_sync("a", "b"))
    assert r.status_code == 403 and r.json()["detail"] == "raw_path_not_allowed"


def test_admin_sync_is_not_restricted_by_pairs(client):
    _storage(client, "a"); _storage(client, "b")
    _login_ops(client)
    assert client.post("/api/user/requests", json=_sync("a", "b")).status_code == 202


def test_missing_storage_field_still_gets_the_precise_validation_error(client):
    _login_ops(client)
    body = {"operation": "sync", "source_storage": "a", "source": "x", "destination": "y"}
    r = client.post("/api/user/requests", json=body)
    assert r.status_code == 422 and r.json()["detail"] == "missing_destination_storage"


# --- planner(계획 시점 재확인) ------------------------------------------------------------

class _Settings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"ops"})


def _plan_sync(db, *, requester, role, batch_id=None, allow=False, owner="alice"):
    repos = Repositories(db)
    for n in ("src", "dst"):
        repos.storages.create(storage_name=n, mount_path=f"/mnt/{n}", managed_root=f"/mnt/{n}/dms",
                              backend_type="cephfs", actor="a")
        repos.storages.set_status(n, "Ready", "ready_nodes=1")
    if allow:
        repos.sync_pairs.add("src", "dst", actor="ops")
    for tool in ("dsync", "nsync"):
        repos.control.upsert_policy(tool, max_nodes=3, procs_per_node=8, queue="dms-data",
                                    default_priority="mid", max_priority="high",
                                    preview_timeout_seconds=3600, execution_timeout_seconds=3600,
                                    enabled=True, actor="admin")
    repos.agents.ingest("n1", {
        "node_name": "n1",
        "mounts": [{"storage_name": n, "mount_path": f"/mnt/{n}", "status": "Ready", "writable": True}
                   for n in ("src", "dst")],
        "tools": [{"name": t, "status": "Ready"} for t in ("dscan", "dsync", "nsync", "drm")]},
        reported_at="2026-08-02T09:59:00Z")
    if role is not None:
        repos.accounts.create(requester, "pw", role)
    kw = {"batch_id": batch_id} if batch_id else {}
    payload = {"source_storage": "src", "source": "a", "destination_storage": "dst", "destination": "b",
               "options": {}, "owner_username": owner}
    if not batch_id:
        payload["run_as_root"] = True               # 단건은 제출이 늘 싣는다(설계 D2) -- 배치 자식은 IF_ELIGIBLE
    rid = repos.requests.create(operation="sync", requester_id=requester, actor=requester,
                                resource_key=f"k-{requester}", payload=payload,
                                priority="mid", auth_method="session", **kw)
    Planner(repos, settings=_Settings()).run_once(now_iso="2026-08-02T10:00:00Z")
    return repos.requests.get(rid)["state"], repos.requests.last_reason_code(rid)


def test_planner_refuses_a_user_raw_sync_before_the_pair_gate(db):
    # 대기 중 DB 직접 삽입된 사용자 직접 경로 sync -- 허용 쌍이 있어도 재확인(raw_path_not_allowed)이 먼저다.
    assert _plan_sync(db, requester="alice", role="user", allow=True) == ("Rejected", "raw_path_not_allowed")


def test_planner_does_not_restrict_admins_or_batch_children(db):
    assert _plan_sync(db, requester="ops", role="admin")[0] == "Planned"


def test_planner_treats_batch_children_as_admin_requests(db):
    # 배치는 관리자 전용 라우트에서만 생긴다 -- 생성자 계정 행이 없어도 사용자 규칙(허용 쌍)에 걸리지 않는다.
    # 실행 신원이 생성자 자신이면(owner 생략) 그대로 계획된다(목록에 있는 생성자 -- IF_ELIGIBLE root).
    assert _plan_sync(db, requester="ops", role=None, batch_id="b-1", owner=None)[0] == "Planned"


def test_planner_batch_child_owner_override_needs_a_current_admin_creator(db):
    # 쌍 게이트는 면제로 통과하지만(sync_pair_not_allowed 가 아니다), 다른 실행 신원 지정은 API 와 같은 술어로
    # 다시 본다(2026-10-07) -- 계정 행이 없는(삭제·강등된) 생성자의 자식은 alice 로 돌지 못한다.
    assert _plan_sync(db, requester="gone-admin", role=None, batch_id="b-1") ==         ("Rejected", "privileged_not_authorized")


# (컨펌 게이트의 쌍 재확인은 사용자 PV↔PV 잡이 면제라 사용자 흐름으로 닿지 않는다 -- 컨펌의 맵핑 재확인은
#  tests/test_k8s_submit.py 의 test_confirm_* 가 고정한다.)
