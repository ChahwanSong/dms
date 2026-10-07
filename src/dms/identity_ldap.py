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
  resolve 는 아래 마감 안에서만 새 연산을 시작하며 연결은 매번 unbind 한다.
- 그룹 cn 은 다중값일 수 있다: 값을 모두 펼쳐 이름 목록에 넣는다(예전엔 리스트가 그대로 흘러 정렬 TypeError
  또는 denylist 비교 AttributeError 로 요청이 매 틱 plan_error 를 남기며 Pending 에 영구히 남았다). 이름이 늘면
  denylist 거부가 느는 쪽이라 안전하다.

보조 그룹(2026-10-07, D14):
- 실행에 흐르는 gid 는 **posixGroup 엔트리의 gidNumber 숫자뿐**이다(ResolvedIdentity.group_gids, 원시값 -- 범위
  판정은 identity.valid_supplementary_gids). cn·DN 은 denylist 이름 매칭 전용이라 그룹 검색 **필터 문자열은 그대로**다.
  중첩 그룹은 해석하지 않는다(1단계 멤버십만). gidNumber 가 없거나·다중값·비숫자인 그룹은 gid 만 건너뛰고 이름은
  남긴다(권한을 줄이는 쪽 -- 사용자 전체를 거부하지 않는다).
- fail-closed: 사용자 엔트리 중복·결과 코드 sizeLimitExceeded(4)/adminLimitExceeded(11)·그룹 페이지 상한 초과는
  IdentityLookupInvalid(이 사용자의 데이터 문제 -- 틱 서킷을 열지 않는다), 그 밖의 비성공 결과 코드(3·32·51·52·53·80…,
  베이스 오구성 32 포함)·전송 오류·자체 마감 초과는 IdentityUnavailable(서버·설정 전역 -- 서킷을 연다; 호출자 마감은
  아래 IdentityDeadlineExceeded). 예전엔 중복이면
  조용히 첫 엔트리, 비성공 결과면 부분·빈 결과를 썼다.
- 그룹 검색은 페이징(500 × 최대 20쪽, criticality False 라 미지원 서버는 전부를 준다).
- resolve 하나는 자체 마감(_RESOLVE_DEADLINE_SECONDS)과 호출자 deadline(틱 예산의 남은 몫) 중 이른 시각 안에서만
  **새 연산을 시작**한다(URI 시도 전·사용자 검색 전·그룹 페이지마다 확인). 그 사이 최장 블록은 한 URI 시도(연결 +
  StartTLS + bind = 3 × ldap_timeout_seconds)라 resolve 하나 ≤ 마감 + 3T. 3T 는 bind 뒤 서버 정보 읽기를 끈 값이다
  (ldap3.Server get_info=NONE -- 기본 SCHEMA 는 bind 뒤 rootDSE·subschema 검색 2회를 더 해 5T 였고, resolve 마다 새
  Server 라 매번 전체 스키마를 받았다). 리졸버는 스키마·DSA 정보를 쓰지 않는다: 스키마 없이 값은 str 로 오고
  int()·_gid_number·_text 가 이미 str 을 처리한다.
- 누구의 마감이 멈췄나(2026-10-08 리뷰): 호출자 deadline 이 자체 마감보다 이르고 그 시각에 걸려 멈췄으면
  IdentityDeadlineExceeded(예산 소진 -- planner 는 Pending, stepper 는 미계수 보류), 자체 마감이면 plain
  IdentityUnavailable(진짜 판정). 예전엔 둘 다 plain 이라 LDAP 가 멀쩡해도 틱 예산 경계에 걸친 요청이 종단 거부됐다.
- 시작 URI 기억(sticky, 2026-10-08 리뷰 -- sssd 처럼): build_ldap_resolver 의 클로저가 마지막으로 붙은 URI(또는 마감으로
  시도하지 못한 첫 URI)의 위치를 기억해 다음 resolve 를 거기서 시작한다. 없으면 타임아웃형으로 죽은 앞쪽 URI 의 비용
  (T 씩)을 resolve 마다 다시 내고, 앞쪽 두 URI 가 죽으면(5s + 5s) 마감 10s 가 세 번째 URI 를 영영 시도하지 못했다
  (T ≥ 10 이면 첫 URI 하나뿐 -- 페일오버가 완전히 꺼졌다). 마감으로 잘린 resolve 의 다음 시도는 시도하지 않았던 URI
  부터라 건강한 URI 에 반드시 닿는다. 전부 실패면 위치를 그대로 둔다.
