from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dms.adapters import StaticKubernetesReadOnlyInventoryAdapter
from dms.agent_daemon import AgentDaemonConfig, build_agent_report, parse_mountinfo
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository


CEPHFS_DRIVER = "rook-ceph.cephfs.csi.ceph.com"
LONGHORN_DRIVER = "driver.longhorn.io"
API_HEADERS = {"x-dms-actor": "api-client"}


class FakeKubernetesClient:
    def node_uid(self, node_name: str) -> tuple[str | None, str | None]:
        return f"uid-{node_name}", None

    def csi_driver(self, name: str) -> tuple[dict | None, str | None]:
        return {"metadata": {"name": name}}, None

    def storage_class(self, name: str) -> tuple[dict | None, str | None]:
        provisioners = {
            "testbed-cephfs": CEPHFS_DRIVER,
            "testbed-longhorn": LONGHORN_DRIVER,
        }
        if name not in provisioners:
            return None, "kubernetes API HTTP 404"
        return {"metadata": {"name": name}, "provisioner": provisioners[name]}, None


def test_agent_probe_builds_phase8_report_from_real_observations(tmp_path):
    mount_path = tmp_path / "testbed-cephfs"
    mount_path.mkdir()
    mountinfo = (
        f"36 25 0:42 / {mount_path} rw,relatime - ceph "
        "rook-ceph rw,name=dms\n"
    )
    mountinfo_path = tmp_path / "mountinfo"
    mountinfo_path.write_text(mountinfo)

    config = AgentDaemonConfig(
        api_url=None,
        cluster_name="cluster-a",
        node_name="c1-worker",
        node_uid=None,
        worker_role="RM",
        storages=[
            {
                "storage_name": "testbed-cephfs",
                "storage_class_name": "testbed-cephfs",
                "csi_driver": CEPHFS_DRIVER,
                "mount_paths": [str(mount_path)],
            }
        ],
        tool_names=["definitely-missing-dms-tool"],
        mountinfo_path=str(mountinfo_path),
    )

    report = build_agent_report(
        config,
        kubernetes_client=FakeKubernetesClient(),
        now=datetime(2026, 5, 29, 0, 0, tzinfo=UTC),
    )

    assert report["schema_version"] == "phase9.v1"
    assert "os_metrics" in report  # cpu/mem/load from the agent's own procfs
    assert report["node_uid"] == "uid-c1-worker"
    assert report["worker_role"] == "RM"
    assert report["mounts"][0]["status"] == "Ready"
    assert report["mounts"][0]["filesystem_type"] == "ceph"
    assert report["csi"][0]["status"] == "Ready"
    assert report["csi"][0]["storage_classes"] == ["testbed-cephfs"]
    assert report["tools"][0]["status"] == "Missing"
    assert report["identity_evidence"]["source"] == "agent-prober"


def test_mountinfo_parser_decodes_mount_paths():
    [mount] = parse_mountinfo(
        "36 25 0:42 / /mnt/dms/testbed\\040cephfs rw,relatime - ceph "
        "rook\\040ceph rw\n"
    )

    assert mount["mount_point"] == "/mnt/dms/testbed cephfs"
    assert mount["source"] == "rook ceph"
    assert mount["filesystem_type"] == "ceph"


def test_non_ready_evidence_is_not_used_as_readiness_candidate(harness):
    client: TestClient = harness["client"]
    base_time = datetime.now(UTC)
    missing = phase8_report(
        reported_at=base_time.isoformat(),
        csi_status="Missing",
        mount_status="Missing",
    )
    response = client.post(
        "/api/v1/agent/reports",
        json=missing,
        headers={"x-dms-actor": "node:cluster-b:c2-worker"},
    )
    assert response.status_code == 200

    mapping = upsert_mapping(client)
    sanity = mapping["mapping"]["sanity_result"]
    assert sanity["readiness"]["resource_management"] == "Missing"
    assert sanity["agent_observed"]["rm_candidates"] == []

    ready = phase8_report(
        reported_at=(base_time + timedelta(seconds=1)).isoformat(),
        csi_status="Ready",
        mount_status="Missing",
    )
    response = client.post(
        "/api/v1/agent/reports",
        json=ready,
        headers={"x-dms-actor": "node:cluster-b:c2-worker"},
    )
    assert response.status_code == 200

    mapping = upsert_mapping(client)
    sanity = mapping["mapping"]["sanity_result"]
    assert sanity["readiness"]["resource_management"] == "Ready"
    assert sanity["readiness"]["data_management"] == "Missing"
    assert sanity["agent_observed"]["rm_candidates"][0]["status"] == "Ready"

    inventory = client.get("/api/v1/operations/inventory", headers=API_HEADERS).json()
    role_nodes = inventory["worker_roles"]["RM"]["cluster-b"]["nodes"]
    assert role_nodes == [
        {
            "node_name": "c2-worker",
            "node_uid": "uid-c2-worker",
            "report_id": role_nodes[0]["report_id"],
        }
    ]


def phase8_report(*, reported_at: str, csi_status: str, mount_status: str) -> dict:
    return {
        "schema_version": "phase8.v1",
        "reported_at": reported_at,
        "cluster_name": "cluster-b",
        "node_name": "c2-worker",
        "node_uid": "uid-c2-worker",
        "worker_role": "RM",
        "mounts": [
            {
                "storage_name": "phase8-longhorn",
                "path": "/mnt/dms/phase8-longhorn",
                "mount_path": "/mnt/dms/phase8-longhorn",
                "status": mount_status,
                "source": "agent-prober",
            }
        ],
        "csi": [
            {
                "driver": LONGHORN_DRIVER,
                "storage_classes": ["testbed-longhorn"],
                "status": csi_status,
                "source": "kubernetes-api",
            }
        ],
        "tools": [{"name": "dscan", "status": "Missing"}],
        "credentials": [],
        "networks": [],
        "identity_evidence": {"source": "agent-prober"},
    }


def upsert_mapping(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/resource-management/storage-mappings",
        json={
            "storage_name": "phase8-longhorn",
            "backend_template": {"backend_type": "longhorn"},
            "cluster_name": "cluster-b",
            "storage_class_name": "testbed-longhorn",
        },
        headers=API_HEADERS,
    )
    assert response.status_code == 200
    return response.json()


def static_inventory() -> dict:
    return {
        "clusters": {
            "cluster-b": {
                "nodes": [{"name": "c2-worker", "uid": "uid-c2-worker"}],
                "storage_classes": [
                    {
                        "name": "testbed-longhorn",
                        "provisioner": LONGHORN_DRIVER,
                        "parameters": {},
                    }
                ],
                "csi_drivers": [{"name": LONGHORN_DRIVER}],
            }
        }
    }


@pytest.fixture()
def harness(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        agent_report_stale_seconds=300,
        control_cluster_name="cluster-a",
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(
        settings,
        repository,
        observability,
        kubernetes_inventory=StaticKubernetesReadOnlyInventoryAdapter(static_inventory()),
    )
    return {"client": TestClient(app), "repository": repository}
