"""요청 삭제 정리(request-purge)의 k8s I/O -- 삭제한 잡의 남은 Pod·vcjob 회수와 trash 를 비우는 purge 파드.

실행 어댑터(ExecutionAdapter)와 프로토콜을 공유하지 않는다(build_runner.py 선례) -- purge 는 JobSpec·큐·phase 가 없다.
컨트롤러 전용(api 는 import 하지 않는다 -- api 에는 pods create RBAC 도 없다, deploy/k8s/10-rbac.yaml).

1) 잡 객체 회수(sweep): **전체 job_id 로 확인된 객체만** 지운다. DMS 의 vcjob·preflight Pod·worker Pod 는 처음부터
   (2026-08-03 첫 매니페스트 빌더) 라벨 `dms.io/job-id=<전체 id>` 를 단다 -- 라벨 셀렉터 스윕이 곧 전체 id 일치다.
   launcher 에는 dms 라벨이 없어(실측 [5]) vcjob 이름(`volcano.sh/job-name`)으로 찾되, 그 파드의 소유 vcjob(controller
   ownerReference, uid 까지)이 이 잡의 라벨을 단 vcjob 이거나 이미 없을 때(고아 -- k8s GC 가 어차피 지운다)만 우리 것으로
   본다. **이름(job_id[:12])만으로는 아무것도 지우지 않는다**(2026-10-09 검증 지적): vcjob·preflight 이름은 앞 12자만
   담아, 접두가 같은 변조·복원 아웃박스 행이 이름으로 지우면 살아 있는 다른 잡의 launcher·worker·vcjob 을 죽인다
   (target_still_present 는 전체 id 만 비교한다). 기록된 ref 는 그 job_id 의 DMS 명명일 때만 받고(ref_belongs_to_job --
   변조된 phase_refs 가 `pod/dms-api-…` 를 가리키는 것을 거른다) launcher 를 찾을 vcjob 이름 후보로만 쓴다. pod-gc 창
   고착·Aborted vcjob 비회수가 실측됐다(실측 [4][5]) -- 자동 정리를 믿을 수 없다. 남은 객체(Terminating 포함)가 0 이
   되기 전에는 파일 단계로 넘어가지 않는다(호출자 몫) -- Terminating launcher 의 write_text 가 `<job_id>/<phase>` 를
   다시 만든다(src/dms_job_runner/runner.py).

2) purge 파드(build_purge_pod): trash(`<base>/.dms-trash/<job_id>`)를 GNU rm 으로 비우는 단명 파드. 제어면이 아니다 --
   제어면 컨테이너에 cap 을 더하지 않으려고(규칙 12) 삭제를 여기로 뺐다. 이미 도는 잡 launcher(root + 기본 cap 전부 +
   base·스토리지 rw)보다 **엄격히 약하다**: root 이지만 cap 은 DAC_OVERRIDE·FOWNER 둘뿐, 볼륨은 base 하나(스토리지 무마운트
   -- 버그가 나도 사용자 데이터에 닿지 않는다), SA 토큰 없음, 스크립트는 고정 문자열이고 이름은 positional 인자로만
   들어간다(규칙 7: 스크립트 본문에 DB·사용자 값 보간 0). 완료 판정은 파드 상태가 아니라 FS 재확인(호출자의
   artifact_trash.entry_states)이다 -- 파드 Succeeded 는 신호일 뿐이다.
"""
import hashlib
import logging
import re
from datetime import datetime, timezone

from .artifact_base import ARTIFACT_MOUNT
from .artifact_files import JOB_ID_RE, PHASES
from .artifact_trash import TRASH

logger = logging.getLogger(__name__)

