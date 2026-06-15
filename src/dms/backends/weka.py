from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Protocol

from dms.adapters import (
    AdapterResult,
    BackendPreconditionError,
    IdentityGroupManager,
    IdentityLookupAdapter,
    IdentityLookupConfigurationError,
    IdentityLookupReadError,
    LdapIdentityGroupManager,
    LdapIdentityLookupAdapter,
    probe_filesystem_access,
)
from dms.config import Settings
from dms.domain import OperationKind, validate_storage_root_basename

WEKAFS_BACKEND_TYPE = "wekafs"
WEKAFS_CSI_DRIVER = "csi.weka.io"
WEKAFS_QUOTA_BACKEND = "weka-fs-quota"
WEKAFS_DIRECTORY_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class WekaFsSideEffectError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False


class WekaFsCommandExecutor(Protocol):
    def run(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> CommandResult: ...


@dataclass(frozen=True)
class LocalWekaFsCommandExecutor:
    def run(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        started = time.monotonic()
        merged_env = None
        if env:
            merged_env = {**os.environ, **env}
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                env=merged_env,
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                argv=argv,
                returncode=124,
                stdout=exc.stdout or "",
                stderr=exc.stderr or "command timed out",
                duration_seconds=time.monotonic() - started,
                timed_out=True,
            )
        return CommandResult(
            argv=argv,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            duration_seconds=time.monotonic() - started,
        )


@dataclass(frozen=True)
class SshWekaFsCommandExecutor:
    host: str
    local: LocalWekaFsCommandExecutor = LocalWekaFsCommandExecutor()

    def run(
        self,
        argv: list[str],
        *,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        # ssh의 SendEnv 의존을 피하기 위해 원격 셸 명령에 env 프리픽스로 주입.
        if env:
            remote = (
                "env "
                + " ".join(f"{shlex.quote(k)}={shlex.quote(v)}" for k, v in env.items())
                + " "
                + shlex.join(argv)
            )
        else:
            remote = shlex.join(argv)
        return self.local.run(
            ["ssh", self.host, remote],
            timeout_seconds=timeout_seconds,
        )


def _pick_rm_worker(mapping: dict[str, Any], rm_workers: list[str]) -> str | None:
    """ssh_host 미지정 시 agent 보고 기반 rm_candidates 중 Ready 노드를 선택한다."""
    candidates = (mapping.get("sanity_result") or {}).get("agent_observed", {}).get(
        "rm_candidates"
    ) or []
    for c in candidates:
        if isinstance(c, dict) and c.get("status") == "Ready" and c.get("node_name"):
            return c["node_name"]
    return rm_workers[0] if rm_workers else None


@dataclass(frozen=True)
class WekaCredentials:
    organization: str
    username: str
    password: str

    def env(self) -> dict[str, str]:
        return {
            "WEKA_ORG": self.organization,
            "WEKA_USERNAME": self.username,
            "WEKA_PASSWORD": self.password,
        }


@dataclass(frozen=True)
class WekaFsBackendTemplate:
    storage_name: str
    cluster_name: str | None
    filesystem_name: str
    mount_path: str
    managed_root: str
    csi_driver: str | None
    storage_class_name: str | None
    data_network: str | None
    rm_worker_node: str | None
    command_runner: str
    command_timeout_seconds: int
    weka_profile: str | None
    credentials: WekaCredentials | None

    @classmethod
    def from_storage_mapping(cls, mapping: dict[str, Any]) -> "WekaFsBackendTemplate":
        template = mapping["backend_template"]
        rm_workers = template.get("rm_worker_nodes") or []
        rm_worker_node = template.get("ssh_host") or _pick_rm_worker(
            mapping, rm_workers
        )
        mount_path = template.get("mount_path", "")
        managed_root = template.get("managed_root") or (
            f"{mount_path.rstrip('/')}/dms" if mount_path else ""
        )
        cred_dict = template.get("weka_credentials") or {}
        credentials: WekaCredentials | None = None
        if cred_dict.get("username") and cred_dict.get("password") is not None:
            credentials = WekaCredentials(
                organization=str(cred_dict.get("organization", "0")),
                username=str(cred_dict["username"]),
                password=str(cred_dict["password"]),
            )
        return cls(
            storage_name=mapping["storage_name"],
            cluster_name=template.get("cluster_name") or mapping.get("cluster_name"),
            filesystem_name=template.get("filesystem_name", mapping["storage_name"]),
            mount_path=mount_path,
            managed_root=managed_root,
            csi_driver=template.get("csi_driver"),
            storage_class_name=(
                template.get("storage_class_name") or mapping.get("storage_class_name")
            ),
            data_network=template.get("data_network"),
            rm_worker_node=rm_worker_node,
            command_runner=template.get("command_runner", "ssh-host-exec"),
            command_timeout_seconds=int(template.get("command_timeout_seconds", 60)),
            weka_profile=template.get("weka_profile"),
            credentials=credentials,
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "backend_type": WEKAFS_BACKEND_TYPE,
            "storage_name": self.storage_name,
            "cluster_name": self.cluster_name,
            "filesystem_name": self.filesystem_name,
            "mount_path": self.mount_path,
            "managed_root": self.managed_root,
            "csi_driver": self.csi_driver,
            "storage_class_name": self.storage_class_name,
            "data_network": self.data_network,
            "rm_worker_node": self.rm_worker_node,
            "command_runner": self.command_runner,
            "weka_profile": self.weka_profile,
            "weka_credentials_present": self.credentials is not None,
        }

    def executor(self) -> WekaFsCommandExecutor:
        if self.command_runner == "ssh-host-exec":
            if not self.rm_worker_node:
                raise BackendPreconditionError(
                    "WekaFS storage mapping requires ssh_host"
                )
            return SshWekaFsCommandExecutor(self.rm_worker_node)
        if self.command_runner == "local":
            return LocalWekaFsCommandExecutor()
        raise BackendPreconditionError(
            f"unsupported WekaFS command runner: {self.command_runner}"
        )


@dataclass(frozen=True)
class WekaFsQuotaStrategy:
    backend_type: str = WEKAFS_BACKEND_TYPE

    def render_quota(self, quota: dict[str, Any]) -> dict[str, Any]:
        capacity = quota.get("capacity_bytes")
        return {
            "backend_type": self.backend_type,
            "quota_backend": WEKAFS_QUOTA_BACKEND,
            "capacity_bytes": capacity,
            "hard_limit_bytes": quota.get("hard_limit_bytes", capacity),
            "soft_limit_bytes": quota.get("soft_limit_bytes", capacity),
            "command_family": "weka-fs-quota",
            "side_effect": "weka-command",
        }


@dataclass
class WekaFsHostMountedFilesystemBackendAdapter:
    """WEKA host-mounted filesystem backend.

    directory create/delete: 호스트 마운트 경로에서 mkdir/chmod/chgrp/rm
    quota: `weka fs quota set/list/reset` CLI (cluster 인증 필요)
    identity: LDAP IdentityGroupManager로 access_group 관리 (CephFS와 동일 패턴)

    NOTE: WEKA path quota는 inode/file_count 미지원 — file_count 지정 시 에러.
    """

    template: WekaFsBackendTemplate
    identity_groups: IdentityGroupManager
    quota_strategy: WekaFsQuotaStrategy = WekaFsQuotaStrategy()
    executor: WekaFsCommandExecutor | None = None
    identity_lookup: IdentityLookupAdapter | None = None

    @classmethod
    def from_storage_mapping(
        cls,
        mapping: dict[str, Any],
        settings: Settings,
        *,
        identity_groups: IdentityGroupManager | None = None,
        executor: WekaFsCommandExecutor | None = None,
        identity_lookup: IdentityLookupAdapter | None = None,
    ) -> "WekaFsHostMountedFilesystemBackendAdapter":
        template = WekaFsBackendTemplate.from_storage_mapping(mapping)
        resolved_identity_lookup = identity_lookup
        if resolved_identity_lookup is None:
            try:
                resolved_identity_lookup = LdapIdentityLookupAdapter.from_settings(
                    settings
                )
            except IdentityLookupConfigurationError:
                resolved_identity_lookup = None
        return cls(
            template=template,
            identity_groups=identity_groups
            or LdapIdentityGroupManager.from_settings(settings),
            executor=executor or template.executor(),
            identity_lookup=resolved_identity_lookup,
        )

    def create(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        target = self._directory_path(directory_name)
        users = _string_list(desired.get("users"), "users")
        if len(users) < 1:
            raise BackendPreconditionError(
                "filesystem create requires at least one user"
            )
        group_name = desired.get("access_group") or f"dms-grp-{directory_name}"
        validate_storage_root_basename("access_group", group_name)
        if not group_name.startswith("dms-"):
            raise BackendPreconditionError(
                "DMS-managed filesystem access groups must start with 'dms-'"
            )
        mode = str(desired.get("mode") or "0750")
        evidence: list[dict[str, Any]] = []
        quota = desired.get("quota") or {}
        _reject_unsupported_quota_fields(quota)
        self._capability(evidence, require_quota=bool(quota))
        try:
            group = self.identity_groups.ensure_group_members(
                group_name=group_name,
                users=users,
                resource_key=plan["resource_key"],
            )
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(str(exc)) from exc

        # Resolve the directory owner BEFORE any side effect so an unresolvable owner
        # fails closed (no mkdir), in parity with CephFS.
        owner_uid = self._resolve_owner_uid(plan, desired)

        side_effect_started = False
        try:
            self._run(["mkdir", "-p", target], evidence, side_effect=True)
            side_effect_started = True
            self._run(["sss_cache", "-E"], evidence, fail=False, side_effect=False)
            gid = group.get("gid")
            if owner_uid is not None and gid is not None:
                self._run(
                    ["chown", f"{owner_uid}:{gid}", target], evidence, side_effect=True
                )
            elif owner_uid is not None:
                self._run(["chown", str(owner_uid), target], evidence, side_effect=True)
            elif gid is not None:
                self._run(["chown", f":{gid}", target], evidence, side_effect=True)
            else:
                self._run(["chgrp", group_name, target], evidence, side_effect=True)
            self._run(["chmod", mode, target], evidence, side_effect=True)
            quota_state: dict[str, Any] = {}
            if quota:
                quota_state = self._apply_quota(target, quota, evidence)
        except BackendPreconditionError as exc:
            if side_effect_started:
                raise WekaFsSideEffectError(str(exc)) from exc
            raise

        # Refresh SSSD cache and probe access (non-fatal)
        self._run(["sss_cache", "-E"], evidence, fail=False, side_effect=False)
        denied_users = list(
            desired.get("denied_users") or desired.get("validation_denied_users") or []
        )
        access_validation = probe_filesystem_access(
            run_cmd=lambda argv: self._run(
                argv, evidence, fail=False, side_effect=False
            ).returncode,
            path=target,
            allowed_users=users,
            denied_users=denied_users,
        )

        applied = {
            "adapter": "wekafs-host-mounted",
            "operation": OperationKind.FILESYSTEM_CREATE.value,
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "path": target,
            "access_group": {
                "group_name": group_name,
                "gid": group.get("gid"),
                "dn": group.get("dn"),
                "members": group.get("members", users),
            },
            "quota": quota,
            "quota_state": quota_state,
            "command_evidence": evidence,
            "backend_side_effect": True,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "exists": True,
            "verified": True,
            "management_mode": "full",
            "group_name": group_name,
            "group_gid": group.get("gid"),
            "ldap_group_dn": group.get("dn"),
            "ldap_members": group.get("members", users),
            "access_validation": access_validation,
        }
        return AdapterResult(applied, observed, "WekaFS directory create completed")

    def update(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        target = self._directory_path(directory_name)
        apply_quota = bool(desired.get("update_apply_quota"))
        apply_owner = bool(desired.get("update_apply_owner"))
        quota = desired.get("quota") or {} if apply_quota else {}
        if apply_quota:
            _reject_unsupported_quota_fields(quota)
        evidence: list[dict[str, Any]] = []
        owner_change: dict[str, Any] = {}
        quota_state: dict[str, Any] = {}
        if apply_quota:
            self._capability(evidence, require_quota=True)
            quota_state = self._apply_quota(target, quota, evidence)
        if apply_owner:
            owner_username = desired.get("update_owner_username")
            if not owner_username:
                raise BackendPreconditionError("update_owner_username missing")
            if self.identity_lookup is None:
                raise BackendPreconditionError(
                    "WekaFS owner update requires LDAP identity lookup"
                )
            try:
                result = self.identity_lookup.lookup("ldap", str(owner_username))
            except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
                raise BackendPreconditionError(str(exc)) from exc
            if not result or result.uid is None:
                raise BackendPreconditionError(
                    f"LDAP user not found for owner update: {owner_username}"
                )
            self._run(["sss_cache", "-E"], evidence, fail=False, side_effect=False)
            self._run(["chown", str(result.uid), target], evidence, side_effect=True)
            owner_change = {"owner_username": owner_username, "uid": int(result.uid)}
        applied = {
            "adapter": "wekafs-host-mounted",
            "operation": OperationKind.FILESYSTEM_UPDATE.value,
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "path": target,
            "quota": quota,
            "quota_state": quota_state,
            "owner_change": owner_change,
            "command_evidence": evidence,
            "backend_side_effect": bool(apply_quota or apply_owner),
        }
        observed = {**applied, "resource_key": plan["resource_key"], "verified": True}
        return AdapterResult(applied, observed, "WekaFS update completed")

    def block(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        target = self._directory_path(directory_name)
        block = bool(desired.get("block"))
        mode = (
            "0000"
            if block
            else str(
                (desired.get("block_state") or {}).get("previous_mode")
                or desired.get("mode")
                or "0750"
            )
        )
        evidence: list[dict[str, Any]] = []
        self._run(["chmod", mode, target], evidence, side_effect=True)
        block_state = dict(desired.get("block_state") or {})
        block_state.update(
            {"blocked": block, "block_mode": "chmod-0000" if block else "restored"}
        )
        applied = {
            "adapter": "wekafs-host-mounted",
            "operation": OperationKind.FILESYSTEM_BLOCK.value,
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "path": target,
            "block_state": block_state,
            "command_evidence": evidence,
            "backend_side_effect": True,
        }
        observed = {**applied, "resource_key": plan["resource_key"], "verified": True}
        return AdapterResult(applied, observed, "WekaFS directory block state updated")

    def initialize(self, plan: dict[str, Any]) -> AdapterResult:
        return self.consistency_check(plan)

    def delete(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        if desired.get("management_mode") == "quota_only" or desired.get("import_mode"):
            raise BackendPreconditionError(
                "WekaFS delete refused for imported/quota-only directory"
            )
        directory_name = _directory_name(desired)
        target = self._directory_path(directory_name)
        group_name = desired.get("access_group") or f"dms-grp-{directory_name}"
        evidence: list[dict[str, Any]] = []

        # Soft-delete: reset quota first, then lock down the directory
        # (chown root:root -> chmod 000) preserving data, and finally remove the
        # LDAP group. Permanent removal is a manual operator step.
        self._reset_quota(target, evidence)
        self._run(
            ["chown", "root:root", target], evidence, fail=False, side_effect=True
        )
        self._run(["chmod", "000", target], evidence, fail=False, side_effect=True)
        group_cleanup: dict = {}
        if group_name.startswith("dms-"):
            group_cleanup = self.identity_groups.delete_group(group_name=group_name)

        applied = {
            "adapter": "wekafs-host-mounted",
            "operation": OperationKind.FILESYSTEM_DELETE.value,
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "path": target,
            "deleted": True,
            "soft_delete": True,
            "access_group_cleanup": group_cleanup,
            "command_evidence": evidence,
            "backend_side_effect": True,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "exists": True,
            "verified": True,
            "resource_status": "Deleted",
        }
        return AdapterResult(
            applied,
            observed,
            "WekaFS directory locked (data preserved, root-only access)",
        )

    def consistency_check(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        target = self._directory_path(directory_name)
        evidence: list[dict[str, Any]] = []
        exists_result = self._run(["test", "-d", target], evidence, fail=False)
        exists = exists_result.returncode == 0
        ownership_state: dict[str, Any] = {}
        permission_state: dict[str, Any] = {}
        group_membership_state: dict[str, Any] = {}
        if not exists:
            issues = [
                {
                    "issue_type": "filesystem_quota_missing",
                    "field": "directory",
                    "reason": "missing",
                }
            ]
            quota_state: dict[str, Any] = {}
            status = "Missing"
        else:
            quota_state = self._read_quota(target, desired.get("quota") or {}, evidence)
            issues = _quota_check_issues(desired.get("quota") or {}, quota_state)
            ownership_state, permission_state, ownership_issues = (
                self._read_directory_attrs(target, desired, evidence)
            )
            issues.extend(ownership_issues)
            group_membership_state, membership_issues = self._read_group_membership(
                desired, evidence
            )
            issues.extend(membership_issues)
            status = "Drifted" if issues else "Consistent"
        applied = {
            "adapter": "wekafs-host-mounted",
            "operation": OperationKind.FILESYSTEM_CHECK.value,
            "backend": self.template.metadata(),
            "command_evidence": evidence,
            "backend_side_effect": False,
            "ownership_state": ownership_state,
            "permission_state": permission_state,
            "group_membership_state": group_membership_state,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "directory_name": directory_name,
            "path": target,
            "quota_state": quota_state,
            "quota_status": status,
            "issues": issues,
            "verified": status != "CheckFailed",
        }
        return AdapterResult(
            applied, observed, "WekaFS directory consistency check completed"
        )

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        target = self._directory_path(directory_name)
        evidence: list[dict[str, Any]] = []
        quota_state = self._read_quota(target, {}, evidence)
        synced_quota = _quota_from_state(quota_state)
        synced_desired = dict(desired)
        if synced_quota:
            synced_desired["quota"] = synced_quota
        applied = {
            "adapter": "wekafs-host-mounted",
            "operation": OperationKind.FILESYSTEM_SYNC.value,
            "backend": self.template.metadata(),
            "synced_desired_state": synced_desired,
            "quota_state": quota_state,
            "backend_side_effect": False,
            "command_evidence": evidence,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "directory_name": directory_name,
            "path": target,
            "quota_status": "Synced",
            "verified": True,
        }
        return AdapterResult(applied, observed, "WekaFS directory live state synced")

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult:
        return self._adopt_existing(plan, management_mode="full")

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult:
        return self._adopt_existing(plan, management_mode="quota_only")

    def _adopt_existing(
        self, plan: dict[str, Any], *, management_mode: str
    ) -> AdapterResult:
        self._validate_template()
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        target = self._directory_path(directory_name)
        evidence: list[dict[str, Any]] = []
        exists = self._run(["test", "-d", target], evidence, fail=False)
        if exists.returncode != 0:
            raise BackendPreconditionError("WekaFS directory not found for adoption")
        adoption_summary: dict[str, Any] = {}
        if management_mode == "full":
            adoption_summary = self._adopt_full_group(plan, desired, target, evidence)
        quota = desired.get("quota") or {}
        _reject_unsupported_quota_fields(quota)
        quota_state: dict[str, Any] = {}
        if quota:
            self._capability(evidence, require_quota=True)
            quota_state = self._apply_quota(target, quota, evidence)
        applied = {
            "adapter": "wekafs-host-mounted",
            "operation": plan["operation_kind"],
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "path": target,
            "quota": quota,
            "quota_state": quota_state,
            "management_mode": management_mode,
            "group_adoption": adoption_summary,
            "backend_side_effect": bool(
                quota or adoption_summary.get("changed_group")
            ),
            "command_evidence": evidence,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "exists": True,
            "verified": True,
        }
        return AdapterResult(
            applied, observed, f"WekaFS directory {management_mode} adoption completed"
        )

    def _adopt_full_group(
        self,
        plan: dict[str, Any],
        desired: dict[str, Any],
        path: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.identity_groups is None:
            return {"reason": "identity_groups_unavailable"}
        directory_name = _directory_name(desired)
        stat_result = self._run(["stat", "-c", "%g", path], evidence, fail=False)
        if stat_result.returncode != 0:
            raise BackendPreconditionError(
                f"WekaFS import stat failed: {(stat_result.stderr or stat_result.stdout).strip()}"
            )
        try:
            live_gid = int(stat_result.stdout.strip())
        except ValueError as exc:
            raise BackendPreconditionError(
                f"WekaFS import: could not parse live gid: {stat_result.stdout!r}"
            ) from exc
        getent_result = self._run(
            ["getent", "group", str(live_gid)], evidence, fail=False
        )
        live_group_name: str | None = None
        if getent_result.returncode == 0 and ":" in getent_result.stdout:
            live_group_name = getent_result.stdout.split(":")[0]
        if not live_group_name:
            try:
                live_group_name = self.identity_groups.lookup_group_name_by_gid(
                    gid=live_gid
                )
            except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
                raise BackendPreconditionError(str(exc)) from exc
            except AttributeError:
                live_group_name = None
        if not live_group_name:
            raise BackendPreconditionError(
                f"WekaFS import: live group (gid={live_gid}) not found in NSS or LDAP"
            )
        is_dms_managed = live_group_name.startswith("dms-grp-")
        try:
            live_members = self.identity_groups.list_group_members(
                group_name=live_group_name
            )
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(str(exc)) from exc
        live_members = sorted(set(live_members or []))
        if is_dms_managed:
            desired["access_group"] = live_group_name
            desired["users"] = live_members
            desired["access_group_gid"] = live_gid
            return {
                "live_group": live_group_name,
                "live_gid": live_gid,
                "adopted_members": live_members,
                "changed_group": False,
                "mode": "adopt_existing_dms_group",
            }
        new_group_name = f"dms-grp-{directory_name}"
        try:
            new_group = self.identity_groups.ensure_group_members(
                group_name=new_group_name,
                users=live_members,
                resource_key=plan["resource_key"],
            )
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(str(exc)) from exc
        new_gid = new_group.get("gid")
        self._run(["sss_cache", "-E"], evidence, fail=False, side_effect=False)
        if new_gid is not None:
            self._run(["chown", f":{new_gid}", path], evidence, side_effect=True)
        else:
            self._run(["chgrp", new_group_name, path], evidence, side_effect=True)
        desired["access_group"] = new_group_name
        desired["users"] = live_members
        if new_gid is not None:
            desired["access_group_gid"] = int(new_gid)
        return {
            "live_group": live_group_name,
            "live_gid": live_gid,
            "new_group": new_group_name,
            "new_gid": new_gid,
            "adopted_members": live_members,
            "changed_group": True,
            "mode": "create_dms_group_from_external",
        }

    def _resolve_owner_uid(
        self, plan: dict[str, Any], desired: dict[str, Any]
    ) -> int | None:
        """Resolve the directory owner's uid via LDAP (strict / fail-closed).

        Returns None ONLY when no owner is specified (group-only chown). When an owner
        IS specified but cannot be resolved to an LDAP uid, raises
        BackendPreconditionError so the create fails closed instead of silently leaving
        the directory owned by root (parity with CephFS). No uid-range restriction.
        """
        username = (
            desired.get("owner_username")
            or desired.get("requester_id")
            or (plan.get("desired_state") or {}).get("requester_id")
        )
        if not username:
            return None
        if self.identity_lookup is None:
            # No LDAP resolver configured at all -> cannot enforce owner; fall back to
            # group-only chown. In production the resolver is always configured, so the
            # strict (fail-closed) branches below apply.
            return None
        try:
            result = self.identity_lookup.lookup("ldap", str(username))
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(
                f"WekaFS owner '{username}' is not a resolvable LDAP user: {exc}"
            ) from exc
        if not result or result.uid is None:
            raise BackendPreconditionError(
                f"WekaFS owner '{username}' is not a resolvable LDAP user"
            )
        return int(result.uid)

    def _read_directory_attrs(
        self, path: str, desired: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        result = self._run(["stat", "-c", "%u %g %a", path], evidence, fail=False)
        ownership: dict[str, Any] = {}
        permission: dict[str, Any] = {}
        issues: list[dict[str, Any]] = []
        if result.returncode != 0:
            issues.append(
                {
                    "issue_type": "filesystem_directory_stat_failed",
                    "reason": (result.stderr or result.stdout).strip()[:200],
                }
            )
            return ownership, permission, issues
        parts = result.stdout.strip().split()
        if len(parts) != 3:
            issues.append(
                {
                    "issue_type": "filesystem_directory_stat_failed",
                    "reason": "unexpected stat output",
                }
            )
            return ownership, permission, issues
        observed_uid, observed_gid, observed_mode_octal = parts
        observed_mode = observed_mode_octal.zfill(4)
        ownership = {
            "observed_uid": int(observed_uid),
            "observed_gid": int(observed_gid),
        }
        desired_uid = self._lookup_desired_uid(desired)
        if desired_uid is not None:
            ownership["desired_uid"] = desired_uid
            if int(observed_uid) != int(desired_uid):
                issues.append(
                    {
                        "issue_type": "filesystem_owner_drifted",
                        "field": "ownership.uid",
                        "desired": desired_uid,
                        "observed": int(observed_uid),
                    }
                )
        desired_gid = self._lookup_desired_gid(desired)
        if desired_gid is not None:
            ownership["desired_gid"] = desired_gid
            if int(observed_gid) != int(desired_gid):
                issues.append(
                    {
                        "issue_type": "filesystem_owner_drifted",
                        "field": "ownership.gid",
                        "desired": desired_gid,
                        "observed": int(observed_gid),
                    }
                )
        desired_mode = str(desired.get("mode") or "0750")
        permission = {"observed_mode": observed_mode, "desired_mode": desired_mode}
        if observed_mode != desired_mode:
            issues.append(
                {
                    "issue_type": "filesystem_mode_drifted",
                    "field": "permission.mode",
                    "desired": desired_mode,
                    "observed": observed_mode,
                }
            )
        return ownership, permission, issues

    def _read_group_membership(
        self, desired: dict[str, Any], evidence: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        group_name = desired.get("access_group")
        users = list(desired.get("users") or [])
        if not group_name or not users:
            return {}, []
        if self.identity_groups is None:
            return {"reason": "identity_groups_unavailable"}, []
        try:
            members = self.identity_groups.list_group_members(group_name=group_name)
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            return {"reason": str(exc)}, [
                {
                    "issue_type": "filesystem_group_membership_unverified",
                    "field": "access_group.members",
                    "reason": str(exc)[:200],
                }
            ]
        except AttributeError:
            return {"reason": "list_group_members_unsupported"}, []
        member_set = set(members or [])
        missing = sorted([u for u in users if u not in member_set])
        state = {
            "group_name": group_name,
            "expected_members": sorted(users),
            "observed_members": sorted(member_set),
            "missing_members": missing,
        }
        issues: list[dict[str, Any]] = []
        if missing:
            issues.append(
                {
                    "issue_type": "filesystem_group_membership_drifted",
                    "field": "access_group.members",
                    "missing": missing,
                }
            )
        return state, issues

    def _lookup_desired_uid(self, desired: dict[str, Any]) -> int | None:
        if self.identity_lookup is None:
            return None
        username = desired.get("owner_username") or desired.get("requester_id")
        if not username:
            return None
        try:
            result = self.identity_lookup.lookup("ldap", str(username))
        except (IdentityLookupConfigurationError, IdentityLookupReadError):
            return None
        if not result or result.uid is None:
            return None
        return int(result.uid)

    def _lookup_desired_gid(self, desired: dict[str, Any]) -> int | None:
        gid_hint = desired.get("access_group_gid")
        if gid_hint is not None:
            try:
                return int(gid_hint)
            except (TypeError, ValueError):
                pass
        group_name = desired.get("access_group")
        if not group_name or self.identity_groups is None:
            return None
        try:
            return self.identity_groups.lookup_group_gid(group_name=group_name)
        except (IdentityLookupConfigurationError, IdentityLookupReadError):
            return None
        except AttributeError:
            return None

    def _capability(
        self, evidence: list[dict[str, Any]], *, require_quota: bool
    ) -> dict[str, Any]:
        # 명령 가용성: weka, mkdir, chmod, chgrp, rm
        for command in ("weka", "mkdir", "chmod", "chgrp", "rm"):
            result = self._run(
                ["sh", "-c", f"command -v {command}"], evidence, fail=False
            )
            if result.returncode != 0:
                raise BackendPreconditionError(f"WekaFS command missing: {command}")
        if require_quota:
            # 인증/quota 명령 가용성 확인 (가벼운 list 호출)
            args = [
                "weka",
                "fs",
                "quota",
                "list",
                self.template.filesystem_name,
                "--format",
                "json",
                "--all",
                "--quick",
            ]
            if self.template.weka_profile:
                args.extend(["--profile", self.template.weka_profile])
            probe = self._run(args, evidence, fail=False)
            if probe.returncode != 0:
                stderr = probe.stderr or probe.stdout
                if "Authentication Failed" in stderr:
                    raise BackendPreconditionError(
                        "WekaFS quota CLI not authenticated; run `weka user login` on the RM worker "
                        "or configure WEKA_USERNAME/WEKA_PASSWORD env"
                    )
                raise BackendPreconditionError(
                    f"WekaFS quota probe failed: {stderr.strip() or 'unknown error'}"
                )
        return {
            "backend_type": WEKAFS_BACKEND_TYPE,
            "quota_backend": WEKAFS_QUOTA_BACKEND,
            "supports_directory_create": True,
            "supports_capacity_quota": True,
            "supports_file_count_quota": False,
            "supports_usage_bytes": True,
            "supports_file_count_usage": False,
            "supports_permission_mode": True,
            "filesystem_name": self.template.filesystem_name,
            "managed_root": self.template.managed_root,
        }

    def _apply_quota(
        self,
        target: str,
        quota: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        capacity = quota.get("capacity_bytes")
        if capacity is None:
            raise BackendPreconditionError("WekaFS quota requires capacity_bytes")
        soft = quota.get("soft_limit_bytes", capacity)
        argv = [
            "weka",
            "fs",
            "quota",
            "set",
            target,
            "--soft",
            str(int(soft)),
            "--hard",
            str(int(capacity)),
        ]
        if self.template.weka_profile:
            argv.extend(["--profile", self.template.weka_profile])
        self._run(argv, evidence, side_effect=True)
        quota_state = self._read_quota(target, quota, evidence)
        issue = _quota_readback_issue(quota, quota_state)
        if issue:
            raise WekaFsSideEffectError(issue)
        return quota_state

    def _read_quota(
        self,
        target: str,
        desired_quota: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        argv = [
            "weka",
            "fs",
            "quota",
            "list",
            self.template.filesystem_name,
            "--path",
            target,
            "--format",
            "json",
            "--all",
            "--raw-units",
        ]
        if self.template.weka_profile:
            argv.extend(["--profile", self.template.weka_profile])
        result = self._run(argv, evidence, fail=False)
        observed_bytes: int | None = None
        soft_bytes: int | None = None
        used_bytes: int | None = None
        if result.returncode == 0 and result.stdout.strip():
            row = _select_quota_row(result.stdout, target, self.template.mount_path)
            if row:
                observed_bytes = _to_int(
                    _first(row, ("hard_limit_bytes", "hard_limit", "hardLimit", "hard"))
                )
                soft_bytes = _to_int(
                    _first(row, ("soft_limit_bytes", "soft_limit", "softLimit", "soft"))
                )
                used_bytes = _to_int(
                    _first(row, ("usage", "used_bytes", "usedBytes", "used"))
                )
        return {
            "backend_type": WEKAFS_BACKEND_TYPE,
            "quota_backend": WEKAFS_QUOTA_BACKEND,
            "path": target,
            "capacity": {
                "desired_bytes": desired_quota.get("capacity_bytes"),
                "applied_bytes": desired_quota.get("capacity_bytes"),
                "observed_bytes": observed_bytes,
                "soft_observed_bytes": soft_bytes,
                "backend_key": "hard_limit_bytes",
            },
            **(
                {
                    "usage_evidence": {
                        "usage_source": "weka-fs-quota-list",
                        "used_bytes": used_bytes,
                        "admission_input": False,
                    }
                }
                if used_bytes is not None
                else {}
            ),
        }

    def _reset_quota(self, target: str, evidence: list[dict[str, Any]]) -> None:
        argv = ["weka", "fs", "quota", "reset", target]
        if self.template.weka_profile:
            argv.extend(["--profile", self.template.weka_profile])
        self._run(argv, evidence, fail=False, side_effect=True)


    def _run(
        self,
        argv: list[str],
        evidence: list[dict[str, Any]],
        *,
        fail: bool = True,
        side_effect: bool = False,
    ) -> CommandResult:
        executor = self.executor or self.template.executor()
        env: dict[str, str] | None = None
        if argv and argv[0] == "weka" and self.template.credentials is not None:
            env = self.template.credentials.env()
        result = executor.run(
            argv,
            timeout_seconds=self.template.command_timeout_seconds,
            env=env,
        )
        evidence.append(_command_evidence(result, side_effect=side_effect))
        if fail and result.returncode != 0:
            message = (
                result.stderr.strip()
                or result.stdout.strip()
                or "WekaFS command failed"
            )
            raise BackendPreconditionError(message)
        return result

    def _validate_template(self) -> None:
        if not self.template.mount_path:
            raise BackendPreconditionError("WekaFS storage mapping requires mount_path")
        if not self.template.managed_root:
            raise BackendPreconditionError(
                "WekaFS storage mapping requires managed_root"
            )
        if not self.template.rm_worker_node and self.executor is None:
            raise BackendPreconditionError(
                "WekaFS storage mapping requires an RM worker node"
            )
        if self.template.credentials is None:
            raise BackendPreconditionError(
                "WekaFS storage mapping requires weka_credentials "
                "(organization, username, password)"
            )

    def _directory_path(self, directory_name: str) -> str:
        mount_path = os.path.normpath(self.template.mount_path)
        root = os.path.normpath(self.template.managed_root)
        if os.path.commonpath([mount_path, root]) != mount_path:
            raise BackendPreconditionError(
                "WekaFS managed_root must be under mount_path"
            )
        path = os.path.normpath(os.path.join(root, directory_name))
        if os.path.commonpath([root, path]) != root:
            raise BackendPreconditionError("WekaFS directory path escaped managed_root")
        return path


# Backward-compat alias (이전 import 경로 유지)
WekaFsFilesystemBackendAdapter = WekaFsHostMountedFilesystemBackendAdapter


@dataclass(frozen=True)
class WekaFsDataManagementAdapter:
    template: WekaFsBackendTemplate

    def worker_pool(self, storage_name: str) -> dict[str, Any]:
        return {
            "selection": "agent-inventory",
            "backend_type": WEKAFS_BACKEND_TYPE,
            "required_mounts": [storage_name],
            "mount_path": self.template.mount_path,
            "filesystem_name": self.template.filesystem_name,
            "data_network": self.template.data_network,
            "tool_candidates": ["dsync", "nsync", "drm", "dscan"],
            "requires_posix_identity": True,
            "candidates": [],
        }


def _directory_name(desired: dict[str, Any]) -> str:
    directory_name = str(desired.get("directory_name") or "")
    validate_storage_root_basename("directory_name", directory_name)
    if not WEKAFS_DIRECTORY_NAME_PATTERN.match(directory_name):
        raise BackendPreconditionError("unsafe WekaFS directory name")
    return directory_name


def _string_list(value: Any, field_name: str, *, required: bool = True) -> list[str]:
    if value is None:
        if required:
            raise BackendPreconditionError(f"{field_name} is required")
        return []
    if not isinstance(value, list):
        raise BackendPreconditionError(f"{field_name} must be a list of strings")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise BackendPreconditionError(
                f"{field_name} entries must be non-empty strings"
            )
        out.append(item.strip())
    return out


def _reject_unsupported_quota_fields(quota: dict[str, Any]) -> None:
    if quota.get("file_count") is not None:
        raise BackendPreconditionError(
            "WekaFS path quota does not support file_count (inode quota); "
            "omit file_count or use a backend that supports it"
        )




def _command_evidence(result: CommandResult, *, side_effect: bool) -> dict[str, Any]:
    return {
        "argv": result.argv,
        "returncode": result.returncode,
        "stdout": result.stdout.strip()[-1000:],
        "stderr": result.stderr.strip()[-1000:],
        "duration_seconds": round(result.duration_seconds, 3),
        "timed_out": result.timed_out,
        "side_effect": side_effect,
    }


def _quota_check_issues(
    desired_quota: dict[str, Any], quota_state: dict[str, Any]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    readback = _quota_readback_issue(desired_quota, quota_state)
    if readback:
        issues.append(
            {
                "issue_type": "filesystem_quota_drifted",
                "field": "quota",
                "reason": readback,
            }
        )
    return issues


QUOTA_CAPACITY_TOLERANCE_BYTES = 100 * 1024 * 1024


def _quota_readback_issue(
    desired_quota: dict[str, Any], quota_state: dict[str, Any]
) -> str | None:
    desired_bytes = desired_quota.get("capacity_bytes")
    observed_bytes = (quota_state.get("capacity") or {}).get("observed_bytes")
    if desired_bytes is not None and observed_bytes is not None:
        # WEKA rounds capacity up to a small block boundary; accept observed within
        # [desired, desired + QUOTA_CAPACITY_TOLERANCE_BYTES]. observed below
        # desired means the quota under-applied and is still treated as mismatch.
        diff = int(observed_bytes) - int(desired_bytes)
        if diff < 0 or diff > QUOTA_CAPACITY_TOLERANCE_BYTES:
            return "WekaFS quota capacity read-back mismatch"
    return None


def _quota_from_state(quota_state: dict[str, Any]) -> dict[str, int]:
    quota: dict[str, int] = {}
    observed_bytes = (quota_state.get("capacity") or {}).get("observed_bytes")
    if observed_bytes is not None:
        quota["capacity_bytes"] = int(observed_bytes)
    return quota


def _select_quota_row(
    stdout: str, target: str, mount_path: str | None = None
) -> dict[str, Any] | None:
    """`weka fs quota list ... --format json` 출력에서 path 매칭 row를 고른다.

    WEKA 4.4.x는 list-of-dict이며 row의 `path`는 filesystem-relative
    (`/dms/foo`), `target`은 host mount prefix 포함 (`/pvs_weka/dms/foo`).
    매칭하려면 mount_path를 벗겨내고 비교한다.
    """
    text = stdout.strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    rows: list[dict[str, Any]]
    if isinstance(parsed, list):
        rows = [r for r in parsed if isinstance(r, dict)]
    elif isinstance(parsed, dict):
        # 일부 버전은 {"data": [...]}형
        if isinstance(parsed.get("data"), list):
            rows = [r for r in parsed["data"] if isinstance(r, dict)]
        else:
            rows = [parsed]
    else:
        return None
    target_stripped = target.rstrip("/")
    if mount_path:
        prefix = mount_path.rstrip("/")
        if target_stripped.startswith(prefix + "/"):
            relative = target_stripped[len(prefix) :]
        elif target_stripped == prefix:
            relative = "/"
        else:
            relative = target_stripped
    else:
        relative = target_stripped
    for row in rows:
        row_path = str(row.get("path") or "").rstrip("/")
        if row_path == target_stripped or row_path == relative:
            return row
    return rows[0] if len(rows) == 1 else None


def _first(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in row and row[key] is not None and row[key] != "":
            return row[key]
    return None


def _to_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"none", "no", "unlimited", "no limit", "--", "0"}:
        # "0"은 weka에서 "unlimited"이지만 quota 미설정과 구분 안 됨 → None
        if text == "0":
            return 0
        return None
    try:
        return int(text)
    except ValueError:
        # "1.5GiB" 같은 단위 표기 — --raw-units이면 정수가 와야 정상
        match = re.match(r"^(\d+)", text)
        return int(match.group(1)) if match else None
