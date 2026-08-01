"""Install manifests must mount shared-filesystem hostPath volumes with
HostToContainer (rslave) propagation.

Long-lived workers (dm-worker) read/operate on a shared FS bind-mounted from the
host. Under the default (None) propagation the
bind goes stale if the host unmounts/remounts the FS while the pod is alive,
leaving the worker with a detached view. HostToContainer lets host (re-)mounts
propagate in live. This is a regression guard for that bind-mount-stale fix.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_MANIFEST_DIR = Path(__file__).resolve().parent.parent / "install" / "kubernetes"


def _load_docs(name: str) -> list[dict]:
    text = (_MANIFEST_DIR / name).read_text()
    return [doc for doc in yaml.safe_load_all(text) if doc]


def _deployment(docs: list[dict], name: str) -> dict:
    for doc in docs:
        if doc.get("kind") == "Deployment" and doc["metadata"]["name"] == name:
            return doc
    raise AssertionError(f"Deployment {name!r} not found")


def _assert_hostpath_mounts_propagate(pod_spec: dict) -> set:
    hostpath_names = {
        v["name"] for v in pod_spec.get("volumes", []) if "hostPath" in v
    }
    assert hostpath_names, "expected at least one hostPath-backed volume"
    for container in pod_spec["containers"]:
        mounts = {m["name"]: m for m in container.get("volumeMounts", [])}
        for name in hostpath_names & set(mounts):
            assert mounts[name].get("mountPropagation") == "HostToContainer", (
                f"hostPath volumeMount {name!r} must set "
                f"mountPropagation=HostToContainer"
            )
    return hostpath_names


def test_control_plane_dm_worker_mounts_shared_fs_with_host_propagation():
    docs = _load_docs("control-plane.yaml")
    pod_spec = _deployment(docs, "dms-dm-worker")["spec"]["template"]["spec"]
    names = _assert_hostpath_mounts_propagate(pod_spec)
    # The artifact volume must bind the shared-FS MOUNT POINT (not a subdir under
    # it -- a subdir bind cannot receive the host re-mount) and use `Directory`
    # (not DirectoryOrCreate) so the pod fails fast if the FS is unmounted.
    for vol in pod_spec["volumes"]:
        if vol["name"] in names:
            assert vol["hostPath"].get("type") == "Directory"
