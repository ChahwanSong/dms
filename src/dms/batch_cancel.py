"""배치 취소의 자식 정리 -- API 취소(routes_batches.cancel_batch)와 컨트롤러 그물(BatchOrchestrator → sweep_cancelled)이 같은
규칙을 쓴다(2026-10-16 사용자 보고: 「배치작업 Cancel 했는데 몇분 후에 k8s 을 직접 확인해보니 아직 돌아가고 있는
launcher/worker pod 들이 보여」).

원인(스크래치 시험으로 재현): 취소는 자식마다 k8s 삭제를 **한 뒤에야** 항목·배치를 Cancelled 로 썼다. 그 사이 stepper 가
지워진 vcjob 을 실패로 보고 자식이 끝나 슬롯이 비면, 배치가 아직 Running/Previewing 이라 orchestrator 가 Queued 항목에서
**새 자식**을 만들었다. 취소는 스냅숏의 Queued/Materialized 항목을 Cancelled 로 덮을 뿐이라 그 자식 요청은 Pending 으로
남았고, planner 는 배치 상태를 보지 않아 계획했다 -- stepper 가 취소 **뒤에** 새 vcjob(launcher·worker)을 만들어 끝까지
돌렸다. 그래서:
  1) 취소 라우트가 먼저 한 트랜잭션으로 Queued 항목을 막고 배치를 **Cancelling**(비종단 -- orchestrator 가 굴리지 않고 종단
     전용 경로가 받지 않는다)으로 쓴 뒤 k8s 를 끝낸다(BatchesRepository.begin_cancel). 자식이 다 끝나면 Cancelled.
  2) planner 는 Cancelling·Cancelled 배치의 Pending 자식을 계획하지 않고 끝낸다. stepper 는 그 배치 자식의 다음 단계를
     제출하지 않고 끝내며, 취소가 지우러 온(CANCEL_MARK_EVENT -- 지우기 **전에** 남긴다) 자식의 실패(지워진 vcjob·파드를
     FAILED·REJECTED 로 본 것)를 Cancelled 로 기록한다 -- 취소가 죽인 자식이 실패 수에 들어가지 않게, 취소가 닿기 전의 진짜
     실패는 그대로.
  3) 컨트롤러(orchestrator 틱)가 유예(CANCEL_SWEEP_GRACE_SECONDS) 뒤에도 Cancelling 에 머문 배치(라우트가 끝내지 못한 취소 --
     종료 실패·API 재시작)와 살아 있는 자식이 남은 Cancelled 배치를 마저 끝낸다(sweep_cancelled). 자식마다 격리한다.
"""
from .api.cancel import sweep_after_cancel, terminate_job
from .db import iso_plus, utc_now_iso
from .domain import DataJobState, RequestState, TERMINAL_DATA_JOB_STATES, TERMINAL_REQUEST_STATES
from .execution import ExecutionError

CANCEL_REASON = "cancelled_by_batch"
# 취소가 이 자식의 k8s 객체를 지우러 왔다는 증거(요청 단위 이벤트, 지우기 전에 남긴다) -- stepper 가 그 자식의 실패를 취소로
# 기록할지 가르는 유일한 근거다(모듈 docstring 2).
CANCEL_MARK_EVENT = "batch_cancel_terminate"
# 진행 중인 취소 라우트와 그물이 겹치지 않게(검증 지적: 라우트가 Cancelling 을 쓴 뒤 k8s 삭제에 몇 초를 쓰는 동안 그물이 같은
# 자식을 다시 끝내며 경고를 남겼다) -- 배치의 마지막 갱신 뒤 이만큼 지나야 그물이 손댄다.
CANCEL_SWEEP_GRACE_SECONDS = 60
# 같은 자식의 실패 이벤트는 이 간격에 한 번만(5초 틱마다 쌓이지 않게).
_EVENT_COOLDOWN_SECONDS = 600
_TERMINAL_REQ = frozenset(s.value for s in TERMINAL_REQUEST_STATES)
_KILLED_BY_CANCEL = frozenset({DataJobState.FAILED.value, DataJobState.TIMED_OUT.value,
                               DataJobState.REJECTED.value})


