"""Request planner: validation, rejection and desired-state generation.

Turns plannable data-job requests into plans + data_jobs rows, rejecting any
whose storage mapping is missing/disabled/unsafe or whose DM readiness is not
fresh.
"""
from __future__ import annotations

from dataclasses import dataclass

from ._base import *  # noqa: F401,F403
from ._core import _PlannerCoreMixin


@dataclass
class Planner(_PlannerCoreMixin):
    """Plans persisted requests into executable desired state."""

    repository: DmsRepository
    backend_registry: BackendAdapterRegistry | None = None
    # DM-only readiness staleness gate. None disables it (default), preserving existing
    # behaviour. When set, DM requests are fail-closed if a storage mapping's sanity is
    # older than this many seconds (the sanity reconciler keeps it fresh).
    sanity_ttl_seconds: float | None = None
    # Runtime settings (DM path base, etc.). None (default) disables managed_root
    # rebasing, preserving existing behaviour and `Planner(repository)` test fixtures.
    settings: Settings | None = None
