from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Any

from .adapters import (
    AdapterResult,
    BackendPreconditionError,
    FilesystemBackendAdapter,
    KubernetesNamespaceQuotaAdapter,
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
    StubVolcanoAdapter,
    zero_kubernetes_resource_quota_hard,
)
from .backend_registry import BackendAdapterRegistry
from .domain import DataJobState, LifecycleState, OperationKind, ResourceKind, WorkerRole
from .repositories import DmsRepository, ObservabilityRepository, SchedulingBlocked, iso_at


class RunHeartbeat:
    def __init__(
        self,
        *,
        repository: DmsRepository,
        observability: ObservabilityRepository,
        run_id: str,
        worker_id: str,
        lease_seconds: int,
        interval_seconds: float | None = None,
    ) -> None:
        self.repository = repository
        self.observability = observability
        self.run_id = run_id
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds
        self.interval_seconds = (
            interval_seconds
            if interval_seconds is not None
            else max(0.1, min(60.0, lease_seconds / 3))
        )
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> RunHeartbeat:
        if self.lease_seconds <= 0:
            return self
        self._thread = threading.Thread(
            target=self._run,
            name=f"dms-run-heartbeat-{self.run_id}",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=min(5.0, self.interval_seconds))

    def _run(self) -> None:
        while not self._stop.wait(self.interval_seconds):
            try:
                self.repository.heartbeat_run(self.run_id, self.lease_seconds)
            except Exception as exc:  # noqa: BLE001 - heartbeat must not fail backend work.
                self.observability.safe_record_event(
                    component="worker-heartbeat",
                    severity="WARN",
                    event_type="run_heartbeat_failed",
                    message=str(exc),
                    payload={"run_id": self.run_id, "worker_id": self.worker_id},
                )


