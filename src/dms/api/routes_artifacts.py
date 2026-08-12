import json

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


def _render_log(log, tail):
    """라이브·박제 공통의 표시 상한: tail 줄 수 + MAX_BYTES 바이트 캡.
    None(로그를 얻을 수 없음)은 None 그대로 통과시킨다 -- 빈 문자열("", 정상값인
    빈 로그)과 뭉개면 "모름"이 "비어 있음"으로 둔갑한다(설계 §1-3)."""
    if log is None:
        return None
    if tail is not None:
        log = tail_lines(log, tail)
    if len(log.encode()) > MAX_BYTES:
        log = log.encode()[-MAX_BYTES:].decode("utf-8", errors="replace")
    return log


def _archived_entries(request, job, phase, tail):
    """diag_logs 박제 사본에서 요청 phase 의 항목을 꺼낸다. 없음/불일치는 None.
    깨진 JSON 은 폴백 포기 + 경고 이벤트 -- 깨진 사본으로 응답을 지어내지 않는다
    (설계 §4). get_job 이 SELECT * 라 diag_logs 가 job 행에 이미 실려 있다 --
    추가 쿼리가 없다.

    "깨짐"에는 모양 불일치도 포함한다(entries 가 리스트가 아님·항목이 dict 가
    아님·log 이 문자열도 null 도 아님): 그대로 렌더하려 들면 TypeError 로 500 이
    되어 **라이브 열람까지 같이 죽는다** -- 폴백 실패가 본 기능을 무너뜨리면
    안 된다."""
    raw = job.get("diag_logs")
    if raw is None:
        return None
    try:
        doc = json.loads(raw)
        entries = doc["entries"]
        archived_phase = doc["phase"]
        if not isinstance(entries, list) or not all(
                isinstance(e, dict)
                and (e.get("log") is None or isinstance(e.get("log"), str))
                for e in entries):
            raise TypeError("diag_logs entries shape")
    except (ValueError, TypeError, KeyError):
        request.app.state.repos.observability.record_event(
            component="api", severity="warning", event_type="diag_logs_corrupt",
            message=f"job={job['job_id']}", request_id=job.get("request_id"))
        return None
    if archived_phase != phase:
        # 박제는 실패한 그 phase 하나뿐이다 -- 다른 phase 자리에 내밀면 오도한다.
        return None
    return [{"pod": e.get("pod"), "log": _render_log(e.get("log"), tail),
             "truncated": bool(e.get("truncated"))} for e in entries]


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
        # 미지 prefix(log_not_available)와 조회 계층 실패(poll_failed) -- null
        # (파드 소실)과 409(조회 오류)를 뭉개지 않는다(설계 §4).
        raise HTTPException(status_code=409, detail=e.reason_code)
    # 3-튜플 (pod, log, waiting_reason) 계약(슬라이스 25 Task 2).
    live = [{"pod": pod, "log": _render_log(log, tail), "waiting_reason": wr}
            for pod, log, wr in entries]
    # 슬라이스 25 §2.5: 라이브 우선, 박제 폴백. 폴백 조건은 "빈 목록 또는 전 항목
    # log=None" -- 빈 문자열은 정상값이라 폴백 조건이 아니다(truthy 검사 금지).
    # 종단 잡 로그는 불변이라 두 소스의 내용 충돌은 없다.
    exhausted = not live or all(e["log"] is None for e in live)
    if exhausted:
        archived = _archived_entries(request, job, phase, tail)
        if archived is not None:
            return {"phase": phase, "ref": ref, "source": "archived",
                    "entries": archived}
    return {"phase": phase, "ref": ref, "source": "live", "entries": live}
