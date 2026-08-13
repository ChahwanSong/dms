from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import (DataJobState, DomainValidationError, Operation,
                      TERMINAL_DATA_JOB_STATES, build_data_payload, validate_batch)
from ..execution import ExecutionError
from .auth import Identity, require_admin
from .cancel import terminate_job
from .routes_requests import reject_when_maintenance

router = APIRouter()


class BatchBody(BaseModel):
    operation: str
    max_concurrency: int
    options: dict = {}
    note: str | None = None
    items: list[dict]
    # 실행 제어(슬라이스 32). None = 미지정(정책 기본) — null≠0.
    priority: str | None = None
    node_count: int | None = None


@router.post("/api/admin/batches", status_code=202)
def create_batch(body: BatchBody, request: Request, identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    try:
        validate_batch(body.operation, body.max_concurrency, body.items,
                       priority=body.priority, node_count=body.node_count)
        for item in body.items:                       # 각 행 검증(조기 거부)
            build_data_payload(body.operation, options=body.options, **item)
    except (DomainValidationError, TypeError) as e:
        raise HTTPException(status_code=422, detail=getattr(e, "reason_code", "invalid_batch"))
    status = "Running" if body.operation == Operation.SCAN.value else "Previewing"
    bid = request.app.state.repos.batches.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        max_concurrency=body.max_concurrency, options=body.options, note=body.note,
        items=body.items, status=status,
        priority=body.priority, node_count=body.node_count)
    return {"batch_id": bid, "status": status}


@router.get("/api/admin/batches")
def list_batches(request: Request, identity: Identity = Depends(require_admin)):
    return request.app.state.repos.batches.list()


@router.get("/api/admin/batches/{batch_id}")
def get_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    b["items"] = repo.list_items(batch_id)
    return b


@router.post("/api/admin/batches/{batch_id}:confirm")
def confirm_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if b["status"] != "PreviewReady":
        raise HTTPException(status_code=409, detail="batch_not_confirmable")
    repo.set_status(batch_id, "Running")
    return {"status": "Running"}


@router.post("/api/admin/batches/{batch_id}:rerun-failed")
def rerun_failed(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    n = repo.reset_failed_items(batch_id)
    if n == 0:
        raise HTTPException(status_code=409, detail="no_failed_items")
    repo.set_status(batch_id, "Running")
    return {"status": "Running", "requeued": n}


@router.post("/api/admin/batches/{batch_id}:cancel")
def cancel_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    repo = repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    if b["status"] not in ("Previewing", "PreviewReady", "Running"):
        raise HTTPException(status_code=409, detail="batch_not_cancelable")
    adapter = request.app.state.execution_adapter
    items = repo.list_items(batch_id)
    # 1) 먼저 종료한다. 하나라도 실패하면 DB는 건드리지 않고 실패를 보고한다
    #    (거짓 취소 금지 — 상위 스펙 §5).
    child_jobs = []
    for it in items:
        rid = it.get("request_id")
        if not rid:
            continue
        for job in repos.data_jobs.list_jobs(request_id=rid):
            child_jobs.append((it, job))
    try:
        for _, job in child_jobs:
            terminate_job(adapter, job)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    # 2) 종료가 전부 성공한 뒤에만 기록한다.
    jobs_by_seq = {}
    for it, job in child_jobs:
        jobs_by_seq.setdefault(it["seq"], []).append(job)
        if DataJobState(job["state"]) in TERMINAL_DATA_JOB_STATES:
            # 이미 실제 결과가 난 잡은 Cancelled로 덮어쓰지 않는다 — Succeeded인 rm을
            # "취소됨"으로 보고하는 건 거짓 취소다(상위 스펙 §5).
            continue
        repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                      reason_code="cancelled_by_batch", actor=identity.actor)
    # 자식 요청은 잡 루프가 아니라 item에서 돈다. materialize는 됐지만 planner가 아직
    # 도달하지 못한 item은 잡 행이 하나도 없어 위 루프에 등장하지 않는다 —
    # 그 요청을 종결하지 않으면 Pending인 채 list_pending()에 남아 planner가 곧바로
    # 집어 실행해 버린다(거짓 취소). orchestrator 5s / planner 10s 주기라 가장 흔한 창이다.
    for it in items:
        rid = it.get("request_id")
        if not rid:
            continue
        jobs = jobs_by_seq.get(it["seq"], [])
        if jobs and all(DataJobState(j["state"]) in TERMINAL_DATA_JOB_STATES for j in jobs):
            # 잡이 이미 종단이면 취소가 아니라 그 실제 결과로 요청을 화해시킨다.
            state, reason = DataJobState(jobs[0]["state"]), "orphan_recovery"
        else:
            state, reason = DataJobState.CANCELLED, "cancelled_by_batch"
        # finalize_from_job은 멱등(종단 요청은 건드리지 않는다) + results 행을 남긴다.
        repos.requests.finalize_from_job(rid, state, reason_code=reason,
                                         actor=identity.actor)
    for it in items:
        if it["status"] in ("Queued", "Materialized"):
            repo.set_item_status(batch_id, it["seq"], "Cancelled")
    repo.set_status(batch_id, "Cancelled")
    return {"status": "Cancelled"}
