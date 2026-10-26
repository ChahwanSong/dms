from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from .. import k8s_access
from ..domain import DataJobState, DomainValidationError, TERMINAL_DATA_JOB_STATES
from ..db import utc_now_iso
from ..execution import ExecutionError
from ..identity import PRIVILEGE_REQUESTED, privilege_policy, root_allowed
from ..repositories.node_exclusions import blocked_nodes
from ..repositories.storages import storage_open_to_users
from ..repositories.sync_pairs import sync_pair_allowed
from .auth import Identity, require_user
from .cancel import sweep_after_cancel, terminate_job

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
    # 컨펌은 사람의 승인이다(2026-10-26 리뷰): k8s 신원 모드에선 모든 단건 잡이 root 라, 공유 토큰(모든 노드 에이전트 보유,
    # role admin -- _owned_job 을 통과한다)으로 컨펌할 수 있으면 남의 미리보기를 사람 확인 없이 uid 0 실행으로 넘긴다.
    # 요청 행의 auth_method 재검사(아래 root_allowed)는 **요청자**의 인증만 본다 -- 컨펌하는 쪽은 여기서 막는다.
    if identity.auth != "session":
        raise HTTPException(status_code=403, detail="confirm_session_required")
    repos = request.app.state.repos
    job = _owned_job(request, job_id, identity)
    # 배치 항목은 배치 단위로만 확인한다(2026-10-07): 배치 자식은 배치 행의 세션 인증·요청자를 물려받아 root 로
    # 돈다. 배치 확인(:confirm)은 특권 3중 게이트를 요구하는데, 여기서 자식 하나를 컨펌할 수 있으면 공유 토큰(role
    # admin)이나 allowlist 밖 관리자가 그 게이트를 우회해 root 실행을 시작한다. 배치 자식의 실행 시작은 배치 확인 →
    # orchestrator 자동 컨펌 경로 하나뿐이다.
    req = repos.requests.get(job["request_id"])
    if req is not None and req.get("batch_id"):
        raise HTTPException(status_code=409, detail="batch_child_confirm_via_batch")
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
    # 노드 배치 제외·k8s 스케줄 불가(repositories/node_exclusions.py): 미리보기 뒤 후보 노드가 막혔으면 실행을 시작하지
    # 않고 **이 자리에서 종단**한다 -- ConfirmPending 은 stepper 가 건드리지 않아, 409 만 주면 잡이 미리보기 유효기간
    # (최대 24h) 동안 컨펌 불가인 채 남는다. preview_expired 분기와 같은 모양(종단 + 요청 화해 + 409). 관리자도 예외 없다.
    wp = job["worker_pool"] if isinstance(job.get("worker_pool"), dict) else {}
    blocked = blocked_nodes(repos, wp.get("candidates"))
    if blocked:
        repos.data_jobs.set_job_state(job_id, DataJobState.REJECTED,
                                      reason_code="node_excluded_at_step", actor=identity.actor)
        repos.requests.finalize_from_job(job["request_id"], DataJobState.REJECTED,
                                         reason_code="node_excluded_at_step", actor=identity.actor)
        raise HTTPException(status_code=409, detail="node_excluded")
    # root 실행 자격을 그 사이 잃었으면(2026-10-12 -- 계정 화면 「자격 빼기」·강등, env 목록에서 빠짐) root 로 계획돼 컨펌을
    # 기다리던 단건 잡을 실행으로 넘기지 않는다: 신원은 계획 때 worker_pool 에 얼어 stepper 는 근거(privilege_policy)만
    # 다시 볼 뿐 자격은 보지 않는다 -- 여기서 안 막으면 회수된 관리자가 미리보기 유효기간(최대 24h) 동안 uid 0 실행을
    # 시작할 수 있다(리뷰). 배치 자식은 위에서 배치 확인으로 보냈다(_require_batch_privilege·auto_confirm_hold 가 같은 집합을
    # 다시 본다). 모양은 node_excluded 와 같다(이 자리에서 종단 + 요청 화해 + 409).
    # k8s 신원 모드(2026-10-25 설계 D3): 같은 자리에서 같은 술어 identity.root_allowed 를 본다 -- 「세션 인증 요청인가」(모든
    # 잡이 root 라 특권 목록으로 보면 일반 사용자의 sync·rm 이 전부 여기서 끊겼다 -- 리뷰).
    ident = wp.get("identity") if isinstance(wp.get("identity"), dict) else {}
    settings = request.app.state.settings
    if ident.get("privileged") is True and privilege_policy(req) == PRIVILEGE_REQUESTED:
        if not root_allowed(allow_privileged=settings.allow_privileged_requesters,
                            session_authenticated=(req.get("auth_method") == "session")):
            repos.data_jobs.set_job_state(job_id, DataJobState.REJECTED,
                                          reason_code="privileged_not_authorized", actor=identity.actor)
            repos.requests.finalize_from_job(job["request_id"], DataJobState.REJECTED,
                                             reason_code="privileged_not_authorized", actor=identity.actor)
            raise HTTPException(status_code=409, detail="privileged_not_authorized")
    # PV 주소·k8s 맵핑 재확인(설계 D13 -- planner 3a 와 같은 함수 k8s_access.recheck_request): 미리보기 뒤 PVC 가 다시 만들어져
    # 경로가 바뀌었거나(pv_moved)·사라졌거나(pv_not_found)·권한이 회수됐으면(k8s_access_denied) 실행을 시작하지 않는다.
    # ConfirmPending 은 미리보기 유효기간(최대 24h) 동안 남으므로 컨펌이 회수를 반영하는 마지막 관문이다. 확인한 경로와
    # 실행할 경로가 갈라진 잡은 다시 컨펌할 수 없으니 privileged_not_authorized 와 같은 모양(이 자리에서 종단 + 요청 화해
    # + 409). 컨펌하는 사람이 관리자여도 판정은 요청 행(요청자) 기준이다.
    try:
        k8s_access.recheck_request(repos, req, settings=settings)
    except DomainValidationError as exc:
        repos.data_jobs.set_job_state(job_id, DataJobState.REJECTED, reason_code=exc.reason_code,
                                      actor=identity.actor)
        repos.requests.finalize_from_job(job["request_id"], DataJobState.REJECTED, reason_code=exc.reason_code,
                                         actor=identity.actor)
        raise HTTPException(status_code=409, detail=exc.reason_code)
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
        # 양쪽이 PV 주소면 면제(설계 D14 -- 제출·planner 3b 와 같은 술어; 위 재확인이 그 pv 블록을 방금 봤다).
        if (job["operation"] == "sync"
                and not k8s_access.pair_gate_exempt((req or {}).get("payload"))
                and not sync_pair_allowed(repos, job.get("source_storage"), job.get("destination_storage"))):
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
    # 커밋 뒤 회수 + 정직한 응답(2026-10-12, cancel.sweep_after_cancel): 취소 도중 제출된 단계를 끝내고, stepper 가 먼저
    # 종단시켰으면(종단 가드가 Cancelled 쓰기를 삼켰다) 실제 결과로 요청을 맞추고 409 -- 예전엔 잡·요청이 Succeeded 인데도
    # 200 Cancelled 를 돌려줬다.
    state = sweep_after_cancel(repos, request.app.state.execution_adapter, job)
    if state is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    if state != DataJobState.CANCELLED.value:
        repos.requests.finalize_from_job(job["request_id"], DataJobState(state),
                                         reason_code="orphan_recovery", actor=identity.actor)
        raise HTTPException(status_code=409, detail="already_terminal")
    repos.requests.finalize_from_job(job["request_id"], DataJobState.CANCELLED,
                                     reason_code="cancelled_by_user", actor=identity.actor)
    return {"state": "Cancelled"}
