from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlparse

from ._base import *  # noqa: F401,F403


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


def _scan_candidate_rejection_reason(
    report: dict[str, Any],
    *,
    storage_name: str,
    tool: str,
    posix_username: str,
) -> str | None:
    if not _mount_ready(report.get("mounts") or [], storage_name):
        return "missing_target_mount"
    if not _tool_ready(report.get("tools") or [], tool):
        return f"missing_{tool}_tool"
    if not _any_ready(
        report.get("credentials") or [], ready_keys=("status", "healthy")
    ):
        return "credential_not_ready"
    if not _any_ready(report.get("networks") or [], ready_keys=("status", "reachable")):
        return "network_not_ready"
    if not _identity_ready(report.get("identity_evidence") or {}, posix_username):
        return "identity_not_ready_on_node"
    return None


def _adapter_nsync_enabled(volcano_adapter: Any) -> bool:
    settings = getattr(volcano_adapter, "settings", None)
    if settings is None:
        return True
    return bool(getattr(settings, "dm_nsync_enabled", True))


def _first_selected_node(selected_candidates: list[dict[str, Any]]) -> str | None:
    for candidate in selected_candidates:
        node_name = candidate.get("node_name")
        if node_name:
            return str(node_name)
    return None


def _phase21_minimal_resource_model(
    selected_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    selected_node = _first_selected_node(selected_candidates)
    return {
        "selected_node": selected_node,
        "selected_node_count": 1 if selected_node else 0,
        "worker_pod_count": 1 if selected_node else 0,
        "process_count": 1 if selected_node else 0,
    }


def _resolve_data_job_resource_model(
    *,
    repository: DmsRepository,
    plan: dict[str, Any],
    tool: str,
    eligible_candidates: list[dict[str, Any]],
    source_candidates: list[dict[str, Any]] | None = None,
    destination_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    policy_operation = {
        "dscan": "scan",
        "scan": "scan",
        "drm": "rm",
        "rm": "rm",
        "dsync": "dsync",
        "nsync": "nsync",
    }.get(tool, tool)
    policy = repository.get_data_management_policy(policy_operation)
    if not policy:
        return {
            "status": "Rejected",
            "reason": "missing_data_management_policy",
            "policy_operation": policy_operation,
            "eligible_node_count": len(_unique_candidate_nodes(eligible_candidates)),
        }
    if not policy.get("enabled", True):
        return {
            "status": "Rejected",
            "reason": "data_management_policy_disabled",
            "policy_operation": policy_operation,
            "policy": policy,
        }
    resources = (plan.get("desired_state") or {}).get("resources") or {}
    processes_requested = resources.get("processes_per_node")
    processes_per_node, process_clamp = _clamp_policy_count(
        requested=processes_requested,
        default=int(policy["default_processes_per_node"]),
        maximum=int(policy["max_processes_per_node"]),
        field="processes_per_node",
    )
    clamp_reasons = [process_clamp] if process_clamp else []
    if policy_operation == "nsync":
        source_hint = resources.get("source_node_count") or resources.get("node_count")
        destination_hint = resources.get("destination_node_count") or resources.get(
            "node_count"
        )
        source_required, source_clamp = _clamp_policy_count(
            requested=source_hint,
            default=int(policy["default_source_nodes"]),
            maximum=int(policy["max_source_nodes"]),
            field="source_node_count",
        )
        destination_required, destination_clamp = _clamp_policy_count(
            requested=destination_hint,
            default=int(policy["default_destination_nodes"]),
            maximum=int(policy["max_destination_nodes"]),
            field="destination_node_count",
        )
        clamp_reasons.extend(item for item in (source_clamp, destination_clamp) if item)
        source_nodes = _unique_candidate_nodes(source_candidates or [])
        destination_nodes = _unique_candidate_nodes(destination_candidates or [])
        if len(source_nodes) < source_required:
            return _resource_shortage_model(
                policy=policy,
                resources=resources,
                reason="insufficient_source_eligible_nodes",
                eligible_node_count=len(source_nodes),
                required_node_count=source_required,
                processes_per_node=processes_per_node,
                clamp_reasons=clamp_reasons,
            )
        if len(destination_nodes) < destination_required:
            return _resource_shortage_model(
                policy=policy,
                resources=resources,
                reason="insufficient_destination_eligible_nodes",
                eligible_node_count=len(destination_nodes),
                required_node_count=destination_required,
                processes_per_node=processes_per_node,
                clamp_reasons=clamp_reasons,
            )
        worker_pod_count = source_required + destination_required
        return {
            "status": "Ready",
            "policy_operation": policy_operation,
            "policy": policy,
            "requested_resources": resources,
            "clamp_reasons": clamp_reasons,
            "scheduler_selection": "eligible_node_set",
            "source_node_count": source_required,
            "destination_node_count": destination_required,
            "selected_node_count": worker_pod_count,
            "worker_pod_count": worker_pod_count,
            "launcher_pod_count": 1,
            "processes_per_node": processes_per_node,
            "process_count": worker_pod_count * processes_per_node,
            "eligible_source_nodes": source_nodes,
            "eligible_destination_nodes": destination_nodes,
            "eligible_node_count": len(set(source_nodes + destination_nodes)),
            "queue": policy.get("default_queue"),
            "priority_class": policy.get("default_priority_class"),
        }
    node_hint = resources.get("node_count")
    required_nodes, node_clamp = _clamp_policy_count(
        requested=node_hint,
        default=int(policy["default_worker_nodes"]),
        maximum=int(policy["max_worker_nodes"]),
        field="node_count",
    )
    if node_clamp:
        clamp_reasons.append(node_clamp)
    eligible_nodes = _unique_candidate_nodes(eligible_candidates)
    if len(eligible_nodes) < required_nodes:
        return _resource_shortage_model(
            policy=policy,
            resources=resources,
            reason="insufficient_eligible_nodes",
            eligible_node_count=len(eligible_nodes),
            required_node_count=required_nodes,
            processes_per_node=processes_per_node,
            clamp_reasons=clamp_reasons,
        )
    return {
        "status": "Ready",
        "policy_operation": policy_operation,
        "policy": policy,
        "requested_resources": resources,
        "clamp_reasons": clamp_reasons,
        "scheduler_selection": "eligible_node_set",
        "selected_node": None,
        "selected_node_count": required_nodes,
        "worker_pod_count": required_nodes,
        "launcher_pod_count": 1,
        "processes_per_node": processes_per_node,
        "process_count": required_nodes * processes_per_node,
        "eligible_nodes": eligible_nodes,
        "eligible_node_count": len(eligible_nodes),
        "queue": policy.get("default_queue"),
        "priority_class": policy.get("default_priority_class"),
    }


def _clamp_policy_count(
    *,
    requested: Any,
    default: int,
    maximum: int,
    field: str,
) -> tuple[int, dict[str, Any] | None]:
    if requested is None:
        return default, None
    value = int(requested)
    if value > maximum:
        return maximum, {
            "field": field,
            "requested": value,
            "effective": maximum,
            "reason": "resource_hint_clamped_to_policy_max",
        }
    return value, None


def _resource_shortage_model(
    *,
    policy: dict[str, Any],
    resources: dict[str, Any],
    reason: str,
    eligible_node_count: int,
    required_node_count: int,
    processes_per_node: int,
    clamp_reasons: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "status": "Rejected",
        "reason": reason,
        "policy": policy,
        "requested_resources": resources,
        "clamp_reasons": clamp_reasons,
        "eligible_node_count": eligible_node_count,
        "required_node_count": required_node_count,
        "worker_pod_count": required_node_count,
        "launcher_pod_count": 1,
        "processes_per_node": processes_per_node,
        "process_count": required_node_count * processes_per_node,
    }


def _unique_candidate_nodes(candidates: list[dict[str, Any]]) -> list[str]:
    nodes: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        node = candidate.get("node_name")
        if not node:
            continue
        node_name = str(node)
        if node_name in seen:
            continue
        seen.add(node_name)
        nodes.append(node_name)
    return nodes


def _sync_dsync_candidate_rejection_reason(
    report: dict[str, Any],
    *,
    source_storage: str,
    destination_storage: str,
    posix_username: str,
) -> str | None:
    if not _mount_ready(report.get("mounts") or [], source_storage):
        return "missing_source_mount"
    if not _mount_ready(report.get("mounts") or [], destination_storage):
        return "missing_destination_mount"
    if not _tool_ready(report.get("tools") or [], "dsync"):
        return "missing_dsync_tool"
    if not _any_ready(
        report.get("credentials") or [], ready_keys=("status", "healthy")
    ):
        return "credential_not_ready"
    if not _any_ready(report.get("networks") or [], ready_keys=("status", "reachable")):
        return "network_not_ready"
    if not _identity_ready(report.get("identity_evidence") or {}, posix_username):
        return "identity_not_ready_on_node"
    return None


def _mount_ready(mounts: list[dict[str, Any]], storage_name: str) -> bool:
    return _ready_mount(mounts, storage_name) is not None


def _ready_mount(
    mounts: list[dict[str, Any]], storage_name: str
) -> dict[str, Any] | None:
    for mount in mounts:
        if mount.get("storage_name") != storage_name:
            continue
        if mount.get("status") == "Ready":
            return mount
        if mount.get("readable") is True:
            return mount
    return None


def _tool_ready(tools: list[Any], tool_name: str) -> bool:
    for tool in tools:
        if isinstance(tool, str):
            if tool == tool_name:
                return True
            continue
        if not isinstance(tool, dict) or tool.get("name") != tool_name:
            continue
        if (
            tool.get("status") == "Ready"
            or tool.get("healthy") is True
            or tool.get("path")
        ):
            return True
    return False


def _any_ready(items: list[dict[str, Any]], *, ready_keys: tuple[str, ...]) -> bool:
    for item in items:
        for key in ready_keys:
            value = item.get(key)
            if value == "Ready" or value is True:
                return True
    return False


def _identity_ready(identity_evidence: dict[str, Any], posix_username: str) -> bool:
    for user in identity_evidence.get("users") or []:
        if user.get("username") != posix_username:
            continue
        return user.get("status") == "Ready" or user.get("uid") is not None
    return False


def _identity_mapping_summary(mapping: dict[str, Any]) -> dict[str, Any]:
    return {
        "requester_id": mapping["requester_id"],
        "identity_provider": mapping["identity_provider"],
        "posix_username": mapping["posix_username"],
        "uid": mapping["uid"],
        "gid": mapping["gid"],
        "groups": mapping["groups"],
        "status": mapping["status"],
        "verified_at": mapping.get("verified_at"),
    }


def _volcano_job_ref(adapter_result: AdapterResult) -> dict[str, Any]:
    ref = adapter_result.applied_state.get(
        "job_ref"
    ) or adapter_result.observed_state.get("job_ref")
    if not ref:
        return {}
    return {
        "job_ref": ref,
        "adapter": adapter_result.applied_state.get("adapter")
        or adapter_result.observed_state.get("adapter"),
    }


def _verify_scan_runtime_preflight(
    volcano_adapter: Any,
    plan: dict[str, Any],
    job: dict[str, Any],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    verifier = getattr(volcano_adapter, "verify_scan_preflight", None)
    if verifier is None:
        return {
            "status": "Ready",
            "source": "adapter-unavailable",
            "reason": "runtime_preflight_not_supported",
        }
    try:
        result = verifier(plan, job, preflight)
    except Exception as exc:  # noqa: BLE001 - preflight must fail closed.
        return {
            "status": "Rejected",
            "source": type(volcano_adapter).__name__,
            "reason": "runtime_preflight_failed",
            "message": str(exc),
        }
    if not isinstance(result, dict):
        return {
            "status": "Rejected",
            "source": type(volcano_adapter).__name__,
            "reason": "runtime_preflight_invalid_result",
        }
    return result


def _verify_data_runtime_preflight(
    volcano_adapter: Any,
    plan: dict[str, Any],
    job: dict[str, Any],
    preflight: dict[str, Any],
    *,
    phase: str,
) -> dict[str, Any]:
    verifier = getattr(volcano_adapter, "verify_data_preflight", None)
    if verifier is None:
        if job["operation"] == OperationKind.DATA_SCAN.value:
            return _verify_scan_runtime_preflight(volcano_adapter, plan, job, preflight)
        return {
            "status": "Ready",
            "source": "adapter-unavailable",
            "reason": "runtime_preflight_not_supported",
        }
    try:
        result = verifier(plan, job, preflight, phase=phase)
    except Exception as exc:  # noqa: BLE001 - preflight must fail closed.
        return {
            "status": "Rejected",
            "source": type(volcano_adapter).__name__,
            "reason": "runtime_preflight_failed",
            "message": str(exc),
        }
    if not isinstance(result, dict):
        return {
            "status": "Rejected",
            "source": type(volcano_adapter).__name__,
            "reason": "runtime_preflight_invalid_result",
        }
    return result


def _scan_result_summary(
    *,
    plan: dict[str, Any],
    job: dict[str, Any],
    adapter_result: AdapterResult,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    artifact_uri = adapter_result.artifact_uri
    try:
        parsed_summary = _scan_artifact_summary(artifact_uri)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise DataManagementRuntimeError(
            f"summary artifact is missing or invalid for {artifact_uri}: {exc}"
        ) from exc
    if _artifact_requires_local_parse(artifact_uri) and parsed_summary is None:
        raise DataManagementRuntimeError(
            f"summary artifact is missing or invalid for {artifact_uri}"
        )
    observed_summary = (
        parsed_summary or adapter_result.observed_state.get("summary") or {}
    )
    target = job.get("normalized_target") or plan["desired_state"].get("target") or {}
    resource_evidence = _phase21_result_resource_evidence(
        job=job, preflight=preflight, adapter_result=adapter_result
    )
    report_uri = _artifact_child_uri(artifact_uri, "dscan-report.json")
    return {
        "status": "Succeeded",
        "tool": job.get("selected_tool") or "dscan",
        "target": target,
        "artifact_base_uri": artifact_uri,
        "stdout_uri": _artifact_child_uri(artifact_uri, "stdout.log"),
        "stderr_uri": _artifact_child_uri(artifact_uri, "stderr.log"),
        "report_uri": report_uri,
        "scan_report_uri": report_uri,
        "summary_uri": _artifact_child_uri(artifact_uri, "summary.json"),
        "summary": {
            "file_count": int(observed_summary.get("file_count", 0)),
            "directory_count": int(observed_summary.get("directory_count", 0)),
            "total_bytes": int(observed_summary.get("total_bytes", 0)),
            "error_count": int(observed_summary.get("error_count", 0)),
            "scan_root": target.get("path") or job.get("target"),
        },
        "summary_source": "artifact" if parsed_summary else "adapter_observed_state",
        "preflight_status": preflight.get("status"),
        "volcano_job_ref": _volcano_job_ref(adapter_result),
        **resource_evidence,
    }


def _mutation_result_summary(
    *,
    plan: dict[str, Any],
    job: dict[str, Any],
    adapter_result: AdapterResult,
    preflight: dict[str, Any],
    phase: str,
) -> dict[str, Any]:
    previous = job.get("result_summary") or {}
    artifact_uri = adapter_result.artifact_uri
    parsed_summary = _mutation_artifact_summary(artifact_uri, phase)
    observed_summary = (
        parsed_summary or adapter_result.observed_state.get("summary") or {}
    )
    phase_base_uri = _artifact_child_uri(artifact_uri, phase)
    resource_evidence = _phase21_result_resource_evidence(
        job=job, preflight=preflight, adapter_result=adapter_result
    )
    phase_entry = {
        "state": "Succeeded",
        "artifact_uri": phase_base_uri,
        "summary_uri": _artifact_child_uri(phase_base_uri, "summary.json"),
        "stdout_uri": _artifact_child_uri(phase_base_uri, "stdout.log"),
        "stderr_uri": _artifact_child_uri(phase_base_uri, "stderr.log"),
        "command_uri": _artifact_child_uri(phase_base_uri, "command.json"),
        "fingerprint": _summary_fingerprint(observed_summary),
        "summary": observed_summary,
    }
    selected_tool = job.get("selected_tool") or preflight.get("selected_tool")
    result = {
        **previous,
        "operation": job["operation"],
        "selected_tool": selected_tool,
        "phase": phase,
        "artifact_base_uri": artifact_uri,
        "preflight_status": preflight.get("status"),
        **resource_evidence,
        phase: phase_entry,
    }
    if phase == "execution":
        result["summary"] = {
            "file_count": int(observed_summary.get("file_count", 0)),
            "directory_count": int(observed_summary.get("directory_count", 0)),
            "total_bytes": int(observed_summary.get("total_bytes", 0)),
            "error_count": int(observed_summary.get("error_count", 0)),
            "target_absent": observed_summary.get("target_absent"),
        }
    if job["operation"] == OperationKind.DATA_SYNC.value:
        normalized = job.get("normalized_target") or plan["desired_state"]
        result["source"] = normalized.get("source")
        result["destination"] = normalized.get("destination")
    if job["operation"] == OperationKind.DATA_RM.value:
        result["target"] = job.get("normalized_target") or plan["desired_state"].get(
            "target"
        )
    return result


def _phase21_result_resource_evidence(
    *,
    job: dict[str, Any],
    preflight: dict[str, Any],
    adapter_result: AdapterResult,
) -> dict[str, Any]:
    selected = (
        preflight.get("selected_candidates")
        or (preflight.get("worker_pool") or {}).get("selected_candidates")
        or (job.get("worker_pool") or {}).get("selected_candidates")
        or []
    )
    if not isinstance(selected, list):
        selected = []
    resource_model = preflight.get("effective_resource_model")
    if not isinstance(resource_model, dict):
        resource_model = {}
    pod_summary = adapter_result.observed_state.get("pod_summary")
    if not isinstance(pod_summary, dict):
        pod_summary = {}
    observed_pod_count = pod_summary.get("worker_pod_count")
    if observed_pod_count is None:
        pods = pod_summary.get("pods")
        if isinstance(pods, list):
            observed_pod_count = len(pods)
    selected_node = resource_model.get(
        "selected_node"
    ) or adapter_result.observed_state.get("selected_node")
    scheduled_nodes = _scheduled_nodes_from_pod_summary(pod_summary)
    if (
        selected_node is None
        and resource_model.get("scheduler_selection") != "eligible_node_set"
    ):
        selected_node = _first_selected_node(selected)
    if selected_node is None and len(scheduled_nodes) == 1:
        selected_node = scheduled_nodes[0]
    worker_pod_count = observed_pod_count
    if worker_pod_count is None:
        worker_pod_count = resource_model.get("worker_pod_count")
    if worker_pod_count is None:
        worker_pod_count = 1 if selected_node else 0
    process_count = resource_model.get("process_count")
    if process_count is None:
        process_count = 1 if worker_pod_count else 0
    selected_node_count = resource_model.get("selected_node_count")
    if selected_node_count is None:
        selected_node_count = len(scheduled_nodes) or (1 if selected_node else 0)
    mpi_metadata = adapter_result.observed_state.get("mpi_metadata")
    if not isinstance(mpi_metadata, dict):
        mpi_metadata = _default_mpi_metadata_uris(adapter_result.artifact_uri)
    return {
        "selected_node": selected_node,
        "selected_node_count": int(selected_node_count or 0),
        "worker_pod_count": int(worker_pod_count or 0),
        "launcher_pod_count": int(resource_model.get("launcher_pod_count") or 0),
        "processes_per_node": int(resource_model.get("processes_per_node") or 1),
        "process_count": int(process_count or 0),
        "eligible_nodes": resource_model.get("eligible_nodes"),
        "eligible_source_nodes": resource_model.get("eligible_source_nodes"),
        "eligible_destination_nodes": resource_model.get("eligible_destination_nodes"),
        "scheduled_nodes": scheduled_nodes,
        "scheduler_selection": resource_model.get("scheduler_selection"),
        "effective_resource_model": resource_model,
        "mpi_metadata": mpi_metadata,
        "pod_summary": pod_summary,
    }


def _scheduled_nodes_from_pod_summary(pod_summary: dict[str, Any]) -> list[str]:
    nodes: list[str] = []
    seen: set[str] = set()
    for pod in pod_summary.get("pods") or []:
        if pod.get("role") == "launcher":
            continue
        node = pod.get("node_name")
        if not node or node in seen:
            continue
        seen.add(str(node))
        nodes.append(str(node))
    return nodes


def _default_mpi_metadata_uris(artifact_uri: str | None) -> dict[str, str | None]:
    return {
        "submitted_uri": _artifact_child_uri(artifact_uri, "mpi/submitted.yaml"),
        "launch_uri": _artifact_child_uri(artifact_uri, "mpi/launch.json"),
        "workers_uri": _artifact_child_uri(artifact_uri, "mpi/workers.json"),
        "scheduler_uri": _artifact_child_uri(artifact_uri, "mpi/scheduler.json"),
        "mpirun_uri": _artifact_child_uri(artifact_uri, "mpi/mpirun.json"),
    }


def _artifact_child_uri(base_uri: str | None, name: str) -> str | None:
    if not base_uri:
        return None
    return f"{base_uri.rstrip('/')}/{name}"


def _artifact_path_within(base: Path, *parts: str) -> Path | None:
    """Join ``base/parts`` and return it only when the resolved real path stays inside
    ``base``. Defends the root dm-worker against a requester-planted symlink in their own
    (writable) artifact dir redirecting the read out of the job's directory."""
    candidate = base.joinpath(*parts)
    try:
        base_real = os.path.realpath(base)
        candidate_real = os.path.realpath(candidate)
    except OSError:
        return None
    if os.path.commonpath([base_real, candidate_real]) != base_real:
        return None
    return candidate


def _mutation_artifact_summary(
    artifact_uri: str | None, phase: str
) -> dict[str, Any] | None:
    if not artifact_uri:
        return None
    parsed = urlparse(artifact_uri)
    if parsed.scheme != "file":
        return None
    summary_path = _artifact_path_within(Path(parsed.path), phase, "summary.json")
    if summary_path is None or not summary_path.exists():
        return None
    with summary_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    summary = payload.get("summary") if isinstance(payload, dict) else None
    return summary if isinstance(summary, dict) else payload


def _scan_artifact_summary(artifact_uri: str | None) -> dict[str, Any] | None:
    if not artifact_uri:
        return None
    parsed = urlparse(artifact_uri)
    if parsed.scheme != "file":
        return None
    artifact_path = Path(parsed.path)
    summary_path = _artifact_path_within(artifact_path, "summary.json")
    report_path = _artifact_path_within(artifact_path, "dscan-report.json")
    source_path = (
        summary_path
        if summary_path is not None and summary_path.exists()
        else report_path
    )
    if source_path is None or not source_path.exists():
        return None
    with source_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return _normalize_scan_summary(payload)


def _normalize_scan_summary(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    summary = payload.get("summary") if isinstance(payload, dict) else None
    if not isinstance(summary, dict):
        return payload
    if "file_count" in summary or "directory_count" in summary:
        return summary
    broken_paths = payload.get("broken_paths")
    return {
        "file_count": int(summary.get("total_files", 0)),
        "directory_count": int(summary.get("total_directories", 0)),
        "total_bytes": int(
            summary.get("total_bytes")
            or summary.get("total_size_bytes")
            or summary.get("total_file_bytes")
            or 0
        ),
        "error_count": len(broken_paths) if isinstance(broken_paths, list) else 0,
    }


def _artifact_requires_local_parse(artifact_uri: str | None) -> bool:
    if not artifact_uri:
        return False
    return urlparse(artifact_uri).scheme == "file"


def _summary_fingerprint(summary: dict[str, Any]) -> str:
    payload = json.dumps(summary or {}, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _is_expired(iso_value: str | None) -> bool:
    if not iso_value:
        return True
    parsed = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed <= datetime.now(UTC)


def confirm_data_job(
    repository: DmsRepository,
    job_id: str,
    actor: str,
    *,
    requester_id: str | None = None,
    confirm: bool = False,
    preview_observed_hash: str | None = None,
    require_preview_fingerprint: bool = False,
) -> None:
    job = repository.get_data_job(job_id)
    if job["state"] != DataJobState.CONFIRM_PENDING.value:
        raise ValueError("data job is not waiting for confirm")
    plan = repository.get_plan_by_request(job["request_id"])
    if not plan:
        raise ValueError("data job has no plan")
    if job["operation"] in {OperationKind.DATA_SYNC.value, OperationKind.DATA_RM.value}:
        if not confirm:
            raise ValueError("confirm=true is required")
        request = repository.get_request(job["request_id"])
        if requester_id is not None and requester_id != request["requester_id"]:
            raise ValueError("confirm requester_id does not match data job requester")
        if _is_expired(job.get("preview_expires_at")):
            repository.update_data_job(job_id, state=DataJobState.PREVIEW_EXPIRED)
            repository.complete_result(
                request_id=job["request_id"],
                plan_id=plan["plan_id"],
                run_id=None,
                terminal_status=LifecycleState.REJECTED,
                message="data job preview expired before confirm",
                verification_summary={
                    "backend_side_effect": False,
                    "reason": "data_job_preview_expired",
                },
                error_category="data_management_confirm",
                actor=actor,
            )
            raise ValueError("data job preview has expired")
        preview = (job.get("result_summary") or {}).get("preview") or {}
        expected_hash = preview.get("fingerprint")
        if require_preview_fingerprint and not preview_observed_hash:
            raise ValueError("preview_observed_hash is required")
        if (
            preview_observed_hash
            and expected_hash
            and preview_observed_hash != expected_hash
        ):
            raise ValueError("preview_observed_hash does not match preview evidence")
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
    volcano_adapter: Any,
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
    refs = job.get("volcano_job_ref") or {}
    job_refs: list[str] = []
    if isinstance(refs, dict):
        if refs.get("job_ref"):
            job_refs.append(refs["job_ref"])
        for value in refs.values():
            if isinstance(value, dict) and value.get("job_ref"):
                job_refs.append(value["job_ref"])
    for job_ref in sorted(set(job_refs)):
        volcano_adapter.terminate_job(job_ref)

