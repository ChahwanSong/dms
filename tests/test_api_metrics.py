from dms.db import dump_json, iso_plus, utc_now_iso
from dms.domain import DataJobState
from dms.queue_pipeline import WAITING_PER_STAGE
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared"}


def _report(*, load1=0.5, mem_total=100, mem_avail=50, rx=0, tx=0, cpu_used=None):
    # build_report(agent/runner.py)와 같은 모양 -- os 키 아래에 probe_os_metrics 반환(+ 러너의 cpu_used_pct)
    return {"mounts": [], "tools": [], "identities": [],
            "os": {"load1": load1, "load5": 0.4, "load15": 0.3, "cpu_used_pct": cpu_used,
                   "memory_total_kb": mem_total, "memory_available_kb": mem_avail,
                   "disks": [{"storage_name": "s1", "total_bytes": 100,
                              "used_bytes": 40}],
                   "network_rx_bytes": rx, "network_tx_bytes": tx}}


def _seed_job(db, repos, *, created_at, state="Succeeded", tool="dscan",
              reason_code=None):
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key=f"k:{created_at}:{state}", payload={}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1",
        target="a", options={}, tool=tool, worker_pool={}, precondition={},
        actor="planner")
    db.execute(
        """UPDATE data_jobs SET state = :st, reason_code = :rc,
               created_at = :c, updated_at = :c WHERE job_id = :j""",
        {"st": state, "rc": reason_code, "c": created_at, "j": job_id})
    return rid


def test_metrics_require_admin(client):
    assert client.get("/api/admin/metrics/nodes").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/metrics/nodes").status_code == 403
    assert client.get("/api/admin/metrics/jobs").status_code == 403


