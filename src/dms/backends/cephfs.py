from __future__ import annotations

from dataclasses import dataclass
import json
import shlex
import subprocess
import textwrap
from typing import Any, Protocol

from dms.adapters import (
    AdapterResult,
    BackendPreconditionError,
    IdentityGroupManager,
    IdentityLookupConfigurationError,
    IdentityLookupReadError,
    LdapIdentityGroupManager,
)
from dms.config import Settings
from dms.domain import validate_storage_root_basename


CEPHFS_BACKEND_TYPE = "cephfs"
MARKER_NAME = ".dms-resource.json"


class HostExecutionError(RuntimeError):
    pass


class FilesystemHostExecutor(Protocol):
    def create_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        marker: dict[str, Any],
        group_name: str,
        mode: str,
        allowed_users: list[str],
        denied_users: list[str],
    ) -> dict[str, Any]: ...

    def delete_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
    ) -> dict[str, Any]: ...

    def block_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        block_mode: str,
        reason: str | None,
        request_id: str,
        existing_block_state: dict[str, Any],
    ) -> dict[str, Any]: ...

    def unblock_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        block_state: dict[str, Any],
        allowed_users: list[str],
        denied_users: list[str],
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class CephFsBackendTemplate:
    storage_name: str
    cluster_name: str | None
    mount_path: str
    managed_root: str
    rm_worker_node: str

    @classmethod
    def from_storage_mapping(cls, mapping: dict[str, Any]) -> "CephFsBackendTemplate":
        template = mapping["backend_template"]
        rm_workers = template.get("rm_worker_nodes") or []
        rm_worker_node = template.get("ssh_host") or (rm_workers[0] if rm_workers else "")
        mount_path = template.get("mount_path", "")
        managed_root = template.get("managed_root") or (
            f"{mount_path.rstrip('/')}/dms-phase10" if mount_path else ""
        )
        return cls(
            storage_name=mapping["storage_name"],
            cluster_name=template.get("cluster_name") or mapping.get("cluster_name"),
            mount_path=mount_path,
            managed_root=managed_root,
            rm_worker_node=rm_worker_node,
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "backend_type": CEPHFS_BACKEND_TYPE,
            "storage_name": self.storage_name,
            "cluster_name": self.cluster_name,
            "mount_path": self.mount_path,
            "managed_root": self.managed_root,
            "rm_worker_node": self.rm_worker_node,
        }


@dataclass(frozen=True)
class PythonHostExecutor:
    host: str | None = None
    mode: str = "ssh-host-exec"
    timeout_seconds: int = 30
    use_sudo: bool = True

    def create_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        marker: dict[str, Any],
        group_name: str,
        mode: str,
        allowed_users: list[str],
        denied_users: list[str],
    ) -> dict[str, Any]:
        return self._run_script(
            _CREATE_DIRECTORY_SCRIPT,
            [
                managed_root,
                directory_name,
                json.dumps(marker, sort_keys=True),
                group_name,
                mode,
                json.dumps(allowed_users),
                json.dumps(denied_users),
            ],
        )

    def delete_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
    ) -> dict[str, Any]:
        return self._run_script(
            _DELETE_DIRECTORY_SCRIPT,
            [managed_root, directory_name, resource_key],
        )

    def block_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        block_mode: str,
        reason: str | None,
        request_id: str,
        existing_block_state: dict[str, Any],
    ) -> dict[str, Any]:
        return self._run_script(
            _BLOCK_DIRECTORY_SCRIPT,
            [
                managed_root,
                directory_name,
                resource_key,
                block_mode,
                reason or "",
                request_id,
                json.dumps(existing_block_state, sort_keys=True),
            ],
        )

    def unblock_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        block_state: dict[str, Any],
        allowed_users: list[str],
        denied_users: list[str],
    ) -> dict[str, Any]:
        return self._run_script(
            _UNBLOCK_DIRECTORY_SCRIPT,
            [
                managed_root,
                directory_name,
                resource_key,
                json.dumps(block_state, sort_keys=True),
                json.dumps(allowed_users),
                json.dumps(denied_users),
            ],
        )

    def _run_script(self, script: str, args: list[str]) -> dict[str, Any]:
        command = self._command(script, args)
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise HostExecutionError(f"filesystem host execution timed out: {exc}") from exc
        if completed.returncode != 0:
            raise HostExecutionError(
                "filesystem host execution failed: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise HostExecutionError(
                f"filesystem host execution returned non-JSON output: {completed.stdout!r}"
            ) from exc

    def _command(self, script: str, args: list[str]) -> list[str]:
        python_command = ["python3", "-c", script, *args]
        if self.use_sudo:
            python_command = ["sudo", *python_command]
        if self.mode == "local":
            return python_command
        if self.mode == "ssh-host-exec":
            if not self.host:
                raise HostExecutionError("ssh-host-exec requires an RM worker host")
            return ["ssh", self.host, shlex.join(python_command)]
        raise HostExecutionError(f"unsupported filesystem mutation mode: {self.mode}")


