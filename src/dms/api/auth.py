import hmac
from collections import namedtuple
from fastapi import HTTPException, Request

# 예약 접두 -- audit_actor()가 토큰 인증 actor 앞에 붙이는 표식과 동일하다. 호출자가
# x-dms-actor에 이 접두를 직접 넣으면 감사 로그에서 서버가 붙인 표식과 구분이 안 돼
# 사람 admin으로 위장할 수 있으므로 여기서 거절한다.
_RESERVED_ACTOR_PREFIX = "token:"

# Identity.actor 자체는 절대 건드리지 않는다 -- 에이전트 노드 인증(routes_agent.py),
# 특권 요청자 판정(routes_requests.py의 settings.privileged_requesters),
# requester_id -> LDAP/denylist 해석, 스캔 경로 소유권, 계정 자기잠금 가드가 전부 이
# 값을 그대로 비교한다. 대신 인증 방식만 auth 필드로 따로 들고 다니고, 감사 로그를
# 쓰는 라우트에서만 audit_actor()를 거쳐 표시용 actor를 만든다.
Identity = namedtuple("Identity", "actor role auth", defaults=("session",))


def audit_actor(identity: Identity) -> str:
    """감사 로그에 쓸 actor. 공유 토큰 호출은 사람이 아니라 스크립트이므로
    출처를 표시한다. Identity.actor 자체는 건드리지 않는다 -- 그 값은 에이전트 인증,
    특권 판정, LDAP/denylist 해석, 소유권 검사가 쓴다."""
    return identity.actor if identity.auth == "session" else f"{_RESERVED_ACTOR_PREFIX}{identity.actor}"


def tokens_match(supplied: str, expected: str) -> bool:
    return hmac.compare_digest(
        supplied.encode("utf-8", "surrogateescape"),
        expected.encode("utf-8", "surrogateescape"))


def current_identity(request: Request) -> Identity:
    settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):]
        if tokens_match(token, settings.shared_token):
            actor = request.headers.get("x-dms-actor", "shared-token")
            if actor.startswith(_RESERVED_ACTOR_PREFIX):
                raise HTTPException(status_code=401, detail="invalid_actor")
            return Identity(actor=actor, role="admin", auth="token")
        raise HTTPException(status_code=401, detail="invalid_token")
    session = request.session
    username = session.get("username")
    if username:
        account = request.app.state.repos.accounts.get(username)
        if account is None or account["disabled"]:
            request.session.clear()
            raise HTTPException(status_code=401, detail="account_disabled")
        return Identity(actor=username, role=account["role"], auth="session")
    raise HTTPException(status_code=401, detail="not_authenticated")


def require_user(request: Request) -> Identity:
    return current_identity(request)


def require_admin(request: Request) -> Identity:
    identity = current_identity(request)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return identity
