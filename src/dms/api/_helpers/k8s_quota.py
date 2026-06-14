"""Helpers for Kubernetes namespace quota API routes."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from ...domain import (
    KubernetesNamespaceQuotaKey,
    OperationKind,
    RequestEnvelope,
    ResourceKind,
)
from .._models import MutatingBody
from .._services import AppServices
from ..deps import submit_request


def k8s_quota_keyed_request(
    cluster_name: str,
    namespace_name: str,
    body: MutatingBody,
    request: Request,
    services: AppServices,
    operation: OperationKind,
) -> dict[str, Any]:
    key = KubernetesNamespaceQuotaKey(cluster_name, namespace_name)
    payload = {
        "cluster_name": cluster_name,
        "namespace_name": namespace_name,
        **body.payload,
    }
    return submit_request(
        services=services,
        request=request,
        envelope=RequestEnvelope(
            requester_id=body.requester_id,
            operation=operation,
            resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
            resource_key=key.as_string(),
            payload=payload,
        ),
    )
