from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT))

from dms.adapters import StubFilesystemBackendAdapter, StubKubernetesNamespaceQuotaAdapter  # noqa: E402
from dms.api import create_app  # noqa: E402
from dms.backend_registry import BackendAdapterRegistry  # noqa: E402
from dms.config import Settings  # noqa: E402
from dms.workers import RMWorkerRuntime  # noqa: E402
from scripts import phase10_ceph_host_filesystem_rm as phase10  # noqa: E402
from scripts.phase6_kubernetes_multi_storage_quota import (  # noqa: E402
    assert_equal,
    assert_true,
    mask_url,
)


EXPIRED_AT = "2000-01-01T00:00:00Z"


@dataclass(frozen=True)
class Phase11Target:
    base: phase10.FilesystemTarget
    directory_name: str
    group_name: str

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
        "Phase 11 live verification must use operational PostgreSQL",
    )
    assert_true(settings.observability_is_separate, "observability DB must be separate")

    token = uuid4().hex[:8]
    c1 = phase10.FilesystemTarget(
        storage_name="cephfs-a",
        cluster_name="cluster-a",
        node_name=os.getenv("DMS_PHASE11_C1_NODE", os.getenv("DMS_PHASE10_C1_NODE", "c1-worker")),
        mount_path=os.getenv(
            "DMS_PHASE11_C1_CEPH_MOUNT_PATH",
            os.getenv("DMS_PHASE10_C1_CEPH_MOUNT_PATH", "/mnt/testbed-cephfs"),
        ),
        storage_class_name="testbed-cephfs",
        csi_driver="rook-ceph.cephfs.csi.ceph.com",
    )
    c2 = phase10.FilesystemTarget(
        storage_name="cephfs-b",
        cluster_name="cluster-b",
        node_name=os.getenv("DMS_PHASE11_C2_NODE", os.getenv("DMS_PHASE10_C2_NODE", "c2-worker")),
        mount_path=os.getenv(
            "DMS_PHASE11_C2_CEPH_MOUNT_PATH",
            os.getenv("DMS_PHASE10_C2_CEPH_MOUNT_PATH", "/mnt/testbed-cephfs-c2"),
        ),
    )
    ldap_password = os.environ["DMS_LDAP_BIND_PASSWORD"]
    created_users: list[str] = []
    created_directories: list[tuple[phase10.FilesystemTarget, str]] = []

    try:
        host_mounts = [phase10.check_host_mount(c1), phase10.check_host_mount(c2)]
        users = ensure_phase11_ldap_users(token=token, ldap_password=ldap_password)
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
            worker_id=f"phase11-rm-{token}",
            lease_seconds=settings.worker_lease_seconds,
            backend_registry=BackendAdapterRegistry.with_phase1_defaults(
                services.repository, settings
            ),
        )

        target_summaries = []
        for base, suffix in [(c1, "a"), (c2, "b")]:
            directory_name = f"phase11-expired-{suffix}-{token}"
            group_name = f"dms-phase11-{directory_name}"
            target = Phase11Target(base, directory_name, group_name)
            created_directories.append((base, directory_name))
            target_summaries.append(
                verify_expiry_block_unblock_delete(
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

        system_directory = f"phase11-system-{token}"
        system_target = Phase11Target(
            c1,
            system_directory,
            f"dms-phase11-{system_directory}",
        )
        created_directories.append((c1, system_directory))
        system_summary = verify_system_resource_sweep_skip(
            client=client,
            repository=services.repository,
            rm_worker=rm_worker,
            target=system_target,
            allowed_users=users["allowed_users"],
            denied_user=users["denied_user"],
            headers=headers,
        )
        created_directories.pop()

        summary = {
            "status": "ok",
            "operational_database_url": mask_url(settings.database_url),
            "observability_database_url": mask_url(settings.observability_database_url),
            "host_mounts": host_mounts,
            "ldap_users": {
                "allowed_users": users["allowed_users"],
                "denied_user": users["denied_user"],
                "created_users": created_users,
            },
            "agent_reports": reports,
            "storage_mappings": mapping_summaries,
            "targets": target_summaries,
            "system_skip": system_summary,
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        for target, directory_name in created_directories:
            phase10.cleanup_directory(target, directory_name)
        for username in created_users:
            phase10.delete_ldap_user(username, ldap_password)


def verify_expiry_block_unblock_delete(
    *,
    client: TestClient,
    repository,
    rm_worker: RMWorkerRuntime,
    target: Phase11Target,
    allowed_users: list[str],
    denied_user: str,
    headers: dict[str, str],
) -> dict:
    create_id = create_filesystem(
        client=client,
        target=target,
        allowed_users=allowed_users,
        denied_user=denied_user,
        resource_type="user",
        headers=headers,
    )
    phase10.run_planner_worker(
        repository, rm_worker, create_id, f"{target.base.storage_name} phase11 create"
    )
    assert_allowed_and_denied(target, allowed_users, denied_user)

    expiring = client.get(
        "/api/v1/operations/filesystems/expiring",
        headers=headers,
        params={"storage_name": target.base.storage_name, "status": "expired"},
    )
    assert_equal(expiring.status_code, 200, f"{target.base.storage_name} expiring query")
    assert_true(
        any(item["resource_key"] == resource_key(target) for item in expiring.json()),
        f"{target.base.storage_name} expired resource visible",
    )
    action_before = client.get("/api/v1/operations/action-required", headers=headers).json()
    assert_true(
        any(
            issue["issue_type"] == "filesystem_expired_unblocked"
            and issue["resource_key"] == resource_key(target)
            for issue in action_before
        ),
        f"{target.base.storage_name} expired_unblocked action-required",
    )

    dry_run_id = expiration_sweep(
        client=client,
        storage_name=target.base.storage_name,
        headers=headers,
        dry_run=True,
    )
    phase10.run_planner_worker(
        repository, rm_worker, dry_run_id, f"{target.base.storage_name} dry-run sweep"
    )
    assert_allowed_and_denied(target, allowed_users, denied_user)

    sweep_id = expiration_sweep(
        client=client,
        storage_name=target.base.storage_name,
        headers=headers,
        dry_run=False,
    )
    phase10.run_planner_worker(
        repository, rm_worker, sweep_id, f"{target.base.storage_name} sweep block"
    )
    resource = repository.get_resource("filesystem", resource_key(target))
    assert_equal(resource["status"], "Blocked", f"{target.base.storage_name} blocked DB status")
    assert_equal(
        resource["desired_state"]["block_state"]["restore"]["mode"],
        "0770",
        f"{target.base.storage_name} restore mode",
    )
    blocked_stat = phase10.run_ssh(
        target.base.node_name, f"sudo stat -c '%U %G %a %n' {shlex.quote(target.directory_path)}"
    )
    assert_equal(
        phase10.run_ssh(
            target.base.node_name, f"sudo stat -c '%a' {shlex.quote(target.directory_path)}"
        ),
        "0",
        f"{target.base.storage_name} blocked mode",
    )
    for user in allowed_users:
        phase10.run_ssh(
            target.base.node_name,
            "sudo -u "
            f"{shlex.quote(user)} sh -c 'test ! -x \"$1\" && test ! -w \"$1\"' sh "
            f"{shlex.quote(target.directory_path)}",
        )
    phase10.run_ssh(
        target.base.node_name,
        "sudo -u "
        f"{shlex.quote(denied_user)} sh -c 'test ! -x \"$1\" && test ! -w \"$1\"' sh "
        f"{shlex.quote(target.directory_path)}",
    )

    unblock_response = client.post(
        f"/api/v1/resource-management/filesystems/{target.base.storage_name}/{target.directory_name}:block",
        headers=headers,
        json={"requester_id": "portal:phase11", "payload": {"block": False, "reason": "phase11 unblock"}},
    )
    assert_equal(unblock_response.status_code, 202, f"{target.base.storage_name} unblock request")
    unblock_id = unblock_response.json()["request_id"]
    phase10.run_planner_worker(
        repository, rm_worker, unblock_id, f"{target.base.storage_name} unblock"
    )
    resource = repository.get_resource("filesystem", resource_key(target))
    assert_equal(resource["status"], "Succeeded", f"{target.base.storage_name} unblocked DB status")
    assert_equal(
        resource["desired_state"]["block_state"]["blocked"],
        False,
        f"{target.base.storage_name} block_state cleared",
    )
    assert_allowed_and_denied(target, allowed_users, denied_user)

    delete_id = delete_filesystem(client, target, headers)
    phase10.run_planner_worker(
        repository, rm_worker, delete_id, f"{target.base.storage_name} phase11 delete"
    )
    phase10.run_ssh(target.base.node_name, f"test ! -e {shlex.quote(target.directory_path)}")
    resource = repository.get_resource("filesystem", resource_key(target))
    assert_equal(resource["status"], "Deleted", f"{target.base.storage_name} deleted")

    return {
        "storage_name": target.base.storage_name,
        "cluster_name": target.base.cluster_name,
        "node_name": target.base.node_name,
        "directory_name": target.directory_name,
        "directory_path": target.directory_path,
        "group_name": target.group_name,
        "create_request_id": create_id,
        "dry_run_sweep_request_id": dry_run_id,
        "sweep_request_id": sweep_id,
        "unblock_request_id": unblock_id,
        "delete_request_id": delete_id,
        "blocked_stat": blocked_stat,
    }


def verify_system_resource_sweep_skip(
    *,
    client: TestClient,
    repository,
    rm_worker: RMWorkerRuntime,
    target: Phase11Target,
    allowed_users: list[str],
    denied_user: str,
    headers: dict[str, str],
) -> dict:
    create_id = create_filesystem(
        client=client,
        target=target,
        allowed_users=allowed_users,
        denied_user=denied_user,
        resource_type="system",
        headers=headers,
    )
    phase10.run_planner_worker(repository, rm_worker, create_id, "phase11 system create")
    sweep_id = expiration_sweep(
        client=client,
        storage_name=target.base.storage_name,
        headers=headers,
        dry_run=False,
        resource_type="system",
    )
    phase10.run_planner_worker(repository, rm_worker, sweep_id, "phase11 system sweep")
    [result] = repository.get_results(sweep_id)
    skipped = [
        item
        for item in result["verification_summary"]["targets"]
        if item["resource_key"] == resource_key(target)
    ]
    assert_true(skipped, "system resource sweep target exists")
    assert_equal(skipped[0]["result"], "skipped", "system resource skipped")
    assert_equal(
        skipped[0]["reason"],
        "resource_type_not_auto_blocked",
        "system resource skip reason",
    )
    action_required = client.get("/api/v1/operations/action-required", headers=headers).json()
    assert_true(
        any(
            issue["issue_type"] == "filesystem_expiration_sweep_skipped"
            and issue["resource_key"] == resource_key(target)
            for issue in action_required
        ),
        "system skip surfaced in action-required",
    )
    delete_id = delete_filesystem(client, target, headers)
    phase10.run_planner_worker(repository, rm_worker, delete_id, "phase11 system delete")
    phase10.run_ssh(target.base.node_name, f"test ! -e {shlex.quote(target.directory_path)}")
    return {
        "storage_name": target.base.storage_name,
        "directory_name": target.directory_name,
        "create_request_id": create_id,
        "sweep_request_id": sweep_id,
        "delete_request_id": delete_id,
        "skip_reason": skipped[0]["reason"],
    }


def create_filesystem(
    *,
    client: TestClient,
    target: Phase11Target,
    allowed_users: list[str],
    denied_user: str,
    resource_type: str,
    headers: dict[str, str],
) -> str:
    response = client.post(
        "/api/v1/resource-management/filesystems",
        headers=headers,
        json={
            "requester_id": "portal:phase11",
            "payload": {
                "storage_name": target.base.storage_name,
                "directory_name": target.directory_name,
                "resource_type": resource_type,
                "users": allowed_users,
                "access_group": target.group_name,
                "validation_denied_users": [denied_user],
                "expires_at": EXPIRED_AT,
                "memo": "phase11 filesystem expiry verification",
            },
        },
    )
    assert_equal(response.status_code, 202, f"{target.base.storage_name} filesystem create")
    return response.json()["request_id"]


def delete_filesystem(
    client: TestClient, target: Phase11Target, headers: dict[str, str]
) -> str:
    response = client.request(
        "DELETE",
        f"/api/v1/resource-management/filesystems/{target.base.storage_name}/{target.directory_name}",
        headers=headers,
        json={"requester_id": "portal:phase11", "payload": {"reason": "phase11 cleanup"}},
    )
    assert_equal(response.status_code, 202, f"{target.base.storage_name} filesystem delete")
    return response.json()["request_id"]


def expiration_sweep(
    *,
    client: TestClient,
    storage_name: str,
    headers: dict[str, str],
    dry_run: bool,
    resource_type: str | None = None,
) -> str:
    scope = {"storage_name": storage_name}
    if resource_type:
        scope["resource_type"] = resource_type
    response = client.post(
        "/api/v1/resource-management/filesystems:expiration-sweep",
        headers=headers,
        json={
            "requester_id": "portal:phase11",
            "payload": {
                "scope": scope,
                "expired_before": "2030-01-01T00:00:00Z",
                "action": "block",
                "dry_run": dry_run,
                "max_targets": 20,
                "reason": "phase11 expiration sweep",
            },
        },
    )
    assert_equal(response.status_code, 202, f"{storage_name} expiration sweep")
    return response.json()["request_id"]


def assert_allowed_and_denied(
    target: Phase11Target, allowed_users: list[str], denied_user: str
) -> None:
    phase10.wait_ssh(target.base.node_name, f"getent group {shlex.quote(target.group_name)}")
    phase10.ldap_group_search(target.group_name, os.environ["DMS_LDAP_BIND_PASSWORD"])
    for user in allowed_users:
        phase10.wait_ssh(target.base.node_name, f"id {shlex.quote(user)}")
        phase10.run_ssh(
            target.base.node_name,
            "sudo -u "
            f"{shlex.quote(user)} sh -c 'touch \"$1\" && rm \"$1\"' sh "
            f"{shlex.quote(target.directory_path + '/.phase11-access-' + user)}",
        )
    phase10.run_ssh(
        target.base.node_name,
        "sudo -u "
        f"{shlex.quote(denied_user)} sh -c 'test ! -x \"$1\" && test ! -w \"$1\"' sh "
        f"{shlex.quote(target.directory_path)}",
    )


def ensure_phase11_ldap_users(*, token: str, ldap_password: str) -> dict:
    configured_allowed = _csv(os.getenv("DMS_PHASE11_ALLOWED_USERS")) or ["alice", "bob"]
    allowed_users = [
        user for user in configured_allowed if phase10.ldap_user_exists(user, ldap_password)
    ]
    created_users: list[str] = []
    uid_base = 26000 + int(token[:4], 16) % 1000
    while len(allowed_users) < 2:
        username = f"dms-phase11-allowed-{len(allowed_users) + 1}-{token}"
        phase10.create_ldap_user(
            username,
            uid_number=uid_base + len(created_users),
            password=ldap_password,
        )
        created_users.append(username)
        allowed_users.append(username)
    denied_user = os.getenv("DMS_PHASE11_DENIED_USER")
    if not denied_user or not phase10.ldap_user_exists(denied_user, ldap_password):
        denied_user = f"dms-phase11-denied-{token}"
        phase10.create_ldap_user(
            denied_user,
            uid_number=uid_base + len(created_users),
            password=ldap_password,
        )
        created_users.append(denied_user)
    return {
        "allowed_users": allowed_users[:2],
        "denied_user": denied_user,
        "created_users": created_users,
    }


def resource_key(target: Phase11Target) -> str:
    return f"{target.base.storage_name}:{target.directory_name}"


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
