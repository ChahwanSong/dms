"""root 실행 자격 부여(2026-10-12, privileged.py · routes_accounts.set_root_grant).

사용자 보고: 「관리 -> 계정 에서 일반 사용자명에게 admin 역할을 줬어. 근데 root로 실행 체크 옵션이 작업 등록할때 안뜨네」 --
자격 = 관리자 + 세션 + 특권 목록(env DMS_PRIVILEGED_REQUESTERS)이라 역할만으로는 생기지 않는다(목록은 관리자 사이의 경계).
경계를 지키며 재배포 없이 넓히는 길: **특권 세션 관리자**가 계정 화면에서 활성 admin 계정에 자격을 준다(DB). 여기서
고정하는 것: 누가 줄 수 있나(특권 세션 관리자만), 누구에게(admin 역할, env 목록 이름·자기 자신 제외), 효력이 닿는 모든
판정(me·제출·실행 신원 지정·배치 생성·planner·자동 확인), 부여된 계정의 보호(특권 관리자만 관리·셀프 재설정 불가),
강등·비활성화·삭제 뒤 효력, 감사."""
import pytest
from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.config import Settings
from dms.planner import Planner
from dms.privileged import privileged_names
from dms.repositories import Repositories

TOKEN = {"Authorization": "Bearer tok-shared"}
SYNC = {"operation": "sync", "source_storage": "s1", "source": "src",
        "destination_storage": "s1", "destination": "dst"}


def _app(db, **overrides):
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess",
            "DMS_ACCOUNT_VERIFICATION_REQUIRED": "false", "DMS_PASSWORD_ENCRYPTION_REQUIRED": "false",
            "DMS_ALLOW_PRIVILEGED_REQUESTERS": "true", "DMS_PRIVILEGED_REQUESTERS": "ops", **overrides}
    app = create_app(Settings.from_env(base), db)
    repos = app.state.repos
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/dms",
                          backend_type="cephfs", actor="test")
    repos.sync_pairs.add("s1", "s1", actor="test")
    for name, role in (("ops", "admin"), ("ops2", "admin"), ("newadm", "admin"), ("plain", "user")):
        repos.accounts.create(name, "p", role)
    return app


def _as(app, name):
    """세션 로그인 클라이언트(계정마다 따로 -- 쿠키가 섞이지 않게)."""
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": name, "password": "p"}).status_code == 200
    return c


def _grant(c, who, granted=True):
    return c.put(f"/api/admin/accounts/{who}/root", json={"granted": granted})


def _me(app, name):
    return _as(app, name).get("/api/auth/me").json()["can_run_as_root"]


# ---- 사용자 보고 그대로 ----

def test_promoted_admin_has_no_root_until_a_privileged_admin_grants_it(db):
    app = _app(db)
    assert _me(app, "newadm") is False                         # 역할만 admin -- 보고된 증상(설계대로)
    r = _grant(_as(app, "ops"), "newadm")
    assert r.status_code == 200, r.text
    assert (r.json()["root_source"], r.json()["root_eligible"]) == ("granted", True)
    assert _me(app, "newadm") is True
    c = _as(app, "newadm")
    s = c.post("/api/user/requests", json={**SYNC, "run_as_root": True})
    assert s.status_code == 202, s.text
    # 실행 신원 지정도 같은 목록 -- 부여 전엔 403 이던 것
    assert c.post("/api/user/requests", json={**SYNC, "destination": "d2", "owner_username": "alice"}).status_code == 202


def test_batch_creation_gate_honours_the_grant(db):
    app = _app(db)
    body = {"operation": "scan", "max_concurrency": 1, "options": {},
            "items": [{"storage": "s1", "target": "t"}]}
    r = _as(app, "newadm").post("/api/admin/batches", json=body)
    assert (r.status_code, r.json()["detail"]) == (403, "privileged_not_authorized")
    _grant(_as(app, "ops"), "newadm")
    assert _as(app, "newadm").post("/api/admin/batches", json=body).status_code == 202


# ---- 누가 줄 수 있나 ----

def test_only_a_privileged_session_admin_can_grant(db):
    app = _app(db)
    r = _grant(_as(app, "ops2"), "newadm")                      # 목록 밖 관리자 -- 경계를 스스로 넓힐 수 없다
    assert (r.status_code, r.json()["detail"]) == (403, "root_grant_privileged_only")
    r = TestClient(app).put("/api/admin/accounts/newadm/root", json={"granted": True}, headers=TOKEN)
    assert (r.status_code, r.json()["detail"]) == (403, "accounts_session_required")
    r = _grant(_as(app, "plain"), "newadm")
    assert (r.status_code, r.json()["detail"]) == (403, "admin_required")
    assert _me(app, "newadm") is False


