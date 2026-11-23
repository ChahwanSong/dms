"""2026-11-23 「base 접근 불가(503 artifact_base_unavailable)」 vs 「그 잡의 아티팩트 없음(404)」 -- 라우트 분류, hung 단락,
관리 API(checks.api 새 모양·api_pods·health·:recheck·PUT kick), metrics/infra. (dms-ssc 의 마운트 현황 /api/admin/mounts
테스트는 옮기지 않았다 -- dms-k8s 는 자기 마운트 현황 GET /api/admin/mount-status 를 쓴다, tests/test_api_mount_status.py·test_mount_status*.py.)"""
import dataclasses
import json
import os
import threading

from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.config import Settings
from dms.db import Database, iso_plus, utc_now_iso
from dms.domain import DataJobState, RequestState
from dms.migrations import migrate

ADMIN = {"Authorization": "Bearer tok-shared"}


def _client(tmp_path, base, **extra):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    settings = Settings(database_url="unused", shared_token="tok-shared", admin_token="tok-admin",
                        session_secret="sess-secret", artifact_base_uri=f"file://{base}", **extra)
    app = create_app(settings, db)
    # 잡 디렉터리 교차 확인은 실 실행 백엔드에서만(identity_job_ids) -- 판정이 읽는 설정만 volcano 로(어댑터는 이미 stub).
    app.state.settings = dataclasses.replace(settings, execution_backend="volcano")
    return TestClient(app)


def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _job(client, *, requester="alice", operation="scan", storage="s1", target="team", base=None,
         state=DataJobState.SUCCEEDED, with_uri=False, report=None, updated_at=None):
    repos = client.app.state.repos
    rid = repos.requests.create(operation=operation, requester_id=requester, actor=requester,
                                resource_key=f"k:{os.urandom(4).hex()}",
                                payload={"storage": storage, "target": target}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, repos.data_jobs.create_plan(rid, actor="planner"), operation=operation, priority="mid",
        storage_name=storage, target=target, options={}, tool="dscan",
        worker_pool={"identity": {"uid": os.getuid(), "gid": os.getgid(), "username": requester}},
        precondition={}, actor="planner")
    if with_uri:
        repos.data_jobs.set_artifact(jid, artifact_uri=f"file://{base}/{jid}", result_summary={})
    repos.data_jobs.set_job_state(jid, state, actor="stepper")
    if state == DataJobState.SUCCEEDED:
        repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="stepper")
    if updated_at is not None:
        repos.db.execute("UPDATE data_jobs SET updated_at = :u, created_at = :u WHERE job_id = :j",
                         {"u": updated_at, "j": jid})
    if report is not None:
        d = base / jid / "execution"
        d.mkdir(parents=True)
        (d / "dscan-report.json").write_text(report if isinstance(report, str) else json.dumps(report))
    return rid, jid


def _mkbase(tmp_path, name="artifacts"):
    b = tmp_path / name
    b.mkdir()
    b.chmod(0o755)
    return b


# ---- 사용자 아티팩트 라우트 ------------------------------------------------------------------------------------------------

def test_empty_local_base_with_recent_jobs_missing_is_503(tmp_path):
    # 사고 서명: base 경로는 있는데(미마운트 노드의 빈 로컬 디렉터리) DB 의 최근 성공 잡 디렉터리가 하나도 없다.
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    _job(client, base=base, with_uri=True)                       # 최근 성공 잡(디렉터리 없음)
    _, jid = _job(client, base=base)
    _login(client)
    for url in (f"/api/user/jobs/{jid}/artifacts", f"/api/user/jobs/{jid}/artifacts/execution/stdout.log",
                f"/api/user/jobs/{jid}/artifacts/execution/stdout.log/download"):
        r = client.get(url)
        assert (r.status_code, r.json()["detail"]) == (503, "artifact_base_unavailable"), url


