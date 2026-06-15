"""GPFS/WekaFS block must set the resource lifecycle status to Blocked.

Regression: the RM worker derives the persisted resource status from
`adapter_result.observed_state["resource_status"]`, falling back to "Succeeded"
when absent (workers.py). CephFS block set it to "Blocked", but GPFS and WekaFS
block omitted it — so an on-disk `chmod 0000` lockdown showed status "Succeeded"
in `GET /operations/filesystems/...`. These tests pin the unified behavior:
block -> "Blocked", unblock -> "Succeeded".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dms.backends.gpfs import (
    CommandResult as GpfsCommandResult,
    GpfsBackendTemplate,
    GpfsFilesystemBackendAdapter,
)
from dms.backends.weka import (
    CommandResult as WekaCommandResult,
    WekaFsBackendTemplate,
    WekaFsHostMountedFilesystemBackendAdapter,
)


# --------------------------------------------------------------------------- #
# GPFS
# --------------------------------------------------------------------------- #
@dataclass
class FakeGpfsExecutor:
    calls: list[list[str]] = field(default_factory=list)

    def run(self, argv: list[str], *, timeout_seconds: int) -> GpfsCommandResult:
        self.calls.append(argv)
        return GpfsCommandResult(argv=argv, returncode=0, stdout="", stderr="")


def _gpfs_adapter(executor: FakeGpfsExecutor) -> GpfsFilesystemBackendAdapter:
    template = GpfsBackendTemplate.from_storage_mapping(
        {
            "storage_name": "gpfs-a",
            "backend_template": {
                "backend_type": "gpfs",
                "filesystem_name": "gpfs0",
                "mount_path": "/gpfs/gpfs0",
                "managed_root": "/gpfs/gpfs0/dms",
                "quota_scope": "fileset",
                "rm_worker_nodes": ["gpfs-rm"],
                "ssh_host": "gpfs-rm",
                "command_runner": "ssh-host-exec",
            },
        }
    )
    return GpfsFilesystemBackendAdapter(template=template, executor=executor)


def _plan(*, block: bool, **desired: Any) -> dict[str, Any]:
    base = {"directory_name": "proj1", "storage_name": "fs-a", "block": block}
    base.update(desired)
    return {
        "plan_id": "p",
        "request_id": "r",
        "resource_key": "fs-a:proj1",
        "operation_kind": "filesystem.block",
        "desired_state": base,
    }


def test_gpfs_block_sets_resource_status_blocked():
    executor = FakeGpfsExecutor()
    result = _gpfs_adapter(executor).block(_plan(block=True))
    assert result.observed_state["resource_status"] == "Blocked"
    assert result.observed_state["block_state"]["blocked"] is True
    # chmod 0000 actually issued
    assert ["chmod", "0000", "/gpfs/gpfs0/dms/proj1"] in executor.calls


def test_gpfs_unblock_sets_resource_status_succeeded():
    executor = FakeGpfsExecutor()
    result = _gpfs_adapter(executor).block(
        _plan(block=False, mode="0770", block_state={"previous_mode": "0770"})
    )
    assert result.observed_state["resource_status"] == "Succeeded"
    assert result.observed_state["block_state"]["blocked"] is False
    assert ["chmod", "0770", "/gpfs/gpfs0/dms/proj1"] in executor.calls


# --------------------------------------------------------------------------- #
# WekaFS
# --------------------------------------------------------------------------- #
@dataclass
class FakeWekaExecutor:
    calls: list[list[str]] = field(default_factory=list)

    def run(
        self, argv: list[str], *, timeout_seconds: int, env: dict[str, str] | None = None
    ) -> WekaCommandResult:
        self.calls.append(argv)
        return WekaCommandResult(argv=argv, returncode=0, stdout="", stderr="")


def _weka_adapter(executor: FakeWekaExecutor) -> WekaFsHostMountedFilesystemBackendAdapter:
    template = WekaFsBackendTemplate.from_storage_mapping(
        {
            "storage_name": "weka-a",
            "backend_template": {
                "backend_type": "wekafs",
                "filesystem_name": "pvs_weka",
                "mount_path": "/pvs_weka",
                "managed_root": "/pvs_weka/dms",
                "rm_worker_nodes": ["weka-rm"],
                "ssh_host": "weka-rm",
                "weka_credentials": {
                    "organization": "0",
                    "username": "admin",
                    "password": "Passw0rd!",
                },
            },
        }
    )
    return WekaFsHostMountedFilesystemBackendAdapter(
        template=template, identity_groups=object(), executor=executor
    )


def test_weka_block_sets_resource_status_blocked():
    executor = FakeWekaExecutor()
    result = _weka_adapter(executor).block(_plan(block=True))
    assert result.observed_state["resource_status"] == "Blocked"
    assert result.observed_state["block_state"]["blocked"] is True
    assert ["chmod", "0000", "/pvs_weka/dms/proj1"] in executor.calls


def test_weka_unblock_sets_resource_status_succeeded():
    executor = FakeWekaExecutor()
    result = _weka_adapter(executor).block(
        _plan(block=False, mode="0770", block_state={"previous_mode": "0770"})
    )
    assert result.observed_state["resource_status"] == "Succeeded"
    assert result.observed_state["block_state"]["blocked"] is False
    assert ["chmod", "0770", "/pvs_weka/dms/proj1"] in executor.calls
