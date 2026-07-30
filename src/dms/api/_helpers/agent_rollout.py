"""Rollout-restart the DMS agent DaemonSets + report rollout status.

The agent reads its storages.json only once at startup, so after a storage
mapping change (which auto-syncs the dms-agent-storages ConfigMap) the agents must
be restarted to pick up the new set. DMS already owns the agents (it deploys them
and syncs their ConfigMap), so it also drives their rollout — the same in-cluster
k8s access the ConfigMap sync uses. A new storage's data_management readiness only
becomes Ready once the DM agent has re-probed it, which is what this restart forces.
Targets the control cluster (settings.control_cluster_name), like the ConfigMap
sync; agents in other clusters are out of scope here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ...config import Settings

_log = logging.getLogger(__name__)

AGENT_DAEMONSETS = ("dms-dm-agent",)
_RESTART_ANNOTATION = "kubectl.kubernetes.io/restartedAt"


class KubernetesUnavailable(RuntimeError):
    """The kubernetes client isn't installed/configured — endpoints map this to 503."""


def _apps_api(settings: Settings):
    """AppsV1Api + namespace, resolved exactly like the ConfigMap sync helper."""
    try:
        from kubernetes import client as k8s_client
        from kubernetes import config as k8s_config
    except ImportError as exc:  # pragma: no cover - optional extra
        raise KubernetesUnavailable("kubernetes package not installed") from exc

    cluster_name = settings.control_cluster_name
    kubeconfigs = settings.cluster_kubeconfigs or {}
    kubeconfig = kubeconfigs.get(cluster_name)
    if kubeconfig:
        k8s_config.load_kube_config(config_file=kubeconfig)
    else:
        try:
            k8s_config.load_incluster_config()
        except Exception:  # noqa: BLE001 - fall back to a local kubeconfig
            k8s_config.load_kube_config()
    return k8s_client.AppsV1Api(), settings.dm_namespace


def restart_agents(settings: Settings, *, restarted_at: str | None = None) -> dict[str, Any]:
    """Trigger a rolling restart of the agent DaemonSets by stamping the standard
    `restartedAt` annotation on the pod template (same mechanism as
    `kubectl rollout restart`). Per-DaemonSet failures are collected, not fatal."""
    stamp = restarted_at or datetime.now(timezone.utc).isoformat()
    apps, namespace = _apps_api(settings)
    restarted: list[str] = []
    errors: dict[str, str] = {}
    for name in AGENT_DAEMONSETS:
        try:
            apps.patch_namespaced_daemon_set(
                name=name,
                namespace=namespace,
                body={
                    "spec": {
                        "template": {
                            "metadata": {"annotations": {_RESTART_ANNOTATION: stamp}}
                        }
                    }
                },
            )
            restarted.append(name)
            _log.info("agent rollout restart: %s/%s @ %s", namespace, name, stamp)
        except Exception as exc:  # noqa: BLE001 - report per-DaemonSet, don't abort
            errors[name] = str(exc)
            _log.warning("agent rollout restart failed for %s/%s: %s", namespace, name, exc)
    return {
        "namespace": namespace,
        "restarted_at": stamp,
        "restarted": restarted,
        "errors": errors,
    }


def agent_rollout_status(settings: Settings) -> dict[str, Any]:
    """Per-DaemonSet rollout progress (desired/updated/ready/available + a `rolling`
    flag) so the UI can show whether the restart has converged."""
    apps, namespace = _apps_api(settings)
    daemonsets: list[dict[str, Any]] = []
    for name in AGENT_DAEMONSETS:
        try:
            ds = apps.read_namespaced_daemon_set(name=name, namespace=namespace)
            st = ds.status
            desired = int(st.desired_number_scheduled or 0)
            updated = int(st.updated_number_scheduled or 0)
            ready = int(st.number_ready or 0)
            available = int(st.number_available or 0)
            unavailable = int(st.number_unavailable or 0)
            observed = int(st.observed_generation or 0)
            generation = int((ds.metadata.generation if ds.metadata else 0) or 0)
            # rollout has converged when the controller has observed the latest spec
            # and every desired pod is updated + available.
            rolling = not (
                observed >= generation
                and updated >= desired
                and available >= desired
                and unavailable == 0
            )
            annotations = {}
            if ds.spec and ds.spec.template and ds.spec.template.metadata:
                annotations = ds.spec.template.metadata.annotations or {}
            daemonsets.append(
                {
                    "name": name,
                    "desired": desired,
                    "updated": updated,
                    "ready": ready,
                    "available": available,
                    "unavailable": unavailable,
                    "rolling": rolling,
                    "restarted_at": annotations.get(_RESTART_ANNOTATION),
                }
            )
        except Exception as exc:  # noqa: BLE001 - surface per-DaemonSet
            daemonsets.append({"name": name, "error": str(exc)})
    return {"namespace": namespace, "daemonsets": daemonsets}