PURGE_LABEL = "dms.io/purge"
PURGE_POD_PREFIX = "dms-artifact-purge-"
MAX_PURGE_NAMES = 100               # 파드 하나에 싣는 trash 이름 상한(argv 크기·한 파드의 일 양)
PURGE_POD_DEADLINE_SECONDS = 3600   # activeDeadlineSeconds -- 실행 중 파드의 상한(아래 PENDING 상한과 짝)
# activeDeadlineSeconds 는 파드가 **시작**(startTime)한 뒤에만 센다 -- 스케줄조차 안 된 파드(자원 부족·taint·마운트
# 실패로 ContainerCreating)는 영원히 Pending 이다. 벽시계로 회수해 행이 지연으로 표면화되게 한다(조용한 정체 금지,
# build_watcher 의 나이 기반 회수와 같은 이유).
PURGE_POD_PENDING_MAX_SECONDS = 600
# 끝나지 않는 파드(실행 중·종료 중): 노드가 죽으면 파드는 Running 으로 남거나 taint eviction 뒤 Terminating 에 갇히고,
# rm 이 D 상태(멈춘 CephFS)면 kubelet 이 activeDeadlineSeconds 를 집행하지 못한다. 생성 후 데드라인 + 이 유예가 지나도
# 끝나지 않은 파드는 stuck 이다 -- 두 번째 파드를 띄우지는 않되(언제나 1개) 막힌 행을 purge_pod_stuck 지연으로
# 표면화한다(request_purger._reap, 2026-10-09 검증 지적 -- 조용한 정체 금지).
PURGE_POD_STUCK_GRACE_SECONDS = 600
PURGE_POD_STUCK_SECONDS = PURGE_POD_DEADLINE_SECONDS + PURGE_POD_STUCK_GRACE_SECONDS
# 기다려도 풀리지 않는 대기 사유 -- 즉시 실패로 접는다(대기 상한까지 기다리지 않는다).
_FATAL_WAITING = frozenset({"ErrImagePull", "ImagePullBackOff", "InvalidImageName",
                            "CreateContainerConfigError", "CreateContainerError"})
_ACTIVE_PHASES = frozenset({"Pending", "Running", ""})   # "" = phase 모름 -- 끝났다고 단정하지 않는다

# 파드 이름 = DNS-1123 subdomain(실제 이름은 63자 이하로 만든다 -- execution_manifests).
_POD_NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*")
# vcjob 이름 = execution_manifests._job_name: dms-<operation>-<phase>-<job_id[:12]>. vcjob 으로 가는 phase 는
# preview·execution 뿐이다(preflight·exec_preflight 는 단일 Pod -- execution_volcano._PREFLIGHT_PHASES).
_VCJOB_RE = re.compile(r"dms-(scan|sync|rm)-(preview|execution)-([0-9a-f]{12})")


class PurgeError(Exception):
    """정리 단계 실패(사유 코드). 첫 인자는 리터럴로만 -- tests/test_reason_codes_coverage.py 의 AST 추출기가 본다."""

    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


