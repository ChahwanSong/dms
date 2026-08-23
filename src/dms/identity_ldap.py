"""ldap3 기반 실행 신원 resolver. LDAP 접근은 주입된 connection factory 뒤에 있다.

프로덕션 호환 3종(2026-08-22, 사용자 sssd.conf 실측 — supercom.samsung):
- 그룹 스키마: rfc2307(posixGroup, memberUid=<uid>)만 알던 것을
  rfc2307bis(groupOfUniqueNames 류, uniqueMember=<사용자 DN>)도 해석한다.
  스위치는 group_member_attr 하나다 -- "memberUid" 면 uid 로, 그 외(member/
  uniqueMember)면 사용자 DN 으로 그룹을 검색한다(sssd 의 ldap_schema +
  ldap_group_member 두 값을 속성명 하나로 접었다).
- StartTLS: sssd `ldap_id_use_start_tls = true` 미러. 인증서 검증은 하지 않는다
  (sssd `ldap_tls_reqcert = never` 미러 -- 사내 LDAP 가 사설 인증서라 검증을
  켜면 어느 노드에서도 안 붙는다. 검증 옵션이 필요해지면 그때 연다).
- 다중 URI: sssd `ldap_uri = ldap://a/, ldap://b/, …` 처럼 콤마 목록을 받아
  ServerPool(FIRST, active) 로 페일오버한다. 단일 URI 는 기존과 동일.
"""
from .identity import IdentityUnavailable, ResolvedIdentity


_FILTER_ESCAPE = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\0": r"\00"}


def _escape_filter(value: str) -> str:
    """RFC 4515 LDAP filter escaping to prevent injection."""
    return "".join(_FILTER_ESCAPE.get(ch, ch) for ch in value)


def _parse_uris(uri: str) -> list[str]:
    """sssd `ldap_uri` 형식(콤마 목록, 후행 '/' 관례)을 ldap3 host 목록으로."""
    return [u.strip().rstrip("/") for u in uri.split(",") if u.strip()]


class LdapIdentityResolver:
    def __init__(self, *, connect, user_base, group_base,
                 group_member_attr="memberUid"):
        self._connect = connect
        self._user_base = user_base
        self._group_base = group_base
        self._group_member_attr = group_member_attr

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
            # 그룹 매칭 값: rfc2307(memberUid)은 uid 문자열, rfc2307bis 계열
            # (member/uniqueMember)은 **사용자 엔트리의 전체 DN** 이다. DN 은
            # 방금 검색한 entry 에서 그대로 얻는다 -- 조립하지 않는다(RDN 형식을
            # 지어내면 ou 구조가 다른 디렉터리에서 조용히 0건이 된다).
            member_value = safe if self._group_member_attr == "memberUid" \
                else _escape_filter(entry.entry_dn)
            conn.search(self._group_base,
                        f"({self._group_member_attr}={member_value})",
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
    use_start_tls = bool(getattr(settings, "ldap_use_start_tls", False))
    group_member_attr = getattr(settings, "ldap_group_member_attr", "") or "memberUid"

    def connect():
        import ldap3
        # 콤마 목록 → 페일오버 풀(sssd ldap_uri 형식 미러).
        uris = _parse_uris(uri)
        tls = None
        if use_start_tls:
            import ssl
            # reqcert=never 미러 -- 사설 인증서 검증 생략(모듈 docstring).
            tls = ldap3.Tls(validate=ssl.CERT_NONE)
        servers = [ldap3.Server(u, tls=tls) for u in uris]
        server = servers[0] if len(servers) == 1 else ldap3.ServerPool(
            servers, ldap3.FIRST, active=True, exhaust=True)
        bind_dn = getattr(settings, "ldap_bind_dn", "") or None
        bind_pw = getattr(settings, "ldap_bind_pw", "") or None
        # StartTLS 는 bind **전에** 올라가야 자격증명이 평문으로 새지 않는다 --
        # ldap3 의 AUTO_BIND_TLS_BEFORE_BIND 가 그 순서를 보장한다.
        auto_bind = ldap3.AUTO_BIND_TLS_BEFORE_BIND if use_start_tls else True
        conn = ldap3.Connection(server, user=bind_dn, password=bind_pw,
                                auto_bind=auto_bind)
        return conn

    return LdapIdentityResolver(connect=connect, user_base=user_base,
                                group_base=group_base,
                                group_member_attr=group_member_attr)
