from __future__ import annotations

from fastapi.testclient import TestClient

from dms.adapters import (
    IdentityLookupResult,
    KubernetesVolcanoAdapter,
    StubIdentityLookupAdapter,
    StubVolcanoAdapter,
)
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import (
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


def test_sync_preview_confirm_execution_state_machine(tmp_path):
    harness = _harness(tmp_path)
    _ingest_ready_dm_report(harness["repository"])

    response = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "portal:alice",
            "source": {"storage_name": "cephfs-a", "path": "project/input"},
            "destination": {"storage_name": "cephfs-a", "path": "project/output"},
            "priority": "High",
            "options": {"contents": True},
        },
        headers=HEADERS,
    )

    assert response.status_code == 202
    assert response.json()["source"] == {
        "storage_name": "cephfs-a",
        "path": "project/input",
    }
    assert response.json()["destination"] == {
        "storage_name": "cephfs-a",
        "path": "project/output",
    }
    assert Planner(harness["repository"]).run_once() == 1
    job = harness["repository"].get_data_job_by_request(response.json()["request_id"])
    assert job["normalized_target"]["source"] == {
        "storage_name": "cephfs-a",
        "path": "project/input",
    }
    assert job["normalized_target"]["destination"] == {
        "storage_name": "cephfs-a",
        "path": "project/output",
    }
    assert job["normalized_target"]["options"] == {"contents": True}
    plan = harness["repository"].get_plan_by_request(response.json()["request_id"])
    assert plan["execution_metadata"]["phase"] == "preview"

    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )
    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(response.json()["request_id"])
    assert job["state"] == DataJobState.CONFIRM_PENDING.value
    assert job["selected_tool"] == "dsync"
    model = job["preflight_result"]["effective_resource_model"]
    assert model["scheduler_selection"] == "eligible_node_set"
    assert model["eligible_nodes"] == ["dm-1"]
    assert model["selected_node_count"] == 1
    assert model["worker_pod_count"] == 1
    assert model["process_count"] == 1
    assert job["result_summary"]["selected_node"] == "dm-1"
    assert job["result_summary"]["worker_pod_count"] == 1
    assert job["result_summary"]["process_count"] == 1
    assert job["result_summary"]["preview"]["summary"]["dry_run"] is True
    preview_hash = job["result_summary"]["preview"]["fingerprint"]

    missing_confirm = harness["client"].post(
        f"/api/v1/data-management/jobs/{job['job_id']}:confirm",
        json={"requester_id": "portal:alice"},
        headers=HEADERS,
    )
    assert missing_confirm.status_code == 409

    wrong_hash = harness["client"].post(
        f"/api/v1/data-management/jobs/{job['job_id']}:confirm",
        json={
            "requester_id": "portal:alice",
            "confirm": True,
            "preview_observed_hash": "sha256:wrong",
        },
        headers=HEADERS,
    )
    assert wrong_hash.status_code == 409

    confirmed = harness["client"].post(
        f"/api/v1/data-management/jobs/{job['job_id']}:confirm",
        json={
            "requester_id": "portal:alice",
            "confirm": True,
            "preview_observed_hash": preview_hash,
        },
        headers=HEADERS,
    )
    assert confirmed.status_code == 200
    assert harness["repository"].get_data_job(job["job_id"])["state"] == (
        DataJobState.CONFIRMED.value
    )
    assert harness["repository"].get_plan_by_request(response.json()["request_id"])[
        "execution_metadata"
    ]["phase"] == "execution"

    assert worker.run_once() == 1
    executed = harness["repository"].get_data_job(job["job_id"])
    assert executed["state"] == DataJobState.SUCCEEDED.value
    assert executed["result_summary"]["execution"]["summary"]["dry_run"] is False
    assert executed["result_summary"]["selected_node"] == "dm-1"
    assert executed["result_summary"]["worker_pod_count"] == 1
    assert executed["result_summary"]["process_count"] == 1
    assert executed["result_summary"]["summary"]["error_count"] == 0
    assert harness["repository"].get_request(response.json()["request_id"])[
        "status"
    ] == LifecycleState.SUCCEEDED.value
    assert adapter.calls == [
        ("verify_data_preflight:preview", job["job_id"]),
        ("create_job", job["job_id"]),
        ("verify_data_preflight:execution", job["job_id"]),
        ("create_job", job["job_id"]),
    ]


