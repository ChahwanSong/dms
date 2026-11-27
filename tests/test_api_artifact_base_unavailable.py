"""2026-11-23 「base 접근 불가(503 artifact_base_unavailable)」 vs 「그 잡의 아티팩트 없음(404)」 -- 라우트 분류, hung 단락,
관리 API(checks.api 새 모양·api_pods·health·:recheck·PUT kick), metrics/infra, 마운트 현황(/api/admin/mounts)."""
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


def _register_scan_path(client, storage="ceph-a", path="team"):
    client.post("/api/admin/storages", json={"storage_name": storage, "mount_path": f"/mnt/{storage}",
                                             "managed_root": f"/mnt/{storage}/dms", "backend_type": "cephfs"},
                headers=ADMIN)
    r = client.post("/api/user/scan-paths", json={"storage_name": storage, "path": path})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_scan_path_stats_503_when_base_is_gone_instead_of_no_covering_scan(tmp_path):
    base = tmp_path / "not-mounted"
    client = _client(tmp_path, base)
    _login(client)
    pid = _register_scan_path(client)
    _job(client, storage="ceph-a", target="team", base=base)
    r = client.get(f"/api/user/scan-paths/{pid}/stats")
    assert (r.status_code, r.json()["detail"]) == (503, "artifact_base_unavailable")


def test_scan_path_stats_same_request_success_means_not_down(tmp_path):
    # 같은 요청에서 읽기가 하나라도 성공했으면 base 는 보였다 -- 뒤 후보의 실패는 그 파일의 일(404 그대로).
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    _login(client)
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
    assert ab["api_pods_failing_waiting"] == 1                   # 그 파드는 시작 게이트 대기(서비스 밖)
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
    assert ab["api_pods_failing_waiting"] == 0


def test_metrics_infra_failing_waiting_counts_only_gate_waiting_pods(tmp_path):
    """api_pods_failing_waiting = 신선한 실패 행 중 gate_state waiting 인 수(서비스 밖) -- 서비스 중 실패(passed·
    started_degraded)·성공인 대기 파드·보고 끊긴 대기 파드는 세지 않는다(dms-k8s e25a2dfb 이식)."""
    base = _mkbase(tmp_path)
    client = _client(tmp_path, base)
    repos = client.app.state.repos
    now = utc_now_iso()
    old = iso_plus(now, -3600)
    for inst, ok, gate, at in (("api-wait", False, "waiting", now), ("api-serving", False, "passed", now),
                               ("api-degraded", False, "started_degraded", now), ("api-wait-ok", True, "waiting", now),
                               ("api-wait-stale", False, "waiting", old)):
        repos.api_base_checks.upsert({"instance": inst, "ok": ok, "uri": "u", "gate_state": gate,
                                      "started_at": at, "checked_at": at})
    ab = client.get("/api/admin/metrics/infra", headers=ADMIN).json()["artifact_base"]
    assert ab["api_pods_failing"] == 3
    assert ab["api_pods_failing_waiting"] == 1


# ---- 마운트 현황 ------------------------------------------------------------------------------------------------------------

def _agent_report(repos, node, mounts, *, artifact_base=None, k8s_node=None, reported_at=None):
    report = {"node_name": node, "probe_mode": "host_root", "mounts": mounts}
    if artifact_base is not None:
        report["artifact_base"] = artifact_base
    if k8s_node is not None:
        report["k8s_node"] = k8s_node
    repos.db.execute("INSERT INTO agent_nodes (node_name, report, reported_at) VALUES (:n, :r, :t)",
                     {"n": node, "r": json.dumps(report), "t": reported_at or utc_now_iso()})


def _mount(name, path, status, *, writable=True, reason=None):
    return {"storage_name": name, "mount_path": path, "exists": status == "Ready", "is_mountpoint": status == "Ready",
            "readable": status == "Ready", "writable": writable, "status": status, "reason": reason}


