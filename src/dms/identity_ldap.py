"""ldap3 기반 실행 신원 resolver. LDAP 접근은 주입된 connection factory 뒤에 있다.

프로덕션 호환 3종(2026-08-22, 사용자 sssd.conf 실측 — supercom.samsung.
2026-08-23 사용자 결정: sssd.conf 값이 **기본값**이다 — uniqueMember·StartTLS 가
기본이고, rfc2307(memberUid)·평문 LDAP 쪽이 env 로 명시하는 예외다. 테스트베드
LDAP 도 프로덕션 미러(rfc2307bis 이중 그룹·StartTLS·검색 계정)로 맞춰져 있다):
- 그룹 스키마: rfc2307(posixGroup, memberUid=<uid>)만 알던 것을
  rfc2307bis(groupOfUniqueNames 류, uniqueMember=<사용자 DN>)도 해석한다.
  스위치는 group_member_attr 하나다 -- "memberUid" 면 uid 로, 그 외(member/
  uniqueMember)면 사용자 DN 으로 그룹을 검색한다(sssd 의 ldap_schema +
  ldap_group_member 두 값을 속성명 하나로 접었다).
- StartTLS: sssd `ldap_id_use_start_tls = true` 미러. 인증서 검증은 하지 않는다
  (sssd `ldap_tls_reqcert = never` 미러 -- 사내 LDAP 가 사설 인증서라 검증을
  켜면 어느 노드에서도 안 붙는다. 검증 옵션이 필요해지면 그때 연다).
- 다중 URI: sssd `ldap_uri = ldap://a/, ldap://b/, …` 처럼 콤마 목록을 받아 **순서대로 하나씩**
  연결을 시도한다(2026-10-07). 예전 ServerPool(FIRST, active=True, exhaust=True)은 한 번 실패한 서버를 풀
  인스턴스에서 영구히 건너뛰어, LDAP 가 돌아와도 connect() 가 영원히 끝나지 않았다(타임아웃도 없어 단일 스레드
  컨트롤러가 통째로 멈췄다). 이제 연결·StartTLS·bind·검색 각각에 ldap_timeout_seconds 상한이 있고, 한 번의
  resolve 는 대략 (URI 수 × (연결 + 2) + 검색 2) × 상한 안에 끝나며 연결은 매번 unbind 한다.
- 그룹 cn 은 다중값일 수 있다: 값을 모두 펼쳐 이름 목록에 넣는다(예전엔 리스트가 그대로 흘러 정렬 TypeError
  또는 denylist 비교 AttributeError 로 요청이 매 틱 plan_error 를 남기며 Pending 에 영구히 남았다). 이름이 늘면
  denylist 거부가 느는 쪽이라 안전하다.
"""
import math

from .identity import IdentityUnavailable, ResolvedIdentity


_FILTER_ESCAPE = {"\\": r"\5c", "*": r"\2a", "(": r"\28", ")": r"\29", "\0": r"\00"}


def _escape_filter(value: str) -> str:
    """RFC 4515 LDAP filter escaping to prevent injection."""
    return "".join(_FILTER_ESCAPE.get(ch, ch) for ch in value)


def _parse_uris(uri: str) -> list[str]:
    """sssd `ldap_uri` 형식(콤마 목록, 후행 '/' 관례)을 ldap3 host 목록으로."""
    return [u.strip().rstrip("/") for u in uri.split(",") if u.strip()]


def _attr_values(attr) -> list:
    """ldap3 속성 값 → 목록. 단일값은 [v], 다중값은 그대로, 없음(None)은 []."""
    value = getattr(attr, "value", None)
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


class LdapIdentityResolver:
    def __init__(self, *, connect, user_base, group_base,
                 group_member_attr="uniqueMember"):
        self._connect = connect
        self._user_base = user_base
        self._group_base = group_base
        self._group_member_attr = group_member_attr

    def resolve(self, username: str):
        conn = None
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
            groups = tuple(sorted({str(cn) for e in conn.entries for cn in _attr_values(e["cn"])}))
        except IdentityUnavailable:
            raise
        except Exception as exc:
            raise IdentityUnavailable(str(exc)[:200])
        finally:
            _unbind(conn)
        return ResolvedIdentity(username, uid, gid, groups, False)


def _unbind(conn) -> None:
    """연결을 매번 닫는다(조회마다 새 연결 -- 소켓·서버 세션이 쌓이지 않게). 닫기 실패는 결과를 바꾸지 않는다."""
    if conn is None:
        return
    unbind = getattr(conn, "unbind", None)
    if unbind is None:
        return
    try:
        unbind()
    except Exception:
        pass


