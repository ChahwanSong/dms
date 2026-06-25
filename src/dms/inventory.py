from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import KubernetesReadOnlyInventoryAdapter
from .config import Settings
from .domain import (
    _FILESYSTEM_BACKEND_TYPES,
    StorageMappingInput,
    StorageMappingSanityStatus,
    WorkerRole,
)
from .repositories import DmsRepository, iso_now


@dataclass
class EffectiveInventoryService:
    repository: DmsRepository
    kubernetes_inventory: KubernetesReadOnlyInventoryAdapter | None
    settings: Settings

    def effective_inventory(self) -> dict[str, Any]:
        k8s_inventory = (
            self.kubernetes_inventory.read_inventory()
            if self.kubernetes_inventory is not None
            else {"clusters": {}}
        )
        reports = self.repository.list_agent_reports(
            stale_seconds=self.settings.agent_report_stale_seconds,
            update_stale=True,
            limit=1000,
        )
        clusters = {
            name: {
                "nodes": data.get("nodes", []),
                "storage_classes": data.get("storage_classes", []),
                "csi_drivers": data.get("csi_drivers", []),
                "agent_reports": {"fresh": [], "stale": []},
            }
            for name, data in k8s_inventory.get("clusters", {}).items()
        }
        worker_roles: dict[str, dict[str, Any]] = {
            WorkerRole.RM.value: {},
            WorkerRole.DM.value: {},
        }
        seen_role_nodes: set[tuple[str, str, str]] = set()
        for report in reports:
            cluster_name = report["cluster_name"]
            clusters.setdefault(
                cluster_name,
                {
                    "nodes": [],
                    "storage_classes": [],
                    "csi_drivers": [],
                    "agent_reports": {"fresh": [], "stale": []},
                },
            )
            bucket = "fresh" if report["freshness_status"] == "Fresh" else "stale"
            clusters[cluster_name]["agent_reports"][bucket].append(report)
            if report["freshness_status"] != "Fresh":
                continue
            role = report["worker_role"]
            role_node_key = (role, cluster_name, report["node_name"])
            if role_node_key in seen_role_nodes:
                continue
            seen_role_nodes.add(role_node_key)
            role_cluster = worker_roles.setdefault(role, {}).setdefault(
                cluster_name,
                {
                    "nodes": [],
                    "mounts_by_storage_name": {},
                    "csi_by_driver": {},
                    "tools": [],
                    "credentials": [],
                    "networks": [],
                },
            )
            role_cluster["nodes"].append(
                {
                    "node_name": report["node_name"],
                    "node_uid": report["node_uid"],
                    "report_id": report["report_id"],
                }
            )
            payload = report["report"]
            for mount in payload.get("mounts", []):
                storage_name = mount.get("storage_name")
                if storage_name and _evidence_is_ready(mount):
                    role_cluster["mounts_by_storage_name"].setdefault(
                        storage_name, []
                    ).append({"node_name": report["node_name"], **mount})
            for csi in payload.get("csi", []):
                driver = csi.get("driver")
                if driver and _evidence_is_ready(csi):
                    role_cluster["csi_by_driver"].setdefault(driver, []).append(
                        {"node_name": report["node_name"], **csi}
                    )
            for tool in payload.get("tools", []):
                role_cluster["tools"].append(_normalize_tool(tool, report["node_name"]))
            role_cluster["credentials"].extend(payload.get("credentials", []))
            role_cluster["networks"].extend(payload.get("networks", []))
        return {
            "clusters": clusters,
            "worker_roles": worker_roles,
            "control_cluster_name": self.settings.control_cluster_name,
        }


def _evidence_is_ready(evidence: dict[str, Any]) -> bool:
    status = evidence.get("status")
    if status is not None and str(status).lower() != "ready":
        return False
    if evidence.get("healthy") is False:
        return False
    if evidence.get("node_plugin_ready") is False:
        return False
    return True


