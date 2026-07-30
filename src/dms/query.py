from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .domain import (
    FILESYSTEM_BACKEND_TYPES,
    DataJobState,
    LifecycleState,
    OperationKind,
    ResourceKind,
)
from .repositories import (
    ATTENTION_RUN_STATES,
    DmsRepository,
    ObservabilityRepository,
)
from .repositories._base import iso_at

# Filesystem backends are mounted on the DM node agents' hosts; agentless CSI
# mappings run no node agent at all — so a "Missing" DM readiness on them is
# expected, not an action item.
_AGENT_BACKED_BACKENDS = frozenset(FILESYSTEM_BACKEND_TYPES)

# request_attention is capped at this many items in the composite list (matches
# DmsRepository.list_action_required default limit); the count is clamped to match.
_ACTION_REQUIRED_REQUEST_LIMIT = 100
# Data jobs in these (operation, state) combinations each surface as exactly one
# action-required item. The scan/count share this predicate AND this cap so the count
# never drifts from the listed items.
_DATA_JOB_ATTENTION_SCAN_LIMIT = 5000
_DATA_JOB_ATTENTION_OPERATIONS = (
    OperationKind.DATA_SCAN.value,
    OperationKind.DATA_SYNC.value,
    OperationKind.DATA_RM.value,
)
_DATA_JOB_ATTENTION_STATES = (
    DataJobState.PREFLIGHT_FAILED.value,
    DataJobState.FAILED.value,
    DataJobState.TIMED_OUT.value,
    # Cancelled is a normal, expected outcome (operator/user cancel) — not an action
    # item — so it is deliberately NOT here (B). The record is still kept as history.
)


def _action_fingerprint(item: dict) -> str:
    """Stable per-item key for acknowledge (issue_type + best identifier). Mirrors the
    portal's fingerprint so an ack targets exactly one action-required item."""
    issue_type = item.get("issue_type") or ""
    ns = item.get("namespace_name") or item.get("namespace")
    key = (
        item.get("resource_key")
        or item.get("request_id")
        or item.get("job_id")
        or item.get("report_id")
        or item.get("storage_name")
        or (f"{item.get('cluster_name')}:{ns}" if ns else None)
        or item.get("node_name")
        or ""
    )
    return f"{issue_type}|{key}"


