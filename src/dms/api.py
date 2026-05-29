from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel

from .agent import AgentReportIngestionService
from .adapters import (
    IdentityLookupAdapter,
    IdentityLookupConfigurationError,
    IdentityLookupReadError,
    IdentityLookupResult,
    KubernetesNamespaceQuotaAdapter,
    KubernetesNamespaceQuotaLiveAdapter,
    KubernetesReadOnlyInventoryAdapter,
    KubectlReadOnlyInventoryAdapter,
    LdapIdentityLookupAdapter,
    StubVolcanoAdapter,
)
from .auth import AuthVerifier, AuthorizationPolicy
from .config import Settings
from .db import Database
from .domain import (
    AgentReport,
    DataJobRequest,
    DefaultQuotaPolicyInput,
    FilesystemResourceKey,
    IdentityMappingInput,
    IdentityMappingStatus,
    KubernetesNamespaceQuotaKey,
    OperationKind,
    RequestEnvelope,
    ResourceKind,
    StorageMappingInput,
    validate_data_job_paths,
)
from .inventory import EffectiveInventoryService, StorageMappingSanityService
from .migrations import migrate_all
from .query import OperationalQueryService
from .repositories import DmsRepository, ObservabilityRepository
from .workers import cancel_data_job, confirm_data_job


class MutatingBody(BaseModel):
    requester_id: str
    payload: dict[str, Any] = {}


class DisableIdentityMappingBody(BaseModel):
    reason: str | None = None


@dataclass
class AppServices:
    settings: Settings
    repository: DmsRepository
    observability: ObservabilityRepository
    auth: AuthVerifier
    authorization: AuthorizationPolicy
    query: OperationalQueryService
    volcano_adapter: StubVolcanoAdapter
    identity_lookup: IdentityLookupAdapter | None = None
    kubernetes_inventory: KubernetesReadOnlyInventoryAdapter | None = None
    kubernetes_quota: KubernetesNamespaceQuotaAdapter | None = None


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
    services = AppServices(
        settings=settings,
        repository=repository,
        observability=observability,
        auth=AuthVerifier(settings),
        authorization=AuthorizationPolicy(),
        query=OperationalQueryService(repository, observability),
        volcano_adapter=StubVolcanoAdapter(),
        identity_lookup=identity_lookup or _identity_lookup_from_settings(settings),
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


def get_services(request: Request) -> AppServices:
    return request.app.state.services


def _identity_lookup_from_settings(settings: Settings) -> IdentityLookupAdapter | None:
    if not settings.ldap_uri and not settings.ldap_base_dn:
        return None
    return LdapIdentityLookupAdapter.from_settings(settings)


def authenticated_actor(request: Request, services: AppServices) -> str:
    result = services.auth.verify(request)
    if not result.authenticated or not result.actor:
        services.observability.record_event(
            component="api-auth",
            severity="WARN",
            event_type="authentication_rejected",
            message=result.reason or "authentication rejected",
            payload={"path": str(request.url.path)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=result.reason or "authentication rejected",
        )
    return result.actor


def submit_request(
    *,
    services: AppServices,
    request: Request,
    envelope: RequestEnvelope,
) -> dict[str, Any]:
    actor = authenticated_actor(request, services)
    request_id = services.repository.create_request(
        requester_id=envelope.requester_id,
        actor=actor,
        operation=envelope.operation.value,
        resource_kind=envelope.resource_kind.value,
        resource_key=envelope.resource_key,
        payload=envelope.payload,
    )
    allowed, reason = services.authorization.authorize(
        actor=actor,
        requester_id=envelope.requester_id,
        operation=envelope.operation.value,
        resource_kind=envelope.resource_kind.value,
        resource_key=envelope.resource_key,
        payload=envelope.payload,
    )
    if not allowed:
        services.repository.record_authorization_failed(request_id, reason)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"request_id": request_id, "reason": reason},
        )
    return {"request_id": request_id, "status": "Persisted"}


