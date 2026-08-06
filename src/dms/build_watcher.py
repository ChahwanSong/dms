"""빌드 상태를 파드에서 DB로 옮기는 컨트롤러 루프.

루프 안의 예외는 controller.run_all_once 가 삼켜 stderr 로만 내보낸다 --
그래서 실패는 예외로 새지 않고 반드시 builds.state 로 드러나야 한다."""
import logging

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
    def __init__(self, repos, runner):
        self._repos = repos
        self._runner = runner

    def _ref(self, build):
        return f"buildpod/{build_pod_name(build['build_id'])}"

    def run_once(self) -> dict:
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

        for build in self._repos.builds.running():
            ref = self._ref(build)
            try:
                status = self._runner.poll(ref)
            except ExecutionError as exc:
                self._repos.builds.finish(build["build_id"], state="Failed",
                                          reason_code=exc.reason_code)
                finished += 1
                continue
            if status not in _TERMINAL:
                continue
            log_text = self._runner.read_log(ref)
            self._repos.builds.finish(
                build["build_id"],
                state="Succeeded" if status == ExecStatus.SUCCEEDED else "Failed",
                reason_code=None if status == ExecStatus.SUCCEEDED else "build_failed",
                commit_sha=parse_commit_sha(log_text),
                log_text=log_text)
            finished += 1
        return {"submitted": submitted, "finished": finished}