def test_metrics_nodes_series_with_backend_computed_throughput(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    repos.agents.ingest("n1", _report(rx=1000), reported_at=iso_plus(now, -120))
    repos.agents.ingest("n1", _report(rx=7000), reported_at=iso_plus(now, -60))
    body = client.get("/api/admin/metrics/nodes?window=24", headers=ADMIN).json()
    assert body["window_hours"] == 24
    node = body["nodes"][0]
    assert node["node_name"] == "n1" and node["fresh"] is True
    # 프론트는 카운터를 모른다 -- 백엔드가 차분한 B/s가 바로 온다(설계 §3)
    assert [p["net_rx_bps"] for p in node["points"]] == [None, 100.0]
    assert node["points"][0]["mem_used_pct"] == 50.0
    assert node["points"][0]["disks"] == [{"storage_name": "s1", "used_pct": 40.0}]


def test_metrics_nodes_series_and_node_list_carry_cpu_utilisation(client, db):
    # 2026-10-16: 시계열(/metrics/nodes)과 최신값(/nodes) 둘 다 에이전트가 계산한 사용률을 낸다. 첫 보고(None)는
    # 0 이 아니라 null, load(코어 수를 넘을 수 있는 별개 지표)는 그대로.
    repos = Repositories(db)
    now = utc_now_iso()
    repos.agents.ingest("n1", _report(load1=213.45), reported_at=iso_plus(now, -120))
    repos.agents.ingest("n1", _report(load1=213.45, cpu_used=42.5), reported_at=iso_plus(now, -60))
    points = client.get("/api/admin/metrics/nodes?window=24", headers=ADMIN).json()["nodes"][0]["points"]
    assert [p["cpu_used_pct"] for p in points] == [None, 42.5]
    assert [p["load1"] for p in points] == [213.45, 213.45]
    nodes = client.get("/api/admin/nodes", headers=ADMIN).json()
    assert nodes[0]["report"]["os"]["cpu_used_pct"] == 42.5


def test_metrics_nodes_window_clamps_to_retention(client, db):
    Repositories(db).agents.ingest("n1", _report(), reported_at=utc_now_iso())
    body = client.get("/api/admin/metrics/nodes?window=1000", headers=ADMIN).json()
    assert body["window_hours"] == 720           # 30일 보존 상한(설계 §6-2)


def test_metrics_nodes_fail_soft_on_corrupt_report(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    repos.agents.ingest("n1", _report(), reported_at=iso_plus(now, -120))
    repos.agents.ingest("n1", _report(), reported_at=iso_plus(now, -60))
    db.execute("UPDATE agent_reports SET report = '{broken' WHERE reported_at = :at",
               {"at": iso_plus(now, -120)})
    body = client.get("/api/admin/metrics/nodes?window=24", headers=ADMIN).json()
    assert len(body["nodes"][0]["points"]) == 1  # 손상 행만 빠지고 시리즈는 산다(설계 §6-6)


def test_metrics_nodes_fail_soft_on_non_list_disks(client, db):
    # 스키마 검증 없이 저장된 리포트라 os.disks 가 트루시 스칼라(5)일 수 있다.
    # `for disk in 5` 는 TypeError -- 나쁜 노드 하나가 라우트 전체를 500으로 죽이면
    # 안 된다. 그 노드만 disks:[]로 강등되고 건강한 노드 시리즈는 살아야 한다(§6-6).
    repos = Repositories(db)
    now = utc_now_iso()
    repos.agents.ingest("healthy", _report(), reported_at=now)
    bad = _report()
    bad["os"]["disks"] = 5               # 비-리스트: 순회 시 TypeError로 라우트가 죽을 것
    repos.agents.ingest("bad", bad, reported_at=now)
    r = client.get("/api/admin/metrics/nodes?window=24", headers=ADMIN)
    assert r.status_code == 200          # 라우트 전체가 500 나지 않는다(불변식)
    nodes = {n["node_name"]: n for n in r.json()["nodes"]}
    assert nodes["healthy"]["points"][0]["disks"] == [
        {"storage_name": "s1", "used_pct": 40.0}]   # 건강한 노드 시리즈는 온전
    assert nodes["bad"]["points"][0]["disks"] == []  # 나쁜 노드만 []로 우아하게 강등


def test_metrics_jobs_aggregates_and_histogram_shape(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    _seed_job(db, repos, created_at=iso_plus(now, -3600))
    _seed_job(db, repos, created_at=iso_plus(now, -1800), state="Failed",
              reason_code="execution_failed")
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    assert body["bucket"] == "hour"
    assert {r["state"]: r["count"] for r in body["by_state"]} == {
        "Succeeded": 1, "Failed": 1}
    assert body["failure_reasons"] == [
        {"reason_code": "execution_failed", "count": 1}]
    assert sum(b["count"] for b in body["throughput"]) == 2
    assert [b["bucket"] for b in body["duration_histogram"]] == [
        "<1m", "1-10m", "10-60m", "1-6h", "6-24h", ">24h"]
    assert body["files_total"] is None and body["bytes_total"] is None
    assert "duration_seconds" not in body        # 원자료는 내보내지 않는다


def test_metrics_jobs_day_bucket_beyond_48h(client):
    body = client.get("/api/admin/metrics/jobs?window=168", headers=ADMIN).json()
    assert body["bucket"] == "day" and body["window_hours"] == 168


def test_request_events_wrapper_scoped_to_request(client, db):
    repos = Repositories(db)
    rid = repos.requests.create(operation="scan", requester_id="alice",
                                actor="alice", resource_key="k:e1", payload={},
                                priority="mid")
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="plan_error", message="boom",
                                     request_id=rid)
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="other", request_id="someone-else")
    body = client.get(f"/api/admin/requests/{rid}/events", headers=ADMIN).json()
    assert body["request_id"] == rid
    assert [e["event_type"] for e in body["events"]] == ["plan_error"]
    assert body["events"][0]["message"] == "boom"


def test_request_events_unknown_request_404(client):
    r = client.get("/api/admin/requests/nope/events", headers=ADMIN)
    assert r.status_code == 404 and r.json()["detail"] == "request_not_found"


def test_request_events_admin_only(client):
    assert client.get("/api/admin/requests/x/events").status_code == 401


def test_metrics_window_zero_and_negative_fold_to_lower_bound(client):
    # §6-2: 기간은 접는다, 거부하지 않는다 -- 하한도 상한과 같은 철학.
    # 0·음수는 422가 아니라 200 + 1시간 창으로 접혀야 한다(clamp_window_hours가
    # 유일한 범위 권위). nodes·jobs 둘 다 같은 규칙.
    for path in ("/api/admin/metrics/nodes", "/api/admin/metrics/jobs"):
        for w in (0, -5):
            r = client.get(f"{path}?window={w}", headers=ADMIN)
            assert r.status_code == 200, (path, w)
            assert r.json()["window_hours"] == 1, (path, w)


def test_metrics_window_non_integer_still_422(client):
    # 비정수는 범위 문제가 아니라 파싱 오류다 -- 여전히 422가 맞다.
    for path in ("/api/admin/metrics/nodes", "/api/admin/metrics/jobs"):
        assert client.get(f"{path}?window=abc", headers=ADMIN).status_code == 422


class _FakeObserver:
    """observe만 쓰는 페어 -- 반환 모양은 StubRolloutRunner.observe(rollout_runner.py
    실측)와 같은 정규화 dict다. api는 patch를 절대 부르지 않는다(슬라이스 13 RBAC)."""
    def __init__(self):
        self.fail = set()
        self.objects = {
            ("DaemonSet", "dms-agent"): {
                "kind": "DaemonSet", "generation": 1, "observed_generation": 1,
                "desired_number_scheduled": 5, "updated_number_scheduled": 5,
                "number_ready": 5, "number_unavailable": 0,
                "number_misscheduled": 0,
                "images": {"agent": "pkg-01:5000/dms-agent:dev6"}},
            ("Deployment", "dms-api"): {
                "kind": "Deployment", "generation": 3, "observed_generation": 3,
                "replicas": 1, "status_replicas": 1, "updated_replicas": 1,
                "ready_replicas": 1, "conditions": [],
                "images": {"api": "pkg-01:5000/dms:d23"}},
            ("Deployment", "dms-controller"): {
                "kind": "Deployment", "generation": 3, "observed_generation": 3,
                "replicas": 1, "status_replicas": 1, "updated_replicas": 1,
                "ready_replicas": 0, "conditions": [],
                "images": {"controller": "pkg-01:5000/dms:d23"}},
        }

    def observe(self, *, kind, name):
        if (kind, name) in self.fail:
            from dms.execution import ExecutionError
            raise ExecutionError("observe_failed", "down")
        return self.objects.get((kind, name))


def test_metrics_infra_passes_counts_and_verdict(client):
    client.app.state.rollout_runner = _FakeObserver()
    body = client.get("/api/admin/metrics/infra", headers=ADMIN).json()
    by = {c["component"]: c for c in body["components"]}
    assert [c["component"] for c in body["components"]] == [
        "dms-agent", "dms-api", "dms-controller"]        # ROLLOUT_ORDER 순
    assert by["dms-agent"]["image"] == "pkg-01:5000/dms-agent:dev6"
    assert (by["dms-agent"]["ready"], by["dms-agent"]["desired"]) == (5, 5)
    assert by["dms-agent"]["verdict"] == "applied"
    assert by["dms-api"]["verdict"] == "applied"
    assert by["dms-controller"]["verdict"] == "progressing"   # ready 0/1


def test_metrics_infra_degrades_only_the_failed_component(client):
    runner = _FakeObserver()
    runner.fail.add(("Deployment", "dms-api"))
    client.app.state.rollout_runner = runner
    body = client.get("/api/admin/metrics/infra", headers=ADMIN).json()
    by = {c["component"]: c for c in body["components"]}
    # observe 실패는 그 컴포넌트만 null 강등(슬라이스 13 규약) -- 화면 전체가 죽지 않는다
    assert by["dms-api"]["image"] is None and by["dms-api"]["verdict"] is None
    assert by["dms-agent"]["verdict"] == "applied"


def test_metrics_infra_admin_only(client):
    assert client.get("/api/admin/metrics/infra").status_code == 401


def test_metrics_infra_observes_in_parallel_and_keeps_order(client):
    # observe는 컴포넌트당 1회 k8s GET(각 10s 타임아웃)이라 순차면 최악 3×10=30초 --
    # 프론트 5s 폴링과 충돌한다. 병렬화하면 최악 1×10초. 완료 순서를 ROLLOUT_ORDER의
    # 역순으로 뒤집어(첫째 dms-agent가 가장 늦게 끝나게) 응답이 완료 순서가 아니라
    # ROLLOUT_ORDER로 정렬됨을 고정하고, 동시에 벽시계로 병렬성을 잡는다.
    import time

    class _SlowObserver(_FakeObserver):
        _delays = {"dms-agent": 0.3, "dms-api": 0.2, "dms-controller": 0.1}

        def observe(self, *, kind, name):
            time.sleep(self._delays.get(name, 0))
            return super().observe(kind=kind, name=name)

    client.app.state.rollout_runner = _SlowObserver()
    t0 = time.monotonic()
    body = client.get("/api/admin/metrics/infra", headers=ADMIN).json()
    elapsed = time.monotonic() - t0
    assert [c["component"] for c in body["components"]] == [
        "dms-agent", "dms-api", "dms-controller"]      # 완료 역순이어도 순서 불변
    assert elapsed < 0.5, elapsed                       # 병렬(≈0.3s) -- 순차면 0.6s


def _stub_manifests(monkeypatch, images=None, job=None):
    # 기본 루트는 저장소의 실 deploy/k8s 를 읽는다(테스트 환경에도 존재) -- 실 태그에
    # 단언을 걸면 태그 범프마다 테스트가 깨지므로 라우트 테스트는 조회 함수를 대역화한다.
    # 실물 파싱 자체는 tests/test_manifest_tags.py 가 고정한다.
    monkeypatch.setattr(
        "dms.api.routes_metrics.manifest_images",
        lambda: images if images is not None else
        {"dms-agent": None, "dms-api": None, "dms-controller": None,
         "dms-migrate": None})
    monkeypatch.setattr("dms.api.routes_metrics.manifest_job_image", lambda: job)


def test_metrics_infra_reports_manifest_image_and_job_image(client, monkeypatch):
    client.app.state.rollout_runner = _FakeObserver()
    _stub_manifests(
        monkeypatch,
        images={"dms-agent": "pkg-01:5000/dms-agent:dev6",   # live 와 일치
                "dms-api": "pkg-01:5000/dms:d99",            # live(d23)와 드리프트
                "dms-controller": None,                      # 동봉 파싱 실패
                "dms-migrate": "pkg-01:5000/dms:d99"},
        job="pkg-01:5000/dms-mpifileutils:job9")
    body = client.get("/api/admin/metrics/infra", headers=ADMIN).json()
    by = {c["component"]: c for c in body["components"]}
    assert by["dms-agent"]["manifest_image"] == "pkg-01:5000/dms-agent:dev6"
    assert by["dms-api"]["manifest_image"] == "pkg-01:5000/dms:d99"
    assert by["dms-controller"]["manifest_image"] is None    # null -> 프론트 무배지
    # conftest 의 settings 는 job_image="" (기본) -- 빈 문자열은 None 으로 접는다
    # source(슬라이스 35): DB 오버라이드 미설정이면 env 가 원천이다.
    assert body["job_image"] == {"live": None, "source": "env",
                                 "manifest": "pkg-01:5000/dms-mpifileutils:job9"}


def test_metrics_infra_manifest_fail_soft_all_none(client, monkeypatch):
    # 동봉본이 없는 이미지(COPY 이전 빌드)에서도 라우트는 200 -- 값만 전량 null(설계 §4)
    client.app.state.rollout_runner = _FakeObserver()
    _stub_manifests(monkeypatch)
    r = client.get("/api/admin/metrics/infra", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert all(c["manifest_image"] is None for c in body["components"])
    assert body["job_image"] == {"live": None, "manifest": None, "source": "env"}


class _FakeQueueReader:
    """StubQueueReader(queue_reader.py)와 같은 두 메서드 페어. 축별로 값/예외를
    주입해 403(예외)·404/CRD 부재(None)·빈 목록([])·정상이 각각 다른 응답으로
    나오는지 -- 뭉개짐 금지(설계 §4) -- 를 고정한다."""
    _UNSET = object()

    def __init__(self, queue=_UNSET, podgroups=_UNSET):
        self._queue = ({"name": "dms-data", "state": "Open"}
                       if queue is self._UNSET else queue)
        self._podgroups = [] if podgroups is self._UNSET else podgroups

    def _resolve(self, value):
        if isinstance(value, Exception):
            raise value
        return value

    def read_queue(self):
        return self._resolve(self._queue)

    def read_podgroups(self):
        return self._resolve(self._podgroups)


def _legacy(body):
    """슬라이스 17 의 두 키만. 2026-10-16 에 새 키(control·pipeline·waiting·waiting_truncated)가 더해졌다 --
    옛 단언은 옛 키의 모양·뜻이 그대로인지만 본다(새 키는 아래 파이프라인 테스트들이 본다)."""
    return {"queue": body["queue"], "podgroups": body["podgroups"]}


def test_metrics_queue_stub_pair_serves_without_cluster(client):
    # 주입 없이 그대로 -- conftest 기본 백엔드(stub)의 wiring 이 StubQueueReader 를
    # 꽂는다. 이 스텁 페어가 없으면 모든 로컬·CI 가 여기서 500 이다(설계 §2.5).
    body = client.get("/api/admin/metrics/queue", headers=ADMIN).json()
    assert _legacy(body) == {"queue": {"name": "dms-data", "state": "Open"},
                             "podgroups": []}
    # 빈 DB 는 「모름」이 아니라 「없음」이다 -- 전 단계 count 0(oldest null), 빈 목록.
    assert body["control"] == {"drain": False, "maintenance": False, "reason": None}
    assert body["waiting"] == [] and body["waiting_truncated"] == []
    for stage in ("planning", "pickup", "preflight", "volcano_wait", "running", "confirm", "batch"):
        assert body["pipeline"][stage]["count"] == 0, stage
        assert body["pipeline"][stage]["oldest_seconds"] is None, stage


def test_metrics_queue_admin_only(client):
    assert client.get("/api/admin/metrics/queue").status_code == 401


def test_metrics_queue_computes_wait_and_sorts_longest_first(client):
    now = utc_now_iso()
    client.app.state.queue_reader = _FakeQueueReader(podgroups=[
        {"name": "dms-b-uid2", "phase": "Inqueue", "min_member": 1,
         "created_at": iso_plus(now, -30)},
        {"name": "dms-a-uid1", "phase": "Pending", "min_member": 3,
         "created_at": iso_plus(now, -300)},
        {"name": "dms-c-uid3", "phase": "Pending", "min_member": 1,
         "created_at": None},                    # 시각 없음 -- null 강등
    ])
    body = client.get("/api/admin/metrics/queue", headers=ADMIN).json()
    pods = body["podgroups"]
    # 오래 기다린 잡이 먼저 -- 표의 목적이 "무엇이 막혀 있나"다(설계 §3)
    assert [p["name"] for p in pods] == ["dms-a-uid1", "dms-b-uid2", "dms-c-uid3"]
    assert 300 <= pods[0]["wait_seconds"] <= 302   # 1초 해상도 + 호출 지연 여유
    assert pods[0]["min_member"] == 3
    assert pods[2]["wait_seconds"] is None


def test_metrics_queue_broken_item_degrades_alone_not_the_route(client):
    # 항목 하나의 결함이 라우트를 죽이거나 목록 전체를 지우면 안 된다. created_at
    # 키가 아예 없으면 맨 서브스크립트는 KeyError -- (TypeError, ValueError) 그물을
    # 통과해 500 이 된다. "리더가 늘 그 키를 채운다"는 다른 모듈의 현재 구현에 대한
    # 의존이지 이 라우트가 스스로 지키는 성질이 아니다(설계 §4).
    now = utc_now_iso()
    client.app.state.queue_reader = _FakeQueueReader(podgroups=[
        {"name": "ok", "phase": "Pending", "min_member": 1,
         "created_at": iso_plus(now, -120)},
        {"name": "no-key", "phase": "Pending", "min_member": 1},  # created_at 결측
        {"name": "bad-str", "phase": "Pending", "min_member": 1,
         "created_at": "2026-08-10 05:00:00+00:00"},              # 파싱 불가 포맷
    ])
    r = client.get("/api/admin/metrics/queue", headers=ADMIN)
    assert r.status_code == 200                    # 결함 항목이 라우트를 죽이지 않는다
    pods = {p["name"]: p for p in r.json()["podgroups"]}
    # 세 항목 모두 살아남는다 -- 한 항목 때문에 목록이 사라지지도 않는다
    assert set(pods) == {"ok", "no-key", "bad-str"}
    assert 120 <= pods["ok"]["wait_seconds"] <= 122   # 멀쩡한 항목은 계산까지 온전
    assert pods["no-key"]["wait_seconds"] is None     # 결함 항목만 null 강등
    assert pods["bad-str"]["wait_seconds"] is None


def test_metrics_queue_unknown_axes_stay_null_not_empty(client):
    # 403(리더 예외)과 CRD 부재(None)는 "빈 큐"가 아니다 -- []로 접으면 권한
    # 누락이 "큐가 한가함"으로 렌더된다(설계 §4). 축 강등이지 라우트 실패가
    # 아니므로 응답은 200 이다.
    client.app.state.queue_reader = _FakeQueueReader(
        queue=RuntimeError("forbidden 403"), podgroups=None)
    r = client.get("/api/admin/metrics/queue", headers=ADMIN)
    assert r.status_code == 200
    assert _legacy(r.json()) == {"queue": None, "podgroups": None}
    # PodGroup 을 못 읽으면 Volcano 대기 단계도 모름이다 -- 0 이 아니다. 「실행 중」은 DB 로 세되
    # Volcano 대기를 뺄 수 없었다는 사실을 함께 싣는다.
    pipeline = r.json()["pipeline"]
    assert pipeline["volcano_wait"] is None
    assert pipeline["running"]["includes_volcano_wait"] is True


def test_metrics_queue_empty_list_is_not_null(client):
    # 반대 방향도 고정: 정말 빈 큐([])가 null 로 승격되면 "알 수 없음" 경고가
    # 정상 상태에 뜬다.
    client.app.state.queue_reader = _FakeQueueReader(queue=None, podgroups=[])
    body = client.get("/api/admin/metrics/queue", headers=ADMIN).json()
    assert _legacy(body) == {"queue": None, "podgroups": []}
    assert body["pipeline"]["volcano_wait"] == {"count": 0, "oldest_seconds": None}


def test_metrics_queue_403_on_queue_axis_keeps_podgroups(client):
    # 두 축은 필요한 권한이 다르다: Queue 는 이름 지정 GET(ClusterRole), PodGroup 은
    # 네임스페이스 list(Role) -- ClusterRole 만 빠지면 queue 축만 403 이다. 두 축을
    # 한 try 로 묶으면 그 하나가 라우트 전체를 500 으로 만들어 살아 있는 대기 목록
    # 까지 잃는다. 그래서 축마다 독립 try/except 여야 한다.
    now = utc_now_iso()
    client.app.state.queue_reader = _FakeQueueReader(
        queue=RuntimeError("queues.scheduling.volcano.sh is forbidden 403"),
        podgroups=[{"name": "dms-a-uid1", "phase": "Pending", "min_member": 1,
                    "created_at": iso_plus(now, -60)}])
    r = client.get("/api/admin/metrics/queue", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["queue"] is None                      # 죽은 축만 "알 수 없음"
    assert [p["name"] for p in body["podgroups"]] == ["dms-a-uid1"]
    assert body["podgroups"][0]["wait_seconds"] >= 60  # 산 축은 계산까지 온전


def test_metrics_queue_403_on_podgroups_axis_keeps_queue(client):
    # 반대 방향(Role 누락 -> podgroups 축만 403)도 같은 독립성이어야 한다.
    client.app.state.queue_reader = _FakeQueueReader(
        queue={"name": "dms-data", "state": "Closed"},
        podgroups=RuntimeError("podgroups.scheduling.volcano.sh is forbidden 403"))
    r = client.get("/api/admin/metrics/queue", headers=ADMIN)
    assert r.status_code == 200
    assert _legacy(r.json()) == {"queue": {"name": "dms-data", "state": "Closed"},
                                 "podgroups": None}


# ---- 큐 현황 파이프라인(2026-10-16, 사용자 요청 「대시보드에서 큐 현황에 대해 조금만 더 자세히 그리고 개선해서
# 보여주면 좋겠어.」) -- DMS 안쪽의 대기 단계(계획·픽업·사전 점검·확인·배치)와 Volcano 대기를 한 응답에. 시각은
# 전이 행(state_transitions.at)을 뒤로 돌려 만든다 -- 대기 시작점이 「그 상태로의 마지막 전이」라는 계약을 그대로 쓴다.

_PIPELINE_SEQ = iter(range(10_000))


def _q_request(db, repos, *, created_at, batch_id=None, requester="alice", operation="sync"):
    rid = repos.requests.create(
        operation=operation, requester_id=requester, actor=requester,
        resource_key=f"q:{next(_PIPELINE_SEQ)}", payload={}, priority="mid", batch_id=batch_id)
    db.execute("UPDATE requests SET created_at = :c WHERE request_id = :r", {"c": created_at, "r": rid})
    return rid


def _q_job(db, repos, *, state, entered_at, batch_id=None, phase_refs=None, expires_at=None,
           requester="alice"):
    rid = _q_request(db, repos, created_at=entered_at, batch_id=batch_id, requester=requester)
    jid = repos.data_jobs.create_plan_and_job(
        rid, operation="sync", priority="mid", options={}, tool="dsync", worker_pool={},
        precondition={}, actor="planner")
    if state != "Pending":
        repos.data_jobs.set_job_state(jid, DataJobState(state), actor="stepper")
    db.execute("""UPDATE state_transitions SET at = :t
                  WHERE entity_kind = 'data_job' AND entity_id = :j AND to_state = :s""",
               {"t": entered_at, "j": jid, "s": state})
    db.execute("UPDATE data_jobs SET phase_refs = :p, preview_expires_at = :e WHERE job_id = :j",
               {"p": dump_json(phase_refs) if phase_refs is not None else None,
                "e": expires_at, "j": jid})
    return rid, jid


def _q_batch(db, repos, *, status, items, oldest_at, name=None):
    bid = repos.batches.create(operation="scan", requester_id="ops", actor="ops", max_concurrency=2,
                               options={}, note=None,
                               items=[{"storage": "s1", "target": f"t{i}"} for i in range(items)],
                               status=status, name=name)
    db.execute("UPDATE batch_items SET updated_at = :t WHERE batch_id = :b", {"t": oldest_at, "b": bid})
    return bid


def _queue(client):
    r = client.get("/api/admin/metrics/queue", headers=ADMIN)
    assert r.status_code == 200
    return r.json()


def _near(value, expected, slack=3):
    # 1초 해상도 + 호출 지연 여유(기존 wait_seconds 테스트와 같은 폭)
    return value is not None and expected <= value <= expected + slack


def test_metrics_queue_pipeline_counts_and_oldest_ages(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    _q_request(db, repos, created_at=iso_plus(now, -600))                      # 계획 대기 2(가장 오래 600s)
    _q_request(db, repos, created_at=iso_plus(now, -60))
    _q_job(db, repos, state="Pending", entered_at=iso_plus(now, -120))          # 픽업
    _q_job(db, repos, state="Preflight", entered_at=iso_plus(now, -45))         # 사전 점검
    _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -7 * 3600),
           expires_at=iso_plus(now, 7200))                                      # 확인 대기 단건 7시간
    # 제출된 잡 둘: 하나는 PodGroup 이 Inqueue(= Volcano 대기), 하나는 실제로 도는 중
    _q_job(db, repos, state="PreviewRunning", entered_at=iso_plus(now, -30),
           phase_refs={"preflight": "pod/dms-sync-preflight-aaaaaaaaaaaa",
                       "preview": "vcjob/dms-sync-preview-aaaaaaaaaaaa"})
    _q_job(db, repos, state="Executing", entered_at=iso_plus(now, -900),
           phase_refs={"preview": "vcjob/dms-sync-preview-bbbbbbbbbbbb",
                       "execution": "vcjob/dms-sync-execution-bbbbbbbbbbbb"})
    _q_batch(db, repos, status="Running", items=3, oldest_at=iso_plus(now, -1800))
    _q_batch(db, repos, status="Cancelled", items=5, oldest_at=iso_plus(now, -9000))  # 아무도 안 집는다 -- 안 셈
    client.app.state.queue_reader = _FakeQueueReader(podgroups=[
        {"name": "dms-sync-preview-aaaaaaaaaaaa-0d1e", "phase": "Inqueue", "min_member": 2,
         "created_at": iso_plus(now, -25), "vcjob": "dms-sync-preview-aaaaaaaaaaaa",
         "reason": "NotEnoughResources", "message": "2/2 tasks in gang unschedulable",
         "priority_class": "dms-mid"},
        {"name": "dms-sync-execution-bbbbbbbbbbbb-9f00", "phase": "Running", "min_member": 2,
         "created_at": iso_plus(now, -880), "vcjob": "dms-sync-execution-bbbbbbbbbbbb"},
    ])
    p = _queue(client)["pipeline"]
    assert p["planning"]["count"] == 2 and _near(p["planning"]["oldest_seconds"], 600)
    assert p["pickup"]["count"] == 1 and _near(p["pickup"]["oldest_seconds"], 120)
    assert p["preflight"]["count"] == 1 and _near(p["preflight"]["oldest_seconds"], 45)
    assert p["volcano_wait"]["count"] == 1 and _near(p["volcano_wait"]["oldest_seconds"], 25)
    # Volcano 대기로 대조된 잡은 「실행 중」에서 빠진다 -- 같은 잡을 두 칸에 세지 않는다
    assert p["running"]["count"] == 1 and _near(p["running"]["oldest_seconds"], 900)
    assert p["running"]["includes_volcano_wait"] is False
    assert p["confirm"]["count"] == 1 and _near(p["confirm"]["oldest_seconds"], 7 * 3600)
    assert p["confirm"]["soonest_expiry"] == iso_plus(now, 7200)
    assert 7197 <= p["confirm"]["soonest_expiry_seconds"] <= 7200
    assert p["batch"]["count"] == 3 and _near(p["batch"]["oldest_seconds"], 1800)
    assert (p["batch"]["batches"], p["batch"]["preview_ready_batches"]) == (1, 0)


def test_metrics_queue_waiting_lists_stages_with_request_links(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    plan_rid = _q_request(db, repos, created_at=iso_plus(now, -100), requester="bob")
    repos.observability.record_event(component="planner", severity="info",
                                     event_type="identity_propagating",
                                     message="identity not ready on: n1", request_id=plan_rid)
    conf_rid, conf_jid = _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -5000),
                                expires_at=iso_plus(now, 600))
    vol_rid, vol_jid = _q_job(db, repos, state="Executing", entered_at=iso_plus(now, -50),
                              phase_refs={"execution": "vcjob/dms-sync-execution-cccccccccccc"})
    bid = _q_batch(db, repos, status="Previewing", items=4, oldest_at=iso_plus(now, -300), name="야간")
    client.app.state.queue_reader = _FakeQueueReader(podgroups=[
        # ownerReferences 가 없는 PodGroup -- 이름 접두(<vcjob>-<uid>)로 대조된다
        {"name": "dms-sync-execution-cccccccccccc-5a7b-11ef", "phase": "Pending", "min_member": 3,
         "created_at": iso_plus(now, -40), "vcjob": None, "reason": None, "message": None},
        # DMS 잡에 대조되지 않는 PodGroup 도 큐를 막고 있으면 대기다
        {"name": "someone-else-uid", "phase": "Pending", "min_member": 1,
         "created_at": iso_plus(now, -10), "priority_class": "dms-high"},
    ])
    body = _queue(client)
    waiting = body["waiting"]
    # 오래 기다린 순(단계를 가로질러)
    assert [e["stage"] for e in waiting] == [
        "confirm", "batch", "planning", "volcano_wait", "volcano_wait"]
    confirm, batch, planning, vol, stranger = waiting
    assert (confirm["request_id"], confirm["job_id"], confirm["requester"]) == (
        conf_rid, conf_jid, "alice")
    assert confirm["detail"]["expires_at"] == iso_plus(now, 600)
    assert 597 <= confirm["detail"]["expires_in_seconds"] <= 600
    assert _near(confirm["wait_seconds"], 5000)
    assert (batch["request_id"], batch["batch_id"]) == (None, bid)
    assert batch["detail"] == {"queued": 4, "held": 0, "max_concurrency": 2, "batch_status": "Previewing",
                               "batch_name": "야간", "expires_at": None, "expires_in_seconds": None}
    assert (planning["request_id"], planning["job_id"], planning["requester"]) == (
        plan_rid, None, "bob")
    assert planning["detail"] == {"note": "identity_propagating",
                                  "message": "identity not ready on: n1"}
    assert (vol["request_id"], vol["job_id"]) == (vol_rid, vol_jid)
    assert vol["detail"]["podgroup"] == "dms-sync-execution-cccccccccccc-5a7b-11ef"
    assert vol["detail"]["phase_ref"] == "execution" and vol["detail"]["min_member"] == 3
    assert _near(vol["wait_seconds"], 40)                       # 대기 시작 = PodGroup 생성
    assert (stranger["request_id"], stranger["job_id"], stranger["priority"]) == (None, None, "high")
    assert body["waiting_truncated"] == []
    # 대조된 잡은 「실행 중」 0 -- Volcano 대기 2(대조 1 + 비대조 1)
    assert body["pipeline"]["running"]["count"] == 0
    assert body["pipeline"]["volcano_wait"]["count"] == 2


def test_metrics_queue_groups_batch_children_awaiting_confirm(client, db):
    # sync 배치 하나가 자식 수백 건을 한꺼번에 확인 대기로 만든다 -- 건별로 늘어놓으면 표가 한 배치로 덮여
    # 사람이 컨펌해야 할 단건이 묻힌다. 배치별 한 줄(자식 수·가장 빠른 만료)로 묶는다. 띠의 count 는 잡 수 그대로.
    repos = Repositories(db)
    now = utc_now_iso()
    bid = _q_batch(db, repos, status="PreviewReady", items=0, oldest_at=now, name="월간 이관")
    for age, exp in ((4000, 9000), (3000, 500)):
        _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -age), batch_id=bid,
               expires_at=iso_plus(now, exp))
    single_rid, _ = _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -100),
                           expires_at=iso_plus(now, 80000))
    body = _queue(client)
    assert body["pipeline"]["confirm"]["count"] == 3
    assert body["pipeline"]["batch"]["preview_ready_batches"] == 1
    confirm = [e for e in body["waiting"] if e["stage"] == "confirm"]
    assert [(e["batch_id"], e["request_id"]) for e in confirm] == [(bid, None), (None, single_rid)]
    group = confirm[0]["detail"]
    assert (group["jobs"], group["batch_status"], group["batch_name"]) == (
        2, "PreviewReady", "월간 이관")
    assert group["expires_at"] == iso_plus(now, 500)            # 묶음의 가장 빠른 만료
    assert _near(confirm[0]["wait_seconds"], 4000)              # 묶음의 가장 오래된 자식


