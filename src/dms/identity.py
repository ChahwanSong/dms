"""실행 신원 모델(dms-k8s -- k8s 신원 모드 전용).

모든 잡은 root(uid 0·gid 0)로 돈다(dms-k8s 설계 D2). 실행 신원 이름(username)은 owner_username 또는 요청자이고, 러너가
uid 0 별칭 계정으로 passwd 에 물질화해 runuser·sshd 가 쓴다(execution_manifests._identity_materialize_stmt). 권한 판정은 이
모듈이 아니라 k8s RoleBinding 맵핑(k8s_access)이 맡는다 -- 이 모듈은 「root 로 돌아도 되는 요청인가」(root_allowed·
privilege_eligible)와 denylist(이름 규칙)만 본다.

LDAP 리졸버·보조 그룹(posixGroup gidNumber)·비 root 실행 경로는 2026-10-26 사용자 결정 (d) 로 지웠다(dms-ssc 라인은 LDAP 을
계속 쓴다 -- 근거는 docs/history·git 히스토리). 그래서 root 근거가 없는 요청(PRIVILEGE_NEVER·자격 없는 배치 자식)은 예전처럼
「실행 신원의 LDAP uid/gid」로 낮추지 않고 privileged_not_authorized 로 **거부**한다 -- 낮출 비 root 신원이 없고, root 로
올리면 근거 없는 uid 0 실행이다.

아래 LDAP 리졸버 도우미(예외·틱 예산·StubIdentityResolver)와 보조 gid 스냅숏 모양 함수는 아직 그것을 import 하는 모듈
(identity_ldap·stepper·execution_manifests)과 함께 지운다 -- 판정 경로(resolve_job_identity)는 이미 쓰지 않는다.
"""
from dataclasses import dataclass
from typing import Protocol


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
# 컨트롤러가 같은 루프를 동시에 돈다). '3T' 는 bind 뒤 서버 정보(rootDSE·subschema 검색 2회)를 읽지 않을 때만 참이다
# -- identity_ldap.build_ldap_resolver 가 ldap3.Server(get_info=NONE)로 끈다(기본 SCHEMA 면 5T 라 T=6 에서 40s).
LDAP_TICK_BUDGET_SECONDS = 10
# resolve 하나의 자체 마감(초) -- deadline 없는 호출자(API 로그인 예열 등)도 유한하게. planner·stepper 는 이 값과
# 틱 예산의 남은 몫을 비교해 '누구의 마감이 묶나' 를 정한다(tick_resolve_deadline).
LDAP_RESOLVE_DEADLINE_SECONDS = 10


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


class IdentityDeadlineExceeded(IdentityUnavailable):
    """**호출자의** deadline(틱 예산의 남은 몫)이 resolve 를 멈췄다 -- 리졸버 자체 마감(LDAP_RESOLVE_DEADLINE_SECONDS)
    이 아니다. 시도하지 못한 URI 가 남은 채 연결 단계에서 멈춘 경우도 포함한다. LDAP 장애 판정이 아니라 예산 소진이라
    planner 는 LdapCircuitOpen(요청 Pending, 다음 틱), stepper 는 미계수 보류(ldap_budget)로 받는다. 예전엔 plain
    IdentityUnavailable 이라 LDAP 가 멀쩡해도 예산 경계에 걸친 요청이 ldap_unavailable 로 종단 거부(planner)되거나
    D2 실패로 계수(stepper)됐다. 하위 클래스인 이유: 이 구분을 모르는 호출자에겐 종전대로 '불가'(fail-closed)다.
    틱의 첫 resolve 는 남은 몫 = 예산 = 자체 마감이라 deadline 을 넘기지 않는다(tick_resolve_deadline) -- 그래서
    진짜 장애·느림은 매 틱 첫 요청에서 여전히 ldap_unavailable 로 판정되고(진행 보장), 영구 Pending 이 생기지 않는다."""


class LdapCircuitOpen(Exception):
    """같은 틱에서 LDAP 불가가 이미 한 번 났다(또는 틱 LDAP 예산을 다 썼다·남은 몫이 resolve 를 멈췄다) -- 이번 틱엔
    LDAP 에 다시 가지 않는다(planner 의 틱 서킷). IdentityUnavailable 의 하위가 **아니다**: resolve_job_identity 가
    ldap_unavailable 로 거부하지 않고 그대로 올려, 호출자가 요청을 Pending 으로 두고 다음 틱에 다시 보게 한다."""


def tick_resolve_deadline(start: float, remaining: float) -> "float | None":
    """틱 예산의 남은 몫 → 리졸버 deadline(planner _TickCircuit·stepper _resolve_fresh 공용). 남은 몫이 리졸버 자체
    마감 이상이면 **None** -- 자체 마감이 묶으므로 그 마감에 걸린 중단은 진짜 판정(IdentityUnavailable)이다. 더 짧을
    때만 start + remaining 을 넘겨, 그 시각에 걸린 중단이 IdentityDeadlineExceeded(예산 소진)가 된다. 실시간에서
    '첫 resolve 의 deadline(t0+10) < 리졸버가 잰 자체 마감(t0+ε+10)' 이라는 ε 차이로 첫 resolve 까지 예산 소진으로
    접히면(영구 Pending) 안 되므로, 비교를 리졸버 안의 시각 대신 여기서 남은 몫으로 한다."""
    if remaining >= LDAP_RESOLVE_DEADLINE_SECONDS:
        return None
    return start + remaining


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
