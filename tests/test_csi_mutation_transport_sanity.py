"""CSI/k8s storage-mapping sanity is gated by the ResourceQuota MUTATION TRANSPORT.

A k8s/CSI mapping may target an agentless managed cluster (no DMS node agent, reached
only via kubectl / ssh-kubectl). For such mappings the readiness axis that matters is
NOT RM/DM agent evidence but whether the per-mapping quota mutation transport actually
reaches the cluster AND is allowed to apply ResourceQuotas there
(``kubectl auth can-i create|patch|delete resourcequota``).

These tests exercise ``StorageMappingSanityService`` with an injected probe:
  * healthy probe        -> Ready, kubernetes_mutation Ready, transport check Passed,
                            no missing_rm/dm warnings;
  * unreachable          -> Failed + mutation_transport_unreachable;
  * reachable/no-perm    -> Failed + mutation_no_permission;
  * CSI stays Ready even with zero fresh agent reports (transport, not agents, decides);
  * filesystem mappings  -> probe NOT consulted, classic RM/DM readiness preserved;
  * per-mapping mutation_mode/control_host are passed to the probe.

Plus a direct unit test of ``KubernetesNamespaceQuotaLiveAdapter.check_mutation_transport``
over a monkeypatched subprocess (can-i yes / no / unreachable).
"""

from __future__ import annotations

import subprocess

