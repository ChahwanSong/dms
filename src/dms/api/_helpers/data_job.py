"""Helpers for data-management (data job) API routes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from ...domain import (
    DataJobRequest,
    OperationKind,
    RequestEnvelope,
    ResourceKind,
    data_job_option_fingerprint,
    normalized_data_job_payload,
    validate_data_job_paths,
)
from .._services import AppServices
from ..deps import submit_request


def policy_operation_or_422(operation: str) -> str:
    normalized = operation.strip().lower()
    if normalized not in {"scan", "rm", "dsync", "nsync"}:
        raise HTTPException(
            status_code=422,
            detail="operation must be one of: scan, rm, dsync, nsync",
        )
    return normalized


def data_job_request(
    body: DataJobRequest,
    operation: OperationKind,
    request: Request,
    services: AppServices,
) -> dict[str, Any]:
    payload = normalized_data_job_payload(body, operation)
    resource_key = data_job_resource_key(payload, operation)
    response = submit_request(
        services=services,
        request=request,
        envelope=RequestEnvelope(
            requester_id=body.requester_id,
            operation=operation,
            resource_kind=ResourceKind.DATA_JOB,
            resource_key=resource_key,
            payload=payload,
        ),
    )
    response.update(
        {
            "resource_key": resource_key,
            "operation": operation.value,
            "source": payload.get("source"),
            "destination": payload.get("destination"),
            "target": payload.get("target"),
            "priority": payload.get("priority_label"),
            "status_query": "/api/v1/operations/data-jobs",
        }
    )
    return response


def data_job_resource_key(payload: dict[str, Any], operation: OperationKind) -> str:
    if operation == OperationKind.DATA_SYNC:
        source = payload["source"]
        destination = payload["destination"]
        fingerprint = payload.get("option_fingerprint") or data_job_option_fingerprint(
            payload.get("options") or {}
        )
        return (
            f"data.sync:{source['storage_name']}:{source['path']}:"
            f"{destination['storage_name']}:{destination['path']}:{fingerprint}"
        )
    if operation == OperationKind.DATA_RM:
        target = payload["target"]
        fingerprint = payload.get("option_fingerprint") or data_job_option_fingerprint(
            payload.get("options") or {}
        )
        return f"data.rm:{target['storage_name']}:{target['path']}:{fingerprint}"
    target = payload["target"]
    return f"data.scan:{target['storage_name']}:{target['path']}"


def validate_data_options_or_422(
    body: DataJobRequest, operation: OperationKind, services: AppServices
) -> None:
    if operation == OperationKind.DATA_SYNC and body.options.get("delete"):
        if not services.settings.dm_sync_allow_delete:
            raise HTTPException(
                status_code=422,
                detail="sync option delete is disabled by DMS_DM_SYNC_ALLOW_DELETE",
            )


def validate_data_job_or_422(body: DataJobRequest, operation: OperationKind) -> None:
    try:
        validate_data_job_paths(body, operation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
