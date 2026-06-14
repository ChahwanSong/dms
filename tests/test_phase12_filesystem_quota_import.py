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
from dms.query import OperationalQueryService
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime

API_HEADERS = {"x-dms-actor": "api-client"}


def test_phase12_create_with_quota_applies_cephfs_quota(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_CREATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "quota-create",
            "users": ["alice", "bob"],
            "validation_denied_users": ["mallory"],
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
        },
    )
    executor = FakePhase12FilesystemExecutor()

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:quota-create"
    )
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert resource["desired_state"]["quota"] == {
        "capacity_bytes": 8 * 1024**2,
        "file_count": 32,
    }
    assert (
        resource["applied_state"]["quota_state"]["capacity"]["observed_bytes"]
        == 8 * 1024**2
    )
    assert resource["observed_state"]["access_validation"]["denied_users"] == {
        "mallory": "denied"
    }
    assert [call["operation"] for call in executor.calls] == ["create", "apply_quota"]


def test_phase12_create_rejects_invalid_quota_values(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_CREATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "bad-quota",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 0, "file_count": 10_000_001},
        },
    )

    Planner(repository).run_once()

    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert {issue["reason"] for issue in result["verification_summary"]["issues"]} == {
        "filesystem_quota_capacity_bytes_invalid",
        "filesystem_quota_file_count_too_large",
    }


def test_phase12_update_increases_quota_and_records_live_state(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_resource(
        repository,
        directory_name="quota-update",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
        usage={"used_bytes": 1024**2, "used_files": 3},
    )
    executor = FakePhase12FilesystemExecutor()
    executor.seed_directory(
        "quota-update",
        resource_key="cephfs-a:quota-update",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
        usage={"used_bytes": 1024**2, "used_files": 3},
    )
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_UPDATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "quota-update",
            "quota": {"capacity_bytes": 32 * 1024**2, "file_count": 128},
        },
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:quota-update"
    )
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert resource["desired_state"]["quota"] == {
        "capacity_bytes": 32 * 1024**2,
        "file_count": 128,
    }
    assert (
        resource["observed_state"]["quota_state"]["capacity"]["observed_bytes"]
        == 32 * 1024**2
    )
    assert [call["operation"] for call in executor.calls] == ["apply_quota"]


def test_phase12_planner_allows_quota_decrease_without_usage_guard(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_resource(
        repository,
        directory_name="quota-decrease",
        quota={"capacity_bytes": 32 * 1024**2, "file_count": 128},
        usage={"used_bytes": 10 * 1024**2, "used_files": 9},
    )
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_UPDATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "quota-decrease",
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 128},
        },
    )
    executor = FakePhase12FilesystemExecutor()
    executor.seed_directory(
        "quota-decrease",
        resource_key="cephfs-a:quota-decrease",
        quota={"capacity_bytes": 32 * 1024**2, "file_count": 128},
        usage={"used_bytes": 10 * 1024**2, "used_files": 9},
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:quota-decrease"
    )
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert resource["desired_state"]["quota"]["capacity_bytes"] == 8 * 1024**2
    assert (
        resource["observed_state"]["quota_state"]["capacity"]["observed_bytes"]
        == 8 * 1024**2
    )


def test_phase12_backend_applies_decrease_without_live_usage_read(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_resource(
        repository,
        directory_name="quota-live-guard",
        quota={"capacity_bytes": 32 * 1024**2, "file_count": 128},
        usage={"used_bytes": 1024**2, "used_files": 3},
    )
    executor = FakePhase12FilesystemExecutor()
    executor.seed_directory(
        "quota-live-guard",
        resource_key="cephfs-a:quota-live-guard",
        quota={"capacity_bytes": 32 * 1024**2, "file_count": 128},
        usage={"used_bytes": 10 * 1024**2, "used_files": 3},
    )
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_UPDATE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "quota-live-guard",
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 128},
        },
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:quota-live-guard"
    )
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert (
        resource["observed_state"]["quota_state"]["capacity"]["observed_bytes"]
        == 8 * 1024**2
    )
    assert [call["operation"] for call in executor.calls] == ["apply_quota"]