def _purge_script(root: str = ARTIFACT_MOUNT) -> str:
    """purge 파드의 셸 본문. 인자 root 는 경로 **상수**다 -- tests/test_purge_script_sh.py 가 임시 디렉터리로 바꿔 끼우는
    자리일 뿐 DB·사용자 값은 절대 들어오지 않는다(이름은 "$@" positional 로만).

    순서가 계약이다: (1) trash 가 심링크거나 디렉터리가 아니면 아무것도 안 하고 exit 3. (2) **모든 이름을 먼저** 검사
    (32자 소문자 hex 만 -- '..'·'a/b'·빈 문자열·대문자 거부) -- 하나라도 틀리면 아무것도 지우지 않고 exit 2(변조된
    목록의 앞쪽 이름만 지워지는 부분 실행이 없다). (3) `rm -rf --one-file-system -- ./<이름>`: GNU rm 은 fts 물리
    순회(심링크를 따라가지 않음, openat/unlinkat)라 trash 안의 심링크는 링크만 지워지고, --one-file-system 이 마운트
    경계를 넘지 않는다. 없는 이름은 조용히 지나간다(멱등).

    (3) 의 rm 실패는 **그 이름에서 멈추지 않는다**(2026-10-09 검증 지적): set -e 로 첫 실패에서 끝내면 정렬상 그 뒤의
    이름은 시도조차 안 되고, 컨트롤러의 FS 기반 실패 귀속이 같은 파드에 실린 무고한 행까지 purge_pod_failed 로 몰아
    같은 백오프로 영영 함께 묶인다(지울 수 없는 항목 하나 -- 손상된 dirfrag 의 EIO 등 -- 가 같이 실린 요청을 모두
    세운다). 그래서 이름마다 끝까지 시도하고 하나라도 실패했으면 마지막에 exit 1(DMS_PURGE_INCOMPLETE, OK 마커 없음)
    -- 실패 귀속은 실제로 trash 에 남은 이름의 행에만 떨어진다(request_purger._reap)."""
    return "\n".join([
        "set -eu",
        f"T={root}/{TRASH}",   # 이름의 단일 출처는 artifact_trash.TRASH
        'if [ -L "$T" ] || [ ! -d "$T" ]; then echo DMS_PURGE_NO_TRASH; exit 3; fi',
        'for n in "$@"; do',
        '  case "$n" in ""|*[!0-9a-f]*) echo DMS_PURGE_BAD_NAME; exit 2;; esac',
        '  [ "${#n}" -eq 32 ] || { echo DMS_PURGE_BAD_NAME; exit 2; }',
        "done",
        'cd -P "$T"',
        "rc=0",
        'for n in "$@"; do',
        '  rm -rf --one-file-system -- "./$n" || rc=1',   # `|| ` 안의 실패는 set -e 를 발화하지 않는다
        "done",
        'if [ "$rc" -ne 0 ]; then echo DMS_PURGE_INCOMPLETE; exit 1; fi',
        "echo DMS_PURGE_OK",
    ])


PURGE_SCRIPT = _purge_script()


def purge_pod_name(names) -> str:
    """결정적 이름 -- 같은 이름 집합이면 같은 파드(AlreadyExists = 이전 틱이 만든 그 파드)."""
    digest = hashlib.sha256(",".join(sorted(names)).encode()).hexdigest()[:12]
    return f"{PURGE_POD_PREFIX}{digest}"


