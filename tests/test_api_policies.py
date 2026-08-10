import pytest

ADMIN = {"Authorization": "Bearer tok-shared"}
BODY = {"max_nodes": 3, "procs_per_node": 8, "queue": "dms-data",
        "default_priority": "mid", "max_priority": "high",
        "preview_timeout_seconds": 3600, "execution_timeout_seconds": 3600,
        "enabled": True}


def test_policies_require_admin(client):
    assert client.get("/api/admin/policies").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/policies").status_code == 403


def test_policy_crud(client):
    assert client.put("/api/admin/policies/dsync", json=BODY,
                      headers=ADMIN).status_code == 200
    assert client.get("/api/admin/policies/dsync",
                      headers=ADMIN).json()["max_nodes"] == 3
    listed = client.get("/api/admin/policies", headers=ADMIN).json()
    # migrate()가 4개 도구를 시드하므로 목록은 항상 4행이고 정렬돼 있다
    assert [p["tool"] for p in listed] == ["dsync", "nsync", "rm", "scan"]
    # 시드된 scan 정책은 조회된다 (시드 전에는 404였다)
    assert client.get("/api/admin/policies/scan", headers=ADMIN).status_code == 200
    assert client.put("/api/admin/policies/dcp", json=BODY,
                      headers=ADMIN).status_code == 422
    assert client.put("/api/admin/policies/scan",
                      json={**BODY, "max_nodes": 0},
                      headers=ADMIN).status_code == 422


@pytest.mark.parametrize("value", [0, -1])
def test_preview_timeout_must_be_positive_or_null(client, value):
    # 0/음수는 그대로 activeDeadlineSeconds로 흘러가 파드 생성이 거부되고
    # 잡이 submit_failed로 죽는다 — execution_timeout_seconds와 같은 하한을 건다.
    assert client.put("/api/admin/policies/scan",
                      json={**BODY, "preview_timeout_seconds": value},
                      headers=ADMIN).status_code == 422
    # null(타임아웃 없음)은 여전히 유효하다
    assert client.put("/api/admin/policies/scan",
                      json={**BODY, "preview_timeout_seconds": None},
                      headers=ADMIN).status_code == 200