def test_rm_preview_confirm_execution_requires_recursive_and_removes_target(tmp_path):
    harness = _harness(tmp_path)
    _ingest_ready_dm_report(harness["repository"])

    invalid = harness["client"].post(
        "/api/v1/data-management/rm",
        json={
            "requester_id": "portal:alice",
            "target": {"storage_name": "cephfs-a", "path": "project/remove-me"},
        },
        headers=HEADERS,
    )
    root = harness["client"].post(
        "/api/v1/data-management/rm",
        json={
            "requester_id": "portal:alice",
            "target": {"storage_name": "cephfs-a", "path": "."},
            "options": {"recursive": True},
        },
        headers=HEADERS,
    )
    assert invalid.status_code == 422
    assert root.status_code == 422

    accepted = harness["client"].post(
        "/api/v1/data-management/rm",
        json={
            "requester_id": "portal:alice",
            "target": {"storage_name": "cephfs-a", "path": "project/remove-me"},
            "options": {"recursive": True, "stat": True},
        },
        headers=HEADERS,
    )
    assert accepted.status_code == 202
    assert Planner(harness["repository"]).run_once() == 1

    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )
    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(accepted.json()["request_id"])
    preview_hash = job["result_summary"]["preview"]["fingerprint"]

    confirmed = harness["client"].post(
        f"/api/v1/data-management/jobs/{job['job_id']}:confirm",
        json={
            "requester_id": "portal:alice",
            "confirm": True,
            "preview_observed_hash": preview_hash,
        },
        headers=HEADERS,
    )
    assert confirmed.status_code == 200

    assert worker.run_once() == 1
    executed = harness["repository"].get_data_job(job["job_id"])
    assert executed["state"] == DataJobState.SUCCEEDED.value
    assert executed["selected_tool"] == "drm"
    assert executed["result_summary"]["selected_node"] == "dm-1"
    assert executed["result_summary"]["worker_pod_count"] == 1
    assert executed["result_summary"]["process_count"] == 1
    assert executed["result_summary"]["execution"]["summary"]["target_absent"] is True


def test_sync_rejects_unsafe_options_and_destination_under_source(tmp_path):
    harness = _harness(tmp_path)

    delete_disabled = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "portal:alice",
            "storage_name": "cephfs-a",
            "source_path": "project/input",
            "destination_path": "project/output",
            "options": {"delete": True},
        },
        headers=HEADERS,
    )
    under_source = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "portal:alice",
            "storage_name": "cephfs-a",
            "source_path": "project",
            "destination_path": "project/subdir",
        },
        headers=HEADERS,
    )
    raw_options = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "portal:alice",
            "storage_name": "cephfs-a",
            "source_path": "project/input",
            "destination_path": "project/output",
            "options": {"raw_options": "--delete"},
        },
        headers=HEADERS,
    )
    node_count = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "portal:alice",
            "storage_name": "cephfs-a",
            "source_path": "project/input",
            "destination_path": "project/output",
            "node_count": 2,
        },
        headers=HEADERS,
    )

    assert delete_disabled.status_code == 422
    assert under_source.status_code == 422
    assert raw_options.status_code == 422
    assert node_count.status_code == 422


def test_confirm_after_preview_ttl_marks_preview_expired(tmp_path):
    harness = _harness(tmp_path)
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_sync(harness)
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
        volcano_adapter=StubVolcanoAdapter(),
        worker_id="dm-worker-1",
        preview_ttl_seconds=-1,
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(request_id)
    expired = harness["client"].post(
        f"/api/v1/data-management/jobs/{job['job_id']}:confirm",
        json={
            "requester_id": "portal:alice",
            "confirm": True,
            "preview_observed_hash": job["result_summary"]["preview"]["fingerprint"],
        },
        headers=HEADERS,
    )

    assert expired.status_code == 409
    assert harness["repository"].get_data_job(job["job_id"])["state"] == (
        DataJobState.PREVIEW_EXPIRED.value
    )


