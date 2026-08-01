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


def authorize_privileged_requester_or_403(
    body: DataJobRequest,
    operation: OperationKind,
    request: Request,
    services: AppServices,
) -> None:
    """Gate privileged (root) DM execution at the authenticated API edge.

    No-op unless the feature is ON *and* the effective principal
    (``owner_username`` or ``requester_id``) is a configured privileged requester --
    so when ``DMS_DM_ALLOW_ROOT_REQUESTER`` is off, behavior is identical to before
    (the request falls through and the worker rejects ``root`` with
    ``ldap_identity_not_found``). When it applies, the request is honored only if the
    CALLER is an mTLS-verified operator (``actor`` carries ``mtls_actor_prefix``),
    optionally on ``dm_privileged_operators``, and every referenced storage/path is
    within ``dm_privileged_scopes`` (empty = all). Otherwise ``403`` and the request is
    never persisted. The worker still re-checks the flag before synthesizing uid 0.
    """
    settings = services.settings
    if not settings.dm_allow_root_requester:
        return
    principal = (body.owner_username or body.requester_id or "").strip()
    if principal not in settings.dm_privileged_requesters:
        return

    result = services.auth.verify(request)
    actor = result.actor or ""
    if not (result.authenticated and actor.startswith(settings.mtls_actor_prefix)):
        _record_privileged_gate(
            services, principal, actor, "privileged_requires_verified_operator"
        )
        raise HTTPException(
            status_code=403,
            detail="privileged (root) data jobs require an mTLS-verified operator",
        )
    if settings.dm_privileged_operators and actor not in settings.dm_privileged_operators:
        _record_privileged_gate(services, principal, actor, "operator_not_allowlisted")
        raise HTTPException(
            status_code=403,
            detail="operator is not on the privileged data-job allowlist (DMS_DM_PRIVILEGED_OPERATORS)",
        )
    if settings.dm_privileged_scopes:
        payload = normalized_data_job_payload(body, operation)
        for storage_name, path in _payload_storage_refs(payload):
            if not _within_privileged_scope(
                storage_name, path, settings.dm_privileged_scopes
            ):
                _record_privileged_gate(
                    services, principal, actor, "target_out_of_scope"
                )
                raise HTTPException(
                    status_code=403,
                    detail=(
                        f"privileged data job target {storage_name}:{path} is outside "
                        "DMS_DM_PRIVILEGED_SCOPES"
                    ),
                )
    _record_privileged_gate(services, principal, actor, "authorized")


def _payload_storage_refs(payload: dict[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for key in ("source", "destination", "target"):
        ref = payload.get(key)
        if isinstance(ref, dict) and ref.get("storage_name"):
            refs.append((str(ref["storage_name"]), str(ref.get("path") or "")))
    return refs


def _within_privileged_scope(
    storage_name: str, path: str, scopes: frozenset[str]
) -> bool:
    norm = (path or "").strip("/")
    for entry in scopes:
        if ":" in entry:
            scope_storage, prefix = entry.split(":", 1)
            prefix = prefix.strip("/")
            if scope_storage == storage_name and (
                norm == prefix or norm.startswith(prefix + "/")
            ):
                return True
        elif entry == storage_name:
            return True
    return False


def _record_privileged_gate(
    services: AppServices, principal: str, actor: str, reason: str
) -> None:
    event_type = (
        "data_job_privileged_authorized"
        if reason == "authorized"
        else "data_job_privileged_denied"
    )
    services.observability.safe_record_event(
        component="api-dm",
        severity="WARN",
        event_type=event_type,
        message=f"privileged DM gate: principal={principal} actor={actor} reason={reason}",
        payload={"principal": principal, "actor": actor, "reason": reason},
    )