def mark_cancel_reached(repos, request_id, batch_id, *, component) -> None:
    """자식의 객체를 지우기 **전에** 남긴다(CANCEL_MARK_EVENT). 기록 실패는 지우기를 막지 않는다(record_event 는 예외를 올리지
    않는다) -- 그때 그 자식의 실패는 실패로 남을 뿐(정확도 손실, 거짓 취소 아님)."""
    repos.observability.record_event(component=component, severity="info", event_type=CANCEL_MARK_EVENT,
                                     message=f"batch {batch_id}: terminating child for cancel"[:500],
                                     payload={"batch_id": batch_id}, request_id=request_id)


def settle_child(repos, adapter, request_id, snapshot_jobs, *, actor, component,
                 batch_id=None) -> "tuple[str | None, str | None]":
    """k8s 종료(terminate_job)를 마친 자식 요청 하나를 DB 에 기록하고 **항목에 옮길 (상태, 사유)** 를 돌려준다(상태 None =
    요청 행 없음, 사유 None = 요청의 마지막 사유를 쓴다 -- record_item_terminal).

    - 스냅숏의 비종단 잡 → Cancelled(종단 가드: 그 사이 stepper 가 끝낸 잡은 그 결과 그대로), 커밋 뒤 sweep_after_cancel
      (스냅숏 뒤 stepper 가 제출한 단계를 끝낸다).
    - 스냅숏에 잡이 없었으면 Pending CAS(cancel_if_pending -- planner 의 잡 생성과 직렬화). 지면 planner 가 그 사이 잡을
      만들었다 -- 다시 읽어 끝낸다(종료 실패는 숨기지 않고 terminate_failed 이벤트).
    - 요청 마무리는 **지금** 잡 상태로(그 사이 성공한 자식을 Cancelled 로 덮지 않는다, 거짓 취소 금지).
    - 항목 상태: 취소가 시작될 때 살아 있던 잡이 그 뒤 실패·시간 초과로 끝났으면 그것은 취소가 지운 vcjob 을 stepper 가
      실패로 본 것이다 -- 항목은 Cancelled·cancelled_by_batch(실패 수에 넣지 않는다, 검증 지적). 그 전에 이미 끝난 결과는
      그대로."""
    live_at_start = any(DataJobState(j["state"]) not in TERMINAL_DATA_JOB_STATES for j in snapshot_jobs)
    for job in snapshot_jobs:
        if DataJobState(job["state"]) not in TERMINAL_DATA_JOB_STATES:
            repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                          reason_code=CANCEL_REASON, actor=actor)
    for job in snapshot_jobs:
        sweep_after_cancel(repos, adapter, job)
    if not snapshot_jobs:
        if repos.requests.cancel_if_pending(request_id, reason_code=CANCEL_REASON, actor=actor):
            return RequestState.CANCELLED.value, None
        late = repos.data_jobs.list_jobs(request_id=request_id)
        if late:
            mark_cancel_reached(repos, request_id, batch_id, component=component)
        for job in late:
            live_at_start = live_at_start or DataJobState(job["state"]) not in TERMINAL_DATA_JOB_STATES
            try:
                terminate_job(adapter, job)
            except ExecutionError as exc:
                _event(repos, request_id, component=component, severity="error", event_type="terminate_failed",
                       message=f"{exc.reason_code}: batch cancel late child {job['job_id']}",
                       payload={"job_id": job["job_id"], "after_cancel": True})
            repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                          reason_code=CANCEL_REASON, actor=actor)
            sweep_after_cancel(repos, adapter, job)
    jobs = repos.data_jobs.list_jobs(request_id=request_id)
    if jobs and not any(j["state"] == DataJobState.CANCELLED.value for j in jobs) \
            and all(DataJobState(j["state"]) in TERMINAL_DATA_JOB_STATES for j in jobs):
        # 잡이 이미 종단이면 취소가 아니라 그 실제 결과로 요청을 화해시킨다.
        state, reason = DataJobState(jobs[0]["state"]), "orphan_recovery"
    else:
        state, reason = DataJobState.CANCELLED, CANCEL_REASON
    # finalize_from_job 은 멱등(종단 요청은 건드리지 않는다 -- 잠근 행으로 다시 본다) + results 행을 남긴다.
    repos.requests.finalize_from_job(request_id, state, reason_code=reason, actor=actor)
    req = repos.requests.get(request_id)
    if req is None:
        return None, None
    if live_at_start and any(j["state"] in _KILLED_BY_CANCEL for j in jobs):
        return RequestState.CANCELLED.value, CANCEL_REASON
    return req["state"], None


