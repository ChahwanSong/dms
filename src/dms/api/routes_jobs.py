from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES
from ..db import utc_now_iso
from ..execution import ExecutionError
from .auth import Identity, require_user
from .cancel import terminate_job

router = APIRouter()


class ConfirmBody(BaseModel):
    fingerprint: str


def _owned_request(request, request_id, identity):
    req = request.app.state.repos.requests.get(request_id)
    if req is None or (identity.role != "admin"
                       and req["requester_id"] != identity.actor):
        raise HTTPException(status_code=404, detail="request_not_found")
    return req


def _owned_job(request, job_id, identity):
    repos = request.app.state.repos
    job = repos.data_jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    req = repos.requests.get(job["request_id"])
    if req is None or (identity.role != "admin"
                       and req["requester_id"] != identity.actor):
        raise HTTPException(status_code=404, detail="job_not_found")
    return job


@router.get("/api/user/requests/{request_id}/jobs")
def list_jobs(request_id: str, request: Request,
              identity: Identity = Depends(require_user)):
    _owned_request(request, request_id, identity)
    repos = request.app.state.repos
    jobs = repos.data_jobs.list_jobs(request_id=request_id)
    for job in jobs:
        job["transitions"] = repos.data_jobs.job_transitions(job["job_id"])
    return jobs


@router.post("/api/user/jobs/{job_id}:confirm")
def confirm_job(job_id: str, body: ConfirmBody, request: Request,
                identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    job = _owned_job(request, job_id, identity)
    if job["state"] != DataJobState.CONFIRM_PENDING.value:
        raise HTTPException(status_code=409, detail="not_confirmable")
    if not job["preview_fingerprint"]:
        raise HTTPException(status_code=409, detail="no_preview_fingerprint")
    if job["preview_expires_at"] and job["preview_expires_at"] < utc_now_iso():
        repos.data_jobs.set_job_state(job_id, DataJobState.PREVIEW_EXPIRED,
                                      reason_code="preview_expired", actor=identity.actor)
        repos.requests.finalize_from_job(job["request_id"],
                                         DataJobState.PREVIEW_EXPIRED,
                                         reason_code="preview_expired",
                                         actor=identity.actor)
        raise HTTPException(status_code=409, detail="preview_expired")
    if body.fingerprint != job["preview_fingerprint"]:
        raise HTTPException(status_code=409, detail="fingerprint_mismatch")
    repos.data_jobs.set_confirmed(job_id, body.fingerprint)
    repos.data_jobs.set_job_state(job_id, DataJobState.EXECUTING, actor=identity.actor)
    return {"state": "Executing"}


@router.post("/api/user/jobs/{job_id}:cancel")
def cancel_job(job_id: str, request: Request,
               identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    job = _owned_job(request, job_id, identity)
    if DataJobState(job["state"]) in TERMINAL_DATA_JOB_STATES:
        raise HTTPException(status_code=409, detail="already_terminal")
    adapter = request.app.state.execution_adapter
    try:
        terminate_job(adapter, job)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    repos.data_jobs.set_job_state(job_id, DataJobState.CANCELLED,
                                  reason_code="cancelled_by_user", actor=identity.actor)
    repos.requests.finalize_from_job(job["request_id"], DataJobState.CANCELLED,
                                     reason_code="cancelled_by_user", actor=identity.actor)
    return {"state": "Cancelled"}
