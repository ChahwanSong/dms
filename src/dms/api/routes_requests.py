from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import (
    DataJobState, DomainValidationError, Operation, PRIORITIES, RequestState,
    TERMINAL_DATA_JOB_STATES, TERMINAL_REQUEST_STATES, build_data_payload,
    resolve_priority, validate_owner_username,
)
from ..execution import ExecutionError
from .auth import Identity, require_user
from .cancel import terminate_job
from .routes_jobs import _owned_request

router = APIRouter()


def reject_when_maintenance(request: Request) -> None:
    # 유지보수 창에는 신규 유입을 막는다 (진행 중인 잡은 건드리지 않는다 — 그건 drain의 몫).
    # 관리자도 예외가 아니다. 콘솔의 control-state PUT은 제출 경로가 아니라 잠기지 않는다.
    state = request.app.state.repos.control.control_state()
    if state and state["maintenance"]:
        raise HTTPException(status_code=503, detail="maintenance_mode")


class RequestBody(BaseModel):
    operation: str
    storage: str | None = None
    source_storage: str | None = None
    destination_storage: str | None = None
    target: str | None = None
    source: str | None = None
    destination: str | None = None
    options: dict = {}
    priority: str | None = None
    owner_username: str | None = None


def _require(value: str | None, reason: str) -> str:
    if not value:
        raise DomainValidationError(reason, "required field missing")
    return value


def _validated_payload(body: RequestBody, priority: str) -> tuple[dict, str]:
    op = Operation(body.operation)
    if priority not in PRIORITIES:
        raise DomainValidationError("invalid_priority", priority)
    if body.owner_username is not None:
        validate_owner_username(body.owner_username)
    # storage 필드 존재 검증은 여기서 구체적 reason_code로 먼저 수행한다
    # (build_data_payload는 sync에 대해 "missing_storage" 하나로 뭉뚱그리므로,
    #  source/destination을 구분하는 기존 API 계약(422 reason_code)을 유지하려면
    #  존재 체크는 라우트에서, 옵션 검증+경로 검증+fingerprint+resource_key는
    #  build_data_payload에 위임한다 — build_data_payload가 이미 validate_options를
    #  수행하고 그 결과를 payload["options"]에 담아 반환하므로 여기서 다시
    #  validate_options를 호출하지 않는다).
    if op is Operation.SYNC:
        src_storage = _require(body.source_storage, "missing_source_storage")
        dst_storage = _require(body.destination_storage, "missing_destination_storage")
        payload, key = build_data_payload(
            "sync", source_storage=src_storage, source=body.source,
            destination_storage=dst_storage, destination=body.destination,
            options=body.options)
    elif op is Operation.RM:
        storage = _require(body.storage, "missing_storage")
        payload, key = build_data_payload(
            "rm", storage=storage, target=body.target, options=body.options)
    else:
        storage = _require(body.storage, "missing_storage")
        payload, key = build_data_payload(
            "scan", storage=storage, target=body.target, options=body.options)
    payload["owner_username"] = body.owner_username
    return payload, key


@router.post("/api/user/requests", status_code=202)
def submit(body: RequestBody, request: Request,
           identity: Identity = Depends(require_user)):
    reject_when_maintenance(request)
    # scan 제출은 관리자 전용이다 (설계 결정 레코드) — /admin/scan이 admin 라우트라는
    # 사실만으로는 강제되지 않으므로 여기서 명시적으로 게이트한다.
    # 원시 문자열로 비교한다 — Operation(...)은 알 수 없는 값에 ValueError를 던지는데
    # 이 지점은 그것을 422로 바꿔주는 try 블록 밖이라 500이 되어 버린다.
    if body.operation == Operation.SCAN.value and identity.role != "admin":
        raise HTTPException(status_code=403, detail="scan_admin_only")
    # 특권 게이트 (스펙 §5): owner_username이 요청자와 다르면 특권 의도 → 인가 필요
    owner = body.owner_username
    if owner is not None and owner != identity.actor:
        settings = request.app.state.settings
        authorized = (identity.role == "admin"
                      and settings.allow_privileged_requesters
                      and identity.actor in settings.privileged_requesters)
        if not authorized:
            raise HTTPException(status_code=403, detail="privileged_not_authorized")

    repos = request.app.state.repos
    priority = resolve_priority(repos, body.operation, body.priority)
    try:
        payload, resource_key = _validated_payload(body, priority)
    except (DomainValidationError, ValueError) as e:
        reason = getattr(e, "reason_code", "invalid_operation")
        raise HTTPException(status_code=422, detail=reason)
    rid = repos.requests.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        resource_key=resource_key, payload=payload, priority=priority)
    return {"request_id": rid, "state": "Pending"}


