from dms.reconciler import reconcile_storages_once
from dms.repositories import Repositories

NOW = "2026-08-02T10:00:00Z"


def _setup(db, mounts_by_node):
    repos = Repositories(db)
    repos.storages.create(storage_name="ceph-a", mount_path="/mnt/ceph",
                          managed_root="/mnt/ceph/dms", backend_type="cephfs",
                          actor="admin")
    for node, mounts in mounts_by_node.items():
        repos.agents.ingest(node, {"node_name": node, "mounts": mounts},
                            reported_at="2026-08-02T09:59:00Z")
    return repos


def _mount(status):
    return {"storage_name": "ceph-a", "mount_path": "/mnt/ceph", "status": status}


def test_no_evidence_is_unknown(db):
    repos = _setup(db, {})
    assert reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW) == {
        "ceph-a": "Unknown"}
    row = repos.storages.get("ceph-a")
    assert row["status"] == "Unknown"
    assert row["status_detail"] == "no_fresh_agent_evidence"


def test_all_ready_is_ready(db):
    repos = _setup(db, {"n1": [_mount("Ready")], "n2": [_mount("Ready")]})
    assert reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW) == {
        "ceph-a": "Ready"}
    assert repos.storages.get("ceph-a")["status_detail"] == "ready_nodes=2"


def test_partial_ready_is_degraded(db):
    repos = _setup(db, {"n1": [_mount("Ready")], "n2": [_mount("Missing")]})
    reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW)
    row = repos.storages.get("ceph-a")
    assert row["status"] == "Degraded" and row["status_detail"] == "ready_nodes=1/2"


def test_stale_evidence_is_ignored(db):
    repos = _setup(db, {"n1": [_mount("Ready")]})
    # 리포트가 10분 전 — stale 300s 기준으로 무시 → Unknown
    result = reconcile_storages_once(repos, stale_seconds=300,
                                     now_iso="2026-08-02T10:09:00Z")
    assert result == {"ceph-a": "Unknown"}


def test_unchanged_status_does_not_touch_row(db):
    repos = _setup(db, {"n1": [_mount("Ready")]})
    reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW)
    before = repos.storages.get("ceph-a")["updated_at"]
    reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW)
    assert repos.storages.get("ceph-a")["updated_at"] == before


def test_disabled_storage_skipped(db):
    repos = _setup(db, {"n1": [_mount("Ready")]})
    repos.storages.update("ceph-a", mount_path="/mnt/ceph",
                          managed_root="/mnt/ceph/dms", backend_type="cephfs",
                          enabled=False, actor="admin")
    assert reconcile_storages_once(repos, stale_seconds=300, now_iso=NOW) == {}
