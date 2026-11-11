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


def test_procs_per_node_is_capped_at_the_worker_builder_limit(client):
    # 2026-10-16 리뷰: 빌더(execution_manifests.processes_per_node)는 워커당 1024 rank 를 넘으면 ValueError 다 -- 정책이
    # 그 위를 저장하면, 노드 CPU 상한을 모르는 동안 계획된 잡이 사전 점검까지 돈 뒤 submit_failed 로 불투명하게 끝났다.
    # 상한은 같은 상수를 import 한다(두 벌로 갈라지지 않게).
    from dms.execution_manifests import MAX_PROCESSES_PER_WORKER
    assert MAX_PROCESSES_PER_WORKER == 1024
    assert client.put("/api/admin/policies/scan", json={**BODY, "procs_per_node": 1025},
                      headers=ADMIN).status_code == 422
    assert client.get("/api/admin/policies/scan", headers=ADMIN).json()["procs_per_node"] != 1025
    assert client.put("/api/admin/policies/scan", json={**BODY, "procs_per_node": 1024},
                      headers=ADMIN).status_code == 200


@pytest.mark.parametrize("field", ["max_nodes", "preview_timeout_seconds", "execution_timeout_seconds"])
def test_integer_fields_are_capped_at_int4(client, field):
    # 2026-11-11: policies 의 정수 칸은 INTEGER(int4) -- 상한이 없으면 9999999999 가 검증을 다 통과한 뒤 PostgreSQL 22003 으로
    # 처리되지 않은 500 이 됐다(SQLite 테스트 DB 는 그대로 받아 못 잡는다). 저장 전에 422 로 막는다.
    from dms.api.routes_policies import INT4_MAX
    assert INT4_MAX == 2147483647
    assert client.put("/api/admin/policies/scan", json={**BODY, field: INT4_MAX + 1},
                      headers=ADMIN).status_code == 422
    assert client.put("/api/admin/policies/scan", json={**BODY, field: INT4_MAX},
                      headers=ADMIN).status_code == 200


@pytest.mark.parametrize("queue", ["", "   ", "dms data", "DMS-DATA", "-dms", "dms-", "x" * 254])
def test_queue_must_be_a_k8s_name(client, queue):
    # 2026-11-11: 빈 큐는 Volcano 가 default 큐로 채워 dms-data 한도 밖에서 돌고, 공백·대문자 섞인 이름은 없는 큐라 그 도구의
    # 잡이 전부 생성 실패한다 -- 저장 시점에 422 invalid_policy.
    before = client.get("/api/admin/policies/scan", headers=ADMIN).json()["queue"]
    r = client.put("/api/admin/policies/scan", json={**BODY, "queue": queue}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_policy"
    assert client.get("/api/admin/policies/scan", headers=ADMIN).json()["queue"] == before


def test_queue_surrounding_whitespace_is_trimmed(client):
    r = client.put("/api/admin/policies/scan", json={**BODY, "queue": "  dms-data.q1 "}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["queue"] == "dms-data.q1"
