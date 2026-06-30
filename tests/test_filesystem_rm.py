from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dms.adapters import (
    StubIdentityGroupManager,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.backends.cephfs import (
    CephFsBackendTemplate,
    CephFsHostMountedFilesystemBackendAdapter,
)
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime


def test_filesystem_create_plans_access_group_and_no_quota(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = create_filesystem_request(
        repository,
        OperationKind.FILESYSTEM_CREATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            # far-future so the request is never treated as already-expired
            # (was hardcoded "2026-06-30", which the planner expired once the system
            # clock reached that date — a time-bomb flake).
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["access_group"] == "dms-grp-project-alpha"
    assert plan["desired_state"]["mode"] == "0750"
    assert "quota" not in plan["desired_state"]
    assert plan["execution_metadata"]["planner"] == "filesystem"


def test_filesystem_create_plans_quota_payload(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = create_filesystem_request(
        repository,
        OperationKind.FILESYSTEM_CREATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 1024, "file_count": 100},
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["quota"] == {
        "capacity_bytes": 1024,
        "file_count": 100,
    }
    assert plan["execution_metadata"]["planner"] == "filesystem-quota"


def test_filesystem_create_requires_at_least_one_user(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = create_filesystem_request(
        repository,
        OperationKind.FILESYSTEM_CREATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": [],
        },
    )

    Planner(repository).run_once()

    [result] = repository.get_results(request_id)
    assert {issue["reason"] for issue in result["verification_summary"]["issues"]} == {
        "filesystem_users_minimum_one_required"
    }


def test_filesystem_create_with_single_user_succeeds(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = create_filesystem_request(
        repository,
        OperationKind.FILESYSTEM_CREATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice"],
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["users"] == ["alice"]


def test_filesystem_create_rejects_existing_resource(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    repository.upsert_resource(
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="cephfs-a:project-alpha",
        desired_state={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
        },
        applied_state={},
        observed_state={},
        status=LifecycleState.SUCCEEDED.value,
    )
    request_id = create_filesystem_request(
        repository,
        OperationKind.FILESYSTEM_CREATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
        },
    )

    Planner(repository).run_once()

    [result] = repository.get_results(request_id)
    assert result["verification_summary"]["issues"] == [
        {
            "reason": "filesystem_resource_already_exists",
            "resource_key": "cephfs-a:project-alpha",
            "status": LifecycleState.SUCCEEDED.value,
        }
    ]


def test_filesystem_update_requires_existing_quota_only_payload(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = create_filesystem_request(
        repository,
        OperationKind.FILESYSTEM_UPDATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
        },
    )

    Planner(repository).run_once()

    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    # An unsupported-field-only PATCH on a missing resource reports exactly the real
    # problems: the unsupported field and the missing resource. It must NOT also emit a
    # spurious `expires_at_required` (update never requires it) nor
    # `filesystem_update_payload_empty` (the payload is unsupported, not empty).
    assert {issue["reason"] for issue in result["verification_summary"]["issues"]} == {
        "filesystem_payload_fields_unsupported",
        "filesystem_resource_missing",
    }


def test_filesystem_delete_reuses_existing_desired_state(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    repository.upsert_resource(
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="cephfs-a:project-alpha",
        desired_state={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "access_group": "dms-grp-project-alpha",
        },
        applied_state={},
        observed_state={},
        status=LifecycleState.SUCCEEDED.value,
    )
    request_id = create_filesystem_request(
        repository,
        OperationKind.FILESYSTEM_DELETE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "reason": "cleanup",
        },
    )

    Planner(repository).run_once()

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["access_group"] == "dms-grp-project-alpha"
    assert plan["desired_state"]["users"] == ["alice", "bob"]
    assert plan["desired_state"]["reason"] == "cleanup"


def test_cephfs_adapter_creates_group_then_host_directory():
    executor = FakeFilesystemExecutor()
    identity_groups = StubIdentityGroupManager(users={"alice": {}, "bob": {}})
    adapter = CephFsHostMountedFilesystemBackendAdapter(
        template=CephFsBackendTemplate(
            storage_name="cephfs-a",
            cluster_name="cluster-a",
            mount_path="/mnt/testbed-cephfs",
            managed_root="/mnt/testbed-cephfs/dms-phase10",
            rm_worker_node="c1-worker",
        ),
        identity_groups=identity_groups,
        executor=executor,
    )
    plan = filesystem_plan(
        operation=OperationKind.FILESYSTEM_CREATE,
        desired_state={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "validation_denied_users": ["mallory"],
            "access_group": "dms-grp-project-alpha",
        },
    )

    result = adapter.create(plan)

    assert result.applied_state["access_group"]["members"] == ["alice", "bob"]
    assert result.observed_state["access_validation"] == {
        "allowed_users": {"alice": "ok", "bob": "ok"},
        "denied_users": {"mallory": "denied"},
    }
    assert executor.calls[0]["operation"] == "create"
    assert executor.calls[0]["group_name"] == "dms-grp-project-alpha"


def test_worker_records_missing_ldap_user_before_filesystem_side_effect(
    tmp_path,
):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = create_filesystem_request(
        repository,
        OperationKind.FILESYSTEM_CREATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "missing-user"],
        },
    )
    Planner(repository).run_once()
    executor = FakeFilesystemExecutor()
    adapter = CephFsHostMountedFilesystemBackendAdapter(
        template=CephFsBackendTemplate(
            storage_name="cephfs-a",
            cluster_name="cluster-a",
            mount_path="/mnt/testbed-cephfs",
            managed_root="/mnt/testbed-cephfs/dms-phase10",
            rm_worker_node="c1-worker",
        ),
        identity_groups=StubIdentityGroupManager(users={"alice": {}}),
        executor=executor,
    )
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=adapter,
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-phase10",
    )

    assert worker.run_once() == 1

    assert executor.calls == []
    assert repository.get_request(request_id)["status"] == (
        LifecycleState.BACKEND_APPLY_FAILED.value
    )
    [result] = repository.get_results(request_id)
    assert result["verification_summary"]["backend_side_effect"] is False
    assert result["error_category"] == "backend_precondition"


@dataclass
class FakeFilesystemExecutor:
    calls: list[dict[str, Any]] = field(default_factory=list)

    def create_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        group_name: str,
        mode: str,
        allowed_users: list[str],
        denied_users: list[str],
        owner_username: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "operation": "create",
                "managed_root": managed_root,
                "directory_name": directory_name,
                "group_name": group_name,
                "mode": mode,
                "allowed_users": allowed_users,
                "denied_users": denied_users,
                "owner_username": owner_username,
            }
        )
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "created": True,
            "owner_username": owner_username,
            "owner_uid": 10001 if owner_username else 0,
            "group_name": group_name,
            "group_gid": 24000,
            "mode": mode,
            "access_validation": {
                "allowed_users": {user: "ok" for user in allowed_users},
                "denied_users": {user: "denied" for user in denied_users},
            },
            "verified": True,
        }

    def delete_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "operation": "delete",
                "managed_root": managed_root,
                "directory_name": directory_name,
                "resource_key": resource_key,
            }
        )
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": False,
            "deleted": True,
            "verified": True,
        }