def test_metrics_queue_held_batch_children_wait_in_batch_not_confirm(client, db):
    """확인 대기 = 사람이 지금 누를 수 있는 것만(2026-10-16 검증 지적): 이미 확인된 Running 배치의 자식은 실행 슬롯을,
    Previewing 배치의 자식은 형제 미리보기를 기다린다 -- 확인 대기에 세면 사람이 할 일이 없는데 「확인 대기 · 13시간」이
    적색으로 떴다. 배치 대기(held)로 센다."""
    repos = Repositories(db)
    now = utc_now_iso()
    running = _q_batch(db, repos, status="Running", items=0, oldest_at=now, name="확인됨")
    for _ in range(3):
        _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -13 * 3600), batch_id=running,
               expires_at=iso_plus(now, 3600))
    previewing = _q_batch(db, repos, status="Previewing", items=2, oldest_at=iso_plus(now, -60))
    _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -600), batch_id=previewing)
    ready = _q_batch(db, repos, status="PreviewReady", items=0, oldest_at=now)
    _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -1200), batch_id=ready,
           expires_at=iso_plus(now, 50000))
    _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -300), expires_at=iso_plus(now, 70000))
    body = _queue(client)
    p = body["pipeline"]
    assert p["confirm"]["count"] == 2 and _near(p["confirm"]["oldest_seconds"], 1200)
    assert p["confirm"]["soonest_expiry"] == iso_plus(now, 50000)
    assert (p["batch"]["count"], p["batch"]["queued"], p["batch"]["held"]) == (2 + 4, 2, 4)
    assert _near(p["batch"]["oldest_seconds"], 13 * 3600)
    rows = {e["batch_id"]: e for e in body["waiting"] if e["stage"] == "batch"}
    assert rows[running]["detail"]["held"] == 3 and rows[running]["detail"]["queued"] == 0
    assert rows[running]["detail"]["batch_status"] == "Running"
    assert rows[running]["detail"]["max_concurrency"] == 2
    assert rows[running]["detail"]["expires_at"] == iso_plus(now, 3600)
    assert (rows[previewing]["detail"]["queued"], rows[previewing]["detail"]["held"]) == (2, 1)
    assert [e["batch_id"] for e in body["waiting"] if e["stage"] == "confirm"] == [ready, None]


