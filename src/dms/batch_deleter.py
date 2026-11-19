"""컨트롤러 batch-delete 루프 -- 배치 단위 백그라운드 삭제(2026-11-19 사용자 결정 「배치 단위 삭제 상한 기본 50000개로」).

API 는 작업 수가 동기 문턱(DMS_BATCH_DELETE_SYNC_MAX_CHILDREN, 기본 500)을 넘는 배치를 한 트랜잭션으로 지우지 않는다(5만이면 API
단일 커넥션 RLock 을 테스트베드 환산 36–43초 쥐어 readiness 를 연속으로 놓친다). 대신 짧은 묘비 트랜잭션
(RequestPurgesRepository._begin_batch_delete -- 배치 상태 Deleting + batch_deletions 행)만 세우고, 이 루프가 나머지를 한다:

  delete_batch_chunk(자식 ≤ request_purges.DELETE_CHUNK = 한 트랜잭션 -- 잠근 행으로 전부 재판정, _purge_locked 로 PURGED_TABLES
  삭제 + 정리 아웃박스(purge_group = batch_id 레인), 청크 요약 감사 1행) → 자식이 0 이면 finish_batch_delete(항목·배치·묘비·꼬리
  DELETE + ('batch','delete') 배치 스냅숏). 결과 파일·파드는 request-purge 루프가 지운다(이 루프는 DB 만 쓴다 -- 제어면 FS 삭제
  코드 0건, tests/test_control_plane_static.py 가 이 모듈도 본다).

규약:
  - **조건 없이 조립된다**(controller.build_loops -- purge_runner 와 무관): 사람이 시작한 삭제라 반드시 끝나야 한다(API 의 단건·동기
    삭제가 purge_runner 와 무관하게 아웃박스를 넣는 것과 같은 판단).
  - 유지보수·drain 이면 쉰다(회차 보존 정리와 같은 규칙).
  - 틱 예산 TICK_BUDGET_SECONDS(2초) -- 컨트롤러는 루프를 한 프로세스·한 커넥션에서 차례로 돌린다(planner·stepper 를 2초 넘게
    밀지 않는다). 진행 중인 삭제마다 청크 하나씩 돌아가며(큰 삭제 하나가 틱을 독차지하지 않게 -- _NEXT_AFTER 커서), 예산까지
    다시 처음부터.
  - 진행 상태(커서·진행 수·청크 수·막힘 사유·백오프)는 batch_deletions 행에 영속한다 -- 컨트롤러가 죽거나 리스가 넘어가도 새
    소유자가 이어서 끝낸다(모듈 메모리는 돌림차례 커서뿐 -- 잃어도 무해).
  - 막힘(끝까지 훑었는데 남은 자식을 지울 수 없음)·예외는 defer_batch_delete(백오프 15·30·…·900초, 「삭제 지연」으로 보인다) +
    사유가 바뀔 때만 이벤트(batch_delete_blocked·batch_delete_error). 포기는 없다(묘비는 되돌릴 수 없다 -- 막힌 자식을 취소하거나
    조용한 창이 지나면 저절로 재개된다).
  - 이벤트는 request_id=NULL(지운 id 로 쓰면 고아 -- request_purger 규약).
"""
import sys
import time

from .artifact_base import resolve_artifact_base, strip_scheme

COMPONENT = "batch-delete"
# 루프 간격(초). 설정 키 아님(batch-round-prune·artifact-expiry 와 같은 판단). 리스 max(5*3, 30) = 30초.
INTERVAL_SECONDS = 5
# 한 틱이 청크에 쓰는 시간 상한(초) -- 청크 하나는 테스트베드 환산 ~45ms 라 5만 배치는 ~12틱(벽시계 1–2분).
TICK_BUDGET_SECONDS = 2.0
# 돌림차례 커서(마지막으로 다룬 batch_id) -- 프로세스 메모리(틱마다 새 인스턴스, batch_round_prune._NEXT_AFTER 관례).
_NEXT_AFTER: "list[str | None]" = [None]


def _rotate(rows, after):
    if after is None:
        return list(rows)
    head = [r for r in rows if r["batch_id"] > after]
    return head + [r for r in rows if r["batch_id"] <= after]