def repository_pair(tmp_path) -> tuple[DmsRepository, ObservabilityRepository]:
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    return DmsRepository(operational), ObservabilityRepository(observability_db)


def register_cephfs_mapping(repository: DmsRepository) -> None:
    readiness = {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    }
    sanity = {
        "status": "Ready",
        "readiness": readiness,
        "agent_observed": {
            "rm_readiness": "Ready",
            "rm_candidates": [{"cluster_name": "cluster-a", "node_name": "c1-worker"}],
        },
        "checks": [],
        "warnings": [],
        "errors": [],
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
        sanity_result=sanity,
        readiness=readiness,
    )


def create_filesystem_request(
    repository: DmsRepository,
    operation: OperationKind,
    *,
    payload: dict[str, Any],
) -> str:
    payload = dict(payload)
    if operation == OperationKind.FILESYSTEM_CREATE:
        payload.setdefault("expires_at", "2099-01-01T00:00:00Z")
    return repository.create_request(
        requester_id="portal:phase10",
        actor="api-client",
        operation=operation.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=f"{payload['storage_name']}:{payload['directory_name']}",
        payload=payload,
    )


def filesystem_plan(
    *, operation: OperationKind, desired_state: dict[str, Any]
) -> dict[str, Any]:
    return {
        "plan_id": "plan-phase10",
        "request_id": "req-phase10",
        "operation_kind": operation.value,
        "resource_key": (
            f"{desired_state['storage_name']}:{desired_state['directory_name']}"
        ),
        "desired_state": {
            "operation": operation.value,
            "resource_kind": ResourceKind.FILESYSTEM.value,
            "resource_key": (
                f"{desired_state['storage_name']}:{desired_state['directory_name']}"
            ),
            **desired_state,
        },
    }
