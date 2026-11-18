"""사내 SSO(OIDC Authorization Code + PKCE, 2026-11-18) -- api/oidc.py·routes_oidc.py.

IdP 는 흉내 낸다(oidc._http_json 을 바꾼다): discovery · 토큰 끝점(id_token) · userinfo. 콜백이 비밀번호 로그인과 같은 세션
(username + epoch)을 만들고, 이메일 → 아이디·자동 생성·특권 이름·차단 이름·비활성·state/nonce/aud·열린 리다이렉트를 본다."""
import base64
import hashlib
import json
import time
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from dms.api import oidc
from dms.api.app import create_app
from dms.config import Settings, SettingsError
from dms.repositories import Repositories
from fastapi.testclient import TestClient

ISSUER = "https://idp.example/realms/prod"
AUTH_EP = f"{ISSUER}/protocol/openid-connect/auth"
TOKEN_EP = f"{ISSUER}/protocol/openid-connect/token"
USERINFO_EP = f"{ISSUER}/protocol/openid-connect/userinfo"
CLIENT_ID = "dms-test"
REDIRECT = "https://dms.example/api/auth/oidc/callback"


def _jwt(claims: dict) -> str:
    enc = lambda obj: base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.c2ln"


class FakeIdP:
    def __init__(self):
        self.email = "cocoa.song@samsung.com"
        self.claims_override: dict = {}
        self.token_calls: list = []
        self.issuer = ISSUER
        self.userinfo_extra: dict = {}

    def __call__(self, settings, url, form=None, basic=None, bearer=None):
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": self.issuer, "authorization_endpoint": AUTH_EP,
                    "token_endpoint": TOKEN_EP, "userinfo_endpoint": USERINFO_EP}
        if url == TOKEN_EP:
            self.token_calls.append({"form": form, "basic": basic})
            now = int(time.time())
            claims = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "uuid-1", "exp": now + 300, "iat": now,
                      "nonce": self.nonce, "email": self.email}
            claims.update(self.claims_override)
            return {"id_token": _jwt({k: v for k, v in claims.items() if v is not None}), "access_token": "at"}
        if url == USERINFO_EP:
            return {"sub": "uuid-1", "email": self.email, **self.userinfo_extra}
        raise AssertionError(f"unexpected url {url}")


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdP()
    monkeypatch.setattr(oidc, "_http_json", fake)
    oidc._discovery_cache.clear()
    yield fake
    oidc._discovery_cache.clear()


def _sso_settings(settings, **kw):
    return replace(settings, oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID, oidc_client_secret="s3cret",
                   oidc_redirect_uri=REDIRECT, oidc_button_label="Sign in with DS AD SSO",
                   account_email_domain="samsung.com", **kw)


@pytest.fixture
def sso_client(db, settings):
    return TestClient(create_app(_sso_settings(settings), db), base_url="https://dms.example")


