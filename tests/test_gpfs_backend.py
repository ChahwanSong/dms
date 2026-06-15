from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from dms.adapters import (
    AdapterResult,
    BackendPreconditionError,
    IdentityLookupResult,
    StubFilesystemBackendAdapter,
    StubIdentityGroupManager,
    StubIdentityLookupAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
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


@dataclass
class RecordingKubernetesQuotaAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {
            "cluster_name": cluster_name,
            "namespace_name": namespace_name,
            "exists": True,
        }

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("apply_resource_quota", plan["plan_id"]))
        desired = plan["desired_state"]
        hard = desired["resource_quota_hard"]
        resource_quota = {
            "exists": True,
            "cluster_name": desired["cluster_name"],
            "name": "dms-storage-quota",
            "namespace": desired["namespace_name"],
            "uid": "rq-gpfs-csi",
            "resource_version": "42",
            "labels": {"app.kubernetes.io/managed-by": "dms"},
            "annotations": {
                "dms.io/resource-key": plan["resource_key"],
                "dms.io/storage-names": "gpfs-a",
                "dms.io/expires-at": desired["expires_at"],
            },
            "spec_hard": hard,
            "status_hard": hard,
            "status_used": {"requests.storage": "0", "persistentvolumeclaims": "0"},
        }
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-namespace-quota-live",
                "operation": "resourcequota.apply",
                "backend_side_effect": True,
                "hard": hard,
            },
            observed_state={
                "adapter": "kubernetes-namespace-quota-live",
                "verified": True,
                "backend_side_effect": True,
                "resource_quota": resource_quota,
            },
            message="Kubernetes ResourceQuota live apply completed",
        )

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult:
        return self.apply_resource_quota(plan)

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        raise NotImplementedError

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        raise NotImplementedError

    def import_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        raise NotImplementedError

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        raise NotImplementedError

    def audit_resource_quotas(self, plan: dict[str, Any]) -> AdapterResult:
        raise NotImplementedError


