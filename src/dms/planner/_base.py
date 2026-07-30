from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..backend_registry import BackendAdapterRegistry
from ..config import Settings
from ..domain import (
    LifecycleState,
    OperationKind,
    ResourceKind,
    WorkerRole,
    apply_managed_root_suffix,
    managed_root_for_mapping,
    managed_root_path_suffix,
)
from ..repositories import DmsRepository

DM_OPERATIONS = {
    OperationKind.DATA_SCAN.value,
    OperationKind.DATA_SYNC.value,
    OperationKind.DATA_RM.value,
}
