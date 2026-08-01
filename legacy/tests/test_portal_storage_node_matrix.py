"""BFF storage-node matrix: worker node × filesystem-storage mount readiness.

Columns = registered FS storages only (CSI excluded); rows = nodes (a node's repeated
reports collapse into ONE row, keeping the newest report's host-level mounts/freshness);
cell = the node's probed mount status per storage (absent → 미구성). Grouped per cluster.
Reads the same agent-reports(mounts) + storage-mappings the dashboard already uses —
no DMS change.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.routers.dashboard import _storage_node_matrix, dashboard_router

DASH = "/api/operator/dashboard"


def _mapping(name: str, backend: str, cluster: str = "dms") -> dict[str, Any]:
    return {"storage_name": name, "cluster_name": cluster,
            "backend_template": {"backend_type": backend}}


def _report(node: str, mounts: list[dict[str, Any]], *, cluster: str = "dms",
            freshness: str = "Fresh", at: str = "2026-07-01T00:00:00Z"):
    # every agent report carries the single worker role, DM
    return {"cluster_name": cluster, "node_name": node, "worker_role": "DM",
            "freshness_status": freshness, "reported_at": at, "report": {"mounts": mounts}}


def _mnt(storage: str, status: str, *, writable: bool = True, path: str | None = None):
    return {"storage_name": storage, "status": status, "mount_path": path or f"/{storage}",
            "readable": status == "Ready", "writable": writable if status == "Ready" else False,
            "is_mountpoint": status == "Ready", "filesystem_type": "ceph", "reason": None}


def test_matrix_excludes_csi_and_builds_cells():
    mappings = [
        _mapping("cephfs-dms", "cephfs"),
        _mapping("cephfs-third", "cephfs"),
        _mapping("ceph-csi-vol", "ceph-csi"),   # CSI → excluded
        _mapping("k8s-csi-vol", "k8s-csi"),      # CSI → excluded
    ]
    reports = [
        # w1 reported twice (successive agent reports for the same host)
        _report("w1", [_mnt("cephfs-dms", "Ready"), _mnt("cephfs-third", "Ready")],
                at="2026-07-01T00:00:00Z"),
        _report("w1", [_mnt("cephfs-dms", "Ready"), _mnt("cephfs-third", "Ready")],
                at="2026-07-01T00:05:00Z"),
        _report("w2", [_mnt("cephfs-dms", "Ready"), _mnt("cephfs-third", "Missing")]),
    ]
    out = _storage_node_matrix(reports, mappings)
    assert len(out) == 1
    cl = out[0]
    assert cl["cluster_name"] == "dms"
    # only FS storages, sorted; CSI excluded
    assert [s["storage_name"] for s in cl["storages"]] == ["cephfs-dms", "cephfs-third"]
    # w1's two reports collapse into ONE row — 2 nodes, not 3 rows
    assert [n["node_name"] for n in cl["nodes"]] == ["w1", "w2"]
    nodes = {n["node_name"]: n for n in cl["nodes"]}
    # roles are a deduped union, so repeated DM reports yield a single role
    assert nodes["w1"]["roles"] == ["DM"]
    assert nodes["w1"]["cells"]["cephfs-dms"]["status"] == "Ready"
    assert nodes["w2"]["cells"]["cephfs-third"]["status"] == "Missing"
    # CSI storage never appears as a cell
    assert "ceph-csi-vol" not in nodes["w1"]["cells"]


def test_matrix_newest_report_wins_for_mounts():
    mappings = [_mapping("cephfs-dms", "cephfs")]
    # same node, two reports over time, fed NEWEST FIRST so "newest wins" can't be
    # satisfied by simply keeping the last row seen
    reports = [
        _report("w1", [_mnt("cephfs-dms", "Ready")],
                at="2026-07-01T01:00:00Z", freshness="Fresh"),
        _report("w1", [_mnt("cephfs-dms", "Missing")],
                at="2026-07-01T00:00:00Z", freshness="Stale"),
    ]
    out = _storage_node_matrix(reports, mappings)
    assert len(out[0]["nodes"]) == 1                          # one row per node
    node = out[0]["nodes"][0]
    assert node["cells"]["cephfs-dms"]["status"] == "Ready"   # newest wins
    # freshness/reported_at come from that same newest report, not the older one
    assert node["freshness"] == "Fresh"
    assert node["reported_at"] == "2026-07-01T01:00:00Z"


def test_matrix_unconfigured_storage_has_no_cell():
    mappings = [_mapping("cephfs-dms", "cephfs"), _mapping("cephfs-third", "cephfs")]
    # node only reports cephfs-dms → cephfs-third cell absent (→ 미구성 in UI)
    reports = [_report("w1", [_mnt("cephfs-dms", "Ready")])]
    out = _storage_node_matrix(reports, mappings)
    node = out[0]["nodes"][0]
    assert "cephfs-dms" in node["cells"]
    assert "cephfs-third" not in node["cells"]


def test_matrix_cluster_without_fs_storage_skipped():
    # cluster has a node but only CSI mappings → no FS matrix for it
    mappings = [_mapping("k8s-csi-vol", "k8s-csi", cluster="edge")]
    reports = [_report("e1", [], cluster="edge")]
    assert _storage_node_matrix(reports, mappings) == []


def test_matrix_multi_cluster_grouped():
    mappings = [_mapping("cephfs-dms", "cephfs", cluster="a"),
                _mapping("gpfs-x", "gpfs", cluster="b")]
    reports = [
        _report("a1", [_mnt("cephfs-dms", "Ready")], cluster="a"),
        _report("b1", [_mnt("gpfs-x", "Missing")], cluster="b"),
    ]
    out = _storage_node_matrix(reports, mappings)
    assert [c["cluster_name"] for c in out] == ["a", "b"]


# --- endpoint via TestClient ---------------------------------------------------

class FakeDms:
    def __init__(self, reports, mappings):
        self._reports = reports
        self._mappings = mappings

    async def list_agent_reports(self, *, actor, latest_per_node=False, **kw):
        return list(self._reports)

    async def list_storage_mappings(self, *, actor=None, limit=10000, **kw):
        return list(self._mappings)


def test_endpoint_returns_matrix():
    dms = FakeDms(
        reports=[_report("w1", [_mnt("cephfs-dms", "Ready")])],
        mappings=[_mapping("cephfs-dms", "cephfs"), _mapping("csi", "ceph-csi")],
    )
    app = FastAPI()
    app.include_router(dashboard_router(Settings()))
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    body = TestClient(app).get(f"{DASH}/storage-node-matrix").json()
    assert len(body["clusters"]) == 1
    assert [s["storage_name"] for s in body["clusters"][0]["storages"]] == ["cephfs-dms"]
    assert body["clusters"][0]["nodes"][0]["cells"]["cephfs-dms"]["status"] == "Ready"
