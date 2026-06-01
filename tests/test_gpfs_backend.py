from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from dms.adapters import StubFilesystemBackendAdapter, StubKubernetesNamespaceQuotaAdapter
from dms.backend_registry import BackendAdapterRegistry
from dms.backends.gpfs import (
    GPFS_BACKEND_TYPE,
    GPFS_CSI_DRIVER,
    CommandResult,
    GpfsBackendTemplate,
    GpfsCommandExecutor,
    GpfsFilesystemBackendAdapter,
    GpfsQuotaStrategy,
    parse_gpfs_y,
    render_gpfs_block_limit,
)
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RMWorkerRuntime


@pytest.fixture()
def repository_pair(tmp_path):
    operational = Database(f"sqlite:///{tmp_path / 'operational.db'}")
    observability_db = Database(f"sqlite:///{tmp_path / 'observability.db'}")
    migrate_all(operational, observability_db)
    return DmsRepository(operational), ObservabilityRepository(observability_db)


def gpfs_mapping() -> StorageMappingInput:
    return StorageMappingInput(
        storage_name="gpfs-a",
        backend_template={
            "backend_type": GPFS_BACKEND_TYPE,
            "filesystem_name": "gpfs0",
            "mount_path": "/gpfs/gpfs0",
            "fileset_root": "/gpfs/gpfs0/dms",
            "quota_scope": "fileset",
            "fileset_name_template": "dms-{directory_name}",
            "csi_driver": GPFS_CSI_DRIVER,
            "data_network": "storage-net-a",
        },
        cluster_name="cluster-a",
        storage_class_name="gpfs-csi",
        sanity_status="Ready",
    )


def gpfs_ready_sanity() -> dict:
    return {
        "storage_name": "gpfs-a",
        "status": "Ready",
        "checked_at": "2026-05-27T00:00:00+00:00",
        "kubernetes_observed": {
            "cluster_name": "cluster-a",
            "storage_class_name": "gpfs-csi",
            "storage_class_exists": True,
            "provisioner": GPFS_CSI_DRIVER,
        },
        "agent_observed": {
            "fresh_reports": 2,
            "stale_reports": 0,
            "rm_readiness": "Ready",
            "dm_readiness": "Ready",
            "rm_candidates": [{"cluster_name": "cluster-a", "node_name": "rm-gpfs"}],
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "dm-gpfs"}],
        },
        "readiness": {
            "resource_management": "Ready",
            "data_management": "Ready",
            "inventory": "Ready",
        },
        "checks": [],
        "warnings": [],
        "errors": [],
    }


