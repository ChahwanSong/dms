import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from ..artifact_base import resolve_artifact_base
from ..domain import (
    DataJobState, DomainValidationError, Operation, PRIORITIES, RequestState,
    TERMINAL_DATA_JOB_STATES, TERMINAL_REQUEST_STATES, build_data_payload,
    resolve_priority, validate_owner_username,
)
from ..execution import ExecutionError
from .artifacts import ArtifactError, read_artifact, strip_scheme
from .auth import Identity, require_admin, require_user
from .cancel import terminate_job
from .routes_jobs import _owned_request
# 모양 투영은 scan_path_stats 와 **한 벌**을 공유한다(routes_scan_paths.py:19-73 의
# 「왜」: 키 화이트리스트만으로는 리포트 값 안의 경로 유출을 못 막는다 — 숫자와
# 구간 라벨만 통과시킨다). 두 벌이 되면 한쪽만 고치는 드리프트가 생긴다.
from .routes_scan_paths import _buckets, _is_number, _numbers, _time_histograms

router = APIRouter()


def reject_when_maintenance(request: Request) -> None:
    # 유지보수 창에는 신규 유입을 막는다 (진행 중인 잡은 건드리지 않는다 — 그건 drain의 몫).
    # 관리자도 예외가 아니다. 콘솔의 control-state PUT은 제출 경로가 아니라 잠기지 않는다.
    state = request.app.state.repos.control.control_state()
    if state and state["maintenance"]:
        raise HTTPException(status_code=503, detail="maintenance_mode")


class RequestBody(BaseModel):
    operation: str
    storage: str | None = None
    source_storage: str | None = None
    destination_storage: str | None = None
    target: str | None = None
    source: str | None = None
    destination: str | None = None
    options: dict = {}
    priority: str | None = None
    owner_username: str | None = None


def _require(value: str | None, reason: str) -> str:
    if not value:
        raise DomainValidationError(reason, "required field missing")
    return value


def _validated_payload(body: RequestBody, priority: str) -> tuple[dict, str]:
    op = Operation(body.operation)
    if priority not in PRIORITIES:
        raise DomainValidationError("invalid_priority", priority)
    if body.owner_username is not None:
        validate_owner_username(body.owner_username)
    # storage 필드 존재 검증은 여기서 구체적 reason_code로 먼저 수행한다
    # (build_data_payload는 sync에 대해 "missing_storage" 하나로 뭉뚱그리므로,
    #  source/destination을 구분하는 기존 API 계약(422 reason_code)을 유지하려면
    #  존재 체크는 라우트에서, 옵션 검증+경로 검증+fingerprint+resource_key는
    #  build_data_payload에 위임한다 — build_data_payload가 이미 validate_options를
    #  수행하고 그 결과를 payload["options"]에 담아 반환하므로 여기서 다시
    #  validate_options를 호출하지 않는다).
    if op is Operation.SYNC:
        src_storage = _require(body.source_storage, "missing_source_storage")
        dst_storage = _require(body.destination_storage, "missing_destination_storage")
        payload, key = build_data_payload(
            "sync", source_storage=src_storage, source=body.source,
            destination_storage=dst_storage, destination=body.destination,
            options=body.options)
    elif op is Operation.RM:
        storage = _require(body.storage, "missing_storage")
        payload, key = build_data_payload(
            "rm", storage=storage, target=body.target, options=body.options)
    else:
        storage = _require(body.storage, "missing_storage")
        payload, key = build_data_payload(
            "scan", storage=storage, target=body.target, options=body.options)
    payload["owner_username"] = body.owner_username
    return payload, key