@dataclass
class CephFsHostMountedFilesystemBackendAdapter:
    template: CephFsBackendTemplate
    identity_groups: IdentityGroupManager
    executor: FilesystemHostExecutor

    @classmethod
    def from_storage_mapping(
        cls,
        mapping: dict[str, Any],
        settings: Settings,
        *,
        identity_groups: IdentityGroupManager | None = None,
        executor: FilesystemHostExecutor | None = None,
    ) -> "CephFsHostMountedFilesystemBackendAdapter":
        template = CephFsBackendTemplate.from_storage_mapping(mapping)
        return cls(
            template=template,
            identity_groups=identity_groups or LdapIdentityGroupManager.from_settings(settings),
            executor=executor
            or PythonHostExecutor(
                host=template.rm_worker_node,
                mode=settings.filesystem_mutation_mode,
                timeout_seconds=settings.filesystem_exec_timeout_seconds,
                use_sudo=settings.filesystem_exec_use_sudo,
            ),
        )

    def create(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        validate_storage_root_basename("directory_name", directory_name)
        users = _string_list(desired.get("users"), "users")
        if len(users) < 2:
            raise BackendPreconditionError("filesystem create requires at least two users")
        denied_users = _string_list(
            desired.get("denied_users") or desired.get("validation_denied_users") or [],
            "denied_users",
            required=False,
        )
        group_name = desired.get("access_group") or f"dms-phase10-{directory_name}"
        validate_storage_root_basename("access_group", group_name)
        if not group_name.startswith("dms-"):
            raise BackendPreconditionError(
                "DMS-managed filesystem access groups must start with 'dms-'"
            )
        mode = str(desired.get("mode", "0770"))
        _validate_mode(mode)
        marker = self._marker(plan, group_name)
        try:
            group = self.identity_groups.ensure_group_members(
                group_name=group_name,
                users=users,
                resource_key=plan["resource_key"],
            )
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(str(exc)) from exc
        observed = self.executor.create_directory(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            marker=marker,
            group_name=group_name,
            mode=mode,
            allowed_users=users,
            denied_users=denied_users,
        )
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "backend": self.template.metadata(),
                "identity_source": group.get("identity_source", "openldap-sssd"),
                "ldap_group_dn": group.get("dn"),
                "ldap_members": group.get("members", users),
                "group_name": group_name,
                "group_gid": group.get("gid") or observed.get("group_gid"),
                "resource_key": plan["resource_key"],
            }
        )
        applied = {
            "adapter": "cephfs-host-mounted",
            "operation": "create",
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "path": observed.get("path"),
            "marker": marker,
            "access_group": {
                "group_name": group_name,
                "gid": group.get("gid"),
                "dn": group.get("dn"),
                "members": group.get("members", users),
            },
            "expires_at": desired.get("expires_at"),
        }
        return AdapterResult(
            applied_state=applied,
            observed_state=observed,
            message="CephFS host-mounted filesystem create completed",
        )

    def delete(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        validate_storage_root_basename("directory_name", directory_name)
        group_name = desired.get("access_group") or f"dms-phase10-{directory_name}"
        observed = self.executor.delete_directory(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            resource_key=plan["resource_key"],
        )
        group_cleanup = self.identity_groups.delete_group(group_name=group_name)
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "resource_status": "Deleted",
                "access_group_cleanup": group_cleanup,
            }
        )
        return AdapterResult(
            applied_state={
                "adapter": "cephfs-host-mounted",
                "operation": "delete",
                "backend": self.template.metadata(),
                "directory_name": directory_name,
                "deleted": observed.get("deleted", False),
                "access_group_cleanup": group_cleanup,
            },
            observed_state=observed,
            message="CephFS host-mounted filesystem delete completed",
        )

    def update(self, plan: dict[str, Any]) -> AdapterResult:
        raise ValueError("filesystem update is unsupported in Phase 10")

    def block(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        validate_storage_root_basename("directory_name", directory_name)
        block = bool(desired.get("block"))
        if block:
            return self._block(plan)
        return self._unblock(plan)

    def initialize(self, plan: dict[str, Any]) -> AdapterResult:
        raise ValueError("filesystem initialize is unsupported in Phase 10")

    def consistency_check(self, plan: dict[str, Any]) -> AdapterResult:
        raise ValueError("filesystem consistency check is unsupported in Phase 10")

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult:
        raise ValueError("filesystem import is unsupported in Phase 10")

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult:
        raise ValueError("filesystem quota assignment is unsupported in Phase 10")

    def _marker(self, plan: dict[str, Any], group_name: str) -> dict[str, Any]:
        desired = plan["desired_state"]
        return {
            "managed_by": "dms",
            "resource_kind": "filesystem",
            "resource_key": plan["resource_key"],
            "storage_name": desired["storage_name"],
            "directory_name": desired["directory_name"],
            "request_id": plan["request_id"],
            "access_group": group_name,
        }

    def _validate_template(self) -> None:
        if not self.template.managed_root:
            raise BackendPreconditionError("CephFS storage mapping requires managed_root")
        if not self.template.mount_path:
            raise BackendPreconditionError("CephFS storage mapping requires mount_path")
        if not self.template.rm_worker_node and self.executor is None:
            raise BackendPreconditionError("CephFS storage mapping requires an RM worker node")

    def _block(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        block_state = dict(desired.get("block_state") or {})
        block_mode = block_state.get("block_mode") or desired.get(
            "block_mode", "permission-zero"
        )
        if block_mode != "permission-zero":
            raise BackendPreconditionError(
                f"unsupported filesystem block mode: {block_mode}"
            )
        observed = self.executor.block_directory(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            resource_key=plan["resource_key"],
            block_mode=block_mode,
            reason=block_state.get("reason") or desired.get("reason"),
            request_id=plan["request_id"],
            existing_block_state=block_state,
        )
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "resource_status": "Blocked",
            }
        )
        synced_desired = dict(desired)
        synced_desired["block"] = True
        synced_desired["block_state"] = observed["block_state"]
        return AdapterResult(
            applied_state={
                "adapter": "cephfs-host-mounted",
                "operation": "block",
                "backend": self.template.metadata(),
                "directory_name": directory_name,
                "path": observed.get("path"),
                "block_state": observed["block_state"],
                "synced_desired_state": synced_desired,
                "expires_at": desired.get("expires_at"),
            },
            observed_state=observed,
            message="CephFS host-mounted filesystem block completed",
        )

    def _unblock(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        block_state = dict(desired.get("block_state") or {})
        restore = block_state.get("restore") or block_state.get("restore_state")
        if not restore:
            raise BackendPreconditionError("filesystem block restore state missing")
        users = _string_list(desired.get("users"), "users")
        denied_users = _string_list(
            desired.get("denied_users") or desired.get("validation_denied_users") or [],
            "denied_users",
            required=False,
        )
        observed = self.executor.unblock_directory(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            resource_key=plan["resource_key"],
            block_state=block_state,
            allowed_users=users,
            denied_users=denied_users,
        )
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "resource_status": "Succeeded",
            }
        )
        synced_desired = dict(desired)
        synced_desired["block"] = False
        synced_desired["block_state"] = observed["block_state"]
        return AdapterResult(
            applied_state={
                "adapter": "cephfs-host-mounted",
                "operation": "unblock",
                "backend": self.template.metadata(),
                "directory_name": directory_name,
                "path": observed.get("path"),
                "block_state": observed["block_state"],
                "synced_desired_state": synced_desired,
                "expires_at": desired.get("expires_at"),
            },
            observed_state=observed,
            message="CephFS host-mounted filesystem unblock completed",
        )


