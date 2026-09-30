"""실행 신원 모델. LDAP 조회는 주입된 resolver 뒤에 있고, 이 모듈은 오케스트레이션만 한다."""
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ResolvedIdentity:
    username: str
    uid: int
    gid: int
    groups: tuple[str, ...]
    privileged: bool


class IdentityUnavailable(Exception):
    """resolver 백엔드(LDAP)가 조회 불가 — fail-closed 대상."""


class IdentityResolver(Protocol):
    def resolve(self, username: str) -> "ResolvedIdentity | None":
        ...


class StubIdentityResolver:
    def __init__(self, users: dict, *, unavailable: bool = False):
        self._users = users
        self._unavailable = unavailable

    def resolve(self, username: str):
        if self._unavailable:
            raise IdentityUnavailable(username)
        return self._users.get(username)


class IdentityRejected(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


PRIVILEGE_NEVER = "never"            # 단건 작업 신청 기본: 실행 신원의 LDAP uid/gid
PRIVILEGE_REQUESTED = "requested"    # 요청이 명시적으로 root 실행을 원함(run_as_root)
PRIVILEGE_IF_ELIGIBLE = "if_eligible"  # 배치 자식(관리자 전용 화면): 자격 있으면 root


def privilege_policy(req) -> str:
    """요청 행 → privilege 정책(단일 규칙, 2026-09-30). planner 가 신원을 정할 때와
    stepper 가 매 제출 직전에 "이 root 잡에 근거가 있나"를 재확인할 때 같은 함수를
    쓴다 -- 두 곳이 규칙을 따로 들면 한쪽만 바뀌는 순간 재확인이 뚫린다.
      - payload.run_as_root 가 **정확히 True** → REQUESTED. `is True` 인 이유: payload 는
        DB 신뢰 경계 밖이라 "yes"·1 같은 값으로 승격되지 않게 한다.
      - batch_id 가 있음(배치 자식) → IF_ELIGIBLE.
      - 그 외(행 없음·모양 틀림 포함) → NEVER(fail-closed)."""
    if not isinstance(req, dict):
        return PRIVILEGE_NEVER
    payload = req.get("payload")
    if isinstance(payload, dict) and payload.get("run_as_root") is True:
        return PRIVILEGE_REQUESTED
    if req.get("batch_id"):
        return PRIVILEGE_IF_ELIGIBLE
    return PRIVILEGE_NEVER


def privilege_eligible(*, requester_id, allow_privileged, privileged_requesters,
                       session_authenticated) -> bool:
    """root 실행 자격(스펙 §5): 설정이 허용하고, 세션 인증이고, 요청자가 목록에 있다.
    **자격 ≠ 사용**(2026-09-30): 자격만으로 root 가 되지 않는다 -- privilege 정책이
    정한다(resolve_job_identity)."""
    return bool(allow_privileged and session_authenticated
                and requester_id in privileged_requesters)


def resolve_job_identity(control, resolver, *, requester_id, owner_username,
                         allow_privileged, privileged_requesters,
                         session_authenticated: bool = False,
                         privilege: str = PRIVILEGE_NEVER) -> ResolvedIdentity:
    """잡 실행 신원.

    privilege(2026-09-30 프로덕션 사고): 예전엔 요청자가 특권 목록에 있기만 하면 어느
    화면에서 무엇을 제출하든 uid 0 이었다 -- 관리자 계정이 작업 신청 화면에서 "실행
    신원 = 일반 사용자" 로 sync 를 내도 root 로 돌아, 그 사용자가 쓸 수 없는 남의 700
    디렉터리를 소스 소유권으로 덮어쓰며 성공했다(dsync 는 소스 최상위의 소유·권한을
    기존 목적지에 적용한다). 이제 root 는 **명시적**일 때만이다:
      - PRIVILEGE_NEVER(기본, fail-closed): 자격이 있어도 실행 신원(owner_username 또는
        요청자)의 LDAP uid/gid -- 그 사용자의 파일 권한이 그대로 적용된다.
      - PRIVILEGE_REQUESTED: 요청이 run_as_root 를 명시. 자격이 없으면 거부
        (privileged_not_authorized) -- 명시한 요청을 조용히 비특권으로 낮추지 않는다.
      - PRIVILEGE_IF_ELIGIBLE: 배치 자식(관리자 전용 화면, 스펙 §5 "관리자 인터페이스에서만")
        -- 자격이 있으면 root(종전 동작 유지)."""
    owner = (owner_username or requester_id).strip()
    eligible = privilege_eligible(requester_id=requester_id,
                                  allow_privileged=allow_privileged,
                                  privileged_requesters=privileged_requesters,
                                  session_authenticated=session_authenticated)
    if privilege == PRIVILEGE_REQUESTED and not eligible:
        raise IdentityRejected("privileged_not_authorized", requester_id)
    # denylist는 최우선 kill-switch이고 특권 경로보다 먼저 평가된다 (스펙 §5).
    # group 규칙이 등재돼 있을 때만 특권 경로에서도 그룹을 해석한다 — 규칙이 없으면
    # 특권 경로는 지금처럼 LDAP 없이 통과한다.
    groups: list[str] = []
    # 특권 승격은 session 인증 요청에만 허용한다(슬라이스 19 심층 방어, 설계 §2.2-2):
    # 공유 토큰 경로는 requester_id 를 자유 지정할 수 없게 이미 좁혔지만(Task 1),
    # 토큰으로 들어온 요청은 여기서도 특권을 못 얻는다.
    #
    # 기본값은 **fail-closed(False)** 다. 지금 프로덕션 호출자는 planner 한 곳뿐이고
    # 그곳은 요청의 auth_method 로 실제 값을 명시해 넘긴다 -- 기본값이 쓰이는 정상
    # 경로는 없다. 그래도 False 인 이유는 미래의 두 번째 호출자다: 기본이 True 면
    # 인자를 빠뜨린 새 호출자가 **조용히 uid 0 승격 경로를 되살린다**(이 슬라이스가
    # 막으려는 바로 그 결함). 빠뜨리면 특권이 안 붙는 쪽이 안전하다 -- 그 실수는
    # "권한이 부족하다"로 시끄럽게 드러나지 보안 구멍으로 잠복하지 않는다.
    privileged = eligible and privilege in (PRIVILEGE_REQUESTED, PRIVILEGE_IF_ELIGIBLE)
    if privileged and control.has_group_denies():
        if resolver is None:
            raise IdentityRejected("ldap_not_configured")
        try:
            probe = resolver.resolve(owner)
        except IdentityUnavailable as exc:
            raise IdentityRejected("ldap_unavailable", str(exc)[:200])
        if probe is not None:
            groups = list(probe.groups)
    denied = control.is_denied(requester=requester_id, owner=owner, groups=groups)
    if denied:
        raise IdentityRejected("identity_denied", denied)
    if privileged:
        return ResolvedIdentity(owner, 0, 0, (), True)
    if resolver is None:
        raise IdentityRejected("ldap_not_configured")
    try:
        resolved = resolver.resolve(owner)
    except IdentityUnavailable as exc:
        raise IdentityRejected("ldap_unavailable", str(exc)[:200])
    if resolved is None:
        raise IdentityRejected("ldap_identity_not_found", owner)
    denied = control.is_denied(requester=requester_id, owner=owner,
                               groups=list(resolved.groups))
    if denied:
        raise IdentityRejected("identity_denied", denied)
    if resolved.uid == 0:
        # 비특권 경로가 uid 0 을 얻는 유일한 방법은 디렉터리의 uidNumber=0 항목이다
        # (예: posix root 를 LDAP 에 실은 사이트). root 실행은 특권 요청자 게이트
        # (privileged) 만의 권한이라 계획 시점에 정확한 사유로 거부한다 -- stepper 의
        # identity_problem(privileged_flag_mismatch) 은 변조 행용 백스톱이다.
        raise IdentityRejected("identity_root_without_privilege", owner)
    control.register_probe_target(owner)
    return ResolvedIdentity(owner, resolved.uid, resolved.gid,
                            tuple(resolved.groups), False)
