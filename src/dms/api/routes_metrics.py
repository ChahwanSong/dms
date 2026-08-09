"""대시보드 메트릭 API(슬라이스 14). 전부 admin 전용 · 읽기 전용 -- 뮤테이션이
없으므로 감사 로그도 없다(설계 §3). 수치 조립은 저장소(앱측 JSON 파싱)와
metrics_series 순수 함수가 하고, 이 계층은 기간 클램프와 응답 조립만 한다."""
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..db import iso_plus, utc_now_iso
from ..metrics_series import (bucket_chars_for, build_node_points,
                              clamp_window_hours, duration_histogram)
from .auth import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])


def _window(request: Request, window: int) -> "tuple[int, str, str]":
    settings = request.app.state.settings
    # 잡 통계도 같은 상한을 쓴다 -- data_jobs는 purge되지 않지만 "대시보드 창은
    # 최대 30일"이라는 하나의 규칙이 두 개의 규칙보다 낫다(설계 §3 기간 규약).
    hours = clamp_window_hours(
        window, retention_days=settings.agent_report_retention_days)
    end = utc_now_iso()
    return hours, iso_plus(end, -hours * 3600), end


@router.get("/api/admin/metrics/nodes")
def metrics_nodes(request: Request, window: int = Query(default=24)):
    # ge=1을 두지 않는다 -- 기간은 접지 거부하지 않는다(§6-2). 0·음수는 여기서
    # 422로 걸지 않고 clamp_window_hours가 하한(1)으로, 과대값은 상한(720)으로
    # 접게 둔다(같은 철학, 한 권위). int 타입은 유지 -- 비정수는 파싱 오류로 422.
    repos = request.app.state.repos
    settings = request.app.state.settings
    hours, start, end = _window(request, window)
    nodes = []
    # 노드 목록은 agent_nodes(노드당 1행)에서 온다 -- 창에 시계열이 비어도 노드가
    # 목록에서 사라지면 신선도(마지막 리포트 나이) 정보를 잃는다.
    for node in repos.agents.list_nodes(
            stale_seconds=settings.agent_report_stale_seconds):
        samples = repos.metrics.node_series(node["node_name"], start=start, end=end)
        nodes.append({"node_name": node["node_name"],
                      "reported_at": node["reported_at"], "fresh": node["fresh"],
                      "points": build_node_points(samples)})
    return {"window_hours": hours, "start": start, "end": end, "nodes": nodes}


@router.get("/api/admin/metrics/jobs")
def metrics_jobs(request: Request, window: int = Query(default=24)):
    # nodes와 같은 규칙 -- ge=1 없이 clamp_window_hours가 상·하한을 접는다(§6-2).
    repos = request.app.state.repos
    hours, start, end = _window(request, window)
    chars = bucket_chars_for(hours)
    stats = repos.metrics.job_stats(start=start, end=end, bucket_chars=chars)
    # 원자료(초 목록)는 응답에 싣지 않는다 -- 프론트가 필요로 하는 것은 분포뿐이고,
    # 창이 크면 행 수만큼 커진다.
    stats["duration_histogram"] = duration_histogram(stats.pop("duration_seconds"))
    stats["window_hours"] = hours
    stats["bucket"] = "hour" if chars == 13 else "day"
    return stats


@router.get("/api/admin/requests/{request_id}/events")
def request_events(request_id: str, request: Request,
                   limit: int = Query(default=100, ge=1, le=1000)):
    # 설계 §2.5: 새 로직 없는 얇은 래퍼. events_for_request는 요청 상세 응답 안에만
    # 있어 대시보드 드릴다운이 요청 전체를 다시 받아야 했다 -- admin 게이트 뒤로만
    # 단독 노출한다.
    repos = request.app.state.repos
    if repos.requests.get(request_id) is None:
        raise HTTPException(status_code=404, detail="request_not_found")
    return {"request_id": request_id,
            "events": repos.observability.events_for_request(request_id, limit=limit)}
