"""FastAPI application factory."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from ..adapters import (
    IdentityLookupAdapter,
    KubernetesNamespaceQuotaAdapter,
    KubernetesNamespaceQuotaLiveAdapter,
    KubernetesReadOnlyInventoryAdapter,
    KubectlReadOnlyInventoryAdapter,
    volcano_adapter_from_settings,
)
from ..auth import AuthVerifier, AuthorizationPolicy
from ..config import Settings
from ..db import Database
from ..migrations import migrate_all
from ..query import OperationalQueryService
from ..repositories import DmsRepository, ObservabilityRepository
from ._services import AppServices
from .deps import identity_lookup_from_settings
from .routers.agent import agent_router
from .routers.data_management import data_management_router
from .routers.identity import identity_router
from .routers.operations import operational_query_router
from .routers.resource_management import resource_management_router


def create_app(
    settings: Settings | None = None,
    repository: DmsRepository | None = None,
    observability: ObservabilityRepository | None = None,
    identity_lookup: IdentityLookupAdapter | None = None,
    kubernetes_inventory: KubernetesReadOnlyInventoryAdapter | None = None,
    kubernetes_quota: KubernetesNamespaceQuotaAdapter | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    if repository is None or observability is None:
        operational_db = Database(settings.database_url)
        observability_db = Database(settings.observability_database_url)
        migrate_all(operational_db, observability_db)
        repository = repository or DmsRepository(operational_db)
        observability = observability or ObservabilityRepository(observability_db)
    repository.bootstrap_data_management_policies(
        settings.data_management_policy_defaults()
    )
    services = AppServices(
        settings=settings,
        repository=repository,
        observability=observability,
        auth=AuthVerifier(settings),
        authorization=AuthorizationPolicy(),
        query=OperationalQueryService(repository, observability),
        volcano_adapter=volcano_adapter_from_settings(settings),
        identity_lookup=identity_lookup or identity_lookup_from_settings(settings),
        kubernetes_inventory=kubernetes_inventory
        or KubectlReadOnlyInventoryAdapter.from_settings(settings),
        kubernetes_quota=kubernetes_quota
        or KubernetesNamespaceQuotaLiveAdapter.from_settings(settings),
    )

    app = FastAPI(title="DMS", version="0.1.0")
    app.state.services = services
    app.include_router(resource_management_router())
    app.include_router(data_management_router())
    app.include_router(identity_router())
    app.include_router(agent_router())
    app.include_router(operational_query_router())

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "observability_separate": settings.observability_is_separate,
        }

    return app