def build_purge_pod(*, names, base, nodes, image, namespace) -> dict:
    """trash 이름들을 지우는 purge 파드 매니페스트(순수 함수). 입력을 믿지 않는다 -- 이름은 32자 hex 1..100개(중복은
    접는다), base 는 절대 경로, nodes 는 비어 있지 않은 문자열 목록, image 는 비어 있지 않은 문자열. 어기면 ValueError.

    securityContext 근거(ARCHITECTURE §7 규칙 12 -- cap 을 주면 이유를 여기 적는다):
      DAC_OVERRIDE  요청자 소유 0755 phase 와 요청자가 만든 0000 하위 디렉터리를 탐색·삭제한다(디렉터리 r/w/x 우회).
      FOWNER        요청자가 하위 디렉터리에 sticky 를 걸어 둔 경우 남의 항목 unlink.
      그 밖(CHOWN·MKNOD·SYS_ADMIN …)은 주지 않는다. runAsUser 0 이지만 allowPrivilegeEscalation false·읽기 전용 루트 fs.
    네임스페이스는 PSA privileged(deploy/k8s/00-namespace.yaml)이고 DAC_OVERRIDE·FOWNER 는 baseline 허용 cap 이다.
    볼륨은 base 하나뿐(hostPath type Directory -- 없으면 마운트 실패로 Pending, 대기 상한이 회수한다)."""
    names = list(names or [])
    for n in names:                       # 정렬 전에 -- 비문자열이 섞이면 sorted 가 TypeError 다
        if not isinstance(n, str) or not JOB_ID_RE.fullmatch(n):
            raise ValueError(f"invalid purge name: {n!r}"[:200])
    names = sorted(set(names))
    if not names:
        raise ValueError("purge pod needs at least one name")
    if len(names) > MAX_PURGE_NAMES:
        raise ValueError(f"too many purge names: {len(names)} > {MAX_PURGE_NAMES}")
    if not isinstance(base, str) or not base.startswith("/") or "\x00" in base:
        raise ValueError(f"invalid artifact base: {base!r}"[:200])
    nodes = sorted({n for n in (nodes or []) if isinstance(n, str) and n})
    if not nodes:
        raise ValueError("purge pod needs at least one eligible node")
    if not isinstance(image, str) or not image:
        raise ValueError("purge pod needs an image")
    return {
        "apiVersion": "v1", "kind": "Pod",
        "metadata": {"name": purge_pod_name(names), "namespace": namespace,
                     "labels": {PURGE_LABEL: "1"}},
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 5,
            "activeDeadlineSeconds": PURGE_POD_DEADLINE_SECONDS,
            "affinity": {"nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [{"matchExpressions": [
                    {"key": "kubernetes.io/hostname", "operator": "In", "values": nodes}]}]}}},
            "containers": [{
                "name": "purge", "image": image,
                "command": ["sh", "-c", PURGE_SCRIPT, "sh", *names],
                "securityContext": {
                    "runAsUser": 0, "runAsGroup": 0,
                    "allowPrivilegeEscalation": False,
                    "readOnlyRootFilesystem": True,
                    "capabilities": {"drop": ["ALL"], "add": ["DAC_OVERRIDE", "FOWNER"]}},
                "resources": {"requests": {"cpu": "10m", "memory": "32Mi"},
                              "limits": {"memory": "256Mi"}},
                "volumeMounts": [{"name": "dms-artifact-base", "mountPath": ARTIFACT_MOUNT,
                                  "mountPropagation": "HostToContainer"}],
            }],
            "volumes": [{"name": "dms-artifact-base",
                         "hostPath": {"path": base, "type": "Directory"}}],
        },
    }


def parse_ref(ref, job_id) -> "list | None":
    """phase ref → [(kind, name)] -- **그 job_id 의 DMS 명명**일 때만. 아니면 None(지우지 않는다).
      pod/dms-preflight-<job12>-…        단일 preflight Pod
      pods/<a>,<b>                       nsync 복합 preflight -- 모든 이름이 dms-preflight-<job12>- 로 시작
      vcjob/dms-<op>-<phase>-<job12>     Volcano Job(op ∈ scan|sync|rm, phase ∈ preview|execution)
    job12 접두만 보는 이유: 이름이 그 이상을 담지 않는다(execution_manifests). 그래서 이 판정은 **모양 거르기**일 뿐
    소유 확인이 아니다 -- 접두가 같은 다른 잡의 객체도 통과한다. 지우는 쪽(PurgeRunner.sweep)은 ref 이름으로 지우지
    않고 전체 job_id 라벨·소유 vcjob 으로 확인한 객체만 지운다."""
    if not isinstance(ref, str) or not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        return None
    prefix, sep, rest = ref.partition("/")
    if not sep or not rest:
        return None
    job12 = job_id[:12]
    if prefix == "vcjob":
        m = _VCJOB_RE.fullmatch(rest)
        return [("Job", rest)] if m and m.group(3) == job12 else None
    if prefix == "pod":
        names = [rest]
    elif prefix == "pods":
        names = rest.split(",")
    else:
        return None
    for n in names:
        if (len(n) > 253 or not _POD_NAME_RE.fullmatch(n)
                or not n.startswith(f"dms-preflight-{job12}-")):
            return None
    return [("Pod", n) for n in names]


def _get(obj, *keys):
    for k in keys:
        if isinstance(obj, dict) and k in obj:
            return obj[k]
    return None


# 실 클라이언트 get 은 Pod 를 to_dict()(snake_case), vcjob 을 원시 JSON(camelCase)으로 준다 -- 둘 다 읽는다.
def _labels(obj) -> dict:
    labels = _get(_get(obj, "metadata") or {}, "labels")
    return labels if isinstance(labels, dict) else {}


