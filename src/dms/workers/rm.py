from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from ._base import *  # noqa: F401,F403


@dataclass
class RMWorkerRuntime:
    repository: DmsRepository
    observability: ObservabilityRepository
    filesystem_adapter: FilesystemBackendAdapter
    kubernetes_adapter: KubernetesNamespaceQuotaAdapter
    worker_id: str
    lease_seconds: int = 300
    recovery_sweep_lease_seconds: int = 30
    backend_registry: BackendAdapterRegistry | None = None

    def run_once(self) -> int:
        # Recovery sweeps are cluster-singleton work: only the leader replica runs them
        # each cycle, so scaling to N workers doesn't repeat the same global cleanup N
        # times. On leader death the short lease expires and another replica takes over.
        if self.repository.try_acquire_leader(
            "recovery-sweeper",
            holder=self.worker_id,
            lease_seconds=self.recovery_sweep_lease_seconds,
        ):
            self.repository.mark_stale_runs(actor=self.worker_id)
            self.repository.close_superseded_preview_runs(actor=self.worker_id)
            self.repository.close_orphaned_stuck_runs(actor=self.worker_id)
        if self.repository.scheduling_blocked():
            return 0
        # Atomic SKIP-LOCKED claim: each replica grabs a DISTINCT oldest plan, so idle
        # workers never contend on the same one (no thundering herd, no wasted attempts).
        try:
            claimed = self.repository.claim_next_plan(
                WorkerRole.RM,
                worker_id=self.worker_id,
                executor_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        except SchedulingBlocked:
            return 0
        if claimed is None:
            return 0
        plan, run_id = claimed
        plan = self.repository.get_plan(plan["plan_id"])
        self.repository.update_run_state(
            run_id,
            LifecycleState.APPLYING,
            reason="rm worker committed applying before backend adapter call",
            actor=self.worker_id,
        )
        try:
            quota_action = self._kubernetes_quota_event_action(plan)
            if quota_action:
                self.observability.safe_record_event(
                    component="rm-worker",
                    severity="INFO",
                    event_type=f"kubernetes_resourcequota_{quota_action}_started",
                    message=f"Kubernetes ResourceQuota {quota_action} started",
                    payload={
                        "plan_id": plan["plan_id"],
                        "run_id": run_id,
                        "cluster_name": plan["desired_state"].get("cluster_name"),
                        "namespace_name": plan["desired_state"].get("namespace_name"),
                        "resource_quota_name": plan["desired_state"].get(
                            "resource_quota_name", "dms-storage-quota"
                        ),
                    },
                    correlation_id=plan["request_id"],
                )
            with RunHeartbeat(
                repository=self.repository,
                observability=self.observability,
                run_id=run_id,
                worker_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            ):
                adapter_result = self._apply(plan)
            self.repository.update_run_state(
                run_id,
                LifecycleState.VERIFYING,
                reason="rm worker verifying live backend state",
                actor=self.worker_id,
            )
            resource_desired_state = adapter_result.applied_state.get(
                "synced_desired_state", plan["desired_state"]
            )
            resource_status = adapter_result.observed_state.get(
                "resource_status",
                (
                    "Deleted"
                    if plan["operation_kind"]
                    in {
                        OperationKind.K8S_QUOTA_DELETE.value,
                        OperationKind.FILESYSTEM_DELETE.value,
                    }
                    else LifecycleState.SUCCEEDED.value
                ),
            )
            if plan["operation_kind"] not in {
                OperationKind.K8S_QUOTA_AUDIT.value,
                OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
                OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value,
            }:
                self.repository.upsert_resource(
                    resource_kind=plan["execution_metadata"]["resource_kind"],
                    resource_key=plan["resource_key"],
                    desired_state=resource_desired_state,
                    applied_state=adapter_result.applied_state,
                    observed_state=adapter_result.observed_state,
                    status=resource_status,
                )
            self.repository.complete_result(
                request_id=plan["request_id"],
                plan_id=plan["plan_id"],
                run_id=run_id,
                terminal_status=LifecycleState.SUCCEEDED,
                message=adapter_result.message,
                verification_summary=adapter_result.observed_state,
                actor=self.worker_id,
            )
            if quota_action:
                self.observability.safe_record_event(
                    component="rm-worker",
                    severity="INFO",
                    event_type=f"kubernetes_resourcequota_{quota_action}_completed",
                    message=f"Kubernetes ResourceQuota {quota_action} completed",
                    payload={
                        "plan_id": plan["plan_id"],
                        "run_id": run_id,
                        "observed_state": adapter_result.observed_state,
                    },
                    correlation_id=plan["request_id"],
                )
            self.observability.safe_record_event(
                component="rm-worker",
                severity="INFO",
                event_type="rm_plan_completed",
                message=adapter_result.message,
                payload={"plan_id": plan["plan_id"], "run_id": run_id},
                correlation_id=plan["request_id"],
            )
        except BackendPreconditionError as exc:
            precondition_issue = _rm_precondition_issue(
                plan["operation_kind"], str(exc)
            )
            self.repository.complete_result(
                request_id=plan["request_id"],
                plan_id=plan["plan_id"],
                run_id=run_id,
                terminal_status=LifecycleState.BACKEND_APPLY_FAILED,
                message=str(exc),
                verification_summary={
                    "backend_side_effect": False,
                    "precondition_failed": True,
                    "issues": [precondition_issue] if precondition_issue else [],
                },
                error_category="backend_precondition",
                actor=self.worker_id,
            )
            self.observability.safe_record_event(
                component="rm-worker",
                severity="WARN",
                event_type="rm_plan_backend_precondition_failed",
                message=str(exc),
                payload={"plan_id": plan["plan_id"], "run_id": run_id},
                correlation_id=plan["request_id"],
            )
        except Exception as exc:
            self.repository.complete_result(
                request_id=plan["request_id"],
                plan_id=plan["plan_id"],
                run_id=run_id,
                terminal_status=LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT,
                message=str(exc),
                verification_summary={"recovery_required": True},
                error_category="backend",
                actor=self.worker_id,
            )
            quota_action = self._kubernetes_quota_event_action(plan)
            if quota_action:
                self.observability.safe_record_event(
                    component="rm-worker",
                    severity="ERROR",
                    event_type=f"kubernetes_resourcequota_{quota_action}_failed",
                    message=str(exc),
                    payload={"plan_id": plan["plan_id"], "run_id": run_id},
                    correlation_id=plan["request_id"],
                )
            raise
        return 1

    def _apply(self, plan: dict[str, Any]) -> Any:
        operation = plan["operation_kind"]
        if operation == OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value:
            return self._apply_filesystem_expiration_sweep(plan)
        if operation == OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value:
            return self._apply_kubernetes_expiration_sweep(plan)
        if operation == OperationKind.FILESYSTEM_CREATE.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.create(plan)
        if operation == OperationKind.FILESYSTEM_UPDATE.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.update(plan)
        if operation == OperationKind.FILESYSTEM_BLOCK.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.block(plan)
        if operation == OperationKind.FILESYSTEM_INITIALIZE.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.initialize(plan)
        if operation == OperationKind.FILESYSTEM_DELETE.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.delete(plan)
        if operation == OperationKind.FILESYSTEM_ASSIGN_QUOTA.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.assign_quota_only(plan)
        if operation == OperationKind.FILESYSTEM_IMPORT.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.import_directory(plan)
        if operation == OperationKind.FILESYSTEM_CHECK.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.consistency_check(plan)
        if operation == OperationKind.FILESYSTEM_SYNC.value:
            filesystem_adapter = self._filesystem_adapter(plan)
            return filesystem_adapter.sync_live_state(plan)
        if operation in {
            OperationKind.K8S_QUOTA_CREATE.value,
            OperationKind.K8S_QUOTA_UPDATE.value,
            OperationKind.K8S_QUOTA_BLOCK.value,
        }:
            kubernetes_adapter = self._kubernetes_adapter(plan)
            return kubernetes_adapter.apply_resource_quota(plan)
        if operation == OperationKind.K8S_QUOTA_DELETE.value:
            kubernetes_adapter = self._kubernetes_adapter(plan)
            return kubernetes_adapter.delete_resource_quota(plan)
        if operation == OperationKind.K8S_QUOTA_SYNC.value:
            kubernetes_adapter = self._kubernetes_adapter(plan)
            return kubernetes_adapter.sync_live_state(plan)
        if operation == OperationKind.K8S_QUOTA_IMPORT.value:
            kubernetes_adapter = self._kubernetes_adapter(plan)
            return kubernetes_adapter.import_resource_quota(plan)
        if operation == OperationKind.K8S_QUOTA_CHECK.value:
            kubernetes_adapter = self._kubernetes_adapter(plan)
            return kubernetes_adapter.check_resource_quota(plan)
        if operation == OperationKind.K8S_QUOTA_AUDIT.value:
            kubernetes_adapter = self._kubernetes_adapter(plan)
            return kubernetes_adapter.audit_resource_quotas(plan)
        raise ValueError(f"unsupported RM operation: {operation}")

    def _apply_filesystem_expiration_sweep(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        dry_run = bool(desired.get("dry_run", False))
        targets = desired.get("targets") or []
        results: list[dict[str, Any]] = []
        blocked_count = 0
        skipped_count = 0
        failed_count = 0
        for target in targets:
            target_result = dict(target)
            skip_reason = self._filesystem_sweep_skip_reason(target)
            if skip_reason:
                target_result.update({"result": "skipped", "reason": skip_reason})
                skipped_count += 1
                results.append(target_result)
                continue
            if dry_run:
                target_result.update(
                    {"result": "would_block", "backend_side_effect": False}
                )
                results.append(target_result)
                continue
            try:
                block_result = self._block_filesystem_sweep_target(plan, target)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - sweep keeps per-target failure evidence.
                target_result.update(
                    {
                        "result": "failed",
                        "reason": _filesystem_sweep_failure_reason(str(exc)),
                        "message": str(exc),
                    }
                )
                failed_count += 1
                results.append(target_result)
                continue
            blocked_count += 1
            target_result.update(
                {
                    "result": "blocked",
                    "backend_side_effect": True,
                    "observed_state": block_result.observed_state,
                }
            )
            results.append(target_result)
        summary = {
            "adapter": "filesystem-expiration-sweep",
            "operation": OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
            "backend_side_effect": not dry_run and blocked_count > 0,
            "dry_run": dry_run,
            "action": desired.get("action", "block"),
            "expired_before": desired.get("expired_before"),
            "target_count": len(targets),
            "blocked_count": blocked_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "targets": results,
            "verified": failed_count == 0,
        }
        return AdapterResult(
            applied_state={"backend_side_effect": summary["backend_side_effect"]},
            observed_state=summary,
            message="Filesystem expiration sweep completed",
        )

    def _filesystem_sweep_skip_reason(self, target: dict[str, Any]) -> str | None:
        resource_key = target.get("resource_key")
        resource_type = target.get("resource_type") or "user"
        if resource_type in {"system", "admin"}:
            return "resource_type_not_auto_blocked"
        block_state = target.get("block_state") or {}
        if target.get(
            "resource_status"
        ) == LifecycleState.BLOCKED.value or block_state.get("blocked"):
            return "already_blocked"
        storage_name = target.get("storage_name")
        if not storage_name:
            return "storage_name_missing"
        mapping = self.repository.get_storage_mapping(storage_name)
        if not mapping:
            return "storage_mapping_missing"
        if (mapping.get("readiness") or {}).get("resource_management") != "Ready":
            return "rm_readiness_not_ready"
        if resource_key:
            active = self.repository.active_work_for_resource(
                resource_kind=ResourceKind.FILESYSTEM.value,
                resource_key=resource_key,
            )
            if active:
                return "resource_has_active_work"
        return None

    def _block_filesystem_sweep_target(
        self, sweep_plan: dict[str, Any], target: dict[str, Any]
    ) -> AdapterResult:
        desired = dict(target.get("desired_state") or {})
        desired["block"] = True
        desired["block_state"] = {
            "blocked": True,
            "block_mode": "permission-zero",
            "reason": sweep_plan["desired_state"].get("reason"),
        }
        desired["operation"] = OperationKind.FILESYSTEM_BLOCK.value
        desired["resource_kind"] = ResourceKind.FILESYSTEM.value
        desired["resource_key"] = target["resource_key"]
        block_plan = dict(sweep_plan)
        block_plan["operation_kind"] = OperationKind.FILESYSTEM_BLOCK.value
        block_plan["resource_key"] = target["resource_key"]
        block_plan["desired_state"] = desired
        block_plan["execution_metadata"] = {
            **sweep_plan.get("execution_metadata", {}),
            "resource_kind": ResourceKind.FILESYSTEM.value,
            "filesystem_backend": {
                "storage_name": desired.get("storage_name"),
                "directory_name": desired.get("directory_name"),
            },
        }
        adapter = self._filesystem_adapter(block_plan)
        result = adapter.block(block_plan)
        self.repository.upsert_resource(
            resource_kind=ResourceKind.FILESYSTEM.value,
            resource_key=target["resource_key"],
            desired_state=result.applied_state.get("synced_desired_state", desired),
            applied_state=result.applied_state,
            observed_state=result.observed_state,
            status=result.observed_state.get(
                "resource_status", LifecycleState.BLOCKED.value
            ),
        )
        return result

    def _apply_kubernetes_expiration_sweep(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        dry_run = bool(desired.get("dry_run", False))
        targets = desired.get("targets") or []
        results: list[dict[str, Any]] = []
        blocked_count = 0
        skipped_count = 0
        failed_count = 0
        for target in targets:
            target_result = dict(target)
            skip_reason = self._kubernetes_sweep_skip_reason(target)
            if skip_reason:
                target_result.update({"result": "skipped", "reason": skip_reason})
                skipped_count += 1
                results.append(target_result)
                continue
            if dry_run:
                target_result.update(
                    {"result": "would_block", "backend_side_effect": False}
                )
                results.append(target_result)
                continue
            try:
                block_result = self._block_kubernetes_sweep_target(plan, target)
            except (
                Exception
            ) as exc:  # noqa: BLE001 - sweep keeps per-target failure evidence.
                target_result.update(
                    {
                        "result": "failed",
                        "reason": _kubernetes_sweep_failure_reason(str(exc)),
                        "message": str(exc),
                    }
                )
                failed_count += 1
                results.append(target_result)
                continue
            blocked_count += 1
            target_result.update(
                {
                    "result": "blocked",
                    "backend_side_effect": True,
                    "observed_state": block_result.observed_state,
                }
            )
            results.append(target_result)
        summary = {
            "adapter": "kubernetes-namespace-quota-expiration-sweep",
            "operation": OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value,
            "backend_side_effect": not dry_run and blocked_count > 0,
            "dry_run": dry_run,
            "action": desired.get("action", "block"),
            "expired_before": desired.get("expired_before"),
            "target_count": len(targets),
            "blocked_count": blocked_count,
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "targets": results,
            "verified": failed_count == 0,
        }
        return AdapterResult(
            applied_state={"backend_side_effect": summary["backend_side_effect"]},
            observed_state=summary,
            message="Kubernetes namespace quota expiration sweep completed",
        )

    def _kubernetes_sweep_skip_reason(self, target: dict[str, Any]) -> str | None:
        resource_key = target.get("resource_key")
        resource_type = target.get("resource_type") or "user"
        if resource_type in {"system", "admin"}:
            return "resource_type_not_auto_blocked"
        block_state = target.get("block_state") or {}
        if target.get(
            "resource_status"
        ) == LifecycleState.BLOCKED.value or block_state.get("blocked"):
            return "already_blocked"
        desired = target.get("desired_state") or {}
        for entry in desired.get("storage_class_quotas") or []:
            storage_name = (
                entry.get("storage_name") if isinstance(entry, dict) else None
            )
            if not storage_name:
                return "storage_name_missing"
            mapping = self.repository.get_storage_mapping(storage_name)
            if not mapping:
                return "storage_mapping_missing"
            if (mapping.get("readiness") or {}).get("resource_management") != "Ready":
                return "rm_readiness_not_ready"
        if resource_key:
            active = self.repository.active_work_for_resource(
                resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
                resource_key=resource_key,
            )
            if active:
                return "resource_has_active_work"
        return None

    def _block_kubernetes_sweep_target(
        self, sweep_plan: dict[str, Any], target: dict[str, Any]
    ) -> AdapterResult:
        desired = dict(target.get("desired_state") or {})
        restore_hard = dict(
            desired.get("resource_quota_hard") or target.get("desired_hard") or {}
        )
        desired["resource_quota_hard"] = zero_kubernetes_resource_quota_hard(
            restore_hard
        )
        desired["block"] = True
        desired["block_state"] = {
            "blocked": True,
            "block_mode": "quota-zero",
            "restore_hard": restore_hard,
            "reason": sweep_plan["desired_state"].get("reason"),
        }
        desired["operation"] = OperationKind.K8S_QUOTA_BLOCK.value
        desired["resource_kind"] = ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value
        desired["resource_key"] = target["resource_key"]
        block_plan = dict(sweep_plan)
        block_plan["operation_kind"] = OperationKind.K8S_QUOTA_BLOCK.value
        block_plan["resource_key"] = target["resource_key"]
        block_plan["desired_state"] = desired
        block_plan["execution_metadata"] = {
            **sweep_plan.get("execution_metadata", {}),
            "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
            "kubernetes_backend": {
                "cluster_name": desired.get("cluster_name"),
                "namespace_name": desired.get("namespace_name"),
                "resource_quota_name": desired.get(
                    "resource_quota_name", "dms-storage-quota"
                ),
            },
        }
        adapter = self._kubernetes_adapter(block_plan)
        result = adapter.apply_resource_quota(block_plan)
        self.repository.upsert_resource(
            resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
            resource_key=target["resource_key"],
            desired_state=result.applied_state.get("synced_desired_state", desired),
            applied_state=result.applied_state,
            observed_state=result.observed_state,
            status=result.observed_state.get(
                "resource_status", LifecycleState.BLOCKED.value
            ),
        )
        return result

    @staticmethod
    def _kubernetes_quota_event_action(plan: dict[str, Any]) -> str | None:
        operation = plan["operation_kind"]
        if operation == OperationKind.K8S_QUOTA_CREATE.value:
            return "apply"
        if operation == OperationKind.K8S_QUOTA_UPDATE.value:
            return "update"
        if operation == OperationKind.K8S_QUOTA_DELETE.value:
            return "delete"
        if operation == OperationKind.K8S_QUOTA_SYNC.value:
            return "sync"
        if operation == OperationKind.K8S_QUOTA_CHECK.value:
            return "consistency_check"
        if operation == OperationKind.K8S_QUOTA_AUDIT.value:
            return "audit"
        if operation == OperationKind.K8S_QUOTA_IMPORT.value:
            return "import"
        if operation == OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value:
            return "expiration_sweep"
        if operation == OperationKind.K8S_QUOTA_BLOCK.value:
            return "block" if plan["desired_state"].get("block") else "unblock"
        return None

    def _filesystem_adapter(self, plan: dict[str, Any]) -> FilesystemBackendAdapter:
        with self._adapter_build_preconditions():
            if self.backend_registry:
                return self.backend_registry.filesystem_for_plan(plan)
            return self.filesystem_adapter

    def _kubernetes_adapter(
        self, plan: dict[str, Any]
    ) -> KubernetesNamespaceQuotaAdapter:
        with self._adapter_build_preconditions():
            if self.backend_registry:
                return self.backend_registry.kubernetes_for_plan(plan)
            return self.kubernetes_adapter

    @contextmanager
    def _adapter_build_preconditions(self) -> Iterator[None]:
        # Building a backend adapter validates configuration/identity prerequisites
        # (e.g. LDAP bind credentials for group management). Such failures occur before
        # any backend side effect, so surface them as BackendPreconditionError -> the
        # request is classified BackendApplyFailed(precondition) instead of falling
        # through to the generic handler, which would mark it UnknownAfterSideEffect and
        # trigger an unnecessary manual recovery flow.
        try:
            yield
        except IdentityLookupConfigurationError as exc:
            raise BackendPreconditionError(str(exc)) from exc


def _filesystem_sweep_failure_reason(message: str) -> str:
    lowered = message.lower()
    if "marker" in lowered and "mismatch" in lowered:
        return "filesystem_marker_mismatch"
    if "restore" in lowered:
        return "filesystem_block_restore_missing"
    if "group" in lowered:
        return "filesystem_access_group_missing"
    return "filesystem_block_failed"


def _kubernetes_sweep_failure_reason(message: str) -> str:
    lowered = message.lower()
    if "resourcequota" in lowered and (
        "does not exist" in lowered or "not found" in lowered
    ):
        return "kubernetes_quota_missing"
    if "non-dms" in lowered or "managed" in lowered:
        return "kubernetes_quota_metadata_drift"
    if "restore" in lowered:
        return "kubernetes_quota_block_restore_missing"
    return "kubernetes_quota_block_failed"


def _rm_precondition_issue(operation: str, message: str) -> dict[str, Any] | None:
    lowered = message.lower()
    if "unsupported" in lowered and "backend" in lowered:
        return {
            "issue_type": "unsupported_backend",
            "reason": "unsupported_backend",
            "message": message,
        }
    if operation == OperationKind.K8S_QUOTA_IMPORT.value:
        # Import is DB-only; a precondition refusal (non-DMS ResourceQuota, missing
        # RQ, unmappable StorageClass keys) carries no side effect.
        return {
            "issue_type": "kubernetes_quota_import_preflight_failed",
            "reason": "kubernetes_quota_import_preflight_failed",
            "message": message,
        }
    if not operation.startswith("filesystem."):
        return None
    if operation == OperationKind.FILESYSTEM_IMPORT.value:
        issue_type = "filesystem_import_preflight_failed"
    elif operation == OperationKind.FILESYSTEM_ASSIGN_QUOTA.value:
        issue_type = "filesystem_assign_quota_failed"
    elif "resolvable" in lowered and "user" in lowered:
        # owner resolution precondition (e.g. create: requester not a resolvable
        # POSIX/LDAP user). Not a block — label by the actual failure.
        issue_type = "filesystem_owner_unresolved"
    elif "quota" in lowered:
        issue_type = "filesystem_quota_apply_failed"
    elif "marker" in lowered:
        issue_type = "filesystem_marker_mismatch"
    elif "group" in lowered:
        issue_type = "filesystem_access_group_unresolved"
    elif operation == OperationKind.FILESYSTEM_CREATE.value:
        issue_type = "filesystem_create_failed"
    elif operation in {
        OperationKind.FILESYSTEM_BLOCK.value,
        OperationKind.FILESYSTEM_INITIALIZE.value,
    }:
        issue_type = "filesystem_block_failed"
    else:
        issue_type = "filesystem_operation_failed"
    return {"issue_type": issue_type, "reason": issue_type, "message": message}

