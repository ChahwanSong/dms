from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dms.adapters import AdapterResult, KubernetesVolcanoAdapter, StubVolcanoAdapter
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import (
    DataJobState,
    IdentityMappingInput,
    LifecycleState,
    OperationKind,
    StorageMappingInput,
)
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import DMWorkerRuntime


HEADERS = {"x-dms-actor": "api-client"}


@pytest.fixture()
def harness(tmp_path):
    operational_url = f"sqlite:///{tmp_path / 'operational.db'}"
    observability_url = f"sqlite:///{tmp_path / 'observability.db'}"
    settings = Settings(
        database_url=operational_url,
        observability_database_url=observability_url,
        worker_lease_seconds=300,
        dm_kubernetes_mode="stub",
    )
    operational = Database(operational_url)
    observability_db = Database(observability_url)
    migrate_all(operational, observability_db)
    repository = DmsRepository(operational)
    observability = ObservabilityRepository(observability_db)
    app = create_app(settings, repository, observability)
    _register_ready_storage_mapping(repository)
    return {
        "client": TestClient(app),
        "repository": repository,
        "observability": observability,
    }


def test_scan_accepts_structured_target_and_query_filters(harness):
    response = harness["client"].post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "portal:alice",
            "target": {"storage_name": "cephfs-a", "path": "project/input"},
            "priority": "High",
            "options": {"summary_only": True, "max_depth": 4},
        },
        headers=HEADERS,
    )

    assert response.status_code == 202
    assert response.json()["target"] == {
        "storage_name": "cephfs-a",
        "path": "project/input",
    }
    assert response.json()["priority"] == "High"
    assert Planner(harness["repository"]).run_once() == 1
    job = harness["repository"].get_data_job_by_request(response.json()["request_id"])
    assert job["normalized_target"] == {
        "storage_name": "cephfs-a",
        "path": "project/input",
    }
    assert job["priority"] == 200

    listed = harness["client"].get(
        "/api/v1/operations/data-jobs",
        params={"requester_id": "portal:alice", "operation": OperationKind.DATA_SCAN.value},
        headers=HEADERS,
    )
    assert listed.status_code == 200
    assert [item["job_id"] for item in listed.json()] == [job["job_id"]]

    detail = harness["client"].get(
        f"/api/v1/data-management/scan/jobs/{job['job_id']}",
        headers=HEADERS,
    )
    assert detail.status_code == 200
    assert detail.json()["requester_id"] == "portal:alice"
    assert detail.json()["request_payload"]["target"]["path"] == "project/input"


def test_scan_rejects_conflicting_target_fields(harness):
    response = harness["client"].post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "portal:alice",
            "storage_name": "cephfs-a",
            "target_path": "project/a",
            "target": {"storage_name": "cephfs-a", "path": "project/b"},
        },
        headers=HEADERS,
    )

    assert response.status_code == 422


def test_scan_preflight_requires_active_identity_before_volcano(harness):
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    assert job["state"] == DataJobState.PREFLIGHT_FAILED.value
    assert job["preflight_result"]["reason"] == "missing_active_identity_mapping"
    assert adapter.calls == []
    action_required = harness["client"].get(
        "/api/v1/operations/action-required", headers=HEADERS
    )
    assert any(
        issue["issue_type"] == "data_job_missing_identity_mapping"
        for issue in action_required.json()
    )


def test_scan_worker_records_preflight_volcano_artifacts_and_summary(harness):
    _register_identity_mapping(harness["repository"])
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    assert job["state"] == DataJobState.SUCCEEDED.value
    assert job["preflight_result"]["status"] == "Ready"
    assert job["preflight_result"]["runtime_permission_check"]["status"] == "Ready"
    assert job["preflight_result"]["identity_mapping"]["posix_username"] == "alice"
    assert job["volcano_job_ref"]["job_ref"].startswith("volcano/")
    assert job["artifact_uri"] == f"stub://artifacts/{job['job_id']}"
    assert job["result_summary"]["report_uri"].endswith("/dscan-report.json")
    assert job["result_summary"]["summary"]["scan_root"] == "project/input"
    assert harness["repository"].get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value


