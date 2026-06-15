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
from .base import *  # noqa: F401,F403
from ._util import (  # noqa: F401
    _first_present,
    _json_stdout,
    _kubectl_not_found,
    _positive_int,
    _shell_quote,
)




@dataclass
class StubKubernetesNamespaceQuotaAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)
    resource_quotas: dict[tuple[str, str, str], dict[str, Any]] = field(
        default_factory=dict
    )
    resource_quota_lists: dict[tuple[str, str], list[dict[str, Any]]] = field(
        default_factory=dict
    )

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {
            "cluster_name": cluster_name,
            "namespace_name": namespace_name,
            "exists": True,
        }

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



def render_kubernetes_resource_quota_hard(
    desired_state: dict[str, Any],
) -> dict[str, str]:
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
        hard = desired.get(
            "resource_quota_hard"
        ) or render_kubernetes_resource_quota_hard(desired)
        namespace = self._ensure_namespace(
            cluster_name=cluster_name,
            namespace_name=namespace_name,
            plan=plan,
            allow_create=bool(desired.get("allow_namespace_create")),
        )
        before = self.read_resource_quota(
            cluster_name, namespace_name, resource_quota_name
        )
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
                # Reflect block state in the persisted lifecycle status so a manual
                # :block shows "Blocked" (not "Succeeded") — matches the sweep path
                # and the filesystem backends.
                "resource_status": (
                    "Blocked"
                    if (desired.get("block_state") or {}).get("blocked")
                    else "Succeeded"
                ),
            },
            message="Kubernetes ResourceQuota live apply completed",
        )

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        cluster_name = desired["cluster_name"]
        namespace_name = desired["namespace_name"]
        resource_quota_name = desired.get("resource_quota_name") or "dms-storage-quota"
        before = self.read_resource_quota(
            cluster_name, namespace_name, resource_quota_name
        )
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
        after = self.read_resource_quota(
            cluster_name, namespace_name, resource_quota_name
        )
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
        observed = self.read_resource_quota(
            cluster_name, namespace_name, resource_quota_name
        )
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
        observed = self.read_resource_quota(
            cluster_name, namespace_name, resource_quota_name
        )
        # Import is DB-only (it never mutates the live ResourceQuota), so every
        # failure below is a pre-side-effect precondition. Raise
        # BackendPreconditionError so the worker reports BackendApplyFailed (no side
        # effect) instead of UnknownAfterSideEffect (which would leave a stuck request).
        if not observed["exists"]:
            raise BackendPreconditionError(
                f"ResourceQuota does not exist: {cluster_name}/{namespace_name}/{resource_quota_name}"
            )
        try:
            _ensure_dms_managed(
                observed,
                resource_quota_name,
                resource_key=desired.get("resource_key"),
            )
        except KubernetesMutationError as exc:
            raise BackendPreconditionError(str(exc)) from exc
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
            raise BackendPreconditionError(
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
                "previous_annotation_expires_at": (
                    observed.get("annotations") or {}
                ).get("dms.io/expires-at"),
            },
            message="Kubernetes ResourceQuota live state imported to DB",
        )

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        desired = plan["desired_state"]
        cluster_name = desired["cluster_name"]
        namespace_name = desired["namespace_name"]
        resource_quota_name = desired.get("resource_quota_name") or "dms-storage-quota"
        observed = self.read_resource_quota(
            cluster_name, namespace_name, resource_quota_name
        )
        desired_hard = desired.get(
            "resource_quota_hard"
        ) or render_kubernetes_resource_quota_hard(desired)
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
        self,
        cluster_name: str,
        namespace_name: str,
        resource_quota_name: str = "dms-storage-quota",
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
            [
                "-n",
                namespace_name,
                "get",
                "resourcequota",
                resource_quota_name,
                "-o",
                "json",
            ],
            check=not allow_missing,
        )
        if (
            completed.returncode != 0
            and allow_missing
            and _kubectl_not_found(completed.stderr)
        ):
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
        raise KubernetesMutationError(
            f"unsupported Kubernetes mutation mode: {self.mode}"
        )



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
                non_dms_value = kubernetes_resource_quota_value_to_base_units(
                    key, value
                )
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
        if targets
        and all(target.get("resource_status") == "QueryFailed" for target in targets)
        else (
            "PartialFailure"
            if partial_failure
            else "ActionRequired" if issue_count else "Consistent"
        )
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
        target.get("desired_state", {}).get("resource_quota_name")
        or "dms-storage-quota"
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
                resource_quotas = adapter.list_resource_quotas(
                    cluster_name, namespace_name
                )
                result["resource_quotas"] = resource_quotas
                result["effective_quota_warnings"] = effective_resource_quota_warnings(
                    resource_quotas=resource_quotas,
                    dms_hard=desired_hard or live.get("spec_hard") or {},
                    resource_quota_name=resource_quota_name,
                )
        result["issues"] = issues
        if resource_status == "Consistent" and (
            result["usage_pressure"] or result["effective_quota_warnings"]
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
        if (
            parse_kubernetes_storage_class_quota_key(key)
            and key not in matched_storage_class_keys
        ):
            warnings.append(
                {
                    "type": "unknown_storageclass_quota_key",
                    "key": key,
                    "live": hard[key],
                }
            )
    return warnings
