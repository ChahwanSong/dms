from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import (DataJobState, DomainValidationError, Operation,
                      TERMINAL_DATA_JOB_STATES, build_data_payload, validate_batch,
                      validate_owner_username)
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
    # 배치 특권 실행: 단건 제출(routes_requests)의 owner_username 이식. None = 비특권.
    owner_username: str | None = None


@router.post("/api/admin/batches", status_code=202)
def create_batch(body: BatchBody, request: Request, identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    # 특권 게이트(단건 제출 routes_requests.py:86-94 미러): owner_username 이
    # 요청자와 다르면 특권 의도 → 인가 필요. 단건의 3중(admin — require_admin 이
    # 이미 보장 — + 기능 플래그 + allowlist)에 **세션 인증**을 더한다: 단건은
    # planner 가 요청 auth_method 로 세션을 재검증하지만, 배치는 생성 시점 인증
    # 방식을 행에 박제해 자식이 물려받으므로 박제 전에 여기서 끊어야 토큰 생성
    # 배치가 특권을 실어 나르지 못한다.
    owner = body.owner_username
    if owner is not None and owner != identity.actor:
        settings = request.app.state.settings
        authorized = (identity.role == "admin"
                      and settings.allow_privileged_requesters
                      and identity.actor in settings.privileged_requesters
                      and identity.auth == "session")
        if not authorized:
            raise HTTPException(status_code=403, detail="privileged_not_authorized")
    try:
        if owner is not None:
            validate_owner_username(owner)    # 단건 _validated_payload 와 같은 검증
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
        priority=body.priority, node_count=body.node_count,
        # auth_method 박제는 특권 여부와 무관한 생성 시점 사실의 기록이다 --
        # 자식 request 가 물려받는다(orchestrator._materialize).
        owner_username=owner, auth_method=identity.auth)
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


@router.post("/api/admin/batches/{batch_id}:rescan")
def rescan_batch(batch_id: str, request: Request, identity: Identity = Depends(require_admin)):
    reject_when_maintenance(request)
    repo = request.app.state.repos.batches
    b = repo.get(batch_id)
    if b is None:
        raise HTTPException(status_code=404, detail="batch_not_found")
    # 종단 배치(Completed/Cancelled) 한정: 종단 배치 ⇒ 전 item 종단 ⇒ 살아있는
    # 자식이 없다 — cancel 선례의 "실행면 먼저 종료" 단계가 공집합으로 성립한다
    # (거짓 취소 방지 정합). 비종단에서 허용하면 활성 자식과 리셋 item 이 충돌
    # (resource_conflict·이중 실행)하므로 fail-closed 거부. PreviewReady 는 자식
    # ConfirmPending(활성)이라 제외 — 취소 후 rescan 이 정상 동선이다.
    if b["status"] not in ("Completed", "Cancelled"):
        raise HTTPException(status_code=409, detail="batch_not_rescannable")
    # 성공 item 포함 전체 리셋: 용도가 성장 모니터링(같은 대상 재스캔)이라
    # "실패만"이 아니라 전부를 다시 돌린다(:rerun-failed 와의 차이).
    n = repo.reset_all_items(batch_id)
    status = "Running" if b["operation"] == Operation.SCAN.value else "Previewing"
    repo.set_status(batch_id, status)
    return {"status": status, "requeued": n}


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
