from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .adapters import (
    KubernetesNamespaceQuotaAdapter,
    effective_resource_quota_warnings,
    kubernetes_resource_quota_hard_issues,
    kubernetes_resource_quota_value_to_base_units,
    render_kubernetes_resource_quota_hard,
)
from .backends.cephfs import CEPHFS_BACKEND_TYPE
from .backends.gpfs import GPFS_BACKEND_TYPE
from .backends.weka import WEKAFS_BACKEND_TYPE
from .domain import (
    DataJobState,
    KubernetesNamespaceQuotaKey,
    LifecycleState,
    OperationKind,
    ResourceKind,
)
from .repositories import (
    ATTENTION_RUN_STATES,
    DmsRepository,
    ObservabilityRepository,
)

# Filesystem backends run RM/DM node agents (they SSH into the storage node to
# mutate the filesystem); agentless mappings (k8s CSI / namespace-quota) apply via
# the cluster API server and run no node agent — so a "Missing" RM/DM readiness on
# them is expected, not an action item.
_AGENT_BACKED_BACKENDS = frozenset(
    {CEPHFS_BACKEND_TYPE, GPFS_BACKEND_TYPE, WEKAFS_BACKEND_TYPE}
)

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
    DataJobState.CANCELLED.value,
)


@dataclass
class OperationalQueryService:
    repository: DmsRepository
    observability: ObservabilityRepository
    # Freshness window for latest-per-node staleness (defaults to the Settings default;
    # create_app injects settings.agent_report_stale_seconds). Freshness is computed on
    # read from agent_node_current, so this threshold decides which CURRENT node reports
    # count as Stale action items.
    agent_report_stale_seconds: int = 300

    def action_required(self) -> list[dict]:
        # Order preserved (request → storage → agent → kubernetes → filesystem → data) so
        # the composite list is byte-for-byte what consumers already render. Each source
        # is a discrete helper so action_required_count() can size them with cheap counts
        # (or their own bounded len) WITHOUT building the full composite list.
        issues: list[dict] = list(self._request_attention_issues())
        issues.extend(self._storage_mapping_action_required())
        issues.extend(self._agent_stale_action_required())
        issues.extend(self._kubernetes_quota_action_required())
        issues.extend(self._filesystem_action_required())
        issues.extend(self._data_management_action_required())
        return issues

    def action_required_count(self) -> int:
        """Exact count of action_required() items via cheap per-source COUNT(*) (plus the
        bounded len of the small complex sources) instead of materializing the full
        composite list. INVARIANT: equals len(action_required()) for the same DB state —
        each term mirrors its list source's cardinality (and its cap), so the dashboard
        tile never drifts from the panel."""
        return (
            min(
                self.repository.count_action_required_requests(),
                _ACTION_REQUIRED_REQUEST_LIMIT,
            )
            + len(self._storage_mapping_action_required())
            + len(self._agent_stale_action_required())
            + len(self._kubernetes_quota_action_required())
            + len(self._filesystem_action_required())
            + min(
                self.repository.count_data_jobs(
                    states=_DATA_JOB_ATTENTION_STATES,
                    operations=_DATA_JOB_ATTENTION_OPERATIONS,
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
            # RM/DM readiness only applies to agent-backed (filesystem) mappings;
            # agentless CSI / namespace-quota mappings legitimately have none, so
            # their Missing readiness must not surface as an action item.
            backend_type = (mapping.get("backend_template") or {}).get("backend_type")
            readiness = mapping.get("readiness") or {}
            if backend_type in _AGENT_BACKED_BACKENDS:
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

        resource_key = KubernetesNamespaceQuotaKey(
            cluster_name, namespace_name
        ).as_string()
        resource_quota_name = "dms-storage-quota"
        diagnostics: list[dict[str, Any]] = []

        db_section: dict[str, Any] = {
            "queried": source in {"both", "db"},
            "exists": False,
        }
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
                            live_section.get("status_hard")
                            or live_section.get("spec_hard")
                            or {},
                            live_section.get("status_used") or {},
                        )
                except (
                    Exception
                ) as exc:  # noqa: BLE001 - query API should return partial diagnostics.
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
        brief: bool = False,
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
            item: dict[str, Any] = {
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
            }
            if not brief:
                request_summary = self._filesystem_request_summary(
                    resource["resource_key"]
                )
                item["recent_requests"] = request_summary["recent_requests"]
                item["last_block_request_id"] = request_summary["last_block_request_id"]
                item["last_block_status"] = request_summary["last_block_status"]
            items.append(item)
        return items

    def kubernetes_namespace_quota_expiring(
        self,
        *,
        cluster_name: str | None = None,
        namespace_name: str | None = None,
        resource_type: str | None = None,
        status: str = "expired",
        before: str | None = None,
        within_seconds: int | None = None,
        include_blocked: bool = False,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        resources = self.repository.list_kubernetes_namespace_quota_resources_expiring(
            cluster_name=cluster_name,
            namespace_name=namespace_name,
            resource_type=resource_type,
            status=status,
            before=before,
            within_seconds=within_seconds,
            include_blocked=include_blocked,
            limit=limit or 1000,
        )
        items: list[dict[str, Any]] = []
        for resource in resources:
            request_summary = self._resource_request_summary(resource["resource_key"])
            items.append(
                {
                    "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
                    "resource_key": resource["resource_key"],
                    "cluster_name": resource.get("cluster_name"),
                    "namespace_name": resource.get("namespace_name"),
                    "resource_type": resource.get("resource_type"),
                    "status": resource["status"],
                    "version": resource["version"],
                    "expires_at": resource.get("expires_at"),
                    "expired": resource.get("expired"),
                    "expiring": resource.get("expiring"),
                    "seconds_overdue": resource.get("seconds_overdue"),
                    "block_state": resource.get("block_state"),
                    "resource_quota_name": resource.get("resource_quota_name"),
                    "desired_hard": resource.get("desired_hard") or {},
                    "updated_at": resource["updated_at"],
                    "recent_requests": request_summary["recent_requests"],
                    "last_check_request_id": request_summary["last_check_request_id"],
                    "last_check_status": request_summary["last_check_status"],
                    "last_sync_request_id": request_summary["last_sync_request_id"],
                    "last_sync_status": request_summary["last_sync_status"],
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
        last_check = _first_request(recent, OperationKind.K8S_QUOTA_CHECK.value)
        last_sync = _first_request(recent, OperationKind.K8S_QUOTA_SYNC.value)
        return {
            "recent_requests": recent,
            "last_check_request_id": (
                last_check.get("request_id") if last_check else None
            ),
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
            "last_block_request_id": (
                last_block.get("request_id") if last_block else None
            ),
            "last_block_status": last_block.get("status") if last_block else None,
        }

    def _kubernetes_quota_action_required(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for (
            resource
        ) in self.repository.list_kubernetes_namespace_quota_resources_expiring(
            status="expired",
            include_blocked=False,
            limit=5000,
        ):
            issues.append(
                {
                    "issue_type": "kubernetes_quota_expired_unblocked",
                    "severity": "WARN",
                    "resource_kind": ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
                    "resource_key": resource["resource_key"],
                    "cluster_name": resource.get("cluster_name"),
                    "namespace_name": resource.get("namespace_name"),
                    "resource_type": resource.get("resource_type"),
                    "expires_at": resource.get("expires_at"),
                    "seconds_overdue": resource.get("seconds_overdue"),
                    "updated_at": resource.get("updated_at"),
                    "recommended_action": (
                        "run Kubernetes namespace quota expiration sweep or manually block the resource"
                    ),
                }
            )
        rows = self.repository.list_results_for_operations(
            operations=(
                OperationKind.K8S_QUOTA_AUDIT.value,
                OperationKind.K8S_QUOTA_CHECK.value,
                OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value,
            ),
            limit=5000,
        )
        seen_resources: set[str] = set()
        for row in rows:
            if row["operation"] == OperationKind.K8S_QUOTA_EXPIRATION_SWEEP.value:
                targets = row["verification_summary"].get("targets") or []
                for target in targets:
                    if target.get("result") == "failed":
                        issues.append(
                            _action_issue(
                                issue_type="kubernetes_quota_expiration_sweep_failed",
                                severity="WARN",
                                resource_key=target.get("resource_key")
                                or row["resource_key"],
                                cluster_name=target.get("cluster_name"),
                                namespace_name=target.get("namespace_name"),
                                source=row,
                                detail=target,
                            )
                        )
                    elif target.get("result") == "skipped" and target.get(
                        "reason"
                    ) not in {
                        "already_blocked",
                    }:
                        issues.append(
                            _action_issue(
                                issue_type="kubernetes_quota_expiration_sweep_skipped",
                                severity="WARN",
                                resource_key=target.get("resource_key")
                                or row["resource_key"],
                                cluster_name=target.get("cluster_name"),
                                namespace_name=target.get("namespace_name"),
                                source=row,
                                detail=target,
                            )
                        )
                continue
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

        # Soft-deleted filesystems: locked + data preserved, awaiting manual removal.
        for resource in self.repository.list_filesystem_resources(
            status=["Deleted"],
            limit=5000,
        ):
            desired = resource.get("desired_state") or {}
            observed = resource.get("observed_state") or {}
            if not observed.get("soft_delete"):
                continue
            backend_type = (observed.get("backend") or {}).get("backend_type")
            if backend_type == "cephfs":
                recommended_action = (
                    "CephFS directory is locked (chown root:root + chmod 000, data preserved). "
                    "To permanently remove: rm -rf the managed directory on a backend node."
                )
            else:
                recommended_action = (
                    "GPFS fileset is locked (chown root:root + chmod 000, data preserved, fileset remains linked). "
                    "To permanently remove: mmunlinkfileset {fs} {fileset} then mmdelfileset {fs} {fileset}."
                )
            issues.append(
                {
                    "issue_type": "filesystem_soft_deleted",
                    "severity": "INFO",
                    "resource_kind": ResourceKind.FILESYSTEM.value,
                    "resource_key": resource["resource_key"],
                    "storage_name": desired.get("storage_name"),
                    "directory_name": desired.get("directory_name"),
                    "fileset_name": observed.get("fileset_name"),
                    "updated_at": resource.get("updated_at"),
                    "recommended_action": recommended_action,
                }
            )

        for resource in self.repository.list_filesystem_resources_expiring(
            status="expired",
            include_blocked=False,
            limit=5000,
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
                OperationKind.FILESYSTEM_CREATE.value,
                OperationKind.FILESYSTEM_UPDATE.value,
                OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
                OperationKind.FILESYSTEM_BLOCK.value,
                OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
                OperationKind.FILESYSTEM_IMPORT.value,
                OperationKind.FILESYSTEM_CHECK.value,
                OperationKind.FILESYSTEM_SYNC.value,
            ),
            limit=5000,
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
                    elif target.get("result") == "skipped" and target.get(
                        "reason"
                    ) not in {
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
            quota_operations = {
                OperationKind.FILESYSTEM_CREATE.value,
                OperationKind.FILESYSTEM_UPDATE.value,
                OperationKind.FILESYSTEM_ASSIGN_QUOTA.value,
                OperationKind.FILESYSTEM_IMPORT.value,
                OperationKind.FILESYSTEM_CHECK.value,
                OperationKind.FILESYSTEM_SYNC.value,
            }
            issue_key = (
                "quota" if row["operation"] in quota_operations else "block",
                resource_key,
            )
            if issue_key in seen_targets:
                continue
            seen_targets.add(issue_key)
            summary = row["verification_summary"]
            summary_issues = list(summary.get("issues") or [])
            if (
                row["terminal_status"] == LifecycleState.SUCCEEDED.value
                and not summary_issues
            ):
                continue
            if summary_issues:
                for detail in summary_issues:
                    issue_type = detail.get("issue_type") or _filesystem_issue_type(
                        {"issues": [detail]}
                    )
                    issues.append(
                        _filesystem_action_issue(
                            issue_type=issue_type,
                            target={
                                "resource_key": resource_key,
                                "storage_name": row["payload_summary"].get(
                                    "storage_name"
                                ),
                                "directory_name": row["payload_summary"].get(
                                    "directory_name"
                                ),
                                "reason": detail.get("reason") or summary.get("reason"),
                                "issues": [detail],
                                **detail,
                            },
                            source=row,
                        )
                    )
                continue
            issues.append(
                _filesystem_action_issue(
                    issue_type=_filesystem_issue_type(summary),
                    target={
                        "resource_key": resource_key,
                        "storage_name": row["payload_summary"].get("storage_name"),
                        "directory_name": row["payload_summary"].get("directory_name"),
                        "reason": summary.get("reason") or row.get("message"),
                        "issues": [],
                    },
                    source=row,
                )
            )
        return issues

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


KUBERNETES_QUOTA_OPERATIONS = {
    OperationKind.K8S_QUOTA_CREATE,
    OperationKind.K8S_QUOTA_UPDATE,
    OperationKind.K8S_QUOTA_BLOCK,
    OperationKind.K8S_QUOTA_DELETE,
    OperationKind.K8S_QUOTA_SYNC,
    OperationKind.K8S_QUOTA_CHECK,
    OperationKind.K8S_QUOTA_AUDIT,
    OperationKind.K8S_QUOTA_IMPORT,
    OperationKind.K8S_QUOTA_EXPIRATION_SWEEP,
}

FILESYSTEM_OPERATIONS = {
    OperationKind.FILESYSTEM_CREATE,
    OperationKind.FILESYSTEM_BLOCK,
    OperationKind.FILESYSTEM_DELETE,
    OperationKind.FILESYSTEM_UPDATE,
    OperationKind.FILESYSTEM_ASSIGN_QUOTA,
    OperationKind.FILESYSTEM_IMPORT,
    OperationKind.FILESYSTEM_CHECK,
    OperationKind.FILESYSTEM_SYNC,
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
    if issue_type in {
        "quota_usage_critical",
        "kubernetes_quota_query_failed",
    }:
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
        "result",
        "expires_at",
        "resource_type",
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
        if reason == "quota_drifted":
            return "filesystem_quota_drifted"
        if reason == "missing":
            return issue.get("issue_type") or "filesystem_quota_missing"
        if reason == "filesystem_unsafe_existing_directory":
            return "filesystem_unsafe_existing_directory"
    if summary.get("precondition_failed"):
        return "filesystem_block_failed"
    return "filesystem_block_verification_failed"


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
    for key in (
        "reason",
        "resource_type",
        "message",
        "result",
        "expires_at",
        "field",
        "desired",
        "live",
        "used",
        "hard",
        "used_percent",
        "management_mode",
    ):
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
    if issue_type == "filesystem_quota_drifted":
        return "run filesystem sync to accept live state or update quota to reapply desired state"
    if issue_type == "filesystem_quota_missing":
        return "reapply filesystem quota or sync DB state after review"
    if issue_type == "filesystem_import_preflight_failed":
        return "repair existing directory ownership, group, marker, or access policy and retry import"
    if issue_type == "filesystem_assign_quota_failed":
        return "inspect existing directory safety and retry assign-quota"
    return "inspect filesystem block/unblock result and rerun after remediation"


def _recommended_action(issue_type: str) -> str:
    if issue_type == "kubernetes_quota_drifted":
        return (
            "run update to re-apply DB desired state or run sync to accept live state"
        )
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
    if issue_type == "kubernetes_quota_expired_unblocked":
        return "run Kubernetes namespace quota expiration sweep or manually block the resource"
    if issue_type == "kubernetes_quota_expiration_sweep_failed":
        return "inspect failed target and rerun sweep or manually block"
    if issue_type == "kubernetes_quota_expiration_sweep_skipped":
        return "review skipped Kubernetes namespace quota expiration target"
    return "review Kubernetes quota state"


def _desired_quota_hard(desired_state: dict[str, Any]) -> dict[str, str]:
    if desired_state.get("resource_quota_hard"):
        return dict(desired_state["resource_quota_hard"])
    try:
        return render_kubernetes_resource_quota_hard(desired_state)
    except (TypeError, ValueError):
        return {}


def _first_request(
    requests: list[dict[str, Any]], operation: str
) -> dict[str, Any] | None:
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
        return {
            "status": "Missing",
            "issues": [{"field": "resource_quota", "reason": "missing"}],
        }
    if db_exists and not live_exists:
        return {
            "status": "Missing",
            "issues": [{"field": "resource_quota", "reason": "missing"}],
        }
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
            item["percent_used"] = (
                round((used_units / hard_units) * 100, 2) if hard_units else None
            )
        except (TypeError, ValueError, ZeroDivisionError):
            item["parse_error"] = True
        summary[key] = item
    return summary
