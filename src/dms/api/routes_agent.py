from fastapi import APIRouter, Depends, HTTPException, Request

from .auth import Identity, require_user

router = APIRouter()


@router.post("/api/agent/report")
def ingest_report(body: dict, request: Request,
                  identity: Identity = Depends(require_user)):
    node_name = body.get("node_name")
    if (not isinstance(node_name, str) or not node_name
            or any(ch.isspace() for ch in node_name)):
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
    }
