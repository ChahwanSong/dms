"""컨트롤러 batch-round-prune 루프 -- 배치 스케줄 회차 보존 정리(2026-11-16 사용자 결정 「배치 회차가 계속 누적되는걸 원하지 않아
최근 10회차만 누적되도록」 -- dms-k8s 라인의 같은 기능(최근 5회차)을 이 라인으로 옮겼다, 기본값만 10).

스케줄이 있는 배치(batch_schedules 행 -- 켜짐·꺼짐·일시정지 무관; 스케줄을 지우면 정리도 멈춘다)는 최근
DMS_BATCH_SCHEDULE_KEEP_ROUNDS(기본 10)회차의 자식 요청만 남긴다. 회차 = BatchesRepository._rescan_locked 한 번(사람 :rescan ·
스케줄 회차 -- 실패분·선택 재실행·항목 추가는 그 회차에 속한다)이고, 회차 경계(batch_rounds -- 시작 순간 그 배치 자식의
MAX(commit_order))는 그 몸통만 남긴다(정의·정확성 전제는 _rescan_locked docstring). 기록을 시작하기 전의 자식(첫 실행과 배포 전
회차 전부)은 가장 오래된 회차 하나로 보고, 기록된 회차가 K개가 되는 순간에만 한꺼번에 정리한다(배포 직후 대량 삭제는 구조적으로
없다). 최신 회차의 경계 자식(닻)은 절대 지우지 않는다(commit_order 재사용 방지 -- BatchSchedulesRepository 의 _cut_from).

이 루프는 **고르고 요청 삭제 몸통에 맡기기만** 한다: BatchSchedulesRepository.prune_targets(경계) →
RequestPurgesRepository.prune_round_children(한 청크 = 한 트랜잭션 -- _purge_locked 로 PURGED_TABLES 삭제 + 정리 아웃박스, 감사는
청크마다 ('batch','prune_rounds') 1행) → 아티팩트 `<base>/<job_id>`·남은 Pod 는 request-purge 루프가 지운다(ARCHITECTURE §7 규칙
13 -- 제어면이 FS 에서 지우는 코드는 여전히 0건, tests/test_control_plane_static.py 가 이 모듈도 본다). 그래서 purge_runner 가
배선된 컨트롤러에서만 조립된다(controller.build_loops -- 정리 루프 없이 아웃박스만 쌓지 않는다).

규약:
  - 절대 지우지 않는 것: 비종단 요청, 비종단 잡이 있는 요청, 조용한 창(DMS_REQUEST_DELETE_QUIET_SECONDS) 안의 요청, 어느
    batch_items 든 가리키는 요청, 보존 회차·지금 회차의 자식, 닻. 판정은 청크 트랜잭션이 잠근 행으로 다시 한다.
  - 유지보수·drain 중에는 쉰다. 확인 게이트·confirm 은 건드리지 않는다(지우기만 한다).
  - 대기열 상한(BACKLOG_MAX): 이 루프가 넣은 정리 대기 행(requested_by = system:batch-scheduler)이 상한 이상이면 그 틱은 쉰다 --
    사람이 방금 지운 작업의 정리가 수천 행 뒤에 서지 않게. 처리량은 아웃박스 처리량에 묶인다.
  - 공평 순환: 배치마다 청크 하나씩 돌아가며(큰 배치 하나가 방을 독차지하지 않게), 틱당 실제 청크(고른 것이 있는 청크)는
    MAX_CHUNKS_PER_TICK 이하. 고른 것이 없는 확인(정상 상태 -- 인덱스 1회)은 세지 않는다(스케줄이 많아도 매 틱 전부를 본다).
  - 고르기 커서(_SCAN_FROM): 청크의 고르기는 경계 이하 자식을 창 단위로 호출당 request_purges.PRUNE_PICK_SCAN_BUDGET 행까지만
    훑는다(청크 비용을 대기량이 아니라 창·예산에 묶는다 -- RequestPurgesRepository._prune_pick). 다 못 훑으면 돌려준 scan_next 를
    배치마다 들고 다음 청크를 그 뒤부터 시작하고, 경계 끝에 닿으면(None) 지운다 -- 건너뛴 막힌 자식은 다음 바퀴에 처음부터 다시
    본다. 프로세스 메모리라 리스가 넘어가면 처음부터(무해).
  - 배치마다 예외를 격리하고 백오프한다(오류 이벤트는 백오프마다 1회). 고를 수 있는 것이 0 인데 경계 이하 자식이 남으면
    (비종단 잔재·항목 참조) batch_round_prune_blocked 경고를 배치마다 1시간에 1회.
  - 잠금 순서(청크): requests(request_id 순) → data_jobs → batch_schedules(맨 끝). 회차 경계는 잠그지 않고 읽는다.
"""
import sys
import time

