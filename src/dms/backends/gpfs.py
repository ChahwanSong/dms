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

from dms.adapters import AdapterResult, BackendPreconditionError
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
        return self.local.run(
            ["ssh", self.host, shlex.join(argv)],
            timeout_seconds=timeout_seconds,
        )


@dataclass(frozen=True)
class GpfsBackendTemplate:
    storage_name: str
    filesystem_name: str
    mount_path: str
    fileset_root: str | None
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
        rm_worker_node = template.get("ssh_host") or (rm_workers[0] if rm_workers else None)
        return cls(
            storage_name=mapping["storage_name"],
            filesystem_name=template.get("filesystem_name", mapping["storage_name"]),
            mount_path=template.get("mount_path", ""),
            fileset_root=template.get("fileset_root"),
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
            "fileset_root": self.fileset_root,
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

    @classmethod
    def from_storage_mapping(
        cls,
        mapping: dict[str, Any],
        *,
        executor: GpfsCommandExecutor | None = None,
    ) -> "GpfsFilesystemBackendAdapter":
        template = GpfsBackendTemplate.from_storage_mapping(mapping)
        return cls(template=template, executor=executor or template.executor())

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

        quota = desired.get("quota") or {}
        side_effect_started = False
        try:
            self._run(
                ["mmcrfileset", self.template.filesystem_name, fileset_name, "--inode-space", "new"],
                evidence,
                side_effect=True,
            )
            side_effect_started = True
            quota_state = {}
            if quota:
                quota_state = self._apply_quota(fileset_name, junction_path, quota, evidence)
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
            group_name = desired.get("access_group")
            if group_name:
                self._run(["chgrp", group_name, junction_path], evidence, side_effect=True)
            mode = str(desired.get("mode") or "0770")
            self._run(["chmod", mode, junction_path], evidence, side_effect=True)
            marker = _marker(plan, desired, fileset_name, junction_path, management_mode="full")
            self._write_marker(junction_path, marker, evidence, side_effect=True)
            fileset_state = self._read_fileset(fileset_name, evidence)
        except BackendPreconditionError as exc:
            if side_effect_started:
                raise GpfsSideEffectError(str(exc)) from exc
            raise

        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": OperationKind.FILESYSTEM_CREATE.value,
            "backend": self.template.metadata(),
            "directory_name": directory_name,
            "fileset_name": fileset_name,
            "junction_path": junction_path,
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
        quota = desired.get("quota")
        if not quota:
            raise BackendPreconditionError("GPFS quota is required for update")
        evidence: list[dict[str, Any]] = []
        self._capability(evidence, require_quota=True)
        self._read_fileset(fileset_name, evidence)
        quota_state = self._apply_quota(fileset_name, junction_path, quota, evidence)
        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": OperationKind.FILESYSTEM_UPDATE.value,
            "backend": self.template.metadata(),
            "quota": quota,
            "quota_state": quota_state,
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "command_evidence": evidence,
            "backend_side_effect": True,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "path": junction_path,
            "exists": True,
            "verified": True,
        }
        return AdapterResult(applied, observed, "GPFS fileset quota update completed")

    def block(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        self._read_fileset(fileset_name, evidence)
        block = bool(desired.get("block"))
        mode = "0000" if block else str((desired.get("block_state") or {}).get("previous_mode") or desired.get("mode") or "0770")
        self._run(["chmod", mode, junction_path], evidence, side_effect=True)
        block_state = dict(desired.get("block_state") or {})
        block_state.update({"blocked": block, "block_mode": "chmod-0000" if block else "restored"})
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
            raise BackendPreconditionError("GPFS delete refused for imported/quota-only fileset")
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        self._read_fileset(fileset_name, evidence)
        self._run(["mmunlinkfileset", self.template.filesystem_name, fileset_name], evidence, side_effect=True)
        self._run(["mmdelfileset", self.template.filesystem_name, fileset_name], evidence, side_effect=True)
        applied = {
            "adapter": "gpfs-fileset-command",
            "operation": OperationKind.FILESYSTEM_DELETE.value,
            "backend": self.template.metadata(),
            "fileset_name": fileset_name,
            "junction_path": junction_path,
            "deleted": True,
            "command_evidence": evidence,
            "backend_side_effect": True,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "exists": False,
            "verified": True,
            "resource_status": "Deleted",
        }
        return AdapterResult(applied, observed, "GPFS fileset deleted")

    def consistency_check(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        fileset_state = self._read_fileset(fileset_name, evidence, fail_on_error=False)
        if not fileset_state.get("exists"):
            issues = [{"issue_type": "filesystem_quota_missing", "field": "fileset", "reason": "missing"}]
            status = "Missing"
            quota_state = {}
        else:
            quota_state = self._read_quota(fileset_name, junction_path, desired.get("quota") or {}, evidence)
            issues = _quota_check_issues(desired.get("quota") or {}, quota_state)
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
            "quota_status": status,
            "issues": issues,
            "verified": status != "CheckFailed",
        }
        return AdapterResult(applied, observed, "GPFS fileset consistency check completed")

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

    def _adopt_existing(self, plan: dict[str, Any], *, management_mode: str) -> AdapterResult:
        desired = plan["desired_state"]
        directory_name = _directory_name(desired)
        fileset_name = self._fileset_name(directory_name)
        junction_path = self._junction_path(directory_name)
        evidence: list[dict[str, Any]] = []
        fileset_state = self._read_fileset_by_junction(junction_path, evidence)
        if fileset_state.get("fileset_name") not in {None, fileset_name}:
            raise BackendPreconditionError("GPFS fileset name mismatch")
        quota = desired.get("quota") or {}
        quota_state = {}
        if quota:
            self._capability(evidence, require_quota=True)
            quota_state = self._apply_quota(fileset_name, junction_path, quota, evidence)
        if desired.get("initialize_marker", True):
            marker = _marker(plan, desired, fileset_name, junction_path, management_mode=management_mode)
            self._write_marker(junction_path, marker, evidence, side_effect=True)
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
            "backend_side_effect": bool(quota or desired.get("initialize_marker", True)),
            "command_evidence": evidence,
        }
        observed = {
            **applied,
            "resource_key": plan["resource_key"],
            "exists": True,
            "verified": True,
        }
        return AdapterResult(applied, observed, f"GPFS fileset {management_mode} adoption completed")

    def _capability(self, evidence: list[dict[str, Any]], *, require_quota: bool) -> dict[str, Any]:
        for command in (
            "mmcrfileset",
            "mmlinkfileset",
            "mmlsfileset",
            "mmsetquota",
            "mmlsquota",
            "mmunlinkfileset",
            "mmdelfileset",
        ):
            result = self._run(["sh", "-c", f"command -v {command}"], evidence, fail=False)
            if result.returncode != 0:
                raise BackendPreconditionError(f"GPFS command missing: {command}")
        if require_quota:
            quota = self._run(["mmlsfs", self.template.filesystem_name, "-Q", "-Y"], evidence)
            quota_rows = parse_gpfs_y(quota.stdout)
            if not _rows_contain_enabled(quota_rows):
                raise BackendPreconditionError("GPFS filesystem quota disabled or unknown")
            perfileset = self._run(
                ["mmlsfs", self.template.filesystem_name, "--perfileset-quota", "-Y"],
                evidence,
            )
            perfileset_rows = parse_gpfs_y(perfileset.stdout)
            if not _rows_contain_enabled(perfileset_rows):
                raise BackendPreconditionError("GPFS per-fileset quota disabled or unknown")
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
            "supports_marker": True,
            "filesystem_name": self.template.filesystem_name,
            "fileset_root": self.template.fileset_root,
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
        quota_state = self._read_quota(fileset_name, junction_path, quota, evidence, rendered=rendered)
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
            ["mmlsquota", "-j", fileset_name, "-v", "-Y", self.template.filesystem_name],
            evidence,
        )
        row = _select_quota_row(parse_gpfs_y(result.stdout), fileset_name)
        observed_bytes = _quota_kb_to_bytes(_first_int(row, ("blockLimit", "block_limit", "limit", "BlockLimit")))
        observed_files = _first_int(row, ("filesLimit", "files_limit", "fileLimit", "FilesLimit"))
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
        used_bytes = _quota_kb_to_bytes(_first_int(row, ("blockUsage", "usage", "KB", "kb")))
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
            ["mmlsfileset", self.template.filesystem_name, "-J", junction_path, "-L", "-Y"],
            evidence,
        )
        state = _fileset_state_from_rows(parse_gpfs_y(result.stdout), None)
        if not state.get("exists"):
            raise BackendPreconditionError("GPFS linked fileset missing")
        return state

    def _write_marker(
        self,
        junction_path: str,
        marker: dict[str, Any],
        evidence: list[dict[str, Any]],
        *,
        side_effect: bool,
    ) -> None:
        script = (
            "import json, os, sys; "
            "path=sys.argv[1]; marker=json.loads(sys.argv[2]); "
            "os.makedirs(path, exist_ok=True); "
            "open(os.path.join(path, '.dms-resource.json'), 'w', encoding='utf-8').write(json.dumps(marker, sort_keys=True))"
        )
        self._run(["python3", "-c", script, junction_path, json.dumps(marker, sort_keys=True)], evidence, side_effect=side_effect)

    def _run(
        self,
        argv: list[str],
        evidence: list[dict[str, Any]],
        *,
        fail: bool = True,
        side_effect: bool = False,
    ) -> CommandResult:
        executor = self.executor or self.template.executor()
        result = executor.run(argv, timeout_seconds=self.template.command_timeout_seconds)
        evidence.append(_command_evidence(result, side_effect=side_effect))
        if fail and result.returncode != 0:
            message = result.stderr.strip() or result.stdout.strip() or "GPFS command failed"
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
            raise BackendPreconditionError("GPFS filesystem operations require fileset quota_scope")
        if not self.template.filesystem_name:
            raise BackendPreconditionError("GPFS filesystem_name is required")
        if not self.template.mount_path:
            raise BackendPreconditionError("GPFS mount_path is required")
        if not self.template.fileset_root:
            raise BackendPreconditionError("GPFS fileset_root is required")
        mount_path = os.path.normpath(self.template.mount_path)
        root = os.path.normpath(self.template.fileset_root)
        if os.path.commonpath([mount_path, root]) != mount_path:
            raise BackendPreconditionError("GPFS fileset_root must be under mount_path")
        path = os.path.normpath(os.path.join(root, directory_name))
        if os.path.commonpath([root, path]) != root:
            raise BackendPreconditionError("GPFS junction path escaped fileset root")
        return path


