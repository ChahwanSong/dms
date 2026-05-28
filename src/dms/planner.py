from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import render_kubernetes_resource_quota_hard
from .backend_registry import BackendAdapterRegistry
from .domain import (
    LifecycleState,
    OperationKind,
    ResourceKind,
    WorkerRole,
)
from .repositories import DmsRepository


RM_OPERATIONS = {
    OperationKind.FILESYSTEM_CREATE.value,
    OperationKind.FILESYSTEM_UPDATE.value,
    OperationKind.FILESYSTEM_BLOCK.value,
    OperationKind.FILESYSTEM_INITIALIZE.value,
    OperationKind.FILESYSTEM_DELETE.value,
    OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
    OperationKind.FILESYSTEM_IMPORT.value,
    OperationKind.FILESYSTEM_CHECK.value,
    OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
    OperationKind.K8S_QUOTA_CREATE.value,
    OperationKind.K8S_QUOTA_UPDATE.value,
    OperationKind.K8S_QUOTA_BLOCK.value,
    OperationKind.K8S_QUOTA_DELETE.value,
    OperationKind.K8S_QUOTA_SYNC.value,
}

DM_OPERATIONS = {
    OperationKind.DATA_SYNC.value,
    OperationKind.DATA_RM.value,
    OperationKind.DATA_SCAN.value,
}


