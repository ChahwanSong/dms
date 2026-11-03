"""비밀번호 변경 시 세션 무효화(2026-11-03, accounts.session_epoch ↔ 세션 쿠키 epoch -- api.auth.current_identity).

비밀번호가 바뀌면(셀프 재설정·관리자 재설정·set_password) 그 계정의 **모든** 세션이 끝난다. 계정을 지웠다 같은 이름으로
다시 만들어도 옛 세션은 붙지 않는다. 옛 계정(session_epoch NULL)·배포 전 세션(epoch 키 없음)은 그대로 유효하다. Bearer 는
대상이 아니다."""
from dataclasses import replace

from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.repositories import Repositories


def _login(c, username, password):
    return c.post("/api/auth/login", json={"username": username, "password": password})


def test_self_service_reset_ends_every_session_of_that_account(db, settings):
    repos = Repositories(db)
    repos.accounts.create("alice", "old", "user")
    repos.accounts.create("bob", "pw", "user")
    app = create_app(replace(settings, account_verification_required=True), db)
    a1, a2, b = TestClient(app), TestClient(app), TestClient(app)
    for c, (u, p) in ((a1, ("alice", "old")), (a2, ("alice", "old")), (b, ("bob", "pw"))):
        assert _login(c, u, p).status_code == 200
    code = a1.post("/api/auth/verification-codes",
                   json={"username": "alice", "purpose": "password_reset"}).json()["stub_code"]
    assert a1.post("/api/auth/password-reset",
                   json={"username": "alice", "password": "new", "code": code}).status_code == 200
    for c in (a1, a2):
        r = c.get("/api/auth/me")
        assert (r.status_code, r.json()["detail"]) == (401, "session_revoked")
    assert b.get("/api/auth/me").json()["actor"] == "bob"           # 다른 계정은 무관
    assert _login(a1, "alice", "new").status_code == 200
    assert a1.get("/api/auth/me").json()["actor"] == "alice"        # 새 로그인은 새 세대
    # 감사에 세션 끝남이 남는다
    row = db.query_one("SELECT after_state FROM audit_log WHERE operation = 'password_reset'")
    assert '"sessions_revoked": true' in row["after_state"]


def test_legacy_account_without_an_epoch_keeps_its_session_until_the_first_change(db, client):
    repos = Repositories(db)
    repos.accounts.create("old.timer", "pw", "user")
    db.execute("UPDATE accounts SET session_epoch = NULL WHERE username = 'old.timer'")   # 기배포 행 흉내
    assert _login(client, "old.timer", "pw").status_code == 200
    assert client.get("/api/auth/me").json()["actor"] == "old.timer"   # None == None 으로 유효
    repos.accounts.set_password("old.timer", "pw2")
    r = client.get("/api/auth/me")
    assert (r.status_code, r.json()["detail"]) == (401, "session_revoked")


def test_pre_deploy_cookie_without_epoch_key_is_valid_for_a_null_epoch_account(db, client):
    # 배포 전에 만든 세션 쿠키에는 epoch 키가 없다 -- 기배포 계정(NULL)이면 그대로 유효해야 배포 순간 전원이 끊기지 않는다.
    # 쿠키는 starlette SessionMiddleware 형식(base64 JSON 을 세션 시크릿으로 서명)으로 직접 만든다.
    import base64
    import json
    from itsdangerous import TimestampSigner
    repos = Repositories(db)
    repos.accounts.create("legacy", "pw", "user")
    db.execute("UPDATE accounts SET session_epoch = NULL WHERE username = 'legacy'")
    data = base64.b64encode(json.dumps({"username": "legacy"}).encode("utf-8"))
    cookie = TimestampSigner("sess-secret").sign(data).decode("utf-8")
    c = TestClient(client.app, cookies={"dms_session": cookie})
    assert c.get("/api/auth/me").json()["actor"] == "legacy"
    # 새 세대가 있는 계정이면 키 없는 옛 쿠키는 끝났다(지운 뒤 다시 만든 계정 등)
    repos.accounts.create("fresh", "pw", "user")
    data = base64.b64encode(json.dumps({"username": "fresh"}).encode("utf-8"))
    cookie = TimestampSigner("sess-secret").sign(data).decode("utf-8")
    c = TestClient(client.app, cookies={"dms_session": cookie})
    r = c.get("/api/auth/me")
    assert (r.status_code, r.json()["detail"]) == (401, "session_revoked")


def test_deleted_and_recreated_account_does_not_inherit_old_sessions(db, client):
    repos = Repositories(db)
    repos.accounts.create("boss", "pw", "admin")
    repos.accounts.create("reborn", "pw", "user")
    old = TestClient(client.app)
    assert _login(old, "reborn", "pw").status_code == 200
    repos.accounts.delete("reborn", actor="boss")
    repos.accounts.create("reborn", "pw", "user")                  # 같은 이름·같은 비밀번호여도
    r = old.get("/api/auth/me")
    assert (r.status_code, r.json()["detail"]) == (401, "session_revoked")


def test_bearer_token_is_not_affected(db, client):
    repos = Repositories(db)
    repos.accounts.create("alice", "pw", "user")
    repos.accounts.set_password("alice", "pw2")
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer tok-shared"})
    assert r.status_code == 200 and r.json()["actor"] == "shared-token"


def test_disabled_account_still_reports_account_disabled(db, client):
    # 비활성화는 세대를 회전하지 않는다 -- 요청마다 재검사로 막는다(기존 사유 그대로).
    repos = Repositories(db)
    repos.accounts.create("alice", "pw", "user")
    assert _login(client, "alice", "pw").status_code == 200
    repos.accounts.set_disabled("alice", True, actor="t")
    r = client.get("/api/auth/me")
    assert (r.status_code, r.json()["detail"]) == (401, "account_disabled")
