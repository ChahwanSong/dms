"""Volcano/k8s 실행 어댑터. k8s 접근은 주입된 K8sClient 뒤, 아티팩트 읽기는 read_text 뒤."""
import hashlib
import json
import logging
import re
import threading
import time
from typing import Protocol

from .artifact_base import ARTIFACT_MOUNT, strip_scheme
from .execution import ExecStatus, ExecutionError, SubmitUndecided
from .execution_manifests import (ADOPTION_LABELS, SPEC_FINGERPRINT_ANNOTATION, build_preflight_pod,
                                  build_volcano_job)

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

# k8s 볼륨 이름 = RFC 1123 label(소문자·숫자·'-', 63자 이하, 양끝 영숫자). 2026-09-15
# 프로덕션 사고: 이름을 mount_path 에서 슬래시만 '-' 로 바꿔 만들어 /mgmt_storage 가
# "mgmt_storage"(밑줄) 로 남았고 apiserver 가 422(spec.volumes[0].name Invalid value)로
# 거부 -> 모든 잡이 preflight submit_failed. 테스트베드(/cephfs)에선 드러나지 않았다.
_VOLUME_NAME_MAX = 63
_VOLUME_NAME_RE = re.compile(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?$")


def volume_name(mount_path: str) -> str:
    """mount_path -> 결정적·RFC 1123 준수 볼륨 이름. 허용 밖 문자 연속은 '-' 하나로
    접고(밑줄·대문자·점·비ASCII 전부), 경로 sha256 앞 8자를 **항상** 덧붙인다 --
    슬러그만 쓰면 /data_1 과 /data-1 이 같은 이름으로 충돌해 한 파드 안에서 apiserver
    가 중복 이름을 거부한다. 길이 상한은 슬러그 쪽을 잘라 지킨다. 이름은 스펙 내부
    (volumes ↔ volumeMounts 짝) 에서만 쓰여 바깥 계약이 없다."""
    slug = re.sub(r"[^a-z0-9]+", "-", mount_path.lower()).strip("-")
    digest = hashlib.sha256(mount_path.encode()).hexdigest()[:8]
    slug = slug[:_VOLUME_NAME_MAX - len(digest) - 1].rstrip("-") or "root"
    name = f"{slug}-{digest}"
    assert _VOLUME_NAME_RE.fullmatch(name) and len(name) <= _VOLUME_NAME_MAX, name
    return name

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


class K8sAlreadyExists(Exception):
    """create 의 409 AlreadyExists -- 같은 이름의 객체가 이미 있다(2026-11-03 B3). 어댑터만 이것을 따로 받아 채택 판정으로
    간다(VolcanoExecutionAdapter._create_or_adopt). Exception 의 하위라 다른 호출자(build_runner·purge_runner 의 「있으면 성공」
    관용)는 종전처럼 일반 예외로 받는다."""


class K8sTransient(Exception):
    """create 가 「반영됐는지 모르게」 끊겼다(2026-11-03 B3 리뷰) -- 연결 끊김·타임아웃(상태 코드 없음)·status 0·429·5xx.
    요청이 etcd 에 반영되고 응답만 잃었을 수 있으므로 어댑터는 이름으로 다시 읽어 판정하고(있으면 409 와 같은 채택 판정),
    없으면 다음 틱에 다시 낸다(SubmitUndecided). 4xx(403·422 …)는 apiserver 가 판정을 내린 것이라 여기 오지 않고 원문 그대로
    submit_failed 다(2026-09-15 교훈). K8sAlreadyExists 처럼 Exception 하위라 다른 호출자는 일반 예외로 받는다."""


# 다시 읽어 보니 객체가 **없거나 삭제 중인** 제출(409·create 결과 모름 뒤)을 다음 틱으로 미루는 상한(초, 이름별 첫 관측부터). 넘기면 종전
# submit_failed -- 웹훅 장애 같은 지속 5xx 가 잡을 영원히 Pending 에 붙잡지 않게. 객체가 있는지조차 모르는 경우(다시 읽기 실패)는
# 상한 없이 미룬다: 접으면 객체는 돌고 잡만 FAILED 인 B3 그대로이고, 폴링도 같은 GET 실패를 상한 없이 step_error 로 남긴다.
SUBMIT_UNDECIDED_WINDOW_SECONDS = 300

# 잡 id 를 라벨 셀렉터에 넣기 전 형식 검사(DB 신뢰 경계 -- 쉼표·연산자가 섞이면 셀렉터가 넓어진다). k8s 라벨 값 문법.
_LABEL_VALUE_RE = re.compile(r"[A-Za-z0-9]([-A-Za-z0-9_.]{0,61}[A-Za-z0-9])?")


def _existing_meta(existing) -> dict:
    meta = existing.get("metadata") if isinstance(existing, dict) else None
    return meta if isinstance(meta, dict) else {}


def _being_deleted(meta) -> bool:
    # vcjob 은 원시 JSON(camelCase), Pod 은 kubernetes 모델 to_dict()(snake_case) -- 둘 다 본다(adoption_problem docstring).
    return meta.get("deletionTimestamp") is not None or meta.get("deletion_timestamp") is not None


def _label_problem(desired: dict, meta: dict) -> "str | None":
    want_labels = (desired.get("metadata") or {}).get("labels") or {}
    have_labels = meta.get("labels") if isinstance(meta.get("labels"), dict) else {}
    for key in ADOPTION_LABELS:
        want = want_labels.get(key)
        if want is None or have_labels.get(key) != want:
            return f"라벨 {key} 불일치"
    return None


def adoption_problem(desired: dict, existing) -> "str | None":
    """409 뒤 다시 읽은 객체를 이번 제출로 채택해도 되는가(2026-11-03 B3). None = 채택, 문자열 = 채택하지 않는 이유.

    전부 만족해야 채택한다: (1) 객체가 있다(409 와 GET 사이에 지워졌으면 아니다), (2) 삭제 중이 아니다(deletionTimestamp --
    취소·회수가 지우는 중인 객체를 붙이면 곧 사라져 폴링이 FAILED 로 읽는다), (3) 라벨 dms.io/job-id(**전체** id)·phase·tool 이
    지금 지은 매니페스트와 같다(이름은 job_id 앞 12자뿐이라 앞자리가 같은 다른 잡의 객체를 이름으로 붙이면 남의 결과를 내 잡에
    기록한다), (4) 지문 annotation 이 지금 지은 매니페스트의 지문과 같다(execution_manifests.manifest_fingerprint -- 같은 잡·
    단계라도 경로·신원·노드·프로세스 수가 바뀌었으면 옛 객체는 다른 일을 한다). 지문이 없는 옛 객체(이 변경 전 코드가 만든 것)도
    채택하지 않는다.

    지문은 **DMS 가 지은 객체끼리의 대조**다 -- 살아 있는 spec 을 다시 해시하지 않으므로(웹훅이 기본값을 채워 넣어 할 수도 없다)
    annotation 을 베낀 객체는 무엇을 하든 통과한다. 위조 방어가 아닌 이유는 RBAC 다: dms 네임스페이스의 pods·jobs(volcano)
    create 는 dms-controller SA 뿐이고 patch 는 어느 DMS SA 에도 없다(deploy/k8s/10-rbac.yaml) -- 그 권한이 있으면 이미 root
    hostPath 파드를 직접 띄울 수 있다. RBAC 를 넓히면 이 전제부터 다시 본다.

    실 클라이언트의 모양 차이: Pod GET 은 kubernetes 모델의 to_dict()(snake_case -- metadata.deletion_timestamp), vcjob 은
    custom objects API 의 원시 JSON(camelCase -- metadata.deletionTimestamp)이다. 라벨·annotation 은 사전이라 키가 그대로다."""
    if not isinstance(existing, dict):
        return "다시 읽을 때 없음(409 뒤 사라짐)"
    meta = _existing_meta(existing)
    if _being_deleted(meta):
        return "삭제 중(deletionTimestamp)"
    problem = _label_problem(desired, meta)
    if problem is not None:
        return problem
    want_fp = ((desired.get("metadata") or {}).get("annotations") or {}).get(SPEC_FINGERPRINT_ANNOTATION)
    have_ann = meta.get("annotations") if isinstance(meta.get("annotations"), dict) else {}
    if want_fp is None or have_ann.get(SPEC_FINGERPRINT_ANNOTATION) != want_fp:
        return f"지문 불일치({SPEC_FINGERPRINT_ANNOTATION})"
    return None


def owned_by_this_phase(desired: dict, existing) -> bool:
    """채택하지 않은 기존 객체가 그래도 **이 잡·이 단계의 것**인가(2026-11-03 B3 리뷰) -- 라벨 셋(전체 job_id·phase·tool)이 전부
    같고 삭제 중이 아니다. 그렇다면 지문만 다른(배포 경계의 옛 객체·재클램프 전 객체) 우리 객체가 ref 없이 돌고 있다는 증거라,
    잡을 「시작 못 함」으로 끝내면서 그대로 두지 않고 stepper 가 회수한다(ExecutionError.strays -- purge 의 「전체 job_id 로
    확인된 객체만 지운다」 선례). 라벨이 하나라도 다르면 남의 것이라 건드리지 않는다."""
    if not isinstance(existing, dict):
        return False
    meta = _existing_meta(existing)
    return not _being_deleted(meta) and _label_problem(desired, meta) is None


def _finished_recheck(desired: dict, existing) -> "str | None":
    """채택 판정을 통과한 기존 객체가 **이미 끝난 실행 전 재점검 파드**(phase exec_preflight, Pod phase Succeeded·Failed)면 그
    phase, 아니면 None(2026-11-03 리뷰 2회차 [3]). 그런 파드는 채택하지 않는다.

    왜: exec_preflight 는 실행 직전 TOCTOU 재점검이고, 첫 실행 관문(stepper GATE_START -- 겹치는 작업이 끝날 때까지 보류)
    **뒤에** 돌아야 의미가 있다. ref 기록이 끊긴 틱의 재점검 파드는 관문 **앞**에서 만들어졌고, 기록이 없으니 그 잡은 겹침 판정에서
    단계 0(재점검 전)으로 보여 commit 순서가 앞선 겹치는 rm 이 먼저 돌 수 있다 -- 보류가 풀린 뒤 그 파드(상대 실행 **전**에 끝난
    결과)를 채택하면 「목적지가 파일로 바뀜」 같은 판정을 건너뛰고 실행으로 간다. 아직 도는 파드는 관문 뒤의 상태를 볼 수 있어
    채택한다. 초기 preflight(phase preflight)는 그대로 채택한다 -- 그 결과는 설계상 보류 뒤에도 쓰이고(scan 의 실행 관문), sync·rm
    은 실행 전에 이 재점검을 다시 거친다(끝난 파드를 다시 돌리는 비용만 늘린다). vcjob(미리보기·실행)은 끝났어도 채택한다 --
    실제로 돈 결과라 다시 돌리면 안 된다."""
    want_phase = ((desired.get("metadata") or {}).get("labels") or {}).get("dms.io/phase")
    if desired.get("kind") != "Pod" or want_phase != "exec_preflight":
        return None
    status = existing.get("status") if isinstance(existing.get("status"), dict) else {}
    phase = status.get("phase")
    return phase if phase in ("Succeeded", "Failed") else None


def _ref_of(manifest) -> str:
    return ("pod/" if manifest["kind"] == "Pod" else "vcjob/") + manifest["metadata"]["name"]


def create_error_class(exc) -> "str | None":
    """실 클라이언트 create 예외의 갈래(2026-11-03 B3): "exists"(409) | "transient"(반영됐는지 모름) | None(apiserver 판정 --
    원문 그대로 submit_failed). status 속성으로 판별하는 이유는 get_workload 와 같다(.venv 에 kubernetes 가 없어 ApiException
    타입을 테스트가 만들 수 없다). 상태 코드가 없는 예외는 **전송 오류 타입일 때만** transient 다(OSError 계열 -- 연결 거부·
    타임아웃, urllib3.exceptions.HTTPError 계열 -- 재시도 소진·프로토콜·읽기 타임아웃): 매니페스트 직렬화의 TypeError 같은
    코드 결함을 「모름」으로 미루면 상한까지 헛돈다."""
    status = getattr(exc, "status", None)
    if isinstance(status, int):
        if status == 409:
            return "exists"
        if status == 0 or status == 429 or status >= 500:
            return "transient"
        return None
    if isinstance(exc, OSError):
        return "transient"
    try:
        import urllib3
    except ImportError:          # pragma: no cover - 실 클라이언트에는 늘 있다(kubernetes 의존)
        return None
    return "transient" if isinstance(exc, urllib3.exceptions.HTTPError) else None


class K8sClient(Protocol):
    def create(self, manifest: dict) -> None:
        """이름이 이미 있으면(409) K8sAlreadyExists, 반영됐는지 모르게 끊기면(연결·타임아웃·0·429·5xx) K8sTransient 를 던진다
        (2026-11-03 B3) -- 그 밖의 실패(4xx 판정)는 원래 예외 그대로."""
        ...
    def get(self, kind: str, name: str, namespace: str) -> "dict | None": ...
    def delete(self, kind: str, name: str, namespace: str) -> None:
        """대상이 이미 없으면 조용히 무시(404 삼킴) — 멱등 종료 계약."""
        ...
    def read_pod_log(self, name: str, namespace: str) -> str: ...
    # 슬라이스 25: vcjob 파드는 이름을 모르므로 라벨로 찾는다. 구현은 이미 있었지만
    # Protocol 에 없어 계약 문서가 실제 의존을 숨기고 있었다(설계 §1-4). 각 항목의
    # "deleting"(2026-10-08) = deletionTimestamp 있음(Terminating) -- 요청 삭제 정리
    # (purge_runner)가 「아직 남은 객체」에 Terminating 도 세려고 쓴다.
    def list_pod_briefs(self, namespace: str, label_selector: str) -> list: ...
    # 요청 삭제 정리(2026-10-08, purge_runner.sweep): 라벨로 vcjob 을 찾는다 --
    # [{"name", "deleting", "uid"}]. phase_refs 에 없는 vcjob(제출 직후 크래시)도 회수해야 한다.
    # uid 는 launcher 파드의 ownerReference 와 대조해 「이 잡의 vcjob 의 launcher」를 전체
    # job_id 로 확인하는 데 쓴다(이름은 job_id[:12] 만 담는다 -- 2026-10-09 검증 지적).
    def list_vcjob_briefs(self, namespace: str, label_selector: str) -> list: ...


_CONTROL_PLANE_LABELS = ("node-role.kubernetes.io/control-plane",
                         "node-role.kubernetes.io/master")


def node_addresses_from(node_list: dict) -> list:
    """V1NodeList.to_dict() -> [{"name", "ip", "control_plane"}]. 순수 함수(테스트용).
    InternalIP 가 없는 노드는 ip=None 으로 남긴다(모름 ≠ 없음 -- 지어내지 않는다)."""
    out = []
    for item in (node_list or {}).get("items") or []:
        meta = item.get("metadata") or {}
        labels = meta.get("labels") or {}
        ip = None
        for addr in ((item.get("status") or {}).get("addresses") or []):
            if addr.get("type") == "InternalIP" and addr.get("address"):
                ip = addr["address"]
                break
        out.append({"name": meta.get("name"), "ip": ip,
                    "control_plane": any(k in labels for k in _CONTROL_PLANE_LABELS)})
    return out


class VolcanoExecutionAdapter:
    def __init__(self, k8s, *, job_image, namespace, storages_lookup, read_text,
                 artifact_base, monotonic=None):
        self._k8s = k8s
        self._monotonic = monotonic or time.monotonic
        # job_image 도 artifact_base 와 같은 계약(슬라이스 35): str(고정값) 또는
        # 0-인자 callable(호출 시점 해석). 생성자 캡처를 피해야 포탈 릴리스의
        # job-image 오버라이드가 재시작 없이 다음 잡부터 반영된다.
        self._job_image_fn = (job_image if callable(job_image)
                              else (lambda: job_image))
        self._namespace = namespace
        self._storages = storages_lookup
        self._read_text = read_text
        # summary 경로 fallback 재구성용. str(고정값) 또는 0-인자 callable(호출
        # 시점 해석) 둘 다 받는다: 설계 §2.1 이 생성자 캡처를 금지하는 이유는
        # §1-7 -- base 변경 후 컨트롤러가 재시작하면 in-flight 잡의 summary 를 옛
        # 경로에서 찾게 된다. wiring 은 resolve 클로저를 넘기고, 고정 문자열을
        # 넘기는 기존 테스트·스텁 조립은 람다로 감싸 그대로 산다.
        self._artifact_base_fn = (artifact_base if callable(artifact_base)
                                  else (lambda: artifact_base))
        self._summary_paths = {}   # ref -> artifact summary.json path (in-memory 빠른 경로)
        self._adopted = {}         # ref -> [채택한 객체 이름](B3 -- take_adopted)
        # 객체 이름 -> 「create 결과 모름 + 다시 읽으니 없음」의 첫 관측 시각(SUBMIT_UNDECIDED_WINDOW_SECONDS). 판정이 나면 지운다.
        self._undecided_since = {}

    def _volumes(self, spec, *, role=None):
        # 데이터 스토리지 mount_path + 아티팩트 base 를 수집해 hostPath 볼륨으로.
        # role: nsync(preflight·미리보기·실행 전부)는 소스/목적지가 disjoint 노드라 각 role
        # 파드에는 해당 스토리지만 마운트해야 한다 — 반대편 스토리지는 그 노드에 없어
        # type:Directory hostPath 가 FailedMount 로 gang 을 못 세운다(2026-10-22 dmsk8s 실증:
        # 실행 vcjob 이 양쪽을 붙여 ContainerCreating 에 묶였다 -- 옛 테스트베드는 반대편
        # 마운트 루트가 빈 디렉터리로 남아 있어 가려졌다). role="launcher" 는 데이터 스토리지
        # 없이 아티팩트 base 만(nsync 런처는 데이터 경로를 만지지 않는다 -- 런처의 데이터
        # 접근은 drm 봉쇄 재확인뿐이고 그건 primary 런처다). role=None 은 양쪽(코로케이션
        # dsync, scan·rm 의 primary)을 마운트.
        if role == "launcher":
            snames = []
        elif spec.operation == "sync":
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
        # 다른 경로가 상위(ancestor)면 하위 경로는 커버되므로 생략 — 중첩 마운트 방지.
        # 후행 슬래시로 "/cephfs" 가 "/cephfs-third" 를 잘못 삼키지 않게 함.
        minimal = []
        for p in mount_paths:
            covered = any(p != q and p.startswith(q.rstrip("/") + "/")
                          for q in mount_paths)
            if covered or p in minimal:
                continue
            minimal.append(p)
        volumes = [{"name": volume_name(mp),
                    "hostPath": {"path": mp}, "mountPath": mp} for mp in minimal]
        # 아티팩트 base 는 **항상 전용 볼륨**으로 ARTIFACT_MOUNT 에(2026-09-09,
        # artifact_base.ARTIFACT_MOUNT 주석). 예전엔 스토리지 마운트가 base 의 상위면
        # 생략했는데(중첩 방지), 그러면 파드 안 경로가 <storage>/.../artifacts 라
        # 요청자 uid 도구가 공용 디렉터리(base 의 부모)를 통과해야 했다 -- 그 부모를
        # root:root 770 으로 잠그면 rank.sh 실행·리포트 쓰기가 EACCES 로 죽는다.
        # 전용 마운트는 마운트 루트가 base 자체라 부모 권한을 보지 않는다. hostPath 는
        # 접두사만 벗긴 base(설계 §2.2: 전체 치환은 경로 중간 file:// 까지 지운다).
        volumes.append({"name": "dms-artifact-base",
                        "hostPath": {"path": strip_scheme(spec.artifact_base)},
                        "mountPath": ARTIFACT_MOUNT})
        return volumes

    # phases that run as a single preflight Pod (not a Volcano Job): the initial
    # preflight AND the post-confirm re-validation. Both must route to
    # build_preflight_pod, else the re-check would be built as a Volcano Job with
    # an "exec_preflight" phase in its name (underscore -> invalid -> 422).
    _PREFLIGHT_PHASES = ("preflight", "exec_preflight")

    def _preflight_pod(self, spec, node, role):
        return build_preflight_pod(
            spec, job_image=self._job_image_fn(), namespace=self._namespace,
            volumes=self._volumes(spec, role=role), node=node, role=role)

    def _create_or_adopt(self, manifest, adopted: list) -> None:
        """create 하고, 409 AlreadyExists 면 그 객체를 다시 읽어 채택 판정한다(2026-11-03 B3 -- adoption_problem).

        왜: 이름이 결정적이고 ref 는 create **뒤에** DB 에 남는다(stepper._record_ref). 그 사이 DB 가 끊기면(장애조치·컨트롤러
        재시작) 잡은 이전 상태로 남아 다음 틱이 같은 이름으로 다시 만든다 -- 예전엔 그 409 가 submit_failed 로 접혀 잡이
        FAILED/REJECTED 로 잘못 기록되고, 객체는 ref 없이 돌아 아무도 추적·회수하지 않았다(pod_gc 는 phase_refs 만 본다).
        채택하면 새로 만든 것과 **같은 ref** 를 돌려주므로 stepper 는 차이를 모른다 -- 틱이 몇 번 반복돼도 같은 이름·같은 ref 다
        (리스가 풀린 두 컨트롤러의 겹침은 stepper 가 phase_refs 빈 자리 CAS 로 한쪽만 진행시킨다 -- data_jobs.record_phase_ref).
        409 로 채택한 이름만 adopted 에 넣는다 -- stepper 가 ref 를 기록한 뒤 이벤트(submit_adopted, 포탈 단계 행 주석 「이전 제출이
        만들고 기록하지 못한 객체」)로 남긴다(take_adopted). create 결과 모름 뒤 다시 읽어 채택한 것은 넣지 않는다(2026-11-03 리뷰
        2회차 [4]): 대개 **이번** create 가 반영되고 응답만 잃은 것이라 그 문구가 거짓이고, apiserver 한 번 끊김마다 사용자 화면에
        주석이 뜬다. 앞 틱의 객체 + 이번 5xx 인 경우와 구별할 수 없어 그때도 주석은 없다(로그만 -- 끊김 자체는 앞 틱의 step_error 가
        이미 남겼다).

        판정(2026-11-03 리뷰 반영):
          채택                              -> 반환(같은 ref)
          라벨·지문 불일치                   -> ExecutionError submit_failed(종전). 라벨 셋은 같고 지문만 다르면(이 잡·단계의
                                               객체 -- owned_by_this_phase) strays 에 실어 stepper 가 회수한다.
          이미 끝난 실행 전 재점검 파드       -> SubmitUndecided(strays) -- stepper 가 지우고 다음 틱에 새로 낸다(_finished_recheck)
          create 결과 모름(K8sTransient)     -> 같은 이름을 다시 읽어 위와 같이 판정(응답만 잃은 create 는 채택된다)
          다시 읽기 실패                     -> SubmitUndecided(모름 -- 다음 틱 재시도, 상한 없음)
          다시 읽으니 없음·삭제 중           -> SubmitUndecided, SUBMIT_UNDECIDED_WINDOW_SECONDS 를 넘기면 submit_failed.
                                               삭제 중은 「곧 없음」이다 -- 다 지워지면 다음 틱의 create 가 새로 만든다(없음과
                                               같은 결과를 몇 초 늦게 낼 뿐이라 종단으로 접지 않는다, 2026-11-03 리뷰 [2]).
                                               finalizer 가 걸려 안 사라지면 상한 뒤 종전 submit_failed.

        문구는 DMS 판정을 앞에, apiserver 원문(K8sTransient)은 따로 잘라 끝에 붙인다(2026-11-03 리뷰 2회차 [5]): 원문을 앞에 두고
        통째로 자르면 ApiException 의 긴 헤더(Audit-Id 등)가 판정 문구를 밀어내 「무엇을 왜 실패로 접었나」가 사라진다. 원문 보존
        (2026-09-15 교훈)은 그대로다 -- 판정이 원문을 자르지 않는다."""
        name = manifest["metadata"]["name"]
        try:
            self._k8s.create(manifest)
        except K8sAlreadyExists:
            head, origin, via = f"AlreadyExists {name}", "", "exists"
        except K8sTransient as exc:
            head, origin, via = f"create 결과 모름 {name}", f" -- 원문: {str(exc)[:160]}", "transient"
        else:
            self._undecided_since.pop(name, None)
            return
        try:
            existing = self._k8s.get(manifest["kind"], name, self._namespace)
        except Exception as exc:
            raise SubmitUndecided(
                f"{head}: 채택 판정 조회 실패 -- {type(exc).__name__}: {exc}"[:300] + origin) from exc
        if existing is None:
            self._undecided(name, f"{head}: 다시 읽을 때 없음", origin)
        if _being_deleted(_existing_meta(existing)):
            self._undecided(name, f"{head}: 삭제 중(deletionTimestamp) -- 사라지면 새로 만든다", origin)
        problem = adoption_problem(manifest, existing)
        self._undecided_since.pop(name, None)
        if problem is None:
            finished = _finished_recheck(manifest, existing)
            if finished is not None:
                raise SubmitUndecided(
                    f"{head}: 채택 안 함 -- 이미 끝난 실행 전 재점검 파드({finished}) -- 지우고 다음 틱에 다시 점검"[:300]
                    + origin, strays=[_ref_of(manifest)])
            if via == "exists":
                logger.warning("adopted existing %s %s/%s (ref was not recorded by an earlier tick)",
                               manifest["kind"], self._namespace, name)
                adopted.append(name)
            else:
                logger.info("create outcome unknown for %s %s/%s; re-read matches this submit -- adopted",
                            manifest["kind"], self._namespace, name)
            return
        if owned_by_this_phase(manifest, existing):
            raise ExecutionError(
                "submit_failed", f"{head}: 채택 안 함 -- {problem} -- 이 잡·단계의 객체(회수 대상)"[:200] + origin,
                strays=[_ref_of(manifest)])
        raise ExecutionError("submit_failed", f"{head}: 채택 안 함 -- {problem}"[:200] + origin)

    def _undecided(self, name, detail, origin=""):
        """객체가 없다고(또는 삭제 중이라고) 확인된 미결 제출: 상한 안이면 SubmitUndecided(다음 틱), 넘기면 종전 submit_failed.
        반환하지 않는다. origin(apiserver 원문 꼬리)은 판정 문구를 자른 **뒤** 붙인다(_create_or_adopt docstring)."""
        now = self._monotonic()
        first = self._undecided_since.setdefault(name, now)
        if now - first >= SUBMIT_UNDECIDED_WINDOW_SECONDS:
            self._undecided_since.pop(name, None)
            raise ExecutionError("submit_failed", f"{detail} ({int(now - first)}초 동안 재시도)"[:200] + origin)
        raise SubmitUndecided(detail[:300] + origin)

    def _note_submitted(self, ref, adopted) -> None:
        # 같은 ref 의 옛 항목을 늘 지운다 -- 남아 있으면 채택 없는 다음 제출이 거짓 채택 이벤트를 남긴다(take_adopted).
        self._adopted.pop(ref, None)
        if adopted:
            self._adopted[ref] = list(adopted)

    def take_adopted(self, ref) -> list:
        """ref 를 만들 때 409 로 채택한 객체 이름(없으면 [] -- create 결과 모름 뒤의 채택은 넣지 않는다, _create_or_adopt
        docstring). 꺼내면 지운다 -- stepper._record_ref 가 ref 를 기록하기 **전에** 한 번
        부르므로 기록이 실패해도 항목은 남지 않는다(다음 틱의 같은 제출이 다시 채택하고 그때 이벤트가 남는다). submit 이 매번
        같은 ref 의 옛 항목을 지우고 쓰므로(_note_submitted) 꺼내지 않은 항목이 다음 제출에 섞이지 않는다."""
        return self._adopted.pop(ref, [])

    def unrecorded_refs(self, job_id, recorded) -> list:
        """이 잡(라벨 dms.io/job-id = **전체** id)의 살아 있는 객체 중 recorded(phase_refs 값들)에 없는 것의 ref(2026-11-03 B3 리뷰).

        ref 기록이 끊긴 틱이 만든 객체는 다음 틱의 같은 제출이 채택해 잇지만, 다음 틱이 제출 **전에** 종단되면(_build_spec 의
        fail-closed -- 그 사이 노드 cordon·스토리지 결측·CPU 부족, 배치 취소) phase_refs 에 없어 아무도 못 치운다 --
        stepper._reclaim_unrecorded 가 이 목록을 terminate 한다. vcjob 은 라벨로, 파드는 preflight 파드만(라벨 dms.io/phase
        가 있는 것 -- vcjob 의 launcher·worker 는 phase 라벨이 없고 vcjob 을 지우면 함께 간다). 삭제 중인 것은 뺀다.
        job_id 가 라벨 값 문법이 아니면 아무것도 고르지 않는다(DB 신뢰 경계 -- 셀렉터가 넓어지지 않게)."""
        if not isinstance(job_id, str) or not _LABEL_VALUE_RE.fullmatch(job_id):
            return []
        pods, vcjobs = set(), set()
        for ref in recorded or ():
            prefix, _, rest = str(ref or "").partition("/")
            if prefix in ("pod", "pods"):
                pods.update(rest.split(","))
            elif prefix == "vcjob":
                vcjobs.add(rest)
        sel = f"dms.io/job-id={job_id}"
        out = []
        for v in self._k8s.list_vcjob_briefs(self._namespace, sel):
            n = v.get("name")
            if isinstance(n, str) and n and n not in vcjobs and v.get("deleting") is not True:
                out.append(f"vcjob/{n}")
        for p in self._k8s.list_pod_briefs(self._namespace, f"{sel},dms.io/phase"):
            n = p.get("name")
            if isinstance(n, str) and n and n not in pods and p.get("deleting") is not True:
                out.append(f"pod/{n}")
        return out

    def submit(self, spec) -> str:
        adopted = []
        try:
            if spec.phase in self._PREFLIGHT_PHASES:
                # nsync = disjoint source/destination pools ("primary" 없음). 소스
                # 노드에서는 목적지 쓰기를, 목적지 노드에서는 소스 읽기를 검증할 수
                # 없으므로 preflight를 양쪽 노드에 각각 띄운다(role src/dst). 복합
                # ref "pods/<src>,<dst>"로 반환 — poll/terminate가 둘 다 다룬다.
                # 채택은 파드별이다(B3): 앞 틱이 src 만 만들고 끊겼으면 src 는 채택, dst 는 새로 만든다.
                if "primary" not in spec.candidates and spec.candidates.get("source"):
                    src = self._preflight_pod(spec, spec.candidates["source"][0], "source")
                    dst = self._preflight_pod(
                        spec, spec.candidates["destination"][0], "destination")
                    done = []      # 이번 제출이 만들었거나 채택한 앞쪽 파드의 ref
                    try:
                        for pod in (src, dst):
                            self._create_or_adopt(pod, adopted)
                            done.append(_ref_of(pod))
                    except SubmitUndecided as exc:
                        # 모름이면 잡은 이전 상태에 남고 다음 틱이 앞쪽 파드를 채택한다 -- 지우지 않는다. 다만 뒤쪽이 이미 끝난
                        # 재점검이라 지우고 다시 내는 경우(strays 있음)는 앞쪽도 함께 지운다 -- 두 파드의 점검이 같은 시점이게
                        # (남기면 다음 틱엔 그것이 끝나 있어 또 한 틱을 지운다).
                        if exc.strays:
                            exc.strays = done + exc.strays
                        raise
                    except ExecutionError as exc:
                        # 잡은 곧 종단되고 ref 는 기록되지 않는다 -- 앞쪽 파드는 stepper 가 회수한다(ExecutionError.strays:
                        # 다른 stepper 가 이 단계를 이미 기록했는지 DB 로 본 뒤에. 어댑터는 DB 를 모르므로 직접 지우지 않는다).
                        exc.strays = done + exc.strays
                        raise
                    except Exception as exc:
                        raise ExecutionError("submit_failed", str(exc)[:200], strays=done) from exc
                    ref = "pods/" + ",".join(
                        [src["metadata"]["name"], dst["metadata"]["name"]])
                    self._note_submitted(ref, adopted)
                    return ref
                nodes = (spec.candidates.get("primary")
                         or spec.candidates.get("source") or ["dms-w1"])
                manifest = self._preflight_pod(spec, nodes[0], None)
                prefix = "pod"
            elif "primary" not in spec.candidates:
                # nsync 미리보기·실행 vcjob: task 마다 자기 쪽 스토리지만(_volumes 주석).
                manifest = build_volcano_job(
                    spec, job_image=self._job_image_fn(), namespace=self._namespace,
                    volumes=self._volumes(spec, role="launcher"),
                    role_volumes={r: self._volumes(spec, role=r)
                                  for r in ("source", "destination")})
                prefix = "vcjob"
            else:
                manifest = build_volcano_job(
                    spec, job_image=self._job_image_fn(), namespace=self._namespace,
                    volumes=self._volumes(spec))
                prefix = "vcjob"
            self._create_or_adopt(manifest, adopted)
        except (ExecutionError, SubmitUndecided):
            raise                      # 채택 판정이 이미 지었다 -- 다시 감싸면 detail 에 코드가 두 번 붙는다
        except Exception as exc:
            raise ExecutionError("submit_failed", str(exc)[:200]) from exc
        name = manifest["metadata"]["name"]
        ref = f"{prefix}/{name}"
        base = strip_scheme(spec.artifact_base)  # 접두사 전용(설계 §2.2)
        self._summary_paths[ref] = f"{base}/{spec.job_id}/{spec.phase}/summary.json"
        self._note_submitted(ref, adopted)
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
        # 호출 시점 해석(설계 §2.1) + 접두사 전용 스킴 제거(설계 §2.2).
        return f"{strip_scheme(self._artifact_base_fn())}/{job_id}/{phase}/summary.json"

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
        """(pod, log, waiting_reason) 목록. log=None 은 "얻을 수 없었다"(파드 소실/
        미기동)고, waiting_reason 은 왜 없는지의 별 채널이다(ImagePullBackOff 류)
        -- null 을 합성 문자열로 뭉개지 않는다(설계 §2.1). 빈 문자열은 정상값이다(러너가 그 지점까지 못 감 --
        2026-10-02 부터 정상 launcher 로그엔 워커마다 DMS_JR_WORKER_READY 줄, 준비 실패 시 DMS_EXEC_REASON= 마커)."""
        prefix, name = ref.split("/", 1)
        if prefix == "vcjob":
            return self._read_vcjob_logs(name)
        if prefix not in ("pod", "pods"):
            # 슬라이스 25 로 vcjob 이 열렸다 -- 이 거절은 미지 prefix 방어로만 남는다.
            raise ExecutionError("log_not_available", prefix)
        out = []
        for pod in name.split(","):
            try:
                out.append((pod, self._k8s.read_pod_log(pod, self._namespace), None))
            except Exception as exc:
                # 파드가 이미 GC됐거나 아직 로그가 없다 — 그 항목만 비우고 계속한다.
                # 다만 RBAC 거부·설정 오류·프로그래밍 버그도 여기로 떨어져 "GC됐다"와
                # 똑같이 렌더된다. 반환 계약은 그대로 두고 흔적만 남긴다.
                logger.warning("read_pod_log failed pod=%s: %s", pod, exc)
                out.append((pod, None, None))
        return out

    def _read_vcjob_logs(self, name):
        """vcjob 파드는 Volcano 자기 라벨(volcano.sh/job-name=<vcjob>)로 찾는다 --
        launcher 는 dms.io 라벨이 없고(§1-4), ref 에 이름이 있어 vcjob GET 도
        불요하다. launcher(이름 접미 -launcher-, §1-1 의 결정적 명명) 항상 +
        그 외는 Failed 만: 성공 워커의 sshd 로그는 노이즈고 남는 파드는 실패
        원인 파드다(§1-2). launcher 가 앞 -- 박제 상한(항목 4)이 잘라도
        launcher 가 살아야 한다. per-pod 실패는 pod 경로와 같은 null 접기지만,
        **list 호출 자체의 예외는 poll_failed 로 던진다**: 403 이 "로그 없음"으로
        렌더되는 사고(:393-398 교훈)를 조회 계층에서 반복하지 않는다. 파드가
        하나도 없으면 빈 목록이다 -- 이건 실패가 아니라 "0 항목"이라는 정보다."""
        try:
            briefs = self._k8s.list_pod_briefs(
                self._namespace, f"volcano.sh/job-name={name}")
        except Exception as exc:
            raise ExecutionError("poll_failed", str(exc)[:200]) from exc
        launchers = [b for b in briefs if "-launcher-" in b["name"]]
        failed = [b for b in briefs
                  if "-launcher-" not in b["name"] and b.get("phase") == "Failed"]
        out = []
        for brief in [*launchers, *failed]:
            pod = brief["name"]
            try:
                out.append((pod, self._k8s.read_pod_log(pod, self._namespace),
                            brief.get("waiting_reason")))
            except Exception as exc:
                logger.warning("read_pod_log failed pod=%s: %s", pod, exc)
                out.append((pod, None, brief.get("waiting_reason")))
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
        try:
            if manifest["kind"] == "Pod":
                self._core.create_namespaced_pod(self._namespace, manifest)
            else:
                self._custom.create_namespaced_custom_object(
                    self._VC["group"], self._VC["version"], self._namespace,
                    self._VC["plural"], manifest)
        except Exception as exc:
            # 409·일시 오류만 갈라 올린다(2026-11-03 B3 -- K8sAlreadyExists·K8sTransient, create_error_class). 원래 예외는
            # __cause__ 로 남는다.
            kind = create_error_class(exc)
            if kind == "exists":
                raise K8sAlreadyExists(
                    f"{manifest.get('kind')} {manifest['metadata']['name']} already exists") from exc
            if kind == "transient":
                raise K8sTransient(f"{type(exc).__name__}: {exc}"[:200]) from exc
            raise

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

    def list_node_addresses(self) -> list:
        """클러스터 노드의 이름·InternalIP·control-plane 여부(2026-09-09, 컨트롤
        상태의 no_proxy 힌트용). 읽기 전용 -- ClusterRole dms-api-nodes-readonly."""
        self._ensure()
        nodes = self._core.list_node(_request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
        return node_addresses_from(nodes.to_dict())

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
                # Terminating(삭제 요청됨, 아직 존재) -- 요청 삭제 정리가 「남은 객체」로 센다.
                "deleting": pod.metadata.deletion_timestamp is not None,
            })
        return briefs

    def list_vcjob_briefs(self, namespace, label_selector):
        """라벨로 Volcano Job 을 나열한다 -- [{"name", "deleting", "uid"}](요청 삭제 정리, purge_runner.sweep). custom
        objects API 는 원시 JSON(camelCase)이라 deletionTimestamp 를 그대로 본다. 404(CRD 부재)는 빈 목록이 아니라
        예외다 -- 「vcjob 없음」으로 접으면 Volcano 가 빠진 클러스터에서 정리가 남은 객체를 못 본 채 파일로 넘어간다."""
        self._ensure()
        resp = self._custom.list_namespaced_custom_object(
            self._VC["group"], self._VC["version"], namespace, self._VC["plural"],
            label_selector=label_selector,
            _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
        out = []
        for item in (resp or {}).get("items") or []:
            meta = item.get("metadata") or {}
            out.append({"name": meta.get("name"),
                        "deleting": meta.get("deletionTimestamp") is not None,
                        "uid": meta.get("uid")})
        return out

    # 슬라이스 17(큐 가시성): scheduling.volcano.sh 는 잡 제출용 _VC(batch.volcano.sh)
    # 와 다른 그룹이다. queues 는 cluster-scoped, podgroups 는 namespaced.
    _SCHED = {"group": "scheduling.volcano.sh", "version": "v1beta1"}

    def get_queue(self, name):
        """이름 지정 Queue GET. 404 는 None 으로 접는다: CRD 부재(Volcano 미설치)
        또는 큐 오브젝트 부재 -- 어느 쪽도 "빈 큐"가 아니라 "알 수 없음"이다(설계
        §4). _request_timeout 필수: urllib3 기본은 무제한이라 5초 폴링 라우트가
        apiserver 멈춤에 스레드풀째 매달린다(설계 §1-8)."""
        self._ensure()
        try:
            return self._custom.get_cluster_custom_object(
                self._SCHED["group"], self._SCHED["version"], "queues", name,
                _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            self._log_forbidden("get", "Queue", name, "-", exc)
            raise

    def list_podgroups(self, namespace):
        """네임스페이스 PodGroup list(필터는 리더의 몫 -- PodGroup 엔 DMS 라벨이
        없어 셀렉터를 못 쓴다). 404 접기·타임아웃은 get_queue 와 같은 이유."""
        self._ensure()
        try:
            return self._custom.list_namespaced_custom_object(
                self._SCHED["group"], self._SCHED["version"], namespace,
                "podgroups", _request_timeout=ROLLOUT_REQUEST_TIMEOUT_SECONDS)
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                return None
            self._log_forbidden("list", "PodGroup", "*", namespace, exc)
            raise