def _string_list(value: Any, field_name: str, *, required: bool = True) -> list[str]:
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    items = [str(item) for item in value if str(item).strip()]
    if len(items) != len(value):
        raise ValueError(f"{field_name} entries must be non-empty strings")
    if len(set(items)) != len(items):
        raise ValueError(f"{field_name} entries must be unique")
    return items


def _validate_mode(mode: str) -> None:
    if mode not in {"0750", "0770"}:
        raise BackendPreconditionError("Phase 10 filesystem mode must be 0750 or 0770")


_CREATE_DIRECTORY_SCRIPT = textwrap.dedent(
    r"""
    import grp
    import json
    import os
    import subprocess
    import sys

    root, directory_name, marker_json, group_name, mode_text, allowed_json, denied_json = sys.argv[1:]
    marker = json.loads(marker_json)
    allowed = json.loads(allowed_json)
    denied = json.loads(denied_json)
    os.makedirs(root, exist_ok=True)
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, directory_name))
    if os.path.commonpath([root_real, target]) != root_real:
        raise SystemExit("target escaped managed root")
    marker_path = os.path.join(target, ".dms-resource.json")
    created = False
    if os.path.exists(target):
        if not os.path.isdir(target):
            raise SystemExit("target exists but is not a directory")
        if not os.path.exists(marker_path):
            raise SystemExit("target directory exists without DMS marker")
        with open(marker_path, encoding="utf-8") as handle:
            existing = json.load(handle)
        if existing.get("resource_key") != marker.get("resource_key"):
            raise SystemExit("target directory marker resource key mismatch")
    else:
        os.mkdir(target)
        created = True
    with open(marker_path, "w", encoding="utf-8") as handle:
        json.dump(marker, handle, sort_keys=True)
    group = grp.getgrnam(group_name)
    os.chown(target, -1, group.gr_gid)
    os.chown(marker_path, -1, group.gr_gid)
    os.chmod(target, int(mode_text, 8))
    access = {"allowed_users": {}, "denied_users": {}}
    for user in allowed:
        probe = os.path.join(target, f".access-allowed-{user}")
        completed = subprocess.run(
            ["sudo", "-u", user, "sh", "-c", "touch \"$1\" && rm \"$1\"", "sh", probe],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit(f"allowed user access failed for {user}: {completed.stderr.strip()}")
        access["allowed_users"][user] = "ok"
    for user in denied:
        completed = subprocess.run(
            ["sudo", "-u", user, "sh", "-c", "test ! -x \"$1\" && test ! -w \"$1\"", "sh", target],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit(f"denied user unexpectedly has access: {user}")
        access["denied_users"][user] = "denied"
    stat_result = os.stat(target)
    print(json.dumps({
        "path": target,
        "exists": True,
        "created": created,
        "owner_uid": stat_result.st_uid,
        "group_gid": stat_result.st_gid,
        "group_name": group_name,
        "mode": oct(stat_result.st_mode & 0o777)[2:].zfill(4),
        "marker": marker,
        "access_validation": access,
        "verified": True,
    }, sort_keys=True))
    """
)


