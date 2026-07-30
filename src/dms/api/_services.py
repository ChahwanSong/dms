"""AppServices container injected into FastAPI app state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..adapters import (
    IdentityLookupAdapter,
    KubernetesReadOnlyInventoryAdapter,
)
from ..auth import AuthVerifier, AuthorizationPolicy
from ..config import Settings
from ..query import OperationalQueryService
from ..repositories import DmsRepository, ObservabilityRepository


@dataclass
class AppServices:
    settings: Settings
    repository: DmsRepository
    observability: ObservabilityRepository
    auth: AuthVerifier
    authorization: AuthorizationPolicy
    query: OperationalQueryService
    volcano_adapter: Any
    identity_lookup: IdentityLookupAdapter | None = None
    kubernetes_inventory: KubernetesReadOnlyInventoryAdapter | None = None
