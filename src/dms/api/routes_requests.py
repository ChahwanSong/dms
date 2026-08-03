from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import (
    DomainValidationError, Operation, PRIORITIES,
    build_resource_key, option_fingerprint, validate_options,
    validate_owner_username, validate_relative_path, validate_rm_target,
    validate_sync_paths,
)
from .auth import Identity, require_user

router = APIRouter()


class RequestBody(BaseModel):
    operation: str
    storage: str | None = None
    source_storage: str | None = None
    destination_storage: str | None = None
    target: str | None = None
    source: str | None = None
    destination: str | None = None
    options: dict = {}
    priority: str = "mid"
    owner_username: str | None = None


def _require(value: str | None, reason: str) -> str:
    if not value:
        raise DomainValidationError(reason, "required field missing")
    return value


def _validated_payload(body: RequestBody) -> tuple[dict, str]:
    op = Operation(body.operation)
    if body.priority not in PRIORITIES:
        raise DomainValidationError("invalid_priority", body.priority)
    options = validate_options(op, body.options)
    if body.owner_username is not None:
        validate_owner_username(body.owner_username)
    fp = option_fingerprint(options)
    if op is Operation.SYNC:
        src_storage = _require(body.source_storage, "missing_source_storage")
        dst_storage = _require(body.destination_storage, "missing_destination_storage")
        src, dst = validate_sync_paths(body.source or "", body.destination or "")
        key = build_resource_key(op, source_storage=src_storage, source=src,
                                 destination_storage=dst_storage,
                                 destination=dst, fingerprint=fp)
        payload = {"source_storage": src_storage, "source": src,
                   "destination_storage": dst_storage,
                   "destination": dst}
    elif op is Operation.RM:
        storage = _require(body.storage, "missing_storage")
        target = validate_rm_target(body.target or "", options)
        key = build_resource_key(op, storage=storage, target=target, fingerprint=fp)
        payload = {"storage": storage, "target": target}
    else:
        storage = _require(body.storage, "missing_storage")
        target = validate_relative_path(body.target or "")
        key = build_resource_key(op, storage=storage, target=target, fingerprint=fp)
        payload = {"storage": storage, "target": target}
    payload.update({"options": options, "owner_username": body.owner_username})
    return payload, key


@router.post("/api/user/requests", status_code=202)
def submit(body: RequestBody, request: Request,
           identity: Identity = Depends(require_user)):
    # 특권 게이트 (스펙 §5): owner_username이 요청자와 다르면 특권 의도 → 인가 필요
    owner = body.owner_username
    if owner is not None and owner != identity.actor:
        settings = request.app.state.settings
        authorized = (identity.role == "admin"
                      and settings.allow_privileged_requesters
                      and identity.actor in settings.privileged_requesters)
        if not authorized:
            raise HTTPException(status_code=403, detail="privileged_not_authorized")

    try:
        payload, resource_key = _validated_payload(body)
    except (DomainValidationError, ValueError) as e:
        reason = getattr(e, "reason_code", "invalid_operation")
        raise HTTPException(status_code=422, detail=reason)
    rid = request.app.state.repos.requests.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        resource_key=resource_key, payload=payload, priority=body.priority)
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
    return row