@dataclass
class GpfsKubernetesNamespaceQuotaAdapter:
    template: GpfsBackendTemplate

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {
            "cluster_name": cluster_name,
            "namespace_name": namespace_name,
            "backend_type": GPFS_BACKEND_TYPE,
            "side_effect": "not-executed-phase1",
        }

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult:
        return self.apply_resource_quota(plan)

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        hard_limits = self._hard_limits(desired)
        backend = self.template.metadata()
        applied = {
            "adapter": "gpfs-kubernetes-quota-stub",
            "resource_quota_name": "dms-storage-quota",
            "backend": backend,
            "hard": hard_limits,
            "side_effect": "not-executed-phase1",
        }
        observed = {
            "adapter": "gpfs-kubernetes-quota-stub",
            "verified": True,
            "resource_quota_name": "dms-storage-quota",
            "backend": backend,
            "hard": hard_limits,
        }
        return AdapterResult(
            applied_state=applied,
            observed_state=observed,
            message="GPFS Kubernetes namespace quota skeleton completed",
        )

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        backend = self.template.metadata()
        return AdapterResult(
            applied_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "deleted": True,
                "backend": backend,
                "resource_quota_name": "dms-storage-quota",
            },
            observed_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "verified": True,
                "deleted": True,
                "backend": backend,
            },
            message="GPFS Kubernetes namespace quota delete skeleton completed",
        )

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        backend = self.template.metadata()
        return AdapterResult(
            applied_state={"adapter": "gpfs-kubernetes-quota-stub", "backend": backend},
            observed_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "verified": True,
                "synced": True,
                "backend": backend,
            },
            message="GPFS Kubernetes namespace quota sync skeleton completed",
        )

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        backend = self.template.metadata()
        return AdapterResult(
            applied_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "backend": backend,
                "backend_side_effect": False,
            },
            observed_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "verified": True,
                "consistency_status": "Consistent",
                "backend": backend,
            },
            message="GPFS Kubernetes namespace quota consistency check skeleton completed",
        )

    def audit_resource_quotas(self, plan: dict[str, Any]) -> AdapterResult:
        backend = self.template.metadata()
        return AdapterResult(
            applied_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "backend": backend,
                "backend_side_effect": False,
                "operation": "resourcequota.audit",
            },
            observed_state={
                "adapter": "gpfs-kubernetes-quota-stub",
                "verified": True,
                "audit_status": "Consistent",
                "target_count": 0,
                "issue_count": 0,
                "targets": [],
                "backend": backend,
            },
            message="GPFS Kubernetes namespace quota audit skeleton completed",
        )

    def _hard_limits(self, desired: dict[str, Any]) -> dict[str, Any]:
        quota = desired.get("quota", {})
        storage_class_name = self.template.storage_class_name
        hard = {
            "requests.storage": quota.get("requests_storage_bytes")
            or quota.get("capacity_bytes"),
            "persistentvolumeclaims": quota.get("pvc_count"),
        }
        if storage_class_name:
            hard[f"{storage_class_name}.storageclass.storage.k8s.io/requests.storage"] = (
                quota.get("storage_class_requests_storage_bytes")
                or quota.get("requests_storage_bytes")
                or quota.get("capacity_bytes")
            )
        return {key: value for key, value in hard.items() if value is not None}


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
    kib = max(1, math.ceil(capacity_bytes / 1024))
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