def record_item_terminal(repos, batch_id, seq, request_id, req_state, *, reason_code=None) -> bool:
    """자식 요청의 종단 결과를 항목에 옮긴다 -- **Materialized 일 때만**(CAS, finish_item_if_materialized) 그리고 바꾼 쪽만
    집계한다. orchestrator 의 기록·취소 라우트·컨트롤러 그물이 같은 항목을 동시에 기록해도 결과가 덮이거나 두 번 세지지
    않는다. 취소는 성공도 실패도 아니다(집계 없음). 사유는 reason_code(settle_child 가 정한 것)가 없으면 요청의 실제 종단
    사유 -- 사유가 없으면 NULL 이다(상태값을 사유인 양 반복하지 않는다는 설계 결정)."""
    if req_state == RequestState.SUCCEEDED.value:
        with repos.db.transaction():
            moved = repos.batches.finish_item_if_materialized(batch_id, seq, "Succeeded")
            if moved:
                repos.batches.bump_counts(batch_id, succeeded=1)
        return moved
    reason = reason_code if reason_code is not None else repos.requests.last_reason_code(request_id)
    if req_state == RequestState.CANCELLED.value:
        return repos.batches.finish_item_if_materialized(batch_id, seq, "Cancelled", reason_code=reason)
    status = "Rejected" if req_state == RequestState.REJECTED.value else "Failed"
    with repos.db.transaction():
        moved = repos.batches.finish_item_if_materialized(batch_id, seq, status, reason_code=reason)
        if moved:
            repos.batches.bump_counts(batch_id, failed=1)
    return moved


def _event(repos, request_id, *, component, severity, event_type, message, payload, now=None):
    """같은 (요청, 종류) 이벤트는 쿨다운 안에 한 번만 -- 영구 실패하는 자식이 5초 틱마다 events 를 채우지 않게. 이벤트 기록
    자체의 실패는 정리를 막지 않는다(record_event 가 예외를 올리지 않는다)."""
    now = now or utc_now_iso()
    if request_id is not None and repos.db.query_one(
            "SELECT 1 AS x FROM events WHERE request_id = :r AND event_type = :t AND at > :since LIMIT 1",
            {"r": request_id, "t": event_type, "since": iso_plus(now, -_EVENT_COOLDOWN_SECONDS)}):
        return
    repos.observability.record_event(component=component, severity=severity, event_type=event_type,
                                     message=message[:500], payload=payload, request_id=request_id)


