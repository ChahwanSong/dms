import hmac
import re
from collections import namedtuple
from fastapi import HTTPException, Request

# 예약 접두 -- audit_actor()가 토큰 인증 actor 앞에 붙이는 표식과 동일하다. 호출자가
# x-dms-actor에 이 접두를 직접 넣으면 감사 로그에서 서버가 붙인 표식과 구분이 안 돼
# 사람 admin으로 위장할 수 있으므로 여기서 거절한다.
_RESERVED_ACTOR_PREFIX = "token:"

# 에이전트 노드 이름 규칙(DNS-1123). routes_agent 의 잡 신원 검증(ingest_report)이
# 쓰는 바로 그 규칙이어야 한다 -- 토큰 경로에서 여기서 통과시킨 node:<이름> 을
# 에이전트 라우트가 다시 검증하므로, 두 곳이 갈라지면 한쪽이 받은 값을 다른 쪽이
# 거절한다. 그래서 정의는 여기 한 곳뿐이고 routes_agent 가 이 상수를 import 한다.
_NODE_NAME_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]{0,252}[A-Za-z0-9])?$")

# Identity.actor 자체는 절대 건드리지 않는다 -- 에이전트 노드 인증(routes_agent.py),
# 특권 요청자 판정(routes_requests.py의 settings.privileged_requesters),
# requester_id -> LDAP/denylist 해석, 스캔 경로 소유권, 계정 자기잠금 가드가 전부 이
# 값을 그대로 비교한다. 대신 인증 방식만 auth 필드로 따로 들고 다니고, 감사 로그를
# 쓰는 라우트에서만 audit_actor()를 거쳐 표시용 actor를 만든다.
# auth 필드에는 일부러 기본값을 두지 않는다 -- current_identity()의 두 반환 지점이
# 이미 둘 다 명시적으로 채우므로 기본값은 어떤 정상 경로에서도 쓰이지 않는다. 만약
# 앞으로 생기는 세 번째 생성 지점이 이 필드를 빠뜨리면, 기본값이 있는 순간 조용히
# "session"으로 분류돼 토큰 호출이 사람 admin으로 감사 로그에 남는다 -- 감사 로그가
# 거짓말하는 것보다는 그 자리에서 TypeError로 즉시 터지는 편이 낫다.
Identity = namedtuple("Identity", "actor role auth")


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
            # 슬라이스 19: 토큰 경로의 x-dms-actor 는 node:<이름>(에이전트 전용)만
            # 허용한다. 빈 값/공백만은 기존대로 shared-token 으로 정규화하고(옆문 유지
            # 금지 -- 헤더 부재/빈 문자열/공백 세 가지를 한 값으로 모아 빈 "token:"
            # 감사 위장을 막는다), 그 외 임의 값(root, alice, token:x ...)은 400 으로
            # 거절한다. 이 게이트가 공유 토큰 보유자가 잡 신원을 자유 지정해 uid 0 으로
            # 승격하던 스푸핑(설계 §1-4)을 닫는다: requester_id 가 사람 이름/특권
            # 이름이 될 여지가 사라진다. token: 접두 거절(구 401)은 더 넓은 이 게이트에
            # 흡수된다(node: 아님 -> 400).
            raw = (request.headers.get("x-dms-actor") or "").strip()
            if not raw:
                actor = "shared-token"
            elif raw.startswith("node:") and _NODE_NAME_RE.fullmatch(raw[len("node:"):]):
                actor = raw
            else:
                raise HTTPException(status_code=400, detail="invalid_actor")
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
