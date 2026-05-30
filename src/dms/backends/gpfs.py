from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dms.adapters import AdapterResult


GPFS_BACKEND_TYPE = "gpfs"
GPFS_CSI_DRIVER = "spectrumscale.csi.ibm.com"


@dataclass(frozen=True)
class GpfsBackendTemplate:
    storage_name: str
    filesystem_name: str
    mount_path: str
    fileset_root: str | None
    quota_scope: str
    csi_driver: str
    storage_class_name: str | None
    data_network: str | None

    @classmethod
    def from_storage_mapping(cls, mapping: dict[str, Any]) -> "GpfsBackendTemplate":
        template = mapping["backend_template"]
        return cls(
            storage_name=mapping["storage_name"],
            filesystem_name=template.get("filesystem_name", mapping["storage_name"]),
            mount_path=template.get("mount_path", ""),
            fileset_root=template.get("fileset_root"),
            quota_scope=template.get("quota_scope", "fileset"),
            csi_driver=template.get("csi_driver", GPFS_CSI_DRIVER),
            storage_class_name=(
                template.get("storage_class_name") or mapping.get("storage_class_name")
            ),
            data_network=template.get("data_network"),
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "backend_type": GPFS_BACKEND_TYPE,
            "storage_name": self.storage_name,
            "filesystem_name": self.filesystem_name,
            "mount_path": self.mount_path,
            "fileset_root": self.fileset_root,
            "quota_scope": self.quota_scope,
            "csi_driver": self.csi_driver,
            "storage_class_name": self.storage_class_name,
            "data_network": self.data_network,
        }


@dataclass(frozen=True)
class GpfsQuotaStrategy:
    backend_type: str = GPFS_BACKEND_TYPE

    def render_quota(self, quota: dict[str, Any]) -> dict[str, Any]:
        return {
            "backend_type": self.backend_type,
            "quota_scope": quota.get("scope", "fileset"),
            "capacity_bytes": quota.get("capacity_bytes"),
            "file_count": quota.get("file_count"),
            "hard_limit_bytes": quota.get("hard_limit_bytes", quota.get("capacity_bytes")),
            "hard_file_limit": quota.get("hard_file_limit", quota.get("file_count")),
            "command_family": "gpfs-quota",
            "side_effect": "not-executed-phase1",
        }


@dataclass
class GpfsFilesystemBackendAdapter:
    template: GpfsBackendTemplate
    quota_strategy: GpfsQuotaStrategy = GpfsQuotaStrategy()

    def create(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("create", plan)

    def update(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("update", plan)

    def block(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("block", plan)

    def initialize(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("initialize", plan)

    def delete(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("delete", plan)

    def consistency_check(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("consistency_check", plan)

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("sync_live_state", plan)

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("import_directory", plan)

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("assign_quota_only", plan)

    def _result(self, operation: str, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        quota = self.quota_strategy.render_quota(desired.get("quota", {}))
        backend = self.template.metadata()
        applied = {
            "adapter": "gpfs-filesystem-stub",
            "operation": operation,
            "backend": backend,
            "quota": quota,
            "directory_name": desired.get("directory_name"),
            "side_effect": "not-executed-phase1",
        }
        observed = {
            "adapter": "gpfs-filesystem-stub",
            "verified": True,
            "operation": operation,
            "backend": backend,
            "resource_key": plan["resource_key"],
            "quota": quota,
        }
        return AdapterResult(
            applied_state=applied,
            observed_state=observed,
            message=f"GPFS filesystem skeleton {operation} completed",
        )


@dataclass
class GpfsKubernetesNamespaceQuotaAdapter:
    template: GpfsBackendTemplate

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {
            "cluster_name": cluster_name,
            "namespace_name": namespace_name,
            "backend_type": GPFS_BACKEND_TYPE,
            "side_effect": "not-executed-phase1",
        }

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult:
        return self.apply_resource_quota(plan)

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        hard_limits = self._hard_limits(desired)
        backend = self.template.metadata()
        applied = {
            "adapter": "gpfs-kubernetes-quota-stub",
            "resource_quota_name": "dms-storage-quota",
            "backend": backend,
            "hard": hard_limits,
            "side_effect": "not-executed-phase1",
        }
        observed = {
            "adapter": "gpfs-kubernetes-quota-stub",
            "verified": True,
            "resource_quota_name": "dms-storage-quota",
            "backend": backend,
            "hard": hard_limits,
        }
        return AdapterResult(
            applied_state=applied,
            observed_state=observed,
            message="GPFS Kubernetes namespace quota skeleton completed",
        )

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        backend = self.template.metadata()
        return AdapterResult(
            applied_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "deleted": True,
                "backend": backend,
                "resource_quota_name": "dms-storage-quota",
            },
            observed_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "verified": True,
                "deleted": True,
                "backend": backend,
            },
            message="GPFS Kubernetes namespace quota delete skeleton completed",
        )

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        backend = self.template.metadata()
        return AdapterResult(
            applied_state={"adapter": "gpfs-kubernetes-quota-stub", "backend": backend},
            observed_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "verified": True,
                "synced": True,
                "backend": backend,
            },
            message="GPFS Kubernetes namespace quota sync skeleton completed",
        )

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        backend = self.template.metadata()
        return AdapterResult(
            applied_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "backend": backend,
                "backend_side_effect": False,
            },
            observed_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "verified": True,
                "consistency_status": "Consistent",
                "backend": backend,
            },
            message="GPFS Kubernetes namespace quota consistency check skeleton completed",
        )

    def audit_resource_quotas(self, plan: dict[str, Any]) -> AdapterResult:
        backend = self.template.metadata()
        return AdapterResult(
            applied_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "backend": backend,
                "backend_side_effect": False,
                "operation": "resourcequota.audit",
            },
            observed_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "verified": True,
                "audit_status": "Consistent",
                "target_count": 0,
                "issue_count": 0,
                "targets": [],
                "backend": backend,
            },
            message="GPFS Kubernetes namespace quota audit skeleton completed",
        )

    def _hard_limits(self, desired: dict[str, Any]) -> dict[str, Any]:
        quota = desired.get("quota", {})
        storage_class_name = self.template.storage_class_name
        hard = {
            "requests.storage": quota.get("requests_storage_bytes")
            or quota.get("capacity_bytes"),
            "persistentvolumeclaims": quota.get("pvc_count"),
        }
        if storage_class_name:
            hard[f"{storage_class_name}.storageclass.storage.k8s.io/requests.storage"] = (
                quota.get("storage_class_requests_storage_bytes")
                or quota.get("requests_storage_bytes")
                or quota.get("capacity_bytes")
            )
        return {key: value for key, value in hard.items() if value is not None}


@dataclass(frozen=True)
class GpfsDataManagementAdapter:
    template: GpfsBackendTemplate

    def worker_pool(self, storage_name: str) -> dict[str, Any]:
        return {
            "selection": "agent-inventory",
            "backend_type": GPFS_BACKEND_TYPE,
            "required_mounts": [storage_name],
            "mount_path": self.template.mount_path,
            "filesystem_name": self.template.filesystem_name,
            "data_network": self.template.data_network,
            "tool_candidates": ["dsync", "nsync", "drm", "dscan"],
            "requires_posix_identity": True,
            "candidates": [],
        }
