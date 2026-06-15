from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Protocol
from urllib.parse import urlparse

from ..config import Settings


@dataclass(frozen=True)
class AdapterResult:
    applied_state: dict[str, Any]
    observed_state: dict[str, Any]
    message: str = "stub adapter completed"
    artifact_uri: str | None = None



class FilesystemBackendAdapter(Protocol):
    def create(self, plan: dict[str, Any]) -> AdapterResult: ...

    def update(self, plan: dict[str, Any]) -> AdapterResult: ...

    def block(self, plan: dict[str, Any]) -> AdapterResult: ...

    def initialize(self, plan: dict[str, Any]) -> AdapterResult: ...

    def delete(self, plan: dict[str, Any]) -> AdapterResult: ...

    def consistency_check(self, plan: dict[str, Any]) -> AdapterResult: ...

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult: ...

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult: ...

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult: ...



class FilesystemQuotaStrategy(Protocol):
    backend_type: str

    def render_quota(self, quota: dict[str, Any]) -> dict[str, Any]: ...



class KubernetesNamespaceQuotaAdapter(Protocol):
    def read_namespace(
        self, cluster_name: str, namespace_name: str
    ) -> dict[str, Any]: ...

    def read_resource_quota(
        self,
        cluster_name: str,
        namespace_name: str,
        resource_quota_name: str = "dms-storage-quota",
    ) -> dict[str, Any]: ...

    def list_resource_quotas(
        self, cluster_name: str, namespace_name: str
    ) -> list[dict[str, Any]]: ...

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult: ...

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult: ...

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult: ...

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult: ...

    def import_resource_quota(self, plan: dict[str, Any]) -> AdapterResult: ...

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult: ...

    def audit_resource_quotas(self, plan: dict[str, Any]) -> AdapterResult: ...



class StorageInventoryAdapter(Protocol):
    def effective_inventory(self) -> dict[str, Any]: ...



class KubernetesInventoryReadError(RuntimeError):
    pass



class KubernetesMutationError(RuntimeError):
    pass



class DataManagementRuntimeError(RuntimeError):
    pass



class KubernetesReadOnlyInventoryAdapter(Protocol):
    def read_inventory(self) -> dict[str, Any]: ...



class DataManagementStorageAdapter(Protocol):
    def worker_pool(self, storage_name: str) -> dict[str, Any]: ...



@dataclass(frozen=True)
class IdentityLookupResult:
    provider: str
    posix_username: str
    uid: int
    primary_gid: int
    groups: list[str]
    user_dn: str
    source_metadata: dict[str, Any]



class IdentityLookupAdapter(Protocol):
    def lookup(
        self, provider: str, posix_username: str
    ) -> IdentityLookupResult | None: ...



class IdentityGroupManager(Protocol):
    def ensure_group_members(
        self,
        *,
        group_name: str,
        users: list[str],
        resource_key: str,
    ) -> dict[str, Any]: ...

    def delete_group(self, *, group_name: str) -> dict[str, Any]: ...

    def list_group_members(self, *, group_name: str) -> list[str]: ...

    def lookup_group_gid(self, *, group_name: str) -> int | None: ...

    def lookup_group_name_by_gid(self, *, gid: int) -> str | None: ...



class IdentityLookupConfigurationError(RuntimeError):
    pass



class IdentityLookupReadError(RuntimeError):
    pass



class BackendPreconditionError(RuntimeError):
    pass



def probe_filesystem_access(
    *,
    run_cmd: Callable[[list[str]], int],
    run_cmd_out: Callable[[list[str]], tuple[int, str]] | None = None,
    path: str,
    allowed_users: list[str],
    denied_users: list[str],
    group_gid: int | None = None,
) -> dict[str, Any]:
    """Verify filesystem access for allowed and denied users.

    When group_gid is provided, verifies access via stat(directory gid) and
    LDAP group membership lookup (getent group <gid>), which works regardless
    of whether SSSD is synced with the local LDAP. This is the preferred mode
    for environments where the OS SSSD does not know about DMS-managed groups.

    Falls back to sudo -u user touch/rm probe when group_gid is not given.
    """
    allowed: dict[str, str] = {}

    if group_gid is not None and run_cmd_out is not None:
        # Stat-based check: verify directory GID matches, then check getent group membership
        rc_stat, stat_out = run_cmd_out(["sh", "-c", f"stat -c '%g %a' {path}"])
        if rc_stat == 0:
            parts = stat_out.strip().split()
            dir_gid = int(parts[0]) if parts else -1
            dir_mode = parts[1] if len(parts) > 1 else ""
            gid_ok = dir_gid == group_gid
            group_writable = len(dir_mode) >= 3 and int(dir_mode[-2]) in (2, 3, 6, 7)
            # Check group membership via getent (reads /etc/group or SSSD if available)
            _, members_out = run_cmd_out(
                ["sh", "-c", f"getent group {group_gid} 2>/dev/null || true"]
            )
            # Format: name:passwd:gid:members (comma separated)
            known_members: set[str] = set()
            if ":" in members_out:
                parts_g = members_out.strip().split(":")
                if len(parts_g) >= 4:
                    known_members = {
                        m.strip() for m in parts_g[3].split(",") if m.strip()
                    }
            for user in allowed_users:
                if not gid_ok:
                    allowed[user] = "gid_mismatch"
                elif not group_writable:
                    allowed[user] = "mode_not_writable"
                elif known_members and user not in known_members:
                    allowed[user] = "not_in_group"
                else:
                    allowed[user] = "ok"
        else:
            for user in allowed_users:
                allowed[user] = "stat_failed"
    else:
        for user in allowed_users:
            probe = f"{path}/.access-probe-{user}"
            rc = run_cmd(
                ["sudo", "-u", user, "sh", "-c", 'touch "$1" && rm "$1"', "sh", probe]
            )
            allowed[user] = "ok" if rc == 0 else "probe_failed"

    denied: dict[str, str] = {}
    for user in denied_users:
        rc = run_cmd(
            [
                "sudo",
                "-u",
                user,
                "sh",
                "-c",
                'test ! -x "$1" && test ! -w "$1"',
                "sh",
                path,
            ]
        )
        denied[user] = "denied" if rc == 0 else "unexpected_access"
    return {"allowed_users": allowed, "denied_users": denied}



class VolcanoAdapter(Protocol):
    def verify_scan_preflight(
        self, plan: dict[str, Any], data_job: dict[str, Any], preflight: dict[str, Any]
    ) -> dict[str, Any]: ...

    def verify_data_preflight(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        preflight: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]: ...

    def create_job(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> AdapterResult: ...

    def get_job(self, job_ref: str) -> dict[str, Any]: ...

    def terminate_job(self, job_ref: str) -> AdapterResult: ...
