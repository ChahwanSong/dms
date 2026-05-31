from __future__ import annotations

from dataclasses import dataclass

from .domain import AgentReport
from .repositories import DmsRepository, ObservabilityRepository


@dataclass
class AgentReportIngestionService:
    repository: DmsRepository
    observability: ObservabilityRepository

    def ingest(self, report: AgentReport, *, actor: str) -> str:
        expected_actor = f"node:{report.cluster_name}:{report.node_name}"
        if actor != expected_actor:
            self.observability.safe_record_event(
                component="agent-ingest",
                severity="WARN",
                event_type="agent_node_identity_mismatch",
                message="agent report actor did not match reported node identity",
                payload={
                    "actor": actor,
                    "expected_actor": expected_actor,
                    "cluster_name": report.cluster_name,
                    "node_name": report.node_name,
                },
            )
            raise PermissionError("node identity mismatch")
        report_id = self.repository.ingest_agent_report(report.model_dump(mode="json"))
        self.observability.safe_record_event(
            component="agent-ingest",
            severity="INFO",
            event_type="agent_report_accepted",
            message="agent report accepted",
            payload={
                "report_id": report_id,
                "cluster_name": report.cluster_name,
                "node_name": report.node_name,
                "worker_role": report.worker_role.value,
            },
        )
        return report_id
