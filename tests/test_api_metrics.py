from dms.db import iso_plus, utc_now_iso
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _report(*, load1=0.5, mem_total=100, mem_avail=50, rx=0, tx=0):
    # build_report(agent/runner.py)와 같은 모양 -- os 키 아래에 probe_os_metrics 반환
    return {"mounts": [], "tools": [], "identities": [],
            "os": {"load1": load1, "load5": 0.4, "load15": 0.3,
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
    assert body["job_image"] == {"live": None,
                                 "manifest": "pkg-01:5000/dms-mpifileutils:job9"}


def test_metrics_infra_manifest_fail_soft_all_none(client, monkeypatch):
    # 동봉본이 없는 이미지(COPY 이전 빌드)에서도 라우트는 200 -- 값만 전량 null(설계 §4)
    client.app.state.rollout_runner = _FakeObserver()
    _stub_manifests(monkeypatch)
    r = client.get("/api/admin/metrics/infra", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert all(c["manifest_image"] is None for c in body["components"])
    assert body["job_image"] == {"live": None, "manifest": None}


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


def test_metrics_queue_stub_pair_serves_without_cluster(client):
    # 주입 없이 그대로 -- conftest 기본 백엔드(stub)의 wiring 이 StubQueueReader 를
    # 꽂는다. 이 스텁 페어가 없으면 모든 로컬·CI 가 여기서 500 이다(설계 §2.5).
    body = client.get("/api/admin/metrics/queue", headers=ADMIN).json()
    assert body == {"queue": {"name": "dms-data", "state": "Open"},
                    "podgroups": []}


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
    assert r.json() == {"queue": None, "podgroups": None}


def test_metrics_queue_empty_list_is_not_null(client):
    # 반대 방향도 고정: 정말 빈 큐([])가 null 로 승격되면 "알 수 없음" 경고가
    # 정상 상태에 뜬다.
    client.app.state.queue_reader = _FakeQueueReader(queue=None, podgroups=[])
    assert client.get("/api/admin/metrics/queue", headers=ADMIN).json() == {
        "queue": None, "podgroups": []}


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
    assert r.json() == {"queue": {"name": "dms-data", "state": "Closed"},
                        "podgroups": None}


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
