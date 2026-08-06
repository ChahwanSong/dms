"""빌드 상태를 파드에서 DB로 옮기는 컨트롤러 루프.

루프 안의 예외는 controller.run_all_once 가 삼켜 stderr 로만 내보낸다 --
그래서 실패는 예외로 새지 않고 반드시 builds.state 로 드러나야 한다."""
import logging

from .build_runner import BUILD_REF_PREFIX
from .db import iso_plus, utc_now_iso
from .execution import ExecStatus, ExecutionError
from .repositories.builds import build_pod_name

logger = logging.getLogger(__name__)

_MARKER = "DMS_COMMIT_SHA="
_TERMINAL = (ExecStatus.SUCCEEDED, ExecStatus.FAILED, ExecStatus.TIMED_OUT)


def parse_commit_sha(log_text):
    """빌드 스크립트가 찍는 DMS_COMMIT_SHA= 마커에서 커밋을 뽑는다.
    로그 형식을 파싱하지 않고 마커 한 줄만 본다 -- buildah 출력은 언제든 바뀐다."""
    if not log_text:
        return None
    for line in log_text.split("\n"):
        line = line.strip()
        if line.startswith(_MARKER):
            value = line[len(_MARKER):].strip()
            if value:
                return value
    return None


class BuildWatcher:
    def __init__(self, repos, runner, *, timeout_seconds=None):
        self._repos = repos
        self._runner = runner
        # None이면 나이 기반 회수를 하지 않는다(기존 호출자와의 하위호환 기본값) --
        # 실제 배선(controller.py)은 항상 settings.build_timeout_seconds를 넘긴다.
        self._timeout_seconds = timeout_seconds

    def _ref(self, build):
        return f"{BUILD_REF_PREFIX}/{build_pod_name(build['build_id'])}"

    def run_once(self, *, now_iso=None) -> dict:
        submitted = finished = 0
        for build in self._repos.builds.pending():
            try:
                self._runner.submit(build)
            except ExecutionError as exc:
                self._repos.builds.finish(build["build_id"], state="Failed",
                                          reason_code=exc.reason_code)
                continue
            self._repos.builds.mark_running(build["build_id"])
            submitted += 1

        now = now_iso or utc_now_iso()
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
                    try:
                        self._runner.terminate(ref)
                    except ExecutionError:
                        pass  # best-effort -- 타임아웃 판정 자체는 지켜야 한다
                    self._repos.builds.finish(build_id, state="Failed",
                                              reason_code="build_timeout")
                    finished += 1
                    continue
                status = self._runner.poll(ref)
                if status not in _TERMINAL:
                    continue
                log_text = self._runner.read_log(ref)
                self._repos.builds.finish(
                    build_id,
                    state="Succeeded" if status == ExecStatus.SUCCEEDED else "Failed",
                    reason_code=None if status == ExecStatus.SUCCEEDED else "build_failed",
                    commit_sha=parse_commit_sha(log_text),
                    log_text=log_text)
                finished += 1
            except Exception as exc:
                logger.warning("build watcher error build_id=%s: %s: %s",
                               build_id, type(exc).__name__, exc)
        return {"submitted": submitted, "finished": finished}
