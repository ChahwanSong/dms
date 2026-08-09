"""대시보드 메트릭 API(슬라이스 14). 전부 admin 전용 · 읽기 전용 -- 뮤테이션이
없으므로 감사 로그도 없다(설계 §3). 수치 조립은 저장소(앱측 JSON 파싱)와
metrics_series 순수 함수가 하고, 이 계층은 기간 클램프와 응답 조립만 한다."""
import concurrent.futures
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..db import iso_plus, utc_now_iso
from ..metrics_series import (bucket_chars_for, build_node_points,
                              clamp_window_hours, duration_histogram)
from ..repositories.releases import COMPONENTS, ROLLOUT_ORDER
from ..rollout_status import assess_daemonset, assess_deployment
from .auth import require_admin

logger = logging.getLogger(__name__)

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


@router.get("/api/admin/metrics/infra")
def metrics_infra(request: Request):
    """컴포넌트 3종의 이미지 + N/N ready + 롤아웃 판정(설계 §2.4). targets가
    이미지만 남기고 버리던 observe()의 카운트와 assess_* 판정을 통과시킨다 --
    레지스트리를 건드리지 않으므로 targets보다 싸고(5s 폴링 가능), 슬라이스 13이
    api Role에 부여한 apps get 권한을 그대로 재사용한다(RBAC 변경 없음).

    observe()는 컴포넌트당 1회 k8s GET이고 각 호출에 ROLLOUT_REQUEST_TIMEOUT_SECONDS
    (=10s)가 걸려 있다 -- 셋을 순차로 돌리면 apiserver 지연 시 최악 3×10=30초다.
    프론트가 5s로 폴링할 예정이라 요청이 쌓이고, 이 라우트는 동기 def(=Starlette
    threadpool에서 실행)라 매달린 요청이 threadpool을 고갈시켜 다른 admin 엔드포인트
    까지 막는다. 그래서 세 observe를 ThreadPoolExecutor로 동시에 던져 최악 지연을
    1×10초로 줄인다 -- 각 스레드의 요청 자체 타임아웃(슬라이스 13)이 무한 매달림을
    막으므로 여기서 별도 타임아웃을 더 걸지 않는다."""
    runner = request.app.state.rollout_runner
    # submit는 즉시 반환하고 실제 대기는 result()에서 일어난다. 결과를 component로
    # 키한 dict에 담아, 완료 순서(병렬이라 뒤섞인다)와 무관하게 아래 응답 루프가
    # ROLLOUT_ORDER를 그대로 돌게 한다 -- 병렬화가 응답 순서를 흩뜨리지 않는다.
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(ROLLOUT_ORDER)) as pool:
        futures = {
            component: pool.submit(
                runner.observe, kind=COMPONENTS[component]["kind"],
                name=COMPONENTS[component]["workload"])
            for component in ROLLOUT_ORDER}

    components = []
    for component in ROLLOUT_ORDER:
        spec = COMPONENTS[component]
        entry = {"component": component, "kind": spec["kind"],
                 "workload": spec["workload"], "image": None, "ready": None,
                 "desired": None, "verdict": None, "detail": None}
        try:
            # future.result()는 워커가 던진 예외(observe는 ExecutionError로 감싼다)를
            # 재던진다 -- 컴포넌트별 try/except로 잡아 그 하나만 null 강등하고 나머지
            # 둘의 결과는 그대로 쓴다. 한 워크로드 읽기 실패가 화면 전체를 죽이지 않는다.
            obs = futures[component].result()
        except Exception as exc:
            # 읽기 실패는 그 컴포넌트만 null 강등(슬라이스 13 규약). pod_briefs와 같은
            # best-effort 실패이므로 흔적을 남긴다 -- 무로그면 SA 권한/워크로드명 오타가
            # 조용히 전 컴포넌트를 null로 만들어 대시보드 자체 버그를 못 잡는다.
            logger.warning("metrics/infra observe failed component=%s: %s",
                           component, exc)
            obs = None
        if obs is not None:
            entry["image"] = (obs.get("images") or {}).get(spec["container"])
            try:
                if spec["kind"] == "DaemonSet":
                    entry["ready"] = obs.get("number_ready")
                    entry["desired"] = obs.get("desired_number_scheduled")
                    entry["verdict"], entry["detail"] = assess_daemonset(obs)
                else:
                    entry["ready"] = obs.get("ready_replicas")
                    entry["desired"] = obs.get("replicas")
                    entry["verdict"], entry["detail"] = assess_deployment(obs)
            except Exception as exc:
                # 정규화 키가 빠진 비정상 관측(구버전 스텁 등) -- 판정만 포기하고
                # 이미지·카운트는 남긴다. 대시보드 읽기는 전부 fail-soft다(설계 §3).
                # COMPONENTS 오타 등 대시보드 자체 버그가 여기로 새므로 로그를 남긴다.
                logger.warning("metrics/infra assess failed component=%s: %s",
                               component, exc)
                entry["verdict"] = entry["detail"] = None
        components.append(entry)
    return {"components": components}
