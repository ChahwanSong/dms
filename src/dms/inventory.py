from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import KubernetesReadOnlyInventoryAdapter
from .config import Settings
from .domain import StorageMappingInput, StorageMappingSanityStatus, WorkerRole
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
            bucket = (
                "fresh" if report["freshness_status"] == "Fresh" else "stale"
            )
            clusters[cluster_name]["agent_reports"][bucket].append(report)
            if report["freshness_status"] != "Fresh":
                continue
            role = report["worker_role"]
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
                if storage_name:
                    role_cluster["mounts_by_storage_name"].setdefault(
                        storage_name, []
                    ).append({"node_name": report["node_name"], **mount})
            for csi in payload.get("csi", []):
                driver = csi.get("driver")
                if driver:
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


@dataclass
class StorageMappingSanityService:
    repository: DmsRepository
    inventory_service: EffectiveInventoryService
    settings: Settings

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
            errors.append(_issue("backend_type_missing", "backend_template.backend_type is required"))
        else:
            checks.append(_passed("backend_type_present"))
        if cluster_name:
            if not cluster_inventory:
                errors.append(_issue("cluster_missing", f"cluster not found: {cluster_name}"))
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
        rm_readiness, rm_candidates = _role_readiness(
            inventory=inventory,
            role=WorkerRole.RM.value,
            cluster_name=cluster_name,
            storage_name=storage_name,
            csi_driver=expected_driver,
            storage_class_name=storage_class_name,
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
        if fresh_reports == 0 and stale_reports > 0:
            errors.append(_issue("stale_only_inventory", "only stale Agent reports are available"))
        elif fresh_reports == 0:
            warnings.append(_issue("agent_inventory_missing", "no fresh Agent reports are available"))
        if rm_readiness != "Ready":
            warnings.append(_issue("missing_rm_readiness", "no fresh RM evidence for storage"))
        if dm_readiness != "Ready":
            warnings.append(_issue("missing_dm_readiness", "no fresh DM evidence for storage"))
        status = _status_from(errors, warnings, fresh_reports)
        readiness = {
            "resource_management": rm_readiness,
            "data_management": dm_readiness,
            "inventory": "Ready" if fresh_reports else "Unknown",
        }
        return {
            "storage_name": storage_name,
            "status": status,
            "checked_at": iso_now(),
            "kubernetes_observed": {
                "cluster_name": cluster_name,
                "storage_class_name": storage_class_name,
                "storage_class_exists": storage_class is not None,
                "provisioner": storage_class.get("provisioner") if storage_class else None,
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
) -> tuple[str, list[dict[str, Any]]]:
    if not cluster_name:
        return "Unknown", []
    role_cluster = (
        inventory.get("worker_roles", {}).get(role, {}).get(cluster_name) or {}
    )
    candidates: list[dict[str, Any]] = []
    candidates.extend(role_cluster.get("mounts_by_storage_name", {}).get(storage_name, []))
    if csi_driver:
        for csi in role_cluster.get("csi_by_driver", {}).get(csi_driver, []):
            storage_classes = csi.get("storage_classes") or []
            if not storage_class_name or storage_class_name in storage_classes:
                candidates.append(csi)
    return ("Ready", candidates) if candidates else ("Missing", [])


def _default_csi_driver(backend_type: str | None) -> str | None:
    return {
        "cephfs": "rook-ceph.cephfs.csi.ceph.com",
        "longhorn": "driver.longhorn.io",
    }.get(backend_type or "")


def _status_from(
    errors: list[dict[str, str]], warnings: list[dict[str, str]], fresh_reports: int
) -> str:
    if errors:
        return StorageMappingSanityStatus.FAILED.value
    if not fresh_reports:
        return StorageMappingSanityStatus.UNKNOWN.value
    if warnings:
        return StorageMappingSanityStatus.DEGRADED.value
    return StorageMappingSanityStatus.READY.value


def _passed(name: str) -> dict[str, str]:
    return {"name": name, "status": "Passed"}


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}
