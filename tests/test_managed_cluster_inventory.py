"""Agentless managed-cluster inventory: per-cluster fail isolation + reconciler wiring.

A k8s/CSI storage mapping may target a MANAGED cluster that runs no DMS node agent and is
reached only via kubectl / ssh-kubectl (see the per-mapping ``mutation_mode`` /
``control_host`` transport). Two correctness properties keep such mappings usable:

  1. ``KubectlReadOnlyInventoryAdapter.read_inventory()`` is per-cluster fail-isolated — a
     single unreachable cluster is omitted, never blanking the whole inventory (which would
     flip EVERY storage mapping, filesystem ones included, to a spurious ``Failed``).
  2. The sanity reconciler is wired with the SAME kubectl inventory adapter as the API, so a
     periodic sweep does not flip agentless managed-cluster mappings back to ``Failed`` (the
     reconciler used to attach no k8s inventory and saw only agent reports).
"""

from __future__ import annotations

import dms.adapters.inventory as inventory_mod
from dms.adapters import KubectlReadOnlyInventoryAdapter
from dms.adapters.base import KubernetesInventoryReadError
from dms.config import Settings
from dms.sanity_reconciler import build_sanity_service


def _healthy_cluster(_name: str) -> dict:
    return {"nodes": [], "storage_classes": [], "csi_drivers": []}


def test_read_inventory_isolates_per_cluster_failure(monkeypatch):
    adapter = KubectlReadOnlyInventoryAdapter(
        cluster_kubeconfigs={"dms": "/kc/dms", "ddz26": "/kc/ddz26"},
        mode="kubectl",
    )

    def fake_read_cluster(self, cluster_name):
        if cluster_name == "ddz26":
            raise KubernetesInventoryReadError("ddz26 unreachable")
        return {"nodes": [{"name": "dms-w1"}], "storage_classes": [], "csi_drivers": []}

    monkeypatch.setattr(
        KubectlReadOnlyInventoryAdapter, "_read_cluster", fake_read_cluster
    )
    inv = adapter.read_inventory()
    # Healthy cluster present; unreachable cluster omitted (NOT a fatal blank inventory).
    assert "dms" in inv["clusters"]
    assert "ddz26" not in inv["clusters"]
    assert inv["clusters"]["dms"]["nodes"] == [{"name": "dms-w1"}]


def test_read_inventory_all_healthy(monkeypatch):
    adapter = KubectlReadOnlyInventoryAdapter(
        cluster_kubeconfigs={"dms": "/kc/dms", "ddz26": "/kc/ddz26"},
        mode="kubectl",
    )
    monkeypatch.setattr(
        KubectlReadOnlyInventoryAdapter,
        "_read_cluster",
        lambda self, c: _healthy_cluster(c),
    )
    inv = adapter.read_inventory()
    assert set(inv["clusters"]) == {"dms", "ddz26"}


def test_read_inventory_all_unreachable_is_empty_not_raising(monkeypatch):
    adapter = KubectlReadOnlyInventoryAdapter(
        cluster_kubeconfigs={"a": "/kc/a", "b": "/kc/b"},
        mode="kubectl",
    )

    def boom(self, cluster_name):
        raise KubernetesInventoryReadError(f"{cluster_name} down")

    monkeypatch.setattr(KubectlReadOnlyInventoryAdapter, "_read_cluster", boom)
    # Must degrade to an empty inventory, not propagate (other sanity checks keep working).
    assert adapter.read_inventory() == {"clusters": {}}


def test_missing_kubectl_binary_is_isolated_not_fatal(monkeypatch):
    # A missing kubectl/ssh binary makes subprocess.run raise FileNotFoundError (an OSError).
    # It must be normalized to KubernetesInventoryReadError and isolated per-cluster, NOT
    # propagate and crash the whole sweep (which would blank the inventory for ALL mappings).
    adapter = KubectlReadOnlyInventoryAdapter(
        cluster_kubeconfigs={"ddz26": "/kc/ddz26"},
        mode="kubectl",
    )

    def raise_missing_binary(*_a, **_k):
        raise FileNotFoundError(2, "No such file or directory", "kubectl")

    monkeypatch.setattr(inventory_mod.subprocess, "run", raise_missing_binary)
    # Per-cluster read raises KubernetesInventoryReadError -> omitted; whole read is empty.
    assert adapter.read_inventory() == {"clusters": {}}


def test_reconciler_wires_kubectl_inventory_like_the_api():
    settings = Settings(
        database_url="sqlite://",
        observability_database_url="sqlite://",
        cluster_kubeconfigs={"dms": "/kc/dms", "ddz26": "/kc/ddz26"},
        kubernetes_inventory_mode="kubectl",
    )
    service = build_sanity_service(repository=None, settings=settings)
    adapter = service.inventory_service.kubernetes_inventory
    # Regression: the reconciler used to attach kubernetes_inventory=None (agent-only),
    # which flipped agentless managed-cluster mappings to a spurious Failed every sweep.
    assert isinstance(adapter, KubectlReadOnlyInventoryAdapter)
    assert adapter.cluster_kubeconfigs == {"dms": "/kc/dms", "ddz26": "/kc/ddz26"}
    assert adapter.mode == "kubectl"