def test_mounts_matrix_cells_roles_and_summary(tmp_path):
    client = _client(tmp_path, "/cephfs/dms/artifacts")
    repos = client.app.state.repos
    for name, path, enabled in (("ceph", "/cephfs", True), ("ceph-dms", "/cephfs/dms", True),
                                ("old", "/old", False), ("weka", "/weka", True)):
        repos.storages.create(storage_name=name, mount_path=path, managed_root=f"{path}/m", backend_type="cephfs",
                              actor="t")
        if not enabled:
            repos.db.execute("UPDATE storages SET enabled = 0 WHERE storage_name = :n", {"n": name})
    base_probe = {"path": "/cephfs/dms/artifacts", "exists": True, "writable": True}
    _agent_report(repos, "w1", [_mount("ceph", "/cephfs", "Ready"), _mount("ceph-dms", "/cephfs/dms", "Ready"),
                                _mount("weka", "/weka", "Ready", writable=False)],
                  artifact_base=base_probe, k8s_node={"schedulable": True})
    # 사고 노드: base 디렉터리는 있고 쓰기도 되지만(빈 로컬) 덮는 스토리지가 마운트되지 않았다
    _agent_report(repos, "w2", [_mount("ceph", "/cephfs", "Missing", reason="not_a_mountpoint"),
                                _mount("ceph-dms", "/cephfs/dms", "Missing", reason="not_a_mountpoint"),
                                _mount("weka", "/weka-old", "Ready")],
                  artifact_base=base_probe, k8s_node={"schedulable": False, "reason": "cordoned"})
    _agent_report(repos, "w3", [_mount("ceph", "/cephfs", "Missing", reason="missing_mount_path")],
                  reported_at=iso_plus(utc_now_iso(), -3600))                      # 보고 끊김
    repos.node_exclusions.exclude("w1", reason=None, actor="t")
    now = utc_now_iso()
    repos.api_base_checks.upsert({"instance": "api-1", "node_name": "cp1", "uri": "file:///cephfs/dms/artifacts",
                                  "ok": False, "reason": "artifact_base_jobs_missing", "gate_state": "waiting",
                                  "started_at": now, "checked_at": now})
    repos.control.set_artifact_base_check(uri="file:///cephfs/dms/artifacts", ok=True, reason=None, node="w1")
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    assert body["artifact_base"] == {"uri": "file:///cephfs/dms/artifacts", "path": "/cephfs/dms/artifacts",
                                     "covering_storage": "ceph-dms"}                # 가장 긴 접두
    assert [s["storage_name"] for s in body["storages"]] == ["ceph", "ceph-dms", "old", "weka"]
    nodes = {n["node_name"]: n for n in body["nodes"]}
    assert list(nodes) == ["cp1", "w1", "w2", "w3"]
    w1, w2, w3, cp1 = nodes["w1"], nodes["w2"], nodes["w3"], nodes["cp1"]
    assert {k: v["status"] for k, v in w1["cells"].items()} == {
        "ceph": "ready", "ceph-dms": "ready", "old": "disabled", "weka": "readonly"}
    assert (w1["excluded"], w1["schedulable"], w1["roles"], w1["has_agent"], w1["fresh"]) == (
        True, True, ["controller"], True, True)
    assert w1["artifact_base"]["status"] == "ready"
    assert w1["artifact_base"]["controller"]["ok"] is True
    # 옛 에이전트(mount_point·fstype 키 없음)는 None(모름) -- 로컬 디렉터리 판정을 하지 않는다
    assert w1["artifact_base"]["agent"] == {"exists": True, "writable": True, "path_matches": True,
                                            "mount_point": None, "fstype": None}
    assert w2["cells"]["ceph-dms"] == {"status": "missing", "reason": "not_a_mountpoint", "exists": False,
                                       "is_mountpoint": False, "readable": False, "writable": True,
                                       "problem": True}
    assert w2["cells"]["weka"]["status"] == "unreported"         # 옛 경로의 보고
    assert w2["artifact_base"]["status"] == "missing"
    assert w2["artifact_base"]["reason"] == "covering_storage_missing:ceph-dms"
    assert (w2["schedulable"], w2["schedulable_reason"]) == (False, "cordoned")
    assert w3["fresh"] is False and w3["cells"]["weka"]["status"] == "unreported"
    # 경로 자체가 없는 Missing = absent(사유 유지); 끊긴 노드의 칸은 문제 아님
    assert (w3["cells"]["ceph"]["status"], w3["cells"]["ceph"]["reason"], w3["cells"]["ceph"]["problem"]) == (
        "absent", "missing_mount_path", False)
    # weka 는 w1 만 신선하게 보고했고 그게 readonly -- 같은 열에 쓰기 가능 노드가 없으니 문제 아님(모두 ro 인 열)
    assert w1["cells"]["weka"]["problem"] is False and w1["artifact_base"]["problem"] is False
    assert w2["artifact_base"]["problem"] is True and cp1["artifact_base"]["problem"] is True
    assert cp1["has_agent"] is False and cp1["roles"] == ["api"]
    assert all(c["status"] in ("unreported", "disabled") for c in cp1["cells"].values())
    assert cp1["artifact_base"]["api"][0]["ok"] is False
    s = body["summary"]
    # w1: weka readonly 지만 같은 열에 쓰기 가능한 신선 노드가 없어 0 / w2: ceph·ceph-dms missing 2 + base missing 1 /
    # cp1: API 점검 실패 1 / w3: 끊김이라 셈 밖
    assert (s["node_count"], s["fresh_node_count"], s["stale_node_count"], s["storage_count"]) == (4, 2, 1, 4)
    assert (s["problem_cells"], s["problem_nodes"]) == (4, 2)