@router.get("/api/user/requests")
def list_requests(request: Request, identity: Identity = Depends(require_user)):
    requester = None if identity.role == "admin" else identity.actor
    return request.app.state.repos.requests.list(requester_id=requester)


@router.get("/api/user/requests/{request_id}")
def get_request(request_id: str, request: Request,
                identity: Identity = Depends(require_user)):
    repo = request.app.state.repos.requests
    row = repo.get(request_id)
    if row is None or (identity.role != "admin"
                       and row["requester_id"] != identity.actor):
        raise HTTPException(status_code=404, detail="request_not_found")
    row["transitions"] = repo.transitions(request_id)
    # events_for_request 기본 limit(100)이 조용히 잘리면 운영자가 눈치채지 못한다 --
    # 스택된 요청이 매 컨트롤러 틱마다 같은 예외로 실패를 반복하면 100건을 넘기는 것도
    # 현실적이다(plan_error/step_error는 정확히 이런 루프에서 기록된다). 표시 상한보다
    # 하나 더 가져와 잘림 여부를 판별하고, 값은 상한만큼만 내려준다.
    display_limit = 100
    events = request.app.state.repos.observability.events_for_request(
        request_id, limit=display_limit + 1)
    row["events_truncated"] = len(events) > display_limit
    row["events"] = events[:display_limit]
    return row


@router.post("/api/user/requests/{request_id}:cancel")
def cancel_request(request_id: str, request: Request,
                   identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    req = _owned_request(request, request_id, identity)
    if RequestState(req["state"]) in TERMINAL_REQUEST_STATES:
        raise HTTPException(status_code=409, detail="already_terminal")
    # planner 경쟁: 요청을 종결하기 전에 잡을 먼저 조회·종료해야 고아가 남지 않는다.
    adapter = request.app.state.execution_adapter
    jobs = repos.data_jobs.list_jobs(request_id=request_id)
    # 잡은 이미 전부 종단인데 요청만 아직 비종단인 창이 있다 — stepper의 finalize가
    # 누락된 고아(data_jobs.terminal_jobs_with_live_request()가 복구하려는 바로 그 상태,
    # 컨트롤러 크래시 후엔 재시작까지 지속된다). 여기서 취소를 기록하면 실제 결과
    # (rm이면 이미 끝난 삭제!)를 Cancelled로 덮어쓰는 거짓 취소가 되고, 결과 행도 없이
    # 요청이 종단이 되어 고아 리컨실러의 시야에서도 사라진다. 취소 대신 실제 결과로
    # 화해시키고 409를 돌려준다.
    if jobs and all(DataJobState(j["state"]) in TERMINAL_DATA_JOB_STATES for j in jobs):
        for job in jobs:
            repos.requests.finalize_from_job(
                request_id, DataJobState(job["state"]),
                reason_code="orphan_recovery", actor=identity.actor)  # 멱등 — 첫 잡이 이긴다
        raise HTTPException(status_code=409, detail="already_terminal")
    try:
        for job in jobs:
            terminate_job(adapter, job)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    for job in jobs:
        repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                      reason_code="cancelled_by_user", actor=identity.actor)
    # set_state가 아니라 finalize_from_job — 다른 모든 종단 전이와 동일하게 results 행을
    # 남긴다(set_state는 상태만 바꾸고 결과를 기록하지 않는다).
    repos.requests.finalize_from_job(request_id, DataJobState.CANCELLED,
                                     reason_code="cancelled_by_user", actor=identity.actor)
    return {"state": "Cancelled"}
