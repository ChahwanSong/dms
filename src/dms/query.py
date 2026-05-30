from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .adapters import (
    KubernetesNamespaceQuotaAdapter,
    effective_resource_quota_warnings,
    kubernetes_resource_quota_hard_issues,
    kubernetes_resource_quota_value_to_base_units,
    render_kubernetes_resource_quota_hard,
)
from .domain import KubernetesNamespaceQuotaKey, LifecycleState, OperationKind, ResourceKind
from .repositories import DmsRepository, ObservabilityRepository


@dataclass
class OperationalQueryService:
    repository: DmsRepository
    observability: ObservabilityRepository

    def action_required(self) -> list[dict]:
        issues: list[dict] = [
            {"issue_type": "request_attention", **request}
            for request in self.repository.list_action_required()
        ]
        for mapping in self.repository.list_storage_mappings():
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
                if error.get("code") in {"storage_class_missing", "csi_driver_mismatch"}:
                    issues.append(
                        {
                            "issue_type": error["code"],
                            "storage_name": mapping["storage_name"],
                            "message": error.get("message"),
                        }
                    )
            readiness = mapping.get("readiness") or {}
            if readiness.get("resource_management") == "Missing":
                issues.append(
                    {
                        "issue_type": "missing_rm_readiness",
                        "storage_name": mapping["storage_name"],
                    }
                )
            if readiness.get("data_management") == "Missing":
                issues.append(
                    {
                        "issue_type": "missing_dm_readiness",
                        "storage_name": mapping["storage_name"],
                    }
                )
        for report in self.repository.list_agent_reports(freshness="Stale", limit=100):
            issues.append(
                {
                    "issue_type": "agent_report_stale",
                    "report_id": report["report_id"],
                    "cluster_name": report["cluster_name"],
                    "node_name": report["node_name"],
                    "worker_role": report["worker_role"],
                }
            )
        issues.extend(self._kubernetes_quota_action_required())
        issues.extend(self._filesystem_action_required())
        return issues

    def request_history(self, request_id: str) -> dict:
        return {
            "request": self.repository.get_request(request_id),
            "plan": self.repository.get_plan_by_request(request_id),
            "results": self.repository.get_results(request_id),
            "transitions": self.repository.list_state_transitions(request_id),
        }

    def kubernetes_namespace_quota(
        self,
        *,
        cluster_name: str,
        namespace_name: str,
        source: str = "both",
        include_non_dms: bool = False,
        include_status_used: bool = True,
        kubernetes_adapter: KubernetesNamespaceQuotaAdapter | None = None,
    ) -> dict[str, Any]:
        source = source.lower()
        if source not in {"both", "db", "live"}:
            raise ValueError("source must be one of: both, db, live")

        resource_key = KubernetesNamespaceQuotaKey(cluster_name, namespace_name).as_string()
        resource_quota_name = "dms-storage-quota"
        diagnostics: list[dict[str, Any]] = []

        db_section: dict[str, Any] = {"queried": source in {"both", "db"}, "exists": False}
        desired_hard: dict[str, str] = {}
        if source in {"both", "db"}:
            resource = self.repository.get_resource(
                ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value, resource_key
            )
            if resource:
                desired_state = resource["desired_state"]
                resource_quota_name = (
                    desired_state.get("resource_quota_name") or resource_quota_name
                )
                desired_hard = _desired_quota_hard(desired_state)
                request_summary = self._resource_request_summary(resource_key)
                db_section = {
                    "queried": True,
                    "exists": True,
                    "resource_id": resource["resource_id"],
                    "resource_type": resource["resource_kind"],
                    "resource_key": resource["resource_key"],
                    "status": resource["status"],
                    "version": resource["version"],
                    "desired_state": desired_state,
                    "desired_hard": desired_hard,
                    "applied_state": resource["applied_state"],
                    "observed_state": resource["observed_state"],
                    "updated_at": resource["updated_at"],
                    "recent_requests": request_summary["recent_requests"],
                    "last_check_request_id": request_summary["last_check_request_id"],
                    "last_check_status": request_summary["last_check_status"],
                    "last_sync_request_id": request_summary["last_sync_request_id"],
                    "last_sync_status": request_summary["last_sync_status"],
                }

        live_section: dict[str, Any] = {
            "queried": source in {"both", "live"},
            "exists": False,
        }
        resource_quotas: list[dict[str, Any]] = []
        if source in {"both", "live"}:
            if kubernetes_adapter is None:
                diagnostics.append(
                    {
                        "source": "live",
                        "reason": "kubernetes_quota_adapter_unavailable",
                    }
                )
            else:
                try:
                    live_section = kubernetes_adapter.read_resource_quota(
                        cluster_name, namespace_name, resource_quota_name
                    )
                    live_section["queried"] = True
                    if not include_status_used:
                        live_section.pop("status_used", None)
                    elif live_section.get("exists"):
                        live_section["usage_summary"] = _quota_usage_summary(
                            live_section.get("status_hard") or live_section.get("spec_hard") or {},
                            live_section.get("status_used") or {},
                        )
                except Exception as exc:  # noqa: BLE001 - query API should return partial diagnostics.
                    diagnostics.append(
                        {
                            "source": "live",
                            "reason": "kubernetes_resourcequota_read_failed",
                            "message": str(exc),
                        }
                    )
                if include_non_dms:
                    try:
                        resource_quotas = kubernetes_adapter.list_resource_quotas(
                            cluster_name, namespace_name
                        )
                        if not include_status_used:
                            resource_quotas = [
                                {
                                    key: value
                                    for key, value in quota.items()
                                    if key != "status_used"
                                }
                                for quota in resource_quotas
                            ]
                    except Exception as exc:  # noqa: BLE001
                        diagnostics.append(
                            {
                                "source": "live",
                                "reason": "kubernetes_resourcequota_list_failed",
                                "message": str(exc),
                            }
                        )

        live_hard = live_section.get("spec_hard") or {}
        dms_hard = desired_hard or live_hard
        effective_warnings = (
            effective_resource_quota_warnings(
                resource_quotas=resource_quotas,
                dms_hard=dms_hard,
                resource_quota_name=resource_quota_name,
            )
            if include_non_dms and dms_hard
            else []
        )

        return {
            "cluster_name": cluster_name,
            "namespace_name": namespace_name,
            "resource_key": resource_key,
            "dms_resource_quota_name": resource_quota_name,
            "source": source,
            "db": db_section,
            "live": live_section,
            "diff": _quota_diff(
                source=source,
                db_exists=bool(db_section.get("exists")),
                live=live_section,
                desired_hard=desired_hard,
                diagnostics=diagnostics,
            ),
            "resource_quotas": resource_quotas,
            "effective_quota_warnings": effective_warnings,
            "diagnostics": diagnostics,
        }

    def filesystem_expiring(
        self,
        *,
        storage_name: str | None = None,
        status: str = "expired",
        before: str | None = None,
        within_seconds: int | None = None,
        include_blocked: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        resources = self.repository.list_filesystem_resources_expiring(
            storage_name=storage_name,
            status=status,
            before=before,
            within_seconds=within_seconds,
            include_blocked=include_blocked,
            limit=limit or 1000,
        )
        items: list[dict[str, Any]] = []
        for resource in resources:
            request_summary = self._filesystem_request_summary(resource["resource_key"])
            items.append(
                {
                    "resource_kind": ResourceKind.FILESYSTEM.value,
                    "resource_key": resource["resource_key"],
                    "storage_name": resource.get("storage_name"),
                    "directory_name": resource.get("directory_name"),
                    "resource_type": resource.get("resource_type"),
                    "status": resource["status"],
                    "version": resource["version"],
                    "expires_at": resource.get("expires_at"),
                    "expired": resource.get("expired"),
                    "expiring": resource.get("expiring"),
                    "seconds_overdue": resource.get("seconds_overdue"),
                    "block_state": resource.get("block_state"),
                    "updated_at": resource["updated_at"],
                    "recent_requests": request_summary["recent_requests"],
                    "last_block_request_id": request_summary["last_block_request_id"],
                    "last_block_status": request_summary["last_block_status"],
                }
            )
        return items

    def _resource_request_summary(self, resource_key: str) -> dict[str, Any]:
        operations = tuple(operation.value for operation in KUBERNETES_QUOTA_OPERATIONS)
        recent = self.repository.list_requests_for_resource(
            resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
            resource_key=resource_key,
            operations=operations,
            limit=20,
        )
        last_check = _first_request(
            recent, OperationKind.K8S_QUOTA_CHECK.value
        )
        last_sync = _first_request(recent, OperationKind.K8S_QUOTA_SYNC.value)
        return {
            "recent_requests": recent,
            "last_check_request_id": last_check.get("request_id") if last_check else None,
            "last_check_status": last_check.get("status") if last_check else None,
            "last_sync_request_id": last_sync.get("request_id") if last_sync else None,
            "last_sync_status": last_sync.get("status") if last_sync else None,
        }

    def _filesystem_request_summary(self, resource_key: str) -> dict[str, Any]:
        operations = tuple(operation.value for operation in FILESYSTEM_OPERATIONS)
        recent = self.repository.list_requests_for_resource(
            resource_kind=ResourceKind.FILESYSTEM.value,
            resource_key=resource_key,
            operations=operations,
            limit=20,
        )
        last_block = _first_request(recent, OperationKind.FILESYSTEM_BLOCK.value)
        return {
            "recent_requests": recent,
            "last_block_request_id": last_block.get("request_id") if last_block else None,
            "last_block_status": last_block.get("status") if last_block else None,
        }

    def _kubernetes_quota_action_required(self) -> list[dict[str, Any]]:
        rows = self.repository.list_results_for_operations(
            operations=(
                OperationKind.K8S_QUOTA_AUDIT.value,
                OperationKind.K8S_QUOTA_CHECK.value,
            ),
            limit=1000,
        )
        seen_resources: set[str] = set()
        issues: list[dict[str, Any]] = []
        for row in rows:
            summary = row["verification_summary"]
            targets = summary.get("targets") or [_target_from_check_result(row)]
            for target in targets:
                resource_key = target.get("resource_key") or row["resource_key"]
                if resource_key in seen_resources:
                    continue
                seen_resources.add(resource_key)
                issues.extend(_quota_target_action_required_issues(target, row))
        return issues

    def _filesystem_action_required(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for resource in self.repository.list_filesystem_resources_expiring(
            status="expired",
            include_blocked=False,
            limit=1000,
        ):
            resource_type = resource.get("resource_type") or "user"
            if resource_type in {"system", "admin"}:
                continue
            issues.append(
                {
                    "issue_type": "filesystem_expired_unblocked",
                    "severity": "WARN",
                    "resource_kind": ResourceKind.FILESYSTEM.value,
                    "resource_key": resource["resource_key"],
                    "storage_name": resource.get("storage_name"),
                    "directory_name": resource.get("directory_name"),
                    "expires_at": resource.get("expires_at"),
                    "updated_at": resource.get("updated_at"),
                    "recommended_action": (
                        "run filesystem expiration sweep or manually block the resource"
                    ),
                }
            )

        rows = self.repository.list_results_for_operations(
            operations=(
                OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
                OperationKind.FILESYSTEM_BLOCK.value,
            ),
            limit=1000,
        )
        seen_targets: set[tuple[str, str]] = set()
        for row in rows:
            if row["operation"] == OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value:
                targets = row["verification_summary"].get("targets") or []
                for target in targets:
                    resource_key = target.get("resource_key") or ""
                    issue_key = ("sweep", resource_key)
                    if issue_key in seen_targets:
                        continue
                    seen_targets.add(issue_key)
                    if target.get("result") == "failed":
                        issues.append(
                            _filesystem_action_issue(
                                issue_type="filesystem_expiration_sweep_partial_failure",
                                target=target,
                                source=row,
                            )
                        )
                    elif target.get("result") == "skipped" and target.get("reason") not in {
                        "already_blocked",
                    }:
                        issues.append(
                            _filesystem_action_issue(
                                issue_type="filesystem_expiration_sweep_skipped",
                                target=target,
                                source=row,
                            )
                        )
                continue
            resource_key = row["resource_key"]
            issue_key = ("block", resource_key)
            if issue_key in seen_targets:
                continue
            seen_targets.add(issue_key)
            summary = row["verification_summary"]
            if row["terminal_status"] == LifecycleState.SUCCEEDED.value and not summary.get("issues"):
                continue
            issue_type = _filesystem_issue_type(summary)
            issues.append(
                _filesystem_action_issue(
                    issue_type=issue_type,
                    target={
                        "resource_key": resource_key,
                        "storage_name": row["payload_summary"].get("storage_name"),
                        "directory_name": row["payload_summary"].get("directory_name"),
                        "reason": summary.get("reason") or row.get("message"),
                        "issues": summary.get("issues") or [],
                    },
                    source=row,
                )
            )
        return issues

    def stale_or_recovery_runs(self) -> list[dict]:
        return self.repository.list_runs(
            states=(
                LifecycleState.STALE_CLAIM.value,
                LifecycleState.RECOVERY_NEEDED.value,
                LifecycleState.BLOCKED.value,
                LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value,
            )
        )

    def worker_agent_health(self) -> dict:
        return {
            "runs": self.repository.list_runs(limit=50),
            "agent_reports": self.repository.list_agent_reports(limit=50),
        }

    def identity_issues(self) -> list[dict]:
        return [
            mapping
            for mapping in self.repository.list_identity_mappings()
            if mapping["status"] in {"Disabled", "NeedsReview", "Stale"}
        ]

    def data_job_status(self, job_id: str) -> dict:
        return self.repository.get_data_job(job_id)

    def diagnostic_correlation(self, correlation_id: str) -> list[dict]:
        return self.observability.list_events(correlation_id=correlation_id)


KUBERNETES_QUOTA_OPERATIONS = {
    OperationKind.K8S_QUOTA_CREATE,
    OperationKind.K8S_QUOTA_UPDATE,
    OperationKind.K8S_QUOTA_BLOCK,
    OperationKind.K8S_QUOTA_DELETE,
    OperationKind.K8S_QUOTA_SYNC,
    OperationKind.K8S_QUOTA_CHECK,
    OperationKind.K8S_QUOTA_AUDIT,
}

FILESYSTEM_OPERATIONS = {
    OperationKind.FILESYSTEM_CREATE,
    OperationKind.FILESYSTEM_BLOCK,
    OperationKind.FILESYSTEM_DELETE,
    OperationKind.FILESYSTEM_EXPIRATION_SWEEP,
}


def _target_from_check_result(row: dict[str, Any]) -> dict[str, Any]:
    summary = row["verification_summary"]
    cluster_name, _, namespace_name = row["resource_key"].partition(":")
    return {
        "cluster_name": cluster_name or None,
        "namespace_name": namespace_name or None,
        "resource_key": row["resource_key"],
        "issues": summary.get("issues") or [],
        "usage_pressure": summary.get("usage_pressure") or [],
        "effective_quota_warnings": summary.get("effective_quota_warnings") or [],
        "resource_status": summary.get("resource_status")
        or summary.get("consistency_status"),
    }


def _quota_target_action_required_issues(
    target: dict[str, Any], row: dict[str, Any]
) -> list[dict[str, Any]]:
    resource_key = target.get("resource_key") or row["resource_key"]
    cluster_name = target.get("cluster_name")
    namespace_name = target.get("namespace_name")
    emitted: list[dict[str, Any]] = []
    for issue in target.get("issues") or []:
        issue_type = issue.get("issue_type") or _issue_type_from_reason(issue)
        if issue_type is None:
            continue
        emitted.append(
            _action_issue(
                issue_type=issue_type,
                severity=_issue_severity(issue_type),
                resource_key=resource_key,
                cluster_name=cluster_name,
                namespace_name=namespace_name,
                source=row,
                detail=issue,
            )
        )
    for pressure in target.get("usage_pressure") or []:
        issue_type = pressure.get("issue_type")
        if not issue_type:
            continue
        emitted.append(
            _action_issue(
                issue_type=issue_type,
                severity=pressure.get("severity") or _issue_severity(issue_type),
                resource_key=resource_key,
                cluster_name=cluster_name,
                namespace_name=namespace_name,
                source=row,
                detail=pressure,
            )
        )
    for warning in target.get("effective_quota_warnings") or []:
        issue_type = warning.get("type")
        if issue_type not in {
            "non_dms_quota_more_restrictive",
            "non_dms_quota_zero_limit",
        }:
            continue
        emitted.append(
            _action_issue(
                issue_type=issue_type,
                severity=_issue_severity(issue_type),
                resource_key=resource_key,
                cluster_name=cluster_name,
                namespace_name=namespace_name,
                source=row,
                detail=warning,
            )
        )
    return emitted


def _issue_type_from_reason(issue: dict[str, Any]) -> str | None:
    reason = issue.get("reason")
    field = issue.get("field")
    if reason == "missing":
        return "kubernetes_quota_missing"
    if reason == "db_resource_missing":
        return "kubernetes_quota_live_only"
    if reason == "not_dms_managed" or str(field or "").startswith("metadata."):
        return "kubernetes_quota_metadata_drift"
    if reason in {
        "hard_limit_drifted",
        "hard_limit_missing_in_live",
        "hard_limit_unexpected_live",
    }:
        return "kubernetes_quota_drifted"
    if reason == "query_failed":
        return "kubernetes_quota_query_failed"
    return issue.get("issue_type")


def _issue_severity(issue_type: str) -> str:
    if issue_type in {"quota_usage_critical", "kubernetes_quota_query_failed"}:
        return "CRITICAL"
    return "WARN"


def _action_issue(
    *,
    issue_type: str,
    severity: str,
    resource_key: str,
    cluster_name: str | None,
    namespace_name: str | None,
    source: dict[str, Any],
    detail: dict[str, Any],
) -> dict[str, Any]:
    issue = {
        "issue_type": issue_type,
        "severity": severity,
        "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        "resource_key": resource_key,
        "cluster_name": cluster_name,
        "namespace_name": namespace_name,
        "source_request_id": source["request_id"],
        "last_seen": source["created_at"],
        "recommended_action": _recommended_action(issue_type),
    }
    for key in (
        "field",
        "key",
        "reason",
        "desired",
        "live",
        "used",
        "hard",
        "used_percent",
        "resource_quota_name",
        "dms_hard",
        "non_dms_hard",
        "message",
    ):
        if key in detail:
            issue[key] = detail[key]
    return issue


def _filesystem_issue_type(summary: dict[str, Any]) -> str:
    for issue in summary.get("issues") or []:
        issue_type = issue.get("issue_type")
        if issue_type:
            return issue_type
        reason = issue.get("reason")
        if reason == "filesystem_marker_mismatch":
            return "filesystem_marker_mismatch"
        if reason == "filesystem_block_restore_missing":
            return "filesystem_unblock_restore_missing"
        if reason == "filesystem_access_group_missing":
            return "filesystem_access_group_missing"
    if summary.get("precondition_failed"):
        return "filesystem_block_failed"
    return "filesystem_block_verification_failed"


def _filesystem_action_issue(
    *,
    issue_type: str,
    target: dict[str, Any],
    source: dict[str, Any],
) -> dict[str, Any]:
    issue = {
        "issue_type": issue_type,
        "severity": _issue_severity(issue_type),
        "resource_kind": ResourceKind.FILESYSTEM.value,
        "resource_key": target.get("resource_key") or source["resource_key"],
        "storage_name": target.get("storage_name"),
        "directory_name": target.get("directory_name"),
        "source_request_id": source["request_id"],
        "last_seen": source["created_at"],
        "recommended_action": _recommended_filesystem_action(issue_type),
    }
    for key in ("reason", "resource_type", "message", "result", "expires_at"):
        if target.get(key) is not None:
            issue[key] = target[key]
    target_issues = target.get("issues") or []
    if target_issues:
        issue["issues"] = target_issues
    return issue


def _recommended_filesystem_action(issue_type: str) -> str:
    if issue_type == "filesystem_expiration_sweep_skipped":
        return "review skipped filesystem expiration target"
    if issue_type == "filesystem_expiration_sweep_partial_failure":
        return "inspect failed target and rerun sweep or manually block"
    if issue_type == "filesystem_unblock_restore_missing":
        return "restore filesystem access manually or repair DB block_state"
    if issue_type == "filesystem_access_group_missing":
        return "restore DMS-managed LDAP access group and rerun unblock"
    if issue_type == "filesystem_marker_mismatch":
        return "inspect filesystem marker before any manual mutation"
    return "inspect filesystem block/unblock result and rerun after remediation"


def _recommended_action(issue_type: str) -> str:
    if issue_type == "kubernetes_quota_drifted":
        return "run update to re-apply DB desired state or run sync to accept live state"
    if issue_type in {"kubernetes_quota_missing", "kubernetes_quota_db_only"}:
        return "recreate DMS-managed ResourceQuota or delete DMS resource record after review"
    if issue_type in {"quota_usage_warning", "quota_usage_critical"}:
        return "increase quota, free storage, or contact namespace owner"
    if issue_type in {"non_dms_quota_more_restrictive", "non_dms_quota_zero_limit"}:
        return "review non-DMS ResourceQuota owner"
    if issue_type == "kubernetes_quota_metadata_drift":
        return "repair DMS metadata with an update/reset apply or investigate manual changes"
    if issue_type == "kubernetes_quota_query_failed":
        return "check Kubernetes API access and rerun audit"
    return "review Kubernetes quota state"


def _desired_quota_hard(desired_state: dict[str, Any]) -> dict[str, str]:
    if desired_state.get("resource_quota_hard"):
        return dict(desired_state["resource_quota_hard"])
    try:
        return render_kubernetes_resource_quota_hard(desired_state)
    except (TypeError, ValueError):
        return {}


def _first_request(requests: list[dict[str, Any]], operation: str) -> dict[str, Any] | None:
    for request in requests:
        if request.get("operation") == operation:
            return request
    return None


def _quota_diff(
    *,
    source: str,
    db_exists: bool,
    live: dict[str, Any],
    desired_hard: dict[str, str],
    diagnostics: list[dict[str, Any]],
) -> dict[str, Any]:
    if diagnostics and source in {"both", "live"}:
        return {"status": "QueryFailed", "issues": list(diagnostics)}
    if source == "db":
        return {"status": "DbOnly", "issues": []}
    live_exists = bool(live.get("exists"))
    if source == "live" and live_exists:
        return {"status": "LiveOnly", "issues": []}
    if source == "live":
        return {"status": "Missing", "issues": [{"field": "resource_quota", "reason": "missing"}]}
    if db_exists and not live_exists:
        return {"status": "Missing", "issues": [{"field": "resource_quota", "reason": "missing"}]}
    if not db_exists and live_exists:
        return {
            "status": "LiveOnly",
            "issues": [{"field": "db.resource", "reason": "db_resource_missing"}],
        }
    if not db_exists and not live_exists:
        return {
            "status": "DbMissing",
            "issues": [{"field": "db.resource", "reason": "db_resource_missing"}],
        }

    issues: list[dict[str, Any]] = []
    labels = live.get("labels") or {}
    if labels.get("app.kubernetes.io/managed-by") != "dms":
        issues.append({"field": "metadata.labels", "reason": "not_dms_managed"})
    issues.extend(
        kubernetes_resource_quota_hard_issues(
            desired_hard=desired_hard,
            live_hard=live.get("spec_hard") or {},
        )
    )
    return {"status": "Drifted" if issues else "Consistent", "issues": issues}


def _quota_usage_summary(
    hard: dict[str, Any], used: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for key, used_value in sorted(used.items()):
        if key not in hard:
            continue
        item: dict[str, Any] = {"hard": hard[key], "used": used_value}
        try:
            hard_units = kubernetes_resource_quota_value_to_base_units(key, hard[key])
            used_units = kubernetes_resource_quota_value_to_base_units(key, used_value)
            item["hard_base_units"] = hard_units
            item["used_base_units"] = used_units
            item["percent_used"] = round((used_units / hard_units) * 100, 2) if hard_units else None
        except (TypeError, ValueError, ZeroDivisionError):
            item["parse_error"] = True
        summary[key] = item
    return summary
