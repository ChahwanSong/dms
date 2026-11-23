"""아티팩트 base 설정 API(슬라이스 18 설계 §2.5). 전부 admin 전용
(routes_control.py 와 같은 라우터 수준 의존성)."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..artifact_base import (allowlist_reason, normalize_artifact_base,
                             roundtrip_artifact_base, strip_scheme)
from ..artifact_base import LOCAL_FSTYPES
from ..db import iso_plus, utc_now_iso
from ..domain import DomainValidationError
from .auth import Identity, audit_actor, require_admin, require_session_admin
from .base_monitor import GATE_WAITING, fresh_window_seconds, run_api_check

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
    # 요청 삭제의 정리 대기(request_purges, 2026-10-08)도 센다: 행은 지워졌어도 그 잡의 결과 파일은 정리 루프가
    # 지울 때까지 **이 base 아래**에 있다. force 없이 base 가 바뀌면 루프는 옛 base 의 파일을 지우지 않고 남긴다
    # (artifact_left_at_old_base 경고 -- DB 값만 믿고 다른 경로를 지우지 않는다). force 의 의미("옛 산출물을 옛
    # base 에 남김을 수용")는 그대로이고 affected_jobs 에 이 건수도 들어간다.
    jobs = repos.db.query_one("SELECT COUNT(*) AS n FROM data_jobs")["n"]
    return jobs + repos.request_purges.pending_count()


def _controller_check(row, effective) -> dict:
    checked_uri = row.get("artifact_base_check_uri") if row else None
    if checked_uri != effective or not (row or {}).get("artifact_base_check_at"):
        # 컨트롤러가 아직 **이** base 를 검증하지 않았다(막 저장했거나 구버전
        # 컨트롤러) -- null(모름)이지 실패가 아니다(설계 §4). check_uri 대조가
        # 없으면 옛 base 의 성공/실패가 새 base 의 것으로 오독된다.
        return {"pending": True, "ok": None, "reason": None, "checked_at": None, "node_name": None}
    # node_name(2026-11-23): 어느 노드에서 본 결과인지 -- API 파드 관점과 엇갈릴 때 화면이 「노드 X 만 못 본다」를 말한다.
    return {"pending": False, "ok": bool(row["artifact_base_check_ok"]),
            "reason": row["artifact_base_check_reason"],
            "checked_at": row["artifact_base_check_at"],
            "node_name": row.get("artifact_base_check_node")}


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


def _check_path(path: str, settings) -> "str | None":
    """(a) 즉석 검증 = allowlist → 쓰기 왕복. 컨트롤러 홉(artifact_base.
    controller_check_once)과 같은 순서·같은 두 함수라 두 홉의 판정이 갈리지 않는다.
    api/controller 가 root 로 돌아 파일시스템이 더는 경로를 걸러 주지 않으므로
    allowlist(DMS_ARTIFACT_BASE_ALLOWED_PREFIXES)가 그 역할을 대신한다."""
    return (allowlist_reason(path, settings.artifact_base_allowed_prefixes)
            or roundtrip_artifact_base(path))


def _api_check(request: Request, effective: str, *, instant_cache_seconds: "float | None" = None) -> dict:
    """(a) API 파드 관점(2026-11-23 개정). GET 은 더는 쓰기 왕복을 하지 않는다 -- 쓰기는 빈 로컬 디렉터리에서도 통과해
    「진짜 공유 스토리지인가」를 말하지 못하고(사고), 10초 폴링마다 base 에 파일을 만들 이유도 없다(PUT/validate 는 왕복
    그대로). 대신 쓰기 없는 가용성(디렉터리 열기·첫 dirent·최근 잡 디렉터리) + 자세(allowlist·소유자·o+w·g+w/ACL·o+x --
    저장 뒤 바뀐 g+w 를 계속 드러낸다) + 파일시스템 종류. 모니터가 돌고 현재 uri 스냅숏이 있으면 그것(source monitor),
    아니면 runner 로 즉석(source instant -- 쓰기 없음, 같은 single-flight·타임아웃). ok = 가용성 ∧ 자세 없음, reason =
    가용성 사유 우선(옛 화면 호환 -- 한 줄 요약)."""
    state = request.app.state
    monitor = state.base_monitor
    snap = monitor.snapshot()
    if instant_cache_seconds is None and monitor.started and snap is not None and snap["uri"] == effective:
        source = "monitor"
        availability = {"ok": snap["ok"], "reason": snap["reason"],
                        "jobs_checked": snap["jobs_checked"], "jobs_found": snap["jobs_found"]}
        posture, fstype, local_fs = snap["posture"], snap["fstype"], snap["local_fs"]
        checked_at, node_name = snap["checked_at"], snap["node_name"]
    else:
        source = "instant"
        kwargs = {} if instant_cache_seconds is None else {"cache_seconds": instant_cache_seconds}
        res = run_api_check(state, effective, **kwargs)
        availability = res["availability"]
        posture, fstype, local_fs = res["posture"], res["fstype"], res["local_fs"]
        checked_at, node_name = utc_now_iso(), monitor.node_name()
    return {"ok": availability["ok"] is True and posture is None,
            "reason": availability["reason"] or posture,
            "availability": availability, "posture": posture, "source": source,
            "instance": monitor.instance, "node_name": node_name, "checked_at": checked_at,
            "fstype": fstype, "local_fs": local_fs, "interval_seconds": monitor.interval,
            "gate": {"enabled": monitor.enabled and monitor.gate_on and monitor.started,
                     "state": monitor.gate_state(), "at": monitor.ready_at}}


def _desc(text: str) -> tuple:
    # 문자열 내림차순 정렬 키(ISO 시각) -- 코드포인트를 뒤집는다.
    return tuple(-ord(c) for c in text)


def _api_pods(request: Request) -> list[dict]:
    """API 파드별 점검 기록(api_base_checks, 24시간 행). Ready 가 아닌 파드(시작 게이트에 걸린 새 파드)는 Service 밖이라
    이 기록으로만 보인다. fresh = 마지막 활동(checked_at, 없으면 started_at)이 신선 창(주기 3배·최소 180초) 안. 순서:
    이 파드 → 실패·대기 → 최근 확인."""
    state = request.app.state
    monitor = state.base_monitor
    now = utc_now_iso()
    cutoff = iso_plus(now, -fresh_window_seconds(monitor.interval))
    pods = []
    for r in state.repos.api_base_checks.list_recent(now_iso=now):
        last = r["checked_at"] or r["started_at"]
        pods.append({**{k: r[k] for k in ("instance", "node_name", "uri", "ok", "reason", "posture", "fstype",
                                           "gate_state", "started_at", "checked_at", "ready_at")},
                     "local_fs": None if r["fstype"] is None else r["fstype"] in LOCAL_FSTYPES,
                     "fresh": last >= cutoff, "this_pod": r["instance"] == monitor.instance})
    pods.sort(key=lambda p: (not p["this_pod"],
                             not (p["ok"] is False or p["gate_state"] == GATE_WAITING),
                             _desc(p["checked_at"] or p["started_at"] or "")))
    return pods


def _payload(request: Request, *, instant_cache_seconds: "float | None" = None) -> dict:
    repos = request.app.state.repos
    settings = request.app.state.settings
    row = repos.control.control_state()
    db_value = row.get("artifact_base_uri") if row else None
    effective = db_value or settings.artifact_base_uri
    path = strip_scheme(effective)
    return {
        "effective": effective,
        "source": "db" if db_value else "env",
        "db_value": db_value,
        "env_value": settings.artifact_base_uri,
        "locked_by_jobs": _job_count(repos),
        "checks": {
            "api": _api_check(request, effective, instant_cache_seconds=instant_cache_seconds),
            "api_pods": _api_pods(request),
            "controller": _controller_check(row, effective),
            "nodes": _node_checks(repos, settings, path),
        },
    }


@router.get("/api/admin/artifact-base")
def get_artifact_base(request: Request):
    return _payload(request)


@router.get("/api/admin/artifact-base/health")
def get_artifact_base_health(request: Request):
    """포탈 폴링용 가벼운 상태(2026-11-23): 사용량 화면이 「공유 스토리지 접근 불가」를 받은 동안만 15초로 보다가 ok 로
    바뀌면 무거운 사용량 쿼리를 다시 부른다(데이터 쿼리 자체를 폴링하지 않는다). 모니터 스냅숏(메모리)만 읽는다 -- 모니터가
    돌지 않을 때(주기 0 비상구·로컬)만 runner 로 즉석 점검(쓰기 없음·3초 캐시). ok null = 모름."""
    state = request.app.state
    monitor = state.base_monitor
    if monitor.started:
        snap = monitor.snapshot()
        if snap is None:
            return {"ok": None, "reason": None, "checked_at": None, "instance": monitor.instance,
                    "node_name": None}
        return {"ok": snap["ok"], "reason": snap["reason"], "checked_at": snap["checked_at"],
                "instance": monitor.instance, "node_name": snap["node_name"]}
    row = state.repos.control.control_state() or {}
    effective = row.get("artifact_base_uri") or state.settings.artifact_base_uri
    avail = run_api_check(state, effective)["availability"]
    return {"ok": avail["ok"], "reason": avail["reason"], "checked_at": utc_now_iso(),
            "instance": monitor.instance, "node_name": monitor.node_name()}


@router.post("/api/admin/artifact-base:recheck")
def recheck_artifact_base(request: Request):
    """「지금 다시 점검」(2026-11-23). 설정 변경이 아니라 require_admin(라우터)만. 모니터가 돌면 지금 시작하는 회차를 최대
    12초 기다린 뒤 GET 과 같은 payload, 꺼져 있으면 캐시 없는 즉석 점검으로 같은 payload."""
    monitor = request.app.state.base_monitor
    if monitor.started:
        monitor.check_now()
        return _payload(request)
    return _payload(request, instant_cache_seconds=0)


@router.get("/api/admin/artifact-base/history")
def get_artifact_base_history(request: Request,
                              limit: int = Query(default=10, ge=1, le=50)):
    """변경 이력(슬라이스 38). 강제 변경은 affected_jobs 건의 아티팩트·로그
    열람을 깨는 결정이라(설계 §2.3) "누가 언제 무엇→무엇, 강제였나"가 화면에
    있어야 한다 -- control-state/history 와 같은 계약(diff 는 화면이 계산)."""
    return request.app.state.repos.control.artifact_base_history(limit=limit)


@router.post("/api/admin/artifact-base/validate")
def validate_artifact_base(body: ValidateBody, request: Request):
    # 저장 없는 (a) 검증(설계 §2.5) -- UI 의 사전 확인용.
    try:
        normalized = normalize_artifact_base(body.uri)
    except DomainValidationError as e:
        raise HTTPException(status_code=422, detail=e.reason_code)
    reason = _check_path(strip_scheme(normalized), request.app.state.settings)
    if reason is not None:
        raise HTTPException(status_code=422, detail=reason)
    return {"normalized": normalized, "ok": True}


@router.put("/api/admin/artifact-base")
def put_artifact_base(body: ArtifactBaseBody, request: Request,
                      identity: Identity = Depends(require_session_admin)):
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
    reason = _check_path(strip_scheme(normalized), request.app.state.settings)
    if reason is not None:
        # 즉석 검증 실패면 저장하지 않는다(설계 §2.4a) -- 422 가 곧 "저장 안 됨".
        raise HTTPException(status_code=422, detail=reason)
    repos.control.set_artifact_base(normalized, actor=audit_actor(identity),
                                    forced=bool(affected and body.force),
                                    affected_jobs=affected)
    # 새 base 를 API 모니터가 바로 보게(다음 주기까지 옛 base 스냅숏으로 남지 않게 -- 그동안 화면은 instant 로 본다).
    request.app.state.base_monitor.kick()
    return _payload(request)