def _start(client, idp, next_path=None):
    params = {"next": next_path} if next_path is not None else None
    r = client.get("/api/auth/oidc/login", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == AUTH_EP
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    idp.nonce = q["nonce"]
    return q


def _callback(client, **params):
    return client.get("/api/auth/oidc/callback", params=params, follow_redirects=False)


def _sso_error(resp):
    assert resp.status_code == 303, resp.text
    loc = resp.headers["location"]
    assert loc.startswith("/login?sso_error="), loc
    return loc.split("=", 1)[1]


# --- 설정 ------------------------------------------------------------------------------------------------------------

_BASE_ENV = {"DMS_DATABASE_URL": "sqlite:///x.db", "DMS_SHARED_TOKEN": "a" * 32, "DMS_ADMIN_TOKEN": "b" * 32,
             "DMS_SESSION_SECRET": "c" * 32}
_FULL = {"DMS_OIDC_ISSUER": ISSUER + "/", "DMS_OIDC_CLIENT_ID": CLIENT_ID, "DMS_OIDC_CLIENT_SECRET": "s3cret",
         "DMS_OIDC_REDIRECT_URI": REDIRECT}


def test_settings_sso_off_by_default_and_on_with_all_four_values():
    assert Settings.from_env(_BASE_ENV).oidc_enabled is False
    s = Settings.from_env({**_BASE_ENV, **_FULL, "DMS_OIDC_BUTTON_LABEL": "Sign in with DS AD SSO"})
    assert s.oidc_enabled is True and s.oidc_issuer == ISSUER          # 끝 / 를 뗀다(discovery issuer 대조)
    assert s.oidc_button_label == "Sign in with DS AD SSO" and s.oidc_auto_provision is True


@pytest.mark.parametrize("env,needle", [
    ({k: v for k, v in _FULL.items() if k != "DMS_OIDC_CLIENT_SECRET"}, "missing DMS_OIDC_CLIENT_SECRET"),
    ({**_FULL, "DMS_OIDC_REDIRECT_URI": "http://dms.example/cb"}, "DMS_OIDC_REDIRECT_URI must be an https"),
    ({**_FULL, "DMS_OIDC_CA_PEM": "not a pem"}, "DMS_OIDC_CA_PEM is not a PEM"),
])
def test_settings_refuse_partial_or_unsafe_sso(env, needle):
    with pytest.raises(SettingsError) as e:
        Settings.from_env({**_BASE_ENV, **env})
    assert needle in str(e.value)


# --- 화면 재료·꺼짐 -----------------------------------------------------------------------------------------------------

def test_sso_info_and_login_when_disabled(client):
    assert client.get("/api/auth/sso-info").json() == {"enabled": False, "label": ""}
    r = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert _sso_error(r) == "sso_not_configured"


def test_sso_info_when_enabled(sso_client):
    assert sso_client.get("/api/auth/sso-info").json() == {"enabled": True, "label": "Sign in with DS AD SSO"}


# --- 정상 흐름 ----------------------------------------------------------------------------------------------------------

def test_full_flow_creates_user_account_and_session(sso_client, idp, db):
    q = _start(sso_client, idp, next_path="/jobs")
    assert q["client_id"] == CLIENT_ID and q["redirect_uri"] == REDIRECT and q["response_type"] == "code"
    assert q["code_challenge_method"] == "S256" and "openid" in q["scope"].split()
    r = _callback(sso_client, code="abc", state=q["state"])
    assert r.status_code == 303 and r.headers["location"] == "/jobs"
    call = idp.token_calls[-1]
    assert call["basic"] == (CLIENT_ID, "s3cret") and call["form"]["code"] == "abc"
    verifier = call["form"]["code_verifier"]
    assert base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode() \
        == q["code_challenge"]
    me = sso_client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["actor"] == "cocoa.song" and me.json()["role"] == "user"
    acct = Repositories(db).accounts.get("cocoa.song")
    assert acct["email"] == "cocoa.song@samsung.com" and acct["role"] == "user"


def test_existing_privileged_account_logs_in_with_its_role(db, settings, idp):
    s = _sso_settings(settings, privileged_requesters=frozenset({"root", "admin", "cocoa.song"}))
    Repositories(db).accounts.create("cocoa.song", "pw-unused-1", "admin", email="cocoa.song@samsung.com")
    c = TestClient(create_app(s, db), base_url="https://dms.example")
    q = _start(c, idp)
    r = _callback(c, code="abc", state=q["state"])
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert c.get("/api/auth/me").json()["role"] == "admin"


def test_email_falls_back_to_userinfo(sso_client, idp):
    idp.claims_override = {"email": None}
    q = _start(sso_client, idp)
    assert _callback(sso_client, code="abc", state=q["state"]).headers["location"] == "/"
    assert sso_client.get("/api/auth/me").json()["actor"] == "cocoa.song"


# --- 거절 ---------------------------------------------------------------------------------------------------------------

def test_privileged_name_is_not_auto_created(db, settings, idp):
    s = _sso_settings(settings, privileged_requesters=frozenset({"root", "admin", "cocoa.song"}))
    c = TestClient(create_app(s, db), base_url="https://dms.example")
    q = _start(c, idp)
    assert _sso_error(_callback(c, code="abc", state=q["state"])) == "sso_privileged_account_missing"
    assert Repositories(db).accounts.get("cocoa.song") is None


def test_auto_provision_off_requires_existing_account(db, settings, idp):
    c = TestClient(create_app(_sso_settings(settings, oidc_auto_provision=False), db), base_url="https://dms.example")
    q = _start(c, idp)
    assert _sso_error(_callback(c, code="abc", state=q["state"])) == "sso_account_not_found"


@pytest.mark.parametrize("email,code", [
    ("cocoa.song@other.com", "sso_email_domain_mismatch"),
    ("root@samsung.com", "sso_username_invalid"),
    ("admin@samsung.com", "sso_username_invalid"),
    ("daemon@samsung.com", "sso_username_invalid"),
    ("12345@samsung.com", "sso_username_invalid"),
    ("not-an-email", "sso_email_missing"),
])
def test_bad_identities_are_refused(sso_client, idp, db, email, code):
    idp.email = email
    q = _start(sso_client, idp)
    assert _sso_error(_callback(sso_client, code="abc", state=q["state"])) == code
    assert sso_client.get("/api/auth/me").status_code == 401


def test_disabled_account_is_refused(sso_client, idp, db):
    repos = Repositories(db)
    repos.accounts.create("cocoa.song", "pw-unused-1", "user", email="cocoa.song@samsung.com")
    repos.accounts.set_disabled("cocoa.song", True, actor="test")
    q = _start(sso_client, idp)
    assert _sso_error(_callback(sso_client, code="abc", state=q["state"])) == "account_disabled"


@pytest.mark.parametrize("override", [{"nonce": "other"}, {"aud": "someone-else"}, {"iss": "https://evil/realms/x"},
                                      {"exp": 1}, {"aud": [CLIENT_ID, "x"], "azp": "x"}])
def test_bad_id_token_claims_are_refused(sso_client, idp, override):
    idp.claims_override = override
    q = _start(sso_client, idp)
    assert _sso_error(_callback(sso_client, code="abc", state=q["state"])) == "sso_token_invalid"


def test_state_mismatch_and_replay_are_refused(sso_client, idp):
    q = _start(sso_client, idp)
    assert _sso_error(_callback(sso_client, code="abc", state="forged")) == "sso_state_invalid"
    # 한 번 쓴 interaction 은 세션에서 지워진다 -- 맞는 state 로 다시 와도 거절(재전송 차단).
    assert _sso_error(_callback(sso_client, code="abc", state=q["state"])) == "sso_state_invalid"


def test_callback_without_login_start_is_refused(sso_client, idp):
    assert _sso_error(_callback(sso_client, code="abc", state="x")) == "sso_state_invalid"


def test_idp_error_is_denied(sso_client, idp):
    _start(sso_client, idp)
    assert _sso_error(_callback(sso_client, error="access_denied")) == "sso_denied"


def test_discovery_issuer_mismatch_is_refused(sso_client, idp):
    idp.issuer = "https://idp.example/realms/other"
    r = sso_client.get("/api/auth/oidc/login", follow_redirects=False)
    assert _sso_error(r) == "sso_upstream_error"


@pytest.mark.parametrize("raw,expected", [
    ("/jobs", "/jobs"), ("/jobs?x=1", "/jobs?x=1"), ("//evil.example/x", "/"), ("https://evil.example", "/"),
    ("/\\evil", "/"), ("/api/admin/accounts", "/"), ("/login", "/"), ("", "/"), (None, "/"),
])
def test_next_is_same_origin_portal_path_only(sso_client, idp, raw, expected):
    q = _start(sso_client, idp, next_path=raw)
    assert _callback(sso_client, code="abc", state=q["state"]).headers["location"] == expected


def test_sso_session_ends_when_password_changes(sso_client, idp, db):
    q = _start(sso_client, idp)
    _callback(sso_client, code="abc", state=q["state"])
    assert sso_client.get("/api/auth/me").status_code == 200
    Repositories(db).accounts.set_password("cocoa.song", "new-password-1")
    r = sso_client.get("/api/auth/me")
    assert r.status_code == 401 and r.json()["detail"] == "session_revoked"


# --- email_verified(2026-11-18 반영 때 더함 — 보안 리뷰: IdP 가 사용자에게 email 수정을 허용하면 남의 메일로 그 계정에 들어온다) ----------

def test_settings_require_email_verified_is_off_by_default_and_parses():
    assert Settings.from_env({**_BASE_ENV, **_FULL}).oidc_require_email_verified is False
    s = Settings.from_env({**_BASE_ENV, **_FULL, "DMS_OIDC_REQUIRE_EMAIL_VERIFIED": "true"})
    assert s.oidc_require_email_verified is True


@pytest.mark.parametrize("override", [{"email_verified": False}, {}])
def test_unverified_email_is_allowed_when_not_required(sso_client, idp, override):
    # 기본(끔) = 사이트에서 검증된 인계 패치의 동작 그대로 — Keycloak AD 연동은 「Trust Email」 없이는 email_verified=false 다.
    idp.claims_override = override
    q = _start(sso_client, idp)
    assert _callback(sso_client, code="abc", state=q["state"]).headers["location"] == "/"
    assert sso_client.get("/api/auth/me").json()["actor"] == "cocoa.song"


@pytest.mark.parametrize("override", [{"email_verified": False}, {}, {"email_verified": "true"}])
def test_required_email_verified_refuses_unverified(db, settings, idp, override):
    c = TestClient(create_app(_sso_settings(settings, oidc_require_email_verified=True), db),
                   base_url="https://dms.example")
    idp.claims_override = override
    q = _start(c, idp)
    assert _sso_error(_callback(c, code="abc", state=q["state"])) == "sso_email_unverified"
    assert Repositories(db).accounts.get("cocoa.song") is None       # 계정을 만들기 전에 막는다


def test_required_email_verified_accepts_verified(db, settings, idp):
    c = TestClient(create_app(_sso_settings(settings, oidc_require_email_verified=True), db),
                   base_url="https://dms.example")
    idp.claims_override = {"email_verified": True}
    q = _start(c, idp)
    assert _callback(c, code="abc", state=q["state"]).headers["location"] == "/"
    assert c.get("/api/auth/me").json()["actor"] == "cocoa.song"


@pytest.mark.parametrize("userinfo_verified,ok", [(True, True), (False, False)])
def test_email_verified_is_read_from_the_same_source_as_the_email(db, settings, idp, userinfo_verified, ok):
    # id_token 에 email 이 없고 email_verified 만 true — userinfo 의 email 에는 userinfo 의 email_verified 를 쓴다.
    c = TestClient(create_app(_sso_settings(settings, oidc_require_email_verified=True), db),
                   base_url="https://dms.example")
    idp.claims_override = {"email": None, "email_verified": True}
    idp.userinfo_extra = {"email_verified": userinfo_verified}
    q = _start(c, idp)
    r = _callback(c, code="abc", state=q["state"])
    if ok:
        assert r.headers["location"] == "/"
    else:
        assert _sso_error(r) == "sso_email_unverified"


def test_username_from_email_rules():
    assert oidc.username_from_email("Cocoa.Song@SAMSUNG.com", "samsung.com") == "cocoa.song"
    with pytest.raises(oidc.OidcError) as e:
        oidc.username_from_email(None, "samsung.com")
    assert e.value.reason_code == "sso_email_missing"
