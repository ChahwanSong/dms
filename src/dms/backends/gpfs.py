"""GPFS (IBM Storage Scale) data-management support.

Only the pieces a *data job* needs: the backend-type constant, the subset of the
storage mapping's ``backend_template`` that describes where the filesystem is
mounted and on which network its data moves, and the adapter that turns that into
a worker pool for the DM worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
