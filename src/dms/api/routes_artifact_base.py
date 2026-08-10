"""아티팩트 base 설정 API(슬라이스 18 설계 §2.5). 전부 admin 전용
(routes_control.py 와 같은 라우터 수준 의존성)."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..artifact_base import (normalize_artifact_base, roundtrip_artifact_base,
                             strip_scheme)
from ..domain import DomainValidationError
from .auth import Identity, audit_actor, require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


class ArtifactBaseBody(BaseModel):
    uri: str
    force: bool = False


class ValidateBody(BaseModel):
    uri: str


def _job_count(repos) -> int:
    # 잠금 카운트(설계 §2.3): artifact_uri IS NOT NULL 로 좁히지 **않는다**.
    # 실패·타임아웃·취소·Rejected 잡은 그 컬럼이 NULL 이지만(stepper 는 성공
    # 경로에서만 기록한다) 디스크에는 러너가 쓴 stdout/stderr 가 있고, 그것이
    # 진단의 **유일한 사본**이다. NOT NULL 로 좁히면 정확히 그 잡들 -- 가장
    # 진단이 필요한 잡들 -- 의 증거를 버린다. 이 잠금은 §1-7(재시작 후 in-flight
    # summary 유실)과 §1-6(COALESCE 덮어쓰기로 preview 위치 유실)도 구조적으로
    # 닫는다: 잡이 존재하는 한 base 가 안 바뀌므로 두 함정의 전제가 성립하지
    # 않는다.
    return repos.db.query_one("SELECT COUNT(*) AS n FROM data_jobs")["n"]


def _controller_check(row, effective) -> dict:
    checked_uri = row.get("artifact_base_check_uri") if row else None
    if checked_uri != effective or not (row or {}).get("artifact_base_check_at"):
        # 컨트롤러가 아직 **이** base 를 검증하지 않았다(막 저장했거나 구버전
        # 컨트롤러) -- null(모름)이지 실패가 아니다(설계 §4). check_uri 대조가
        # 없으면 옛 base 의 성공/실패가 새 base 의 것으로 오독된다.
        return {"pending": True, "ok": None, "reason": None, "checked_at": None}
    return {"pending": False, "ok": bool(row["artifact_base_check_ok"]),
            "reason": row["artifact_base_check_reason"],
            "checked_at": row["artifact_base_check_at"]}


def _node_checks(repos, settings, effective_path) -> list[dict]:
    nodes = []
    for node in repos.agents.list_nodes(
            stale_seconds=settings.agent_report_stale_seconds):
        ab = (node["report"] or {}).get("artifact_base")
        # 아직 새 base 를 프로브하지 않은 노드(리포트에 필드가 없거나 다른 경로를
        # 프로브)는 "확인 대기 중" -- 실패와 뭉개지 않는다(설계 §4). 판정은
        # probe_mounts 식 status 가 아니라 exists/writable 필드 직접이다
        # (§1-12: status 는 writable 을 반영하지 않는다).
        pending = (not node["fresh"] or not isinstance(ab, dict)
                   or ab.get("path") != effective_path)
        nodes.append({
            "node_name": node["node_name"], "reported_at": node["reported_at"],
            "fresh": node["fresh"], "pending": pending,
            "exists": None if pending else bool(ab.get("exists")),
            "writable": None if pending else bool(ab.get("writable")),
        })
    return nodes


def _payload(request: Request) -> dict:
    repos = request.app.state.repos
    settings = request.app.state.settings
    row = repos.control.control_state()
    db_value = row.get("artifact_base_uri") if row else None
    effective = db_value or settings.artifact_base_uri
    path = strip_scheme(effective)
    # (a) API 즉석 홉은 GET 에서도 실파일 왕복으로 산다 -- 저장 시점 스냅숏이
    # 아니라 "지금 이 파드에서 되는가"가 화면이 답할 질문이다(설계 §2.4).
    # 왕복은 임시 파일 1개라 10s 폴링에도 무해하다.
    api_reason = roundtrip_artifact_base(path)
    return {
        "effective": effective,
        "source": "db" if db_value else "env",
        "db_value": db_value,
        "env_value": settings.artifact_base_uri,
        "locked_by_jobs": _job_count(repos),
        "checks": {
            "api": {"ok": api_reason is None, "reason": api_reason},
            "controller": _controller_check(row, effective),
            "nodes": _node_checks(repos, settings, path),
        },
    }


@router.get("/api/admin/artifact-base")
def get_artifact_base(request: Request):
    return _payload(request)


@router.post("/api/admin/artifact-base/validate")
def validate_artifact_base(body: ValidateBody, request: Request):
    # 저장 없는 (a) 검증(설계 §2.5) -- UI 의 사전 확인용.
    try:
        normalized = normalize_artifact_base(body.uri)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    reason = roundtrip_artifact_base(strip_scheme(normalized))
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    return {"normalized": normalized, "ok": True}


@router.put("/api/admin/artifact-base")
def put_artifact_base(body: ArtifactBaseBody, request: Request,
                      identity: Identity = Depends(require_admin)):
    repos = request.app.state.repos
    # 순서(설계 §2.5): 정규화 -> 잠금 -> 즉석 검증 -> 저장+감사. 잠금이 검증보다
    # 먼저다 -- 잠긴 상태에서 "경로가 없다"부터 보이면 운영자가 디렉터리를 만든
    # 다음에야 잠금을 알게 된다.
    try:
        normalized = normalize_artifact_base(body.uri)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    affected = _job_count(repos)
    if affected and not body.force:
        # detail 은 문자열 코드 하나만 -- 프론트 request() 는 dict detail 을
        # http_409 로 접는다(frontend/src/lib/api.ts). N 건은 GET 의
        # locked_by_jobs 가 이미 화면에 나른다.
        raise HTTPException(status_code=409, detail="artifact_base_locked")
    reason = roundtrip_artifact_base(strip_scheme(normalized))
    if reason is not None:
        # 즉석 검증 실패면 저장하지 않는다(설계 §2.4a) -- 422 가 곧 "저장 안 됨".
        raise HTTPException(status_code=422, detail=reason)
    repos.control.set_artifact_base(normalized, actor=audit_actor(identity),
                                    forced=bool(affected and body.force),
                                    affected_jobs=affected)
    return _payload(request)