@dataclass
class OperationalQueryService:
    repository: DmsRepository
    observability: ObservabilityRepository
    # Freshness window for latest-per-node staleness (defaults to the Settings default;
    # create_app injects settings.agent_report_stale_seconds). Freshness is computed on
    # read from agent_node_current, so this threshold decides which CURRENT node reports
    # count as Stale action items.
    agent_report_stale_seconds: int = 300
    # (A′) Recency window for data-job attention: only jobs updated within this many
    # seconds surface as action-required. The job ROW is preserved as history — older
    # terminal jobs just stop alarming, so the alarm stays bounded without deleting
    # anything. 0 = no window (surface all matching, legacy behavior).
    data_job_attention_window_seconds: int = 0

    def _data_job_attention_since(self) -> str | None:
        w = self.data_job_attention_window_seconds
        return iso_at(-w) if w and w > 0 else None

    def action_required(self) -> list[dict]:
        # Order preserved (request → storage → agent → data) so
        # the composite list is byte-for-byte what consumers already render. Each source
        # is a discrete helper so action_required_count() can size them with cheap counts
        # (or their own bounded len) WITHOUT building the full composite list.
        issues: list[dict] = list(self._request_attention_issues())
        issues.extend(self._storage_mapping_action_required())
        issues.extend(self._agent_stale_action_required())
        issues.extend(self._data_management_action_required())
        # Server-side acknowledge: drop items an operator marked handled (by
        # fingerprint) — record-preserving, applies across all clients.
        acked = self.repository.action_ack_fingerprints()
        if acked:
            issues = [i for i in issues if _action_fingerprint(i) not in acked]
        return issues

    def action_required_count(self) -> int:
        """Exact count of action_required() items. Fast path = cheap per-source COUNT(*)
        (each term mirrors its list source's cardinality/cap, so it equals
        len(action_required())). When acks exist, that fast path can't cheaply exclude
        acked items, so we materialize the (bounded) list to stay exact."""
        if self.repository.action_ack_fingerprints():
            return len(self.action_required())
        return (
            min(
                self.repository.count_action_required_requests(),
                _ACTION_REQUIRED_REQUEST_LIMIT,
            )
            + len(self._storage_mapping_action_required())
            + len(self._agent_stale_action_required())
            + min(
                self.repository.count_data_jobs(
                    states=_DATA_JOB_ATTENTION_STATES,
                    operations=_DATA_JOB_ATTENTION_OPERATIONS,
                    updated_since=self._data_job_attention_since(),
                ),
                _DATA_JOB_ATTENTION_SCAN_LIMIT,
            )
        )

    def _request_attention_issues(self) -> list[dict]:
        return [
            {"issue_type": "request_attention", **request}
            for request in self.repository.list_action_required()
        ]

    def _agent_stale_action_required(self) -> list[dict]:
        # Node agent freshness: look only at the LATEST report per (cluster, node, role)
        # via agent_node_current — O(#nodes), independent of history depth. A node needs
        # attention iff its CURRENT report is Stale (computed on read against the
        # configured staleness window); a newer Fresh report resolves it.
        issues: list[dict] = []
        for report in self.repository.list_agent_reports(
            latest_per_node=True, stale_seconds=self.agent_report_stale_seconds
        ):
            if report.get("freshness_status") != "Stale":
                continue
            issues.append(
                {
                    "issue_type": "agent_report_stale",
                    "report_id": report["report_id"],
                    "cluster_name": report["cluster_name"],
                    "node_name": report["node_name"],
                    "worker_role": report["worker_role"],
                    "reported_at": report.get("reported_at"),
                }
            )
        return issues

    def _storage_mapping_action_required(self) -> list[dict]:
        issues: list[dict] = []
        for mapping in self.repository.list_storage_mappings(limit=10000):
            if mapping["sanity_status"] == "Failed":
                issues.append(
                    {
                        "issue_type": "storage_mapping_failed",
                        "storage_name": mapping["storage_name"],
                        "sanity_status": mapping["sanity_status"],
                        "sanity_result": mapping["sanity_result"],
                    }
                )
            elif mapping["sanity_status"] == "Unknown":
                issues.append(
                    {
                        "issue_type": "storage_mapping_unknown",
                        "storage_name": mapping["storage_name"],
                        "sanity_status": mapping["sanity_status"],
                    }
                )
            for error in mapping["sanity_result"].get("errors", []):
                if error.get("code") in {
                    "storage_class_missing",
                    "csi_driver_mismatch",
                }:
                    issues.append(
                        {
                            "issue_type": error["code"],
                            "storage_name": mapping["storage_name"],
                            "message": error.get("message"),
                        }
                    )
            # DM readiness only applies to agent-backed (filesystem) mappings;
            # agentless CSI mappings legitimately have none, so their Missing
            # readiness must not surface as an action item.
            backend_type = (mapping.get("backend_template") or {}).get("backend_type")
            readiness = mapping.get("readiness") or {}
            if backend_type in _AGENT_BACKED_BACKENDS:
                if readiness.get("data_management") == "Missing":
                    issues.append(
                        {
                            "issue_type": "missing_dm_readiness",
                            "storage_name": mapping["storage_name"],
                        }
                    )
        return issues

    def request_history(self, request_id: str) -> dict:
        return {
            "request": self.repository.get_request(request_id),
            "plan": self.repository.get_plan_by_request(request_id),
            "results": self.repository.get_results(request_id),
            "transitions": self.repository.list_state_transitions(request_id),
        }

    def _data_management_action_required(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        # SQL-filter to the matching (operation, state) jobs directly (via
        # idx_data_jobs_operation_state) rather than scanning the newest N jobs of any
        # kind and filtering in Python — the latter silently drops attention items once
        # total jobs outgrow the window (~10k/day). One issue per job; the cap matches
        # count_data_jobs so action_required_count() stays exact.
        for job in self.repository.list_data_jobs(
            limit=_DATA_JOB_ATTENTION_SCAN_LIMIT,
            operations=_DATA_JOB_ATTENTION_OPERATIONS,
            states=_DATA_JOB_ATTENTION_STATES,
            updated_since=self._data_job_attention_since(),
        ):
            preflight = job.get("preflight_result") or {}
            result_summary = job.get("result_summary") or {}
            if job["state"] == "PreflightFailed":
                reason = preflight.get("reason") or result_summary.get("reason")
            else:
                reason = result_summary.get("reason") or preflight.get("reason")
            reason = reason or result_summary.get("status") or job["state"]
            issue_type = _data_job_issue_type(job, reason)
            issues.append(
                {
                    "issue_type": issue_type,
                    "severity": "WARN" if job["state"] == "Cancelled" else "ERROR",
                    "resource_kind": ResourceKind.DATA_JOB.value,
                    "job_id": job["job_id"],
                    "request_id": job["request_id"],
                    "requester_id": job.get("requester_id"),
                    "operation": job["operation"],
                    "storage_name": job["storage_name"],
                    "target": job.get("normalized_target") or {},
                    "state": job["state"],
                    "reason": reason,
                    "preflight_result": preflight,
                    "result_summary": result_summary,
                    "updated_at": job["updated_at"],
                    "recommended_action": "inspect data job detail and remediate data-management preflight/runtime issue",
                }
            )
        return issues

    def stale_or_recovery_runs(
        self, *, limit: int = 100, offset: int = 0
    ) -> list[dict]:
        return self.repository.list_runs(
            states=(
                LifecycleState.STALE_CLAIM.value,
                LifecycleState.RECOVERY_NEEDED.value,
                LifecycleState.BLOCKED.value,
                LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
            ),
            limit=limit,
            offset=offset,
        )

    def active_plans(
        self,
        *,
        statuses: tuple[str, ...] | None = None,
        worker_role: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return [
            _plan_summary(plan)
            for plan in self.repository.list_active_plans(
                statuses=statuses,
                worker_role=worker_role,
                limit=limit,
            )
        ]

    def active_runs(
        self,
        *,
        states: tuple[str, ...] | None = None,
        worker_role: str | None = None,
        worker_id: str | None = None,
        lease_expiring_within_seconds: int = 60,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        runs = self.repository.list_active_runs(
            states=states,
            worker_role=worker_role,
            worker_id=worker_id,
            limit=limit,
            offset=offset,
        )
        summaries = [
            _run_summary(
                run,
                lease_expiring_within_seconds=lease_expiring_within_seconds,
            )
            for run in runs
        ]
        return summaries

    def work_summary(
        self, *, lease_expiring_within_seconds: int = 60
    ) -> dict[str, Any]:
        # Totals come from exact COUNT(*) so they never saturate at the list cap
        # (production accumulates thousands of jobs/day). The capped lists below
        # only feed the by_* breakdowns / lease-expiry derivation, which are
        # naturally bounded by the in-flight (active) set.
        plans = self.active_plans(limit=1000)
        active_runs = self.active_runs(
            lease_expiring_within_seconds=lease_expiring_within_seconds,
            limit=1000,
        )
        return {
            "plans": {
                "total_active": self.repository.count_active_plans(),
                "by_status": _count_by(plans, "status"),
                "by_worker_role": _count_by(plans, "worker_role"),
            },
            "runs": {
                "total_active": self.repository.count_active_runs(),
                "by_state": _count_by(active_runs, "state"),
                "by_worker_role": _count_by(active_runs, "worker_role"),
                "by_worker_id": _count_by(active_runs, "worker_id"),
                "lease_expiring_soon": sum(
                    1 for run in active_runs if run["lease_expiring_soon"]
                ),
                "stale_or_recovery": self.repository.count_runs(
                    states=ATTENTION_RUN_STATES
                ),
            },
            "requests": {"action_required": self.action_required_count()},
        }

    def drain_status(self) -> dict[str, Any]:
        control_state = self.repository.control_state()
        active_runs = self.active_runs(limit=1000)
        blocked_or_recovery = self.repository.list_runs(
            states=ATTENTION_RUN_STATES,
            limit=1000,
        )
        hard_blockers = [
            run
            for run in blocked_or_recovery
            if run.get("state")
            in {
                LifecycleState.RECOVERY_NEEDED.value,
                LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
                LifecycleState.BACKEND_APPLY_FAILED.value,
            }
        ]
        ready_for_shutdown = (
            bool(control_state.get("scheduling_blocked"))
            and not active_runs
            and not hard_blockers
        )
        return {
            "control_state": control_state,
            "active_runs": {
                "count": len(active_runs),
                "states": _count_by(active_runs, "state"),
                "runs": active_runs,
            },
            "blocked_or_recovery_runs": {
                "count": len(blocked_or_recovery),
                "states": _count_by(blocked_or_recovery, "state"),
                "runs": blocked_or_recovery,
            },
            "action_required": {"count": self.action_required_count()},
            "ready_for_shutdown": ready_for_shutdown,
        }

    def resume_blockers(self) -> list[dict[str, Any]]:
        return [
            run
            for run in self.repository.list_runs(
                states=(
                    LifecycleState.RECOVERY_NEEDED.value,
                    LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
                    LifecycleState.BACKEND_APPLY_FAILED.value,
                ),
                limit=1000,
            )
        ]

    def worker_agent_health(self) -> dict:
        return {
            "runs": self.repository.list_runs(limit=50),
            "agent_reports": self.repository.list_agent_reports(limit=50),
        }

    def data_job_status(self, job_id: str) -> dict:
        job = self.repository.get_data_job(job_id)
        request = self.repository.get_request(job["request_id"])
        plan = self.repository.get_plan_by_request(job["request_id"])
        return {
            **job,
            "requester_id": request["requester_id"],
            "request_actor": request["actor"],
            "request_status": request["status"],
            "resource_key": request["resource_key"],
            "request_payload": request["payload_summary"],
            "plan": plan,
            "results": self.repository.get_results(job["request_id"]),
        }

    def diagnostic_correlation(self, correlation_id: str) -> list[dict]:
        return self.observability.list_events(correlation_id=correlation_id)


def _plan_summary(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "plan_id": plan["plan_id"],
        "request_id": plan["request_id"],
        "requester_id": plan.get("requester_id"),
        "worker_role": plan["worker_role"],
        "status": plan["status"],
        "operation_kind": plan["operation_kind"],
        "resource_kind": plan.get("resource_kind"),
        "resource_key": plan["resource_key"],
        "attempt_count": plan["attempt_count"],
        "created_at": plan["created_at"],
        "updated_at": plan["updated_at"],
        "request_status": plan.get("request_status"),
    }


def _run_summary(
    run: dict[str, Any], *, lease_expiring_within_seconds: int
) -> dict[str, Any]:
    remaining = _seconds_until(run.get("lease_expires_at"))
    return {
        "run_id": run["run_id"],
        "plan_id": run["plan_id"],
        "request_id": run["request_id"],
        "requester_id": run.get("requester_id"),
        "worker_id": run["worker_id"],
        "executor_id": run["executor_id"],
        "worker_role": run["worker_role"],
        "state": run["state"],
        "lease_expires_at": run["lease_expires_at"],
        "heartbeat_at": run["heartbeat_at"],
        "lease_seconds_remaining": remaining,
        "lease_expiring_soon": remaining <= lease_expiring_within_seconds,
        "operation_kind": run.get("operation_kind"),
        "resource_kind": run.get("resource_kind"),
        "resource_key": run.get("resource_key"),
        "plan_status": run.get("plan_status"),
        "request_status": run.get("request_status"),
        "started_at": run.get("started_at"),
        "updated_at": run.get("updated_at"),
    }


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _seconds_until(value: str | None) -> int:
    if not value:
        return 0
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        remaining = (parsed.astimezone(UTC) - datetime.now(UTC)).total_seconds()
        return int(remaining)
    except ValueError:
        return 0


def _data_job_issue_type(job: dict[str, Any], reason: Any) -> str:
    reason_text = str(reason or "").lower()
    if "deferred" in reason_text:
        return "data_job_nsync_deferred"
    if job["state"] == "PreflightFailed":
        if "policy" in reason_text or reason_text == "nsync_disabled":
            return "data_job_policy_failed"
        if "identity" in reason_text or "ldap" in reason_text:
            return "data_job_identity_unresolved"
        if "permission" in reason_text or "posix" in reason_text:
            return "data_job_permission_denied"
        if "candidate" in reason_text or "node" in reason_text:
            return "data_job_no_ready_candidate"
        return "data_job_preflight_failed"
    if job["state"] == "TimedOut":
        return "data_job_volcano_timeout"
    if "artifact" in reason_text:
        return "data_job_artifact_parse_failed"
    if "volcano" in reason_text or "mpi" in reason_text or "scheduler" in reason_text:
        return "data_job_volcano_failed"
    if job["state"] == "Cancelled":
        return "data_job_cancelled"
    return "data_job_failed"