@dataclass
class RMWorkerRuntime:
    repository: DmsRepository
    observability: ObservabilityRepository
    filesystem_adapter: FilesystemBackendAdapter
    kubernetes_adapter: KubernetesNamespaceQuotaAdapter
    worker_id: str
    lease_seconds: int = 300
    backend_registry: BackendAdapterRegistry | None = None

    def run_once(self) -> int:
        self.repository.mark_stale_runs(actor=self.worker_id)
        if self.repository.scheduling_blocked():
            return 0
        plans = self.repository.list_claimable_plans(WorkerRole.RM, limit=1)
        if not plans:
            return 0
        plan = plans[0]
        try:
            run_id = self.repository.claim_plan(
                plan_id=plan["plan_id"],
                worker_id=self.worker_id,
                executor_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        except SchedulingBlocked:
            return 0
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
                "Deleted"
                if plan["operation_kind"] == OperationKind.K8S_QUOTA_DELETE.value
                else LifecycleState.SUCCEEDED.value,
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
            precondition_issue = _rm_precondition_issue(plan["operation_kind"], str(exc))
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
                target_result.update({"result": "would_block", "backend_side_effect": False})
                results.append(target_result)
                continue
            try:
                block_result = self._block_filesystem_sweep_target(plan, target)
            except Exception as exc:  # noqa: BLE001 - sweep keeps per-target failure evidence.
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
        if target.get("resource_status") == LifecycleState.BLOCKED.value or block_state.get(
            "blocked"
        ):
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
            status=result.observed_state.get("resource_status", LifecycleState.BLOCKED.value),
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
                target_result.update({"result": "would_block", "backend_side_effect": False})
                results.append(target_result)
                continue
            try:
                block_result = self._block_kubernetes_sweep_target(plan, target)
            except Exception as exc:  # noqa: BLE001 - sweep keeps per-target failure evidence.
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
        if target.get("resource_status") == LifecycleState.BLOCKED.value or block_state.get(
            "blocked"
        ):
            return "already_blocked"
        desired = target.get("desired_state") or {}
        for entry in desired.get("storage_class_quotas") or []:
            storage_name = entry.get("storage_name") if isinstance(entry, dict) else None
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
        desired["resource_quota_hard"] = zero_kubernetes_resource_quota_hard(restore_hard)
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
            status=result.observed_state.get("resource_status", LifecycleState.BLOCKED.value),
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
        if self.backend_registry:
            return self.backend_registry.filesystem_for_plan(plan)
        return self.filesystem_adapter

    def _kubernetes_adapter(self, plan: dict[str, Any]) -> KubernetesNamespaceQuotaAdapter:
        if self.backend_registry:
            return self.backend_registry.kubernetes_for_plan(plan)
        return self.kubernetes_adapter


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
    if "resourcequota" in lowered and ("does not exist" in lowered or "not found" in lowered):
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
    if not operation.startswith("filesystem."):
        return None
    if operation == OperationKind.FILESYSTEM_IMPORT.value:
        issue_type = "filesystem_import_preflight_failed"
    elif operation == OperationKind.FILESYSTEM_ASSIGN_QUOTA.value:
        issue_type = "filesystem_assign_quota_failed"
    elif "quota" in lowered:
        issue_type = "filesystem_quota_apply_failed"
    elif "marker" in lowered:
        issue_type = "filesystem_marker_mismatch"
    elif "group" in lowered:
        issue_type = "filesystem_access_group_unresolved"
    else:
        issue_type = "filesystem_block_failed"
    return {"issue_type": issue_type, "reason": issue_type, "message": message}


@dataclass
class DMWorkerRuntime:
    repository: DmsRepository
    observability: ObservabilityRepository
    volcano_adapter: StubVolcanoAdapter
    worker_id: str
    lease_seconds: int = 300
    preview_ttl_seconds: int = 24 * 60 * 60

    def run_once(self) -> int:
        self.repository.mark_stale_runs(actor=self.worker_id)
        if self.repository.scheduling_blocked():
            return 0
        plans = self.repository.list_claimable_plans(WorkerRole.DM, limit=1)
        if not plans:
            return 0
        plan = plans[0]
        try:
            run_id = self.repository.claim_plan(
                plan_id=plan["plan_id"],
                worker_id=self.worker_id,
                executor_id=self.worker_id,
                lease_seconds=self.lease_seconds,
            )
        except SchedulingBlocked:
            return 0
        plan = self.repository.get_plan(plan["plan_id"])
        job = self.repository.get_data_job(plan["execution_metadata"]["job_id"])
        self.repository.update_run_state(
            run_id,
            LifecycleState.RUNNING,
            reason="dm worker committed running before data-operation adapter call",
            actor=self.worker_id,
        )
        with RunHeartbeat(
            repository=self.repository,
            observability=self.observability,
            run_id=run_id,
            worker_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        ):
            if (
                self._requires_preview(plan)
                and plan["execution_metadata"].get("phase") == "preview"
            ):
                self._run_preview_phase(plan, run_id, job)
                return 1
            self._run_execution_phase(plan, run_id, job)
        return 1

    def _run_preview_phase(
        self, plan: dict[str, Any], run_id: str, job: dict[str, Any]
    ) -> None:
        selected_tool = self._select_tool(job["operation"])
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.PREFLIGHT_RUNNING,
            selected_tool=selected_tool,
        )
        self.repository.update_data_job(job["job_id"], state=DataJobState.PREVIEW_RUNNING)
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.PREVIEW_SUCCEEDED,
            artifact_uri=f"stub://preview/{job['job_id']}",
        )
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.CONFIRM_PENDING,
            preview_expires_at=iso_at(self.preview_ttl_seconds),
        )
        metadata = dict(plan["execution_metadata"])
        metadata["phase"] = "awaiting-confirm"
        self.repository.update_plan_metadata(plan["plan_id"], metadata)
        self.repository.update_run_state(
            run_id,
            LifecycleState.BLOCKED,
            reason="preview succeeded; waiting for user confirm in data_jobs",
            actor=self.worker_id,
        )
        self.observability.safe_record_event(
            component="dm-worker",
            severity="INFO",
            event_type="data_job_preview_ready",
            message="data job preview completed and confirm is pending",
            payload={"job_id": job["job_id"], "plan_id": plan["plan_id"]},
            correlation_id=plan["request_id"],
        )

    def _run_execution_phase(
        self, plan: dict[str, Any], run_id: str, job: dict[str, Any]
    ) -> None:
        selected_tool = job["selected_tool"] or self._select_tool(job["operation"])
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.PREFLIGHT_RUNNING,
            selected_tool=selected_tool,
        )
        self.repository.update_data_job(job["job_id"], state=DataJobState.SCHEDULED)
        self.repository.update_data_job(job["job_id"], state=DataJobState.RUNNING)
        adapter_result = self.volcano_adapter.create_job(plan, self.repository.get_data_job(job["job_id"]))
        self.repository.update_run_state(
            run_id,
            LifecycleState.VERIFYING,
            reason="dm worker verifying Volcano job result",
            actor=self.worker_id,
        )
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.SUCCEEDED,
            artifact_uri=adapter_result.artifact_uri,
        )
        self.repository.upsert_resource(
            resource_kind=ResourceKind.DATA_JOB.value,
            resource_key=job["job_id"],
            desired_state=plan["desired_state"],
            applied_state=adapter_result.applied_state,
            observed_state=adapter_result.observed_state,
            status=LifecycleState.SUCCEEDED.value,
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
        self.observability.safe_record_event(
            component="dm-worker",
            severity="INFO",
            event_type="data_job_completed",
            message=adapter_result.message,
            payload={"job_id": job["job_id"], "plan_id": plan["plan_id"]},
            correlation_id=plan["request_id"],
        )

    @staticmethod
    def _requires_preview(plan: dict[str, Any]) -> bool:
        return plan["operation_kind"] in {
            OperationKind.DATA_SYNC.value,
            OperationKind.DATA_RM.value,
        }

    @staticmethod
    def _select_tool(operation: str) -> str:
        return {
            OperationKind.DATA_SYNC.value: "dsync",
            OperationKind.DATA_RM.value: "drm",
            OperationKind.DATA_SCAN.value: "dscan",
        }[operation]


def confirm_data_job(repository: DmsRepository, job_id: str, actor: str) -> None:
    job = repository.get_data_job(job_id)
    if job["state"] != DataJobState.CONFIRM_PENDING.value:
        raise ValueError("data job is not waiting for confirm")
    plan = repository.get_plan_by_request(job["request_id"])
    if not plan:
        raise ValueError("data job has no plan")
    repository.update_data_job(job_id, state=DataJobState.CONFIRMED)
    metadata = dict(plan["execution_metadata"])
    metadata["phase"] = "execution"
    repository.update_plan_metadata(plan["plan_id"], metadata)
    repository.update_plan_status(
        plan["plan_id"],
        LifecycleState.PLANNED,
        reason="data job confirmed; execution can be claimed",
        actor=actor,
    )
    repository.update_request_status(
        job["request_id"],
        LifecycleState.PLANNED,
        reason="data job confirmed; common lifecycle leaves blocked state",
        actor=actor,
    )


def cancel_data_job(
    repository: DmsRepository,
    volcano_adapter: StubVolcanoAdapter,
    job_id: str,
    actor: str,
) -> None:
    job = repository.get_data_job(job_id)
    repository.update_data_job(job_id, state=DataJobState.CANCELLED)
    plan = repository.get_plan_by_request(job["request_id"])
    if plan:
        repository.complete_result(
            request_id=job["request_id"],
            plan_id=plan["plan_id"],
            run_id=None,
            terminal_status=LifecycleState.CANCELLED,
            message="data job cancelled",
            verification_summary={"backend_side_effect": False},
            actor=actor,
        )
    if job["artifact_uri"] and job["artifact_uri"].startswith("volcano/"):
        volcano_adapter.terminate_job(job["artifact_uri"])
