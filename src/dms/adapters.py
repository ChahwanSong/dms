from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Protocol
from urllib.parse import urlparse

from .config import Settings


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
    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]: ...

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
    def lookup(self, provider: str, posix_username: str) -> IdentityLookupResult | None: ...


class IdentityGroupManager(Protocol):
    def ensure_group_members(
        self,
        *,
        group_name: str,
        users: list[str],
        resource_key: str,
    ) -> dict[str, Any]: ...

    def delete_group(self, *, group_name: str) -> dict[str, Any]: ...


class IdentityLookupConfigurationError(RuntimeError):
    pass


class IdentityLookupReadError(RuntimeError):
    pass


class BackendPreconditionError(RuntimeError):
    pass


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

    def create_job(self, plan: dict[str, Any], data_job: dict[str, Any]) -> AdapterResult: ...

    def get_job(self, job_ref: str) -> dict[str, Any]: ...

    def terminate_job(self, job_ref: str) -> AdapterResult: ...


@dataclass
class StubFilesystemBackendAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def _result(self, operation: str, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append((operation, plan["plan_id"]))
        desired = plan["desired_state"]
        return AdapterResult(
            applied_state={"adapter": "filesystem-stub", "operation": operation, **desired},
            observed_state={
                "adapter": "filesystem-stub",
                "verified": True,
                "operation": operation,
                "resource_key": plan["resource_key"],
            },
            message=f"filesystem stub {operation} completed",
        )

    def create(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("create", plan)

    def update(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("update", plan)

    def block(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("block", plan)

    def initialize(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("initialize", plan)

    def delete(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("delete", plan)

    def consistency_check(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("consistency_check", plan)

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("sync_live_state", plan)

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("import_directory", plan)

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("assign_quota_only", plan)


@dataclass
class StubKubernetesNamespaceQuotaAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)
    resource_quotas: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    resource_quota_lists: dict[tuple[str, str], list[dict[str, Any]]] = field(
        default_factory=dict
    )

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {"cluster_name": cluster_name, "namespace_name": namespace_name, "exists": True}

    def read_resource_quota(
        self,
        cluster_name: str,
        namespace_name: str,
        resource_quota_name: str = "dms-storage-quota",
    ) -> dict[str, Any]:
        return self.resource_quotas.get(
            (cluster_name, namespace_name, resource_quota_name),
            {
                "exists": False,
                "cluster_name": cluster_name,
                "namespace": namespace_name,
                "name": resource_quota_name,
            },
        )

    def list_resource_quotas(
        self, cluster_name: str, namespace_name: str
    ) -> list[dict[str, Any]]:
        return self.resource_quota_lists.get((cluster_name, namespace_name), [])

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult:
        return self.apply_resource_quota(plan)

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("apply_resource_quota", plan["plan_id"]))
        desired = plan["desired_state"]
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-quota-stub",
                "resource_quota_name": "dms-storage-quota",
                **desired,
            },
            observed_state={
                "adapter": "kubernetes-quota-stub",
                "verified": True,
                "resource_quota_name": "dms-storage-quota",
            },
            message="kubernetes namespace quota stub completed",
        )

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("delete_resource_quota", plan["plan_id"]))
        return AdapterResult(
            applied_state={"deleted": True, "resource_quota_name": "dms-storage-quota"},
            observed_state={"verified": True, "deleted": True},
            message="kubernetes namespace quota delete stub completed",
        )

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("sync_live_state", plan["plan_id"]))
        return AdapterResult(
            applied_state=plan["desired_state"],
            observed_state={"verified": True, "synced": True},
            message="kubernetes namespace quota live sync stub completed",
        )

    def import_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("import_resource_quota", plan["plan_id"]))
        desired = dict(plan["desired_state"])
        desired.setdefault("resource_quota_hard", {})
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-quota-stub",
                "operation": "resourcequota.import",
                "backend_side_effect": False,
                "synced_desired_state": desired,
            },
            observed_state={
                "adapter": "kubernetes-quota-stub",
                "verified": True,
                "backend_side_effect": False,
                "imported": True,
            },
            message="kubernetes namespace quota import stub completed",
        )

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("check_resource_quota", plan["plan_id"]))
        return AdapterResult(
            applied_state={"backend_side_effect": False},
            observed_state={"verified": True, "consistency_status": "Consistent"},
            message="kubernetes namespace quota consistency check stub completed",
        )

    def audit_resource_quotas(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("audit_resource_quotas", plan["plan_id"]))
        return _audit_kubernetes_resource_quotas(self, plan)


def render_kubernetes_resource_quota_hard(desired_state: dict[str, Any]) -> dict[str, str]:
    quota = desired_state.get("quota") or {}
    hard: dict[str, str] = {}
    storage_bytes = _positive_int(
        quota.get("requests_storage_bytes") or quota.get("capacity_bytes"),
        "quota.requests_storage_bytes",
    )
    pvc_count = _positive_int(quota.get("pvc_count"), "quota.pvc_count")
    if storage_bytes is not None:
        hard["requests.storage"] = kubernetes_quantity_from_bytes(storage_bytes)
    if pvc_count is not None:
        hard["persistentvolumeclaims"] = str(pvc_count)
    storage_class_quotas = desired_state.get("storage_class_quotas") or []
    for entry in storage_class_quotas:
        if not isinstance(entry, dict):
            raise ValueError("storage_class_quotas entries must be objects")
        storage_class_name = entry.get("storage_class_name")
        if not storage_class_name:
            raise ValueError("storage_class_quotas[].storage_class_name is required")
        entry_bytes = _positive_int(
            _first_present(entry, "requests_storage_bytes", "capacity_bytes"),
            "storage_class_quotas[].requests_storage_bytes",
        )
        if entry_bytes is None and len(storage_class_quotas) == 1:
            entry_bytes = storage_bytes
        if entry_bytes is not None:
            hard[
                kubernetes_storage_class_quota_key(
                    storage_class_name, "requests.storage"
                )
            ] = kubernetes_quantity_from_bytes(entry_bytes)
        entry_pvc_count = _positive_int(
            _first_present(entry, "pvc_count", "pvc_count_quota"),
            "storage_class_quotas[].pvc_count",
        )
        if entry_pvc_count is not None:
            hard[
                kubernetes_storage_class_quota_key(
                    storage_class_name, "persistentvolumeclaims"
                )
            ] = str(entry_pvc_count)
    if not hard:
        raise ValueError("at least one ResourceQuota hard limit is required")
    return hard


def zero_kubernetes_resource_quota_hard(hard: dict[str, str]) -> dict[str, str]:
    if not hard:
        raise ValueError("hard limits are required for ResourceQuota block")
    return {key: "0" for key in hard}


def kubernetes_quantity_from_bytes(value: int) -> str:
    size = _positive_int(value, "bytes")
    if size is None:
        raise ValueError("bytes are required")
    units = (("Gi", 1024**3), ("Mi", 1024**2), ("Ki", 1024))
    for suffix, multiplier in units:
        if size % multiplier == 0:
            return f"{size // multiplier}{suffix}"
    return str(size)


def kubernetes_quantity_to_bytes(value: Any) -> int:
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    units = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
    }
    for suffix, multiplier in units.items():
        if text.endswith(suffix):
            return int(text[: -len(suffix)]) * multiplier
    return int(text)


def kubernetes_storage_class_quota_key(
    storage_class_name: str, resource_name: str
) -> str:
    return f"{storage_class_name}.storageclass.storage.k8s.io/{resource_name}"


def parse_kubernetes_storage_class_quota_key(key: str) -> tuple[str, str] | None:
    marker = ".storageclass.storage.k8s.io/"
    if marker not in key:
        return None
    storage_class_name, resource_name = key.split(marker, 1)
    if not storage_class_name or not resource_name:
        return None
    return storage_class_name, resource_name


def kubernetes_resource_quota_value_to_base_units(key: str, value: Any) -> int:
    if key == "persistentvolumeclaims" or key.endswith("/persistentvolumeclaims"):
        return int(value)
    return kubernetes_quantity_to_bytes(value)


