from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from dms.adapters import (  # noqa: E402
    LdapIdentityGroupManager,
    StubFilesystemBackendAdapter,
    StubKubernetesNamespaceQuotaAdapter,
)
from dms.api import create_app  # noqa: E402
from dms.backend_registry import BackendAdapterRegistry  # noqa: E402
from dms.config import Settings  # noqa: E402
from dms.domain import LifecycleState, OperationKind, ResourceKind  # noqa: E402
from dms.planner import Planner  # noqa: E402
from dms.workers import RMWorkerRuntime  # noqa: E402
from scripts import phase10_ceph_host_filesystem_rm as phase10  # noqa: E402
from scripts.phase6_kubernetes_multi_storage_quota import (  # noqa: E402
    assert_equal,
    assert_true,
    mask_url,
)


INITIAL_QUOTA_BYTES = 8 * 1024 * 1024
INCREASED_QUOTA_BYTES = 32 * 1024 * 1024
DRIFT_QUOTA_BYTES = 14 * 1024 * 1024
ASSIGN_QUOTA_BYTES = 16 * 1024 * 1024
FILE_COUNT_QUOTA = 32


@dataclass(frozen=True)
class Phase12Target:
    base: phase10.FilesystemTarget
    directory_name: str
    group_name: str

    @property
    def resource_key(self) -> str:
        return f"{self.base.storage_name}:{self.directory_name}"

    @property
    def directory_path(self) -> str:
        return f"{self.base.managed_root}/{self.directory_name}"