from .artifact_base import resolve_artifact_base, strip_scheme
from .batch_scheduler import SCHEDULER_ACTOR

COMPONENT = "batch-round-prune"
# 루프 간격(초). 설정 키 아님(운영자가 따로 튜닝할 값이 아니다). 리스 max(30*3, 30) = 90초. 사람 :rescan 회차도 이 간격 안에
# 정리된다.
INTERVAL_SECONDS = 30
# 정리 트랜잭션 하나의 자식 상한 -- 감사 요약 1행이 32 KiB 안에 자식별 기록까지 담는 크기(~25 KB).
CHUNK = 100
# 이 루프가 넣은 정리 대기 행 상한 -- 사람이 지운 작업의 정리는 최대 ~100행 뒤에 선다.
BACKLOG_MAX = 100
MAX_CHUNKS_PER_TICK = 10
_ERROR_BACKOFF_SECONDS = 300
_BLOCKED_WARN_SECONDS = 3600
# 모듈 수준 상태(틱마다 새 인스턴스 -- batch_scheduler 의 백오프와 같은 관례). 프로세스 메모리라 리스가 넘어가면 처음부터 --
# 무해하다(판정은 청크 트랜잭션의 잠근 행과 CAS 다).
_BACKOFF: "dict[str, float]" = {}          # batch_id → 다시 볼 epoch
_NEXT_AFTER: "list[str | None]" = [None]    # 공평 순환 커서(마지막으로 다룬 batch_id)
_BLOCKED_WARNED: "dict[str, float]" = {}   # batch_id → 막힘 경고를 남긴 epoch
_SCAN_FROM: "dict[str, int]" = {}          # batch_id → 고르기 커서(commit_order -- 다음 청크는 이 번호 초과부터 훑는다)


def _rotate(targets, after):
    """batch_id 순 목록을 커서 다음부터 돌린다(커서가 없거나 끝이면 처음부터)."""
    if after is None:
        return list(targets)
    head = [t for t in targets if t["batch_id"] > after]
    return head + [t for t in targets if t["batch_id"] <= after]