def test_scan_file_artifact_parse_failure_fails_job(harness, tmp_path):
    _register_identity_mapping(harness["repository"])
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = MissingSummaryFileAdapter(tmp_path)
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    assert job["state"] == DataJobState.FAILED.value
    assert job["result_summary"]["reason"] == "data_job_artifact_parse_failed"
    assert job["artifact_uri"] is None
    action_required = harness["client"].get(
        "/api/v1/operations/action-required", headers=HEADERS
    )
    assert any(
        issue["issue_type"] == "data_job_artifact_parse_failed"
        for issue in action_required.json()
    )


def test_scan_parses_mpifileutils_dscan_report_artifact(harness, tmp_path):
    _register_identity_mapping(harness["repository"])
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = DscanReportFileAdapter(tmp_path)
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    assert job["state"] == DataJobState.SUCCEEDED.value
    assert job["result_summary"]["summary"] == {
        "file_count": 2,
        "directory_count": 1,
        "total_bytes": 0,
        "error_count": 1,
        "scan_root": "project/input",
    }
    assert job["result_summary"]["report_uri"].endswith("/dscan-report.json")
    assert job["result_summary"]["summary_source"] == "artifact"


def test_kubernetes_scan_manifest_uses_identity_security_context():
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
        dm_job_image="registry.local/dms-mpifileutils:test",
        dm_artifact_base_uri="file:///artifacts/dms",
        dm_kubernetes_mode="cluster",
    )
    adapter = KubernetesVolcanoAdapter(settings)
    plan = {
        "desired_state": {
            "target": {"storage_name": "cephfs-a", "path": "project/input"}
        }
    }
    data_job = {
        "job_id": "job-phase19",
        "request_id": "req-phase19",
        "operation": OperationKind.DATA_SCAN.value,
        "storage_name": "cephfs-a",
        "target": "project/input",
        "normalized_target": {"storage_name": "cephfs-a", "path": "project/input"},
        "selected_tool": "dscan",
        "priority": 100,
        "worker_pool": {
            "selected_candidates": [
                {"node_name": "c1-worker", "mount_path": "/mnt/testbed-cephfs"},
                {"node_name": "c1-worker", "mount_path": "/mnt/testbed-cephfs"},
            ]
        },
        "preflight_result": {
            "identity_mapping": {
                "uid": 10000,
                "gid": 10000,
                "posix_username": "alice",
            }
        },
    }

    manifest = adapter._manifest(plan, data_job)
    task = manifest["spec"]["tasks"][0]
    assert task["replicas"] == 1
    task_spec = manifest["spec"]["tasks"][0]["template"]["spec"]
    container = task_spec["containers"][0]
    assert task_spec["nodeSelector"] == {"kubernetes.io/hostname": "c1-worker"}
    assert task_spec["securityContext"]["fsGroup"] == 10000
    assert container["securityContext"]["runAsUser"] == 10000
    assert container["securityContext"]["runAsGroup"] == 10000
    assert "test -r \"$target\"" in container["command"][2]
    assert "dscan --directory \"$target\" --output \"$report\"" in container["command"][2]
    assert "test -f \"$report\"" in container["command"][2]
    assert "summary=/dms/artifacts/${DMS_DATA_JOB_ID}/summary.json" in container["command"][2]
    assert "total_bytes=$(find \"$target\" -type f -printf '%s\\n'" in container["command"][2]
    assert "test -f \"$summary\"" in container["command"][2]

    preflight_pod = adapter._preflight_pod_manifest(
        plan, data_job, data_job["preflight_result"]
    )
    preflight_container = preflight_pod["spec"]["containers"][0]
    assert preflight_pod["spec"]["nodeSelector"] == {
        "kubernetes.io/hostname": "c1-worker"
    }
    assert preflight_container["securityContext"]["runAsUser"] == 10000
    assert "test -x \"$target\"" in preflight_container["command"][2]


