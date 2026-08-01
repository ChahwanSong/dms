from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from fastapi.testclient import TestClient

from dms.adapters import (
    IdentityLookupResult,
    StubIdentityLookupAdapter,
    StubVolcanoAdapter,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import DataJobState, LifecycleState, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.query import OperationalQueryService
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import DMWorkerRuntime


class FailingObservabilityRepository(ObservabilityRepository):
    def record_event(self, **_: Any) -> str:
        raise RuntimeError("observability database unavailable")


def test_auth_rejection_survives_observability_write_failure(tmp_path, caplog):
    settings, repository, observability = _repositories(tmp_path)
    client = TestClient(create_app(settings, repository, observability))

    with caplog.at_level(logging.WARNING, logger="dms.repositories"):
        response = client.post(
            "/api/v1/data-management/scan",
            json=_scan_body("auth-failure"),
        )

    assert response.status_code == 401
    assert repository.list_requests(requester_id="user-1") == []
    assert "observability event write failed" in caplog.text


def test_worker_success_survives_observability_write_failure(tmp_path):
    settings, repository, observability = _repositories(tmp_path)
    _register_mapping(repository, storage_name="cephfs-a", backend_type="cephfs")
    _ingest_ready_dm_report(repository, storage_name="cephfs-a")
    client = TestClient(create_app(settings, repository, observability))
    request_id = client.post(
        "/api/v1/data-management/scan",
        json=_scan_body("phase14-success"),
        headers={"x-dms-actor": "api-client"},
    ).json()["request_id"]
    Planner(repository, settings=settings).run_once()
    worker = DMWorkerRuntime(
        repository=repository,
        observability=observability,
        identity_lookup=_identity_lookup(),
        volcano_adapter=StubVolcanoAdapter(),
        worker_id="dm-phase14",
    )

    assert worker.run_once() == 1
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert (
        repository.get_data_job_by_request(request_id)["state"]
        == DataJobState.SUCCEEDED.value
    )
    results = repository.get_results(request_id)
    assert [result["terminal_status"] for result in results] == [
        LifecycleState.SUCCEEDED.value
    ]


def test_action_required_readiness_only_for_agent_backed_mappings(tmp_path):
    """Agentless CSI mappings legitimately have Missing DM readiness (they run
    no node agent); only agent-backed (filesystem) mappings should surface
    missing_dm_readiness as an action item."""
    _, repository, observability = _repositories(tmp_path)
    missing = {
        "data_management": "Missing",
        "inventory": "Ready",
    }
    _register_mapping(
        repository, storage_name="cephfs-fs", backend_type="cephfs",
        storage_class_name="sc-fs",
    )
    _register_mapping(
        repository, storage_name="ceph-csi-x", backend_type="ceph-csi",
        storage_class_name="sc-csi",
    )
    for name in ("cephfs-fs", "ceph-csi-x"):
        repository.update_storage_mapping_sanity(
            name,
            sanity_result={"status": "Ready"},
            readiness=missing,
            actor="test",
        )
    issues = OperationalQueryService(repository, observability).action_required()
    codes = {(i["issue_type"], i.get("storage_name")) for i in issues}
    # cephfs (agent-backed) mapping surfaces the readiness warning
    assert ("missing_dm_readiness", "cephfs-fs") in codes
    # agentless CSI mapping does NOT (its Missing readiness is expected)
    assert ("missing_dm_readiness", "ceph-csi-x") not in codes


def _repositories(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        worker_lease_seconds=300,
        dm_kubernetes_mode="stub",
        dm_policy_default_worker_nodes=1,
        dm_policy_default_processes_per_node=1,
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    return (
        settings,
        DmsRepository(operational),
        FailingObservabilityRepository(observability_db),
    )


def _scan_body(target_name: str) -> dict[str, Any]:
    return {
        "requester_id": "user-1",
        "storage_name": "cephfs-a",
        "target_path": f"project/{target_name}",
    }


def _identity_lookup() -> StubIdentityLookupAdapter:
    return StubIdentityLookupAdapter(
        mappings={
            ("ldap", "user-1"): IdentityLookupResult(
                provider="ldap",
                posix_username="user-1",
                uid=10000,
                primary_gid=10000,
                groups=["dms-users"],
                user_dn="uid=user-1,ou=people,dc=example,dc=internal",
                source_metadata={"adapter": "phase14-test"},
            )
        }
    )


def _register_mapping(
    repository: DmsRepository,
    *,
    storage_name: str,
    backend_type: str,
    cluster_name: str = "cluster-a",
    storage_class_name: str = "testbed-cephfs",
) -> None:
    sanity = {
        "storage_name": storage_name,
        "status": "Ready",
        "checked_at": "2026-05-31T00:00:00+00:00",
        "kubernetes_observed": {
            "cluster_name": cluster_name,
            "storage_class_name": storage_class_name,
            "storage_class_exists": True,
            "provisioner": f"{backend_type}.csi.dms.test",
        },
        "agent_observed": {
            "fresh_reports": 1,
            "stale_reports": 0,
            "dm_readiness": "Ready",
            "dm_candidates": [{"cluster_name": cluster_name, "node_name": "dm-1"}],
        },
        "readiness": {
            "data_management": "Ready",
            "inventory": "Ready",
        },
        "checks": [],
        "warnings": [],
        "errors": [],
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template={
                "backend_type": backend_type,
                "mount_path": "/mnt/testbed-cephfs",
                "managed_root": "/mnt/testbed-cephfs/dms",
            },
            cluster_name=cluster_name,
            storage_class_name=storage_class_name,
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def _ingest_ready_dm_report(
    repository: DmsRepository,
    *,
    storage_name: str,
    node_name: str = "dm-1",
) -> None:
    repository.ingest_agent_report(
        {
            "schema_version": "phase19.v1",
            # Freshness is computed ON READ against the staleness window, so a
            # "ready" node must have reported recently — use now, not a fixed past.
            "reported_at": datetime.now(timezone.utc).isoformat(),
            "cluster_name": "cluster-a",
            "node_name": node_name,
            "node_uid": f"uid-{node_name}",
            "worker_role": "DM",
            "mounts": [
                {
                    "storage_name": storage_name,
                    "mount_path": "/mnt/testbed-cephfs",
                    "status": "Ready",
                    "readable": True,
                }
            ],
            "tools": [{"name": "dscan", "status": "Ready"}],
            "credentials": [{"name": "kubernetes-service-account", "status": "Ready"}],
            "networks": [{"name": "storage-net", "status": "Ready"}],
            "identity_evidence": {
                "source": "phase14-test",
                "users": [
                    {
                        "username": "user-1",
                        "status": "Ready",
                        "uid": 10000,
                        "gid": 10000,
                        "groups": ["dms-users"],
                    }
                ],
            },
            "csi": [],
        }
    )