def test_phase12_check_drift_action_required_and_sync_resolution(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    # desired 8 MiB; live 200 MiB ⇒ 192 MiB diff exceeds 100 MiB capacity tolerance.
    seed_resource(
        repository,
        directory_name="quota-drift",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
        usage={"used_bytes": 1024**2, "used_files": 2},
    )
    executor = FakePhase12FilesystemExecutor()
    executor.seed_directory(
        "quota-drift",
        resource_key="cephfs-a:quota-drift",
        quota={"capacity_bytes": 200 * 1024**2, "file_count": 32},
        usage={"used_bytes": 14 * 1024**2, "used_files": 2},
    )
    check_id = create_request(
        repository,
        OperationKind.FILESYSTEM_CHECK,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "quota-drift",
        },
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    [check_result] = repository.get_results(check_id)
    assert check_result["verification_summary"]["quota_status"] == "ActionRequired"
    issues = OperationalQueryService(repository, observability).action_required()
    issue_types = {issue["issue_type"] for issue in issues}
    assert "filesystem_quota_drifted" in issue_types
    assert "filesystem_quota_usage_warning" not in issue_types

    sync_id = create_request(
        repository,
        OperationKind.FILESYSTEM_SYNC,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "quota-drift",
            "source": "live",
        },
    )
    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:quota-drift"
    )
    assert repository.get_request(sync_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["desired_state"]["quota"]["capacity_bytes"] == 200 * 1024**2
    issue_types_after_sync = {
        issue["issue_type"]
        for issue in OperationalQueryService(
            repository, observability
        ).action_required()
    }
    assert "filesystem_quota_drifted" not in issue_types_after_sync


def test_phase12_check_reports_missing_directory(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_resource(
        repository,
        directory_name="missing-live",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
        usage={"used_bytes": 0, "used_files": 0},
    )
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_CHECK,
        payload={"storage_name": "cephfs-a", "directory_name": "missing-live"},
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, FakePhase12FilesystemExecutor())

    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.SUCCEEDED.value
    assert result["verification_summary"]["issues"] == [
        {
            "issue_type": "filesystem_quota_missing",
            "field": "directory",
            "reason": "missing",
        }
    ]


def test_phase12_rejects_filesystem_usage_payload_fields(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_resource(
        repository,
        directory_name="usage-scan",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
        usage={"used_bytes": 0, "used_files": 0},
    )
    check_id = create_request(
        repository,
        OperationKind.FILESYSTEM_CHECK,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "usage-scan",
            "include_usage": True,
            "usage_thresholds": {"warning_percent": 80, "critical_percent": 95},
        },
    )
    sync_id = create_request(
        repository,
        OperationKind.FILESYSTEM_SYNC,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "usage-scan",
            "include_usage": True,
        },
    )

    assert Planner(repository).run_once() == 2

    [check_result] = repository.get_results(check_id)
    [sync_result] = repository.get_results(sync_id)
    assert check_result["verification_summary"]["issues"] == [
        {
            "reason": "filesystem_payload_fields_unsupported",
            "fields": ["include_usage", "usage_thresholds"],
        }
    ]
    assert sync_result["verification_summary"]["issues"] == [
        {
            "reason": "filesystem_payload_fields_unsupported",
            "fields": ["include_usage"],
        }
    ]


def test_phase12_assign_quota_writes_quota_only_marker(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    executor = FakePhase12FilesystemExecutor()
    executor.seed_directory(
        "existing-quota-only",
        resource_key=None,
        quota=None,
        marker=None,
        management_mode=None,
    )
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_ASSIGN_QUOTA,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "existing-quota-only",
            "management_mode": "quota_only",
            "quota": {"capacity_bytes": 16 * 1024**2, "file_count": 64},
        },
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor)

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:existing-quota-only"
    )
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert resource["desired_state"]["management_mode"] == "quota_only"
    assert resource["observed_state"]["management_mode"] == "quota_only"


