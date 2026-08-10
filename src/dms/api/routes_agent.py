from fastapi import APIRouter, Depends, HTTPException, Request

from ..artifact_base import resolve_artifact_base, strip_scheme
# 노드 이름 규칙은 auth 가 유일 출처다 -- 토큰 경로의 actor 게이트가 통과시킨
# node:<이름> 을 여기서 다시 검증하므로 두 규칙이 갈라지면 안 된다(슬라이스 19).
from .auth import Identity, _NODE_NAME_RE, require_user

router = APIRouter()


@router.post("/api/agent/report")
def ingest_report(body: dict, request: Request,
                  identity: Identity = Depends(require_user)):
    node_name = body.get("node_name")
    if not isinstance(node_name, str) or not _NODE_NAME_RE.fullmatch(node_name):
        raise HTTPException(status_code=422, detail="invalid_node_name")
    if identity.actor != f"node:{node_name}":
        raise HTTPException(status_code=403, detail="agent_node_identity_mismatch")
    repos = request.app.state.repos
    settings = request.app.state.settings
    repos.agents.ingest(node_name, body)
    storages = [{"storage_name": s["storage_name"], "mount_path": s["mount_path"],
                 "managed_root": s["managed_root"]}
                for s in repos.storages.list() if s["enabled"]]
    return {
        "storages": storages,
        "identity_probe_targets": repos.control.probe_targets(
            ttl_seconds=settings.identity_probe_ttl_seconds),
        "report_interval_seconds": settings.agent_report_interval_seconds,
        # 슬라이스 18(설계 §2.4b): 에이전트는 ConfigMap 을 envFrom 으로 받지 않아
        # (50-agent-daemonset.yaml) base 를 아는 유일한 경로가 이 응답이다.
        # 스킴을 벗겨 내린다 -- 에이전트 프로브는 os.path 만 안다.
        "artifact_base_path": strip_scheme(
            resolve_artifact_base(repos.control, settings)),
    }