def _uid(obj) -> "str | None":
    uid = _get(_get(obj, "metadata") or {}, "uid")
    return uid if isinstance(uid, str) and uid else None


def _owner_job(obj) -> "tuple[str | None, str | None]":
    """파드의 소유 vcjob (name, uid) -- ownerReferences 의 kind Job(Volcano 가 단다, 실측 2026-10-09). 없으면
    (None, None), uid 모름은 None(모름을 일치로 접지 않는다 -- 호출자가 이름·라벨로 판정한다)."""
    refs = _get(_get(obj, "metadata") or {}, "owner_references", "ownerReferences")
    for ref in refs if isinstance(refs, list) else []:
        if isinstance(ref, dict) and ref.get("kind") == "Job":
            name, uid = ref.get("name"), ref.get("uid")
            return (name if isinstance(name, str) and name else None,
                    uid if isinstance(uid, str) and uid else None)
    return None, None


def _created_epoch(obj) -> "float | None":
    """파드 생성 시각(epoch). 실 클라이언트는 to_dict()(snake_case, datetime), 테스트·원시 JSON 은 camelCase 문자열 --
    둘 다 읽는다. 모르면 None(대기 상한을 적용하지 않는다 -- 모름을 0 으로 접지 않는다)."""
    meta = _get(obj, "metadata") or {}
    ts = _get(meta, "creation_timestamp", "creationTimestamp")
    if isinstance(ts, datetime):
        return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).timestamp()
    if isinstance(ts, str):
        try:
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            return None
    return None


def _names_from_pod(obj) -> "list | None":
    """purge 컨테이너 command 의 positional 이름들(["sh","-c",<script>,"sh",*names]). 모양이 다르면 None(모름)."""
    containers = _get(_get(obj, "spec") or {}, "containers") or []
    for c in containers:
        if isinstance(c, dict) and c.get("name") == "purge":
            cmd = c.get("command")
            if not isinstance(cmd, list) or len(cmd) < 4 or cmd[:2] != ["sh", "-c"]:
                return None
            names = cmd[4:]
            if not all(isinstance(n, str) and JOB_ID_RE.fullmatch(n) for n in names):
                return None
            return list(names)
    return None


