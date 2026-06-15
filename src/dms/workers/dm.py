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
from ._base import (  # noqa: F401
    _adapter_nsync_enabled,
    _any_ready,
    _artifact_child_uri,
    _artifact_requires_local_parse,
    _clamp_policy_count,
    _default_mpi_metadata_uris,
    _filesystem_sweep_failure_reason,
    _first_selected_node,
    _identity_mapping_summary,
    _identity_ready,
    _is_expired,
    _kubernetes_sweep_failure_reason,
    _mount_ready,
    _mutation_artifact_summary,
    _mutation_result_summary,
    _normalize_scan_summary,
    _phase21_minimal_resource_model,
    _phase21_result_resource_evidence,
    _ready_mount,
    _resolve_data_job_resource_model,
    _resource_shortage_model,
    _rm_precondition_issue,
    _scan_artifact_summary,
    _scan_candidate_rejection_reason,
    _scan_result_summary,
    _scheduled_nodes_from_pod_summary,
    _summary_fingerprint,
    _sync_dsync_candidate_rejection_reason,
    _tool_ready,
    _unique_candidate_nodes,
    _verify_data_runtime_preflight,
    _verify_scan_runtime_preflight,
    _volcano_job_ref,
)


@dataclass
class DMWorkerRuntime:
    repository: DmsRepository
    observability: ObservabilityRepository
    volcano_adapter: Any
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
        preflight = self._mutation_preflight(plan, job)
        selected_tool = preflight.get("selected_tool") or self._select_tool(
            job["operation"]
        )
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.PREFLIGHT_RUNNING,
            selected_tool=selected_tool,
        )
        self.repository.update_data_job(job["job_id"], preflight_result=preflight)
        if preflight["status"] != "Ready":
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.PREFLIGHT_FAILED,
                terminal_status=LifecycleState.REJECTED,
                message="data mutation preflight rejected request",
                error_category="data_management_preflight",
                summary={
                    "backend_side_effect": False,
                    "reason": preflight.get("reason"),
                    "preflight_result": preflight,
                },
            )
            return
        self.repository.update_data_job(
            job["job_id"],
            worker_pool={
                **(job.get("worker_pool") or {}),
                **(preflight.get("worker_pool") or {}),
            },
        )
        job = self.repository.get_data_job(job["job_id"])
        runtime_preflight = _verify_data_runtime_preflight(
            self.volcano_adapter, plan, job, preflight, phase="preview"
        )
        preflight = {**preflight, "runtime_permission_check": runtime_preflight}
        if runtime_preflight.get("status") != "Ready":
            preflight = {
                **preflight,
                "status": "Rejected",
                "reason": runtime_preflight.get("reason") or "runtime_preflight_failed",
            }
            self.repository.update_data_job(job["job_id"], preflight_result=preflight)
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.PREFLIGHT_FAILED,
                terminal_status=LifecycleState.REJECTED,
                message="data mutation runtime preflight rejected request",
                error_category="data_management_preflight",
                summary={
                    "backend_side_effect": False,
                    "reason": preflight.get("reason"),
                    "preflight_result": preflight,
                },
            )
            return
        self.repository.update_data_job(job["job_id"], preflight_result=preflight)
        self.repository.update_data_job(
            job["job_id"], state=DataJobState.PREVIEW_RUNNING
        )
        try:
            adapter_result = self.volcano_adapter.create_job(
                plan, self.repository.get_data_job(job["job_id"])
            )
        except Exception as exc:  # noqa: BLE001
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.FAILED,
                terminal_status=LifecycleState.FAILED,
                message=f"data mutation preview Volcano submission failed: {exc}",
                error_category="data_management_preview",
                summary={
                    "backend_side_effect": False,
                    "reason": "preview_volcano_submission_failed",
                    "message": str(exc),
                },
            )
            return
        volcano_job_ref = _volcano_job_ref(adapter_result)
        if volcano_job_ref:
            self.repository.update_data_job(
                job["job_id"], volcano_job_ref={"preview": volcano_job_ref}
            )
        if adapter_result.observed_state.get("phase") != "Succeeded":
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.FAILED,
                terminal_status=LifecycleState.FAILED,
                message=adapter_result.message,
                error_category="data_management_preview",
                summary={
                    "backend_side_effect": False,
                    "reason": "data_job_preview_failed",
                    "observed_state": adapter_result.observed_state,
                    "volcano_job_ref": volcano_job_ref,
                },
            )
            return
        result_summary = _mutation_result_summary(
            plan=plan,
            job=self.repository.get_data_job(job["job_id"]),
            adapter_result=adapter_result,
            preflight=preflight,
            phase="preview",
        )
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.PREVIEW_SUCCEEDED,
            artifact_uri=adapter_result.artifact_uri,
            result_summary=result_summary,
            log_uri=result_summary.get("preview", {}).get("stdout_uri"),
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
        if job["operation"] != OperationKind.DATA_SCAN.value:
            self._run_mutation_execution_phase(plan, run_id, job)
            return
        selected_tool = job["selected_tool"] or self._select_tool(job["operation"])
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.PREFLIGHT_RUNNING,
            selected_tool=selected_tool,
        )
        job = self.repository.get_data_job(job["job_id"])
        preflight = self._scan_preflight(plan, job)
        self.repository.update_data_job(job["job_id"], preflight_result=preflight)
        if preflight["status"] != "Ready":
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.PREFLIGHT_FAILED,
                terminal_status=LifecycleState.REJECTED,
                message="data scan preflight rejected request",
                error_category="data_management_preflight",
                summary={
                    "backend_side_effect": False,
                    "reason": preflight.get("reason"),
                    "preflight_result": preflight,
                },
            )
            return
        self.repository.update_data_job(
            job["job_id"],
            worker_pool={
                **(job.get("worker_pool") or {}),
                **(preflight.get("worker_pool") or {}),
            },
        )
        job = self.repository.get_data_job(job["job_id"])
        runtime_preflight = _verify_scan_runtime_preflight(
            self.volcano_adapter, plan, job, preflight
        )
        preflight = {**preflight, "runtime_permission_check": runtime_preflight}
        if runtime_preflight.get("status") != "Ready":
            preflight = {
                **preflight,
                "status": "Rejected",
                "reason": runtime_preflight.get("reason") or "runtime_preflight_failed",
            }
            self.repository.update_data_job(job["job_id"], preflight_result=preflight)
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.PREFLIGHT_FAILED,
                terminal_status=LifecycleState.REJECTED,
                message="data scan runtime preflight rejected request",
                error_category="data_management_preflight",
                summary={
                    "backend_side_effect": False,
                    "reason": preflight.get("reason"),
                    "preflight_result": preflight,
                },
            )
            return
        self.repository.update_data_job(job["job_id"], preflight_result=preflight)
        self.repository.update_data_job(job["job_id"], state=DataJobState.SCHEDULED)
        self.repository.update_data_job(job["job_id"], state=DataJobState.RUNNING)
        try:
            adapter_result = self.volcano_adapter.create_job(
                plan, self.repository.get_data_job(job["job_id"])
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - runtime failure must close the data job.
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.FAILED,
                terminal_status=LifecycleState.FAILED,
                message=f"data scan Volcano submission failed: {exc}",
                error_category="data_management_volcano",
                summary={
                    "backend_side_effect": False,
                    "reason": "volcano_submission_failed",
                    "message": str(exc),
                },
            )
            return
        volcano_job_ref = _volcano_job_ref(adapter_result)
        if volcano_job_ref:
            self.repository.update_data_job(
                job["job_id"], volcano_job_ref=volcano_job_ref
            )
        self.repository.update_run_state(
            run_id,
            LifecycleState.VERIFYING,
            reason="dm worker verifying Volcano job result",
            actor=self.worker_id,
        )
        observed_phase = adapter_result.observed_state.get("phase")
        if observed_phase != "Succeeded":
            timed_out = observed_phase == "TimedOut"
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.TIMED_OUT if timed_out else DataJobState.FAILED,
                terminal_status=(
                    LifecycleState.TIMED_OUT if timed_out else LifecycleState.FAILED
                ),
                message=adapter_result.message,
                error_category="data_management_volcano",
                summary={
                    "backend_side_effect": True,
                    "reason": (
                        "volcano_job_timed_out"
                        if timed_out
                        else "volcano_job_not_succeeded"
                    ),
                    "observed_state": adapter_result.observed_state,
                    "volcano_job_ref": volcano_job_ref or {},
                },
            )
            return
        try:
            result_summary = _scan_result_summary(
                plan=plan,
                job=self.repository.get_data_job(job["job_id"]),
                adapter_result=adapter_result,
                preflight=preflight,
            )
        except DataManagementRuntimeError as exc:
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.FAILED,
                terminal_status=LifecycleState.FAILED,
                message=f"data scan artifact parsing failed: {exc}",
                error_category="data_management_artifact",
                summary={
                    "backend_side_effect": True,
                    "reason": "data_job_artifact_parse_failed",
                    "message": str(exc),
                    "artifact_uri": adapter_result.artifact_uri,
                    "observed_state": adapter_result.observed_state,
                    "volcano_job_ref": volcano_job_ref or {},
                },
            )
            return
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.SUCCEEDED,
            artifact_uri=adapter_result.artifact_uri,
            result_summary=result_summary,
            log_uri=result_summary.get("stdout_uri"),
        )
        self.repository.upsert_resource(
            resource_kind=ResourceKind.DATA_JOB.value,
            resource_key=job["job_id"],
            desired_state=plan["desired_state"],
            applied_state=adapter_result.applied_state,
            observed_state={
                **adapter_result.observed_state,
                "result_summary": result_summary,
            },
            status=LifecycleState.SUCCEEDED.value,
        )
        self.repository.complete_result(
            request_id=plan["request_id"],
            plan_id=plan["plan_id"],
            run_id=run_id,
            terminal_status=LifecycleState.SUCCEEDED,
            message=adapter_result.message,
            verification_summary={
                **adapter_result.observed_state,
                "preflight_result": preflight,
                "result_summary": result_summary,
            },
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

    def _run_mutation_execution_phase(
        self, plan: dict[str, Any], run_id: str, job: dict[str, Any]
    ) -> None:
        selected_tool = job["selected_tool"] or self._select_tool(job["operation"])
        self.repository.update_data_job(
            job["job_id"], state=DataJobState.CONFIRMED, selected_tool=selected_tool
        )
        preflight = self._mutation_preflight(plan, job)
        preflight = {**preflight, "execution_recheck": True}
        if preflight["status"] != "Ready":
            self.repository.update_data_job(job["job_id"], preflight_result=preflight)
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.PREFLIGHT_FAILED,
                terminal_status=LifecycleState.REJECTED,
                message="confirmed data mutation preflight rejected request",
                error_category="data_management_preflight",
                summary={
                    "backend_side_effect": False,
                    "reason": preflight.get("reason"),
                    "preflight_result": preflight,
                },
            )
            return
        self.repository.update_data_job(job["job_id"], preflight_result=preflight)
        self.repository.update_data_job(
            job["job_id"],
            worker_pool={
                **(job.get("worker_pool") or {}),
                **(preflight.get("worker_pool") or {}),
            },
        )
        job = self.repository.get_data_job(job["job_id"])
        runtime_preflight = _verify_data_runtime_preflight(
            self.volcano_adapter, plan, job, preflight, phase="execution"
        )
        preflight = {
            **preflight,
            "runtime_permission_check_execution": runtime_preflight,
        }
        if runtime_preflight.get("status") != "Ready":
            self.repository.update_data_job(job["job_id"], preflight_result=preflight)
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.PREFLIGHT_FAILED,
                terminal_status=LifecycleState.REJECTED,
                message="confirmed data mutation runtime preflight rejected request",
                error_category="data_management_preflight",
                summary={
                    "backend_side_effect": False,
                    "reason": runtime_preflight.get("reason"),
                    "preflight_result": preflight,
                },
            )
            return
        self.repository.update_data_job(job["job_id"], preflight_result=preflight)
        self.repository.update_data_job(job["job_id"], state=DataJobState.SCHEDULED)
        self.repository.update_data_job(job["job_id"], state=DataJobState.RUNNING)
        try:
            adapter_result = self.volcano_adapter.create_job(
                plan, self.repository.get_data_job(job["job_id"])
            )
        except Exception as exc:  # noqa: BLE001
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.FAILED,
                terminal_status=LifecycleState.FAILED,
                message=f"data mutation Volcano submission failed: {exc}",
                error_category="data_management_mutation",
                summary={
                    "backend_side_effect": False,
                    "reason": "mutation_volcano_submission_failed",
                    "message": str(exc),
                },
            )
            return
        volcano_job_ref = _volcano_job_ref(adapter_result)
        existing_refs = (
            (job.get("volcano_job_ref") or {})
            if isinstance(job.get("volcano_job_ref"), dict)
            else {}
        )
        if volcano_job_ref:
            self.repository.update_data_job(
                job["job_id"],
                volcano_job_ref={**existing_refs, "execution": volcano_job_ref},
            )
        observed_phase = adapter_result.observed_state.get("phase")
        if observed_phase != "Succeeded":
            timed_out = observed_phase == "TimedOut"
            self._fail_data_job(
                plan,
                run_id,
                job,
                state=DataJobState.TIMED_OUT if timed_out else DataJobState.FAILED,
                terminal_status=(
                    LifecycleState.TIMED_OUT if timed_out else LifecycleState.FAILED
                ),
                message=adapter_result.message,
                error_category="data_management_mutation",
                summary={
                    "backend_side_effect": True,
                    "reason": (
                        "data_job_mutation_timed_out"
                        if timed_out
                        else "data_job_mutation_failed"
                    ),
                    "observed_state": adapter_result.observed_state,
                    "volcano_job_ref": volcano_job_ref,
                },
            )
            return
        result_summary = _mutation_result_summary(
            plan=plan,
            job=self.repository.get_data_job(job["job_id"]),
            adapter_result=adapter_result,
            preflight=preflight,
            phase="execution",
        )
        self.repository.update_data_job(
            job["job_id"],
            state=DataJobState.SUCCEEDED,
            artifact_uri=adapter_result.artifact_uri,
            result_summary=result_summary,
            log_uri=result_summary.get("execution", {}).get("stdout_uri"),
        )
        self.repository.upsert_resource(
            resource_kind=ResourceKind.DATA_JOB.value,
            resource_key=job["job_id"],
            desired_state=plan["desired_state"],
            applied_state=adapter_result.applied_state,
            observed_state={
                **adapter_result.observed_state,
                "result_summary": result_summary,
            },
            status=LifecycleState.SUCCEEDED.value,
        )
        self.repository.complete_result(
            request_id=plan["request_id"],
            plan_id=plan["plan_id"],
            run_id=run_id,
            terminal_status=LifecycleState.SUCCEEDED,
            message=adapter_result.message,
            verification_summary={
                **adapter_result.observed_state,
                "preflight_result": preflight,
                "result_summary": result_summary,
            },
            actor=self.worker_id,
        )
        self.observability.safe_record_event(
            component="dm-worker",
            severity="INFO",
            event_type="data_job_mutation_completed",
            message=adapter_result.message,
            payload={"job_id": job["job_id"], "plan_id": plan["plan_id"]},
            correlation_id=plan["request_id"],
        )

    def _fail_data_job(
        self,
        plan: dict[str, Any],
        run_id: str,
        job: dict[str, Any],
        *,
        state: DataJobState,
        terminal_status: LifecycleState,
        message: str,
        error_category: str,
        summary: dict[str, Any],
    ) -> None:
        self.repository.update_data_job(
            job["job_id"],
            state=state,
            result_summary=summary,
        )
        self.repository.upsert_resource(
            resource_kind=ResourceKind.DATA_JOB.value,
            resource_key=job["job_id"],
            desired_state=plan["desired_state"],
            applied_state={
                "backend_side_effect": summary.get("backend_side_effect", False)
            },
            observed_state=summary,
            status=terminal_status.value,
        )
        self.repository.complete_result(
            request_id=plan["request_id"],
            plan_id=plan["plan_id"],
            run_id=run_id,
            terminal_status=terminal_status,
            message=message,
            verification_summary=summary,
            error_category=error_category,
            actor=self.worker_id,
        )
        self.observability.safe_record_event(
            component="dm-worker",
            severity="WARN",
            event_type="data_job_failed",
            message=message,
            payload={"job_id": job["job_id"], "plan_id": plan["plan_id"], **summary},
            correlation_id=plan["request_id"],
        )

    def _scan_preflight(
        self, plan: dict[str, Any], job: dict[str, Any]
    ) -> dict[str, Any]:
        request = self.repository.get_request(job["request_id"])
        mappings = self.repository.list_identity_mappings(
            requester_id=request["requester_id"],
            status=IdentityMappingStatus.ACTIVE.value,
            limit=1,
        )
        if not mappings:
            return {
                "status": "Rejected",
                "reason": "missing_active_identity_mapping",
                "requester_id": request["requester_id"],
                "selected_candidates": [],
            }
        mapping = mappings[0]
        target = job.get("normalized_target") or {
            "storage_name": job["storage_name"],
            "path": job.get("target"),
        }
        reports = self.repository.list_agent_reports(freshness="Fresh", limit=1000)
        selected: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for report in reports:
            if report["worker_role"] != WorkerRole.DM.value:
                continue
            report_evidence = report.get("report") or {}
            reason = _scan_candidate_rejection_reason(
                report_evidence,
                storage_name=job["storage_name"],
                tool="dscan",
                posix_username=mapping["posix_username"],
            )
            candidate = {
                "cluster_name": report["cluster_name"],
                "node_name": report["node_name"],
                "report_id": report["report_id"],
                "reported_at": report["reported_at"],
            }
            ready_mount = _ready_mount(
                report_evidence.get("mounts") or [], job["storage_name"]
            )
            if ready_mount:
                candidate["mount_path"] = ready_mount.get(
                    "mount_path"
                ) or ready_mount.get("path")
            if reason:
                rejected.append({**candidate, "reason": reason})
                continue
            selected.append(candidate)
        if not selected:
            return {
                "status": "Rejected",
                "reason": "no_ready_dm_candidate",
                "requester_id": request["requester_id"],
                "identity_mapping": _identity_mapping_summary(mapping),
                "target": target,
                "rejected_candidates": rejected,
                "selected_candidates": [],
            }
        resource_model = _resolve_data_job_resource_model(
            repository=self.repository,
            plan=plan,
            tool="scan",
            eligible_candidates=selected,
        )
        if resource_model.get("status") != "Ready":
            return {
                "status": "Rejected",
                "reason": resource_model.get("reason"),
                "requester_id": request["requester_id"],
                "identity_mapping": _identity_mapping_summary(mapping),
                "target": target,
                "rejected_candidates": rejected,
                "selected_candidates": [],
                "eligible_candidates": selected,
                "effective_resource_model": resource_model,
            }
        return {
            "status": "Ready",
            "reason": "scan_preflight_passed",
            "requester_id": request["requester_id"],
            "identity_mapping": _identity_mapping_summary(mapping),
            "target": target,
            "selected_candidates": selected,
            "eligible_candidates": selected,
            "rejected_candidates": rejected,
            "worker_pool": {
                "selected_candidates": selected,
                "eligible_candidates": selected,
            },
            "effective_resource_model": resource_model,
            "posix_permission_check": {
                "source": "agent-inventory",
                "uid": mapping["uid"],
                "gid": mapping["gid"],
                "groups": mapping["groups"],
                "required": ["execute_ancestors", "read_target", "execute_target"],
                "result": "agent_evidence_ready",
            },
        }

    def _mutation_preflight(
        self, plan: dict[str, Any], job: dict[str, Any]
    ) -> dict[str, Any]:
        request = self.repository.get_request(job["request_id"])
        mappings = self.repository.list_identity_mappings(
            requester_id=request["requester_id"],
            status=IdentityMappingStatus.ACTIVE.value,
            limit=1,
        )
        if not mappings:
            return {
                "status": "Rejected",
                "reason": "missing_active_identity_mapping",
                "requester_id": request["requester_id"],
                "selected_candidates": [],
            }
        mapping = mappings[0]
        if job["operation"] == OperationKind.DATA_SYNC.value:
            return self._sync_preflight(plan, job, request, mapping)
        if job["operation"] == OperationKind.DATA_RM.value:
            return self._rm_preflight(plan, job, request, mapping)
        return {
            "status": "Rejected",
            "reason": "unsupported_data_mutation_operation",
            "operation": job["operation"],
        }

    def _sync_preflight(
        self,
        plan: dict[str, Any],
        job: dict[str, Any],
        request: dict[str, Any],
        mapping: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = job.get("normalized_target") or plan["desired_state"]
        source = normalized.get("source") or plan["desired_state"].get("source") or {}
        destination = (
            normalized.get("destination")
            or plan["desired_state"].get("destination")
            or {}
        )
        reports = self.repository.list_agent_reports(freshness="Fresh", limit=1000)
        dsync_candidates: list[dict[str, Any]] = []
        source_candidates: list[dict[str, Any]] = []
        destination_candidates: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for report in reports:
            if report["worker_role"] != WorkerRole.DM.value:
                continue
            evidence = report.get("report") or {}
            candidate = {
                "cluster_name": report["cluster_name"],
                "node_name": report["node_name"],
                "report_id": report["report_id"],
                "reported_at": report["reported_at"],
            }
            source_mount = _ready_mount(
                evidence.get("mounts") or [], source.get("storage_name")
            )
            destination_mount = _ready_mount(
                evidence.get("mounts") or [], destination.get("storage_name")
            )
            identity_ready = _identity_ready(
                evidence.get("identity_evidence") or {}, mapping["posix_username"]
            )
            credentials_ready = _any_ready(
                evidence.get("credentials") or [], ready_keys=("status", "healthy")
            )
            network_ready = _any_ready(
                evidence.get("networks") or [], ready_keys=("status", "reachable")
            )
            if (
                source_mount
                and _tool_ready(evidence.get("tools") or [], "nsync")
                and identity_ready
            ):
                source_candidates.append(
                    {
                        **candidate,
                        "mount_path": source_mount.get("mount_path")
                        or source_mount.get("path"),
                    }
                )
            if (
                destination_mount
                and _tool_ready(evidence.get("tools") or [], "nsync")
                and identity_ready
            ):
                destination_candidates.append(
                    {
                        **candidate,
                        "mount_path": destination_mount.get("mount_path")
                        or destination_mount.get("path"),
                    }
                )
            reason = _sync_dsync_candidate_rejection_reason(
                evidence,
                source_storage=source.get("storage_name"),
                destination_storage=destination.get("storage_name"),
                posix_username=mapping["posix_username"],
            )
            if reason:
                rejected.append({**candidate, "reason": reason})
                continue
            if not credentials_ready:
                rejected.append({**candidate, "reason": "credential_not_ready"})
                continue
            if not network_ready:
                rejected.append({**candidate, "reason": "network_not_ready"})
                continue
            dsync_candidates.append(
                {
                    **candidate,
                    "source_mount_path": source_mount.get("mount_path")
                    or source_mount.get("path"),
                    "destination_mount_path": destination_mount.get("mount_path")
                    or destination_mount.get("path"),
                }
            )
        if dsync_candidates:
            resource_model = _resolve_data_job_resource_model(
                repository=self.repository,
                plan=plan,
                tool="dsync",
                eligible_candidates=dsync_candidates,
            )
            if resource_model.get("status") != "Ready":
                return {
                    "status": "Rejected",
                    "reason": resource_model.get("reason"),
                    "requester_id": request["requester_id"],
                    "identity_mapping": _identity_mapping_summary(mapping),
                    "source": source,
                    "destination": destination,
                    "selected_tool": "dsync",
                    "tool_selection_reason": "same_node_source_destination_mount",
                    "selected_candidates": [],
                    "eligible_candidates": dsync_candidates,
                    "rejected_candidates": rejected,
                    "effective_resource_model": resource_model,
                }
            return {
                "status": "Ready",
                "reason": "sync_preflight_passed",
                "requester_id": request["requester_id"],
                "identity_mapping": _identity_mapping_summary(mapping),
                "source": source,
                "destination": destination,
                "selected_tool": "dsync",
                "tool_selection_reason": "same_node_source_destination_mount",
                "selected_candidates": dsync_candidates,
                "eligible_candidates": dsync_candidates,
                "rejected_candidates": rejected,
                "worker_pool": {
                    "selected_candidates": dsync_candidates,
                    "eligible_candidates": dsync_candidates,
                },
                "effective_resource_model": resource_model,
                "posix_permission_check": {
                    "source": "agent-inventory",
                    "uid": mapping["uid"],
                    "gid": mapping["gid"],
                    "groups": mapping["groups"],
                    "required": [
                        "read_source",
                        "execute_source",
                        "write_destination_parent",
                        "execute_destination_parent",
                    ],
                    "result": "agent_evidence_ready",
                },
            }
        if source_candidates and destination_candidates:
            nsync_enabled = _adapter_nsync_enabled(self.volcano_adapter)
            resource_model = _resolve_data_job_resource_model(
                repository=self.repository,
                plan=plan,
                tool="nsync",
                eligible_candidates=[*source_candidates, *destination_candidates],
                source_candidates=source_candidates,
                destination_candidates=destination_candidates,
            )
            if not nsync_enabled:
                resource_model = {
                    **resource_model,
                    "status": "Rejected",
                    "reason": "nsync_disabled",
                }
            if resource_model.get("status") != "Ready":
                return {
                    "status": "Rejected",
                    "reason": resource_model.get("reason"),
                    "requester_id": request["requester_id"],
                    "identity_mapping": _identity_mapping_summary(mapping),
                    "source": source,
                    "destination": destination,
                    "selected_tool": "nsync",
                    "tool_selection_reason": "separated_role_source_destination_mounts",
                    "backend_side_effect": False,
                    "nsync_enabled": nsync_enabled,
                    "source_candidates": source_candidates,
                    "destination_candidates": destination_candidates,
                    "selected_source_candidates": [],
                    "selected_destination_candidates": [],
                    "selected_candidates": [],
                    "eligible_candidates": [
                        *source_candidates,
                        *destination_candidates,
                    ],
                    "rejected_candidates": rejected,
                    "worker_pool": {
                        "source_candidates": source_candidates,
                        "destination_candidates": destination_candidates,
                        "eligible_candidates": [
                            *source_candidates,
                            *destination_candidates,
                        ],
                    },
                    "effective_resource_model": resource_model,
                }
            return {
                "status": "Ready",
                "reason": "sync_preflight_passed",
                "requester_id": request["requester_id"],
                "identity_mapping": _identity_mapping_summary(mapping),
                "source": source,
                "destination": destination,
                "selected_tool": "nsync",
                "tool_selection_reason": "separated_role_source_destination_mounts",
                "nsync_enabled": nsync_enabled,
                "source_candidates": source_candidates,
                "destination_candidates": destination_candidates,
                "selected_source_candidates": source_candidates,
                "selected_destination_candidates": destination_candidates,
                "selected_candidates": [*source_candidates, *destination_candidates],
                "eligible_candidates": [*source_candidates, *destination_candidates],
                "rejected_candidates": rejected,
                "worker_pool": {
                    "source_candidates": source_candidates,
                    "destination_candidates": destination_candidates,
                    "selected_source_candidates": source_candidates,
                    "selected_destination_candidates": destination_candidates,
                    "selected_candidates": [
                        *source_candidates,
                        *destination_candidates,
                    ],
                    "eligible_candidates": [
                        *source_candidates,
                        *destination_candidates,
                    ],
                },
                "effective_resource_model": resource_model,
            }
        return {
            "status": "Rejected",
            "reason": "no_ready_sync_candidate",
            "requester_id": request["requester_id"],
            "identity_mapping": _identity_mapping_summary(mapping),
            "source": source,
            "destination": destination,
            "selected_candidates": [],
            "rejected_candidates": rejected,
        }

    def _rm_preflight(
        self,
        plan: dict[str, Any],
        job: dict[str, Any],
        request: dict[str, Any],
        mapping: dict[str, Any],
    ) -> dict[str, Any]:
        target = (
            job.get("normalized_target") or plan["desired_state"].get("target") or {}
        )
        reports = self.repository.list_agent_reports(freshness="Fresh", limit=1000)
        selected: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for report in reports:
            if report["worker_role"] != WorkerRole.DM.value:
                continue
            evidence = report.get("report") or {}
            reason = _scan_candidate_rejection_reason(
                evidence,
                storage_name=target.get("storage_name") or job["storage_name"],
                tool="drm",
                posix_username=mapping["posix_username"],
            )
            candidate = {
                "cluster_name": report["cluster_name"],
                "node_name": report["node_name"],
                "report_id": report["report_id"],
                "reported_at": report["reported_at"],
            }
            ready_mount = _ready_mount(
                evidence.get("mounts") or [],
                target.get("storage_name") or job["storage_name"],
            )
            if ready_mount:
                candidate["mount_path"] = ready_mount.get(
                    "mount_path"
                ) or ready_mount.get("path")
            if reason:
                rejected.append({**candidate, "reason": reason})
                continue
            selected.append(candidate)
        if not selected:
            return {
                "status": "Rejected",
                "reason": "no_ready_rm_candidate",
                "requester_id": request["requester_id"],
                "identity_mapping": _identity_mapping_summary(mapping),
                "target": target,
                "selected_candidates": [],
                "rejected_candidates": rejected,
            }
        resource_model = _resolve_data_job_resource_model(
            repository=self.repository,
            plan=plan,
            tool="rm",
            eligible_candidates=selected,
        )
        if resource_model.get("status") != "Ready":
            return {
                "status": "Rejected",
                "reason": resource_model.get("reason"),
                "requester_id": request["requester_id"],
                "identity_mapping": _identity_mapping_summary(mapping),
                "target": target,
                "selected_tool": "drm",
                "tool_selection_reason": "target_mount_with_drm",
                "selected_candidates": [],
                "eligible_candidates": selected,
                "rejected_candidates": rejected,
                "effective_resource_model": resource_model,
            }
        return {
            "status": "Ready",
            "reason": "rm_preflight_passed",
            "requester_id": request["requester_id"],
            "identity_mapping": _identity_mapping_summary(mapping),
            "target": target,
            "selected_tool": "drm",
            "tool_selection_reason": "target_mount_with_drm",
            "selected_candidates": selected,
            "eligible_candidates": selected,
            "rejected_candidates": rejected,
            "worker_pool": {
                "selected_candidates": selected,
                "eligible_candidates": selected,
            },
            "effective_resource_model": resource_model,
            "posix_permission_check": {
                "source": "agent-inventory",
                "uid": mapping["uid"],
                "gid": mapping["gid"],
                "groups": mapping["groups"],
                "required": [
                    "read_target",
                    "execute_target",
                    "write_parent",
                    "execute_parent",
                ],
                "result": "agent_evidence_ready",
            },
        }

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
