from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import shlex
import subprocess
import time
from typing import Any, Protocol
from urllib.parse import urlparse

from ..config import Settings
from .base import *  # noqa: F401,F403




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

    def create_job(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        *,
        on_submit: Callable[[str], None] | None = None,
    ) -> AdapterResult:
        self.calls.append(("create_job", data_job["job_id"]))
        if on_submit is not None:
            on_submit(f"volcano/{data_job['job_id']}")
        tool = data_job["selected_tool"] or _default_tool_for_operation(
            data_job["operation"]
        )
        phase = (plan.get("execution_metadata") or {}).get("phase", "execution")
        resource_model = (data_job.get("preflight_result") or {}).get(
            "effective_resource_model"
        ) or {}
        selected = _unique_selected_candidates(
            (data_job.get("worker_pool") or {}).get("selected_candidates") or []
        )
        worker_pod_count = int(
            resource_model.get("worker_pod_count") or (1 if selected else 0)
        )
        processes_per_node = int(resource_model.get("processes_per_node") or 1)
        scheduled = selected[:worker_pod_count]
        selected_nodes = [
            item.get("node_name") for item in scheduled if item.get("node_name")
        ]
        selected_node = selected_nodes[0] if len(selected_nodes) == 1 else None
        process_count = worker_pod_count * processes_per_node
        artifact_uri = f"stub://artifacts/{data_job['job_id']}"
        mpi_metadata = _mpi_metadata_uris(artifact_uri)
        summary = {
            "operation": data_job["operation"],
            "selected_tool": tool,
            "phase": phase,
            "dry_run": phase == "preview",
            "file_count": 0,
            "directory_count": 1,
            "total_bytes": 0,
            "error_count": 0,
            "selected_node": selected_node,
            "scheduled_nodes": selected_nodes,
            "worker_pod_count": worker_pod_count,
            "processes_per_node": processes_per_node,
            "process_count": process_count,
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
                "scheduler_backend": "stub",
                "selected_node": selected_node,
                "selected_node_count": len(selected_nodes),
                "worker_pod_count": worker_pod_count,
                "processes_per_node": processes_per_node,
                "process_count": process_count,
                "mpi_metadata": mpi_metadata,
            },
            observed_state={
                "adapter": "volcano-stub",
                "job_ref": f"volcano/{data_job['job_id']}",
                "phase": "Succeeded",
                "summary": summary,
                "selected_node": selected_node,
                "selected_node_count": len(selected_nodes),
                "process_count": process_count,
                "mpi_metadata": mpi_metadata,
                "pod_summary": {
                    "worker_pod_count": worker_pod_count,
                    "pods": (
                        [
                            {
                                "name": f"{tool}-{index}-{data_job['job_id']}",
                                "node_name": candidate.get("node_name"),
                                "phase": "Succeeded",
                                "role": "worker",
                            }
                            for index, candidate in enumerate(scheduled)
                        ]
                        + [
                            {
                                "name": f"launcher-{data_job['job_id']}",
                                "node_name": "stub-launcher",
                                "phase": "Succeeded",
                                "role": "launcher",
                            }
                        ]
                        if worker_pod_count
                        else []
                    ),
                },
            },
            message="volcano job stub completed",
            artifact_uri=artifact_uri,
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

    def volcano_status(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "queues": [],
            "jobs": [],
            "scheduler": [],
            "errors": {"queues": None, "jobs": None, "scheduler": None},
        }

    def volcano_job_metrics(self, **kwargs: Any) -> dict[str, Any]:
        return {"jobs": [], "error": None}



# Preflight pods print this marker (one line) when a POSIX check fails, so the DM
# worker can surface a precise reason (e.g. source_not_found,
# destination_parent_missing, destination_parent_not_writable) instead of a generic
# posix_permission_denied.
_PREFLIGHT_REASON_MARKER = "DMS_PREFLIGHT_REASON="


def _parse_preflight_reason(logs: Any) -> str | None:
    """Return the precise reason a preflight pod emitted (last marker line wins),
    or None if the pod produced no marker (e.g. it crashed before the checks).

    ``logs`` may be the dict returned by ``_pod_logs`` ({stdout, stderr, ...}) or a
    raw string."""
    if isinstance(logs, dict):
        text = (logs.get("stdout") or "") + "\n" + (logs.get("stderr") or "")
    else:
        text = logs or ""
    if not text:
        return None
    reason: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(_PREFLIGHT_REASON_MARKER):
            value = stripped[len(_PREFLIGHT_REASON_MARKER) :].strip()
            if value:
                reason = value
    return reason


def _preflight_script(setup: list[str], checks: list[str], ok_print: str) -> str:
    """Build a POSIX-sh preflight script. Each entry in ``checks`` runs a ``[ ... ]``
    test that, on failure, calls ``fail <reason>`` -- printing a
    ``DMS_PREFLIGHT_REASON=<reason>`` marker and exiting non-zero. On success the
    script prints ``ok_print`` and exits 0. The first failing check wins."""
    return "\n".join(
        [
            "set -u",
            "fail() { printf '" + _PREFLIGHT_REASON_MARKER + "%s\\n' \"$1\"; exit 1; }",
            *setup,
            *checks,
            ok_print,
        ]
    )


# --- Volcano job-metric parsing helpers (pure, for volcano_job_metrics) ----------

def _parse_k8s_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _secs_between(a: str | None, b: str | None) -> float | None:
    da, db = _parse_k8s_ts(a), _parse_k8s_ts(b)
    if da is None or db is None:
        return None
    delta = (db - da).total_seconds()
    return round(delta, 3) if delta >= 0 else None


def _min_iso(values: list[str | None]) -> str | None:
    parsed = [(v, _parse_k8s_ts(v)) for v in values]
    parsed = [(v, d) for v, d in parsed if d is not None]
    return min(parsed, key=lambda x: x[1])[0] if parsed else None


def _max_iso(values: list[str | None]) -> str | None:
    parsed = [(v, _parse_k8s_ts(v)) for v in values]
    parsed = [(v, d) for v, d in parsed if d is not None]
    return max(parsed, key=lambda x: x[1])[0] if parsed else None


def _parse_cpu_cores(value: Any) -> float:
    s = str(value).strip() if value is not None else ""
    if not s:
        return 0.0
    try:
        if s.endswith("m"):
            return int(s[:-1]) / 1000.0
        if s.endswith("n"):
            return int(s[:-1]) / 1_000_000_000.0
        return float(s)
    except ValueError:
        return 0.0


_MEM_UNITS = {
    "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4, "Pi": 1024**5,
    "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4, "P": 1000**5,
}


def _parse_mem_bytes(value: Any) -> int:
    s = str(value).strip() if value is not None else ""
    if not s:
        return 0
    for suffix, mult in _MEM_UNITS.items():
        if s.endswith(suffix):
            try:
                return int(float(s[: -len(suffix)]) * mult)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def _pod_scheduled_iso(pod: dict) -> str | None:
    for c in ((pod.get("status") or {}).get("conditions")) or []:
        if c.get("type") == "PodScheduled":
            return c.get("lastTransitionTime")
    return None


def _pod_finished_iso(pod: dict) -> str | None:
    finishes: list[str | None] = []
    for c in ((pod.get("status") or {}).get("containerStatuses")) or []:
        term = (c.get("state") or {}).get("terminated") or {}
        if term.get("finishedAt"):
            finishes.append(term["finishedAt"])
    return _max_iso(finishes)


def _pod_env(pods: list[dict], name: str) -> str | None:
    """First non-empty value of env var `name` across a job's pods' containers."""
    for p in pods:
        for c in ((p.get("spec") or {}).get("containers")) or []:
            for e in c.get("env") or []:
                if e.get("name") == name and e.get("value"):
                    return e.get("value")
    return None