class MissingSummaryFileAdapter:
    def __init__(self, artifact_root) -> None:
        self.artifact_root = artifact_root
        self.calls: list[tuple[str, str]] = []

    def verify_scan_preflight(
        self, plan: dict, data_job: dict, preflight: dict
    ) -> dict:
        self.calls.append(("verify_scan_preflight", data_job["job_id"]))
        return {"status": "Ready", "source": "missing-summary-test"}

    def create_job(self, plan: dict, data_job: dict) -> AdapterResult:
        self.calls.append(("create_job", data_job["job_id"]))
        artifact_dir = self.artifact_root / data_job["job_id"]
        artifact_dir.mkdir()
        return AdapterResult(
            applied_state={
                "adapter": "missing-summary-test",
                "job_ref": f"volcano://test/{data_job['job_id']}",
            },
            observed_state={"phase": "Succeeded"},
            artifact_uri=f"file://{artifact_dir}",
        )


class DscanReportFileAdapter(MissingSummaryFileAdapter):
    def create_job(self, plan: dict, data_job: dict) -> AdapterResult:
        self.calls.append(("create_job", data_job["job_id"]))
        artifact_dir = self.artifact_root / data_job["job_id"]
        artifact_dir.mkdir()
        (artifact_dir / "dscan-report.json").write_text(
            """
            {
              "directory": "/dms/target/project/input",
              "summary": {
                "total_entries": 3,
                "total_files": 2,
                "total_directories": 1,
                "total_symlinks": 0,
                "total_other": 0
              },
              "broken_paths": [
                {"path": "/dms/target/project/input/unreadable", "reason": ["unreadable"]}
              ]
            }
            """,
            encoding="utf-8",
        )
        return AdapterResult(
            applied_state={
                "adapter": "dscan-report-test",
                "job_ref": f"volcano://test/{data_job['job_id']}",
            },
            observed_state={"phase": "Succeeded"},
            artifact_uri=f"file://{artifact_dir}",
        )


def _submit_and_plan_scan(client: TestClient, repository: DmsRepository) -> str:
    response = client.post(
        "/api/v1/data-management/scan",
        json={
            "requester_id": "portal:alice",
            "target": {"storage_name": "cephfs-a", "path": "project/input"},
            "priority": "Mid",
            "options": {"summary_only": True},
        },
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert Planner(repository).run_once() == 1
    return response.json()["request_id"]


def _register_ready_storage_mapping(repository: DmsRepository) -> None:
    sanity = {
        "storage_name": "cephfs-a",
        "status": "Ready",
        "agent_observed": {
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "dm-1"}],
        },
        "readiness": {"data_management": "Ready", "resource_management": "Ready"},
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="cephfs-a",
            backend_template={"backend_type": "cephfs"},
            cluster_name="cluster-a",
            storage_class_name="cephfs-sc",
        ),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def _register_identity_mapping(repository: DmsRepository) -> None:
    repository.upsert_identity_mapping(
        IdentityMappingInput(
            requester_id="portal:alice",
            identity_provider="ldap-main",
            posix_username="alice",
            uid=10000,
            gid=10000,
            groups=["dms-users"],
        ),
        verification_result="matched",
    )


def _ingest_ready_dm_report(repository: DmsRepository) -> None:
    repository.ingest_agent_report(
        {
            "schema_version": "phase19.v1",
            "reported_at": "2026-06-03T00:00:00+00:00",
            "cluster_name": "cluster-a",
            "node_name": "dm-1",
            "node_uid": "uid-dm-1",
            "worker_role": "DM",
            "mounts": [
                {
                    "storage_name": "cephfs-a",
                    "mount_path": "/mnt/dms/cephfs-a",
                    "status": "Ready",
                    "readable": True,
                }
            ],
            "tools": [{"name": "dscan", "status": "Ready"}],
            "credentials": [{"name": "kubernetes-service-account", "status": "Ready"}],
            "networks": [{"name": "storage-net", "status": "Ready"}],
            "identity_evidence": {
                "source": "phase19-test",
                "users": [
                    {
                        "username": "alice",
                        "status": "Ready",
                        "uid": 10000,
                        "gid": 10000,
                        "groups": ["dms-users"],
                    }
                ],
            },
            "csi": [],
        }
    )
