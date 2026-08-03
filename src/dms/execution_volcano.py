"""Volcano/k8s 실행 어댑터. k8s 접근은 주입된 K8sClient 뒤, 아티팩트 읽기는 read_text 뒤."""
import json
from typing import Protocol

from .execution import ExecStatus, ExecutionError
from .execution_manifests import build_preflight_pod, build_volcano_job

_POD_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
              "Succeeded": ExecStatus.SUCCEEDED, "Failed": ExecStatus.FAILED,
              "Unknown": ExecStatus.FAILED}
_VCJOB_PHASE = {"Pending": ExecStatus.PENDING, "Running": ExecStatus.RUNNING,
                "Inqueue": ExecStatus.PENDING,
                "Completed": ExecStatus.SUCCEEDED, "Completing": ExecStatus.RUNNING,
                "Failed": ExecStatus.FAILED, "Aborted": ExecStatus.FAILED,
                "Aborting": ExecStatus.RUNNING, "Terminating": ExecStatus.RUNNING,
                "Restarting": ExecStatus.RUNNING}
_KIND = {"pod": "Pod", "vcjob": "Job"}


class K8sClient(Protocol):
    def create(self, manifest: dict) -> None: ...
    def get(self, kind: str, name: str, namespace: str) -> "dict | None": ...
    def delete(self, kind: str, name: str, namespace: str) -> None:
        """대상이 이미 없으면 조용히 무시(404 삼킴) — 멱등 종료 계약."""
        ...
    def read_pod_log(self, name: str, namespace: str) -> str: ...


class VolcanoExecutionAdapter:
    def __init__(self, k8s, *, job_image, namespace, storages_lookup, read_text,
                 artifact_base):
        self._k8s = k8s
        self._job_image = job_image
        self._namespace = namespace
        self._storages = storages_lookup
        self._read_text = read_text
        self._artifact_base = artifact_base  # summary 경로 fallback 재구성용
        self._summary_paths = {}   # ref -> artifact summary.json path (in-memory 빠른 경로)

    def _volumes(self, spec):
        # 데이터 스토리지 mount_path + 아티팩트 base 를 수집해 hostPath 볼륨으로.
        if spec.operation == "sync":
            snames = [spec.paths.get("source_storage"),
                      spec.paths.get("destination_storage")]
        else:
            snames = [spec.paths.get("storage")]
        mount_paths = []
        for sname in snames:
            if sname:
                mount_paths.append(self._storages(sname)["mount_path"])
        # summary.json 기록 위치 — 스킴 제거한 artifact base 도 반드시 마운트.
        mount_paths.append(spec.artifact_base.replace("file://", ""))
        # 다른 경로가 상위(ancestor)면 하위 경로는 커버되므로 생략 — 중첩 마운트 방지.
        # 후행 슬래시로 "/cephfs" 가 "/cephfs-third" 를 잘못 삼키지 않게 함.
        minimal = []
        for p in mount_paths:
            covered = any(p != q and p.startswith(q.rstrip("/") + "/")
                          for q in mount_paths)
            if covered or p in minimal:
                continue
            minimal.append(p)
        return [{"name": mp.strip("/").replace("/", "-") or "root",
                 "hostPath": {"path": mp}, "mountPath": mp} for mp in minimal]

    # phases that run as a single preflight Pod (not a Volcano Job): the initial
    # preflight AND the post-confirm re-validation. Both must route to
    # build_preflight_pod, else the re-check would be built as a Volcano Job with
    # an "exec_preflight" phase in its name (underscore -> invalid -> 422).
    _PREFLIGHT_PHASES = ("preflight", "exec_preflight")

    def submit(self, spec) -> str:
        try:
            if spec.phase in self._PREFLIGHT_PHASES:
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
            raise ExecutionError("submit_failed", str(exc)[:200]) from exc
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
            path = self._reconstruct_summary_path(ref)
            if path is None:
                return None
        text = self._read_text(path)
        if not text:
            return None
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    def _reconstruct_summary_path(self, ref):
        """컨트롤러 재시작으로 _summary_paths가 유실됐을 때 k8s 오브젝트 라벨에서
        summary.json 경로를 재구성한다. build_volcano_job/build_preflight_pod가
        dms.io/job-id(전체 job_id)와 dms.io/phase 라벨을 이미 붙여둔다."""
        prefix, name = ref.split("/", 1)
        obj = self._k8s.get(_KIND[prefix], name, self._namespace)
        if obj is None:
            return None
        labels = ((obj.get("metadata") or {}).get("labels") or {})
        job_id = labels.get("dms.io/job-id")
        phase = labels.get("dms.io/phase")
        if not job_id or not phase:
            return None
        return (f"{self._artifact_base}/{job_id}/{phase}/summary.json"
                .replace("file://", ""))

    def terminate(self, ref) -> None:
        prefix, name = ref.split("/", 1)
        try:
            self._k8s.delete(_KIND[prefix], name, self._namespace)
        except Exception as exc:
            raise ExecutionError("terminate_failed", str(exc)[:200]) from exc


class KubernetesClient:  # pragma: no cover - 실증 대상
    """실 kubernetes 파이썬 클라이언트 기반 K8sClient. in-cluster config를
    최초 메서드 호출 시점까지 lazy 하게 미룬다 — 클러스터 밖 단위 테스트가
    이 클래스를 생성만 하고(config 로드 없이) mock으로 대체해 사용하기 때문."""
    _VC = {"group": "batch.volcano.sh", "version": "v1alpha1", "plural": "jobs"}

    def __init__(self, namespace):
        self._namespace = namespace
        self._core = None
        self._custom = None

    def _ensure(self):
        if self._core is None:
            import kubernetes
            kubernetes.config.load_incluster_config()
            self._core = kubernetes.client.CoreV1Api()
            self._custom = kubernetes.client.CustomObjectsApi()

    def create(self, manifest):
        self._ensure()
        if manifest["kind"] == "Pod":
            self._core.create_namespaced_pod(self._namespace, manifest)
        else:
            self._custom.create_namespaced_custom_object(
                self._VC["group"], self._VC["version"], self._namespace,
                self._VC["plural"], manifest)

    def get(self, kind, name, namespace):
        self._ensure()
        import kubernetes
        try:
            if kind == "Pod":
                obj = self._core.read_namespaced_pod_status(name, namespace)
                return obj.to_dict()
            return self._custom.get_namespaced_custom_object(
                self._VC["group"], self._VC["version"], namespace,
                self._VC["plural"], name)
        except kubernetes.client.ApiException as exc:
            if exc.status == 404:
                return None
            raise

    def delete(self, kind, name, namespace):
        self._ensure()
        import kubernetes
        try:
            if kind == "Pod":
                self._core.delete_namespaced_pod(name, namespace)
            else:
                self._custom.delete_namespaced_custom_object(
                    self._VC["group"], self._VC["version"], namespace,
                    self._VC["plural"], name)
        except kubernetes.client.ApiException as exc:
            if exc.status != 404:
                raise

    def read_pod_log(self, name, namespace):
        self._ensure()
        return self._core.read_namespaced_pod_log(name, namespace)
