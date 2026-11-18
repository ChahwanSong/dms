"""사내 SSO -- OIDC Authorization Code + PKCE(S256) 클라이언트(2026-11-18, 사용자 요청 「Sign in with DS AD SSO」).

사내 AD 인증은 IdP(Keycloak)가 대행한다 -- DMS 는 LDAP·AD 에 직접 붙지 않고 OIDC 만 구현한다(참고: vcf-create-api auth.py 와 같은
방식). 라우트(routes_oidc)가 세션 쿠키·리다이렉트를 맡고, 이 모듈은 프로토콜만 한다:

  start()   authorization URL + interaction(state·nonce·code_verifier) -- 호출자가 세션 쿠키(서명됨)에 얼린다.
  finish()  state 대조 → 토큰 끝점에서 code 교환(confidential client, client_secret_basic + code_verifier)
            → id_token 클레임 대조(iss·aud·azp·exp·nonce) → 클레임 dict.
  username_from_email()  <아이디>@<DMS_ACCOUNT_EMAIL_DOMAIN> 만 받는다 -- DMS 계정 아이디 = 사내 메일 아이디(k8s 맵핑 ID)라
            메일 인증과 같은 결속을 IdP 가 보증한다.

id_token 서명은 검사하지 않는다: 토큰 끝점에서 서버 인증서를 검증한 TLS 로 **직접** 받은 id_token 은 OIDC Core 3.1.3.7(6)이
서명 검증 대신 TLS 서버 검증을 허용한다. 그래서 TLS 검증은 끄는 길이 없다 -- 사내 CA 는 DMS_OIDC_CA_PEM 으로 준다.
프록시 env 는 쓰지 않는다(ProxyHandler({})) -- IdP 는 사내망이고, 빌드용 프록시 env 가 섞이면 토큰이 프록시를 탄다.
표준 라이브러리만 쓴다."""
import base64
import hashlib
import json
import secrets
import ssl
import threading
import time
from urllib import error as _urlerror
from urllib import request as _rq
from urllib.parse import urlencode

INTERACTION_TTL_SECONDS = 600       # 로그인 시작 → 콜백 사이 10분
CLOCK_SKEW_SECONDS = 120            # id_token exp·iat 비교 여유
_HTTP_TIMEOUT_SECONDS = 15
DISCOVERY_TTL_SECONDS = 3600

_lock = threading.Lock()
_discovery_cache: dict = {}         # issuer -> (fetched_at, document)


class OidcError(Exception):
    """라우트가 /login?sso_error=<reason_code> 로 옮기는 실패. detail 은 로그 전용(사용자에게 보이지 않는다)."""

    def __init__(self, reason_code: str, detail: str = ""):
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)
        self.reason_code = reason_code
        self.detail = detail


def _ssl_context(settings) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if settings.oidc_ca_pem:
        ctx.load_verify_locations(cadata=settings.oidc_ca_pem)
    return ctx