def test_mounts_requires_admin_and_reads_no_filesystem(tmp_path, monkeypatch):
    client = _client(tmp_path, tmp_path / "missing")
    assert client.get("/api/admin/mounts").status_code == 401
    monkeypatch.setattr(os, "open", lambda *a, **k: (_ for _ in ()).throw(AssertionError("fs touched")))
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    assert body["nodes"] == [] and body["summary"]["problem_cells"] == 0


def test_mounts_base_column_counts_once_and_ignores_disabled_cover(tmp_path):
    # 2026-11-23 리뷰: (1) 「아티팩트 경로」 열은 한 칸 -- 덮는 스토리지 미마운트 + 같은 노드 API 점검 실패 + 컨트롤러 점검
    # 실패여도 1(포탈 mountView.baseProblem 과 같은 규칙). (2) 더 긴 **비활성** 스토리지가 base 를 덮어도 활성 중 가장 긴
    # 것을 고른다 -- 비활성 셀은 늘 disabled 라 사고 노드의 base 칸이 조용히 「정상」이 됐다. (3) readonly base 도 문제 1칸.
    client = _client(tmp_path, "/mgmt_storage/dms/artifacts")
    repos = client.app.state.repos
    for name, path in (("mgmt", "/mgmt_storage"), ("legacy", "/mgmt_storage/dms")):
        repos.storages.create(storage_name=name, mount_path=path, managed_root=f"{path}/m", backend_type="cephfs",
                              actor="t")
    repos.db.execute("UPDATE storages SET enabled = 0 WHERE storage_name = 'legacy'")
    base_probe = {"path": "/mgmt_storage/dms/artifacts", "exists": True, "writable": True}
    _agent_report(repos, "bad", [_mount("mgmt", "/mgmt_storage", "Missing", reason="not_a_mountpoint")],
                  artifact_base=base_probe)
    _agent_report(repos, "ro", [_mount("mgmt", "/mgmt_storage", "Ready")],
                  artifact_base={**base_probe, "writable": False})
    # readonly base 는 다른 신선한 노드의 base 가 쓰기 가능(ready)일 때만 문제다(2026-11-27) -- 그 동료
    _agent_report(repos, "ok", [_mount("mgmt", "/mgmt_storage", "Ready")], artifact_base=base_probe)
    now = utc_now_iso()
    repos.api_base_checks.upsert({"instance": "api-x", "node_name": "bad", "uri": "file:///mgmt_storage/dms/artifacts",
                                  "ok": False, "reason": "artifact_base_jobs_missing", "gate_state": "waiting",
                                  "started_at": now, "checked_at": now})
    repos.control.set_artifact_base_check(uri="file:///mgmt_storage/dms/artifacts", ok=False,
                                          reason="artifact_base_jobs_missing", node="bad")
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    assert body["artifact_base"]["covering_storage"] == "mgmt"
    nodes = {n["node_name"]: n for n in body["nodes"]}
    assert nodes["bad"]["artifact_base"]["status"] == "missing"
    assert nodes["bad"]["artifact_base"]["reason"] == "covering_storage_missing:mgmt"
    assert nodes["ro"]["artifact_base"]["status"] == "readonly"
    assert [nodes[n]["artifact_base"]["problem"] for n in ("bad", "ok", "ro")] == [True, False, True]
    # bad: mgmt missing 1 + base 1(미마운트·API·컨트롤러 실패를 한 칸으로) / ro: base readonly 1 / ok: 0
    assert (body["summary"]["problem_cells"], body["summary"]["problem_nodes"]) == (3, 2)


