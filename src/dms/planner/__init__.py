"""Request planner: validation, rejection and desired-state generation.

Historically a single planner.py module; the Planner class is now composed
from core/filesystem/kubernetes mixins. The public `dms.planner` surface
(notably `Planner`) is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

from ._base import *  # noqa: F401,F403
from ._core import _PlannerCoreMixin
from ._filesystem import FilesystemPlannerMixin
from ._kubernetes import KubernetesPlannerMixin


@dataclass
class Planner(
    _PlannerCoreMixin,
    FilesystemPlannerMixin,
    KubernetesPlannerMixin,
):
    """Plans persisted requests into executable desired state."""

    repository: DmsRepository
    backend_registry: BackendAdapterRegistry | None = None
