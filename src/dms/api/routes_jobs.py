from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES
from ..db import utc_now_iso
from ..execution import ExecutionError
from ..repositories.storages import storage_open_to_users
from ..repositories.sync_pairs import sync_pair_allowed
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
    # 관리자 전용 스토리지(2026-09-30 사용 범위): 미리보기까지 끝난 사용자 잡이 그 사이 관리자
    # 전용으로 바뀐 스토리지를 쓰면 컨펌(= 실행 시작)을 막는다 -- 제출 게이트·planner 만으로는
    # ConfirmPending(최대 preview TTL) 동안 남은 잡이 사용자 손으로 실행된다(리뷰 발견).
    # 완전 비활성은 종전대로 진행 중 잡을 막지 않는다(화면 문구 "진행 중 작업은 그대로").
    if identity.role != "admin":
        for name in (job.get("storage_name"), job.get("source_storage"),
                     job.get("destination_storage")):
            row = repos.storages.get(name) if name else None
            if row is not None and row["enabled"] == 1 and not storage_open_to_users(row):
                raise HTTPException(status_code=403, detail="storage_admin_only")
        # 사용자 sync 허용 쌍(repositories/sync_pairs.py): 컨펌 대기 중 허용이 빠진 사용자 sync 도
        # 실행 시작을 막는다(같은 이유 -- 제출·계획 게이트만으로는 preview TTL 동안 샌다).
        if job["operation"] == "sync" and not sync_pair_allowed(
                repos, job.get("source_storage"), job.get("destination_storage")):
            raise HTTPException(status_code=403, detail="sync_pair_not_allowed")
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