_DELETE_DIRECTORY_SCRIPT = textwrap.dedent(
    r"""
    import json
    import os
    import shutil
    import sys

    root, directory_name, resource_key = sys.argv[1:]
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, directory_name))
    if os.path.commonpath([root_real, target]) != root_real:
        raise SystemExit("target escaped managed root")
    marker_path = os.path.join(target, ".dms-resource.json")
    if not os.path.isdir(target):
        raise SystemExit("target directory missing")
    if not os.path.exists(marker_path):
        raise SystemExit("target directory exists without DMS marker")
    with open(marker_path, encoding="utf-8") as handle:
        marker = json.load(handle)
    if marker.get("resource_key") != resource_key:
        raise SystemExit("target directory marker resource key mismatch")
    allowed_files = {".dms-resource.json"}
    for dirpath, dirnames, filenames in os.walk(target):
        if dirnames:
            raise SystemExit("safe delete refuses nested directories")
        for filename in filenames:
            if filename in allowed_files or filename.startswith(".access-allowed-"):
                continue
            raise SystemExit(f"safe delete refuses unexpected file: {filename}")
    shutil.rmtree(target)
    print(json.dumps({
        "path": target,
        "exists": False,
        "deleted": True,
        "marker": marker,
        "verified": True,
    }, sort_keys=True))
    """
)


