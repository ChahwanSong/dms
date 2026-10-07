"""실행 신원 모델. LDAP 조회는 주입된 resolver 뒤에 있고, 이 모듈은 오케스트레이션만 한다.

보조 그룹(2026-10-07, LDAP posixGroup gidNumber 인정): 비 root 잡은 계획 시점에 실행 신원이 속한 posixGroup 의
gidNumber 를 **숫자로만** 스냅숏에 얼린다(worker_pool.identity 의 4키 -- supplementary_gids·supplementary_gids_status·
supplementary_gids_excluded·supplementary_gids_found). 규칙은 이 모듈의 순수 함수 하나씩이다 --
valid_supplementary_gids(무엇이 유효한가)·limit_supplementary_gids(상한)·supplementary_gids_problem(스냅숏 모양) 를
planner·stepper·execution_manifests 가 공유해, 규칙이 한쪽만 바뀌어 단계 사이에 그룹이 갈라지는 일을 막는다.
cn·DN 은 denylist 이름 매칭 전용이고 실행으로 흐르지 않는다. 키 부재·None 은 [](더 좁은 권한)이지 실패가 아니다.
"""
from dataclasses import dataclass
from typing import Protocol

from .domain import chown_gid_part


# D3 "기술적으로 무효한 값"의 이 시스템 경계: k8s apiserver 는 PodSecurityContext.supplementalGroups 원소를
# IsValidGroupID(0..math.MaxInt32)로 검증한다(runAsUser·runAsGroup 과 같은 규칙). 2147483648..4294967295 가 하나라도
# 실리면 preflight Pod 생성이 422 → submit_failed 로 그 사용자의 비 root 잡이 전부 깨진다 -- 그래서 제외(화면 '제외').
# 주 uid·gid 가 MaxInt32 를 넘는 경우는 지금도 runAsUser/runAsGroup 에서 422 다(변경 없음).
GID_MAX = 2147483647
# D4: 넘으면 통째로 미적용(over_limit -- 자르면 어느 그룹이 빠지는지 드러나지 않는 권한 축소, 거부하면 지금 돌던
# 사용자가 회귀). 스냅숏 모양 검사의 길이 상한도 이 상수다(하드 -- 설정 키가 아니라 설정 변경이 위조로 보이지 않는다).
MAX_SUPPLEMENTARY_GROUPS = 256
SUPP_APPLIED, SUPP_NONE, SUPP_OVER_LIMIT, SUPP_DISABLED, SUPP_PRIVILEGED = (
    "applied", "none", "over_limit", "disabled", "privileged")
SUPP_STATUSES = frozenset({SUPP_APPLIED, SUPP_NONE, SUPP_OVER_LIMIT, SUPP_DISABLED, SUPP_PRIVILEGED})
# planner·stepper 공유: 한 틱에서 resolve 에 쓰는 누적 시간 상한(초). 넘으면 새 resolve 를 하지 않는다(서킷과 같은 보류).
# 루프 리스 max(interval*3, 30)=30s 와 맞물린 내부 불변식이다 -- 틱 LDAP 시간 ≤ 예산 + 진행 중 한 단계(한 URI 의
# 연결·StartTLS·bind = 3 × DMS_LDAP_TIMEOUT_SECONDS) = 10 + 15 < 30. 설정 키로 빼지 않는다(키우면 리스를 넘어 두 번째
# 컨트롤러가 같은 루프를 동시에 돈다).
LDAP_TICK_BUDGET_SECONDS = 10


