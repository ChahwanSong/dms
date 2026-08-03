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
                         allow_privileged, privileged_requesters) -> ResolvedIdentity:
    owner = (owner_username or requester_id).strip()
    denied = control.is_denied(requester=requester_id, owner=owner, groups=[])
    if denied:
        raise IdentityRejected("identity_denied", denied)
    if allow_privileged and requester_id in privileged_requesters:
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
