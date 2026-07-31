"""A CSI/agentless storage mapping must not be reported Ready on zero evidence.

A CSI mapping runs no node agent, so every agent-evidence signal is deliberately
skipped for it: the `agent_inventory_missing` / `missing_dm_readiness` warnings are
both behind `if not is_csi`. Its ONLY real checks are `storage_class_exists` and
`csi_driver_matches`, and both are conditional on `cluster_name` / `storage_class_name`
being set — which `StorageMappingInput` leaves optional.

So a CSI mapping that declares neither would pass with one trivial
`backend_type_present` check, zero errors, zero warnings → `Ready`, and the planner's
admission gate (`unsafe_sanity = {"Failed", "Unknown"}`) would let data jobs through
against a storage nothing has ever verified.

Before the resource-management removal this could not happen: CSI mappings also carried
a `kubernetes_mutation` axis from the ResourceQuota mutation-transport probe, which
errored when the cluster was unreachable. Removing that probe took the last gate with
it, so the mapping must now fail closed explicitly.
"""

from __future__ import annotations

import pytest

from dms.config import Settings
from dms.db import Database
from dms.domain import StorageMappingInput
from dms.inventory import EffectiveInventoryService, StorageMappingSanityService
from dms.migrations import migrate_all
from dms.repositories import DmsRepository

_DRIVER = "cephfs.csi.ceph.com"


class _Inventory:
    def __init__(self, clusters: dict) -> None:
        self._clusters = clusters

    def read_inventory(self) -> dict:
        return {"clusters": self._clusters}


@pytest.fixture()
def sanity(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'op.db'}")
    observability = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(operational, observability)
    repository = DmsRepository(operational)
    settings = Settings(
        database_url="sqlite://", observability_database_url="sqlite://"
    )

    def build(clusters: dict) -> StorageMappingSanityService:
        return StorageMappingSanityService(
            repository=repository,
            inventory_service=EffectiveInventoryService(
                repository=repository,
                kubernetes_inventory=_Inventory(clusters),
                settings=settings,
            ),
            settings=settings,
        )

    return build


def _codes(result: dict, key: str) -> list[str]:
    return [item["code"] for item in result[key]]


def test_csi_mapping_without_cluster_or_storage_class_fails_closed(sanity):
    result = sanity({}).check_input(
        StorageMappingInput(
            storage_name="rogue", backend_template={"backend_type": "ceph-csi"}
        )
    )

    assert result["status"] == "Failed"
    assert "csi_mapping_unpinned" in _codes(result, "errors")


def test_csi_mapping_with_cluster_but_no_storage_class_fails_closed(sanity):
    result = sanity({"c1": {"storage_classes": [], "csi_drivers": []}}).check_input(
        StorageMappingInput(
            storage_name="half",
            backend_template={"backend_type": "ceph-csi"},
            cluster_name="c1",
        )
    )

    assert result["status"] == "Failed"
    assert "csi_mapping_unpinned" in _codes(result, "errors")


def test_fully_pinned_csi_mapping_is_ready_and_actually_checked(sanity):
    clusters = {
        "c1": {
            "storage_classes": [{"name": "sc1", "provisioner": _DRIVER}],
            "csi_drivers": [],
        }
    }
    result = sanity(clusters).check_input(
        StorageMappingInput(
            storage_name="good",
            backend_template={"backend_type": "ceph-csi", "csi_driver": _DRIVER},
            cluster_name="c1",
            storage_class_name="sc1",
        )
    )

    assert result["status"] == "Ready"
    assert result["errors"] == []
    # Ready must rest on real evidence, not on the absence of checks.
    assert {"storage_class_exists", "csi_driver_matches"} <= {
        c["name"] for c in result["checks"]
    }


def test_filesystem_mapping_is_unaffected_by_the_csi_pin_requirement(sanity):
    """Filesystem mappings are validated by agent evidence, not by a StorageClass, so
    they legitimately carry neither cluster_name nor storage_class_name."""
    result = sanity({}).check_input(
        StorageMappingInput(
            storage_name="fs",
            backend_template={
                "backend_type": "cephfs",
                "mount_path": "/cephfs",
                "managed_root": "/cephfs/dms",
            },
        )
    )

    assert "csi_mapping_unpinned" not in _codes(result, "errors")
    assert result["status"] != "Failed"


def test_unpinned_csi_mapping_is_rejected_by_the_planner_admission_gate(sanity, tmp_path):
    """The whole point of failing closed: `Failed` is in the planner's unsafe set, so a
    data job cannot be planned against an unverified mapping."""
    from dms.domain import OperationKind, ResourceKind
    from dms.planner import Planner

    operational = Database(f"sqlite:///{tmp_path / 'gate-op.db'}")
    observability = Database(f"sqlite:///{tmp_path / 'gate-obs.db'}")
    migrate_all(operational, observability)
    repository = DmsRepository(operational)

    result = sanity({}).check_input(
        StorageMappingInput(
            storage_name="rogue", backend_template={"backend_type": "ceph-csi"}
        )
    )
    data = StorageMappingInput(
        storage_name="rogue",
        backend_template={"backend_type": "ceph-csi"},
        sanity_status=result["status"],
    )
    repository.upsert_storage_mapping(
        data, actor="admin", sanity_result=result, readiness=result["readiness"]
    )

    request_id = repository.create_request(
        requester_id="alice",
        actor="api",
        operation=OperationKind.DATA_SCAN.value,
        resource_kind=ResourceKind.DATA_JOB.value,
        resource_key="rogue:data.scan:p",
        payload={
            "storage_name": "rogue",
            "target": {"storage_name": "rogue", "path": "p"},
            "target_path": "p",
        },
    )
    Planner(repository).run_once()

    assert repository.get_request(request_id)["status"] == "Rejected"
    [row] = repository.get_results(request_id)
    issues = row["verification_summary"]["issues"]
    assert [i["reason"] for i in issues] == ["storage_mapping_sanity"]