def test_sync_preflight_requires_identity_before_volcano(tmp_path):
    harness = _harness(tmp_path)
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_sync(harness)
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
        and issue["operation"] == OperationKind.DATA_SYNC.value
        for issue in action_required.json()
    )


def test_phase22_split_role_nsync_preview_reaches_confirm_pending(tmp_path):
    harness = _harness(tmp_path)
    _ingest_split_role_dm_reports(harness["repository"])
    response = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "portal:alice",
            "source": {"storage_name": "cephfs-a", "path": "project/input"},
            "destination": {"storage_name": "cephfs-b", "path": "project/output"},
        },
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert Planner(harness["repository"]).run_once() == 1
    adapter = StubVolcanoAdapter()
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
        identity_lookup=_identity_lookup(),
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )

    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(response.json()["request_id"])
    assert job["state"] == DataJobState.CONFIRM_PENDING.value
    assert job["selected_tool"] == "nsync"
    assert job["preflight_result"]["reason"] == "sync_preflight_passed"
    assert job["preflight_result"]["nsync_enabled"] is True
    assert job["preflight_result"]["selected_source_candidates"][0]["node_name"] == "dm-src"
    assert job["preflight_result"]["selected_destination_candidates"][0]["node_name"] == "dm-dst"
    model = job["preflight_result"]["effective_resource_model"]
    assert model["source_node_count"] == 1
    assert model["destination_node_count"] == 1
    assert model["worker_pod_count"] == 2
    assert model["process_count"] == 2
    assert job["result_summary"]["selected_tool"] == "nsync"
    assert job["result_summary"]["worker_pod_count"] == 2
    assert job["result_summary"]["scheduled_nodes"] == ["dm-src", "dm-dst"]
    assert adapter.calls == [
        ("verify_data_preflight:preview", job["job_id"]),
        ("create_job", job["job_id"]),
    ]


def test_phase21_nsync_disabled_fails_closed(tmp_path):
    harness = _harness(tmp_path)
    _ingest_split_role_dm_reports(harness["repository"])
    request_id = _submit_and_plan_cross_storage_sync(harness)
    adapter = KubernetesVolcanoAdapter(
        Settings(
            database_url="sqlite:///:memory:",
            observability_database_url="sqlite:///:memory:",
            dm_kubernetes_mode="cluster",
            dm_nsync_enabled=False,
        )
    )
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
    assert job["selected_tool"] == "nsync"
    assert job["preflight_result"]["reason"] == "nsync_disabled"
    assert job["preflight_result"]["nsync_enabled"] is False


