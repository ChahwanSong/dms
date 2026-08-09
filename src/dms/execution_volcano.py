"""Volcano/k8s 실행 어댑터. k8s 접근은 주입된 K8sClient 뒤, 아티팩트 읽기는 read_text 뒤."""
import json
import logging
import threading
from typing import Protocol

from .execution import ExecStatus, ExecutionError
from .execution_manifests import build_preflight_pod, build_volcano_job

logger = logging.getLogger(__name__)

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

# 롤아웃 apiserver 호출의 요청 상한(초). urllib3 기본값은 무제한이라 apiserver 가 멈추면
# 틱이 영원히 블록된다 -- 이 저장소의 리스는 갱신되지 않으므로(controller.run_all_once 가
# 틱마다 새로 획득한다) 리스 max(interval*3, 30)=30초가 만료되고 다른 파드가 같은 루프에
# 진입한다. 롤아웃은 자기 자신을 재시작하는 경로라 여기서 막히는 것이 특히 나쁘다.
# 한 틱의 최악은 apiserver 호출 2회다 -- observe 뒤 spec 이미지가 목표와 달라
# 재패치하는 크래시 복구 경로(rollout_watcher.run_once)가 그것이다. observe 와
# pod_briefs 가 함께 나가는 경로는 없다: 회수(_reclaim, pod_briefs 를 부르는 유일한
# 자리)는 observe 앞에서 판정하고 즉시 반환한다. 10초면 최악 20초 < 30초.
# 설정 키로 빼지 않는다: 운영자가 튜닝할 값이 아니라 리스 TTL 과 맞물린 내부 불변식이고,
# 노출하면 리스보다 큰 값이 설정돼 이 가드가 그대로 무력화될 수 있다.
ROLLOUT_REQUEST_TIMEOUT_SECONDS = 10


def _deadline_exceeded(obj) -> bool:
    """Pod가 activeDeadlineSeconds로 죽었는지. kubelet은 status.phase=Failed 와 함께
    status.reason=DeadlineExceeded를 쓴다. 확실하지 않으면 False —
    오분류(멀쩡한 실패를 TimedOut으로)보다 보수적 유지가 낫다."""
    status = obj.get("status") or {}
    if status.get("reason") == "DeadlineExceeded":
        return True
    for cond in (status.get("conditions") or []):
        if cond.get("reason") == "DeadlineExceeded":
            return True
    return False


