def _agent_headers(node="node-a"):
    return {"Authorization": "Bearer tok-shared", "x-dms-actor": f"node:{node}"}


ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}
REPORT = {"node_name": "node-a", "mounts": [], "tools": [], "identities": [], "os": {}}


def test_report_roundtrip_returns_config(client):
    client.post("/api/admin/storages", json={
        "storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}, headers=ADMIN)
    r = client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert r.status_code == 200
    body = r.json()
    assert body["storages"] == [{"storage_name": "ceph-a", "mount_path": "/mnt/ceph",
                                 "managed_root": "/mnt/ceph/dms"}]
    assert body["identity_probe_targets"] == []
    assert body["report_interval_seconds"] == 60


def test_disabled_storage_not_served(client):
    client.post("/api/admin/storages", json={
        "storage_name": "ceph-a", "mount_path": "/mnt/ceph",
        "managed_root": "/mnt/ceph/dms", "backend_type": "cephfs"}, headers=ADMIN)
    client.put("/api/admin/storages/ceph-a", json={
        "mount_path": "/mnt/ceph", "managed_root": "/mnt/ceph/dms",
        "backend_type": "cephfs", "enabled": False}, headers=ADMIN)
    r = client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert r.json()["storages"] == []


def test_actor_mismatch_403(client):
    r = client.post("/api/agent/report", json=REPORT,
                    headers=_agent_headers(node="node-b"))
    assert r.status_code == 403
    assert r.json()["detail"] == "agent_node_identity_mismatch"


def test_session_user_cannot_report(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    r = client.post("/api/agent/report", json=REPORT)
    assert r.status_code == 403


def test_invalid_node_name_422(client):
    r = client.post("/api/agent/report", json={"node_name": "bad name"},
                    headers={"Authorization": "Bearer tok-shared",
                             "x-dms-actor": "node:bad name"})
    assert r.status_code == 422 and r.json()["detail"] == "invalid_node_name"


def test_report_is_persisted(client, db):
    client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert db.query_one("SELECT node_name FROM agent_nodes")["node_name"] == "node-a"
    assert len(db.query("SELECT id FROM agent_reports")) == 1
