from fastapi import APIRouter, Depends, HTTPException, Query, Request
from ..artifact_base import resolve_artifact_base
from ..execution import ExecutionError
from .artifacts import (ArtifactError, MAX_BYTES, PHASES, list_artifacts, read_artifact,
                        strip_scheme, tail_lines)
from .auth import Identity, require_user
from .routes_jobs import _owned_job

router = APIRouter()


def _base(request: Request) -> str:
    # 슬라이스 18: 설정 스냅숏이 아니라 DB 우선 해석(설계 §2.1). 읽기 라우트는 잡
    # 행이 아니라 현재 base 로 경로를 조립한다(설계 §1-5) -- base 는 잠금(§2.3)
    # 탓에 잡이 존재하는 한 바뀌지 않으므로 이 조립은 여전히 안전하다.
    return strip_scheme(resolve_artifact_base(request.app.state.repos.control,
                                              request.app.state.settings))


@router.get("/api/user/jobs/{job_id}/artifacts")
def list_job_artifacts(job_id: str, request: Request,
                       identity: Identity = Depends(require_user)):
    _owned_job(request, job_id, identity)
    try:
        # {"entries": [...], "truncated": bool} — 사용자가 phase 디렉터리 소유자라
        # 항목 수를 묶어야 한다(MAX_ENTRIES). 잘렸는지는 호출자가 알 수 있어야 한다.
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
        if e.reason_code in ("artifact_not_found", "artifact_forbidden"):
            # 봉쇄 실패(탈출 시도)와 단순 미존재는 클라이언트에게 완전히 같아야 한다 —
            # 403/404가 갈리면 팟 안의 임의 절대경로 존재 여부를 캐는 오라클이 된다.
            # (artifact_forbidden은 서버 내부 구분용으로만 남긴다.)
            raise HTTPException(status_code=404, detail="artifact_not_found")
        raise HTTPException(status_code=422, detail=e.reason_code)


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
    # 3-튜플 (pod, log, waiting_reason) 계약(슬라이스 25 Task 2). waiting_reason 의
    # 응답 노출은 Task 6 -- 여기서는 언팩만 맞춰 기존 응답 계약을 유지한다.
    for pod, log, _waiting_reason in entries:
        if log is not None:
            if tail is not None:
                log = tail_lines(log, tail)
            if len(log.encode()) > MAX_BYTES:
                log = log.encode()[-MAX_BYTES:].decode("utf-8", errors="replace")
        out.append({"pod": pod, "log": log})
    return {"phase": phase, "ref": ref, "entries": out}
