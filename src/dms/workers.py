from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import (
    FilesystemBackendAdapter,
    KubernetesNamespaceQuotaAdapter,
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
    StubVolcanoAdapter,
)
from .backend_registry import BackendAdapterRegistry
from .domain import DataJobState, LifecycleState, OperationKind, ResourceKind, WorkerRole
from .repositories import DmsRepository, ObservabilityRepository, iso_at


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
        plans = self.repository.list_claimable_plans(WorkerRole.RM, limit=1)
        if not plans:
            return 0
        plan = plans[0]
        run_id = self.repository.claim_plan(
            plan_id=plan["plan_id"],
            worker_id=self.worker_id,
            executor_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
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
                self.observability.record_event(
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
                self.observability.record_event(
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
            self.observability.record_event(
                component="rm-worker",
                severity="INFO",
                event_type="rm_plan_completed",
                message=adapter_result.message,
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
                self.observability.record_event(
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
        filesystem_adapter = self._filesystem_adapter(plan)
        kubernetes_adapter = self._kubernetes_adapter(plan)
        if operation == OperationKind.FILESYSTEM_CREATE.value:
            return filesystem_adapter.create(plan)
        if operation == OperationKind.FILESYSTEM_UPDATE.value:
            return filesystem_adapter.update(plan)
        if operation == OperationKind.FILESYSTEM_BLOCK.value:
            return filesystem_adapter.block(plan)
        if operation == OperationKind.FILESYSTEM_INITIALIZE.value:
            return filesystem_adapter.initialize(plan)
        if operation == OperationKind.FILESYSTEM_DELETE.value:
            return filesystem_adapter.delete(plan)
        if operation == OperationKind.FILESYSTEM_ASSIGN_QUOTA.value:
            return filesystem_adapter.assign_quota_only(plan)
        if operation == OperationKind.FILESYSTEM_IMPORT.value:
            return filesystem_adapter.import_directory(plan)
        if operation == OperationKind.FILESYSTEM_CHECK.value:
            return filesystem_adapter.consistency_check(plan)
        if operation in {
            OperationKind.K8S_QUOTA_CREATE.value,
            OperationKind.K8S_QUOTA_UPDATE.value,
            OperationKind.K8S_QUOTA_BLOCK.value,
        }:
            return kubernetes_adapter.apply_resource_quota(plan)
        if operation == OperationKind.K8S_QUOTA_DELETE.value:
            return kubernetes_adapter.delete_resource_quota(plan)
        if operation == OperationKind.K8S_QUOTA_SYNC.value:
            return kubernetes_adapter.sync_live_state(plan)
        if operation == OperationKind.K8S_QUOTA_CHECK.value:
            return kubernetes_adapter.check_resource_quota(plan)
        raise ValueError(f"unsupported RM operation: {operation}")

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
        plans = self.repository.list_claimable_plans(WorkerRole.DM, limit=1)
        if not plans:
            return 0
        plan = plans[0]
        run_id = self.repository.claim_plan(
            plan_id=plan["plan_id"],
            worker_id=self.worker_id,
            executor_id=self.worker_id,
            lease_seconds=self.lease_seconds,
        )
        plan = self.repository.get_plan(plan["plan_id"])
        job = self.repository.get_data_job(plan["execution_metadata"]["job_id"])
        self.repository.update_run_state(
            run_id,
            LifecycleState.RUNNING,
            reason="dm worker committed running before data-operation adapter call",
            actor=self.worker_id,
        )
        if self._requires_preview(plan) and plan["execution_metadata"].get("phase") == "preview":
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
        self.observability.record_event(
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
        self.observability.record_event(
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