@dataclass(frozen=True)
class ResolvedIdentity:
    username: str
    uid: int
    gid: int
    groups: tuple[str, ...]
    privileged: bool
    # 리졸버 출력 전용: posixGroup 엔트리의 gidNumber 원시값(정렬·중복 제거, 범위 검사 전, 주 gid 포함 가능).
    # resolve_job_identity 출력에선 항상 () -- 스냅숏에 싣지 않는다(planner 가 dict 에서 뺀다).
    group_gids: tuple[int, ...] = ()
    # resolve_job_identity 출력(= 잡 스냅숏) 전용. found 는 None(보지 않았다 -- 스위치 꺼짐·root)과 0(없음 확정)이 다르다.
    supplementary_gids: tuple[int, ...] = ()
    supplementary_gids_status: str = SUPP_NONE
    supplementary_gids_excluded: tuple[int, ...] = ()
    supplementary_gids_found: "int | None" = None


class IdentityUnavailable(Exception):
    """resolver 백엔드(LDAP)가 조회 불가 — fail-closed 대상."""


class IdentityLookupInvalid(IdentityUnavailable):
    """LDAP 는 응답했지만 이 사용자의 결과를 쓸 수 없다(사용자 엔트리 중복, 결과 코드 sizeLimitExceeded(4)·
    adminLimitExceeded(11), 그룹 페이지 상한 초과). 사용자별·결정적 데이터 문제라 **틱 서킷을 열지 않는다**(다른
    사용자의 요청·잡이 같은 틱에 계속 진행). 상위 클래스라 resolve_job_identity 의 ldap_unavailable 의미는 그대로(D14)."""


class LdapCircuitOpen(Exception):
    """같은 틱에서 LDAP 불가가 이미 한 번 났다(또는 틱 LDAP 예산을 다 썼다) -- 이번 틱엔 LDAP 에 다시 가지 않는다
    (planner 의 틱 서킷). IdentityUnavailable 의 하위가 **아니다**: resolve_job_identity 가 ldap_unavailable 로
    거부하지 않고 그대로 올려, 호출자가 요청을 Pending 으로 두고 다음 틱에 다시 보게 한다."""


class IdentityResolver(Protocol):
    def resolve(self, username: str, *, deadline: "float | None" = None) -> "ResolvedIdentity | None":
        """deadline = time.monotonic() 기준 절대 시각. 리졸버는 이 시각을 넘기면 새 LDAP 연산을 시작하지 않는다
        (틱 예산의 남은 몫 -- planner·stepper 가 넘긴다). None 이면 리졸버 자체 마감만."""
        ...


class StubIdentityResolver:
    def __init__(self, users: dict, *, unavailable: bool = False):
        self._users = users
        self._unavailable = unavailable

    def resolve(self, username: str, *, deadline: "float | None" = None):
        # deadline 은 무시한다(조회가 즉시라 마감이 의미 없다) -- 시그니처만 실 리졸버와 맞춘다.
        if self._unavailable:
            raise IdentityUnavailable(username)
        return self._users.get(username)


def _is_int(value) -> bool:
    # bool 은 int 의 하위라 따로 뺀다 -- True 가 gid 1 로 승격되지 않게(DB 신뢰 경계).
    return isinstance(value, int) and not isinstance(value, bool)


def valid_supplementary_gids(raw, *, primary_gid) -> "tuple[tuple[int, ...], tuple[int, ...]]":
    """(유효, 제외). D3: 범위 필터 없음 -- 0·65534 도 유효(사용자가 위험을 알고 승인). bool·비int 는 조용히 버린다
    (리졸버는 int 만 낸다 -- 방어). g < 0 또는 g > GID_MAX 는 '제외'(화면에 보인다 -- 조용한 권한 축소가 되지 않게).
    g == primary_gid 는 조용히 뺀다(중복일 뿐 제외가 아니다). 둘 다 정렬·중복 제거."""
    valid, excluded = set(), set()
    for g in raw or ():
        if not _is_int(g):
            continue
        if g < 0 or g > GID_MAX:
            excluded.add(g)
        elif g != primary_gid:
            valid.add(g)
    return tuple(sorted(valid)), tuple(sorted(excluded))


