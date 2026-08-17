"""빌드 상태를 파드에서 DB로 옮기는 컨트롤러 루프.

루프 안의 예외는 controller.run_all_once 가 삼켜 stderr 로만 내보낸다 --
그래서 실패는 예외로 새지 않고 반드시 builds.state 로 드러나야 한다.

슬라이스 21(§2.5): Pending 빌드는 곧장 제출하지 않는다 -- 적합성 프로브 파드가
빌드 노드에서 egress/레지스트리/디스크를 실검사하고, 통과해야 빌드 파드를 낸다.
상태를 DB 에 더 만들지 않는다: 프로브 파드 이름이 build_id 에서 결정적이고
(build_probe_pod_name) submit_preflight 가 멱등(AlreadyExists 관용)이라,
"없음→생성"과 "진행 중→대기"가 상태 저장 없이 매 틱 재호출 한 번으로 접힌다."""
import logging

from .build_runner import BUILD_REF_PREFIX
from .db import iso_plus, utc_now_iso
from .execution import ExecStatus, ExecutionError
from .repositories.builds import build_pod_name, build_probe_pod_name

logger = logging.getLogger(__name__)

_MARKER = "DMS_COMMIT_SHA="
_PF_MARKER = "DMS_PREFLIGHT_REASON="
_PF_OK = "DMS_PREFLIGHT_OK"
# 프로브 로그는 신뢰 입력이 아니다(설계 §4) -- 마커가 이 화이트리스트 밖이면
# 코드를 지어내지 않고 build_preflight_failed 로 접는다. 네 코드는 프로브 스크립트
# (build_manifests._PROBE_SCRIPT)·frontend/src/lib/reasonCodes.json 과 같은 철자다.
_PF_REASONS = frozenset({"build_node_no_egress", "build_registry_unreachable",
                         "build_node_disk_low", "build_source_unavailable"})
_TERMINAL = (ExecStatus.SUCCEEDED, ExecStatus.FAILED, ExecStatus.TIMED_OUT)


def _marker_value(log_text, marker):
    """로그에서 marker= 한 줄의 값만 뽑는다 -- 로그 형식(buildah/파이썬 출력)은
    언제든 바뀌므로 형식을 파싱하지 않고 마커 관례 하나만 본다."""
    if not log_text:
        return None
    for line in log_text.split("\n"):
        line = line.strip()
        if line.startswith(marker):
            value = line[len(marker):].strip()
            if value:
                return value
    return None


def parse_commit_sha(log_text):
    """빌드 스크립트가 찍는 DMS_COMMIT_SHA= 마커에서 커밋을 뽑는다."""
    return _marker_value(log_text, _MARKER)


def parse_preflight_reason(log_text):
    """프로브가 찍는 DMS_PREFLIGHT_REASON= 마커에서 사유 코드 후보를 뽑는다 --
    실행 preflight(execution_manifests._preflight_script)와 같은 마커 관례라
    운영자가 한 가지 문법만 알면 된다. 화이트리스트 판정은 호출자(run_once)가
    한다 -- 파서는 정책을 모른다."""
    return _marker_value(log_text, _PF_MARKER)