def test_metrics_queue_exec_preflight_counts_as_preflight_not_running(client, db):
    """확인 뒤 실행 전 재점검(Executing 인데 execution ref 가 없다 -- exec_preflight 파드, vcjob 없음)은 사전 점검이다.
    예전엔 「Volcano 실행 중」에 들어가 재점검 파드가 Pending 에 멈춰도 대기로 보이지 않았다(검증 지적)."""
    repos = Repositories(db)
    now = utc_now_iso()
    _, jid = _q_job(db, repos, state="Executing", entered_at=iso_plus(now, -1800),
                    phase_refs={"preflight": "pod/dms-sync-preflight-dddddddddddd",
                                "preview": "vcjob/dms-sync-preview-dddddddddddd",
                                "exec_preflight": "pod/dms-sync-exec-preflight-dddddddddddd"})
    _q_job(db, repos, state="Executing", entered_at=iso_plus(now, -60),
           phase_refs={"execution": "vcjob/dms-sync-execution-eeeeeeeeeeee"})
    client.app.state.queue_reader = _FakeQueueReader(podgroups=[])
    body = _queue(client)
    p = body["pipeline"]
    assert p["preflight"]["count"] == 1 and _near(p["preflight"]["oldest_seconds"], 1800)
    assert p["running"]["count"] == 1 and _near(p["running"]["oldest_seconds"], 60)
    [pf] = [e for e in body["waiting"] if e["stage"] == "preflight"]
    assert pf["job_id"] == jid and pf["detail"] == {"exec_preflight": True}