def test_phase12_delete_rejects_quota_only_resource(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    seed_resource(
        repository,
        directory_name="quota-only-delete",
        quota={"capacity_bytes": 16 * 1024**2, "file_count": 64},
        usage={"used_bytes": 0, "used_files": 0},
        management_mode="quota_only",
    )
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_DELETE,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "quota-only-delete",
        },
    )

    Planner(repository).run_once()

    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert result["verification_summary"]["issues"] == [
        {
            "reason": "filesystem_quota_only_delete_refused",
            "resource_key": "cephfs-a:quota-only-delete",
        }
    ]


def test_phase12_import_existing_directory_records_access_quota_state(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    executor = FakePhase12FilesystemExecutor()
    # Live dir already carries a dms-grp-* group (gid 9000001). access_policy is
    # omitted entirely: the backend auto-discovers the group via stat (GPFS/WEKA parity).
    executor.seed_directory(
        "existing-import",
        resource_key=None,
        quota=None,
        group_name="dms-grp-existing-import",
        group_gid=9000001,
        mode="0770",
    )
    identity = StubIdentityGroupManager(
        users={"alice": {}, "bob": {}},
        groups={
            "dms-grp-existing-import": {
                "group_name": "dms-grp-existing-import",
                "gid": 9000001,
                "members": ["alice", "bob"],
            }
        },
    )
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_IMPORT,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "existing-import",
            "import_mode": "full",
            "quota": {"capacity_bytes": 16 * 1024**2, "file_count": 64},
            "preserve_existing_data": True,
        },
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor, identity=identity)

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:existing-import"
    )
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert resource["desired_state"]["management_mode"] == "full"
    # members auto-adopted from the live dms-grp-* group via LDAP
    assert resource["desired_state"]["users"] == ["alice", "bob"]
    adoption = resource["observed_state"]["group_adoption"]
    assert adoption["mode"] == "adopt_existing_dms_group"
    assert adoption["changed_group"] is False
    assert adoption["live_group"] == "dms-grp-existing-import"
    assert (
        resource["observed_state"]["quota_state"]["file_count"]["observed_count"] == 64
    )


def test_phase12_import_external_group_creates_dms_group(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    executor = FakePhase12FilesystemExecutor()
    # Live dir carries an EXTERNAL (non dms-grp-*) group → import creates a new
    # dms-grp-{dir}, copies members, and chowns the dir to the new gid.
    executor.seed_directory(
        "ext-import",
        resource_key=None,
        quota=None,
        group_name="ext-team",
        group_gid=8500001,
        mode="0770",
    )
    identity = StubIdentityGroupManager(
        users={"alice": {}, "bob": {}},
        groups={
            "ext-team": {
                "group_name": "ext-team",
                "gid": 8500001,
                "members": ["alice", "bob"],
            }
        },
    )
    request_id = create_request(
        repository,
        OperationKind.FILESYSTEM_IMPORT,
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "ext-import",
            "import_mode": "full",
            "quota": {"capacity_bytes": 16 * 1024**2},
        },
    )

    assert Planner(repository).run_once() == 1
    run_worker(repository, observability, executor, identity=identity)

    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "cephfs-a:ext-import"
    )
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    adoption = resource["observed_state"]["group_adoption"]
    assert adoption["mode"] == "create_dms_group_from_external"
    assert adoption["changed_group"] is True
    assert adoption["live_group"] == "ext-team"
    assert adoption["new_group"] == "dms-grp-ext-import"
    # external members copied into the new DMS group
    assert resource["desired_state"]["users"] == ["alice", "bob"]
    assert "dms-grp-ext-import" in identity.groups
    # directory was chowned to the new group's gid
    assert executor.directories["ext-import"]["group_gid"] == identity.groups[
        "dms-grp-ext-import"
    ]["gid"]


def test_phase12_import_rejects_missing_access_policy_and_unsafe_name(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="portal:phase12",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_IMPORT.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="cephfs-a:nested/path",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "nested/path",
        },
    )

    Planner(repository).run_once()

    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    # access_policy is now optional (backend auto-discovers live group); only
    # the unsafe directory_name is rejected here.
    assert {issue["reason"] for issue in result["verification_summary"]["issues"]} >= {
        "directory_name_invalid"
    }


