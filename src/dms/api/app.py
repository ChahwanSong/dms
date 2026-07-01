"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
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
from ..db import Database, close_all_pools
from ..migrations import migrate_all
from ..query import OperationalQueryService
from ..repositories import DmsRepository, ObservabilityRepository
from ._services import AppServices
from .deps import identity_lookup_from_settings
from .routers.agent import agent_router
from .routers.data_management import data_management_router
from .routers.identity import identity_denylist_router
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
        operational_db = Database(
            settings.database_url,
            pool_config=settings.operational_pool_config(role="api"),
        )
        observability_db = Database(
            settings.observability_database_url,
            pool_config=settings.observability_pool_config(),
        )
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
        query=OperationalQueryService(
            repository,
            observability,
            agent_report_stale_seconds=settings.agent_report_stale_seconds,
            data_job_attention_window_seconds=settings.data_job_attention_window_seconds,
        ),
        volcano_adapter=volcano_adapter_from_settings(settings),
        identity_lookup=identity_lookup or identity_lookup_from_settings(settings),
        kubernetes_inventory=kubernetes_inventory
        or KubectlReadOnlyInventoryAdapter.from_settings(settings),
        kubernetes_quota=kubernetes_quota
        or KubernetesNamespaceQuotaLiveAdapter.from_settings(settings),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Cap the sync-handler threadpool to the operational pool size so the API
        # can never oversubscribe its DB pool: excess concurrent requests queue for
        # a worker thread (bounded latency) instead of piling onto pool checkout and
        # failing with PoolTimeout. (Each request holds at most one operational
        # connection at a time, so threads == op pool size is the right alignment.)
        import anyio

        try:
            anyio.to_thread.current_default_thread_limiter().total_tokens = max(
                settings.db_api_pool_max_size, 1
            )
        except Exception:  # noqa: BLE001 - best effort; never block startup
            pass
        yield
        # Best-effort: return all pooled PG backends on graceful shutdown so a
        # rolling restart does not transiently double the connection count.
        close_all_pools()

    app = FastAPI(title="DMS", version="0.1.0", lifespan=lifespan)
    app.state.services = services
    app.include_router(resource_management_router())
    app.include_router(data_management_router())
    app.include_router(identity_denylist_router())
    app.include_router(agent_router())
    app.include_router(operational_query_router())

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "observability_separate": settings.observability_is_separate,
        }

    return app