def test_non_privileged_admin_cannot_grant_itself(db):
    app = _app(db)
    r = _grant(_as(app, "ops2"), "ops2")
    assert r.status_code == 403
    assert _me(app, "ops2") is False


# ---- 누구에게 ----

def test_grant_targets_and_refusals(db):
    app = _app(db)
    ops = _as(app, "ops")
    r = _grant(ops, "plain")                                  # 자격은 admin 역할에만 뜻이 있다
    assert (r.status_code, r.json()["detail"]) == (409, "root_grant_requires_admin")
    assert _grant(ops, "plain", granted=False).status_code == 200   # 회수는 역할과 무관(멱등)
    r = _grant(ops, "ops")
    assert (r.status_code, r.json()["detail"]) == (409, "cannot_lock_self")
    r = _grant(ops, "nobody")
    assert (r.status_code, r.json()["detail"]) == (404, "account_not_found")
    assert ops.put("/api/admin/accounts/newadm/root", json={"granted": 1}).status_code == 422
    assert ops.put("/api/admin/accounts/newadm/root", json={}).status_code == 422


def test_env_listed_names_are_config_managed(db):
    app = _app(db, DMS_PRIVILEGED_REQUESTERS="ops,cfgadm")
    app.state.repos.accounts.create("cfgadm", "p", "admin")
    r = _grant(_as(app, "ops"), "cfgadm")                       # 주기는 배포 설정으로만(빼기는 된다 -- 아래 시험)
    assert (r.status_code, r.json()["detail"]) == (409, "root_grant_config_managed")
    rows = {a["username"]: a for a in _as(app, "ops").get("/api/admin/accounts").json()}
    assert (rows["cfgadm"]["root_source"], rows["cfgadm"]["root_eligible"]) == ("config", True)
    assert (rows["newadm"]["root_source"], rows["newadm"]["root_eligible"]) == (None, False)
    assert (rows["plain"]["root_source"], rows["plain"]["root_eligible"]) == (None, False)


# ---- 부여된 계정의 보호 ----

def test_granted_account_gets_the_privileged_account_protection(db):
    app = _app(db)
    _grant(_as(app, "ops"), "newadm")
    ops2 = _as(app, "ops2")
    for r in (ops2.put("/api/admin/accounts/newadm/role", json={"role": "user"}),
              ops2.put("/api/admin/accounts/newadm/disabled", json={"disabled": True}),
              ops2.delete("/api/admin/accounts/newadm")):
        assert (r.status_code, r.json()["detail"]) == (403, "privileged_account_protected")
    # 셀프 비밀번호 재설정(메일 인증번호)도 env 목록 계정처럼 막힌다 -- 목록 밖 관리자가 메일 설정을 바꿔 코드를 받아
    # 그 세션으로 root 를 얻는 길(_refuse_privileged_reset). 부여 전엔 이 거절이 없다(대조).
    anon = TestClient(app)
    r = anon.post("/api/auth/verification-codes", json={"username": "newadm", "purpose": "password_reset"})
    assert (r.status_code, r.json()["detail"]) == (403, "privileged_account_protected")
    r = anon.post("/api/auth/verification-codes", json={"username": "ops2", "purpose": "password_reset"})
    assert r.json().get("detail") != "privileged_account_protected"


def test_disabled_granted_account_stays_protected(db):
    """보안 리뷰(2026-10-12): 보호를 효력(활성 admin) 기준으로 보면 비활성화된 부여 계정이 보호에서 빠져, 목록 밖 관리자가 다시
    켜는 것만으로 root 자격을 되살렸다. 보호는 저장된 부여 기준이다."""
    app = _app(db)
    ops = _as(app, "ops")
    _grant(ops, "newadm")
    assert ops.put("/api/admin/accounts/newadm/disabled", json={"disabled": True}).status_code == 200
    ops2 = _as(app, "ops2")
    for r in (ops2.put("/api/admin/accounts/newadm/disabled", json={"disabled": False}),
              ops2.put("/api/admin/accounts/newadm/role", json={"role": "user"}),
              ops2.delete("/api/admin/accounts/newadm")):
        assert (r.status_code, r.json()["detail"]) == (403, "privileged_account_protected")
    r = TestClient(app).post("/api/auth/verification-codes", json={"username": "newadm", "purpose": "password_reset"})
    assert (r.status_code, r.json()["detail"]) == (403, "privileged_account_protected")
    # 특권 관리자는 다시 켤 수 있고, 켜면 효력이 돌아온다
    assert ops.put("/api/admin/accounts/newadm/disabled", json={"disabled": False}).status_code == 200
    assert _me(app, "newadm") is True