def test_mounts_absent_is_not_a_problem_and_readonly_counts_only_beside_a_writable_peer(tmp_path):
    # 2026-11-27(dms-k8s 판정 이식): missing_mount_path = absent(그 노드엔 그 스토리지가 없다 -- 문제 아님, 사유 유지).
    # readonly 는 같은 열에 신선한 ready 노드가 하나라도 있을 때만 문제(모든 노드 ro 인 이관 소스 열은 문제 아님).
    # 끊긴 노드의 ready 는 동료로 세지 않는다.
    client = _client(tmp_path, "/cephfs/dms/artifacts")
    repos = client.app.state.repos
    for name, path in (("ceph", "/cephfs"), ("src", "/src"), ("weka", "/weka")):
        repos.storages.create(storage_name=name, mount_path=path, managed_root=f"{path}/m", backend_type="cephfs",
                              actor="t")
    base_probe = {"path": "/cephfs/dms/artifacts", "exists": True, "writable": True,
                  "mount_point": "/cephfs", "fstype": "ceph"}
    _agent_report(repos, "n1", [_mount("ceph", "/cephfs", "Ready"), _mount("src", "/src", "Ready", writable=False),
                                _mount("weka", "/weka", "Ready")], artifact_base=base_probe)
    _agent_report(repos, "n2", [_mount("ceph", "/cephfs", "Ready"), _mount("src", "/src", "Ready", writable=False),
                                _mount("weka", "/weka", "Ready", writable=False)], artifact_base=base_probe)
    _agent_report(repos, "n3", [_mount("ceph", "/cephfs", "Ready"),
                                _mount("src", "/src", "Missing", writable=False, reason="missing_mount_path"),
                                _mount("weka", "/weka", "Missing", writable=False, reason="missing_mount_path")],
                  artifact_base=base_probe)
    # 끊긴 노드: src 를 쓰기 가능으로 붙였지만 지금 상태를 모른다 -- 동료가 아니다
    _agent_report(repos, "old", [_mount("src", "/src", "Ready")], reported_at=iso_plus(utc_now_iso(), -3600))
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    nodes = {n["node_name"]: n for n in body["nodes"]}
    n1, n2, n3, old = nodes["n1"], nodes["n2"], nodes["n3"], nodes["old"]
    assert n3["cells"]["weka"] == {"status": "absent", "reason": "missing_mount_path", "exists": False,
                                   "is_mountpoint": False, "readable": False, "writable": False, "problem": False}
    assert n3["cells"]["src"]["status"] == "absent" and n3["cells"]["src"]["problem"] is False
    assert (n1["cells"]["src"]["status"], n1["cells"]["src"]["problem"]) == ("readonly", False)   # 모두 ro 인 열
    assert (n2["cells"]["src"]["status"], n2["cells"]["src"]["problem"]) == ("readonly", False)
    assert (n2["cells"]["weka"]["status"], n2["cells"]["weka"]["problem"]) == ("readonly", True)  # n1 이 쓰기 가능
    assert n1["cells"]["weka"]["problem"] is False and n1["cells"]["ceph"]["problem"] is False
    assert old["fresh"] is False and old["cells"]["src"]["problem"] is False
    assert all(n["artifact_base"]["status"] == "ready" and n["artifact_base"]["problem"] is False
               for n in (n1, n2, n3))
    assert (n1["artifact_base"]["agent"]["mount_point"], n1["artifact_base"]["agent"]["fstype"]) == ("/cephfs", "ceph")
    # 문제 = n2 의 weka 한 칸뿐(summary 는 플래그만 센다)
    assert (body["summary"]["problem_cells"], body["summary"]["problem_nodes"]) == (1, 1)