def test_kubernetes_sync_and_rm_manifests_use_dry_run_and_identity_context():
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
        dm_job_image="registry.local/dms-mpifileutils:test",
        dm_artifact_base_uri="file:///artifacts/dms",
        dm_kubernetes_mode="cluster",
    )
    adapter = KubernetesVolcanoAdapter(settings)
    preflight = {
        "identity_mapping": {
            "uid": 10000,
            "gid": 10000,
            "posix_username": "alice",
        }
    }
    sync_plan = {
        "desired_state": {
            "source": {"storage_name": "cephfs-a", "path": "project/input"},
            "destination": {"storage_name": "cephfs-a", "path": "project/output"},
            "options": {"contents": True},
        },
        "execution_metadata": {"phase": "preview"},
    }
    sync_job = {
        "job_id": "job-sync",
        "request_id": "req-sync",
        "operation": OperationKind.DATA_SYNC.value,
        "storage_name": "cephfs-a",
        "target": None,
        "normalized_target": {
            "source": {"storage_name": "cephfs-a", "path": "project/input"},
            "destination": {"storage_name": "cephfs-a", "path": "project/output"},
        },
        "selected_tool": "dsync",
        "priority": 200,
        "worker_pool": {
            "selected_candidates": [
                {
                    "node_name": "c1-worker",
                    "source_mount_path": "/mnt/cephfs-a",
                    "destination_mount_path": "/mnt/cephfs-a",
                },
                {
                    "node_name": "c1-control",
                    "source_mount_path": "/mnt/cephfs-a",
                    "destination_mount_path": "/mnt/cephfs-a",
                },
            ]
        },
        "preflight_result": preflight,
    }

    sync_manifest = adapter._manifest(sync_plan, sync_job)
    sync_tasks = {task["name"]: task for task in sync_manifest["spec"]["tasks"]}
    assert set(sync_tasks) == {"launcher", "worker"}
    assert sync_manifest["spec"]["minAvailable"] == 2
    sync_launcher_spec = sync_tasks["launcher"]["template"]["spec"]
    sync_worker_spec = sync_tasks["worker"]["template"]["spec"]
    sync_launcher_container = sync_launcher_spec["containers"][0]
    sync_worker_container = sync_worker_spec["containers"][0]
    assert sync_tasks["launcher"]["replicas"] == 1
    assert sync_tasks["worker"]["replicas"] == 1
    assert sync_worker_spec["nodeSelector"] == {}
    sync_values = sync_worker_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert sync_values == ["c1-worker", "c1-control"]
    assert sync_worker_spec["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ][0]["topologyKey"] == "kubernetes.io/hostname"
    assert sync_manifest["metadata"]["labels"]["dms.openai.com/data-phase"] == "preview"
    assert (
        "mpirun --allow-run-as-root --mca pml ob1 --mca btl tcp,self"
        in sync_launcher_container["command"][2]
    )
    assert (
        "runuser -u \"$DMS_POSIX_USERNAME\" --preserve-environment -- dsync --dryrun --contents"
        in sync_launcher_container["command"][2]
    )
    assert "/usr/sbin/sshd -D -e" in sync_worker_container["command"][2]
    assert {"name": "DMS_SELECTED_TOOL", "value": "dsync"} in sync_worker_container["env"]
    assert {"name": "DMS_SELECTED_NODE", "value": ""} in sync_worker_container["env"]

    rm_plan = {
        "desired_state": {
            "target": {"storage_name": "cephfs-a", "path": "project/remove-me"},
            "options": {"recursive": True},
        },
        "execution_metadata": {"phase": "execution"},
    }
    rm_job = {
        "job_id": "job-rm",
        "request_id": "req-rm",
        "operation": OperationKind.DATA_RM.value,
        "storage_name": "cephfs-a",
        "target": "project/remove-me",
        "normalized_target": {"storage_name": "cephfs-a", "path": "project/remove-me"},
        "selected_tool": "drm",
        "priority": 100,
            "worker_pool": {
            "selected_candidates": [
                {"node_name": "c1-worker", "mount_path": "/mnt/cephfs-a"},
                {"node_name": "c1-control", "mount_path": "/mnt/cephfs-a"},
            ]
        },
        "preflight_result": preflight,
    }

    rm_manifest = adapter._manifest(rm_plan, rm_job)
    rm_tasks = {task["name"]: task for task in rm_manifest["spec"]["tasks"]}
    assert set(rm_tasks) == {"launcher", "worker"}
    assert rm_manifest["spec"]["minAvailable"] == 2
    rm_launcher_spec = rm_tasks["launcher"]["template"]["spec"]
    rm_worker_spec = rm_tasks["worker"]["template"]["spec"]
    rm_launcher_container = rm_launcher_spec["containers"][0]
    rm_worker_container = rm_worker_spec["containers"][0]
    assert rm_tasks["launcher"]["replicas"] == 1
    assert rm_tasks["worker"]["replicas"] == 1
    assert rm_worker_spec["nodeSelector"] == {}
    rm_values = rm_worker_spec["affinity"]["nodeAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ]["nodeSelectorTerms"][0]["matchExpressions"][0]["values"]
    assert rm_values == ["c1-worker", "c1-control"]
    assert rm_worker_spec["affinity"]["podAntiAffinity"][
        "requiredDuringSchedulingIgnoredDuringExecution"
    ][0]["topologyKey"] == "kubernetes.io/hostname"
    assert rm_manifest["metadata"]["labels"]["dms.openai.com/data-phase"] == "execution"
    assert (
        "mpirun --allow-run-as-root --mca pml ob1 --mca btl tcp,self"
        in rm_launcher_container["command"][2]
    )
    assert "runuser -u \"$DMS_POSIX_USERNAME\" --preserve-environment -- drm \"$DMS_MPI_RM_TARGET\"" in rm_launcher_container[
        "command"
    ][2]
    assert "drm --dryrun" not in rm_launcher_container["command"][2]
    assert "/usr/sbin/sshd -D -e" in rm_worker_container["command"][2]
    assert {"name": "DMS_RM_TARGET_PATH", "value": "project/remove-me"} in rm_worker_container[
        "env"
    ]
    assert {"name": "DMS_SELECTED_NODE", "value": ""} in rm_worker_container["env"]


def test_phase22_nsync_native_volcano_manifest_has_launcher_and_role_workers():
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
        dm_job_image="registry.local/dms-mpifileutils:test",
        dm_artifact_base_uri="file:///artifacts/dms",
        dm_kubernetes_mode="cluster",
        dm_scheduler_backend="volcano-job",
    )
    adapter = KubernetesVolcanoAdapter(settings)
    plan = {
        "desired_state": {
            "source": {"storage_name": "cephfs-a", "path": "project/input"},
            "destination": {"storage_name": "cephfs-b", "path": "project/output"},
            "options": {},
        },
        "execution_metadata": {"phase": "preview"},
    }
    data_job = {
        "job_id": "job-nsync",
        "request_id": "req-nsync",
        "operation": OperationKind.DATA_SYNC.value,
        "storage_name": "cephfs-a",
        "target": None,
        "normalized_target": {
            "source": {"storage_name": "cephfs-a", "path": "project/input"},
            "destination": {"storage_name": "cephfs-b", "path": "project/output"},
        },
        "selected_tool": "nsync",
        "priority": 200,
        "worker_pool": {
            "source_candidates": [
                {"node_name": "src-1", "mount_path": "/mnt/source"},
                {"node_name": "src-2", "mount_path": "/mnt/source"},
            ],
            "destination_candidates": [
                {"node_name": "dst-1", "mount_path": "/mnt/destination"},
                {"node_name": "dst-2", "mount_path": "/mnt/destination"},
            ],
            "selected_candidates": [
                {"node_name": "src-1", "mount_path": "/mnt/source"},
                {"node_name": "src-2", "mount_path": "/mnt/source"},
                {"node_name": "dst-1", "mount_path": "/mnt/destination"},
                {"node_name": "dst-2", "mount_path": "/mnt/destination"},
            ],
        },
        "preflight_result": {
            "identity_mapping": {
                "uid": 10000,
                "gid": 10000,
                "posix_username": "alice",
            },
            "effective_resource_model": {
                "source_node_count": 2,
                "destination_node_count": 2,
                "worker_pod_count": 4,
                "launcher_pod_count": 1,
                "processes_per_node": 3,
                "process_count": 12,
                "queue": "phase22-queue",
                "priority_class": "phase22-priority",
            },
        },
    }

    manifest = adapter._manifest(plan, data_job)
    assert manifest["kind"] == "Job"
    assert manifest["spec"]["minAvailable"] == 5
    assert manifest["spec"]["queue"] == "phase22-queue"
    assert manifest["spec"]["priorityClassName"] == "phase22-priority"
    tasks = {task["name"]: task for task in manifest["spec"]["tasks"]}
    assert set(tasks) == {"launcher", "source-worker", "destination-worker"}
    assert tasks["launcher"]["replicas"] == 1
    assert tasks["source-worker"]["replicas"] == 2
    assert tasks["destination-worker"]["replicas"] == 2
    source_values = tasks["source-worker"]["template"]["spec"]["affinity"][
        "nodeAffinity"
    ]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0][
        "matchExpressions"
    ][0]["values"]
    destination_values = tasks["destination-worker"]["template"]["spec"]["affinity"][
        "nodeAffinity"
    ]["requiredDuringSchedulingIgnoredDuringExecution"]["nodeSelectorTerms"][0][
        "matchExpressions"
    ][0]["values"]
    assert source_values == ["src-1", "src-2"]
    assert destination_values == ["dst-1", "dst-2"]


