from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import (
    FilesystemBackendAdapter,
    KubernetesNamespaceQuotaAdapter,
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from .backends.gpfs import (
    GPFS_BACKEND_TYPE,
    GpfsBackendTemplate,
    GpfsDataManagementAdapter,
    GpfsFilesystemBackendAdapter,
    GpfsKubernetesNamespaceQuotaAdapter,
)
from .repositories import DmsRepository


@dataclass
class BackendAdapterRegistry:
    repository: DmsRepository
    default_filesystem_adapter: FilesystemBackendAdapter
    default_kubernetes_adapter: KubernetesNamespaceQuotaAdapter

    @classmethod
    def with_phase1_defaults(cls, repository: DmsRepository) -> "BackendAdapterRegistry":
        return cls(
            repository=repository,
            default_filesystem_adapter=StubFilesystemBackendAdapter(),
            default_kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        )

    def filesystem_for_plan(self, plan: dict[str, Any]) -> FilesystemBackendAdapter:
        mapping = self._mapping_for_plan(plan)
        if self._backend_type(mapping) == GPFS_BACKEND_TYPE:
            return GpfsFilesystemBackendAdapter(
                GpfsBackendTemplate.from_storage_mapping(mapping)
            )
        return self.default_filesystem_adapter

    def kubernetes_for_plan(self, plan: dict[str, Any]) -> KubernetesNamespaceQuotaAdapter:
        mapping = self._mapping_for_plan(plan)
        if self._backend_type(mapping) == GPFS_BACKEND_TYPE:
            return GpfsKubernetesNamespaceQuotaAdapter(
                GpfsBackendTemplate.from_storage_mapping(mapping)
            )
        return self.default_kubernetes_adapter

    def data_worker_pool(self, storage_name: str) -> dict[str, Any]:
        mapping = self.repository.get_storage_mapping(storage_name)
        if self._backend_type(mapping) == GPFS_BACKEND_TYPE:
            template = GpfsBackendTemplate.from_storage_mapping(mapping)
            return GpfsDataManagementAdapter(template).worker_pool(storage_name)
        return {
            "selection": "agent-inventory",
            "required_mounts": [storage_name],
            "candidates": [],
        }

    def _mapping_for_plan(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        desired = plan.get("desired_state", {})
        storage_name = desired.get("storage_name")
        if storage_name:
            return self.repository.get_storage_mapping(storage_name)
        cluster_name = desired.get("cluster_name")
        storage_class_name = desired.get("storage_class_name")
        if cluster_name and storage_class_name:
            return self.repository.get_storage_mapping_by_cluster_storage_class(
                cluster_name, storage_class_name
            )
        return None

    @staticmethod
    def _backend_type(mapping: dict[str, Any] | None) -> str | None:
        if not mapping:
            return None
        return mapping["backend_template"].get("backend_type")
