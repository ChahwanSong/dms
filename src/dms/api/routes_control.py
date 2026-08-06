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
    # build_node_name은 그대로 k8s nodeSelector로 흘러간다 -- 공백만 있는 값이
    # 저장되면 파드가 스케줄되지 않고 조용히 Pending에 머문다. 저장 시점에 trim하고
    # 빈 문자열은 "미설정"과 같은 뜻이므로 None으로 정규화한다 -- 이 값을 읽는 모든
    # 소비자(admin 빌드 API, 향후 BuildWatcher/매니페스트)가 각자 방어 코드를
    # 중복해서 두지 않아도 되도록 한 곳에서만 정규화한다.
    build_node_name = (body.build_node_name or "").strip() or None
    control.set_control_state(maintenance=body.maintenance, drain=body.drain,
                              reason=body.reason, build_node_name=build_node_name,
                              actor=identity.actor)
    return control.control_state()
