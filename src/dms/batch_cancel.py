"""배치 취소의 자식 정리 -- API 취소(routes_batches.cancel_batch)와 컨트롤러 그물(BatchOrchestrator.sweep_cancelled)이 같은
규칙을 쓴다(2026-10-16 사용자 보고: 「배치작업 Cancel 했는데 몇분 후에 k8s 을 직접 확인해보니 아직 돌아가고 있는
launcher/worker pod 들이 보여」).

원인(스크래치 시험으로 재현): 취소는 자식마다 k8s 삭제를 **한 뒤에야** 항목·배치를 Cancelled 로 썼다. 그 사이 stepper 가
지워진 vcjob 을 실패로 보고 자식이 끝나 슬롯이 비면, 배치가 아직 Running/Previewing 이라 orchestrator 가 Queued 항목에서
**새 자식**을 만들었다. 취소는 스냅숏의 Queued/Materialized 항목을 Cancelled 로 덮을 뿐이라 그 자식 요청은 Pending 으로
남았고, planner 는 배치 상태를 보지 않아 계획했다 -- stepper 가 취소 **뒤에** 새 vcjob(launcher·worker)을 만들어 끝까지
돌렸다. 그래서 세 겹으로 막는다:
  1) 취소 라우트가 먼저 Queued 항목을 막고(BatchesRepository.bar_queued_items -- claim_queued_item 의 Queued 가드가 0 행)
     배치를 Cancelled 로 쓴 뒤 k8s 를 끝낸다 -- orchestrator 는 Cancelled 배치를 굴리지 않는다.
  2) planner 는 Cancelled 배치의 Pending 자식을 계획하지 않고 Cancelled 로 끝낸다.
  3) 컨트롤러(orchestrator 틱)가 Cancelled 배치 밑에 살아 있는 자식을 찾아 끝낸다 -- 부분 실패·앞으로 생길 경합의 그물.
"""
from .api.cancel import sweep_after_cancel, terminate_job
from .domain import DataJobState, RequestState, TERMINAL_DATA_JOB_STATES
from .execution import ExecutionError

CANCEL_REASON = "cancelled_by_batch"


def settle_child(repos, adapter, request_id, snapshot_jobs, *, actor, component) -> "str | None":
    """k8s 종료(terminate_job)를 마친 자식 요청 하나를 DB 에 기록하고 요청의 최종 상태를 돌려준다(None = 요청 행 없음).

    - 스냅숏의 비종단 잡 → Cancelled(종단 가드: 그 사이 stepper 가 끝낸 잡은 실제 결과 그대로), 커밋 뒤 sweep_after_cancel
      (스냅숏 뒤 stepper 가 제출한 단계를 끝낸다).
    - 스냅숏에 잡이 없었으면 Pending CAS(cancel_if_pending -- planner 의 잡 생성과 직렬화). 지면 planner 가 그 사이 잡을
      만들었다 -- 다시 읽어 끝낸다(종료 실패는 이미 다른 자식의 취소를 기록했으므로 숨기지 않고 terminate_failed 이벤트).
    - 요청 마무리는 **지금** 잡 상태로(스냅숏이 아니다 -- 그 사이 성공한 자식을 Cancelled 로 덮지 않는다, 거짓 취소 금지)."""
    for job in snapshot_jobs:
        if DataJobState(job["state"]) not in TERMINAL_DATA_JOB_STATES:
            repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                          reason_code=CANCEL_REASON, actor=actor)
    for job in snapshot_jobs:
        sweep_after_cancel(repos, adapter, job)
    if not snapshot_jobs:
        if repos.requests.cancel_if_pending(request_id, reason_code=CANCEL_REASON, actor=actor):
            return RequestState.CANCELLED.value
        for job in repos.data_jobs.list_jobs(request_id=request_id):
            try:
                terminate_job(adapter, job)
            except ExecutionError as exc:
                repos.observability.record_event(
                    component=component, severity="error", event_type="terminate_failed",
                    message=f"{exc.reason_code}: batch cancel late child {job['job_id']}"[:500],
                    payload={"job_id": job["job_id"], "after_cancel": True}, request_id=request_id)
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
    # finalize_from_job 은 멱등(종단 요청은 건드리지 않는다) + results 행을 남긴다.
    repos.requests.finalize_from_job(request_id, state, reason_code=reason, actor=actor)
    req = repos.requests.get(request_id)
    return None if req is None else req["state"]


def record_item_terminal(repos, batch_id, seq, request_id, req_state) -> bool:
    """자식 요청의 종단 결과를 항목에 옮긴다 -- **Materialized 일 때만**(CAS, finish_item_if_materialized) 그리고 바꾼 쪽만
    집계한다. orchestrator 의 기록·취소 라우트·컨트롤러 그물이 같은 항목을 동시에 기록해도 결과가 덮이거나 두 번 세지지
    않는다. 취소는 성공도 실패도 아니다(집계 없음). 사유는 요청의 실제 종단 사유(상태값 반복이 아니다)."""
    if req_state == RequestState.SUCCEEDED.value:
        with repos.db.transaction():
            moved = repos.batches.finish_item_if_materialized(batch_id, seq, "Succeeded")
            if moved:
                repos.batches.bump_counts(batch_id, succeeded=1)
        return moved
    reason = repos.requests.last_reason_code(request_id)
    if req_state == RequestState.CANCELLED.value:
        return repos.batches.finish_item_if_materialized(batch_id, seq, "Cancelled", reason_code=reason)
    status = "Rejected" if req_state == RequestState.REJECTED.value else "Failed"
    with repos.db.transaction():
        moved = repos.batches.finish_item_if_materialized(batch_id, seq, status, reason_code=reason)
        if moved:
            repos.batches.bump_counts(batch_id, failed=1)
    return moved


def sweep_cancelled(repos, adapter, *, limit: int = 50, actor="batch-orchestrator") -> int:
    """컨트롤러 그물: Cancelled 배치 밑에 아직 살아 있는 자식을 끝낸다(모듈 docstring 3). 자식마다 독립 -- k8s 종료가 실패한
    자식은 기록하지 않고(파드가 돌 수 있는데 Cancelled 로 쓰면 거짓 취소) terminate_failed 이벤트만 남겨 다음 틱이 다시
    시도한다. 끝낸 자식 수를 돌려준다."""
    done = 0
    for row in repos.batches.live_children_of_cancelled(limit=limit):
        rid = row["request_id"]
        jobs = repos.data_jobs.list_jobs(request_id=rid)
        try:
            for job in jobs:
                terminate_job(adapter, job)
        except ExecutionError as exc:
            repos.observability.record_event(
                component="batch-orchestrator", severity="error", event_type="terminate_failed",
                message=f"{exc.reason_code}: cancelled batch {row['batch_id']} child {rid}"[:500],
                payload={"batch_id": row["batch_id"], "after_cancel": True}, request_id=rid)
            continue
        state = settle_child(repos, adapter, rid, jobs, actor=actor, component="batch-orchestrator")
        if state is not None and row.get("seq") is not None:
            record_item_terminal(repos, row["batch_id"], row["seq"], rid, state)
        repos.observability.record_event(
            component="batch-orchestrator", severity="warning", event_type="cancelled_batch_child_swept",
            message=f"cancelled batch {row['batch_id']}: live child {rid} ended ({state})"[:500],
            payload={"batch_id": row["batch_id"]}, request_id=rid)
        done += 1
    return done