@dataclass
class Planner:
    repository: DmsRepository
    backend_registry: BackendAdapterRegistry | None = None

    def run_once(self, limit: int = 50) -> int:
        planned = 0
        for request in self.repository.list_plannable_requests(limit=limit):
            self._plan_request(request)
            planned += 1
        return planned

    def _plan_request(self, request: dict[str, Any]) -> None:
        prior = self.repository.find_prior_active_request(request)
        if prior:
            self.repository.complete_result(
                request_id=request["request_id"],
                plan_id=None,
                run_id=None,
                terminal_status=LifecycleState.CONFLICT,
                message=(
                    "prior request for the same resource has not reached a "
                    f"terminal state: {prior['request_id']}"
                ),
                verification_summary={
                    "resource_key": request["resource_key"],
                    "prior_request_id": prior["request_id"],
                    "backend_side_effect": False,
                },
                error_category="ordering",
                actor="planner",
            )
            return

        self.repository.update_request_status(
            request["request_id"],
            LifecycleState.PLANNING,
            reason="planner started",
            actor="planner",
        )

        operation = request["operation"]
        if operation in RM_OPERATIONS:
            if self._reject_invalid_kubernetes_quota_request(request):
                return
            if self._reject_unsafe_storage_mapping(request, WorkerRole.RM):
                return
            if self._reject_inconsistent_kubernetes_quota_mapping(request):
                return
            desired_state = self._desired_state(request)
            self.repository.create_plan(
                request_id=request["request_id"],
                worker_role=WorkerRole.RM,
                operation_kind=operation,
                resource_key=request["resource_key"],
                desired_state=desired_state,
                precondition=self._precondition(request),
                execution_metadata=self._rm_execution_metadata(request, desired_state),
            )
            return

        if operation in DM_OPERATIONS:
            storage_name = request["payload_summary"]["storage_name"]
            if self._reject_unsafe_storage_mapping(request, WorkerRole.DM):
                return
            job_id = self.repository.create_data_job(
                request_id=request["request_id"],
                operation=operation,
                storage_name=storage_name,
                source=request["payload_summary"].get("source_path"),
                destination=request["payload_summary"].get("destination_path"),
                target=request["payload_summary"].get("target_path"),
                priority=int(request["payload_summary"].get("priority", 100)),
                worker_pool=self._worker_pool(storage_name),
            )
            self.repository.create_plan(
                request_id=request["request_id"],
                worker_role=WorkerRole.DM,
                operation_kind=operation,
                resource_key=request["resource_key"],
                desired_state=self._desired_state(request),
                precondition={
                    "job_id": job_id,
                    "safe_paths_only": True,
                    "preview_required": operation
                    in {OperationKind.DATA_SYNC.value, OperationKind.DATA_RM.value},
                },
                execution_metadata={
                    "resource_kind": ResourceKind.DATA_JOB.value,
                    "job_id": job_id,
                    "phase": "preview"
                    if operation
                    in {OperationKind.DATA_SYNC.value, OperationKind.DATA_RM.value}
                    else "execution",
                    "backend_side_effect_owner": "dm-worker",
                    "storage_backend": self._storage_backend(request),
                },
            )
            return

        self.repository.complete_result(
            request_id=request["request_id"],
            plan_id=None,
            run_id=None,
            terminal_status=LifecycleState.REJECTED,
            message=f"operation is not supported by planner: {operation}",
            verification_summary={"backend_side_effect": False},
            error_category="planner",
            actor="planner",
        )

    def _desired_state(self, request: dict[str, Any]) -> dict[str, Any]:
        desired = dict(request["payload_summary"])
        desired.update(
            {
                "operation": request["operation"],
                "resource_kind": request["resource_kind"],
                "resource_key": request["resource_key"],
            }
        )
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            desired.setdefault("resource_quota_name", "dms-storage-quota")
            self._enrich_kubernetes_quota_desired(desired)
        return desired

    def _rm_execution_metadata(
        self, request: dict[str, Any], desired_state: dict[str, Any]
    ) -> dict[str, Any]:
        metadata = {
            "resource_kind": request["resource_kind"],
            "planner": "phase1",
            "backend_side_effect_owner": "rm-worker",
            "storage_backend": self._storage_backend(request),
        }
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            metadata["planner"] = "phase4"
            metadata["kubernetes_backend"] = {
                "cluster_name": desired_state.get("cluster_name"),
                "namespace_name": desired_state.get("namespace_name"),
                "resource_quota_name": desired_state.get(
                    "resource_quota_name", "dms-storage-quota"
                ),
                "storage_classes": [
                    {
                        "storage_name": entry.get("storage_name"),
                        "storage_class_name": entry.get("storage_class_name"),
                    }
                    for entry in desired_state.get("storage_class_quotas") or []
                ],
            }
        return metadata

    @staticmethod
    def _precondition(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "commit_order": request["commit_order"],
            "resource_key": request["resource_key"],
            "source_of_truth": "operational-postgresql",
        }

    def _worker_pool(self, storage_name: str) -> dict[str, Any]:
        mapping = self.repository.get_storage_mapping(storage_name)
        if self.backend_registry is not None:
            return self.backend_registry.data_worker_pool(storage_name)
        if mapping and mapping.get("sanity_result"):
            agent_observed = mapping["sanity_result"].get("agent_observed", {})
            return {
                "selection": "agent-inventory",
                "required_mounts": [storage_name],
                "readiness": mapping.get("readiness", {}),
                "candidates": agent_observed.get("dm_candidates", []),
                "sanity_status": mapping.get("sanity_status"),
            }
        registry = self.backend_registry or BackendAdapterRegistry.with_phase1_defaults(
            self.repository
        )
        return registry.data_worker_pool(storage_name)

    def _storage_backend(self, request: dict[str, Any]) -> dict[str, Any]:
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            mappings = []
            for storage_name in sorted(self._required_storage_names(request)):
                mapping = self.repository.get_storage_mapping(storage_name)
                if mapping:
                    mappings.append(
                        {
                            "backend_type": mapping["backend_template"].get(
                                "backend_type", "unknown"
                            ),
                            "storage_name": mapping["storage_name"],
                            "cluster_name": mapping.get("cluster_name"),
                            "storage_class_name": mapping.get("storage_class_name"),
                            "sanity_status": mapping["sanity_status"],
                            "version": mapping["version"],
                            "readiness": mapping.get("readiness", {}),
                        }
                    )
            return {"backend_type": "kubernetes", "storage_mappings": mappings}
        storage_name = request["payload_summary"].get("storage_name")
        if not storage_name:
            return {"backend_type": "unmapped"}
        mapping = self.repository.get_storage_mapping(storage_name)
        if not mapping:
            return {"backend_type": "unmapped", "storage_name": storage_name}
        return {
            "backend_type": mapping["backend_template"].get("backend_type", "unknown"),
            "storage_name": mapping["storage_name"],
            "sanity_status": mapping["sanity_status"],
            "version": mapping["version"],
            "readiness": mapping.get("readiness", {}),
        }

    def _reject_unsafe_storage_mapping(
        self, request: dict[str, Any], worker_role: WorkerRole
    ) -> bool:
        storage_names = self._required_storage_names(request)
        if not storage_names:
            return False
        issues: list[dict[str, Any]] = []
        for storage_name in sorted(storage_names):
            mapping = self.repository.get_storage_mapping(storage_name)
            if not mapping:
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": "storage_mapping_missing",
                    }
                )
                continue
            if mapping.get("disabled_at"):
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": "storage_mapping_disabled",
                        "sanity_status": mapping["sanity_status"],
                    }
                )
                continue
            if mapping["sanity_status"] in {"Failed", "Unknown"}:
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": "storage_mapping_sanity",
                        "sanity_status": mapping["sanity_status"],
                        "sanity_result": mapping.get("sanity_result", {}),
                    }
                )
                continue
            readiness_key = (
                "resource_management"
                if worker_role == WorkerRole.RM
                else "data_management"
            )
            readiness = (mapping.get("readiness") or {}).get(readiness_key)
            if readiness != "Ready":
                issues.append(
                    {
                        "storage_name": storage_name,
                        "reason": f"missing_{worker_role.value.lower()}_readiness",
                        "sanity_status": mapping["sanity_status"],
                        "readiness": mapping.get("readiness", {}),
                    }
                )
        if not issues:
            return False
        self.repository.complete_result(
            request_id=request["request_id"],
            plan_id=None,
            run_id=None,
            terminal_status=LifecycleState.REJECTED,
            message="storage mapping sanity/readiness guard rejected request",
            verification_summary={
                "backend_side_effect": False,
                "storage_names": sorted(storage_names),
                "issues": issues,
            },
            error_category="storage_mapping_sanity",
            actor="planner",
        )
        return True

    def _reject_invalid_kubernetes_quota_request(self, request: dict[str, Any]) -> bool:
        if request["resource_kind"] != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            return False
        operation = request["operation"]
        if operation != OperationKind.K8S_QUOTA_CREATE.value:
            return self._reject_planner_issue(
                request,
                message="Phase 4 only implements Kubernetes namespace quota create/apply",
                issues=[
                    {
                        "reason": "not_implemented_phase4",
                        "operation": operation,
                    }
                ],
                error_category="not_implemented",
            )
        payload = request["payload_summary"]
        issues: list[dict[str, Any]] = []
        if not payload.get("cluster_name"):
            issues.append({"reason": "cluster_name_missing"})
        if not payload.get("namespace_name"):
            issues.append({"reason": "namespace_name_missing"})
        storage_class_quotas = payload.get("storage_class_quotas") or []
        if len(storage_class_quotas) != 1:
            issues.append(
                {
                    "reason": "unsupported_storage_class_quota_count",
                    "count": len(storage_class_quotas),
                }
            )
        for entry in storage_class_quotas:
            if not isinstance(entry, dict) or not entry.get("storage_name"):
                issues.append({"reason": "storage_class_quota_storage_name_missing"})
        quota = payload.get("quota") or {}
        for key in ("requests_storage_bytes", "pvc_count"):
            value = quota.get(key)
            try:
                if value is None or int(value) <= 0:
                    issues.append({"reason": f"quota_{key}_invalid", "value": value})
            except (TypeError, ValueError):
                issues.append({"reason": f"quota_{key}_invalid", "value": value})
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid Kubernetes namespace quota request",
            issues=issues,
            error_category="validation",
        )

    def _reject_inconsistent_kubernetes_quota_mapping(
        self, request: dict[str, Any]
    ) -> bool:
        if request["resource_kind"] != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            return False
        payload = request["payload_summary"]
        cluster_name = payload.get("cluster_name")
        issues: list[dict[str, Any]] = []
        for entry in payload.get("storage_class_quotas") or []:
            storage_name = entry.get("storage_name") if isinstance(entry, dict) else None
            if not storage_name:
                continue
            mapping = self.repository.get_storage_mapping(storage_name)
            if not mapping:
                continue
            if mapping.get("cluster_name") != cluster_name:
                issues.append(
                    {
                        "reason": "storage_mapping_cluster_mismatch",
                        "storage_name": storage_name,
                        "request_cluster_name": cluster_name,
                        "mapping_cluster_name": mapping.get("cluster_name"),
                    }
                )
            requested_storage_class = entry.get("storage_class_name")
            if (
                requested_storage_class
                and requested_storage_class != mapping.get("storage_class_name")
            ):
                issues.append(
                    {
                        "reason": "storage_class_name_mismatch",
                        "storage_name": storage_name,
                        "request_storage_class_name": requested_storage_class,
                        "mapping_storage_class_name": mapping.get("storage_class_name"),
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="Kubernetes namespace quota storage mapping mismatch",
            issues=issues,
            error_category="storage_mapping_sanity",
        )

    def _reject_planner_issue(
        self,
        request: dict[str, Any],
        *,
        message: str,
        issues: list[dict[str, Any]],
        error_category: str,
    ) -> bool:
        self.repository.complete_result(
            request_id=request["request_id"],
            plan_id=None,
            run_id=None,
            terminal_status=LifecycleState.REJECTED,
            message=message,
            verification_summary={"backend_side_effect": False, "issues": issues},
            error_category=error_category,
            actor="planner",
        )
        return True

    @staticmethod
    def _required_storage_names(request: dict[str, Any]) -> set[str]:
        payload = request["payload_summary"]
        operation = request["operation"]
        if operation == OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value:
            return set()
        if operation in DM_OPERATIONS:
            return {payload["storage_name"]} if payload.get("storage_name") else set()
        if request["resource_kind"] == ResourceKind.FILESYSTEM.value:
            return {payload["storage_name"]} if payload.get("storage_name") else set()
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            storage_names = {
                entry["storage_name"]
                for entry in payload.get("storage_class_quotas") or []
                if isinstance(entry, dict) and entry.get("storage_name")
            }
            if payload.get("storage_name"):
                storage_names.add(payload["storage_name"])
            return storage_names
        return set()

    def _enrich_kubernetes_quota_desired(self, desired: dict[str, Any]) -> None:
        if desired.get("resource_kind") != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            return
        storage_class_quotas: list[dict[str, Any]] = []
        for entry in desired.get("storage_class_quotas") or []:
            enriched = dict(entry)
            storage_name = enriched.get("storage_name")
            mapping = self.repository.get_storage_mapping(storage_name) if storage_name else None
            if mapping:
                enriched["storage_class_name"] = mapping.get("storage_class_name")
                enriched["cluster_name"] = mapping.get("cluster_name")
            storage_class_quotas.append(enriched)
        desired["storage_class_quotas"] = storage_class_quotas
        desired["resource_quota_name"] = desired.get(
            "resource_quota_name", "dms-storage-quota"
        )
        desired["resource_quota_hard"] = render_kubernetes_resource_quota_hard(desired)