def _harness(tmp_path) -> dict:
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
    _register_ready_storage_mapping(repository, "cephfs-a")
    _register_ready_storage_mapping(repository, "cephfs-b")
    return {
        "client": TestClient(app),
        "repository": repository,
        "observability": observability,
    }


def _submit_and_plan_sync(harness: dict) -> str:
    response = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "portal:alice",
            "source": {"storage_name": "cephfs-a", "path": "project/input"},
            "destination": {"storage_name": "cephfs-a", "path": "project/output"},
        },
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert Planner(harness["repository"]).run_once() == 1
    return response.json()["request_id"]


def _submit_and_plan_cross_storage_sync(harness: dict) -> str:
    response = harness["client"].post(
        "/api/v1/data-management/sync",
        json={
            "requester_id": "portal:alice",
            "source": {"storage_name": "cephfs-a", "path": "project/input"},
            "destination": {"storage_name": "cephfs-b", "path": "project/output"},
        },
        headers=HEADERS,
    )
    assert response.status_code == 202
    assert Planner(harness["repository"]).run_once() == 1
    return response.json()["request_id"]


def _register_ready_storage_mapping(
    repository: DmsRepository, storage_name: str = "cephfs-a"
) -> None:
    sanity = {
        "storage_name": storage_name,
        "status": "Ready",
        "agent_observed": {
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "dm-1"}],
        },
        "readiness": {"data_management": "Ready", "resource_management": "Ready"},
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name=storage_name,
            backend_template={"backend_type": "cephfs"},
            cluster_name="cluster-a",
            storage_class_name=f"{storage_name}-sc",
        ),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def _identity_lookup() -> StubIdentityLookupAdapter:
    # DM resolves the requester's POSIX identity by READ-ONLY LDAP lookup. The worker
    # looks up owner_username (defaults to requester_id) under the configured provider.
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