def register_gpfs_mapping(repository: DmsRepository) -> None:
    sanity = gpfs_ready_sanity()
    repository.upsert_storage_mapping(
        gpfs_mapping(),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


def test_gpfs_quota_strategy_renders_filesystem_quota_shape():
    rendered = GpfsQuotaStrategy().render_quota(
        {"capacity_bytes": 10**12, "file_count": 5_000_000}
    )

    assert rendered["backend_type"] == GPFS_BACKEND_TYPE
    assert rendered["quota_scope"] == "fileset"
    assert rendered["hard_limit_bytes"] == 10**12
    assert rendered["hard_file_limit"] == 5_000_000
    assert rendered["block_limit"] == render_gpfs_block_limit(10**12)
    assert rendered["files_limit"] == "5000000:5000000"
    assert rendered["side_effect"] == "gpfs-command"


def test_gpfs_parse_y_output_uses_headers():
    rows = parse_gpfs_y(
        "\n".join(
            [
                "mmlsquota::HEADER:version:reserved:reserved:filesystemName:filesetName:type:blockLimit:filesLimit",
                "mmlsquota::0:1:::gpfs0:dms-alpha:FILESET:8192:32",
            ]
        )
    )

    assert rows == [
        {
            "version": "1",
            "reserved": "",
            "filesystemName": "gpfs0",
            "filesetName": "dms-alpha",
            "type": "FILESET",
            "blockLimit": "8192",
            "filesLimit": "32",
            "_raw": "mmlsquota::0:1:::gpfs0:dms-alpha:FILESET:8192:32",
        }
    ]


def test_gpfs_filesystem_create_uses_storage_scale_commands(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={
            "storage_name": "gpfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    worker = gpfs_worker(repository, observability, FakeGpfsExecutor())

    assert worker.run_once() == 1
    [resource] = repository.list_resources()
    commands = resource["observed_state"]["command_evidence"]
    argv = [entry["argv"] for entry in commands]
    assert repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["observed_state"]["adapter"] == "gpfs-fileset-command"
    assert ["mmcrfileset", "gpfs0", "dms-project-alpha", "--inode-space", "new"] in argv
    assert [
        "mmsetquota",
        "gpfs0:dms-project-alpha",
        "--block",
        "8192K:8192K",
        "--files",
        "32:32",
    ] in argv
    assert [
        "mmlinkfileset",
        "gpfs0",
        "dms-project-alpha",
        "-J",
        "/gpfs/gpfs0/dms/project-alpha",
    ] in argv
    assert resource["observed_state"]["quota_state"]["capacity"]["observed_bytes"] == 8 * 1024**2
    assert resource["observed_state"]["quota_state"]["file_count"]["observed_count"] == 32


def test_gpfs_check_reports_quota_drift(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    executor.filesets["dms-project-alpha"] = "/gpfs/gpfs0/dms/project-alpha"
    executor.quotas["dms-project-alpha"] = {"block_kib": 16384, "files": 32}
    seed_gpfs_resource(
        repository,
        directory_name="project-alpha",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
    )
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CHECK.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={"storage_name": "gpfs-a", "directory_name": "project-alpha"},
    )
    Planner(repository).run_once()
    worker = gpfs_worker(repository, observability, executor)

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.SUCCEEDED.value
    assert result["verification_summary"]["quota_status"] == "Drifted"
    assert result["verification_summary"]["issues"][0]["issue_type"] == "filesystem_quota_drifted"


def test_gpfs_sync_accepts_live_quota(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    executor.filesets["dms-project-alpha"] = "/gpfs/gpfs0/dms/project-alpha"
    executor.quotas["dms-project-alpha"] = {"block_kib": 16384, "files": 64}
    seed_gpfs_resource(
        repository,
        directory_name="project-alpha",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
    )
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_SYNC.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={"storage_name": "gpfs-a", "directory_name": "project-alpha"},
    )
    Planner(repository).run_once()
    worker = gpfs_worker(repository, observability, executor)

    assert worker.run_once() == 1
    resource = repository.get_resource(ResourceKind.FILESYSTEM.value, "gpfs-a:project-alpha")
    assert resource["desired_state"]["quota"] == {
        "capacity_bytes": 16 * 1024**2,
        "file_count": 64,
    }


def test_gpfs_import_requires_linked_fileset(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_IMPORT.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:ordinary-dir",
        payload={
            "storage_name": "gpfs-a",
            "directory_name": "ordinary-dir",
            "import_mode": "full",
            "access_policy": {
                "mode": "adopt_existing_group",
                "expected_group": "dms-phase13",
                "expected_mode": "0770",
                "users": ["alice", "bob"],
            },
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
        },
    )
    Planner(repository).run_once()
    worker = gpfs_worker(repository, observability, FakeGpfsExecutor())

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    assert result["verification_summary"]["backend_side_effect"] is False


def test_gpfs_delete_refuses_quota_only_resource(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    seed_gpfs_resource(
        repository,
        directory_name="quota-only",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
        management_mode="quota_only",
    )
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_DELETE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:quota-only",
        payload={"storage_name": "gpfs-a", "directory_name": "quota-only"},
    )
    Planner(repository).run_once()
    assert repository.get_request(request_id)["status"] == LifecycleState.REJECTED.value
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.REJECTED.value
    assert result["verification_summary"]["issues"] == [
        {
            "reason": "filesystem_quota_only_delete_refused",
            "resource_key": "gpfs-a:quota-only",
        }
    ]


def test_gpfs_missing_command_fails_closed(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={
            "storage_name": "gpfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    worker = gpfs_worker(repository, observability, FakeGpfsExecutor(missing={"mmsetquota"}))

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    assert "GPFS command missing: mmsetquota" in result["message"]


def test_gpfs_quota_disabled_fails_before_side_effect(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={
            "storage_name": "gpfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    worker = gpfs_worker(repository, observability, FakeGpfsExecutor(quota_enabled=False))

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    assert "GPFS filesystem quota disabled" in result["message"]
    assert result["verification_summary"]["backend_side_effect"] is False


def test_gpfs_perfileset_quota_disabled_fails_before_side_effect(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={
            "storage_name": "gpfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    worker = gpfs_worker(
        repository,
        observability,
        FakeGpfsExecutor(perfileset_quota_enabled=False),
    )

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    assert "GPFS per-fileset quota disabled" in result["message"]
    assert result["verification_summary"]["backend_side_effect"] is False


def test_gpfs_quota_readback_mismatch_records_unknown_after_side_effect(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={
            "storage_name": "gpfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    worker = gpfs_worker(
        repository,
        observability,
        FakeGpfsExecutor(quota_readback_block_kib_delta=1),
    )

    with pytest.raises(RuntimeError, match="GPFS quota capacity read-back mismatch"):
        worker.run_once()
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value
    assert result["verification_summary"]["recovery_required"] is True


def test_gpfs_kubernetes_namespace_quota_uses_gpfs_csi_mapping(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.K8S_QUOTA_CREATE.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key="cluster-a:alice",
        payload={
            "cluster_name": "cluster-a",
            "namespace_name": "alice",
            "storage_class_quotas": [{"storage_name": "gpfs-a"}],
            "quota": {"requests_storage_bytes": 4 * 10**12, "pvc_count": 20},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    registry = BackendAdapterRegistry.with_test_stubs(repository)
    Planner(repository, backend_registry=registry).run_once()
    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=StubFilesystemBackendAdapter(),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-gpfs",
        backend_registry=registry,
    )

    assert worker.run_once() == 1
    [resource] = repository.list_resources()
    assert repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    assert resource["observed_state"]["adapter"] == "gpfs-kubernetes-quota-stub"
    assert resource["observed_state"]["backend"]["csi_driver"] == GPFS_CSI_DRIVER
    assert resource["observed_state"]["backend"]["storage_class_name"] == "gpfs-csi"
    assert resource["observed_state"]["hard"]["requests.storage"] == 4 * 10**12


def test_gpfs_data_management_planning_records_gpfs_worker_pool(repository_pair):
    repository, _ = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.DATA_SCAN.value,
        resource_kind=ResourceKind.DATA_JOB.value,
        resource_key="gpfs-a:data.scan:project-alpha",
        payload={
            "storage_name": "gpfs-a",
            "target_path": "project-alpha",
            "priority": 100,
        },
    )

    Planner(
        repository,
        backend_registry=BackendAdapterRegistry.with_test_stubs(repository),
    ).run_once()

    job = repository.get_data_job_by_request(request_id)
    assert job is not None
    assert job["worker_pool"]["backend_type"] == GPFS_BACKEND_TYPE
    assert job["worker_pool"]["required_mounts"] == ["gpfs-a"]
    assert job["worker_pool"]["mount_path"] == "/gpfs/gpfs0"
    assert "dscan" in job["worker_pool"]["tool_candidates"]


def gpfs_worker(
    repository: DmsRepository,
    observability: ObservabilityRepository,
    executor: GpfsCommandExecutor,
) -> RMWorkerRuntime:
    template = GpfsBackendTemplate.from_storage_mapping(repository.get_storage_mapping("gpfs-a"))
    return RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=GpfsFilesystemBackendAdapter(template=template, executor=executor),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-gpfs",
    )


def seed_gpfs_resource(
    repository: DmsRepository,
    *,
    directory_name: str,
    quota: dict[str, int],
    management_mode: str = "full",
) -> None:
    desired = {
        "storage_name": "gpfs-a",
        "directory_name": directory_name,
        "access_group": f"dms-phase10-{directory_name}",
        "mode": "0770",
        "resource_kind": ResourceKind.FILESYSTEM.value,
        "resource_key": f"gpfs-a:{directory_name}",
        "management_mode": management_mode,
        "quota": quota,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    observed = {
        "adapter": "gpfs-fileset-command",
        "path": f"/gpfs/gpfs0/dms/{directory_name}",
        "quota_state": {
            "backend_type": GPFS_BACKEND_TYPE,
            "fileset_name": f"dms-{directory_name}",
            "capacity": {"observed_bytes": quota["capacity_bytes"]},
            "file_count": {"observed_count": quota["file_count"]},
        },
        "management_mode": management_mode,
    }
    repository.upsert_resource(
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=f"gpfs-a:{directory_name}",
        desired_state=desired,
        applied_state={"quota": quota, "quota_state": observed["quota_state"]},
        observed_state=observed,
        status=LifecycleState.SUCCEEDED.value,
    )


@dataclass
class FakeGpfsExecutor:
    missing: set[str] = field(default_factory=set)
    filesets: dict[str, str] = field(default_factory=dict)
    quotas: dict[str, dict[str, int]] = field(default_factory=dict)
    commands: list[list[str]] = field(default_factory=list)
    quota_enabled: bool = True
    perfileset_quota_enabled: bool = True
    quota_readback_block_kib_delta: int = 0

    def run(self, argv: list[str], *, timeout_seconds: int) -> CommandResult:
        self.commands.append(argv)
        if argv[:2] == ["sh", "-c"]:
            command = argv[2].split()[-1]
            return result(argv, 1 if command in self.missing else 0, stdout=f"/usr/lpp/mmfs/bin/{command}\n")
        command = argv[0]
        if command in self.missing:
            return result(argv, 127, stderr=f"{command}: not found")
        if command == "mmlsfs":
            enabled = self.quota_enabled
            if "--perfileset-quota" in argv:
                enabled = self.perfileset_quota_enabled
            return result(argv, 0, stdout=mmlsfs_y(argv[1], enabled))
        if command == "mmlsfileset":
            return self._mmlsfileset(argv)
        if command == "mmcrfileset":
            self.filesets[argv[2]] = ""
            return result(argv, 0, stdout=f"Fileset '{argv[2]}' created.\n")
        if command == "mmsetquota":
            fileset_name = argv[1].split(":", 1)[1]
            block = argv[argv.index("--block") + 1] if "--block" in argv else "0K:0K"
            files = argv[argv.index("--files") + 1] if "--files" in argv else "0:0"
            self.quotas[fileset_name] = {
                "block_kib": int(block.split(":", 1)[1].removesuffix("K")),
                "files": int(files.split(":", 1)[1]),
            }
            return result(argv, 0)
        if command == "mmlinkfileset":
            self.filesets[argv[2]] = argv[argv.index("-J") + 1]
            return result(argv, 0)
        if command == "mmlsquota":
            fileset_name = argv[argv.index("-j") + 1]
            quota = self.quotas.get(fileset_name, {"block_kib": 0, "files": 0})
            quota = {
                **quota,
                "block_kib": quota["block_kib"] + self.quota_readback_block_kib_delta,
            }
            return result(argv, 0, stdout=mmlsquota_y(argv[-1], fileset_name, quota))
        if command in {"chgrp", "chmod", "python3", "mmunlinkfileset", "mmdelfileset"}:
            if command == "mmunlinkfileset":
                self.filesets[argv[2]] = ""
            if command == "mmdelfileset":
                self.filesets.pop(argv[2], None)
            return result(argv, 0)
        return result(argv, 0)

    def _mmlsfileset(self, argv: list[str]) -> CommandResult:
        if "-J" in argv:
            junction = argv[argv.index("-J") + 1]
            for name, path in self.filesets.items():
                if path == junction:
                    return result(argv, 0, stdout=mmlsfileset_y(name, path))
            return result(argv, 1, stderr="No filesets found")
        fileset_name = argv[2] if len(argv) > 2 and not argv[2].startswith("-") else None
        if fileset_name in self.filesets:
            return result(argv, 0, stdout=mmlsfileset_y(fileset_name, self.filesets[fileset_name]))
        return result(argv, 1, stderr="No filesets found")


def result(argv: list[str], returncode: int, *, stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(argv=argv, returncode=returncode, stdout=stdout, stderr=stderr)


def mmlsfs_y(filesystem_name: str, enabled: bool) -> str:
    value = "yes" if enabled else "no"
    return "\n".join(
        [
            "mmlsfs::HEADER:version:reserved:reserved:deviceName:fieldName:data",
            f"mmlsfs::0:1:::{filesystem_name}:quota:{value}",
        ]
    )


def mmlsfileset_y(fileset_name: str, path: str) -> str:
    return "\n".join(
        [
            "mmlsfileset::HEADER:version:reserved:reserved:filesetName:status:path:inodeSpace",
            f"mmlsfileset::0:1:::{fileset_name}:Linked:{path}:new",
        ]
    )


def mmlsquota_y(filesystem_name: str, fileset_name: str, quota: dict[str, int]) -> str:
    return "\n".join(
        [
            "mmlsquota::HEADER:version:reserved:reserved:filesystemName:filesetName:type:blockUsage:blockQuota:blockLimit:filesUsage:filesQuota:filesLimit",
            f"mmlsquota::0:1:::{filesystem_name}:{fileset_name}:FILESET:0:{quota['block_kib']}:{quota['block_kib']}:0:{quota['files']}:{quota['files']}",
        ]
    )
