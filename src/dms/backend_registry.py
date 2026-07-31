from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .backends.gpfs import (
    GPFS_BACKEND_TYPE,
    GpfsBackendTemplate,
    GpfsDataManagementAdapter,
)
from .backends.weka import (
    WEKAFS_BACKEND_TYPE,
    WekaFsBackendTemplate,
    WekaFsDataManagementAdapter,
)
from .repositories import DmsRepository


@dataclass
class BackendAdapterRegistry:
    """Resolves the per-storage data-management worker pool from a storage mapping.

    Backends that pin their own node set (GPFS / WekaFS templates) get it from the
    backend adapter; everything else falls back to agent-inventory selection, i.e. the
    DM worker picks from nodes whose agent reports the mount as ready.
    """

    repository: DmsRepository

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

    @staticmethod
    def _backend_type(mapping: dict[str, Any] | None) -> str | None:
        if not mapping:
            return None
        return (mapping.get("backend_template") or {}).get("backend_type")
