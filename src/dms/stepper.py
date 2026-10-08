"""job-stepper: 계획된 data_job을 비블로킹 스텝으로 전진시키는 루프 본체. 실행은 어댑터 뒤.

보조 그룹 재확인(2026-10-07 D1·D2): 계획 시점 스냅숏에 LDAP 보조 gid 가 실린 비 root 잡은 매 제출 직전
(_build_spec -- preflight·preview·exec_preflight·execution 네 경로의 단일 관문)과 vcjob 큐 대기(PENDING) 중에 LDAP
를 다시 보고, 스냅숏 ⊄ 최신(탈퇴)·uid/gid 변경·계정 삭제면 identity_changed_at_step 으로 종단한다. LDAP 장애는
상태를 바꾸지 않는 보류(재시도 3번, 간격 ≥ 60s -- 시도 횟수는 events 가 카운터다, 스키마 변경 없음)이고, 한 틱 안
에서는 서킷(한 번 불가를 보면 나머지는 LDAP 를 부르지 않는다)과 LDAP 시간 예산(identity.LDAP_TICK_BUDGET_SECONDS)
이 루프 리스 30s 를 지킨다. JobStepper 는 틱마다 새로 만들어지므로 인스턴스 필드 = 틱 상태다(지속 상태는 DB).
"""
import hashlib
import json
import logging
import posixpath
import sys
import time

from .artifact_base import resolve_artifact_base, static_base_problem, strip_scheme
from .db import iso_epoch, iso_plus, utc_now_iso
from .domain import DataJobState, TERMINAL_DATA_JOB_STATES, chown_problem
from .execution import ExecStatus, ExecutionError, JobSpec
from .execution_manifests import parse_execution_reason, parse_preflight_reason
from .identity import (LDAP_TICK_BUDGET_SECONDS, PRIVILEGE_NEVER, IdentityDeadlineExceeded,
                       IdentityLookupInvalid, IdentityUnavailable, privilege_policy,
                       supplementary_gids_problem, tick_resolve_deadline, valid_supplementary_gids)
from .placement import TOOL_TO_POLICY
from .repositories.node_exclusions import blocked_nodes

logger = logging.getLogger(__name__)

# D2(사용자 지정 "재시도 3번만"): 첫 실패 뒤 재시도 횟수 -- streak 가 이 값에 이른 뒤 또 실패(= 4번째 시도)면 종단.
_LDAP_RECHECK_RETRIES = 3
# D2: 재시도 간격 하한(초). 마지막 계수 시도(이벤트 payload 의 attempted_at_epoch)로부터 이만큼 지나야 LDAP 를 다시
# 부른다 -- 3번이 몇 초 안에 소진되지 않고 최소 ≈180s 동안 순간 장애를 흡수한다.
_LDAP_RECHECK_SPACING_SECONDS = 60
_MISS = object()            # 틱 캐시 표식(None 은 '계정 삭제'라는 정상 결과라 표식으로 못 쓴다)
_SLOW_TICK_SECONDS = 20     # 이보다 긴 틱은 stderr 에 시간 줄을 남긴다(리스 30s 앞의 경고 -- 실증 측정점)