class PurgeRunner:
    """실 클러스터 구현(execution_backend == volcano). k8s 는 execution_volcano.KubernetesClient(같은 K8sClient 계약 --
    list_vcjob_briefs 의 uid 와 get 의 metadata.labels·uid·ownerReferences 로 소유를 확인한다). job_image 는 str 또는 0-인자 callable(호출 시점 해석 -- 포탈 릴리스의 job-image 가 재시작 없이
    반영, VolcanoExecutionAdapter 와 같은 계약). purge 파드 이미지는 잡 이미지(Debian bookworm + GNU coreutils)다."""

    def __init__(self, k8s, *, namespace, job_image, clock=None):
        self._k8s = k8s
        self._ns = namespace
        self._job_image_fn = job_image if callable(job_image) else (lambda: job_image)
        self._clock = clock or (lambda: datetime.now(timezone.utc).timestamp())

    # ---- 잡 객체 회수 ----

    @staticmethod
    def ref_belongs_to_job(ref, job_id) -> bool:
        return parse_ref(ref, job_id) is not None

    def _live(self, job_id, vcjob_names) -> dict:
        """지금 있는, **이 잡(전체 job_id)의 것으로 확인된** 객체 {(kind, name): deleting}.

        - Pod(preflight·worker)·vcjob: 라벨 `dms.io/job-id=<전체 id>` 셀렉터 -- 셀렉터 자체가 전체 id 일치다.
        - launcher: dms 라벨이 없어 `volcano.sh/job-name in (후보)` 로 찾는다. 후보 = 기록된 ref 의 vcjob ∪ 라벨로 찾은
          vcjob ∪ **이 잡의 결정적 vcjob 이름 6개**(dms-<scan|sync|rm>-<preview|execution>-<job12>) -- vcjob 은 이미
          지워졌는데(앞 틱의 우리 삭제·TTL) GC 중인 launcher 가 남은 경우, 그 vcjob 이 phase_refs 에 없었으면(제출 직후
          크래시) 이름을 알 길이 이것뿐이다. 후보 이름은 job_id[:12] 만 담으므로 찾은 파드마다 _launcher_is_ours 로
          소유를 확인한다(접두가 같은 다른 잡의 launcher·worker 는 남의 것 -- 지우지도 세지도 않는다)."""
        sel = f"dms.io/job-id={job_id}"
        objs = {}
        for p in self._k8s.list_pod_briefs(self._ns, sel):
            objs[("Pod", p["name"])] = p.get("deleting") is True
        ours = {}                                    # 라벨로 확인한 vcjob 이름 -> uid(모르면 None)
        for v in self._k8s.list_vcjob_briefs(self._ns, sel):
            objs[("Job", v["name"])] = v.get("deleting") is True
            ours[v["name"]] = v.get("uid") if isinstance(v.get("uid"), str) and v.get("uid") else None
        candidates = sorted(set(vcjob_names) | set(ours) | {
            f"dms-{op}-{phase}-{job_id[:12]}" for op in ("scan", "sync", "rm") for phase in ("preview", "execution")})
        for p in self._k8s.list_pod_briefs(self._ns, f"volcano.sh/job-name in ({','.join(candidates)})"):
            key = ("Pod", p["name"])
            if key not in objs and self._launcher_is_ours(p["name"], job_id, ours):
                objs[key] = p.get("deleting") is True
        return objs

    def _launcher_is_ours(self, name, job_id, ours) -> bool:
        """vcjob 이름 후보로 찾은 파드가 이 잡의 것인가. 파드를 **나열한 뒤** 다시 조회한다 -- 파드는 소유 vcjob 이 생긴
        다음에야 생기므로, 그 뒤의 조회는 살아 있는 주인을 놓치지 않는다(「vcjob 없음」으로 오판해 살아 있는 잡의
        launcher 를 지우는 경합이 없다).
          자기 dms.io/job-id 라벨이 있으면(worker)  그 라벨이 판정한다.
          소유 vcjob 이 이 잡의 라벨 vcjob(uid 일치 또는 uid 모름)   우리 것.
          소유 vcjob 이 지금 없음 / 같은 이름의 다른 uid             고아(주인이 사라졌다 -- k8s GC 가 어차피 지운다.
                                                                  살아 있는 잡의 launcher 일 수 없다) -- 우리 것으로 센다.
          그 밖(소유 vcjob 이 남의 라벨·라벨 없음, 소유를 알 길 없음) 남의 것."""
        obj = self._k8s.get("Pod", name, self._ns)
        if obj is None:
            return False                             # 그 사이 사라졌다
        labels = _labels(obj)
        if "dms.io/job-id" in labels:
            return labels["dms.io/job-id"] == job_id
        owner_name, owner_uid = _owner_job(obj)
        vc_name = owner_name or labels.get("volcano.sh/job-name")
        if not isinstance(vc_name, str) or not vc_name:
            return False
        if vc_name in ours:
            known = ours[vc_name]
            if owner_uid is None or known is None or owner_uid == known:
                return True
        vc = self._k8s.get("Job", vc_name, self._ns)
        if vc is None:
            return True
        vc_uid = _uid(vc)
        if owner_uid is not None and vc_uid is not None and owner_uid != vc_uid:
            return True
        return _labels(vc).get("dms.io/job-id") == job_id

    def sweep(self, job_id, refs) -> dict:
        """한 잡의 남은 객체를 지우고 다시 센다. refs 는 호출자가 ref_belongs_to_job 으로 거른 것만(여기서도 다시 본다)
        -- ref 는 launcher 를 찾을 vcjob 이름 후보로만 쓰고, **이름으로 지우지 않는다**(지우는 것은 _live 가 전체
        job_id 로 확인한 객체뿐 -- 모듈 docstring 1). 반환 {"deleted": 살아 있다가 이번에 지운 객체 수, "remaining":
        다시 나열한 객체 수(Terminating 포함)}. k8s 오류는 PurgeError("purge_k8s_failed") -- 호출자가 백오프한다."""
        if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
            raise PurgeError("purge_k8s_failed", f"invalid job id {job_id!r}"[:200])
        ref_vcjobs = set()
        for ref in refs:
            for kind, name in parse_ref(ref, job_id) or []:
                if kind == "Job":
                    ref_vcjobs.add(name)
        try:
            # 1) 나열 먼저 -- 이번 틱에 「살아 있던」 것만 센다(Terminating 은 이미 지워지는 중). 404 는 클라이언트가
            #    삼킨다(멱등 -- vcjob 을 먼저 지우면 Volcano 가 그 파드를 지워 뒤 삭제가 404 일 수 있다).
            objs = self._live(job_id, ref_vcjobs)
            deleted = 0
            for (kind, name), deleting in sorted(objs.items()):
                if not deleting:
                    self._k8s.delete(kind, name, self._ns)
                    deleted += 1
            # 2) 다시 나열 -- 0 이어야 파일 단계로 간다.
            remaining = self._live(job_id, ref_vcjobs)
        except PurgeError:
            raise
        except Exception as exc:
            raise PurgeError("purge_k8s_failed", f"{type(exc).__name__}: {exc}"[:200]) from exc
        return {"deleted": deleted, "remaining": len(remaining)}

    # ---- purge 파드 ----

    def list_purge_pods(self) -> list:
        """[{name, phase, deleting, names(list|None), age_seconds(float|None), failed(bool), stuck(bool)}].
        failed = 끝났는데 Succeeded 가 아니거나, 기다려도 안 풀리는 대기 사유(이미지 pull 실패 등)거나, 대기 상한
        (PURGE_POD_PENDING_MAX_SECONDS)을 넘긴 Pending. stuck = 끝나지 않은(실행 중·종료 중·phase 모름) 파드가 생성 후
        PURGE_POD_STUCK_SECONDS 를 넘겼다(위 상수 주석). 나이는 **모든** 파드에 대해 조회한다 -- purge 파드는 언제나
        1~2개라 GET 비용이 무시할 만하고, 실행 중·종료 중 파드를 조회하지 않으면 영영 끝나지 않는 파드가 보이지 않는다.
        나이 모름(None)은 stuck·대기 상한을 적용하지 않는다(모름을 0 이나 초과로 접지 않는다)."""
        try:
            briefs = self._k8s.list_pod_briefs(self._ns, f"{PURGE_LABEL}=1")
            out = []
            for b in briefs:
                name = b.get("name")
                if not isinstance(name, str) or not name.startswith(PURGE_POD_PREFIX):
                    continue            # 라벨만 같은 남의 파드는 관리하지 않는다
                phase = b.get("phase") or ""
                deleting = b.get("deleting") is True
                obj = self._k8s.get("Pod", name, self._ns)
                if obj is None:
                    continue            # 그 사이 사라졌다
                created = _created_epoch(obj)
                age = None if created is None else max(self._clock() - created, 0.0)
                item = {"name": name, "phase": phase, "deleting": deleting, "names": _names_from_pod(obj),
                        "age_seconds": age, "failed": False, "stuck": False}
                if not deleting and phase == "Pending":
                    reasons = set((b.get("waiting_reason") or "").split(","))
                    item["failed"] = bool(reasons & _FATAL_WAITING) or (
                        age is not None and age > PURGE_POD_PENDING_MAX_SECONDS)
                elif not deleting and phase not in _ACTIVE_PHASES:
                    item["failed"] = phase != "Succeeded"
                finished = not deleting and (item["failed"] or phase == "Succeeded")
                item["stuck"] = not finished and age is not None and age > PURGE_POD_STUCK_SECONDS
                out.append(item)
            return out
        except Exception as exc:
            raise PurgeError("purge_k8s_failed", f"{type(exc).__name__}: {exc}"[:200]) from exc

    def create_purge_pod(self, *, base, names, nodes) -> str:
        """멱등 생성: 이름이 이름 집합에서 결정적이라 AlreadyExists 는 이전 틱이 만든 같은 파드다(build_runner 선례)."""
        try:
            manifest = build_purge_pod(names=names, base=base, nodes=nodes,
                                       image=self._job_image_fn(), namespace=self._ns)
        except ValueError as exc:
            raise PurgeError("purge_pod_failed", str(exc)[:200]) from exc
        name = manifest["metadata"]["name"]
        try:
            self._k8s.create(manifest)
        except Exception as exc:
            try:
                exists = self._k8s.get("Pod", name, self._ns) is not None
            except Exception:
                exists = False
            if not exists:
                raise PurgeError("purge_pod_failed", f"create: {type(exc).__name__}: {exc}"[:200]) from exc
        return name

    def delete_purge_pod(self, name) -> None:
        if not isinstance(name, str) or not name.startswith(PURGE_POD_PREFIX):
            raise PurgeError("purge_k8s_failed", f"not a purge pod: {name!r}"[:200])
        try:
            self._k8s.delete("Pod", name, self._ns)
        except Exception as exc:
            raise PurgeError("purge_k8s_failed", f"{type(exc).__name__}: {exc}"[:200]) from exc