@dataclass
class StorageMappingSanityService:
    repository: DmsRepository
    inventory_service: EffectiveInventoryService
    settings: Settings
    # Optional ResourceQuota mutation-transport probe (any adapter exposing
    # ``check_mutation_transport``). When None, the kubernetes_mutation axis is left
    # "Unknown" (no probe runs); CSI mappings still skip the RM/DM agent-evidence
    # warnings and use the CSI status rule. In practice both wirings (API + reconciler)
    # always inject a stub/live probe, so None only occurs on direct construction.
    kubernetes_quota_probe: Any | None = None

    def check_input(self, data: StorageMappingInput) -> dict[str, Any]:
        return self._check(
            storage_name=data.storage_name,
            backend_template=data.backend_template,
            cluster_name=data.cluster_name,
            storage_class_name=data.storage_class_name,
        )

    def check_mapping(self, mapping: dict[str, Any]) -> dict[str, Any]:
        return self._check(
            storage_name=mapping["storage_name"],
            backend_template=mapping["backend_template"],
            cluster_name=mapping.get("cluster_name"),
            storage_class_name=mapping.get("storage_class_name"),
        )

    def _check(
        self,
        *,
        storage_name: str,
        backend_template: dict[str, Any],
        cluster_name: str | None,
        storage_class_name: str | None,
    ) -> dict[str, Any]:
        inventory = self.inventory_service.effective_inventory()
        checks: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []
        errors: list[dict[str, str]] = []
        expected_driver = backend_template.get("csi_driver") or _default_csi_driver(
            backend_template.get("backend_type")
        )
        cluster_inventory = (
            inventory.get("clusters", {}).get(cluster_name) if cluster_name else None
        )
        storage_class: dict[str, Any] | None = None
        if not backend_template.get("backend_type"):
            errors.append(
                _issue(
                    "backend_type_missing", "backend_template.backend_type is required"
                )
            )
        else:
            checks.append(_passed("backend_type_present"))
        if cluster_name:
            if not cluster_inventory:
                errors.append(
                    _issue("cluster_missing", f"cluster not found: {cluster_name}")
                )
            else:
                checks.append(_passed("cluster_exists"))
        if storage_class_name:
            storage_class = _find_storage_class(cluster_inventory, storage_class_name)
            if not storage_class:
                errors.append(
                    _issue(
                        "storage_class_missing",
                        f"StorageClass not found: {cluster_name}/{storage_class_name}",
                    )
                )
            else:
                checks.append(_passed("storage_class_exists"))
                actual_driver = storage_class.get("provisioner")
                if expected_driver and actual_driver != expected_driver:
                    errors.append(
                        _issue(
                            "csi_driver_mismatch",
                            f"expected {expected_driver}, observed {actual_driver}",
                        )
                    )
                else:
                    checks.append(_passed("csi_driver_matches"))
        rm_worker_nodes = backend_template.get("rm_worker_nodes") or []
        rm_readiness, rm_candidates = _role_readiness(
            inventory=inventory,
            role=WorkerRole.RM.value,
            cluster_name=cluster_name,
            storage_name=storage_name,
            csi_driver=expected_driver,
            storage_class_name=storage_class_name,
            allowed_nodes=set(rm_worker_nodes) if rm_worker_nodes else None,
        )
        dm_readiness, dm_candidates = _role_readiness(
            inventory=inventory,
            role=WorkerRole.DM.value,
            cluster_name=self.settings.control_cluster_name,
            storage_name=storage_name,
            csi_driver=expected_driver,
            storage_class_name=storage_class_name,
        )
        fresh_reports = sum(
            len(cluster.get("agent_reports", {}).get("fresh", []))
            for cluster in inventory.get("clusters", {}).values()
        )
        stale_reports = sum(
            len(cluster.get("agent_reports", {}).get("stale", []))
            for cluster in inventory.get("clusters", {}).values()
        )
        backend_type = backend_template.get("backend_type")
        is_csi = bool(backend_type) and backend_type not in _FILESYSTEM_BACKEND_TYPES
        # Agent-report aggregate signals only gate filesystem mappings (which depend on
        # node-agent evidence). CSI/k8s mappings may target agentless managed clusters, so
        # the absence/staleness of agent reports must not degrade or fail them -- their
        # readiness comes from the ResourceQuota mutation transport axis below.
        if not is_csi:
            if fresh_reports == 0 and stale_reports > 0:
                errors.append(
                    _issue(
                        "stale_only_inventory",
                        "only stale Agent reports are available",
                    )
                )
            elif fresh_reports == 0:
                warnings.append(
                    _issue(
                        "agent_inventory_missing",
                        "no fresh Agent reports are available",
                    )
                )
        readiness = {
            "resource_management": rm_readiness,
            "data_management": dm_readiness,
            "inventory": "Ready" if fresh_reports else "Unknown",
        }
        # CSI/k8s mappings are validated by their ResourceQuota MUTATION TRANSPORT (the
        # path the RM worker actually takes to apply a quota), not by RM/DM agent evidence
        # -- agentless managed clusters legitimately run no DMS node agent. So we skip the
        # missing_rm/dm_readiness warnings for them and, when a probe is wired, replace the
        # agent-evidence axis with a kubernetes_mutation transport+permission check.
        mutation_observed: dict[str, Any] | None = None
        if is_csi:
            if self.kubernetes_quota_probe is not None:
                try:
                    mutation_observed = (
                        self.kubernetes_quota_probe.check_mutation_transport(
                            cluster_name,
                            mutation_mode=backend_template.get("mutation_mode"),
                            control_host=backend_template.get("control_host"),
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - probe must never break the sweep
                    mutation_observed = {
                        "mode": backend_template.get("mutation_mode"),
                        "control_host": backend_template.get("control_host"),
                        "reachable": False,
                        "permissions": {
                            "create": None,
                            "patch": None,
                            "delete": None,
                        },
                        "can_mutate": False,
                        "detail": f"mutation transport probe failed: {exc}",
                    }
                if not mutation_observed.get("reachable"):
                    via = mutation_observed.get("control_host")
                    mode = mutation_observed.get("mode") or "kubectl"
                    target = f"{mode} via {via}" if via else mode
                    errors.append(
                        _issue(
                            "mutation_transport_unreachable",
                            f"{target}로 대상 클러스터 도달 실패: "
                            f"{mutation_observed.get('detail')}",
                        )
                    )
                    readiness["kubernetes_mutation"] = "Failed"
                elif not mutation_observed.get("can_mutate"):
                    errors.append(
                        _issue(
                            "mutation_no_permission",
                            "ResourceQuota create/patch/delete 권한 없음 "
                            "(kubectl auth can-i = no)",
                        )
                    )
                    readiness["kubernetes_mutation"] = "Failed"
                else:
                    checks.append(_passed("kubernetes_mutation_transport"))
                    readiness["kubernetes_mutation"] = "Ready"
            else:
                readiness["kubernetes_mutation"] = "Unknown"
        else:
            if rm_readiness != "Ready":
                warnings.append(
                    _issue("missing_rm_readiness", "no fresh RM evidence for storage")
                )
            if dm_readiness != "Ready":
                warnings.append(
                    _issue("missing_dm_readiness", "no fresh DM evidence for storage")
                )
        status = _status_from(errors, warnings, fresh_reports, is_csi=is_csi)
        result: dict[str, Any] = {
            "storage_name": storage_name,
            "status": status,
            "checked_at": iso_now(),
            "kubernetes_observed": {
                "cluster_name": cluster_name,
                "storage_class_name": storage_class_name,
                "storage_class_exists": storage_class is not None,
                "provisioner": (
                    storage_class.get("provisioner") if storage_class else None
                ),
            },
            "agent_observed": {
                "fresh_reports": fresh_reports,
                "stale_reports": stale_reports,
                "rm_readiness": rm_readiness,
                "dm_readiness": dm_readiness,
                "rm_candidates": rm_candidates,
                "dm_candidates": dm_candidates,
            },
            "readiness": readiness,
            "checks": checks,
            "warnings": warnings,
            "errors": errors,
        }
        if mutation_observed is not None:
            # Expose the probe result so the portal / docs can render mode, control_host,
            # reachable, permissions and can_mutate for CSI mappings.
            result["mutation_observed"] = mutation_observed
        return result


def _normalize_tool(tool: Any, node_name: str) -> dict[str, Any]:
    if isinstance(tool, str):
        return {"name": tool, "healthy": True, "node_name": node_name}
    if isinstance(tool, dict):
        return {"node_name": node_name, **tool}
    return {"name": str(tool), "healthy": True, "node_name": node_name}


def _find_storage_class(
    cluster_inventory: dict[str, Any] | None, storage_class_name: str
) -> dict[str, Any] | None:
    if not cluster_inventory:
        return None
    for storage_class in cluster_inventory.get("storage_classes", []):
        if storage_class.get("name") == storage_class_name:
            return storage_class
    return None


def _role_readiness(
    *,
    inventory: dict[str, Any],
    role: str,
    cluster_name: str | None,
    storage_name: str,
    csi_driver: str | None,
    storage_class_name: str | None,
    allowed_nodes: set[str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    if not cluster_name:
        return "Unknown", []
    role_cluster = (
        inventory.get("worker_roles", {}).get(role, {}).get(cluster_name) or {}
    )
    candidates: list[dict[str, Any]] = []
    for entry in role_cluster.get("mounts_by_storage_name", {}).get(storage_name, []):
        if allowed_nodes is None or entry.get("node_name") in allowed_nodes:
            candidates.append(entry)
    if csi_driver:
        for csi in role_cluster.get("csi_by_driver", {}).get(csi_driver, []):
            if allowed_nodes is not None and csi.get("node_name") not in allowed_nodes:
                continue
            storage_classes = csi.get("storage_classes") or []
            if not storage_class_name or storage_class_name in storage_classes:
                candidates.append(csi)
    return ("Ready", candidates) if candidates else ("Missing", [])


def _default_csi_driver(backend_type: str | None) -> str | None:
    return {
        "cephfs": "rook-ceph.cephfs.csi.ceph.com",
        "gpfs": "spectrumscale.csi.ibm.com",
        "longhorn": "driver.longhorn.io",
        "wekafs": "csi.weka.io",
    }.get(backend_type or "")


def _status_from(
    errors: list[dict[str, str]],
    warnings: list[dict[str, str]],
    fresh_reports: int,
    *,
    is_csi: bool = False,
) -> str:
    if errors:
        return StorageMappingSanityStatus.FAILED.value
    if is_csi:
        # CSI/k8s mappings are validated by the ResourceQuota mutation transport, not by
        # agent reports -- so a healthy transport must not be downgraded to Unknown just
        # because no DMS node agent reports for the (possibly agentless) cluster.
        if warnings:
            return StorageMappingSanityStatus.DEGRADED.value
        return StorageMappingSanityStatus.READY.value
    if not fresh_reports:
        return StorageMappingSanityStatus.UNKNOWN.value
    if warnings:
        return StorageMappingSanityStatus.DEGRADED.value
    return StorageMappingSanityStatus.READY.value


def _passed(name: str) -> dict[str, str]:
    return {"name": name, "status": "Passed"}


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