def _summary_fingerprint(summary):
    if not summary:
        return None
    payload = json.dumps(summary, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()


# 슬라이스 25 §2.2: 진단 로그 박제 상한. 파드당 꼬리 16KB x 항목 4 = 총 64KB --
# builds.LOG_TEXT_MAX(64KB)와 같은 총량 규약이다(계약 테스트가 곱을 고정한다).
# 상한 없는 박제는 다행 조회에서 이미 격리했더라도(리포지토리 몫) DB 자체를
# 부풀린다 -- 꼬리를 남기는 이유는 트레이스백·실패 사유가 끝에 몰리기 때문.
DIAG_TAIL_BYTES = 16 * 1024
DIAG_MAX_ENTRIES = 4


def _diag_entry(pod, log):
    """박제 항목 하나. log=None(얻을 수 없었다)은 None 그대로 저장한다 --
    "박제 시점에 이미 없었다"는 사실 자체가 진단이다. 빈 문자열은 정상값이라
    truthy 검사를 쓰지 않는다(설계 §4). 꼬리 자르기는 바이트 기준이다."""
    if log is None:
        return {"pod": pod, "log": None, "truncated": False}
    raw = log.encode()
    if len(raw) <= DIAG_TAIL_BYTES:
        return {"pod": pod, "log": log, "truncated": False}
    tail = raw[-DIAG_TAIL_BYTES:]
    # 경계에서 코드포인트 가운데가 잘렸으면 조각을 **버리고** 물러난다. 그냥
    # errors="replace" 로 넘기면 1~3바이트 조각이 U+FFFD(3바이트)로 부풀어
    # 결과가 도리어 DIAG_TAIL_BYTES 를 넘는다 -- 상한이 상한이 아니게 된다.
    # 게다가 원본에 없던 깨진 글자를 진단 로그에 심는 셈이라(한국어 실패 사유가
    # 흔하다) 버리는 쪽이 정직하다. log 는 str 이라 raw 는 항상 정상 UTF-8 이고,
    # 선두 연속 바이트를 걷어낸 접미사는 그대로 디코드된다 -- replace 는 방어용.
    cut = 0
    while cut < len(tail) and 0x80 <= tail[cut] < 0xC0:
        cut += 1
    return {"pod": pod,
            "log": tail[cut:].decode("utf-8", errors="replace"),
            "truncated": True}


class StorageMissingAtStep(Exception):
    """_abs 가 storage 행/managed_root 를 찾지 못했다 -- 요청 시점엔 있었는데
    스텝 시점에 없다는 뜻이다(행 삭제 또는 직접 DB 조작; 라우트 update 는 가드가
    막는다 -- 슬라이스 24 §2.4). 예전 폴백(상대경로 반환, 로그 0건)은 dsync 를
    launcher cwd 기준 컨테이너 오버레이에 쓰고 SUCCEEDED 로 끝내는 조용한 데이터
    증발이었고 drm 이면 cwd 기준 상대 삭제였다 -- 예외로 끊고 종단시킨다."""

    def __init__(self, storage_name):
        self.storage_name = storage_name
        super().__init__(f"storage {storage_name!r} missing at step time")


class IdentityMissingAtStep(Exception):
    """worker_pool.identity 가 없거나 모양이 틀렸다(2026-09-09, 제어면 root 전환의
    검토에서 나온 "null ≠ 0" 규칙의 uid 판). execution_manifests 는 uid/gid 부재를
    0 으로, username 부재를 "root" 로 기본값 처리하므로(_worker_env/_launcher_env/
    build_preflight_pod) 변조된 행(candidates 는 있고 identity 는 없음)은 preflight
    를 uid 0 으로, mpirun 을 runuser root 로 돌린다. 정상 producer(planner)는
    항상 채우므로 도달 경로는 DB 직접 쓰기뿐이지만 unknown_tool/_abs 와 같은 위협
    클래스(DB 가 신뢰 경계)라 같은 층에서 종단시킨다. **uid 0 자체는 거부하지
    않는다** -- 특권 요청자(identity.py, privileged=True)는 정당한 0 이다.
    2026-10-07: 비특권 주 gid 0(D5 백스톱 -- 규칙 전에 계획된 잡·변조 행)과 보조 gid 스냅숏의 모양(하드 상수·
    상태 키 결속, identity.supplementary_gids_problem)도 같은 사유로 끊는다 -- 위조된 목록이 pod
    supplementalGroups·/etc/group 으로 흐르기 전에."""

    def __init__(self, problem):
        self.problem = problem
        super().__init__(f"identity {problem} at step time")


class PrivilegeNotRequestedAtStep(Exception):
    """root(privileged) 신원을 실은 잡인데 요청에 그 근거(run_as_root 명시·배치 자식)가
    없다(2026-09-30). 신원은 planner 가 한 번 정해 worker_pool 에 얼리므로, 규칙이
    바뀌기 전에 계획된 잡(예: ConfirmPending 으로 최대 preview TTL 동안 대기 중인
    "관리자가 실행 신원=일반 사용자로 낸 sync")은 배포 뒤에도 uid 0 으로 exec_preflight
    ·dsync 를 돈다 -- 사고 경로 그대로다. 변조 행(worker_pool 에 privileged 를 써 넣음)
    도 같은 클래스다. 매 제출 직전(_build_spec)에 요청 행을 다시 읽어
    identity.privilege_policy 로 재확인하고, 근거가 없으면 제출 전에 종단시킨다."""

    def __init__(self, request_id):
        self.request_id = request_id
        super().__init__(f"privileged identity without run_as_root/batch (request {request_id})")


class ChownNameAtStep(Exception):
    """제출 직전 chown 재검사(2026-10-01, domain.chown_problem 주석). 이름 chown 은 제출 검증이 막지만, 규칙
    전에 만들어진 대기 잡·DB 직접 쓰기는 그대로 dsync/nsync --chown 으로 간다 -- root 실행이면 실행 신원
    이름이 uid 0 으로 풀려 목적지가 조용히 root 소유가 된다. 모든 제출 경로가 지나는 _build_spec 에서
    끊어 종단시킨다(identity_missing_at_step 과 같은 fail-closed)."""

    def __init__(self, problem):
        self.problem = problem
        super().__init__(problem)


class NodeBlockedAtStep(Exception):
    """제출 직전 노드 재검사(2026-10-02, repositories/node_exclusions.py). 후보 노드는 계획 시점에 worker_pool 에
    굳는다 -- 그 뒤 관리자가 노드를 배치에서 빼거나 k8s 에서 cordon/taint 하면, 막힌 노드로 다음 단계를 제출하는
    대신 여기서 끊어 종단시킨다(남은 노드로 다시 계획하지 않는다: 노드 수·프로세스 수·신원이 함께 굳어 있다).
    k8s 쪽은 그대로 두면 gang 이 안 서서 Pending 에 영원히 멈춘다."""

    def __init__(self, blocked: dict):
        self.blocked = blocked
        super().__init__(", ".join(f"{n}={r}" for n, r in sorted(blocked.items())))


class ArtifactBaseUnsafeAtStep(Exception):
    """보조 그룹이 실린 비 root 잡의 제출 직전 base 정적 관문(2026-10-08 리뷰, artifact_base.static_base_problem):
    base 에 g+w·POSIX ACL 쓰기 항목·**default ACL**·other-x 없음이 있으면 제출 전에 끊는다. preflight 의
    `test -w` 는 base **자체**의 쓰기만 보므로, base 엔 쓰기가 없고 default ACL 만 있는 경우(러너가 root 로 만드는
    <job_id>/<phase> 가 그 ACL 을 상속)를 통과시켰다 -- 그 그룹의 다른 사용자가 남의 rank.sh 를 바꿔치기해 mpirun 이
    피해자 신원으로 실행하는 교차 사용자 코드 실행이었다(불변식 5). 제어면 판정(3홉·PUT)은 표시·저장용이라 잡을
    막지 않고, env 로 준 base·저장 뒤 바뀐 mode 도 있어 컨트롤러(root, base 마운트)가 직접 본다. problem 은 사유 코드
    (artifact_base_group_writable·artifact_base_not_traversable) -- _step_one 이 키워드 리터럴로 종단한다."""

    def __init__(self, problem):
        self.problem = problem
        super().__init__(f"artifact base {problem} at step time")


class IdentityChangedAtStep(Exception):
    """계획 뒤 LDAP 가 바뀌었다(2026-10-07 D1): 스냅숏의 보조 gid 가 최신 유효 gid 에 없거나(탈퇴), uid·주 gid 가
    바뀌었거나, 계정이 삭제됐다. 스냅숏은 계획 시점에 얼린 권한이라 그대로 실행하면 이미 회수된 그룹 권한으로
    파일을 만진다 -- 제출 전(또는 vcjob 큐 대기 중, 도구 시작 전)에 끊는다. 교집합으로 깎아 계속 가지 않는 이유:
    preflight 가 본 권한과 실행 권한이 갈라진다(drift). payload 는 이벤트에 그대로 싣는다(LDAP 원문 없음)."""

    def __init__(self, payload: dict):
        self.payload = payload
        super().__init__(f"identity changed at step: {payload}")


class IdentityRecheckHeld(Exception):
    """D2 보류: 상태를 바꾸지 않고 이번 틱을 넘긴다(_step_one 이 touch 로 claim 큐 뒤로만 보낸다). counted=True 만
    시도 횟수(identity_recheck_deferred 이벤트 -- 카운터)로 남는다. reason ∈ {"ldap_unavailable"(계수),
    "spacing"·"circuit_open"·"ldap_budget"(미계수 -- LDAP 를 부르지 않았으니 시도가 아니다; ldap_budget 은 틱 예산의
    남은 몫이 resolve 를 멈춘 경우(IdentityDeadlineExceeded)도 포함 -- 예산 소진이지 LDAP 판정이 아니다)}."""

    def __init__(self, phase, *, counted: bool, reason: str, attempt: "int | None" = None,
                 attempted_at_epoch: "float | None" = None):
        self.phase = phase
        self.counted = counted
        self.reason = reason
        self.attempt = attempt
        self.attempted_at_epoch = attempted_at_epoch
        super().__init__(f"identity recheck held ({reason}) at {phase}")


class IdentityRecheckExhausted(Exception):
    """D2: 첫 실패 + 재시도 3회가 모두 LDAP 불가 → ldap_unavailable 종단."""

    def __init__(self, phase, *, attempts: int):
        self.phase = phase
        self.attempts = attempts
        super().__init__(f"identity recheck exhausted after {attempts} attempts at {phase}")


class LdapNotConfiguredAtStep(Exception):
    """보조 gid 가 실린 잡인데 컨트롤러에 resolver 가 없다 -- 일시 장애가 아니라 설정 상태라 재시도 없이
    ldap_not_configured 로 종단한다(재확인 없이 그룹을 싣는 길을 남기지 않는다)."""


def identity_problem(ident) -> "str | None":
    """실행 신원의 모양 검사 -- 단일 장소. 정상 = planner 가 ResolvedIdentity 를 asdict 한 것:
    uid/gid 는 int(bool 제외), username 은 비어 있지 않은 str, privileged 는
    uid == 0 과 일치. 2026-10-07: 비특권 gid 0(D5 백스톱)과 보조 gid 모양(하드 상수·상태 키 결속,
    identity.supplementary_gids_problem -- 키 부재·None 은 [] 로 통과)도 본다. 문제가 있으면 짧은 설명(이벤트
    메시지), 없으면 None."""
    if not isinstance(ident, dict):
        return "identity_missing"
    for key in ("uid", "gid"):
        value = ident.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            return f"{key}_missing"
        if value < 0:
            return f"{key}_negative"     # k8s 가 거부하기 전에, 정확한 사유로
    username = ident.get("username")
    if not isinstance(username, str) or not username:
        return "username_missing"
    if bool(ident.get("privileged")) != (ident["uid"] == 0):
        return "privileged_flag_mismatch"
    if ident["gid"] == 0 and not ident.get("privileged"):
        # D5 백스톱: planner 는 identity_root_group_without_privilege 로 거부하지만, 규칙 전에 계획된 잡·변조 행은
        # 비 root 잡에 root 그룹 권한을 싣는다(runAsGroup 0).
        return "root_group_without_privilege"
    return supplementary_gids_problem(ident)


def _identity_change(ident, gids, fresh) -> "dict | None":
    """스냅숏 대 최신 LDAP 비교(제출 재확인·큐 대기 재확인 공용). None = 그대로 진행.
    최신값은 valid_supplementary_gids 로 거른 **상한 적용 전** 유효 집합과 비교한다 -- '그룹이 늘어 256 을 넘었다'
    는 이유로 종단되지 않게. 최신에만 있는 gid 는 무시한다(권한은 늘지 않는다 -- 새 그룹은 재신청해야 반영)."""
    if fresh is None:
        return {"user_missing": True, "missing_gids": list(gids), "uid_changed": None, "gid_changed": None}
    valid, _excluded = valid_supplementary_gids(fresh.group_gids, primary_gid=fresh.gid)
    missing = sorted(set(gids) - set(valid))
    uid_changed, gid_changed = fresh.uid != ident["uid"], fresh.gid != ident["gid"]
    if missing or uid_changed or gid_changed:
        return {"user_missing": False, "missing_gids": missing,
                "uid_changed": uid_changed, "gid_changed": gid_changed}
    return None


class JobStepper:
    def __init__(self, repos, execution_adapter, *, settings, identity_resolver=None, clock=None,
                 monotonic=None):
        self._repos = repos
        self._exec = execution_adapter
        self._settings = settings
        # 보조 그룹 재확인(모듈 docstring). resolver 는 원시 LdapIdentityResolver 다 -- planner 의 _TickCircuit 은
        # planner 전용이고, stepper 의 서킷·예산은 아래 틱 상태가 직접 든다.
        self._resolver = identity_resolver
        self._clock = clock or utc_now_iso             # D2 시도 시각(이벤트 payload attempted_at_epoch)
        self._monotonic = monotonic or time.monotonic  # 틱 LDAP 예산 시계
        # 틱 상태(JobStepper 는 틱마다 새로 만들어진다 -- controller._stepper_step). 틱을 넘는 메모리 상태는 두지
        # 않는다(지속 상태는 DB): D2 시도 횟수·간격은 events 로 센다(_recheck_history).
        self._fresh_cache: dict = {}     # username → ResolvedIdentity | None(계정 삭제) | IdentityLookupInvalid
        self._ldap_circuit_open = False
        self._ldap_spent = 0.0
        self._queued_rechecks: list = []  # [(job, phase)] -- run_once 가 클레임 루프 뒤에 처리(2패스)

    def run_once(self) -> dict:
        control = self._repos.control.control_state()
        if control and control["drain"]:
            return {}
        t0 = self._monotonic()
        results = {}
        for job in self._repos.data_jobs.claim_steppable():
            jid = job["job_id"]
            try:
                results[jid] = self._step_one(job)
            except Exception as exc:
                print(f"stepper error on {jid}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                results[jid] = f"error:{type(exc).__name__}"
                # 전이를 남기지 못한 실패 -- stderr로만 새면 파드 재시작에 사라진다. claim 스냅숏 뒤 행이 사라진
                # 잡(요청 삭제 -- 종단 전이의 KeyError 등)은 request_id=NULL 로 남긴다: 지워진 id 로 쓰면 어디서도
                # 안 보이는 고아 이벤트다(id 는 payload 에 둔다).
                gone = self._job_gone(jid)
                self._repos.observability.record_event(
                    component="stepper", severity="error", event_type="step_error",
                    message=f"{type(exc).__name__}: {exc}"[:500],
                    payload=({"job_id": jid, "request_id": job.get("request_id"), "job_deleted": True}
                             if gone else None),
                    request_id=None if gone else job.get("request_id"))
        # 2패스: vcjob 큐 대기 재확인은 제출이 LDAP 예산을 먼저 쓴 **뒤에** 한다 -- 그 틱의 첫 LDAP 사용은 언제나
        # 계수되는 제출 시도라 "한 번 불가를 보면 나머지는 보류"(D2)가 문언 그대로 성립하고 폴링 잡이 제출을 굶기지
        # 않는다(폴링은 updated_at 을 갱신하지 않아 claim 앞쪽에 선다).
        self._run_queued_rechecks(results)
        elapsed = self._monotonic() - t0
        if (elapsed > _SLOW_TICK_SECONDS or self._ldap_circuit_open
                or self._ldap_spent >= LDAP_TICK_BUDGET_SECONDS):
            # 틱 시간 불변식(예산 + 진행 중 한 단계 < 리스 30s)의 운영 측정점. 서킷·예산이 걸린 틱도 남긴다.
            print(f"stepper: tick {elapsed:.1f}s ldap {self._ldap_spent:.1f}s/{LDAP_TICK_BUDGET_SECONDS}s "
                  f"circuit={'open' if self._ldap_circuit_open else 'closed'}", file=sys.stderr)
        return results

    # ---- 보조 그룹 LDAP 재확인(D1·D2) ----

    def _ldap_gate(self) -> "str | None":
        """같은 틱의 서킷·예산. None 이면 LDAP 를 불러도 된다."""
        if self._ldap_circuit_open:
            return "circuit_open"
        if self._ldap_spent >= LDAP_TICK_BUDGET_SECONDS:
            return "ldap_budget"
        return None

    def _resolve_fresh(self, username):
        """캐시 미스에서만 부른다(호출자가 _ldap_gate 를 먼저 본다). 남은 틱 예산을 deadline 으로 넘겨 리졸버가 그
        시각 뒤엔 새 LDAP 연산을 시작하지 않게 한다(남은 몫이 리졸버 자체 마감 이상이면 넘기지 않는다 --
        identity.tick_resolve_deadline). IdentityLookupInvalid(사용자별 데이터 문제 -- 중복 엔트리 등)는 캐시만 하고
        서킷을 열지 않는다: 한 사용자의 디렉터리 문제가 그 틱 전원을 보류시키지 않게. IdentityDeadlineExceeded(남은
        몫이 resolve 를 멈췄다 -- LDAP 판정이 아니다)는 예산 소진으로 기록하고(이 틱 나머지는 _ldap_gate 가
        ldap_budget 으로 보류) 캐시하지 않는다 -- 호출자가 미계수 보류로 바꾼다(2026-10-08 리뷰: 예전엔 D2 계수 실패라
        LDAP 가 멀쩡해도 예산 경계에 4번 걸린 잡이 ldap_unavailable 로 종단될 수 있었다). 전송 오류·자체 마감·서버 전역
        결과 코드(plain IdentityUnavailable)만 서킷을 연다."""
        t0 = self._monotonic()
        remaining = LDAP_TICK_BUDGET_SECONDS - self._ldap_spent
        budget_cut = False
        try:
            fresh = self._resolver.resolve(username, deadline=tick_resolve_deadline(t0, remaining))
        except IdentityLookupInvalid as exc:
            self._fresh_cache[username] = exc
            raise
        except IdentityDeadlineExceeded:
            budget_cut = True
            raise
        except IdentityUnavailable:
            self._ldap_circuit_open = True
            raise
        finally:
            self._ldap_spent += self._monotonic() - t0
            if budget_cut:
                # 리졸버가 deadline 을 지난 뒤에 멈추므로 보통 이미 예산 이상이다 -- 시계가 덜 갔어도 이 틱의 남은
                # resolve 를 막도록 명시한다(stderr 측정줄도 예산 소진으로 찍힌다).
                self._ldap_spent = max(self._ldap_spent, LDAP_TICK_BUDGET_SECONDS)
        self._fresh_cache[username] = fresh       # None(계정 삭제)도 캐시 -- 같은 틱 같은 사용자는 한 번만
        return fresh

    def _recheck_history(self, job) -> "tuple[int, float | None]":
        """(streak, last_epoch) -- 이 잡의 연속 계수 실패 수와 마지막 계수 시도 시각. events 가 카운터다(스키마 변경
        없음): identity_recheck_deferred 는 +1, identity_groups_checked(재확인 통과)는 0 으로 리셋. 시각은 행의 at 이
        아니라 payload.attempted_at_epoch(주입 clock 기준 -- 실시간과 섞이지 않게). 같은 요청의 다른 잡 이벤트는
        payload.job_id 로 거른다."""
        streak, last = 0, None
        events = self._repos.observability.events_of_types(
            job["request_id"], ("identity_recheck_deferred", "identity_groups_checked"), limit=50)
        for e in events:
            payload = e.get("payload")
            if not isinstance(payload, dict) or payload.get("job_id") != job["job_id"]:
                continue
            if e["event_type"] == "identity_groups_checked":
                streak, last = 0, None
            else:
                streak += 1
                at = payload.get("attempted_at_epoch")
                last = at if isinstance(at, (int, float)) and not isinstance(at, bool) else None
        return streak, last

    def _judge(self, job, ident, gids, phase, fresh):
        change = _identity_change(ident, gids, fresh)
        if change is not None:
            raise IdentityChangedAtStep({"job_id": job["job_id"], "phase": phase, "queued": False, **change})
        # 통과 기록은 D2 streak 리셋 표식을 겸한다. 일반 record_event 인 이유: 기록이 실패하면 streak 가 리셋되지
        # 않아 다음 단계의 장애 재시도가 줄어드는 쪽(보수적)이라 strict 로 막을 이유가 없다.
        self._repos.observability.record_event(
            component="stepper", severity="info", event_type="identity_groups_checked",
            message=f"보조 그룹 재확인 통과 {phase} gids={gids}"[:500],
            payload={"job_id": job["job_id"], "phase": phase, "gids": gids},
            request_id=job.get("request_id"))

    def _recheck_groups_for_submit(self, job, ident, gids, phase):
        """D1 제출 직전 재확인 + D2 보류·재시도. 순서가 계약이다(값싼 판정 먼저, 쓸 수 있는 결과를 두고 기다리지 않음):
        (1) 같은 틱 캐시 적중 → LDAP 호출 없이 판정(간격 보류보다 먼저 -- 이미 받은 최신값을 두고 60s 를 더 기다리지
        않는다) (2) 간격(마지막 계수 시도 < 60s 면 미계수 보류) (3) 같은 틱 서킷·예산(미계수 보류) (4) resolve.
        실패(같은 틱 같은 사용자의 IdentityLookupInvalid 캐시 포함)는 계수 보류, streak 3 에서 또 실패하면 종단."""
        if self._resolver is None:
            raise LdapNotConfiguredAtStep()
        username = ident["username"]
        hit = self._fresh_cache.get(username, _MISS)
        if hit is not _MISS and not isinstance(hit, Exception):
            return self._judge(job, ident, gids, phase, hit)
        streak, last_epoch = self._recheck_history(job)
        now_epoch = iso_epoch(self._clock())
        if last_epoch is not None and now_epoch - last_epoch < _LDAP_RECHECK_SPACING_SECONDS:
            raise IdentityRecheckHeld(phase, counted=False, reason="spacing")
        if hit is _MISS:
            gate = self._ldap_gate()
            if gate is not None:
                raise IdentityRecheckHeld(phase, counted=False, reason=gate)
            try:
                fresh = self._resolve_fresh(username)
            except IdentityDeadlineExceeded:
                # 남은 예산이 멈춘 resolve = 예산 소진 -- LDAP 판정이 아니라 미계수 보류(게이트의 ldap_budget 과 같다).
                raise IdentityRecheckHeld(phase, counted=False, reason="ldap_budget") from None
            except IdentityUnavailable as exc:
                failure = exc
            else:
                return self._judge(job, ident, gids, phase, fresh)
        else:
            failure = hit      # 같은 틱 같은 사용자의 IdentityLookupInvalid -- 재호출 없이 이 잡의 시도로 센다
        # LDAP 원문(URI·소켓 오류)은 로그에만 -- 요청 이벤트는 비관리자 요청자에게도 반환된다.
        logger.warning("identity recheck failed job=%s phase=%s: %s", job["job_id"], phase, failure)
        if streak >= _LDAP_RECHECK_RETRIES:
            raise IdentityRecheckExhausted(phase, attempts=streak + 1)
        raise IdentityRecheckHeld(phase, counted=True, reason="ldap_unavailable",
                                  attempt=streak + 1, attempted_at_epoch=now_epoch)

    def _record_identity_changed(self, job, exc):
        self._repos.observability.record_event(
            component="stepper", severity="warning", event_type="identity_changed_at_step",
            message=f"LDAP 변경으로 중단 missing={exc.payload.get('missing_gids')} job={job['job_id']}"[:500],
            payload=exc.payload, request_id=job.get("request_id"))

    def _note_queued_recheck(self, job, phase):
        """vcjob PENDING(preview·execution) 1패스: 모양은 즉시 검사하고(제출 뒤 변조 -- 도구 시작 전이라 종단해도
        잃을 것이 없다), LDAP 비교는 2패스로 미룬다. 파드 단계(preflight·exec_preflight)는 넣지 않는다 -- 바로 뒤에
        반드시 제출 재확인이 오므로 LDAP 부하만 더한다."""
        wp = job["worker_pool"] if isinstance(job["worker_pool"], dict) else {}
        ident = wp.get("identity")
        problem = identity_problem(ident)
        if problem is not None:
            raise IdentityMissingAtStep(problem)
        if ident.get("privileged") or self._resolver is None:
            return
        v = ident.get("supplementary_gids")
        if v is None or len(v) == 0:      # null ≠ 0: 부재·[] 만 '없음'(모양 검사를 통과했으니 v 는 list 또는 None)
            return
        self._queued_rechecks.append((job, phase))

    def _run_queued_rechecks(self, results):
        for job, phase in self._queued_rechecks:
            jid = job["job_id"]
            try:
                self._queued_recheck_one(job, phase)
            except IdentityChangedAtStep as exc:
                self._record_identity_changed(job, exc)
                results[jid] = self._fail_closed(job, reason_code="identity_changed_at_step")
            except Exception as exc:     # 잡마다 격리(run_once 의 step_error 관례)
                print(f"stepper queued recheck error on {jid}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                results[jid] = f"error:{type(exc).__name__}"
                self._repos.observability.record_event(
                    component="stepper", severity="error", event_type="step_error",
                    message=f"{type(exc).__name__}: {exc}"[:500], request_id=job.get("request_id"))

    def _queued_recheck_one(self, job, phase):
        """2패스 한 건. LDAP 불가·서킷·예산·사용자별 조회 오류는 **아무것도 하지 않는다**(미계수·무이벤트 -- 제출 때
        이미 확인됐고, '3번' 계수는 제출 재확인만). 성공도 이벤트를 남기지 않는다(틱마다 남기면 요청 이벤트 목록을
        덮는다). 줄었으면 종단."""
        ident = job["worker_pool"]["identity"]      # 1패스가 모양을 검사했다
        gids = list(ident["supplementary_gids"])
        hit = self._fresh_cache.get(ident["username"], _MISS)
        if hit is _MISS:
            if self._ldap_gate() is not None:
                return
            try:
                hit = self._resolve_fresh(ident["username"])
            except IdentityUnavailable:      # IdentityDeadlineExceeded(예산 소진) 포함 -- 큐 재확인 실패는 전부 no-op
                return
        if isinstance(hit, Exception):
            return
        change = _identity_change(ident, gids, hit)
        if change is not None:
            raise IdentityChangedAtStep({"job_id": job["job_id"], "phase": phase, "queued": True, **change})

    def _abs(self, storage_name, rel):
        storage = self._repos.storages.get(storage_name)
        root = (storage or {}).get("managed_root")
        if root is None or root == "":
            # 컬럼이 NOT NULL(migrations.py:209)이라 여기 도달은 사실상 "행
            # 삭제"와 직접 DB 조작뿐이다(설계 §1-8). 폴백 금지 -- fail-closed.
            raise StorageMissingAtStep(storage_name)
        # f-string 결합이 아니라 join(설계 §2.2): 검증 이전에 DB 에 남아 있을 수
        # 있는 root "/" 행에서 f"{root}/{rel}" 은 "//rel" 을 만들고 POSIX 는
        # "//" 를 구현 정의로 취급한다(문자열 비교 계열 -- 감사 로그·아티팩트
        # 표시 -- 와도 어긋난다). normpath 후처리는 "//x" 를 보존해서(실측)
        # 대안이 못 된다. 정상 root 에선 출력이 동일하다(test_stepper_enrich 앵커).
        # lstrip("/") 이 붙는 이유: join 은 둘째 인자가 절대경로면 root 를 **버린다**
        # (join("/cephfs/dms", "/etc") == "/etc"). 요청 경로는 validate_relative_path
        # (domain.py:79)가 절대경로를 막지만 create_job 은 무검증 INSERT 라 DB 가
        # 신뢰 경계다(§1-1) -- 변조된 절대 target 이 그대로 실리면 drm 이
        # managed_root 밖을 지운다. 기존 f-string 은 "/cephfs/dms//etc" 로 안에
        # 가뒀었고, 그 봉쇄를 join 치환의 부수효과로 잃을 수는 없다.
        return posixpath.join(root, rel.lstrip("/"))

    def _artifact_base(self):
        # 슬라이스 18: DB 가 env 를 이긴다(설계 §2.1). JobStepper 는 매 틱
        # 재생성되고 정책도 매 틱 DB 재조회라 이 조회가 새 비용을 만들지 않는다.
        # 스냅숏을 들고 있으면 base 변경이 컨트롤러 재시작 전까지 반영되지
        # 않는다(설계 §1-7).
        return resolve_artifact_base(self._repos.control, self._settings)

    def _raise_if_base_unsafe_for_groups(self, job):
        path = strip_scheme(self._artifact_base())
        try:
            problem = static_base_problem(path)
        except OSError as exc:
            # 컨트롤러가 base 를 못 봤다(마운트 없음 등) -- '모름' 을 통과로 접는 게 아니라 판정을 preflight 에 넘긴다:
            # 잡 파드의 hostPath(type Directory)·test -x·other-x 비트·test -w 가 그대로 돈다. 여기서 종단하면 컨트롤러
            # 쪽 마운트 문제 하나로 모든 그룹 잡이 끝난다(3홉 화면의 컨트롤러 홉이 그 사실을 따로 보인다).
            logger.warning("artifact base static check skipped job=%s: %s", job["job_id"], exc)
            return
        if problem is not None:
            raise ArtifactBaseUnsafeAtStep(problem)

    def _build_spec(self, job, phase, dryrun):
        # 비-dict worker_pool(변조 행)도 identity_missing 경로로 -- AttributeError 로
        # 새면 run_once 의 step_error 루프(매 틱 재시도)에 영구히 낀다.
        wp = job["worker_pool"] if isinstance(job["worker_pool"], dict) else {}
        # 신원 가드(IdentityMissingAtStep docstring): 어댑터가 uid/gid 를 0 으로
        # 기본값 처리하기 **전에** 끊는다. 모든 제출 경로(preflight/preview/
        # exec_preflight/execution)가 이 함수를 지나므로 여기가 단일 관문이다 --
        # 보조 gid 가 있으면 LDAP 재확인(D1)도 여기서 한다(값싼 검사들 뒤, JobSpec 앞).
        problem = identity_problem(wp.get("identity"))
        if problem is not None:
            raise IdentityMissingAtStep(problem)
        # root 근거 재확인(PrivilegeNotRequestedAtStep docstring). identity_problem 을
        # 통과했으면 identity 는 dict 이고 privileged == (uid == 0) 이다. 비특권 잡은
        # 요청 행을 읽지 않는다(추가 조회는 root 잡에만).
        if wp["identity"].get("privileged"):
            req = self._repos.requests.get(job["request_id"])
            if privilege_policy(req) == PRIVILEGE_NEVER:
                raise PrivilegeNotRequestedAtStep(job["request_id"])
        # 노드 배치 제외·k8s 스케줄 불가(NodeBlockedAtStep docstring) -- 모든 제출 경로가 여기를 지난다. 제출 뒤
        # 아직 스케줄 전(PENDING)인 단계는 폴링이 다시 본다(_raise_if_blocked).
        self._raise_if_blocked(job)
        options = job["options"] if isinstance(job["options"], dict) else {}
        if job["tool"] in ("dsync", "nsync") and "chown" in options:
            problem = chown_problem(options["chown"])
            if problem is not None:
                raise ChownNameAtStep(problem)
        op = job["operation"]
        if op == "sync":
            paths = {"source": self._abs(job["source_storage"], job["source"]),
                     "source_storage": job["source_storage"],
                     "destination": self._abs(job["destination_storage"], job["destination"]),
                     "destination_storage": job["destination_storage"]}
        else:
            paths = {"target": self._abs(job["storage_name"], job["target"]),
                     "storage": job["storage_name"]}
        # job["tool"]은 실행 파일 이름(dscan/dsync/nsync/drm)이지 정책 키(scan/dsync/
        # nsync/rm)가 아니다 -- planner.py가 policy를 조회할 때 쓰는 것과 동일한
        # TOOL_TO_POLICY 매핑을 거쳐야 scan/rm 잡의 정책을 정확히 찾는다.
        # 미지 tool 은 _step_one 층1 가드(슬라이스 24)가 이미 종단시켰으므로 여기서
        # 직접 인덱싱해도 KeyError 불능이다. policy None 은 이제 "정책 행이 지워진"
        # 운영 조작뿐이라 크래시 대신 타임아웃 없음으로 관용한다(기존 동작 유지).
        policy = self._repos.control.get_policy(TOOL_TO_POLICY[job["tool"]])
        if policy is None:
            timeout = None
        elif phase == "execution":
            timeout = policy["execution_timeout_seconds"]
        else:
            timeout = policy["preview_timeout_seconds"]
        # 보조 그룹 재확인(D1, 모듈 docstring) -- 값싼 실패(모양·root 근거·노드·chown·경로)가 LDAP 보다 먼저다.
        # 모양 검사를 통과했으니 v 는 None(배포 전 잡 = 없음) 또는 유효 list. 비교는 `is None`(null ≠ []).
        v = wp["identity"].get("supplementary_gids")
        gids = [] if v is None else list(v)
        if gids:
            # base 정적 관문(ArtifactBaseUnsafeAtStep docstring) -- stat·getxattr 라 LDAP 보다 싸다. 그룹이 없는 잡은
            # 종전 그대로(그 잡의 쓰기 범위는 주 gid 뿐이라 기존 preflight 판정이 유지된다).
            self._raise_if_base_unsafe_for_groups(job)
            self._recheck_groups_for_submit(job, wp["identity"], gids, phase)
        return JobSpec(
            job_id=job["job_id"], phase=phase, operation=op, tool=job["tool"],
            # 키를 항상 싣는 정규화 사본 -- 빌더는 재확인을 통과한 **스냅숏**(최신값이 아니라)만 본다.
            dryrun=dryrun, identity={**wp["identity"], "supplementary_gids": gids}, paths=paths,
            options=job["options"] or {}, candidates=wp.get("candidates", {}),
            process_count=wp.get("process_count", 1), queue=wp.get("queue", "dms-data"),
            priority_class=wp.get("priority_class", "dms-mid"),
            artifact_base=self._artifact_base(), timeout_seconds=timeout,
            ttl_seconds=self._settings.vcjob_ttl_seconds)

    def _finalize(self, job, job_state, *, reason_code=None, summary=None, diag=None):
        # 슬라이스 25 §2.2: diag=(phase, ref) 가 오면 종단 전이 **전에** 박제한다.
        # 순서가 계약이다 -- 박제 후 크래시하면 잡이 비종단으로 남아 다음 틱이
        # finalize 를 재시도하고(archive 는 IS NULL 이 중복을 막는다), 역순이면
        # 종단 잡은 다시 스텝되지 않아 박제 기회가 영영 사라진다.
        if diag is not None:
            self._archive_diag(job, *diag)
        self._repos.data_jobs.set_job_state(job["job_id"], job_state,
                                            reason_code=reason_code, actor="stepper")
        self._repos.requests.finalize_from_job(
            job["request_id"], job_state, reason_code=reason_code, summary=summary,
            actor="stepper")

    def _record_submit_failure(self, job, phase, exc):
        """제출 실패 원문 보존(2026-09-15 프로덕션 사고: apiserver 422 의 causes --
        볼륨 이름 RFC 1123 위반 -- 가 어디에도 남지 않아 컨트롤러 파드 안에서 제출
        경로를 재현해야 원인을 알 수 있었다). 어댑터가 blanket except 로 접은
        str(exc)[:200] 이 exc.detail 이다. (1) 관측 이벤트(submit_failed)로 운영
        콘솔에, (2) diag_logs 에 합성 항목(pod="submit:<phase>")으로 박제해 요청
        상세의 진단 로그 자리에 보이게 한다 -- 파드가 만들어지지 않았으니 박제할
        파드 로그는 없고 이 원문이 유일한 진단이다. 기록 실패는 finalize 를 막지
        않는다(_archive_diag 와 같은 원칙)."""
        try:
            self._repos.observability.record_event(
                component="stepper", severity="warning", event_type="submit_failed",
                message=f"{phase}: {exc.detail}"[:500],
                payload={"phase": phase, "reason_code": exc.reason_code},
                request_id=job.get("request_id"))
            self._repos.data_jobs.archive_diag_logs(
                job["job_id"], phase=phase,
                entries=[{"pod": f"submit:{phase}",
                          "log": f"{exc.reason_code}: {exc.detail}", "truncated": False}])
        except Exception as inner:  # noqa: BLE001 -- 진단 기록이 종단을 막으면 잡이 낀다
            logger.warning("submit failure record failed job=%s: %s", job["job_id"], inner)

    def _archive_diag(self, job, phase, ref):
        """실패 종단 시점 파드 로그 박제(설계 §2.2). 어댑터가 launcher 를 앞에
        놓으므로 [:DIAG_MAX_ENTRIES] 상한이 잘라도 launcher 가 산다. 박제 실패는
        finalize 를 막지 않는다 -- 한 잡의 로그 때문에 종단 전이가 막히면 잡이
        낀다 -- 대신 이벤트로 표면화한다(조용한 실패 금지, 설계 §4).

        반환값은 read_log 원본(못 읽었으면 None)이다 -- preflight 경로가 같은
        조회 결과에서 사유 마커까지 뽑는다(_preflight_reason). 두 번 읽으면
        박제된 로그와 사유가 어긋날 수 있고(그 사이 파드 GC) k8s 조회도 공짜가
        아니다. raw 를 먼저 잡아두는 이유: 박제(archive_diag_logs)만 실패해도
        사유 승격은 살아야 한다."""
        raw = None
        try:
            raw = self._exec.read_log(ref)
            entries = [_diag_entry(pod, log)
                       for pod, log, _wr in raw[:DIAG_MAX_ENTRIES]]
            self._repos.data_jobs.archive_diag_logs(job["job_id"], phase=phase,
                                                    entries=entries)
        except Exception as exc:
            self._repos.observability.record_event(
                component="stepper", severity="warning",
                event_type="diag_archive_failed",
                message=f"{type(exc).__name__}: {exc}"[:500],
                payload={"phase": phase, "ref": ref},
                request_id=job.get("request_id"))
        return raw

    def _preflight_reason(self, job, phase, ref, *, reason_code):
        """preflight 실패의 사유 결정 + 진단 로그 박제(한 번의 read_log 로 둘 다).

        인자 이름이 fallback 이 아니라 reason_code 인 이유: 사유 코드 커버리지
        그물(tests/test_reason_codes_coverage.py)은 `reason_code=` 키워드 리터럴만
        AST 로 추출한다 -- 이름을 바꾸면 폴백 두 코드가 그물 밖으로 빠진다.

        스크립트가 찍은 DMS_PREFLIGHT_REASON= 마커를 잡 사유로 승격한다. 승격
        경로가 없던 동안 모든 preflight 실패는 fallback 하나로 뭉개졌다 --
        "목적지 부모가 없다"와 "소스를 읽을 수 없다"가 화면에서 구분되지 않아
        운영자가 파드 로그를 직접 열어야 했고, 파드는 GC 로 시한부다.

        마커가 없거나 화이트리스트(execution_manifests.PREFLIGHT_REASONS) 밖이면
        넘어온 reason_code 로 접는다 -- 파드 로그는 신뢰 입력이 아니라(설계 §4)
        임의 문자열을 사유에 박으면 프론트 매핑이 없어 원문 코드가 그대로
        노출된다. 박제 순서는 그대로 유지된다: 여기서 박제한 뒤 _finalize 가
        전이하므로 "박제 -> set_job_state" 계약이 깨지지 않는다.

        LDAP 보류 중 사라진 파드(2026-10-07 D2): 보류는 preflight 가 **성공한 뒤** 다음 제출 직전에만 생기고, 그동안
        Succeeded 파드가 남아 매 틱 다시 SUCCEEDED 로 폴링된다. 그 파드가 수동 삭제·kube terminated-pod GC 로
        사라지면 폴링은 FAILED(마커 없음)라 폴백(preflight_failed/execution_recheck_failed)은 실제 원인과 다른
        사유다. 마지막 재확인 이벤트가 deferred(streak > 0)면 그 경우로 보고 ldap_unavailable 로 접는다 -- 재확인
        통과가 streak 를 리셋하므로 통과 뒤의 진짜 preflight 실패를 오판하지 않는다."""
        raw = self._archive_diag(job, phase, ref)
        promoted = parse_preflight_reason(raw)
        if promoted is None and self._recheck_history(job)[0] > 0:
            self._repos.observability.record_event(
                component="stepper", severity="warning", event_type="identity_recheck_failed",
                message=f"LDAP 보류 중 {phase} 파드가 사라져 중단",
                payload={"job_id": job["job_id"], "phase": phase, "reason": "held_pod_vanished"},
                request_id=job.get("request_id"))
            return "ldap_unavailable"
        return reason_code if promoted is None else promoted

    def _run_failure_reason(self, job, phase, ref, *, reason_code):
        """미리보기·실행(launcher 가 mpirun 을 돌리는 단계) 실패의 사유 결정 + 진단 로그 박제(read_log 한 번).
        _preflight_reason 과 같은 구조: 러너가 찍은 DMS_EXEC_REASON= 마커(화이트리스트
        execution_manifests.EXECUTION_REASONS)를 잡 사유로 승격하고, 없으면 넘어온 reason_code 로 접는다.
        2026-10-02: 워커 준비 실패(workers_unreachable)가 첫 마커 -- 예전엔 사유 없는 execution_failed 였다."""
        raw = self._archive_diag(job, phase, ref)
        promoted = parse_execution_reason(raw)
        return reason_code if promoted is None else promoted

    def _surface_failed_artifact(self, job, ref):
        """실패 잡의 summary 표면화(설계 §2.4). 러너는 도구 비0 종료에도
        summary.json 을 쓰고 exit 하므로(§1-7) returncode 가 카드에 뜬다.
        read_summary 예외는 None(모름)으로 접는다 -- 실패 잡의 보강은 best-effort
        고, 여기서 던지면 종단 전이 자체가 막혀 run_once 의 step_error 루프(매 틱
        재시도)에 낀다. None 이면 기록하지 않는다 -- artifact_uri 를 지어내면
        포탈이 존재하지 않는 디렉터리를 가리킨다. 비교가 `is not None` 인 것은
        이 슬라이스의 규칙 그대로다: 빈 summary({})는 "러너가 쓰긴 썼다"는
        정상값이라 모름과 뭉개지 않는다. metrics 는 오염되지 않는다(합계가
        state='Succeeded' 필터 -- §1-12)."""
        try:
            summary = self._exec.read_summary(ref)
        except Exception:
            summary = None
        if summary is not None:
            self._repos.data_jobs.set_artifact(
                job["job_id"],
                artifact_uri=f"{self._artifact_base()}/{job['job_id']}",
                result_summary=summary)

    def _job_gone(self, job_id) -> bool:
        """잡 행이 없는가(요청 삭제). 조회 실패는 False(모름 -- 지금처럼 request_id 를 단 채 남긴다)."""
        try:
            return self._repos.data_jobs.get_job(job_id) is None
        except Exception:  # noqa: BLE001 -- 진단 보강용 조회가 step_error 기록을 막으면 안 된다
            return False

    def _record_ref(self, job, phase, ref) -> bool:
        """제출 직후 ref 를 행에 남긴다(2026-10-08, 요청 삭제 선행 보강). False = 잡 행이 없다 -- claim 스냅숏 뒤
        관리자가 요청을 지웠다. 방금 만든 Pod/vcjob 은 여기서 이미 회수했으니 호출자는 "gone" 을 돌려준다(진행하면
        요청 없는 실제 rm/sync 가 고아로 돈다)."""
        if self._repos.data_jobs.set_phase_ref(job["job_id"], phase, ref):
            return True
        self._reclaim_deleted(job, ref)
        return False

    def _reclaim_deleted(self, job, ref):
        """행이 사라진 잡에 방금 제출한 ref 를 회수한다. 이벤트는 request_id=NULL 로 남긴다 -- 지워진 id 로 쓰면
        어디서도 안 보이는 고아가 된다(job·request id 는 payload 에). terminate 실패도 같은 이벤트에 싣고 severity 를
        error 로 올린다: 요청 없는 잡이 클러스터에서 돌고 있을 수 있다(조용히 두지 않는다)."""
        error = None
        try:
            self._exec.terminate(ref)
        except ExecutionError as exc:
            error = exc.reason_code
        self._repos.observability.record_event(
            component="stepper", severity="warning" if error is None else "error",
            event_type="submitted_for_deleted_job",
            message=(f"job {job['job_id']} deleted after claim -- reclaimed {ref}" if error is None
                     else f"job {job['job_id']} deleted after claim -- terminate {ref} failed: {error}")[:500],
            payload={"job_id": job["job_id"], "request_id": job.get("request_id"), "ref": ref,
                     "terminate_error": error},
            request_id=None)

    def _reclaim_if_terminal(self, job, ref):
        """제출 직후 잡이 이미 종단이면(= claim과 제출 사이에 취소가 들어왔다) 방금 만든
        Pod/vcjob을 즉시 회수하고 현재 상태를 돌려준다. None이면 계속 진행해도 된다.

        claim_steppable의 스냅샷에는 잠금이 없다 — 커넥션이 autocommit이라
        FOR UPDATE SKIP LOCKED가 곧바로 풀린다. 그 창에서 취소된 잡도 _step_one이
        그대로 제출해 버리고, 뒤따르는 set_job_state는 종단 가드가 삼키므로 클러스터에만
        고아가 남는다. 그 고아는 아무도 못 치운다 — cancel_job은 종단 잡에 409,
        terminate_job은 종단 잡에 no-op이기 때문. 그래서 여기서 한 번 더 읽는다.

        행이 아예 없으면(set_phase_ref 뒤에 요청이 지워졌다 -- 삭제 게이트의 조용한 창이 방금 updated_at 이 찍힌
        잡을 거르므로 좁은 창이다) 스냅숏 상태로 폴백하지 않고 같은 회수 후 "gone" 이다(예전 폴백은 스냅숏의 비종단을
        읽어 계속 진행했다)."""
        current = self._repos.data_jobs.get_job(job["job_id"])
        if current is None:
            self._reclaim_deleted(job, ref)
            return "gone"
        state = current["state"]
        if DataJobState(state) not in TERMINAL_DATA_JOB_STATES:
            return None
        try:
            self._exec.terminate(ref)
        except ExecutionError as exc:
            # best-effort -- 잡은 이미 종단이라 더 기록할 상태가 없다. 그래도 고아
            # 리소스가 남았을 수 있으니 진단 채널에는 남긴다.
            self._repos.observability.record_event(
                component="stepper", severity="warning", event_type="terminate_failed",
                message=exc.reason_code, payload={"ref": ref},
                request_id=job.get("request_id"))
        return state

    # FAILED 로 갈리는 상태: 실행 자원이 이미 붙었다(execution vcjob 제출 이후).
    # 그 전 단계는 REJECTED -- preflight_submit_failed→REJECTED /
    # execution_submit_failed→FAILED 의 기존 대칭을 그대로 따른다(설계 §2.1).
    _EXEC_STATES = (DataJobState.EXECUTING.value, DataJobState.RUNNING.value)

    def _fail_closed(self, job, *, reason_code):
        """신뢰 경계가 깨진 잡(미지 tool·스텝 시점 스토리지 결측)의 종단 처리.

        살아 있을 수 있는 phase_refs 는 _reclaim_if_terminal 관례대로 best-effort
        terminate 하고, 실패는 terminate_failed 이벤트로 남긴다 -- 고아 리소스를
        조용히 두지 않는다(설계 §4). 이미 끝난 파드의 terminate 는 무해하다."""
        target = (DataJobState.FAILED if job["state"] in self._EXEC_STATES
                  else DataJobState.REJECTED)
        for ref in (job["phase_refs"] or {}).values():
            try:
                self._exec.terminate(ref)
            except ExecutionError as exc:
                self._repos.observability.record_event(
                    component="stepper", severity="warning",
                    event_type="terminate_failed", message=exc.reason_code,
                    payload={"ref": ref}, request_id=job.get("request_id"))
        self._finalize(job, target, reason_code=reason_code)
        return target.value

    def _step_one(self, job) -> str:
        # 슬라이스 24 §2.1 층1: tool 의 유일한 정상 원천은 placement 의 리터럴
        # 4종이고 create_job 은 무검증 INSERT 다(§1-1) -- DB 가 신뢰 경계다.
        # 미지 tool 이 층2 이전의 fall-through 를 타면 drm 꼴 argv(파괴적)로
        # 실행되므로 제출 전에 종단시킨다. 이 가드로 _build_spec 의 "미지 tool ->
        # 타임아웃 없음" 관용 분기는 도달 불능이 되어 제거했다 -- 그 주석이
        # 걱정한 "매 틱 예외로 영구히 낀 잡"은 종단이라 애초에 생기지 않는다.
        if job["tool"] not in TOOL_TO_POLICY:
            return self._fail_closed(job, reason_code="unknown_tool")
        try:
            return self._dispatch(job)
        except StorageMissingAtStep as exc:
            # 종단 전이의 reason_code 만으론 "어느 스토리지가 없었는지"가 남지
            # 않는다 -- 이벤트로 보강한다(설계 §2.4). run_once 의 step_error
            # (매 틱 재시도 루프)와 달리 여기는 종단이라 한 번만 남는다.
            self._repos.observability.record_event(
                component="stepper", severity="error",
                event_type="storage_missing_at_step",
                message=f"storage={exc.storage_name} job={job['job_id']}",
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="storage_missing_at_step")
        except IdentityMissingAtStep as exc:
            # 같은 신뢰 경계 위반(변조 행)의 신원 판 -- 어댑터의 0/root 기본값이
            # 도달 불능이 되도록 제출 전에 종단시킨다.
            self._repos.observability.record_event(
                component="stepper", severity="error",
                event_type="identity_missing_at_step",
                message=f"{exc.problem} job={job['job_id']}",
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="identity_missing_at_step")
        except ChownNameAtStep as exc:
            # 규칙(숫자 uid:gid 만) 전에 만들어진 잡 -- root 로 이름이 uid 0 으로 풀리기 전에 끊는다.
            self._repos.observability.record_event(
                component="stepper", severity="error", event_type="chown_name_at_step",
                message=f"{exc.problem} job={job['job_id']}",
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="chown_name_not_supported")
        except NodeBlockedAtStep as exc:
            # 계획 뒤 막힌 노드(관리자 배치 제외·cordon) -- 어느 노드가 왜 막혔는지는 이벤트로 남긴다(종단 사유
            # 코드만으론 노드가 안 보인다). 배치 항목도 이 종단을 그대로 받는다(사용자 결정 2026-10-02: 종료).
            self._repos.observability.record_event(
                component="stepper", severity="warning", event_type="node_excluded_at_step",
                message=f"{exc} job={job['job_id']}", payload={"blocked": exc.blocked},
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="node_excluded_at_step")
        except PrivilegeNotRequestedAtStep:
            # 규칙 변경 전에 얼린 root 신원·변조 행 -- root 로 한 번이라도 제출되기 전에
            # 끊는다(재계획은 하지 않는다: 요청자가 의도를 다시 정해 새로 내야 한다).
            self._repos.observability.record_event(
                component="stepper", severity="error",
                event_type="privilege_not_requested",
                message=f"privileged identity without run_as_root/batch job={job['job_id']}",
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="privilege_not_requested")
        except ArtifactBaseUnsafeAtStep as exc:
            # 그룹 잡의 base 정적 관문 -- 어느 판정이었는지는 사유 코드가 말한다(경로는 싣지 않는다: 관리자 설정).
            self._repos.observability.record_event(
                component="stepper", severity="error", event_type="artifact_base_unsafe_at_step",
                message=f"{exc.problem} job={job['job_id']}",
                payload={"job_id": job["job_id"], "problem": exc.problem},
                request_id=job.get("request_id"))
            if exc.problem == "artifact_base_not_traversable":
                return self._fail_closed(job, reason_code="artifact_base_not_traversable")
            return self._fail_closed(job, reason_code="artifact_base_group_writable")
        # ---- 보조 그룹 재확인(D1·D2). 이벤트 message·payload 에 LDAP 예외 원문을 싣지 않는다(로그에만) ----
        except IdentityChangedAtStep as exc:
            self._record_identity_changed(job, exc)
            return self._fail_closed(job, reason_code="identity_changed_at_step")
        except IdentityRecheckHeld as exc:
            if exc.counted:
                try:
                    # 이 이벤트가 곧 D2 카운터다 -- strict 기록(실패하면 예외).
                    self._repos.observability.record_event_strict(
                        component="stepper", severity="warning", event_type="identity_recheck_deferred",
                        message=f"LDAP 재확인 불가 -- {exc.phase} 제출 보류 {exc.attempt}/{_LDAP_RECHECK_RETRIES + 1}",
                        payload={"job_id": job["job_id"], "phase": exc.phase, "attempt": exc.attempt,
                                 "max_attempts": _LDAP_RECHECK_RETRIES + 1,
                                 "attempted_at_epoch": exc.attempted_at_epoch},
                        request_id=job.get("request_id"))
                except Exception as inner:  # noqa: BLE001 -- 카운터를 못 쓰면 "3번만"을 보장할 수 없다: fail-closed
                    logger.warning("recheck counter write failed job=%s: %s", job["job_id"], inner)
                    self._repos.observability.record_event(
                        component="stepper", severity="error", event_type="identity_recheck_failed",
                        message=f"LDAP 재확인 보류 기록 실패로 중단 -- {exc.phase}",
                        payload={"job_id": job["job_id"], "phase": exc.phase, "reason": "counter_unwritable"},
                        request_id=job.get("request_id"))
                    return self._fail_closed(job, reason_code="ldap_unavailable")
            # 보류: 상태 불변, claim 큐 뒤로만(다른 잡을 굶기지 않는다). 다음 틱에 같은 분기로 _build_spec 에 다시 온다.
            self._repos.data_jobs.touch(job["job_id"], expected_state=job["state"])
            return job["state"]
        except IdentityRecheckExhausted as exc:
            self._repos.observability.record_event(
                component="stepper", severity="error", event_type="identity_recheck_failed",
                message=f"LDAP 재확인 {exc.attempts}회 실패 -- {exc.phase} 종단",
                payload={"job_id": job["job_id"], "phase": exc.phase, "attempts": exc.attempts,
                         "reason": "ldap_unavailable"},
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="ldap_unavailable")
        except LdapNotConfiguredAtStep:
            self._repos.observability.record_event(
                component="stepper", severity="error", event_type="identity_recheck_failed",
                message=f"보조 그룹 재확인 불가(LDAP 미구성) job={job['job_id']}",
                payload={"job_id": job["job_id"], "reason": "ldap_not_configured"},
                request_id=job.get("request_id"))
            return self._fail_closed(job, reason_code="ldap_not_configured")

    def _dispatch(self, job) -> str:
        state = job["state"]
        if state == DataJobState.PENDING.value:
            return self._submit_preflight(job)
        if state == DataJobState.PREFLIGHT.value:
            return self._poll_preflight(job)
        if state == DataJobState.RUNNING.value:
            return self._poll_execution(job)
        if state == DataJobState.PREVIEW_RUNNING.value:
            return self._poll_preview(job)
        if state == DataJobState.EXECUTING.value:
            return self._poll_or_submit_execution(job)
        return state

    def _submit_preflight(self, job):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "preflight", dryrun=False))
        except ExecutionError as exc:
            self._record_submit_failure(job, "preflight", exc)
            self._finalize(job, DataJobState.REJECTED,
                           reason_code=f"preflight_submit_failed:{exc.reason_code}")
            return "Rejected"
        if not self._record_ref(job, "preflight", ref):
            return "gone"
        reclaimed = self._reclaim_if_terminal(job, ref)
        if reclaimed is not None:
            return reclaimed
        self._repos.data_jobs.set_job_state(jid, DataJobState.PREFLIGHT, actor="stepper")
        return "Preflight"

    def _raise_if_blocked(self, job):
        """제출됐지만 아직 스케줄되지 않은(PENDING) 단계의 노드 재검사(2026-10-02 적대적 리뷰). 제출 직전 검사만으로는
        Volcano 큐·gang 대기 중에 막힌 노드를 못 본다 -- cordon 이면 gang 이 안 서서 영원히 멈추고, 배치 제외만이면
        큐가 풀리는 순간 그 노드에 앉는다. 도구가 아직 시작 전이라 종단해도 잃을 작업이 없다(RUNNING 은 건드리지
        않는다 -- 노드에서 이미 도는 잡은 그대로)."""
        wp = job["worker_pool"] if isinstance(job["worker_pool"], dict) else {}
        blocked = blocked_nodes(self._repos, wp.get("candidates"))
        if blocked:
            raise NodeBlockedAtStep(blocked)

    def _poll_preflight(self, job):
        jid = job["job_id"]
        ref = (job["phase_refs"] or {}).get("preflight")
        status = self._exec.poll(ref)
        if status == ExecStatus.PENDING:
            self._raise_if_blocked(job)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "Preflight"
        if status == ExecStatus.SUCCEEDED:
            # scan: 바로 execution. (sync/rm preview는 Task 7)
            if job["operation"] == "scan":
                return self._submit_execution(job, DataJobState.RUNNING)
            return self._submit_preview(job)  # Task 7에서 구현
        # diag= 를 넘기지 않는 이유: _preflight_reason 이 이미 박제했다(전이 전).
        reason = self._preflight_reason(job, "preflight", ref,
                                        reason_code="preflight_failed")
        self._finalize(job, DataJobState.REJECTED, reason_code=reason)
        return "Rejected"

    def _submit_execution(self, job, running_state):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "execution", dryrun=False))
        except ExecutionError as exc:
            self._record_submit_failure(job, "execution", exc)
            self._finalize(job, DataJobState.FAILED,
                           reason_code=f"execution_submit_failed:{exc.reason_code}")
            return "Failed"
        if not self._record_ref(job, "execution", ref):
            return "gone"
        # 슬라이스 20(설계 §2.2, 플랜 D1): 스케줄 대기의 앵커 = "execution vcjob
        # 제출 직후". 전이 행(Preflight→Running/Executing→Executing)의 at 을 나중에
        # 해석하는 대신 여기서 컬럼에 직접 남긴다 -- 세 모듈 교차 불변식(자기 전이
        # 유일성)에 측정이 얹히지 않고, write-once 는 SQL 술어가 강제한다.
        # preview(_submit_preview)/preflight 제출은 앵커를 남기지 않는다.
        self._repos.data_jobs.mark_exec_submitted(jid)
        reclaimed = self._reclaim_if_terminal(job, ref)
        if reclaimed is not None:
            return reclaimed
        self._repos.data_jobs.set_job_state(jid, running_state, actor="stepper")
        return running_state.value

    def _poll_execution(self, job):
        ref = (job["phase_refs"] or {}).get("execution")
        status = self._exec.poll(ref)
        if status == ExecStatus.RUNNING:
            # 슬라이스 20(설계 §2.3, 플랜 D2): execution vcjob 의 첫 RUNNING 관측
            # -- 스케줄 대기를 write-once 기록한다. execution ref 를 폴링하는
            # 함수는 여기뿐이라(preview 는 _poll_preview, preflight 는
            # _poll_preflight) preview 대기가 섞일 경로가 없다. 이미 기록된 잡은
            # 스냅샷 선독으로 no-op. 기록 실패는 run_once 의 잡 단위 try/except 로
            # 격리되고 다음 틱의 RUNNING 관측이 재시도한다(설계 §4). Completing
            # 등도 RUNNING 으로 접히므로(_VCJOB_PHASE) 이 값은 근사다(설계 §2.2).
            self._repos.data_jobs.record_sched_wait(job)
        if status == ExecStatus.PENDING:
            self._raise_if_blocked(job)
            # vcjob 큐 대기 중 보조 그룹 재확인(D1) -- RUNNING 은 하지 않는다(프로세스 자격이 이미 고정, 노드 재검사와
            # 같은 원칙). 모양은 즉시, LDAP 비교는 2패스(_run_queued_rechecks).
            self._note_queued_recheck(job, "execution")
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return job["state"]
        if status == ExecStatus.SUCCEEDED:
            summary = self._exec.read_summary(ref)
            if summary is None:
                # 정상 잡은 job-runner가 summary.json을 항상 쓴다. None은 컨트롤러가
                # artifact_base 파일시스템을 못 읽는 배포 오구성을 뜻한다 — vcjob phase가
                # 권위이므로 SUCCEEDED는 유지하되, null을 조용히 묻지 않고 가시화한다.
                summary = {"summary_unavailable": True}
                self._repos.observability.record_event(
                    component="stepper", severity="warning",
                    event_type="summary_unreadable", payload={"ref": ref},
                    request_id=job.get("request_id"))
            # 성공 경로도 URI를 남긴다 — preview를 거치지 않는 scan 잡은 여기서만
            # 기록되고, 없으면 포탈이 아티팩트를 가리킬 수 없다.
            self._repos.data_jobs.set_artifact(
                job["job_id"],
                artifact_uri=f"{self._artifact_base()}/{job['job_id']}",
                result_summary=summary)
            self._finalize(job, DataJobState.SUCCEEDED, summary=summary)
            return "Succeeded"
        self._surface_failed_artifact(job, ref)
        if status == ExecStatus.TIMED_OUT:
            self._finalize(job, DataJobState.TIMED_OUT, reason_code="execution_failed",
                           diag=("execution", ref))
            return DataJobState.TIMED_OUT.value
        # 박제는 _run_failure_reason 이 했다(전이 전) -- diag= 를 다시 넘기지 않는다.
        reason = self._run_failure_reason(job, "execution", ref, reason_code="execution_failed")
        self._finalize(job, DataJobState.FAILED, reason_code=reason)
        return DataJobState.FAILED.value

    def _submit_preview(self, job):
        jid = job["job_id"]
        try:
            ref = self._exec.submit(self._build_spec(job, "preview", dryrun=True))
        except ExecutionError as exc:
            self._record_submit_failure(job, "preview", exc)
            self._finalize(job, DataJobState.FAILED,
                           reason_code=f"preview_submit_failed:{exc.reason_code}")
            return "Failed"
        if not self._record_ref(job, "preview", ref):
            return "gone"
        reclaimed = self._reclaim_if_terminal(job, ref)
        if reclaimed is not None:
            return reclaimed
        self._repos.data_jobs.set_job_state(jid, DataJobState.PREVIEW_RUNNING,
                                            actor="stepper")
        return "PreviewRunning"

    def _poll_preview(self, job):
        jid = job["job_id"]
        ref = (job["phase_refs"] or {}).get("preview")
        status = self._exec.poll(ref)
        if status == ExecStatus.PENDING:
            self._raise_if_blocked(job)
            self._note_queued_recheck(job, "preview")   # _poll_execution 과 같은 큐 대기 재확인(D1)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "PreviewRunning"
        if status == ExecStatus.SUCCEEDED:
            summary = self._exec.read_summary(ref)
            fingerprint = _summary_fingerprint(summary)
            if fingerprint is None:
                self._finalize(job, DataJobState.REJECTED, reason_code="empty_preview")
                return "Rejected"
            expires = iso_plus(utc_now_iso(), self._settings.preview_ttl_seconds)
            artifact = f"{self._artifact_base()}/{jid}"
            # 요약 사본도 함께 남긴다(2026-09-17) -- 컨펌 창이 "무엇을 컨펌하는지"
            # (dry-run 개수·크기)를 보여준다. 지문은 바로 이 객체의 해시다.
            self._repos.data_jobs.set_preview(jid, fingerprint=fingerprint,
                                              expires_at=expires, artifact_uri=artifact,
                                              summary=summary)
            self._repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING,
                                                actor="stepper")
            return "ConfirmPending"
        if status == ExecStatus.TIMED_OUT:
            self._surface_failed_artifact(job, ref)
            self._finalize(job, DataJobState.TIMED_OUT, reason_code="preview_timed_out",
                           diag=("preview", ref))
            return "TimedOut"
        self._surface_failed_artifact(job, ref)
        # 박제는 _run_failure_reason 이 했다(전이 전) -- diag= 를 다시 넘기지 않는다(_preflight_reason 과 같은 규칙).
        reason = self._run_failure_reason(job, "preview", ref, reason_code="preview_failed")
        self._finalize(job, DataJobState.FAILED, reason_code=reason)
        return "Failed"

    def _poll_or_submit_execution(self, job):
        jid = job["job_id"]
        refs = job["phase_refs"] or {}
        if "execution" in refs:
            return self._poll_execution(job)
        # confirm 후 execution 전 preflight 재검증 (Phase 3b 파킹 백로그).
        # phase="exec_preflight"(초기 preflight의 "preflight"와 구분) — build_preflight_pod의
        # 파드 이름이 phase를 포함하므로, 초기 preflight 파드가 아직 남아 있어도 이름이
        # 충돌하지 않는다(안 그러면 create가 AlreadyExists→submit_failed로 실패).
        if "exec_preflight" not in refs:
            try:
                ref = self._exec.submit(self._build_spec(job, "exec_preflight", dryrun=False))
            except ExecutionError as exc:
                self._record_submit_failure(job, "exec_preflight", exc)
                self._finalize(job, DataJobState.FAILED,
                               reason_code=f"execution_recheck_submit_failed:{exc.reason_code}")
                return "Failed"
            if not self._record_ref(job, "exec_preflight", ref):
                return "gone"
            reclaimed = self._reclaim_if_terminal(job, ref)
            if reclaimed is not None:
                return reclaimed
            return "Executing"
        status = self._exec.poll(refs["exec_preflight"])
        if status == ExecStatus.PENDING:
            self._raise_if_blocked(job)
        if status in (ExecStatus.PENDING, ExecStatus.RUNNING):
            return "Executing"
        if status == ExecStatus.SUCCEEDED:
            return self._submit_execution(job, DataJobState.EXECUTING)
        # 재검증도 같은 preflight 스크립트다 -- 미리보기와 confirm 사이에 목적지가
        # 파일로 바뀌는 TOCTOU 가 여기서 잡히므로 사유를 뭉개지 않는다.
        reason = self._preflight_reason(job, "exec_preflight", refs["exec_preflight"],
                                        reason_code="execution_recheck_failed")
        self._finalize(job, DataJobState.REJECTED, reason_code=reason)
        return "Rejected"