@dataclass(frozen=True)
class KubernetesNamespaceQuotaLiveAdapter:
    cluster_kubeconfigs: dict[str, str] = field(default_factory=dict)
    cluster_control_hosts: dict[str, str] = field(default_factory=dict)
    mode: str = "ssh-kubectl"
    timeout_seconds: int = 30

    @classmethod
    def from_settings(cls, settings: Settings) -> "KubernetesNamespaceQuotaLiveAdapter":
        return cls(
            cluster_kubeconfigs=settings.cluster_kubeconfigs or {},
            cluster_control_hosts=settings.cluster_control_hosts or {},
            mode=settings.kubernetes_mutation_mode,
            timeout_seconds=settings.kubernetes_mutation_timeout_seconds,
        )

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        completed = self._kubectl(
            cluster_name,
            ["get", "namespace", namespace_name, "-o", "json"],
            check=False,
        )
        if completed.returncode != 0:
            if _kubectl_not_found(completed.stderr):
                return {
                    "cluster_name": cluster_name,
                    "namespace_name": namespace_name,
                    "exists": False,
                }
            raise KubernetesMutationError(
                f"failed to read namespace {cluster_name}/{namespace_name}: "
                f"{completed.stderr.strip()}"
            )
        payload = _json_stdout(completed.stdout, "namespace")
        return {
            "cluster_name": cluster_name,
            "namespace_name": namespace_name,
            "exists": True,
            "uid": payload.get("metadata", {}).get("uid"),
            "labels": payload.get("metadata", {}).get("labels") or {},
            "annotations": payload.get("metadata", {}).get("annotations") or {},
        }

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        cluster_name = desired["cluster_name"]
        namespace_name = desired["namespace_name"]
        namespace = self._ensure_namespace(
            cluster_name=cluster_name,
            namespace_name=namespace_name,
            plan=plan,
            allow_create=bool(desired.get("allow_namespace_create")),
        )
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-namespace-quota-live",
                "operation": "namespace.apply",
                "backend_side_effect": namespace["created"],
                "namespace": namespace,
            },
            observed_state={
                "adapter": "kubernetes-namespace-quota-live",
                "verified": namespace["exists"],
                "namespace": namespace,
            },
            message="Kubernetes namespace ensured",
        )

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        cluster_name = desired["cluster_name"]
        namespace_name = desired["namespace_name"]
        resource_quota_name = desired.get("resource_quota_name") or "dms-storage-quota"
        hard = desired.get("resource_quota_hard") or render_kubernetes_resource_quota_hard(
            desired
        )
        namespace = self._ensure_namespace(
            cluster_name=cluster_name,
            namespace_name=namespace_name,
            plan=plan,
            allow_create=bool(desired.get("allow_namespace_create")),
        )
        before = self.read_resource_quota(cluster_name, namespace_name, resource_quota_name)
        if before["exists"]:
            _ensure_dms_managed(
                before,
                resource_quota_name,
                resource_key=plan["resource_key"],
                allow_metadata_repair=True,
            )
        manifest = self._resource_quota_manifest(
            plan=plan,
            namespace_name=namespace_name,
            resource_quota_name=resource_quota_name,
            hard=hard,
        )
        self._kubectl(
            cluster_name,
            ["apply", "-f", "-"],
            input_text=json.dumps(manifest, sort_keys=True),
        )
        observed = self._read_resource_quota(
            cluster_name=cluster_name,
            namespace_name=namespace_name,
            resource_quota_name=resource_quota_name,
        )
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-namespace-quota-live",
                "operation": "resourcequota.apply",
                "backend_side_effect": True,
                "cluster_name": cluster_name,
                "namespace_name": namespace_name,
                "resource_quota_name": resource_quota_name,
                "namespace": namespace,
                "manifest": manifest,
                "hard": hard,
            },
            observed_state={
                "adapter": "kubernetes-namespace-quota-live",
                "verified": observed["exists"],
                "backend_side_effect": True,
                "cluster_name": cluster_name,
                "namespace_name": namespace_name,
                "resource_quota_name": resource_quota_name,
                "namespace": namespace,
                "resource_quota": observed,
            },
            message="Kubernetes ResourceQuota live apply completed",
        )

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        cluster_name = desired["cluster_name"]
        namespace_name = desired["namespace_name"]
        resource_quota_name = desired.get("resource_quota_name") or "dms-storage-quota"
        before = self.read_resource_quota(cluster_name, namespace_name, resource_quota_name)
        if not before["exists"]:
            raise KubernetesMutationError(
                f"ResourceQuota does not exist: {cluster_name}/{namespace_name}/{resource_quota_name}"
            )
        _ensure_dms_managed(
            before,
            resource_quota_name,
            resource_key=desired.get("resource_key"),
        )
        self._kubectl(
            cluster_name,
            ["-n", namespace_name, "delete", "resourcequota", resource_quota_name],
        )
        after = self.read_resource_quota(cluster_name, namespace_name, resource_quota_name)
        namespace = self.read_namespace(cluster_name, namespace_name)
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-namespace-quota-live",
                "operation": "resourcequota.delete",
                "backend_side_effect": True,
                "cluster_name": cluster_name,
                "namespace_name": namespace_name,
                "resource_quota_name": resource_quota_name,
                "before": before,
            },
            observed_state={
                "adapter": "kubernetes-namespace-quota-live",
                "verified": not after["exists"],
                "backend_side_effect": True,
                "deleted": not after["exists"],
                "namespace": namespace,
                "resource_quota": after,
            },
            message="Kubernetes ResourceQuota live delete completed",
        )

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        cluster_name = desired["cluster_name"]
        namespace_name = desired["namespace_name"]
        resource_quota_name = desired.get("resource_quota_name") or "dms-storage-quota"
        observed = self.read_resource_quota(cluster_name, namespace_name, resource_quota_name)
        if not observed["exists"]:
            raise KubernetesMutationError(
                f"ResourceQuota does not exist: {cluster_name}/{namespace_name}/{resource_quota_name}"
            )
        _ensure_dms_managed(
            observed,
            resource_quota_name,
            resource_key=desired.get("resource_key"),
        )
        synced_desired = dict(desired)
        sync_warnings = _sync_desired_from_resource_quota_hard(
            synced_desired, observed["spec_hard"]
        )
        resource_quotas: list[dict[str, Any]] = []
        effective_warnings: list[dict[str, Any]] = []
        if desired.get("include_effective_quota"):
            resource_quotas = self.list_resource_quotas(cluster_name, namespace_name)
            effective_warnings = effective_resource_quota_warnings(
                resource_quotas=resource_quotas,
                dms_hard=synced_desired["resource_quota_hard"],
                resource_quota_name=resource_quota_name,
            )
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-namespace-quota-live",
                "operation": "resourcequota.sync",
                "backend_side_effect": False,
                "cluster_name": cluster_name,
                "namespace_name": namespace_name,
                "resource_quota_name": resource_quota_name,
                "synced_desired_state": synced_desired,
                "live_resource_quota": observed,
                "sync_warnings": sync_warnings,
            },
            observed_state={
                "adapter": "kubernetes-namespace-quota-live",
                "verified": True,
                "backend_side_effect": False,
                "synced": True,
                "resource_quota": observed,
                "sync_warnings": sync_warnings,
                "effective_quota_warnings": effective_warnings,
                "resource_quotas": resource_quotas,
            },
            message="Kubernetes ResourceQuota live state synced to DB",
        )

    def import_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        cluster_name = desired["cluster_name"]
        namespace_name = desired["namespace_name"]
        resource_quota_name = desired.get("resource_quota_name") or "dms-storage-quota"
        observed = self.read_resource_quota(cluster_name, namespace_name, resource_quota_name)
        if not observed["exists"]:
            raise KubernetesMutationError(
                f"ResourceQuota does not exist: {cluster_name}/{namespace_name}/{resource_quota_name}"
            )
        _ensure_dms_managed(
            observed,
            resource_quota_name,
            resource_key=desired.get("resource_key"),
        )
        synced_desired = dict(desired)
        if not synced_desired.get("storage_class_quotas"):
            synced_desired["storage_class_quotas"] = _infer_storage_class_quotas(
                hard=observed.get("spec_hard") or {},
                candidates=desired.get("storage_mapping_candidates") or [],
            )
        sync_warnings = _sync_desired_from_resource_quota_hard(
            synced_desired, observed["spec_hard"]
        )
        if sync_warnings:
            raise KubernetesMutationError(
                f"failed to infer all StorageClass quota keys: {sync_warnings}"
            )
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-namespace-quota-live",
                "operation": "resourcequota.import",
                "backend_side_effect": False,
                "cluster_name": cluster_name,
                "namespace_name": namespace_name,
                "resource_quota_name": resource_quota_name,
                "synced_desired_state": synced_desired,
                "live_resource_quota": observed,
                "annotation_update_unsupported": True,
            },
            observed_state={
                "adapter": "kubernetes-namespace-quota-live",
                "verified": True,
                "backend_side_effect": False,
                "imported": True,
                "resource_quota": observed,
                "annotation_update_unsupported": True,
                "previous_annotation_expires_at": (observed.get("annotations") or {}).get(
                    "dms.io/expires-at"
                ),
            },
            message="Kubernetes ResourceQuota live state imported to DB",
        )

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        cluster_name = desired["cluster_name"]
        namespace_name = desired["namespace_name"]
        resource_quota_name = desired.get("resource_quota_name") or "dms-storage-quota"
        observed = self.read_resource_quota(cluster_name, namespace_name, resource_quota_name)
        desired_hard = desired.get("resource_quota_hard") or render_kubernetes_resource_quota_hard(
            desired
        )
        issues: list[dict[str, Any]] = []
        status = "Consistent"
        if not observed["exists"]:
            status = "Missing"
            issues.append(
                {
                    "issue_type": "kubernetes_quota_missing",
                    "field": "resource_quota",
                    "reason": "missing",
                }
            )
        else:
            metadata_issues = kubernetes_resource_quota_metadata_issues(
                observed,
                resource_quota_name=resource_quota_name,
                resource_key=desired.get("resource_key"),
            )
            if metadata_issues:
                status = "Drifted"
                issues.extend(metadata_issues)
            hard_issues = kubernetes_resource_quota_hard_issues(
                desired_hard=desired_hard,
                live_hard=observed.get("spec_hard") or {},
            )
            if hard_issues:
                status = "Drifted"
                issues.extend(_quota_drift_issues(hard_issues))
        resource_quotas: list[dict[str, Any]] = []
        effective_warnings: list[dict[str, Any]] = []
        if desired.get("include_effective_quota") and observed["exists"]:
            resource_quotas = self.list_resource_quotas(cluster_name, namespace_name)
            effective_warnings = effective_resource_quota_warnings(
                resource_quotas=resource_quotas,
                dms_hard=desired_hard,
                resource_quota_name=resource_quota_name,
            )
        return AdapterResult(
            applied_state={
                "adapter": "kubernetes-namespace-quota-live",
                "operation": "resourcequota.check",
                "backend_side_effect": False,
                "cluster_name": cluster_name,
                "namespace_name": namespace_name,
                "resource_quota_name": resource_quota_name,
            },
            observed_state={
                "adapter": "kubernetes-namespace-quota-live",
                "verified": status == "Consistent",
                "backend_side_effect": False,
                "resource_status": status,
                "consistency_status": status,
                "issues": issues,
                "desired_hard": desired_hard,
                "resource_quota": observed,
                "effective_quota_warnings": effective_warnings,
                "resource_quotas": resource_quotas,
            },
            message=f"Kubernetes ResourceQuota consistency check {status}",
        )

    def audit_resource_quotas(self, plan: dict[str, Any]) -> AdapterResult:
        return _audit_kubernetes_resource_quotas(self, plan)

    def read_resource_quota(
        self, cluster_name: str, namespace_name: str, resource_quota_name: str = "dms-storage-quota"
    ) -> dict[str, Any]:
        return self._read_resource_quota(
            cluster_name=cluster_name,
            namespace_name=namespace_name,
            resource_quota_name=resource_quota_name,
            allow_missing=True,
        )

    def list_resource_quotas(
        self, cluster_name: str, namespace_name: str
    ) -> list[dict[str, Any]]:
        completed = self._kubectl(
            cluster_name,
            ["-n", namespace_name, "get", "resourcequota", "-o", "json"],
        )
        payload = _json_stdout(completed.stdout, "resourcequota-list")
        return [
            _resource_quota_summary(item, cluster_name=cluster_name)
            for item in payload.get("items", [])
        ]

    def _ensure_namespace(
        self,
        *,
        cluster_name: str,
        namespace_name: str,
        plan: dict[str, Any],
        allow_create: bool,
    ) -> dict[str, Any]:
        existing = self.read_namespace(cluster_name, namespace_name)
        if existing["exists"]:
            existing["created"] = False
            return existing
        if not allow_create:
            raise KubernetesMutationError(
                f"namespace does not exist and allow_namespace_create is false: "
                f"{cluster_name}/{namespace_name}"
            )
        manifest = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": namespace_name,
                "labels": {
                    "app.kubernetes.io/managed-by": "dms",
                    "dms.io/resource-kind": "kubernetes-namespace-quota",
                },
                "annotations": {
                    "dms.io/resource-key": plan["resource_key"],
                    "dms.io/request-id": plan["request_id"],
                },
            },
        }
        self._kubectl(
            cluster_name,
            ["apply", "-f", "-"],
            input_text=json.dumps(manifest, sort_keys=True),
        )
        namespace = self.read_namespace(cluster_name, namespace_name)
        namespace["created"] = True
        return namespace

    def _resource_quota_manifest(
        self,
        *,
        plan: dict[str, Any],
        namespace_name: str,
        resource_quota_name: str,
        hard: dict[str, str],
    ) -> dict[str, Any]:
        desired = plan["desired_state"]
        storage_names = [
            entry["storage_name"]
            for entry in desired.get("storage_class_quotas") or []
            if isinstance(entry, dict) and entry.get("storage_name")
        ]
        annotations = {
            "dms.io/resource-key": plan["resource_key"],
            "dms.io/request-id": plan["request_id"],
            "dms.io/storage-names": ",".join(storage_names),
        }
        if desired.get("expires_at"):
            annotations["dms.io/expires-at"] = str(desired["expires_at"])
        return {
            "apiVersion": "v1",
            "kind": "ResourceQuota",
            "metadata": {
                "name": resource_quota_name,
                "namespace": namespace_name,
                "labels": {
                    "app.kubernetes.io/managed-by": "dms",
                    "dms.io/resource-kind": "kubernetes-namespace-quota",
                },
                "annotations": annotations,
            },
            "spec": {"hard": hard},
        }

    def _read_resource_quota(
        self,
        *,
        cluster_name: str,
        namespace_name: str,
        resource_quota_name: str,
        allow_missing: bool = False,
    ) -> dict[str, Any]:
        completed = self._kubectl(
            cluster_name,
            ["-n", namespace_name, "get", "resourcequota", resource_quota_name, "-o", "json"],
            check=not allow_missing,
        )
        if completed.returncode != 0 and allow_missing and _kubectl_not_found(completed.stderr):
            return {
                "exists": False,
                "name": resource_quota_name,
                "namespace": namespace_name,
                "cluster_name": cluster_name,
            }
        if completed.returncode != 0:
            raise KubernetesMutationError(
                f"failed to read ResourceQuota {cluster_name}/{namespace_name}/{resource_quota_name}: "
                f"{completed.stderr.strip()}"
            )
        payload = _json_stdout(completed.stdout, "resourcequota")
        summary = _resource_quota_summary(payload, cluster_name=cluster_name)
        summary["exists"] = True
        return summary

    def _kubectl(
        self,
        cluster_name: str,
        args: list[str],
        *,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = self._command(cluster_name, args)
        try:
            completed = subprocess.run(
                command,
                input=input_text,
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise KubernetesMutationError(
                f"Kubernetes mutation timed out for {cluster_name}: {exc}"
            ) from exc
        if check and completed.returncode != 0:
            raise KubernetesMutationError(
                f"kubectl failed for {cluster_name}: {completed.stderr.strip()}"
            )
        return completed

    def _command(self, cluster_name: str, args: list[str]) -> list[str]:
        if self.mode == "ssh-kubectl":
            host = self.cluster_control_hosts.get(cluster_name)
            if not host:
                raise KubernetesMutationError(
                    f"missing control host for cluster {cluster_name}"
                )
            quoted = " ".join(_shell_quote(part) for part in ["kubectl", *args])
            return ["ssh", host, quoted]
        if self.mode == "kubectl":
            command = ["kubectl"]
            kubeconfig = self.cluster_kubeconfigs.get(cluster_name)
            if kubeconfig:
                command.extend(["--kubeconfig", kubeconfig])
            command.extend(args)
            return command
        raise KubernetesMutationError(f"unsupported Kubernetes mutation mode: {self.mode}")


@dataclass
class StubStorageInventoryAdapter:
    reports: list[dict[str, Any]] = field(default_factory=list)

    def effective_inventory(self) -> dict[str, Any]:
        return {
            "rm": {"storage_classes": [], "quota_capabilities": [], "reports": self.reports},
            "dm": {"worker_pool": [], "tools": [], "reports": self.reports},
        }


@dataclass
class StaticKubernetesReadOnlyInventoryAdapter:
    inventory: dict[str, Any] = field(default_factory=lambda: {"clusters": {}})

    def read_inventory(self) -> dict[str, Any]:
        return self.inventory


@dataclass(frozen=True)
class KubectlReadOnlyInventoryAdapter:
    cluster_kubeconfigs: dict[str, str] = field(default_factory=dict)
    cluster_control_hosts: dict[str, str] = field(default_factory=dict)
    mode: str = "ssh-kubectl"
    timeout_seconds: int = 10

    @classmethod
    def from_settings(cls, settings: Settings) -> "KubectlReadOnlyInventoryAdapter":
        return cls(
            cluster_kubeconfigs=settings.cluster_kubeconfigs or {},
            cluster_control_hosts=settings.cluster_control_hosts or {},
            mode=settings.kubernetes_inventory_mode,
            timeout_seconds=settings.kubernetes_inventory_timeout_seconds,
        )

    def read_inventory(self) -> dict[str, Any]:
        clusters: dict[str, Any] = {}
        cluster_names = sorted(
            set(self.cluster_kubeconfigs.keys()) | set(self.cluster_control_hosts.keys())
        )
        for cluster_name in cluster_names:
            clusters[cluster_name] = self._read_cluster(cluster_name)
        return {"clusters": clusters}

    def _read_cluster(self, cluster_name: str) -> dict[str, Any]:
        if self.mode == "python-client":
            return self._read_cluster_python_client(cluster_name)
        nodes = self._kubectl_json(cluster_name, ["get", "nodes", "-o", "json"])
        storage_classes = self._kubectl_json(
            cluster_name, ["get", "storageclass", "-o", "json"]
        )
        try:
            csi_drivers = self._kubectl_json(
                cluster_name, ["get", "csidrivers.storage.k8s.io", "-o", "json"]
            )
        except KubernetesInventoryReadError:
            csi_drivers = {"items": []}
        return {
            "nodes": [_node_summary(item) for item in nodes.get("items", [])],
            "storage_classes": [
                _storage_class_summary(item) for item in storage_classes.get("items", [])
            ],
            "csi_drivers": [
                {"name": item.get("metadata", {}).get("name")}
                for item in csi_drivers.get("items", [])
            ],
        }

    def _read_cluster_python_client(self, cluster_name: str) -> dict[str, Any]:
        try:
            from kubernetes import client as k8s_client
            from kubernetes import config as k8s_config
        except ImportError as exc:
            raise KubernetesInventoryReadError(
                "python-client inventory mode requires installing the kubernetes extra"
            ) from exc

        try:
            kubeconfig = self.cluster_kubeconfigs.get(cluster_name)
            if kubeconfig:
                k8s_config.load_kube_config(config_file=kubeconfig)
            else:
                try:
                    k8s_config.load_incluster_config()
                except Exception:
                    k8s_config.load_kube_config()
            api_client = k8s_client.ApiClient()
            core = k8s_client.CoreV1Api(api_client)
            storage = k8s_client.StorageV1Api(api_client)
            nodes = api_client.sanitize_for_serialization(core.list_node())
            storage_classes = api_client.sanitize_for_serialization(
                storage.list_storage_class()
            )
            csi_drivers = api_client.sanitize_for_serialization(
                storage.list_csi_driver()
            )
        except Exception as exc:
            raise KubernetesInventoryReadError(
                f"Python Kubernetes inventory failed for {cluster_name}: {exc}"
            ) from exc

        return {
            "nodes": [_node_summary(item) for item in nodes.get("items", [])],
            "storage_classes": [
                _storage_class_summary(item) for item in storage_classes.get("items", [])
            ],
            "csi_drivers": [
                {"name": item.get("metadata", {}).get("name")}
                for item in csi_drivers.get("items", [])
            ],
        }

    def _kubectl_json(self, cluster_name: str, args: list[str]) -> dict[str, Any]:
        command = self._command(cluster_name, args)
        try:
            completed = subprocess.run(
                command,
                check=True,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise KubernetesInventoryReadError(
                f"read-only Kubernetes inventory failed for {cluster_name}: {exc}"
            ) from exc
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise KubernetesInventoryReadError(
                f"kubectl returned non-JSON output for {cluster_name}"
            ) from exc

    def _command(self, cluster_name: str, args: list[str]) -> list[str]:
        if self.mode == "ssh-kubectl":
            host = self.cluster_control_hosts.get(cluster_name)
            if not host:
                raise KubernetesInventoryReadError(
                    f"missing control host for cluster {cluster_name}"
                )
            quoted = " ".join(_shell_quote(part) for part in ["kubectl", *args])
            return ["ssh", host, quoted]
        if self.mode == "kubectl":
            command = ["kubectl"]
            kubeconfig = self.cluster_kubeconfigs.get(cluster_name)
            if kubeconfig:
                command.extend(["--kubeconfig", kubeconfig])
            command.extend(args)
            return command
        raise KubernetesInventoryReadError(f"unsupported Kubernetes inventory mode: {self.mode}")


def _node_summary(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata", {})
    spec = item.get("spec", {})
    return {
        "name": metadata.get("name"),
        "uid": metadata.get("uid"),
        "labels": metadata.get("labels") or {},
        "taints": spec.get("taints") or [],
    }


def _storage_class_summary(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata", {})
    return {
        "name": metadata.get("name"),
        "provisioner": item.get("provisioner"),
        "parameters": item.get("parameters") or {},
        "reclaim_policy": item.get("reclaimPolicy"),
        "volume_binding_mode": item.get("volumeBindingMode"),
    }


def _shell_quote(value: str) -> str:
    if not value:
        return "''"
    safe = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-=./:")
    if all(char in safe for char in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _positive_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{label} must be greater than zero")
    return parsed


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _json_stdout(stdout: str, kind: str) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise KubernetesMutationError(f"kubectl returned non-JSON {kind}") from exc


def _kubectl_not_found(stderr: str) -> bool:
    return "NotFound" in stderr or "not found" in stderr.lower()


def _ensure_dms_managed(
    resource_quota: dict[str, Any],
    resource_quota_name: str,
    *,
    resource_key: str | None = None,
    allow_metadata_repair: bool = False,
) -> None:
    labels = resource_quota.get("labels") or {}
    annotations = resource_quota.get("annotations") or {}
    if resource_quota.get("name") != resource_quota_name:
        raise KubernetesMutationError("unexpected ResourceQuota name")
    if labels.get("app.kubernetes.io/managed-by") != "dms":
        raise KubernetesMutationError(
            f"refusing to mutate non-DMS ResourceQuota: {resource_quota_name}"
        )
    resource_kind = labels.get("dms.io/resource-kind") or annotations.get(
        "dms.io/resource-kind"
    )
    if resource_kind != "kubernetes-namespace-quota":
        if resource_kind or not allow_metadata_repair:
            raise KubernetesMutationError(
                f"refusing to mutate ResourceQuota with invalid DMS resource kind: "
                f"{resource_kind!r}"
            )
    live_resource_key = annotations.get("dms.io/resource-key")
    if resource_key and live_resource_key != resource_key:
        if live_resource_key or not allow_metadata_repair:
            raise KubernetesMutationError(
                f"refusing to mutate ResourceQuota for different DMS resource key: "
                f"{live_resource_key!r}"
            )


def _resource_quota_summary(
    payload: dict[str, Any], *, cluster_name: str
) -> dict[str, Any]:
    metadata = payload.get("metadata", {})
    status = payload.get("status", {})
    spec = payload.get("spec", {})
    return {
        "exists": True,
        "cluster_name": cluster_name,
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "uid": metadata.get("uid"),
        "resource_version": metadata.get("resourceVersion"),
        "labels": metadata.get("labels") or {},
        "annotations": metadata.get("annotations") or {},
        "spec_hard": spec.get("hard") or {},
        "status_hard": status.get("hard") or {},
        "status_used": status.get("used") or {},
    }


def kubernetes_resource_quota_hard_issues(
    *, desired_hard: dict[str, str], live_hard: dict[str, str]
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for key in sorted(set(desired_hard) | set(live_hard)):
        if key not in desired_hard:
            issues.append(
                {
                    "field": "spec.hard",
                    "key": key,
                    "reason": "hard_limit_unexpected_live",
                    "desired": None,
                    "live": live_hard[key],
                }
            )
            continue
        if key not in live_hard:
            issues.append(
                {
                    "field": "spec.hard",
                    "key": key,
                    "reason": "hard_limit_missing_in_live",
                    "desired": desired_hard[key],
                    "live": None,
                }
            )
            continue
        if str(desired_hard[key]) != str(live_hard[key]):
            issues.append(
                {
                    "field": "spec.hard",
                    "key": key,
                    "reason": "hard_limit_drifted",
                    "desired": desired_hard[key],
                    "live": live_hard[key],
                }
            )
    return issues


def kubernetes_resource_quota_metadata_issues(
    resource_quota: dict[str, Any],
    *,
    resource_quota_name: str = "dms-storage-quota",
    resource_key: str | None = None,
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    labels = resource_quota.get("labels") or {}
    annotations = resource_quota.get("annotations") or {}
    if resource_quota.get("name") != resource_quota_name:
        issues.append(
            {
                "issue_type": "kubernetes_quota_metadata_drift",
                "field": "metadata.name",
                "reason": "unexpected_resource_quota_name",
                "desired": resource_quota_name,
                "live": resource_quota.get("name"),
            }
        )
    if labels.get("app.kubernetes.io/managed-by") != "dms":
        issues.append(
            {
                "issue_type": "kubernetes_quota_metadata_drift",
                "field": "metadata.labels.app.kubernetes.io/managed-by",
                "reason": "not_dms_managed",
                "desired": "dms",
                "live": labels.get("app.kubernetes.io/managed-by"),
            }
        )
    resource_kind = labels.get("dms.io/resource-kind") or annotations.get(
        "dms.io/resource-kind"
    )
    if resource_kind != "kubernetes-namespace-quota":
        issues.append(
            {
                "issue_type": "kubernetes_quota_metadata_drift",
                "field": "metadata.labels.dms.io/resource-kind",
                "reason": "resource_kind_mismatch",
                "desired": "kubernetes-namespace-quota",
                "live": resource_kind,
            }
        )
    if resource_key and annotations.get("dms.io/resource-key") != resource_key:
        issues.append(
            {
                "issue_type": "kubernetes_quota_metadata_drift",
                "field": "metadata.annotations.dms.io/resource-key",
                "reason": "resource_key_mismatch",
                "desired": resource_key,
                "live": annotations.get("dms.io/resource-key"),
            }
        )
    return issues


def kubernetes_quota_usage_pressure(
    *,
    hard: dict[str, str],
    used: dict[str, str],
    warning_percent: float = 80,
    critical_percent: float = 95,
    blocked: bool = False,
) -> list[dict[str, Any]]:
    pressure: list[dict[str, Any]] = []
    for key, hard_value in sorted(hard.items()):
        if key not in used:
            continue
        try:
            hard_units = kubernetes_resource_quota_value_to_base_units(key, hard_value)
            used_units = kubernetes_resource_quota_value_to_base_units(key, used[key])
        except (TypeError, ValueError):
            continue
        if hard_units == 0:
            if blocked:
                continue
            pressure.append(
                {
                    "issue_type": "quota_usage_critical",
                    "severity": "CRITICAL",
                    "key": key,
                    "used": used[key],
                    "hard": hard_value,
                    "used_percent": None,
                    "reason": "zero_hard_limit_without_block_state",
                }
            )
            continue
        percent = round((used_units / hard_units) * 100, 2)
        if percent >= critical_percent:
            pressure.append(
                {
                    "issue_type": "quota_usage_critical",
                    "severity": "CRITICAL",
                    "key": key,
                    "used": used[key],
                    "hard": hard_value,
                    "used_percent": percent,
                }
            )
        elif percent >= warning_percent:
            pressure.append(
                {
                    "issue_type": "quota_usage_warning",
                    "severity": "WARN",
                    "key": key,
                    "used": used[key],
                    "hard": hard_value,
                    "used_percent": percent,
                }
            )
    return pressure


def _quota_drift_issues(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "issue_type": "kubernetes_quota_drifted",
            **issue,
        }
        for issue in issues
    ]


def effective_resource_quota_warnings(
    *,
    resource_quotas: list[dict[str, Any]],
    dms_hard: dict[str, str],
    resource_quota_name: str = "dms-storage-quota",
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for resource_quota in resource_quotas:
        if resource_quota.get("name") == resource_quota_name:
            continue
        hard = resource_quota.get("spec_hard") or {}
        for key, value in sorted(hard.items()):
            if key not in dms_hard:
                warnings.append(
                    {
                        "type": "unknown_non_dms_quota_key",
                        "resource_quota_name": resource_quota.get("name"),
                        "key": key,
                        "non_dms_hard": value,
                    }
                )
                continue
            try:
                non_dms_value = kubernetes_resource_quota_value_to_base_units(key, value)
                dms_value = kubernetes_resource_quota_value_to_base_units(
                    key, dms_hard[key]
                )
            except (TypeError, ValueError):
                warnings.append(
                    {
                        "type": "unparseable_non_dms_quota_value",
                        "resource_quota_name": resource_quota.get("name"),
                        "key": key,
                        "dms_hard": dms_hard[key],
                        "non_dms_hard": value,
                    }
                )
                continue
            if non_dms_value == 0:
                warnings.append(
                    {
                        "type": "non_dms_quota_zero_limit",
                        "resource_quota_name": resource_quota.get("name"),
                        "key": key,
                        "dms_hard": dms_hard[key],
                        "non_dms_hard": value,
                    }
                )
            elif non_dms_value < dms_value:
                warnings.append(
                    {
                        "type": "non_dms_quota_more_restrictive",
                        "resource_quota_name": resource_quota.get("name"),
                        "key": key,
                        "dms_hard": dms_hard[key],
                        "non_dms_hard": value,
                    }
                )
    return warnings


def _audit_kubernetes_resource_quotas(
    adapter: KubernetesNamespaceQuotaAdapter, plan: dict[str, Any]
) -> AdapterResult:
    desired = plan["desired_state"]
    include_non_dms = bool(desired.get("include_non_dms"))
    include_usage_pressure = bool(desired.get("include_usage_pressure", True))
    thresholds = desired.get("usage_thresholds") or {}
    warning_percent = float(thresholds.get("warning_percent", 80))
    critical_percent = float(thresholds.get("critical_percent", 95))
    targets: list[dict[str, Any]] = []
    issue_count = 0
    partial_failure = False
    for target in desired.get("targets") or []:
        target_result = _audit_kubernetes_resource_quota_target(
            adapter=adapter,
            target=target,
            include_non_dms=include_non_dms,
            include_usage_pressure=include_usage_pressure,
            warning_percent=warning_percent,
            critical_percent=critical_percent,
        )
        targets.append(target_result)
        issue_count += (
            len(target_result.get("issues") or [])
            + len(target_result.get("usage_pressure") or [])
            + len(target_result.get("effective_quota_warnings") or [])
        )
        if target_result.get("resource_status") == "QueryFailed":
            partial_failure = True
    audit_status = (
        "Failed"
        if targets and all(target.get("resource_status") == "QueryFailed" for target in targets)
        else "PartialFailure"
        if partial_failure
        else "ActionRequired"
        if issue_count
        else "Consistent"
    )
    observed = {
        "adapter": "kubernetes-namespace-quota-live",
        "operation": "resourcequota.audit",
        "backend_side_effect": False,
        "verified": issue_count == 0 and not partial_failure,
        "audit_status": audit_status,
        "target_count": len(targets),
        "issue_count": issue_count,
        "targets": targets,
    }
    return AdapterResult(
        applied_state={
            "adapter": "kubernetes-namespace-quota-live",
            "operation": "resourcequota.audit",
            "backend_side_effect": False,
            "target_count": len(targets),
        },
        observed_state=observed,
        message=f"Kubernetes ResourceQuota audit {audit_status}",
    )


def _audit_kubernetes_resource_quota_target(
    *,
    adapter: KubernetesNamespaceQuotaAdapter,
    target: dict[str, Any],
    include_non_dms: bool,
    include_usage_pressure: bool,
    warning_percent: float,
    critical_percent: float,
) -> dict[str, Any]:
    cluster_name = target.get("cluster_name")
    namespace_name = target.get("namespace_name")
    resource_key = target.get("resource_key")
    resource_quota_name = (
        target.get("desired_state", {}).get("resource_quota_name") or "dms-storage-quota"
    )
    desired_hard = target.get("desired_hard") or {}
    result = {
        "cluster_name": cluster_name,
        "namespace_name": namespace_name,
        "resource_key": resource_key,
        "db_exists": bool(target.get("db_exists")),
        "desired_hard": desired_hard,
        "issues": [],
        "usage_pressure": [],
        "effective_quota_warnings": [],
        "diagnostics": [],
    }
    try:
        live = adapter.read_resource_quota(
            cluster_name, namespace_name, resource_quota_name
        )
        result["resource_quota"] = live
        issues: list[dict[str, Any]] = []
        if not live.get("exists"):
            issues.append(
                {
                    "issue_type": "kubernetes_quota_missing",
                    "field": "resource_quota",
                    "reason": "missing",
                }
            )
            resource_status = "Missing"
        else:
            metadata_issues = kubernetes_resource_quota_metadata_issues(
                live,
                resource_quota_name=resource_quota_name,
                resource_key=resource_key if target.get("db_exists") else None,
            )
            hard_issues = (
                _quota_drift_issues(
                    kubernetes_resource_quota_hard_issues(
                        desired_hard=desired_hard,
                        live_hard=live.get("spec_hard") or {},
                    )
                )
                if desired_hard
                else []
            )
            issues.extend(metadata_issues)
            issues.extend(hard_issues)
            resource_status = "Drifted" if issues else "Consistent"
            if include_usage_pressure:
                result["usage_pressure"] = kubernetes_quota_usage_pressure(
                    hard=live.get("spec_hard") or {},
                    used=live.get("status_used") or {},
                    warning_percent=warning_percent,
                    critical_percent=critical_percent,
                    blocked=bool(
                        target.get("desired_state", {})
                        .get("block_state", {})
                        .get("blocked")
                    ),
                )
            if include_non_dms:
                resource_quotas = adapter.list_resource_quotas(cluster_name, namespace_name)
                result["resource_quotas"] = resource_quotas
                result["effective_quota_warnings"] = effective_resource_quota_warnings(
                    resource_quotas=resource_quotas,
                    dms_hard=desired_hard or live.get("spec_hard") or {},
                    resource_quota_name=resource_quota_name,
                )
        result["issues"] = issues
        if (
            resource_status == "Consistent"
            and (result["usage_pressure"] or result["effective_quota_warnings"])
        ):
            resource_status = "ActionRequired"
        result["resource_status"] = resource_status
        return result
    except Exception as exc:  # noqa: BLE001 - audit should keep per-target diagnostics.
        result["resource_status"] = "QueryFailed"
        result["issues"] = [
            {
                "issue_type": "kubernetes_quota_query_failed",
                "field": "resource_quota",
                "reason": "query_failed",
                "message": str(exc),
            }
        ]
        result["diagnostics"] = [{"reason": "query_failed", "message": str(exc)}]
        return result


def _infer_storage_class_quotas(
    *, hard: dict[str, str], candidates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_storage_class: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        storage_class_name = candidate.get("storage_class_name")
        if not storage_class_name:
            continue
        by_storage_class.setdefault(storage_class_name, []).append(candidate)
    entries: dict[str, dict[str, Any]] = {}
    for key in sorted(hard):
        parsed = parse_kubernetes_storage_class_quota_key(key)
        if not parsed:
            continue
        storage_class_name, _ = parsed
        matches = by_storage_class.get(storage_class_name) or []
        if len(matches) != 1:
            raise KubernetesMutationError(
                f"cannot map StorageClass quota key {key!r} to one DMS storage mapping"
            )
        match = matches[0]
        entries.setdefault(
            storage_class_name,
            {
                "storage_name": match["storage_name"],
                "storage_class_name": storage_class_name,
            },
        )
    return list(entries.values())


def _sync_desired_from_resource_quota_hard(
    desired: dict[str, Any], hard: dict[str, str]
) -> list[dict[str, Any]]:
    desired["resource_quota_hard"] = dict(hard)
    warnings: list[dict[str, Any]] = []
    quota = dict(desired.get("quota") or {})
    if "requests.storage" in hard:
        quota["requests_storage_bytes"] = kubernetes_quantity_to_bytes(
            hard["requests.storage"]
        )
    if "persistentvolumeclaims" in hard:
        quota["pvc_count"] = int(hard["persistentvolumeclaims"])
    if quota:
        desired["quota"] = quota

    storage_class_quotas: list[dict[str, Any]] = []
    matched_storage_class_keys: set[str] = set()
    for entry in desired.get("storage_class_quotas") or []:
        if not isinstance(entry, dict):
            continue
        synced_entry = dict(entry)
        storage_class_name = synced_entry.get("storage_class_name")
        if storage_class_name:
            hard_key = kubernetes_storage_class_quota_key(
                storage_class_name, "requests.storage"
            )
            if hard_key in hard:
                synced_entry["requests_storage_bytes"] = kubernetes_quantity_to_bytes(
                    hard[hard_key]
                )
                matched_storage_class_keys.add(hard_key)
            pvc_key = kubernetes_storage_class_quota_key(
                storage_class_name, "persistentvolumeclaims"
            )
            if pvc_key in hard:
                synced_entry["pvc_count"] = int(hard[pvc_key])
                matched_storage_class_keys.add(pvc_key)
        storage_class_quotas.append(synced_entry)
    if storage_class_quotas:
        desired["storage_class_quotas"] = storage_class_quotas
    for key in sorted(hard):
        if parse_kubernetes_storage_class_quota_key(key) and key not in matched_storage_class_keys:
            warnings.append(
                {
                    "type": "unknown_storageclass_quota_key",
                    "key": key,
                    "live": hard[key],
                }
            )
    return warnings


@dataclass
class StubIdentityLookupAdapter:
    mappings: dict[tuple[str, str], IdentityLookupResult] = field(default_factory=dict)

    def lookup(self, provider: str, posix_username: str) -> IdentityLookupResult | None:
        return self.mappings.get((provider, posix_username))


@dataclass
class StubIdentityGroupManager:
    users: dict[str, IdentityLookupResult] = field(default_factory=dict)
    groups: dict[str, dict[str, Any]] = field(default_factory=dict)
    next_gid: int = 24000

    def ensure_group_members(
        self,
        *,
        group_name: str,
        users: list[str],
        resource_key: str,
    ) -> dict[str, Any]:
        missing = [user for user in users if user not in self.users]
        if missing:
            raise IdentityLookupReadError(
                f"LDAP users not found: {', '.join(sorted(missing))}"
            )
        group = self.groups.get(group_name)
        if not group:
            group = {
                "group_name": group_name,
                "gid": self.next_gid,
                "dn": f"cn={group_name},ou=groups,dc=testbed,dc=local",
                "members": [],
                "created": True,
                "resource_key": resource_key,
            }
            self.next_gid += 1
            self.groups[group_name] = group
        else:
            group = dict(group)
            group["created"] = False
        members = set(group.get("members") or [])
        members.update(users)
        self.groups[group_name]["members"] = sorted(members)
        group["members"] = sorted(members)
        group["identity_source"] = "stub-ldap"
        return group

    def delete_group(self, *, group_name: str) -> dict[str, Any]:
        existed = group_name in self.groups
        if existed:
            del self.groups[group_name]
        return {"group_name": group_name, "deleted": existed, "identity_source": "stub-ldap"}


@dataclass(frozen=True)
class LdapIdentityLookupAdapter:
    uri: str
    base_dn: str
    bind_dn: str | None = None
    bind_password: str | None = None
    user_search_base: str | None = None
    group_search_base: str | None = None
    user_filter: str = "(uid={username})"
    timeout_seconds: int = 5

    @classmethod
    def from_settings(cls, settings: Settings) -> "LdapIdentityLookupAdapter":
        if not settings.ldap_uri or not settings.ldap_base_dn:
            raise IdentityLookupConfigurationError(
                "DMS_LDAP_URI and DMS_LDAP_BASE_DN are required for direct LDAP identity lookup"
            )
        return cls(
            uri=settings.ldap_uri,
            base_dn=settings.ldap_base_dn,
            bind_dn=settings.ldap_bind_dn,
            bind_password=settings.ldap_bind_password,
            user_search_base=settings.ldap_user_search_base,
            group_search_base=settings.ldap_group_search_base,
            user_filter=settings.ldap_user_filter,
            timeout_seconds=settings.ldap_timeout_seconds,
        )

    def lookup(self, provider: str, posix_username: str) -> IdentityLookupResult | None:
        try:
            from ldap3 import ALL_ATTRIBUTES, Connection, Server, SUBTREE
            from ldap3.utils.conv import escape_filter_chars
        except ImportError as exc:
            raise IdentityLookupConfigurationError(
                "LDAP identity lookup requires installing the ldap extra: "
                "pip install 'dms[ldap]'"
            ) from exc

        username = escape_filter_chars(posix_username)
        user_base = self.user_search_base or self.base_dn
        group_base = self.group_search_base or self.base_dn
        user_filter = self.user_filter.format(username=username)
        server = Server(self.uri, connect_timeout=self.timeout_seconds)
        try:
            with Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                receive_timeout=self.timeout_seconds,
            ) as connection:
                connection.search(
                    search_base=user_base,
                    search_filter=user_filter,
                    search_scope=SUBTREE,
                    attributes=[ALL_ATTRIBUTES],
                    size_limit=2,
                )
                if not connection.entries:
                    return None
                if len(connection.entries) > 1:
                    raise IdentityLookupReadError(
                        f"LDAP user lookup returned multiple entries for {posix_username}"
                    )
                user_entry = connection.entries[0]
                user_attrs = user_entry.entry_attributes_as_dict
                uid_number = _single_int(user_attrs, "uidNumber")
                gid_number = _single_int(user_attrs, "gidNumber")
                user_dn = user_entry.entry_dn
                groups = self._lookup_groups(
                    connection=connection,
                    group_base=group_base,
                    username=username,
                    user_dn=escape_filter_chars(user_dn),
                    primary_gid=gid_number,
                )
        except IdentityLookupReadError:
            raise
        except Exception as exc:
            raise IdentityLookupReadError(f"LDAP identity lookup failed: {exc}") from exc

        return IdentityLookupResult(
            provider=provider,
            posix_username=posix_username,
            uid=uid_number,
            primary_gid=gid_number,
            groups=groups,
            user_dn=user_dn,
            source_metadata={
                "adapter": "ldap3-direct",
                "read_only": True,
                "uri": self.uri,
                "base_dn": self.base_dn,
                "user_search_base": user_base,
                "group_search_base": group_base,
                "user_filter": user_filter,
                "user_dn": user_dn,
            },
        )

    def _lookup_groups(
        self,
        *,
        connection: Any,
        group_base: str,
        username: str,
        user_dn: str,
        primary_gid: int,
    ) -> list[str]:
        from ldap3 import SUBTREE

        group_filter = f"(|(memberUid={username})(member={user_dn})(gidNumber={primary_gid}))"
        connection.search(
            search_base=group_base,
            search_filter=group_filter,
            search_scope=SUBTREE,
            attributes=["cn", "gidNumber", "memberUid", "member"],
        )
        names: set[str] = set()
        for entry in connection.entries:
            attrs = entry.entry_attributes_as_dict
            cn_values = attrs.get("cn") or []
            if cn_values:
                names.add(str(cn_values[0]))
        return sorted(names)


@dataclass(frozen=True)
class LdapIdentityGroupManager:
    uri: str
    base_dn: str
    bind_dn: str
    bind_password: str
    user_search_base: str
    group_search_base: str
    timeout_seconds: int = 5
    gid_start: int = 24000
    gid_end: int = 24999

    @classmethod
    def from_settings(cls, settings: Settings) -> "LdapIdentityGroupManager":
        if (
            not settings.ldap_uri
            or not settings.ldap_base_dn
            or not settings.ldap_bind_dn
            or not settings.ldap_bind_password
        ):
            raise IdentityLookupConfigurationError(
                "DMS_LDAP_URI, DMS_LDAP_BASE_DN, DMS_LDAP_BIND_DN, and "
                "DMS_LDAP_BIND_PASSWORD are required for LDAP group management"
            )
        return cls(
            uri=settings.ldap_uri,
            base_dn=settings.ldap_base_dn,
            bind_dn=settings.ldap_bind_dn,
            bind_password=settings.ldap_bind_password,
            user_search_base=settings.ldap_user_search_base
            or f"ou=people,{settings.ldap_base_dn}",
            group_search_base=settings.ldap_group_search_base
            or f"ou=groups,{settings.ldap_base_dn}",
            timeout_seconds=settings.ldap_timeout_seconds,
            gid_start=settings.ldap_group_gid_start,
            gid_end=settings.ldap_group_gid_end,
        )

    def ensure_group_members(
        self,
        *,
        group_name: str,
        users: list[str],
        resource_key: str,
    ) -> dict[str, Any]:
        self._require_dms_group_name(group_name)
        try:
            from ldap3 import ALL_ATTRIBUTES, MODIFY_ADD, Connection, Server, SUBTREE
            from ldap3.utils.conv import escape_filter_chars
        except ImportError as exc:
            raise IdentityLookupConfigurationError(
                "LDAP group management requires installing the ldap extra: "
                "pip install 'dms[ldap]'"
            ) from exc
        server = Server(self.uri, connect_timeout=self.timeout_seconds)
        try:
            with Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                receive_timeout=self.timeout_seconds,
            ) as connection:
                missing = [
                    user
                    for user in users
                    if not self._ldap_user_exists(connection, escape_filter_chars(user))
                ]
                if missing:
                    raise IdentityLookupReadError(
                        f"LDAP users not found: {', '.join(sorted(missing))}"
                    )
                group_dn = f"cn={group_name},{self.group_search_base}"
                connection.search(
                    search_base=self.group_search_base,
                    search_filter=f"(cn={escape_filter_chars(group_name)})",
                    search_scope=SUBTREE,
                    attributes=[ALL_ATTRIBUTES],
                    size_limit=2,
                )
                created = False
                if not connection.entries:
                    gid_number = self._next_gid(connection)
                    if not connection.add(
                        group_dn,
                        object_class=["top", "posixGroup"],
                        attributes={
                            "cn": group_name,
                            "gidNumber": gid_number,
                            "memberUid": list(users),
                            "description": f"DMS managed access group for {resource_key}",
                        },
                    ):
                        raise IdentityLookupReadError(
                            f"LDAP group create failed: {connection.result}"
                        )
                    created = True
                elif len(connection.entries) > 1:
                    raise IdentityLookupReadError(
                        f"LDAP group lookup returned multiple entries for {group_name}"
                    )
                else:
                    entry = connection.entries[0]
                    group_dn = entry.entry_dn
                    attrs = entry.entry_attributes_as_dict
                    gid_values = attrs.get("gidNumber") or []
                    gid_number = int(gid_values[0])
                    existing = {str(value) for value in attrs.get("memberUid") or []}
                    missing_members = [user for user in users if user not in existing]
                    if missing_members:
                        if not connection.modify(
                            group_dn,
                            {"memberUid": [(MODIFY_ADD, missing_members)]},
                        ):
                            raise IdentityLookupReadError(
                                f"LDAP group membership update failed: {connection.result}"
                            )
                connection.search(
                    search_base=group_dn,
                    search_filter="(objectClass=posixGroup)",
                    attributes=["cn", "gidNumber", "memberUid", "description"],
                )
                if not connection.entries:
                    raise IdentityLookupReadError(f"LDAP group read-back failed: {group_dn}")
                attrs = connection.entries[0].entry_attributes_as_dict
        except IdentityLookupReadError:
            raise
        except Exception as exc:
            raise IdentityLookupReadError(f"LDAP group management failed: {exc}") from exc
        return {
            "identity_source": "openldap-sssd",
            "group_name": group_name,
            "dn": group_dn,
            "gid": int((attrs.get("gidNumber") or [gid_number])[0]),
            "members": sorted(str(value) for value in attrs.get("memberUid") or []),
            "created": created,
            "resource_key": resource_key,
        }

    def delete_group(self, *, group_name: str) -> dict[str, Any]:
        self._require_dms_group_name(group_name)
        try:
            from ldap3 import Connection, Server, SUBTREE
            from ldap3.utils.conv import escape_filter_chars
        except ImportError as exc:
            raise IdentityLookupConfigurationError(
                "LDAP group management requires installing the ldap extra"
            ) from exc
        server = Server(self.uri, connect_timeout=self.timeout_seconds)
        deleted = False
        try:
            with Connection(
                server,
                user=self.bind_dn,
                password=self.bind_password,
                auto_bind=True,
                receive_timeout=self.timeout_seconds,
            ) as connection:
                connection.search(
                    search_base=self.group_search_base,
                    search_filter=f"(cn={escape_filter_chars(group_name)})",
                    search_scope=SUBTREE,
                    attributes=["cn"],
                    size_limit=2,
                )
                if connection.entries:
                    group_dn = connection.entries[0].entry_dn
                    if not connection.delete(group_dn):
                        raise IdentityLookupReadError(
                            f"LDAP group delete failed: {connection.result}"
                        )
                    deleted = True
        except IdentityLookupReadError:
            raise
        except Exception as exc:
            raise IdentityLookupReadError(f"LDAP group delete failed: {exc}") from exc
        return {
            "identity_source": "openldap-sssd",
            "group_name": group_name,
            "deleted": deleted,
        }

    def _ldap_user_exists(self, connection: Any, escaped_username: str) -> bool:
        from ldap3 import SUBTREE

        connection.search(
            search_base=self.user_search_base,
            search_filter=f"(uid={escaped_username})",
            search_scope=SUBTREE,
            attributes=["uid"],
            size_limit=1,
        )
        return bool(connection.entries)

    def _next_gid(self, connection: Any) -> int:
        from ldap3 import SUBTREE

        connection.search(
            search_base=self.group_search_base,
            search_filter="(gidNumber=*)",
            search_scope=SUBTREE,
            attributes=["gidNumber"],
        )
        used = {
            int(values[0])
            for entry in connection.entries
            for values in [entry.entry_attributes_as_dict.get("gidNumber") or []]
            if values
        }
        for gid in range(self.gid_start, self.gid_end + 1):
            if gid not in used:
                return gid
        raise IdentityLookupReadError(
            f"no free LDAP gidNumber in range {self.gid_start}-{self.gid_end}"
        )

    @staticmethod
    def _require_dms_group_name(group_name: str) -> None:
        if not group_name.startswith("dms-"):
            raise IdentityLookupReadError(
                "DMS-managed LDAP access group names must start with 'dms-'"
            )


def _single_int(attributes: dict[str, Any], name: str) -> int:
    values = attributes.get(name)
    if not values:
        raise IdentityLookupReadError(f"LDAP user entry missing {name}")
    try:
        return int(values[0])
    except (TypeError, ValueError) as exc:
        raise IdentityLookupReadError(f"LDAP user entry has invalid {name}") from exc


@dataclass
class StubVolcanoAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def verify_scan_preflight(
        self, plan: dict[str, Any], data_job: dict[str, Any], preflight: dict[str, Any]
    ) -> dict[str, Any]:
        self.calls.append(("verify_scan_preflight", data_job["job_id"]))
        return {
            "status": "Ready",
            "source": "volcano-stub",
            "reason": "stub_preflight_ready",
        }

    def verify_data_preflight(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        preflight: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        self.calls.append((f"verify_data_preflight:{phase}", data_job["job_id"]))
        return {
            "status": "Ready",
            "source": "volcano-stub",
            "reason": f"stub_{phase}_preflight_ready",
        }

    def create_job(self, plan: dict[str, Any], data_job: dict[str, Any]) -> AdapterResult:
        self.calls.append(("create_job", data_job["job_id"]))
        tool = data_job["selected_tool"] or _default_tool_for_operation(data_job["operation"])
        phase = (plan.get("execution_metadata") or {}).get("phase", "execution")
        summary = {
            "operation": data_job["operation"],
            "selected_tool": tool,
            "phase": phase,
            "dry_run": phase == "preview",
            "file_count": 0,
            "directory_count": 1,
            "total_bytes": 0,
            "error_count": 0,
        }
        if data_job["operation"] == "data.rm" and phase == "execution":
            summary["target_absent"] = True
        return AdapterResult(
            applied_state={
                "adapter": "volcano-stub",
                "job_ref": f"volcano/{data_job['job_id']}",
                "selected_tool": tool,
                "priority": data_job["priority"],
                "phase": phase,
            },
            observed_state={
                "adapter": "volcano-stub",
                "job_ref": f"volcano/{data_job['job_id']}",
                "phase": "Succeeded",
                "summary": summary,
            },
            message="volcano job stub completed",
            artifact_uri=f"stub://artifacts/{data_job['job_id']}",
        )

    def get_job(self, job_ref: str) -> dict[str, Any]:
        return {"job_ref": job_ref, "phase": "Succeeded"}

    def terminate_job(self, job_ref: str) -> AdapterResult:
        self.calls.append(("terminate_job", job_ref))
        return AdapterResult(
            applied_state={"job_ref": job_ref, "terminated": True},
            observed_state={"job_ref": job_ref, "phase": "Cancelled"},
            message="volcano job termination stub completed",
        )


@dataclass
class KubernetesVolcanoAdapter:
    settings: Settings

    @classmethod
    def from_settings(cls, settings: Settings) -> "KubernetesVolcanoAdapter":
        return cls(settings=settings)

    def verify_scan_preflight(
        self, plan: dict[str, Any], data_job: dict[str, Any], preflight: dict[str, Any]
    ) -> dict[str, Any]:
        if not self.settings.dm_job_image:
            return {
                "status": "Rejected",
                "source": "kubernetes-preflight-pod",
                "reason": "missing_dm_job_image",
            }
        manifest = self._preflight_pod_manifest(plan, data_job, preflight)
        name = manifest["metadata"]["name"]
        namespace = manifest["metadata"]["namespace"]
        apply_result = subprocess.run(
            ["kubectl", "-n", namespace, "apply", "-f", "-"],
            input=json.dumps(manifest),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_mutation_timeout_seconds,
            check=False,
        )
        if apply_result.returncode != 0:
            return {
                "status": "Rejected",
                "source": "kubernetes-preflight-pod",
                "reason": "preflight_pod_apply_failed",
                "message": apply_result.stderr.strip() or apply_result.stdout.strip(),
            }
        observed = self._wait_for_pod_terminal(namespace, name)
        logs = self._pod_logs(namespace, name)
        delete_result = subprocess.run(
            ["kubectl", "-n", namespace, "delete", "pod", name, "--ignore-not-found"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_mutation_timeout_seconds,
            check=False,
        )
        cleanup = {
            "attempted": True,
            "returncode": delete_result.returncode,
            "message": delete_result.stderr.strip() or delete_result.stdout.strip(),
        }
        if observed.get("phase") == "Succeeded":
            return {
                "status": "Ready",
                "source": "kubernetes-preflight-pod",
                "reason": "posix_permission_check_passed",
                "pod_ref": f"pod://{namespace}/{name}",
                "observed_state": observed,
                "logs": logs,
                "cleanup": cleanup,
            }
        reason = (
            "posix_permission_denied"
            if observed.get("phase") == "Failed"
            else "preflight_pod_not_succeeded"
        )
        return {
            "status": "Rejected",
            "source": "kubernetes-preflight-pod",
            "reason": reason,
            "pod_ref": f"pod://{namespace}/{name}",
            "observed_state": observed,
            "logs": logs,
            "cleanup": cleanup,
        }

    def verify_data_preflight(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        preflight: dict[str, Any],
        *,
        phase: str,
    ) -> dict[str, Any]:
        if data_job["operation"] == "data.scan":
            return self.verify_scan_preflight(plan, data_job, preflight)
        if not self.settings.dm_job_image:
            return {
                "status": "Rejected",
                "source": "kubernetes-preflight-pod",
                "reason": "missing_dm_job_image",
            }
        manifest = self._data_preflight_pod_manifest(plan, data_job, preflight, phase)
        name = manifest["metadata"]["name"]
        namespace = manifest["metadata"]["namespace"]
        apply_result = subprocess.run(
            ["kubectl", "-n", namespace, "apply", "-f", "-"],
            input=json.dumps(manifest),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_mutation_timeout_seconds,
            check=False,
        )
        if apply_result.returncode != 0:
            return {
                "status": "Rejected",
                "source": "kubernetes-preflight-pod",
                "reason": "preflight_pod_apply_failed",
                "message": apply_result.stderr.strip() or apply_result.stdout.strip(),
            }
        observed = self._wait_for_pod_terminal(namespace, name)
        logs = self._pod_logs(namespace, name)
        delete_result = subprocess.run(
            ["kubectl", "-n", namespace, "delete", "pod", name, "--ignore-not-found"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_mutation_timeout_seconds,
            check=False,
        )
        cleanup = {
            "attempted": True,
            "returncode": delete_result.returncode,
            "message": delete_result.stderr.strip() or delete_result.stdout.strip(),
        }
        if observed.get("phase") == "Succeeded":
            return {
                "status": "Ready",
                "source": "kubernetes-preflight-pod",
                "reason": f"{phase}_posix_permission_check_passed",
                "pod_ref": f"pod://{namespace}/{name}",
                "observed_state": observed,
                "logs": logs,
                "cleanup": cleanup,
            }
        return {
            "status": "Rejected",
            "source": "kubernetes-preflight-pod",
            "reason": "posix_permission_denied"
            if observed.get("phase") == "Failed"
            else "preflight_pod_not_succeeded",
            "pod_ref": f"pod://{namespace}/{name}",
            "observed_state": observed,
            "logs": logs,
            "cleanup": cleanup,
        }

    def create_job(self, plan: dict[str, Any], data_job: dict[str, Any]) -> AdapterResult:
        if not self.settings.dm_job_image:
            raise DataManagementRuntimeError("DMS_DM_JOB_IMAGE is required for live data jobs")
        manifest = self._manifest(plan, data_job)
        job_name = manifest["metadata"]["name"]
        namespace = manifest["metadata"]["namespace"]
        completed = subprocess.run(
            ["kubectl", "-n", namespace, "apply", "-f", "-"],
            input=json.dumps(manifest),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_mutation_timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise DataManagementRuntimeError(
                "VolcanoJob apply failed: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        job_ref = f"volcano://{namespace}/{job_name}"
        phase = (plan.get("execution_metadata") or {}).get("phase", "execution")
        observed = self._wait_for_terminal(
            job_ref, timeout_seconds=self._timeout_seconds(data_job["operation"], phase)
        )
        artifact_uri = _artifact_job_uri(self.settings.dm_artifact_base_uri, data_job["job_id"])
        return AdapterResult(
            applied_state={
                "adapter": "volcano-kubectl",
                "job_ref": job_ref,
                "namespace": namespace,
                "name": job_name,
                "selected_tool": data_job["selected_tool"] or _default_tool_for_operation(data_job["operation"]),
                "priority": data_job["priority"],
                "phase": phase,
                "image_ref": self.settings.dm_job_image_ref,
                "artifact_uri": artifact_uri,
            },
            observed_state=observed,
            message="volcano data job completed",
            artifact_uri=artifact_uri,
        )

    def get_job(self, job_ref: str) -> dict[str, Any]:
        namespace, name = _parse_volcano_ref(job_ref)
        completed = subprocess.run(
            ["kubectl", "-n", namespace, "get", "job.batch.volcano.sh", name, "-o", "json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_inventory_timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise DataManagementRuntimeError(
                "VolcanoJob read failed: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        payload = json.loads(completed.stdout)
        state = payload.get("status", {}).get("state") or {}
        phase = state.get("phase") or payload.get("status", {}).get("phase") or "Unknown"
        return {
            "adapter": "volcano-kubectl",
            "job_ref": job_ref,
            "phase": phase,
            "state": state,
            "status": payload.get("status", {}),
        }

    def terminate_job(self, job_ref: str) -> AdapterResult:
        namespace, name = _parse_volcano_ref(job_ref)
        completed = subprocess.run(
            ["kubectl", "-n", namespace, "delete", "job.batch.volcano.sh", name, "--ignore-not-found"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_mutation_timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise DataManagementRuntimeError(
                "VolcanoJob delete failed: "
                f"{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return AdapterResult(
            applied_state={"job_ref": job_ref, "terminated": True},
            observed_state={"job_ref": job_ref, "phase": "Cancelled"},
            message="volcano scan job terminated",
        )

    def _wait_for_terminal(
        self, job_ref: str, *, timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        deadline = time.monotonic() + (timeout_seconds or self.settings.dm_scan_timeout_seconds)
        last: dict[str, Any] = {"job_ref": job_ref, "phase": "Unknown"}
        while time.monotonic() < deadline:
            last = self.get_job(job_ref)
            if last.get("phase") in {"Completed", "Succeeded"}:
                last["phase"] = "Succeeded"
                return last
            if last.get("phase") in {"Failed", "Terminated", "Aborted"}:
                return last
            time.sleep(max(self.settings.dm_monitor_poll_seconds, 1))
        self.terminate_job(job_ref)
        return {**last, "phase": "TimedOut", "reason": "dm_job_timeout"}

    def _wait_for_pod_terminal(self, namespace: str, name: str) -> dict[str, Any]:
        timeout = max(self.settings.kubernetes_mutation_timeout_seconds, 30)
        deadline = time.monotonic() + timeout
        last: dict[str, Any] = {
            "adapter": "kubernetes-preflight-pod",
            "namespace": namespace,
            "name": name,
            "phase": "Unknown",
        }
        while time.monotonic() < deadline:
            completed = subprocess.run(
                ["kubectl", "-n", namespace, "get", "pod", name, "-o", "json"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.kubernetes_inventory_timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                last = {
                    **last,
                    "phase": "Unknown",
                    "reason": "preflight_pod_read_failed",
                    "message": completed.stderr.strip() or completed.stdout.strip(),
                }
            else:
                payload = json.loads(completed.stdout)
                status = payload.get("status", {})
                phase = status.get("phase") or "Unknown"
                container_statuses = status.get("containerStatuses") or []
                last = {
                    "adapter": "kubernetes-preflight-pod",
                    "namespace": namespace,
                    "name": name,
                    "phase": phase,
                    "reason": status.get("reason"),
                    "message": status.get("message"),
                    "container_statuses": container_statuses,
                }
                if phase in {"Succeeded", "Failed"}:
                    return last
            time.sleep(max(min(self.settings.dm_monitor_poll_seconds, 5), 1))
        return {**last, "phase": "TimedOut", "reason": "preflight_pod_timeout"}

    def _pod_logs(self, namespace: str, name: str) -> dict[str, Any]:
        completed = subprocess.run(
            ["kubectl", "-n", namespace, "logs", name],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_inventory_timeout_seconds,
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }

    def _preflight_pod_manifest(
        self, plan: dict[str, Any], data_job: dict[str, Any], preflight: dict[str, Any]
    ) -> dict[str, Any]:
        name = _kubernetes_name(f"dms-scan-preflight-{data_job['job_id']}")
        scan = self._scan_context(plan, data_job)
        command = [
            "/bin/sh",
            "-c",
            "\n".join(
                [
                    "set -eu",
                    "target=/dms/target/${DMS_SCAN_PATH}",
                    "test -d \"$target\"",
                    "test -r \"$target\"",
                    "test -x \"$target\"",
                    "printf 'scan preflight ok: %s\\n' \"$target\"",
                ]
            ),
        ]
        spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "nodeSelector": scan["node_selector"],
            "securityContext": _pod_security_context(preflight),
            "volumes": scan["volumes"],
            "containers": [
                {
                    "name": "scan-preflight",
                    "image": self.settings.dm_job_image,
                    "command": command,
                    "env": scan["env"],
                    "volumeMounts": scan["volume_mounts"],
                    "securityContext": _container_security_context(preflight),
                }
            ],
        }
        if scan["affinity"]:
            spec["affinity"] = scan["affinity"]
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": self.settings.dm_namespace,
                "labels": {
                    "app.kubernetes.io/name": "dms-data-management-preflight",
                    "dms.openai.com/data-job-id": data_job["job_id"],
                    "dms.openai.com/request-id": data_job["request_id"],
                },
            },
            "spec": spec,
        }

    def _data_preflight_pod_manifest(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        preflight: dict[str, Any],
        phase: str,
    ) -> dict[str, Any]:
        name = _kubernetes_name(
            f"dms-{_operation_suffix(data_job['operation'])}-{phase}-preflight-{data_job['job_id']}"
        )
        context = self._mutation_context(plan, data_job)
        if data_job["operation"] == "data.sync":
            command_text = "\n".join(
                [
                    "set -eu",
                    "source=/dms/source/${DMS_SYNC_SOURCE_PATH}",
                    "destination=/dms/destination/${DMS_SYNC_DESTINATION_PATH}",
                    "destination_parent=$(dirname \"$destination\")",
                    "test -e \"$source\"",
                    "test -r \"$source\"",
                    "if [ -d \"$source\" ]; then test -x \"$source\"; fi",
                    "test -d \"$destination_parent\"",
                    "test -w \"$destination_parent\"",
                    "test -x \"$destination_parent\"",
                    "if [ -e \"$destination\" ]; then test -w \"$destination\"; fi",
                    "printf 'sync %s preflight ok: %s -> %s\\n' \"$DMS_DM_PHASE\" \"$source\" \"$destination\"",
                ]
            )
            container_name = "sync-preflight"
        elif data_job["operation"] == "data.rm":
            command_text = "\n".join(
                [
                    "set -eu",
                    "target=/dms/target/${DMS_RM_TARGET_PATH}",
                    "parent=$(dirname \"$target\")",
                    "test -d \"$target\"",
                    "test -r \"$target\"",
                    "test -x \"$target\"",
                    "test -w \"$parent\"",
                    "test -x \"$parent\"",
                    "printf 'rm %s preflight ok: %s\\n' \"$DMS_DM_PHASE\" \"$target\"",
                ]
            )
            container_name = "rm-preflight"
        else:
            raise DataManagementRuntimeError(
                f"unsupported data preflight operation: {data_job['operation']}"
            )
        spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "nodeSelector": context["node_selector"],
            "securityContext": _pod_security_context(preflight),
            "volumes": context["volumes"],
            "containers": [
                {
                    "name": container_name,
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", command_text],
                    "env": [*context["env"], {"name": "DMS_DM_PHASE", "value": phase}],
                    "volumeMounts": context["volume_mounts"],
                    "securityContext": _container_security_context(preflight),
                }
            ],
        }
        if context["affinity"]:
            spec["affinity"] = context["affinity"]
        return {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": name,
                "namespace": self.settings.dm_namespace,
                "labels": {
                    "app.kubernetes.io/name": "dms-data-management-preflight",
                    "dms.openai.com/data-job-id": data_job["job_id"],
                    "dms.openai.com/request-id": data_job["request_id"],
                    "dms.openai.com/data-phase": phase,
                },
            },
            "spec": spec,
        }

    def _manifest(self, plan: dict[str, Any], data_job: dict[str, Any]) -> dict[str, Any]:
        if data_job["operation"] != "data.scan":
            return self._mutation_manifest(plan, data_job)
        job_name = _kubernetes_name(f"dms-scan-{data_job['job_id']}")
        scan = self._scan_context(plan, data_job)
        command = [
            "/bin/sh",
            "-c",
            "\n".join(
                [
                    "set -eu",
                    "mkdir -p /dms/artifacts/${DMS_DATA_JOB_ID}",
                    "target=/dms/target/${DMS_SCAN_PATH}",
                    "test -d \"$target\"",
                    "test -r \"$target\"",
                    "test -x \"$target\"",
                    "report=/dms/artifacts/${DMS_DATA_JOB_ID}/dscan-report.json",
                    "summary=/dms/artifacts/${DMS_DATA_JOB_ID}/summary.json",
                    "find_errors=/dms/artifacts/${DMS_DATA_JOB_ID}/find-errors.log",
                    "dscan --directory \"$target\" --output \"$report\" --print > /dms/artifacts/${DMS_DATA_JOB_ID}/stdout.log 2> /dms/artifacts/${DMS_DATA_JOB_ID}/stderr.log",
                    "test -f \"$report\"",
                    ": > \"$find_errors\"",
                    "file_count=$(find \"$target\" -type f -print 2>> \"$find_errors\" | wc -l | awk '{print $1}')",
                    "directory_count=$(find \"$target\" -type d -print 2>> \"$find_errors\" | wc -l | awk '{print $1}')",
                    "total_bytes=$(find \"$target\" -type f -printf '%s\\n' 2>> \"$find_errors\" | awk '{sum += $1} END {print sum + 0}')",
                    "error_count=$(wc -l < \"$find_errors\" | awk '{print $1}')",
                    "printf '{\"summary\":{\"file_count\":%s,\"directory_count\":%s,\"total_bytes\":%s,\"error_count\":%s}}\\n' \"$file_count\" \"$directory_count\" \"$total_bytes\" \"$error_count\" > \"$summary\"",
                    "test -f \"$summary\"",
                ]
            ),
        ]
        task_pod_spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "nodeSelector": scan["node_selector"],
            "securityContext": _pod_security_context(data_job.get("preflight_result") or {}),
            "volumes": scan["volumes"],
            "containers": [
                {
                    "name": "dscan",
                    "image": self.settings.dm_job_image,
                    "command": command,
                    "env": scan["env"],
                    "volumeMounts": scan["volume_mounts"],
                    "securityContext": _container_security_context(data_job.get("preflight_result") or {}),
                }
            ],
        }
        if scan["affinity"]:
            task_pod_spec["affinity"] = scan["affinity"]
        return {
            "apiVersion": "batch.volcano.sh/v1alpha1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": self.settings.dm_namespace,
                "labels": {
                    "app.kubernetes.io/name": "dms-data-management",
                    "dms.openai.com/data-job-id": data_job["job_id"],
                    "dms.openai.com/request-id": data_job["request_id"],
                },
            },
            "spec": {
                "schedulerName": "volcano",
                "minAvailable": 1,
                "tasks": [
                    {
                        "name": "dscan",
                        "replicas": max(
                            1,
                            min(len(scan["selected"]) or 1, self.settings.dm_max_nodes),
                        ),
                        "template": {
                            "spec": task_pod_spec
                        },
                    }
                ],
            },
        }

    def _mutation_manifest(self, plan: dict[str, Any], data_job: dict[str, Any]) -> dict[str, Any]:
        phase = (plan.get("execution_metadata") or {}).get("phase", "execution")
        tool = data_job.get("selected_tool") or _default_tool_for_operation(data_job["operation"])
        if tool == "nsync":
            raise DataManagementRuntimeError("nsync live execution is not enabled in this adapter path")
        context = self._mutation_context(plan, data_job)
        job_name = _kubernetes_name(
            f"dms-{_operation_suffix(data_job['operation'])}-{phase}-{data_job['job_id']}"
        )
        command = self._mutation_command(plan, data_job, phase=phase, tool=tool)
        task_pod_spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "nodeSelector": context["node_selector"],
            "securityContext": _pod_security_context(data_job.get("preflight_result") or {}),
            "volumes": context["volumes"],
            "containers": [
                {
                    "name": tool,
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", command],
                    "env": [*context["env"], {"name": "DMS_DM_PHASE", "value": phase}],
                    "volumeMounts": context["volume_mounts"],
                    "securityContext": _container_security_context(data_job.get("preflight_result") or {}),
                }
            ],
        }
        if context["affinity"]:
            task_pod_spec["affinity"] = context["affinity"]
        return {
            "apiVersion": "batch.volcano.sh/v1alpha1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": self.settings.dm_namespace,
                "labels": {
                    "app.kubernetes.io/name": "dms-data-management",
                    "dms.openai.com/data-job-id": data_job["job_id"],
                    "dms.openai.com/request-id": data_job["request_id"],
                    "dms.openai.com/data-phase": phase,
                    "dms.openai.com/data-tool": tool,
                },
            },
            "spec": {
                "schedulerName": "volcano",
                "minAvailable": 1,
                "tasks": [
                    {
                        "name": tool,
                        "replicas": 1,
                        "template": {"spec": task_pod_spec},
                    }
                ],
            },
        }

    def _mutation_command(
        self, plan: dict[str, Any], data_job: dict[str, Any], *, phase: str, tool: str
    ) -> str:
        options = (plan.get("desired_state") or {}).get("options") or {}
        dryrun = "--dryrun " if phase == "preview" else ""
        if data_job["operation"] == "data.sync":
            flags = _sync_flags(options)
            return "\n".join(
                [
                    "set -eu",
                    "artifact=/dms/artifacts/${DMS_DATA_JOB_ID}/${DMS_DM_PHASE}",
                    "mkdir -p \"$artifact\"",
                    "source=/dms/source/${DMS_SYNC_SOURCE_PATH}",
                    "destination=/dms/destination/${DMS_SYNC_DESTINATION_PATH}",
                    "find_errors=\"$artifact/find-errors.log\"",
                    ": > \"$find_errors\"",
                    f"printf '{{\"tool\":\"{tool}\",\"phase\":\"%s\",\"dry_run\":%s}}\\n' \"$DMS_DM_PHASE\" \"$( [ \"$DMS_DM_PHASE\" = preview ] && echo true || echo false )\" > \"$artifact/command.json\"",
                    f"{tool} {dryrun}{flags}\"$source\" \"$destination\" > \"$artifact/stdout.log\" 2> \"$artifact/stderr.log\"",
                    "summary_root=\"$destination\"",
                    "if [ \"$DMS_DM_PHASE\" = preview ]; then summary_root=\"$source\"; fi",
                    "file_count=$(find \"$summary_root\" -type f -print 2>> \"$find_errors\" | wc -l | awk '{print $1}')",
                    "directory_count=$(find \"$summary_root\" -type d -print 2>> \"$find_errors\" | wc -l | awk '{print $1}')",
                    "total_bytes=$(find \"$summary_root\" -type f -printf '%s\\n' 2>> \"$find_errors\" | awk '{sum += $1} END {print sum + 0}')",
                    "error_count=$(wc -l < \"$find_errors\" | awk '{print $1}')",
                    "printf '{\"summary\":{\"operation\":\"data.sync\",\"selected_tool\":\"%s\",\"phase\":\"%s\",\"dry_run\":%s,\"file_count\":%s,\"directory_count\":%s,\"total_bytes\":%s,\"error_count\":%s}}\\n' \"$DMS_SELECTED_TOOL\" \"$DMS_DM_PHASE\" \"$( [ \"$DMS_DM_PHASE\" = preview ] && echo true || echo false )\" \"$file_count\" \"$directory_count\" \"$total_bytes\" \"$error_count\" > \"$artifact/summary.json\"",
                ]
            )
        if data_job["operation"] == "data.rm":
            flags = _rm_flags(options, phase=phase)
            return "\n".join(
                [
                    "set -eu",
                    "artifact=/dms/artifacts/${DMS_DATA_JOB_ID}/${DMS_DM_PHASE}",
                    "mkdir -p \"$artifact\"",
                    "target=/dms/target/${DMS_RM_TARGET_PATH}",
                    "find_errors=\"$artifact/find-errors.log\"",
                    ": > \"$find_errors\"",
                    "file_count=$(find \"$target\" -type f -print 2>> \"$find_errors\" | wc -l | awk '{print $1}')",
                    "directory_count=$(find \"$target\" -type d -print 2>> \"$find_errors\" | wc -l | awk '{print $1}')",
                    "total_bytes=$(find \"$target\" -type f -printf '%s\\n' 2>> \"$find_errors\" | awk '{sum += $1} END {print sum + 0}')",
                    f"printf '{{\"tool\":\"{tool}\",\"phase\":\"%s\",\"dry_run\":%s}}\\n' \"$DMS_DM_PHASE\" \"$( [ \"$DMS_DM_PHASE\" = preview ] && echo true || echo false )\" > \"$artifact/command.json\"",
                    f"{tool} {dryrun}{flags}\"$target\" > \"$artifact/stdout.log\" 2> \"$artifact/stderr.log\"",
                    "target_absent=false",
                    "if [ ! -e \"$target\" ]; then target_absent=true; fi",
                    "error_count=$(wc -l < \"$find_errors\" | awk '{print $1}')",
                    "printf '{\"summary\":{\"operation\":\"data.rm\",\"selected_tool\":\"%s\",\"phase\":\"%s\",\"dry_run\":%s,\"file_count\":%s,\"directory_count\":%s,\"total_bytes\":%s,\"error_count\":%s,\"target_absent\":%s}}\\n' \"$DMS_SELECTED_TOOL\" \"$DMS_DM_PHASE\" \"$( [ \"$DMS_DM_PHASE\" = preview ] && echo true || echo false )\" \"$file_count\" \"$directory_count\" \"$total_bytes\" \"$error_count\" \"$target_absent\" > \"$artifact/summary.json\"",
                ]
            )
        raise DataManagementRuntimeError(f"unsupported mutation operation: {data_job['operation']}")

    def _mutation_context(self, plan: dict[str, Any], data_job: dict[str, Any]) -> dict[str, Any]:
        artifact_uri = _artifact_job_uri(self.settings.dm_artifact_base_uri, data_job["job_id"])
        normalized = data_job.get("normalized_target") or plan.get("desired_state") or {}
        selected = _unique_selected_candidates(
            (data_job.get("worker_pool") or {}).get("selected_candidates") or []
        )
        node_selector: dict[str, str] = {}
        affinity: dict[str, Any] = {}
        node_names = [item["node_name"] for item in selected if item.get("node_name")]
        if len(node_names) == 1:
            node_selector["kubernetes.io/hostname"] = node_names[0]
        elif node_names:
            affinity = _node_name_affinity(node_names)

        artifact_path = _file_uri_parent_path(artifact_uri)
        volumes: list[dict[str, Any]] = []
        volume_mounts: list[dict[str, Any]] = []
        env: list[dict[str, str]] = [
            {"name": "DMS_DATA_JOB_ID", "value": data_job["job_id"]},
            {"name": "DMS_ARTIFACT_URI", "value": artifact_uri},
            {
                "name": "DMS_SELECTED_TOOL",
                "value": data_job.get("selected_tool") or _default_tool_for_operation(data_job["operation"]),
            },
        ]

        if data_job["operation"] == "data.sync":
            source = normalized.get("source") or plan.get("desired_state", {}).get("source") or {}
            destination = (
                normalized.get("destination")
                or plan.get("desired_state", {}).get("destination")
                or {}
            )
            candidate = selected[0] if selected else {}
            source_mount = candidate.get("source_mount_path") or candidate.get("mount_path")
            destination_mount = candidate.get("destination_mount_path") or candidate.get("mount_path")
            if source_mount:
                volumes.append(
                    {
                        "name": "sync-source",
                        "hostPath": {"path": source_mount, "type": "Directory"},
                    }
                )
                volume_mounts.append(
                    {
                        "name": "sync-source",
                        "mountPath": "/dms/source",
                        "readOnly": True,
                    }
                )
            if destination_mount:
                volumes.append(
                    {
                        "name": "sync-destination",
                        "hostPath": {"path": destination_mount, "type": "Directory"},
                    }
                )
                volume_mounts.append(
                    {"name": "sync-destination", "mountPath": "/dms/destination"}
                )
            env.extend(
                [
                    {
                        "name": "DMS_SYNC_SOURCE_STORAGE",
                        "value": source.get("storage_name", data_job.get("storage_name") or ""),
                    },
                    {
                        "name": "DMS_SYNC_DESTINATION_STORAGE",
                        "value": destination.get("storage_name", ""),
                    },
                    {"name": "DMS_SYNC_SOURCE_PATH", "value": source.get("path") or "."},
                    {
                        "name": "DMS_SYNC_DESTINATION_PATH",
                        "value": destination.get("path") or ".",
                    },
                ]
            )
        elif data_job["operation"] == "data.rm":
            target = normalized.get("target") or normalized
            candidate = selected[0] if selected else {}
            target_mount = candidate.get("mount_path")
            if target_mount:
                volumes.append(
                    {
                        "name": "rm-target",
                        "hostPath": {"path": target_mount, "type": "Directory"},
                    }
                )
                volume_mounts.append({"name": "rm-target", "mountPath": "/dms/target"})
            env.extend(
                [
                    {
                        "name": "DMS_RM_STORAGE",
                        "value": target.get("storage_name", data_job.get("storage_name") or ""),
                    },
                    {"name": "DMS_RM_TARGET_PATH", "value": target.get("path") or "."},
                ]
            )
        else:
            raise DataManagementRuntimeError(
                f"unsupported mutation context operation: {data_job['operation']}"
            )

        if artifact_path:
            volumes.append(
                {
                    "name": "mutation-artifacts",
                    "hostPath": {"path": artifact_path, "type": "DirectoryOrCreate"},
                }
            )
            volume_mounts.append(
                {"name": "mutation-artifacts", "mountPath": "/dms/artifacts"}
            )
        return {
            "artifact_uri": artifact_uri,
            "selected": selected,
            "node_selector": node_selector,
            "affinity": affinity,
            "volumes": volumes,
            "volume_mounts": volume_mounts,
            "env": env,
        }

    def _timeout_seconds(self, operation: str, phase: str) -> int:
        if operation == "data.sync":
            if phase == "preview":
                return self.settings.dm_sync_preview_timeout_seconds
            return self.settings.dm_sync_execution_timeout_seconds
        if operation == "data.rm":
            if phase == "preview":
                return self.settings.dm_rm_preview_timeout_seconds
            return self.settings.dm_rm_execution_timeout_seconds
        return self.settings.dm_scan_timeout_seconds

    def _scan_context(self, plan: dict[str, Any], data_job: dict[str, Any]) -> dict[str, Any]:
        artifact_uri = _artifact_job_uri(self.settings.dm_artifact_base_uri, data_job["job_id"])
        target = data_job.get("normalized_target") or plan.get("desired_state", {}).get("target") or {}
        selected = _unique_selected_candidates(
            (data_job.get("worker_pool") or {}).get("selected_candidates") or []
        )
        node_selector = {}
        affinity = {}
        node_names = [
            item["node_name"] for item in selected if item.get("node_name")
        ]
        if len(node_names) == 1:
            node_selector["kubernetes.io/hostname"] = node_names[0]
        elif node_names:
            affinity = _node_name_affinity(node_names)
        target_mount = selected[0].get("mount_path") if selected else None
        artifact_path = _file_uri_parent_path(artifact_uri)
        volumes: list[dict[str, Any]] = []
        volume_mounts: list[dict[str, Any]] = []
        if target_mount:
            volumes.append(
                {
                    "name": "scan-target",
                    "hostPath": {"path": target_mount, "type": "Directory"},
                }
            )
            volume_mounts.append(
                {"name": "scan-target", "mountPath": "/dms/target", "readOnly": True}
            )
        if artifact_path:
            volumes.append(
                {
                    "name": "scan-artifacts",
                    "hostPath": {"path": artifact_path, "type": "DirectoryOrCreate"},
                }
            )
            volume_mounts.append(
                {"name": "scan-artifacts", "mountPath": "/dms/artifacts"}
            )
        return {
            "artifact_uri": artifact_uri,
            "target": target,
            "selected": selected,
            "node_selector": node_selector,
            "affinity": affinity,
            "volumes": volumes,
            "volume_mounts": volume_mounts,
            "env": [
                {"name": "DMS_DATA_JOB_ID", "value": data_job["job_id"]},
                {
                    "name": "DMS_SCAN_STORAGE",
                    "value": target.get("storage_name", data_job["storage_name"]),
                },
                {
                    "name": "DMS_SCAN_PATH",
                    "value": target.get("path", data_job.get("target") or "."),
                },
                {"name": "DMS_ARTIFACT_URI", "value": artifact_uri},
            ],
        }


def volcano_adapter_from_settings(settings: Settings) -> StubVolcanoAdapter | KubernetesVolcanoAdapter:
    if settings.dm_kubernetes_mode == "stub":
        return StubVolcanoAdapter()
    return KubernetesVolcanoAdapter.from_settings(settings)


def _parse_volcano_ref(job_ref: str) -> tuple[str, str]:
    if not job_ref.startswith("volcano://"):
        raise DataManagementRuntimeError(f"invalid Volcano job ref: {job_ref}")
    namespace, name = job_ref.removeprefix("volcano://").split("/", 1)
    return namespace, name


def _artifact_job_uri(base_uri: str, job_id: str) -> str:
    return f"{base_uri.rstrip('/')}/{job_id}"


def _file_uri_parent_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return str(Path(parsed.path).parent)


def _unique_selected_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for candidate in candidates:
        key = (candidate.get("node_name"), candidate.get("mount_path"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _node_name_affinity(node_names: list[str]) -> dict[str, Any]:
    return {
        "nodeAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [
                    {
                        "matchExpressions": [
                            {
                                "key": "kubernetes.io/hostname",
                                "operator": "In",
                                "values": node_names,
                            }
                        ]
                    }
                ]
            }
        }
    }


def _pod_security_context(preflight: dict[str, Any]) -> dict[str, Any]:
    mapping = preflight.get("identity_mapping") or {}
    gid = mapping.get("gid")
    if gid is None:
        return {}
    return {"fsGroup": int(gid), "runAsNonRoot": True}


def _container_security_context(preflight: dict[str, Any]) -> dict[str, Any]:
    mapping = preflight.get("identity_mapping") or {}
    uid = mapping.get("uid")
    gid = mapping.get("gid")
    if uid is None or gid is None:
        return {}
    return {
        "allowPrivilegeEscalation": False,
        "runAsNonRoot": True,
        "runAsUser": int(uid),
        "runAsGroup": int(gid),
    }


def _kubernetes_name(value: str) -> str:
    normalized = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
    normalized = normalized.strip("-") or "dms-scan"
    return normalized[:63].rstrip("-")


def _operation_suffix(operation: str) -> str:
    return {
        "data.scan": "scan",
        "data.sync": "sync",
        "data.rm": "rm",
    }.get(operation, "data")


def _default_tool_for_operation(operation: str) -> str:
    return {
        "data.sync": "dsync",
        "data.rm": "drm",
        "data.scan": "dscan",
    }.get(operation, "mpifileutils")


def _sync_flags(options: dict[str, Any]) -> str:
    flag_specs = {
        "delete": ("--delete", None),
        "contents": ("--contents", None),
        "direct": ("--direct", None),
        "open_noatime": ("--open-noatime", None),
        "quiet": ("--quiet", None),
        "batch_files": ("--batch-files", "value"),
        "bufsize": ("--bufsize", "value"),
    }
    return _render_option_flags(options, flag_specs)


def _rm_flags(options: dict[str, Any], *, phase: str) -> str:
    del phase
    flag_specs = {
        "stat": ("--stat", None),
        "lite": ("--lite", None),
        "quiet": ("--quiet", None),
    }
    return _render_option_flags(options, flag_specs)


def _render_option_flags(
    options: dict[str, Any], flag_specs: dict[str, tuple[str, str | None]]
) -> str:
    flags: list[str] = []
    for key, (flag, mode) in flag_specs.items():
        value = options.get(key)
        if value is None or value is False:
            continue
        if mode == "value":
            flags.extend([flag, shlex.quote(str(value))])
        else:
            flags.append(flag)
    if not flags:
        return ""
    return " ".join(flags) + " "
