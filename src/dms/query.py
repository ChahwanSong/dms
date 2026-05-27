from __future__ import annotations

from dataclasses import dataclass

from .domain import LifecycleState
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
        return issues

    def request_history(self, request_id: str) -> dict:
        return {
            "request": self.repository.get_request(request_id),
            "plan": self.repository.get_plan_by_request(request_id),
            "results": self.repository.get_results(request_id),
            "transitions": self.repository.list_state_transitions(request_id),
        }

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
