#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from dms.adapters import KubernetesVolcanoAdapter
from dms.config import Settings


def main() -> int:
    suffix = _env("DMS_PHASE22_SUFFIX")
    namespace = _env("DMS_PHASE22_NAMESPACE")
    image = _env("DMS_PHASE22_K8S_IMAGE")
    service_account = _env("DMS_PHASE22_SERVICE_ACCOUNT", "dms-dm-worker")
    queue = _env("DMS_PHASE22_QUEUE")
    priority_class = _env("DMS_PHASE22_PRIORITY_CLASS")
    mount_path = Path(_env("DMS_PHASE22_CEPH_MOUNT", "/mnt/testbed-cephfs"))
    node_names = [
        item.strip()
        for item in _env("DMS_PHASE22_NODES", "c1-control,c1-worker").split(",")
        if item.strip()
    ]
    if len(node_names) < 2:
        raise RuntimeError("Phase 22 testbed verification requires at least two nodes")

    data_root = mount_path / f"dms-phase22-{suffix}"
    artifact_root = mount_path / f"dms-phase22-artifacts-{suffix}"
    _prepare_fixtures(data_root, artifact_root)

    summary: dict[str, Any] = {
        "suffix": suffix,
        "namespace": namespace,
        "image": image,
        "nodes": node_names,
        "artifact_root": str(artifact_root),
        "jobs": {},
    }

    scan = _run_adapter_job(
        namespace=namespace,
        image=image,
        service_account=service_account,
        artifact_root=artifact_root,
        scheduler_backend="mpi-operator",
        plan=_scan_plan(suffix),
        data_job=_scan_job(
            suffix=suffix,
            mount_path=str(mount_path),
            node_names=node_names,
            queue=queue,
            priority_class=priority_class,
        ),
    )
    _assert_succeeded(scan, namespace, "scan")
    _require_file(artifact_root / f"job-phase22-scan-{suffix}" / "dscan-report.json")
    _require_metadata(artifact_root / f"job-phase22-scan-{suffix}")
    summary["jobs"]["scan"] = _job_summary(scan)

    dsync_preview = _run_adapter_job(
        namespace=namespace,
        image=image,
        service_account=service_account,
        artifact_root=artifact_root,
        scheduler_backend="volcano-job",
        plan=_sync_plan(suffix, phase="preview", tool="dsync"),
        data_job=_sync_job(
            suffix=suffix,
            mount_path=str(mount_path),
            node_names=node_names,
            queue=queue,
            priority_class=priority_class,
            tool="dsync",
            job_id=f"job-phase22-dsync-preview-{suffix}",
            request_id=f"req-phase22-dsync-preview-{suffix}",
        ),
    )
    _assert_succeeded(dsync_preview, namespace, "dsync-preview")
    _require_metadata(artifact_root / f"job-phase22-dsync-preview-{suffix}")
    summary["jobs"]["dsync_preview"] = _job_summary(dsync_preview)

    dsync_execution = _run_adapter_job(
        namespace=namespace,
        image=image,
        service_account=service_account,
        artifact_root=artifact_root,
        scheduler_backend="volcano-job",
        plan=_sync_plan(suffix, phase="execution", tool="dsync"),
        data_job=_sync_job(
            suffix=suffix,
            mount_path=str(mount_path),
            node_names=node_names,
            queue=queue,
            priority_class=priority_class,
            tool="dsync",
            job_id=f"job-phase22-dsync-execution-{suffix}",
            request_id=f"req-phase22-dsync-execution-{suffix}",
        ),
    )
    _assert_succeeded(dsync_execution, namespace, "dsync-execution")
    _require_file(data_root / "sync-dest" / "alpha.txt")
    _require_file(data_root / "sync-dest" / "nested" / "gamma.txt")
    _require_metadata(artifact_root / f"job-phase22-dsync-execution-{suffix}")
    summary["jobs"]["dsync_execution"] = _job_summary(dsync_execution)

    rm_preview = _run_adapter_job(
        namespace=namespace,
        image=image,
        service_account=service_account,
        artifact_root=artifact_root,
        scheduler_backend="volcano-job",
        plan=_rm_plan(suffix, phase="preview"),
        data_job=_rm_job(
            suffix=suffix,
            mount_path=str(mount_path),
            node_names=node_names,
            queue=queue,
            priority_class=priority_class,
            job_id=f"job-phase22-rm-preview-{suffix}",
            request_id=f"req-phase22-rm-preview-{suffix}",
        ),
    )
    _assert_succeeded(rm_preview, namespace, "rm-preview")
    _require_dir(data_root / "remove-me")
    _require_metadata(artifact_root / f"job-phase22-rm-preview-{suffix}")
    summary["jobs"]["rm_preview"] = _job_summary(rm_preview)

    rm_execution = _run_adapter_job(
        namespace=namespace,
        image=image,
        service_account=service_account,
        artifact_root=artifact_root,
        scheduler_backend="volcano-job",
        plan=_rm_plan(suffix, phase="execution"),
        data_job=_rm_job(
            suffix=suffix,
            mount_path=str(mount_path),
            node_names=node_names,
            queue=queue,
            priority_class=priority_class,
            job_id=f"job-phase22-rm-execution-{suffix}",
            request_id=f"req-phase22-rm-execution-{suffix}",
        ),
    )
    _assert_succeeded(rm_execution, namespace, "rm-execution")
    _require_absent(data_root / "remove-me")
    _require_metadata(artifact_root / f"job-phase22-rm-execution-{suffix}")
    summary["jobs"]["rm_execution"] = _job_summary(rm_execution)

    nsync_execution = _run_adapter_job(
        namespace=namespace,
        image=image,
        service_account=service_account,
        artifact_root=artifact_root,
        scheduler_backend="volcano-job",
        plan=_sync_plan(suffix, phase="execution", tool="nsync"),
        data_job=_nsync_job(
            suffix=suffix,
            mount_path=str(mount_path),
            source_node=node_names[0],
            destination_node=node_names[1],
            queue=queue,
            priority_class=priority_class,
        ),
    )
    _assert_succeeded(nsync_execution, namespace, "nsync-execution")
    _require_file(data_root / "nsync-dest" / "alpha.txt")
    _require_file(data_root / "nsync-dest" / "nested" / "gamma.txt")
    _require_metadata(artifact_root / f"job-phase22-nsync-execution-{suffix}")
    summary["jobs"]["nsync_execution"] = _job_summary(nsync_execution)

    summary_path = artifact_root / "phase22-adapter-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _run_adapter_job(
    *,
    namespace: str,
    image: str,
    service_account: str,
    artifact_root: Path,
    scheduler_backend: str,
    plan: dict[str, Any],
    data_job: dict[str, Any],
) -> Any:
    settings = Settings(
        database_url="sqlite:///:memory:",
        observability_database_url="sqlite:///:memory:",
        dm_kubernetes_mode="cluster",
        dm_namespace=namespace,
        dm_job_image=image,
        dm_artifact_base_uri=f"file://{artifact_root}",
        dm_service_account=service_account,
        dm_scheduler_backend=scheduler_backend,
        dm_scan_timeout_seconds=180,
        dm_sync_preview_timeout_seconds=180,
        dm_sync_execution_timeout_seconds=180,
        dm_rm_preview_timeout_seconds=180,
        dm_rm_execution_timeout_seconds=180,
        dm_monitor_poll_seconds=2,
        kubernetes_inventory_timeout_seconds=30,
        kubernetes_mutation_timeout_seconds=60,
    )
    return KubernetesVolcanoAdapter(settings).create_job(plan, data_job)