# ---- 효력의 수명 ----

def test_demotion_clears_the_grant_and_repromotion_does_not_restore_it(db):
    app = _app(db)
    ops = _as(app, "ops")
    _grant(ops, "newadm")
    assert ops.put("/api/admin/accounts/newadm/role", json={"role": "user"}).status_code == 200
    assert ops.put("/api/admin/accounts/newadm/role", json={"role": "admin"}).status_code == 200
    assert _me(app, "newadm") is False
    rows = {a["username"]: a for a in ops.get("/api/admin/accounts").json()}
    assert rows["newadm"]["root_source"] is None


def test_disabled_account_loses_effect_but_keeps_the_grant(db):
    app = _app(db)
    ops = _as(app, "ops")
    _grant(ops, "newadm")
    ops.put("/api/admin/accounts/newadm/disabled", json={"disabled": True})
    assert "newadm" not in privileged_names(app.state.settings, app.state.repos)
    rows = {a["username"]: a for a in ops.get("/api/admin/accounts").json()}
    assert (rows["newadm"]["root_source"], rows["newadm"]["root_eligible"]) == ("granted", False)
    ops.put("/api/admin/accounts/newadm/disabled", json={"disabled": False})
    assert _me(app, "newadm") is True


def test_recreated_account_does_not_inherit_a_grant(db):
    app = _app(db)
    ops = _as(app, "ops")
    _grant(ops, "newadm")
    assert ops.delete("/api/admin/accounts/newadm").status_code == 204
    app.state.repos.accounts.create("newadm", "p", "admin")
    assert _me(app, "newadm") is False


def test_revoke(db):
    app = _app(db)
    ops = _as(app, "ops")
    _grant(ops, "newadm")
    r = _grant(ops, "newadm", granted=False)
    assert (r.status_code, r.json()["root_source"], r.json()["root_eligible"]) == (200, None, False)
    assert _me(app, "newadm") is False


def test_global_switch_off_disables_granted_accounts_too(tmp_path):
    from dms.db import Database
    from dms.migrations import migrate
    db = Database.connect(f"sqlite:///{tmp_path}/off.db")
    migrate(db)
    # dms-k8s 는 허용 스위치 false 를 기동 거부한다(모든 잡 root) -- 판정 함수들이 스위치를 따로 보는지는 기동 검증을 건너뛴
    # 설정(dataclass 교체)으로 본다.
    import dataclasses
    app = _app(db)
    app.state.settings = dataclasses.replace(app.state.settings, allow_privileged_requesters=False)
    app.state.repos.accounts.set_root_granted("newadm", True, actor="t")
    assert _me(app, "newadm") is False
    rows = {a["username"]: a for a in TestClient(app).get("/api/admin/accounts", headers=TOKEN).json()}
    assert rows["newadm"]["root_eligible"] is False


def test_grant_and_revoke_are_audited(db):
    app = _app(db)
    ops = _as(app, "ops")
    _grant(ops, "newadm")
    _grant(ops, "newadm", granted=False)
    _grant(ops, "newadm")
    ops.put("/api/admin/accounts/newadm/role", json={"role": "user"})
    rows = db.query("SELECT operation, actor, after_state FROM audit_log WHERE mutation_class = 'account' "
                    "AND target_key = 'newadm' AND operation IN ('root_grant', 'root_revoke') ORDER BY id")
    assert [(r["operation"], r["actor"]) for r in rows] == [
        ("root_grant", "ops"), ("root_revoke", "ops"), ("root_grant", "ops"), ("root_revoke", "ops")]
    assert '"demoted"' in rows[-1]["after_state"]


# ---- 컨트롤러 쪽(planner·자동 확인)도 같은 집합 ----

class _Settings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"ops"})
    planner_identity_grace_seconds = 300