def test_metrics_queue_planning_note_is_the_latest_event(client, db):
    # 요청마다 가장 최근 하나(ORDER BY id DESC LIMIT 1 -- 막힌 요청의 이벤트 전체를 읽지 않는다).
    repos = Repositories(db)
    rid = _q_request(db, repos, created_at=iso_plus(utc_now_iso(), -100))
    for et, msg in (("identity_propagating", "old"), ("plan_error", "boom 1"), ("plan_error", "boom 2")):
        repos.observability.record_event(component="planner", severity="error", event_type=et, message=msg,
                                         request_id=rid)
    repos.observability.record_event(component="planner", severity="info", event_type="something_else",
                                     message="not a note", request_id=rid)
    [e] = [e for e in _queue(client)["waiting"] if e["stage"] == "planning"]
    assert e["detail"] == {"note": "plan_error", "message": "boom 2"}


def test_metrics_queue_waiting_is_capped_per_stage_not_overall(client, db):
    # 상한은 단계마다다: 전체 오래된 순으로 자르면 몇 시간짜리 확인 대기가 상한을 다 채워 막 쌓이는 계획 대기가
    # 목록에서 사라진다(필터를 눌러도 띠의 숫자와 다른 「없음」). 잘린 단계는 이름으로 알린다.
    repos = Repositories(db)
    now = utc_now_iso()
    for i in range(WAITING_PER_STAGE + 3):
        _q_job(db, repos, state="ConfirmPending", entered_at=iso_plus(now, -10_000 - i),
               expires_at=iso_plus(now, 3600))
    newest_plan = _q_request(db, repos, created_at=iso_plus(now, -5))
    body = _queue(client)
    stages = [e["stage"] for e in body["waiting"]]
    assert stages.count("confirm") == WAITING_PER_STAGE
    assert stages[-1] == "planning" and body["waiting"][-1]["request_id"] == newest_plan
    assert body["waiting_truncated"] == ["confirm"]
    assert body["pipeline"]["confirm"]["count"] == WAITING_PER_STAGE + 3   # 띠는 잘리지 않은 총수
    confirm_waits = [e["wait_seconds"] for e in body["waiting"] if e["stage"] == "confirm"]
    # 잘린 것은 가장 최근 셋이다 -- 가장 오래 기다린 20건이 남는다
    assert min(confirm_waits) >= 10_003
    waits = [e["wait_seconds"] for e in body["waiting"]]
    assert waits == sorted(waits, reverse=True)                # 오래 기다린 것이 위