def _scan_plan(suffix: str) -> dict[str, Any]:
    return {
        "desired_state": {
            "target": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/scan-input",
            }
        },
        "execution_metadata": {"phase": "execution"},
    }


def _scan_job(
    *,
    suffix: str,
    mount_path: str,
    node_names: list[str],
    queue: str,
    priority_class: str,
) -> dict[str, Any]:
    selected = [
        {"cluster_name": "cluster-a", "node_name": node, "mount_path": mount_path}
        for node in node_names
    ]
    return {
        "job_id": f"job-phase22-scan-{suffix}",
        "request_id": f"req-phase22-scan-{suffix}",
        "operation": "data.scan",
        "storage_name": "testbed-cephfs",
        "target": f"dms-phase22-{suffix}/scan-input",
        "normalized_target": {
            "storage_name": "testbed-cephfs",
            "path": f"dms-phase22-{suffix}/scan-input",
        },
        "selected_tool": "dscan",
        "priority": 200,
        "worker_pool": {"selected_candidates": selected},
        "preflight_result": {
            "identity_mapping": _identity(),
            "effective_resource_model": _resource_model(
                worker_nodes=len(node_names),
                processes_per_node=1,
                queue=queue,
                priority_class=priority_class,
                eligible_nodes=node_names,
            ),
        },
    }


def _sync_plan(suffix: str, *, phase: str, tool: str) -> dict[str, Any]:
    source_name = "nsync-source" if tool == "nsync" else "sync-source"
    destination_name = "nsync-dest" if tool == "nsync" else "sync-dest"
    return {
        "desired_state": {
            "source": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/{source_name}",
            },
            "destination": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/{destination_name}",
            },
            "options": {"contents": True},
        },
        "execution_metadata": {"phase": phase},
    }