def _job_resource_requests(spec: dict) -> tuple[float, int, int]:
    """Sum requested cpu cores / mem bytes / pods across a vcjob's tasks."""
    cpu, mem, pods = 0.0, 0, 0
    for task in spec.get("tasks") or []:
        replicas = int(task.get("replicas") or 0)
        pods += replicas
        containers = (((task.get("template") or {}).get("spec")) or {}).get("containers") or []
        per_cpu = sum(_parse_cpu_cores(((ct.get("resources") or {}).get("requests") or {}).get("cpu")) for ct in containers)
        per_mem = sum(_parse_mem_bytes(((ct.get("resources") or {}).get("requests") or {}).get("memory")) for ct in containers)
        cpu += replicas * per_cpu
        mem += replicas * per_mem
    return round(cpu, 3), mem, pods


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
        if observed.get("phase") == "Failed":
            reason = _parse_preflight_reason(logs) or "posix_permission_denied"
        else:
            reason = "preflight_pod_not_succeeded"
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
        # The POSIX permission preflight runs as the resolved POSIX user inside a pod that
        # hostPath-mounts the relevant storage. For a co-located tool (dsync/drm) one pod
        # mounts everything it needs. For nsync the source and destination live on DISJOINT
        # node sets, so no single node can mount both -- we run one preflight per role, each
        # pinned to a representative node of that role, and require every role to pass.
        role_manifests = self._data_preflight_manifests(plan, data_job, preflight, phase)
        results = [
            (role, self._run_preflight_pod(manifest, phase))
            for role, manifest in role_manifests
        ]
        if len(results) == 1:
            return results[0][1]
        role_checks = {role: result for role, result in results}
        failed = [
            (role, result)
            for role, result in results
            if result.get("status") != "Ready"
        ]
        if not failed:
            return {
                "status": "Ready",
                "source": "kubernetes-preflight-pod",
                "reason": f"{phase}_posix_permission_check_passed",
                "role_checks": role_checks,
            }
        failed_role, failed_result = failed[0]
        return {
            **failed_result,
            "status": "Rejected",
            "source": "kubernetes-preflight-pod",
            "reason": failed_result.get("reason") or "posix_permission_denied",
            "failed_role": failed_role,
            "role_checks": role_checks,
        }

    def _data_preflight_manifests(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        preflight: dict[str, Any],
        phase: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Preflight pod manifests tagged by role.

        nsync (separated source/destination node sets) -> one ``source`` + one
        ``destination`` pod; every co-located tool (dsync/drm) -> a single ``both`` pod.
        """
        if (
            data_job["operation"] == "data.sync"
            and data_job.get("selected_tool") == "nsync"
        ):
            return [
                (
                    "source",
                    self._sync_role_preflight_manifest(
                        plan, data_job, preflight, phase, "source"
                    ),
                ),
                (
                    "destination",
                    self._sync_role_preflight_manifest(
                        plan, data_job, preflight, phase, "destination"
                    ),
                ),
            ]
        return [
            ("both", self._data_preflight_pod_manifest(plan, data_job, preflight, phase))
        ]

    def _run_preflight_pod(
        self, manifest: dict[str, Any], phase: str
    ) -> dict[str, Any]:
        """Apply one preflight pod, wait for it to terminate, judge it, and clean it up."""
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
        if observed.get("phase") == "Failed":
            reason = _parse_preflight_reason(logs) or "posix_permission_denied"
        else:
            reason = "preflight_pod_not_succeeded"
        return {
            "status": "Rejected",
            "source": "kubernetes-preflight-pod",
            "reason": reason,
            "pod_ref": f"pod://{namespace}/{name}",
            "observed_state": observed,
            "logs": logs,
            "cleanup": cleanup,
        }

    def create_job(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        *,
        on_submit: Callable[[str], None] | None = None,
    ) -> AdapterResult:
        if not self.settings.dm_job_image:
            raise DataManagementRuntimeError(
                "DMS_DM_JOB_IMAGE is required for live data jobs"
            )
        last_error = ""
        manifest: dict[str, Any] | None = None
        completed: subprocess.CompletedProcess[str] | None = None
        for candidate in self._candidate_manifests(plan, data_job):
            namespace = candidate["metadata"]["namespace"]
            completed = subprocess.run(
                ["kubectl", "-n", namespace, "apply", "-f", "-"],
                input=json.dumps(_drop_none(candidate)),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.kubernetes_mutation_timeout_seconds,
                check=False,
            )
            if completed.returncode == 0:
                manifest = candidate
                break
            last_error = completed.stderr.strip() or completed.stdout.strip()
            if not _can_fallback_from_manifest_apply(
                candidate, last_error, self.settings.dm_scheduler_backend
            ):
                break
        if manifest is None or completed is None or completed.returncode != 0:
            raise DataManagementRuntimeError(
                "data management job apply failed: " f"{last_error}"
            )
        job_name = manifest["metadata"]["name"]
        namespace = manifest["metadata"]["namespace"]
        job_ref = _job_ref_for_manifest(manifest)
        # Hand the ref back to the caller BEFORE blocking on the run so a concurrent cancel
        # can find and terminate this job (otherwise the ref is only recorded once it ends).
        if on_submit is not None:
            on_submit(job_ref)
        phase = (plan.get("execution_metadata") or {}).get("phase", "execution")
        observed = self._wait_for_terminal(
            job_ref, timeout_seconds=self._timeout_seconds(data_job["operation"], phase)
        )
        artifact_uri = _artifact_job_uri(
            self.settings.dm_artifact_base_uri, data_job["job_id"]
        )
        _write_mpi_metadata_artifacts(
            artifact_uri=artifact_uri,
            manifest=manifest,
            data_job=data_job,
            phase=phase,
            observed=observed,
        )
        resource_model = _resource_model(data_job)
        selected = _unique_selected_candidates(
            (data_job.get("worker_pool") or {}).get("selected_candidates") or []
        )
        scheduled_nodes = _scheduled_nodes_from_observed(observed)
        selected_node = scheduled_nodes[0] if len(scheduled_nodes) == 1 else None
        worker_pod_count = int(
            (observed.get("pod_summary") or {}).get("worker_pod_count")
            or resource_model.get("worker_pod_count")
            or len(scheduled_nodes)
            or 0
        )
        processes_per_node = int(resource_model.get("processes_per_node") or 1)
        process_count = worker_pod_count * processes_per_node
        mpi_metadata = _mpi_metadata_uris(artifact_uri)
        return AdapterResult(
            applied_state={
                "adapter": "volcano-kubectl",
                "job_ref": job_ref,
                "namespace": namespace,
                "name": job_name,
                "submitted_kind": manifest["kind"],
                "scheduler_backend": _scheduler_backend_for_manifest(manifest),
                "selected_tool": data_job["selected_tool"]
                or _default_tool_for_operation(data_job["operation"]),
                "priority": data_job["priority"],
                "phase": phase,
                "image_ref": self.settings.dm_job_image_ref,
                "artifact_uri": artifact_uri,
                "eligible_nodes": [
                    item.get("node_name") for item in selected if item.get("node_name")
                ],
                "selected_node": selected_node,
                "selected_node_count": len(scheduled_nodes),
                "worker_pod_count": worker_pod_count,
                "processes_per_node": processes_per_node,
                "process_count": process_count,
                "mpi_metadata": mpi_metadata,
            },
            observed_state={
                **observed,
                "selected_node": selected_node,
                "selected_node_count": len(scheduled_nodes),
                "scheduled_nodes": scheduled_nodes,
                "worker_pod_count": worker_pod_count,
                "processes_per_node": processes_per_node,
                "process_count": process_count,
                "mpi_metadata": mpi_metadata,
            },
            message="volcano data job completed",
            artifact_uri=artifact_uri,
        )

    def get_job(self, job_ref: str) -> dict[str, Any]:
        if job_ref.startswith("mpijob://"):
            namespace, name = _parse_kind_ref(job_ref, "mpijob://")
            completed = subprocess.run(
                ["kubectl", "-n", namespace, "get", "mpijob", name, "-o", "json"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.settings.kubernetes_inventory_timeout_seconds,
                check=False,
            )
            if completed.returncode != 0:
                raise DataManagementRuntimeError(
                    "MPIJob read failed: "
                    f"{completed.stderr.strip() or completed.stdout.strip()}"
                )
            payload = json.loads(completed.stdout)
            labels = payload.get("metadata", {}).get("labels") or {}
            pod_summary = self._job_pod_summary(namespace, labels)
            phase = _mpijob_phase(payload)
            return {
                "adapter": "mpijob-kubectl",
                "job_ref": job_ref,
                "phase": phase,
                "status": payload.get("status", {}),
                "pod_summary": pod_summary,
            }
        namespace, name = _parse_volcano_ref(job_ref)
        completed = subprocess.run(
            [
                "kubectl",
                "-n",
                namespace,
                "get",
                "job.batch.volcano.sh",
                name,
                "-o",
                "json",
            ],
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
        phase = (
            state.get("phase") or payload.get("status", {}).get("phase") or "Unknown"
        )
        labels = payload.get("metadata", {}).get("labels") or {}
        pod_summary = self._job_pod_summary(namespace, labels)
        return {
            "adapter": "volcano-kubectl",
            "job_ref": job_ref,
            "phase": phase,
            "state": state,
            "status": payload.get("status", {}),
            "pod_summary": pod_summary,
        }

    def _job_pod_summary(
        self, namespace: str, job_labels: dict[str, Any]
    ) -> dict[str, Any]:
        data_job_id = job_labels.get("dms.openai.com/data-job-id")
        if not data_job_id:
            return {"worker_pod_count": 0, "pods": []}
        selectors = [
            "app.kubernetes.io/name=dms-data-management",
            f"dms.openai.com/data-job-id={data_job_id}",
        ]
        phase = job_labels.get("dms.openai.com/data-phase")
        if phase:
            selectors.append(f"dms.openai.com/data-phase={phase}")
        tool = job_labels.get("dms.openai.com/data-tool")
        if tool:
            selectors.append(f"dms.openai.com/data-tool={tool}")
        selector = ",".join(selectors)
        completed = subprocess.run(
            ["kubectl", "-n", namespace, "get", "pods", "-l", selector, "-o", "json"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_inventory_timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            return {
                "worker_pod_count": 0,
                "pods": [],
                "reason": "pod_summary_read_failed",
                "message": completed.stderr.strip() or completed.stdout.strip(),
            }
        payload = json.loads(completed.stdout)
        pods: list[dict[str, Any]] = []
        for item in payload.get("items") or []:
            status = item.get("status") or {}
            spec = item.get("spec") or {}
            labels = item.get("metadata", {}).get("labels") or {}
            pods.append(
                {
                    "name": item.get("metadata", {}).get("name"),
                    "node_name": spec.get("nodeName"),
                    "phase": status.get("phase"),
                    "role": labels.get("dms.openai.com/data-role") or "worker",
                    "role_kind": labels.get("dms.openai.com/data-role-kind"),
                    "container_statuses": status.get("containerStatuses") or [],
                }
            )
        worker_pods = [pod for pod in pods if pod.get("role") != "launcher"]
        launcher_pods = [pod for pod in pods if pod.get("role") == "launcher"]
        return {
            "worker_pod_count": len(worker_pods),
            "launcher_pod_count": len(launcher_pods),
            "pods": pods,
        }

    def terminate_job(self, job_ref: str) -> AdapterResult:
        if job_ref.startswith("mpijob://"):
            namespace, name = _parse_kind_ref(job_ref, "mpijob://")
            resource = "mpijob"
            message = "MPIJob delete failed: "
        else:
            namespace, name = _parse_volcano_ref(job_ref)
            resource = "job.batch.volcano.sh"
            message = "VolcanoJob delete failed: "
        completed = subprocess.run(
            [
                "kubectl",
                "-n",
                namespace,
                "delete",
                resource,
                name,
                "--ignore-not-found",
            ],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=self.settings.kubernetes_mutation_timeout_seconds,
            check=False,
        )
        if completed.returncode != 0:
            raise DataManagementRuntimeError(
                f"{message}{completed.stderr.strip() or completed.stdout.strip()}"
            )
        return AdapterResult(
            applied_state={"job_ref": job_ref, "terminated": True},
            observed_state={"job_ref": job_ref, "phase": "Cancelled"},
            message="data management job terminated",
        )

    def _kubectl_get_items(self, args: list[str], timeout: float = 10.0) -> list[dict]:
        result = subprocess.run(
            ["kubectl", *args, "-o", "json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip()[:300])
        return json.loads(result.stdout).get("items", [])

    def volcano_status(
        self,
        *,
        job_namespace: str = "dms",
        scheduler_namespace: str = "volcano-system",
    ) -> dict[str, Any]:
        errors: dict[str, str | None] = {"queues": None, "jobs": None, "scheduler": None}

        # Queues (cluster-scoped)
        queues: list[dict[str, Any]] = []
        try:
            items = self._kubectl_get_items(["get", "queues.scheduling.volcano.sh"])
            for it in items:
                meta = it.get("metadata") or {}
                st = it.get("status") or {}
                queues.append({
                    "name": meta.get("name"),
                    "state": st.get("state"),
                    "running": st.get("running") or 0,
                    "pending": st.get("pending") or 0,
                    "inqueue": st.get("inqueue") or 0,
                })
        except Exception as exc:
            errors["queues"] = str(exc)

        # Jobs (vcjob, namespaced)
        jobs: list[dict[str, Any]] = []
        try:
            items = self._kubectl_get_items(["get", "vcjob", "-n", job_namespace])
            for it in items:
                meta = it.get("metadata") or {}
                spec = it.get("spec") or {}
                st = it.get("status") or {}
                state = st.get("state") or {}
                jobs.append({
                    "name": meta.get("name"),
                    "namespace": meta.get("namespace"),
                    "queue": spec.get("queue"),
                    "phase": state.get("phase"),
                    "running": st.get("running") or 0,
                    "pending": st.get("pending") or 0,
                    "succeeded": st.get("succeeded") or 0,
                    "failed": st.get("failed") or 0,
                    "min_available": spec.get("minAvailable"),
                })
        except Exception as exc:
            errors["jobs"] = str(exc)

        # Scheduler pods
        scheduler: list[dict[str, Any]] = []
        try:
            items = self._kubectl_get_items(["get", "pods", "-n", scheduler_namespace])
            for it in items:
                meta = it.get("metadata") or {}
                st = it.get("status") or {}
                container_statuses = st.get("containerStatuses") or []
                ready = (
                    all(cs.get("ready") for cs in container_statuses)
                    if container_statuses
                    else None
                )
                restarts = sum(cs.get("restartCount") or 0 for cs in container_statuses)
                scheduler.append({
                    "name": meta.get("name"),
                    "phase": st.get("phase"),
                    "ready": ready,
                    "restarts": restarts,
                })
        except Exception as exc:
            errors["scheduler"] = str(exc)

        return {"queues": queues, "jobs": jobs, "scheduler": scheduler, "errors": errors}

    def volcano_job_metrics(
        self, *, job_namespace: str = "dms", limit: int = 300
    ) -> dict[str, Any]:
        """Per-job Volcano lifecycle metrics (read-only live snapshot) for the dashboard:
        timestamps, the three latencies, status counts, and requested resources. Pods are
        correlated to their vcjob via the ``volcano.sh/job-name`` label. Newest jobs first,
        capped at ``limit``."""
        try:
            vcjobs = self._kubectl_get_items(["get", "vcjob", "-n", job_namespace])
            pods = self._kubectl_get_items(["get", "pods", "-n", job_namespace])
        except Exception as exc:  # noqa: BLE001 - surface as a soft error, not a 500
            return {"jobs": [], "error": str(exc)}

        pods_by_job: dict[str, list[dict]] = {}
        for p in pods:
            jn = ((p.get("metadata") or {}).get("labels") or {}).get("volcano.sh/job-name")
            if jn:
                pods_by_job.setdefault(jn, []).append(p)

        def _created(it: dict) -> str:
            return (it.get("metadata") or {}).get("creationTimestamp") or ""

        vcjobs.sort(key=_created, reverse=True)
        jobs_out: list[dict[str, Any]] = []
        for it in vcjobs[:limit]:
            meta = it.get("metadata") or {}
            spec = it.get("spec") or {}
            st = it.get("status") or {}
            state = st.get("state") or {}
            name = meta.get("name")
            created_at = meta.get("creationTimestamp")
            jpods = pods_by_job.get(name, [])
            pod_created = _min_iso([(p.get("metadata") or {}).get("creationTimestamp") for p in jpods])
            scheduled = _max_iso([_pod_scheduled_iso(p) for p in jpods])
            started = _min_iso([(p.get("status") or {}).get("startTime") for p in jpods])
            finished = _max_iso([_pod_finished_iso(p) for p in jpods])
            cpu, mem, req_pods = _job_resource_requests(spec)
            labels = meta.get("labels") or {}
            jobs_out.append({
                "name": name,
                "queue": spec.get("queue"),
                "phase": state.get("phase"),
                "tool": labels.get("dms.openai.com/data-tool"),
                "src_storage": _pod_env(jpods, "DMS_SYNC_SOURCE_STORAGE"),
                "dst_storage": _pod_env(jpods, "DMS_SYNC_DESTINATION_STORAGE"),
                "src_path": _pod_env(jpods, "DMS_SYNC_SOURCE_PATH"),
                "dst_path": _pod_env(jpods, "DMS_SYNC_DESTINATION_PATH"),
                "created_at": created_at,
                "pod_created_at": pod_created,
                "scheduled_at": scheduled,
                "started_at": started,
                "finished_at": finished,
                "running": st.get("running") or 0,
                "pending": st.get("pending") or 0,
                "succeeded": st.get("succeeded") or 0,
                "failed": st.get("failed") or 0,
                "min_available": spec.get("minAvailable"),
                "req_cpu_cores": cpu,
                "req_mem_bytes": mem,
                "req_pods": req_pods,
                "latencies": {
                    "job_to_pod_s": _secs_between(created_at, pod_created),
                    "pod_to_sched_s": _secs_between(pod_created, scheduled),
                    # scheduled -> started captures image pull + container creation, the
                    # gap the previous 3-stage breakdown silently dropped.
                    "sched_to_start_s": _secs_between(scheduled, started),
                    "run_s": _secs_between(started, finished),
                },
            })
        return {"jobs": jobs_out, "error": None}

    def _wait_for_terminal(
        self, job_ref: str, *, timeout_seconds: int | None = None
    ) -> dict[str, Any]:
        deadline = time.monotonic() + (
            timeout_seconds or self.settings.dm_scan_timeout_seconds
        )
        last: dict[str, Any] = {"job_ref": job_ref, "phase": "Unknown"}
        last_with_workers: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            last = self.get_job(job_ref)
            if _observed_has_worker_pods(last):
                last_with_workers = last
            if last.get("phase") in {"Completed", "Succeeded"}:
                if last_with_workers is not None and not _observed_has_worker_pods(
                    last
                ):
                    last = {**last, "pod_summary": last_with_workers.get("pod_summary")}
                last["phase"] = "Succeeded"
                return last
            if last.get("phase") in {"Failed", "Terminated", "Aborted"}:
                if last_with_workers is not None and not _observed_has_worker_pods(
                    last
                ):
                    last = {**last, "pod_summary": last_with_workers.get("pod_summary")}
                return last
            time.sleep(max(self.settings.dm_monitor_poll_seconds, 1))
        self.terminate_job(job_ref)
        if last_with_workers is not None and not _observed_has_worker_pods(last):
            last = {**last, "pod_summary": last_with_workers.get("pod_summary")}
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
            _preflight_script(
                setup=["target=/dms/target/${DMS_SCAN_PATH}"],
                checks=[
                    '[ -e "$target" ] || fail target_not_found',
                    '[ -d "$target" ] || fail target_not_directory',
                    '[ -r "$target" ] || fail target_not_readable',
                    '[ -x "$target" ] || fail target_not_traversable',
                ],
                ok_print="printf 'scan preflight ok: %s\\n' \"$target\"",
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
            command_text = _preflight_script(
                setup=[
                    "source=/dms/source/${DMS_SYNC_SOURCE_PATH}",
                    "destination=/dms/destination/${DMS_SYNC_DESTINATION_PATH}",
                    'destination_parent=$(dirname "$destination")',
                ],
                checks=[
                    '[ -e "$source" ] || fail source_not_found',
                    '[ -r "$source" ] || fail source_not_readable',
                    'if [ -d "$source" ]; then [ -x "$source" ] || fail source_not_traversable; fi',
                    '[ -d "$destination_parent" ] || fail destination_parent_missing',
                    '[ -w "$destination_parent" ] || fail destination_parent_not_writable',
                    '[ -x "$destination_parent" ] || fail destination_parent_not_traversable',
                    'if [ -e "$destination" ]; then [ -w "$destination" ] || fail destination_not_writable; fi',
                ],
                ok_print='printf \'sync %s preflight ok: %s -> %s\\n\' "$DMS_DM_PHASE" "$source" "$destination"',
            )
            container_name = "sync-preflight"
        elif data_job["operation"] == "data.rm":
            command_text = _preflight_script(
                setup=[
                    "target=/dms/target/${DMS_RM_TARGET_PATH}",
                    'parent=$(dirname "$target")',
                ],
                checks=[
                    '[ -e "$target" ] || fail target_not_found',
                    '[ -d "$target" ] || fail target_not_directory',
                    '[ -r "$target" ] || fail target_not_readable',
                    '[ -x "$target" ] || fail target_not_traversable',
                    '[ -w "$parent" ] || fail parent_not_writable',
                    '[ -x "$parent" ] || fail parent_not_traversable',
                ],
                ok_print='printf \'rm %s preflight ok: %s\\n\' "$DMS_DM_PHASE" "$target"',
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

    def _sync_role_preflight_manifest(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        preflight: dict[str, Any],
        phase: str,
        role: str,
    ) -> dict[str, Any]:
        """Single-role POSIX preflight pod for nsync.

        ``role`` is ``source`` or ``destination``. The pod is pinned to a representative
        node of that role (all nodes mounting the same storage share the same POSIX view,
        so one node per role is sufficient) and mounts only that role's storage. This is
        what makes nsync -- whose source and destination are on disjoint node sets -- pass
        preflight, where a single both-mounts pod (used by dsync) cannot.
        """
        normalized = (
            data_job.get("normalized_target") or plan.get("desired_state") or {}
        )
        source = (
            normalized.get("source") or plan.get("desired_state", {}).get("source") or {}
        )
        destination = (
            normalized.get("destination")
            or plan.get("desired_state", {}).get("destination")
            or {}
        )
        worker_pool = data_job.get("worker_pool") or {}
        identity = preflight.get("identity_mapping") or {}

        if role == "source":
            candidates = _unique_selected_candidates(
                worker_pool.get("source_candidates") or []
            )
            mount = candidates[0].get("mount_path") if candidates else None
            node = candidates[0].get("node_name") if candidates else None
            volumes = (
                [{"name": "sync-source", "hostPath": {"path": mount, "type": "Directory"}}]
                if mount
                else []
            )
            volume_mounts = (
                [_host_volume_mount("sync-source", "/dms/source", read_only=True)]
                if mount
                else []
            )
            command_text = _preflight_script(
                setup=["source=/dms/source/${DMS_SYNC_SOURCE_PATH}"],
                checks=[
                    '[ -e "$source" ] || fail source_not_found',
                    '[ -r "$source" ] || fail source_not_readable',
                    'if [ -d "$source" ]; then [ -x "$source" ] || fail source_not_traversable; fi',
                ],
                ok_print="printf 'sync %s source preflight ok: %s\\n' \"$DMS_DM_PHASE\" \"$source\"",
            )
        elif role == "destination":
            candidates = _unique_selected_candidates(
                worker_pool.get("destination_candidates") or []
            )
            mount = candidates[0].get("mount_path") if candidates else None
            node = candidates[0].get("node_name") if candidates else None
            volumes = (
                [
                    {
                        "name": "sync-destination",
                        "hostPath": {"path": mount, "type": "Directory"},
                    }
                ]
                if mount
                else []
            )
            volume_mounts = (
                [_host_volume_mount("sync-destination", "/dms/destination")]
                if mount
                else []
            )
            command_text = _preflight_script(
                setup=[
                    "destination=/dms/destination/${DMS_SYNC_DESTINATION_PATH}",
                    'destination_parent=$(dirname "$destination")',
                ],
                checks=[
                    '[ -d "$destination_parent" ] || fail destination_parent_missing',
                    '[ -w "$destination_parent" ] || fail destination_parent_not_writable',
                    '[ -x "$destination_parent" ] || fail destination_parent_not_traversable',
                    'if [ -e "$destination" ]; then [ -w "$destination" ] || fail destination_not_writable; fi',
                ],
                ok_print="printf 'sync %s destination preflight ok: %s\\n' \"$DMS_DM_PHASE\" \"$destination\"",
            )
        else:
            raise DataManagementRuntimeError(
                f"unsupported sync preflight role: {role}"
            )

        env = [
            {
                "name": "DMS_POSIX_USERNAME",
                "value": str(identity.get("posix_username") or ""),
            },
            {
                "name": "DMS_SYNC_SOURCE_STORAGE",
                "value": source.get("storage_name", data_job.get("storage_name") or ""),
            },
            {
                "name": "DMS_SYNC_DESTINATION_STORAGE",
                "value": destination.get("storage_name", ""),
            },
            {"name": "DMS_SYNC_SOURCE_PATH", "value": source.get("path") or "."},
            {"name": "DMS_SYNC_DESTINATION_PATH", "value": destination.get("path") or "."},
            {"name": "DMS_DM_PHASE", "value": phase},
        ]
        node_selector = {"kubernetes.io/hostname": node} if node else {}
        spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "nodeSelector": node_selector,
            "securityContext": _pod_security_context(preflight),
            "volumes": volumes,
            "containers": [
                {
                    "name": f"sync-preflight-{role}",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", command_text],
                    "env": env,
                    "volumeMounts": volume_mounts,
                    "securityContext": _container_security_context(preflight),
                }
            ],
        }
        name = _kubernetes_name(
            f"dms-sync-{phase}-preflight-{role}-{data_job['job_id']}"
        )
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
                    "dms.openai.com/preflight-role": role,
                },
            },
            "spec": spec,
        }

    def _candidate_manifests(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> list[dict[str, Any]]:
        backend = (self.settings.dm_scheduler_backend or "auto").strip().lower()
        tool = data_job.get("selected_tool") or _default_tool_for_operation(
            data_job["operation"]
        )
        native = self._manifest(plan, data_job)
        if tool == "nsync":
            return [native]
        mpi = self._mpijob_manifest(plan, data_job)
        if backend == "mpi-operator":
            return [mpi]
        if backend == "volcano-job":
            return [native]
        return [mpi, native]

    def _mpijob_manifest(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> dict[str, Any]:
        phase = (plan.get("execution_metadata") or {}).get("phase", "execution")
        tool = data_job.get("selected_tool") or _default_tool_for_operation(
            data_job["operation"]
        )
        job_name = _data_job_kubernetes_name(
            f"dms-{_operation_suffix(data_job['operation'])}-{phase}-mpi",
            data_job["job_id"],
        )
        context = (
            self._scan_context(plan, data_job)
            if data_job["operation"] == "data.scan"
            else self._mutation_context(plan, data_job)
        )
        resource_model = _resource_model(data_job)
        worker_replicas = int(resource_model.get("worker_pod_count") or 1)
        processes_per_node = int(resource_model.get("processes_per_node") or 1)
        launcher_command = (
            self._scan_mpi_launcher_command()
            if data_job["operation"] == "data.scan"
            else self._mutation_command(
                plan, data_job, phase=phase, tool=tool, mpi=True
            )
        )
        worker_spec = {
            "restartPolicy": "OnFailure",
            "serviceAccountName": self.settings.dm_service_account,
            "nodeSelector": context["node_selector"],
            "securityContext": {},
            "volumes": context["volumes"],
            "containers": [
                {
                    "name": "mpi-worker",
                    "image": self.settings.dm_job_image,
                    "command": [
                        "/bin/sh",
                        "-c",
                        _mpi_worker_command(),
                    ],
                    "env": [*context["env"], {"name": "DMS_DM_PHASE", "value": phase}],
                    "volumeMounts": context["volume_mounts"],
                    "securityContext": _mpi_worker_container_security_context(),
                }
            ],
        }
        if context["affinity"]:
            worker_spec["affinity"] = context["affinity"]
        launcher_node_names = _candidate_node_names(context["selected"])
        launcher_spec = {
            "restartPolicy": "OnFailure",
            "serviceAccountName": self.settings.dm_service_account,
            "securityContext": {},
            "volumes": context["volumes"],
            "containers": [
                {
                    "name": "mpi-launcher",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", launcher_command],
                    "env": [
                        *context["env"],
                        {"name": "DMS_DM_PHASE", "value": phase},
                        {"name": "DMS_MPI_HOSTFILE", "value": "/etc/mpi/hostfile"},
                    ],
                    "volumeMounts": context["volume_mounts"],
                    "securityContext": {},
                }
            ],
        }
        if context["node_selector"]:
            launcher_spec["nodeSelector"] = context["node_selector"]
        elif launcher_node_names:
            launcher_spec["affinity"] = _node_name_affinity(launcher_node_names)
        return {
            "apiVersion": "kubeflow.org/v2beta1",
            "kind": "MPIJob",
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
                "slotsPerWorker": processes_per_node,
                "runPolicy": {
                    "schedulingPolicy": {
                        "queue": resource_model.get("queue"),
                        "priorityClass": resource_model.get("priority_class"),
                        "minAvailable": worker_replicas + 1,
                    }
                },
                "mpiReplicaSpecs": {
                    "Launcher": {
                        "replicas": 1,
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-phase": phase,
                                    "dms.openai.com/data-tool": tool,
                                    "dms.openai.com/data-role": "launcher",
                                }
                            },
                            "spec": launcher_spec,
                        },
                    },
                    "Worker": {
                        "replicas": worker_replicas,
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-phase": phase,
                                    "dms.openai.com/data-tool": tool,
                                    "dms.openai.com/data-role": "worker",
                                }
                            },
                            "spec": worker_spec,
                        },
                    },
                },
            },
        }

    def _manifest(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> dict[str, Any]:
        if data_job["operation"] != "data.scan":
            return self._mutation_manifest(plan, data_job)
        job_name = _data_job_kubernetes_name("dms-scan", data_job["job_id"])
        scan = self._scan_context(plan, data_job)
        resource_model = _resource_model(data_job)
        worker_replicas = int(resource_model.get("worker_pod_count") or 1)
        worker_pod_spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "nodeSelector": scan["node_selector"],
            "securityContext": {},
            "volumes": scan["volumes"],
            "containers": [
                {
                    "name": "dscan-worker",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", _mpi_worker_command()],
                    "env": scan["env"],
                    "volumeMounts": scan["volume_mounts"],
                    "securityContext": _mpi_worker_container_security_context(),
                }
            ],
        }
        if scan["affinity"]:
            worker_pod_spec["affinity"] = scan["affinity"]
        launcher_pod_spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "securityContext": {},
            "volumes": scan["volumes"],
            "containers": [
                {
                    "name": "dscan-launcher",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", self._scan_mpi_launcher_command()],
                    "env": [
                        *scan["env"],
                        {
                            "name": "DMS_MPI_HOSTFILE",
                            "value": "/etc/volcano/worker.host",
                        },
                    ],
                    "volumeMounts": scan["volume_mounts"],
                    "securityContext": {},
                }
            ],
        }
        launcher_node_names = _candidate_node_names(scan["selected"])
        if scan["node_selector"]:
            launcher_pod_spec["nodeSelector"] = scan["node_selector"]
        elif launcher_node_names:
            launcher_pod_spec["affinity"] = _node_name_affinity(launcher_node_names)
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
                "minAvailable": worker_replicas + 1,
                "queue": resource_model.get("queue"),
                "priorityClassName": resource_model.get("priority_class"),
                "plugins": {"ssh": [], "svc": []},
                "tasks": [
                    {
                        "name": "launcher",
                        "replicas": 1,
                        "policies": [
                            {
                                "event": "TaskCompleted",
                                "action": "CompleteJob",
                            }
                        ],
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-tool": "dscan",
                                    "dms.openai.com/data-role": "launcher",
                                }
                            },
                            "spec": launcher_pod_spec,
                        },
                    },
                    {
                        "name": "worker",
                        "replicas": worker_replicas,
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-tool": "dscan",
                                    "dms.openai.com/data-role": "worker",
                                }
                            },
                            "spec": worker_pod_spec,
                        },
                    },
                ],
            },
        }

    def _mutation_manifest(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> dict[str, Any]:
        phase = (plan.get("execution_metadata") or {}).get("phase", "execution")
        tool = data_job.get("selected_tool") or _default_tool_for_operation(
            data_job["operation"]
        )
        if tool == "nsync":
            return self._nsync_manifest(plan, data_job)
        context = self._mutation_context(plan, data_job)
        resource_model = _resource_model(data_job)
        worker_replicas = int(resource_model.get("worker_pod_count") or 1)
        job_name = _data_job_kubernetes_name(
            f"dms-{_operation_suffix(data_job['operation'])}-{phase}",
            data_job["job_id"],
        )
        launcher_command = self._mutation_command(
            plan, data_job, phase=phase, tool=tool, mpi=True
        )
        worker_pod_spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "nodeSelector": context["node_selector"],
            "securityContext": {},
            "volumes": context["volumes"],
            "containers": [
                {
                    "name": f"{tool}-worker",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", _mpi_worker_command()],
                    "env": [*context["env"], {"name": "DMS_DM_PHASE", "value": phase}],
                    "volumeMounts": context["volume_mounts"],
                    "securityContext": _mpi_worker_container_security_context(),
                }
            ],
        }
        if context["affinity"]:
            worker_pod_spec["affinity"] = context["affinity"]
        launcher_pod_spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "securityContext": {},
            "volumes": context["volumes"],
            "containers": [
                {
                    "name": f"{tool}-launcher",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", launcher_command],
                    "env": [
                        *context["env"],
                        {"name": "DMS_DM_PHASE", "value": phase},
                        {
                            "name": "DMS_MPI_HOSTFILE",
                            "value": "/etc/volcano/worker.host",
                        },
                    ],
                    "volumeMounts": context["volume_mounts"],
                    "securityContext": _mpi_worker_container_security_context(),
                }
            ],
        }
        launcher_node_names = _candidate_node_names(context["selected"])
        if context["node_selector"]:
            launcher_pod_spec["nodeSelector"] = context["node_selector"]
        elif launcher_node_names:
            launcher_pod_spec["affinity"] = _node_name_affinity(launcher_node_names)
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
                "minAvailable": worker_replicas + 1,
                "queue": resource_model.get("queue"),
                "priorityClassName": resource_model.get("priority_class"),
                "plugins": {"ssh": [], "svc": []},
                "tasks": [
                    {
                        "name": "launcher",
                        "replicas": 1,
                        "policies": [
                            {
                                "event": "TaskCompleted",
                                "action": "CompleteJob",
                            }
                        ],
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-phase": phase,
                                    "dms.openai.com/data-tool": tool,
                                    "dms.openai.com/data-role": "launcher",
                                }
                            },
                            "spec": launcher_pod_spec,
                        },
                    },
                    {
                        "name": "worker",
                        "replicas": worker_replicas,
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-phase": phase,
                                    "dms.openai.com/data-tool": tool,
                                    "dms.openai.com/data-role": "worker",
                                }
                            },
                            "spec": worker_pod_spec,
                        },
                    },
                ],
            },
        }

    def _nsync_manifest(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> dict[str, Any]:
        phase = (plan.get("execution_metadata") or {}).get("phase", "execution")
        resource_model = _resource_model(data_job)
        source_replicas = int(resource_model.get("source_node_count") or 1)
        destination_replicas = int(resource_model.get("destination_node_count") or 1)
        context = self._nsync_context(plan, data_job)
        job_name = _data_job_kubernetes_name(
            f"dms-sync-{phase}-nsync", data_job["job_id"]
        )
        launcher_command = self._nsync_launcher_command(plan, data_job, phase=phase)
        source_candidates = (data_job.get("worker_pool") or {}).get(
            "source_candidates"
        ) or []
        destination_candidates = (data_job.get("worker_pool") or {}).get(
            "destination_candidates"
        ) or []
        base_spec = {
            "restartPolicy": "Never",
            "serviceAccountName": self.settings.dm_service_account,
            "securityContext": {},
        }
        launcher_spec = {
            **base_spec,
            "volumes": context["artifact_volumes"],
            "containers": [
                {
                    "name": "nsync-launcher",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", launcher_command],
                    "env": [
                        *context["env"],
                        {"name": "DMS_DM_PHASE", "value": phase},
                        {
                            "name": "DMS_MPI_SOURCE_HOSTFILE",
                            "value": "/etc/volcano/source_worker.host",
                        },
                        {
                            "name": "DMS_MPI_DESTINATION_HOSTFILE",
                            "value": "/etc/volcano/destination_worker.host",
                        },
                    ],
                    "volumeMounts": context["artifact_volume_mounts"],
                    "securityContext": _mpi_worker_container_security_context(),
                }
            ],
        }
        launcher_node_names = _candidate_node_names(
            source_candidates + destination_candidates
        )
        if launcher_node_names:
            launcher_spec["affinity"] = _node_name_affinity(launcher_node_names)
        source_spec = {
            **base_spec,
            "volumes": context["source_volumes"],
            "containers": [
                {
                    "name": "nsync-source-worker",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", _mpi_worker_command()],
                    "env": [*context["env"], {"name": "DMS_DM_PHASE", "value": phase}],
                    "volumeMounts": context["source_volume_mounts"],
                    "securityContext": _mpi_worker_container_security_context(),
                }
            ],
            "affinity": _merge_affinity(
                _node_name_affinity(_candidate_node_names(source_candidates)),
                _worker_pod_anti_affinity(data_job["job_id"]),
            ),
        }
        destination_spec = {
            **base_spec,
            "volumes": context["destination_volumes"],
            "containers": [
                {
                    "name": "nsync-destination-worker",
                    "image": self.settings.dm_job_image,
                    "command": ["/bin/sh", "-c", _mpi_worker_command()],
                    "env": [*context["env"], {"name": "DMS_DM_PHASE", "value": phase}],
                    "volumeMounts": context["destination_volume_mounts"],
                    "securityContext": _mpi_worker_container_security_context(),
                }
            ],
            "affinity": _merge_affinity(
                _node_name_affinity(_candidate_node_names(destination_candidates)),
                _worker_pod_anti_affinity(data_job["job_id"]),
            ),
        }
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
                    "dms.openai.com/data-tool": "nsync",
                },
            },
            "spec": {
                "schedulerName": "volcano",
                "minAvailable": source_replicas + destination_replicas + 1,
                "queue": resource_model.get("queue"),
                "priorityClassName": resource_model.get("priority_class"),
                "plugins": {"ssh": [], "svc": []},
                "tasks": [
                    {
                        "name": "launcher",
                        "replicas": 1,
                        "policies": [
                            {
                                "event": "TaskCompleted",
                                "action": "CompleteJob",
                            }
                        ],
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-phase": phase,
                                    "dms.openai.com/data-tool": "nsync",
                                    "dms.openai.com/data-role": "launcher",
                                }
                            },
                            "spec": launcher_spec,
                        },
                    },
                    {
                        "name": "source-worker",
                        "replicas": source_replicas,
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-phase": phase,
                                    "dms.openai.com/data-tool": "nsync",
                                    "dms.openai.com/data-role": "worker",
                                    "dms.openai.com/data-role-kind": "source",
                                }
                            },
                            "spec": source_spec,
                        },
                    },
                    {
                        "name": "destination-worker",
                        "replicas": destination_replicas,
                        "template": {
                            "metadata": {
                                "labels": {
                                    "app.kubernetes.io/name": "dms-data-management",
                                    "dms.openai.com/data-job-id": data_job["job_id"],
                                    "dms.openai.com/request-id": data_job["request_id"],
                                    "dms.openai.com/data-phase": phase,
                                    "dms.openai.com/data-tool": "nsync",
                                    "dms.openai.com/data-role": "worker",
                                    "dms.openai.com/data-role-kind": "destination",
                                }
                            },
                            "spec": destination_spec,
                        },
                    },
                ],
            },
        }

    def _nsync_launcher_command(
        self, plan: dict[str, Any], data_job: dict[str, Any], *, phase: str
    ) -> str:
        options = (plan.get("desired_state") or {}).get("options") or {}
        flags = _sync_flags(options)
        dryrun = "--dryrun " if phase == "preview" else ""
        return "\n".join(
            [
                "set -eu",
                "umask 077",
                "artifact=/dms/artifacts/${DMS_DATA_JOB_ID}/${DMS_DM_PHASE}",
                "mpi_dir=/dms/artifacts/${DMS_DATA_JOB_ID}/mpi",
                'mkdir -p "$artifact" "$mpi_dir"',
                _chown_artifact_line("$artifact"),
                "source=/dms/source/${DMS_SYNC_SOURCE_PATH}",
                "destination=/dms/destination/${DMS_SYNC_DESTINATION_PATH}",
                'rank_script="$mpi_dir/run-${DMS_DM_PHASE}-nsync-rank.sh"',
                'rank_hostfile="$mpi_dir/nsync-hostfile"',
                'role_map_file="$mpi_dir/nsync-role-map.txt"',
                'source_raw_hostfile="$mpi_dir/nsync-source-hosts"',
                'destination_raw_hostfile="$mpi_dir/nsync-destination-hosts"',
                ': > "$rank_hostfile"',
                ': > "$role_map_file"',
                ': > "$source_raw_hostfile"',
                ': > "$destination_raw_hostfile"',
                "rank=0",
                'if [ ! -f "$DMS_MPI_SOURCE_HOSTFILE" ]; then echo "missing source hostfile: $DMS_MPI_SOURCE_HOSTFILE" >&2; exit 1; fi',
                'if [ ! -f "$DMS_MPI_DESTINATION_HOSTFILE" ]; then echo "missing destination hostfile: $DMS_MPI_DESTINATION_HOSTFILE" >&2; exit 1; fi',
                'cat "$DMS_MPI_SOURCE_HOSTFILE" > "$source_raw_hostfile"',
                'cat "$DMS_MPI_DESTINATION_HOSTFILE" > "$destination_raw_hostfile"',
                "attempts=0",
                'while [ "$attempts" -lt 60 ]; do',
                "  source_host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$source_raw_hostfile\")",
                '  [ "$source_host_count" -ge "$DMS_NSYNC_SOURCE_NODE_COUNT" ] && break',
                '  cat "$DMS_MPI_SOURCE_HOSTFILE" > "$source_raw_hostfile"',
                "  source_host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$source_raw_hostfile\")",
                '  [ "$source_host_count" -ge "$DMS_NSYNC_SOURCE_NODE_COUNT" ] && break',
                "  if [ -n \"${VC_SOURCE_WORKER_HOSTS:-}\" ]; then printf '%s\\n' \"$VC_SOURCE_WORKER_HOSTS\" | tr ',' '\\n' > \"$source_raw_hostfile\"; fi",
                "  source_host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$source_raw_hostfile\")",
                '  [ "$source_host_count" -ge "$DMS_NSYNC_SOURCE_NODE_COUNT" ] && break',
                "  attempts=$((attempts + 1))",
                "  sleep 1",
                "done",
                "attempts=0",
                'while [ "$attempts" -lt 60 ]; do',
                "  destination_host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$destination_raw_hostfile\")",
                '  [ "$destination_host_count" -ge "$DMS_NSYNC_DESTINATION_NODE_COUNT" ] && break',
                '  cat "$DMS_MPI_DESTINATION_HOSTFILE" > "$destination_raw_hostfile"',
                "  destination_host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$destination_raw_hostfile\")",
                '  [ "$destination_host_count" -ge "$DMS_NSYNC_DESTINATION_NODE_COUNT" ] && break',
                "  if [ -n \"${VC_DESTINATION_WORKER_HOSTS:-}\" ]; then printf '%s\\n' \"$VC_DESTINATION_WORKER_HOSTS\" | tr ',' '\\n' > \"$destination_raw_hostfile\"; fi",
                "  destination_host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$destination_raw_hostfile\")",
                '  [ "$destination_host_count" -ge "$DMS_NSYNC_DESTINATION_NODE_COUNT" ] && break',
                "  attempts=$((attempts + 1))",
                "  sleep 1",
                "done",
                'while IFS= read -r host_line || [ -n "$host_line" ]; do',
                "  host=$(printf '%s\\n' \"$host_line\" | awk '{print $1}')",
                '  [ -n "$host" ] || continue',
                '  resolved=""',
                "  attempts=0",
                '  while [ "$attempts" -lt 30 ]; do',
                "    resolved=$(getent hosts \"$host\" 2>/dev/null | awk 'NR == 1 {print $1}')",
                '    [ -n "$resolved" ] && break',
                "    attempts=$((attempts + 1))",
                "    sleep 1",
                "  done",
                '  if [ -n "$resolved" ]; then host="$resolved"; fi',
                '  printf \'%s slots=%s\\n\' "$host" "$DMS_MPI_PROCESSES_PER_NODE" >> "$rank_hostfile"',
                "  i=0",
                '  while [ "$i" -lt "$DMS_MPI_PROCESSES_PER_NODE" ]; do',
                '    printf \'%s:src\\n\' "$rank" >> "$role_map_file"',
                "    rank=$((rank + 1))",
                "    i=$((i + 1))",
                "  done",
                'done < "$source_raw_hostfile"',
                'while IFS= read -r host_line || [ -n "$host_line" ]; do',
                "  host=$(printf '%s\\n' \"$host_line\" | awk '{print $1}')",
                '  [ -n "$host" ] || continue',
                '  resolved=""',
                "  attempts=0",
                '  while [ "$attempts" -lt 30 ]; do',
                "    resolved=$(getent hosts \"$host\" 2>/dev/null | awk 'NR == 1 {print $1}')",
                '    [ -n "$resolved" ] && break',
                "    attempts=$((attempts + 1))",
                "    sleep 1",
                "  done",
                '  if [ -n "$resolved" ]; then host="$resolved"; fi',
                '  printf \'%s slots=%s\\n\' "$host" "$DMS_MPI_PROCESSES_PER_NODE" >> "$rank_hostfile"',
                "  i=0",
                '  while [ "$i" -lt "$DMS_MPI_PROCESSES_PER_NODE" ]; do',
                '    printf \'%s:dst\\n\' "$rank" >> "$role_map_file"',
                "    rank=$((rank + 1))",
                "    i=$((i + 1))",
                "  done",
                'done < "$destination_raw_hostfile"',
                'role_map=$(paste -sd, "$role_map_file")',
                'test -n "$role_map"',
                "cat > \"$rank_script\" <<'DMS_MPI_RANK'",
                "#!/bin/sh",
                "set -eu",
                'if [ "$(id -u)" = 0 ] && command -v runuser >/dev/null 2>&1 && [ -n "${DMS_POSIX_USERNAME:-}" ]; then',
                f'  exec runuser -u "$DMS_POSIX_USERNAME" --preserve-environment -- nsync --role-mode map --role-map "$DMS_NSYNC_ROLE_MAP" {dryrun}{flags}"$DMS_MPI_SYNC_SOURCE" "$DMS_MPI_SYNC_DESTINATION"',
                "fi",
                f'exec nsync --role-mode map --role-map "$DMS_NSYNC_ROLE_MAP" {dryrun}{flags}"$DMS_MPI_SYNC_SOURCE" "$DMS_MPI_SYNC_DESTINATION"',
                "DMS_MPI_RANK",
                'chmod 0700 "$rank_script"',
                'export DMS_NSYNC_ROLE_MAP="$role_map"',
                'export DMS_MPI_SYNC_SOURCE="$source"',
                'export DMS_MPI_SYNC_DESTINATION="$destination"',
                'mpi_hostfile="$rank_hostfile"',
                'hostfile_arg="--hostfile $mpi_hostfile"',
                _mpiexec_line(
                    stdout='"$artifact/stdout.log"',
                    stderr='"$artifact/stderr.log"',
                ),
                f'printf \'{{"tool":"nsync","phase":"%s","dry_run":%s,"role_map":"%s"}}\\n\' "$DMS_DM_PHASE" "$( [ "$DMS_DM_PHASE" = preview ] && echo true || echo false )" "$role_map" > "$artifact/command.json"',
                'printf \'{"summary":{"operation":"data.sync","selected_tool":"nsync","phase":"%s","dry_run":%s,"source_node_count":%s,"destination_node_count":%s,"worker_pod_count":%s,"processes_per_node":%s,"process_count":%s,"error_count":0}}\\n\' "$DMS_DM_PHASE" "$( [ "$DMS_DM_PHASE" = preview ] && echo true || echo false )" "$DMS_NSYNC_SOURCE_NODE_COUNT" "$DMS_NSYNC_DESTINATION_NODE_COUNT" "$DMS_WORKER_POD_COUNT" "$DMS_MPI_PROCESSES_PER_NODE" "$DMS_MPI_PROCESS_COUNT" > "$artifact/summary.json"',
                'printf \'{"process_count":%s,"processes_per_node":%s,"interface_mode":"auto","role_map":"%s"}\\n\' "$DMS_MPI_PROCESS_COUNT" "$DMS_MPI_PROCESSES_PER_NODE" "$role_map" > "$mpi_dir/mpirun.json"',
                'touch "$artifact/.done"',
            ]
        )

    def _scan_mpi_launcher_command(self) -> str:
        return "\n".join(
            [
                "set -eu",
                "umask 077",
                "mkdir -p /dms/artifacts/${DMS_DATA_JOB_ID}/mpi",
                _chown_artifact_line("/dms/artifacts/${DMS_DATA_JOB_ID}"),
                "target=/dms/target/${DMS_SCAN_PATH}",
                'test -d "$target"',
                'test -r "$target"',
                'test -x "$target"',
                "report=/dms/artifacts/${DMS_DATA_JOB_ID}/dscan-report.json",
                "summary=/dms/artifacts/${DMS_DATA_JOB_ID}/summary.json",
                "find_errors=/dms/artifacts/${DMS_DATA_JOB_ID}/find-errors.log",
                "rank_script=/dms/artifacts/${DMS_DATA_JOB_ID}/mpi/run-dscan-rank.sh",
                "cat > \"$rank_script\" <<'DMS_MPI_RANK'",
                "#!/bin/sh",
                "set -eu",
                'if [ "$(id -u)" = 0 ] && command -v runuser >/dev/null 2>&1 && [ -n "${DMS_POSIX_USERNAME:-}" ]; then',
                '  exec runuser -u "$DMS_POSIX_USERNAME" --preserve-environment -- dscan --directory "$DMS_MPI_SCAN_TARGET" --output "$DMS_MPI_SCAN_REPORT" --print',
                "fi",
                'exec dscan --directory "$DMS_MPI_SCAN_TARGET" --output "$DMS_MPI_SCAN_REPORT" --print',
                "DMS_MPI_RANK",
                'chmod 0700 "$rank_script"',
                'export DMS_MPI_SCAN_TARGET="$target"',
                'export DMS_MPI_SCAN_REPORT="$report"',
                *_mpi_hostfile_lines("/dms/artifacts/${DMS_DATA_JOB_ID}/mpi/hostfile"),
                _mpiexec_line(
                    stdout="/dms/artifacts/${DMS_DATA_JOB_ID}/stdout.log",
                    stderr="/dms/artifacts/${DMS_DATA_JOB_ID}/stderr.log",
                ),
                'test -f "$report"',
                ': > "$find_errors"',
                'file_count=$(find "$target" -type f -print 2>> "$find_errors" | wc -l | awk \'{print $1}\')',
                'directory_count=$(find "$target" -type d -print 2>> "$find_errors" | wc -l | awk \'{print $1}\')',
                "total_bytes=$(find \"$target\" -type f -printf '%s\\n' 2>> \"$find_errors\" | awk '{sum += $1} END {print sum + 0}')",
                "error_count=$(wc -l < \"$find_errors\" | awk '{print $1}')",
                'printf \'{"summary":{"file_count":%s,"directory_count":%s,"total_bytes":%s,"error_count":%s,"selected_node":"%s","worker_pod_count":%s,"processes_per_node":%s,"process_count":%s}}\\n\' "$file_count" "$directory_count" "$total_bytes" "$error_count" "$DMS_SELECTED_NODE" "$DMS_WORKER_POD_COUNT" "$DMS_MPI_PROCESSES_PER_NODE" "$DMS_MPI_PROCESS_COUNT" > "$summary"',
                'printf \'{"process_count":%s,"processes_per_node":%s,"interface_mode":"auto"}\\n\' "$DMS_MPI_PROCESS_COUNT" "$DMS_MPI_PROCESSES_PER_NODE" > /dms/artifacts/${DMS_DATA_JOB_ID}/mpi/mpirun.json',
                "touch /dms/artifacts/${DMS_DATA_JOB_ID}/.done",
            ]
        )

    def _mutation_command(
        self,
        plan: dict[str, Any],
        data_job: dict[str, Any],
        *,
        phase: str,
        tool: str,
        mpi: bool = False,
    ) -> str:
        options = (plan.get("desired_state") or {}).get("options") or {}
        dryrun = "--dryrun " if phase == "preview" else ""
        if data_job["operation"] == "data.sync":
            flags = _sync_flags(options)
            run_command = (
                "\n".join(
                    [
                        "mpi_dir=/dms/artifacts/${DMS_DATA_JOB_ID}/mpi",
                        'mkdir -p "$mpi_dir"',
                        _chown_artifact_line("$artifact"),
                        'rank_script="$mpi_dir/run-${DMS_DM_PHASE}-sync-rank.sh"',
                        "cat > \"$rank_script\" <<'DMS_MPI_RANK'",
                        "#!/bin/sh",
                        "set -eu",
                        'if [ "$(id -u)" = 0 ] && command -v runuser >/dev/null 2>&1 && [ -n "${DMS_POSIX_USERNAME:-}" ]; then',
                        f'  exec runuser -u "$DMS_POSIX_USERNAME" --preserve-environment -- {tool} {dryrun}{flags}"$DMS_MPI_SYNC_SOURCE" "$DMS_MPI_SYNC_DESTINATION"',
                        "fi",
                        f'exec {tool} {dryrun}{flags}"$DMS_MPI_SYNC_SOURCE" "$DMS_MPI_SYNC_DESTINATION"',
                        "DMS_MPI_RANK",
                        'chmod 0700 "$rank_script"',
                        'export DMS_MPI_SYNC_SOURCE="$source"',
                        'export DMS_MPI_SYNC_DESTINATION="$destination"',
                        *_mpi_hostfile_lines("$mpi_dir/hostfile"),
                        _mpiexec_line(
                            stdout='"$artifact/stdout.log"',
                            stderr='"$artifact/stderr.log"',
                        ),
                    ]
                )
                if mpi
                else f'{tool} {dryrun}{flags}"$source" "$destination" > "$artifact/stdout.log" 2> "$artifact/stderr.log"'
            )
            return "\n".join(
                [
                    "set -eu",
                    "umask 077",
                    "artifact=/dms/artifacts/${DMS_DATA_JOB_ID}/${DMS_DM_PHASE}",
                    'mkdir -p "$artifact"',
                    "source=/dms/source/${DMS_SYNC_SOURCE_PATH}",
                    "destination=/dms/destination/${DMS_SYNC_DESTINATION_PATH}",
                    'find_errors="$artifact/find-errors.log"',
                    ': > "$find_errors"',
                    f'printf \'{{"tool":"{tool}","phase":"%s","dry_run":%s}}\\n\' "$DMS_DM_PHASE" "$( [ "$DMS_DM_PHASE" = preview ] && echo true || echo false )" > "$artifact/command.json"',
                    run_command,
                    'summary_root="$destination"',
                    'if [ "$DMS_DM_PHASE" = preview ]; then summary_root="$source"; fi',
                    'file_count=$(find "$summary_root" -type f -print 2>> "$find_errors" | wc -l | awk \'{print $1}\')',
                    'directory_count=$(find "$summary_root" -type d -print 2>> "$find_errors" | wc -l | awk \'{print $1}\')',
                    "total_bytes=$(find \"$summary_root\" -type f -printf '%s\\n' 2>> \"$find_errors\" | awk '{sum += $1} END {print sum + 0}')",
                    "error_count=$(wc -l < \"$find_errors\" | awk '{print $1}')",
                    'printf \'{"summary":{"operation":"data.sync","selected_tool":"%s","phase":"%s","dry_run":%s,"file_count":%s,"directory_count":%s,"total_bytes":%s,"error_count":%s,"selected_node":"%s","worker_pod_count":%s,"processes_per_node":%s,"process_count":%s}}\\n\' "$DMS_SELECTED_TOOL" "$DMS_DM_PHASE" "$( [ "$DMS_DM_PHASE" = preview ] && echo true || echo false )" "$file_count" "$directory_count" "$total_bytes" "$error_count" "$DMS_SELECTED_NODE" "$DMS_WORKER_POD_COUNT" "$DMS_MPI_PROCESSES_PER_NODE" "$DMS_MPI_PROCESS_COUNT" > "$artifact/summary.json"',
                    'touch "$artifact/.done"',
                ]
            )
        if data_job["operation"] == "data.rm":
            flags = _rm_flags(options, phase=phase)
            run_command = (
                "\n".join(
                    [
                        "mpi_dir=/dms/artifacts/${DMS_DATA_JOB_ID}/mpi",
                        'mkdir -p "$mpi_dir"',
                        _chown_artifact_line("$artifact"),
                        'rank_script="$mpi_dir/run-${DMS_DM_PHASE}-rm-rank.sh"',
                        "cat > \"$rank_script\" <<'DMS_MPI_RANK'",
                        "#!/bin/sh",
                        "set -eu",
                        'if [ "$(id -u)" = 0 ] && command -v runuser >/dev/null 2>&1 && [ -n "${DMS_POSIX_USERNAME:-}" ]; then',
                        f'  exec runuser -u "$DMS_POSIX_USERNAME" --preserve-environment -- {tool} {dryrun}{flags}"$DMS_MPI_RM_TARGET"',
                        "fi",
                        f'exec {tool} {dryrun}{flags}"$DMS_MPI_RM_TARGET"',
                        "DMS_MPI_RANK",
                        'chmod 0700 "$rank_script"',
                        'export DMS_MPI_RM_TARGET="$target"',
                        *_mpi_hostfile_lines("$mpi_dir/hostfile"),
                        _mpiexec_line(
                            stdout='"$artifact/stdout.log"',
                            stderr='"$artifact/stderr.log"',
                        ),
                    ]
                )
                if mpi
                else f'{tool} {dryrun}{flags}"$target" > "$artifact/stdout.log" 2> "$artifact/stderr.log"'
            )
            return "\n".join(
                [
                    "set -eu",
                    "umask 077",
                    "artifact=/dms/artifacts/${DMS_DATA_JOB_ID}/${DMS_DM_PHASE}",
                    'mkdir -p "$artifact"',
                    "target=/dms/target/${DMS_RM_TARGET_PATH}",
                    'find_errors="$artifact/find-errors.log"',
                    ': > "$find_errors"',
                    'file_count=$(find "$target" -type f -print 2>> "$find_errors" | wc -l | awk \'{print $1}\')',
                    'directory_count=$(find "$target" -type d -print 2>> "$find_errors" | wc -l | awk \'{print $1}\')',
                    "total_bytes=$(find \"$target\" -type f -printf '%s\\n' 2>> \"$find_errors\" | awk '{sum += $1} END {print sum + 0}')",
                    f'printf \'{{"tool":"{tool}","phase":"%s","dry_run":%s}}\\n\' "$DMS_DM_PHASE" "$( [ "$DMS_DM_PHASE" = preview ] && echo true || echo false )" > "$artifact/command.json"',
                    run_command,
                    "target_absent=false",
                    'if [ ! -e "$target" ]; then target_absent=true; fi',
                    "error_count=$(wc -l < \"$find_errors\" | awk '{print $1}')",
                    'printf \'{"summary":{"operation":"data.rm","selected_tool":"%s","phase":"%s","dry_run":%s,"file_count":%s,"directory_count":%s,"total_bytes":%s,"error_count":%s,"target_absent":%s,"selected_node":"%s","worker_pod_count":%s,"processes_per_node":%s,"process_count":%s}}\\n\' "$DMS_SELECTED_TOOL" "$DMS_DM_PHASE" "$( [ "$DMS_DM_PHASE" = preview ] && echo true || echo false )" "$file_count" "$directory_count" "$total_bytes" "$error_count" "$target_absent" "$DMS_SELECTED_NODE" "$DMS_WORKER_POD_COUNT" "$DMS_MPI_PROCESSES_PER_NODE" "$DMS_MPI_PROCESS_COUNT" > "$artifact/summary.json"',
                    'touch "$artifact/.done"',
                ]
            )
        raise DataManagementRuntimeError(
            f"unsupported mutation operation: {data_job['operation']}"
        )

    def _mutation_context(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> dict[str, Any]:
        artifact_uri = _artifact_job_uri(
            self.settings.dm_artifact_base_uri, data_job["job_id"]
        )
        normalized = (
            data_job.get("normalized_target") or plan.get("desired_state") or {}
        )
        selected = _unique_selected_candidates(
            (data_job.get("worker_pool") or {}).get("selected_candidates") or []
        )
        node_selector: dict[str, str] = {}
        affinity: dict[str, Any] = {}
        node_names = [item["node_name"] for item in selected if item.get("node_name")]
        if len(node_names) == 1:
            node_selector["kubernetes.io/hostname"] = node_names[0]
        elif node_names:
            affinity = _merge_affinity(
                _node_name_affinity(node_names),
                _worker_pod_anti_affinity(data_job["job_id"]),
            )

        artifact_path = _file_uri_parent_path(artifact_uri)
        volumes: list[dict[str, Any]] = []
        volume_mounts: list[dict[str, Any]] = []
        identity = data_job.get("preflight_result", {}).get("identity_mapping") or {}
        env: list[dict[str, str]] = [
            {"name": "DMS_DATA_JOB_ID", "value": data_job["job_id"]},
            {"name": "DMS_ARTIFACT_URI", "value": artifact_uri},
            {
                "name": "DMS_POSIX_USERNAME",
                "value": str(identity.get("posix_username") or ""),
            },
            {
                "name": "DMS_SELECTED_TOOL",
                "value": data_job.get("selected_tool")
                or _default_tool_for_operation(data_job["operation"]),
            },
            {
                "name": "DMS_SELECTED_NODE",
                "value": node_names[0] if len(node_names) == 1 else "",
            },
            {
                "name": "DMS_MPI_PROCESSES_PER_NODE",
                "value": str(_resource_model(data_job).get("processes_per_node") or 1),
            },
            {
                "name": "DMS_MPI_PROCESS_COUNT",
                "value": str(_resource_model(data_job).get("process_count") or 1),
            },
            {
                "name": "DMS_WORKER_POD_COUNT",
                "value": str(_resource_model(data_job).get("worker_pod_count") or 1),
            },
        ]

        if data_job["operation"] == "data.sync":
            source = (
                normalized.get("source")
                or plan.get("desired_state", {}).get("source")
                or {}
            )
            destination = (
                normalized.get("destination")
                or plan.get("desired_state", {}).get("destination")
                or {}
            )
            candidate = selected[0] if selected else {}
            source_mount = candidate.get("source_mount_path") or candidate.get(
                "mount_path"
            )
            destination_mount = candidate.get(
                "destination_mount_path"
            ) or candidate.get("mount_path")
            if source_mount:
                volumes.append(
                    {
                        "name": "sync-source",
                        "hostPath": {"path": source_mount, "type": "Directory"},
                    }
                )
                volume_mounts.append(
                    _host_volume_mount("sync-source", "/dms/source", read_only=True)
                )
            if destination_mount:
                volumes.append(
                    {
                        "name": "sync-destination",
                        "hostPath": {"path": destination_mount, "type": "Directory"},
                    }
                )
                volume_mounts.append(
                    _host_volume_mount("sync-destination", "/dms/destination")
                )
            env.extend(
                [
                    {
                        "name": "DMS_SYNC_SOURCE_STORAGE",
                        "value": source.get(
                            "storage_name", data_job.get("storage_name") or ""
                        ),
                    },
                    {
                        "name": "DMS_SYNC_DESTINATION_STORAGE",
                        "value": destination.get("storage_name", ""),
                    },
                    {
                        "name": "DMS_SYNC_SOURCE_PATH",
                        "value": source.get("path") or ".",
                    },
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
                volume_mounts.append(_host_volume_mount("rm-target", "/dms/target"))
            env.extend(
                [
                    {
                        "name": "DMS_RM_STORAGE",
                        "value": target.get(
                            "storage_name", data_job.get("storage_name") or ""
                        ),
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
                _host_volume_mount("mutation-artifacts", "/dms/artifacts")
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

    def _scan_context(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> dict[str, Any]:
        artifact_uri = _artifact_job_uri(
            self.settings.dm_artifact_base_uri, data_job["job_id"]
        )
        target = (
            data_job.get("normalized_target")
            or plan.get("desired_state", {}).get("target")
            or {}
        )
        selected = _unique_selected_candidates(
            (data_job.get("worker_pool") or {}).get("selected_candidates") or []
        )
        node_selector = {}
        affinity = {}
        node_names = [item["node_name"] for item in selected if item.get("node_name")]
        if len(node_names) == 1:
            node_selector["kubernetes.io/hostname"] = node_names[0]
        elif node_names:
            affinity = _merge_affinity(
                _node_name_affinity(node_names),
                _worker_pod_anti_affinity(data_job["job_id"]),
            )
        target_mount = selected[0].get("mount_path") if selected else None
        artifact_path = _file_uri_parent_path(artifact_uri)
        volumes: list[dict[str, Any]] = []
        volume_mounts: list[dict[str, Any]] = []
        identity = data_job.get("preflight_result", {}).get("identity_mapping") or {}
        if target_mount:
            volumes.append(
                {
                    "name": "scan-target",
                    "hostPath": {"path": target_mount, "type": "Directory"},
                }
            )
            volume_mounts.append(
                _host_volume_mount("scan-target", "/dms/target", read_only=True)
            )
        if artifact_path:
            volumes.append(
                {
                    "name": "scan-artifacts",
                    "hostPath": {"path": artifact_path, "type": "DirectoryOrCreate"},
                }
            )
            volume_mounts.append(
                _host_volume_mount("scan-artifacts", "/dms/artifacts")
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
                    "name": "DMS_POSIX_USERNAME",
                    "value": str(identity.get("posix_username") or ""),
                },
                {
                    "name": "DMS_SCAN_STORAGE",
                    "value": target.get("storage_name", data_job["storage_name"]),
                },
                {
                    "name": "DMS_SCAN_PATH",
                    "value": target.get("path", data_job.get("target") or "."),
                },
                {"name": "DMS_ARTIFACT_URI", "value": artifact_uri},
                {
                    "name": "DMS_SELECTED_NODE",
                    "value": node_names[0] if len(node_names) == 1 else "",
                },
                {
                    "name": "DMS_MPI_PROCESSES_PER_NODE",
                    "value": str(
                        _resource_model(data_job).get("processes_per_node") or 1
                    ),
                },
                {
                    "name": "DMS_MPI_PROCESS_COUNT",
                    "value": str(_resource_model(data_job).get("process_count") or 1),
                },
                {
                    "name": "DMS_WORKER_POD_COUNT",
                    "value": str(
                        _resource_model(data_job).get("worker_pod_count") or 1
                    ),
                },
            ],
        }

    def _nsync_context(
        self, plan: dict[str, Any], data_job: dict[str, Any]
    ) -> dict[str, Any]:
        artifact_uri = _artifact_job_uri(
            self.settings.dm_artifact_base_uri, data_job["job_id"]
        )
        normalized = (
            data_job.get("normalized_target") or plan.get("desired_state") or {}
        )
        source = (
            normalized.get("source")
            or plan.get("desired_state", {}).get("source")
            or {}
        )
        destination = (
            normalized.get("destination")
            or plan.get("desired_state", {}).get("destination")
            or {}
        )
        worker_pool = data_job.get("worker_pool") or {}
        source_candidates = _unique_selected_candidates(
            worker_pool.get("source_candidates") or []
        )
        destination_candidates = _unique_selected_candidates(
            worker_pool.get("destination_candidates") or []
        )
        source_mount = (
            source_candidates[0].get("mount_path") if source_candidates else None
        )
        destination_mount = (
            destination_candidates[0].get("mount_path")
            if destination_candidates
            else None
        )
        artifact_path = _file_uri_parent_path(artifact_uri)
        artifact_volumes: list[dict[str, Any]] = []
        artifact_mounts: list[dict[str, Any]] = []
        if artifact_path:
            artifact_volumes.append(
                {
                    "name": "mutation-artifacts",
                    "hostPath": {"path": artifact_path, "type": "DirectoryOrCreate"},
                }
            )
            artifact_mounts.append(
                _host_volume_mount("mutation-artifacts", "/dms/artifacts")
            )

        source_volumes = [*artifact_volumes]
        source_mounts = [*artifact_mounts]
        if source_mount:
            source_volumes.append(
                {
                    "name": "sync-source",
                    "hostPath": {"path": source_mount, "type": "Directory"},
                }
            )
            source_mounts.append(
                _host_volume_mount("sync-source", "/dms/source", read_only=True)
            )
        destination_volumes = [*artifact_volumes]
        destination_mounts = [*artifact_mounts]
        if destination_mount:
            destination_volumes.append(
                {
                    "name": "sync-destination",
                    "hostPath": {"path": destination_mount, "type": "Directory"},
                }
            )
            destination_mounts.append(
                _host_volume_mount("sync-destination", "/dms/destination")
            )

        identity = data_job.get("preflight_result", {}).get("identity_mapping") or {}
        resource_model = _resource_model(data_job)
        env = [
            {"name": "DMS_DATA_JOB_ID", "value": data_job["job_id"]},
            {"name": "DMS_ARTIFACT_URI", "value": artifact_uri},
            {
                "name": "DMS_POSIX_USERNAME",
                "value": str(identity.get("posix_username") or ""),
            },
            {"name": "DMS_SELECTED_TOOL", "value": "nsync"},
            {"name": "DMS_SELECTED_NODE", "value": ""},
            {
                "name": "DMS_MPI_PROCESSES_PER_NODE",
                "value": str(resource_model.get("processes_per_node") or 1),
            },
            {
                "name": "DMS_MPI_PROCESS_COUNT",
                "value": str(resource_model.get("process_count") or 1),
            },
            {
                "name": "DMS_WORKER_POD_COUNT",
                "value": str(resource_model.get("worker_pod_count") or 1),
            },
            {
                "name": "DMS_NSYNC_SOURCE_NODE_COUNT",
                "value": str(resource_model.get("source_node_count") or 1),
            },
            {
                "name": "DMS_NSYNC_DESTINATION_NODE_COUNT",
                "value": str(resource_model.get("destination_node_count") or 1),
            },
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
        return {
            "artifact_uri": artifact_uri,
            "env": env,
            "artifact_volumes": artifact_volumes,
            "artifact_volume_mounts": artifact_mounts,
            "source_volumes": source_volumes,
            "source_volume_mounts": source_mounts,
            "destination_volumes": destination_volumes,
            "destination_volume_mounts": destination_mounts,
        }



def _host_volume_mount(
    name: str, mount_path: str, *, read_only: bool = False
) -> dict[str, Any]:
    """Build a volumeMount for a hostPath backed by the shared filesystem.

    Uses ``HostToContainer`` (rslave) propagation so that re-mounts of the shared
    FS on the host propagate into the job pod. Without it the kubelet bind mount
    is private and goes stale if the host unmounts/remounts the FS while the pod
    is alive, leaving the pod with a detached view (see the dm-worker Deployment
    and the ARCHITECTURE bind-mount-stale note). source/target mounts are the
    storage mount points themselves; the artifact subdir mount also opts in for
    consistency (job pods are short-lived so the residual subdir-bind risk is low).
    """
    mount: dict[str, Any] = {
        "name": name,
        "mountPath": mount_path,
        "mountPropagation": "HostToContainer",
    }
    if read_only:
        mount["readOnly"] = True
    return mount


def _mpi_worker_command() -> str:
    return "\n".join(
        [
            "set -eu",
            "mkdir -p /run/sshd",
            "ssh-keygen -A >/dev/null 2>&1 || true",
            'if [ -n "${DMS_POSIX_USERNAME:-}" ] && id "$DMS_POSIX_USERNAME" >/dev/null 2>&1; then',
            "  user_home=$(getent passwd \"$DMS_POSIX_USERNAME\" | awk -F: '{print $6}')",
            "  # Skip for root: user_home=/root already holds the read-only Volcano SSH keys",
            "  # (sshd reads them directly); copying/chowning would fail on the RO mount.",
            '  if [ -n "$user_home" ] && [ "$user_home" != /root ]; then',
            '    mkdir -p "$user_home/.ssh"',
            '    chown "$DMS_POSIX_USERNAME" "$user_home" 2>/dev/null || true',
            '    if [ -f /root/.ssh/authorized_keys ]; then cp /root/.ssh/authorized_keys "$user_home/.ssh/authorized_keys"; fi',
            '    chown -R "$DMS_POSIX_USERNAME" "$user_home/.ssh"',
            '    chmod 0700 "$user_home/.ssh"',
            '    chmod 0600 "$user_home/.ssh"/* 2>/dev/null || true',
            "  fi",
            "fi",
            "exec /usr/sbin/sshd -D -e -o StrictModes=no",
        ]
    )



def _mpi_worker_container_security_context() -> dict[str, Any]:
    return {"capabilities": {"add": ["SYS_CHROOT"]}}



def _mpi_hostfile_lines(
    output_path: str, *, env_name: str = "DMS_MPI_HOSTFILE"
) -> list[str]:
    return [
        f'mpi_hostfile="{output_path}"',
        ': > "$mpi_hostfile"',
        'raw_hostfile="$mpi_hostfile.raw"',
        ': > "$raw_hostfile"',
        "expected_hosts=${DMS_WORKER_POD_COUNT:-0}",
        f'if [ -n "${{{env_name}:-}}" ] && [ -f "${env_name}" ]; then cat "${env_name}" > "$raw_hostfile"; fi',
        'if [ "$expected_hosts" -gt 0 ]; then',
        "  attempts=0",
        '  while [ "$attempts" -lt 60 ]; do',
        "    host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$raw_hostfile\")",
        '    [ "$host_count" -ge "$expected_hosts" ] && break',
        f'    if [ -n "${{{env_name}:-}}" ] && [ -f "${env_name}" ]; then cat "${env_name}" > "$raw_hostfile"; fi',
        "    host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$raw_hostfile\")",
        '    [ "$host_count" -ge "$expected_hosts" ] && break',
        "    if [ -n \"${VC_WORKER_HOSTS:-}\" ]; then printf '%s\\n' \"$VC_WORKER_HOSTS\" | tr ',' '\\n' > \"$raw_hostfile\"; fi",
        "    host_count=$(awk 'NF { count++ } END { print count + 0 }' \"$raw_hostfile\")",
        '    [ "$host_count" -ge "$expected_hosts" ] && break',
        "    attempts=$((attempts + 1))",
        "    sleep 1",
        "  done",
        "fi",
        'if [ -s "$raw_hostfile" ]; then',
        f'  while IFS= read -r host_line || [ -n "$host_line" ]; do',
        "    host=$(printf '%s\\n' \"$host_line\" | awk '{print $1}')",
        '    [ -n "$host" ] || continue',
        '    resolved=""',
        "    attempts=0",
        '    while [ "$attempts" -lt 30 ]; do',
        "      resolved=$(getent hosts \"$host\" 2>/dev/null | awk 'NR == 1 {print $1}')",
        '      [ -n "$resolved" ] && break',
        "      attempts=$((attempts + 1))",
        "      sleep 1",
        "    done",
        '    if [ -n "$resolved" ]; then host="$resolved"; fi',
        '    printf \'%s slots=%s\\n\' "$host" "$DMS_MPI_PROCESSES_PER_NODE" >> "$mpi_hostfile"',
        f'  done < "$raw_hostfile"',
        "fi",
        'hostfile_arg=""',
        'if [ -s "$mpi_hostfile" ]; then hostfile_arg="--hostfile $mpi_hostfile"; fi',
    ]



def _mpiexec_line(*, stdout: str, stderr: str) -> str:
    return (
        "export OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1; "
        "export OMPI_MCA_plm_rsh_agent='ssh -o StrictHostKeyChecking=no "
        "-o UserKnownHostsFile=/dev/null'; "
        'export DMS_POSIX_USERNAME="${DMS_POSIX_USERNAME:-}" '
        'DMS_MPI_SCAN_TARGET="${DMS_MPI_SCAN_TARGET:-}" '
        'DMS_MPI_SCAN_REPORT="${DMS_MPI_SCAN_REPORT:-}" '
        'DMS_MPI_SYNC_SOURCE="${DMS_MPI_SYNC_SOURCE:-}" '
        'DMS_MPI_SYNC_DESTINATION="${DMS_MPI_SYNC_DESTINATION:-}" '
        'DMS_MPI_RM_TARGET="${DMS_MPI_RM_TARGET:-}" '
        'DMS_NSYNC_ROLE_MAP="${DMS_NSYNC_ROLE_MAP:-}"; '
        "env_exports='-x PATH -x LD_LIBRARY_PATH -x DMS_POSIX_USERNAME "
        "-x DMS_MPI_SCAN_TARGET -x DMS_MPI_SCAN_REPORT -x DMS_MPI_SYNC_SOURCE "
        "-x DMS_MPI_SYNC_DESTINATION -x DMS_MPI_RM_TARGET -x DMS_NSYNC_ROLE_MAP'; "
        'mpi_run_prefix=""; '
        'if [ "$(id -u)" = 0 ] && [ -n "${DMS_POSIX_USERNAME:-}" ] '
        '&& id "$DMS_POSIX_USERNAME" >/dev/null 2>&1 '
        "&& command -v runuser >/dev/null 2>&1; then "
        "user_home=$(getent passwd \"$DMS_POSIX_USERNAME\" | awk -F: '{print $6}'); "
        # Skip for a root requester: user_home is /root, where Volcano already mounts the
        # MPI SSH keys READ-ONLY. root reads them directly, so copying/chowning them would
        # fail on the read-only mount. (Non-root users get the keys copied into their home.)
        'if [ -n "$user_home" ] && [ "$user_home" != /root ]; then '
        'mkdir -p "$user_home/.ssh"; '
        'chown "$DMS_POSIX_USERNAME" "$user_home" 2>/dev/null || true; '
        'cp -a /root/.ssh/. "$user_home/.ssh/" 2>/dev/null || true; '
        'chown -R "$DMS_POSIX_USERNAME" "$user_home/.ssh"; '
        'chmod 0700 "$user_home/.ssh"; '
        'chmod 0600 "$user_home/.ssh"/* 2>/dev/null || true; '
        "fi; "
        # mpirun runs as the requester (runuser); with `umask 077` the root-created
        # coordination files (hostfile, rank script) are root-only, so hand the mpi
        # directory to the requester right before launch. Output (summary/logs) stays
        # root-owned and the dm-worker (root) reads it.
        'chown -R "$DMS_POSIX_USERNAME" "$(dirname "$(dirname "$rank_script")")" 2>/dev/null || true; '
        'mpi_run_prefix="runuser -u $DMS_POSIX_USERNAME --preserve-environment --"; '
        "fi; "
        "$mpi_run_prefix mpirun --allow-run-as-root --mca pml ob1 --mca btl tcp,self "
        "--mca oob_tcp_if_exclude lo --mca btl_tcp_if_exclude lo "
        '$hostfile_arg $env_exports -np "$DMS_MPI_PROCESS_COUNT" '
        f'"$rank_script" > {stdout} 2> {stderr}'
    )



def _chown_artifact_line(path: str) -> str:
    # Requester-owned only (no world bits). With `umask 077` in the launcher this keeps
    # per-job artifacts unreadable by other tenants' job pods on the shared FS; the
    # dm-worker reads them as root (deployment securityContext runAsUser:0).
    return (
        'if [ "$(id -u)" = 0 ] && [ -n "${DMS_POSIX_USERNAME:-}" ]; then '
        f'chown -R "$DMS_POSIX_USERNAME" "{path}" || true; '
        f'chmod -R u+rwX,go-rwx "{path}" || true; fi'
    )



def volcano_adapter_from_settings(
    settings: Settings,
) -> StubVolcanoAdapter | KubernetesVolcanoAdapter:
    if settings.dm_kubernetes_mode == "stub":
        return StubVolcanoAdapter()
    return KubernetesVolcanoAdapter.from_settings(settings)



def _parse_volcano_ref(job_ref: str) -> tuple[str, str]:
    if not job_ref.startswith("volcano://"):
        raise DataManagementRuntimeError(f"invalid Volcano job ref: {job_ref}")
    namespace, name = job_ref.removeprefix("volcano://").split("/", 1)
    return namespace, name



def _parse_kind_ref(job_ref: str, prefix: str) -> tuple[str, str]:
    if not job_ref.startswith(prefix):
        raise DataManagementRuntimeError(f"invalid job ref: {job_ref}")
    namespace, name = job_ref.removeprefix(prefix).split("/", 1)
    return namespace, name



def _job_ref_for_manifest(manifest: dict[str, Any]) -> str:
    namespace = manifest["metadata"]["namespace"]
    name = manifest["metadata"]["name"]
    if manifest.get("kind") == "MPIJob":
        return f"mpijob://{namespace}/{name}"
    return f"volcano://{namespace}/{name}"



def _scheduler_backend_for_manifest(manifest: dict[str, Any]) -> str:
    if manifest.get("kind") == "MPIJob":
        return "mpi-operator"
    return "volcano-job"



def _can_fallback_from_manifest_apply(
    manifest: dict[str, Any], message: str, backend: str
) -> bool:
    if (backend or "auto").strip().lower() != "auto":
        return False
    if manifest.get("kind") != "MPIJob":
        return False
    lowered = message.lower()
    return (
        "no matches for kind" in lowered
        or "could not find the requested resource" in lowered
        or "the server doesn't have a resource type" in lowered
        or "mpijobs" in lowered
        and "not found" in lowered
    )



def _mpijob_phase(payload: dict[str, Any]) -> str:
    status = payload.get("status") or {}
    for condition in status.get("conditions") or []:
        type_name = str(condition.get("type") or "").lower()
        condition_status = str(condition.get("status") or "").lower()
        if condition_status not in {"true", "1"}:
            continue
        if type_name in {"succeeded", "complete", "completed"}:
            return "Succeeded"
        if type_name in {"failed"}:
            return "Failed"
        if type_name in {"running"}:
            return "Running"
    if status.get("completionTime"):
        return "Succeeded"
    if status.get("startTime"):
        return "Running"
    return "Pending"



def _drop_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _drop_none(item) for key, item in value.items() if item is not None
        }
    if isinstance(value, list):
        return [_drop_none(item) for item in value]
    return value



def _artifact_job_uri(base_uri: str, job_id: str) -> str:
    return f"{base_uri.rstrip('/')}/{job_id}"



def _artifact_child_uri(base_uri: str | None, name: str) -> str | None:
    if not base_uri:
        return None
    return f"{base_uri.rstrip('/')}/{name.lstrip('/')}"



def _mpi_metadata_uris(artifact_uri: str | None) -> dict[str, str | None]:
    return {
        "submitted_uri": _artifact_child_uri(artifact_uri, "mpi/submitted.yaml"),
        "launch_uri": _artifact_child_uri(artifact_uri, "mpi/launch.json"),
        "workers_uri": _artifact_child_uri(artifact_uri, "mpi/workers.json"),
        "scheduler_uri": _artifact_child_uri(artifact_uri, "mpi/scheduler.json"),
        "mpirun_uri": _artifact_child_uri(artifact_uri, "mpi/mpirun.json"),
    }



def _scheduled_nodes_from_observed(observed: dict[str, Any]) -> list[str]:
    pod_summary = observed.get("pod_summary") if isinstance(observed, dict) else {}
    if not isinstance(pod_summary, dict):
        return []
    nodes: list[str] = []
    seen: set[str] = set()
    for pod in pod_summary.get("pods") or []:
        if pod.get("role") == "launcher":
            continue
        node_name = pod.get("node_name")
        if not node_name or node_name in seen:
            continue
        seen.add(str(node_name))
        nodes.append(str(node_name))
    return nodes



def _observed_has_worker_pods(observed: dict[str, Any]) -> bool:
    pod_summary = observed.get("pod_summary") if isinstance(observed, dict) else {}
    if not isinstance(pod_summary, dict):
        return False
    return any(
        isinstance(pod, dict) and pod.get("role") != "launcher"
        for pod in pod_summary.get("pods") or []
    )



def _write_mpi_metadata_artifacts(
    *,
    artifact_uri: str | None,
    manifest: dict[str, Any],
    data_job: dict[str, Any],
    phase: str,
    observed: dict[str, Any],
) -> None:
    if not artifact_uri or urlparse(artifact_uri).scheme != "file":
        return
    base = Path(urlparse(artifact_uri).path)
    mpi_dir = base / "mpi"
    mpi_dir.mkdir(parents=True, exist_ok=True)
    submitted = _drop_none(manifest)
    (mpi_dir / "submitted.yaml").write_text(
        _simple_yaml(submitted),
        encoding="utf-8",
    )
    resource_model = _resource_model(data_job)
    pod_summary = observed.get("pod_summary") if isinstance(observed, dict) else {}
    if not isinstance(pod_summary, dict):
        pod_summary = {}
    pods = pod_summary.get("pods") or []
    launcher_pods = [pod for pod in pods if pod.get("role") == "launcher"]
    worker_pods = [pod for pod in pods if pod.get("role") != "launcher"]
    _write_json(
        mpi_dir / "launch.json",
        {
            "backend": _scheduler_backend_for_manifest(manifest),
            "phase": phase,
            "tool": data_job.get("selected_tool")
            or _default_tool_for_operation(data_job["operation"]),
            "launcher_pods": launcher_pods,
            "image": data_job.get("image_ref"),
            "queue": resource_model.get("queue"),
            "priority_class": resource_model.get("priority_class"),
        },
    )
    _write_json(
        mpi_dir / "workers.json",
        {
            "worker_pod_count": len(worker_pods),
            "workers": worker_pods,
            "eligible_nodes": resource_model.get("eligible_nodes"),
            "eligible_source_nodes": resource_model.get("eligible_source_nodes"),
            "eligible_destination_nodes": resource_model.get(
                "eligible_destination_nodes"
            ),
        },
    )
    _write_json(
        mpi_dir / "scheduler.json",
        {
            "backend": _scheduler_backend_for_manifest(manifest),
            "scheduler_name": "volcano",
            "queue": resource_model.get("queue"),
            "priority_class": resource_model.get("priority_class"),
            "min_available": _manifest_min_available(manifest),
            "phase": observed.get("phase"),
            "status": observed.get("status") or observed.get("state"),
            "job_ref": observed.get("job_ref"),
        },
    )
    mpirun_path = mpi_dir / "mpirun.json"
    existing_mpirun: dict[str, Any] = {}
    if mpirun_path.exists():
        try:
            loaded = json.loads(mpirun_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_mpirun = loaded
        except (OSError, json.JSONDecodeError):
            existing_mpirun = {"previous_metadata_parse_error": True}
    _write_json(
        mpirun_path,
        {
            **existing_mpirun,
            "process_count": resource_model.get("process_count"),
            "processes_per_node": resource_model.get("processes_per_node"),
            "worker_pod_count": resource_model.get("worker_pod_count"),
            "interface_mode": existing_mpirun.get("interface_mode", "auto"),
            "exit_phase": observed.get("phase"),
        },
    )



def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")



def _simple_yaml(value: Any, *, indent: int = 0) -> str:
    rendered = _render_yaml(value, indent=indent)
    return rendered if rendered.endswith("\n") else f"{rendered}\n"



def _render_yaml(value: Any, *, indent: int) -> str:
    prefix = " " * indent
    if isinstance(value, dict):
        lines: list[str] = []
        for key, item in value.items():
            if isinstance(item, (dict, list)):
                lines.append(f"{prefix}{key}:")
                lines.append(_render_yaml(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}{key}: {_yaml_scalar(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, dict):
                lines.append(f"{prefix}-")
                lines.append(_render_yaml(item, indent=indent + 2))
            elif isinstance(item, list):
                lines.append(f"{prefix}-")
                lines.append(_render_yaml(item, indent=indent + 2))
            else:
                lines.append(f"{prefix}- {_yaml_scalar(item)}")
        return "\n".join(lines)
    return f"{prefix}{_yaml_scalar(value)}"



def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value))



def _manifest_min_available(manifest: dict[str, Any]) -> int | None:
    spec = manifest.get("spec") or {}
    if manifest.get("kind") == "MPIJob":
        return ((spec.get("runPolicy") or {}).get("schedulingPolicy") or {}).get(
            "minAvailable"
        )
    return spec.get("minAvailable")



def _file_uri_parent_path(uri: str) -> str | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return str(Path(parsed.path).parent)



def _unique_selected_candidates(
    candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str | None, str | None]] = set()
    for candidate in candidates:
        key = (candidate.get("cluster_name"), candidate.get("node_name"))
        if key in seen:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique



def _candidate_node_names(candidates: list[dict[str, Any]]) -> list[str]:
    return [
        candidate["node_name"]
        for candidate in _unique_selected_candidates(candidates)
        if candidate.get("node_name")
    ]



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



def _worker_pod_anti_affinity(job_id: str) -> dict[str, Any]:
    return {
        "podAntiAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": [
                {
                    "labelSelector": {
                        "matchLabels": {
                            "app.kubernetes.io/name": "dms-data-management",
                            "dms.openai.com/data-job-id": job_id,
                            "dms.openai.com/data-role": "worker",
                        }
                    },
                    "topologyKey": "kubernetes.io/hostname",
                }
            ]
        }
    }



def _merge_affinity(*items: dict[str, Any]) -> dict[str, Any]:
    affinity: dict[str, Any] = {}
    for item in items:
        for key, value in item.items():
            if key not in affinity:
                affinity[key] = value
                continue
            if isinstance(value, dict) and isinstance(affinity[key], dict):
                affinity[key].update(value)
            else:
                affinity[key] = value
    return affinity



def _resource_model(data_job: dict[str, Any]) -> dict[str, Any]:
    model = (data_job.get("preflight_result") or {}).get(
        "effective_resource_model"
    ) or {}
    return model if isinstance(model, dict) else {}



def _pod_security_context(preflight: dict[str, Any]) -> dict[str, Any]:
    mapping = preflight.get("identity_mapping") or {}
    gid = mapping.get("gid")
    if gid is None:
        return {}
    uid = mapping.get("uid")
    if uid is not None and int(uid) == 0:
        # Privileged (root) data job: the pod runs as root, so runAsNonRoot must NOT be set
        # (kubelet would refuse to start the container). Authorized upstream at the API edge.
        return {"fsGroup": 0}
    return {"fsGroup": int(gid), "runAsNonRoot": True}



def _container_security_context(preflight: dict[str, Any]) -> dict[str, Any]:
    mapping = preflight.get("identity_mapping") or {}
    uid = mapping.get("uid")
    gid = mapping.get("gid")
    if uid is None or gid is None:
        return {}
    if int(uid) == 0:
        # Privileged (root) data job: run as root (no runAsNonRoot), still no privilege
        # escalation. Gated by the API mTLS-operator check; the floor is bypassed only for
        # synthesized privileged identities.
        return {
            "allowPrivilegeEscalation": False,
            "runAsUser": 0,
            "runAsGroup": int(gid),
        }
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



def _data_job_kubernetes_name(prefix: str, job_id: str, *, max_length: int = 42) -> str:
    token = _kubernetes_name(job_id)[-12:] or "job"
    normalized_prefix = _kubernetes_name(prefix)
    candidate = _kubernetes_name(f"{normalized_prefix}-{token}")
    if len(candidate) <= max_length:
        return candidate
    prefix_budget = max(max_length - len(token) - 1, 1)
    return f"{normalized_prefix[:prefix_budget].rstrip('-')}-{token}"[
        :max_length
    ].rstrip("-")



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
        "chmod": ("--chmod", "value"),
        "chown": ("--chown", "value"),
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