def test_metrics_queue_db_axis_failure_nulls_only_its_stages(client, db, monkeypatch):
    # DB 축도 k8s 축과 같은 규칙: 한 축의 실패는 그 축에 기대는 단계만 모름(null). 잡 축이 죽어도 계획·
    # Volcano 단계와 옛 두 키는 그대로다.
    repos = client.app.state.repos
    now = utc_now_iso()
    _q_request(db, repos, created_at=iso_plus(now, -30))
    _q_batch(db, repos, status="Running", items=2, oldest_at=iso_plus(now, -60))

    def boom(**_):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(repos.metrics, "queue_jobs", boom)
    client.app.state.queue_reader = _FakeQueueReader(podgroups=[
        {"name": "dms-sync-preview-dddddddddddd-1", "phase": "Pending", "min_member": 1,
         "created_at": iso_plus(now, -20), "vcjob": "dms-sync-preview-dddddddddddd"}])
    body = _queue(client)
    p = body["pipeline"]
    # 배치 대기도 모름: held(굴러가는 배치의 ConfirmPending 자식)가 잡 축에서 온다 -- Queued 만으로 「대기 없음」을 내지 않는다.
    for stage in ("pickup", "preflight", "running", "confirm", "batch"):
        assert p[stage] is None, stage                       # 모름 -- 0 으로 접지 않는다
    assert p["planning"]["count"] == 1
    assert p["volcano_wait"]["count"] == 1
    # 잡 축 없이도 PodGroup 은 대기 목록에 남는다(대조만 못 한다)
    assert {e["stage"] for e in body["waiting"]} == {"planning", "volcano_wait"}
    vol = next(e for e in body["waiting"] if e["stage"] == "volcano_wait")
    assert vol["request_id"] is None and vol["detail"]["phase_ref"] is None
    assert body["queue"] == {"name": "dms-data", "state": "Open"} and len(body["podgroups"]) == 1


def test_metrics_queue_control_axis_and_all_db_failure(client, monkeypatch):
    repos = client.app.state.repos
    repos.control.set_control_state(maintenance=True, drain=True, reason="스토리지 점검", actor="admin")
    assert _queue(client)["control"] == {"drain": True, "maintenance": True, "reason": "스토리지 점검"}

    def boom(*_, **__):
        raise RuntimeError("db down")
    for name in ("queue_planning", "queue_jobs", "queue_batches"):
        monkeypatch.setattr(repos.metrics, name, boom)
    monkeypatch.setattr(repos.control, "control_state", boom)
    client.app.state.queue_reader = _FakeQueueReader(podgroups=RuntimeError("forbidden 403"))
    body = _queue(client)
    # 아무 대기 단계도 못 읽었다 -- 목록은 []("없음")가 아니라 null("모름")이다
    assert body["waiting"] is None and body["waiting_truncated"] is None
    assert body["control"] is None
    assert all(v is None for v in body["pipeline"].values())
    assert body["queue"] == {"name": "dms-data", "state": "Open"}   # 산 축은 그대로


def test_metrics_queue_assembly_bug_degrades_new_keys_only(client, monkeypatch):
    # 조립(순수 함수)의 결함은 새 키만 모름으로 접는다 -- 옛 두 키까지 500 으로 끌고 가지 않는다.
    import dms.api.routes_metrics as rm

    def broken(*_, **__):
        raise KeyError("oops")
    monkeypatch.setattr(rm, "build_queue_view", broken)
    body = _queue(client)
    assert _legacy(body) == {"queue": {"name": "dms-data", "state": "Open"}, "podgroups": []}
    assert (body["pipeline"], body["waiting"], body["waiting_truncated"]) == (None, None, None)
    assert body["control"] == {"drain": False, "maintenance": False, "reason": None}


# ---- 큐 흐름(2026-10-16, 사용자 지시 「큐 대기가 일종의 파이프라인 이잖아? … 흐름 형식으로 애니메이션화해서」) -- 최근 5분의
# 단계 사이 이동. 실제 작성자(requests.create · create_plan_and_job · set_job_state)가 남기는 전이 모양 그대로 지나가게 한다:
# 매핑이 작성자의 사본 행(plan·잡 None→Pending)까지 세면 계획 한 번이 엣지 셋이 된다.

_FLOW_EDGES0 = dict.fromkeys(("submit>planning", "batch>planning", "planning>pickup", "pickup>preflight",
                              "preflight>volcano_wait", "running>confirm", "running>batch", "confirm>preflight",
                              "batch>preflight", "running>done"), 0)
_FLOW_EXITS0 = dict.fromkeys(("planning", "pickup", "preflight", "running", "confirm", "batch"), 0)


def _tr(db, kind, entity_id, f, t, *, at, actor="stepper", reason=None):
    # 작성자 경로가 없는 전이(이탈·특정 actor·reason)만 직접 넣는다 -- 모양은 _record_transition 과 같다.
    db.execute("""INSERT INTO state_transitions (entity_kind, entity_id, from_state, to_state, reason_code, actor, at)
                  VALUES (:k, :id, :f, :t, :r, :a, :at)""",
               {"k": kind, "id": entity_id, "f": f, "t": t, "r": reason, "a": actor, "at": at})


def _flow_job(repos, *, batch_id=None, operation="sync"):
    rid = repos.requests.create(operation=operation, requester_id="alice", actor="alice",
                                resource_key=f"f:{next(_PIPELINE_SEQ)}", payload={}, priority="mid",
                                batch_id=batch_id)
    jid = repos.data_jobs.create_plan_and_job(rid, operation=operation, priority="mid", options={}, tool="dsync",
                                              worker_pool={}, precondition={}, actor="planner")
    return rid, jid