def _sync_job(
    *,
    suffix: str,
    mount_path: str,
    node_names: list[str],
    queue: str,
    priority_class: str,
    tool: str,
    job_id: str,
    request_id: str,
) -> dict[str, Any]:
    selected = [
        {
            "cluster_name": "cluster-a",
            "node_name": node,
            "mount_path": mount_path,
            "source_mount_path": mount_path,
            "destination_mount_path": mount_path,
        }
        for node in node_names
    ]
    return {
        "job_id": job_id,
        "request_id": request_id,
        "operation": "data.sync",
        "storage_name": "testbed-cephfs",
        "target": None,
        "normalized_target": {
            "source": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/sync-source",
            },
            "destination": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/sync-dest",
            },
        },
        "selected_tool": tool,
        "priority": 200,
        "worker_pool": {"selected_candidates": selected},
        "preflight_result": {
            "identity_mapping": _identity(),
            "effective_resource_model": _resource_model(
                worker_nodes=len(node_names),
                processes_per_node=1,
                queue=queue,
                priority_class=priority_class,
                eligible_nodes=node_names,
            ),
        },
    }


def _nsync_job(
    *,
    suffix: str,
    mount_path: str,
    source_node: str,
    destination_node: str,
    queue: str,
    priority_class: str,
) -> dict[str, Any]:
    source_candidate = {
        "cluster_name": "cluster-a",
        "node_name": source_node,
        "mount_path": mount_path,
    }
    destination_candidate = {
        "cluster_name": "cluster-a",
        "node_name": destination_node,
        "mount_path": mount_path,
    }
    return {
        "job_id": f"job-phase22-nsync-execution-{suffix}",
        "request_id": f"req-phase22-nsync-execution-{suffix}",
        "operation": "data.sync",
        "storage_name": "testbed-cephfs",
        "target": None,
        "normalized_target": {
            "source": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/nsync-source",
            },
            "destination": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/nsync-dest",
            },
        },
        "selected_tool": "nsync",
        "priority": 200,
        "worker_pool": {
            "source_candidates": [source_candidate],
            "destination_candidates": [destination_candidate],
        },
        "preflight_result": {
            "identity_mapping": _identity(),
            "effective_resource_model": {
                "scheduler_selection": "eligible_node_set",
                "source_node_count": 1,
                "destination_node_count": 1,
                "worker_pod_count": 2,
                "processes_per_node": 1,
                "process_count": 2,
                "queue": queue,
                "priority_class": priority_class,
                "eligible_source_nodes": [source_node],
                "eligible_destination_nodes": [destination_node],
            },
        },
    }


def _rm_plan(suffix: str, *, phase: str) -> dict[str, Any]:
    return {
        "desired_state": {
            "target": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/remove-me",
            },
            "options": {},
        },
        "execution_metadata": {"phase": phase},
    }


def _rm_job(
    *,
    suffix: str,
    mount_path: str,
    node_names: list[str],
    queue: str,
    priority_class: str,
    job_id: str,
    request_id: str,
) -> dict[str, Any]:
    selected = [
        {"cluster_name": "cluster-a", "node_name": node, "mount_path": mount_path}
        for node in node_names
    ]
    return {
        "job_id": job_id,
        "request_id": request_id,
        "operation": "data.rm",
        "storage_name": "testbed-cephfs",
        "target": {
            "storage_name": "testbed-cephfs",
            "path": f"dms-phase22-{suffix}/remove-me",
        },
        "normalized_target": {
            "target": {
                "storage_name": "testbed-cephfs",
                "path": f"dms-phase22-{suffix}/remove-me",
            }
        },
        "selected_tool": "drm",
        "priority": 200,
        "worker_pool": {"selected_candidates": selected},
        "preflight_result": {
            "identity_mapping": _identity(),
            "effective_resource_model": _resource_model(
                worker_nodes=len(node_names),
                processes_per_node=1,
                queue=queue,
                priority_class=priority_class,
                eligible_nodes=node_names,
            ),
        },
    }


def _resource_model(
    *,
    worker_nodes: int,
    processes_per_node: int,
    queue: str,
    priority_class: str,
    eligible_nodes: list[str],
) -> dict[str, Any]:
    return {
        "scheduler_selection": "eligible_node_set",
        "required_node_count": worker_nodes,
        "selected_node_count": worker_nodes,
        "worker_pod_count": worker_nodes,
        "processes_per_node": processes_per_node,
        "process_count": worker_nodes * processes_per_node,
        "queue": queue,
        "priority_class": priority_class,
        "eligible_nodes": eligible_nodes,
    }


def _identity() -> dict[str, Any]:
    return {
        "uid": 10000,
        "gid": 10000,
        "posix_username": "alice",
    }