from dms.adapters import StaticKubernetesReadOnlyInventoryAdapter
from dms.adapters.kubernetes_quota import (
    KubernetesNamespaceQuotaLiveAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.config import Settings
from dms.db import Database
from dms.inventory import EffectiveInventoryService, StorageMappingSanityService
from dms.migrations import migrate_all
from dms.repositories import DmsRepository, ObservabilityRepository

CSI_DRIVER = "cephfs.csi.ceph.com"


def _static_inventory() -> dict:
    return {
        "clusters": {
            "ddz26": {
                "nodes": [],
                "storage_classes": [
                    {"name": "ceph-csi-sc", "provisioner": CSI_DRIVER},
                ],
                "csi_drivers": [{"name": CSI_DRIVER}],
            }
        }
    }


def _make_service(tmp_path, probe) -> StorageMappingSanityService:
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        agent_report_stale_seconds=1,
        control_cluster_name="ddz26",
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    ObservabilityRepository(observability_db)
    inventory_service = EffectiveInventoryService(
        repository=repository,
        kubernetes_inventory=StaticKubernetesReadOnlyInventoryAdapter(
            _static_inventory()
        ),
        settings=settings,
    )
    return StorageMappingSanityService(
        repository=repository,
        inventory_service=inventory_service,
        settings=settings,
        kubernetes_quota_probe=probe,
    )


def _csi_mapping(**bt) -> dict:
    return {
        "storage_name": "csi-ddz26",
        "backend_template": {
            "backend_type": "ceph-csi",
            "csi_driver": CSI_DRIVER,
            **bt,
        },
        "cluster_name": "ddz26",
        "storage_class_name": "ceph-csi-sc",
    }


def _codes(issues) -> set[str]:
    return {issue["code"] for issue in issues}


def _check_names(checks) -> set[str]:
    return {check["name"] for check in checks}


def test_csi_mapping_healthy_transport_is_ready(tmp_path):
    probe = StubKubernetesNamespaceQuotaAdapter()  # default reachable + can_mutate
    service = _make_service(tmp_path, probe)
    result = service.check_mapping(_csi_mapping())
    assert result["status"] == "Ready"
    assert result["readiness"]["kubernetes_mutation"] == "Ready"
    assert "kubernetes_mutation_transport" in _check_names(result["checks"])
    # CSI mappings do not surface RM/DM agent-evidence warnings.
    assert "missing_rm_readiness" not in _codes(result["warnings"])
    assert "missing_dm_readiness" not in _codes(result["warnings"])
    assert result["mutation_observed"]["can_mutate"] is True


def test_csi_mapping_unreachable_transport_fails(tmp_path):
    probe = StubKubernetesNamespaceQuotaAdapter(transport_reachable=False)
    service = _make_service(tmp_path, probe)
    result = service.check_mapping(_csi_mapping())
    assert result["status"] == "Failed"
    assert "mutation_transport_unreachable" in _codes(result["errors"])
    assert result["readiness"]["kubernetes_mutation"] == "Failed"


def test_csi_mapping_no_permission_fails(tmp_path):
    probe = StubKubernetesNamespaceQuotaAdapter(
        transport_reachable=True, transport_can_mutate=False
    )
    service = _make_service(tmp_path, probe)
    result = service.check_mapping(_csi_mapping())
    assert result["status"] == "Failed"
    assert "mutation_no_permission" in _codes(result["errors"])
    assert result["readiness"]["kubernetes_mutation"] == "Failed"


def test_csi_ready_even_without_fresh_agent_reports(tmp_path):
    # No agent reports exist at all -> fresh_reports == 0. A healthy transport must still
    # yield Ready (NOT Unknown) for CSI: agents do not gate CSI readiness.
    probe = StubKubernetesNamespaceQuotaAdapter()
    service = _make_service(tmp_path, probe)
    result = service.check_mapping(_csi_mapping())
    assert result["agent_observed"]["fresh_reports"] == 0
    assert result["status"] == "Ready"


def test_filesystem_mapping_does_not_consult_probe(tmp_path):
    probe = StubKubernetesNamespaceQuotaAdapter()
    service = _make_service(tmp_path, probe)
    # cephfs is a filesystem backend type; the cluster/storage-class exist in inventory
    # so there are no errors, only the classic RM/DM agent-evidence warnings.
    mapping = {
        "storage_name": "cephfs-fs",
        "backend_template": {"backend_type": "cephfs", "csi_driver": CSI_DRIVER},
        "cluster_name": "ddz26",
        "storage_class_name": "ceph-csi-sc",
    }
    result = service.check_mapping(mapping)
    assert probe.transport_calls == []  # probe never called for filesystem mappings
    assert "kubernetes_mutation" not in result["readiness"]
    assert "mutation_observed" not in result
    # Classic agent-evidence warnings still emitted for filesystem mappings.
    assert "missing_rm_readiness" in _codes(result["warnings"])
    assert "missing_dm_readiness" in _codes(result["warnings"])


def test_probe_receives_per_mapping_transport_args(tmp_path):
    probe = StubKubernetesNamespaceQuotaAdapter()
    service = _make_service(tmp_path, probe)
    service.check_mapping(
        _csi_mapping(mutation_mode="ssh-kubectl", control_host="10.10.10.20")
    )
    assert probe.transport_calls == [
        {
            "cluster_name": "ddz26",
            "mutation_mode": "ssh-kubectl",
            "control_host": "10.10.10.20",
        }
    ]


def test_no_probe_skips_transport_axis_backward_compatible(tmp_path):
    service = _make_service(tmp_path, None)
    result = service.check_mapping(_csi_mapping())
    # With no probe the transport axis is unknown and not asserted on; CSI still does not
    # fall back to agent-evidence warnings, and status is not Failed.
    assert result["readiness"]["kubernetes_mutation"] == "Unknown"
    assert "mutation_observed" not in result
    assert result["status"] != "Failed"


# --------------------------------------------------------------------------- #
# direct unit test: live adapter check_mutation_transport over subprocess
# --------------------------------------------------------------------------- #


def _completed(returncode: int, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["kubectl"], returncode=returncode, stdout="", stderr=stderr
    )


def test_live_check_mutation_transport_all_yes(monkeypatch):
    adapter = KubernetesNamespaceQuotaLiveAdapter(
        cluster_kubeconfigs={"ddz26": "/kc/ddz26"}, mode="kubectl"
    )
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return _completed(0)

    monkeypatch.setattr(
        "dms.adapters.kubernetes_quota.subprocess.run", fake_run
    )
    result = adapter.check_mutation_transport("ddz26")
    assert result["reachable"] is True
    assert result["can_mutate"] is True
    assert result["permissions"] == {"create": True, "patch": True, "delete": True}
    # all three verbs probed via kubectl auth can-i
    assert all("auth" in c and "can-i" in c for c in calls)
    assert {c[c.index("can-i") + 1] for c in calls} == {"create", "patch", "delete"}


def test_live_check_mutation_transport_no_permission(monkeypatch):
    adapter = KubernetesNamespaceQuotaLiveAdapter(
        cluster_kubeconfigs={"ddz26": "/kc/ddz26"}, mode="kubectl"
    )

    def fake_run(command, **_kwargs):
        return _completed(1)  # can-i = no

    monkeypatch.setattr(
        "dms.adapters.kubernetes_quota.subprocess.run", fake_run
    )
    result = adapter.check_mutation_transport("ddz26")
    assert result["reachable"] is True
    assert result["can_mutate"] is False
    assert result["permissions"]["create"] is False


def test_live_check_mutation_transport_unreachable(monkeypatch):
    adapter = KubernetesNamespaceQuotaLiveAdapter(
        cluster_kubeconfigs={"ddz26": "/kc/ddz26"}, mode="kubectl"
    )

    def fake_run(command, **_kwargs):
        raise FileNotFoundError(2, "No such file or directory", "kubectl")

    monkeypatch.setattr(
        "dms.adapters.kubernetes_quota.subprocess.run", fake_run
    )
    result = adapter.check_mutation_transport("ddz26")
    assert result["reachable"] is False
    assert result["can_mutate"] is False
    assert result["permissions"] == {"create": None, "patch": None, "delete": None}


def test_live_check_mutation_transport_rc1_connection_refused_is_unreachable(monkeypatch):
    # The real-world trap: a valid kubeconfig/ssh path but the API server is down. kubectl
    # exits 1 (same code as a clean RBAC "no") with a connection error on stderr. This must
    # be classified as unreachable, NOT permission-denied, so operators don't see
    # reachable=True/permissions=False for a cluster we never reached.
    adapter = KubernetesNamespaceQuotaLiveAdapter(
        cluster_kubeconfigs={"ddz26": "/kc/ddz26"}, mode="kubectl"
    )

    def fake_run(command, **_kwargs):
        return _completed(
            1,
            stderr=(
                "The connection to the server 10.0.0.1:6443 was refused - "
                "did you specify the right host or port?"
            ),
        )

    monkeypatch.setattr("dms.adapters.kubernetes_quota.subprocess.run", fake_run)
    result = adapter.check_mutation_transport("ddz26")
    assert result["reachable"] is False
    assert result["can_mutate"] is False
    assert result["permissions"] == {"create": None, "patch": None, "delete": None}
    assert "refused" in result["detail"]


def test_live_check_mutation_transport_never_raises_on_bad_returncode(monkeypatch):
    adapter = KubernetesNamespaceQuotaLiveAdapter(
        cluster_kubeconfigs={"ddz26": "/kc/ddz26"}, mode="kubectl"
    )

    def fake_run(command, **_kwargs):
        return _completed(255, stderr="connection refused")

    monkeypatch.setattr(
        "dms.adapters.kubernetes_quota.subprocess.run", fake_run
    )
    result = adapter.check_mutation_transport("ddz26")
    assert result["reachable"] is False
    assert "255" in result["detail"]


# --------------------------------------------------------------------------- #
# probe selection: a mapping's per-mapping transport must force the LIVE probe
# even with no global DMS_CLUSTER_CONTROL_HOSTS_JSON (the ddz26 case), while a
# transport-less environment stays on the healthy stub (tests / default CLI).
# --------------------------------------------------------------------------- #


def _settings(**kw) -> Settings:
    return Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
        **kw,
    )


