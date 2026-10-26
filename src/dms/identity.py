"""실행 신원 모델(dms-k8s -- k8s 신원 모드 전용).

모든 잡은 root(uid 0·gid 0)로 돈다(dms-k8s 설계 D2). 실행 신원 이름(username)은 owner_username 또는 요청자이고, 러너가
uid 0 별칭 계정으로 passwd 에 물질화해 runuser·sshd 가 쓴다(execution_manifests._identity_materialize_stmt). 권한 판정은 이
모듈이 아니라 k8s RoleBinding 맵핑(k8s_access)이 맡는다 -- 이 모듈은 「root 로 돌아도 되는 요청인가」(root_allowed·
privilege_eligible)와 denylist(이름 규칙)만 본다.

LDAP 리졸버·보조 그룹(posixGroup gidNumber)·비 root 실행 경로는 2026-10-26 사용자 결정 (d) 로 지웠다(dms-ssc 라인은 LDAP 을
계속 쓴다 -- 근거는 docs/history·git 히스토리). 그래서 root 근거가 없는 요청(PRIVILEGE_NEVER·자격 없는 배치 자식)은 예전처럼
「실행 신원의 LDAP uid/gid」로 낮추지 않고 privileged_not_authorized 로 **거부**한다 -- 낮출 비 root 신원이 없고, root 로
올리면 근거 없는 uid 0 실행이다.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedIdentity:
    """planner 가 worker_pool.identity 에 asdict 로 얼리는 스냅숏. groups 는 늘 ()(예전 LDAP 그룹 이름 칸 -- 스냅숏 모양을
    유지한다). stepper.identity_problem 이 이 모양을 다시 본다."""
    username: str
    uid: int
    gid: int
    groups: tuple[str, ...]
    privileged: bool


def owner_override_allowed(*, owner_username, requester_id, requester_is_admin,
                           allow_privileged, privileged_requesters) -> bool:
    """다른 실행 신원 지정(owner_username ≠ 요청자)은 특권이다 -- API(routes_requests.submit)와 planner 가 **같은
    술어**를 쓴다(planner 는 API 를 거치지 않은 DB 직접 쓰기 방어 -- 그 경로가 남의 이름을 실행 신원으로 얻는다).
    비교는 **원문 그대로**(strip 없음): API 의 기존 `owner != identity.actor` 와 같아 ' alice' 같은 값의
    403 이 유지된다(validate_owner_username 의 422 는 그 뒤). 세션 요구 없음(API 와 맞춤)."""
    if owner_username is None:
        return True
    if not isinstance(owner_username, str):
        return False                     # DB 변조(int·list) -- 거부 쪽(planner 는 그 전에 invalid_owner_username)
    if owner_username == requester_id:
        return True
    return bool(requester_is_admin and allow_privileged and requester_id in privileged_requesters)


class IdentityRejected(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


PRIVILEGE_NEVER = "never"            # payload 에 root 근거 없음(fail-closed): 거부(privileged_not_authorized)
PRIVILEGE_REQUESTED = "requested"    # payload run_as_root is True(제출이 늘 싣는다 -- 설계 D2)
PRIVILEGE_IF_ELIGIBLE = "if_eligible"  # 배치 자식(관리자 전용 화면): 자격 있으면 root, 없으면 거부


def privilege_policy(req) -> str:
    """요청 행 → privilege 정책(단일 규칙, 2026-09-30). planner 가 신원을 정할 때와
    stepper 가 매 제출 직전에 "이 root 잡에 근거가 있나"를 재확인할 때 같은 함수를
    쓴다 -- 두 곳이 규칙을 따로 들면 한쪽만 바뀌는 순간 재확인이 뚫린다.
      - batch_id 가 있음(배치 자식) → IF_ELIGIBLE. **run_as_root 보다 먼저** 본다(2026-10-26 리뷰): orchestrator 는 자식
        payload 에 run_as_root 를 싣지 않는다 -- 실려 있으면 DB 변조다. REQUESTED 의 술어(root_allowed)는 세션만 보므로
        변조된 자식이 특권 목록 판정(privilege_eligible)을 건너뛰게 두지 않는다.
      - payload.run_as_root 가 **정확히 True** → REQUESTED. `is True` 인 이유: payload 는
        DB 신뢰 경계 밖이라 "yes"·1 같은 값으로 승격되지 않게 한다.
      - 그 외(행 없음·모양 틀림 포함) → NEVER(fail-closed)."""
    if not isinstance(req, dict):
        return PRIVILEGE_NEVER
    if req.get("batch_id"):
        return PRIVILEGE_IF_ELIGIBLE
    payload = req.get("payload")
    if isinstance(payload, dict) and payload.get("run_as_root") is True:
        return PRIVILEGE_REQUESTED
    return PRIVILEGE_NEVER


def privilege_eligible(*, requester_id, allow_privileged, privileged_requesters,
                       session_authenticated) -> bool:
    """root 실행 자격(스펙 §5): 설정이 허용하고, 세션 인증이고, 요청자가 목록에 있다. 배치 자식(IF_ELIGIBLE)·맵핑 면제
    (k8s_access.unrestricted_request)가 쓴다. **자격 ≠ 사용**(2026-09-30): 자격만으로 root 가 되지 않는다 -- privilege
    정책이 정한다(resolve_job_identity)."""
    return bool(allow_privileged and session_authenticated
                and requester_id in privileged_requesters)


# /api/auth/me·k8s 개요의 identity_mode 값(2026-10-26 -- dms-k8s 는 k8s 모드 전용이라 상수다). 옛 포탈 탭이 이 키로 화면을
# 가르므로 키를 지우지 않는다.
IDENTITY_MODE_K8S = "k8s"


def root_allowed(*, allow_privileged, session_authenticated) -> bool:
    """「run_as_root 를 명시한(REQUESTED) 요청이 root 로 돌 수 있나」의 **단일 술어**(dms-k8s 설계 D3, 2026-10-25). 세 지점이
    같은 함수를 쓴다 -- 제출(routes_requests.submit), 계획(resolve_job_identity 의 REQUESTED 경로), 컨펌
    (routes_jobs.confirm_job 의 REQUESTED 재검사). 한쪽만 바뀌면 「제출은 됐는데 컨펌에서 영원히 409」 같은 틈이 생긴다.

    모든 단건 잡이 root 다(D2 -- 서버가 run_as_root 를 강제한다). 권한 판정은 root 자격이 아니라 k8s 맵핑(k8s_access)이
    맡고, 여기서는 **세션 인증 요청인가**만 본다(+ 특권 허용 스위치 -- 끄면 아무것도 돌지 않는다, config 가 기동에서 막는다).
    세션 조건은 빼지 마라: 공유 토큰(모든 노드 에이전트 보유, role admin)이 모든 경로에 root 잡을 내는 길이 된다.
    배치 자식(IF_ELIGIBLE)은 이 술어가 아니라 privilege_eligible 이다(설계 D3 -- 세션만 보도록 넓히면 목록에서 빠진 생성자의
    scan 자식이 root 로 돈다)."""
    return bool(allow_privileged and session_authenticated)


def resolve_job_identity(control, *, requester_id, owner_username,
                         allow_privileged, privileged_requesters,
                         session_authenticated: bool = False,
                         privilege: str = PRIVILEGE_NEVER) -> ResolvedIdentity:
    """잡 실행 신원 -- 통과하면 늘 root(uid 0·gid 0, privileged=True), 아니면 IdentityRejected.

      - PRIVILEGE_REQUESTED: root_allowed(세션 인증)가 아니면 privileged_not_authorized(denylist 보다 먼저 -- 종전 순서).
      - PRIVILEGE_IF_ELIGIBLE(배치 자식): privilege_eligible(허용·세션·특권 목록)이 아니면 privileged_not_authorized.
      - PRIVILEGE_NEVER(근거 없음 -- DB 직접 삽입·run_as_root 없는 옛 행): privileged_not_authorized.
    뒤 둘은 예전엔 「실행 신원의 LDAP uid/gid」로 돌던 경로다(2026-09-30 프로덕션 사고 뒤 root 는 명시일 때만) -- LDAP 이 없는
    이 라인에선 낮출 신원이 없고 root 로 올리면 근거 없는 uid 0 이라 거부한다(2026-10-26).

    denylist 는 최우선 kill-switch 다(스펙 §5): 요청자·실행 신원 이름 규칙을 자격 판정(위 둘째·셋째)보다 먼저 본다 --
    막힌 사용자는 자격과 무관하게 identity_denied. 그룹 규칙은 평가하지 않는다(LDAP 이 없다 -- 추가는 routes_denylist 가
    422 denylist_group_unsupported 로 막는다).

    session_authenticated 기본값은 **fail-closed(False)** 다: 인자를 빠뜨린 미래 호출자가 조용히 uid 0 경로를 얻지 않게
    (슬라이스 19 심층 방어 -- 공유 토큰 요청은 특권을 못 얻는다). 지금 호출자는 planner 한 곳이고 요청의 auth_method 로
    명시해 넘긴다."""
    owner = (owner_username or requester_id).strip()
    if privilege == PRIVILEGE_REQUESTED:
        if not root_allowed(allow_privileged=allow_privileged, session_authenticated=session_authenticated):
            raise IdentityRejected("privileged_not_authorized", requester_id)
        allowed = True
    elif privilege == PRIVILEGE_IF_ELIGIBLE:
        allowed = privilege_eligible(requester_id=requester_id, allow_privileged=allow_privileged,
                                     privileged_requesters=privileged_requesters,
                                     session_authenticated=session_authenticated)
    else:
        allowed = False
    denied = control.is_denied(requester=requester_id, owner=owner)
    if denied:
        raise IdentityRejected("identity_denied", denied)
    if not allowed:
        raise IdentityRejected("privileged_not_authorized", requester_id)
    return ResolvedIdentity(owner, 0, 0, (), True)