def _marker(
    plan: dict[str, Any],
    desired: dict[str, Any],
    fileset_name: str,
    junction_path: str,
    *,
    management_mode: str,
) -> dict[str, Any]:
    return {
        "managed_by": "dms",
        "resource_kind": "filesystem",
        "resource_key": plan["resource_key"],
        "storage_name": desired.get("storage_name"),
        "directory_name": desired.get("directory_name"),
        "management_mode": management_mode,
        "backend_type": GPFS_BACKEND_TYPE,
        "fileset_name": fileset_name,
        "junction_path": junction_path,
    }


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
            row[field].strip().lower()
            for field in enabled_fields
            if row.get(field)
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
        "junction_path": _first_str(row, ("path", "Path", "junctionPath", "JunctionPath")),
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
        if _first_str(row, ("filesetName", "fileset", "objectName", "name")) == fileset_name:
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


def _quota_readback_issue(
    desired_quota: dict[str, Any], quota_state: dict[str, Any]
) -> str | None:
    desired_bytes = desired_quota.get("capacity_bytes")
    observed_bytes = (quota_state.get("capacity") or {}).get("observed_bytes")
    if desired_bytes is not None and observed_bytes is not None:
        rounded = math.ceil(int(desired_bytes) / 1024) * 1024
        if int(observed_bytes) != rounded:
            return "GPFS quota capacity read-back mismatch"
    desired_files = desired_quota.get("file_count")
    observed_files = (quota_state.get("file_count") or {}).get("observed_count")
    if desired_files is not None and observed_files is not None:
        if int(observed_files) != int(desired_files):
            return "GPFS quota file-count read-back mismatch"
    return None