def test_phase12_sync_endpoint_persists_filesystem_sync_request(tmp_path):
    repository, observability = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    client = client_for(tmp_path, repository, observability)

    response = client.post(
        "/api/v1/resource-management/filesystems/cephfs-a/project-alpha:sync",
        headers=API_HEADERS,
        json={
            "requester_id": "portal:phase12",
            "payload": {"source": "live", "include_quota": True},
        },
    )

    assert response.status_code == 202
    request_id = response.json()["request_id"]
    request = repository.get_request(request_id)
    assert request["operation"] == OperationKind.FILESYSTEM_SYNC.value
    assert request["resource_key"] == "cephfs-a:project-alpha"
    assert request["payload_summary"]["include_quota"] is True


@dataclass
class FakePhase12FilesystemExecutor:
    calls: list[dict[str, Any]] = field(default_factory=list)
    directories: dict[str, dict[str, Any]] = field(default_factory=dict)

    def seed_directory(
        self,
        directory_name: str,
        *,
        resource_key: str | None,
        quota: dict[str, int] | None,
        usage: dict[str, int] | None = None,
        marker: dict[str, Any] | None = None,
        management_mode: str | None = "full",
        group_name: str = "dms-grp-project",
        group_gid: int = 24000,
        mode: str = "0770",
    ) -> None:
        self.directories[directory_name] = {
            "path": f"/mnt/testbed-cephfs/dms-phase10/{directory_name}",
            "group_name": group_name,
            "group_gid": group_gid,
            "mode": mode,
            "quota_state": quota_state(
                quota or {}, usage or {"used_bytes": 0, "used_files": 0}
            ),
        }

    def create_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        group_name: str,
        mode: str,
        allowed_users: list[str],
        denied_users: list[str],
    ) -> dict[str, Any]:
        self.calls.append({"operation": "create", "directory_name": directory_name})
        self.directories[directory_name] = {
            "path": f"{managed_root}/{directory_name}",
            "group_name": group_name,
            "group_gid": 24000,
            "mode": mode,
            "quota_state": quota_state({}, {"used_bytes": 0, "used_files": 0}),
        }
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "created": True,
            "group_name": group_name,
            "group_gid": 24000,
            "mode": mode,
            "access_validation": {
                "allowed_users": {user: "ok" for user in allowed_users},
                "denied_users": {user: "denied" for user in denied_users},
            },
            "verified": True,
        }

    def apply_quota(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        quota: dict[str, int],
    ) -> dict[str, Any]:
        self.calls.append(
            {"operation": "apply_quota", "directory_name": directory_name}
        )
        directory = self.directories[directory_name]
        directory["quota_state"] = quota_state(quota, None)
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "quota_state": directory["quota_state"],
            "quota_capability": {"quota_backend": "cephfs-xattr"},
            "backend_side_effect": True,
            "verified": True,
        }

    def read_directory_state(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        include_quota: bool,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "operation": "read_directory_state",
                "directory_name": directory_name,
                "include_quota": include_quota,
            }
        )
        directory = self.directories.get(directory_name)
        if not directory:
            return {
                "path": f"{managed_root}/{directory_name}",
                "exists": False,
                "verified": False,
            }
        state = {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "group_name": directory["group_name"],
            "group_gid": directory["group_gid"],
            "mode": directory["mode"],
            "verified": True,
            "backend_side_effect": False,
        }
        if include_quota:
            current = directory["quota_state"]
            state["quota_state"] = {
                "backend_type": current.get("backend_type"),
                "capacity": current.get("capacity"),
                "file_count": current.get("file_count"),
            }
        return state

    def assign_quota_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        quota: dict[str, int],
    ) -> dict[str, Any]:
        self.calls.append(
            {"operation": "assign_quota", "directory_name": directory_name}
        )
        directory = self.directories[directory_name]
        directory["quota_state"] = quota_state(quota, None)
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "management_mode": "quota_only",
            "quota_state": directory["quota_state"],
            "backend_side_effect": True,
            "verified": True,
        }

    def import_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        chown_gid: int | None,
        quota: dict[str, int] | None,
    ) -> dict[str, Any]:
        self.calls.append({"operation": "import", "directory_name": directory_name})
        directory = self.directories[directory_name]
        if chown_gid is not None:
            # External-group adoption: directory chowned to the new DMS group.
            directory["group_gid"] = chown_gid
        if quota:
            directory["quota_state"] = quota_state(quota, None)
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "group_gid": directory["group_gid"],
            "mode": directory["mode"],
            "quota_state": directory["quota_state"],
            "management_mode": "full",
            "backend_side_effect": bool(chown_gid is not None or quota),
            "verified": True,
        }

    def delete_directory(self, **kwargs) -> dict[str, Any]:
        raise AssertionError("Phase 12 tests do not delete through fake executor")

    def block_directory(self, **kwargs) -> dict[str, Any]:
        raise AssertionError("Phase 12 tests do not block through fake executor")

    def unblock_directory(self, **kwargs) -> dict[str, Any]:
        raise AssertionError("Phase 12 tests do not unblock through fake executor")


