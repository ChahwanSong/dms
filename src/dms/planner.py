from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
            if self._reject_unsafe_storage_mapping(request, WorkerRole.RM):
                return
            self.repository.create_plan(
                request_id=request["request_id"],
                worker_role=WorkerRole.RM,
                operation_kind=operation,
                resource_key=request["resource_key"],
                desired_state=self._desired_state(request),
                precondition=self._precondition(request),
                execution_metadata={
                    "resource_kind": request["resource_kind"],
                    "planner": "phase1",
                    "backend_side_effect_owner": "rm-worker",
                    "storage_backend": self._storage_backend(request),
                },
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

    @staticmethod
    def _desired_state(request: dict[str, Any]) -> dict[str, Any]:
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
        return desired

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
