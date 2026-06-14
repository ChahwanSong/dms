from __future__ import annotations

from typing import Any

import pytest

from dms.adapters import BackendPreconditionError, StubIdentityGroupManager
from dms.backends.cephfs import (
    CephFsBackendTemplate,
    CephFsHostMountedFilesystemBackendAdapter,
    HostExecutionError,
    PythonHostExecutor,
)


class _RecordingExecutor:
    """Minimal executor that records delete calls and returns a soft-delete result."""

    def __init__(self) -> None:
        self.delete_calls: list[dict[str, Any]] = []

    def delete_directory(
        self, *, managed_root: str, directory_name: str, resource_key: str
    ) -> dict[str, Any]:
        self.delete_calls.append(
            {
                "managed_root": managed_root,
                "directory_name": directory_name,
                "resource_key": resource_key,
            }
        )
        return {
            "path": f"{managed_root}/{directory_name}",
            "exists": True,
            "deleted": True,
            "soft_delete": True,
            "data_preserved": True,
        }


def _adapter(executor: Any) -> CephFsHostMountedFilesystemBackendAdapter:
    template = CephFsBackendTemplate(
        storage_name="cephfs-a",
        cluster_name="dms",
        mount_path="/cephfs",
        managed_root="/cephfs/dms",
        rm_worker_node="dms-w1",
    )
    return CephFsHostMountedFilesystemBackendAdapter(
        template=template,
        identity_groups=StubIdentityGroupManager(),
        executor=executor,
    )


def _delete_plan(**desired: Any) -> dict[str, Any]:
    base = {
        "storage_name": "cephfs-a",
        "directory_name": "proj1",
        "access_group": "dms-grp-proj1",
    }
    base.update(desired)
    return {
        "request_id": "req_test",
        "resource_key": "cephfs-a:proj1",
        "desired_state": base,
    }


def test_cephfs_delete_is_soft_and_preserves_data():
    executor = _RecordingExecutor()
    adapter = _adapter(executor)

    result = adapter.delete(_delete_plan())

    assert executor.delete_calls, "backend lock (delete_directory) must be invoked"
    assert result.applied_state["operation"] == "delete"
    assert result.applied_state["soft_delete"] is True
    assert result.applied_state["data_preserved"] is True
    assert result.observed_state["resource_status"] == "Deleted"
    assert result.observed_state["soft_delete"] is True


def test_cephfs_delete_refuses_quota_only_resource():
    adapter = _adapter(_RecordingExecutor())
    with pytest.raises(BackendPreconditionError):
        adapter.delete(_delete_plan(management_mode="quota_only"))


def test_cephfs_delete_refuses_imported_resource():
    adapter = _adapter(_RecordingExecutor())
    with pytest.raises(BackendPreconditionError):
        adapter.delete(_delete_plan(import_mode="full"))


def test_host_script_precondition_sentinel_maps_to_precondition_error():
    executor = PythonHostExecutor(mode="local", use_sudo=False)
    with pytest.raises(BackendPreconditionError) as exc:
        executor._run_script("raise SystemExit('PRECONDITION: nope')", [])
    assert "nope" in str(exc.value)


def test_host_script_plain_failure_stays_host_execution_error():
    executor = PythonHostExecutor(mode="local", use_sudo=False)
    with pytest.raises(HostExecutionError):
        executor._run_script("raise SystemExit('plain failure')", [])


def test_delete_script_is_idempotent_when_directory_missing(tmp_path):
    # Soft-delete script runs without root and returns success when the directory
    # is already gone (no rmtree, no marker required).
    executor = PythonHostExecutor(mode="local", use_sudo=False)
    out = executor.delete_directory(
        managed_root=str(tmp_path),
        directory_name="does-not-exist",
        resource_key="cephfs-a:does-not-exist",
    )
    assert out["exists"] is False
    assert out["deleted"] is True
    assert out["soft_delete"] is True