def limit_supplementary_gids(valid) -> "tuple[tuple[int, ...], str]":
    """D4: () → ((), SUPP_NONE); len > MAX_SUPPLEMENTARY_GROUPS → ((), SUPP_OVER_LIMIT)(통째로 미적용 = 오늘 동작);
    그 외 (valid, SUPP_APPLIED)."""
    valid = tuple(valid)
    if not valid:
        return (), SUPP_NONE
    if len(valid) > MAX_SUPPLEMENTARY_GROUPS:
        return (), SUPP_OVER_LIMIT
    return valid, SUPP_APPLIED


def supplementary_gids_problem(ident: dict) -> "str | None":
    """스냅숏 모양 검사 -- **하드 상수만** 본다(정책값·설정을 보지 않아 설정 변경이 위조로 표시되지 않는다). 반환은
    이벤트 메시지용 문제 문자열이다(사유 코드 아님 -- 호출자가 identity_missing_at_step 으로 접는다). uid·gid·
    username 검사는 stepper.identity_problem 몫이라 여기선 gid 가 int 일 때만 '주 gid 와 같음'을 본다(빌더 테스트의
    빈 identity 허용).

    순서는 값싼 것 먼저 -- 위조된 거대 목록이 매 틱 O(n) 순회가 되지 않게 길이를 원소보다 먼저 본다. 키 부재·None
    은 [](배포 전에 계획된 잡 = 더 좁은 권한, 실패 아님). 상태 키 결속은 키가 **있을 때만**(배포 전 잡은 키가 없다)."""
    v = ident.get("supplementary_gids")
    if v is not None:
        if not isinstance(v, list):
            return "supplementary_gids_not_list"
        if len(v) > MAX_SUPPLEMENTARY_GROUPS:
            return "supplementary_gids_too_many"
        for g in v:
            if not _is_int(g) or g < 0 or g > GID_MAX:
                return "supplementary_gid_invalid"
        primary = ident.get("gid")
        if _is_int(primary) and primary in v:
            return "supplementary_gid_is_primary"
        if any(a >= b for a, b in zip(v, v[1:])):
            return "supplementary_gids_unsorted"      # 중복도 여기 걸린다(엄격 오름차순)
        if ident.get("privileged") and v:
            return "supplementary_gids_on_privileged"
    if "supplementary_gids_status" in ident:
        status = ident["supplementary_gids_status"]
        # isinstance 먼저: 리스트 같은 비해시 값이 frozenset 멤버십에서 TypeError 를 내지 않게.
        if not isinstance(status, str) or status not in SUPP_STATUSES:
            return "supplementary_gids_status_mismatch"
        if (len(v or []) > 0) != (status == SUPP_APPLIED):          # 비어 있지 않음 ⇔ applied
            return "supplementary_gids_status_mismatch"
        if (status == SUPP_PRIVILEGED) != bool(ident.get("privileged")):
            return "supplementary_gids_status_mismatch"
    return None


def owner_override_allowed(*, owner_username, requester_id, requester_is_admin,
                           allow_privileged, privileged_requesters) -> bool:
    """다른 실행 신원 지정(owner_username ≠ 요청자)은 특권이다 -- API(routes_requests.submit)와 planner 가 **같은
    술어**를 쓴다(planner 는 API 를 거치지 않은 DB 직접 쓰기 방어 -- 그 경로가 남의 uid 와 이제 보조 그룹까지
    얻는다). 비교는 **원문 그대로**(strip 없음): API 의 기존 `owner != identity.actor` 와 같아 ' alice' 같은 값의
    403 이 유지된다(validate_owner_username 의 422 는 그 뒤). 세션 요구 없음(API 와 맞춤)."""
    if owner_username is None:
        return True
    if not isinstance(owner_username, str):
        return False                     # DB 변조(int·list) -- 거부 쪽(planner 는 그 전에 invalid_owner_username)
    if owner_username == requester_id:
        return True
    return bool(requester_is_admin and allow_privileged and requester_id in privileged_requesters)