def test_mounts_base_on_root_fs_is_local_dir_and_readonly_base_needs_a_writable_peer(tmp_path):
    # 2026-11-27(dms-k8s d14ee402 이식): 에이전트가 base 를 덮는 마운트를 '/' 로 보고하면(공유 스토리지가 빠지고 노드
    # 루트 fs 의 로컬 디렉터리만 남음) exists·writable 이 참이어도 missing(artifact_base_local_dir). 덮는 등록 스토리지가
    # 없는 base 라 covering_storage_missing 겹침으로는 못 잡는 모양이다. readonly base 는 다른 신선한 노드의 base 가 ready
    # 일 때만 문제다 -- local 노드(missing)는 동료가 아니다.
    client = _client(tmp_path, "/shared/dms/artifacts")
    repos = client.app.state.repos
    shared = {"path": "/shared/dms/artifacts", "exists": True, "writable": True, "mount_point": "/shared",
              "fstype": "nfs4"}
    _agent_report(repos, "local", [], artifact_base={**shared, "mount_point": "/", "fstype": "ext4"})
    _agent_report(repos, "ro", [], artifact_base={**shared, "writable": False})
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    assert body["artifact_base"]["covering_storage"] is None
    nodes = {n["node_name"]: n for n in body["nodes"]}
    local, ro = nodes["local"]["artifact_base"], nodes["ro"]["artifact_base"]
    assert (local["status"], local["reason"], local["problem"]) == ("missing", "artifact_base_local_dir", True)
    assert local["agent"] == {"exists": True, "writable": True, "path_matches": True, "mount_point": "/",
                              "fstype": "ext4"}
    assert (ro["status"], ro["problem"]) == ("readonly", False)        # 쓰기 가능한 base 동료가 없다
    assert (body["summary"]["problem_cells"], body["summary"]["problem_nodes"]) == (1, 1)

    # 쓰기 가능한 동료가 생기면 readonly base 는 문제다
    _agent_report(repos, "good", [], artifact_base=shared)
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    nodes = {n["node_name"]: n for n in body["nodes"]}
    assert nodes["ro"]["artifact_base"]["problem"] is True and nodes["good"]["artifact_base"]["problem"] is False
    assert (body["summary"]["problem_cells"], body["summary"]["problem_nodes"]) == (2, 2)


def test_mounts_local_dir_needs_exists_and_current_path(tmp_path):
    # '/' 판정은 지금 base 를 잰 보고에서 exists 가 참일 때만 -- 없는 경로는 종전 사유(artifact_base_missing), 옛 base
    # 경로의 보고는 unreported(모름 ≠ 실패), 덮는 마운트가 빈 문자열·비 문자열이면 None(모름)으로 읽고 판정하지 않는다.
    client = _client(tmp_path, "/shared/dms/artifacts")
    repos = client.app.state.repos
    _agent_report(repos, "gone", [], artifact_base={"path": "/shared/dms/artifacts", "exists": False,
                                                    "writable": False, "mount_point": "/", "fstype": "ext4"})
    _agent_report(repos, "moved", [], artifact_base={"path": "/old/dms/artifacts", "exists": True,
                                                     "writable": True, "mount_point": "/", "fstype": "ext4"})
    _agent_report(repos, "junk", [], artifact_base={"path": "/shared/dms/artifacts", "exists": True,
                                                    "writable": True, "mount_point": "", "fstype": 7})
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    nodes = {n["node_name"]: n["artifact_base"] for n in body["nodes"]}
    assert (nodes["gone"]["status"], nodes["gone"]["reason"]) == ("missing", "artifact_base_missing")
    assert (nodes["moved"]["status"], nodes["moved"]["problem"]) == ("unreported", False)
    assert nodes["junk"]["status"] == "ready"
    assert (nodes["junk"]["agent"]["mount_point"], nodes["junk"]["agent"]["fstype"]) == (None, None)


def test_mounts_covering_storage_absent_still_marks_base_missing(tmp_path):
    # absent 는 스토리지 열에선 (다른 신선한 노드가 그 스토리지를 붙였으면) 문제 아니지만, base 는 모든 노드에 있어야 하므로
    # (잡·dms-api 파드가 hostPath 로 붙인다) 덮는 스토리지가 absent 인 노드의 base 칸은 missing(covering_storage_missing) 이고 문제다.
    client = _client(tmp_path, "/cephfs/dms/artifacts")
    repos = client.app.state.repos
    repos.storages.create(storage_name="ceph", mount_path="/cephfs", managed_root="/cephfs/m", backend_type="cephfs",
                          actor="t")
    _agent_report(repos, "n", [_mount("ceph", "/cephfs", "Missing", writable=False, reason="missing_mount_path")])
    _agent_report(repos, "peer", [_mount("ceph", "/cephfs", "Ready")])
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    nodes = {n["node_name"]: n for n in body["nodes"]}
    n = nodes["n"]
    assert (n["cells"]["ceph"]["status"], n["cells"]["ceph"]["problem"]) == ("absent", False)
    assert (n["artifact_base"]["status"], n["artifact_base"]["reason"], n["artifact_base"]["problem"]) == (
        "missing", "covering_storage_missing:ceph", True)
    assert nodes["peer"]["artifact_base"]["problem"] is False
    assert (body["summary"]["problem_cells"], body["summary"]["problem_nodes"]) == (1, 1)


