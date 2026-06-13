from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import (
    BackendPreconditionError,
    FilesystemBackendAdapter,
    KubernetesNamespaceQuotaAdapter,
    KubernetesNamespaceQuotaLiveAdapter,
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from .backends.gpfs import (
    GPFS_BACKEND_TYPE,
    GpfsBackendTemplate,
    GpfsDataManagementAdapter,
    GpfsFilesystemBackendAdapter,
)
from .backends.cephfs import (
    CEPHFS_BACKEND_TYPE,
    CephFsHostMountedFilesystemBackendAdapter,
)
from .backends.weka import (
    WEKAFS_BACKEND_TYPE,
    WekaFsBackendTemplate,
    WekaFsDataManagementAdapter,
    WekaFsHostMountedFilesystemBackendAdapter,
)
from .config import Settings
from .repositories import DmsRepository


@dataclass
class BackendAdapterRegistry:
    repository: DmsRepository
    default_filesystem_adapter: FilesystemBackendAdapter | None = None
    default_kubernetes_adapter: KubernetesNamespaceQuotaAdapter | None = None
    settings: Settings | None = None
    enforce_supported_backends: bool = True

    @classmethod
    def with_live_defaults(
        cls, repository: DmsRepository, settings: Settings
    ) -> "BackendAdapterRegistry":
        return cls(
            repository=repository,
            default_filesystem_adapter=None,
            default_kubernetes_adapter=KubernetesNamespaceQuotaLiveAdapter.from_settings(
                settings
            ),
            settings=settings,
            enforce_supported_backends=True,
        )

    @classmethod
    def with_test_stubs(
        cls, repository: DmsRepository, settings: Settings | None = None
    ) -> "BackendAdapterRegistry":
        return cls(
            repository=repository,
            default_filesystem_adapter=StubFilesystemBackendAdapter(),
            default_kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
            settings=settings,
            enforce_supported_backends=False,
        )

    @classmethod
    def with_phase1_defaults(
        cls, repository: DmsRepository, settings: Settings | None = None
    ) -> "BackendAdapterRegistry":
        if settings is not None:
            return cls.with_live_defaults(repository, settings)
        return cls.with_test_stubs(repository, settings)

    def filesystem_for_plan(self, plan: dict[str, Any]) -> FilesystemBackendAdapter:
        mapping = self._mapping_for_plan(plan)
        backend_type = self._backend_type(mapping)
        if backend_type == GPFS_BACKEND_TYPE:
            return GpfsFilesystemBackendAdapter.from_storage_mapping(
                mapping,
                self.settings,
            )
        if backend_type == CEPHFS_BACKEND_TYPE:
            return CephFsHostMountedFilesystemBackendAdapter.from_storage_mapping(
                mapping,
                self.settings or Settings.from_env(),
            )
        if backend_type == WEKAFS_BACKEND_TYPE:
            return WekaFsHostMountedFilesystemBackendAdapter.from_storage_mapping(
                mapping,
                self.settings or Settings.from_env(),
            )
        if not self.enforce_supported_backends and self.default_filesystem_adapter:
            return self.default_filesystem_adapter
        raise BackendPreconditionError(
            self._unsupported_backend_message("filesystem", backend_type, plan)
        )

    def kubernetes_for_plan(
        self, plan: dict[str, Any]
    ) -> KubernetesNamespaceQuotaAdapter:
        if self.default_kubernetes_adapter:
            return self.default_kubernetes_adapter
        raise BackendPreconditionError(
            "Kubernetes namespace quota live adapter is not configured for "
            f"{plan.get('resource_key')}"
        )

    def data_worker_pool(self, storage_name: str) -> dict[str, Any]:
        mapping = self.repository.get_storage_mapping(storage_name)
        backend_type = self._backend_type(mapping)
        if backend_type == GPFS_BACKEND_TYPE:
            template = GpfsBackendTemplate.from_storage_mapping(mapping)
            return GpfsDataManagementAdapter(template).worker_pool(storage_name)
        if backend_type == WEKAFS_BACKEND_TYPE:
            template = WekaFsBackendTemplate.from_storage_mapping(mapping)
            return WekaFsDataManagementAdapter(template).worker_pool(storage_name)
        return {
            "selection": "agent-inventory",
            "required_mounts": [storage_name],
            "candidates": [],
        }

    def _mapping_for_plan(self, plan: dict[str, Any]) -> dict[str, Any] | None:
        mappings = self._mappings_for_plan(plan)
        if mappings:
            return mappings[0]
        return None

    def _mappings_for_plan(self, plan: dict[str, Any]) -> list[dict[str, Any]]:
        desired = plan.get("desired_state", {})
        mappings: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add_mapping(mapping: dict[str, Any] | None) -> None:
            if not mapping:
                return
            key = mapping.get("storage_name") or (
                f"{mapping.get('cluster_name')}:{mapping.get('storage_class_name')}"
            )
            if key in seen:
                return
            seen.add(key)
            mappings.append(mapping)

        storage_name = desired.get("storage_name")
        if storage_name:
            add_mapping(self.repository.get_storage_mapping(storage_name))
        for entry in desired.get("storage_class_quotas") or []:
            if isinstance(entry, dict) and entry.get("storage_name"):
                add_mapping(self.repository.get_storage_mapping(entry["storage_name"]))
        cluster_name = desired.get("cluster_name")
        storage_class_name = desired.get("storage_class_name")
        if cluster_name and storage_class_name:
            add_mapping(
                self.repository.get_storage_mapping_by_cluster_storage_class(
                    cluster_name, storage_class_name
                )
            )
        for entry in desired.get("storage_class_quotas") or []:
            if not isinstance(entry, dict):
                continue
            entry_storage_class = entry.get("storage_class_name")
            if cluster_name and entry_storage_class:
                add_mapping(
                    self.repository.get_storage_mapping_by_cluster_storage_class(
                        cluster_name, entry_storage_class
                    )
                )
        return mappings

    @staticmethod
    def _backend_type(mapping: dict[str, Any] | None) -> str | None:
        if not mapping:
            return None
        return mapping["backend_template"].get("backend_type")

    @staticmethod
    def _unsupported_backend_message(
        backend_kind: str, backend_type: str | None, plan: dict[str, Any]
    ) -> str:
        return (
            f"unsupported {backend_kind} backend type for {plan.get('resource_key')}: "
            f"{backend_type or 'unmapped'}"
        )