def sweep_cancelled(repos, adapter, *, grace_seconds: int = CANCEL_SWEEP_GRACE_SECONDS, limit: int = 50,
                    now=None, actor="batch-orchestrator") -> int:
    """컨트롤러 그물(모듈 docstring 3). 끝낸 자식 수를 돌려준다.

    1) 살아 있는 자식(유예 뒤의 Cancelling 배치, 또는 Cancelled 배치에 남은 것): 자식마다 격리 -- k8s 종료가 실패한 자식은
       기록하지 않고(파드가 돌 수 있는데 Cancelled 로 쓰면 거짓 취소) 다음 틱이 다시 시도한다. 그 밖의 예외(변조된 행 등)도
       그 자식만 건너뛴다 -- 독 행 하나가 그물 전체를 멈추지 않게(검증 지적, 고아 스윕과 같은 규약). 고르는 순서는 무작위다
       (live_children_of_cancelled) -- 계속 실패하는 자식이 limit 칸을 늘 차지해 다른 배치의 자식이 굶지 않게.
    2) 유예 뒤의 Cancelling 배치마다 아직 Materialized 인 항목을 자식의 결과로 기록하고(라우트가 중간에 멈춘 경우 -- 검증 지적:
       항목이 영원히 Materialized 로 남았다), 남은 게 없으면 Cancelled 로 넘긴다(finish_cancel_if_settled)."""
    now = now or utc_now_iso()
    before = iso_plus(now, -grace_seconds)
    done = 0
    for row in repos.batches.live_children_of_cancelled(before=before, limit=limit):
        rid = row["request_id"]
        try:
            jobs = repos.data_jobs.list_jobs(request_id=rid)
            if jobs:
                mark_cancel_reached(repos, rid, row["batch_id"], component="batch-orchestrator")
            try:
                for job in jobs:
                    terminate_job(adapter, job)
            except ExecutionError as exc:
                _event(repos, rid, component="batch-orchestrator", severity="error", event_type="terminate_failed",
                       message=f"{exc.reason_code}: cancelled batch {row['batch_id']} child {rid}",
                       payload={"batch_id": row["batch_id"], "after_cancel": True}, now=now)
                continue
            state, why = settle_child(repos, adapter, rid, jobs, actor=actor, component="batch-orchestrator",
                                      batch_id=row["batch_id"])
            if state is not None and row.get("seq") is not None:
                record_item_terminal(repos, row["batch_id"], row["seq"], rid, state, reason_code=why)
            repos.observability.record_event(
                component="batch-orchestrator", severity="warning", event_type="cancelled_batch_child_swept",
                message=f"cancelled batch {row['batch_id']}: live child {rid} ended ({state})"[:500],
                payload={"batch_id": row["batch_id"]}, request_id=rid)
            done += 1
        except Exception as exc:  # 한 자식이 그물 전체를 막지 않는다(위 docstring 1)
            _event(repos, rid, component="batch-orchestrator", severity="error", event_type="cancel_sweep_failed",
                   message=f"{type(exc).__name__}: {exc}", payload={"batch_id": row["batch_id"]}, now=now)
    for bid in repos.batches.cancelling_batch_ids(before=before):
        try:
            repos.batches.bar_queued_if_cancelling(bid)      # 경합으로 끼어든 Queued(항목 추가·재실행) -- 다시 막는다
            for item in repos.batches.materialized_items(bid):
                rid = item.get("request_id")
                req = repos.requests.get(rid) if rid else None
                if req is None:
                    # 자식 요청 행이 없다(지워졌거나 비정상) -- 기록할 결과가 없다. 취소로 닫는다(집계 없음).
                    repos.batches.finish_item_if_materialized(bid, item["seq"], "Cancelled", reason_code=CANCEL_REASON)
                elif req["state"] in _TERMINAL_REQ:
                    # 그 결과 그대로 -- 취소가 지운 vcjob 을 stepper 가 실패로 본 자식은 stepper 가 이미 Cancelled 로
                    # 기록한다(stepper._finalize 의 취소 배치 규칙). 취소 전에 실제로 끝난 결과는 그대로 둔다.
                    record_item_terminal(repos, bid, item["seq"], rid, req["state"])
            repos.batches.finish_cancel_if_settled(bid)
        except Exception as exc:
            _event(repos, None, component="batch-orchestrator", severity="error", event_type="cancel_sweep_failed",
                   message=f"{type(exc).__name__}: batch {bid}: {exc}", payload={"batch_id": bid}, now=now)
    return done
