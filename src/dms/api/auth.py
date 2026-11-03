import hmac
import ipaddress
import re
from collections import namedtuple
from fastapi import HTTPException, Request

from ..identity import privilege_eligible
from ..privileged import privileged_names

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
# requester_id -> denylist·k8s 맵핑 판정, 스캔 경로 소유권, 계정 자기잠금 가드가 전부 이
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
    특권 판정, denylist·k8s 맵핑 판정, 소유권 검사가 쓴다."""
    return identity.actor if identity.auth == "session" else f"{_RESERVED_ACTOR_PREFIX}{identity.actor}"


def client_ip(request: Request) -> str | None:
    """요청의 원 IP -- **기록·경보 전용**이다(2026-11-03, 설계 v6 §5 B안). 어떤 상한·거절의 키도 아니다.

    순서: X-Forwarded-For 의 **마지막** 항목(여러 헤더면 이어 붙인 뒤의 마지막, ipaddress 로 형식 검증 -- 통과하면
    정규화 표기) → 없거나 IP 가 아니면 소켓 피어(request.client.host, 검증 안 함 -- TestClient 는 "testclient") → 둘 다
    없으면 None.

    「왜」(2026-11-03):
    - 앞단 프록시는 1개라는 계약이다 -- Envoy 가 XFF 끝에 PROXY 프로토콜로 받은 원 IP 를 덧붙인다(clientIPDetection
      미설정 = use_remote_address). 그래서 클라이언트가 심을 수 있는 앞 항목이 아니라 마지막 항목을 쓴다. ingress-nginx
      (지금 테스트베드 입구)도 마지막 항목이 nginx 가 본 접속 주소라 같은 규칙이 맞다(덧붙이거나 $remote_addr 로 덮는다).
    - X-Real-IP 는 읽지 않는다: Envoy 의 X-Real-IP 제거 정책이 빠지면 클라이언트가 보낸 위조값(실측 6.6.6.6)이 그대로
      온다. XFF 마지막 항목은 정책이 빠지거나 틀려도 원 IP 와 맞았다(v3 2(마) 실측 V0~V5).
    - IP 를 차단 키로 쓰지 않는다: NAT·SNAT·점프 서버 뒤의 여러 사람이 함께 잠기고, 앞단 계약이 하나라도 틀리면 헤더를
      위조해 남을 잠글 수 있다. 계약이 깨져도 이 값으로는 기록이 틀릴 뿐 판정은 틀리지 않게 하려는 것이다 -- 이 값으로
      거절하는 경로를 새로 만들지 마라(로그인·인증 메일·인증번호의 IP 축 셋은 이날 지웠다).
    - 프록시 없이 직접 닿는 배치(port-forward·테스트)에선 XFF 를 클라이언트가 심을 수 있다 -- 역시 기록만 틀린다. 위조
      IP 를 바꿔 가며 출발지 경보를 부풀려도 events 유입은 창당 몫으로 묶인다(login_guard.LoginAlarms.note_failure,
      2026-11-03 리뷰)."""
    forwarded = ",".join(request.headers.getlist("x-forwarded-for"))
    if forwarded.strip():
        last = forwarded.rsplit(",", 1)[-1].strip()
        try:
            return str(ipaddress.ip_address(last))
        except ValueError:
            pass
    return request.client.host if request.client else None


def request_origin(request: Request) -> dict:
    """보안 이벤트 payload 의 출발지 두 칸(2026-11-03): client_ip(위 원 IP)와 peer(소켓 피어 -- 앞단 프록시의 주소).
    둘을 함께 남기는 이유: 앞단 계약이 깨졌을 때(XFF 를 덧붙이지 않는 프록시·직접 접속) 운영자가 기록만 보고도 어느 쪽이
    맞는지 가늠하게. 기록 전용이다(client_ip 와 같다)."""
    return {"client_ip": client_ip(request),
            "peer": request.client.host if request.client else None}


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
        state = request.app.state.repos.accounts.session_state(username)
        if state is None or state["disabled"]:
            request.session.clear()
            raise HTTPException(status_code=401, detail="account_disabled")
        # 세션 세대(2026-11-03): 비밀번호가 바뀌면(셀프·관리자 재설정·set_password) accounts.session_epoch 가 회전하고, 계정을
        # 지웠다 다시 만들어도 새 값이다 -- 쿠키에 얼린 세대와 다르면 그 세션은 끝났다. 배포 전 세션(epoch 키 없음)과 옛 계정
        # (session_epoch NULL)은 None == None 으로 그대로 유효하다(백필하지 않는다 -- 첫 비밀번호 변경부터 끊긴다). Bearer 경로는
        # 대상이 아니다(위에서 이미 돌아갔다).
        if session.get("epoch") != state["session_epoch"]:
            request.session.clear()
            raise HTTPException(status_code=401, detail="session_revoked")
        return Identity(actor=username, role=state["role"], auth="session")
    raise HTTPException(status_code=401, detail="not_authenticated")


def can_run_as_root(identity: Identity, settings, repos) -> bool:
    """이 로그인이 잡을 root 로 낼 수 있나(2026-09-30): 관리자 역할 + identity.
    privilege_eligible(허용 설정·세션 인증·특권 목록). 제출 게이트(routes_requests)의
    판정이자 /api/auth/me 가 포탈에 알려 주는 값 -- 포탈은 이걸로 'root 권한으로 실행'
    을 보여 주고 기본으로 켤지 정하지만, 표시일 뿐 서버가 제출 때 다시 본다. 특권 목록 =
    env ∪ 계정 화면 부여(privileged.privileged_names, 2026-10-12)."""
    return identity.role == "admin" and privilege_eligible(
        requester_id=identity.actor,
        allow_privileged=settings.allow_privileged_requesters,
        privileged_requesters=privileged_names(settings, repos),
        session_authenticated=(identity.auth == "session"))


def require_user(request: Request) -> Identity:
    return current_identity(request)


def require_admin(request: Request) -> Identity:
    identity = current_identity(request)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return identity


def require_session_admin(request: Request) -> Identity:
    """배포 경로(빌드·릴리스·컨트롤 상태·레지스트리 삭제·artifact base 변경)는 **세션으로 로그인한 관리자**만
    (2026-10-07 리뷰). 공유 토큰은 모든 노드 에이전트가 가진 role admin 자격인데, 그것으로 릴리스를 내 수정 전
    이미지로 롤백하거나(계정·배치 특권 게이트가 통째로 무효) 빌드 소스·노드를 바꿔 root 로 도는 제어면·잡 이미지를
    갈아 끼울 수 있었다. 에이전트는 /api/agent/* 만 부르므로 영향이 없다. 조회(GET)는 토큰도 된다.
    작업(요청) 삭제(2026-10-08, routes_request_purge)도 같다 -- 실행을 줄이는 게 아니라 rm/sync 를 누가 컨펌·취소했는지의
    유일한 기록(전이)과 root 실행 산출물을 없애는 증거 삭제라, 공유 토큰으로 할 수 있으면 안 된다."""
    identity = require_admin(request)
    if identity.auth != "session":
        raise HTTPException(status_code=403, detail="admin_session_required")
    return identity