def test_metrics_queue_flow_maps_recent_transitions_to_pipeline_edges(client, db):
    repos = client.app.state.repos
    now = utc_now_iso()
    _q_request(db, repos, created_at=now)                                  # 직접 제출 → submit>planning
    _q_request(db, repos, created_at=now, batch_id="b" * 32)              # 배치 materialize → batch>planning
    # 단건 sync 한 바퀴: 계획 → 픽업 → 미리보기 제출 → 확인 대기 → 사람의 컨펌 → 실행 제출(자기 전이) → 성공
    _, single = _flow_job(repos)
    for state, actor in (("Preflight", "stepper"), ("PreviewRunning", "stepper"), ("ConfirmPending", "stepper"),
                         ("Executing", "alice"), ("Executing", "stepper"), ("Succeeded", "stepper")):
        repos.data_jobs.set_job_state(single, DataJobState(state), actor=actor)
    # 배치 자식: 미리보기 끝 → held(배치 대기) → 오케스트레이터의 컨펌 → 사전 점검(재점검)
    _, child = _flow_job(repos, batch_id="c" * 32)
    for state, actor in (("Preflight", "stepper"), ("PreviewRunning", "stepper"), ("ConfirmPending", "stepper"),
                         ("Executing", "batch-orchestrator")):
        repos.data_jobs.set_job_state(child, DataJobState(state), actor=actor)
    # scan: 사전 점검 → 실행 제출(Preflight→Running) → 실행 중 실패(이탈: 실행 중)
    _, scan = _flow_job(repos, operation="scan")
    for state in ("Preflight", "Running", "Failed"):
        repos.data_jobs.set_job_state(scan, DataJobState(state), actor="stepper")
    # 이탈: 계획 거부 · 재점검 거부(Executing 이지만 사전 점검 칸) · 실행 실패 · 배치 자식 만료 · 단건 취소
    _tr(db, "request", "r-rejected", "Pending", "Rejected", at=now, actor="planner", reason="no_eligible_nodes")
    _tr(db, "data_job", "x-recheck", "Executing", "Rejected", at=now, reason="execution_recheck_failed")
    _tr(db, "data_job", "x-submit", "Executing", "Failed", at=now, reason="execution_submit_failed:submit_failed")
    _tr(db, "data_job", "x-exec", "Executing", "Failed", at=now, reason="execution_failed")
    _tr(db, "data_job", child, "ConfirmPending", "PreviewExpired", at=now, reason="preview_expired")
    _tr(db, "data_job", single, "ConfirmPending", "Cancelled", at=now, actor="alice")
    # 창 밖(10분 전)은 세지 않는다 -- 같은 엣지라도
    _tr(db, "data_job", "x-old", "Pending", "Preflight", at=iso_plus(now, -600))

    flow = _queue(client)["flow"]
    assert flow["window_seconds"] == 300 and flow["saturated"] is False
    # 직접 제출 = _q_request 1 + 단건·scan 의 요청 2(배치 자식 요청은 batch>planning 으로 간다)
    assert flow["edges"] == {**_FLOW_EDGES0, "submit>planning": 3, "batch>planning": 2, "planning>pickup": 3,
                             "pickup>preflight": 3, "preflight>volcano_wait": 4, "running>confirm": 1,
                             "running>batch": 1, "confirm>preflight": 1, "batch>preflight": 1, "running>done": 1}
    assert flow["exits"] == {**_FLOW_EXITS0, "planning": 1, "preflight": 2, "running": 2, "confirm": 1, "batch": 1}
    assert flow["done"] == 1
    # Volcano 대기 → 실행 중은 전이로 남지 않는다 -- 키 자체가 없다(0 으로 지어내지 않는다)
    assert "volcano_wait>running" not in flow["edges"]


def test_metrics_queue_flow_cancelled_preview_ready_batch_children_fold_into_batch(client, db):
    # 알려진 근사(build_flow docstring, 재검토 지적): 배치 취소는 begin_cancel 이 배치를 먼저 Cancelling 으로 쓰고 자식을
    # 끝낸다 -- 확인 대기(PreviewReady) 배치 자식의 취소 이탈은 조회 시점 배치 상태로 늘 배치 대기에 접힌다. 이 동작을 고정한다.
    repos = client.app.state.repos
    bid = repos.batches.create(operation="sync", requester_id="ops", actor="ops", max_concurrency=2,
                               options={}, note=None, items=[], status="PreviewReady")
    _, jid = _flow_job(repos, batch_id=bid)
    for state in ("Preflight", "PreviewRunning", "ConfirmPending"):
        repos.data_jobs.set_job_state(jid, DataJobState(state), actor="stepper")
    assert repos.batches.begin_cancel(bid, from_states=("PreviewReady",))
    repos.data_jobs.set_job_state(jid, DataJobState.CANCELLED, reason_code="cancelled_by_batch", actor="api")
    exits = _queue(client)["flow"]["exits"]
    assert (exits["batch"], exits["confirm"]) == (1, 0)


def test_metrics_queue_flow_exits_land_on_the_stage_the_screen_counted(client, db):
    # 리뷰 지적 두 갈래(2026-10-16): ① 실행 vcjob 을 내기 전(실행 전 재점검 중)의 Executing 이탈은 화면이 사전 점검 칸에
    # 세던 잡이다 -- 사유를 열거하지 않고 실행 제출 앵커로 가른다(노드 CPU 부족·노드 제외·배치 취소는 재점검 사유가 아니라
    # 예전엔 실행 중 이탈로 샜다). ② 확인 대기(PreviewReady) 배치 자식의 만료는 확인 대기 칸에서 떠난 것이다(held 가 아니다).
    # 실제 작성자(set_job_state · mark_exec_submitted · expire_previews)로 만든다.
    repos = client.app.state.repos
    now = utc_now_iso()

    def walk(jid, states, actor="stepper"):
        for state in states:
            repos.data_jobs.set_job_state(jid, DataJobState(state), actor=actor)

    def to_executing(batch_id=None):
        _, jid = _flow_job(repos, batch_id=batch_id)
        walk(jid, ("Preflight", "PreviewRunning", "ConfirmPending"))
        walk(jid, ("Executing",), actor="alice")
        return jid

    for reason in ("node_cpu_insufficient_at_step", "node_excluded_at_step"):
        repos.data_jobs.set_job_state(to_executing(), DataJobState.FAILED, reason_code=reason, actor="stepper")
    repos.data_jobs.set_job_state(to_executing(batch_id="d" * 32), DataJobState.CANCELLED,
                                  reason_code="cancelled_by_batch", actor="stepper")
    ran = to_executing()                                       # 실행 vcjob 제출 뒤(앵커 있음)의 실패 -- 실행 중
    repos.data_jobs.mark_exec_submitted(ran)
    walk(ran, ("Executing",))
    repos.data_jobs.set_job_state(ran, DataJobState.FAILED, reason_code="execution_failed", actor="stepper")

    def batch(status):
        return repos.batches.create(operation="sync", requester_id="ops", actor="ops", max_concurrency=2,
                                    options={}, note=None, items=[], status=status)

    def previewed(batch_id=None):
        _, jid = _flow_job(repos, batch_id=batch_id)
        walk(jid, ("Preflight", "PreviewRunning", "ConfirmPending"))
        db.execute("UPDATE data_jobs SET preview_expires_at = :e WHERE job_id = :j",
                   {"e": iso_plus(now, -1), "j": jid})
        return jid

    ready, previewing = batch("PreviewReady"), batch("Previewing")
    for _ in range(2):
        previewed(ready)
    previewed(previewing)                                      # 형제 미리보기를 기다리는 held -- 배치 대기
    previewed()                                                # 단건 컨펌 대기
    pipe = _queue(client)["pipeline"]
    assert (pipe["confirm"]["count"], pipe["batch"]["held"]) == (3, 1)      # 화면이 센 칸(만료 전)
    assert len(repos.data_jobs.expire_previews(now_iso=utc_now_iso())) == 4

    flow = _queue(client)["flow"]
    assert flow["exits"] == {**_FLOW_EXITS0, "preflight": 3, "running": 1, "confirm": 3, "batch": 1}


def test_metrics_queue_flow_saturation_marks_counts_as_a_lower_bound(client, db):
    repos = client.app.state.repos
    now = utc_now_iso()
    for i in range(3):
        _tr(db, "data_job", f"j{i}", "Pending", "Preflight", at=now)
    # 상한(3)을 다 채웠고 가장 오래된 것도 창 안이다 -- 창 안에 더 있을 수 있다(수치는 하한)
    raw = repos.metrics.queue_flow(cap=3)
    assert raw["saturated"] is True and sum(r["n"] for r in raw["rows"]) == 3
    # 상한 안에 창 밖 행이 보이면 창 전체를 본 것이다 -- 포화 아님
    _tr(db, "data_job", "old", "Pending", "Preflight", at=iso_plus(now, -900))
    _tr(db, "data_job", "j9", "Pending", "Preflight", at=now)
    raw = repos.metrics.queue_flow(cap=3)
    assert raw["saturated"] is False
    assert sum(r["n"] for r in raw["rows"]) == 2                 # 상한 3 = j9·old·j2 -- 창 안은 j9·j2
    # 상한을 못 채우면(행이 적으면) 포화일 수 없다
    assert repos.metrics.queue_flow(cap=100)["saturated"] is False


def test_metrics_queue_flow_empty_db_is_zero_not_null(client):
    # 읽었고 아무 이동도 없었다 -- 전 엣지 0(null 이 아니다). 화면은 이것을 「입자 없음 · 완료 0」으로 그린다.
    assert _queue(client)["flow"] == {"window_seconds": 300, "saturated": False, "edges": _FLOW_EDGES0,
                                      "exits": _FLOW_EXITS0, "done": 0}


