ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _ingest(client, node, seq=0):
    client.post("/api/agent/report",
                json={"node_name": node, "seq": seq},
                headers={"Authorization": "Bearer tok-shared",
                         "x-dms-actor": f"node:{node}"})


def test_nodes_require_admin(client):
    assert client.get("/api/admin/nodes").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/nodes").status_code == 403


def test_nodes_list_and_history(client):
    _ingest(client, "node-a", seq=1)
    _ingest(client, "node-a", seq=2)
    _ingest(client, "node-b")
    nodes = client.get("/api/admin/nodes", headers=ADMIN).json()
    assert [n["node_name"] for n in nodes] == ["node-a", "node-b"]
    assert all(n["fresh"] for n in nodes)  # 방금 수집 — 신선
    history = client.get("/api/admin/nodes/node-a/reports?limit=1",
                         headers=ADMIN).json()
    assert len(history) == 1 and history[0]["report"]["seq"] == 2


def test_unknown_node_404_and_limit_bound(client):
    r = client.get("/api/admin/nodes/ghost/reports", headers=ADMIN)
    assert r.status_code == 404 and r.json()["detail"] == "node_not_found"
    _ingest(client, "node-a")
    assert client.get("/api/admin/nodes/node-a/reports?limit=0",
                      headers=ADMIN).status_code == 422
    assert client.get("/api/admin/nodes/node-a/reports?limit=5000",
                      headers=ADMIN).status_code == 422
