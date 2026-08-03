"""Volcano/k8s 실행 어댑터. k8s 접근은 주입된 K8sClient 뒤, 아티팩트 읽기는 read_text 뒤."""
import json
from typing import Protocol

from .execution import ExecStatus, ExecutionError
from .execution_manifests import build_preflight_pod, build_volcano_job

_POD_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
              "Succeeded": ExecStatus.SUCCEEDED, "Failed": ExecStatus.FAILED,
              "Unknown": ExecStatus.FAILED}
_VCJOB_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
                "Completed": ExecStatus.SUCCEEDED, "Completing": ExecStatus.RUNNING,
                "Failed": ExecStatus.FAILED, "Aborted": ExecStatus.FAILED,
                "Aborting": ExecStatus.RUNNING, "Terminating": ExecStatus.RUNNING,
                "Restarting": ExecStatus.RUNNING}
_KIND = {"pod": "Pod", "vcjob": "Job"}


class K8sClient(Protocol):
    def create(self, manifest: dict) -> None: ...
    def get(self, kind: str, name: str, namespace: str) -> "dict | None": ...
    def delete(self, kind: str, name: str, namespace: str) -> None: ...
    def read_pod_log(self, name: str, namespace: str) -> str: ...


class VolcanoExecutionAdapter:
    def __init__(self, k8s, *, job_image, namespace, storages_lookup, read_text):
        self._k8s = k8s
        self._job_image = job_image
        self._namespace = namespace
        self._storages = storages_lookup
        self._read_text = read_text
        self._summary_paths = {}   # ref -> artifact summary.json path

    def _volumes(self, spec):
        names = set()
        if spec.operation == "sync":
            names.update([spec.paths.get("source_storage"),
                          spec.paths.get("destination_storage")])
        else:
            names.add(spec.paths.get("storage"))
        vols = []
        seen = set()
        for sname in names:
            if not sname:
                continue
            meta = self._storages(sname)
            mp = meta["mount_path"]
            if mp in seen:
                continue
            seen.add(mp)
            vols.append({"name": mp.strip("/").replace("/", "-") or "root",
                         "hostPath": {"path": mp}, "mountPath": mp})
        return vols

    def submit(self, spec) -> str:
        try:
            if spec.phase == "preflight":
                nodes = (spec.candidates.get("primary")
                         or spec.candidates.get("source") or ["dms-w1"])
                manifest = build_preflight_pod(
                    spec, job_image=self._job_image, namespace=self._namespace,
                    volumes=self._volumes(spec), node=nodes[0])
                prefix = "pod"
            else:
                manifest = build_volcano_job(
                    spec, job_image=self._job_image, namespace=self._namespace,
                    volumes=self._volumes(spec))
                prefix = "vcjob"
            self._k8s.create(manifest)
        except Exception as exc:
            raise ExecutionError("submit_failed", str(exc)[:200])
        name = manifest["metadata"]["name"]
        ref = f"{prefix}/{name}"
        base = spec.artifact_base
        self._summary_paths[ref] = (
            f"{base}/{spec.job_id}/{spec.phase}/summary.json".replace("file://", ""))
        return ref

    def poll(self, ref) -> ExecStatus:
        prefix, name = ref.split("/", 1)
        obj = self._k8s.get(_KIND[prefix], name, self._namespace)
        if obj is None:
            return ExecStatus.FAILED
        status = obj.get("status") or {}
        if prefix == "pod":
            return _POD_PHASE.get(status.get("phase"), ExecStatus.FAILED)
        phase = (status.get("state") or {}).get("phase")
        return _VCJOB_PHASE.get(phase, ExecStatus.FAILED)

    def read_summary(self, ref):
        path = self._summary_paths.get(ref)
        if path is None:
            return None
        text = self._read_text(path)
        if not text:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    def terminate(self, ref) -> None:
        prefix, name = ref.split("/", 1)
        try:
            self._k8s.delete(_KIND[prefix], name, self._namespace)
        except Exception as exc:
            raise ExecutionError("terminate_failed", str(exc)[:200])
