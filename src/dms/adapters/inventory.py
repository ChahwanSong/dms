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
            set(self.cluster_kubeconfigs.keys())
            | set(self.cluster_control_hosts.keys())
        )
        for cluster_name in cluster_names:
            # Per-cluster fail isolation: one unreachable/misconfigured cluster must not
            # blank out the whole inventory (which would flip EVERY storage mapping —
            # including filesystem mappings that don't even need k8s inventory — to a
            # spurious Failed). The unreadable cluster is simply omitted, so only its own
            # mappings surface `cluster_missing`; all others are evaluated normally.
            try:
                clusters[cluster_name] = self._read_cluster(cluster_name)
            except KubernetesInventoryReadError:
                continue
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
                _storage_class_summary(item)
                for item in storage_classes.get("items", [])
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
                _storage_class_summary(item)
                for item in storage_classes.get("items", [])
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
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
        ) as exc:
            # OSError covers a missing/non-executable kubectl|ssh binary
            # (FileNotFoundError) and the like — normalized to KubernetesInventoryReadError
            # so per-cluster fail isolation in read_inventory() catches it instead of
            # crashing the whole sweep.
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
        # Per-cluster transport: a pinned control_host means reach THIS cluster via
        # ssh-kubectl regardless of the global inventory mode (mirrors the per-mapping
        # mutation transport, so e.g. ddz26 is read over ssh-kubectl while the in-cluster
        # `dms` cluster is read with a local kubectl/kubeconfig in the same sweep).
        host = self.cluster_control_hosts.get(cluster_name)
        if host:
            quoted = " ".join(_shell_quote(part) for part in ["kubectl", *args])
            return ["ssh", host, quoted]
        if self.mode == "ssh-kubectl":
            raise KubernetesInventoryReadError(
                f"missing control host for cluster {cluster_name}"
            )
        if self.mode == "kubectl":
            command = ["kubectl"]
            kubeconfig = self.cluster_kubeconfigs.get(cluster_name)
            if kubeconfig:
                command.extend(["--kubeconfig", kubeconfig])
            command.extend(args)
            return command
        raise KubernetesInventoryReadError(
            f"unsupported Kubernetes inventory mode: {self.mode}"
        )



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
