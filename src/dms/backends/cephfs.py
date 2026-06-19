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


class HostExecutionError(RuntimeError):
    pass


class FilesystemHostExecutor(Protocol):
    def create_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
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

    def apply_quota(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        quota: dict[str, int],
    ) -> dict[str, Any]: ...

    def apply_owner(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        owner_username: str,
    ) -> dict[str, Any]: ...

    def read_directory_state(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        include_quota: bool,
    ) -> dict[str, Any]: ...

    def assign_quota_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        quota: dict[str, int],
    ) -> dict[str, Any]: ...

    def import_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        chown_gid: int | None,
        quota: dict[str, int] | None,
    ) -> dict[str, Any]: ...


def _pick_rm_worker(mapping: dict[str, Any], rm_workers: list[str]) -> str:
    """ssh_host 미지정 시 agent 보고 기반 rm_candidates 중 Ready 노드를 선택한다.

    sanity_result가 없거나 rm_candidates가 비어 있으면 rm_workers[0]으로 fallback.
    """
    candidates = (mapping.get("sanity_result") or {}).get("agent_observed", {}).get(
        "rm_candidates"
    ) or []
    for c in candidates:
        if isinstance(c, dict) and c.get("status") == "Ready" and c.get("node_name"):
            return c["node_name"]
    return rm_workers[0] if rm_workers else ""


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
        rm_worker_node = template.get("ssh_host") or _pick_rm_worker(
            mapping, rm_workers
        )
        mount_path = template.get("mount_path", "")
        # managed_root is mandatory at registration (validate_filesystem_managed_root);
        # there is no implicit {mount_path}/dms fallback. Empty only if a pre-migration
        # row slips through -- the runtime managed_root guard then rejects it.
        managed_root = template.get("managed_root") or ""
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
        group_name: str,
        mode: str,
        allowed_users: list[str],
        denied_users: list[str],
        owner_username: str | None = None,
    ) -> dict[str, Any]:
        return self._run_script(
            _CREATE_DIRECTORY_SCRIPT,
            [
                managed_root,
                directory_name,
                group_name,
                mode,
                json.dumps(allowed_users),
                json.dumps(denied_users),
                owner_username or "",
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

    def apply_quota(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        quota: dict[str, int],
    ) -> dict[str, Any]:
        return self._run_script(
            _APPLY_QUOTA_SCRIPT,
            [
                managed_root,
                directory_name,
                resource_key,
                json.dumps(quota, sort_keys=True),
            ],
        )

    def apply_owner(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        owner_username: str,
    ) -> dict[str, Any]:
        return self._run_script(
            _APPLY_OWNER_SCRIPT,
            [
                managed_root,
                directory_name,
                resource_key,
                owner_username,
            ],
        )

    def read_directory_state(
        self,
        *,
        managed_root: str,
        directory_name: str,
        resource_key: str,
        include_quota: bool,
    ) -> dict[str, Any]:
        return self._run_script(
            _READ_DIRECTORY_STATE_SCRIPT,
            [
                managed_root,
                directory_name,
                resource_key,
                "true" if include_quota else "false",
            ],
        )

    def assign_quota_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        quota: dict[str, int],
    ) -> dict[str, Any]:
        return self._run_script(
            _ASSIGN_QUOTA_DIRECTORY_SCRIPT,
            [
                managed_root,
                directory_name,
                json.dumps(quota, sort_keys=True),
            ],
        )

    def import_directory(
        self,
        *,
        managed_root: str,
        directory_name: str,
        chown_gid: int | None,
        quota: dict[str, int] | None,
    ) -> dict[str, Any]:
        return self._run_script(
            _IMPORT_DIRECTORY_SCRIPT,
            [
                managed_root,
                directory_name,
                "" if chown_gid is None else str(int(chown_gid)),
                json.dumps(quota or {}, sort_keys=True),
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
            raise HostExecutionError(
                f"filesystem host execution timed out: {exc}"
            ) from exc
        if completed.returncode != 0:
            error_text = completed.stderr.strip() or completed.stdout.strip()
            sentinel = "PRECONDITION:"
            if sentinel in error_text:
                # Pre-side-effect refusal from the host script: classify as a
                # precondition failure (-> BackendApplyFailed) instead of letting it
                # fall through to the worker's generic UnknownAfterSideEffect handler.
                message = error_text.split(sentinel, 1)[1].strip()
                raise BackendPreconditionError(
                    message or "filesystem host precondition failed"
                )
            raise HostExecutionError(
                "filesystem host execution failed: " + error_text
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
            identity_groups=identity_groups
            or LdapIdentityGroupManager.from_settings(settings),
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
        if len(users) < 1:
            raise BackendPreconditionError(
                "filesystem create requires at least one user"
            )
        denied_users = _string_list(
            desired.get("denied_users") or desired.get("validation_denied_users") or [],
            "denied_users",
            required=False,
        )
        group_name = desired.get("access_group") or f"dms-grp-{directory_name}"
        validate_storage_root_basename("access_group", group_name)
        if not group_name.startswith("dms-"):
            raise BackendPreconditionError(
                "DMS-managed filesystem access groups must start with 'dms-'"
            )
        mode = str(desired.get("mode", "0750"))
        _validate_mode(mode)
        try:
            group = self.identity_groups.ensure_group_members(
                group_name=group_name,
                users=users,
                resource_key=plan["resource_key"],
            )
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(str(exc)) from exc
        owner_username = desired.get("owner_username")
        observed = self.executor.create_directory(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            group_name=group_name,
            mode=mode,
            allowed_users=users,
            denied_users=denied_users,
            owner_username=owner_username,
        )
        quota_state: dict[str, Any] | None = None
        quota = _normalize_quota(desired.get("quota"))
        if quota:
            quota_observed = self.executor.apply_quota(
                managed_root=self.template.managed_root,
                directory_name=directory_name,
                resource_key=plan["resource_key"],
                quota=quota,
            )
            quota_state = quota_observed.get("quota_state")
            observed["quota_apply"] = quota_observed
            observed["quota_state"] = quota_state
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
            "owner_username": owner_username,
            "owner_uid": observed.get("owner_uid"),
            "access_group": {
                "group_name": group_name,
                "gid": group.get("gid"),
                "dn": group.get("dn"),
                "members": group.get("members", users),
            },
            "expires_at": desired.get("expires_at"),
        }
        if quota:
            applied["quota"] = quota
            applied["quota_state"] = quota_state
        return AdapterResult(
            applied_state=applied,
            observed_state=observed,
            message="CephFS host-mounted filesystem create completed",
        )

    def delete(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        if desired.get("management_mode") == "quota_only" or desired.get("import_mode"):
            raise BackendPreconditionError(
                "CephFS delete refused for imported/quota-only directory"
            )
        directory_name = desired["directory_name"]
        validate_storage_root_basename("directory_name", directory_name)
        group_name = desired.get("access_group") or f"dms-grp-{directory_name}"
        # Soft delete (parity with GPFS): lock the directory (chown root:root +
        # chmod 000) and preserve data. The directory/data stays on the filesystem,
        # accessible only by root; permanent removal is a manual operator step.
        observed = self.executor.delete_directory(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            resource_key=plan["resource_key"],
        )
        group_cleanup: dict = {}
        if group_name.startswith("dms-"):
            group_cleanup = self.identity_groups.delete_group(group_name=group_name)
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "resource_status": "Deleted",
                "soft_delete": True,
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
                "soft_delete": True,
                "data_preserved": observed.get("data_preserved", True),
                "access_group_cleanup": group_cleanup,
            },
            observed_state=observed,
            message="CephFS host-mounted filesystem soft-deleted (data preserved, directory locked)",
        )

    def update(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        validate_storage_root_basename("directory_name", directory_name)
        # Honor the planner's per-field flags so a PATCH applies exactly what the
        # caller changed (quota, owner, or both). Legacy plans (pre-flag) carried a
        # quota with no flags; preserve the old quota-only behavior for those.
        has_flags = (
            "update_apply_quota" in desired or "update_apply_owner" in desired
        )
        apply_quota = (
            bool(desired.get("update_apply_quota")) if has_flags else True
        )
        apply_owner = bool(desired.get("update_apply_owner"))
        if not apply_quota and not apply_owner:
            raise BackendPreconditionError(
                "filesystem update requires a quota or owner change"
            )

        quota = _normalize_quota(desired.get("quota"))
        observed: dict[str, Any] = {}
        quota_state: dict[str, Any] | None = None
        if apply_quota:
            if not quota:
                raise BackendPreconditionError(
                    "filesystem quota is required for update"
                )
            observed = self.executor.apply_quota(
                managed_root=self.template.managed_root,
                directory_name=directory_name,
                resource_key=plan["resource_key"],
                quota=quota,
            )
            quota_state = observed.get("quota_state")

        # Owner change. CephFS resolves the owner on the backend node via NSS/SSSD
        # (pwd.getpwnam) — the same path as create — not via the LDAP adapter. The
        # host script refuses an unresolvable owner BEFORE chown (-> precondition),
        # so this is fail-closed (BackendApplyFailed -> filesystem_owner_unresolved).
        owner_change: dict[str, Any] = {}
        if apply_owner:
            owner_username = desired.get("update_owner_username")
            if not owner_username:
                raise BackendPreconditionError("update_owner_username missing")
            owner_observed = self.executor.apply_owner(
                managed_root=self.template.managed_root,
                directory_name=directory_name,
                resource_key=plan["resource_key"],
                owner_username=str(owner_username),
            )
            owner_change = {
                "owner_username": owner_username,
                "uid": owner_observed.get("owner_uid"),
            }
            # When quota was not (re)applied, the owner script's directory state is
            # the only observed snapshot we have for this update.
            if not observed:
                observed = dict(owner_observed)
            else:
                observed["owner_state"] = owner_observed

        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "operation": "update",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "owner_change": owner_change,
                "backend_side_effect": True,
                "resource_status": (
                    desired.get("resource_status", "Blocked")
                    if desired.get("block_state", {}).get("blocked")
                    else "Succeeded"
                ),
            }
        )
        synced_desired = dict(desired)
        if apply_quota and quota:
            synced_desired["quota"] = quota
        applied: dict[str, Any] = {
            "adapter": "cephfs-host-mounted",
            "operation": "update",
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "path": observed.get("path"),
            "owner_change": owner_change,
            "synced_desired_state": synced_desired,
        }
        if apply_quota and quota:
            applied["quota"] = quota
            applied["quota_state"] = quota_state
        if apply_owner and apply_quota:
            message = "CephFS host-mounted filesystem quota + owner update completed"
        elif apply_owner:
            message = "CephFS host-mounted filesystem owner update completed"
        else:
            message = "CephFS host-mounted filesystem quota update completed"
        return AdapterResult(
            applied_state=applied,
            observed_state=observed,
            message=message,
        )

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
        raise ValueError("filesystem initialize is unsupported")

    def consistency_check(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        observed = self._read_directory_state(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            resource_key=plan["resource_key"],
            include_quota=bool(desired.get("include_quota", True)),
        )
        issues = _quota_check_issues(desired, observed)
        status = "ActionRequired" if issues else "Consistent"
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "operation": "consistency_check",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "quota_status": status,
                "issues": issues,
                "resource_status": desired.get("resource_status", "Succeeded"),
            }
        )
        return AdapterResult(
            applied_state={
                "adapter": "cephfs-host-mounted",
                "operation": "consistency_check",
                "backend": self.template.metadata(),
                "directory_name": directory_name,
            },
            observed_state=observed,
            message="CephFS host-mounted filesystem consistency check completed",
        )

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        observed = self._read_directory_state(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            resource_key=plan["resource_key"],
            include_quota=bool(desired.get("include_quota", True)),
        )
        synced_desired = dict(desired)
        synced_quota = _quota_from_state(observed.get("quota_state") or {})
        if synced_quota:
            synced_desired["quota"] = synced_quota
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "operation": "sync_live_state",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "quota_status": "Synced",
                "issues": [],
                "resource_status": desired.get("resource_status", "Succeeded"),
            }
        )
        return AdapterResult(
            applied_state={
                "adapter": "cephfs-host-mounted",
                "operation": "sync_live_state",
                "backend": self.template.metadata(),
                "directory_name": directory_name,
                "quota": synced_quota,
                "quota_state": observed.get("quota_state"),
                "synced_desired_state": synced_desired,
            },
            observed_state=observed,
            message="CephFS host-mounted filesystem sync completed",
        )

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        validate_storage_root_basename("directory_name", directory_name)
        access_policy = desired.get("access_policy") or {}
        denied_users = _string_list(
            access_policy.get("denied_users")
            or desired.get("denied_users")
            or desired.get("validation_denied_users")
            or [],
            "denied_users",
            required=False,
        )
        quota = _normalize_quota(desired.get("quota"))

        # Discover the live directory + its group via stat (parity with GPFS/WekaFS):
        # access_policy is optional — the group is auto-discovered, not required.
        live = self._read_directory_state(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            resource_key="",
            include_quota=False,
        )
        if not live.get("exists"):
            raise BackendPreconditionError(
                "CephFS import: target directory does not exist"
            )
        # Optional hints are validated only when provided.
        expected_group = access_policy.get("expected_group")
        if expected_group and live.get("group_name") != expected_group:
            raise BackendPreconditionError(
                f"CephFS import: live group {live.get('group_name')!r} does not "
                f"match expected_group {expected_group!r}"
            )
        expected_mode = access_policy.get("expected_mode")
        if expected_mode and live.get("mode") != str(expected_mode):
            raise BackendPreconditionError(
                f"CephFS import: live mode {live.get('mode')!r} does not match "
                f"expected_mode {expected_mode!r}"
            )

        adoption = self._adopt_full_group(plan, directory_name, live)
        users = _string_list(
            access_policy.get("users")
            or desired.get("users")
            or adoption.get("members")
            or [],
            "users",
        )

        observed = self.executor.import_directory(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            chown_gid=adoption.get("chown_gid"),
            quota=quota or None,
        )
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "operation": "import",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "management_mode": "full",
                "resource_status": "Succeeded",
                "group_name": adoption.get("group_name"),
                "group_gid": adoption.get("gid"),
                "group_adoption": adoption.get("summary", {}),
            }
        )
        synced_desired = dict(desired)
        synced_desired["management_mode"] = "full"
        synced_desired["users"] = users
        if adoption.get("group_name"):
            synced_desired["access_group"] = adoption["group_name"]
        if denied_users:
            synced_desired["validation_denied_users"] = denied_users
        if quota:
            synced_desired["quota"] = quota
        return AdapterResult(
            applied_state={
                "adapter": "cephfs-host-mounted",
                "operation": "import",
                "backend": self.template.metadata(),
                "directory_name": directory_name,
                "path": observed.get("path"),
                "quota": quota,
                "quota_state": observed.get("quota_state"),
                "group_adoption": adoption.get("summary", {}),
                "synced_desired_state": synced_desired,
            },
            observed_state=observed,
            message="CephFS host-mounted filesystem import completed",
        )

    def _adopt_full_group(
        self, plan: dict[str, Any], directory_name: str, live: dict[str, Any]
    ) -> dict[str, Any]:
        """Discover the imported directory's live group and adopt or recreate it.

        Mirrors GPFS/WekaFS import adoption:
          - live group `dms-grp-*`  -> adopt as-is (pull members from LDAP).
          - external (other) group -> create `dms-grp-{dir}`, copy the external
            group's members into it, and chown the directory to the new gid.
        """
        if self.identity_groups is None:
            return {"changed_group": False, "chown_gid": None, "summary": {}}
        live_gid = live.get("group_gid")
        live_group_name = live.get("group_name")
        # If NSS could not resolve the gid to a name, fall back to LDAP by gid.
        if not live_group_name or live_group_name == str(live_gid):
            try:
                resolved = self.identity_groups.lookup_group_name_by_gid(gid=live_gid)
            except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
                raise BackendPreconditionError(str(exc)) from exc
            live_group_name = resolved or live_group_name
        if not live_group_name or live_group_name == str(live_gid):
            raise BackendPreconditionError(
                f"CephFS import: live group (gid={live_gid}) not found in NSS or LDAP"
            )
        try:
            members = sorted(
                set(
                    self.identity_groups.list_group_members(
                        group_name=live_group_name
                    )
                    or []
                )
            )
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(str(exc)) from exc
        if live_group_name.startswith("dms-grp-"):
            return {
                "group_name": live_group_name,
                "gid": live_gid,
                "members": members,
                "changed_group": False,
                "chown_gid": None,
                "summary": {
                    "live_group": live_group_name,
                    "live_gid": live_gid,
                    "adopted_members": members,
                    "changed_group": False,
                    "mode": "adopt_existing_dms_group",
                },
            }
        new_group_name = f"dms-grp-{directory_name}"
        try:
            new_group = self.identity_groups.ensure_group_members(
                group_name=new_group_name,
                users=members,
                resource_key=plan["resource_key"],
            )
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(str(exc)) from exc
        new_gid = new_group.get("gid")
        return {
            "group_name": new_group_name,
            "gid": new_gid,
            "members": members,
            "changed_group": True,
            "chown_gid": new_gid,
            "summary": {
                "live_group": live_group_name,
                "live_gid": live_gid,
                "new_group": new_group_name,
                "new_gid": new_gid,
                "adopted_members": members,
                "changed_group": True,
                "mode": "create_dms_group_from_external",
            },
        }

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = desired["directory_name"]
        validate_storage_root_basename("directory_name", directory_name)
        quota = _normalize_quota(desired.get("quota"))
        if not quota:
            raise BackendPreconditionError("filesystem quota is required")
        observed = self.executor.assign_quota_directory(
            managed_root=self.template.managed_root,
            directory_name=directory_name,
            quota=quota,
        )
        observed.update(
            {
                "adapter": "cephfs-host-mounted",
                "operation": "assign_quota_only",
                "backend": self.template.metadata(),
                "resource_key": plan["resource_key"],
                "management_mode": "quota_only",
                "resource_status": "Succeeded",
            }
        )
        synced_desired = dict(desired)
        synced_desired["management_mode"] = "quota_only"
        synced_desired["quota"] = quota
        return AdapterResult(
            applied_state={
                "adapter": "cephfs-host-mounted",
                "operation": "assign_quota_only",
                "backend": self.template.metadata(),
                "directory_name": directory_name,
                "path": observed.get("path"),
                "quota": quota,
                "quota_state": observed.get("quota_state"),
                "synced_desired_state": synced_desired,
            },
            observed_state=observed,
            message="CephFS host-mounted filesystem quota assignment completed",
        )

    def _validate_template(self) -> None:
        if not self.template.managed_root:
            raise BackendPreconditionError(
                "CephFS storage mapping requires managed_root"
            )
        if not self.template.mount_path:
            raise BackendPreconditionError("CephFS storage mapping requires mount_path")
        if not self.template.rm_worker_node and self.executor is None:
            raise BackendPreconditionError(
                "CephFS storage mapping requires an RM worker node"
            )

    def _read_directory_state(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return self.executor.read_directory_state(**kwargs)
        except HostExecutionError as exc:
            raise BackendPreconditionError(str(exc)) from exc

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
        raise BackendPreconditionError("filesystem mode must be 0750 or 0770")


def _normalize_quota(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    quota: dict[str, int] = {}
    for key in ("capacity_bytes", "file_count"):
        if value.get(key) is not None:
            quota[key] = int(value[key])
    return quota


def _quota_from_state(quota_state: dict[str, Any]) -> dict[str, int]:
    quota: dict[str, int] = {}
    capacity = quota_state.get("capacity") or {}
    file_count = quota_state.get("file_count") or {}
    if capacity.get("observed_bytes") is not None:
        quota["capacity_bytes"] = int(capacity["observed_bytes"])
    if file_count.get("observed_count") is not None:
        quota["file_count"] = int(file_count["observed_count"])
    return quota


def _quota_check_issues(
    desired: dict[str, Any], observed: dict[str, Any]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not observed.get("exists", True):
        return [
            {
                "issue_type": "filesystem_quota_missing",
                "field": "directory",
                "reason": "missing",
            }
        ]
    desired_quota = _normalize_quota(desired.get("quota"))
    quota_state = observed.get("quota_state") or {}
    live_quota = _quota_from_state(quota_state)
    capacity_tolerance = 100 * 1024 * 1024
    for key, desired_value in desired_quota.items():
        live_value = live_quota.get(key)
        if live_value is None:
            issues.append(
                {
                    "issue_type": "filesystem_quota_missing",
                    "field": f"quota.{key}",
                    "reason": "missing",
                    "desired": desired_value,
                }
            )
            continue
        if key == "capacity_bytes":
            diff = int(live_value) - int(desired_value)
            drifted = diff < 0 or diff > capacity_tolerance
        else:
            drifted = int(live_value) != int(desired_value)
        if drifted:
            issues.append(
                {
                    "issue_type": "filesystem_quota_drifted",
                    "field": f"quota.{key}",
                    "reason": "quota_drifted",
                    "desired": desired_value,
                    "live": live_value,
                }
            )
    return issues


_CREATE_DIRECTORY_SCRIPT = textwrap.dedent(r"""
    import grp
    import json
    import os
    import pwd
    import subprocess
    import sys
    import time

    root, directory_name, group_name, mode_text, allowed_json, denied_json, owner_username = sys.argv[1:]
    allowed = json.loads(allowed_json)
    denied = json.loads(denied_json)

    def wait_command(command, message, timeout_seconds=60):
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0:
                return
            last_error = completed.stderr.strip() or completed.stdout.strip()
            time.sleep(3)
        raise SystemExit(f"{message}: {last_error}")

    def refresh_sssd_cache():
        for command in (["sss_cache", "-E"], ["/usr/sbin/sss_cache", "-E"]):
            try:
                subprocess.run(command, check=False, capture_output=True, text=True)
                return
            except FileNotFoundError:
                continue

    # Resolve the requesting owner's POSIX identity BEFORE any side effect so an
    # unresolvable requester is refused as a precondition (no mkdir). The owner must
    # exist as a POSIX user on the backend node; no uid-range restriction is imposed.
    owner_uid = -1
    if owner_username:
        try:
            owner_entry = pwd.getpwnam(owner_username)
        except KeyError:
            raise SystemExit(
                f"PRECONDITION: requester '{owner_username}' is not a resolvable POSIX user on the backend node"
            )
        owner_uid = owner_entry.pw_uid

    root_existed = os.path.isdir(root)
    os.makedirs(root, exist_ok=True)
    if not root_existed:
        # DMS-created managed root: world-traversable (0711) so arbitrary-uid resource
        # owners can reach their own 0750/0770 dirs, but NOT world-listable -- so the set
        # of resource directory names is not exposed. A pre-existing managed root keeps the
        # operator-chosen mode (provision it 0711; see install docs).
        os.chmod(root, 0o711)
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, directory_name))
    if os.path.commonpath([root_real, target]) != root_real:
        raise SystemExit("PRECONDITION: target escaped managed root")
    created = False
    if os.path.exists(target):
        if not os.path.isdir(target):
            raise SystemExit("PRECONDITION: target exists but is not a directory")
    else:
        os.mkdir(target)
        created = True
    # No on-disk marker is written: DMS tracks ownership via its database, not a
    # .dms-resource.json file inside the managed directory.
    group = grp.getgrnam(group_name)
    # Owner = the requesting user (owner_uid resolved above); -1 leaves the owner as
    # the creating root when no requester was supplied. Group = DMS access group.
    os.chown(target, owner_uid, group.gr_gid)
    os.chmod(target, int(mode_text, 8))
    refresh_sssd_cache()
    access = {"allowed_users": {}, "denied_users": {}}
    for user in allowed:
        probe = os.path.join(target, f".access-allowed-{user}")
        wait_command(
            ["sudo", "-u", user, "sh", "-c", "touch \"$1\" && rm \"$1\"", "sh", probe],
            f"allowed user access failed for {user}",
        )
        access["allowed_users"][user] = "ok"
    for user in denied:
        wait_command(
            ["sudo", "-u", user, "sh", "-c", "test ! -x \"$1\" && test ! -w \"$1\"", "sh", target],
            f"denied user unexpectedly has access: {user}",
        )
        access["denied_users"][user] = "denied"
    stat_result = os.stat(target)
    print(json.dumps({
        "path": target,
        "exists": True,
        "created": created,
        "owner_uid": stat_result.st_uid,
        "owner_username": owner_username or None,
        "group_gid": stat_result.st_gid,
        "group_name": group_name,
        "mode": oct(stat_result.st_mode & 0o777)[2:].zfill(4),
        "access_validation": access,
        "verified": True,
    }, sort_keys=True))
    """)


_DELETE_DIRECTORY_SCRIPT = textwrap.dedent(r"""
    import grp
    import json
    import os
    import pwd
    import sys

    root, directory_name, resource_key = sys.argv[1:]
    root_real = os.path.realpath(root)
    target = os.path.realpath(os.path.join(root_real, directory_name))
    if os.path.commonpath([root_real, target]) != root_real:
        raise SystemExit("PRECONDITION: target escaped managed root")
    if not os.path.isdir(target):
        # Idempotent: nothing to lock (already removed); report success.
        print(json.dumps({
            "path": target,
            "exists": False,
            "deleted": True,
            "soft_delete": True,
            "data_preserved": False,
            "verified": True,
        }, sort_keys=True))
        raise SystemExit(0)
    stat_before = os.stat(target)
    try:
        owner = pwd.getpwuid(stat_before.st_uid).pw_name
    except KeyError:
        owner = str(stat_before.st_uid)
    try:
        group_name = grp.getgrgid(stat_before.st_gid).gr_name
    except KeyError:
        group_name = str(stat_before.st_gid)
    prior_state = {
        "owner": owner,
        "uid": stat_before.st_uid,
        "group_name": group_name,
        "gid": stat_before.st_gid,
        "mode": oct(stat_before.st_mode & 0o777)[2:].zfill(4),
    }
    # Soft delete: lock down (chown root:root + chmod 000), preserve data.
    # No rmtree; permanent removal is a manual operator step.
    os.chown(target, 0, 0)
    os.chmod(target, 0)
    stat_after = os.stat(target)
    print(json.dumps({
        "path": target,
        "exists": True,
        "deleted": True,
        "soft_delete": True,
        "data_preserved": True,
        "prior_state": prior_state,
        "mode": oct(stat_after.st_mode & 0o777)[2:].zfill(4),
        "verified": (stat_after.st_mode & 0o777) == 0,
    }, sort_keys=True))
    """)


_BLOCK_DIRECTORY_SCRIPT = textwrap.dedent(r"""
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
        raise SystemExit("PRECONDITION: target escaped managed root")
    if not os.path.isdir(target):
        raise SystemExit("PRECONDITION: target directory missing")
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
        "mode_before": mode_before,
        "mode": oct(stat_after.st_mode & 0o777)[2:].zfill(4),
        "already_blocked": bool(already_blocked),
        "block_state": block_state,
        "blocked": True,
        "verified": (stat_after.st_mode & 0o777) == 0,
        "backend_side_effect": not already_blocked,
    }, sort_keys=True))
    """)


_UNBLOCK_DIRECTORY_SCRIPT = textwrap.dedent(r"""
    import grp
    import json
    import os
    import subprocess
    import sys
    import time

    root, directory_name, resource_key, block_state_json, allowed_json, denied_json = sys.argv[1:]
    block_state = json.loads(block_state_json or "{}")
    allowed = json.loads(allowed_json)
    denied = json.loads(denied_json)
    restore = block_state.get("restore") or block_state.get("restore_state") or {}

    def wait_command(command, message, timeout_seconds=60):
        deadline = time.monotonic() + timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0:
                return
            last_error = completed.stderr.strip() or completed.stdout.strip()
            time.sleep(3)
        raise SystemExit(f"{message}: {last_error}")

    def refresh_sssd_cache():
        for command in (["sss_cache", "-E"], ["/usr/sbin/sss_cache", "-E"]):
            try:
                subprocess.run(command, check=False, capture_output=True, text=True)
                return
            except FileNotFoundError:
                continue

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
        raise SystemExit("PRECONDITION: target escaped managed root")
    if not os.path.isdir(target):
        raise SystemExit("PRECONDITION: target directory missing")
    group = grp.getgrnam(group_name)
    os.chown(target, -1, group.gr_gid)
    os.chmod(target, int(mode_text, 8))
    refresh_sssd_cache()
    access = {"allowed_users": {}, "denied_users": {}}
    for user in allowed:
        probe = os.path.join(target, f".access-unblocked-{user}")
        wait_command(
            ["sudo", "-u", user, "sh", "-c", "touch \"$1\" && rm \"$1\"", "sh", probe],
            f"allowed user access failed for {user}",
        )
        access["allowed_users"][user] = "ok"
    for user in denied:
        wait_command(
            ["sudo", "-u", user, "sh", "-c", "test ! -x \"$1\" && test ! -w \"$1\"", "sh", target],
            f"denied user unexpectedly has access: {user}",
        )
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
        "group_name": group_name,
        "group_gid": group.gr_gid,
        "mode": oct(stat_after.st_mode & 0o777)[2:].zfill(4),
        "block_state": restored_block_state,
        "blocked": False,
        "access_validation": access,
        "verified": True,
        "backend_side_effect": True,
    }, sort_keys=True))
    """)


_APPLY_QUOTA_SCRIPT = textwrap.dedent(r"""
    import json
    import os
    import shutil
    import subprocess
    import sys
    import time

    root, directory_name, resource_key, quota_json = sys.argv[1:]
    quota = json.loads(quota_json or "{}")

    def fail(message):
        raise SystemExit(message)

    def run(command):
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            fail(completed.stderr.strip() or completed.stdout.strip() or "command failed")
        return completed.stdout.strip()

    def getx(path, name):
        completed = subprocess.run(["getfattr", "--only-values", "-n", name, path], check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return int(value) if value else None

    if not shutil.which("setfattr") or not shutil.which("getfattr"):
        fail("filesystem quota capability missing: setfattr/getfattr")
    root_real = os.path.realpath(root)
    target_joined = os.path.join(root_real, directory_name)
    if os.path.islink(target_joined):
        fail("target directory is symlink")
    target = os.path.realpath(target_joined)
    if os.path.commonpath([root_real, target]) != root_real:
        fail("PRECONDITION: target escaped managed root")
    if not os.path.isdir(target):
        fail("PRECONDITION: target directory missing")
    if quota.get("capacity_bytes") is not None:
        run(["setfattr", "-n", "ceph.quota.max_bytes", "-v", str(int(quota["capacity_bytes"])), target])
    if quota.get("file_count") is not None:
        run(["setfattr", "-n", "ceph.quota.max_files", "-v", str(int(quota["file_count"])), target])
    observed_capacity = getx(target, "ceph.quota.max_bytes")
    observed_files = getx(target, "ceph.quota.max_files")
    if quota.get("capacity_bytes") is not None and observed_capacity is not None:
        diff = int(observed_capacity) - int(quota["capacity_bytes"])
        if diff < 0 or diff > 100 * 1024 * 1024:
            fail("quota capacity read-back mismatch")
    if quota.get("file_count") is not None and observed_files != int(quota["file_count"]):
        fail("quota file-count read-back mismatch")
    quota_state = {
        "backend_type": "cephfs",
        "capacity": {
            "desired_bytes": quota.get("capacity_bytes"),
            "applied_bytes": quota.get("capacity_bytes"),
            "observed_bytes": observed_capacity,
            "backend_key": "ceph.quota.max_bytes",
        },
        "file_count": {
            "desired_count": quota.get("file_count"),
            "applied_count": quota.get("file_count"),
            "observed_count": observed_files,
            "backend_key": "ceph.quota.max_files",
        },
    }
    print(json.dumps({
        "path": target,
        "exists": True,
        "quota_state": quota_state,
        "quota_capability": {
            "supports_capacity_quota": True,
            "supports_file_count_quota": True,
            "supports_usage_bytes": False,
            "supports_file_count_usage": False,
            "quota_backend": "cephfs-xattr",
        },
        "backend_side_effect": True,
        "verified": True,
    }, sort_keys=True))
    """)


_APPLY_OWNER_SCRIPT = textwrap.dedent(r"""
    import grp
    import json
    import os
    import pwd
    import subprocess
    import sys

    root, directory_name, resource_key, owner_username = sys.argv[1:]

    def refresh_sssd_cache():
        for command in (["sss_cache", "-E"], ["/usr/sbin/sss_cache", "-E"]):
            try:
                subprocess.run(command, check=False, capture_output=True, text=True)
                return
            except FileNotFoundError:
                continue

    if not owner_username:
        raise SystemExit("PRECONDITION: owner_username is required for owner update")
    # Resolve the new owner's POSIX identity BEFORE any side effect; an unresolvable
    # owner is refused as a precondition (no chown). No uid-range restriction.
    refresh_sssd_cache()
    try:
        owner_entry = pwd.getpwnam(owner_username)
    except KeyError:
        raise SystemExit(
            f"PRECONDITION: owner '{owner_username}' is not a resolvable POSIX user on the backend node"
        )
    owner_uid = owner_entry.pw_uid

    root_real = os.path.realpath(root)
    target_joined = os.path.join(root_real, directory_name)
    if os.path.islink(target_joined):
        raise SystemExit("PRECONDITION: target directory is symlink")
    target = os.path.realpath(target_joined)
    if os.path.commonpath([root_real, target]) != root_real:
        raise SystemExit("PRECONDITION: target escaped managed root")
    if not os.path.isdir(target):
        raise SystemExit("PRECONDITION: target directory missing")
    # Change owner only; preserve the existing DMS access group (gid -1).
    os.chown(target, owner_uid, -1)
    stat_result = os.stat(target)
    try:
        group_name = grp.getgrgid(stat_result.st_gid).gr_name
    except KeyError:
        group_name = str(stat_result.st_gid)
    print(json.dumps({
        "path": target,
        "exists": True,
        "owner_uid": stat_result.st_uid,
        "owner_username": owner_username,
        "group_gid": stat_result.st_gid,
        "group_name": group_name,
        "mode": oct(stat_result.st_mode & 0o777)[2:].zfill(4),
        "backend_side_effect": True,
        "verified": True,
    }, sort_keys=True))
    """)


_READ_DIRECTORY_STATE_SCRIPT = textwrap.dedent(r"""
    import grp
    import json
    import os
    import shutil
    import subprocess
    import sys

    root, directory_name, resource_key, include_quota_text = sys.argv[1:]
    include_quota = include_quota_text == "true"

    def getx(path, name):
        if not shutil.which("getfattr"):
            return None
        completed = subprocess.run(
            ["getfattr", "--only-values", "-n", name, path],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return int(value) if value else None

    root_real = os.path.realpath(root)
    target_joined = os.path.join(root_real, directory_name)
    if os.path.islink(target_joined):
        raise SystemExit("target directory is symlink")
    target = os.path.realpath(target_joined)
    if os.path.commonpath([root_real, target]) != root_real:
        raise SystemExit("PRECONDITION: target escaped managed root")
    if not os.path.isdir(target):
        print(json.dumps({"path": target, "exists": False, "verified": False}, sort_keys=True))
        raise SystemExit(0)
    stat_result = os.stat(target)
    try:
        group_name = grp.getgrgid(stat_result.st_gid).gr_name
    except KeyError:
        group_name = str(stat_result.st_gid)
    quota_state = {}
    if include_quota:
        quota_state = {
            "backend_type": "cephfs",
            "capacity": {
                "observed_bytes": getx(target, "ceph.quota.max_bytes"),
                "backend_key": "ceph.quota.max_bytes",
            },
            "file_count": {
                "observed_count": getx(target, "ceph.quota.max_files"),
                "backend_key": "ceph.quota.max_files",
            },
        }
    print(json.dumps({
        "path": target,
        "exists": True,
        "owner_uid": stat_result.st_uid,
        "group_gid": stat_result.st_gid,
        "group_name": group_name,
        "mode": oct(stat_result.st_mode & 0o777)[2:].zfill(4),
        "quota_state": quota_state,
        "verified": True,
        "backend_side_effect": False,
    }, sort_keys=True))
    """)


_ASSIGN_QUOTA_DIRECTORY_SCRIPT = textwrap.dedent(r"""
    import json
    import os
    import shutil
    import subprocess
    import sys

    root, directory_name, quota_json = sys.argv[1:]
    quota = json.loads(quota_json or "{}")

    def fail(message):
        raise SystemExit(message)

    def run(command):
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            fail(completed.stderr.strip() or completed.stdout.strip() or "command failed")
        return completed.stdout.strip()

    def getx(path, name):
        completed = subprocess.run(["getfattr", "--only-values", "-n", name, path], check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return int(value) if value else None

    if not shutil.which("setfattr") or not shutil.which("getfattr"):
        fail("filesystem quota capability missing: setfattr/getfattr")
    root_real = os.path.realpath(root)
    target_joined = os.path.join(root_real, directory_name)
    if os.path.islink(target_joined):
        fail("target directory is symlink")
    target = os.path.realpath(target_joined)
    if os.path.commonpath([root_real, target]) != root_real:
        fail("PRECONDITION: target escaped managed root")
    if not os.path.isdir(target):
        fail("PRECONDITION: target directory missing")
    # No on-disk marker is written; DMS tracks quota_only management in its database.
    if quota.get("capacity_bytes") is not None:
        run(["setfattr", "-n", "ceph.quota.max_bytes", "-v", str(int(quota["capacity_bytes"])), target])
    if quota.get("file_count") is not None:
        run(["setfattr", "-n", "ceph.quota.max_files", "-v", str(int(quota["file_count"])), target])
    quota_state = {
        "backend_type": "cephfs",
        "capacity": {
            "desired_bytes": quota.get("capacity_bytes"),
            "applied_bytes": quota.get("capacity_bytes"),
            "observed_bytes": getx(target, "ceph.quota.max_bytes"),
            "backend_key": "ceph.quota.max_bytes",
        },
        "file_count": {
            "desired_count": quota.get("file_count"),
            "applied_count": quota.get("file_count"),
            "observed_count": getx(target, "ceph.quota.max_files"),
            "backend_key": "ceph.quota.max_files",
        },
    }
    print(json.dumps({
        "path": target,
        "exists": True,
        "management_mode": "quota_only",
        "quota_state": quota_state,
        "backend_side_effect": True,
        "verified": True,
    }, sort_keys=True))
    """)


_IMPORT_DIRECTORY_SCRIPT = textwrap.dedent(r"""
    import grp
    import json
    import os
    import shutil
    import subprocess
    import sys

    root, directory_name, chown_gid, quota_json = sys.argv[1:]
    quota = json.loads(quota_json or "{}")

    def fail(message):
        raise SystemExit(message)

    def refresh_sssd_cache():
        for command in (["sss_cache", "-E"], ["/usr/sbin/sss_cache", "-E"]):
            try:
                subprocess.run(command, check=False, capture_output=True, text=True)
                return
            except FileNotFoundError:
                continue

    def run(command):
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            fail(completed.stderr.strip() or completed.stdout.strip() or "command failed")
        return completed.stdout.strip()

    def getx(path, name):
        if not shutil.which("getfattr"):
            return None
        completed = subprocess.run(["getfattr", "--only-values", "-n", name, path], check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return int(value) if value else None

    root_real = os.path.realpath(root)
    target_joined = os.path.join(root_real, directory_name)
    if os.path.islink(target_joined):
        fail("PRECONDITION: target directory is symlink")
    target = os.path.realpath(target_joined)
    if os.path.commonpath([root_real, target]) != root_real:
        fail("PRECONDITION: target escaped managed root")
    if not os.path.isdir(target):
        fail("PRECONDITION: target directory missing")
    # External-group adoption: chown the directory to the new DMS group when asked.
    # (dms-grp-* directories are adopted as-is and need no chown.)
    if chown_gid:
        os.chown(target, -1, int(chown_gid))
        refresh_sssd_cache()
    if quota:
        if not shutil.which("setfattr") or not shutil.which("getfattr"):
            fail("filesystem quota capability missing: setfattr/getfattr")
        if quota.get("capacity_bytes") is not None:
            run(["setfattr", "-n", "ceph.quota.max_bytes", "-v", str(int(quota["capacity_bytes"])), target])
        if quota.get("file_count") is not None:
            run(["setfattr", "-n", "ceph.quota.max_files", "-v", str(int(quota["file_count"])), target])
    stat_result = os.stat(target)
    try:
        group_name = grp.getgrgid(stat_result.st_gid).gr_name
    except KeyError:
        group_name = str(stat_result.st_gid)
    quota_state = {
        "backend_type": "cephfs",
        "capacity": {"observed_bytes": getx(target, "ceph.quota.max_bytes"), "backend_key": "ceph.quota.max_bytes"},
        "file_count": {"observed_count": getx(target, "ceph.quota.max_files"), "backend_key": "ceph.quota.max_files"},
    }
    print(json.dumps({
        "path": target,
        "exists": True,
        "group_name": group_name,
        "group_gid": stat_result.st_gid,
        "mode": oct(stat_result.st_mode & 0o777)[2:].zfill(4),
        "quota_state": quota_state,
        "management_mode": "full",
        "verified": True,
        "backend_side_effect": bool(chown_gid or quota),
    }, sort_keys=True))
    """)