class StubPurgeRunner:
    """클러스터가 없을 때(execution_backend != volcano -- 로컬·CI·e2e). 스텁 실행 어댑터의 ref 모양
    (`stub-<phase>-<job_id>`)만 「그 잡의 ref」로 받고, 회수할 객체는 언제나 0 이다. purge 파드는 기록만 하고
    Succeeded 로 보인다 -- **파일을 지우지 않는다**(src/dms 에는 삭제 코드를 두지 않는다, 규칙 7·13). 스텁 실행
    어댑터는 아티팩트를 쓰지 않으므로 보통 trash 가 비어 파드가 필요 없다. trash 에 무언가 있으면 그 행은
    purge_pod_failed 로 지연 표면화된다(조용한 성공으로 위장하지 않는다)."""

    def __init__(self):
        self.pods = {}           # name -> {"names", "base", "nodes"}
        self.swept = []          # [(job_id, refs)]
        self.deleted_pods = []

    @staticmethod
    def ref_belongs_to_job(ref, job_id) -> bool:
        return (isinstance(ref, str) and isinstance(job_id, str) and bool(JOB_ID_RE.fullmatch(job_id))
                and any(ref == f"stub-{phase}-{job_id}" for phase in PHASES))

    def sweep(self, job_id, refs) -> dict:
        self.swept.append((job_id, list(refs)))
        return {"deleted": 0, "remaining": 0}

    def list_purge_pods(self) -> list:
        return [{"name": name, "phase": "Succeeded", "deleting": False, "names": list(p["names"]),
                 "age_seconds": 0.0, "failed": False, "stuck": False} for name, p in self.pods.items()]

    def create_purge_pod(self, *, base, names, nodes) -> str:
        try:
            manifest = build_purge_pod(names=names, base=base, nodes=nodes, image="stub", namespace="stub")
        except ValueError as exc:
            raise PurgeError("purge_pod_failed", str(exc)[:200]) from exc
        name = manifest["metadata"]["name"]
        self.pods[name] = {"names": sorted(set(names)), "base": base, "nodes": list(nodes)}
        return name

    def delete_purge_pod(self, name) -> None:
        self.pods.pop(name, None)
        self.deleted_pods.append(name)
