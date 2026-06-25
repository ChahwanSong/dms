"""Per-storage-mapping Kubernetes ResourceQuota mutation transport.

A k8s/CSI storage mapping may pin, in its backend_template, how DMS applies quota
mutations to its cluster (``mutation_mode`` kubectl|ssh-kubectl + ``control_host``),
overriding the GLOBAL ``DMS_KUBERNETES_MUTATION_MODE``. The planner resolves the pin
from the target cluster's mapping into the plan's desired_state; the adapter rebinds to
it per operation, falling back to the global settings when nothing is pinned.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from dms.adapters.kubernetes_quota import (
    KubernetesMutationError,
    KubernetesNamespaceQuotaLiveAdapter,
)
from dms.domain import validate_kubernetes_mutation_template
from dms.planner._kubernetes import KubernetesPlannerMixin


# --------------------------------------------------------------------------- #
# domain validation (registration-time, fail-closed -> 422)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "tmpl",
    [
        {},
        {"backend_type": "ceph-csi", "csi_driver": "cephfs.csi.ceph.com"},
        {"mutation_mode": "kubectl"},
        {"mutation_mode": "ssh-kubectl", "control_host": "10.10.10.20"},
        {"mutation_mode": "kubectl", "control_host": "10.10.10.20"},  # explicit kubectl, host ignored
    ],
)
def test_validate_accepts(tmpl):
    validate_kubernetes_mutation_template(tmpl)  # must not raise


@pytest.mark.parametrize(
    "tmpl",
    [
        {"mutation_mode": "nope"},
        {"mutation_mode": "ssh"},
        {"mutation_mode": 123},
        {"mutation_mode": "ssh-kubectl"},          # missing control_host
        {"mutation_mode": "ssh-kubectl", "control_host": ""},
        {"mutation_mode": "ssh-kubectl", "control_host": "has space"},
        {"control_host": "10.10.10.20"},  # valid host but no mutation_mode (ignored under kubectl default)
        {"control_host": "dms-w1"},       # ditto -- control_host requires explicit mutation_mode
        {"control_host": "has space"},
        {"control_host": ""},
        {"mutation_mode": "ssh-kubectl", "control_host": "-oProxyCommand=evil"},  # ssh opt injection
        {"control_host": "-oProxyCommand=x"},
        {"control_host": "root@host"},   # user@ redirect
        {"control_host": "a;b"},         # shell metachar
        {"control_host": "a|b"},
        {"control_host": ".leading-dot-ok-but"},  # ok start? '.' allowed as first? -> no, must be alnum
    ],
)
def test_validate_rejects(tmpl):
    with pytest.raises(ValueError):
        validate_kubernetes_mutation_template(tmpl)


# --------------------------------------------------------------------------- #
# adapter _bind + _command: per-cluster transport, global fallback
# --------------------------------------------------------------------------- #

def _global_kubectl():
    return KubernetesNamespaceQuotaLiveAdapter(
        cluster_kubeconfigs={"ddz26": "/kc/ddz26", "dms": "/kc/dms"},
        cluster_control_hosts={},
        mode="kubectl",
    )


def test_no_override_returns_same_adapter_and_uses_global_kubectl():
    g = _global_kubectl()
    a = g._bind({"cluster_name": "ddz26"})
    assert a is g  # nothing pinned -> unchanged
    assert a._command("ddz26", ["get", "ns"]) == [
        "kubectl", "--kubeconfig", "/kc/ddz26", "get", "ns",
    ]


def test_per_mapping_ssh_override_when_global_is_kubectl():
    g = _global_kubectl()
    desired = {
        "cluster_name": "ddz26",
        "mutation_mode": "ssh-kubectl",
        "control_host": "10.10.10.20",
    }
    cmd = g._bind(desired)._command("ddz26", ["get", "ns"])
    assert cmd[0] == "ssh" and cmd[1] == "10.10.10.20"
    assert cmd[2] == "kubectl get ns"


def test_per_mapping_kubectl_override_when_global_is_ssh():
    g = replace(_global_kubectl(), mode="ssh-kubectl",
                cluster_control_hosts={"dms": "ctrl"})
    cmd = g._bind({"cluster_name": "dms", "mutation_mode": "kubectl"})._command(
        "dms", ["get", "ns"]
    )
    assert cmd[0] == "kubectl"
    assert "--kubeconfig" in cmd and "/kc/dms" in cmd


def test_control_host_only_override_replaces_global_host():
    g = replace(_global_kubectl(), mode="ssh-kubectl",
                cluster_control_hosts={"ddz26": "old-host"})
    cmd = g._bind({"cluster_name": "ddz26", "control_host": "new-host"})._command(
        "ddz26", ["x"]
    )
    assert cmd[:2] == ["ssh", "new-host"]


def test_ssh_mode_without_control_host_raises():
    g = replace(_global_kubectl(), mode="ssh-kubectl", cluster_control_hosts={})
    with pytest.raises(KubernetesMutationError):
        g._bind({"cluster_name": "ddz26", "mutation_mode": "ssh-kubectl",
                 "control_host": None})._command("ddz26", ["x"])


def test_bind_does_not_mutate_global_adapter():
    g = _global_kubectl()
    g._bind({"cluster_name": "ddz26", "mutation_mode": "ssh-kubectl",
             "control_host": "h"})
    assert g.mode == "kubectl" and g.cluster_control_hosts == {}


# --------------------------------------------------------------------------- #
# planner resolution: backend_template -> desired_state
# --------------------------------------------------------------------------- #

class _FakeRepo:
    def __init__(self, mappings):
        self._mappings = mappings

    def list_storage_mappings(self, cluster_name=None):
        return [m for m in self._mappings if m.get("cluster_name") == cluster_name]


class _Resolver(KubernetesPlannerMixin):
    def __init__(self, mappings):
        self.repository = _FakeRepo(mappings)


def _mapping(cluster, **bt):
    return {"cluster_name": cluster, "backend_template": {"backend_type": "ceph-csi", **bt}}


def test_planner_embeds_ssh_kubectl_from_mapping():
    r = _Resolver([_mapping("ddz26", mutation_mode="ssh-kubectl", control_host="10.10.10.20")])
    desired = {"cluster_name": "ddz26"}
    r._apply_kubernetes_mutation_config(desired)
    assert desired["mutation_mode"] == "ssh-kubectl"
    assert desired["control_host"] == "10.10.10.20"


def test_planner_embeds_kubectl_without_control_host():
    r = _Resolver([_mapping("dms", mutation_mode="kubectl")])
    desired = {"cluster_name": "dms"}
    r._apply_kubernetes_mutation_config(desired)
    assert desired["mutation_mode"] == "kubectl"
    assert "control_host" not in desired


def test_planner_no_pin_leaves_fields_absent_and_clears_stale():
    r = _Resolver([_mapping("ddz26")])  # mapping without mutation_mode
    desired = {"cluster_name": "ddz26", "mutation_mode": "stale", "control_host": "stale"}
    r._apply_kubernetes_mutation_config(desired)
    assert "mutation_mode" not in desired and "control_host" not in desired


def test_planner_unknown_cluster_no_pin():
    r = _Resolver([_mapping("ddz26", mutation_mode="ssh-kubectl", control_host="h")])
    desired = {"cluster_name": "other"}
    r._apply_kubernetes_mutation_config(desired)
    assert "mutation_mode" not in desired


def test_planner_control_host_only_is_embedded():
    # control_host without mutation_mode -> embedded (overrides global control host,
    # keeps global mode). Regression: planner used to drop control_host-only overrides.
    r = _Resolver([_mapping("ddz26", control_host="bastion-1")])
    desired = {"cluster_name": "ddz26"}
    r._apply_kubernetes_mutation_config(desired)
    assert "mutation_mode" not in desired
    assert desired["control_host"] == "bastion-1"


def test_planner_multi_mapping_deterministic_by_storage_name():
    # Two mappings for one cluster with different modes -> lowest storage_name wins
    # deterministically (not dict/insertion order).
    m_b = {"storage_name": "b-map", "cluster_name": "ddz26",
           "backend_template": {"backend_type": "ceph-csi", "mutation_mode": "kubectl"}}
    m_a = {"storage_name": "a-map", "cluster_name": "ddz26",
           "backend_template": {"backend_type": "ceph-csi", "mutation_mode": "ssh-kubectl",
                                 "control_host": "h"}}
    r = _Resolver([m_b, m_a])  # input order b,a -> sorted a wins
    desired = {"cluster_name": "ddz26"}
    r._apply_kubernetes_mutation_config(desired)
    assert desired["mutation_mode"] == "ssh-kubectl"
    assert desired["control_host"] == "h"


def test_planner_targets_enriches_target_and_desired_state():
    # AUDIT/SWEEP multi-cluster: each target (and its nested desired_state) gets its
    # cluster's transport. ddz26 -> ssh-kubectl, dms -> kubectl.
    r = _Resolver([
        _mapping("ddz26", mutation_mode="ssh-kubectl", control_host="bastion-1"),
        _mapping("dms", mutation_mode="kubectl"),
    ])
    targets = [
        {"cluster_name": "ddz26", "desired_state": {"cluster_name": "ddz26"}},
        {"cluster_name": "dms", "desired_state": {"cluster_name": "dms"}},
        {"desired_state": {"cluster_name": "ddz26"}},  # cluster only nested
    ]
    r._apply_kubernetes_mutation_config_to_targets(targets)
    assert targets[0]["mutation_mode"] == "ssh-kubectl"
    assert targets[0]["control_host"] == "bastion-1"
    assert targets[0]["desired_state"]["mutation_mode"] == "ssh-kubectl"  # sweep block path
    assert targets[1]["mutation_mode"] == "kubectl" and "control_host" not in targets[1]
    assert targets[2]["desired_state"]["mutation_mode"] == "ssh-kubectl"


def test_planner_targets_clears_stale():
    r = _Resolver([_mapping("dms", mutation_mode="kubectl")])
    targets = [{"cluster_name": "unknown", "mutation_mode": "stale",
                "desired_state": {"cluster_name": "unknown", "control_host": "stale"}}]
    r._apply_kubernetes_mutation_config_to_targets(targets)
    assert "mutation_mode" not in targets[0]
    assert "control_host" not in targets[0]["desired_state"]
