from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from .auth import Identity, audit_actor, require_admin

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
    repos = request.app.state.repos
    control = repos.control
    # build_node_name은 그대로 k8s nodeSelector로 흘러간다 -- 공백만 있는 값이
    # 저장되면 파드가 스케줄되지 않고 조용히 Pending에 머문다. 저장 시점에 trim하고
    # 빈 문자열은 "미설정"과 같은 뜻이므로 None으로 정규화한다 -- 이 값을 읽는 모든
    # 소비자(admin 빌드 API, 향후 BuildWatcher/매니페스트)가 각자 방어 코드를
    # 중복해서 두지 않아도 되도록 한 곳에서만 정규화한다.
    build_node_name = (body.build_node_name or "").strip() or None
    # 설계 §3: 자유 입력 금지 -- 오타가 nodeSelector로 새면 빌드 파드가 스케줄조차
    # 안 돼 영원히 Pending이다(activeDeadlineSeconds는 스케줄된 뒤에만 발화하므로
    # 이 경로는 파드 타임아웃으로도 못 잡는다). agent_nodes에 실제로 보고된 노드
    # 이름 중에서만 고르게 한다.
    if build_node_name is not None and not repos.agents.node_exists(build_node_name):
        raise HTTPException(status_code=422, detail="unknown_build_node")
    control.set_control_state(maintenance=body.maintenance, drain=body.drain,
                              reason=body.reason, build_node_name=build_node_name,
                              actor=audit_actor(identity))
    return control.control_state()