def gpfs_mapping() -> StorageMappingInput:
    return StorageMappingInput(
        storage_name="gpfs-a",
        backend_template={
            "backend_type": GPFS_BACKEND_TYPE,
            "filesystem_name": "gpfs0",
            "mount_path": "/gpfs/gpfs0",
            "managed_root": "/gpfs/gpfs0/dms",
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
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
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
    assert (
        resource["observed_state"]["quota_state"]["capacity"]["observed_bytes"]
        == 8 * 1024**2
    )
    assert (
        resource["observed_state"]["quota_state"]["file_count"]["observed_count"] == 32
    )


def test_gpfs_check_reports_quota_drift(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    executor.filesets["dms-project-alpha"] = "/gpfs/gpfs0/dms/project-alpha"
    # desired 8 MiB; live 200 MiB ⇒ 192 MiB diff exceeds 100 MiB capacity tolerance.
    executor.quotas["dms-project-alpha"] = {"block_kib": 200 * 1024, "files": 32}
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
    assert (
        result["verification_summary"]["issues"][0]["issue_type"]
        == "filesystem_quota_drifted"
    )


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
    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "gpfs-a:project-alpha"
    )
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
    worker = gpfs_worker(
        repository, observability, FakeGpfsExecutor(missing={"mmsetquota"})
    )

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
    worker = gpfs_worker(
        repository, observability, FakeGpfsExecutor(quota_enabled=False)
    )

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


def test_gpfs_quota_readback_mismatch_records_unknown_after_side_effect(
    repository_pair,
):
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
    # 200 MiB delta exceeds the 100 MiB capacity tolerance.
    worker = gpfs_worker(
        repository,
        observability,
        FakeGpfsExecutor(quota_readback_block_kib_delta=200 * 1024),
    )

    with pytest.raises(RuntimeError, match="GPFS quota capacity read-back mismatch"):
        worker.run_once()
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value
    assert result["verification_summary"]["recovery_required"] is True


def test_gpfs_kubernetes_namespace_quota_uses_gpfs_csi_mapping(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    kubernetes_adapter = RecordingKubernetesQuotaAdapter()
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.K8S_QUOTA_CREATE.value,
        resource_kind=ResourceKind.KUBERNETES_NAMESPACE_QUOTA.value,
        resource_key="cluster-a:alice",
        payload={
            "cluster_name": "cluster-a",
            "namespace_name": "alice",
            "storage_class_quotas": [{"storage_name": "gpfs-a", "pvc_count": 20}],
            "quota": {"requests_storage_bytes": 4 * 1024**4, "pvc_count": 20},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    registry = BackendAdapterRegistry(
        repository=repository,
        default_kubernetes_adapter=kubernetes_adapter,
        enforce_supported_backends=True,
    )
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
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert kubernetes_adapter.calls == [
        ("apply_resource_quota", repository.get_plan_by_request(request_id)["plan_id"])
    ]
    assert resource["observed_state"]["adapter"] == "kubernetes-namespace-quota-live"
    assert resource["observed_state"]["resource_quota"]["spec_hard"] == {
        "requests.storage": "4096Gi",
        "persistentvolumeclaims": "20",
        "gpfs-csi.storageclass.storage.k8s.io/requests.storage": "4096Gi",
        "gpfs-csi.storageclass.storage.k8s.io/persistentvolumeclaims": "20",
    }
    assert "gpfs-kubernetes-quota-stub" not in str(resource["observed_state"])


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
    template = GpfsBackendTemplate.from_storage_mapping(
        repository.get_storage_mapping("gpfs-a")
    )
    return RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=GpfsFilesystemBackendAdapter(
            template=template, executor=executor
        ),
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
        "access_group": f"dms-grp-{directory_name}",
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
            script = argv[2]
            # Batch command-check: "command -v X >/dev/null || echo MISSING:X && ..."
            if "MISSING:" in script:
                missing_lines = [f"MISSING:{c}" for c in self.missing if c in script]
                return result(argv, 0, stdout="\n".join(missing_lines))
            # stat-based access probe
            if script.startswith("stat -c"):
                return result(argv, 0, stdout="9000000 770\n")
            # getent group probe
            if script.startswith("getent group"):
                return result(argv, 0, stdout="")
            # Legacy single command -v fallback
            command = script.split()[-1]
            return result(
                argv,
                1 if command in self.missing else 0,
                stdout=f"/usr/lpp/mmfs/bin/{command}\n",
            )
        command = argv[0]
        if command in self.missing:
            return result(argv, 127, stderr=f"{command}: not found")
        if command == "mmlsfs":
            # Combined -Q --perfileset-quota call or individual
            enabled = self.quota_enabled
            pf_enabled = self.perfileset_quota_enabled
            return result(argv, 0, stdout=mmlsfs_y(argv[1], enabled, pf_enabled))
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
        if command in {
            "chgrp",
            "chown",
            "chmod",
            "python3",
            "mmunlinkfileset",
            "mmdelfileset",
            "sss_cache",
            "sudo",
        }:
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
        fileset_name = (
            argv[2] if len(argv) > 2 and not argv[2].startswith("-") else None
        )
        if fileset_name in self.filesets:
            return result(
                argv, 0, stdout=mmlsfileset_y(fileset_name, self.filesets[fileset_name])
            )
        return result(argv, 1, stderr="No filesets found")


def result(
    argv: list[str], returncode: int, *, stdout: str = "", stderr: str = ""
) -> CommandResult:
    return CommandResult(argv=argv, returncode=returncode, stdout=stdout, stderr=stderr)


def mmlsfs_y(
    filesystem_name: str, enabled: bool, perfileset_enabled: bool = True
) -> str:
    value = "yes" if enabled else "no"
    pf_value = "yes" if perfileset_enabled else "no"
    return "\n".join(
        [
            "mmlsfs::HEADER:version:reserved:reserved:deviceName:fieldName:data:remarks:",
            f"mmlsfs::0:1:::{filesystem_name}:quotasEnforced:{value}::",
            f"mmlsfs::0:1:::{filesystem_name}:perfilesetQuotas:{pf_value}::",
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


def gpfs_adapter_with_groups(
    repository: DmsRepository,
    executor: GpfsCommandExecutor,
    *,
    users: dict[str, Any],
) -> GpfsFilesystemBackendAdapter:
    template = GpfsBackendTemplate.from_storage_mapping(
        repository.get_storage_mapping("gpfs-a")
    )
    return GpfsFilesystemBackendAdapter(
        template=template,
        executor=executor,
        identity_groups=StubIdentityGroupManager(users=users),
    )


def test_gpfs_create_calls_ensure_group_members_and_chown_by_gid(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    adapter = gpfs_adapter_with_groups(
        repository, executor, users={"alice": {}, "bob": {}}
    )
    plan = {
        "plan_id": "p1",
        "request_id": "r1",
        "resource_key": "gpfs-a:project-alpha",
        "operation_kind": OperationKind.FILESYSTEM_CREATE.value,
        "desired_state": {
            "storage_name": "gpfs-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }

    result_adapter = adapter.create(plan)

    argv = [cmd for cmd in executor.commands]
    chown_cmds = [a for a in argv if a[0] == "chown"]
    chgrp_cmds = [a for a in argv if a[0] == "chgrp"]
    assert chown_cmds, "chown should be called when identity_groups is configured"
    assert not chgrp_cmds, "chgrp should not be called when chown by gid is used"
    assert any(
        ":24000" in " ".join(a) for a in chown_cmds
    ), "chown should use gid 24000"
    assert result_adapter.applied_state["access_group"]["gid"] == 24000
    assert (
        result_adapter.applied_state["access_group"]["group_name"]
        == "dms-grp-project-alpha"
    )
    assert set(result_adapter.applied_state["access_group"]["members"]) == {
        "alice",
        "bob",
    }


def test_gpfs_create_access_probe_in_observed_state(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    adapter = gpfs_adapter_with_groups(
        repository, executor, users={"alice": {}, "bob": {}}
    )
    plan = {
        "plan_id": "p2",
        "request_id": "r2",
        "resource_key": "gpfs-a:project-beta",
        "operation_kind": OperationKind.FILESYSTEM_CREATE.value,
        "desired_state": {
            "storage_name": "gpfs-a",
            "directory_name": "project-beta",
            "users": ["alice", "bob"],
        },
    }

    result_adapter = adapter.create(plan)

    assert "access_validation" in result_adapter.observed_state
    av = result_adapter.observed_state["access_validation"]
    assert "alice" in av["allowed_users"]
    assert "bob" in av["allowed_users"]


def test_gpfs_create_denied_users_probe(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    adapter = gpfs_adapter_with_groups(
        repository, executor, users={"alice": {}, "bob": {}}
    )
    plan = {
        "plan_id": "p3",
        "request_id": "r3",
        "resource_key": "gpfs-a:project-gamma",
        "operation_kind": OperationKind.FILESYSTEM_CREATE.value,
        "desired_state": {
            "storage_name": "gpfs-a",
            "directory_name": "project-gamma",
            "users": ["alice", "bob"],
            "denied_users": ["eve"],
        },
    }

    result_adapter = adapter.create(plan)

    av = result_adapter.observed_state["access_validation"]
    assert "eve" in av["denied_users"]


def test_gpfs_delete_calls_delete_group_when_identity_groups_configured(
    repository_pair,
):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    executor.filesets["dms-project-alpha"] = "/gpfs/gpfs0/dms/project-alpha"
    seed_gpfs_resource(
        repository,
        directory_name="project-alpha",
        quota={"capacity_bytes": 8 * 1024**2, "file_count": 32},
    )
    identity = StubIdentityGroupManager(users={"alice": {}, "bob": {}})
    identity.groups["dms-grp-project-alpha"] = {
        "group_name": "dms-grp-project-alpha",
        "gid": 24000,
        "members": ["alice", "bob"],
    }
    template = GpfsBackendTemplate.from_storage_mapping(
        repository.get_storage_mapping("gpfs-a")
    )
    adapter = GpfsFilesystemBackendAdapter(
        template=template, executor=executor, identity_groups=identity
    )

    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_DELETE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:project-alpha",
        payload={"storage_name": "gpfs-a", "directory_name": "project-alpha"},
    )
    Planner(repository).run_once()
    from dms.workers import RMWorkerRuntime

    worker = RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=adapter,
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-gpfs",
    )
    assert worker.run_once() == 1

    assert "dms-grp-project-alpha" not in identity.groups
    [result_item] = repository.get_results(request_id)
    assert result_item["terminal_status"] == LifecycleState.SUCCEEDED.value
    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "gpfs-a:project-alpha"
    )
    assert "access_group_cleanup" in resource["applied_state"]


def test_gpfs_create_without_identity_groups_falls_back_to_chgrp(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    # identity_groups=None → legacy chgrp behavior
    adapter = gpfs_worker(repository, observability, executor).filesystem_adapter
    plan = {
        "plan_id": "p5",
        "request_id": "r5",
        "resource_key": "gpfs-a:project-delta",
        "operation_kind": OperationKind.FILESYSTEM_CREATE.value,
        "desired_state": {
            "storage_name": "gpfs-a",
            "directory_name": "project-delta",
            "access_group": "dms-grp-project-delta",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 8 * 1024**2, "file_count": 32},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }

    adapter.create(plan)

    chgrp_cmds = [a for a in executor.commands if a[0] == "chgrp"]
    chown_cmds = [a for a in executor.commands if a[0] == "chown"]
    assert chgrp_cmds, "chgrp should be called when identity_groups is None"
    assert not chown_cmds, "chown should not be called when identity_groups is None"


def test_gpfs_filesystem_create_with_single_user_succeeds(repository_pair):
    repository, observability = repository_pair
    register_gpfs_mapping(repository)
    request_id = repository.create_request(
        requester_id="portal",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="gpfs-a:solo-project",
        payload={
            "storage_name": "gpfs-a",
            "directory_name": "solo-project",
            "users": ["alice"],
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )

    assert Planner(repository).run_once() == 1

    plan = repository.get_plan_by_request(request_id)
    assert plan is not None
    assert plan["desired_state"]["users"] == ["alice"]


# --- owner = requester: strict resolution (fail-closed), no uid-range restriction ---


def _ldap_lookup(username: str, uid: int) -> StubIdentityLookupAdapter:
    return StubIdentityLookupAdapter(
        mappings={
            ("ldap", username): IdentityLookupResult(
                provider="ldap",
                posix_username=username,
                uid=uid,
                primary_gid=10000,
                groups=[],
                user_dn=f"uid={username},ou=people,dc=dms,dc=local",
                source_metadata={},
            )
        }
    )


def _gpfs_adapter_with_identity(repository, executor, *, users, identity_lookup):
    template = GpfsBackendTemplate.from_storage_mapping(
        repository.get_storage_mapping("gpfs-a")
    )
    return GpfsFilesystemBackendAdapter(
        template=template,
        executor=executor,
        identity_groups=StubIdentityGroupManager(users=users),
        identity_lookup=identity_lookup,
    )


def _gpfs_owner_plan(owner_username: str) -> dict[str, Any]:
    return {
        "plan_id": "p-own",
        "request_id": "r-own",
        "resource_key": "gpfs-a:owned-dir",
        "operation_kind": OperationKind.FILESYSTEM_CREATE.value,
        "desired_state": {
            "storage_name": "gpfs-a",
            "directory_name": "owned-dir",
            "users": ["alice", "bob"],
            "owner_username": owner_username,
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }


def test_gpfs_create_sets_owner_when_requester_resolves(repository_pair):
    repository, _ = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    adapter = _gpfs_adapter_with_identity(
        repository,
        executor,
        users={"alice": {}, "bob": {}},
        identity_lookup=_ldap_lookup("alice", 10001),
    )

    adapter.create(_gpfs_owner_plan("alice"))

    chown_cmds = [a for a in executor.commands if a and a[0] == "chown"]
    assert any(
        "10001:24000" in " ".join(a) for a in chown_cmds
    ), "owner uid must be chowned together with the access group gid"


def test_gpfs_create_allows_low_uid_owner_no_uid_restriction(repository_pair):
    # No uid>=1000 restriction: a low uid resolves and is set as owner.
    repository, _ = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    adapter = _gpfs_adapter_with_identity(
        repository,
        executor,
        users={"alice": {}, "bob": {}},
        identity_lookup=_ldap_lookup("svc", 200),
    )

    adapter.create(_gpfs_owner_plan("svc"))

    chown_cmds = [a for a in executor.commands if a and a[0] == "chown"]
    assert any("200:24000" in " ".join(a) for a in chown_cmds)


def test_gpfs_create_fails_closed_when_owner_unresolvable(repository_pair):
    repository, _ = repository_pair
    register_gpfs_mapping(repository)
    executor = FakeGpfsExecutor()
    adapter = _gpfs_adapter_with_identity(
        repository,
        executor,
        users={"alice": {}, "bob": {}},
        identity_lookup=_ldap_lookup("alice", 10001),  # 'ghostuser' not mapped
    )

    with pytest.raises(BackendPreconditionError, match="not a resolvable LDAP user"):
        adapter.create(_gpfs_owner_plan("ghostuser"))

    # Fail-closed: the owner is resolved before any fileset side effect.
    assert not any(
        a and a[0] == "mmcrfileset" for a in executor.commands
    ), "no fileset should be created when the owner cannot be resolved"