class BuildWatcher:
    def __init__(self, repos, runner, *, timeout_seconds=None,
                 preflight_timeout_seconds=None):
        self._repos = repos
        self._runner = runner
        # None이면 나이 기반 회수를 하지 않는다(기존 호출자와의 하위호환 기본값) --
        # 실제 배선(controller.py)은 항상 settings 값 둘 다 넘긴다.
        self._timeout_seconds = timeout_seconds
        self._preflight_timeout_seconds = preflight_timeout_seconds

    def _ref(self, build):
        return f"{BUILD_REF_PREFIX}/{build_pod_name(build['build_id'])}"

    def _pf_ref(self, build):
        return f"{BUILD_REF_PREFIX}/{build_probe_pod_name(build['build_id'])}"

    def _reclaim(self, build_id, ref, *, reason_code):
        """로그 박제 -> 파드 삭제 -> Failed 박제. 타임아웃 계열 회수의 공통 순서:
        terminate 를 먼저 하면 유일한 증거(파드 로그)가 함께 사라진다."""
        try:
            log_text = self._runner.read_log(ref)
        except ExecutionError:
            # 로그 조회 실패로 회수 자체를 막으면 다시 갇힌다 -- None 은 finish 의
            # COALESCE 라 기존 log_text 를 지우지 않는다.
            log_text = None
        try:
            self._runner.terminate(ref)
        except ExecutionError:
            pass  # best-effort -- 타임아웃 판정 자체는 지켜야 한다
        self._repos.builds.finish(build_id, state="Failed",
                                  reason_code=reason_code, log_text=log_text)

    def run_once(self, *, now_iso=None) -> dict:
        submitted = finished = 0
        now = now_iso or utc_now_iso()
        pf_cutoff = (iso_plus(now, -self._preflight_timeout_seconds)
                     if self._preflight_timeout_seconds is not None else None)
        for build in self._repos.builds.pending():
            build_id = build["build_id"]
            pf_ref = self._pf_ref(build)
            # 회수 판정을 프로브 생성보다 먼저 -- 회수 틱에 새 프로브를 만들지
            # 않는다. 프로브가 스케줄조차 안 되는 경우(노드 다운)의 유일한
            # 탈출구다: 프로브의 activeDeadlineSeconds 는 스케줄 후에만 발화한다.
            if pf_cutoff is not None and build["created_at"] < pf_cutoff:
                self._reclaim(build_id, pf_ref,
                              reason_code="build_preflight_timeout")
                finished += 1
                continue
            try:
                # 매 틱 멱등 제출(AlreadyExists 관용 -- BuildRunner.submit 선례):
                # "없음→생성"과 "진행 중→대기"가 상태 저장 없이 한 호출로 접힌다.
                self._runner.submit_preflight(build)
            except ExecutionError as exc:
                # 프로브 생성 실패(k8s API 오류)는 기존 submit_failed 재사용(§4) --
                # detail 의 "preflight:" 접두가 빌드 파드 제출 실패와 구분한다.
                self._repos.builds.finish(build_id, state="Failed",
                                          reason_code=exc.reason_code)
                finished += 1
                continue
            try:
                status = self._runner.poll(pf_ref)
            except ExecutionError as exc:
                # I6 관용구: 일시 오류 하나로 Failed 못박지 않는다 -- 다음 틱이
                # 재시도하고, 영구 오류는 위 프리플라이트 타임아웃이 회수한다.
                logger.warning("build preflight poll error build_id=%s: %s",
                               build_id, exc)
                continue
            if status not in _TERMINAL:
                continue
            try:
                log_text = self._runner.read_log(pf_ref)
            except ExecutionError:
                log_text = None
            if status == ExecStatus.SUCCEEDED:
                if log_text is None or _PF_OK not in log_text:
                    # 종료는 성공인데 OK 마커를 아직 못 읽었다(로그 일시 결손) --
                    # 실패를 지어내지 않고 다음 틱 재시도. 최후 회수는 타임아웃.
                    continue
                try:
                    self._runner.submit(build)
                except ExecutionError as exc:
                    self._repos.builds.finish(build_id, state="Failed",
                                              reason_code=exc.reason_code)
                    finished += 1
                    continue
                self._repos.builds.mark_running(build_id)
                submitted += 1
                continue
            # 프로브 실패: 마커를 화이트리스트로만 채택(§2.5) -- 로그 전체를
            # 박제해 실패 호스트/실측 바이트가 /log 로 보이게 한다(64KB 꼬리
            # 규칙은 finish 가 적용).
            reason = parse_preflight_reason(log_text)
            if reason not in _PF_REASONS:
                reason = "build_preflight_failed"
            self._repos.builds.finish(build_id, state="Failed",
                                      reason_code=reason, log_text=log_text)
            finished += 1

        cutoff = (iso_plus(now, -self._timeout_seconds)
                 if self._timeout_seconds is not None else None)
        for build in self._repos.builds.running():
            build_id = build["build_id"]
            ref = self._ref(build)
            # I6: 빌드별로 예외를 격리한다(stepper.py와 같은 관용구) -- poll의 일시적
            # 오류(예: apiserver 재시작으로 상태 조회 1회 실패) 하나가 즉시 Failed로
            # 못박히면 파드는 계속 돌아 이미지를 push하는데 포탈은 실패를 보여주는
            # 불일치가 생긴다. 로그만 남기고 상태를 그대로 두면 다음 틱이 재시도한다.
            # C2(b)의 나이 기반 회수(아래 cutoff 체크)가 없으면 영구 오류 시 Running에
            # 갇히므로 반드시 이 둘이 함께 있어야 한다.
            try:
                if cutoff is not None and build["created_at"] < cutoff:
                    # 회수 전에 딱 한 번 poll: PENDING 이면 "스케줄되지 못한 채
                    # 마감"(§2.1 build_stuck_pending) -- activeDeadlineSeconds 는
                    # 스케줄 후에만 발화하므로 이 구분은 여기서만 가능하다.
                    # poll 실패는 회수를 막지 않고 generic 코드로 폴백한다.
                    try:
                        stuck = self._runner.poll(ref) == ExecStatus.PENDING
                    except ExecutionError:
                        stuck = False
                    self._reclaim(build_id, ref,
                                  reason_code="build_stuck_pending" if stuck
                                  else "build_timeout")
                    finished += 1
                    continue
                status = self._runner.poll(ref)
                if status not in _TERMINAL:
                    continue
                log_text = self._runner.read_log(ref)
                self._repos.builds.finish(
                    build_id,
                    state="Succeeded" if status == ExecStatus.SUCCEEDED else "Failed",
                    # 실패면 파드 상태로 사유를 구분한다(OOMKilled/Evicted) -- 재료가
                    # 없으면 runner 가 build_failed 를 그대로 돌려준다.
                    reason_code=(None if status == ExecStatus.SUCCEEDED
                                 else self._runner.failure_reason(ref)),
                    commit_sha=parse_commit_sha(log_text),
                    log_text=log_text)
                finished += 1
            except Exception as exc:
                logger.warning("build watcher error build_id=%s: %s: %s",
                               build_id, type(exc).__name__, exc)
        return {"submitted": submitted, "finished": finished}