def resource_management_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/resource-management", tags=["resource-management"])

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
        key = FilesystemResourceKey(
            body.payload["storage_name"], body.payload["directory_name"]
        )
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
        payload = {"storage_name": storage_name, "directory_name": directory_name, **body.payload}
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
        return _filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_BLOCK,
        )

    @router.post("/filesystems/{storage_name}/{directory_name}:initialize", status_code=202)
    def filesystem_initialize(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return _filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_INITIALIZE,
        )

    @router.delete("/filesystems/{storage_name}/{directory_name}", status_code=202)
    def filesystem_delete(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return _filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_DELETE,
        )

    @router.post("/filesystems/{storage_name}/{directory_name}:assign-quota", status_code=202)
    def filesystem_assign_quota(
        storage_name: str,
        directory_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return _filesystem_keyed_request(
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
        return _filesystem_keyed_request(
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
        return _filesystem_keyed_request(
            storage_name,
            directory_name,
            body,
            request,
            services,
            OperationKind.FILESYSTEM_CHECK,
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
        key = KubernetesNamespaceQuotaKey(
            body.payload["cluster_name"], body.payload["namespace_name"]
        )
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

    @router.patch("/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}", status_code=202)
    def k8s_quota_update(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return _k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_UPDATE,
        )

    @router.post("/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:block", status_code=202)
    def k8s_quota_block(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return _k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_BLOCK,
        )

    @router.delete("/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}", status_code=202)
    def k8s_quota_delete(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return _k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_DELETE,
        )

    @router.post("/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:sync", status_code=202)
    def k8s_quota_sync(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return _k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_SYNC,
        )

    @router.post("/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}:check", status_code=202)
    def k8s_quota_check(
        cluster_name: str,
        namespace_name: str,
        body: MutatingBody,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        return _k8s_quota_keyed_request(
            cluster_name,
            namespace_name,
            body,
            request,
            services,
            OperationKind.K8S_QUOTA_CHECK,
        )

    @router.post("/storage-mappings")
    def upsert_storage_mapping(
        data: StorageMappingInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        conflict = services.repository.active_work_for_storage(data.storage_name)
        if conflict:
            services.repository.record_storage_mapping_conflict(
                storage_name=data.storage_name, actor=actor, conflict=conflict
            )
            raise HTTPException(status_code=409, detail=conflict)
        sanity = _sanity_service(services).check_input(data)
        data.sanity_status = sanity["status"]
        services.repository.upsert_storage_mapping(
            data,
            actor=actor,
            sanity_result=sanity,
            readiness=sanity["readiness"],
        )
        services.observability.record_event(
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
        return {"storage_name": data.storage_name, "status": sanity["status"], "mapping": mapping}

    @router.post("/storage-mappings/{storage_name}:check")
    def check_storage_mapping(
        storage_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        mapping = services.repository.get_storage_mapping(storage_name)
        if not mapping:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        sanity = _sanity_service(services).check_mapping(mapping)
        updated = services.repository.update_storage_mapping_sanity(
            storage_name,
            sanity_result=sanity,
            readiness=sanity["readiness"],
            actor=actor,
        )
        services.observability.record_event(
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
        return {"storage_name": storage_name, "status": sanity["status"], "mapping": updated}

    @router.post("/default-quota-policies")
    def upsert_default_quota_policy(
        data: DefaultQuotaPolicyInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        policy_id = services.repository.upsert_default_quota_policy(
            resource_kind=data.resource_kind.value,
            resource_type=data.resource_type,
            quota=data.quota,
            actor=actor,
        )
        return {"policy_id": policy_id, "status": "stored"}

    return router


def _filesystem_keyed_request(
    storage_name: str,
    directory_name: str,
    body: MutatingBody,
    request: Request,
    services: AppServices,
    operation: OperationKind,
) -> dict[str, Any]:
    key = FilesystemResourceKey(storage_name, directory_name)
    payload = {"storage_name": storage_name, "directory_name": directory_name, **body.payload}
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


def _k8s_quota_keyed_request(
    cluster_name: str,
    namespace_name: str,
    body: MutatingBody,
    request: Request,
    services: AppServices,
    operation: OperationKind,
) -> dict[str, Any]:
    key = KubernetesNamespaceQuotaKey(cluster_name, namespace_name)
    payload = {"cluster_name": cluster_name, "namespace_name": namespace_name, **body.payload}
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


def _inventory_service(services: AppServices) -> EffectiveInventoryService:
    return EffectiveInventoryService(
        repository=services.repository,
        kubernetes_inventory=services.kubernetes_inventory,
        settings=services.settings,
    )


def _sanity_service(services: AppServices) -> StorageMappingSanityService:
    return StorageMappingSanityService(
        repository=services.repository,
        inventory_service=_inventory_service(services),
        settings=services.settings,
    )


def data_management_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/data-management", tags=["data-management"])

    @router.post("/sync", status_code=202)
    def data_sync(
        body: DataJobRequest,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        _validate_data_job_or_422(body, OperationKind.DATA_SYNC)
        return _data_job_request(body, OperationKind.DATA_SYNC, request, services)

    @router.post("/rm", status_code=202)
    def data_rm(
        body: DataJobRequest,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        _validate_data_job_or_422(body, OperationKind.DATA_RM)
        return _data_job_request(body, OperationKind.DATA_RM, request, services)

    @router.post("/scan", status_code=202)
    def data_scan(
        body: DataJobRequest,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        _validate_data_job_or_422(body, OperationKind.DATA_SCAN)
        return _data_job_request(body, OperationKind.DATA_SCAN, request, services)

    @router.post("/jobs/{job_id}:confirm")
    def confirm_job(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        try:
            confirm_data_job(services.repository, job_id, actor=actor)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"job_id": job_id, "status": "Confirmed"}

    @router.post("/jobs/{job_id}:cancel")
    def cancel_job(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        cancel_data_job(services.repository, services.volcano_adapter, job_id, actor=actor)
        return {"job_id": job_id, "status": "Cancelled"}

    @router.get("/help")
    def data_help() -> dict[str, Any]:
        return {
            "operations": {
                "sync": {
                    "requires_preview_confirm": True,
                    "tool_selection": ["dsync", "nsync"],
                    "path_model": "registered storage_name plus storage-relative paths",
                },
                "rm": {
                    "requires_preview_confirm": True,
                    "tool": "drm",
                    "path_model": "registered storage_name plus storage-relative target_path",
                },
                "scan": {
                    "requires_preview_confirm": False,
                    "tool": "dscan",
                    "path_model": "registered storage_name plus storage-relative target_path",
                },
            },
            "raw_command_line_options": "rejected",
        }

    return router


def _data_job_request(
    body: DataJobRequest,
    operation: OperationKind,
    request: Request,
    services: AppServices,
) -> dict[str, Any]:
    key_parts = [body.storage_name, operation.value]
    key_parts.extend([body.source_path or "", body.destination_path or "", body.target_path or ""])
    return submit_request(
        services=services,
        request=request,
        envelope=RequestEnvelope(
            requester_id=body.requester_id,
            operation=operation,
            resource_kind=ResourceKind.DATA_JOB,
            resource_key=":".join(key_parts),
            payload=body.model_dump(),
        ),
    )


def _validate_data_job_or_422(body: DataJobRequest, operation: OperationKind) -> None:
    try:
        validate_data_job_paths(body, operation)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def identity_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/identity-mappings", tags=["identity-mapping"])

    @router.put("/{identity_provider}/{requester_id}")
    def upsert_identity_mapping(
        identity_provider: str,
        requester_id: str,
        body: IdentityMappingInput,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        if body.identity_provider != identity_provider or body.requester_id != requester_id:
            raise HTTPException(status_code=400, detail="path and body identity mismatch")
        lookup = _require_identity_lookup(services)
        result = _lookup_identity_or_http(
            lookup=lookup,
            provider=identity_provider,
            posix_username=body.posix_username,
        )
        mismatches = _expected_identity_mismatches(body, result)
        mapping_status = (
            IdentityMappingStatus.NEEDS_REVIEW
            if mismatches
            else IdentityMappingStatus.ACTIVE
        )
        mapping_id = services.repository.upsert_identity_mapping(
            body,
            uid=result.uid,
            gid=result.primary_gid,
            groups=result.groups,
            status=mapping_status,
            verification_result="mismatch" if mismatches else "matched",
            mismatch_reason="; ".join(mismatches) if mismatches else None,
            source_metadata=result.source_metadata,
        )
        mapping = services.repository.get_identity_mapping(requester_id, identity_provider)
        return {
            "mapping_id": mapping_id,
            "status": mapping_status.value,
            "mapping": mapping,
        }

    @router.post("/{identity_provider}/{requester_id}:refresh")
    def refresh_identity_mapping(
        identity_provider: str,
        requester_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        lookup = _require_identity_lookup(services)
        mapping = services.repository.get_identity_mapping(requester_id, identity_provider)
        if not mapping:
            raise HTTPException(status_code=404, detail="identity mapping not found")
        if mapping["status"] == IdentityMappingStatus.DISABLED.value:
            return {
                "requester_id": requester_id,
                "identity_provider": identity_provider,
                "status": IdentityMappingStatus.DISABLED.value,
                "mapping": mapping,
            }
        result = _perform_identity_lookup(
            lookup=lookup,
            provider=identity_provider,
            posix_username=mapping["posix_username"],
        )
        if result is None:
            mapping = services.repository.mark_identity_mapping_stale(
                requester_id=requester_id,
                provider=identity_provider,
                mismatch_reason=f"LDAP user not found: {mapping['posix_username']}",
                verification_result="not_found",
                source_metadata={"adapter": "ldap3-direct", "read_only": True},
            )
            return {
                "requester_id": requester_id,
                "identity_provider": identity_provider,
                "status": mapping["status"],
                "mapping": mapping,
            }
        drift = _mapping_drift(mapping, result)
        if drift:
            mapping = services.repository.mark_identity_mapping_stale(
                requester_id=requester_id,
                provider=identity_provider,
                mismatch_reason="; ".join(drift),
                verification_result="drift",
                source_metadata=result.source_metadata,
            )
        else:
            mapping = services.repository.verify_identity_mapping(
                requester_id=requester_id,
                provider=identity_provider,
                source_metadata=result.source_metadata,
            )
        return {
            "requester_id": requester_id,
            "identity_provider": identity_provider,
            "status": mapping["status"],
            "mapping": mapping,
        }

    @router.post("/{identity_provider}/{requester_id}:disable")
    def disable_identity_mapping(
        identity_provider: str,
        requester_id: str,
        request: Request,
        body: DisableIdentityMappingBody | None = None,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        services.repository.disable_identity_mapping(
            requester_id,
            identity_provider,
            reason=body.reason if body else None,
        )
        return {
            "requester_id": requester_id,
            "identity_provider": identity_provider,
            "status": "Disabled",
        }

    @router.get("")
    def list_identity_mappings(
        request: Request,
        requester_id: str | None = None,
        identity_provider: str | None = None,
        status: str | None = None,
        failed: bool = False,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_identity_mappings(
            requester_id=requester_id,
            identity_provider=identity_provider,
            status=status,
            failed=failed,
        )

    return router


def _require_identity_lookup(services: AppServices) -> IdentityLookupAdapter:
    if services.identity_lookup is None:
        raise HTTPException(
            status_code=503,
            detail="direct LDAP identity lookup is not configured",
        )
    return services.identity_lookup


def _perform_identity_lookup(
    *,
    lookup: IdentityLookupAdapter,
    provider: str,
    posix_username: str,
) -> IdentityLookupResult | None:
    try:
        return lookup.lookup(provider, posix_username)
    except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _lookup_identity_or_http(
    *,
    lookup: IdentityLookupAdapter,
    provider: str,
    posix_username: str,
) -> IdentityLookupResult:
    result = _perform_identity_lookup(
        lookup=lookup,
        provider=provider,
        posix_username=posix_username,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="LDAP user not found")
    return result


def _expected_identity_mismatches(
    body: IdentityMappingInput, result: IdentityLookupResult
) -> list[str]:
    mismatches: list[str] = []
    if body.expected_uid is not None and body.expected_uid != result.uid:
        mismatches.append(
            f"expected uid {body.expected_uid} but LDAP returned {result.uid}"
        )
    if (
        body.expected_primary_gid is not None
        and body.expected_primary_gid != result.primary_gid
    ):
        mismatches.append(
            "expected primary gid "
            f"{body.expected_primary_gid} but LDAP returned {result.primary_gid}"
        )
    expected_groups = set(body.expected_groups)
    actual_groups = set(result.groups)
    missing_groups = sorted(expected_groups - actual_groups)
    if missing_groups:
        mismatches.append(
            "expected groups missing from LDAP result: " + ", ".join(missing_groups)
        )
    return mismatches


def _mapping_drift(mapping: dict[str, Any], result: IdentityLookupResult) -> list[str]:
    drift: list[str] = []
    if mapping["uid"] != result.uid:
        drift.append(f"uid changed from {mapping['uid']} to {result.uid}")
    if mapping["gid"] != result.primary_gid:
        drift.append(f"primary gid changed from {mapping['gid']} to {result.primary_gid}")
    if set(mapping["groups"]) != set(result.groups):
        drift.append(
            "groups changed from "
            f"{sorted(mapping['groups'])} to {sorted(result.groups)}"
        )
    return drift


def agent_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

    @router.post("/reports")
    def submit_agent_report(
        report: AgentReport,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        actor = authenticated_actor(request, services)
        service = AgentReportIngestionService(services.repository, services.observability)
        try:
            report_id = service.ingest(report, actor=actor)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"report_id": report_id, "status": "Fresh"}

    return router


def operational_query_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/operations", tags=["operational-query"])

    @router.get("/action-required")
    def action_required(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.action_required()

    @router.get("/inventory")
    def inventory(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return _inventory_service(services).effective_inventory()

    @router.get("/agent-reports")
    def agent_reports(
        request: Request,
        freshness: str | None = None,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_agent_reports(
            freshness=freshness.capitalize() if freshness else None,
            stale_seconds=services.settings.agent_report_stale_seconds,
            update_stale=True,
        )

    @router.get("/storage-mappings")
    def storage_mappings(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_storage_mappings()

    @router.get("/storage-mappings/{storage_name}")
    def storage_mapping(
        storage_name: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        mapping = services.repository.get_storage_mapping(storage_name)
        if not mapping:
            raise HTTPException(status_code=404, detail="storage mapping not found")
        return mapping

    @router.get("/requests")
    def requests(
        request: Request,
        requester_id: str = Query(..., min_length=1),
        limit: int | None = Query(default=None, gt=0),
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        if limit is None:
            return services.repository.list_requests(requester_id=requester_id)
        return services.repository.list_requests(requester_id=requester_id, limit=limit)

    @router.get("/requests/{request_id}")
    def request_history(
        request_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.request_history(request_id)

    @router.get("/resources")
    def resource_history(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_resources()

    @router.get("/kubernetes/namespace-quotas/{cluster_name}/{namespace_name}")
    def kubernetes_namespace_quota(
        cluster_name: str,
        namespace_name: str,
        request: Request,
        source: str = "both",
        include_non_dms: bool = False,
        include_status_used: bool = True,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        try:
            return services.query.kubernetes_namespace_quota(
                cluster_name=cluster_name,
                namespace_name=namespace_name,
                source=source,
                include_non_dms=include_non_dms,
                include_status_used=include_status_used,
                kubernetes_adapter=services.kubernetes_quota,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @router.get("/runs/stale")
    def stale_runs(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.stale_or_recovery_runs()

    @router.get("/worker-agent-health")
    def worker_agent_health(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.worker_agent_health()

    @router.get("/identity-issues")
    def identity_issues(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.identity_issues()

    @router.get("/data-jobs")
    def list_data_jobs(
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.repository.list_data_jobs()

    @router.get("/data-jobs/{job_id}")
    def data_job_status(
        job_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> dict[str, Any]:
        authenticated_actor(request, services)
        return services.query.data_job_status(job_id)

    @router.get("/diagnostics/{correlation_id}")
    def diagnostics(
        correlation_id: str,
        request: Request,
        services: AppServices = Depends(get_services),
    ) -> list[dict[str, Any]]:
        authenticated_actor(request, services)
        return services.query.diagnostic_correlation(correlation_id)

    return router