def quota_state(quota: dict[str, int], usage: dict[str, int] | None) -> dict[str, Any]:
    state = {
        "backend_type": "cephfs",
        "capacity": {
            "desired_bytes": quota.get("capacity_bytes"),
            "applied_bytes": quota.get("capacity_bytes"),
            "observed_bytes": quota.get("capacity_bytes"),
            "backend_key": "ceph.quota.max_bytes",
        },
        "file_count": {
            "desired_count": quota.get("file_count"),
            "applied_count": quota.get("file_count"),
            "observed_count": quota.get("file_count"),
            "backend_key": "ceph.quota.max_files",
        },
    }
    if usage is not None:
        state["usage"] = {
            "used_bytes": usage.get("used_bytes", 0),
            "used_files": usage.get("used_files", 0),
            "usage_source": "fake",
        }
    return state


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
        sanity_result={
            "status": "Ready",
            "readiness": readiness,
            "errors": [],
            "warnings": [],
        },
        readiness=readiness,
    )


def seed_resource(
    repository: DmsRepository,
    *,
    directory_name: str,
    quota: dict[str, int],
    usage: dict[str, int],
    management_mode: str = "full",
    status: str = LifecycleState.SUCCEEDED.value,
) -> None:
    desired = {
        "storage_name": "cephfs-a",
        "directory_name": directory_name,
        "users": ["alice", "bob"],
        "validation_denied_users": ["mallory"],
        "access_group": f"dms-grp-{directory_name}",
        "mode": "0770",
        "resource_type": "user",
        "resource_kind": ResourceKind.FILESYSTEM.value,
        "resource_key": f"cephfs-a:{directory_name}",
        "requester_id": "portal:phase12",
        "management_mode": management_mode,
        "quota": quota,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    observed = {
        "path": f"/mnt/testbed-cephfs/dms-phase10/{directory_name}",
        "mode": "0770",
        "quota_state": quota_state(quota, usage),
        "management_mode": management_mode,
    }
    repository.upsert_resource(
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=f"cephfs-a:{directory_name}",
        desired_state=desired,
        applied_state={"quota": quota, "quota_state": observed["quota_state"]},
        observed_state=observed,
        status=status,
    )


def create_request(
    repository: DmsRepository,
    operation: OperationKind,
    *,
    payload: dict[str, Any],
) -> str:
    payload = dict(payload)
    if operation == OperationKind.FILESYSTEM_CREATE:
        payload.setdefault("expires_at", "2099-01-01T00:00:00Z")
    return repository.create_request(
        requester_id="portal:phase12",
        actor="api-client",
        operation=operation.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=f"{payload['storage_name']}:{payload['directory_name']}",
        payload=payload,
    )


def run_worker(
    repository: DmsRepository,
    observability: ObservabilityRepository,
    executor: FakePhase12FilesystemExecutor,
    identity: StubIdentityGroupManager | None = None,
) -> None:
    adapter = CephFsHostMountedFilesystemBackendAdapter(
        template=CephFsBackendTemplate(
            storage_name="cephfs-a",
            cluster_name="cluster-a",
            mount_path="/mnt/testbed-cephfs",
            managed_root="/mnt/testbed-cephfs/dms-phase10",
            rm_worker_node="c1-worker",
        ),
        identity_groups=identity
        or StubIdentityGroupManager(users={"alice": {}, "bob": {}}),
        executor=executor,
    )
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=adapter,
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-phase12",
    )
    assert worker.run_once() == 1
