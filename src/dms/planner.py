from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
    validate_storage_root_basename,
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
    OperationKind.FILESYSTEM_SYNC.value,
    OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
    OperationKind.K8S_QUOTA_CREATE.value,
    OperationKind.K8S_QUOTA_UPDATE.value,
    OperationKind.K8S_QUOTA_BLOCK.value,
    OperationKind.K8S_QUOTA_DELETE.value,
    OperationKind.K8S_QUOTA_SYNC.value,
    OperationKind.K8S_QUOTA_CHECK.value,
    OperationKind.K8S_QUOTA_AUDIT.value,
}

PHASE12_FILESYSTEM_OPERATIONS = {
    OperationKind.FILESYSTEM_CREATE.value,
    OperationKind.FILESYSTEM_UPDATE.value,
    OperationKind.FILESYSTEM_BLOCK.value,
    OperationKind.FILESYSTEM_DELETE.value,
    OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
    OperationKind.FILESYSTEM_IMPORT.value,
    OperationKind.FILESYSTEM_CHECK.value,
    OperationKind.FILESYSTEM_SYNC.value,
    OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
}

PHASE12_FILESYSTEM_CREATE_UNSUPPORTED_PAYLOAD_FIELDS = {
    "acl",
    "capacity_bytes",
    "file_count",
    "rename",
    "block",
    "check",
    "sync",
    "storage_class_quotas",
    "resource_quota_hard",
    "reset_quota_to_default",
}

PHASE12_FILESYSTEM_BLOCK_UNSUPPORTED_PAYLOAD_FIELDS = {
    "acl",
    "capacity_bytes",
    "file_count",
    "quota",
    "rename",
    "check",
    "sync",
    "storage_class_quotas",
    "resource_quota_hard",
    "reset_quota_to_default",
}

PHASE12_FILESYSTEM_UPDATE_ALLOWED_PAYLOAD_FIELDS = {
    "storage_name",
    "directory_name",
    "quota",
    "reason",
}

PHASE12_FILESYSTEM_CHECK_ALLOWED_PAYLOAD_FIELDS = {
    "storage_name",
    "directory_name",
    "include_quota",
    "include_permission",
    "record_action_required",
    "reason",
}

PHASE12_FILESYSTEM_SYNC_ALLOWED_PAYLOAD_FIELDS = {
    "storage_name",
    "directory_name",
    "source",
    "include_quota",
    "reason",
}

