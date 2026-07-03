"""Resource-management router: filesystem, kubernetes-quota, storage-mapping, default-policy."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ...domain import (
    DefaultQuotaPolicyInput,
    FilesystemResourceKey,
    KubernetesNamespaceQuotaKey,
    LifecycleState,
    OperationKind,
    RequestEnvelope,
    ResourceKind,
    StorageMappingInput,
    TERMINAL_LIFECYCLE_STATES,
    validate_filesystem_managed_root,
    validate_kubernetes_mutation_template,
)
from .._helpers.configmap import sync_agent_storages_configmap
from .._helpers.filesystem import (
    filesystem_key_from_payload,
    filesystem_keyed_request,
)
from .._helpers.inventory import sanity_service
from .._helpers.k8s_quota import k8s_quota_keyed_request
from .._helpers.storage_mapping import (
    merge_storage_mapping_secrets,
    redact_storage_mapping,
)
from .._models import MutatingBody
from .._services import AppServices
from ..deps import (
    _reject_if_maintenance_blocked,
    authenticated_actor,
    get_services,
    submit_request,
)


def resource_management_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/resource-management", tags=["resource-management"]
    )

    @router.post("/requests", status_code=status.HTTP_202_ACCEPTED)
    def generic_request(
        envelope: RequestEnvelope,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return submit_request(services=services, request=request, envelope=envelope)

    @router.post("/filesystems", status_code=status.HTTP_202_ACCEPTED)
    def filesystem_create(
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        key = filesystem_key_from_payload(body.payload)
        return submit_request(
            services=services,
            request=request,
            envelope=RequestEnvelope(
                requester_id=body.requester_id,
                operation=OperationKind.FILESYSTEM_CREATE,
                resource_kind=ResourceKind.FILESYSTEM,
                resource_key=key.as_string(),
                payload=body.payload,
            ),
        )

    @router.patch("/filesystems/{storage_name}/{directory_name}", status_code=202)
    def filesystem_update(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        key = FilesystemResourceKey(storage_name, directory_name)
        payload = {
            "storage_name": storage_name,
            "directory_name": directory_name,
            **body.payload,
        }
        return submit_request(
            services=services,
            request=request,
            envelope=RequestEnvelope(
                requester_id=body.requester_id,
                operation=OperationKind.FILESYSTEM_UPDATE,
                resource_kind=ResourceKind.FILESYSTEM,
                resource_key=key.as_string(),
                payload=payload,
            ),
        )

    @router.post("/filesystems/{storage_name}/{directory_name}:block", status_code=202)
    def filesystem_block(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_BLOCK,
            payload_override={"block": True},
        )

    @router.post(
        "/filesystems/{storage_name}/{directory_name}:initialize", status_code=202
    )
    def filesystem_initialize(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_BLOCK,
            payload_override={"block": False},
        )

    @router.delete("/filesystems/{storage_name}/{directory_name}", status_code=202)
    def filesystem_delete(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_DELETE,
        )

    @router.post(
        "/filesystems/{storage_name}/{directory_name}:assign-quota", status_code=202
    )
    def filesystem_assign_quota(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_ASSIGN_QUOTA,
        )

    @router.post("/filesystems/{storage_name}/{directory_name}:import", status_code=202)
    def filesystem_import(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_IMPORT,
        )

    @router.post("/filesystems/{storage_name}/{directory_name}:check", status_code=202)
    def filesystem_check(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_CHECK,
        )

    @router.post("/filesystems/{storage_name}/{directory_name}:sync", status_code=202)
    def filesystem_sync(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_SYNC,
        )

    @router.post("/filesystems:expiration-sweep", status_code=202)
    def filesystem_expiration_sweep(
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return submit_request(
            services=services,
            request=request,
            envelope=RequestEnvelope(
                requester_id=body.requester_id,
                operation=OperationKind.FILESYSTEM_EXPIRATION_SWEEP,
                resource_kind=ResourceKind.FILESYSTEM,
                resource_key="filesystem-expiration-sweep",
                payload=body.payload,
            ),
        )

    @router.post("/kubernetes/namespace-quotas", status_code=202)
    def k8s_quota_create(
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        cluster_name = body.payload.get("cluster_name")
        namespace_name = body.payload.get("namespace_name")
        if not cluster_name or not namespace_name:
            raise HTTPException(
                status_code=422,
                detail="cluster_name and namespace_name are required",
            )
        key = KubernetesNamespaceQuotaKey(cluster_name, namespace_name)
        return submit_request(
            services=services,
            request=request,
            envelope=RequestEnvelope(
                requester_id=body.requester_id,
                operation=OperationKind.K8S_QUOTA_CREATE,
                resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
                resource_key=key.as_string(),
                payload=body.payload,
            ),
        )

    @router.patch(
        "/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}", status_code=202
    )
    def k8s_quota_update(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_UPDATE,
        )

    @router.post(
        "/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:block",
        status_code=202,
    )
    def k8s_quota_block(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_BLOCK,
        )

    @router.delete(
        "/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}", status_code=202
    )
    def k8s_quota_delete(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_DELETE,
        )

    @router.post(
        "/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:sync",
        status_code=202,
    )
    def k8s_quota_sync(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_SYNC,
        )

    @router.post(
        "/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:import",
        status_code=202,
    )
    def k8s_quota_import(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_IMPORT,
        )

    @router.post(
        "/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:check",
        status_code=202,
    )
    def k8s_quota_check(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_CHECK,
        )

    @router.post("/kubernetes/namespace-quotas:expiration-sweep", status_code=202)
    def k8s_quota_expiration_sweep(
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return submit_request(
            services=services,
            request=request,
            envelope=RequestEnvelope(
                requester_id=body.requester_id,
                operation=OperationKind.K8S_QUOTA_EXPIRATION_SWEEP,
                resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
                resource_key="kubernetes-namespace-quota-expiration-sweep",
                payload=body.payload,
            ),
        )

    @router.post("/kubernetes/namespace-quotas:audit", status_code=202)
    def k8s_quota_audit(
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        scope = body.payload.get("scope") or {}
        if scope.get("cluster_name") and scope.get("namespace_name"):
            resource_key = KubernetesNamespaceQuotaKey(
                scope["cluster_name"], scope["namespace_name"]
            ).as_string()
        else:
            resource_key = "kubernetes-namespace-quota-audit"
        return submit_request(
            services=services,
            request=request,
            envelope=RequestEnvelope(
                requester_id=body.requester_id,
                operation=OperationKind.K8S_QUOTA_AUDIT,
                resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA,
                resource_key=resource_key,
                payload=body.payload,
            ),
        )

    @router.post("/storage-mappings")
    def upsert_storage_mapping(
        data: StorageMappingInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        conflict = services.repository.active_work_for_storage(data.storage_name)
        if conflict:
            services.repository.record_storage_mapping_conflict(
                storage_name=data.storage_name, actor=actor, conflict=conflict
            )
            raise HTTPException(status_code=409, detail=conflict)
        existing = services.repository.get_storage_mapping(data.storage_name)
        merge_storage_mapping_secrets(data.backend_template, existing)
        try:
            validate_filesystem_managed_root(data.backend_template)
            validate_kubernetes_mutation_template(data.backend_template)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        sanity = sanity_service(services).check_input(data)
        data.sanity_status = sanity["status"]
        services.repository.upsert_storage_mapping(
            data,
            actor=actor,
            sanity_result=sanity,
            readiness=sanity["readiness"],
        )
        services.observability.safe_record_event(
            component="storage-mapping",
            severity="INFO" if sanity["status"] != "Failed" else "WARN",
            event_type="storage_mapping_sanity_check_completed",
            message="storage mapping sanity check completed",
            payload={
                "storage_name": data.storage_name,
                "status": sanity["status"],
                "errors": sanity["errors"],
                "warnings": sanity["warnings"],
            },
        )
        mapping = services.repository.get_storage_mapping(data.storage_name)
        sync_agent_storages_configmap(services.settings, data.storage_name, mapping)
        return {
            "storage_name": data.storage_name,
            "status": sanity["status"],
            "mapping": redact_storage_mapping(mapping),
        }

    @router.post("/storage-mappings/{storage_name}:check")
    def check_storage_mapping(
        storage_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        mapping = services.repository.get_storage_mapping(storage_name)
        if not mapping:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        sanity = sanity_service(services).check_mapping(mapping)
        updated = services.repository.update_storage_mapping_sanity(
            storage_name,
            sanity_result=sanity,
            readiness=sanity["readiness"],
            actor=actor,
        )
        services.observability.safe_record_event(
            component="storage-mapping",
            severity="INFO" if sanity["status"] != "Failed" else "WARN",
            event_type="storage_mapping_sanity_check_completed",
            message="storage mapping sanity check completed",
            payload={
                "storage_name": storage_name,
                "status": sanity["status"],
                "errors": sanity["errors"],
                "warnings": sanity["warnings"],
            },
        )
        return {
            "storage_name": storage_name,
            "status": sanity["status"],
            "mapping": redact_storage_mapping(updated),
        }

    @router.patch("/storage-mappings/{storage_name}")
    def patch_storage_mapping(
        storage_name: str,
        data: StorageMappingInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        existing = services.repository.get_storage_mapping(storage_name)
        if not existing:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        if data.storage_name != storage_name:
            raise HTTPException(
                status_code=400, detail="storage_name in body must match path"
            )
        conflict = services.repository.active_work_for_storage(storage_name)
        if conflict:
            services.repository.record_storage_mapping_conflict(
                storage_name=storage_name, actor=actor, conflict=conflict
            )
            raise HTTPException(status_code=409, detail=conflict)
        merge_storage_mapping_secrets(data.backend_template, existing)
        try:
            validate_filesystem_managed_root(data.backend_template)
            validate_kubernetes_mutation_template(data.backend_template)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        sanity = sanity_service(services).check_input(data)
        data.sanity_status = sanity["status"]
        services.repository.upsert_storage_mapping(
            data,
            actor=actor,
            sanity_result=sanity,
            readiness=sanity["readiness"],
        )
        services.observability.safe_record_event(
            component="storage-mapping",
            severity="INFO" if sanity["status"] != "Failed" else "WARN",
            event_type="storage_mapping_sanity_check_completed",
            message="storage mapping updated and sanity check completed",
            payload={
                "storage_name": storage_name,
                "status": sanity["status"],
                "errors": sanity["errors"],
                "warnings": sanity["warnings"],
            },
        )
        mapping = services.repository.get_storage_mapping(storage_name)
        sync_agent_storages_configmap(services.settings, storage_name, mapping)
        return {
            "storage_name": storage_name,
            "status": sanity["status"],
            "mapping": redact_storage_mapping(mapping),
        }

    @router.post("/requests/{request_id}:resolve", status_code=200)
    def resolve_request(
        request_id: str,
        body: dict[str, Any],
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        """Manually terminalize a request STUCK in a non-terminal state so its resource
        stops blocking new requests (the planner Conflicts a new request while a prior
        one for the same resource is still active).

        Resolvable states: UnknownAfterSideEffect, BackendApplyFailed, StaleClaim,
        RecoveryNeeded, VerificationFailed — the full set of stuck states that surface in
        조치 필요 (ACTION_REQUIRED_REQUEST_STATUSES). Actively-leased (Claimed/Running/
        Applying/Verifying) and already-terminal requests are rejected (409).

        body fields:
          - resolution: "abandon" (→ Cancelled for StaleClaim [no side effect], else
            Failed) or "succeeded" (→ Succeeded, when the operator confirms it applied)
          - reason: human-readable explanation (required)

        Abandon of RecoveryNeeded/UnknownAfterSideEffect may leave a PARTIALLY-applied
        backend side effect (the worker died mid-apply); the caller should verify the
        backend first. The audit result records backend_state_verified=false for these.
        The request's still-open plan + run are terminalized in the same transaction.
        """
        RESOLVABLE_STATES = {
            LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
            LifecycleState.BACKEND_APPLY_FAILED.value,
            LifecycleState.STALE_CLAIM.value,
            LifecycleState.RECOVERY_NEEDED.value,
            LifecycleState.VERIFICATION_FAILED.value,
        }

        actor = authenticated_actor(request, services)
        resolution = body.get("resolution")
        reason = body.get("reason", "").strip()
        if resolution not in ("abandon", "succeeded"):
            raise HTTPException(
                status_code=422,
                detail="resolution must be 'abandon' or 'succeeded'",
            )
        if not reason:
            raise HTTPException(status_code=422, detail="reason is required")

        try:
            req = services.repository.get_request(request_id)
        except KeyError:
            raise HTTPException(status_code=404, detail="request not found") from None

        current_status = req.get("status")
        if (
            current_status in TERMINAL_LIFECYCLE_STATES
            and current_status not in RESOLVABLE_STATES
        ):
            raise HTTPException(
                status_code=409,
                detail=f"request is already in terminal state '{current_status}' and cannot be resolved",
            )
        if current_status not in RESOLVABLE_STATES:
            raise HTTPException(
                status_code=409,
                detail=f"request is in state '{current_status}'; only {sorted(RESOLVABLE_STATES)} can be resolved",
            )

        # abandon: StaleClaim never ran a side effect -> Cancelled; other stuck states
        # may have a (possibly partial) side effect -> Failed. succeeded -> Succeeded.
        if resolution == "succeeded":
            target_state = LifecycleState.SUCCEEDED
        elif current_status == LifecycleState.STALE_CLAIM.value:
            target_state = LifecycleState.CANCELLED
        else:
            target_state = LifecycleState.FAILED
        # RecoveryNeeded/UnknownAfterSideEffect abandon: a backend side effect may be
        # partially applied and was not verified — record that in the audit result.
        backend_unverified = resolution == "abandon" and current_status in {
            LifecycleState.RECOVERY_NEEDED.value,
            LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
            LifecycleState.VERIFICATION_FAILED.value,
        }
        services.repository.resolve_stuck_request(
            request_id,
            terminal_status=target_state,
            reason=f"manually resolved by {actor}: {reason}",
            actor=actor,
            backend_unverified=backend_unverified,
        )
        return {
            "request_id": request_id,
            "previous_status": current_status,
            "resolved_to": target_state.value,
            "resolution": resolution,
            "backend_state_verified": not backend_unverified,
            "actor": actor,
            "reason": reason,
        }

    @router.delete("/storage-mappings/{storage_name}", status_code=200)
    def delete_storage_mapping(
        storage_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        existing = services.repository.get_storage_mapping(storage_name)
        if not existing:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        conflict = services.repository.active_work_for_storage(storage_name)
        if conflict:
            raise HTTPException(
                status_code=409,
                detail=f"storage mapping has active work: {conflict}",
            )
        deleted = services.repository.delete_storage_mapping(storage_name, actor)
        sync_agent_storages_configmap(services.settings, storage_name, None)
        services.observability.safe_record_event(
            component="storage-mapping",
            severity="INFO",
            event_type="storage_mapping_deleted",
            message="storage mapping deleted",
            payload={"storage_name": storage_name},
        )
        return {"storage_name": storage_name, "deleted": True, "mapping": deleted}

    @router.post("/default-quota-policies")
    def upsert_default_quota_policy(
        data: DefaultQuotaPolicyInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        _reject_if_maintenance_blocked(services)
        policy_id = services.repository.upsert_default_quota_policy(
            resource_kind=data.resource_kind.value,
            resource_type=data.resource_type,
            quota=data.quota,
            actor=actor,
        )
        return {"policy_id": policy_id, "status": "stored"}

    return router