class _MappingsRepo:
    """Minimal repository stub exposing only list_storage_mappings."""

    def __init__(self, mappings):
        self._mappings = mappings

    def list_storage_mappings(self, **_kwargs):
        return self._mappings


def test_should_use_live_probe_global_clusters():
    from dms.sanity_reconciler import _should_use_live_probe

    assert _should_use_live_probe(
        _settings(cluster_control_hosts={"ddz26": "10.10.10.20"}), None
    )
    assert _should_use_live_probe(_settings(cluster_kubeconfigs={"ddz26": "/kc"}), None)


def test_should_use_live_probe_per_mapping_transport_forces_live():
    # The ddz26 trap: NO global control-hosts env, but a mapping pins control_host /
    # mutation_mode -> live, so the real ssh-kubectl transport is validated (not stubbed
    # to a permanent Ready).
    from dms.sanity_reconciler import _should_use_live_probe

    repo_host = _MappingsRepo(
        [{"backend_template": {"backend_type": "ceph-csi", "control_host": "10.10.10.20"}}]
    )
    assert _should_use_live_probe(_settings(), repo_host) is True

    repo_mode = _MappingsRepo(
        [{"backend_template": {"backend_type": "ceph-csi", "mutation_mode": "ssh-kubectl"}}]
    )
    assert _should_use_live_probe(_settings(), repo_mode) is True


def test_should_use_live_probe_stub_when_no_transport():
    from dms.sanity_reconciler import _should_use_live_probe

    assert _should_use_live_probe(_settings(), None) is False
    repo_plain = _MappingsRepo([{"backend_template": {"backend_type": "ceph-csi"}}])
    assert _should_use_live_probe(_settings(), repo_plain) is False


def test_probe_factory_picks_live_for_per_mapping_transport():
    from dms.sanity_reconciler import mutation_transport_probe_from_settings

    repo = _MappingsRepo([{"backend_template": {"control_host": "10.10.10.20"}}])
    assert isinstance(
        mutation_transport_probe_from_settings(_settings(), repo),
        KubernetesNamespaceQuotaLiveAdapter,
    )
    assert isinstance(
        mutation_transport_probe_from_settings(_settings(), None),
        StubKubernetesNamespaceQuotaAdapter,
    )
