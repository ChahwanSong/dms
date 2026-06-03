from __future__ import annotations

from fastapi.testclient import TestClient

from dms.adapters import KubernetesVolcanoAdapter, StubVolcanoAdapter
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


def test_sync_preview_confirm_execution_state_machine(tmp_path):
    harness = _harness(tmp_path)
    _register_identity_mapping(harness["repository"])
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
        volcano_adapter=adapter,
        worker_id="dm-worker-1",
    )
    assert worker.run_once() == 1
    job = harness["repository"].get_data_job_by_request(response.json()["request_id"])
    assert job["state"] == DataJobState.CONFIRM_PENDING.value
    assert job["selected_tool"] == "dsync"
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
    _register_identity_mapping(harness["repository"])
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

    assert delete_disabled.status_code == 422
    assert under_source.status_code == 422
    assert raw_options.status_code == 422


def test_confirm_after_preview_ttl_marks_preview_expired(tmp_path):
    harness = _harness(tmp_path)
    _register_identity_mapping(harness["repository"])
    _ingest_ready_dm_report(harness["repository"])
    request_id = _submit_and_plan_sync(harness)
    worker = DMWorkerRuntime(
        repository=harness["repository"],
        observability=harness["observability"],
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
        and issue["operation"] == OperationKind.DATA_SYNC.value
        for issue in action_required.json()
    )


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
                }
            ]
        },
        "preflight_result": preflight,
    }

    sync_manifest = adapter._manifest(sync_plan, sync_job)
    sync_container = sync_manifest["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    assert sync_manifest["spec"]["tasks"][0]["name"] == "dsync"
    assert sync_manifest["metadata"]["labels"]["dms.openai.com/data-phase"] == "preview"
    assert "dsync --dryrun --contents \"$source\" \"$destination\"" in sync_container[
        "command"
    ][2]
    assert "runAsUser" in sync_container["securityContext"]
    assert {"name": "DMS_SELECTED_TOOL", "value": "dsync"} in sync_container["env"]

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
                {"node_name": "c1-worker", "mount_path": "/mnt/cephfs-a"}
            ]
        },
        "preflight_result": preflight,
    }

    rm_manifest = adapter._manifest(rm_plan, rm_job)
    rm_container = rm_manifest["spec"]["tasks"][0]["template"]["spec"]["containers"][0]
    assert rm_manifest["metadata"]["labels"]["dms.openai.com/data-phase"] == "execution"
    assert "drm \"$target\"" in rm_container["command"][2]
    assert "drm --dryrun" not in rm_container["command"][2]
    assert {"name": "DMS_RM_TARGET_PATH", "value": "project/remove-me"} in rm_container[
        "env"
    ]


def _harness(tmp_path) -> dict:
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
