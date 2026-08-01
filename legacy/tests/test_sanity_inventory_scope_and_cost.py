"""Storage-mapping sanity: per-mapping evidence, read once per sweep.

Three defects this pins down:

1. **`readiness["inventory"]` was fleet-global.** It was `"Ready" if fresh_reports`,
   where `fresh_reports` summed the fresh agent reports of EVERY cluster. Every mapping
   in one sweep therefore got the same value, one fresh report from one unrelated node
   flipped it Ready for all of them, and it could never read anything but Ready/Unknown.
   It is now scoped to the mapping.

2. **Sanity read the `agent_reports` HISTORY table** (`limit=1000`, plus a whole-table
   Fresh→Stale UPDATE sweep) while every other consumer had moved to
   `agent_node_current`. On a fleet reporting every minute, the newest 1000 rows can all
   belong to a handful of chatty nodes, hiding the rest.

3. **The reconciler re-read the whole fleet once per mapping.** N mappings meant N full
   Kubernetes inventory reads (3 kubectl/ssh calls per configured cluster each). Nothing
   in one sweep can change that data.
"""

from __future__ import annotations

import pytest

from dms.config import Settings
from dms.db import Database
from dms.domain import StorageMappingInput
from dms.inventory import EffectiveInventoryService, StorageMappingSanityService
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.sanity_reconciler import reconcile_once

_DRIVER = "cephfs.csi.ceph.com"


class _CountingInventory:
    """Counts how often the Kubernetes side is actually read."""

    def __init__(self, clusters: dict) -> None:
        self._clusters = clusters
        self.reads = 0

    def read_inventory(self) -> dict:
        self.reads += 1
        return {"clusters": self._clusters}


@pytest.fixture()
def env(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'op.db'}")
    observability = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(operational, observability)
    settings = Settings(
        database_url="sqlite://",
        observability_database_url="sqlite://",
        control_cluster_name="ctrl",
    )
    return DmsRepository(operational), ObservabilityRepository(observability), settings


def _services(repo, settings, clusters):
    k8s = _CountingInventory(clusters)
    inv = EffectiveInventoryService(
        repository=repo, kubernetes_inventory=k8s, settings=settings
    )
    return k8s, inv, StorageMappingSanityService(
        repository=repo, inventory_service=inv, settings=settings
    )


def _report(repo, cluster: str, node: str) -> None:
    repo.ingest_agent_report(
        {
            "cluster_name": cluster,
            "node_name": node,
            "node_uid": f"uid-{cluster}-{node}",
            "worker_role": "DM",
            "reported_at": "2999-01-01T00:00:00+00:00",
            "mounts": [],
            "tools": [],
            "credentials": [],
            "networks": [],
        }
    )


def _fs(name: str, cluster: str | None) -> StorageMappingInput:
    return StorageMappingInput(
        storage_name=name,
        backend_template={
            "backend_type": "cephfs",
            "mount_path": "/cephfs",
            "managed_root": "/cephfs/dms",
        },
        cluster_name=cluster,
    )


# --- (1) inventory axis is per mapping ---------------------------------------
def test_inventory_axis_is_scoped_to_the_mapping_cluster(env):
    repo, _obs, settings = env
    _report(repo, "ctrl", "n1")  # fresh evidence ONLY in the control cluster
    _, _, sanity = _services(repo, settings, {})

    here = sanity.check_input(_fs("here", cluster="ctrl"))
    elsewhere = sanity.check_input(_fs("elsewhere", cluster="other-cluster"))

    assert here["readiness"]["inventory"] == "Ready"
    # the old fleet-wide count made this Ready too, on evidence from another cluster
    assert elsewhere["readiness"]["inventory"] == "Unknown"


def test_csi_inventory_axis_reflects_the_storage_class_not_agent_reports(env):
    repo, _obs, settings = env
    _report(repo, "ctrl", "n1")  # agent evidence must be irrelevant to a CSI mapping
    clusters = {
        "c1": {"storage_classes": [{"name": "sc1", "provisioner": _DRIVER}], "csi_drivers": []},
        "c2": {"storage_classes": [], "csi_drivers": []},
    }
    _, _, sanity = _services(repo, settings, clusters)

    found = sanity.check_input(
        StorageMappingInput(
            storage_name="csi-ok",
            backend_template={"backend_type": "ceph-csi", "csi_driver": _DRIVER},
            cluster_name="c1",
            storage_class_name="sc1",
        )
    )
    missing = sanity.check_input(
        StorageMappingInput(
            storage_name="csi-bad",
            backend_template={"backend_type": "ceph-csi", "csi_driver": _DRIVER},
            cluster_name="c2",
            storage_class_name="sc-nope",
        )
    )

    assert found["readiness"]["inventory"] == "Ready"
    assert missing["readiness"]["inventory"] == "Missing"


# --- (2) reads agent_node_current, not the capped history ---------------------
def test_sanity_sees_every_node_not_just_the_newest_history_rows(env):
    """Latest-per-node means a quiet node is still visible however chatty its peers."""
    repo, _obs, settings = env
    for i in range(30):
        _report(repo, "ctrl", "chatty")  # 30 history rows for ONE node
    _report(repo, "ctrl", "quiet")
    _, inv, _ = _services(repo, settings, {})

    fresh = inv.effective_inventory()["clusters"]["ctrl"]["agent_reports"]["fresh"]

    # one row per node, not 31 rows dominated by the chatty one
    assert sorted(r["node_name"] for r in fresh) == ["chatty", "quiet"]


# --- (3) one fleet read per sweep --------------------------------------------
def test_reconcile_reads_the_kubernetes_inventory_once_per_sweep(env):
    repo, obs, settings = env
    _report(repo, "ctrl", "n1")
    k8s, _inv, sanity = _services(repo, settings, {})
    for name in ("s1", "s2", "s3", "s4", "s5"):
        data = _fs(name, cluster="ctrl")
        result = sanity.check_input(data)
        repo.upsert_storage_mapping(
            data, actor="admin", sanity_result=result, readiness=result["readiness"]
        )
    k8s.reads = 0

    reconcile_once(repo, sanity, observability=obs)

    assert k8s.reads == 1, f"5 mappings caused {k8s.reads} full inventory reads"


def test_the_api_path_still_sees_a_live_cluster(env):
    """The snapshot is sweep-scoped; a registration check must NOT reuse it."""
    repo, _obs, settings = env
    k8s, _inv, sanity = _services(repo, settings, {})

    sanity.check_input(_fs("a", cluster="ctrl"))
    sanity.check_input(_fs("b", cluster="ctrl"))

    assert k8s.reads == 2
