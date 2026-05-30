from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from fastapi.testclient import TestClient

from dms.adapters import StubIdentityGroupManager, StubKubernetesNamespaceQuotaAdapter
from dms.api import create_app
from dms.backends.cephfs import (
    CephFsBackendTemplate,
    CephFsHostMountedFilesystemBackendAdapter,
)
from dms.config import Settings
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime


API_HEADERS = {"x-dms-actor": "api-client"}


def test_phase11_expired_filesystem_query_and_action_required(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_filesystem_resource(repository, expires_at="2026-05-30T00:00:00Z")
    client = client_for(tmp_path, repository, observability)

    response = client.get(
        "/api/v1/operations/filesystems/expiring",
        headers=API_HEADERS,
        params={"before": "2026-05-30T01:00:00Z"},
    )

    assert response.status_code == 200
    [expired] = response.json()
    assert expired["resource_key"] == "cephfs-a:project-alpha"
    assert expired["expired"] is True
    assert expired["block_state"] == {"blocked": False}

    action_required = client.get(
        "/api/v1/operations/action-required", headers=API_HEADERS
    ).json()
    assert {
        issue["issue_type"] for issue in action_required
    } >= {"filesystem_expired_unblocked"}


def test_phase11_filesystem_expiration_sweep_dry_run_records_targets(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_filesystem_resource(repository, expires_at="2026-05-30T00:00:00Z")
    seed_filesystem_resource(
        repository,
        directory_name="project-system",
        resource_type="system",
        expires_at="2026-05-30T00:00:00Z",
    )
    request_id = create_sweep_request(
        repository,
        {
            "scope": {"storage_name": "cephfs-a"},
            "expired_before": "2026-05-30T01:00:00Z",
            "dry_run": True,
            "max_targets": 10,
        },
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, FakeFilesystemExecutor())

    [result] = repository.get_results(request_id)
    summary = result["verification_summary"]
    assert summary["dry_run"] is True
    assert summary["target_count"] == 2
    target_results = {target["resource_key"]: target["result"] for target in summary["targets"]}
    assert target_results["cephfs-a:project-alpha"] == "would_block"
    assert target_results["cephfs-a:project-system"] == "skipped"
    assert repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:project-alpha"
    )["status"] == LifecycleState.SUCCEEDED.value


def test_phase11_filesystem_block_and_unblock_restore_state(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_filesystem_resource(repository, expires_at="2026-05-30T00:00:00Z")
    executor = FakeFilesystemExecutor()
    block_id = create_block_request(repository, block=True)

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    resource = repository.get_resource(ResourceKind.FILESYSTEM.value, "cephfs-a:project-alpha")
    assert repository.get_request(block_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["status"] == LifecycleState.BLOCKED.value
    assert resource["desired_state"]["expires_at"] == "2026-05-30T00:00:00Z"
    assert resource["desired_state"]["block_state"]["blocked"] is True
    assert resource["desired_state"]["block_state"]["restore"]["mode"] == "0770"
    assert executor.directories["project-alpha"]["mode"] == "0000"

    unblock_id = create_block_request(repository, block=False)
    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    resource = repository.get_resource(ResourceKind.FILESYSTEM.value, "cephfs-a:project-alpha")
    assert repository.get_request(unblock_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["status"] == LifecycleState.SUCCEEDED.value
    assert resource["desired_state"]["expires_at"] == "2026-05-30T00:00:00Z"
    assert resource["desired_state"]["block_state"]["blocked"] is False
    assert executor.directories["project-alpha"]["mode"] == "0770"


def test_phase11_expiration_sweep_blocks_user_and_skips_system(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_filesystem_resource(repository, expires_at="2026-05-30T00:00:00Z")
    seed_filesystem_resource(
        repository,
        directory_name="project-system",
        resource_type="system",
        expires_at="2026-05-30T00:00:00Z",
    )
    executor = FakeFilesystemExecutor()
    request_id = create_sweep_request(
        repository,
        {
            "scope": {"storage_name": "cephfs-a"},
            "expired_before": "2026-05-30T01:00:00Z",
            "dry_run": False,
            "max_targets": 10,
            "reason": "test sweep",
        },
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    [result] = repository.get_results(request_id)
    summary = result["verification_summary"]
    assert summary["blocked_count"] == 1
    assert summary["skipped_count"] == 1
    assert {
        (target["resource_key"], target["result"], target.get("reason"))
        for target in summary["targets"]
    } == {
        ("cephfs-a:project-alpha", "blocked", None),
        (
            "cephfs-a:project-system",
            "skipped",
            "resource_type_not_auto_blocked",
        ),
    }
    assert repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:project-alpha"
    )["status"] == LifecycleState.BLOCKED.value


def test_phase11_filesystem_unblock_rejects_missing_restore_state(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_filesystem_resource(repository, status=LifecycleState.BLOCKED.value)
    request_id = create_block_request(repository, block=False)

    Planner(repository).run_once()

    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert result["verification_summary"]["issues"] == [
        {"reason": "filesystem_block_restore_missing"}
    ]


@dataclass
class FakeFilesystemExecutor:
    calls: list[dict[str, Any]] = field(default_factory=list)
    directories: dict[str, dict[str, Any]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.directories.setdefault(
            "project-alpha",
            {
                "mode": "0770",
                "group_name": "dms-phase10-project-alpha",
                "group_gid": 24000,
                "marker": {"resource_key": "cephfs-a:project-alpha"},
            },
        )

    def create_directory(self, **kwargs) -> dict[str, Any]:
        raise AssertionError("Phase 11 tests seed filesystem DB resources directly")

    def delete_directory(self, **kwargs) -> dict[str, Any]:
        raise AssertionError("Phase 11 tests do not delete through fake executor")

    def block_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        block_mode: str,
        reason: str | None,
        request_id: str,
        existing_block_state: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append({"operation": "block", "directory_name": directory_name})
        directory = self.directories.setdefault(
            directory_name,
            {
                "mode": "0770",
                "group_name": f"dms-phase10-{directory_name}",
                "group_gid": 24000,
                "marker": {"resource_key": resource_key},
            },
        )
        if directory["marker"]["resource_key"] != resource_key:
            raise RuntimeError("target directory marker resource key mismatch")
        restore = existing_block_state.get("restore") or {
            "owner": "root",
            "uid": 0,
            "group_name": directory["group_name"],
            "gid": directory["group_gid"],
            "mode": directory["mode"],
        }
        already_blocked = directory["mode"] == "0000"
        directory["mode"] = "0000"
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "marker": directory["marker"],
            "mode": "0000",
            "already_blocked": already_blocked,
            "backend_side_effect": not already_blocked,
            "verified": True,
            "block_state": {
                "blocked": True,
                "block_mode": block_mode,
                "blocked_by_request_id": request_id,
                "reason": reason,
                "restore": restore,
            },
        }

    def unblock_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        block_state: dict[str, Any],
        allowed_users: list[str],
        denied_users: list[str],
    ) -> dict[str, Any]:
        self.calls.append({"operation": "unblock", "directory_name": directory_name})
        directory = self.directories[directory_name]
        if directory["marker"]["resource_key"] != resource_key:
            raise RuntimeError("target directory marker resource key mismatch")
        restore = block_state["restore"]
        directory["mode"] = restore["mode"]
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "marker": directory["marker"],
            "mode": restore["mode"],
            "group_name": restore["group_name"],
            "group_gid": restore["gid"],
            "backend_side_effect": True,
            "verified": True,
            "access_validation": {
                "allowed_users": {user: "ok" for user in allowed_users},
                "denied_users": {user: "denied" for user in denied_users},
            },
            "block_state": {
                "blocked": False,
                "block_mode": block_state.get("block_mode", "permission-zero"),
                "restore": restore,
            },
        }


def repository_pair(tmp_path) -> tuple[DmsRepository, ObservabilityRepository]:
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    return DmsRepository(operational), ObservabilityRepository(observability_db)


def client_for(
    tmp_path, repository: DmsRepository, observability: ObservabilityRepository
) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'operational.db'}",
        observability_database_url=f"sqlite:///{tmp_path / 'observability.db'}",
    )
    return TestClient(create_app(settings, repository, observability))


def register_cephfs_mapping(repository: DmsRepository) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    repository.upsert_storage_mapping(
        StorageMappingInput(
            storage_name="cephfs-a",
            backend_template={
                "backend_type": "cephfs",
                "cluster_name": "cluster-a",
                "mount_path": "/mnt/testbed-cephfs",
                "managed_root": "/mnt/testbed-cephfs/dms-phase10",
                "rm_worker_nodes": ["c1-worker"],
            },
            cluster_name="cluster-a",
            storage_class_name="testbed-cephfs",
            sanity_status="Ready",
        ),
        actor="admin",
        sanity_result={"status": "Ready", "readiness": readiness, "errors": [], "warnings": []},
        readiness=readiness,
    )


def seed_filesystem_resource(
    repository: DmsRepository,
    *,
    directory_name: str = "project-alpha",
    resource_type: str = "user",
    expires_at: str = "2026-05-30T00:00:00Z",
    status: str = LifecycleState.SUCCEEDED.value,
) -> None:
    desired = {
        "storage_name": "cephfs-a",
        "directory_name": directory_name,
        "users": ["alice", "bob"],
        "validation_denied_users": ["mallory"],
        "access_group": f"dms-phase10-{directory_name}",
        "mode": "0770",
        "resource_type": resource_type,
        "expires_at": expires_at,
        "resource_kind": ResourceKind.FILESYSTEM.value,
        "resource_key": f"cephfs-a:{directory_name}",
        "requester_id": "portal:phase11",
    }
    repository.upsert_resource(
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=f"cephfs-a:{directory_name}",
        desired_state=desired,
        applied_state={"expires_at": expires_at},
        observed_state={
            "path": f"/mnt/testbed-cephfs/dms-phase10/{directory_name}",
            "mode": "0770",
            "block_state": {"blocked": False},
        },
        status=status,
    )


def create_block_request(repository: DmsRepository, *, block: bool) -> str:
    return repository.create_request(
        requester_id="portal:phase11",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_BLOCK.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="cephfs-a:project-alpha",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "block": block,
            "block_mode": "permission-zero",
            "reason": "phase11 test",
        },
    )


def create_sweep_request(repository: DmsRepository, payload: dict[str, Any]) -> str:
    return repository.create_request(
        requester_id="portal:phase11",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_EXPIRATION_SWEEP.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="filesystem-expiration-sweep",
        payload=payload,
    )


def run_worker(
    repository: DmsRepository,
    observability: ObservabilityRepository,
    executor: FakeFilesystemExecutor,
) -> None:
    adapter = CephFsHostMountedFilesystemBackendAdapter(
        template=CephFsBackendTemplate(
            storage_name="cephfs-a",
            cluster_name="cluster-a",
            mount_path="/mnt/testbed-cephfs",
            managed_root="/mnt/testbed-cephfs/dms-phase10",
            rm_worker_node="c1-worker",
        ),
        identity_groups=StubIdentityGroupManager(users={"alice": {}, "bob": {}}),
        executor=executor,
    )
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=adapter,
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-phase11",
    )
    assert worker.run_once() == 1
