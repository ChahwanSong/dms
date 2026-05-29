from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import (
    kubernetes_resource_quota_value_to_base_units,
    render_kubernetes_resource_quota_hard,
    zero_kubernetes_resource_quota_hard,
)
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
    OperationKind.K8S_QUOTA_CHECK.value,
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
            if self._reject_kubernetes_quota_decrease(request, desired_state):
                return
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
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if existing and request["operation"] != OperationKind.K8S_QUOTA_CREATE.value:
                desired = self._merge_kubernetes_quota_desired(
                    existing["desired_state"], desired
                )
        desired.update(
            {
                "operation": request["operation"],
                "resource_kind": request["resource_kind"],
                "resource_key": request["resource_key"],
            }
        )
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            desired.setdefault("resource_quota_name", "dms-storage-quota")
            if not self._should_preserve_kubernetes_quota_hard(request):
                self._enrich_kubernetes_quota_desired(desired)
            self._apply_kubernetes_block_desired(request, desired)
            self._apply_kubernetes_blocked_update_desired(request, desired)
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
            metadata["planner"] = (
                "phase6"
                if len(desired_state.get("storage_class_quotas") or []) > 1
                else
                "phase4"
                if request["operation"] == OperationKind.K8S_QUOTA_CREATE.value
                else "phase5"
            )
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
        payload = request["payload_summary"]
        issues: list[dict[str, Any]] = []
        if not payload.get("cluster_name"):
            issues.append({"reason": "cluster_name_missing"})
        if not payload.get("namespace_name"):
            issues.append({"reason": "namespace_name_missing"})
        existing = self.repository.get_resource(request["resource_kind"], request["resource_key"])
        if operation != OperationKind.K8S_QUOTA_CREATE.value and not existing:
            issues.append({"reason": "resource_missing", "resource_key": request["resource_key"]})
        raw_storage_class_quotas = payload.get("storage_class_quotas")
        storage_class_quotas = raw_storage_class_quotas or []
        if raw_storage_class_quotas is not None and not isinstance(raw_storage_class_quotas, list):
            issues.append({"reason": "storage_class_quotas_must_be_list"})
            storage_class_quotas = []
        seen_storage_names: set[str] = set()
        for entry in storage_class_quotas:
            if not isinstance(entry, dict) or not entry.get("storage_name"):
                issues.append({"reason": "storage_class_quota_storage_name_missing"})
                continue
            storage_name = entry["storage_name"]
            if storage_name in seen_storage_names:
                issues.append(
                    {
                        "reason": "duplicate_storage_name",
                        "storage_name": storage_name,
                    }
                )
            seen_storage_names.add(storage_name)
            if operation in {
                OperationKind.K8S_QUOTA_CREATE.value,
                OperationKind.K8S_QUOTA_UPDATE.value,
            } and len(storage_class_quotas) > 1:
                if entry.get("requests_storage_bytes") is None and entry.get(
                    "capacity_bytes"
                ) is None:
                    issues.append(
                        {
                            "reason": "storage_class_quota_requests_storage_bytes_required",
                            "storage_name": storage_name,
                        }
                    )
            for key in ("requests_storage_bytes", "capacity_bytes", "pvc_count"):
                if entry.get(key) is None:
                    continue
                try:
                    if int(entry[key]) <= 0:
                        issues.append(
                            {
                                "reason": f"storage_class_quota_{key}_invalid",
                                "storage_name": storage_name,
                                "value": entry[key],
                            }
                        )
                except (TypeError, ValueError):
                    issues.append(
                        {
                            "reason": f"storage_class_quota_{key}_invalid",
                            "storage_name": storage_name,
                            "value": entry[key],
                        }
                    )
        quota = payload.get("quota") or {}
        if operation in {
            OperationKind.K8S_QUOTA_CREATE.value,
            OperationKind.K8S_QUOTA_UPDATE.value,
        }:
            for key in ("requests_storage_bytes", "pvc_count"):
                value = quota.get(key)
                if operation == OperationKind.K8S_QUOTA_UPDATE.value and value is None:
                    continue
                try:
                    if value is None or int(value) <= 0:
                        issues.append({"reason": f"quota_{key}_invalid", "value": value})
                except (TypeError, ValueError):
                    issues.append({"reason": f"quota_{key}_invalid", "value": value})
        if operation == OperationKind.K8S_QUOTA_BLOCK.value:
            block = payload.get("block")
            if not isinstance(block, bool):
                issues.append({"reason": "block_boolean_required", "value": block})
            if block is True and existing:
                resource_type = existing["desired_state"].get("resource_type")
                if resource_type in {"system", "admin"}:
                    issues.append(
                        {"reason": "resource_type_cannot_be_blocked", "resource_type": resource_type}
                    )
            if block is False and existing:
                restore_hard = (
                    existing["desired_state"].get("block_state", {}).get("restore_hard")
                )
                if not restore_hard:
                    issues.append({"reason": "block_restore_state_missing"})
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
        seen_storage_classes: set[str] = set()
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
            derived_storage_class = mapping.get("storage_class_name")
            if derived_storage_class in seen_storage_classes:
                issues.append(
                    {
                        "reason": "duplicate_storage_class_name",
                        "storage_name": storage_name,
                        "storage_class_name": derived_storage_class,
                    }
                )
            if derived_storage_class:
                seen_storage_classes.add(derived_storage_class)
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

    def _required_storage_names(self, request: dict[str, Any]) -> set[str]:
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
            if not storage_names:
                resource = self.repository.get_resource(
                    request["resource_kind"], request["resource_key"]
                )
                desired = resource["desired_state"] if resource else {}
                storage_names = {
                    entry["storage_name"]
                    for entry in desired.get("storage_class_quotas") or []
                    if isinstance(entry, dict) and entry.get("storage_name")
                }
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

    @staticmethod
    def _merge_kubernetes_quota_desired(
        existing_desired: dict[str, Any], request_payload: dict[str, Any]
    ) -> dict[str, Any]:
        desired = dict(existing_desired)
        desired.update(request_payload)
        if isinstance(existing_desired.get("quota"), dict):
            quota = dict(existing_desired["quota"])
            if isinstance(request_payload.get("quota"), dict):
                quota.update(request_payload["quota"])
            desired["quota"] = quota
        return desired

    @staticmethod
    def _should_preserve_kubernetes_quota_hard(request: dict[str, Any]) -> bool:
        if request["resource_kind"] != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            return False
        if request["operation"] not in {
            OperationKind.K8S_QUOTA_CHECK.value,
            OperationKind.K8S_QUOTA_DELETE.value,
            OperationKind.K8S_QUOTA_SYNC.value,
        }:
            return False
        payload = request["payload_summary"]
        return not payload.get("quota") and not payload.get("storage_class_quotas")

    def _apply_kubernetes_block_desired(
        self, request: dict[str, Any], desired: dict[str, Any]
    ) -> None:
        if request["operation"] != OperationKind.K8S_QUOTA_BLOCK.value:
            return
        block = bool(request["payload_summary"].get("block"))
        if block:
            restore_hard = dict(desired["resource_quota_hard"])
            desired["resource_quota_hard"] = zero_kubernetes_resource_quota_hard(restore_hard)
            desired["block"] = True
            desired["block_state"] = {
                "blocked": True,
                "block_mode": request["payload_summary"].get("block_mode", "quota-zero"),
                "restore_hard": restore_hard,
                "reason": request["payload_summary"].get("reason"),
            }
            return
        restore_hard = desired.get("block_state", {}).get("restore_hard")
        if restore_hard:
            desired["resource_quota_hard"] = dict(restore_hard)
        desired["block"] = False
        desired["block_state"] = {
            "blocked": False,
            "restored_hard": desired.get("resource_quota_hard", {}),
            "reason": request["payload_summary"].get("reason"),
        }

    def _apply_kubernetes_blocked_update_desired(
        self, request: dict[str, Any], desired: dict[str, Any]
    ) -> None:
        if request["operation"] != OperationKind.K8S_QUOTA_UPDATE.value:
            return
        block_state = desired.get("block_state") or {}
        if not block_state.get("blocked"):
            return
        restore_hard = dict(desired.get("resource_quota_hard") or {})
        if not restore_hard:
            return
        updated_block_state = dict(block_state)
        updated_block_state["restore_hard"] = restore_hard
        updated_block_state["updated_while_blocked"] = True
        desired["resource_quota_hard"] = zero_kubernetes_resource_quota_hard(restore_hard)
        desired["block"] = True
        desired["block_state"] = updated_block_state

    def _reject_kubernetes_quota_decrease(
        self, request: dict[str, Any], desired_state: dict[str, Any]
    ) -> bool:
        if request["operation"] != OperationKind.K8S_QUOTA_UPDATE.value:
            return False
        resource = self.repository.get_resource(request["resource_kind"], request["resource_key"])
        if not resource:
            return False
        used = _observed_quota_used(resource["observed_state"])
        hard = desired_state.get("resource_quota_hard") or {}
        block_state = desired_state.get("block_state") or {}
        if block_state.get("blocked") and block_state.get("restore_hard"):
            hard = block_state["restore_hard"]
        issues: list[dict[str, Any]] = []
        for key, used_value in used.items():
            if key not in hard:
                continue
            desired_value = kubernetes_resource_quota_value_to_base_units(key, hard[key])
            parsed_used = kubernetes_resource_quota_value_to_base_units(key, used_value)
            if desired_value < parsed_used:
                issues.append(
                    {
                        "reason": "quota_decrease_below_live_used",
                        "resource": key,
                        "desired": hard[key],
                        "used": used_value,
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="Kubernetes namespace quota decrease is below live used",
            issues=issues,
            error_category="quota_decrease_guard",
        )


def _observed_quota_used(observed_state: dict[str, Any]) -> dict[str, Any]:
    resource_quota = observed_state.get("resource_quota") or {}
    if resource_quota.get("status_used"):
        return resource_quota["status_used"]
    verification = observed_state.get("pvc_admission_verification") or {}
    after_allowed = verification.get("resource_quota_status_after_allowed_pvc") or {}
    return after_allowed.get("used") or {}
