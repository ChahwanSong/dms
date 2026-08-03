from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from ..domain import DomainValidationError, PRIORITIES
from ..repositories.control import POLICY_TOOLS
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class PolicyBody(BaseModel):
    max_nodes: int = Field(ge=1)
    procs_per_node: int = Field(ge=1)
    queue: str = "dms-data"
    default_priority: str = "mid"
    max_priority: str = "high"
    preview_timeout_seconds: int | None = None
    execution_timeout_seconds: int = Field(ge=1)
    enabled: bool = True


@router.get("/api/admin/policies")
def list_policies(request: Request):
    control = request.app.state.repos.control
    out = [control.get_policy(t) for t in sorted(POLICY_TOOLS)]
    return [p for p in out if p is not None]


@router.get("/api/admin/policies/{tool}")
def get_policy(tool: str, request: Request):
    policy = request.app.state.repos.control.get_policy(tool)
    if policy is None:
        raise HTTPException(status_code=404, detail="policy_not_found")
    return policy


@router.put("/api/admin/policies/{tool}")
def put_policy(tool: str, body: PolicyBody, request: Request,
               identity: Identity = Depends(require_admin)):
    if body.default_priority not in PRIORITIES or body.max_priority not in PRIORITIES:
        raise HTTPException(status_code=422, detail="invalid_priority")
    try:
        request.app.state.repos.control.upsert_policy(
            tool, max_nodes=body.max_nodes, procs_per_node=body.procs_per_node,
            queue=body.queue, default_priority=body.default_priority,
            max_priority=body.max_priority,
            preview_timeout_seconds=body.preview_timeout_seconds,
            execution_timeout_seconds=body.execution_timeout_seconds,
            enabled=body.enabled, actor=identity.actor)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    return request.app.state.repos.control.get_policy(tool)