@router.post("/api/user/requests", status_code=202)
def submit(body: RequestBody, request: Request,
           identity: Identity = Depends(require_user)):
    reject_when_maintenance(request)
    # scan 제출은 관리자 전용이다 (설계 결정 레코드) — /admin/scan이 admin 라우트라는
    # 사실만으로는 강제되지 않으므로 여기서 명시적으로 게이트한다.
    # 원시 문자열로 비교한다 — Operation(...)은 알 수 없는 값에 ValueError를 던지는데
    # 이 지점은 그것을 422로 바꿔주는 try 블록 밖이라 500이 되어 버린다.
    if body.operation == Operation.SCAN.value and identity.role != "admin":
        raise HTTPException(status_code=403, detail="scan_admin_only")
    # 특권 게이트 (스펙 §5): owner_username이 요청자와 다르면 특권 의도 → 인가 필요
    owner = body.owner_username
    if owner is not None and owner != identity.actor:
        settings = request.app.state.settings
        authorized = (identity.role == "admin"
                      and settings.allow_privileged_requesters
                      and identity.actor in settings.privileged_requesters)
        if not authorized:
            raise HTTPException(status_code=403, detail="privileged_not_authorized")

    repos = request.app.state.repos
    priority = resolve_priority(repos, body.operation, body.priority)
    try:
        payload, resource_key = _validated_payload(body, priority)
    except (DomainValidationError, ValueError) as e:
        reason = getattr(e, "reason_code", "invalid_operation")
        raise HTTPException(status_code=422, detail=reason)
    rid = repos.requests.create(
        operation=body.operation, requester_id=identity.actor, actor=identity.actor,
        resource_key=resource_key, payload=payload, priority=priority,
        # 특권 승격을 session 요청에만 허용하기 위해 인증 방식을 요청에 실어 둔다
        # (planner 가 plan 시점에 읽는다, 설계 §2.2-2).
        auth_method=identity.auth)
    return {"request_id": rid, "state": "Pending"}


@router.get("/api/user/requests")
def list_requests(request: Request, identity: Identity = Depends(require_user),
                  # 대시보드 「최근 작업」이 200을 요청한다. 서버가 1..200으로 캡한다
                  # -- 무제한이면 전량 SELECT 가 화면 하나에 끌려 나온다. 범위 밖은
                  # FastAPI 기본 422(이 라우터의 도메인 422 는 detail=사유코드지만,
                  # 쿼리 파라미터 검증은 프레임워크 몫이라 굳이 흉내내지 않는다).
                  limit: int = Query(50, ge=1, le=200)):
    requester = None if identity.role == "admin" else identity.actor
    return request.app.state.repos.requests.list(requester_id=requester, limit=limit)


@router.get("/api/user/requests/{request_id}")
def get_request(request_id: str, request: Request,
                identity: Identity = Depends(require_user)):
    repo = request.app.state.repos.requests
    row = repo.get(request_id)
    if row is None or (identity.role != "admin"
                       and row["requester_id"] != identity.actor):
        raise HTTPException(status_code=404, detail="request_not_found")
    row["transitions"] = repo.transitions(request_id)
    # events_for_request 기본 limit(100)이 조용히 잘리면 운영자가 눈치채지 못한다 --
    # 스택된 요청이 매 컨트롤러 틱마다 같은 예외로 실패를 반복하면 100건을 넘기는 것도
    # 현실적이다(plan_error/step_error는 정확히 이런 루프에서 기록된다). 표시 상한보다
    # 하나 더 가져와 잘림 여부를 판별한다. events_for_request는 이제 최신 N건을
    # 오름차순으로 돌려주므로(오래된 쪽을 버린다), 잘렸을 때 버릴 것도 리스트 앞쪽
    # (가장 오래된 한 건)이다 -- events[-display_limit:]로 뒤에서부터 자른다.
    display_limit = 100
    events = request.app.state.repos.observability.events_for_request(
        request_id, limit=display_limit + 1)
    row["events_truncated"] = len(events) > display_limit
    row["events"] = events[-display_limit:]
    return row