def test_mounts_dead_mount_reporting_missing_mount_path_stays_missing(tmp_path):
    # 2026-11-27 리뷰: 죽은 ceph-fuse(ENOTCONN)·낡은 NFS(ESTALE)는 호스트 mountinfo 에 마운트가 남아(is_mountpoint 참) 있는데
    # isdir 가 오류를 삼켜 exists 거짓 → 에이전트 사유 missing_mount_path. 이것을 absent(제공 안 함)로 읽으면 끊긴 마운트가 숨는다
    # -- missing 이고 문제다. 호스트에 마운트가 없을 때(is_mountpoint 거짓·모름)만 absent.
    client = _client(tmp_path, "/shared/dms/artifacts")
    repos = client.app.state.repos
    repos.storages.create(storage_name="ceph", mount_path="/cephfs", managed_root="/cephfs/m", backend_type="cephfs",
                          actor="t")
    dead = {"storage_name": "ceph", "mount_path": "/cephfs", "exists": False, "is_mountpoint": True, "readable": False,
            "writable": False, "status": "Missing", "reason": "missing_mount_path"}
    _agent_report(repos, "dead", [dead])
    _agent_report(repos, "none", [{**dead, "is_mountpoint": False}])
    _agent_report(repos, "nokey", [{k: v for k, v in dead.items() if k != "is_mountpoint"}])   # 키 없는 보고 = 모름
    _agent_report(repos, "ok", [_mount("ceph", "/cephfs", "Ready")])
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    cells = {n["node_name"]: n["cells"]["ceph"] for n in body["nodes"]}
    assert cells["dead"] == {"status": "missing", "reason": "missing_mount_path", "exists": False, "is_mountpoint": True,
                             "readable": False, "writable": False, "problem": True}
    assert (cells["none"]["status"], cells["none"]["reason"], cells["none"]["problem"]) == (
        "absent", "missing_mount_path", False)
    assert (cells["nokey"]["status"], cells["nokey"]["problem"]) == ("absent", False)
    assert (body["summary"]["problem_cells"], body["summary"]["problem_nodes"]) == (1, 1)


def test_mounts_storage_absent_on_every_fresh_node_is_a_problem(tmp_path):
    # 2026-11-27 리뷰: mount_path 오타(/cephf)·마운트 배포 전처럼 신선한 어느 노드에도 그 스토리지가 마운트돼 있지 않으면(같은 열에
    # 신선한 ready·readonly 가 0) absent 도 문제다 -- 열 전체가 회색 「경로 없음」인데 요약이 「문제 0」이던 구멍. 끊긴 노드의 ready 는
    # 「어딘가엔 마운트」로 세지 않는다. readonly 만 있는 열이면 그 스토리지는 마운트돼 있다(absent 는 문제 아님).
    client = _client(tmp_path, "/shared/dms/artifacts")
    repos = client.app.state.repos
    for name, path in (("typo", "/cephf"), ("src", "/src")):
        repos.storages.create(storage_name=name, mount_path=path, managed_root=f"{path}/m", backend_type="cephfs",
                              actor="t")
    _agent_report(repos, "n1", [_mount("typo", "/cephf", "Missing", writable=False, reason="missing_mount_path"),
                                _mount("src", "/src", "Ready", writable=False)])
    _agent_report(repos, "n2", [_mount("typo", "/cephf", "Missing", writable=False, reason="missing_mount_path"),
                                _mount("src", "/src", "Missing", writable=False, reason="missing_mount_path")])
    _agent_report(repos, "old", [_mount("typo", "/cephf", "Ready")], reported_at=iso_plus(utc_now_iso(), -3600))
    body = client.get("/api/admin/mounts", headers=ADMIN).json()
    nodes = {n["node_name"]: n["cells"] for n in body["nodes"]}
    assert [(nodes[n]["typo"]["status"], nodes[n]["typo"]["problem"]) for n in ("n1", "n2")] == [
        ("absent", True), ("absent", True)]
    assert (nodes["n2"]["src"]["status"], nodes["n2"]["src"]["problem"]) == ("absent", False)   # n1 이 ro 로 붙였다
    assert (nodes["n1"]["src"]["status"], nodes["n1"]["src"]["problem"]) == ("readonly", False)
    assert nodes["old"]["typo"]["problem"] is False                                             # 끊긴 노드 칸은 셈 밖
    assert (body["summary"]["problem_cells"], body["summary"]["problem_nodes"]) == (2, 2)
