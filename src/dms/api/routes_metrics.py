"""대시보드 메트릭 API(슬라이스 14). 전부 admin 전용 · 읽기 전용 -- 뮤테이션이
없으므로 감사 로그도 없다(설계 §3). 수치 조립은 저장소(앱측 JSON 파싱)와
metrics_series 순수 함수가 하고, 이 계층은 기간 클램프와 응답 조립만 한다."""
import concurrent.futures
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from ..db import iso_epoch, iso_plus, utc_now_iso
from ..manifest_tags import manifest_images, manifest_job_image
from ..metrics_series import (SUBMIT_WAIT_BUCKETS, SUBMIT_WAIT_OVERFLOW,
                              bucket_chars_for, build_node_points,
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
    # 제출 대기 분포(슬라이스 17): duration 과 같은 이유로 원자료 대신 분포만.
    # counted 는 NULL 제외 후 집계 대상 건수 -- excluded 와 함께 내 화면이 백필
    # 공백을 숨기지 못하게 한다(설계 §3). len() 그대로다: 0(같은 초 픽업)을 걸러
    # 내는 truthy 필터를 끼우면 가장 건강한 잡이 집계 건수에서 사라진다.
    submit_waits = stats.pop("submit_wait_seconds")
    stats["submit_wait_counted"] = len(submit_waits)
    stats["submit_wait_histogram"] = duration_histogram(
        submit_waits, buckets=SUBMIT_WAIT_BUCKETS, overflow=SUBMIT_WAIT_OVERFLOW)
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
    settings = request.app.state.settings
    # 동봉 매니페스트(이미지에 COPY 된 스냅샷)는 프로세스 수명 동안 불변이지만 수 KB
    # 텍스트 5개라 요청마다 읽어도 5s 폴링에 부담이 없다 -- 캐시 없는 단순성을 택한다.
    # 못 읽으면 값이 전부 None 일 뿐 예외가 없다(설계 §4 fail-soft).
    manifest = manifest_images()
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
                 "desired": None, "verdict": None, "detail": None,
                 # 비교는 프론트가 한다 -- 서버는 "이 이미지를 만든 소스 트리의
                 # 매니페스트" 값을 정직하게 실어줄 뿐이다(설계 §2.1/§3).
                 "manifest_image": manifest.get(component)}
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
    # 잡 이미지도 같은 위험이다(설계 §2.1): api 가 실제로 든 env 값(live) vs 동봉
    # 20-config.yaml 값(manifest). 빈 문자열(미설정)은 None 으로 접어 프론트가
    # "비교 불가"와 "불일치"를 헷갈리지 않게 한다.
    return {"components": components,
            "job_image": {"live": settings.job_image or None,
                          "manifest": manifest_job_image()}}


@router.get("/api/admin/metrics/queue")
def metrics_queue(request: Request):
    """Volcano 큐 현황(슬라이스 17 설계 §3). 축마다 독립 fail-soft: queue(이름
    지정 GET)와 podgroups(네임스페이스 list)는 필요한 권한이 다르므로 --
    ClusterRole 누락은 queue 만, Role 누락은 podgroups 만 403 -- 한쪽이 죽어도
    다른 쪽은 산다. null = 알 수 없음(403/CRD 부재), [] = 정말 비었음. 이 구분을
    접으면 권한 누락이 "큐가 한가함"으로 렌더된다(설계 §4).

    metrics_infra 와 같은 이유로 동기 def + ThreadPoolExecutor 병렬이다: 각 k8s
    호출엔 _request_timeout(10s)이 걸려 있지만 순차면 최악 2x10초가 5초 폴링에
    쌓여 threadpool 을 고갈시킨다."""
    reader = request.app.state.queue_reader
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {"queue": pool.submit(reader.read_queue),
                   "podgroups": pool.submit(reader.read_podgroups)}
    results = {}
    for axis, future in futures.items():
        try:
            results[axis] = future.result()
        except Exception as exc:
            # 403 등 리더가 올린 예외는 그 축만 null(알 수 없음) 강등. 축을 하나의
            # try 로 묶으면 RBAC 한 줄 누락이 라우트 전체를 500 으로 만든다. 로그가
            # 없으면 RBAC 누락이 화면의 "알 수 없음"으로만 보여 원인 추적이
            # 안 된다(read_pod_log 403 사고의 교훈).
            logger.warning("metrics/queue read failed axis=%s: %s", axis, exc)
            results[axis] = None
    pods = results["podgroups"]
    if pods is not None:
        now = iso_epoch(utc_now_iso())
        for pg in pods:
            # 대기 시간(now - creationTimestamp)은 서버가 계산한다 -- 브라우저
            # 시계 스큐가 대기 시간을 왜곡하지 않게. 시각이 깨진 항목만 null이고
            # 항목 자체는 목록에 남는다.
            #
            # .get 이다: 맨 서브스크립트면 created_at 키가 없는 항목 하나가
            # KeyError 로 이 그물을 통과해 라우트 전체를 500 낸다. 리더가 늘 그
            # 키를 채운다는 건 다른 모듈의 현재 구현에 대한 의존이지 이 라우트가
            # 스스로 지키는 성질이 아니다 -- 결측(None)도 파싱 실패와 같은 등급의
            # 항목 단위 강등으로 닫는다(설계 §4).
            #
            # pg 를 제자리 수정한다: 리더가 매 호출 새 dict 를 만들어 주므로 안전
            # 하지만, 리더가 캐시된 객체를 재사용하게 되면 캐시를 오염시킨다.
            try:
                pg["wait_seconds"] = max(
                    0, int(now - iso_epoch(pg.get("created_at"))))
            except (TypeError, ValueError):
                pg["wait_seconds"] = None
        # 오래 기다린 잡이 위로 -- 표의 목적이 "무엇이 막혀 있나"이므로.
        pods.sort(key=lambda p: -(p["wait_seconds"] or 0))
    return {"queue": results["queue"], "podgroups": pods}
