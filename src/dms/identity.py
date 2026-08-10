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


def resolve_job_identity(control, resolver, *, requester_id, owner_username,
                         allow_privileged, privileged_requesters,
                         session_authenticated: bool = True) -> ResolvedIdentity:
    owner = (owner_username or requester_id).strip()
    # denylist는 최우선 kill-switch이고 특권 경로보다 먼저 평가된다 (스펙 §5).
    # group 규칙이 등재돼 있을 때만 특권 경로에서도 그룹을 해석한다 — 규칙이 없으면
    # 특권 경로는 지금처럼 LDAP 없이 통과한다.
    groups: list[str] = []
    # 특권 승격은 session 인증 요청에만 허용한다(슬라이스 19 심층 방어, 설계 §2.2-2):
    # 공유 토큰 경로는 requester_id 를 자유 지정할 수 없게 이미 좁혔지만(Task 1),
    # 토큰으로 들어온 요청은 여기서도 특권을 못 얻는다. 기본값 True 는 이 함수의 유일
    # 프로덕션 호출자(planner)가 요청의 auth_method 로 실제 값을 넘기기 때문에 정상
    # 경로에선 쓰이지 않는다 -- 직접 호출하는 단위 테스트의 편의를 위한 기본이다.
    privileged = (allow_privileged and session_authenticated
                  and requester_id in privileged_requesters)
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
    control.register_probe_target(owner)
    return ResolvedIdentity(owner, resolved.uid, resolved.gid,
                            tuple(resolved.groups), False)
