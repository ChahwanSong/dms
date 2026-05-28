from __future__ import annotations

from dataclasses import dataclass, field
import json
import subprocess
from typing import Any, Protocol

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

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult: ...

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult: ...


class FilesystemQuotaStrategy(Protocol):
    backend_type: str

    def render_quota(self, quota: dict[str, Any]) -> dict[str, Any]: ...


class KubernetesNamespaceQuotaAdapter(Protocol):
    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]: ...

    def create_namespace(self, plan: dict[str, Any]) -> AdapterResult: ...

    def apply_resource_quota(self, plan: dict[str, Any]) -> AdapterResult: ...

    def delete_resource_quota(self, plan: dict[str, Any]) -> AdapterResult: ...

    def sync_live_state(self, plan: dict[str, Any]) -> AdapterResult: ...

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult: ...


class StorageInventoryAdapter(Protocol):
    def effective_inventory(self) -> dict[str, Any]: ...


class KubernetesInventoryReadError(RuntimeError):
    pass


class KubernetesMutationError(RuntimeError):
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


class IdentityLookupConfigurationError(RuntimeError):
    pass


class IdentityLookupReadError(RuntimeError):
    pass


class VolcanoAdapter(Protocol):
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

    def import_directory(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("import_directory", plan)

    def assign_quota_only(self, plan: dict[str, Any]) -> AdapterResult:
        return self._result("assign_quota_only", plan)


@dataclass
class StubKubernetesNamespaceQuotaAdapter:
    calls: list[tuple[str, str]] = field(default_factory=list)

    def read_namespace(self, cluster_name: str, namespace_name: str) -> dict[str, Any]:
        return {"cluster_name": cluster_name, "namespace_name": namespace_name, "exists": True}

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

    def check_resource_quota(self, plan: dict[str, Any]) -> AdapterResult:
        self.calls.append(("check_resource_quota", plan["plan_id"]))
        return AdapterResult(
            applied_state={"backend_side_effect": False},
            observed_state={"verified": True, "consistency_status": "Consistent"},
            message="kubernetes namespace quota consistency check stub completed",
        )


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
    for entry in desired_state.get("storage_class_quotas") or []:
        if not isinstance(entry, dict):
            raise ValueError("storage_class_quotas entries must be objects")
        storage_class_name = entry.get("storage_class_name")
        if not storage_class_name:
            raise ValueError("storage_class_quotas[].storage_class_name is required")
        entry_bytes = _positive_int(
            entry.get("requests_storage_bytes")
            or entry.get("capacity_bytes")
            or storage_bytes,
            "storage_class_quotas[].requests_storage_bytes",
        )
        if entry_bytes is None:
            raise ValueError("storage class quota storage bytes are required")
        hard[
            f"{storage_class_name}.storageclass.storage.k8s.io/requests.storage"
        ] = kubernetes_quantity_from_bytes(entry_bytes)
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
        _ensure_dms_managed(before, resource_quota_name)
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
        _ensure_dms_managed(observed, resource_quota_name)
        synced_desired = dict(desired)
        _sync_desired_from_resource_quota_hard(synced_desired, observed["spec_hard"])
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
            },
            observed_state={
                "adapter": "kubernetes-namespace-quota-live",
                "verified": True,
                "backend_side_effect": False,
                "synced": True,
                "resource_quota": observed,
            },
            message="Kubernetes ResourceQuota live state synced to DB",
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
            issues.append({"field": "resource_quota", "reason": "missing"})
        else:
            labels = observed.get("labels") or {}
            if labels.get("app.kubernetes.io/managed-by") != "dms":
                status = "Drifted"
                issues.append({"field": "metadata.labels", "reason": "not_dms_managed"})
            if dict(observed.get("spec_hard") or {}) != dict(desired_hard):
                status = "Drifted"
                issues.append(
                    {
                        "field": "spec.hard",
                        "reason": "hard_limits_drifted",
                        "desired": desired_hard,
                        "live": observed.get("spec_hard") or {},
                    }
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
            },
            message=f"Kubernetes ResourceQuota consistency check {status}",
        )

    def read_resource_quota(
        self, cluster_name: str, namespace_name: str, resource_quota_name: str = "dms-storage-quota"
    ) -> dict[str, Any]:
        return self._read_resource_quota(
            cluster_name=cluster_name,
            namespace_name=namespace_name,
            resource_quota_name=resource_quota_name,
            allow_missing=True,
        )

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
                "annotations": {
                    "dms.io/resource-key": plan["resource_key"],
                    "dms.io/request-id": plan["request_id"],
                    "dms.io/storage-names": ",".join(storage_names),
                },
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
        metadata = payload.get("metadata", {})
        status = payload.get("status", {})
        spec = payload.get("spec", {})
        return {
            "exists": True,
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


def _json_stdout(stdout: str, kind: str) -> dict[str, Any]:
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise KubernetesMutationError(f"kubectl returned non-JSON {kind}") from exc


def _kubectl_not_found(stderr: str) -> bool:
    return "NotFound" in stderr or "not found" in stderr.lower()


def _ensure_dms_managed(resource_quota: dict[str, Any], resource_quota_name: str) -> None:
    labels = resource_quota.get("labels") or {}
    if resource_quota.get("name") != resource_quota_name:
        raise KubernetesMutationError("unexpected ResourceQuota name")
    if labels.get("app.kubernetes.io/managed-by") != "dms":
        raise KubernetesMutationError(
            f"refusing to mutate non-DMS ResourceQuota: {resource_quota_name}"
        )


def _sync_desired_from_resource_quota_hard(
    desired: dict[str, Any], hard: dict[str, str]
) -> None:
    desired["resource_quota_hard"] = dict(hard)
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
    for entry in desired.get("storage_class_quotas") or []:
        if not isinstance(entry, dict):
            continue
        synced_entry = dict(entry)
        storage_class_name = synced_entry.get("storage_class_name")
        if storage_class_name:
            hard_key = f"{storage_class_name}.storageclass.storage.k8s.io/requests.storage"
            if hard_key in hard:
                synced_entry["requests_storage_bytes"] = kubernetes_quantity_to_bytes(
                    hard[hard_key]
                )
        storage_class_quotas.append(synced_entry)
    if storage_class_quotas:
        desired["storage_class_quotas"] = storage_class_quotas


@dataclass
class StubIdentityLookupAdapter:
    mappings: dict[tuple[str, str], IdentityLookupResult] = field(default_factory=dict)

    def lookup(self, provider: str, posix_username: str) -> IdentityLookupResult | None:
        return self.mappings.get((provider, posix_username))


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

    def create_job(self, plan: dict[str, Any], data_job: dict[str, Any]) -> AdapterResult:
        self.calls.append(("create_job", data_job["job_id"]))
        tool = data_job["selected_tool"] or _default_tool_for_operation(data_job["operation"])
        return AdapterResult(
            applied_state={
                "adapter": "volcano-stub",
                "job_ref": f"volcano/{data_job['job_id']}",
                "selected_tool": tool,
                "priority": data_job["priority"],
            },
            observed_state={
                "adapter": "volcano-stub",
                "job_ref": f"volcano/{data_job['job_id']}",
                "phase": "Succeeded",
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


def _default_tool_for_operation(operation: str) -> str:
    return {
        "data.sync": "dsync",
        "data.rm": "drm",
        "data.scan": "dscan",
    }.get(operation, "mpifileutils")