def test_metrics_queue_flow_failure_nulls_only_flow(client, db, monkeypatch):
    import dms.api.routes_metrics as rm
    repos = client.app.state.repos
    _q_request(db, repos, created_at=utc_now_iso())

    def boom(*_, **__):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(repos.metrics, "queue_flow", boom)
    body = _queue(client)
    assert body["flow"] is None                                    # 모름 -- 0 으로 접지 않는다
    assert body["pipeline"]["planning"]["count"] == 1              # 다른 축은 그대로
    monkeypatch.undo()
    # 조립(순수 함수)의 결함도 flow 만 모름이다 -- 띠·표는 흐름 없이도 참이다
    monkeypatch.setattr(rm, "build_flow", boom)
    body = _queue(client)
    assert body["flow"] is None and body["pipeline"]["planning"]["count"] == 1
    assert _legacy(body) == {"queue": {"name": "dms-data", "state": "Open"}, "podgroups": []}


def test_metrics_jobs_submit_wait_distribution_and_counts(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    rid = _seed_job(db, repos, created_at=iso_plus(now, -3600))
    _seed_job(db, repos, created_at=iso_plus(now, -1800))              # NULL 유지
    db.execute("UPDATE data_jobs SET submit_wait_seconds = 12 WHERE request_id = :r",
               {"r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    hist = {b["bucket"]: b["count"] for b in body["submit_wait_histogram"]}
    assert list(hist) == ["<10s", "10-30s", "30-60s", "1-5m", "5-30m", ">30m"]
    assert hist["10-30s"] == 1
    assert body["submit_wait_counted"] == 1
    assert body["submit_wait_excluded"] == 1     # NULL 잡의 제외를 표면화(설계 §3)
    assert "submit_wait_seconds" not in body     # 원자료는 내보내지 않는다(duration 규칙)


def test_metrics_jobs_submit_wait_zero_counts_toward_the_total(client, db):
    # 라우트 층의 falsy 함정: counted 를 `len([w for w in waits if w])` 로 세거나
    # 원자료를 `waits or []` 로 다루면 0(같은 초 픽업 = 가장 건강한 잡)이 사라져
    # "N건 중 M건 집계"의 M 이 조용히 줄고 첫 버킷이 빈다. 0 은 결측이 아니다.
    repos = Repositories(db)
    now = utc_now_iso()
    rid = _seed_job(db, repos, created_at=iso_plus(now, -3600))
    db.execute("UPDATE data_jobs SET submit_wait_seconds = 0 WHERE request_id = :r",
               {"r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    hist = {b["bucket"]: b["count"] for b in body["submit_wait_histogram"]}
    assert hist["<10s"] == 1
    assert body["submit_wait_counted"] == 1
    assert body["submit_wait_excluded"] == 0


def test_metrics_jobs_sched_wait_distribution_and_counts(client, db):
    repos = Repositories(db)
    now = utc_now_iso()
    rid = _seed_job(db, repos, created_at=iso_plus(now, -3600))
    _seed_job(db, repos, created_at=iso_plus(now, -1800))     # NULL 유지(관측 없음)
    db.execute("UPDATE data_jobs SET sched_wait_seconds = 12 WHERE request_id = :r",
               {"r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    hist = {b["bucket"]: b["count"] for b in body["sched_wait_histogram"]}
    # submit_wait 과 같은 축(설계 §2.7) -- 두 분포가 나란히 비교 가능해야 한다
    assert list(hist) == ["<10s", "10-30s", "30-60s", "1-5m", "5-30m", ">30m"]
    assert hist["10-30s"] == 1
    assert body["sched_wait_counted"] == 1
    assert body["sched_wait_excluded"] == 1   # 관측 없는 잡의 제외를 표면화(설계 §2.5)
    assert "sched_wait_seconds" not in body   # 원자료는 내보내지 않는다(duration 규칙)


def test_metrics_jobs_exec_runtime_distribution_counts_and_summary(client, db):
    # 실행시간(슬라이스 31): epoch(updated_at) - epoch(exec_submitted_at)
    # - sched_wait_seconds 의 파생 계산. 버킷은 전체 수명(duration)과 같은 축 --
    # 두 분포를 나란히 비교할 수 있어야 한다.
    repos = Repositories(db)
    now = utc_now_iso()
    rid = _seed_job(db, repos, created_at=iso_plus(now, -3600))
    _seed_job(db, repos, created_at=iso_plus(now, -1800))     # 재료 NULL -> 제외
    db.execute(
        """UPDATE data_jobs SET exec_submitted_at = :x, sched_wait_seconds = 60,
               updated_at = :u WHERE request_id = :r""",
        {"x": iso_plus(now, -3000), "u": iso_plus(now, -2000), "r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    hist = {b["bucket"]: b["count"] for b in body["exec_runtime_histogram"]}
    assert list(hist) == ["<1m", "1-10m", "10-60m", "1-6h", "6-24h", ">24h"]
    assert hist["10-60m"] == 1                # 1000 - 60 = 940초
    assert body["exec_runtime_counted"] == 1
    assert body["exec_runtime_excluded"] == 1  # 앵커 없는 종단 잡의 제외를 표면화
    assert "exec_runtime_seconds" not in body  # 원자료는 내보내지 않는다(duration 규칙)
    # 숫자 요약: 표본 1개면 평균=중앙값=p95. 원자료 없이 요약만 내려간다.
    assert body["exec_runtime_summary"] == {
        "mean_seconds": 940.0, "p50_seconds": 940.0, "p95_seconds": 940.0}
    # 전체 수명 요약: 잡1 = 1600초(-3600 -> -2000), 잡2 = 0초(created=updated).
    # p50/p95 는 nearest-rank 실측값(0, 1600) -- 보간하지 않는다.
    assert body["duration_summary"] == {
        "mean_seconds": 800.0, "p50_seconds": 0.0, "p95_seconds": 1600.0}


def test_metrics_jobs_summaries_are_null_without_samples(client):
    # 표본 없음은 null -- 0 요약으로 뭉개면 "즉시 끝났다"는 거짓말이 된다.
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    assert body["duration_summary"] is None
    assert body["exec_runtime_summary"] is None
    assert body["exec_runtime_counted"] == 0
    assert body["exec_runtime_excluded"] == 0


def test_metrics_jobs_plan_rejected_fields(client, db):
    # 계획 거부 지표(results 집계)의 응답 형태 -- data_jobs 집계(전부 위 필드)와
    # 분리된 필드로 내려간다. 사유 목록은 failure_reasons 와 같은 모양.
    from dms.domain import RequestState
    repos = Repositories(db)
    now = utc_now_iso()
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key="k:rej:api", payload={}, priority="mid")
    repos.requests.set_state_with_result(
        rid, RequestState.REJECTED, reason_code="ldap_identity_not_found",
        actor="planner")
    db.execute("UPDATE results SET completed_at = :c WHERE request_id = :r",
               {"c": iso_plus(now, -3600), "r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    assert body["plan_rejected"] == 1
    assert body["plan_rejection_reasons"] == [
        {"reason_code": "ldap_identity_not_found", "count": 1}]


def test_metrics_jobs_plan_rejected_zero_is_zero(client):
    # 0 은 정상값(거부 없음)이다 -- null 로 뭉개지 않는다(null≠0).
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    assert body["plan_rejected"] == 0
    assert body["plan_rejection_reasons"] == []


def test_metrics_jobs_sched_wait_zero_counts_toward_the_total(client, db):
    # 라우트 층의 falsy 함정(submit_wait 선례 그대로): counted 를
    # `len([w for w in ws if w])` 로 세거나 원자료를 truthy 로 거르면 0(같은 틱
    # 스케줄 = 가장 건강한 잡)이 사라져 counted 가 조용히 줄고 첫 버킷이 빈다.
    repos = Repositories(db)
    now = utc_now_iso()
    rid = _seed_job(db, repos, created_at=iso_plus(now, -3600))
    db.execute("UPDATE data_jobs SET sched_wait_seconds = 0 WHERE request_id = :r",
               {"r": rid})
    body = client.get("/api/admin/metrics/jobs?window=24", headers=ADMIN).json()
    hist = {b["bucket"]: b["count"] for b in body["sched_wait_histogram"]}
    assert hist["<10s"] == 1
    assert body["sched_wait_counted"] == 1
    assert body["sched_wait_excluded"] == 0
