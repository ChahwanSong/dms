"""Per-backend data-job support: templates, worker-pool adapters, and the registry.

GPFS and WekaFS storage mappings carry placement hints (mount_path, filesystem_name,
data_network) that the planner stamps onto the data_jobs row so the DM worker can pick
nodes and build the MPI job. Every other backend falls back to agent-inventory
selection: the DM worker picks from nodes whose agent reports the mount as ready.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .repositories import DmsRepository

GPFS_BACKEND_TYPE = "gpfs"
GPFS_CSI_DRIVER = "spectrumscale.csi.ibm.com"


@dataclass(frozen=True)
class GpfsBackendTemplate:
    storage_name: str
    filesystem_name: str
    mount_path: str
    managed_root: str | None
    csi_driver: str
    storage_class_name: str | None
    data_network: str | None

    @classmethod
    def from_storage_mapping(cls, mapping: dict[str, Any]) -> "GpfsBackendTemplate":
        template = mapping["backend_template"]
        return cls(
            storage_name=mapping["storage_name"],
            # filesystem_name is required at registration (validate_filesystem_managed_root);
            # no implicit storage_name fallback.
            filesystem_name=template.get("filesystem_name", ""),
            mount_path=template.get("mount_path", ""),
            managed_root=template.get("managed_root"),
            csi_driver=template.get("csi_driver", GPFS_CSI_DRIVER),
            storage_class_name=(
                template.get("storage_class_name") or mapping.get("storage_class_name")
            ),
            data_network=template.get("data_network"),
        )


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


WEKAFS_BACKEND_TYPE = "wekafs"
WEKAFS_CSI_DRIVER = "csi.weka.io"


@dataclass(frozen=True)
class WekaFsBackendTemplate:
    storage_name: str
    cluster_name: str | None
    filesystem_name: str
    mount_path: str
    managed_root: str
    csi_driver: str | None
    storage_class_name: str | None
    data_network: str | None

    @classmethod
    def from_storage_mapping(cls, mapping: dict[str, Any]) -> "WekaFsBackendTemplate":
        template = mapping["backend_template"]
        return cls(
            storage_name=mapping["storage_name"],
            cluster_name=template.get("cluster_name") or mapping.get("cluster_name"),
            filesystem_name=template.get("filesystem_name", mapping["storage_name"]),
            mount_path=template.get("mount_path", ""),
            # managed_root is mandatory at registration; no implicit {mount_path}/dms fallback.
            managed_root=template.get("managed_root") or "",
            csi_driver=template.get("csi_driver"),
            storage_class_name=(
                template.get("storage_class_name") or mapping.get("storage_class_name")
            ),
            data_network=template.get("data_network"),
        )


@dataclass(frozen=True)
class WekaFsDataManagementAdapter:
    template: WekaFsBackendTemplate

    def worker_pool(self, storage_name: str) -> dict[str, Any]:
        return {
            "selection": "agent-inventory",
            "backend_type": WEKAFS_BACKEND_TYPE,
            "required_mounts": [storage_name],
            "mount_path": self.template.mount_path,
            "filesystem_name": self.template.filesystem_name,
            "data_network": self.template.data_network,
            "tool_candidates": ["dsync", "nsync", "drm", "dscan"],
            "requires_posix_identity": True,
            "candidates": [],
        }


@dataclass
class BackendAdapterRegistry:
    """Resolves the per-storage data-management worker pool from a storage mapping."""

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
