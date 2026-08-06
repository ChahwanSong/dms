ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def test_control_state_requires_admin(client):
    assert client.get("/api/admin/control-state").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/control-state").status_code == 403


def test_control_state_get_defaults(client):
    body = client.get("/api/admin/control-state", headers=ADMIN).json()
    assert body["maintenance"] == 0
    assert body["drain"] == 0


def test_control_state_put_updates_and_returns_current(client):
    res = client.put("/api/admin/control-state",
                     json={"maintenance": True, "drain": False, "reason": "점검"},
                     headers=ADMIN)
    assert res.status_code == 200
    body = res.json()
    assert body["maintenance"] == 1 and body["drain"] == 0
    assert body["reason"] == "점검"
    assert client.get("/api/admin/control-state", headers=ADMIN).json()["maintenance"] == 1


def test_control_state_put_is_audited(client, db):
    client.put("/api/admin/control-state",
               json={"maintenance": False, "drain": True, "reason": None},
               headers=ADMIN)
    rows = db.query("SELECT * FROM audit_log WHERE mutation_class = 'control_state'")
    assert len(rows) == 1
    assert rows[0]["operation"] == "set"


def test_control_state_rejects_unknown_build_node(client):
    # I1: build_node_name은 agent_nodes에 보고된 노드 이름 중에서만 골라야 한다 --
    # 자유 입력 오타가 nodeSelector로 새면 빌드 파드가 영원히 Pending이다(C2의 최빈
    # 트리거). 노드를 하나도 등록하지 않은 채로 PUT하면 422로 거절돼야 한다.
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": "dms-w1-typo"},
                   headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_build_node"


def test_control_state_accepts_a_reported_agent_node(client, db):
    from dms.repositories import Repositories
    Repositories(db).agents.ingest("dms-w1", {})
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": "dms-w1"},
                   headers=ADMIN)
    assert r.status_code == 200 and r.json()["build_node_name"] == "dms-w1"


def test_control_state_put_without_build_node_skips_the_agent_node_check(client):
    # build_node_name을 아예 안 주거나 공백만 주면(=미설정으로 정규화) 노드 존재
    # 검사 자체를 건너뛴다 -- 지정 안 함은 항상 허용돼야 한다.
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None},
                   headers=ADMIN)
    assert r.status_code == 200 and r.json()["build_node_name"] is None