"""
import math
import re
import time

from .identity import (LDAP_RESOLVE_DEADLINE_SECONDS, IdentityDeadlineExceeded, IdentityLookupInvalid,
                       IdentityUnavailable, ResolvedIdentity)


_GID_RE = re.compile(r"-?[0-9]{1,20}")
_GROUP_PAGE_SIZE = 500
_MAX_GROUP_PAGES = 20                       # 10,000 그룹 -- 넘으면 IdentityLookupInvalid(부분 결과로 판정하지 않는다)
_PAGED_RESULTS_OID = "1.2.840.113556.1.4.319"
_RESOLVE_DEADLINE_SECONDS = LDAP_RESOLVE_DEADLINE_SECONDS   # resolve 하나의 자체 마감(identity.py -- planner·stepper 공유)
_USER_SPECIFIC_RESULT_CODES = frozenset({4, 11})   # sizeLimitExceeded·adminLimitExceeded -- 이 사용자의 결과 집합 문제


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


def _optional_values(entry, name) -> list:
    """엔트리에 속성이 없을 수 있다(ldap3 LDAPKeyError ⊂ KeyError·AttributeError) -- 없으면 []. gidNumber·objectClass
    가 없는 그룹(비 POSIX 앱 그룹)이 사용자 전체를 ldap_unavailable 로 접지 않게."""
    try:
        attr = entry[name]
    except (KeyError, AttributeError):
        return []
    return _attr_values(attr)


def _text(value) -> str:
    # 스키마 없이 읽힌 값은 bytes 로 올 수 있다 -- str(b"..") 의 "b'..'" 모양으로 판정이 어긋나지 않게 푼다.
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value)


def _gid_number(entry) -> "int | None":
    """objectClass 에 posixGroup(대소문자 무시)이 있는 엔트리만. gidNumber 값이 **정확히 하나**이고 int(bool 제외)
    또는 _GID_RE 문자열일 때만 int. 그 밖(없음·다중값·비숫자)은 None -- 예외를 내지 않는다(내면 사용자 전체가
    ldap_unavailable 로 접힌다). 범위 검사는 identity.valid_supplementary_gids 몫이라 음수·거대값도 원시 그대로
    넘긴다(화면에 '제외'로 보이게). posixGroup 만인 이유: sambaGroupMapping 같은 다른 클래스의 gidNumber 는 POSIX
    그룹 멤버십의 근거가 아니다."""
    if not any(_text(c).lower() == "posixgroup" for c in _optional_values(entry, "objectClass")):
        return None
    values = _optional_values(entry, "gidNumber")
    if len(values) != 1:
        return None
    value = values[0]
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (str, bytes)):
        text = _text(value).strip()
        if _GID_RE.fullmatch(text):
            return int(text)
    return None


def _check_result(conn, what) -> None:
    """conn.result['result'] != 0 이면 fail-closed. 코드 ∈ _USER_SPECIFIC_RESULT_CODES → IdentityLookupInvalid(이
    사용자의 결과 집합 문제 -- 서킷 비개방), 그 밖(3 timeLimit·32 noSuchObject(베이스 오구성)·51/52/53/80 …) →
    IdentityUnavailable(서버·설정 전역). 예전엔 비성공 결과의 부분·빈 엔트리를 그대로 써서 그룹이 조용히 빠졌다.
    result 가 dict 가 아니면(테스트 페이크) 검사를 생략한다."""
    result = getattr(conn, "result", None)
    if not isinstance(result, dict):
        return
    code = result.get("result")
    if code is None or code == 0:
        return
    desc = result.get("description", "")
    message = f"ldap {what} search result {code} {desc}"[:200]
    if code in _USER_SPECIFIC_RESULT_CODES:
        raise IdentityLookupInvalid(message)
    raise IdentityUnavailable(message)


def _paged_cookie(conn) -> "bytes | None":
    """conn.result['controls'][_PAGED_RESULTS_OID]['value']['cookie'] -- 없거나 빈 값이면 None(마지막 쪽)."""
    result = getattr(conn, "result", None)
    if not isinstance(result, dict):
        return None
    try:
        cookie = result["controls"][_PAGED_RESULTS_OID]["value"]["cookie"]
    except (KeyError, TypeError):
        return None
    return cookie or None


class _DeadlineStop(IdentityUnavailable):
    """마감 때문에 새 연산(URI 시도·검색)을 시작하지 않았다 -- connect_first·_check_deadline 의 내부 표식. resolve 가
    **누구의 마감이었나**에 따라 IdentityDeadlineExceeded(호출자 deadline) 또는 plain IdentityUnavailable(자체 마감)로
    바꿔 올린다. IdentityUnavailable 의 하위라 connect_first 를 직접 부르는 호출자에겐 종전대로 '불가'다."""


def _check_deadline(monotonic, limit, what) -> None:
    if monotonic() >= limit:
        # 사용자별 데이터 문제가 아니다. 장애(자체 마감)인지 예산 소진(호출자 마감)인지는 resolve 가 정한다.
        raise _DeadlineStop(f"ldap resolve deadline exceeded before {what}")


class LdapIdentityResolver:
    def __init__(self, *, connect, user_base, group_base,
                 group_member_attr="uniqueMember", monotonic=None):
        self._connect = connect
        self._user_base = user_base
        self._group_base = group_base
        self._group_member_attr = group_member_attr
        # 마감 판정 시계(테스트 주입용). 호출자 deadline 도 time.monotonic 기준이라 운영에선 같은 시계다.
        self._monotonic = monotonic or time.monotonic

    def resolve(self, username: str, *, deadline: "float | None" = None):
        own = self._monotonic() + _RESOLVE_DEADLINE_SECONDS
        # 호출자 deadline 이 자체 마감보다 이를 때만 그쪽이 묶는다 -- 그 마감에 걸린 중단은 LDAP 판정이 아니라 호출자
        # 예산 소진이다(IdentityDeadlineExceeded). planner·stepper 는 남은 몫이 자체 마감 이상이면 deadline 을 넘기지
        # 않는다(identity.tick_resolve_deadline) -- 틱 첫 resolve 의 마감 중단은 진짜 판정으로 남는다.
        caller_bound = deadline is not None and deadline < own
        limit = deadline if caller_bound else own
        conn = None
        try:
            conn = self._connect(deadline=limit)
            safe = _escape_filter(username)
            _check_deadline(self._monotonic, limit, "user search")
            conn.search(self._user_base, f"(uid={safe})",
                        attributes=["uidNumber", "gidNumber"])
            _check_result(conn, "user")
            entries = list(conn.entries)
            if not entries:
                return None
            if len(entries) > 1:
                # 예전엔 조용히 entries[0] -- 어느 엔트리의 uid/gid·그룹이 쓰일지 디렉터리 순서에 달렸다(D14).
                raise IdentityLookupInvalid(f"duplicate user entries: {len(entries)}")
            entry = entries[0]
            uid = int(entry["uidNumber"].value)
            gid = int(entry["gidNumber"].value)
            # 그룹 매칭 값: rfc2307(memberUid)은 uid 문자열, rfc2307bis 계열
            # (member/uniqueMember)은 **사용자 엔트리의 전체 DN** 이다. DN 은
            # 방금 검색한 entry 에서 그대로 얻는다 -- 조립하지 않는다(RDN 형식을
            # 지어내면 ou 구조가 다른 디렉터리에서 조용히 0건이 된다).
            member_value = safe if self._group_member_attr == "memberUid" \
                else _escape_filter(entry.entry_dn)
            group_entries, cookie = [], None
            for _ in range(_MAX_GROUP_PAGES):
                _check_deadline(self._monotonic, limit, "group search page")
                conn.search(self._group_base,
                            f"({self._group_member_attr}={member_value})",
                            attributes=["cn", "gidNumber", "objectClass"],
                            paged_size=_GROUP_PAGE_SIZE, paged_cookie=cookie)
                _check_result(conn, "group")
                group_entries.extend(conn.entries)
                cookie = _paged_cookie(conn)
                if not cookie:
                    break
            else:
                # 부분 결과로 판정하지 않는다 -- 빠진 쪽의 그룹이 조용히 사라지면 재확인(stepper)이 오판한다.
                raise IdentityLookupInvalid("ldap group search exceeded page cap")
            groups = tuple(sorted({str(cn) for e in group_entries for cn in _attr_values(e["cn"])}))
            group_gids = tuple(sorted({g for g in map(_gid_number, group_entries) if g is not None}))
        except _DeadlineStop as exc:
            # 마감 때문에 멈췄다(연결 단계에서 시도 못 한 URI 가 남은 경우 포함) -- 누구의 마감이었나로 의미가 갈린다.
            if caller_bound:
                raise IdentityDeadlineExceeded(str(exc)[:500]) from None
            raise IdentityUnavailable(str(exc)[:500]) from None
        except IdentityUnavailable:
            raise                       # IdentityLookupInvalid 포함 -- 하위 클래스 그대로 올린다(서킷 판정이 다르다)
        except Exception as exc:
            raise IdentityUnavailable(str(exc)[:200])
        finally:
            _unbind(conn)
        return ResolvedIdentity(username, uid, gid, groups, False, group_gids=group_gids)


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


def connect_first(uris, open_one, *, deadline=None, monotonic=None, rotation=None):
    """URI 를 순서대로 하나씩 열어 처음 성공한 연결을 돌려준다(sssd ldap_uri 페일오버 미러). 전부 실패하면
    IdentityUnavailable -- 각 URI 의 실패 사유를 모아 남긴다. 한 URI 의 시도는 open_one 이 건 타임아웃(연결·
    StartTLS·bind 각각)으로 유한하다. ServerPool 을 쓰지 않는 이유는 모듈 docstring.
    deadline(time.monotonic 기준 절대 시각)을 넘었으면 남은 URI 를 시도하지 않고 _DeadlineStop(IdentityUnavailable
    의 하위 -- resolve 가 누구의 마감이었나로 바꿔 올린다)을 낸다 -- URI 수 × 3T 가 resolve 마감을 넘지 않게.
    rotation(dict, 선택): 시작 위치 기억(모듈 docstring 'sticky'). {"start": i} 의 i 번째 URI 부터 돌아가며 시도하고,
    성공하면 그 위치를, 마감으로 멈추면 **시도하지 못한 첫 URI** 의 위치를 적는다(전부 실패면 그대로). 동시 호출의
    경합은 시작 순서만 바꿀 뿐 결과의 정확성과 무관하다(dict 대입 하나)."""
    mono = monotonic or time.monotonic
    n = len(uris)
    start = rotation.get("start", 0) % n if (rotation is not None and n) else 0
    errors = []
    for idx in list(range(start, n)) + list(range(start)):
        if deadline is not None and mono() >= deadline:
            if rotation is not None:
                rotation["start"] = idx          # 다음 resolve 는 이번에 못 가 본 URI 부터
            errors.append("deadline exceeded")
            raise _DeadlineStop("; ".join(errors)[:500])
        try:
            conn = open_one(uris[idx])
        except Exception as exc:
            errors.append(f"{uris[idx]}: {type(exc).__name__}: {exc}"[:200])
            continue
        if rotation is not None:
            rotation["start"] = idx              # 붙은 URI 를 기억 -- 죽은 앞쪽 URI 의 타임아웃을 매번 내지 않는다
        return conn
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

    # 시작 URI 기억(모듈 docstring 'sticky') -- 리졸버는 프로세스당 하나라(wiring.build_identity_resolver: 컨트롤러·
    # API 기동 시 1회) 이 클로저의 수명 = 프로세스 수명이다.
    rotation = {"start": 0}

    def connect(deadline=None):
        import ldap3
        tls = None
        if use_start_tls:
            import ssl
            # reqcert=never 미러 -- 사설 인증서 검증 생략(모듈 docstring).
            tls = ldap3.Tls(validate=ssl.CERT_NONE)
        # StartTLS 는 bind **전에** 올라가야 자격증명이 평문으로 새지 않는다 --
        # ldap3 의 AUTO_BIND_TLS_BEFORE_BIND 가 그 순서를 보장한다.
        auto_bind = ldap3.AUTO_BIND_TLS_BEFORE_BIND if use_start_tls else True
        # get_info=NONE: bind 뒤 rootDSE·subschema 검색 2회(각각 receive_timeout 까지 막힌다)를 하지 않는다 -- 한 URI
        # 시도 = 연결·StartTLS·bind = 3T 라는 틱 시간 불변식(identity.LDAP_TICK_BUDGET_SECONDS)의 전제다(모듈 docstring).
        return connect_first(cfg["uris"], lambda uri: ldap3.Connection(
            ldap3.Server(uri, tls=tls, connect_timeout=timeout, get_info=ldap3.NONE),
            user=cfg["bind_dn"], password=cfg["bind_pw"], auto_bind=auto_bind,
            receive_timeout=receive_timeout), deadline=deadline, rotation=rotation)

    return LdapIdentityResolver(connect=connect, user_base=user_base,
                                group_base=group_base,
                                group_member_attr=group_member_attr)
