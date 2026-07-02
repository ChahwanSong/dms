"""Operator/admin interface API (role: operator).

Proxies the DMS storage-mapping endpoints (storage inventory management) through
the BFF. The browser never talks to DMS directly; the BFF attaches DMS auth and
the operator's identity as the ``x-dms-actor`` for DMS-side audit. DMS errors are
forwarded with their original status code so the SPA can render them.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..deps import get_dms_client
from ..dms_client import DmsApiError, DmsClient
from ..security import ROLE_OPERATOR, require_role


class StorageMappingPayload(BaseModel):
    """Write model mirroring DMS StorageMappingInput (the writable subset).

    ``backend_template`` is free-form per backend_type (DMS validates it); the
    server overwrites sanity_status, so it is intentionally omitted here.
    """

    storage_name: str
    backend_template: dict[str, Any] = Field(default_factory=dict)
    cluster_name: str | None = None
    storage_class_name: str | None = None
    version: int = 1


def _actor(user: dict[str, Any]) -> str:
    return str(user.get("username") or ROLE_OPERATOR)


async def _forward(coro: Any) -> Any:
    """Await a DmsClient call, re-raising DmsApiError as HTTPException."""
    try:
        return await coro
    except DmsApiError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def operator_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/operator",
        tags=["operator"],
        dependencies=[Depends(require_role(ROLE_OPERATOR))],
    )

    # --- storage inventory (storage_mapping) ------------------------------

    @router.get("/storage-mappings")
    async def list_storage_mappings(
        cluster_name: str | None = Query(default=None),
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> list[dict[str, Any]]:
        # Single-fetch the full bounded set (limit=10000) so the inventory never
        # silently truncates at the DMS default page size. Storage mappings number
        # in the tens–hundreds, so this stays one cheap call.
        return await _forward(
            dms.list_storage_mappings(
                actor=_actor(user), cluster_name=cluster_name, limit=10000
            )
        )

    @router.get("/storage-mappings/{storage_name}")
    async def get_storage_mapping(
        storage_name: str,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        return await _forward(
            dms.get_storage_mapping(storage_name, actor=_actor(user))
        )

    @router.post("/storage-mappings")
    async def create_storage_mapping(
        payload: StorageMappingPayload,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        return await _forward(
            dms.upsert_storage_mapping(payload.model_dump(), actor=_actor(user))
        )

    @router.patch("/storage-mappings/{storage_name}")
    async def update_storage_mapping(
        storage_name: str,
        payload: StorageMappingPayload,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        if payload.storage_name != storage_name:
            raise HTTPException(
                status_code=400, detail="storage_name in body must match the path"
            )
        return await _forward(
            dms.patch_storage_mapping(
                storage_name, payload.model_dump(), actor=_actor(user)
            )
        )

    @router.post("/storage-mappings/{storage_name}/check")
    async def check_storage_mapping(
        storage_name: str,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        return await _forward(
            dms.check_storage_mapping(storage_name, actor=_actor(user))
        )

    @router.delete("/storage-mappings/{storage_name}")
    async def delete_storage_mapping(
        storage_name: str,
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        # DMS's DELETE returns the deleted mapping UN-redacted (cleartext weka
        # password). Do NOT forward that body to the browser — call for the side
        # effect / error propagation only, then return a minimal confirmation.
        await _forward(dms.delete_storage_mapping(storage_name, actor=_actor(user)))
        return {"storage_name": storage_name, "deleted": True}

    # --- agent DaemonSet rollout ------------------------------------------
    # After adding/changing a storage the agents must restart to re-read
    # storages.json (they read it once at startup). DMS owns the agents and
    # drives the rollout via its in-cluster k8s access; the portal just proxies.

    @router.post("/agents/rollout-restart")
    async def rollout_restart_agents(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        return await _forward(dms.rollout_restart_agents(actor=_actor(user)))

    @router.get("/agents/rollout-status")
    async def agent_rollout_status(
        dms: DmsClient = Depends(get_dms_client),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        return await _forward(dms.agent_rollout_status(actor=_actor(user)))

    return router
