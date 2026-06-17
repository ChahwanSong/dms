from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from dms.adapters import (
    AdapterResult,
    IdentityLookupResult,
    StubIdentityLookupAdapter,
    KubernetesVolcanoAdapter,
    StubVolcanoAdapter,
    _write_mpi_metadata_artifacts,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import (
    DataManagementPolicyInput,
    DataJobState,
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
        dm_policy_default_worker_nodes=1,
        dm_policy_default_processes_per_node=1,
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
    # No identity_lookup configured -> DM fails closed (no LDAP) before touching Volcano.
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=None,
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    assert job["state"] == DataJobState.PREFLIGHT_FAILED.value
    assert job["preflight_result"]["reason"] == "ldap_not_configured"
    assert adapter.calls == []
    action_required = harness["client"].get(
        "/api/v1/operations/action-required", headers=HEADERS
    )
    assert any(
        issue["issue_type"] == "data_job_identity_unresolved"
        for issue in action_required.json()
    )


def test_scan_worker_records_preflight_volcano_artifacts_and_summary(harness):
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
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
    assert job["result_summary"]["scan_report_uri"].endswith("/dscan-report.json")
    assert job["result_summary"]["selected_node"] == "dm-1"
    assert job["result_summary"]["worker_pod_count"] == 1
    assert job["result_summary"]["process_count"] == 1
    assert job["result_summary"]["summary"]["scan_root"] == "project/input"
    assert harness["repository"].get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value


def test_phase22_scan_keeps_eligible_candidates_with_single_worker_policy(harness):
    _ingest_ready_dm_report(harness["repository"], node_name="dm-1")
    _ingest_ready_dm_report(harness["repository"], node_name="dm-2")
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    assert job["state"] == DataJobState.SUCCEEDED.value
    assert [item["node_name"] for item in job["preflight_result"]["selected_candidates"]] == [
        "dm-1",
        "dm-2",
    ]
    assert [item["node_name"] for item in job["worker_pool"]["selected_candidates"]] == [
        "dm-1",
        "dm-2",
    ]
    resource_model = job["preflight_result"]["effective_resource_model"]
    assert resource_model["scheduler_selection"] == "eligible_node_set"
    assert resource_model["eligible_nodes"] == ["dm-1", "dm-2"]
    assert resource_model["selected_node"] is None
    assert resource_model["selected_node_count"] == 1
    assert resource_model["worker_pod_count"] == 1
    assert resource_model["process_count"] == 1
    assert job["result_summary"]["selected_node"] == "dm-1"
    assert job["result_summary"]["selected_node_count"] == 1
    assert job["result_summary"]["worker_pod_count"] == 1
    assert job["result_summary"]["process_count"] == 1
    assert job["result_summary"]["scheduled_nodes"] == ["dm-1"]


def test_phase22_policy_api_and_resource_hint_clamp(harness):
    for node_name in ("dm-1", "dm-2", "dm-3"):
        _ingest_ready_dm_report(harness["repository"], node_name=node_name)

    listed = harness["client"].get("/api/v1/data-management/policies", headers=HEADERS)
    assert listed.status_code == 200
    assert {item["operation"] for item in listed.json()} == {
        "scan",
        "rm",
        "dsync",
        "nsync",
    }

    updated = harness["client"].put(
        "/api/v1/data-management/policies/scan",
        json={
            "operation": "scan",
            "default_worker_nodes": 2,
            "max_worker_nodes": 3,
            "default_processes_per_node": 2,
            "max_processes_per_node": 4,
            "default_queue": "phase22-queue",
            "default_priority_class": "phase22-priority",
            "enabled": True,
        },
        headers=HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["policy"]["default_worker_nodes"] == 2

    request_id = _submit_and_plan_scan(
        harness["client"],
        harness["repository"],
        resources={"node_count": 9, "processes_per_node": 99},
    )
    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    assert job["state"] == DataJobState.SUCCEEDED.value
    model = job["preflight_result"]["effective_resource_model"]
    assert model["worker_pod_count"] == 3
    assert model["processes_per_node"] == 4
    assert model["process_count"] == 12
    assert model["eligible_nodes"] == ["dm-1", "dm-2", "dm-3"]
    assert model["queue"] == "phase22-queue"
    assert model["priority_class"] == "phase22-priority"
    assert {item["field"] for item in model["clamp_reasons"]} == {
        "node_count",
        "processes_per_node",
    }
    assert job["result_summary"]["scheduled_nodes"] == ["dm-1", "dm-2", "dm-3"]
    assert job["result_summary"]["worker_pod_count"] == 3
    assert job["result_summary"]["processes_per_node"] == 4
    assert job["result_summary"]["process_count"] == 12


def test_phase22_scan_policy_shortage_fails_closed(harness):
    _ingest_ready_dm_report(harness["repository"], node_name="dm-1")
    harness["repository"].upsert_data_management_policy(
        DataManagementPolicyInput(
            operation="scan",
            default_worker_nodes=2,
            max_worker_nodes=3,
            default_processes_per_node=1,
            max_processes_per_node=4,
            default_queue="phase22-queue",
            enabled=True,
        ),
        actor="test",
    )
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    assert job["state"] == DataJobState.PREFLIGHT_FAILED.value
    assert job["preflight_result"]["reason"] == "insufficient_eligible_nodes"
    assert job["preflight_result"]["effective_resource_model"]["required_node_count"] == 2
    assert adapter.calls == []


def test_scan_file_artifact_parse_failure_fails_job(harness, tmp_path):
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = MissingSummaryFileAdapter(tmp_path)
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
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
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_scan(harness["client"], harness["repository"])
    adapter = DscanReportFileAdapter(tmp_path)
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
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
    tasks = {task["name"]: task for task in manifest["spec"]["tasks"]}
    assert set(tasks) == {"launcher", "worker"}
    assert manifest["spec"]["minAvailable"] == 2
    worker_spec = tasks["worker"]["template"]["spec"]
    worker_container = worker_spec["containers"][0]
    assert tasks["worker"]["replicas"] == 1
    assert worker_spec["nodeSelector"] == {"kubernetes.io/hostname": "c1-worker"}
    assert "/usr/sbin/sshd -D -e" in worker_container["command"][2]
    launcher_spec = tasks["launcher"]["template"]["spec"]
    launcher_container = launcher_spec["containers"][0]
    launcher_command = launcher_container["command"][2]
    assert launcher_spec["nodeSelector"] == {"kubernetes.io/hostname": "c1-worker"}
    assert "test -r \"$target\"" in launcher_command
    assert "runuser -u \"$DMS_POSIX_USERNAME\" --preserve-environment -- dscan" in launcher_command
    assert "mpirun --allow-run-as-root --mca pml ob1 --mca btl tcp,self" in launcher_command
    assert "summary=/dms/artifacts/${DMS_DATA_JOB_ID}/summary.json" in launcher_command
    assert "total_bytes=$(find \"$target\" -type f -printf '%s\\n'" in launcher_command

    preflight_pod = adapter._preflight_pod_manifest(
        plan, data_job, data_job["preflight_result"]
    )
    preflight_container = preflight_pod["spec"]["containers"][0]
    assert preflight_pod["spec"]["nodeSelector"] == {
        "kubernetes.io/hostname": "c1-worker"
    }
    assert preflight_container["securityContext"]["runAsUser"] == 10000
    assert "test -x \"$target\"" in preflight_container["command"][2]


def test_phase22_kubernetes_scan_manifest_uses_scheduler_eligible_set():
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
        "job_id": "job-phase21-scan",
        "request_id": "req-phase21-scan",
        "operation": OperationKind.DATA_SCAN.value,
        "storage_name": "cephfs-a",
        "target": "project/input",
        "normalized_target": {"storage_name": "cephfs-a", "path": "project/input"},
        "selected_tool": "dscan",
        "priority": 100,
        "worker_pool": {
            "selected_candidates": [
                {
                    "cluster_name": "cluster-a",
                    "node_name": "c1-worker",
                    "mount_path": "/mnt/testbed-cephfs",
                },
                {
                    "cluster_name": "cluster-a",
                    "node_name": "c1-control",
                    "mount_path": "/mnt/testbed-cephfs",
                },
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
    tasks = {task["name"]: task for task in manifest["spec"]["tasks"]}
    worker = tasks["worker"]
    worker_spec = worker["template"]["spec"]
    launcher = tasks["launcher"]
    launcher_spec = launcher["template"]["spec"]
    worker_container = worker_spec["containers"][0]
    launcher_container = launcher_spec["containers"][0]
    assert launcher["replicas"] == 1
    assert worker["replicas"] == 1
    assert worker_spec["nodeSelector"] == {}
    node_expression = worker_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]
    assert node_expression == {
        "key": "kubernetes.io/hostname",
        "operator": "In",
        "values": ["c1-worker", "c1-control"],
    }
    assert worker_spec["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ][0]["topologyKey"] == "kubernetes.io/hostname"
    launcher_values = launcher_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert launcher_values == ["c1-worker", "c1-control"]
    assert "/usr/sbin/sshd -D -e" in worker_container["command"][2]
    assert "mpirun --allow-run-as-root --mca pml ob1 --mca btl tcp,self" in launcher_container["command"][2]
    assert {"name": "DMS_SELECTED_NODE", "value": ""} in worker_container["env"]
    assert {"name": "DMS_WORKER_POD_COUNT", "value": "1"} in worker_container["env"]
    assert {"name": "DMS_MPI_PROCESS_COUNT", "value": "1"} in worker_container["env"]


def test_phase22_mpijob_manifest_uses_volcano_policy_and_worker_slots():
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
        dm_job_image="registry.local/dms-mpifileutils:test",
        dm_artifact_base_uri="file:///artifacts/dms",
        dm_kubernetes_mode="cluster",
        dm_scheduler_backend="mpi-operator",
    )
    adapter = KubernetesVolcanoAdapter(settings)
    plan = {
        "desired_state": {
            "target": {"storage_name": "cephfs-a", "path": "project/input"}
        },
        "execution_metadata": {"phase": "execution"},
    }
    data_job = {
        "job_id": "job-phase22-scan",
        "request_id": "req-phase22-scan",
        "operation": OperationKind.DATA_SCAN.value,
        "storage_name": "cephfs-a",
        "target": "project/input",
        "normalized_target": {"storage_name": "cephfs-a", "path": "project/input"},
        "selected_tool": "dscan",
        "priority": 100,
        "worker_pool": {
            "selected_candidates": [
                {"node_name": "c1-worker", "mount_path": "/mnt/testbed-cephfs"},
                {"node_name": "c1-control", "mount_path": "/mnt/testbed-cephfs"},
                {"node_name": "c1-extra", "mount_path": "/mnt/testbed-cephfs"},
            ]
        },
        "preflight_result": {
            "identity_mapping": {
                "uid": 10000,
                "gid": 10000,
                "posix_username": "alice",
            },
            "effective_resource_model": {
                "worker_pod_count": 3,
                "launcher_pod_count": 1,
                "processes_per_node": 3,
                "process_count": 9,
                "queue": "phase22-queue",
                "priority_class": "phase22-priority",
                "scheduler_selection": "eligible_node_set",
                "eligible_nodes": ["c1-worker", "c1-control", "c1-extra"],
            },
        },
    }

    [manifest] = adapter._candidate_manifests(plan, data_job)
    assert manifest["kind"] == "MPIJob"
    assert manifest["spec"]["slotsPerWorker"] == 3
    assert manifest["spec"]["runPolicy"]["schedulingPolicy"] == {
        "queue": "phase22-queue",
        "priorityClass": "phase22-priority",
        "minAvailable": 4,
    }
    worker = manifest["spec"]["mpiReplicaSpecs"]["Worker"]
    assert worker["replicas"] == 3
    launcher_command = manifest["spec"]["mpiReplicaSpecs"]["Launcher"]["template"]["spec"][
        "containers"
    ][0]["command"][2]
    assert "mpirun --allow-run-as-root --mca pml ob1 --mca btl tcp,self" in launcher_command
    assert "dscan --directory \"$DMS_MPI_SCAN_TARGET\"" in launcher_command
    assert "resolved=$(getent hosts \"$host\"" in launcher_command
    assert "printf '%s slots=%s\\n' \"$host\" \"$DMS_MPI_PROCESSES_PER_NODE\"" in launcher_command
    worker_spec = worker["template"]["spec"]
    values = worker_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert values == ["c1-worker", "c1-control", "c1-extra"]
    assert worker_spec["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ][0]["topologyKey"] == "kubernetes.io/hostname"


def test_phase22_mpijob_name_stays_short_for_mpi_dns():
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
        dm_job_image="registry.local/dms-mpifileutils:test",
        dm_artifact_base_uri="file:///artifacts/dms",
        dm_kubernetes_mode="cluster",
        dm_scheduler_backend="mpi-operator",
    )
    adapter = KubernetesVolcanoAdapter(settings)
    manifest = adapter._mpijob_manifest(
        {"desired_state": {"target": {"storage_name": "cephfs-a", "path": "project/input"}}},
        {
            "job_id": "job-phase22-scan-with-a-very-long-identifier-20260604223053",
            "request_id": "req-phase22-scan",
            "operation": OperationKind.DATA_SCAN.value,
            "storage_name": "cephfs-a",
            "target": "project/input",
            "normalized_target": {"storage_name": "cephfs-a", "path": "project/input"},
            "selected_tool": "dscan",
            "priority": 200,
            "worker_pool": {
                "selected_candidates": [
                    {"cluster_name": "cluster-a", "node_name": "c1-control", "mount_path": "/mnt/a"},
                    {"cluster_name": "cluster-a", "node_name": "c1-worker", "mount_path": "/mnt/a"},
                ]
            },
            "preflight_result": {
                "identity_mapping": {"uid": 10000, "gid": 10000, "posix_username": "alice"},
                "effective_resource_model": {
                    "worker_pod_count": 2,
                    "process_count": 2,
                    "processes_per_node": 1,
                    "queue": "phase22-queue",
                    "priority_class": "phase22-priority",
                },
            },
        },
    )

    name = manifest["metadata"]["name"]
    assert len(name) <= 42
    assert len(f"{name}-worker-0") <= 63


def test_phase22_mpi_metadata_artifacts_preserve_runtime_mpirun_fields(tmp_path):
    artifact_dir = tmp_path / "job-metadata"
    mpi_dir = artifact_dir / "mpi"
    mpi_dir.mkdir(parents=True)
    (mpi_dir / "mpirun.json").write_text(
        json.dumps({"role_map": "0:src,1:dst", "interface_mode": "auto"}),
        encoding="utf-8",
    )
    manifest = {
        "apiVersion": "kubeflow.org/v2beta1",
        "kind": "MPIJob",
        "metadata": {"name": "dms-scan", "namespace": "dms"},
        "spec": {
            "slotsPerWorker": 3,
            "runPolicy": {"schedulingPolicy": {"minAvailable": 4}},
        },
    }
    data_job = {
        "job_id": "job-metadata",
        "operation": OperationKind.DATA_SCAN.value,
        "selected_tool": "dscan",
        "preflight_result": {
            "effective_resource_model": {
                "worker_pod_count": 3,
                "processes_per_node": 3,
                "process_count": 9,
                "queue": "phase22-queue",
                "priority_class": "phase22-priority",
            }
        },
    }

    _write_mpi_metadata_artifacts(
        artifact_uri=f"file://{artifact_dir}",
        manifest=manifest,
        data_job=data_job,
        phase="execution",
        observed={
            "phase": "Succeeded",
            "job_ref": "mpijob://dms/dms-scan",
            "pod_summary": {
                "pods": [
                    {"name": "launcher", "role": "launcher", "node_name": "n1"},
                    {"name": "worker-0", "role": "worker", "node_name": "n1"},
                    {"name": "worker-1", "role": "worker", "node_name": "n2"},
                    {"name": "worker-2", "role": "worker", "node_name": "n3"},
                ]
            },
        },
    )

    assert (mpi_dir / "submitted.yaml").read_text(encoding="utf-8").startswith(
        'apiVersion: "kubeflow.org/v2beta1"'
    )
    assert json.loads((mpi_dir / "workers.json").read_text(encoding="utf-8"))[
        "worker_pod_count"
    ] == 3
    mpirun = json.loads((mpi_dir / "mpirun.json").read_text(encoding="utf-8"))
    assert mpirun["role_map"] == "0:src,1:dst"
    assert mpirun["process_count"] == 9
    assert mpirun["exit_phase"] == "Succeeded"


def test_phase22_terminate_job_supports_mpijob_refs(monkeypatch):
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
    )
    adapter = KubernetesVolcanoAdapter(settings)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)

        class Completed:
            returncode = 0
            stdout = "deleted"
            stderr = ""

        return Completed()

    monkeypatch.setattr("dms.adapters.subprocess.run", fake_run)

    result = adapter.terminate_job("mpijob://dms/dms-scan")

    assert result.observed_state["phase"] == "Cancelled"
    assert calls == [
        ["kubectl", "-n", "dms", "delete", "mpijob", "dms-scan", "--ignore-not-found"]
    ]


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


def _submit_and_plan_scan(
    client: TestClient,
    repository: DmsRepository,
    *,
    resources: dict[str, int] | None = None,
) -> str:
    payload = {
        "requester_id": "portal:alice",
        "target": {"storage_name": "cephfs-a", "path": "project/input"},
        "priority": "Mid",
        "options": {"summary_only": True},
    }
    if resources:
        payload["resources"] = resources
    response = client.post(
        "/api/v1/data-management/scan",
        json=payload,
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


def _identity_lookup() -> StubIdentityLookupAdapter:
    result = IdentityLookupResult(
        provider="ldap",
        posix_username="alice",
        uid=10000,
        primary_gid=10000,
        groups=["dms-users"],
        user_dn="uid=alice,ou=people,dc=test",
        source_metadata={"adapter": "stub"},
    )
    return StubIdentityLookupAdapter(mappings={("ldap", "portal:alice"): result})


def _ingest_ready_dm_report(
    repository: DmsRepository, *, node_name: str = "dm-1"
) -> None:
    repository.ingest_agent_report(
        {
            "schema_version": "phase19.v1",
            "reported_at": "2026-06-03T00:00:00+00:00",
            "cluster_name": "cluster-a",
            "node_name": node_name,
            "node_uid": f"uid-{node_name}",
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
