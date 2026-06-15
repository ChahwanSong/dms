from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
import re
import shlex
import subprocess
import time
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

GPFS_BACKEND_TYPE = "gpfs"
GPFS_CSI_DRIVER = "spectrumscale.csi.ibm.com"
GPFS_QUOTA_BACKEND = "gpfs-fileset-quota"
GPFS_FILESET_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class GpfsSideEffectError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0
    timed_out: bool = False


class GpfsCommandExecutor(Protocol):
    def run(self, argv: list[str], *, timeout_seconds: int) -> CommandResult: ...


@dataclass(frozen=True)
class LocalGpfsCommandExecutor:
    def run(self, argv: list[str], *, timeout_seconds: int) -> CommandResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
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
class SshGpfsCommandExecutor:
    host: str
    local: LocalGpfsCommandExecutor = LocalGpfsCommandExecutor()

    def run(self, argv: list[str], *, timeout_seconds: int) -> CommandResult:
        cmd = f"PATH=/usr/lpp/mmfs/bin:$PATH {shlex.join(argv)}"
        return self.local.run(
            ["ssh", self.host, cmd],
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
class GpfsBackendTemplate:
    storage_name: str
    filesystem_name: str
    mount_path: str
    managed_root: str | None
    quota_scope: str
    csi_driver: str
    storage_class_name: str | None
    data_network: str | None
    fileset_name_template: str
    rm_worker_node: str | None
    command_runner: str
    command_timeout_seconds: int

    @classmethod
    def from_storage_mapping(cls, mapping: dict[str, Any]) -> "GpfsBackendTemplate":
        template = mapping["backend_template"]
        rm_workers = template.get("rm_worker_nodes") or []
        rm_worker_node = template.get("ssh_host") or _pick_rm_worker(
            mapping, rm_workers
        )
        return cls(
            storage_name=mapping["storage_name"],
            filesystem_name=template.get("filesystem_name", mapping["storage_name"]),
            mount_path=template.get("mount_path", ""),
            managed_root=template.get("managed_root"),
            quota_scope=template.get("quota_scope", "fileset"),
            csi_driver=template.get("csi_driver", GPFS_CSI_DRIVER),
            storage_class_name=(
                template.get("storage_class_name") or mapping.get("storage_class_name")
            ),
            data_network=template.get("data_network"),
            fileset_name_template=template.get(
                "fileset_name_template", "dms-{directory_name}"
            ),
            rm_worker_node=rm_worker_node,
            command_runner=template.get("command_runner", "local"),
            command_timeout_seconds=int(template.get("command_timeout_seconds", 60)),
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "backend_type": GPFS_BACKEND_TYPE,
            "storage_name": self.storage_name,
            "filesystem_name": self.filesystem_name,
            "mount_path": self.mount_path,
            "managed_root": self.managed_root,
            "quota_scope": self.quota_scope,
            "csi_driver": self.csi_driver,
            "storage_class_name": self.storage_class_name,
            "data_network": self.data_network,
            "fileset_name_template": self.fileset_name_template,
            "rm_worker_node": self.rm_worker_node,
            "command_runner": self.command_runner,
        }

    def executor(self) -> GpfsCommandExecutor:
        if self.command_runner == "ssh-host-exec":
            if not self.rm_worker_node:
                raise BackendPreconditionError("GPFS storage mapping requires ssh_host")
            return SshGpfsCommandExecutor(self.rm_worker_node)
        if self.command_runner == "local":
            return LocalGpfsCommandExecutor()
        raise BackendPreconditionError(
            f"unsupported GPFS command runner: {self.command_runner}"
        )


@dataclass(frozen=True)
class GpfsQuotaStrategy:
    backend_type: str = GPFS_BACKEND_TYPE

    def render_quota(self, quota: dict[str, Any]) -> dict[str, Any]:
        capacity = quota.get("capacity_bytes")
        file_count = quota.get("file_count")
        rendered: dict[str, Any] = {
            "backend_type": self.backend_type,
            "quota_backend": GPFS_QUOTA_BACKEND,
            "quota_scope": quota.get("scope", "fileset"),
            "capacity_bytes": capacity,
            "file_count": file_count,
            "hard_limit_bytes": quota.get("hard_limit_bytes", capacity),
            "hard_file_limit": quota.get("hard_file_limit", file_count),
            "command_family": "gpfs-quota",
            "side_effect": "gpfs-command",
        }
        if capacity is not None:
            rendered["block_limit"] = render_gpfs_block_limit(int(capacity))
        if file_count is not None:
            rendered["files_limit"] = f"{int(file_count)}:{int(file_count)}"
        return rendered


@dataclass
class GpfsFilesystemBackendAdapter:
    template: GpfsBackendTemplate
    quota_strategy: GpfsQuotaStrategy = GpfsQuotaStrategy()
    executor: GpfsCommandExecutor | None = None
    identity_groups: IdentityGroupManager | None = None
    identity_lookup: IdentityLookupAdapter | None = None

    @classmethod
    def from_storage_mapping(
        cls,
        mapping: dict[str, Any],
        settings: Settings | None = None,
        *,
        executor: GpfsCommandExecutor | None = None,
        identity_groups: IdentityGroupManager | None = None,
        identity_lookup: IdentityLookupAdapter | None = None,
    ) -> "GpfsFilesystemBackendAdapter":
        template = GpfsBackendTemplate.from_storage_mapping(mapping)
        resolved_identity_groups = identity_groups
        if resolved_identity_groups is None and settings is not None:
            try:
                resolved_identity_groups = LdapIdentityGroupManager.from_settings(
                    settings
                )
            except IdentityLookupConfigurationError:
                resolved_identity_groups = None
        resolved_identity_lookup = identity_lookup
        if resolved_identity_lookup is None and settings is not None:
            try:
                resolved_identity_lookup = LdapIdentityLookupAdapter.from_settings(
                    settings
                )
            except IdentityLookupConfigurationError:
                resolved_identity_lookup = None
        return cls(
            template=template,
            executor=executor or template.executor(),
            identity_groups=resolved_identity_groups,
            identity_lookup=resolved_identity_lookup,
        )

    def create(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        self._capability(evidence, require_quota=bool(desired.get("quota")))
        existing = self._read_fileset(fileset_name, evidence, fail_on_error=False)
        if existing.get("exists"):
            raise BackendPreconditionError("GPFS fileset already exists")

        group_name = desired.get("access_group") or f"dms-grp-{directory_name}"
        users = list(desired.get("users") or [])
        quota = desired.get("quota") or {}

        # LDAP group creation before any side effects so failure is clean rollback
        group: dict[str, Any] = {}
        if self.identity_groups is not None:
            try:
                group = self.identity_groups.ensure_group_members(
                    group_name=group_name,
                    users=users,
                    resource_key=plan["resource_key"],
                )
            except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
                raise BackendPreconditionError(str(exc)) from exc

        # Resolve the directory owner BEFORE any side effect so an unresolvable owner
        # fails closed (no fileset created), in parity with CephFS.
        owner_uid = self._resolve_owner_uid(plan, desired)

        side_effect_started = False
        try:
            self._run(
                [
                    "mmcrfileset",
                    self.template.filesystem_name,
                    fileset_name,
                    "--inode-space",
                    "new",
                ],
                evidence,
                side_effect=True,
            )
            side_effect_started = True
            quota_state = {}
            if quota:
                quota_state = self._apply_quota(
                    fileset_name, junction_path, quota, evidence
                )
            self._run(
                [
                    "mmlinkfileset",
                    self.template.filesystem_name,
                    fileset_name,
                    "-J",
                    junction_path,
                ],
                evidence,
                side_effect=True,
            )
            # Refresh SSSD cache so chown/probe can resolve the new group
            self._run(["sss_cache", "-E"], evidence, fail=False, side_effect=False)
            gid = group.get("gid")
            if owner_uid is not None and gid is not None:
                self._run(
                    ["chown", f"{owner_uid}:{gid}", junction_path],
                    evidence,
                    side_effect=True,
                )
            elif owner_uid is not None:
                self._run(
                    ["chown", str(owner_uid), junction_path], evidence, side_effect=True
                )
            elif gid is not None:
                self._run(
                    ["chown", f":{gid}", junction_path], evidence, side_effect=True
                )
            elif group_name:
                self._run(
                    ["chgrp", group_name, junction_path], evidence, side_effect=True
                )
            mode = str(desired.get("mode") or "0750")
            self._run(["chmod", mode, junction_path], evidence, side_effect=True)
            fileset_state = self._read_fileset(fileset_name, evidence)
        except BackendPreconditionError as exc:
            if side_effect_started:
                raise GpfsSideEffectError(str(exc)) from exc
            raise

        # Access verification (non-fatal, recorded in observed state)
        access_validation: dict[str, Any] = {}
        if self.identity_groups is not None and users:
            denied_users = list(
                desired.get("denied_users")
                or desired.get("validation_denied_users")
                or []
            )
            access_validation = probe_filesystem_access(
                run_cmd=lambda argv: self._run(
                    argv, evidence, fail=False, side_effect=False
                ).returncode,
                run_cmd_out=lambda argv: (lambda r: (r.returncode, r.stdout))(
                    self._run(argv, evidence, fail=False, side_effect=False)
                ),
                path=junction_path,
                allowed_users=users,
                denied_users=denied_users,
                group_gid=group.get("gid"),
            )

        access_group_info: dict[str, Any] = {
            "group_name": group_name,
            "gid": group.get("gid"),
            "dn": group.get("dn"),
            "members": group.get("members", users),
        }
        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": OperationKind.FILESYSTEM_CREATE.value,
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "access_group": access_group_info,
            "quota": quota,
            "quota_state": quota_state,
            "fileset_state": fileset_state,
            "command_evidence": evidence,
            "backend_side_effect": True,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "path": junction_path,
            "exists": True,
            "verified": True,
            "management_mode": "full",
        }
        if access_validation:
            observed["access_validation"] = access_validation
        return AdapterResult(
            applied_state=applied,
            observed_state=observed,
            message="GPFS fileset create completed",
        )

    def update(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        quota = desired.get("quota") if desired.get("update_apply_quota") else None
        owner_username = (
            desired.get("update_owner_username")
            if desired.get("update_apply_owner")
            else None
        )
        evidence: list[dict[str, Any]] = []
        owner_change: dict[str, Any] = {}
        quota_state: dict[str, Any] = {}
        self._read_fileset(fileset_name, evidence)
        if quota:
            self._capability(evidence, require_quota=True)
            quota_state = self._apply_quota(
                fileset_name, junction_path, quota, evidence
            )
        if owner_username:
            if self.identity_lookup is None:
                raise BackendPreconditionError(
                    "GPFS owner update requires LDAP identity lookup"
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
            self._run(
                ["chown", str(result.uid), junction_path],
                evidence,
                side_effect=True,
            )
            owner_change = {
                "owner_username": owner_username,
                "uid": int(result.uid),
            }
        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": OperationKind.FILESYSTEM_UPDATE.value,
            "backend": self.template.metadata(),
            "quota": quota or {},
            "quota_state": quota_state,
            "owner_change": owner_change,
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "command_evidence": evidence,
            "backend_side_effect": bool(quota or owner_change),
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "path": junction_path,
            "exists": True,
            "verified": True,
        }
        return AdapterResult(applied, observed, "GPFS fileset update completed")

    def block(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        self._read_fileset(fileset_name, evidence)
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
        self._run(["chmod", mode, junction_path], evidence, side_effect=True)
        block_state = dict(desired.get("block_state") or {})
        block_state.update(
            {"blocked": block, "block_mode": "chmod-0000" if block else "restored"}
        )
        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": OperationKind.FILESYSTEM_BLOCK.value,
            "backend": self.template.metadata(),
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "block_state": block_state,
            "command_evidence": evidence,
            "backend_side_effect": True,
        }
        observed = {**applied, "resource_key": plan["resource_key"], "verified": True}
        return AdapterResult(applied, observed, "GPFS fileset block state updated")

    def initialize(self, plan: dict[str, Any]) -> AdapterResult:
        return self.consistency_check(plan)

    def delete(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        if desired.get("management_mode") == "quota_only" or desired.get("import_mode"):
            raise BackendPreconditionError(
                "GPFS delete refused for imported/quota-only fileset"
            )
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        self._read_fileset(fileset_name, evidence, fail_on_error=False)

        # Lock down the junction path: chown root:root + chmod 000
        # Fileset stays linked; data is preserved and accessible only by root.
        self._run(
            ["chown", "root:root", junction_path],
            evidence,
            fail=False,
            side_effect=True,
        )
        self._run(
            ["chmod", "000", junction_path], evidence, fail=False, side_effect=True
        )

        group_cleanup: dict[str, Any] = {}
        if self.identity_groups is not None:
            group_name = desired.get("access_group") or f"dms-grp-{directory_name}"
            if group_name.startswith("dms-"):
                group_cleanup = self.identity_groups.delete_group(group_name=group_name)

        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": OperationKind.FILESYSTEM_DELETE.value,
            "backend": self.template.metadata(),
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "deleted": True,
            "soft_delete": True,
            "command_evidence": evidence,
            "backend_side_effect": True,
        }
        if group_cleanup:
            applied["access_group_cleanup"] = group_cleanup
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
            "GPFS fileset locked (data preserved, fileset remains linked)",
        )

    def consistency_check(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        fileset_state = self._read_fileset(fileset_name, evidence, fail_on_error=False)
        ownership_state: dict[str, Any] = {}
        permission_state: dict[str, Any] = {}
        group_membership_state: dict[str, Any] = {}
        if not fileset_state.get("exists"):
            issues = [
                {
                    "issue_type": "filesystem_quota_missing",
                    "field": "fileset",
                    "reason": "missing",
                }
            ]
            status = "Missing"
            quota_state = {}
        else:
            quota_state = self._read_quota(
                fileset_name, junction_path, desired.get("quota") or {}, evidence
            )
            issues = _quota_check_issues(desired.get("quota") or {}, quota_state)
            ownership_state, permission_state, ownership_issues = (
                self._read_directory_attrs(junction_path, desired, evidence)
            )
            issues.extend(ownership_issues)
            group_membership_state, membership_issues = self._read_group_membership(
                desired, evidence
            )
            issues.extend(membership_issues)
            status = "Drifted" if issues else "Consistent"
        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": OperationKind.FILESYSTEM_CHECK.value,
            "backend": self.template.metadata(),
            "backend_side_effect": False,
            "command_evidence": evidence,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "fileset_state": fileset_state,
            "quota_state": quota_state,
            "ownership_state": ownership_state,
            "permission_state": permission_state,
            "group_membership_state": group_membership_state,
            "quota_status": status,
            "issues": issues,
            "verified": status != "CheckFailed",
        }
        return AdapterResult(
            applied, observed, "GPFS fileset consistency check completed"
        )

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        self._read_fileset(fileset_name, evidence)
        quota_state = self._read_quota(fileset_name, junction_path, {}, evidence)
        synced_quota = _quota_from_state(quota_state)
        synced_desired = dict(desired)
        if synced_quota:
            synced_desired["quota"] = synced_quota
        applied = {
            "adapter": "gpfs-fileset-command",
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
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "quota_status": "Synced",
            "verified": True,
        }
        return AdapterResult(applied, observed, "GPFS fileset live state synced")

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult:
        return self._adopt_existing(plan, management_mode="full")

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult:
        return self._adopt_existing(plan, management_mode="quota_only")

    def _adopt_existing(
        self, plan: dict[str, Any], *, management_mode: str
    ) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        fileset_state = self._read_fileset_by_junction(junction_path, evidence)
        fileset_name = fileset_state.get("fileset_name") or self._fileset_name(
            directory_name
        )

        adoption_summary: dict[str, Any] = {}
        if management_mode == "full":
            adoption_summary = self._adopt_full_group(
                plan, desired, junction_path, evidence
            )

        quota = desired.get("quota") or {}
        quota_state = {}
        if quota:
            self._capability(evidence, require_quota=True)
            quota_state = self._apply_quota(
                fileset_name, junction_path, quota, evidence
            )
        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": plan["operation_kind"],
            "backend": self.template.metadata(),
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "fileset_state": fileset_state,
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
            applied, observed, f"GPFS fileset {management_mode} adoption completed"
        )

    def _adopt_full_group(
        self,
        plan: dict[str, Any],
        desired: dict[str, Any],
        path: str,
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Import-time access-group adoption.

        Path is already linked. Discover live group via stat:
          - if the live group is `dms-grp-*` → adopt it (pull members from LDAP).
          - else → create `dms-grp-{directory_name}`, copy the live external
            group's members into it, chown the path to the new gid.
        """
        if self.identity_groups is None:
            return {"reason": "identity_groups_unavailable"}
        directory_name = _directory_name(desired)
        # 1. stat -> gid
        stat_result = self._run(["stat", "-c", "%g", path], evidence, fail=False)
        if stat_result.returncode != 0:
            raise BackendPreconditionError(
                f"GPFS import stat failed: {(stat_result.stderr or stat_result.stdout).strip()}"
            )
        try:
            live_gid = int(stat_result.stdout.strip())
        except ValueError as exc:
            raise BackendPreconditionError(
                f"GPFS import: could not parse live gid: {stat_result.stdout!r}"
            ) from exc
        # 2. live group name from getent (sssd / files)
        getent_result = self._run(
            ["getent", "group", str(live_gid)], evidence, fail=False
        )
        live_group_name: str | None = None
        if getent_result.returncode == 0 and ":" in getent_result.stdout:
            live_group_name = getent_result.stdout.split(":")[0]
        # 3. fall back to LDAP gid lookup
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
                f"GPFS import: live group (gid={live_gid}) not found in NSS or LDAP"
            )
        is_dms_managed = live_group_name.startswith("dms-grp-")
        # 4. members from LDAP
        try:
            live_members = self.identity_groups.list_group_members(
                group_name=live_group_name
            )
        except (IdentityLookupConfigurationError, IdentityLookupReadError) as exc:
            raise BackendPreconditionError(str(exc)) from exc
        live_members = sorted(set(live_members or []))
        if is_dms_managed:
            # Adopt as-is
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
        # External group: create new dms-grp-, migrate members, chown
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
        # SSSD cache refresh + chown to new gid
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

    def _read_directory_attrs(
        self,
        path: str,
        desired: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        """`stat` 결과로 ownership(uid/gid)와 mode를 desired와 대조."""
        result = self._run(["stat", "-c", "%u %g %a", path], evidence, fail=False)
        ownership: dict[str, Any] = {}
        permission: dict[str, Any] = {}
        issues: list[dict[str, Any]] = []
        if result.returncode != 0:
            issues.append(
                {
                    "issue_type": "filesystem_directory_stat_failed",
                    "field": "directory",
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
        """desired_state.users 가 access_group LDAP 그룹에 모두 포함됐는지 확인."""
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

    def _resolve_owner_uid(
        self, plan: dict[str, Any], desired: dict[str, Any]
    ) -> int | None:
        """LDAP에서 directory owner의 uidNumber를 조회한다 (strict / fail-closed).

        우선순위: desired.owner_username -> desired.requester_id -> plan.requester_id.
        owner가 지정되지 않은 경우에만 None을 반환한다(group-only chown). owner가
        지정됐는데 LDAP uid로 해석되지 않으면, 디렉토리를 조용히 root 소유로 두지 않고
        BackendPreconditionError로 fail-closed한다(CephFS와 패리티). uid 범위 제한은 없다.
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
                f"GPFS owner '{username}' is not a resolvable LDAP user: {exc}"
            ) from exc
        if not result or result.uid is None:
            raise BackendPreconditionError(
                f"GPFS owner '{username}' is not a resolvable LDAP user"
            )
        return int(result.uid)

    def _capability(
        self, evidence: list[dict[str, Any]], *, require_quota: bool
    ) -> dict[str, Any]:
        # Check all required commands in a single SSH call
        cmds = [
            "mmcrfileset",
            "mmlinkfileset",
            "mmlsfileset",
            "mmsetquota",
            "mmlsquota",
            "mmunlinkfileset",
            "mmdelfileset",
        ]
        check_script = " && ".join(
            f"command -v {c} >/dev/null 2>&1 || echo MISSING:{c}" for c in cmds
        )
        result = self._run(["sh", "-c", check_script], evidence, fail=False)
        for line in result.stdout.splitlines():
            if line.startswith("MISSING:"):
                raise BackendPreconditionError(
                    f"GPFS command missing: {line[len('MISSING:'):]}"
                )
        if require_quota:
            # Single SSH call to get both quota and perfileset-quota status
            combined = self._run(
                [
                    "mmlsfs",
                    self.template.filesystem_name,
                    "-Q",
                    "--perfileset-quota",
                    "-Y",
                ],
                evidence,
            )
            rows = parse_gpfs_y(combined.stdout)
            quota_rows = [
                r
                for r in rows
                if "quota" in r.get("fieldName", "").lower()
                and "perfileset" not in r.get("fieldName", "").lower()
            ]
            if not _rows_contain_enabled(quota_rows):
                raise BackendPreconditionError(
                    "GPFS filesystem quota disabled or unknown"
                )
            perfileset_rows = [
                r for r in rows if r.get("fieldName") == "perfilesetQuotas"
            ]
            if not perfileset_rows or not _rows_contain_enabled(perfileset_rows):
                raise BackendPreconditionError(
                    "GPFS per-fileset quota disabled or unknown"
                )
        return {
            "backend_type": GPFS_BACKEND_TYPE,
            "quota_backend": GPFS_QUOTA_BACKEND,
            "supports_directory_create": True,
            "supports_capacity_quota": require_quota,
            "supports_file_count_quota": require_quota,
            "supports_usage_bytes": False,
            "supports_file_count_usage": False,
            "supports_fileset_create": True,
            "supports_fileset_link": True,
            "supports_fileset_delete": True,
            "supports_permission_mode": True,
            "filesystem_name": self.template.filesystem_name,
            "managed_root": self.template.managed_root,
        }

    def _apply_quota(
        self,
        fileset_name: str,
        junction_path: str,
        quota: dict[str, Any],
        evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        rendered = self.quota_strategy.render_quota(quota)
        argv = ["mmsetquota", f"{self.template.filesystem_name}:{fileset_name}"]
        if rendered.get("block_limit"):
            argv.extend(["--block", rendered["block_limit"]])
        if rendered.get("files_limit"):
            argv.extend(["--files", rendered["files_limit"]])
        self._run(argv, evidence, side_effect=True)
        quota_state = self._read_quota(
            fileset_name, junction_path, quota, evidence, rendered=rendered
        )
        issue = _quota_readback_issue(quota, quota_state)
        if issue:
            raise GpfsSideEffectError(issue)
        return quota_state

    def _read_quota(
        self,
        fileset_name: str,
        junction_path: str,
        desired_quota: dict[str, Any],
        evidence: list[dict[str, Any]],
        *,
        rendered: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = self._run(
            [
                "mmlsquota",
                "-j",
                fileset_name,
                "-v",
                "-Y",
                self.template.filesystem_name,
            ],
            evidence,
        )
        row = _select_quota_row(parse_gpfs_y(result.stdout), fileset_name)
        observed_bytes = _quota_kb_to_bytes(
            _first_int(row, ("blockLimit", "block_limit", "limit", "BlockLimit"))
        )
        observed_files = _first_int(
            row, ("filesLimit", "files_limit", "fileLimit", "FilesLimit")
        )
        rendered = rendered or self.quota_strategy.render_quota(desired_quota)
        state = {
            "backend_type": GPFS_BACKEND_TYPE,
            "quota_backend": GPFS_QUOTA_BACKEND,
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "capacity": {
                "desired_bytes": desired_quota.get("capacity_bytes"),
                "applied_bytes": desired_quota.get("capacity_bytes"),
                "observed_bytes": observed_bytes,
                "backend_key": "blockLimit",
                "rendered_limit": rendered.get("block_limit"),
            },
            "file_count": {
                "desired_count": desired_quota.get("file_count"),
                "applied_count": desired_quota.get("file_count"),
                "observed_count": observed_files,
                "backend_key": "filesLimit",
                "rendered_limit": rendered.get("files_limit"),
            },
            "backend_rounding": {
                "capacity_unit": "KiB",
                "rounded_up": bool(
                    desired_quota.get("capacity_bytes")
                    and int(desired_quota["capacity_bytes"]) % 1024
                ),
            },
        }
        used_bytes = _quota_kb_to_bytes(
            _first_int(row, ("blockUsage", "usage", "KB", "kb"))
        )
        used_files = _first_int(row, ("filesUsage", "files", "fileUsage"))
        if used_bytes is not None or used_files is not None:
            state["usage_evidence"] = {
                "usage_source": "mmlsquota",
                "used_bytes": used_bytes,
                "used_files": used_files,
                "admission_input": False,
            }
        return state

    def _read_fileset(
        self,
        fileset_name: str,
        evidence: list[dict[str, Any]],
        *,
        fail_on_error: bool = True,
    ) -> dict[str, Any]:
        result = self._run(
            ["mmlsfileset", self.template.filesystem_name, fileset_name, "-L", "-Y"],
            evidence,
            fail=fail_on_error,
        )
        if result.returncode != 0:
            return {"exists": False, "fileset_name": fileset_name}
        return _fileset_state_from_rows(parse_gpfs_y(result.stdout), fileset_name)

    def _read_fileset_by_junction(
        self, junction_path: str, evidence: list[dict[str, Any]]
    ) -> dict[str, Any]:
        result = self._run(
            [
                "mmlsfileset",
                self.template.filesystem_name,
                "-J",
                junction_path,
                "-L",
                "-Y",
            ],
            evidence,
        )
        state = _fileset_state_from_rows(parse_gpfs_y(result.stdout), None)
        if not state.get("exists"):
            raise BackendPreconditionError("GPFS linked fileset missing")
        return state

    def _run(
        self,
        argv: list[str],
        evidence: list[dict[str, Any]],
        *,
        fail: bool = True,
        side_effect: bool = False,
    ) -> CommandResult:
        executor = self.executor or self.template.executor()
        result = executor.run(
            argv, timeout_seconds=self.template.command_timeout_seconds
        )
        evidence.append(_command_evidence(result, side_effect=side_effect))
        if fail and result.returncode != 0:
            message = (
                result.stderr.strip() or result.stdout.strip() or "GPFS command failed"
            )
            raise BackendPreconditionError(message)
        return result

    def _fileset_name(self, directory_name: str) -> str:
        fileset_name = self.template.fileset_name_template.format(
            directory_name=directory_name,
            storage_name=self.template.storage_name,
        )
        if not GPFS_FILESET_NAME_PATTERN.match(fileset_name):
            raise BackendPreconditionError("unsafe GPFS fileset name")
        return fileset_name

    def _junction_path(self, directory_name: str) -> str:
        if self.template.quota_scope != "fileset":
            raise BackendPreconditionError(
                "GPFS filesystem operations require fileset quota_scope"
            )
        if not self.template.filesystem_name:
            raise BackendPreconditionError("GPFS filesystem_name is required")
        if not self.template.mount_path:
            raise BackendPreconditionError("GPFS mount_path is required")
        if not self.template.managed_root:
            raise BackendPreconditionError("GPFS managed_root is required")
        mount_path = os.path.normpath(self.template.mount_path)
        root = os.path.normpath(self.template.managed_root)
        if os.path.commonpath([mount_path, root]) != mount_path:
            raise BackendPreconditionError("GPFS managed_root must be under mount_path")
        path = os.path.normpath(os.path.join(root, directory_name))
        if os.path.commonpath([root, path]) != root:
            raise BackendPreconditionError("GPFS junction path escaped managed_root")
        return path


@dataclass(frozen=True)
class GpfsDataManagementAdapter:
    template: GpfsBackendTemplate

    def worker_pool(self, storage_name: str) -> dict[str, Any]:
        return {
            "selection": "agent-inventory",
            "backend_type": GPFS_BACKEND_TYPE,
            "required_mounts": [storage_name],
            "mount_path": self.template.mount_path,
            "filesystem_name": self.template.filesystem_name,
            "data_network": self.template.data_network,
            "tool_candidates": ["dsync", "nsync", "drm", "dscan"],
            "requires_posix_identity": True,
            "candidates": [],
        }


def render_gpfs_block_limit(capacity_bytes: int) -> str:
    # GPFS rounds block quotas up to 8 MiB (8192 KiB) boundaries internally.
    # Pre-align to avoid read-back mismatch after mmsetquota.
    _8mib_kib = 8 * 1024
    kib = max(_8mib_kib, math.ceil(capacity_bytes / 1024))
    kib = math.ceil(kib / _8mib_kib) * _8mib_kib
    return f"{kib}K:{kib}K"


def parse_gpfs_y(output: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_headers: list[str] | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        parts = line.split(":")
        if "HEADER" in parts:
            current_headers = parts[parts.index("HEADER") + 1 :]
            continue
        if not current_headers or len(parts) < len(current_headers):
            continue
        values = parts[-len(current_headers) :]
        row = dict(zip(current_headers, values, strict=False))
        row["_raw"] = line
        rows.append(row)
    return rows


def _directory_name(desired: dict[str, Any]) -> str:
    directory_name = str(desired.get("directory_name") or "")
    validate_storage_root_basename("directory_name", directory_name)
    return directory_name


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


def _rows_contain_enabled(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    disabled = {"no", "none", "disabled", "off", "false", "0"}
    enabled_fields = (
        "data",
        "value",
        "enabled",
        "quota",
        "quotas",
        "perfilesetQuota",
        "perfileset_quota",
    )
    for row in rows:
        values = [
            row[field].strip().lower() for field in enabled_fields if row.get(field)
        ]
        if any(value and value not in disabled for value in values):
            return True
    return False


def _fileset_state_from_rows(
    rows: list[dict[str, str]], fileset_name: str | None
) -> dict[str, Any]:
    row = _select_fileset_row(rows, fileset_name)
    if not row:
        return {"exists": False, "fileset_name": fileset_name}
    name = _first_str(row, ("filesetName", "fileset", "name", "Name")) or fileset_name
    return {
        "exists": True,
        "fileset_name": name,
        "status": _first_str(row, ("status", "Status")),
        "junction_path": _first_str(
            row, ("path", "Path", "junctionPath", "JunctionPath")
        ),
        "inode_space": _first_str(row, ("inodeSpace", "InodeSpace")),
        "raw": row,
    }


def _select_fileset_row(
    rows: list[dict[str, str]], fileset_name: str | None
) -> dict[str, str]:
    if not rows:
        return {}
    if fileset_name is None:
        return rows[0]
    for row in rows:
        if _first_str(row, ("filesetName", "fileset", "name", "Name")) == fileset_name:
            return row
    return rows[0] if len(rows) == 1 else {}


def _select_quota_row(rows: list[dict[str, str]], fileset_name: str) -> dict[str, str]:
    for row in rows:
        if (
            _first_str(row, ("filesetName", "fileset", "objectName", "name"))
            == fileset_name
        ):
            return row
    return rows[0] if rows else {}


def _first_str(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value not in {None, ""}:
            return str(value)
    return None


def _first_int(row: dict[str, str], keys: tuple[str, ...]) -> int | None:
    value = _first_str(row, keys)
    if value is None:
        return None
    return _parse_gpfs_int(value)


def _parse_gpfs_int(value: str) -> int | None:
    text = value.strip()
    if not text or text.lower() in {"none", "no", "no limits", "--"}:
        return None
    match = re.match(r"^([0-9]+)", text)
    return int(match.group(1)) if match else None


def _quota_kb_to_bytes(value: int | None) -> int | None:
    return value * 1024 if value is not None else None


def _quota_from_state(quota_state: dict[str, Any]) -> dict[str, int]:
    quota: dict[str, int] = {}
    observed_bytes = (quota_state.get("capacity") or {}).get("observed_bytes")
    observed_files = (quota_state.get("file_count") or {}).get("observed_count")
    if observed_bytes is not None:
        quota["capacity_bytes"] = int(observed_bytes)
    if observed_files is not None:
        quota["file_count"] = int(observed_files)
    return quota


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
        # GPFS rounds block quota up to 8 MiB; compare against the rounded value
        # and tolerate up to QUOTA_CAPACITY_TOLERANCE_BYTES extra (observed must
        # not be below desired — that would mean the quota under-applied).
        _8mib = 8 * 1024 * 1024
        rounded = math.ceil(int(desired_bytes) / _8mib) * _8mib
        diff = int(observed_bytes) - rounded
        if diff < 0 or diff > QUOTA_CAPACITY_TOLERANCE_BYTES:
            return "GPFS quota capacity read-back mismatch"
    desired_files = desired_quota.get("file_count")
    observed_files = (quota_state.get("file_count") or {}).get("observed_count")
    if desired_files is not None and observed_files is not None:
        if int(observed_files) != int(desired_files):
            return "GPFS quota file-count read-back mismatch"
    return None
