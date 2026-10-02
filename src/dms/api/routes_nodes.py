from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..repositories.node_exclusions import MAX_EXCLUSION_REASON
from .auth import Identity, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/api/admin/nodes")
def list_nodes(request: Request):
    settings = request.app.state.settings
    repos = request.app.state.repos
    nodes = repos.agents.list_nodes(stale_seconds=settings.agent_report_stale_seconds)
    # 노드 배치 제외(2026-10-02, repositories/node_exclusions.py): 행마다 제외 정보(사유·누가·언제) 또는 null.
    # k8s 스케줄 불가(cordon·taint)는 report.k8s_node 에 이미 실려 있다(에이전트 보고 그대로).
    exclusions = {r["node_name"]: r for r in repos.node_exclusions.list()}
    for n in nodes:
        n["exclusion"] = exclusions.get(n["node_name"])
    return nodes


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


class ExclusionBody(BaseModel):
    reason: "str | None" = None


@router.put("/api/admin/nodes/{name}/exclusion")
def exclude_node(name: str, body: ExclusionBody, request: Request,
                 identity: Identity = Depends(require_admin)):
    """노드를 DMS 잡 배치에서 뺀다(멱등 -- 이미 제외 중이면 기존 행). 보고한 적이 있는 노드만 받는다: 이름 오타가
    조용히 "아무것도 제외 안 함"이 되면 관리자는 막았다고 믿는다(빌드 노드 선택의 node_exists 선례). 이름은
    대소문자 그대로 비교한다 -- placement 가 에이전트 보고의 node_name 원문을 쓴다. 유지보수 중에도 허용한다(장애
    대응 동작이다 -- control-state 와 같은 이유)."""
    repos = request.app.state.repos
    if not repos.agents.node_exists(name):
        raise HTTPException(status_code=404, detail="node_not_found")
    reason = (body.reason or "").strip() or None
    # 제어문자 거부: PostgreSQL TEXT 는 NUL 을 담지 못해 INSERT 가 500 이 됐다(리뷰) -- 한 줄 자유 텍스트라 C0 전부 거부.
    if reason is not None and (len(reason) > MAX_EXCLUSION_REASON
                               or any(ord(c) < 32 or ord(c) == 127 for c in reason)):
        raise HTTPException(status_code=422, detail="invalid_exclusion_reason")
    return repos.node_exclusions.exclude(name, reason=reason, actor=identity.actor)


@router.delete("/api/admin/nodes/{name}/exclusion")
def include_node(name: str, request: Request, identity: Identity = Depends(require_admin)):
    """다시 포함(제외 해제). 다음 계획(planner 틱)부터 후보로 돌아온다. 제외 때문에 이미 거부·종단된 요청은 되살리지
    않는다(재제출). 제외 중이 아니면 404 -- 조용한 성공 금지."""
    if not request.app.state.repos.node_exclusions.include(name, actor=identity.actor):
        raise HTTPException(status_code=404, detail="node_exclusion_not_found")
    return {"included": name}
