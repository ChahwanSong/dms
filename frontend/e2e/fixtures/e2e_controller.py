"""e2e 전용 컨트롤러 진입점(2026-10-21, E12 배치 rm) -- `dms controller` 와 같은 배선에 실행 어댑터 하나만 바꾼다.

왜: 하네스의 실행 어댑터는 결정적 stub(execution.StubExecutionAdapter)이라 잡이 「성공」해도 파일은 그대로다. 배치 rm 은
「확인한 경로가 실제로 지워지고, 확인하지 않은 동안엔 아무것도 지워지지 않는다」가 계약의 전부라 stub 으로는 그 절반을
못 본다. 그래서 drm 잡만 이 머신에서 **가짜 drm**(fixtures/bin/drm -- 하네스 tmp 스토리지 안만 지운다)을 실제로 돌린다:
argv 는 실 빌더(execution_manifests.tool_argv -- 미리보기 --dryrun, 옵션 플래그, stepper 가 managed_root 로 조합한 절대경로)
그대로, summary 는 러너와 같은 함수(dms_job_runner.runner._build_summary -- 2026-10-27 d192 summary v2: 핵심 3키 + 상세 키,
미리보기 files = walk 수, 실행 = Removed 수)로 만든다 -- 포탈이 보는 모양이 실 잡과 같다.
scan·sync·preflight 는 종전 stub 그대로라 다른 시나리오의 동작은 바뀌지 않는다.

봉쇄(테스트 도구도 지우는 도구다): 대상의 realpath 가 DMS_E2E_RM_ROOT(하네스가 tmp 스토리지로 준다) **아래**가 아니면
돌리지 않고 실패로 둔다. 가짜 drm 도 같은 검사를 한 번 더 한다. 운영 코드는 이 파일을 모른다(프로덕션 배선 무변경).
"""
import os
import subprocess
import sys

from dms import cli, wiring
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.execution_manifests import tool_argv
from dms_job_runner.runner import _build_summary

FAKE_DRM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin", "drm")


class LocalDrmStubAdapter(StubExecutionAdapter):
    def __init__(self, root: str):
        super().__init__()
        self._root_raw = root                      # stepper 가 조합한 경로와 같은 표기(가짜 drm 의 접두 검사용)
        self._root = os.path.realpath(root)        # 봉쇄 판정은 실제 경로로

    def submit(self, spec):
        ref = super().submit(spec)
        if spec.tool == "drm" and spec.phase in ("preview", "execution"):
            self._run_drm(ref, spec)
        return ref

    def _inside(self, path) -> bool:
        if not isinstance(path, str) or path == "":
            return False
        real = os.path.realpath(path)
        return real != self._root and real.startswith(self._root + os.sep)

    def _run_drm(self, ref, spec):
        target = (spec.paths or {}).get("target")
        if not self._inside(target):
            print(f"e2e drm: refuse {target!r} -- outside {self._root}", file=sys.stderr, flush=True)
            self.set_summary(ref, {"returncode": 2, "files": None, "bytes": None})
            self.script(ref, [ExecStatus.FAILED])
            return
        argv = [FAKE_DRM, *tool_argv(spec, abs_paths=spec.paths)]
        proc = subprocess.run(argv, capture_output=True, text=True, errors="replace", timeout=60,
                              env={**os.environ, "DMS_E2E_RM_ROOT": self._root_raw})
        tool_args = argv[1:]
        summary = _build_summary("drm", proc.stdout, proc.returncode, "", dryrun="--dryrun" in tool_args,
                                 quiet=("--quiet" in tool_args or "-q" in tool_args), stderr=proc.stderr)
        print(f"e2e drm: phase={spec.phase} rc={proc.returncode} files={summary.get('files')} argv={tool_args}",
              file=sys.stderr, flush=True)
        self.set_summary(ref, summary)
        if proc.returncode != 0:
            # 실 러너처럼 사유 마커를 launcher 로그에 남긴다(d192 -- 스테퍼가 이 줄을 rm_items_failed 등으로 승격한다).
            reason = summary.get("reason")
            if reason is not None:
                self.set_log(ref, [(ref, f"DMS_EXEC_REASON={reason}\n", None)])
            self.script(ref, [ExecStatus.FAILED])


def main() -> int:
    root = os.environ.get("DMS_E2E_RM_ROOT", "")
    if root in ("", "/"):
        print("DMS_E2E_RM_ROOT 가 없다 -- e2e 컨트롤러는 하네스 tmp 스토리지 밖을 지울 수 없게 시작을 거부한다",
              file=sys.stderr)
        return 2
    # cli.main 은 controller 분기에서 wiring 의 함수를 **호출 시점에** import 한다 -- 모듈 속성을 바꾸면 그대로 탄다.
    wiring.build_execution_adapter = lambda settings, repos: LocalDrmStubAdapter(root)
    return cli.main(["controller", *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