def _prepare_fixtures(data_root: Path, artifact_root: Path) -> None:
    _run(["sudo", "rm", "-rf", str(data_root), str(artifact_root)])
    for path in [
        data_root / "scan-input" / "nested",
        data_root / "sync-source" / "nested",
        data_root / "sync-dest",
        data_root / "remove-me" / "nested",
        data_root / "nsync-source" / "nested",
        data_root / "nsync-dest",
        artifact_root,
    ]:
        _run(["sudo", "mkdir", "-p", str(path)])
    _run(["sudo", "chown", "-R", "alice:developers", str(data_root)])
    _run(["sudo", "chmod", "-R", "0770", str(data_root)])
    _run(["sudo", "chmod", "-R", "0777", str(artifact_root)])
    _write_as_alice(data_root / "scan-input" / "alpha.txt", "scan-alpha\n")
    _write_as_alice(data_root / "scan-input" / "nested" / "gamma.txt", "scan-gamma\n")
    _write_as_alice(data_root / "sync-source" / "alpha.txt", "sync-alpha\n")
    _write_as_alice(data_root / "sync-source" / "nested" / "gamma.txt", "sync-gamma\n")
    _write_as_alice(data_root / "remove-me" / "doomed.txt", "remove-me\n")
    _write_as_alice(data_root / "remove-me" / "nested" / "doomed.txt", "remove-me-nested\n")
    _write_as_alice(data_root / "nsync-source" / "alpha.txt", "nsync-alpha\n")
    _write_as_alice(data_root / "nsync-source" / "nested" / "gamma.txt", "nsync-gamma\n")


def _write_as_alice(path: Path, content: str) -> None:
    script = f"printf %s {sh_quote(content)} > {sh_quote(str(path))}"
    _run(["sudo", "-u", "alice", "sh", "-c", script])


def _assert_succeeded(result: Any, namespace: str, label: str) -> None:
    phase = result.observed_state.get("phase")
    if phase == "Succeeded":
        return
    _debug_kubernetes(namespace)
    raise RuntimeError(f"{label} did not succeed: phase={phase} observed={result.observed_state}")


def _job_summary(result: Any) -> dict[str, Any]:
    return {
        "job_ref": result.applied_state.get("job_ref"),
        "scheduler_backend": result.applied_state.get("scheduler_backend"),
        "submitted_kind": result.applied_state.get("submitted_kind"),
        "scheduled_nodes": result.observed_state.get("scheduled_nodes"),
        "selected_node_count": result.observed_state.get("selected_node_count"),
        "worker_pod_count": result.observed_state.get("worker_pod_count"),
        "process_count": result.observed_state.get("process_count"),
        "artifact_uri": result.artifact_uri,
        "mpi_metadata": result.applied_state.get("mpi_metadata"),
    }


def _require_metadata(job_artifact_dir: Path) -> None:
    for relative in [
        "mpi/submitted.yaml",
        "mpi/launch.json",
        "mpi/workers.json",
        "mpi/scheduler.json",
        "mpi/mpirun.json",
    ]:
        _require_file(job_artifact_dir / relative)


def _require_file(path: Path) -> None:
    if not _sudo_path_test("-f", path):
        raise RuntimeError(f"missing expected file: {path}")


def _require_dir(path: Path) -> None:
    if not _sudo_path_test("-d", path):
        raise RuntimeError(f"missing expected directory: {path}")


def _require_absent(path: Path) -> None:
    if not _sudo_path_test("!", path):
        raise RuntimeError(f"expected path to be absent: {path}")


def _sudo_path_test(operator: str, path: Path) -> bool:
    if operator == "!":
        command = ["sudo", "test", "!", "-e", str(path)]
    else:
        command = ["sudo", "test", operator, str(path)]
    return subprocess.run(command, check=False).returncode == 0


def _debug_kubernetes(namespace: str) -> None:
    for command in [
        ["kubectl", "-n", namespace, "get", "mpijob,job.batch.volcano.sh,pod", "-o", "wide"],
        ["kubectl", "-n", namespace, "describe", "pods"],
    ]:
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        print(f"$ {' '.join(command)}", file=sys.stderr)
        print(completed.stdout[-12000:], file=sys.stderr)
    pods = subprocess.run(
        ["kubectl", "-n", namespace, "get", "pods", "-o", "jsonpath={range .items[*]}{.metadata.name}{\"\\n\"}{end}"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    for pod in [item for item in pods.stdout.splitlines() if item.strip()]:
        completed = subprocess.run(
            ["kubectl", "-n", namespace, "logs", pod, "--all-containers=true", "--tail=120"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        print(f"$ kubectl -n {namespace} logs {pod} --all-containers=true --tail=120", file=sys.stderr)
        print(completed.stdout[-12000:], file=sys.stderr)


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def _env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise RuntimeError(f"{name} is required")
    return value


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


if __name__ == "__main__":
    raise SystemExit(main())