def ldap_directory_config(settings):
    """LDAP 디렉터리 설정의 **유일한 추출점**(2026-09-29). 제어면 리졸버
    (build_ldap_resolver)와 에이전트 nslcd(agent_directory.directory_block 이 보고
    응답으로 내려준다)가 둘 다 이 함수의 결과만 쓴다 -- 같은 설정을 두 경로로 따로
    배선했다가(API 는 envFrom, 에이전트는 개별 env) 에이전트 쪽에서 bind 계정이 빠져
    프로덕션 전 요청이 identity_not_ready_on_node 가 된 사고의 재발 방지다. 플래너는
    에이전트 보고로 신원 준비를 판정하므로 둘은 같은 디렉터리·같은 계정이어야 한다.

    None = 미구성(엔드포인트·베이스 중 하나라도 자리표시자/빈 값, fail-closed)."""
    from .config import _is_placeholder  # local import to avoid cycle
    uri = getattr(settings, "ldap_uri", None)
    user_base = getattr(settings, "ldap_user_base", None)
    group_base = getattr(settings, "ldap_group_base", None)
    if _is_placeholder(uri) or _is_placeholder(user_base) or _is_placeholder(group_base):
        return None
    # 결측 폴백도 프로덕션 기본과 같은 방향(uniqueMember/StartTLS) -- 기본값이
    # 곧 sssd.conf 값이라는 원칙을 duck-typed settings 에도 일관 적용.
    return {
        # 콤마 목록 → 페일오버 목록(sssd ldap_uri 형식 미러).
        "uris": _parse_uris(uri),
        "user_base": user_base,
        "group_base": group_base,
        "start_tls": bool(getattr(settings, "ldap_use_start_tls", True)),
        "bind_dn": getattr(settings, "ldap_bind_dn", "") or None,
        "bind_pw": getattr(settings, "ldap_bind_pw", "") or None,
        "group_member_attr": getattr(settings, "ldap_group_member_attr", "") or "uniqueMember",
    }


def connect_first(uris, open_one):
    """URI 를 순서대로 하나씩 열어 처음 성공한 연결을 돌려준다(sssd ldap_uri 페일오버 미러). 전부 실패하면
    IdentityUnavailable -- 각 URI 의 실패 사유를 모아 남긴다. 한 URI 의 시도는 open_one 이 건 타임아웃(연결·
    StartTLS·bind 각각)으로 유한하다. ServerPool 을 쓰지 않는 이유는 모듈 docstring."""
    errors = []
    for uri in uris:
        try:
            return open_one(uri)
        except Exception as exc:
            errors.append(f"{uri}: {type(exc).__name__}: {exc}"[:200])
    raise IdentityUnavailable("; ".join(errors)[:500] or "no ldap uri")


def build_ldap_resolver(settings):
    cfg = ldap_directory_config(settings)
    if cfg is None:
        return None
    user_base, group_base = cfg["user_base"], cfg["group_base"]
    use_start_tls = cfg["start_tls"]
    group_member_attr = cfg["group_member_attr"]
    timeout = float(getattr(settings, "ldap_timeout_seconds", 5.0))
    # receive_timeout 은 정수여야 한다 -- ldap3 가 SO_RCVTIMEO 로 struct.pack 한다(실 LDAP 확인: 실수면
    # "required argument is not an integer" 로 모든 연결이 실패했다). 올림해 상한을 줄이지 않는다.
    receive_timeout = max(1, math.ceil(timeout))

    def connect():
        import ldap3
        tls = None
        if use_start_tls:
            import ssl
            # reqcert=never 미러 -- 사설 인증서 검증 생략(모듈 docstring).
            tls = ldap3.Tls(validate=ssl.CERT_NONE)
        # StartTLS 는 bind **전에** 올라가야 자격증명이 평문으로 새지 않는다 --
        # ldap3 의 AUTO_BIND_TLS_BEFORE_BIND 가 그 순서를 보장한다.
        auto_bind = ldap3.AUTO_BIND_TLS_BEFORE_BIND if use_start_tls else True
        return connect_first(cfg["uris"], lambda uri: ldap3.Connection(
            ldap3.Server(uri, tls=tls, connect_timeout=timeout),
            user=cfg["bind_dn"], password=cfg["bind_pw"], auto_bind=auto_bind,
            receive_timeout=receive_timeout))

    return LdapIdentityResolver(connect=connect, user_base=user_base,
                                group_base=group_base,
                                group_member_attr=group_member_attr)