def main() -> int:
    settings = Settings.from_env()
    headers = {"x-dms-actor": "api-client"}
    if settings.auth_shared_token:
        headers["authorization"] = f"Bearer {settings.auth_shared_token}"
    phase10.API_HEADERS.clear()
    phase10.API_HEADERS.update(headers)

    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 12 live verification must use operational PostgreSQL",
    )
    assert_true(settings.observability_is_separate, "observability DB must be separate")

    token = uuid4().hex[:8]
    c1 = phase10.FilesystemTarget(
        storage_name="cephfs-a",
        cluster_name="cluster-a",
        node_name=os.getenv("DMS_PHASE12_C1_NODE", os.getenv("DMS_PHASE10_C1_NODE", "c1-worker")),
        mount_path=os.getenv(
            "DMS_PHASE12_C1_CEPH_MOUNT_PATH",
            os.getenv("DMS_PHASE10_C1_CEPH_MOUNT_PATH", "/mnt/testbed-cephfs"),
        ),
        storage_class_name="testbed-cephfs",
        csi_driver="rook-ceph.cephfs.csi.ceph.com",
    )
    c2 = phase10.FilesystemTarget(
        storage_name="cephfs-b",
        cluster_name="cluster-b",
        node_name=os.getenv("DMS_PHASE12_C2_NODE", os.getenv("DMS_PHASE10_C2_NODE", "c2-worker")),
        mount_path=os.getenv(
            "DMS_PHASE12_C2_CEPH_MOUNT_PATH",
            os.getenv("DMS_PHASE10_C2_CEPH_MOUNT_PATH", "/mnt/testbed-cephfs-c2"),
        ),
    )

    ldap_password = os.environ["DMS_LDAP_BIND_PASSWORD"]
    created_users: list[str] = []
    created_directories: list[tuple[phase10.FilesystemTarget, str]] = []
    created_groups: list[str] = []

    try:
        host_mounts = [phase10.check_host_mount(c1), phase10.check_host_mount(c2)]
        quota_tools = [ensure_quota_tools(c1), ensure_quota_tools(c2)]
        quota_probe = [probe_cephfs_quota(c1, token), probe_cephfs_quota(c2, token)]
        users = phase10.ensure_ldap_users(token=token, ldap_password=ldap_password)
        created_users = users["created_users"]
        phase10.verify_sssd_users(
            [c1.node_name, c2.node_name],
            users["allowed_users"] + [users["denied_user"]],
        )

        app = create_app(settings)
        services = app.state.services
        client = TestClient(app)
        reports = phase10.wait_for_phase10_reports(client, [c1, c2])
        mapping_summaries = [phase10.upsert_mapping(client, c1), phase10.upsert_mapping(client, c2)]
        rm_worker = RMWorkerRuntime(
            repository=services.repository,
            observability=services.observability,
            filesystem_adapter=StubFilesystemBackendAdapter(),
            kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
            worker_id=f"phase12-rm-{token}",
            lease_seconds=settings.worker_lease_seconds,
            backend_registry=BackendAdapterRegistry.with_phase1_defaults(
                services.repository, settings
            ),
        )

        lifecycle_summaries = []
        for base, suffix in [(c1, "a"), (c2, "b")]:
            directory_name = f"phase12-quota-{suffix}-{token}"
            target = Phase12Target(base, directory_name, f"dms-phase12-{directory_name}")
            created_directories.append((base, directory_name))
            lifecycle_summaries.append(
                verify_quota_lifecycle(
                    client=client,
                    repository=services.repository,
                    rm_worker=rm_worker,
                    target=target,
                    allowed_users=users["allowed_users"],
                    denied_user=users["denied_user"],
                    headers=headers,
                )
            )
            created_directories.pop()

        assign_target = Phase12Target(
            c1,
            f"phase12-assign-{token}",
            f"dms-phase12-assign-{token}",
        )
        created_directories.append((c1, assign_target.directory_name))
        assign_summary = verify_assign_quota(
            client=client,
            repository=services.repository,
            rm_worker=rm_worker,
            target=assign_target,
            headers=headers,
        )
        created_directories.pop()

        import_target = Phase12Target(
            c2,
            f"phase12-import-{token}",
            f"dms-phase12-import-{token}",
        )
        created_groups.append(import_target.group_name)
        created_directories.append((c2, import_target.directory_name))
        import_summary = verify_full_import(
            client=client,
            repository=services.repository,
            rm_worker=rm_worker,
            target=import_target,
            allowed_users=users["allowed_users"],
            denied_user=users["denied_user"],
            headers=headers,
            settings=settings,
        )
        created_directories.pop()

        unsafe_summary = verify_unsafe_nested_path_rejected(
            client=client,
            repository=services.repository,
            headers=headers,
            storage_name=c1.storage_name,
        )

        summary = {
            "status": "ok",
            "operational_database_url": mask_url(settings.database_url),
            "observability_database_url": mask_url(settings.observability_database_url),
            "host_mounts": host_mounts,
            "quota_tools": quota_tools,
            "quota_probe": quota_probe,
            "ldap_users": {
                "allowed_users": users["allowed_users"],
                "denied_user": users["denied_user"],
                "created_users": created_users,
            },
            "agent_reports": reports,
            "storage_mappings": mapping_summaries,
            "quota_lifecycle": lifecycle_summaries,
            "assign_quota": assign_summary,
            "full_import": import_summary,
            "unsafe_case": unsafe_summary,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        for target, directory_name in created_directories:
            phase10.cleanup_directory(target, directory_name)
        for group_name in created_groups:
            delete_ldap_group(group_name, ldap_password)
        for username in created_users:
            phase10.delete_ldap_user(username, ldap_password)


def ensure_quota_tools(target: phase10.FilesystemTarget) -> dict:
    before = run_ssh_completed(
        target.node_name,
        "command -v setfattr >/dev/null 2>&1 && command -v getfattr >/dev/null 2>&1",
    )
    installed = False
    if before.returncode != 0:
        phase10.run_ssh(
            target.node_name,
            "sudo DEBIAN_FRONTEND=noninteractive apt-get update >/dev/null && "
            "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y attr",
        )
        installed = True
        note_testbed_install(target)
    setfattr_path = phase10.run_ssh(target.node_name, "command -v setfattr")
    getfattr_path = phase10.run_ssh(target.node_name, "command -v getfattr")
    return {
        "storage_name": target.storage_name,
        "node_name": target.node_name,
        "installed_attr_package": installed,
        "setfattr": setfattr_path,
        "getfattr": getfattr_path,
    }


def note_testbed_install(target: phase10.FilesystemTarget) -> None:
    testbed_dir = Path(os.getenv("DMS_TESTBED_DIR", "/home/mason/workspace/testbed"))
    testbed_dir.mkdir(parents=True, exist_ok=True)
    notes = testbed_dir / "dms-phase12-testbed-notes.md"
    timestamp = datetime.now(UTC).isoformat()
    with notes.open("a", encoding="utf-8") as handle:
        handle.write(
            f"- {timestamp}: installed Debian `attr` package on `{target.node_name}` "
            "for CephFS quota xattr verification (`setfattr`/`getfattr`).\n"
        )


def probe_cephfs_quota(target: phase10.FilesystemTarget, token: str) -> dict:
    probe_dir = f"{target.managed_root}/.phase12-quota-probe-{token}"
    phase10.run_ssh(
        target.node_name,
        "sudo mkdir -p "
        f"{shlex.quote(probe_dir)} && "
        f"sudo setfattr -n ceph.quota.max_bytes -v 1048576 {shlex.quote(probe_dir)} && "
        f"sudo setfattr -n ceph.quota.max_files -v 8 {shlex.quote(probe_dir)}",
    )
    capacity = read_quota_xattr(target, probe_dir, "ceph.quota.max_bytes")
    files = read_quota_xattr(target, probe_dir, "ceph.quota.max_files")
    phase10.run_ssh(target.node_name, f"sudo rm -rf {shlex.quote(probe_dir)}")
    assert_equal(capacity, "1048576", f"{target.storage_name} capacity quota probe")
    assert_equal(files, "8", f"{target.storage_name} file count quota probe")
    return {
        "storage_name": target.storage_name,
        "node_name": target.node_name,
        "supports_capacity_quota": True,
        "supports_file_count_quota": True,
        "quota_backend": "cephfs-xattr",
        "probe_capacity_bytes": int(capacity),
        "probe_file_count": int(files),
    }


def verify_quota_lifecycle(
    *,
    client: TestClient,
    repository,
    rm_worker: RMWorkerRuntime,
    target: Phase12Target,
    allowed_users: list[str],
    denied_user: str,
    headers: dict[str, str],
) -> dict:
    create_id = create_filesystem_with_quota(
        client=client,
        target=target,
        allowed_users=allowed_users,
        denied_user=denied_user,
        headers=headers,
    )
    run_success(repository, rm_worker, create_id, f"{target.base.storage_name} quota create")
    assert_quota_xattrs(target, INITIAL_QUOTA_BYTES, FILE_COUNT_QUOTA)
    assert_allowed_small_write(target, allowed_users[0], "small.bin", megabytes=1)
    assert_denied_access(target, denied_user)
    capacity_failure = assert_write_fails(target, allowed_users[0], "over-capacity.bin", megabytes=16)
    file_count_failure = assert_file_count_fails(target, allowed_users[0])

    update_id = update_quota(
        client=client,
        target=target,
        quota_bytes=INCREASED_QUOTA_BYTES,
        file_count=128,
        headers=headers,
    )
    run_success(repository, rm_worker, update_id, f"{target.base.storage_name} quota increase")
    assert_quota_xattrs(target, INCREASED_QUOTA_BYTES, 128)
    assert_allowed_small_write(
        target, allowed_users[0], "inside-after-increase.bin", megabytes=12
    )

    check_id = check_quota(client=client, target=target, headers=headers)
    run_success(repository, rm_worker, check_id, f"{target.base.storage_name} quota check")
    check_result = repository.get_results(check_id)[0]["verification_summary"]
    assert_equal(
        check_result["quota_status"],
        "Consistent",
        f"{target.base.storage_name} check consistent",
    )

    decrease_id = update_quota(
        client=client,
        target=target,
        quota_bytes=1024 * 1024,
        file_count=128,
        headers=headers,
    )
    run_planner_rejected(
        repository,
        decrease_id,
        f"{target.base.storage_name} decrease below usage rejected",
    )
    assert_quota_xattrs(target, INCREASED_QUOTA_BYTES, 128)

    phase10.run_ssh(
        target.base.node_name,
        f"sudo setfattr -n ceph.quota.max_bytes -v {DRIFT_QUOTA_BYTES} "
        f"{shlex.quote(target.directory_path)}",
    )
    drift_id = check_quota(client=client, target=target, headers=headers)
    run_success(repository, rm_worker, drift_id, f"{target.base.storage_name} drift check")
    action_before_sync = client.get(
        "/api/v1/operations/action-required", headers=headers
    ).json()
    assert_true(
        any(
            issue["issue_type"] == "filesystem_quota_drifted"
            and issue["resource_key"] == target.resource_key
            for issue in action_before_sync
        ),
        f"{target.base.storage_name} quota drift action-required",
    )

    sync_id = sync_quota(client=client, target=target, headers=headers)
    run_success(repository, rm_worker, sync_id, f"{target.base.storage_name} quota sync")
    resource = repository.get_resource(ResourceKind.FILESYSTEM.value, target.resource_key)
    assert_equal(
        resource["desired_state"]["quota"]["capacity_bytes"],
        DRIFT_QUOTA_BYTES,
        f"{target.base.storage_name} sync accepted live quota",
    )
    action_after_sync = client.get(
        "/api/v1/operations/action-required", headers=headers
    ).json()
    assert_true(
        not any(
            issue["issue_type"] == "filesystem_quota_drifted"
            and issue["resource_key"] == target.resource_key
            for issue in action_after_sync
        ),
        f"{target.base.storage_name} drift resolved after sync",
    )

    warning_id = check_quota(
        client=client,
        target=target,
        headers=headers,
        warning_percent=70,
        critical_percent=98,
    )
    run_success(repository, rm_worker, warning_id, f"{target.base.storage_name} usage warning")
    action_usage = client.get("/api/v1/operations/action-required", headers=headers).json()
    assert_true(
        any(
            issue["issue_type"] == "filesystem_quota_usage_warning"
            and issue["resource_key"] == target.resource_key
            for issue in action_usage
        ),
        f"{target.base.storage_name} usage warning action-required",
    )

    cleanup_data_files(target)
    delete_id = delete_filesystem(client=client, target=target, headers=headers)
    run_success(repository, rm_worker, delete_id, f"{target.base.storage_name} quota delete")
    phase10.run_ssh(target.base.node_name, f"test ! -e {shlex.quote(target.directory_path)}")
    return {
        "storage_name": target.base.storage_name,
        "cluster_name": target.base.cluster_name,
        "node_name": target.base.node_name,
        "directory_name": target.directory_name,
        "create_request_id": create_id,
        "update_request_id": update_id,
        "check_request_id": check_id,
        "decrease_request_id": decrease_id,
        "drift_check_request_id": drift_id,
        "sync_request_id": sync_id,
        "usage_warning_request_id": warning_id,
        "delete_request_id": delete_id,
        "capacity_failure": capacity_failure,
        "file_count_failure": file_count_failure,
        "synced_capacity_bytes": DRIFT_QUOTA_BYTES,
    }


def verify_assign_quota(
    *,
    client: TestClient,
    repository,
    rm_worker: RMWorkerRuntime,
    target: Phase12Target,
    headers: dict[str, str],
) -> dict:
    phase10.run_ssh(
        target.base.node_name,
        f"sudo rm -rf {shlex.quote(target.directory_path)} && "
        f"sudo mkdir -p {shlex.quote(target.directory_path)} && "
        f"sudo chmod 0777 {shlex.quote(target.directory_path)}",
    )
    assign_id = keyed_request(
        client,
        target,
        OperationKind.FILESYSTEM_ASSIGN_QUOTA,
        {
            "management_mode": "quota_only",
            "initialize_marker": True,
            "quota": {"capacity_bytes": ASSIGN_QUOTA_BYTES, "file_count": 64},
            "reason": "phase12 assign quota to existing directory",
        },
        headers,
    )
    run_success(repository, rm_worker, assign_id, f"{target.base.storage_name} assign quota")
    assert_quota_xattrs(target, ASSIGN_QUOTA_BYTES, 64)
    marker = read_marker(target)
    assert_equal(marker["management_mode"], "quota_only", "quota-only marker")

    check_id = check_quota(client=client, target=target, headers=headers)
    run_success(repository, rm_worker, check_id, f"{target.base.storage_name} assign check")

    delete_id = delete_filesystem(client=client, target=target, headers=headers)
    run_planner_rejected(repository, delete_id, "quota-only delete rejected")
    phase10.run_ssh(target.base.node_name, f"test -d {shlex.quote(target.directory_path)}")
    phase10.cleanup_directory(target.base, target.directory_name)
    return {
        "storage_name": target.base.storage_name,
        "directory_name": target.directory_name,
        "assign_request_id": assign_id,
        "check_request_id": check_id,
        "delete_rejected_request_id": delete_id,
        "marker_management_mode": marker["management_mode"],
    }


def verify_full_import(
    *,
    client: TestClient,
    repository,
    rm_worker: RMWorkerRuntime,
    target: Phase12Target,
    allowed_users: list[str],
    denied_user: str,
    headers: dict[str, str],
    settings: Settings,
) -> dict:
    manager = LdapIdentityGroupManager.from_settings(settings)
    group = manager.ensure_group_members(
        group_name=target.group_name,
        users=allowed_users,
        resource_key=target.resource_key,
    )
    refresh_sssd_cache(target.base.node_name)
    phase10.wait_ssh(target.base.node_name, f"getent group {shlex.quote(target.group_name)}")
    for user in allowed_users:
        phase10.wait_ssh(
            target.base.node_name,
            f"getent group {shlex.quote(target.group_name)} | grep -w {shlex.quote(user)}",
            timeout_seconds=120,
        )
    phase10.run_ssh(
        target.base.node_name,
        f"sudo rm -rf {shlex.quote(target.directory_path)} && "
        f"sudo mkdir -p {shlex.quote(target.directory_path)} && "
        f"sudo chgrp {shlex.quote(target.group_name)} {shlex.quote(target.directory_path)} && "
        f"sudo chmod 0770 {shlex.quote(target.directory_path)}",
    )
    import_id = keyed_request(
        client,
        target,
        OperationKind.FILESYSTEM_IMPORT,
        {
            "import_mode": "full",
            "initialize_marker": True,
            "access_policy": {
                "mode": "adopt_existing_group",
                "expected_group": target.group_name,
                "expected_mode": "0770",
                "users": allowed_users,
                "denied_users": [denied_user],
            },
            "quota": {"capacity_bytes": ASSIGN_QUOTA_BYTES, "file_count": 64},
            "preserve_existing_data": True,
            "reason": "phase12 import existing directory",
        },
        headers,
    )
    run_success(repository, rm_worker, import_id, f"{target.base.storage_name} full import")
    assert_allowed_small_write(target, allowed_users[0], "import-write.bin", megabytes=1)
    assert_denied_access(target, denied_user)
    assert_quota_xattrs(target, ASSIGN_QUOTA_BYTES, 64)

    update_id = update_quota(
        client=client,
        target=target,
        quota_bytes=INCREASED_QUOTA_BYTES,
        file_count=128,
        headers=headers,
    )
    run_success(repository, rm_worker, update_id, f"{target.base.storage_name} import quota update")
    assert_quota_xattrs(target, INCREASED_QUOTA_BYTES, 128)
    cleanup_data_files(target)
    delete_id = delete_filesystem(client=client, target=target, headers=headers)
    run_success(repository, rm_worker, delete_id, f"{target.base.storage_name} import delete")
    phase10.run_ssh(target.base.node_name, f"test ! -e {shlex.quote(target.directory_path)}")
    return {
        "storage_name": target.base.storage_name,
        "directory_name": target.directory_name,
        "group_name": target.group_name,
        "group_gid": group["gid"],
        "import_request_id": import_id,
        "quota_update_request_id": update_id,
        "delete_request_id": delete_id,
    }


def verify_unsafe_nested_path_rejected(
    *,
    client: TestClient,
    repository,
    headers: dict[str, str],
    storage_name: str,
) -> dict:
    response = client.post(
        "/api/v1/resource-management/requests",
        headers=headers,
        json={
            "requester_id": "portal:phase12",
            "operation": OperationKind.FILESYSTEM_IMPORT.value,
            "resource_kind": ResourceKind.FILESYSTEM.value,
            "resource_key": f"{storage_name}:unsafe/nested",
            "payload": {
                "storage_name": storage_name,
                "directory_name": "unsafe/nested",
                "import_mode": "full",
            },
        },
    )
    assert_equal(response.status_code, 202, "unsafe nested path request accepted")
    request_id = response.json()["request_id"]
    run_planner_rejected(repository, request_id, "unsafe nested path rejected")
    [result] = repository.get_results(request_id)
    reasons = {issue["reason"] for issue in result["verification_summary"]["issues"]}
    assert_true("directory_name_invalid" in reasons, "unsafe nested path reason")
    return {"request_id": request_id, "reasons": sorted(reasons)}


def create_filesystem_with_quota(
    *,
    client: TestClient,
    target: Phase12Target,
    allowed_users: list[str],
    denied_user: str,
    headers: dict[str, str],
) -> str:
    response = client.post(
        "/api/v1/resource-management/filesystems",
        headers=headers,
        json={
            "requester_id": "portal:phase12",
            "payload": {
                "storage_name": target.base.storage_name,
                "directory_name": target.directory_name,
                "resource_type": "user",
                "users": allowed_users,
                "access_group": target.group_name,
                "validation_denied_users": [denied_user],
                "quota": {
                    "capacity_bytes": INITIAL_QUOTA_BYTES,
                    "file_count": FILE_COUNT_QUOTA,
                },
                "expires_at": "2026-07-01T00:00:00Z",
            },
        },
    )
    assert_equal(response.status_code, 202, f"{target.base.storage_name} quota create request")
    return response.json()["request_id"]


def update_quota(
    *,
    client: TestClient,
    target: Phase12Target,
    quota_bytes: int,
    file_count: int,
    headers: dict[str, str],
) -> str:
    return keyed_request(
        client,
        target,
        OperationKind.FILESYSTEM_UPDATE,
        {
            "quota": {"capacity_bytes": quota_bytes, "file_count": file_count},
            "reason": "phase12 quota update",
        },
        headers,
        method="PATCH",
    )


def check_quota(
    *,
    client: TestClient,
    target: Phase12Target,
    headers: dict[str, str],
    warning_percent: int = 80,
    critical_percent: int = 95,
) -> str:
    return keyed_request(
        client,
        target,
        OperationKind.FILESYSTEM_CHECK,
        {
            "include_usage": True,
            "include_quota": True,
            "include_permission": True,
            "usage_thresholds": {
                "warning_percent": warning_percent,
                "critical_percent": critical_percent,
            },
            "record_action_required": True,
        },
        headers,
    )


def sync_quota(*, client: TestClient, target: Phase12Target, headers: dict[str, str]) -> str:
    return keyed_request(
        client,
        target,
        OperationKind.FILESYSTEM_SYNC,
        {"source": "live", "include_quota": True, "include_usage": True},
        headers,
    )


def delete_filesystem(
    *, client: TestClient, target: Phase12Target, headers: dict[str, str]
) -> str:
    return keyed_request(
        client,
        target,
        OperationKind.FILESYSTEM_DELETE,
        {"reason": "phase12 cleanup"},
        headers,
        method="DELETE",
    )


def keyed_request(
    client: TestClient,
    target: Phase12Target,
    operation: OperationKind,
    payload: dict,
    headers: dict[str, str],
    *,
    method: str = "POST",
) -> str:
    suffix = {
        OperationKind.FILESYSTEM_ASSIGN_QUOTA: ":assign-quota",
        OperationKind.FILESYSTEM_IMPORT: ":import",
        OperationKind.FILESYSTEM_CHECK: ":check",
        OperationKind.FILESYSTEM_SYNC: ":sync",
        OperationKind.FILESYSTEM_UPDATE: "",
        OperationKind.FILESYSTEM_DELETE: "",
    }[operation]
    url = (
        f"/api/v1/resource-management/filesystems/"
        f"{target.base.storage_name}/{target.directory_name}{suffix}"
    )
    response = client.request(
        method,
        url,
        headers=headers,
        json={"requester_id": "portal:phase12", "payload": payload},
    )
    assert_equal(response.status_code, 202, f"{target.base.storage_name} {operation.value}")
    return response.json()["request_id"]


def run_success(repository, rm_worker: RMWorkerRuntime, request_id: str, label: str) -> None:
    assert_equal(Planner(repository).run_once(), 1, f"{label} planner")
    assert_true(repository.get_plan_by_request(request_id) is not None, f"{label} plan")
    assert_equal(rm_worker.run_once(), 1, f"{label} worker")
    assert_equal(
        repository.get_request(request_id)["status"],
        LifecycleState.SUCCEEDED.value,
        f"{label} request succeeded",
    )


def run_planner_rejected(repository, request_id: str, label: str) -> None:
    assert_equal(Planner(repository).run_once(), 1, f"{label} planner")
    assert_equal(
        repository.get_request(request_id)["status"],
        LifecycleState.REJECTED.value,
        f"{label} rejected",
    )


def assert_quota_xattrs(target: Phase12Target, capacity_bytes: int, file_count: int) -> None:
    capacity = read_quota_xattr(target.base, target.directory_path, "ceph.quota.max_bytes")
    files = read_quota_xattr(target.base, target.directory_path, "ceph.quota.max_files")
    assert_equal(capacity, str(capacity_bytes), f"{target.resource_key} quota bytes")
    assert_equal(files, str(file_count), f"{target.resource_key} quota files")


def read_quota_xattr(
    target: phase10.FilesystemTarget, path: str, name: str
) -> str:
    return phase10.run_ssh(
        target.node_name,
        f"sudo getfattr --only-values -n {shlex.quote(name)} {shlex.quote(path)}",
    ).strip()


def read_marker(target: Phase12Target) -> dict:
    marker_json = phase10.run_ssh(
        target.base.node_name,
        f"sudo cat {shlex.quote(target.directory_path + '/.dms-resource.json')}",
    )
    return json.loads(marker_json)


def assert_allowed_small_write(
    target: Phase12Target, user: str, filename: str, *, megabytes: int
) -> None:
    path = f"{target.directory_path}/{filename}"
    phase10.run_ssh(
        target.base.node_name,
        "sudo -u "
        f"{shlex.quote(user)} sh -c 'dd if=/dev/zero of=\"$1\" bs=1M "
        f"count={megabytes} status=none' sh {shlex.quote(path)}",
    )


def assert_write_fails(
    target: Phase12Target, user: str, filename: str, *, megabytes: int
) -> dict:
    path = f"{target.directory_path}/{filename}"
    completed = run_ssh_completed(
        target.base.node_name,
        "sudo -u "
        f"{shlex.quote(user)} sh -c 'dd if=/dev/zero of=\"$1\" bs=1M "
        f"count={megabytes} status=none' sh {shlex.quote(path)}",
    )
    assert_true(completed.returncode != 0, f"{target.resource_key} over-quota write failed")
    phase10.run_ssh(target.base.node_name, f"sudo rm -f {shlex.quote(path)}", check=False)
    return {"returncode": completed.returncode, "stderr": completed.stderr.strip()[-300:]}


def assert_file_count_fails(target: Phase12Target, user: str) -> dict:
    script = (
        'directory="$1"; i=0; while [ "$i" -lt 64 ]; do '
        ': > "$directory/file-$i" || exit 9; i=$((i+1)); done'
    )
    completed = run_ssh_completed(
        target.base.node_name,
        "sudo -u "
        f"{shlex.quote(user)} bash -lc {shlex.quote(script)} bash "
        f"{shlex.quote(target.directory_path)}",
    )
    assert_true(completed.returncode != 0, f"{target.resource_key} file-count quota failed")
    failure_text = f"{completed.stderr}\n{completed.stdout}".lower()
    assert_true(
        "quota" in failure_text,
        f"{target.resource_key} file-count failure mentions quota",
    )
    phase10.run_ssh(
        target.base.node_name,
        f"sudo rm -f {shlex.quote(target.directory_path)}/file-*",
        check=False,
    )
    return {"returncode": completed.returncode, "stderr": completed.stderr.strip()[-300:]}


def assert_denied_access(target: Phase12Target, denied_user: str) -> None:
    phase10.run_ssh(
        target.base.node_name,
        "sudo -u "
        f"{shlex.quote(denied_user)} sh -c 'test ! -x \"$1\" && test ! -w \"$1\"' sh "
        f"{shlex.quote(target.directory_path)}",
    )


def cleanup_data_files(target: Phase12Target) -> None:
    phase10.run_ssh(
        target.base.node_name,
        f"sudo find {shlex.quote(target.directory_path)} -maxdepth 1 -type f "
        "! -name '.dms-resource.json' -delete",
        check=False,
    )


def delete_ldap_group(group_name: str, ldap_password: str) -> None:
    subprocess.run(
        [
            "ssh",
            "ldap",
            "ldapdelete",
            "-x",
            "-H",
            "ldap://127.0.0.1",
            "-D",
            phase10.LDAP_BIND_DN,
            "-w",
            ldap_password,
            f"cn={group_name},{phase10.LDAP_GROUPS_DN}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def refresh_sssd_cache(host: str) -> None:
    phase10.run_ssh(
        host,
        "if command -v sss_cache >/dev/null 2>&1; then sudo sss_cache -E; "
        "elif [ -x /usr/sbin/sss_cache ]; then sudo /usr/sbin/sss_cache -E; "
        "else true; fi",
        check=False,
    )


def run_ssh_completed(host: str, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["ssh", host, command],
        check=False,
        capture_output=True,
        text=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