def _plan_root_request(db, *, grant: bool):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/dms",
                          backend_type="cephfs", actor="admin")
    repos.storages.set_status("s1", "Ready", "ready_nodes=1")
    repos.control.upsert_policy("scan", max_nodes=3, procs_per_node=8, queue="dms-data",
                                default_priority="mid", max_priority="high",
                                preview_timeout_seconds=3600, execution_timeout_seconds=3600,
                                enabled=True, actor="admin")
    repos.agents.ingest("n1", {
        "node_name": "n1",
        "mounts": [{"storage_name": "s1", "mount_path": "/mnt/s1", "status": "Ready", "writable": True}],
        "tools": [{"name": t, "status": "Ready"} for t in ("dscan", "dsync", "nsync", "drm")]},
        reported_at="2026-08-02T09:59:00Z")
    repos.accounts.create("newadm", "pw", "admin")
    if grant:
        repos.accounts.set_root_granted("newadm", True, actor="ops")
    rid = repos.requests.create(operation="scan", requester_id="newadm", actor="newadm", resource_key="k",
                                payload={"storage": "s1", "target": "a", "options": {}, "owner_username": "alice",
                                         "run_as_root": True},
                                priority="mid", auth_method="session")
    Planner(repos, settings=_Settings()).run_once(
        now_iso="2026-08-02T10:00:00Z")
    jobs = repos.data_jobs.list_jobs(request_id=rid)
    return repos.requests.get(rid), (jobs[0]["worker_pool"]["identity"] if jobs else None)


def test_planner_honours_a_grant(db):
    req, ident = _plan_root_request(db, grant=True)
    assert (ident["uid"], ident["privileged"]) == (0, True)


@pytest.mark.parametrize("grant", [False])
def test_planner_rejects_root_without_a_grant(db, grant):
    req, ident = _plan_root_request(db, grant=grant)
    assert ident is None
    assert req["state"] == "Rejected"


def test_auto_confirm_hold_honours_a_grant(db):
    """자동 확인(batch_orchestrator.auto_confirm_hold)도 같은 집합을 본다 -- 켠 사람이 부여받은 관리자면 보류하지 않고,
    회수되면 auto_confirm_not_privileged 로 보류한다."""
    from dms.batch_orchestrator import auto_confirm_hold
    repos = Repositories(db)
    repos.accounts.create("newadm", "pw", "admin")
    batch = {"operation": "sync", "auto_confirm_by": "newadm", "options": {}}
    assert auto_confirm_hold(repos, _Settings(), batch) == "auto_confirm_not_privileged"
    repos.accounts.set_root_granted("newadm", True, actor="ops")
    assert auto_confirm_hold(repos, _Settings(), batch) is None
    repos.accounts.set_root_granted("newadm", False, actor="ops")
    assert auto_confirm_hold(repos, _Settings(), batch) == "auto_confirm_not_privileged"


# ---- 리뷰 반영(2026-10-12) ----

def test_grant_write_requires_admin_role_inside_the_transaction(db):
    """라우트가 역할을 읽은 뒤 다른 관리자가 강등하면(대상은 아직 보호 밖) 쓰기 문장의 역할 조건이 막는다 -- user 행에 플래그가
    남아 나중의 승격이 자격을 몰래 되살리지 않게."""
    from dms.domain import DomainValidationError
    repos = Repositories(db)
    repos.accounts.create("bob", "pw", "user")
    with pytest.raises(DomainValidationError) as e:
        repos.accounts.set_root_granted("bob", True, actor="ops")
    assert e.value.reason_code == "root_grant_requires_admin"
    assert repos.accounts.root_grant_flagged_names() == frozenset()


def test_promotion_clears_a_stale_flag(db):
    """어느 경로로든 user 행에 플래그가 남았으면(경합·DB 변조) 승격이 그것을 되살리지 않고 지운다(감사 promoted_stale_flag)."""
    app = _app(db)
    db.execute("UPDATE accounts SET root_granted = 1 WHERE username = 'plain'")
    ops = _as(app, "ops")
    assert ops.put("/api/admin/accounts/plain/role", json={"role": "admin"}).status_code == 200
    assert _me(app, "plain") is False
    assert app.state.repos.accounts.root_grant_flagged_names() == frozenset()
    row = db.query_one("SELECT after_state FROM audit_log WHERE operation = 'root_revoke' AND target_key = 'plain'")
    assert '"promoted_stale_flag"' in row["after_state"]
    # 이미 admin 인 계정에 같은 역할을 다시 써도 부여는 그대로다(승격이 아니다)
    _grant(ops, "newadm")
    assert ops.put("/api/admin/accounts/newadm/role", json={"role": "admin"}).status_code == 200
    assert _me(app, "newadm") is True