@router.get("/api/admin/requests/{request_id}/scan-stats")
def request_scan_stats(request_id: str, request: Request,
                       identity: Identity = Depends(require_admin)):
    """이 요청의 성공 scan 잡 리포트 1건을 모양 투영해 서빙한다 — scan_path_stats
    와 같은 단일 리포트 계약(합산 없음. 배치 합산 라우트는 제거됐다 — 사용자 결정:
    온도는 배치 전체가 아니라 항목별로 본다). 소비자는 배치 항목 expand 지만
    경로가 요청 단위라 단건 요청 상세(RequestDetail)에도 배선만으로 붙는다.

    리포트가 애초에 없는 경우는 전부 404 no_scan_report 하나다: 비 scan 요청
    (dscan 리포트가 존재할 수 없다)·성공 잡 없음·파일 부재·파싱 불가 — 화면엔
    같은 사실("보여줄 리포트 없음")이다. 기존 no_covering_scan(경로 커버 탐색)·
    invalid_batch_operation(배치 검증)은 다른 사실을 말하는 코드라 재사용하지
    않는다. truncated 만 503 scan_report_too_large — 리포트가 "있는데 못 읽는"
    상태는 운영자가 고칠 수 있어야 한다(scan_path_stats 선례)."""
    repos = request.app.state.repos
    row = repos.requests.get(request_id)
    if row is None:
        raise HTTPException(status_code=404, detail="request_not_found")
    if row["operation"] != Operation.SCAN.value:
        raise HTTPException(status_code=404, detail="no_scan_report")
    job = next((j for j in repos.data_jobs.list_jobs(request_id=request_id)
                if j["state"] == DataJobState.SUCCEEDED.value), None)
    if job is None:
        raise HTTPException(status_code=404, detail="no_scan_report")
    # 슬라이스 18: DB 우선 해석(설계 §2.1) -- routes_artifacts._base 와 같은 이유.
    base = strip_scheme(resolve_artifact_base(repos.control,
                                              request.app.state.settings))
    try:
        f = read_artifact(base, job["job_id"], "execution", "dscan-report.json")
    except ArtifactError:
        raise HTTPException(status_code=404, detail="no_scan_report")
    if f["truncated"]:
        raise HTTPException(status_code=503, detail="scan_report_too_large")
    try:
        report = json.loads(f["content"])
    except ValueError:
        report = None
    if not isinstance(report, dict):
        # 파싱 불가·비 dict(null·[]·"x" 도 유효한 JSON)는 쓸 수 있는 리포트가
        # 없다는 같은 사실이다.
        raise HTTPException(status_code=404, detail="no_scan_report")
    epoch = report.get("generated_at_epoch")
    broken_total = report.get("broken_paths_total")
    broken_limit = report.get("broken_paths_limit")
    return {
        "summary": _numbers(report.get("summary")),
        "file_size_histogram": _buckets(report.get("file_size_histogram")),
        "time_histograms": _time_histograms(report.get("time_histograms")),
        "generated_at_epoch": epoch if _is_number(epoch) else None,
        # 숫자만 통과(모양 투영: 문자열이 오면 경로일 수 있다). 구형 리포트(키
        # 부재)는 None(미기록) — null≠0: 0 은 "파손 없음"의 정상값이다.
        "broken_paths_total": broken_total if _is_number(broken_total) else None,
        "broken_paths_limit": broken_limit if _is_number(broken_limit) else None,
    }


@router.post("/api/user/requests/{request_id}:cancel")
def cancel_request(request_id: str, request: Request,
                   identity: Identity = Depends(require_user)):
    repos = request.app.state.repos
    req = _owned_request(request, request_id, identity)
    if RequestState(req["state"]) in TERMINAL_REQUEST_STATES:
        raise HTTPException(status_code=409, detail="already_terminal")
    # planner 경쟁: 요청을 종결하기 전에 잡을 먼저 조회·종료해야 고아가 남지 않는다.
    adapter = request.app.state.execution_adapter
    jobs = repos.data_jobs.list_jobs(request_id=request_id)
    # 잡은 이미 전부 종단인데 요청만 아직 비종단인 창이 있다 — stepper의 finalize가
    # 누락된 고아(data_jobs.terminal_jobs_with_live_request()가 복구하려는 바로 그 상태,
    # 컨트롤러 크래시 후엔 재시작까지 지속된다). 여기서 취소를 기록하면 실제 결과
    # (rm이면 이미 끝난 삭제!)를 Cancelled로 덮어쓰는 거짓 취소가 되고, 결과 행도 없이
    # 요청이 종단이 되어 고아 리컨실러의 시야에서도 사라진다. 취소 대신 실제 결과로
    # 화해시키고 409를 돌려준다.
    if jobs and all(DataJobState(j["state"]) in TERMINAL_DATA_JOB_STATES for j in jobs):
        for job in jobs:
            repos.requests.finalize_from_job(
                request_id, DataJobState(job["state"]),
                reason_code="orphan_recovery", actor=identity.actor)  # 멱등 — 첫 잡이 이긴다
        raise HTTPException(status_code=409, detail="already_terminal")
    try:
        for job in jobs:
            terminate_job(adapter, job)
    except ExecutionError:
        raise HTTPException(status_code=500, detail="cancel_failed")
    for job in jobs:
        repos.data_jobs.set_job_state(job["job_id"], DataJobState.CANCELLED,
                                      reason_code="cancelled_by_user", actor=identity.actor)
    # set_state가 아니라 finalize_from_job — 다른 모든 종단 전이와 동일하게 results 행을
    # 남긴다(set_state는 상태만 바꾸고 결과를 기록하지 않는다).
    repos.requests.finalize_from_job(request_id, DataJobState.CANCELLED,
                                     reason_code="cancelled_by_user", actor=identity.actor)
    return {"state": "Cancelled"}
