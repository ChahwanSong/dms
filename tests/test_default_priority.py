"""정책 default_priority 적용 (슬라이스 10 Task 3).

클라이언트가 priority를 명시하면 그 값이 이긴다. 생략하면 정책의 default_priority를
쓰고, 정책이 없거나 default_priority가 비어 있으면 mid로 폴백한다. sync는 제출 시점에
도구(dsync/nsync)가 미정이므로 dsync 정책을 대표로 읽는다. 배치가 materialize하는
자식 요청도 같은 규칙을 따른다.
"""
from dms.batch_orchestrator import BatchOrchestrator
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}

POLICY_BODY = {"max_nodes": 3, "procs_per_node": 8, "queue": "dms-data",
               "max_priority": "high", "preview_timeout_seconds": 3600,
               "execution_timeout_seconds": 3600, "enabled": True}


def _login(client, name):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _set_default_priority(client, tool, priority):
    r = client.put(f"/api/admin/policies/{tool}",
                   json={**POLICY_BODY, "default_priority": priority}, headers=ADMIN)
    assert r.status_code == 200, r.json()


def test_omitted_priority_uses_policy_default(client):
    _set_default_priority(client, "scan", "low")
    r = client.post("/api/user/requests", headers=ADMIN, json={
        "operation": "scan", "storage": "s1", "target": "a"})
    assert r.status_code == 202
    detail = client.get(f"/api/user/requests/{r.json()['request_id']}",
                        headers=ADMIN).json()
    assert detail["priority"] == "low"


def test_explicit_priority_overrides_policy_default(client):
    _set_default_priority(client, "scan", "low")
    r = client.post("/api/user/requests", headers=ADMIN, json={
        "operation": "scan", "storage": "s1", "target": "a", "priority": "high"})
    assert r.status_code == 202
    detail = client.get(f"/api/user/requests/{r.json()['request_id']}",
                        headers=ADMIN).json()
    assert detail["priority"] == "high"


def test_missing_policy_falls_back_to_mid(client, db):
    _login(client, "carol")
    # rm 정책 행 자체를 지워서 "정책이 없다"를 시뮬레이션한다.
    db.execute("DELETE FROM policies WHERE tool = 'rm'")
    r = client.post("/api/user/requests", json={
        "operation": "rm", "storage": "s1", "target": "a",
        "options": {"recursive": True}})
    assert r.status_code == 202
    detail = client.get(f"/api/user/requests/{r.json()['request_id']}").json()
    assert detail["priority"] == "mid"


def test_rm_uses_rm_policy_and_sync_uses_dsync_policy(client):
    _login(client, "dave")
    _set_default_priority(client, "rm", "low")
    _set_default_priority(client, "dsync", "high")
    # nsync 정책은 손대지 않는다(기본 mid) — sync 제출이 nsync가 아니라 dsync를
    # 대표로 읽는다는 것을 증명하려면 둘의 default_priority가 달라야 한다.
    _set_default_priority(client, "nsync", "mid")

    rm = client.post("/api/user/requests", json={
        "operation": "rm", "storage": "s1", "target": "a",
        "options": {"recursive": True}})
    assert rm.status_code == 202
    rm_detail = client.get(f"/api/user/requests/{rm.json()['request_id']}").json()
    assert rm_detail["priority"] == "low"

    sync = client.post("/api/user/requests", json={
        "operation": "sync", "source_storage": "s1", "source": "a",
        "destination_storage": "s2", "destination": "b"})
    assert sync.status_code == 202
    sync_detail = client.get(f"/api/user/requests/{sync.json()['request_id']}").json()
    assert sync_detail["priority"] == "high"


def test_batch_materialized_child_follows_same_rule(client, db):
    _set_default_priority(client, "scan", "low")
    repos = Repositories(db)
    bid = repos.batches.create(
        operation="scan", requester_id="admin", actor="admin", max_concurrency=2,
        options={}, note=None, items=[{"storage": "cephfs-dms", "target": "a"}],
        status="Running")

    class _S:
        preview_ttl_seconds = 900

    BatchOrchestrator(repos, settings=_S()).run_once()

    items = repos.batches.list_items(bid)
    assert items[0]["status"] == "Materialized"
    child = repos.requests.get(items[0]["request_id"])
    assert child["priority"] == "low"
