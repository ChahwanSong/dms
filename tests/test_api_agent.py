import pytest


def _agent_headers(node="node-a"):
    return {"Authorization": "Bearer tok-shared", "x-dms-actor": f"node:{node}"}


ADMIN = {"Authorization": "Bearer tok-shared"}
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


@pytest.mark.parametrize("bad", ["bad name", "a/b", "-lead", "trail-", ""])
def test_invalid_node_name_422(client, bad):
    # 본문 node_name 검증은 헤더와 독립이다 -- actor 헤더를 유효한 node:<이름> 으로
    # 두고 본문만 망가뜨려 라우트의 422 를 그대로 유지한다. (슬라이스 19 전에는
    # 헤더에도 같은 bad 값을 실었는데, 이제 그건 auth 게이트가 400 으로 먼저 잡는다 --
    # 아래 test_invalid_node_name_in_actor_header_400 이 그 경로를 따로 못박는다.)
    r = client.post("/api/agent/report", json={"node_name": bad},
                    headers=_agent_headers())
    assert r.status_code == 422 and r.json()["detail"] == "invalid_node_name"


@pytest.mark.parametrize("bad", ["bad name", "a/b", "-lead", "trail-", ""])
def test_invalid_node_name_in_actor_header_400(client, bad):
    # 슬라이스 19 actor 게이트: node: 접두가 붙어도 이름부가 DNS-1123 이 아니면
    # 인증 단계에서 400 이다. 라우트의 422 보다 앞서므로 잘못된 노드 이름은 이제
    # 요청 처리에 아예 도달하지 못한다.
    r = client.post("/api/agent/report", json={"node_name": bad},
                    headers={"Authorization": "Bearer tok-shared",
                             "x-dms-actor": f"node:{bad}"})
    assert r.status_code == 400 and r.json()["detail"] == "invalid_actor"


def test_report_is_persisted(client, db):
    client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert db.query_one("SELECT node_name FROM agent_nodes")["node_name"] == "node-a"
    assert len(db.query("SELECT id FROM agent_reports")) == 1


def test_report_response_carries_artifact_base_path(client, db):
    # 하달 경로(설계 §2.4b): 스킴 제거된 파일시스템 경로 -- 에이전트 프로브는
    # os.path 만 안다. DB 설정이 env 를 이긴다(resolve 와 같은 규칙).
    r = client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert r.json()["artifact_base_path"] == "/artifacts/dms"
    from dms.repositories import Repositories
    Repositories(db).control.set_artifact_base("file:///new/base", actor="ops")
    r = client.post("/api/agent/report", json=REPORT, headers=_agent_headers())
    assert r.json()["artifact_base_path"] == "/new/base"
