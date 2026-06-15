from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from dms.adapters import (
    BackendPreconditionError,
    IdentityLookupResult,
    StubIdentityLookupAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.backend_registry import BackendAdapterRegistry
from dms.backends.weka import (
    WEKAFS_BACKEND_TYPE,
    CommandResult,
    WekaFsBackendTemplate,
    WekaFsCommandExecutor,
    WekaFsHostMountedFilesystemBackendAdapter,
    WekaFsQuotaStrategy,
)
from dms.config import Settings
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


def weka_mapping() -> StorageMappingInput:
    return StorageMappingInput(
        storage_name="weka-a",
        backend_template={
            "backend_type": WEKAFS_BACKEND_TYPE,
            "filesystem_name": "pvs_weka",
            "mount_path": "/pvs_weka",
            "managed_root": "/pvs_weka/dms",
            "csi_driver": "csi.weka.io",
            "rm_worker_nodes": ["weka-rm"],
            "ssh_host": "weka-rm",
            "weka_credentials": {
                "organization": "0",
                "username": "admin",
                "password": "Passw0rd!",
            },
        },
        cluster_name="cluster-a",
        storage_class_name=None,
        sanity_status="Ready",
    )


def weka_ready_sanity() -> dict:
    return {
        "storage_name": "weka-a",
        "status": "Ready",
        "checked_at": "2026-06-08T00:00:00+00:00",
        "kubernetes_observed": {
            "cluster_name": "cluster-a",
            "storage_class_name": None,
            "storage_class_exists": False,
            "provisioner": None,
        },
        "agent_observed": {
            "fresh_reports": 1,
            "stale_reports": 0,
            "rm_readiness": "Ready",
            "dm_readiness": "Ready",
            "rm_candidates": [
                {"cluster_name": "cluster-a", "node_name": "weka-rm", "status": "Ready"}
            ],
            "dm_candidates": [{"cluster_name": "cluster-a", "node_name": "weka-rm"}],
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


def register_weka_mapping(repository: DmsRepository) -> None:
    sanity = weka_ready_sanity()
    repository.upsert_storage_mapping(
        weka_mapping(),
        actor="admin",
        sanity_result=sanity,
        readiness=sanity["readiness"],
    )


@dataclass
class FakeIdentityGroupManager:
    deletions: list[str] = field(default_factory=list)

    def ensure_group_members(self, *, group_name, users, resource_key):
        return {
            "dn": f"cn={group_name},ou=groups,dc=example,dc=test",
            "gid": 24000,
            "members": users,
            "identity_source": "openldap-sssd",
        }

    def delete_group(self, *, group_name):
        self.deletions.append(group_name)
        return {"deleted": True, "group_name": group_name}

    def list_group_members(self, *, group_name):
        return []

    def lookup_group_gid(self, *, group_name):
        return 24000

    def lookup_group_name_by_gid(self, *, gid):
        return None


@dataclass
class FakeWekaExecutor:
    """In-memory WEKA CLI fake.

    Tracks created directories, applied quotas, and simulates capability/auth probes.
    """

    missing: set[str] = field(default_factory=set)
    directories: set[str] = field(default_factory=set)
    quotas: dict[str, dict[str, int]] = field(
        default_factory=dict
    )  # path -> {soft, hard}
    commands: list[list[str]] = field(default_factory=list)
    auth_failed: bool = False
    quota_readback_capacity_delta: int = 0
    last_env: dict[str, str] | None = None

    def run(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        self.commands.append(argv)
        if env is not None:
            self.last_env = env
        # capability probe: sh -c "command -v <cmd>"
        if argv[:2] == ["sh", "-c"]:
            command = argv[2].split()[-1]
            if command in self.missing:
                return _result(argv, 1, stdout="")
            return _result(argv, 0, stdout=f"/usr/bin/{command}\n")
        cmd = argv[0]
        if cmd in self.missing:
            return _result(argv, 127, stderr=f"{cmd}: not found")
        if cmd == "weka":
            return self._weka(argv)
        if cmd == "mkdir":
            self.directories.add(argv[-1])
            return _result(argv, 0)
        if cmd == "rm":
            target = argv[-1]
            self.directories.discard(target)
            return _result(argv, 0)
        if cmd == "test":
            target = argv[-1]
            return _result(argv, 0 if target in self.directories else 1)
        if cmd in {"chgrp", "chmod", "python3"}:
            return _result(argv, 0)
        return _result(argv, 0)

    def _weka(self, argv: list[str]) -> CommandResult:
        # weka fs quota set <path> --soft N --hard M
        # weka fs quota list <fs> --path <p> --format json --all --raw-units
        # weka fs quota reset <path>
        if argv[1:4] == ["fs", "quota", "set"]:
            if self.auth_failed:
                return _result(argv, 1, stderr="error: Authentication Failed: ")
            path = argv[4]
            soft = int(argv[argv.index("--soft") + 1]) if "--soft" in argv else 0
            hard = int(argv[argv.index("--hard") + 1]) if "--hard" in argv else 0
            self.quotas[path] = {"soft": soft, "hard": hard}
            return _result(argv, 0)
        if argv[1:4] == ["fs", "quota", "list"]:
            if self.auth_failed:
                return _result(argv, 1, stderr="error: Authentication Failed: ")
            # extract --path if present
            path = argv[argv.index("--path") + 1] if "--path" in argv else None
            rows = []
            for p, q in self.quotas.items():
                if path and p != path:
                    continue
                rows.append(
                    {
                        "path": p,
                        "filesystem": argv[4],
                        "soft_limit": q["soft"],
                        "hard_limit": q["hard"] + self.quota_readback_capacity_delta,
                        "used": 0,
                    }
                )
            return _result(argv, 0, stdout=json.dumps(rows))
        if argv[1:4] == ["fs", "quota", "reset"]:
            path = argv[4]
            self.quotas.pop(path, None)
            return _result(argv, 0)
        return _result(argv, 0)


def _result(
    argv: list[str], rc: int, *, stdout: str = "", stderr: str = ""
) -> CommandResult:
    return CommandResult(argv=argv, returncode=rc, stdout=stdout, stderr=stderr)


def weka_worker(
    repository: DmsRepository,
    observability: ObservabilityRepository,
    executor: WekaFsCommandExecutor,
    identity: FakeIdentityGroupManager | None = None,
) -> RMWorkerRuntime:
    template = WekaFsBackendTemplate.from_storage_mapping(
        repository.get_storage_mapping("weka-a")
    )
    return RMWorkerRuntime(
        repository=repository,
        observability=observability,
        filesystem_adapter=WekaFsHostMountedFilesystemBackendAdapter(
            template=template,
            identity_groups=identity or FakeIdentityGroupManager(),
            executor=executor,
        ),
        kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
        worker_id="rm-weka",
    )


def seed_weka_resource(
    repository: DmsRepository,
    *,
    directory_name: str,
    quota: dict[str, int],
    management_mode: str = "full",
) -> None:
    desired = {
        "storage_name": "weka-a",
        "directory_name": directory_name,
        "access_group": f"dms-grp-{directory_name}",
        "mode": "0770",
        "resource_kind": ResourceKind.FILESYSTEM.value,
        "resource_key": f"weka-a:{directory_name}",
        "management_mode": management_mode,
        "quota": quota,
        "expires_at": "2099-01-01T00:00:00Z",
    }
    observed = {
        "adapter": "wekafs-host-mounted",
        "path": f"/pvs_weka/dms/{directory_name}",
        "quota_state": {
            "backend_type": WEKAFS_BACKEND_TYPE,
            "path": f"/pvs_weka/dms/{directory_name}",
            "capacity": {"observed_bytes": quota["capacity_bytes"]},
        },
        "management_mode": management_mode,
    }
    repository.upsert_resource(
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key=f"weka-a:{directory_name}",
        desired_state=desired,
        applied_state={"quota": quota, "quota_state": observed["quota_state"]},
        observed_state=observed,
        status=LifecycleState.SUCCEEDED.value,
    )


# ---------- pure unit tests ----------


def test_weka_quota_strategy_renders_capacity_only():
    rendered = WekaFsQuotaStrategy().render_quota({"capacity_bytes": 10**9})

    assert rendered["backend_type"] == WEKAFS_BACKEND_TYPE
    assert rendered["hard_limit_bytes"] == 10**9
    assert rendered["soft_limit_bytes"] == 10**9
    assert rendered["side_effect"] == "weka-command"


def test_weka_template_metadata_round_trip():
    mapping = {
        "storage_name": "weka-a",
        "cluster_name": "cluster-a",
        "backend_template": weka_mapping().backend_template,
        "sanity_result": weka_ready_sanity(),
    }
    template = WekaFsBackendTemplate.from_storage_mapping(mapping)
    assert template.filesystem_name == "pvs_weka"
    assert template.mount_path == "/pvs_weka"
    assert template.managed_root == "/pvs_weka/dms"
    assert template.rm_worker_node == "weka-rm"
    md = template.metadata()
    assert md["backend_type"] == WEKAFS_BACKEND_TYPE
    assert md["filesystem_name"] == "pvs_weka"


# ---------- lifecycle tests ----------


def test_weka_filesystem_create_uses_weka_quota_set(repository_pair):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:project-alpha",
        payload={
            "storage_name": "weka-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 200 * 1024**2},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    executor = FakeWekaExecutor()
    worker = weka_worker(repository, observability, executor)

    assert worker.run_once() == 1
    [resource] = repository.list_resources()
    argv = [entry["argv"] for entry in resource["observed_state"]["command_evidence"]]
    assert (
        repository.get_request(request_id)["status"] == LifecycleState.SUCCEEDED.value
    )
    assert resource["observed_state"]["adapter"] == "wekafs-host-mounted"
    # mkdir
    assert ["mkdir", "-p", "/pvs_weka/dms/project-alpha"] in argv
    # quota set with --soft / --hard
    quota_set = next(a for a in argv if a[:4] == ["weka", "fs", "quota", "set"])
    assert quota_set[4] == "/pvs_weka/dms/project-alpha"
    assert "--hard" in quota_set
    assert quota_set[quota_set.index("--hard") + 1] == str(200 * 1024**2)
    # observed quota state
    assert (
        resource["observed_state"]["quota_state"]["capacity"]["observed_bytes"]
        == 200 * 1024**2
    )


def test_weka_create_rejects_file_count_quota(repository_pair):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:project-alpha",
        payload={
            "storage_name": "weka-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 200 * 1024**2, "file_count": 100},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    worker = weka_worker(repository, observability, FakeWekaExecutor())

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    assert "file_count" in result["message"]


def test_weka_create_fails_closed_when_unauthenticated(repository_pair):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:project-alpha",
        payload={
            "storage_name": "weka-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 200 * 1024**2},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    worker = weka_worker(repository, observability, FakeWekaExecutor(auth_failed=True))

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    assert (
        "not authenticated" in result["message"].lower()
        or "Authentication Failed" in result["message"]
    )
    # capability probe before any side effect
    assert result["verification_summary"]["backend_side_effect"] is False


def test_weka_missing_command_fails_closed(repository_pair):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:project-alpha",
        payload={
            "storage_name": "weka-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 200 * 1024**2},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    worker = weka_worker(repository, observability, FakeWekaExecutor(missing={"weka"}))

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.BACKEND_APPLY_FAILED.value
    assert "WekaFS command missing: weka" in result["message"]


def test_weka_check_reports_drift(repository_pair):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    executor = FakeWekaExecutor()
    target = "/pvs_weka/dms/project-alpha"
    executor.directories.add(target)
    # desired 200 MiB; live 500 MiB ⇒ 300 MiB diff exceeds 100 MiB tolerance.
    executor.quotas[target] = {"soft": 100 * 1024**2, "hard": 500 * 1024**2}
    seed_weka_resource(
        repository,
        directory_name="project-alpha",
        quota={"capacity_bytes": 200 * 1024**2},
    )
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CHECK.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:project-alpha",
        payload={"storage_name": "weka-a", "directory_name": "project-alpha"},
    )
    Planner(repository).run_once()
    worker = weka_worker(repository, observability, executor)

    assert worker.run_once() == 1
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.SUCCEEDED.value
    assert result["verification_summary"]["quota_status"] == "Drifted"


def test_weka_sync_picks_up_live_quota(repository_pair):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    executor = FakeWekaExecutor()
    target = "/pvs_weka/dms/project-alpha"
    executor.directories.add(target)
    executor.quotas[target] = {"soft": 250 * 1024**2, "hard": 500 * 1024**2}
    seed_weka_resource(
        repository,
        directory_name="project-alpha",
        quota={"capacity_bytes": 200 * 1024**2},
    )
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_SYNC.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:project-alpha",
        payload={"storage_name": "weka-a", "directory_name": "project-alpha"},
    )
    Planner(repository).run_once()
    worker = weka_worker(repository, observability, executor)

    assert worker.run_once() == 1
    resource = repository.get_resource(
        ResourceKind.FILESYSTEM.value, "weka-a:project-alpha"
    )
    assert resource["desired_state"]["quota"] == {"capacity_bytes": 500 * 1024**2}


def test_weka_delete_soft_locks_directory(repository_pair):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    executor = FakeWekaExecutor()
    target = "/pvs_weka/dms/project-alpha"
    executor.directories.add(target)
    executor.quotas[target] = {"soft": 200 * 1024**2, "hard": 200 * 1024**2}
    identity = FakeIdentityGroupManager()
    seed_weka_resource(
        repository,
        directory_name="project-alpha",
        quota={"capacity_bytes": 200 * 1024**2},
    )
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_DELETE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:project-alpha",
        payload={"storage_name": "weka-a", "directory_name": "project-alpha"},
    )
    Planner(repository).run_once()
    worker = weka_worker(repository, observability, executor, identity=identity)

    assert worker.run_once() == 1
    argv = executor.commands
    # Soft-delete: quota reset first, then lock down (chown root:root -> chmod 000),
    # then free the LDAP group. No rm -rf; directory + data preserved.
    quota_reset = [a for a in argv if a[:4] == ["weka", "fs", "quota", "reset"]]
    assert quota_reset and quota_reset[0][4] == target
    assert ["chown", "root:root", target] in argv
    assert ["chmod", "000", target] in argv
    assert not any(a[:2] == ["rm", "-rf"] for a in argv)
    assert target in executor.directories
    assert "dms-grp-project-alpha" in identity.deletions
    # Order: quota reset -> chown -> chmod.
    reset_idx = next(
        i for i, a in enumerate(argv) if a[:4] == ["weka", "fs", "quota", "reset"]
    )
    chown_idx = argv.index(["chown", "root:root", target])
    chmod_idx = argv.index(["chmod", "000", target])
    assert reset_idx < chown_idx < chmod_idx


def test_weka_delete_refuses_quota_only_resource(repository_pair):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    seed_weka_resource(
        repository,
        directory_name="quota-only",
        quota={"capacity_bytes": 200 * 1024**2},
        management_mode="quota_only",
    )
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_DELETE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:quota-only",
        payload={"storage_name": "weka-a", "directory_name": "quota-only"},
    )
    Planner(repository).run_once()
    assert repository.get_request(request_id)["status"] == LifecycleState.REJECTED.value


def test_weka_quota_readback_mismatch_records_unknown_after_side_effect(
    repository_pair,
):
    repository, observability = repository_pair
    register_weka_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.FILESYSTEM_CREATE.value,
        resource_kind=ResourceKind.FILESYSTEM.value,
        resource_key="weka-a:project-alpha",
        payload={
            "storage_name": "weka-a",
            "directory_name": "project-alpha",
            "users": ["alice", "bob"],
            "quota": {"capacity_bytes": 200 * 1024**2},
            "expires_at": "2099-01-01T00:00:00Z",
        },
    )
    Planner(repository).run_once()
    # 200 MiB delta exceeds the 100 MiB tolerance and must trigger mismatch.
    executor = FakeWekaExecutor(quota_readback_capacity_delta=200 * 1024 * 1024)
    worker = weka_worker(repository, observability, executor)

    with pytest.raises(RuntimeError, match="WekaFS quota capacity read-back mismatch"):
        worker.run_once()
    [result] = repository.get_results(request_id)
    assert result["terminal_status"] == LifecycleState.UNKNOWN_AFTER_SIDE_EFFECT.value


def test_weka_data_management_planning_records_weka_worker_pool(repository_pair):
    repository, _ = repository_pair
    register_weka_mapping(repository)
    request_id = repository.create_request(
        requester_id="alice",
        actor="api-client",
        operation=OperationKind.DATA_SCAN.value,
        resource_kind=ResourceKind.DATA_JOB.value,
        resource_key="weka-a:data.scan:project-alpha",
        payload={
            "storage_name": "weka-a",
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
    assert job["worker_pool"]["backend_type"] == WEKAFS_BACKEND_TYPE
    assert job["worker_pool"]["required_mounts"] == ["weka-a"]
    assert job["worker_pool"]["mount_path"] == "/pvs_weka"
    assert "dscan" in job["worker_pool"]["tool_candidates"]


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


def _weka_adapter_with_identity(repository, executor, *, identity_lookup):
    template = WekaFsBackendTemplate.from_storage_mapping(
        repository.get_storage_mapping("weka-a")
    )
    return WekaFsHostMountedFilesystemBackendAdapter(
        template=template,
        identity_groups=FakeIdentityGroupManager(),
        executor=executor,
        identity_lookup=identity_lookup,
    )


def _weka_owner_plan(owner_username: str) -> dict[str, Any]:
    return {
        "plan_id": "p-own",
        "request_id": "r-own",
        "resource_key": "weka-a:owned-dir",
        "operation_kind": OperationKind.FILESYSTEM_CREATE.value,
        "desired_state": {
            "storage_name": "weka-a",
            "directory_name": "owned-dir",
            "users": ["alice", "bob"],
            "owner_username": owner_username,
            "mode": "0770",
            "expires_at": "2099-01-01T00:00:00Z",
        },
    }


def test_weka_create_sets_owner_when_requester_resolves(repository_pair):
    repository, _ = repository_pair
    register_weka_mapping(repository)
    executor = FakeWekaExecutor()
    adapter = _weka_adapter_with_identity(
        repository, executor, identity_lookup=_ldap_lookup("alice", 10001)
    )

    adapter.create(_weka_owner_plan("alice"))

    chown_cmds = [a for a in executor.commands if a and a[0] == "chown"]
    assert any(
        a[1].startswith("10001:") for a in chown_cmds
    ), "owner uid must be chowned together with the access group gid"


def test_weka_create_allows_low_uid_owner_no_uid_restriction(repository_pair):
    repository, _ = repository_pair
    register_weka_mapping(repository)
    executor = FakeWekaExecutor()
    adapter = _weka_adapter_with_identity(
        repository, executor, identity_lookup=_ldap_lookup("svc", 200)
    )

    adapter.create(_weka_owner_plan("svc"))

    chown_cmds = [a for a in executor.commands if a and a[0] == "chown"]
    assert any(a[1].startswith("200:") for a in chown_cmds)


def test_weka_create_fails_closed_when_owner_unresolvable(repository_pair):
    repository, _ = repository_pair
    register_weka_mapping(repository)
    executor = FakeWekaExecutor()
    adapter = _weka_adapter_with_identity(
        repository, executor, identity_lookup=_ldap_lookup("alice", 10001)
    )

    with pytest.raises(BackendPreconditionError, match="not a resolvable LDAP user"):
        adapter.create(_weka_owner_plan("ghostuser"))

    assert not any(
        a and a[0] == "mkdir" for a in executor.commands
    ), "no directory should be created when the owner cannot be resolved"