def _http_json(settings, url: str, form: dict | None = None, basic: tuple | None = None,
               bearer: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if basic is not None:
        raw = f"{basic[0]}:{basic[1]}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    if bearer is not None:
        headers["Authorization"] = "Bearer " + bearer
    body = urlencode(form).encode() if form is not None else None
    if body is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = _rq.Request(url, data=body, headers=headers, method="POST" if body is not None else "GET")
    opener = _rq.build_opener(_rq.ProxyHandler({}), _rq.HTTPSHandler(context=_ssl_context(settings)))
    try:
        with opener.open(req, timeout=_HTTP_TIMEOUT_SECONDS) as r:
            return json.loads(r.read().decode())
    except _urlerror.HTTPError as e:
        # 토큰 끝점의 400 본문(error·error_description)은 code·비밀을 담지 않는다 -- 로그에 남겨 원인을 가린다.
        try:
            info = json.loads(e.read().decode())
            info = {k: info.get(k) for k in ("error", "error_description")}
        except (ValueError, OSError):
            info = {}
        raise OidcError("sso_upstream_error", f"HTTP {e.code} from {url} {info}") from None
    except (_urlerror.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise OidcError("sso_upstream_unreachable", f"{url}: {reason}") from None
    except ValueError:
        raise OidcError("sso_upstream_error", f"non-JSON response from {url}") from None


def discover(settings) -> dict:
    """issuer 의 openid-configuration(1시간 캐시). 문서의 issuer 가 설정과 다르면 거부한다(다른 realm 을 가리키는 오설정)."""
    issuer = settings.oidc_issuer
    with _lock:
        hit = _discovery_cache.get(issuer)
        if hit is not None and time.time() - hit[0] < DISCOVERY_TTL_SECONDS:
            return hit[1]
    doc = _http_json(settings, f"{issuer}/.well-known/openid-configuration")
    if (doc.get("issuer") or "").rstrip("/") != issuer:
        raise OidcError("sso_upstream_error", f"discovery issuer {doc.get('issuer')!r} != configured {issuer!r}")
    for key in ("authorization_endpoint", "token_endpoint"):
        if not str(doc.get(key) or "").startswith("https://"):
            raise OidcError("sso_upstream_error", f"discovery {key} missing or not https")
    with _lock:
        _discovery_cache[issuer] = (time.time(), doc)
    return doc


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def start(settings, now: float | None = None) -> tuple[str, dict]:
    """(authorization URL, interaction). interaction 은 세션 쿠키에 그대로 얼린다(서명돼 변조 불가 -- verifier 는 이 브라우저의
    것이라 그 브라우저가 보는 것은 PKCE 의 위협 모형 밖이다)."""
    doc = discover(settings)
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    query = {
        "client_id": settings.oidc_client_id,
        "response_type": "code",
        "scope": "openid email profile",
        "redirect_uri": settings.oidc_redirect_uri,
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    interaction = {"state": state, "nonce": nonce, "verifier": verifier,
                   "at": int(now if now is not None else time.time())}
    return f"{doc['authorization_endpoint']}?{urlencode(query)}", interaction


def _jwt_claims(token: str) -> dict:
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise OidcError("sso_token_invalid", "id_token is not a JWS compact token")
    try:
        pad = "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + pad).decode())
    except (ValueError, UnicodeDecodeError):
        raise OidcError("sso_token_invalid", "id_token payload is not JSON") from None
    if not isinstance(claims, dict):
        raise OidcError("sso_token_invalid", "id_token payload is not an object")
    return claims


def check_id_token(settings, claims: dict, nonce: str, now: float | None = None) -> None:
    """OIDC Core 3.1.3.7 의 클레임 대조(서명 제외 -- 모듈 docstring)."""
    now = now if now is not None else time.time()
    if (str(claims.get("iss") or "")).rstrip("/") != settings.oidc_issuer:
        raise OidcError("sso_token_invalid", f"iss {claims.get('iss')!r}")
    aud = claims.get("aud")
    audiences = [aud] if isinstance(aud, str) else (aud if isinstance(aud, list) else [])
    if settings.oidc_client_id not in audiences:
        raise OidcError("sso_token_invalid", f"aud {aud!r}")
    if len(audiences) > 1 and claims.get("azp") != settings.oidc_client_id:
        raise OidcError("sso_token_invalid", f"azp {claims.get('azp')!r}")
    exp = claims.get("exp")
    if not isinstance(exp, (int, float)) or exp + CLOCK_SKEW_SECONDS < now:
        raise OidcError("sso_token_invalid", f"exp {exp!r}")
    iat = claims.get("iat")
    if isinstance(iat, (int, float)) and iat - CLOCK_SKEW_SECONDS > now:
        raise OidcError("sso_token_invalid", f"iat {iat!r} in the future")
    if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"], nonce):
        raise OidcError("sso_token_invalid", "nonce mismatch")


def finish(settings, interaction, code, state, now: float | None = None) -> dict:
    """콜백 → id_token 클레임(+ id_token 에 email 이 없으면 userinfo 의 email)."""
    now = now if now is not None else time.time()
    if (not isinstance(interaction, dict) or not isinstance(state, str) or not state
            or not isinstance(interaction.get("state"), str)
            or not secrets.compare_digest(interaction["state"], state)):
        raise OidcError("sso_state_invalid", "state missing or mismatched")
    at = interaction.get("at")
    if not isinstance(at, int) or now - at > INTERACTION_TTL_SECONDS:
        raise OidcError("sso_state_invalid", "login interaction expired")
    if not isinstance(code, str) or not code:
        raise OidcError("sso_state_invalid", "code missing")
    doc = discover(settings)
    tok = _http_json(settings, doc["token_endpoint"], form={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.oidc_redirect_uri,
        "code_verifier": interaction.get("verifier") or "",
    }, basic=(settings.oidc_client_id, settings.oidc_client_secret))
    claims = _jwt_claims(tok.get("id_token"))
    check_id_token(settings, claims, interaction.get("nonce") or "", now=now)
    if not claims.get("email") and doc.get("userinfo_endpoint") and tok.get("access_token"):
        info = _http_json(settings, doc["userinfo_endpoint"], bearer=tok["access_token"])
        if info.get("sub") == claims.get("sub"):
            # email_verified 는 email 과 **같은 출처**에서 읽는다 -- id_token 의 email_verified 를 userinfo 의 email 에 붙이지 않는다.
            claims = {**claims, "email": info.get("email"), "email_verified": info.get("email_verified")}
    return claims


def email_verified(settings, claims) -> bool:
    """IdP 가 이 이메일을 확인(verified)했다고 했는가. DMS_OIDC_REQUIRE_EMAIL_VERIFIED 가 켜졌는데 아니면(false·없음) 거절한다.
    끈 상태의 결속은 IdP 설정(사용자가 자기 email 을 못 바꿈)이 지킨다 -- 반환값은 라우트가 로그로 남긴다."""
    verified = claims.get("email_verified") is True
    if not verified and settings.oidc_require_email_verified:
        raise OidcError("sso_email_unverified", f"email_verified {claims.get('email_verified')!r}")
    return verified


def username_from_email(email, domain: str) -> str:
    """cocoa.song@samsung.com → cocoa.song. 도메인은 대소문자 무시로 DMS_ACCOUNT_EMAIL_DOMAIN 과 같아야 한다."""
    if not isinstance(email, str) or "@" not in email:
        raise OidcError("sso_email_missing", f"email {email!r}")
    local, _, host = email.strip().rpartition("@")
    if host.lower() != (domain or "").strip().lower():
        raise OidcError("sso_email_domain_mismatch", f"email domain {host!r} != {domain!r}")
    return local.lower()
