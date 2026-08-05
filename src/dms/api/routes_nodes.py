from fastapi import APIRouter, Depends, HTTPException, Query, Request

from .auth import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/api/admin/nodes")
def list_nodes(request: Request):
    settings = request.app.state.settings
    return request.app.state.repos.agents.list_nodes(
        stale_seconds=settings.agent_report_stale_seconds)


@router.get("/api/admin/nodes/{name}/reports")
def node_reports(name: str, request: Request,
                 limit: int = Query(default=100, ge=1, le=1000)):
    repos = request.app.state.repos
    rows = repos.agents.node_reports(name, limit=limit)
    if not rows:
        # 이력이 없더라도 노드 자체(agent_nodes)가 존재하면 빈 목록을 돌려준다 —
        # retention이 agent_reports만 지우고 agent_nodes는 남기므로, 목록에 보이는
        # 노드가 상세 화면에서 404로 보이면 안 된다.
        if not repos.agents.node_exists(name):
            raise HTTPException(status_code=404, detail="node_not_found")
    return rows
