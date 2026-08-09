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
