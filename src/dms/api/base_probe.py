"""요청 단위 base 가용성 프로브(2026-11-23, 설계 v2 §D).

아티팩트를 여는 라우트가 「그 잡의 아티팩트 없음(404)」과 「base 접근 불가(503 artifact_base_unavailable)」를 가르는 데
쓴다. 판정은 **base 수준**뿐이다(artifact_base.availability_check -- 디렉터리 열기·첫 dirent·최근 잡 디렉터리) -- 파일
open 의 errno 로 가르면 요청자가 자기 <phase> 를 chmod 000·심링크로 바꿔 응답을 고르는 존재 오라클이 된다
(artifact_files 모듈 docstring). 같은 base 의 모든 잡에 같은 답이고, 실패 경로에서만 부른다(성공 경로 비용 0).

- problem(): 첫 호출 때 한 번만 판정(메모). runner(프로세스 단일 single-flight·짧은 캐시)를 모니터·관리 GET 과 공유한다.
- note_success(): 같은 요청에서 읽기가 하나라도 성공했으면 down 이 아니다(base 는 보였다 -- 진짜 404).
- hung(): FS 를 **열기 전에** 본다 -- 같은 키의 점검이 마감을 넘겨 멈춰 있거나(runner), 모니터의 신선한 현재 uri 스냅숏이
  타임아웃이고 그 점검이 아직 끝나지 않았으면 True. 멈춘 마운트에 요청 스레드가 하나씩 매달려 스레드풀(→ /readyz)까지
  굶는 것을 막는다.
- halted / unread: 집계 라우트(routes_usage)가 down 을 본 뒤 남은 리포트를 열지 않고 멈췄다는 표시와, 그 요청에서 못 읽은
  잡 id 들(응답의 unreadable_reason 을 붙일 행).
"""
from ..artifact_base import strip_scheme
from .base_monitor import api_check_key, run_api_check

_UNSET = object()


class BaseProbe:
    def __init__(self, state, uri: str):
        self._state = state
        self.uri = uri
        self.path = strip_scheme(uri)
        self._problem = _UNSET
        self._hung = None
        self._success = False
        self.halted = False
        self.unread: set = set()

    def hung(self) -> bool:
        if self._hung is None:
            self._hung = _is_hung(self._state, self.uri, self.path)
        return self._hung

    def problem(self) -> "str | None":
        if self._problem is _UNSET:
            if self.hung():
                self._problem = "artifact_base_check_timeout"
            else:
                avail = run_api_check(self._state, self.uri)["availability"]
                self._problem = None if avail["ok"] is True else avail["reason"]
        return self._problem

    def note_success(self) -> None:
        self._success = True

    @property
    def down(self) -> bool:
        return not self._success and self.problem() is not None


def _is_hung(state, uri: str, path: str) -> bool:
    runner = getattr(state, "base_check_runner", None)
    if runner is None:
        return False
    key = api_check_key(path)
    if runner.stuck_since(key) is not None:
        return True
    monitor = getattr(state, "base_monitor", None)
    # 모니터가 방금 타임아웃을 봤어도 그 점검이 이미 끝났으면(마운트 회복) 단락하지 않는다 -- 다음 회차까지 거짓 503 금지.
    return monitor is not None and monitor.reports_timeout(uri) and runner.busy(key)
