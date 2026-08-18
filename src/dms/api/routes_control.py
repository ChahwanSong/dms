from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from .auth import Identity, audit_actor, require_admin
from .routes_builds import validate_source_path

router = APIRouter(dependencies=[Depends(require_admin)])


class ControlStateBody(BaseModel):
    maintenance: bool
    drain: bool
    reason: str | None = None
    build_node_name: str | None = None
    build_source_path: str | None = None


@router.get("/api/admin/control-state")
def get_control_state(request: Request):
    return request.app.state.repos.control.control_state()


@router.get("/api/admin/control-state/history")
def get_control_state_history(request: Request,
                              limit: int = Query(default=10, ge=1, le=50)):
    """컨트롤 상태 변경 이력(슬라이스 36). 유지보수·드레인은 "누가 언제 왜"가
    본질인 운영 스위치라 마지막 1건(changed_by/changed_at)만으로는 부족하다 --
    감사 로그의 before/after 스냅샷을 그대로 내보내고 diff 는 화면이 계산한다
    (서버가 문구를 만들면 표시 언어가 API 계약에 박힌다)."""
    return request.app.state.repos.control.control_state_history(limit=limit)


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
    # 소스 경로는 노드처럼 목록 대조를 할 수 없다(API 파드는 빌드 노드의 파일시스템을
    # 못 본다) -- 모양만 저장 시점에 거르고, 실재 여부는 빌드 프리플라이트 프로브가
    # 노드 위에서 검사한다(build_source_unavailable). 검사는 제출 라우트와 같은
    # 함수를 쓴다(validate_source_path 주석).
    build_source_path = (body.build_source_path or "").strip() or None
    if build_source_path is not None:
        build_source_path = validate_source_path(build_source_path)
        if build_source_path is None:
            raise HTTPException(status_code=422, detail="invalid_source_path")
    control.set_control_state(maintenance=body.maintenance, drain=body.drain,
                              reason=body.reason, build_node_name=build_node_name,
                              build_source_path=build_source_path,
                              actor=audit_actor(identity))
    return control.control_state()
