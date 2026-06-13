"""Helpers for filesystem-resource API routes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from ...domain import (
    FilesystemResourceKey,
    OperationKind,
    RequestEnvelope,
    ResourceKind,
)
from .._models import MutatingBody
from .._services import AppServices
from ..deps import submit_request


def filesystem_keyed_request(
    storage_name: str,
    directory_name: str,
    body: MutatingBody,
    request: Request,
    services: AppServices,
    operation: OperationKind,
    payload_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    key = filesystem_key(storage_name, directory_name)
    payload = {
        "storage_name": storage_name,
        "directory_name": directory_name,
        **body.payload,
        **(payload_override or {}),
    }

    return submit_request(
        services=services,
        request=request,
        envelope=RequestEnvelope(
            requester_id=body.requester_id,
            operation=operation,
            resource_kind=ResourceKind.FILESYSTEM,
            resource_key=key.as_string(),
            payload=payload,
        ),
    )


def filesystem_key_from_payload(payload: dict[str, Any]) -> FilesystemResourceKey:
    missing = [
        field for field in ("storage_name", "directory_name") if not payload.get(field)
    ]

    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"missing": missing},
        )

    return filesystem_key(
        str(payload["storage_name"]),
        str(payload["directory_name"]),
    )


def filesystem_key(storage_name: str, directory_name: str) -> FilesystemResourceKey:
    try:
        return FilesystemResourceKey(storage_name, directory_name)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
