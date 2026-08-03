"""ldap3 기반 실행 신원 resolver. LDAP 접근은 주입된 connection factory 뒤에 있다."""
from .identity import IdentityUnavailable, ResolvedIdentity


_FILTER_ESCAPE = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\0": r"\00"}


def _escape_filter(value: str) -> str:
    """RFC 4515 LDAP filter escaping to prevent injection."""
    return "".join(_FILTER_ESCAPE.get(ch, ch) for ch in value)


class LdapIdentityResolver:
    def __init__(self, *, connect, user_base, group_base):
        self._connect = connect
        self._user_base = user_base
        self._group_base = group_base

    def resolve(self, username: str):
        try:
            conn = self._connect()
            safe = _escape_filter(username)
            conn.search(self._user_base, f"(uid={safe})",
                        attributes=["uidNumber", "gidNumber"])
            if not conn.entries:
                return None
            entry = conn.entries[0]
            uid = int(entry["uidNumber"].value)
            gid = int(entry["gidNumber"].value)
            conn.search(self._group_base, f"(memberUid={safe})",
                        attributes=["cn"])
            groups = tuple(sorted(e["cn"].value for e in conn.entries))
        except IdentityUnavailable:
            raise
        except Exception as exc:
            raise IdentityUnavailable(str(exc)[:200])
        return ResolvedIdentity(username, uid, gid, groups, False)


def build_ldap_resolver(settings):
    from .config import _is_placeholder  # local import to avoid cycle
    uri = getattr(settings, "ldap_uri", None)
    user_base = getattr(settings, "ldap_user_base", None)
    group_base = getattr(settings, "ldap_group_base", None)
    if _is_placeholder(uri) or _is_placeholder(user_base) or _is_placeholder(group_base):
        return None

    def connect():
        import ldap3
        server = ldap3.Server(uri)
        bind_dn = getattr(settings, "ldap_bind_dn", "") or None
        bind_pw = getattr(settings, "ldap_bind_pw", "") or None
        conn = ldap3.Connection(server, user=bind_dn, password=bind_pw,
                                auto_bind=True)
        return conn

    return LdapIdentityResolver(connect=connect, user_base=user_base,
                                group_base=group_base)
