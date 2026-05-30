from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dms.adapters import StubFilesystemBackendAdapter, StubKubernetesNamespaceQuotaAdapter  # noqa: E402
from dms.api import create_app  # noqa: E402
from dms.backend_registry import BackendAdapterRegistry  # noqa: E402
from dms.config import Settings  # noqa: E402
from dms.domain import LifecycleState  # noqa: E402
from dms.planner import Planner  # noqa: E402
from dms.workers import RMWorkerRuntime  # noqa: E402
from scripts.phase6_kubernetes_multi_storage_quota import (  # noqa: E402
    API_HEADERS,
    assert_equal,
    assert_true,
    mask_url,
)


LDAP_BASE_DN = "dc=testbed,dc=local"
LDAP_PEOPLE_DN = f"ou=people,{LDAP_BASE_DN}"
LDAP_GROUPS_DN = f"ou=groups,{LDAP_BASE_DN}"
LDAP_BIND_DN = f"cn=admin,{LDAP_BASE_DN}"


@dataclass(frozen=True)
class FilesystemTarget:
    storage_name: str
    cluster_name: str
    node_name: str
    mount_path: str
    storage_class_name: str | None = None
    csi_driver: str | None = None

    @property
    def managed_root(self) -> str:
        return f"{self.mount_path.rstrip('/')}/dms-phase10"


def main() -> int:
    settings = Settings.from_env()
    if settings.auth_shared_token:
        API_HEADERS["authorization"] = f"Bearer {settings.auth_shared_token}"
    assert_true(
        settings.database_url.startswith("postgresql://"),
        "Phase 10 live verification must use operational PostgreSQL",
    )
    assert_true(settings.observability_is_separate, "observability DB must be separate")

    token = uuid4().hex[:8]
    c1 = FilesystemTarget(
        storage_name="cephfs-a",
        cluster_name="cluster-a",
        node_name=os.getenv("DMS_PHASE10_C1_NODE", "c1-worker"),
        mount_path=os.getenv("DMS_PHASE10_C1_CEPH_MOUNT_PATH", "/mnt/testbed-cephfs"),
        storage_class_name="testbed-cephfs",
        csi_driver="rook-ceph.cephfs.csi.ceph.com",
    )
    c2 = FilesystemTarget(
        storage_name="cephfs-b",
        cluster_name="cluster-b",
        node_name=os.getenv("DMS_PHASE10_C2_NODE", "c2-worker"),
        mount_path=os.getenv(
            "DMS_PHASE10_C2_CEPH_MOUNT_PATH", "/mnt/testbed-cephfs-c2"
        ),
    )
    ldap_password = os.environ["DMS_LDAP_BIND_PASSWORD"]
    created_users: list[str] = []
    created_directories: list[tuple[FilesystemTarget, str]] = []

    try:
        host_mounts = [check_host_mount(c1), check_host_mount(c2)]
        users = ensure_ldap_users(token=token, ldap_password=ldap_password)
        created_users = users["created_users"]
        verify_sssd_users([c1.node_name, c2.node_name], users["allowed_users"] + [users["denied_user"]])

        app = create_app(settings)
        services = app.state.services
        client = TestClient(app)
        reports = wait_for_phase10_reports(client, [c1, c2])
        inventory = client.get("/api/v1/operations/inventory", headers=API_HEADERS).json()
        assert_true(c1.cluster_name in inventory["clusters"], "cluster-a inventory")
        assert_true(c2.cluster_name in inventory["clusters"], "cluster-b inventory")

        mapping_summaries = [upsert_mapping(client, c1), upsert_mapping(client, c2)]
        rm_worker = RMWorkerRuntime(
            repository=services.repository,
            observability=services.observability,
            filesystem_adapter=StubFilesystemBackendAdapter(),
            kubernetes_adapter=StubKubernetesNamespaceQuotaAdapter(),
            worker_id=f"phase10-rm-{token}",
            lease_seconds=settings.worker_lease_seconds,
            backend_registry=BackendAdapterRegistry.with_phase1_defaults(
                services.repository, settings
            ),
        )

        target_summaries = []
        for target, suffix in [(c1, "a"), (c2, "b")]:
            directory_name = f"phase10-{suffix}-{token}"
            created_directories.append((target, directory_name))
            target_summaries.append(
                verify_create_delete(
                    client=client,
                    repository=services.repository,
                    rm_worker=rm_worker,
                    target=target,
                    directory_name=directory_name,
                    allowed_users=users["allowed_users"],
                    denied_user=users["denied_user"],
                )
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
        }
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0
    finally:
        for target, directory_name in created_directories:
            cleanup_directory(target, directory_name)
        for username in created_users:
            delete_ldap_user(username, ldap_password)