def _vcjob_deadline_exceeded(obj) -> bool:
    """Volcano Job이 deadline으로 죽었는지. 파드와 필드 위치가 다르다: v1.15.0 CRD의
    Job.status.conditions[] 항목은 {lastTransitionTime, status}만 갖고 reason/message는
    status.state에만 있다 — status.reason이나 conditions[].reason을 보면 영원히 못 잡는다.
    파드와 마찬가지로 불확실하면 False(FAILED 유지)."""
    state = (obj.get("status") or {}).get("state") or {}
    if state.get("reason") == "DeadlineExceeded":
        return True
    return "DeadlineExceeded" in (state.get("message") or "")


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

    def _volumes(self, spec, *, role=None):
        # 데이터 스토리지 mount_path + 아티팩트 base 를 수집해 hostPath 볼륨으로.
        # role: nsync preflight는 소스/목적지가 disjoint 노드라 각 role 파드에는 해당
        # 스토리지만 마운트해야 한다 — 반대편 스토리지는 그 노드에 없어 type:Directory
        # hostPath가 스케줄 실패한다. role=None은 양쪽(코로케이션 dsync/실행)을 마운트.
        if spec.operation == "sync":
            if role == "source":
                snames = [spec.paths.get("source_storage")]
            elif role == "destination":
                snames = [spec.paths.get("destination_storage")]
            else:
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

    def _preflight_pod(self, spec, node, role):
        return build_preflight_pod(
            spec, job_image=self._job_image, namespace=self._namespace,
            volumes=self._volumes(spec, role=role), node=node, role=role)

    def submit(self, spec) -> str:
        try:
            if spec.phase in self._PREFLIGHT_PHASES:
                # nsync = disjoint source/destination pools ("primary" 없음). 소스
                # 노드에서는 목적지 쓰기를, 목적지 노드에서는 소스 읽기를 검증할 수
                # 없으므로 preflight를 양쪽 노드에 각각 띄운다(role src/dst). 복합
                # ref "pods/<src>,<dst>"로 반환 — poll/terminate가 둘 다 다룬다.
                if "primary" not in spec.candidates and spec.candidates.get("source"):
                    src = self._preflight_pod(spec, spec.candidates["source"][0], "source")
                    dst = self._preflight_pod(
                        spec, spec.candidates["destination"][0], "destination")
                    self._k8s.create(src)
                    self._k8s.create(dst)
                    return "pods/" + ",".join(
                        [src["metadata"]["name"], dst["metadata"]["name"]])
                nodes = (spec.candidates.get("primary")
                         or spec.candidates.get("source") or ["dms-w1"])
                manifest = self._preflight_pod(spec, nodes[0], None)
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

    def _poll_pod(self, name) -> ExecStatus:
        obj = self._k8s.get("Pod", name, self._namespace)
        if obj is None:
            return ExecStatus.FAILED
        status = _POD_PHASE.get((obj.get("status") or {}).get("phase"), ExecStatus.FAILED)
        if status == ExecStatus.FAILED and _deadline_exceeded(obj):
            return ExecStatus.TIMED_OUT
        return status

    def poll(self, ref) -> ExecStatus:
        prefix, name = ref.split("/", 1)
        if prefix == "pods":
            # 복합 preflight(nsync src+dst): fail-closed 결합 — 하나라도 TIMED_OUT이면
            # TIMED_OUT(deadline이 곧 실패의 특수 케이스이므로 fail-closed와 같은 우선순위),
            # 그 다음 하나라도 FAILED면 FAILED, 전부 SUCCEEDED라야 SUCCEEDED,
            # 그 외(진행 중)는 RUNNING.
            statuses = [self._poll_pod(n) for n in name.split(",")]
            if any(s == ExecStatus.TIMED_OUT for s in statuses):
                return ExecStatus.TIMED_OUT
            if any(s == ExecStatus.FAILED for s in statuses):
                return ExecStatus.FAILED
            if all(s == ExecStatus.SUCCEEDED for s in statuses):
                return ExecStatus.SUCCEEDED
            return ExecStatus.RUNNING
        if prefix == "pod":
            return self._poll_pod(name)
        obj = self._k8s.get(_KIND[prefix], name, self._namespace)
        if obj is None:
            return ExecStatus.FAILED
        phase = ((obj.get("status") or {}).get("state") or {}).get("phase")
        status = _VCJOB_PHASE.get(phase, ExecStatus.FAILED)
        if status == ExecStatus.FAILED and _vcjob_deadline_exceeded(obj):
            return ExecStatus.TIMED_OUT
        return status

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
        if prefix not in _KIND:      # 복합 preflight ref("pods") 등 — summary 없음
            return None
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
        names = name.split(",") if prefix == "pods" else [name]
        kind = "Pod" if prefix in ("pod", "pods") else _KIND[prefix]
        try:
            for n in names:
                self._k8s.delete(kind, n, self._namespace)
        except Exception as exc:
            raise ExecutionError("terminate_failed", str(exc)[:200]) from exc

    def read_log(self, ref):
        # preflight는 파드 ref라 직접 읽는다. vcjob(launcher)은 파드 이름이 잡 이름과
        # 달라 라벨 조회가 필요하고 잡 종료 후 사라진다 — 이 슬라이스 범위 밖이므로
        # 명시적으로 거절하고, 호출자는 아티팩트 stdout/stderr로 유도한다.
        prefix, name = ref.split("/", 1)
        if prefix not in ("pod", "pods"):
            raise ExecutionError("log_not_available", prefix)
        out = []
        for pod in name.split(","):
            try:
                out.append((pod, self._k8s.read_pod_log(pod, self._namespace)))
            except Exception as exc:
                # 파드가 이미 GC됐거나 아직 로그가 없다 — 그 항목만 비우고 계속한다.
                # 다만 RBAC 거부·설정 오류·프로그래밍 버그도 여기로 떨어져 "GC됐다"와
                # 똑같이 렌더된다. 반환 계약은 그대로 두고 흔적만 남긴다.
                logger.warning("read_pod_log failed pod=%s: %s", pod, exc)
                out.append((pod, None))
        return out