MAX_FILESYSTEM_QUOTA_CAPACITY_BYTES = 1024**4
MAX_FILESYSTEM_QUOTA_FILE_COUNT = 10_000_000

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
            if self._reject_invalid_filesystem_phase12_request(request):
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
        if request["resource_kind"] == ResourceKind.FILESYSTEM.value:
            desired = self._filesystem_desired_state(request, desired)
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            if request["operation"] == OperationKind.K8S_QUOTA_AUDIT.value:
                desired.update(
                    {
                        "operation": request["operation"],
                        "resource_kind": request["resource_kind"],
                        "resource_key": request["resource_key"],
                        "targets": self._resolve_kubernetes_quota_audit_targets(request),
                    }
                )
                return desired
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
                "requester_id": request.get("requester_id"),
            }
        )
        if request["resource_kind"] == ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            desired.setdefault("resource_quota_name", "dms-storage-quota")
            if self._is_kubernetes_default_reset(request):
                self._apply_kubernetes_default_quota_reset(request, desired)
            if not self._should_preserve_kubernetes_quota_hard(request):
                self._enrich_kubernetes_quota_desired(desired)
            self._apply_kubernetes_block_desired(request, desired)
            self._apply_kubernetes_blocked_update_desired(request, desired)
        return desired

    def _filesystem_desired_state(
        self, request: dict[str, Any], desired: dict[str, Any]
    ) -> dict[str, Any]:
        if request["operation"] == OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value:
            desired.update(
                {
                    "operation": request["operation"],
                    "resource_kind": request["resource_kind"],
                    "resource_key": request["resource_key"],
                    "targets": self._resolve_filesystem_expiration_targets(request),
                }
            )
            return desired
        existing = self.repository.get_resource(
            request["resource_kind"], request["resource_key"]
        )
        if (
            request["operation"]
            in {
                OperationKind.FILESYSTEM_UPDATE.value,
                OperationKind.FILESYSTEM_BLOCK.value,
                OperationKind.FILESYSTEM_DELETE.value,
                OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
                OperationKind.FILESYSTEM_IMPORT.value,
                OperationKind.FILESYSTEM_CHECK.value,
                OperationKind.FILESYSTEM_SYNC.value,
            }
            and existing
            and existing.get("status") != "Deleted"
        ):
            merged = dict(existing["desired_state"])
            merged.update(desired)
            desired = merged
        if request["operation"] == OperationKind.FILESYSTEM_CREATE.value:
            directory_name = desired.get("directory_name")
            if directory_name and not desired.get("access_group"):
                desired["access_group"] = f"dms-phase10-{directory_name}"
            desired.setdefault("mode", "0770")
            desired.setdefault("resource_type", "user")
            if "quota" in desired:
                desired["quota"] = _normalized_filesystem_quota(desired["quota"])
        if request["operation"] == OperationKind.FILESYSTEM_UPDATE.value and "quota" in request["payload_summary"]:
            desired["quota"] = _normalized_filesystem_quota(request["payload_summary"]["quota"])
        if request["operation"] == OperationKind.FILESYSTEM_ASSIGN_QUOTA.value:
            desired.setdefault("management_mode", "quota_only")
            desired["quota"] = _normalized_filesystem_quota(request["payload_summary"]["quota"])
            desired.setdefault("initialize_marker", True)
            desired.setdefault("resource_type", "user")
        if request["operation"] == OperationKind.FILESYSTEM_IMPORT.value:
            desired.setdefault("import_mode", "full")
            desired.setdefault("management_mode", "full")
            desired.setdefault("initialize_marker", True)
            access_policy = desired.get("access_policy") or {}
            if access_policy.get("users") is not None:
                desired["users"] = list(access_policy["users"])
            if access_policy.get("denied_users") is not None:
                desired["validation_denied_users"] = list(access_policy["denied_users"])
            if access_policy.get("expected_group") and not desired.get("access_group"):
                desired["access_group"] = access_policy["expected_group"]
            if access_policy.get("expected_mode") and not desired.get("mode"):
                desired["mode"] = access_policy["expected_mode"]
            desired.setdefault("resource_type", "user")
            if "quota" in request["payload_summary"]:
                desired["quota"] = _normalized_filesystem_quota(request["payload_summary"]["quota"])
        if request["operation"] == OperationKind.FILESYSTEM_CHECK.value:
            desired.setdefault("include_quota", True)
            desired.setdefault("include_permission", True)
        if request["operation"] == OperationKind.FILESYSTEM_SYNC.value:
            desired.setdefault("source", "live")
            desired.setdefault("include_quota", True)
        if request["operation"] == OperationKind.FILESYSTEM_BLOCK.value:
            self._apply_filesystem_block_desired(request, existing, desired)
        return desired

    def _apply_filesystem_block_desired(
        self,
        request: dict[str, Any],
        existing: dict[str, Any] | None,
        desired: dict[str, Any],
    ) -> None:
        block = bool(request["payload_summary"].get("block"))
        desired["block"] = block
        if block:
            prior_block_state = dict((existing or {}).get("desired_state", {}).get("block_state") or {})
            if prior_block_state.get("blocked"):
                desired["block_state"] = prior_block_state
                return
            desired["block_state"] = {
                "blocked": True,
                "block_mode": request["payload_summary"].get(
                    "block_mode", "permission-zero"
                ),
                "reason": request["payload_summary"].get("reason"),
            }
            return
        block_state = dict((existing or {}).get("desired_state", {}).get("block_state") or {})
        if not block_state:
            block_state = dict((existing or {}).get("observed_state", {}).get("block_state") or {})
        block_state["blocked"] = False
        block_state["reason"] = request["payload_summary"].get("reason")
        desired["block_state"] = block_state

    def _resolve_filesystem_expiration_targets(
        self, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = request["payload_summary"]
        scope = payload.get("scope") or {}
        max_targets = int(payload.get("max_targets", 100))
        resources = self.repository.list_filesystem_resources_expiring(
            storage_name=scope.get("storage_name"),
            resource_type=scope.get("resource_type"),
            status="expired",
            before=payload.get("expired_before"),
            include_blocked=True,
            limit=max_targets + 1,
        )
        targets: list[dict[str, Any]] = []
        for resource in resources[:max_targets]:
            desired = resource["desired_state"]
            targets.append(
                {
                    "storage_name": desired.get("storage_name"),
                    "directory_name": desired.get("directory_name"),
                    "resource_key": resource["resource_key"],
                    "resource_id": resource["resource_id"],
                    "resource_status": resource["status"],
                    "resource_type": desired.get("resource_type") or "user",
                    "expires_at": resource.get("expires_at"),
                    "block_state": resource.get("block_state") or {},
                    "desired_state": desired,
                }
            )
        return targets

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
            if request["operation"] == OperationKind.K8S_QUOTA_AUDIT.value:
                metadata["planner"] = "phase9"
            else:
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
                "target_count": len(desired_state.get("targets") or []),
                "storage_classes": [
                    {
                        "storage_name": entry.get("storage_name"),
                        "storage_class_name": entry.get("storage_class_name"),
                    }
                    for entry in desired_state.get("storage_class_quotas") or []
                ],
            }
        elif request["resource_kind"] == ResourceKind.FILESYSTEM.value:
            metadata["planner"] = (
                "phase12"
                if (
                    request["operation"]
                    in {
                        OperationKind.FILESYSTEM_UPDATE.value,
                        OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
                        OperationKind.FILESYSTEM_IMPORT.value,
                        OperationKind.FILESYSTEM_CHECK.value,
                        OperationKind.FILESYSTEM_SYNC.value,
                    }
                    or (
                        request["operation"] == OperationKind.FILESYSTEM_CREATE.value
                        and "quota" in desired_state
                    )
                )
                else
                "phase11"
                if request["operation"]
                in {
                    OperationKind.FILESYSTEM_BLOCK.value,
                    OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
                }
                else "phase10"
            )
            metadata["filesystem_backend"] = {
                "storage_name": desired_state.get("storage_name"),
                "directory_name": desired_state.get("directory_name"),
                "access_group": desired_state.get("access_group"),
                "mode": desired_state.get("mode"),
                "target_count": len(desired_state.get("targets") or []),
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
        if operation == OperationKind.K8S_QUOTA_AUDIT.value:
            return self._reject_invalid_kubernetes_quota_audit_request(request)
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
            reset_to_default = self._is_kubernetes_default_reset(request)
            for key in ("requests_storage_bytes", "pvc_count"):
                value = quota.get(key)
                if (
                    operation == OperationKind.K8S_QUOTA_UPDATE.value
                    and (value is None or reset_to_default)
                ):
                    continue
                try:
                    if value is None or int(value) <= 0:
                        issues.append({"reason": f"quota_{key}_invalid", "value": value})
                except (TypeError, ValueError):
                    issues.append({"reason": f"quota_{key}_invalid", "value": value})
            if reset_to_default:
                policy, policy_issues = self._kubernetes_default_policy_for_request(request)
                issues.extend(policy_issues)
                if policy:
                    for issue in self._validate_default_policy_storage_entries(
                        request, policy["quota"]
                    ):
                        issues.append(issue)
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

    def _reject_invalid_filesystem_phase12_request(
        self, request: dict[str, Any]
    ) -> bool:
        if request["resource_kind"] != ResourceKind.FILESYSTEM.value:
            return False
        operation = request["operation"]
        payload = request["payload_summary"]
        issues: list[dict[str, Any]] = []

        if operation not in PHASE12_FILESYSTEM_OPERATIONS:
            return self._reject_planner_issue(
                request,
                message="filesystem operation is unsupported in Phase 12",
                issues=[
                    {
                        "reason": "filesystem_operation_unsupported_phase12",
                        "operation": operation,
                    }
                ],
                error_category="unsupported",
            )

        if operation == OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value:
            return self._reject_invalid_filesystem_expiration_sweep_request(
                request, issues
            )

        storage_name = payload.get("storage_name")
        directory_name = payload.get("directory_name")
        _append_basename_issue(issues, "storage_name", storage_name)
        _append_basename_issue(issues, "directory_name", directory_name)

        if operation == OperationKind.FILESYSTEM_CREATE.value:
            unsupported = sorted(
                field
                for field in PHASE12_FILESYSTEM_CREATE_UNSUPPORTED_PAYLOAD_FIELDS
                if field in payload
            )
            if unsupported:
                issues.append(
                    {
                        "reason": "filesystem_payload_fields_unsupported_phase12",
                        "fields": unsupported,
                    }
                )
            if "quota" in payload:
                issues.extend(_filesystem_quota_issues(payload.get("quota")))
            resource_type = payload.get("resource_type")
            if resource_type is not None and str(resource_type) not in {
                "user",
                "system",
                "admin",
            }:
                issues.append(
                    {
                        "reason": "filesystem_resource_type_unsupported",
                        "resource_type": resource_type,
                    }
                )
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if existing and existing.get("status") != "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_already_exists",
                        "resource_key": request["resource_key"],
                        "status": existing.get("status"),
                    }
                )
            users = payload.get("users")
            users_list = _validate_string_list(issues, "users", users, required=True)
            if users_list is not None and len(users_list) < 2:
                issues.append({"reason": "filesystem_users_minimum_two_required"})
            access_group = payload.get("access_group")
            if access_group is not None:
                _append_basename_issue(issues, "access_group", access_group)
                if isinstance(access_group, str) and not access_group.startswith("dms-"):
                    issues.append(
                        {
                            "reason": "filesystem_access_group_must_be_dms_managed",
                            "access_group": access_group,
                        }
                    )
            mode = payload.get("mode")
            if mode is not None and str(mode) not in {"0750", "0770"}:
                issues.append(
                    {
                        "reason": "filesystem_mode_unsupported_phase10",
                        "mode": mode,
                    }
                )
            denied_users = payload.get("denied_users") or payload.get(
                "validation_denied_users"
            )
            if denied_users is not None:
                _validate_string_list(
                    issues,
                    "denied_users",
                    denied_users,
                    required=False,
                )
            expires_at = payload.get("expires_at")
            if expires_at is not None:
                try:
                    datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
                except ValueError:
                    issues.append(
                        {
                            "reason": "filesystem_expires_at_invalid",
                            "expires_at": expires_at,
                        }
                    )

        if operation == OperationKind.FILESYSTEM_BLOCK.value:
            unsupported = sorted(
                field
                for field in PHASE12_FILESYSTEM_BLOCK_UNSUPPORTED_PAYLOAD_FIELDS
                if field in payload
            )
            if unsupported:
                issues.append(
                    {
                        "reason": "filesystem_payload_fields_unsupported_phase12",
                        "fields": unsupported,
                    }
                )
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            block = payload.get("block")
            if not isinstance(block, bool):
                issues.append({"reason": "block_boolean_required", "value": block})
            if block is True and existing:
                resource_type = existing["desired_state"].get("resource_type") or "user"
                if resource_type in {"system", "admin"}:
                    issues.append(
                        {
                            "reason": "resource_type_cannot_be_blocked",
                            "resource_type": resource_type,
                        }
                    )
                block_mode = payload.get("block_mode", "permission-zero")
                if block_mode != "permission-zero":
                    issues.append(
                        {
                            "reason": "filesystem_block_mode_unsupported",
                            "block_mode": block_mode,
                        }
                    )
            if block is False and existing:
                restore = _filesystem_restore_state(existing)
                if not restore:
                    issues.append({"reason": "filesystem_block_restore_missing"})

        if operation == OperationKind.FILESYSTEM_DELETE.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            elif existing["desired_state"].get("management_mode") == "quota_only":
                issues.append(
                    {
                        "reason": "filesystem_quota_only_delete_refused",
                        "resource_key": request["resource_key"],
                    }
                )

        if operation == OperationKind.FILESYSTEM_UPDATE.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            issues.extend(
                _unsupported_payload_issues(
                    payload,
                    PHASE12_FILESYSTEM_UPDATE_ALLOWED_PAYLOAD_FIELDS,
                    "filesystem_payload_fields_unsupported_phase12",
                )
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            if "quota" not in payload:
                issues.append({"reason": "filesystem_quota_required"})
            else:
                issues.extend(_filesystem_quota_issues(payload.get("quota")))

        if operation == OperationKind.FILESYSTEM_ASSIGN_QUOTA.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if existing and existing.get("status") != "Deleted":
                mode = existing["desired_state"].get("management_mode", "full")
                if mode != "quota_only":
                    issues.append(
                        {
                            "reason": "filesystem_resource_already_exists",
                            "resource_key": request["resource_key"],
                            "status": existing.get("status"),
                        }
                    )
            management_mode = payload.get("management_mode", "quota_only")
            if management_mode != "quota_only":
                issues.append(
                    {
                        "reason": "filesystem_management_mode_unsupported",
                        "management_mode": management_mode,
                    }
                )
            if "quota" not in payload:
                issues.append({"reason": "filesystem_quota_required"})
            else:
                issues.extend(_filesystem_quota_issues(payload.get("quota")))
            initialize_marker = payload.get("initialize_marker", True)
            if not isinstance(initialize_marker, bool):
                issues.append(
                    {
                        "reason": "initialize_marker_boolean_required",
                        "value": initialize_marker,
                    }
                )

        if operation == OperationKind.FILESYSTEM_IMPORT.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            if existing and existing.get("status") != "Deleted":
                mode = existing["desired_state"].get("management_mode", "full")
                if mode != "quota_only":
                    issues.append(
                        {
                            "reason": "filesystem_resource_already_exists",
                            "resource_key": request["resource_key"],
                            "status": existing.get("status"),
                        }
                    )
            import_mode = payload.get("import_mode", "full")
            if import_mode != "full":
                issues.append(
                    {
                        "reason": "filesystem_import_mode_unsupported",
                        "import_mode": import_mode,
                    }
                )
            initialize_marker = payload.get("initialize_marker", True)
            if not isinstance(initialize_marker, bool):
                issues.append(
                    {
                        "reason": "initialize_marker_boolean_required",
                        "value": initialize_marker,
                    }
                )
            access_policy = payload.get("access_policy")
            if not isinstance(access_policy, dict):
                issues.append({"reason": "filesystem_access_policy_required"})
                access_policy = {}
            elif access_policy.get("mode", "adopt_existing_group") != "adopt_existing_group":
                issues.append(
                    {
                        "reason": "filesystem_access_policy_mode_unsupported",
                        "mode": access_policy.get("mode"),
                    }
                )
            expected_group = access_policy.get("expected_group")
            if expected_group is not None:
                _append_basename_issue(issues, "expected_group", expected_group)
            else:
                issues.append({"reason": "filesystem_access_group_required"})
            expected_mode = access_policy.get("expected_mode", "0770")
            if str(expected_mode) not in {"0750", "0770"}:
                issues.append(
                    {
                        "reason": "filesystem_mode_unsupported_phase12",
                        "mode": expected_mode,
                    }
                )
            users = _validate_string_list(
                issues, "access_policy.users", access_policy.get("users"), required=True
            )
            if users is not None and len(users) < 2:
                issues.append({"reason": "filesystem_users_minimum_two_required"})
            denied_users = access_policy.get("denied_users")
            if denied_users is not None:
                _validate_string_list(
                    issues,
                    "access_policy.denied_users",
                    denied_users,
                    required=False,
                )
            if "quota" in payload:
                issues.extend(_filesystem_quota_issues(payload.get("quota")))

        if operation == OperationKind.FILESYSTEM_CHECK.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            issues.extend(
                _unsupported_payload_issues(
                    payload,
                    PHASE12_FILESYSTEM_CHECK_ALLOWED_PAYLOAD_FIELDS,
                    "filesystem_payload_fields_unsupported_phase12",
                )
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            _append_boolean_payload_issues(
                issues,
                payload,
                ("include_quota", "include_permission", "record_action_required"),
            )

        if operation == OperationKind.FILESYSTEM_SYNC.value:
            existing = self.repository.get_resource(
                request["resource_kind"], request["resource_key"]
            )
            issues.extend(
                _unsupported_payload_issues(
                    payload,
                    PHASE12_FILESYSTEM_SYNC_ALLOWED_PAYLOAD_FIELDS,
                    "filesystem_payload_fields_unsupported_phase12",
                )
            )
            if not existing or existing.get("status") == "Deleted":
                issues.append(
                    {
                        "reason": "filesystem_resource_missing",
                        "resource_key": request["resource_key"],
                    }
                )
            if payload.get("source", "live") != "live":
                issues.append(
                    {
                        "reason": "filesystem_sync_source_unsupported",
                        "source": payload.get("source"),
                    }
                )
            _append_boolean_payload_issues(
                issues,
                payload,
                ("include_quota",),
            )

        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid filesystem Phase 12 request",
            issues=issues,
            error_category="validation",
        )

    def _reject_invalid_filesystem_expiration_sweep_request(
        self, request: dict[str, Any], issues: list[dict[str, Any]]
    ) -> bool:
        payload = request["payload_summary"]
        action = payload.get("action", "block")
        if action != "block":
            issues.append({"reason": "filesystem_sweep_action_unsupported", "action": action})
        dry_run = payload.get("dry_run", False)
        if not isinstance(dry_run, bool):
            issues.append({"reason": "dry_run_boolean_required", "value": dry_run})
        max_targets = payload.get("max_targets", 100)
        try:
            if int(max_targets) <= 0 or int(max_targets) > 1000:
                issues.append({"reason": "max_targets_invalid", "value": max_targets})
        except (TypeError, ValueError):
            issues.append({"reason": "max_targets_invalid", "value": max_targets})
        expired_before = payload.get("expired_before")
        if expired_before is not None:
            try:
                datetime.fromisoformat(str(expired_before).replace("Z", "+00:00"))
            except ValueError:
                issues.append(
                    {
                        "reason": "filesystem_expired_before_invalid",
                        "expired_before": expired_before,
                    }
                )
        scope = payload.get("scope") or {}
        if scope and not isinstance(scope, dict):
            issues.append({"reason": "filesystem_sweep_scope_invalid"})
            scope = {}
        storage_name = scope.get("storage_name")
        if storage_name is not None:
            _append_basename_issue(issues, "storage_name", storage_name)
        resource_type = scope.get("resource_type")
        if resource_type is not None and str(resource_type) not in {
            "user",
            "system",
            "admin",
        }:
            issues.append(
                {
                    "reason": "filesystem_resource_type_unsupported",
                    "resource_type": resource_type,
                }
            )
        if not issues:
            targets = self._resolve_filesystem_expiration_targets(request)
            if len(targets) > int(max_targets):
                issues.append(
                    {
                        "reason": "filesystem_sweep_targets_exceed_max",
                        "target_count": len(targets),
                        "max_targets": int(max_targets),
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid filesystem Phase 11 request",
            issues=issues,
            error_category="validation",
        )

    def _reject_inconsistent_kubernetes_quota_mapping(
        self, request: dict[str, Any]
    ) -> bool:
        if request["resource_kind"] != ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value:
            return False
        payload = request["payload_summary"]
        if request["operation"] == OperationKind.K8S_QUOTA_AUDIT.value:
            return False
        cluster_name = payload.get("cluster_name")
        issues: list[dict[str, Any]] = []
        seen_storage_classes: set[str] = set()
        entries = payload.get("storage_class_quotas") or []
        if self._is_kubernetes_default_reset(request):
            policy, _ = self._kubernetes_default_policy_for_request(request)
            entries = (policy or {}).get("quota", {}).get("storage_class_quotas") or []
        for entry in entries:
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
            if operation == OperationKind.K8S_QUOTA_AUDIT.value:
                scope = payload.get("scope") or {}
                return {scope["storage_name"]} if scope.get("storage_name") else set()
            if self._is_kubernetes_default_reset(request):
                policy, _ = self._kubernetes_default_policy_for_request(request)
                if policy:
                    return {
                        entry["storage_name"]
                        for entry in policy["quota"].get("storage_class_quotas") or []
                        if isinstance(entry, dict) and entry.get("storage_name")
                    }
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

    def _apply_kubernetes_default_quota_reset(
        self, request: dict[str, Any], desired: dict[str, Any]
    ) -> None:
        policy, issues = self._kubernetes_default_policy_for_request(request)
        if issues or not policy:
            return
        quota = dict(policy["quota"])
        storage_class_quotas = quota.pop("storage_class_quotas", None)
        desired["quota"] = quota
        desired["storage_class_quotas"] = list(storage_class_quotas or [])
        desired["resource_type"] = policy["resource_type"]
        desired["reset_quota_to_default"] = True
        desired["default_quota_policy_id"] = policy["policy_id"]

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
            OperationKind.K8S_QUOTA_AUDIT.value,
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

    @staticmethod
    def _is_kubernetes_default_reset(request: dict[str, Any]) -> bool:
        return (
            request["operation"] == OperationKind.K8S_QUOTA_UPDATE.value
            and request["payload_summary"].get("reset_quota_to_default") is True
        )

    def _kubernetes_default_policy_for_request(
        self, request: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        existing = self.repository.get_resource(request["resource_kind"], request["resource_key"])
        resource_type = (
            request["payload_summary"].get("resource_type")
            or (existing or {}).get("desired_state", {}).get("resource_type")
        )
        if not resource_type:
            return None, [{"reason": "resource_type_required_for_default_quota_reset"}]
        policy = self.repository.get_default_quota_policy(
            resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
            resource_type=resource_type,
        )
        if not policy:
            return None, [
                {
                    "reason": "default_quota_policy_missing",
                    "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
                    "resource_type": resource_type,
                }
            ]
        return policy, []

    def _validate_default_policy_storage_entries(
        self, request: dict[str, Any], quota: dict[str, Any]
    ) -> list[dict[str, Any]]:
        target_cluster = request["payload_summary"].get("cluster_name")
        issues: list[dict[str, Any]] = []
        for entry in quota.get("storage_class_quotas") or []:
            if not isinstance(entry, dict) or not entry.get("storage_name"):
                issues.append({"reason": "default_policy_storage_name_missing"})
                continue
            mapping = self.repository.get_storage_mapping(entry["storage_name"])
            if mapping and target_cluster and mapping.get("cluster_name") != target_cluster:
                issues.append(
                    {
                        "reason": "default_policy_storage_mapping_cluster_mismatch",
                        "storage_name": entry["storage_name"],
                        "request_cluster_name": target_cluster,
                        "mapping_cluster_name": mapping.get("cluster_name"),
                    }
                )
            for key in ("requests_storage_bytes", "capacity_bytes", "pvc_count"):
                if entry.get(key) is None:
                    continue
                try:
                    if int(entry[key]) <= 0:
                        issues.append(
                            {
                                "reason": f"default_policy_{key}_invalid",
                                "storage_name": entry["storage_name"],
                                "value": entry[key],
                            }
                        )
                except (TypeError, ValueError):
                    issues.append(
                        {
                            "reason": f"default_policy_{key}_invalid",
                            "storage_name": entry["storage_name"],
                            "value": entry[key],
                        }
                    )
        return issues

    def _reject_invalid_kubernetes_quota_audit_request(
        self, request: dict[str, Any]
    ) -> bool:
        payload = request["payload_summary"]
        issues: list[dict[str, Any]] = []
        scope = payload.get("scope")
        if not isinstance(scope, dict) or not scope:
            issues.append({"reason": "audit_scope_required"})
            scope = {}
        max_targets = payload.get("max_targets", 100)
        try:
            if int(max_targets) <= 0 or int(max_targets) > 1000:
                issues.append({"reason": "max_targets_invalid", "value": max_targets})
        except (TypeError, ValueError):
            issues.append({"reason": "max_targets_invalid", "value": max_targets})
        thresholds = payload.get("usage_thresholds") or {}
        try:
            warning = float(thresholds.get("warning_percent", 80))
            critical = float(thresholds.get("critical_percent", 95))
            if warning <= 0 or critical <= 0 or critical <= warning:
                issues.append(
                    {
                        "reason": "usage_thresholds_invalid",
                        "warning_percent": warning,
                        "critical_percent": critical,
                    }
                )
        except (TypeError, ValueError):
            issues.append({"reason": "usage_thresholds_invalid", "value": thresholds})
        if scope and not issues:
            targets = self._resolve_kubernetes_quota_audit_targets(request)
            if not targets:
                issues.append({"reason": "audit_targets_empty", "scope": scope})
            if len(targets) > int(max_targets):
                issues.append(
                    {
                        "reason": "audit_targets_exceed_max",
                        "target_count": len(targets),
                        "max_targets": int(max_targets),
                    }
                )
        if not issues:
            return False
        return self._reject_planner_issue(
            request,
            message="invalid Kubernetes namespace quota audit request",
            issues=issues,
            error_category="validation",
        )

    def _resolve_kubernetes_quota_audit_targets(
        self, request: dict[str, Any]
    ) -> list[dict[str, Any]]:
        payload = request["payload_summary"]
        scope = payload.get("scope") or {}
        max_targets = int(payload.get("max_targets", 100))
        status_filter = scope.get("status")
        if isinstance(status_filter, str):
            statuses: list[str] | None = [status_filter]
        elif isinstance(status_filter, list):
            statuses = [str(value) for value in status_filter]
        else:
            statuses = None
        resources = self.repository.list_kubernetes_namespace_quota_resources(
            cluster_name=scope.get("cluster_name"),
            namespace_name=scope.get("namespace_name"),
            requester_id=scope.get("requester_id"),
            resource_type=scope.get("resource_type"),
            status=statuses,
            storage_name=scope.get("storage_name"),
            limit=max_targets + 1,
        )
        targets: list[dict[str, Any]] = []
        for resource in resources[:max_targets]:
            desired = resource["desired_state"]
            targets.append(
                {
                    "cluster_name": desired.get("cluster_name"),
                    "namespace_name": desired.get("namespace_name"),
                    "resource_key": resource["resource_key"],
                    "resource_id": resource["resource_id"],
                    "db_exists": True,
                    "resource_status": resource["status"],
                    "resource_type": desired.get("resource_type"),
                    "desired_hard": desired.get("resource_quota_hard") or {},
                    "desired_state": desired,
                }
            )
        if not targets and scope.get("cluster_name") and scope.get("namespace_name"):
            targets.append(
                {
                    "cluster_name": scope["cluster_name"],
                    "namespace_name": scope["namespace_name"],
                    "resource_key": f"{scope['cluster_name']}:{scope['namespace_name']}",
                    "db_exists": False,
                    "desired_hard": {},
                    "desired_state": {},
                }
            )
        return targets


def _observed_quota_used(observed_state: dict[str, Any]) -> dict[str, Any]:
    resource_quota = observed_state.get("resource_quota") or {}
    if resource_quota.get("status_used"):
        return resource_quota["status_used"]
    verification = observed_state.get("pvc_admission_verification") or {}
    after_allowed = verification.get("resource_quota_status_after_allowed_pvc") or {}
    return after_allowed.get("used") or {}


def _filesystem_restore_state(resource: dict[str, Any]) -> dict[str, Any]:
    for section in ("desired_state", "observed_state", "applied_state"):
        state = resource.get(section) or {}
        block_state = state.get("block_state") or {}
        restore = block_state.get("restore") or block_state.get("restore_state")
        if isinstance(restore, dict) and restore:
            return restore
    return {}


def _append_basename_issue(
    issues: list[dict[str, Any]], field_name: str, value: Any
) -> None:
    if not isinstance(value, str) or not value:
        issues.append({"reason": f"{field_name}_missing"})
        return
    if len(value) > 128:
        issues.append(
            {
                "reason": f"{field_name}_too_long",
                "max_length": 128,
                "value_length": len(value),
            }
        )
        return
    try:
        validate_storage_root_basename(field_name, value)
    except ValueError as exc:
        issues.append(
            {
                "reason": f"{field_name}_invalid",
                "message": str(exc),
            }
        )


def _validate_string_list(
    issues: list[dict[str, Any]],
    field_name: str,
    value: Any,
    *,
    required: bool,
) -> list[str] | None:
    if value is None:
        if required:
            issues.append({"reason": f"{field_name}_required"})
        return None
    if not isinstance(value, list):
        issues.append({"reason": f"{field_name}_must_be_list"})
        return None
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            issues.append({"reason": f"{field_name}_entries_must_be_non_empty_strings"})
            return None
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        issues.append({"reason": f"{field_name}_entries_must_be_unique"})
        return None
    return normalized


def _unsupported_payload_issues(
    payload: dict[str, Any],
    allowed_fields: set[str],
    reason: str,
) -> list[dict[str, Any]]:
    unsupported = sorted(field for field in payload if field not in allowed_fields)
    return [{"reason": reason, "fields": unsupported}] if unsupported else []


def _append_boolean_payload_issues(
    issues: list[dict[str, Any]],
    payload: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        if field in payload and not isinstance(payload[field], bool):
            issues.append({"reason": f"{field}_boolean_required", "value": payload[field]})


def _filesystem_quota_issues(quota: Any) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not isinstance(quota, dict):
        return [{"reason": "filesystem_quota_must_be_object"}]
    allowed = {"capacity_bytes", "file_count"}
    unsupported = sorted(field for field in quota if field not in allowed)
    if unsupported:
        issues.append(
            {
                "reason": "filesystem_quota_fields_unsupported_phase12",
                "fields": unsupported,
            }
        )
    if not any(field in quota for field in allowed):
        issues.append({"reason": "filesystem_quota_required"})
    for field in sorted(allowed):
        if field not in quota:
            continue
        value = quota[field]
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            issues.append({"reason": f"filesystem_quota_{field}_invalid", "value": value})
            continue
        if parsed <= 0:
            issues.append({"reason": f"filesystem_quota_{field}_invalid", "value": value})
            continue
        if field == "capacity_bytes" and parsed > MAX_FILESYSTEM_QUOTA_CAPACITY_BYTES:
            issues.append(
                {
                    "reason": "filesystem_quota_capacity_bytes_too_large",
                    "value": value,
                    "max": MAX_FILESYSTEM_QUOTA_CAPACITY_BYTES,
                }
            )
        if field == "file_count" and parsed > MAX_FILESYSTEM_QUOTA_FILE_COUNT:
            issues.append(
                {
                    "reason": "filesystem_quota_file_count_too_large",
                    "value": value,
                    "max": MAX_FILESYSTEM_QUOTA_FILE_COUNT,
                }
            )
    return issues


def _normalized_filesystem_quota(quota: dict[str, Any]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for field in ("capacity_bytes", "file_count"):
        if field in quota:
            normalized[field] = int(quota[field])
    return normalized