def test_healthy_base_keeps_404_for_missing_and_forbidden_alike(tmp_path):
    # base 가 보이면 단순 미존재·봉쇄 실패(심링크 탈출) 모두 같은 404 -- 503 판정은 base 수준이라 파일과 무관하다(오라클 아님).
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    _, recent = _job(client, base=base, with_uri=True)
    (base / recent).mkdir()                                      # 최근 잡 디렉터리가 보인다 = 진짜 공유 스토리지
    _, jid = _job(client, base=base)
    d = base / jid / "execution"
    d.mkdir(parents=True)
    outside = tmp_path / "secret"
    outside.write_text("x")
    (d / "link.log").symlink_to(outside)
    _login(client)
    for name in ("missing.log", "link.log"):
        r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/{name}")
        assert (r.status_code, r.json()["detail"]) == (404, "artifact_not_found"), name
        r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/{name}/download")
        assert (r.status_code, r.json()["detail"]) == (404, "artifact_not_found"), name


def test_requester_cannot_turn_404_into_503_by_breaking_their_phase_dir(tmp_path):
    # 요청자가 자기 <phase> 를 chmod 000 해도 판정은 base 수준이라 404 그대로(errno 를 응답에 반영하지 않는다).
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    _, jid = _job(client, base=base)
    d = base / jid / "execution"
    d.mkdir(parents=True)
    (d / "stdout.log").write_text("x")
    d.chmod(0o000)
    _login(client)
    try:
        r = client.get(f"/api/user/jobs/{jid}/artifacts/execution/stdout.log")
        assert r.status_code in (200, 404)          # root 면 읽히고(200), 아니면 404 -- 어느 쪽도 503 이 아니다
        assert client.get(f"/api/user/jobs/{jid}/artifacts").status_code == 200
    finally:
        d.chmod(0o755)


def test_cached_probe_skips_the_recent_jobs_query(tmp_path, monkeypatch):
    # 실행 전 잡의 빈 목록 폴링마다 프로브가 돈다 -- runner 캐시(3초)가 맞으면 최근 잡 DB 질의도 하지 않는다.
    import dms.api.base_monitor as bm
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    _, jid = _job(client, base=base)
    _login(client)
    calls = []
    real = bm.identity_job_ids
    monkeypatch.setattr(bm, "identity_job_ids", lambda *a, **k: calls.append(1) or real(*a, **k))
    client.app.state.base_check_runner._clock = lambda: 1000.0     # 캐시 창 안에 고정(느린 테스트 호스트 무관)
    for _ in range(3):
        assert client.get(f"/api/user/jobs/{jid}/artifacts").status_code == 200
    assert calls == [1]