class KubernetesClient:  # pragma: no cover - 실증 대상
    """실 kubernetes 파이썬 클라이언트 기반 K8sClient. in-cluster config를
    최초 메서드 호출 시점까지 lazy 하게 미룬다 — 클러스터 밖 단위 테스트가
    이 클래스를 생성만 하고(config 로드 없이) mock으로 대체해 사용하기 때문."""
    _VC = {"group": "batch.volcano.sh", "version": "v1alpha1", "plural": "jobs"}
    _WORKLOAD_KINDS = ("Deployment", "DaemonSet")

    def __init__(self, namespace):
        self._namespace = namespace
        self._core = None
        self._custom = None
        self._apps = None
        self._init_lock = threading.Lock()

    def _ensure(self):
        # 병렬 진입(예: /api/admin/metrics/infra 가 세 컴포넌트를 스레드풀로 동시에
        # observe) 시 _core 를 세팅했지만 _apps 를 아직 못 세팅한 창에서 다른 스레드가
        # None 인 _apps 를 만지지 않도록 이중검사 잠금으로 세 핸들의 세팅을 원자화한다.
        if self._core is not None:
            return
        with self._init_lock:
            if self._core is not None:
                return
            import kubernetes
            kubernetes.config.load_incluster_config()
            core = kubernetes.client.CoreV1Api()
            self._custom = kubernetes.client.CustomObjectsApi()
            self._apps = kubernetes.client.AppsV1Api()
            self._core = core  # _core 는 마지막에 — 나머지 두 핸들이 준비된 뒤 게이트 해제

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
        # _preload_content=True로 두면 클라이언트가 본문을 역직렬화하면서 bytes의 repr을
        # 문자열로 돌려주는 경우가 있다 — 실증에서 포탈에 b'DMS_PREFLIGHT_OK\n' 이 그대로
        # 노출됐다. 원시 응답을 받아 우리가 직접 디코드한다.
        resp = self._core.read_namespaced_pod_log(name, namespace, _preload_content=False)
        data = getattr(resp, "data", resp)
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data)

    def _check_kind(self, kind):
        # 검증은 try 밖에서 한다 -- 안에서 터지면 프로그래밍 오류가 _log_forbidden 을
        # 거쳐 API 오류처럼 새어 나간다. _ensure 보다도 먼저다: 잘못된 kind 는
        # apiserver 연결과 무관하게 확정적으로 틀렸다.
        if kind not in self._WORKLOAD_KINDS:
            raise ValueError(f"unsupported workload kind: {kind}")

    def patch_workload(self, kind, name, namespace, body):
        self._check_kind(kind)
        self._ensure()
        # 콘텐츠 타입을 명시한다 -- 생략하면 클라이언트 기본값이
        # application/json-patch+json(RFC 6902, 최상위가 배열)이라 이 dict 본문은
        # apiserver 가 422 로 거절한다(설계 §4). kubernetes>=36 에서만 받는 kwarg다
        # (pyproject 의 하한 근거).
        content_type = "application/strategic-merge-patch+json"
        try:
            if kind == "Deployment":
                self._apps.patch_namespaced_deployment(
                    name, namespace, body, _content_type=content_type,
                    _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
            else:
                self._apps.patch_namespaced_daemon_set(
                    name, namespace, body, _content_type=content_type,
                    _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:
            self._log_forbidden("patch", kind, name, namespace, exc)
            raise

    def get_workload(self, kind, name, namespace):
        from .rollout_status import normalize_daemonset, normalize_deployment
        self._check_kind(kind)
        self._ensure()
        try:
            if kind == "Deployment":
                obj = self._apps.read_namespaced_deployment(
                    name, namespace,
                    _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
                return normalize_deployment(obj.to_dict())
            obj = self._apps.read_namespaced_daemon_set(
                name, namespace, _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
            return normalize_daemonset(obj.to_dict())
        except Exception as exc:
            # ApiException 타입 대신 status 속성으로 판별한다 -- .venv에 kubernetes가
            # 없어 테스트가 그 타입을 만들 수 없고, 클라이언트가 보는 것도 status뿐이다.
            # (실 패키지 35.0.0/36.0.2 로 ApiException.status 존재를 확인했다 --
            #  duck-typing 이 실 클라이언트에서도 동작한다.)
            if getattr(exc, "status", None) == 404:
                return None
            self._log_forbidden("get", kind, name, namespace, exc)
            raise

    def _log_forbidden(self, verb, kind, name, namespace, exc):
        # RBAC 거부(403)가 "객체가 없다"와 똑같이 렌더된 read_pod_log 사고의 교훈 --
        # 403은 로그에서 즉시 구분돼야 한다(설계 §4).
        if getattr(exc, "status", None) == 403:
            logger.error("workload %s forbidden(403, RBAC?) %s %s/%s",
                         verb, kind, namespace, name)

    def list_pod_briefs(self, namespace, label_selector):
        self._ensure()
        pods = self._core.list_namespaced_pod(
            namespace, label_selector=label_selector,
            _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
        briefs = []
        for pod in pods.items:
            # waiting 컨테이너가 여럿이면 전부 모은다(중복 제거, 등장 순서 유지).
            # 하나만 채택하면 보고되는 원인이 컨테이너 순서에 좌우된다 -- 사이드카가
            # ImagePullBackOff 인데 주 컨테이너의 CrashLoopBackOff 만 보이는 식이다.
            # 순수 진단 채널이라 정보를 버리는 쪽보다 다 보여주는 쪽이 낫다.
            reasons = []
            for cs in (pod.status.container_statuses or []):
                waiting = getattr(cs.state, "waiting", None)
                if waiting is not None and waiting.reason:
                    if waiting.reason not in reasons:
                        reasons.append(waiting.reason)   # 예: ImagePullBackOff
            reason = ",".join(reasons) or None   # 단일 컨테이너면 이전과 같은 문자열
            briefs.append({
                "name": pod.metadata.name,
                "node": pod.spec.node_name,
                "images": {c.name: c.image for c in pod.spec.containers},
                "phase": pod.status.phase or "",
                "waiting_reason": reason,
            })
        return briefs