class BatchRoundPruner:
    def __init__(self, repos, *, settings, clock=None):
        self._repos = repos
        self._settings = settings
        self._clock = clock or time.time      # 시험용(백오프·경고 창)

    def run_once(self) -> dict:
        keep = getattr(self._settings, "batch_schedule_keep_rounds", None)
        if not (isinstance(keep, int) and not isinstance(keep, bool) and 1 <= keep <= 100):
            return {"disabled": True}         # Settings.from_env 가 이미 막는다 -- 변조·직접 생성 대비(fail-closed)
        control = self._repos.control.control_state()
        if control and (control.get("maintenance") or control.get("drain")):
            return {"held": True}
        purges = self._repos.request_purges
        backlog = purges.pending_requested_by(SCHEDULER_ACTOR)
        room = BACKLOG_MAX - backlog
        if room <= 0:
            return {"backlog": backlog, "deleted": 0}
        now = self._clock()
        for bid in [b for b, until in _BACKOFF.items() if until <= now]:
            _BACKOFF.pop(bid, None)
        targets = [t for t in self._repos.batch_schedules.prune_targets(keep=keep) if t["batch_id"] not in _BACKOFF]
        live = {t["batch_id"] for t in targets}
        for bid in [b for b in _SCAN_FROM if b not in live]:
            _SCAN_FROM.pop(bid, None)         # 스케줄·배치가 사라졌거나 백오프 중 -- 다음에 처음부터
        targets = _rotate(targets, _NEXT_AFTER[0])
        # 삭제 시점 base -- 작업 삭제 라우트와 같은 모양(None = 모름: 정리 루프가 파일 단계를 보류한다, 빈 문자열을 넘기지 않는다).
        base = strip_scheme(resolve_artifact_base(self._repos.control, self._settings) or "") or None
        quiet = self._settings.request_delete_quiet_seconds
        chunks, deleted, jobs, touched = 0, 0, 0, set()
        while room > 0 and chunks < MAX_CHUNKS_PER_TICK and targets:
            for t in list(targets):
                if room <= 0 or chunks >= MAX_CHUNKS_PER_TICK:
                    break
                bid = t["batch_id"]
                _NEXT_AFTER[0] = bid
                try:
                    res = purges.prune_round_children(
                        bid, upto_order=t["upto"], limit=min(CHUNK, room), actor=SCHEDULER_ACTOR,
                        artifact_base=base, quiet_seconds=quiet, scan_after=_SCAN_FROM.get(bid),
                        audit_extra={"schedule_id": t["schedule_id"], "keep_rounds": keep,
                                     "rounds_recorded": t["last_seq"], "cut_round_seq": t["cut_seq"],
                                     "upto_order": t["upto"], "anchor_order": t["anchor"]})
                except Exception as exc:      # 한 배치가 틱 전체를 막지 않는다 -- 백오프 동안 빠진다
                    _BACKOFF[bid] = now + _ERROR_BACKOFF_SECONDS
                    targets.remove(t)
                    self._event("error", "batch_round_prune_error",
                                f"batch {bid}: {type(exc).__name__}: {exc}", {"batch_id": bid})
                    continue
                if res.get("scan_next") is None:
                    _SCAN_FROM.pop(bid, None)
                else:
                    _SCAN_FROM[bid] = res["scan_next"]
                n = len(res["deleted"])
                room -= n
                deleted += n
                jobs += len(res["job_ids"])
                if n:
                    touched.add(bid)
                if res["picked"] > 0:
                    chunks += 1
                if res["picked"] < res["limit"] or n == 0:
                    targets.remove(t)         # 이 틱엔 더 없음(남은 것은 막혔거나 경합·훑기 예산 소진 -- 다음 틱이 이어서 본다)
                if res["picked"] == 0 and res.get("scan_next") is None:
                    self._maybe_warn_blocked(t, now)    # 경계 끝까지 훑었는데 고를 것이 없었다(예산 소진 중간은 아직 모른다)
        if deleted:
            self._event("info", "batch_round_prune_done",
                        f"pruned {deleted} request(s) / {jobs} job(s) of {len(touched)} scheduled batch(es) "
                        f"beyond the latest {keep} round(s)",
                        {"batches": len(touched), "requests": deleted, "jobs": jobs, "keep_rounds": keep})
        return {"backlog": backlog, "deleted": deleted, "jobs": jobs, "batches": len(touched), "chunks": chunks}

    def _maybe_warn_blocked(self, t, now) -> None:
        """고를 수 있는 것이 0 인데 경계 이하 자식이 남았다 = 비종단 잔재(취소 경합)·항목 참조 -- 처방은 그 작업을 취소하는 것.
        배치마다 1시간에 1회만(매 틱 같은 경고로 이벤트를 채우지 않는다). 조용한 창 안의 방금 끝난 자식도 여기 걸리지만 다음
        틱에 풀린다 -- 1시간 창이라 소음이 작다."""
        bid = t["batch_id"]
        last = _BLOCKED_WARNED.get(bid)
        if last is not None and now - last < _BLOCKED_WARN_SECONDS:
            return
        try:
            if not self._repos.request_purges.round_children_exist(bid, upto_order=t["upto"]):
                return
        except Exception as exc:
            print(f"batch-round-prune probe failed for {bid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        _BLOCKED_WARNED[bid] = now
        self._event("warning", "batch_round_prune_blocked",
                    f"batch {bid}: children older than the kept rounds remain but none can be pruned now "
                    "(running/unfinished jobs or item references) -- cancel the leftover jobs",
                    {"batch_id": bid, "upto_order": t["upto"], "cut_round_seq": t["cut_seq"]})

    def _event(self, severity, event_type, message, payload) -> None:
        try:
            self._repos.observability.record_event(
                component=COMPONENT, severity=severity, event_type=event_type, message=message[:500],
                payload=payload, request_id=None)
        except Exception as exc:              # record_event 는 원래 던지지 않는다 -- 이중 방어(정리는 이미 커밋됐다)
            print(f"batch-round-prune event failed: {type(exc).__name__}: {exc}", file=sys.stderr)