def test_hung_mount_short_circuits_before_opening_anything(tmp_path, monkeypatch):
    # 같은 키의 점검이 마감을 넘겨 멈춰 있으면 라우트는 FS 를 열지 않고 바로 503 -- 요청 스레드가 하나씩 매달려 스레드풀이
    # 고갈되지 않게.
    import dms.api.routes_artifacts as ra
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    _, jid = _job(client, base=base)
    _login(client)
    runner = client.app.state.base_check_runner
    release = threading.Event()
    runner.run(("api", str(base)), lambda: release.wait(10), timeout=0.05)
    monkeypatch.setattr("dms.artifact_base.CHECK_TIMEOUT_SECONDS", 0.05)
    original = runner.stuck_since
    monkeypatch.setattr(runner, "stuck_since", lambda key, timeout=0.05: original(key, timeout=0.05))
    monkeypatch.setattr(ra, "list_artifacts", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    monkeypatch.setattr(ra, "read_artifact", lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    try:
        for url in (f"/api/user/jobs/{jid}/artifacts", f"/api/user/jobs/{jid}/artifacts/execution/x.log"):
            r = client.get(url)
            assert (r.status_code, r.json()["detail"]) == (503, "artifact_base_unavailable"), url
    finally:
        release.set()


def _login_privileged(client, name="alice"):
    # dms-k8s: 스캔 경로 등록·통계는 면제자(can_run_as_root -- 관리자 + 특권 목록 + 세션) 전용이다(routes_scan_paths.
    # _require_unrestricted, scan_path_unrestricted_only). 클라이언트는 privileged_requesters 에 name 을 넣어 만든다.
    accounts = client.app.state.repos.accounts
    if accounts.get(name) is None:
        accounts.create(name, "p", "admin")
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": name, "password": "p"})


_PRIV = {"privileged_requesters": frozenset({"root", "admin", "alice"})}


def _register_scan_path(client, storage="ceph-a", path="team"):
    client.post("/api/admin/storages", json={"storage_name": storage, "mount_path": f"/mnt/{storage}",
                                             "managed_root": f"/mnt/{storage}/dms", "backend_type": "cephfs"},
                headers=ADMIN)
    r = client.post("/api/user/scan-paths", json={"storage_name": storage, "path": path})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_scan_path_stats_503_when_base_is_gone_instead_of_no_covering_scan(tmp_path):
    base = tmp_path / "not-mounted"
    client = _client(tmp_path, base, **_PRIV)
    _login_privileged(client)
    pid = _register_scan_path(client)
    _job(client, storage="ceph-a", target="team", base=base)
    r = client.get(f"/api/user/scan-paths/{pid}/stats")
    assert (r.status_code, r.json()["detail"]) == (503, "artifact_base_unavailable")


def test_scan_path_stats_same_request_success_means_not_down(tmp_path):
    # 같은 요청에서 읽기가 하나라도 성공했으면 base 는 보였다 -- 뒤 후보의 실패는 그 파일의 일(404 그대로).
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base, **_PRIV)
    _login_privileged(client)
    pid = _register_scan_path(client)
    _job(client, base=base, with_uri=True, updated_at="2026-01-01T00:00:00Z")   # 디렉터리 없는 최근 잡 → 판정은 down
    _job(client, storage="ceph-a", target="team", base=base, report="{not json",
         updated_at="2026-03-02T00:00:00Z")                                     # 읽기 성공, 파싱 실패
    _job(client, storage="ceph-a", target="team", base=base, updated_at="2026-03-01T00:00:00Z")   # 파일 없음
    r = client.get(f"/api/user/scan-paths/{pid}/stats")
    assert (r.status_code, r.json()["detail"]) == (404, "no_covering_scan")


# ---- 관리 API -------------------------------------------------------------------------------------------------------------

def test_admin_payload_uses_the_monitor_snapshot_when_started(tmp_path):
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    mon = client.app.state.base_monitor
    mon.instance = "api-1"
    mon.start(spawn=False)
    mon.tick()
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    api = body["checks"]["api"]
    assert set(api) == {"ok", "reason", "availability", "posture", "source", "instance", "node_name",
                        "checked_at", "fstype", "local_fs", "interval_seconds", "gate"}
    assert (api["ok"], api["reason"], api["source"], api["instance"], api["interval_seconds"]) == (
        True, None, "monitor", "api-1", 60)
    assert api["availability"] == {"ok": True, "reason": None, "jobs_checked": 0, "jobs_found": None}
    assert api["gate"] == {"enabled": True, "state": "waiting", "at": None}
    client.get("/readyz")
    gate = client.get("/api/admin/artifact-base", headers=ADMIN).json()["checks"]["api"]["gate"]
    assert gate["state"] == "passed" and gate["at"] is not None


def test_api_pods_list_orders_this_pod_then_failures_and_marks_freshness(tmp_path):
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    repos = client.app.state.repos
    mon = client.app.state.base_monitor
    mon.instance = "api-me"
    now = utc_now_iso()
    old = iso_plus(now, -3600)
    for inst, ok, gate, at in (("api-ok", True, "passed", now), ("api-bad", False, "waiting", now),
                               ("api-me", True, "passed", old), ("api-gone", True, "passed", old)):
        repos.api_base_checks.upsert({"instance": inst, "node_name": f"n-{inst}", "uri": f"file://{base}",
                                      "ok": ok, "reason": None if ok else "artifact_base_jobs_missing",
                                      "fstype": "ext4" if inst == "api-bad" else "ceph", "gate_state": gate,
                                      "started_at": at, "checked_at": at})
    pods = client.get("/api/admin/artifact-base", headers=ADMIN).json()["checks"]["api_pods"]
    assert [p["instance"] for p in pods] == ["api-me", "api-bad", "api-ok", "api-gone"]
    by = {p["instance"]: p for p in pods}
    assert by["api-me"]["this_pod"] is True and by["api-ok"]["this_pod"] is False
    assert (by["api-ok"]["fresh"], by["api-gone"]["fresh"]) == (True, False)
    assert (by["api-bad"]["local_fs"], by["api-ok"]["local_fs"]) == (True, False)
    assert set(by["api-bad"]) == {"instance", "node_name", "uri", "ok", "reason", "posture", "fstype", "local_fs",
                                  "gate_state", "started_at", "checked_at", "ready_at", "fresh", "this_pod"}


def test_health_is_light_and_follows_the_snapshot(tmp_path):
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    mon = client.app.state.base_monitor
    # 모니터가 돌지 않으면 즉석(쓰기 없음) 점검
    h = client.get("/api/admin/artifact-base/health", headers=ADMIN).json()
    assert set(h) == {"ok", "reason", "checked_at", "instance", "node_name"} and h["ok"] is True
    mon.start(spawn=False)
    assert client.get("/api/admin/artifact-base/health", headers=ADMIN).json()["ok"] is None   # 첫 회차 전 = 모름
    base.rename(tmp_path / "gone")
    mon.tick()
    h = client.get("/api/admin/artifact-base/health", headers=ADMIN).json()
    assert (h["ok"], h["reason"]) == (False, "artifact_base_missing")
    assert client.get("/api/admin/artifact-base/health").status_code == 401


def test_recheck_runs_a_fresh_check(tmp_path):
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    r = client.post("/api/admin/artifact-base:recheck", headers=ADMIN)
    assert r.status_code == 200 and r.json()["checks"]["api"]["ok"] is True
    base.rename(tmp_path / "gone")
    # 즉석 점검은 3초 캐시지만 :recheck 는 캐시를 읽지 않는다
    r = client.post("/api/admin/artifact-base:recheck", headers=ADMIN)
    assert r.json()["checks"]["api"]["reason"] == "artifact_base_missing"
    mon = client.app.state.base_monitor
    mon.start()
    try:
        (tmp_path / "gone").rename(base)
        r = client.post("/api/admin/artifact-base:recheck", headers=ADMIN)
        api = r.json()["checks"]["api"]
        assert (api["source"], api["ok"]) == ("monitor", True)
    finally:
        mon.stop()
    assert client.post("/api/admin/artifact-base:recheck").status_code == 401


def test_put_kicks_the_monitor(tmp_path):
    base = _mkbase(tmp_path)
    client = _client(tmp_path, tmp_path / "old")
    kicks = []
    client.app.state.base_monitor.kick = lambda: kicks.append(1)
    client.app.state.repos.accounts.create("boss", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "boss", "password": "pw"})
    r = client.put("/api/admin/artifact-base", json={"uri": f"file://{base}"})
    assert r.status_code == 200, r.text
    assert kicks == [1]


