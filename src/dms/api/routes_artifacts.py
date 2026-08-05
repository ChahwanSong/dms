from fastapi import APIRouter, Depends, HTTPException, Query, Request
from ..execution import ExecutionError
from .artifacts import ArtifactError, MAX_BYTES, PHASES, list_artifacts, read_artifact, strip_scheme
from .auth import Identity, require_user
from .routes_jobs import _owned_job

router = APIRouter()


def _base(request: Request) -> str:
    return strip_scheme(request.app.state.settings.artifact_base_uri)


@router.get("/api/user/jobs/{job_id}/artifacts")
def list_job_artifacts(job_id: str, request: Request,
                       identity: Identity = Depends(require_user)):
    _owned_job(request, job_id, identity)
    try:
        return list_artifacts(_base(request), job_id)
    except ArtifactError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)


@router.get("/api/user/jobs/{job_id}/artifacts/{phase}/{name}")
def get_job_artifact(job_id: str, phase: str, name: str, request: Request,
                     tail: int | None = Query(default=None, ge=1),
                     identity: Identity = Depends(require_user)):
    _owned_job(request, job_id, identity)
    try:
        return read_artifact(_base(request), job_id, phase, name, tail=tail)
    except ArtifactError as e:
        status = {"artifact_not_found": 404, "artifact_forbidden": 403}.get(
            e.reason_code, 422)
        raise HTTPException(status_code=status, detail=e.reason_code)


@router.get("/api/user/jobs/{job_id}/logs")
def get_job_logs(job_id: str, request: Request, phase: str = Query(default="preflight"),
                 tail: int | None = Query(default=None, ge=1),
                 identity: Identity = Depends(require_user)):
    job = _owned_job(request, job_id, identity)
    if phase not in PHASES:
        raise HTTPException(status_code=422, detail="invalid_phase")
    ref = (job["phase_refs"] or {}).get(phase)
    if not ref:
        raise HTTPException(status_code=404, detail="log_ref_not_found")
    try:
        entries = request.app.state.execution_adapter.read_log(ref)
    except ExecutionError as e:
        raise HTTPException(status_code=409, detail=e.reason_code)
    out = []
    for pod, log in entries:
        if log is not None:
            if tail is not None:
                log = "\n".join(log.splitlines()[-tail:])
            if len(log.encode()) > MAX_BYTES:
                log = log.encode()[-MAX_BYTES:].decode("utf-8", errors="replace")
        out.append({"pod": pod, "log": log})
    return {"phase": phase, "ref": ref, "entries": out}