class BatchDeleter:
    def __init__(self, repos, *, settings, clock=None, budget_seconds=TICK_BUDGET_SECONDS):
        self._repos = repos
        self._settings = settings
        self._clock = clock or time.monotonic       # 시험용(틱 예산)
        self._budget = budget_seconds

    def run_once(self) -> dict:
        control = self._repos.control.control_state()
        if control and (control.get("maintenance") or control.get("drain")):
            return {"held": True}
        purges = self._repos.request_purges
        rows = sorted(purges.batch_deletions_due(), key=lambda r: r["batch_id"])
        if not rows:
            return {"batches": 0, "chunks": 0, "deleted": 0, "finished": 0}
        rows = _rotate(rows, _NEXT_AFTER[0])
        base = strip_scheme(resolve_artifact_base(self._repos.control, self._settings) or "") or None
        quiet = self._settings.request_delete_quiet_seconds
        start = self._clock()
        live = [r["batch_id"] for r in rows]
        chunks = deleted = finished = 0
        while live and self._clock() - start < self._budget:
            for bid in list(live):
                if self._clock() - start >= self._budget:
                    break
                _NEXT_AFTER[0] = bid
                try:
                    res = purges.delete_batch_chunk(bid, artifact_base=base, quiet_seconds=quiet)
                    if res.get("done"):
                        fin = purges.finish_batch_delete(bid)
                        live.remove(bid)
                        if fin.get("done"):
                            finished += 1
                            self._event("info", "batch_delete_done",
                                        f"batch {bid}: background batch delete finished "
                                        f"({fin.get('requests')} request(s), {fin.get('chunks')} chunk(s))",
                                        {"batch_id": bid, "requests": fin.get("requests"), "chunks": fin.get("chunks")})
                        elif fin.get("blocked"):
                            self._defer(bid, fin["blocked"], fin.get("request_id"))
                        continue
                    if res.get("blocked"):
                        live.remove(bid)
                        self._defer(bid, res["blocked"], res.get("request_id"))
                        continue
                    if res.get("gone") or res.get("lost"):
                        live.remove(bid)          # 끝났거나(다른 소유자) 경합 -- 다음 틱
                        continue
                    n = len(res.get("deleted") or [])
                    deleted += n
                    if n:
                        chunks += 1
                    elif res.get("stuck"):
                        # 고른 자식을 잠근 행 재판정이 전부 막았다 -- 같은 자식을 매 틱 다시 고르지 않게 백오프(사유는 묘비 행에).
                        live.remove(bid)
                        self._defer(bid, res["stuck"][0], res["stuck"][1])
                    elif res.get("skipped") or res.get("scan_next") is None:
                        # 고른 것이 전부 막혔거나(묘비 행에 사유가 남았다) 고르기 뒤 풀린 경합 -- 이 틱엔 더 없음, 다음 틱이 이어서.
                        # (끝까지 훑어 고를 것이 없으면 저장소가 blocked 를 돌려준다 -- 위 갈래, 커서부터 훑은 바퀴 끝도 같다.)
                        # (훑기 예산만 소진한 빈 청크 -- scan_next 있음·skipped 없음 -- 는 같은 틱에 계속 훑는다.)
                        live.remove(bid)
                except Exception as exc:          # 한 배치가 틱 전체를 막지 않는다 -- 백오프 동안 빠진다
                    live.remove(bid)
                    print(f"batch-delete: {bid} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
                    changed = self._defer(bid, "batch_delete_failed", None, warn=False)
                    if changed:
                        self._event("error", "batch_delete_error", f"batch {bid}: {type(exc).__name__}: {exc}",
                                    {"batch_id": bid})
        return {"batches": len(rows), "chunks": chunks, "deleted": deleted, "finished": finished}

    def _defer(self, bid, reason, request_id, *, warn=True) -> bool:
        try:
            changed = self._repos.request_purges.defer_batch_delete(bid, reason_code=reason, request_id=request_id)
        except Exception as exc:
            print(f"batch-delete: defer failed for {bid}: {type(exc).__name__}: {exc}", file=sys.stderr)
            return False
        if changed and warn:
            self._event("warning", "batch_delete_blocked",
                        f"batch {bid}: background batch delete is waiting ({reason})"
                        + (f" -- request {request_id}" if request_id else ""),
                        {"batch_id": bid, "reason_code": reason, "blocked_request_id": request_id})
        return changed

    def _event(self, severity, event_type, message, payload) -> None:
        try:
            self._repos.observability.record_event(
                component=COMPONENT, severity=severity, event_type=event_type, message=message[:500],
                payload=payload, request_id=None)
        except Exception as exc:                  # record_event 는 원래 던지지 않는다 -- 이중 방어(삭제는 이미 커밋됐다)
            print(f"batch-delete event failed: {type(exc).__name__}: {exc}", file=sys.stderr)