def test_metrics_infra_carries_artifact_base_summary(tmp_path):
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    repos = client.app.state.repos
    now = utc_now_iso()
    repos.api_base_checks.upsert({"instance": "api-x", "node_name": "n2", "uri": f"file://{base}", "ok": False,
                                  "reason": "artifact_base_jobs_missing", "gate_state": "waiting",
                                  "started_at": now, "checked_at": now})
    repos.api_base_checks.upsert({"instance": "api-stale", "ok": False, "uri": "u", "gate_state": "waiting",
                                  "started_at": iso_plus(now, -3600), "checked_at": iso_plus(now, -3600)})
    repos.control.set_artifact_base_check(uri=f"file://{base}", ok=True, reason=None, node="n1")
    ab = client.get("/api/admin/metrics/infra", headers=ADMIN).json()["artifact_base"]
    assert ab["api"] is None                                     # 모니터 미시작 = 모름
    assert ab["api_pods_failing"] == 1                           # 신선 행만
    assert ab["controller"] == {"pending": False, "ok": True, "reason": None, "node_name": "n1"}
    mon = client.app.state.base_monitor
    mon.instance = "api-me"
    mon.start(spawn=False)
    mon.tick()
    ab = client.get("/api/admin/metrics/infra", headers=ADMIN).json()["artifact_base"]
    assert ab["api"] == {"ok": True, "reason": None, "node_name": None, "instance": "api-me"}
    # 이 파드의 실패는 api 가 말한다 -- api_pods_failing 은 다른 파드만 센다(두 번 세지 않는다)
    mon.instance = "api-x"
    ab = client.get("/api/admin/metrics/infra", headers=ADMIN).json()["artifact_base"]
    assert ab["api_pods_failing"] == 0
