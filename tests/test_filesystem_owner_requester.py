"""Owner-of-directory = requester for filesystem create (2026-06-15).

The planner defaults the new directory's owner to the request's requester_id and
honours an explicit ``owner_username`` override (with a cheap basename / reserved
sanity check). The CephFS adapter forwards the resolved owner to the host executor,
which resolves it to a POSIX uid and refuses unresolvable / system owners as a
pre-side-effect precondition (enforced live, not in these unit tests).
"""

from __future__ import annotations

from typing import Any

from dms.adapters import StubIdentityGroupManager
from dms.backends.cephfs import (
    CephFsBackendTemplate,
    CephFsHostMountedFilesystemBackendAdapter,
)
from dms.domain import LifecycleState, OperationKind, ResourceKind
from dms.planner import Planner

from test_phase10_filesystem_rm import (
    FakeFilesystemExecutor,
    filesystem_plan,
    register_cephfs_mapping,
    repository_pair,
)


def _create_request(repository, *, requester_id: str, payload: dict[str, Any]) -> str:
    payload = dict(payload)
    payload.setdefault("expires_at", "2099-01-01T00:00:00Z")
    return repository.create_request(
        requester_id=requester_id,
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=f"{payload['storage_name']}:{payload['directory_name']}",
        payload=payload,
    )


def _reasons(repository, request_id: str) -> set[str]:
    [result] = repository.get_results(request_id)
    return {issue["reason"] for issue in result["verification_summary"]["issues"]}


def test_create_defaults_owner_to_requester(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = _create_request(
        repository,
        requester_id="alice",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "owned-by-alice",
            "users": ["alice", "bob"],
        },
    )
    assert Planner(repository).run_once() == 1
    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["owner_username"] == "alice"


def test_create_honors_explicit_owner_override(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = _create_request(
        repository,
        requester_id="portal:service",  # non-POSIX requester id
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "owned-by-bob",
            "users": ["alice", "bob"],
            "owner_username": "bob",
        },
    )
    assert Planner(repository).run_once() == 1
    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["owner_username"] == "bob"


def test_create_rejects_reserved_owner_override(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = _create_request(
        repository,
        requester_id="portal:service",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "owned-by-root",
            "users": ["alice", "bob"],
            "owner_username": "root",
        },
    )
    Planner(repository).run_once()
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert "filesystem_owner_username_unsupported" in _reasons(repository, request_id)


def test_create_rejects_unsafe_owner_override(tmp_path):
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = _create_request(
        repository,
        requester_id="portal:service",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "owned-by-unsafe",
            "users": ["alice", "bob"],
            "owner_username": "bad:name",  # not a safe POSIX basename
        },
    )
    Planner(repository).run_once()
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert "owner_username_invalid" in _reasons(repository, request_id)


def test_create_does_not_basename_check_free_form_requester(tmp_path):
    # requester_id may be a non-POSIX logical id (e.g. "portal:phase10"); the planner
    # must NOT reject create on that basis (the backend enforces resolvability live).
    repository, _ = repository_pair(tmp_path)
    register_cephfs_mapping(repository)
    request_id = _create_request(
        repository,
        requester_id="portal:phase10",
        payload={
            "storage_name": "cephfs-a",
            "directory_name": "free-form-requester",
            "users": ["alice", "bob"],
        },
    )
    assert Planner(repository).run_once() == 1
    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["owner_username"] == "portal:phase10"


def test_adapter_forwards_owner_to_executor_and_applied_state():
    executor = FakeFilesystemExecutor()
    identity_groups = StubIdentityGroupManager(users={"alice": {}, "bob": {}})
    adapter = CephFsHostMountedFilesystemBackendAdapter(
        template=CephFsBackendTemplate(
            storage_name="cephfs-a",
            cluster_name="cluster-a",
            mount_path="/mnt/testbed-cephfs",
            managed_root="/mnt/testbed-cephfs/dms",
            rm_worker_node="c1-worker",
        ),
        identity_groups=identity_groups,
        executor=executor,
    )
    plan = filesystem_plan(
        operation=OperationKind.FILESYSTEM_CREATE,
        desired_state={
            "storage_name": "cephfs-a",
            "directory_name": "owned-by-alice",
            "users": ["alice", "bob"],
            "access_group": "dms-grp-owned-by-alice",
            "owner_username": "alice",
        },
    )

    result = adapter.create(plan)

    assert executor.calls[0]["owner_username"] == "alice"
    assert result.applied_state["owner_username"] == "alice"