def check_host_mount(target: FilesystemTarget) -> dict:
    findmnt = run_ssh(
        target.node_name,
        f"findmnt {shlex.quote(target.mount_path)} -o TARGET,SOURCE,FSTYPE -n",
    )
    statfs = run_ssh(target.node_name, f"stat -f -c '%T' {shlex.quote(target.mount_path)}")
    run_ssh(target.node_name, f"sudo mkdir -p {shlex.quote(target.managed_root)}")
    probe = f"{target.managed_root}/.phase10-probe-{uuid4().hex[:8]}"
    run_ssh(target.node_name, f"sudo sh -c 'touch {shlex.quote(probe)} && rm {shlex.quote(probe)}'")
    return {
        "storage_name": target.storage_name,
        "cluster_name": target.cluster_name,
        "node_name": target.node_name,
        "mount_path": target.mount_path,
        "findmnt": findmnt,
        "statfs_type": statfs,
        "managed_root": target.managed_root,
    }


def ensure_ldap_users(*, token: str, ldap_password: str) -> dict:
    configured_allowed = _csv(os.getenv("DMS_PHASE10_ALLOWED_USERS")) or ["alice", "bob"]
    allowed_users = [user for user in configured_allowed if ldap_user_exists(user, ldap_password)]
    created_users: list[str] = []
    uid_base = 25000 + int(token[:4], 16) % 1000
    while len(allowed_users) < 2:
        username = f"dms-phase10-allowed-{len(allowed_users) + 1}-{token}"
        create_ldap_user(
            username,
            uid_number=uid_base + len(created_users),
            password=ldap_password,
        )
        created_users.append(username)
        allowed_users.append(username)
    denied_user = os.getenv("DMS_PHASE10_DENIED_USER")
    if not denied_user or not ldap_user_exists(denied_user, ldap_password):
        denied_user = f"dms-phase10-denied-{token}"
        create_ldap_user(
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


def ldap_user_exists(username: str, ldap_password: str) -> bool:
    command = (
        "ldapsearch -x -LLL -H ldap://127.0.0.1 "
        f"-D {shlex.quote(LDAP_BIND_DN)} -w {shlex.quote(ldap_password)} "
        f"-b {shlex.quote(LDAP_PEOPLE_DN)} "
        f"{shlex.quote('(uid=' + username + ')')} uid"
    )
    completed = subprocess.run(
        ["ssh", "ldap", command],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode == 0 and f"uid: {username}" in completed.stdout


def create_ldap_user(username: str, *, uid_number: int, password: str) -> None:
    user_password = os.getenv("DMS_PHASE10_FIXTURE_USER_PASSWORD", "testbed123")
    password_hash = run_ssh("ldap", f"slappasswd -s {shlex.quote(user_password)}")
    ldif = f"""dn: uid={username},{LDAP_PEOPLE_DN}
objectClass: inetOrgPerson
objectClass: posixAccount
objectClass: shadowAccount
cn: {username}
sn: Phase10
uid: {username}
uidNumber: {uid_number}
gidNumber: 10000
homeDirectory: /home/{username}
loginShell: /bin/bash
mail: {username}@testbed.local
userPassword: {password_hash}
"""
    subprocess.run(
        [
            "ssh",
            "ldap",
            "ldapadd",
            "-x",
            "-H",
            "ldap://127.0.0.1",
            "-D",
            LDAP_BIND_DN,
            "-w",
            password,
        ],
        input=ldif,
        check=True,
        text=True,
    )


def delete_ldap_user(username: str, ldap_password: str) -> None:
    subprocess.run(
        [
            "ssh",
            "ldap",
            "ldapdelete",
            "-x",
            "-H",
            "ldap://127.0.0.1",
            "-D",
            LDAP_BIND_DN,
            "-w",
            ldap_password,
            f"uid={username},{LDAP_PEOPLE_DN}",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def verify_sssd_users(hosts: list[str], users: list[str]) -> None:
    for host in hosts:
        run_ssh(host, "sudo sss_cache -E || true")
        for user in users:
            wait_ssh(host, f"getent passwd {shlex.quote(user)}", timeout_seconds=60)


def wait_for_phase10_reports(
    client: TestClient, targets: list[FilesystemTarget], *, timeout_seconds: int = 180
) -> dict:
    required = {(target.cluster_name, target.node_name, target.storage_name) for target in targets}
    deadline = time.monotonic() + timeout_seconds
    observed: dict[tuple[str, str, str], dict] = {}
    while time.monotonic() < deadline:
        response = client.get("/api/v1/operations/agent-reports", headers=API_HEADERS)
        assert_equal(response.status_code, 200, "agent report query")
        for report in response.json():
            if report["freshness_status"] != "Fresh":
                continue
            payload = report["report"]
            if payload.get("schema_version") != "phase8.v1":
                continue
            for mount in payload.get("mounts", []):
                if mount.get("status") != "Ready":
                    continue
                key = (report["cluster_name"], report["node_name"], mount.get("storage_name"))
                if key in required:
                    observed[key] = {
                        "report_id": report["report_id"],
                        "mount_path": mount.get("mount_path") or mount.get("path"),
                        "filesystem_type": mount.get("filesystem_type"),
                        "readable": mount.get("readable"),
                        "writable": mount.get("writable"),
                    }
        if required.issubset(observed):
            return {":".join(key): value for key, value in sorted(observed.items())}
        time.sleep(5)
    raise AssertionError(f"missing Phase 10 host mount reports: {sorted(required - set(observed))}")


def upsert_mapping(client: TestClient, target: FilesystemTarget) -> dict:
    backend_template = {
        "backend_type": "cephfs",
        "cluster_name": target.cluster_name,
        "mount_path": target.mount_path,
        "managed_root": target.managed_root,
        "rm_worker_nodes": [target.node_name],
        "ssh_host": target.node_name,
    }
    if target.csi_driver:
        backend_template["csi_driver"] = target.csi_driver
    response = client.post(
        "/api/v1/resource-management/storage-mappings",
        json={
            "storage_name": target.storage_name,
            "backend_template": backend_template,
            "cluster_name": target.cluster_name,
            "storage_class_name": target.storage_class_name,
        },
        headers=API_HEADERS,
    )
    assert_equal(response.status_code, 200, f"{target.storage_name} mapping upsert")
    payload = response.json()
    assert_equal(
        payload["mapping"]["readiness"]["resource_management"],
        "Ready",
        f"{target.storage_name} RM readiness",
    )
    return {
        "storage_name": target.storage_name,
        "status": payload["status"],
        "readiness": payload["mapping"]["readiness"],
        "sanity_errors": payload["mapping"]["sanity_result"]["errors"],
        "sanity_warnings": payload["mapping"]["sanity_result"]["warnings"],
    }


def verify_create_delete(
    *,
    client: TestClient,
    repository,
    rm_worker: RMWorkerRuntime,
    target: FilesystemTarget,
    directory_name: str,
    allowed_users: list[str],
    denied_user: str,
) -> dict:
    group_name = f"dms-phase10-{directory_name}"
    create_response = client.post(
        "/api/v1/resource-management/filesystems",
        json={
            "requester_id": "portal:phase10",
            "payload": {
                "storage_name": target.storage_name,
                "directory_name": directory_name,
                "resource_type": "user",
                "users": allowed_users,
                "access_group": group_name,
                "validation_denied_users": [denied_user],
                "expires_at": "2026-06-30T00:00:00Z",
                "memo": "phase10 host-mounted CephFS create",
            },
        },
        headers=API_HEADERS,
    )
    assert_equal(create_response.status_code, 202, f"{target.storage_name} create request")
    create_id = create_response.json()["request_id"]
    run_planner_worker(repository, rm_worker, create_id, f"{target.storage_name} create")
    directory_path = f"{target.managed_root}/{directory_name}"
    ldap_group = ldap_group_search(group_name, os.environ["DMS_LDAP_BIND_PASSWORD"])
    wait_ssh(target.node_name, f"getent group {shlex.quote(group_name)}")
    for user in allowed_users:
        wait_ssh(target.node_name, f"id {shlex.quote(user)}")
        run_ssh(
            target.node_name,
            "sudo -u "
            f"{shlex.quote(user)} sh -c 'touch \"$1\" && rm \"$1\"' sh "
            f"{shlex.quote(directory_path + '/.access-allowed-live-' + user)}",
        )
    run_ssh(
        target.node_name,
        "sudo -u "
        f"{shlex.quote(denied_user)} sh -c 'test ! -x \"$1\" && test ! -w \"$1\"' sh "
        f"{shlex.quote(directory_path)}",
    )
    stat_output = run_ssh(
        target.node_name,
        f"sudo stat -c '%U %G %a %n' {shlex.quote(directory_path)} && "
        f"sudo test -f {shlex.quote(directory_path + '/.dms-resource.json')}",
    )

    delete_response = client.request(
        "DELETE",
        f"/api/v1/resource-management/filesystems/{target.storage_name}/{directory_name}",
        json={"requester_id": "portal:phase10", "payload": {"reason": "phase10 cleanup"}},
        headers=API_HEADERS,
    )
    assert_equal(delete_response.status_code, 202, f"{target.storage_name} delete request")
    delete_id = delete_response.json()["request_id"]
    run_planner_worker(repository, rm_worker, delete_id, f"{target.storage_name} delete")
    run_ssh(target.node_name, f"test ! -e {shlex.quote(directory_path)}")
    resource = repository.get_resource("filesystem", f"{target.storage_name}:{directory_name}")
    assert_equal(resource["status"], "Deleted", f"{target.storage_name} resource deleted")
    return {
        "storage_name": target.storage_name,
        "cluster_name": target.cluster_name,
        "node_name": target.node_name,
        "directory_name": directory_name,
        "directory_path": directory_path,
        "group_name": group_name,
        "create_request_id": create_id,
        "delete_request_id": delete_id,
        "ldap_group": ldap_group,
        "stat": stat_output,
    }


def run_planner_worker(
    repository, rm_worker: RMWorkerRuntime, request_id: str, label: str
) -> None:
    assert_equal(Planner(repository).run_once(), 1, f"{label} planner")
    assert_true(repository.get_plan_by_request(request_id) is not None, f"{label} plan")
    assert_equal(rm_worker.run_once(), 1, f"{label} worker")
    assert_equal(
        repository.get_request(request_id)["status"],
        LifecycleState.SUCCEEDED.value,
        f"{label} request succeeded",
    )


def ldap_group_search(group_name: str, ldap_password: str) -> str:
    return run_ssh(
        "ldap",
        "ldapsearch -x -LLL -H ldap://127.0.0.1 "
        f"-D {shlex.quote(LDAP_BIND_DN)} -w {shlex.quote(ldap_password)} "
        f"-b {shlex.quote(LDAP_GROUPS_DN)} "
        f"{shlex.quote('(cn=' + group_name + ')')} cn gidNumber memberUid",
    )


def cleanup_directory(target: FilesystemTarget, directory_name: str) -> None:
    path = f"{target.managed_root}/{directory_name}"
    run_ssh(target.node_name, f"sudo rm -rf {shlex.quote(path)}", check=False)


def run_ssh(host: str, command: str, *, check: bool = True) -> str:
    completed = subprocess.run(
        ["ssh", host, command],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and completed.returncode != 0:
        raise AssertionError(
            f"ssh {host} {command!r} failed: {completed.stderr.strip()}"
        )
    return completed.stdout.strip()


def wait_ssh(host: str, command: str, *, timeout_seconds: int = 60) -> str:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        completed = subprocess.run(
            ["ssh", host, command],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return completed.stdout.strip()
        last_error = completed.stderr.strip() or completed.stdout.strip()
        time.sleep(3)
    raise AssertionError(f"ssh {host} {command!r} did not succeed: {last_error}")


def _csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