def check_chown_group(chown, *, identity: "ResolvedIdentity") -> None:
    """D7: 명시 chown 의 gid 가 실행 신원이 속한 그룹이 아니면 IdentityRejected("chown_group_not_member").
    - identity.privileged 면 검사 안 함(root 는 어떤 그룹으로도 chown 가능).
    - domain.chown_gid_part(chown) 가 None 이면(uid 만·이름·형식 오류) 대상 아님 -- 그 거부는 기존 chown_problem 경로.
    - 허용 집합 = {identity.gid} ∪ identity.supplementary_gids(= **적용된** 목록; over_limit/disabled/none 이면 주 gid 만).
    없애는 함정: 예전엔 비소속 gid 를 지정하면 dsync 가 데이터를 다 복사한 뒤 EPERM 으로 Failed 였다."""
    gid = chown_gid_part(chown)
    if identity.privileged or gid is None:
        return
    if gid != identity.gid and gid not in identity.supplementary_gids:
        raise IdentityRejected("chown_group_not_member", str(chown))


class IdentityRejected(Exception):
    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


PRIVILEGE_NEVER = "never"            # payload 에 root 근거 없음(fail-closed): 실행 신원의 LDAP uid/gid
PRIVILEGE_REQUESTED = "requested"    # payload run_as_root is True(포탈의 관리자 기본 root 도 명시로 온다)
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
                         privilege: str = PRIVILEGE_NEVER,
                         supplementary_groups: bool = False) -> ResolvedIdentity:
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
        -- 자격이 있으면 root(종전 동작 유지).

    비 root 경로의 주 gid 0 도 거부한다(D5, identity_root_group_without_privilege): uid 만 보던 때는 디렉터리가
    gidNumber=0 을 주면 비 root 잡이 root 그룹 권한(테스트베드 /cephfs/dms root:root 770 등)을 얻었다. stepper 의
    identity_problem 이 변조 행용 백스톱이다.

    supplementary_groups(보조 그룹, 2026-10-07): True 면 리졸버가 준 posixGroup gidNumber 를 valid_supplementary_gids →
    limit_supplementary_gids 로 걸러 스냅숏 4키(supplementary_gids·_status·_excluded·_found)를 채운다. 기본값은
    session_authenticated 와 같은 이유로 **False**(fail-closed) -- 인자를 빠뜨린 미래 호출자가 권한을 넓히지 못하게.
    꺼짐은 status disabled·found None(보지 않았다), 그룹 없음은 none·found 0(없음 확정), root 는 privileged·목록 ()
    (DAC override 라 그룹이 무의미하고, 실려 있으면 변조 신호다). group_gids(리졸버 원시값)는 출력에서 항상 ()."""
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
        return ResolvedIdentity(owner, 0, 0, (), True, supplementary_gids_status=SUPP_PRIVILEGED)
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
    if resolved.gid == 0:
        # D5: 비특권 주 gid 0 -- uid 검사만으로는 root 그룹 권한이 비 root 잡에 실렸다. uid 0 거부가 먼저다
        # (둘 다 0 이면 더 정확한 사유). 보조 gid 0 은 D3 로 인정한다(여기가 아니라 valid_supplementary_gids).
        raise IdentityRejected("identity_root_group_without_privilege", owner)
    control.register_probe_target(owner)
    if supplementary_groups:
        valid, excluded = valid_supplementary_gids(resolved.group_gids, primary_gid=resolved.gid)
        applied, status = limit_supplementary_gids(valid)
        found = len(valid)                      # 0 은 정상값("없음 확정")
    else:
        applied, excluded, status, found = (), (), SUPP_DISABLED, None   # None = 보지 않았다(모름)
    return ResolvedIdentity(owner, resolved.uid, resolved.gid, tuple(resolved.groups), False,
                            supplementary_gids=applied, supplementary_gids_status=status,
                            supplementary_gids_excluded=excluded, supplementary_gids_found=found)
