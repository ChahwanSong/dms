"""dms-agent-storages ConfigMap synchronization helper."""

from __future__ import annotations

import json
import logging
from typing import Any

from ...config import Settings

_log = logging.getLogger(__name__)
_AGENT_STORAGES_CONFIGMAP = "dms-agent-storages"


def sync_agent_storages_configmap(
    settings: Settings,
    storage_name: str,
    mapping: dict[str, Any] | None,
) -> None:
    """storage mapping POST/PATCH/DELETE 시 dms-agent-storages ConfigMap을 동기화한다.

    mapping=None 이면 해당 storage_name 항목을 제거, 아니면 upsert.
    K8s 접근 실패 시 경고만 기록하고 API 응답은 정상으로 반환한다.
    """
    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config
    except ImportError:
        _log.warning(
            "kubernetes package not installed; skipping ConfigMap sync for %s",
            storage_name,
        )
        return

    try:
        cluster_name = settings.control_cluster_name
        kubeconfigs = settings.cluster_kubeconfigs or {}
        kubeconfig = kubeconfigs.get(cluster_name)
        if kubeconfig:
            k8s_config.load_kube_config(config_file=kubeconfig)
        else:
            try:
                k8s_config.load_incluster_config()
            except Exception:
                k8s_config.load_kube_config()

        core = k8s_client.CoreV1Api()
        namespace = settings.dm_namespace
        cm = core.read_namespaced_config_map(
            name=_AGENT_STORAGES_CONFIGMAP, namespace=namespace
        )
        raw = (cm.data or {}).get("storages.json", "{}")
        try:
            cm_data = json.loads(raw)
        except json.JSONDecodeError:
            cm_data = {}
        storages: list[dict[str, Any]] = cm_data.get("storages", [])

        # upsert or remove
        updated = [s for s in storages if s.get("storage_name") != storage_name]
        if mapping is not None:
            tmpl = mapping.get("backend_template") or {}
            mount_path = tmpl.get("mount_path", "")
            entry: dict[str, Any] = {
                "storage_name": storage_name,
                "backend_type": tmpl.get("backend_type", ""),
                "cluster_name": mapping.get("cluster_name") or "",
                "storage_class_name": mapping.get("storage_class_name")
                or tmpl.get("storage_class_name", ""),
                "mount_paths": [mount_path] if mount_path else [],
                "network_endpoints": tmpl.get("network_endpoints") or [],
            }
            if tmpl.get("csi_driver"):
                entry["csi_driver"] = tmpl["csi_driver"]
            updated.append(entry)

        new_json = json.dumps({"storages": updated}, indent=2)
        core.patch_namespaced_config_map(
            name=_AGENT_STORAGES_CONFIGMAP,
            namespace=namespace,
            body={"data": {"storages.json": new_json}},
        )
        action = "upserted" if mapping is not None else "removed"
        _log.info(
            "ConfigMap %s/%s %s entry for %s",
            namespace,
            _AGENT_STORAGES_CONFIGMAP,
            action,
            storage_name,
        )
    except Exception as exc:
        _log.warning(
            "Failed to sync ConfigMap %s/%s for %s: %s",
            settings.dm_namespace,
            _AGENT_STORAGES_CONFIGMAP,
            storage_name,
            exc,
        )
