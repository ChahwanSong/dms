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


def _validated_payload(body: RequestBody) -> tuple[dict, str]:
    op = Operation(body.operation)
    if body.priority not in PRIORITIES:
        raise DomainValidationError("invalid_priority", body.priority)
    options = validate_options(op, body.options)
    if body.owner_username is not None:
        validate_owner_username(body.owner_username)
    fp = option_fingerprint(options)
    if op is Operation.SYNC:
        src, dst = validate_sync_paths(body.source or "", body.destination or "")
        key = build_resource_key(op, source_storage=body.source_storage, source=src,
                                 destination_storage=body.destination_storage,
                                 destination=dst, fingerprint=fp)
        payload = {"source_storage": body.source_storage, "source": src,
                   "destination_storage": body.destination_storage,
                   "destination": dst}
    elif op is Operation.RM:
        target = validate_rm_target(body.target or "", options)
        key = build_resource_key(op, storage=body.storage, target=target, fingerprint=fp)
        payload = {"storage": body.storage, "target": target}
    else:
        target = validate_relative_path(body.target or "")
        key = build_resource_key(op, storage=body.storage, target=target, fingerprint=fp)
        payload = {"storage": body.storage, "target": target}
    payload.update({"options": options, "owner_username": body.owner_username})
    return payload, key


@router.post("/api/user/requests", status_code=202)
def submit(body: RequestBody, request: Request,
           identity: Identity = Depends(require_user)):
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