_BLOCK_DIRECTORY_SCRIPT = textwrap.dedent(
    r"""
    import grp
    import json
    import os
    import pwd
    import sys

    root, directory_name, resource_key, block_mode, reason, request_id, prior_json = sys.argv[1:]
    if block_mode != "permission-zero":
        raise SystemExit("unsupported block mode")
    prior_block_state = json.loads(prior_json or "{}")
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, directory_name))
    if os.path.commonpath([root_real, target]) != root_real:
        raise SystemExit("target escaped managed root")
    marker_path = os.path.join(target, ".dms-resource.json")
    if not os.path.isdir(target):
        raise SystemExit("target directory missing")
    if not os.path.exists(marker_path):
        raise SystemExit("target directory exists without DMS marker")
    with open(marker_path, encoding="utf-8") as handle:
        marker = json.load(handle)
    if marker.get("resource_key") != resource_key:
        raise SystemExit("target directory marker resource key mismatch")
    stat_before = os.stat(target)
    mode_before = oct(stat_before.st_mode & 0o777)[2:].zfill(4)
    already_blocked = mode_before == "0000" and prior_block_state.get("blocked")
    restore = prior_block_state.get("restore") or {}
    if not already_blocked:
        try:
            owner = pwd.getpwuid(stat_before.st_uid).pw_name
        except KeyError:
            owner = str(stat_before.st_uid)
        try:
            group = grp.getgrgid(stat_before.st_gid)
            group_name = group.gr_name
        except KeyError:
            group_name = str(stat_before.st_gid)
        restore = {
            "owner": owner,
            "uid": stat_before.st_uid,
            "group_name": group_name,
            "gid": stat_before.st_gid,
            "mode": mode_before,
        }
        os.chmod(target, 0)
    stat_after = os.stat(target)
    block_state = {
        "blocked": True,
        "block_mode": block_mode,
        "blocked_by_request_id": request_id,
        "reason": reason or None,
        "restore": restore,
    }
    print(json.dumps({
        "path": target,
        "exists": True,
        "marker": marker,
        "mode_before": mode_before,
        "mode": oct(stat_after.st_mode & 0o777)[2:].zfill(4),
        "already_blocked": bool(already_blocked),
        "block_state": block_state,
        "blocked": True,
        "verified": (stat_after.st_mode & 0o777) == 0,
        "backend_side_effect": not already_blocked,
    }, sort_keys=True))
    """
)


_UNBLOCK_DIRECTORY_SCRIPT = textwrap.dedent(
    r"""
    import grp
    import json
    import os
    import subprocess
    import sys

    root, directory_name, resource_key, block_state_json, allowed_json, denied_json = sys.argv[1:]
    block_state = json.loads(block_state_json or "{}")
    allowed = json.loads(allowed_json)
    denied = json.loads(denied_json)
    restore = block_state.get("restore") or block_state.get("restore_state") or {}
    if not restore:
        raise SystemExit("filesystem block restore state missing")
    group_name = restore.get("group_name")
    mode_text = str(restore.get("mode") or "")
    if not group_name:
        raise SystemExit("filesystem access group missing from restore state")
    if mode_text not in {"0750", "0770"}:
        raise SystemExit("filesystem restore mode unsupported")
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, directory_name))
    if os.path.commonpath([root_real, target]) != root_real:
        raise SystemExit("target escaped managed root")
    marker_path = os.path.join(target, ".dms-resource.json")
    if not os.path.isdir(target):
        raise SystemExit("target directory missing")
    if not os.path.exists(marker_path):
        raise SystemExit("target directory exists without DMS marker")
    with open(marker_path, encoding="utf-8") as handle:
        marker = json.load(handle)
    if marker.get("resource_key") != resource_key:
        raise SystemExit("target directory marker resource key mismatch")
    group = grp.getgrnam(group_name)
    os.chown(target, -1, group.gr_gid)
    os.chown(marker_path, -1, group.gr_gid)
    os.chmod(target, int(mode_text, 8))
    access = {"allowed_users": {}, "denied_users": {}}
    for user in allowed:
        probe = os.path.join(target, f".access-unblocked-{user}")
        completed = subprocess.run(
            ["sudo", "-u", user, "sh", "-c", "touch \"$1\" && rm \"$1\"", "sh", probe],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit(f"allowed user access failed for {user}: {completed.stderr.strip()}")
        access["allowed_users"][user] = "ok"
    for user in denied:
        completed = subprocess.run(
            ["sudo", "-u", user, "sh", "-c", "test ! -x \"$1\" && test ! -w \"$1\"", "sh", target],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise SystemExit(f"denied user unexpectedly has access: {user}")
        access["denied_users"][user] = "denied"
    stat_after = os.stat(target)
    restored_block_state = {
        "blocked": False,
        "block_mode": block_state.get("block_mode", "permission-zero"),
        "restore": restore,
    }
    print(json.dumps({
        "path": target,
        "exists": True,
        "marker": marker,
        "group_name": group_name,
        "group_gid": group.gr_gid,
        "mode": oct(stat_after.st_mode & 0o777)[2:].zfill(4),
        "block_state": restored_block_state,
        "blocked": False,
        "access_validation": access,
        "verified": True,
        "backend_side_effect": True,
    }, sort_keys=True))
    """
)