def test_shared_token_actor_name_cannot_be_granted(db):
    """공유 Bearer 토큰의 actor 와 같은 이름의 계정에 자격을 주면 토큰(모든 노드 에이전트)이 실행 신원 지정을 얻는다(세션 요구
    없음) -- 부여 거부 + 효력 집합에서도 늘 뺀다(DB 변조 방어)."""
    app = _app(db)
    app.state.repos.accounts.create("shared-token", "p", "admin")
    r = _grant(_as(app, "ops"), "shared-token")
    assert (r.status_code, r.json()["detail"]) == (409, "root_grant_reserved_name")
    db.execute("UPDATE accounts SET root_granted = 1 WHERE username = 'shared-token'")
    assert "shared-token" not in privileged_names(app.state.settings, app.state.repos)
    r = TestClient(app).post("/api/user/requests", json={**SYNC, "owner_username": "alice"}, headers=TOKEN)
    assert (r.status_code, r.json()["detail"]) == (403, "privileged_not_authorized")


def test_env_listed_name_with_a_stored_grant_can_be_revoked(db):
    """화면에서 준 뒤 env 에도 들어간 이름: 저장된 부여가 보이고 뺄 수 있다 -- 운영자가 env 에서 빼면 효력이 끝나게."""
    app = _app(db, DMS_PRIVILEGED_REQUESTERS="ops,cfgadm")
    app.state.repos.accounts.create("cfgadm", "p", "admin")
    app.state.repos.accounts.set_root_granted("cfgadm", True, actor="ops")
    ops = _as(app, "ops")
    rows = {a["username"]: a for a in ops.get("/api/admin/accounts").json()}
    assert (rows["cfgadm"]["root_source"], rows["cfgadm"]["root_granted"]) == ("config", True)
    r = _grant(ops, "cfgadm", granted=False)
    assert r.status_code == 200 and r.json()["root_granted"] is False
    assert _grant(ops, "cfgadm").json()["detail"] == "root_grant_config_managed"     # 주기는 여전히 배포 설정으로만


def _root_confirm_pending(app):
    """newadm 이 root 로 낸 sync 가 root 로 계획돼(worker_pool identity privileged) 컨펌을 기다린다."""
    repos = app.state.repos
    rid = repos.requests.create(operation="sync", requester_id="newadm", actor="newadm", resource_key="k:rc",
                                payload={**{k: v for k, v in SYNC.items() if k != "operation"}, "run_as_root": True},
                                priority="mid", auth_method="session")
    from dms.domain import DataJobState, RequestState
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid", source_storage="s1",
        source="src", destination_storage="s1", destination="dst", options={}, tool="dsync",
        worker_pool={"identity": {"username": "newadm", "uid": 0, "gid": 0, "privileged": True}},
        precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc", expires_at="2099-01-01T00:00:00Z",
                                artifact_uri="file:///art/j")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return rid, jid


def test_revoked_admin_cannot_confirm_an_already_planned_root_job(db):
    """신원은 계획 때 얼어 stepper 는 자격을 다시 보지 않는다 -- 「자격 빼기」 뒤 컨펌 대기 잡의 컨펌을 여기서 막는다
    (Rejected + 요청 화해). 자격이 있는 동안은 컨펌된다(대조). k8s 신원 모드에서 자격 = 맵핑 면제라, 회수된 관리자의 직접 경로
    잡은 컨펌의 재확인(k8s_access.recheck_request)이 raw_path_not_allowed 로 끊는다(root 자체는 세션 요청이면 허용 -- D3)."""
    app = _app(db)
    ops = _as(app, "ops")
    _grant(ops, "newadm")
    _, ok_jid = _root_confirm_pending(app)
    c = _as(app, "newadm")
    assert c.post(f"/api/user/jobs/{ok_jid}:confirm", json={"fingerprint": "sha256:abc"}).status_code == 200
    app.state.repos.data_jobs.set_job_state(ok_jid, __import__("dms.domain", fromlist=["DataJobState"]).DataJobState.CANCELLED,
                                            actor="t")
    rid, jid = _root_confirm_pending(app)
    _grant(ops, "newadm", granted=False)
    r = c.post(f"/api/user/jobs/{jid}:confirm", json={"fingerprint": "sha256:abc"})
    assert (r.status_code, r.json()["detail"]) == (409, "raw_path_not_allowed")
    job = app.state.repos.data_jobs.get_job(jid)
    assert (job["state"], job["reason_code"]) == ("Rejected", "raw_path_not_allowed")
    assert app.state.repos.requests.get(rid)["state"] == "Rejected"