def _ingest_ready_dm_report(repository: DmsRepository) -> None:
    repository.ingest_agent_report(
        {
            "schema_version": "phase20.v1",
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
                },
                {
                    "storage_name": "cephfs-b",
                    "mount_path": "/mnt/dms/cephfs-b",
                    "status": "Ready",
                    "readable": True,
                },
            ],
            "tools": [
                {"name": "dscan", "status": "Ready"},
                {"name": "dsync", "status": "Ready"},
                {"name": "drm", "status": "Ready"},
                {"name": "nsync", "status": "Ready"},
            ],
            "credentials": [{"name": "kubernetes-service-account", "status": "Ready"}],
            "networks": [{"name": "storage-net", "status": "Ready"}],
            "identity_evidence": {
                "source": "phase20-test",
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


def _ingest_split_role_dm_reports(repository: DmsRepository) -> None:
    for node_name, storage_name in (
        ("dm-src", "cephfs-a"),
        ("dm-dst", "cephfs-b"),
    ):
        repository.ingest_agent_report(
            {
                "schema_version": "phase21.v1",
                "reported_at": "2026-06-03T00:00:00+00:00",
                "cluster_name": "cluster-a",
                "node_name": node_name,
                "node_uid": f"uid-{node_name}",
                "worker_role": "DM",
                "mounts": [
                    {
                        "storage_name": storage_name,
                        "mount_path": f"/mnt/dms/{storage_name}",
                        "status": "Ready",
                        "readable": True,
                    }
                ],
                "tools": [
                    {"name": "dscan", "status": "Ready"},
                    {"name": "dsync", "status": "Ready"},
                    {"name": "drm", "status": "Ready"},
                    {"name": "nsync", "status": "Ready"},
                ],
                "credentials": [{"name": "kubernetes-service-account", "status": "Ready"}],
                "networks": [{"name": "storage-net", "status": "Ready"}],
                "identity_evidence": {
                    "source": "phase21-test",
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


def _preflight_adapter() -> KubernetesVolcanoAdapter:
    return KubernetesVolcanoAdapter(
        Settings(
            database_url="sqlite:///:memory:",
            observability_database_url="sqlite:///:memory:",
            dm_job_image="registry.local/dms-mpifileutils:test",
            dm_artifact_base_uri="file:///artifacts/dms",
            dm_kubernetes_mode="cluster",
        )
    )


def _sync_plan_job(tool: str, worker_pool: dict) -> tuple[dict, dict, dict]:
    plan = {
        "desired_state": {
            "source": {"storage_name": "cephfs-dms", "path": "dms/src"},
            "destination": {"storage_name": "cephfs-dms-secondary", "path": "dms/dst"},
            "options": {},
        },
        "execution_metadata": {"phase": "preview"},
    }
    data_job = {
        "job_id": "job-pf",
        "request_id": "req-pf",
        "operation": OperationKind.DATA_SYNC.value,
        "storage_name": "cephfs-dms",
        "normalized_target": {
            "source": {"storage_name": "cephfs-dms", "path": "dms/src"},
            "destination": {"storage_name": "cephfs-dms-secondary", "path": "dms/dst"},
        },
        "selected_tool": tool,
        "worker_pool": worker_pool,
    }
    preflight = {
        "identity_mapping": {"uid": 10003, "gid": 10000, "posix_username": "cocoa.song"}
    }
    return plan, data_job, preflight


def _container(manifest: dict) -> dict:
    return manifest["spec"]["containers"][0]


def _mount_names(manifest: dict) -> set:
    return {m["name"] for m in _container(manifest)["volumeMounts"]}


def test_nsync_preflight_splits_into_source_and_destination_role_pods():
    # nsync's source and destination are on DISJOINT node sets, so the POSIX preflight
    # must run one pod per role (each pinned to a node of its own role, mounting only its
    # own storage) -- not a single both-mounts pod (which would land on one node where the
    # other role's mount is absent and always fail posix_permission_denied).
    adapter = _preflight_adapter()
    worker_pool = {
        "source_candidates": [
            {"node_name": "dms-w1", "mount_path": "/cephfs"},
            {"node_name": "dms-w2", "mount_path": "/cephfs"},
        ],
        "destination_candidates": [
            {"node_name": "dms-w4", "mount_path": "/cephfs-secondary"},
            {"node_name": "dms-w5", "mount_path": "/cephfs-secondary"},
        ],
        "selected_candidates": [
            {"node_name": "dms-w1", "mount_path": "/cephfs"},
            {"node_name": "dms-w4", "mount_path": "/cephfs-secondary"},
        ],
    }
    plan, data_job, preflight = _sync_plan_job("nsync", worker_pool)

    manifests = adapter._data_preflight_manifests(plan, data_job, preflight, "preview")
    roles = [role for role, _ in manifests]
    assert roles == ["source", "destination"]
    by_role = {role: m for role, m in manifests}

    src = by_role["source"]
    assert src["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w1"}
    assert _mount_names(src) == {"sync-source"}  # source pod must NOT mount destination
    assert src["spec"]["volumes"][0]["hostPath"]["path"] == "/cephfs"
    assert "/dms/source/${DMS_SYNC_SOURCE_PATH}" in _container(src)["command"][2]
    assert "/dms/destination" not in _container(src)["command"][2]

    dst = by_role["destination"]
    assert dst["spec"]["nodeSelector"] == {"kubernetes.io/hostname": "dms-w4"}
    assert _mount_names(dst) == {"sync-destination"}  # destination pod must NOT mount source
    assert dst["spec"]["volumes"][0]["hostPath"]["path"] == "/cephfs-secondary"
    cmd = _container(dst)["command"][2]
    assert "destination_parent" in cmd and 'test -w "$destination_parent"' in cmd

    # both role pods run as the resolved POSIX identity
    for m in (src, dst):
        assert _container(m)["securityContext"]["runAsUser"] == 10003
        assert m["metadata"]["labels"]["dms.openai.com/preflight-role"] in (
            "source",
            "destination",
        )


def test_dsync_preflight_stays_single_both_mounts_pod():
    # Co-located dsync keeps the single-pod check that mounts BOTH source and destination
    # from the one node that has them.
    adapter = _preflight_adapter()
    worker_pool = {
        "selected_candidates": [
            {
                "node_name": "dms-w1",
                "source_mount_path": "/cephfs",
                "destination_mount_path": "/cephfs",
            }
        ]
    }
    plan, data_job, preflight = _sync_plan_job("dsync", worker_pool)
    manifests = adapter._data_preflight_manifests(plan, data_job, preflight, "preview")
    assert [role for role, _ in manifests] == ["both"]
    both = manifests[0][1]
    assert _mount_names(both) >= {"sync-source", "sync-destination"}
