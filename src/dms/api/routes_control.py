from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class ControlStateBody(BaseModel):
    maintenance: bool
    drain: bool
    reason: str | None = None
    build_node_name: str | None = None


@router.get("/api/admin/control-state")
def get_control_state(request: Request):
    return request.app.state.repos.control.control_state()


@router.put("/api/admin/control-state")
def put_control_state(body: ControlStateBody, request: Request,
                      identity: Identity = Depends(require_admin)):
    control = request.app.state.repos.control
    control.set_control_state(maintenance=body.maintenance, drain=body.drain,
                              reason=body.reason, build_node_name=body.build_node_name,
                              actor=identity.actor)
    return control.control_state()
