"""기존 객체 채택(2026-11-03 B3 -- 설계 v6 9절 「① B3 세부」).

잡 객체 이름은 결정적(dms-{op}-{phase}-{job_id[:12]}·dms-preflight-…)이고 ref 는 create **뒤에** DB 에 남는다
(stepper._record_ref). 그 사이 DB 가 끊기면 다음 틱이 같은 이름으로 다시 만들어 409 를 받는다 -- 예전엔 그것이
submit_failed 로 접혀 잡이 FAILED/REJECTED 로 잘못 기록되고 객체는 추적 없이 돌았다. 이제 어댑터는 409 면 다시 읽어
라벨(전체 job_id·phase·tool)·지문 annotation·삭제 중 아님을 모두 만족할 때만 그 객체를 채택해 같은 ref 를 돌려준다.

리뷰 반영(같은 날): 「모름」(409 뒤 다시 읽기 실패·사라짐, create 결과 모름)은 실패로 접지 않고 다음 틱으로 미룬다
(SubmitUndecided), 라벨 셋은 같고 지문만 다른 이 잡의 객체는 stepper 가 회수한다(ExecutionError.strays), 겹친 컨트롤러의 낡은
스냅숏은 phase_refs 빈 자리 CAS·상태 CAS 로 아무것도 더 하지 않는다, 성공 아닌 종단(_finalize 한 곳)은 ref 없이 남은 객체를
라벨로 찾아 회수한다(_reclaim_unrecorded).

리뷰 2회차(같은 날): 회수는 _finalize 한 곳으로(앞 단계 파드 소실·소유자 측정 거부 종단도), 이미 끝난 실행 전 재점검 파드는
채택하지 않고 지운 뒤 다시 낸다(SubmitUndecided.strays), create 결과 모름 뒤의 채택은 「이전 제출」 주석을 남기지 않는다, 미결
문구는 판정을 앞에·원문은 따로 잘라 뒤에.
"""
import copy
import datetime

import pytest

from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, ExecutionError, JobSpec, SubmitUndecided
from dms.execution_manifests import (SPEC_FINGERPRINT_ANNOTATION, build_preflight_pod, build_volcano_job,
                                     manifest_fingerprint)
from dms.execution_volcano import (SUBMIT_UNDECIDED_WINDOW_SECONDS, K8sAlreadyExists, K8sTransient,
                                   KubernetesClient, VolcanoExecutionAdapter, adoption_problem,
                                   create_error_class)
from dms.repositories import Repositories
from dms.stepper import JobStepper

JOB = "0123456789ab" + "c" * 20          # 32자 hex -- 이름엔 앞 12자만 들어간다


class _StrictK8s:
    """apiserver 처럼 같은 이름의 두 번째 create 를 409(K8sAlreadyExists)로 거절한다. 저장본은 깊은 사본에 apiserver 가
    채우는 기본값·메타를 덧붙여, 채택 판정이 spec 글자 비교가 아니라 지문 annotation 으로 하는지 드러나게 한다."""

    def __init__(self):
        self.objs = {}            # (kind, name) -> obj
        self.creates = []         # create 를 시도한 이름(성공·409 모두)
        self.created = []         # 실제로 만든 이름
        self.deleted = []
        self.gets = []
        self.fail_create = {}     # name -> 던질 예외(409 가 아닌 실패)
        self.lose_response = set()   # 이 이름의 create 는 반영하고 응답만 잃는다(K8sTransient)
        self.fail_get = None
        self.fail_delete = None
        self.lists = []

    def create(self, manifest):
        name = manifest["metadata"]["name"]
        self.creates.append(name)
        if name in self.fail_create:
            raise self.fail_create[name]
        key = (manifest["kind"], name)
        if name in self.lose_response and key not in self.objs:
            self.lose_response.discard(name)
            self._store(manifest)
            raise K8sTransient("ReadTimeoutError: read timed out")
        if key in self.objs:
            raise K8sAlreadyExists(f"{manifest['kind']} {name} already exists")
        self._store(manifest)

    def _store(self, manifest):
        name = manifest["metadata"]["name"]
        key = (manifest["kind"], name)
        if key in self.objs:
            raise K8sAlreadyExists(f"{manifest['kind']} {name} already exists")
        obj = copy.deepcopy(manifest)
        obj["metadata"].update({"uid": f"uid-{name}", "creationTimestamp": "2026-11-03T00:00:00Z"})
        obj["spec"].setdefault("dnsPolicy", "ClusterFirst")      # 기본값 채움 흉내
        self.objs[key] = obj
        self.created.append(name)

    def get(self, kind, name, namespace):
        self.gets.append((kind, name))
        if self.fail_get is not None:
            raise self.fail_get
        return self.objs.get((kind, name))

    def delete(self, kind, name, namespace):
        if self.fail_delete is not None:
            raise self.fail_delete
        self.deleted.append((kind, name))
        self.objs.pop((kind, name), None)

    def read_pod_log(self, name, namespace):
        return ""

    def set_status(self, kind, name, status):
        self.objs[(kind, name)]["status"] = status

    def _select(self, kind, selector):
        # "k=v,k2" 꼴만(실 셀렉터의 부분집합) -- 값이 있으면 일치, 없으면 키 존재.
        self.lists.append((kind, selector))
        out = []
        for (k, name), obj in self.objs.items():
            labels = obj["metadata"].get("labels") or {}
            ok = all((labels.get(t.split("=", 1)[0]) == t.split("=", 1)[1]) if "=" in t else (t in labels)
                     for t in selector.split(","))
            if k == kind and ok:
                out.append({"name": name, "deleting": obj["metadata"].get("deletionTimestamp") is not None})
        return out

    def list_pod_briefs(self, namespace, label_selector):
        return self._select("Pod", label_selector)

    def list_vcjob_briefs(self, namespace, label_selector):
        return self._select("Job", label_selector)


def _spec(phase="execution", op="scan", tool="dscan", cand=None, paths=None, job_id=JOB, process_count=8):
    return JobSpec(job_id=job_id, phase=phase, operation=op, tool=tool, dryrun=(phase == "preview"),
                   identity={"uid": 0, "gid": 0, "username": "root", "privileged": True},
                   paths=paths or {"target": "/cephfs/dms/a", "storage": "cephfs-dms", "managed_root": "/cephfs/dms"},
                   options={}, candidates=cand or {"primary": ["dms-w1"]},
                   process_count=process_count, queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///cephfs/dms/artifacts")


_NSYNC_PATHS = {"source": "/cephfs-third/a", "source_storage": "cephfs-third", "source_root": "/cephfs-third",
                "destination": "/cephfs-secondary/b", "destination_storage": "cephfs-secondary",
                "destination_root": "/cephfs-secondary"}


def _nsync_spec(phase):
    return _spec(phase=phase, op="sync", tool="nsync", paths=dict(_NSYNC_PATHS),
                 cand={"source": ["dms-w1", "dms-w2"], "destination": ["dms-w4"]})


def _storages(name):
    return {"cephfs-third": {"mount_path": "/cephfs-third"},
            "cephfs-secondary": {"mount_path": "/cephfs-secondary"}}.get(name, {"mount_path": "/cephfs"})


def _adapter(k8s, image="reg/img:1", monotonic=None):
    return VolcanoExecutionAdapter(k8s, job_image=image, namespace="dms", storages_lookup=_storages,
                                   read_text=lambda p: None, artifact_base="file:///cephfs/dms/artifacts",
                                   monotonic=monotonic)


def _names(ref):
    return ref.split("/", 1)[1].split(",")


# ---- 채택: 모든 제출 경로(preflight·exec_preflight 맨 파드, 미리보기·실행 vcjob, nsync 복합 preflight·vcjob) ----

@pytest.mark.parametrize("spec", [
    _spec("preflight"), _spec("exec_preflight"), _spec("execution"),
    _spec("preview", op="rm", tool="drm"), _spec("execution", op="rm", tool="drm"),
    _spec("preview", op="sync", tool="dsync", paths=dict(_NSYNC_PATHS)),
    _nsync_spec("preflight"), _nsync_spec("exec_preflight"), _nsync_spec("preview"), _nsync_spec("execution"),
], ids=["preflight", "exec_preflight", "scan-execution", "rm-preview", "rm-execution", "dsync-preview",
        "nsync-preflight", "nsync-exec_preflight", "nsync-preview", "nsync-execution"])
def test_matching_existing_object_is_adopted_with_the_same_ref(spec):
    k8s = _StrictK8s()
    first = _adapter(k8s).submit(spec)
    created = list(k8s.created)
    # 다음 틱(새 어댑터 -- 컨트롤러 재시작과 같은 모양): 같은 이름에서 409 -> 다시 읽어 채택.
    again = _adapter(k8s)
    second = again.submit(spec)
    assert second == first
    assert k8s.created == created                         # 새 객체는 없다
    assert again.take_adopted(second) == _names(second)   # 채택한 이름 전부(복합 preflight 는 둘)
    assert again.take_adopted(second) == []               # 꺼내면 지운다


def test_fresh_submit_reports_no_adoption():
    k8s = _StrictK8s()
    a = _adapter(k8s)
    ref = a.submit(_spec("execution"))
    assert a.take_adopted(ref) == []
    assert k8s.gets == []                                 # 409 가 아니면 다시 읽지 않는다


def test_two_controllers_racing_get_the_same_ref():
    # 리스가 풀린 두 컨트롤러(또는 롤링 업데이트의 겹침)가 같은 틱에 같은 잡을 낸다: 하나가 만들고 하나가 채택 -- 같은 ref.
    k8s = _StrictK8s()
    a, b = _adapter(k8s), _adapter(k8s)
    ra, rb = a.submit(_spec("execution")), b.submit(_spec("execution"))
    assert ra == rb and len(k8s.created) == 1
    assert a.take_adopted(ra) == [] and b.take_adopted(rb) == _names(rb)


def test_adopted_object_is_polled_like_a_fresh_one():
    # 채택한 ref 는 새로 만든 ref 와 구별되지 않는다 -- 그 사이 끝났으면 그 결과가 곧 이 단계의 결과다.
    k8s = _StrictK8s()
    ref = _adapter(k8s).submit(_spec("execution"))
    k8s.set_status("Job", _names(ref)[0], {"state": {"phase": "Completed"}})
    a = _adapter(k8s)
    assert a.submit(_spec("execution")) == ref
    assert a.poll(ref) == ExecStatus.SUCCEEDED


# ---- 채택하지 않음: 지금처럼 submit_failed(detail 에 이유) ----

def _seed(k8s, spec, mutate):
    """spec 으로 객체를 만들어 두고 저장본을 mutate 로 바꾼다 -- 다음 submit 이 409 를 받는다."""
    ref = _adapter(k8s).submit(spec)
    name = _names(ref)[0]
    kind = "Job" if ref.startswith("vcjob/") else "Pod"
    mutate(k8s.objs[(kind, name)])
    return name


@pytest.mark.parametrize("label,value", [
    ("dms.io/job-id", "0123456789ab" + "d" * 20),   # 앞 12자가 같은 다른 잡 -- 이름만 같다
    ("dms.io/phase", "preview"),
    ("dms.io/tool", "drm"),
])
def test_label_mismatch_is_not_adopted(label, value):
    k8s = _StrictK8s()
    _seed(k8s, _spec("execution"), lambda o: o["metadata"]["labels"].__setitem__(label, value))
    with pytest.raises(ExecutionError) as ei:
        _adapter(k8s).submit(_spec("execution"))
    assert ei.value.reason_code == "submit_failed"
    assert f"라벨 {label} 불일치" in ei.value.detail
    assert ei.value.detail.startswith("AlreadyExists ")
    assert ei.value.strays == []                          # 남의 객체 -- 회수 대상이 아니다
    assert k8s.deleted == []


def test_missing_label_is_not_adopted():
    k8s = _StrictK8s()
    _seed(k8s, _spec("preflight"), lambda o: o["metadata"]["labels"].pop("dms.io/tool"))
    with pytest.raises(ExecutionError, match="라벨 dms.io/tool 불일치") as ei:
        _adapter(k8s).submit(_spec("preflight"))
    assert ei.value.strays == []


def test_fingerprint_mismatch_is_not_adopted_and_is_reported_as_this_jobs_stray():
    # 같은 잡·같은 단계라도 지금 지을 것과 다른 일을 하는 객체(여기선 프로세스 수)는 붙이지 않는다. 라벨 셋이 같으니 이 잡의
    # 객체다 -- 어댑터는 지우지 않고 strays 로 알린다(stepper 가 DB 를 본 뒤 회수 -- _fail_submit).
    k8s = _StrictK8s()
    ref = _adapter(k8s).submit(_spec("execution", process_count=8))
    with pytest.raises(ExecutionError) as ei:
        _adapter(k8s).submit(_spec("execution", process_count=4))
    assert "지문 불일치" in ei.value.detail and ei.value.reason_code == "submit_failed"
    assert "이 잡·단계의 객체" in ei.value.detail
    assert ei.value.strays == [ref] and k8s.deleted == []


def test_object_without_fingerprint_is_not_adopted():
    # 이 변경 전 코드가 만든 객체(annotation 없음) -- 같은 내용인지 알 수 없다 -> 채택 안 함(이 잡의 것이라 회수 대상).
    k8s = _StrictK8s()
    name = _seed(k8s, _spec("execution"), lambda o: o["metadata"].pop("annotations"))
    with pytest.raises(ExecutionError, match="지문 불일치") as ei:
        _adapter(k8s).submit(_spec("execution"))
    assert ei.value.strays == [f"vcjob/{name}"]


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_vcjob_being_deleted_is_not_adopted_but_retried_until_it_is_gone():
    # custom objects API 는 원시 JSON(camelCase). 삭제 중인 객체는 붙이지 않는다(곧 사라져 폴링이 FAILED 로 읽는다) -- 그렇다고
    # 종단으로 접지도 않는다(2026-11-03 리뷰 [2]): 「곧 없음」이라 다 지워지면 다음 틱의 create 가 새로 만든다.
    k8s = _StrictK8s()
    name = _seed(k8s, _spec("execution"),
                 lambda o: o["metadata"].__setitem__("deletionTimestamp", "2026-11-03T00:00:01Z"))
    a = _adapter(k8s, monotonic=_Clock())
    with pytest.raises(SubmitUndecided, match="삭제 중"):
        a.submit(_spec("execution"))
    assert k8s.deleted == []                                  # 지우는 중인 것을 다시 지우지 않는다
    del k8s.objs[("Job", name)]                               # 삭제가 끝났다
    ref = a.submit(_spec("execution"))
    assert ref == f"vcjob/{name}" and a.take_adopted(ref) == [] and k8s.created.count(name) == 2


def test_pod_being_deleted_is_undecided_then_bounded():
    # Pod GET 은 kubernetes 모델의 to_dict()(snake_case, datetime). finalizer 가 걸려 안 사라지면 상한 뒤 종전 submit_failed --
    # 이미 지워지는 중이라 회수할 것(strays)도 없다.
    k8s = _StrictK8s()
    when = datetime.datetime(2026, 11, 3, tzinfo=datetime.timezone.utc)
    _seed(k8s, _spec("preflight"), lambda o: o["metadata"].__setitem__("deletion_timestamp", when))
    clock = _Clock()
    a = _adapter(k8s, monotonic=clock)
    with pytest.raises(SubmitUndecided, match="삭제 중"):
        a.submit(_spec("preflight"))
    clock.t += SUBMIT_UNDECIDED_WINDOW_SECONDS
    with pytest.raises(ExecutionError) as ei:
        a.submit(_spec("preflight"))
    assert ei.value.reason_code == "submit_failed" and "삭제 중" in ei.value.detail and ei.value.strays == []


def test_adoption_problem_refuses_an_object_being_deleted_in_either_shape():
    # 순수 판정 함수의 계약(어댑터는 그 앞에서 「삭제 중」을 미결로 돌리지만 판정 자체도 채택하지 않는다).
    desired = build_volcano_job(_spec("execution"), job_image="i", namespace="dms", volumes=_vols())
    for key, value in (("deletionTimestamp", "2026-11-03T00:00:01Z"),
                       ("deletion_timestamp", datetime.datetime(2026, 11, 3, tzinfo=datetime.timezone.utc))):
        existing = copy.deepcopy(desired)
        existing["metadata"][key] = value
        assert adoption_problem(desired, existing) == "삭제 중(deletionTimestamp)"


def test_object_gone_between_409_and_get_is_undecided_then_bounded():
    # 409 와 GET 사이에 지워짐(실 클라이언트는 404 -> None) -- 「없음」은 다음 틱에 새로 만들면 되는 일시 상태라 종단으로 접지
    # 않는다(SubmitUndecided). 같은 일이 상한 넘게 되풀이되면(객체가 없다고 확인된 경우만) 종전 submit_failed.
    k8s = _StrictK8s()
    _adapter(k8s).submit(_spec("execution"))
    k8s.get = lambda kind, name, ns: None
    clock = _Clock()
    a = _adapter(k8s, monotonic=clock)
    with pytest.raises(SubmitUndecided, match="다시 읽을 때 없음"):
        a.submit(_spec("execution"))
    clock.t += SUBMIT_UNDECIDED_WINDOW_SECONDS - 1
    with pytest.raises(SubmitUndecided):
        a.submit(_spec("execution"))
    clock.t += 1
    with pytest.raises(ExecutionError) as ei:
        a.submit(_spec("execution"))
    assert ei.value.reason_code == "submit_failed" and "다시 읽을 때 없음" in ei.value.detail
    assert "300초 동안 재시도" in ei.value.detail
    with pytest.raises(SubmitUndecided):                  # 판정이 나면 창을 지운다 -- 다음 일은 처음부터
        a.submit(_spec("execution"))


def test_get_failure_after_409_is_undecided_not_failed():
    # 409 는 그 이름의 객체가 있다는 증거다 -- 다시 읽기가 일시 실패했다고 실패로 접으면 객체는 돌고 잡만 FAILED 인 B3 그대로다.
    # 「모름」은 다음 틱으로(상한 없음 -- 폴링의 GET 실패도 같다).
    k8s = _StrictK8s()
    _adapter(k8s).submit(_spec("execution"))
    k8s.fail_get = RuntimeError("apiserver 503")
    clock = _Clock()
    a = _adapter(k8s, monotonic=clock)
    for _ in range(2):
        with pytest.raises(SubmitUndecided) as ei:
            a.submit(_spec("execution"))
        assert "채택 판정 조회 실패" in str(ei.value) and "apiserver 503" in str(ei.value)
        clock.t += SUBMIT_UNDECIDED_WINDOW_SECONDS * 10
    k8s.fail_get = None
    ref = a.submit(_spec("execution"))                     # 회복하면 채택
    assert a.take_adopted(ref) == _names(ref)


def test_non_409_create_failure_is_unchanged_and_does_not_read_back():
    k8s = _StrictK8s()
    name = build_volcano_job(_spec("execution"), job_image="x", namespace="dms",
                             volumes=[{"name": "v", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]
                             )["metadata"]["name"]
    k8s.fail_create[name] = RuntimeError("422 Unprocessable Entity: spec.volumes[0].name")
    with pytest.raises(ExecutionError) as ei:
        _adapter(k8s).submit(_spec("execution"))
    assert ei.value.detail.startswith("422 Unprocessable Entity")     # 원문 보존(2026-09-15 교훈)
    assert k8s.gets == []


# ---- create 결과 모름(K8sTransient -- 응답 유실·5xx·429·연결 끊김) ----

def test_lost_create_response_is_adopted_in_the_same_tick():
    # 요청은 etcd 에 반영됐는데 응답만 잃었다 -- 다시 읽어 보니 우리가 지은 그대로라 바로 채택(같은 ref). 채택 목록(take_adopted --
    # 「이전 제출이 만들고 기록하지 못한 객체」 주석)에는 넣지 않는다: 이번 create 가 만든 것이라 그 문구가 거짓이다(리뷰 2회차 [4]).
    k8s = _StrictK8s()
    name = _names(_adapter(_StrictK8s()).submit(_spec("execution")))[0]
    k8s.lose_response.add(name)
    a = _adapter(k8s)
    ref = a.submit(_spec("execution"))
    assert ref == f"vcjob/{name}" and k8s.created == [name]
    assert a.take_adopted(ref) == []


def test_transient_create_failure_with_no_object_is_undecided_then_bounded_with_the_original_text():
    k8s = _StrictK8s()
    name = _names(_adapter(_StrictK8s()).submit(_spec("execution")))[0]
    k8s.fail_create[name] = K8sTransient("ApiException: (500) Internal error occurred: failed calling webhook")
    clock = _Clock()
    a = _adapter(k8s, monotonic=clock)
    with pytest.raises(SubmitUndecided):
        a.submit(_spec("execution"))
    clock.t += SUBMIT_UNDECIDED_WINDOW_SECONDS
    with pytest.raises(ExecutionError) as ei:
        a.submit(_spec("execution"))
    # 판정이 앞, 원문은 따로 잘라 뒤(리뷰 2회차 [5]) -- 둘 다 남는다(원문 보존은 2026-09-15 교훈).
    assert ei.value.detail.startswith(f"create 결과 모름 {name}: 다시 읽을 때 없음 (300초 동안 재시도)")
    assert ei.value.detail.endswith(" -- 원문: ApiException: (500) Internal error occurred: failed calling webhook")
    del k8s.fail_create[name]
    assert a.submit(_spec("execution")) == f"vcjob/{name}"   # 웹훅이 돌아오면 새로 만든다


def test_long_apiserver_text_does_not_push_the_verdict_out():
    # ApiException 의 str 은 응답 헤더(Audit-Id·flowschema …)가 본문보다 앞이라 길다. 예전엔 원문을 앞에 두고 통째로 [:200] 을
    # 걸어 「객체 없음·300초 재시도」 판정이 잘려 나갔다(리뷰 2회차 [5]).
    k8s = _StrictK8s()
    name = _names(_adapter(_StrictK8s()).submit(_spec("execution")))[0]
    k8s.fail_create[name] = K8sTransient("ApiException: (503)\nHTTP response headers: " + "x" * 400)
    clock = _Clock()
    a = _adapter(k8s, monotonic=clock)
    with pytest.raises(SubmitUndecided) as und:
        a.submit(_spec("execution"))
    assert str(und.value).startswith(f"create 결과 모름 {name}: 다시 읽을 때 없음 -- 원문: ApiException: (503)")
    clock.t += SUBMIT_UNDECIDED_WINDOW_SECONDS
    with pytest.raises(ExecutionError) as ei:
        a.submit(_spec("execution"))
    assert "다시 읽을 때 없음 (300초 동안 재시도) -- 원문: ApiException: (503)" in ei.value.detail
    assert len(ei.value.detail) <= 200 + len(" -- 원문: ") + 160


def test_transient_create_failure_then_get_failure_is_undecided():
    k8s = _StrictK8s()
    name = _names(_adapter(_StrictK8s()).submit(_spec("execution")))[0]
    k8s.fail_create[name] = K8sTransient("ProtocolError: connection reset")
    k8s.fail_get = ConnectionResetError("reset")
    with pytest.raises(SubmitUndecided, match="채택 판정 조회 실패"):
        _adapter(k8s).submit(_spec("execution"))


def test_transient_create_finding_a_foreign_object_is_submit_failed():
    k8s = _StrictK8s()
    name = _seed(k8s, _spec("execution"), lambda o: o["metadata"]["labels"].__setitem__("dms.io/job-id", "f" * 32))
    k8s.fail_create[name] = K8sTransient("ReadTimeoutError")
    with pytest.raises(ExecutionError, match="라벨 dms.io/job-id 불일치") as ei:
        _adapter(k8s).submit(_spec("execution"))
    assert ei.value.strays == []


# ---- 복합 preflight(nsync src+dst): 파드별 ----

def test_composite_preflight_adopts_per_pod():
    # 앞 틱이 src 만 만들고 끊겼다 -> src 는 채택, dst 는 새로 만든다. ref 는 처음과 같은 "pods/<src>,<dst>".
    k8s = _StrictK8s()
    ref = _adapter(k8s).submit(_nsync_spec("preflight"))
    src, dst = _names(ref)
    k8s.objs.pop(("Pod", dst))
    a = _adapter(k8s)
    assert a.submit(_nsync_spec("preflight")) == ref
    assert ("Pod", dst) in k8s.objs and k8s.created.count(dst) == 2 and k8s.created.count(src) == 1
    assert a.take_adopted(ref) == [src]


def test_composite_preflight_second_pod_not_adopted_reports_the_first_as_stray():
    # dst 이름을 다른 잡의 객체가 쥐고 있다 -> submit_failed. 이번 제출이 만든 src 는 ref 가 기록되지 않으므로 회수 대상으로
    # 알린다(stepper 가 지운다 -- 어댑터는 DB 를 모른다). 남의 객체(dst)는 회수 대상이 아니다.
    k8s = _StrictK8s()
    ref = _adapter(k8s).submit(_nsync_spec("preflight"))
    src, dst = _names(ref)
    k8s.objs.pop(("Pod", src))
    k8s.objs[("Pod", dst)]["metadata"]["labels"]["dms.io/job-id"] = "0123456789ab" + "e" * 20
    with pytest.raises(ExecutionError, match="라벨 dms.io/job-id 불일치") as ei:
        _adapter(k8s).submit(_nsync_spec("preflight"))
    assert ei.value.strays == [f"pod/{src}"]
    assert k8s.deleted == [] and ("Pod", dst) in k8s.objs


def test_composite_preflight_second_pod_create_error_reports_the_first_as_stray():
    k8s = _StrictK8s()
    probe = _StrictK8s()
    src, dst = _names(_adapter(probe).submit(_nsync_spec("preflight")))
    k8s.fail_create[dst] = RuntimeError("403 Forbidden")
    with pytest.raises(ExecutionError, match="403 Forbidden") as ei:
        _adapter(k8s).submit(_nsync_spec("preflight"))
    assert ei.value.strays == [f"pod/{src}"] and k8s.deleted == []


def test_composite_preflight_second_pod_undecided_keeps_the_first_for_the_next_tick():
    # dst 결과를 모른다 -- 잡은 이전 상태로 남고 다음 틱이 src 를 채택한다(지우면 다시 만들 뿐이다).
    k8s = _StrictK8s()
    probe = _StrictK8s()
    src, dst = _names(_adapter(probe).submit(_nsync_spec("preflight")))
    k8s.fail_create[dst] = K8sTransient("ReadTimeoutError")
    with pytest.raises(SubmitUndecided):
        _adapter(k8s).submit(_nsync_spec("preflight"))
    assert ("Pod", src) in k8s.objs and k8s.deleted == []
    del k8s.fail_create[dst]
    a = _adapter(k8s)
    ref = a.submit(_nsync_spec("preflight"))
    assert a.take_adopted(ref) == [src]


# ---- 이미 끝난 실행 전 재점검 파드는 채택하지 않는다(리뷰 2회차 [3]) ----

@pytest.mark.parametrize("phase", ["Succeeded", "Failed"])
def test_finished_exec_preflight_pod_is_not_adopted_but_handed_back_for_a_fresh_recheck(phase):
    # 재점검(TOCTOU)은 첫 실행 관문 뒤의 결과여야 한다 -- ref 기록이 끊긴 틱의 파드가 이미 끝나 있으면 그 결과는 관문 앞의 것일 수
    # 있다. 채택 대신 「모름」(잡은 이전 상태)으로 돌려주며 지울 것(strays)을 싣는다 -- 어댑터는 DB 를 모르므로 스스로 지우지 않는다.
    k8s = _StrictK8s()
    ref = _adapter(k8s).submit(_spec("exec_preflight"))
    k8s.set_status("Pod", _names(ref)[0], {"phase": phase})
    a = _adapter(k8s)
    with pytest.raises(SubmitUndecided, match="이미 끝난 실행 전 재점검") as ei:
        a.submit(_spec("exec_preflight"))
    assert ei.value.strays == [ref] and k8s.deleted == [] and a.take_adopted(ref) == []


def test_running_exec_preflight_pod_and_finished_initial_preflight_pod_are_adopted():
    # 아직 도는 재점검은 관문 뒤의 상태를 볼 수 있어 채택한다. 초기 preflight 는 끝났어도 채택한다(그 결과는 설계상 보류 뒤에도
    # 쓰이고, sync·rm 은 실행 전에 재점검을 다시 거친다 -- _finished_recheck docstring).
    k8s = _StrictK8s()
    for phase, status in (("exec_preflight", "Running"), ("preflight", "Succeeded"), ("preflight", "Failed")):
        k8s.objs.clear()
        ref = _adapter(k8s).submit(_spec(phase))
        k8s.set_status("Pod", _names(ref)[0], {"phase": status})
        a = _adapter(k8s)
        assert a.submit(_spec(phase)) == ref and a.take_adopted(ref) == _names(ref)


def test_composite_exec_preflight_with_a_finished_pod_hands_back_both_pods():
    # nsync 복합 재점검: 뒤쪽(dst)이 끝나 있으면 이번 제출이 만들거나 채택한 앞쪽(src)도 함께 지운다 -- 두 점검이 같은 시점이게.
    k8s = _StrictK8s()
    ref = _adapter(k8s).submit(_nsync_spec("exec_preflight"))
    src, dst = _names(ref)
    k8s.set_status("Pod", dst, {"phase": "Succeeded"})
    with pytest.raises(SubmitUndecided) as ei:
        _adapter(k8s).submit(_nsync_spec("exec_preflight"))
    assert ei.value.strays == [f"pod/{src}", f"pod/{dst}"]
    k8s.set_status("Pod", dst, {"phase": "Running"})
    k8s.set_status("Pod", src, {"phase": "Failed"})            # 앞쪽이 끝났으면 거기서 멈춘다(뒤쪽은 건드리지 않는다)
    with pytest.raises(SubmitUndecided) as ei:
        _adapter(k8s).submit(_nsync_spec("exec_preflight"))
    assert ei.value.strays == [f"pod/{src}"]


def test_stale_adoption_entry_does_not_leak_into_a_later_fresh_submit():
    # 꺼내지 않은 채택 항목이 같은 ref 의 다음(채택 없는) 제출에 거짓 채택으로 섞이지 않는다.
    k8s = _StrictK8s()
    ref = _adapter(k8s).submit(_spec("execution"))
    a = _adapter(k8s)
    assert a.submit(_spec("execution")) == ref             # 채택(꺼내지 않음)
    k8s.objs.clear()
    assert a.submit(_spec("execution")) == ref             # 새로 만듦
    assert a.take_adopted(ref) == []


# ---- 지문 ----

def _vols():
    return [{"name": "cephfs-1", "hostPath": {"path": "/cephfs"}, "mountPath": "/cephfs"}]


def test_fingerprint_ignores_the_job_image():
    # 이미지는 호출 시점 해석(포탈 릴리스) -- create 와 다음 틱 사이 릴리스가 채택을 막지 않게 지문에서 뺀다.
    a = build_volcano_job(_spec("execution"), job_image="reg/dms-job:d16", namespace="dms", volumes=_vols())
    b = build_volcano_job(_spec("execution"), job_image="reg/dms-job:d17", namespace="dms", volumes=_vols())
    assert a != b
    assert (a["metadata"]["annotations"][SPEC_FINGERPRINT_ANNOTATION]
            == b["metadata"]["annotations"][SPEC_FINGERPRINT_ANNOTATION])
    p1 = build_preflight_pod(_spec("preflight"), job_image="i:1", namespace="dms", volumes=_vols(), node="n1")
    p2 = build_preflight_pod(_spec("preflight"), job_image="i:2", namespace="dms", volumes=_vols(), node="n1")
    assert (p1["metadata"]["annotations"][SPEC_FINGERPRINT_ANNOTATION]
            == p2["metadata"]["annotations"][SPEC_FINGERPRINT_ANNOTATION])


def test_image_change_between_ticks_still_adopts():
    k8s = _StrictK8s()
    ref = _adapter(k8s, image="reg/dms-job:d16").submit(_spec("execution"))
    a = _adapter(k8s, image=lambda: "reg/dms-job:d17")
    assert a.submit(_spec("execution")) == ref
    assert a.take_adopted(ref) == _names(ref)


@pytest.mark.parametrize("change", [
    {"process_count": 4},
    {"paths": {"target": "/cephfs/dms/b", "storage": "cephfs-dms", "managed_root": "/cephfs/dms"}},
    {"cand": {"primary": ["dms-w2"]}},
])
def test_fingerprint_changes_with_what_the_job_does(change):
    base = build_volcano_job(_spec("execution"), job_image="i", namespace="dms", volumes=_vols())
    other = build_volcano_job(_spec("execution", **change), job_image="i", namespace="dms", volumes=_vols())
    assert (base["metadata"]["annotations"][SPEC_FINGERPRINT_ANNOTATION]
            != other["metadata"]["annotations"][SPEC_FINGERPRINT_ANNOTATION])


def test_fingerprint_is_deterministic_and_excludes_its_own_annotation():
    m = build_volcano_job(_spec("execution"), job_image="i", namespace="dms", volumes=_vols())
    fp = m["metadata"]["annotations"][SPEC_FINGERPRINT_ANNOTATION]
    assert fp.startswith("v1:") and len(fp) == 3 + 64
    assert manifest_fingerprint(m) == fp                  # annotation 이 실린 채로 다시 계산해도 같다
    assert manifest_fingerprint(copy.deepcopy(m)) == fp


_HASHSEED_PROBE = """
from dms.execution import JobSpec
from dms.execution_volcano import VolcanoExecutionAdapter

class K:
    def __init__(self):
        self.m = []
    def create(self, m):
        self.m.append(m)

sync = {"source": "/s/dms/a", "source_storage": "s", "source_root": "/s/dms", "destination": "/d/dms/b",
        "destination_storage": "d", "destination_root": "/d/dms",
        "contain": {"source": {"root": "/s/dms/pv1"}, "destination": {"root": "/d/dms/pv2"}}}
cases = [("dsync", "sync", sync, {"primary": ["n1", "n2", "n3"]}, {"chown": "1000:1000", "delete": True}),
         ("nsync", "sync", sync, {"source": ["n1", "n2"], "destination": ["n3", "n4"]}, {"delete": True}),
         ("drm", "rm", {"target": "/s/dms/x", "storage": "s", "managed_root": "/s/dms",
                        "contain": {"target": {"root": "/s/dms"}}}, {"primary": ["n2", "n1"]}, {}),
         ("dscan", "scan", {"target": "/s/dms/x", "storage": "s", "managed_root": "/s/dms"}, {"primary": ["n1"]},
          {"verbose": True})]
for tool, op, paths, cand, opts in cases:
    for phase in ("preflight", "preview", "exec_preflight", "execution"):
        k = K()
        spec = JobSpec(job_id="0123456789ab" + "c" * 20, phase=phase, operation=op, tool=tool,
                       dryrun=(phase == "preview"), identity={"uid": 0, "gid": 0, "username": "root", "privileged": True},
                       paths=paths, options=opts, candidates=cand, process_count=8, queue="dms-data",
                       priority_class="dms-mid", artifact_base="file:///art", timeout_seconds=3600, ttl_seconds=86400,
                       result_owner="1000:1000" if op == "sync" else None, result_owner_rule="storage" if op == "sync" else None)
        VolcanoExecutionAdapter(k, job_image="i", namespace="dms", storages_lookup=lambda n: {"mount_path": "/" + n},
                                read_text=lambda p: None, artifact_base="file:///art").submit(spec)
        print(tool, phase, *[m["metadata"]["annotations"]["dms.io/spec-fingerprint"] for m in k.m])
"""


def test_fingerprint_is_stable_across_processes_with_different_hash_seeds():
    # 컨트롤러 재시작 = 새 프로세스(다른 해시 시드). 집합·사전 순회 순서가 빌더 출력에 새면 재시작 뒤 채택이 늘 실패한다
    # (리뷰 B 의 scratch fp_hashseed.py 를 고정 -- contain·chown·정렬 안 된 후보까지).
    import os
    import subprocess
    import sys

    import dms
    src = os.path.dirname(os.path.dirname(dms.__file__))
    outs = []
    for seed in ("1", "987"):
        env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=src)
        r = subprocess.run([sys.executable, "-c", _HASHSEED_PROBE], env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        outs.append(r.stdout)
    assert outs[0] == outs[1] and outs[0].count("v1:") >= 16


def test_every_job_object_carries_the_adoption_labels():
    p = build_preflight_pod(_spec("preflight"), job_image="i", namespace="dms", volumes=_vols(), node="n1")
    j = build_volcano_job(_spec("execution"), job_image="i", namespace="dms", volumes=_vols())
    assert p["metadata"]["labels"] == {"dms.io/job-id": JOB, "dms.io/phase": "preflight", "dms.io/tool": "dscan"}
    assert j["metadata"]["labels"] == {"dms.io/job-id": JOB, "dms.io/phase": "execution", "dms.io/tool": "dscan"}


def test_adoption_problem_accepts_the_snake_case_pod_shape():
    # 실 Pod GET(read_namespaced_pod_status().to_dict()) 모양: deletion_timestamp None 이면 삭제 중이 아니다.
    desired = build_preflight_pod(_spec("preflight"), job_image="i", namespace="dms", volumes=_vols(), node="n1")
    existing = {"metadata": {"name": desired["metadata"]["name"], "deletion_timestamp": None,
                             "labels": dict(desired["metadata"]["labels"]),
                             "annotations": dict(desired["metadata"]["annotations"])},
                "spec": {"dns_policy": "ClusterFirst"}, "status": {"phase": "Running"}}
    assert adoption_problem(desired, existing) is None


# ---- 실 클라이언트: 409 만 K8sAlreadyExists 로 가른다 ----

class _ApiErr(Exception):
    def __init__(self, status):
        super().__init__(f"({status})")
        self.status = status


def _client(core_exc=None, custom_exc=None):
    class _Core:
        def create_namespaced_pod(self, ns, body):
            if core_exc is not None:
                raise core_exc

    class _Custom:
        def create_namespaced_custom_object(self, *a):
            if custom_exc is not None:
                raise custom_exc

    c = KubernetesClient("dms")
    c._core, c._custom = _Core(), _Custom()
    c._ensure = lambda: None
    return c


def test_k8s_client_create_raises_already_exists_on_409():
    pod = {"kind": "Pod", "metadata": {"name": "p"}}
    job = {"kind": "Job", "metadata": {"name": "j"}}
    with pytest.raises(K8sAlreadyExists) as ei:
        _client(core_exc=_ApiErr(409)).create(pod)
    assert isinstance(ei.value.__cause__, _ApiErr)
    with pytest.raises(K8sAlreadyExists):
        _client(custom_exc=_ApiErr(409)).create(job)


@pytest.mark.parametrize("status", [403, 422, None])
def test_k8s_client_create_passes_other_errors_through(status):
    # 4xx 는 apiserver 의 판정 -- 원문 그대로(submit 이 submit_failed 로 접는다). 상태 코드 없는 예외도 전송 오류 타입이 아니면
    # (코드 결함) 그대로다 -- 「모름」으로 미루면 상한까지 헛돈다.
    err = _ApiErr(status) if status is not None else RuntimeError("serialization bug")
    with pytest.raises(type(err)) as ei:
        _client(custom_exc=err).create({"kind": "Job", "metadata": {"name": "j"}})
    assert not isinstance(ei.value, (K8sAlreadyExists, K8sTransient))


@pytest.mark.parametrize("err", [_ApiErr(500), _ApiErr(503), _ApiErr(429), _ApiErr(0),
                                 ConnectionResetError("reset"), TimeoutError("timed out")])
def test_k8s_client_create_raises_transient_when_the_outcome_is_unknown(err):
    with pytest.raises(K8sTransient) as ei:
        _client(core_exc=err).create({"kind": "Pod", "metadata": {"name": "p"}})
    assert ei.value.__cause__ is err


def test_create_error_class_treats_urllib3_transport_errors_as_transient(monkeypatch):
    # .venv 엔 urllib3 가 없을 수 있다(kubernetes 미설치) -- 실 클라이언트가 보는 모양(exceptions.HTTPError 계열)만 흉내 낸다.
    import sys
    import types

    class HTTPError(Exception):
        pass

    class ReadTimeoutError(HTTPError):
        pass
    fake = types.ModuleType("urllib3")
    fake.exceptions = types.SimpleNamespace(HTTPError=HTTPError)
    monkeypatch.setitem(sys.modules, "urllib3", fake)
    assert create_error_class(ReadTimeoutError("read timed out")) == "transient"
    assert create_error_class(_ApiErr(409)) == "exists"
    assert create_error_class(_ApiErr(404)) is None
    assert create_error_class(ValueError("bad manifest")) is None


# ---- stepper: ref 기록이 끊긴 다음 틱 ----

class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _scan_job(repos):
    repos.storages.create(storage_name="s1", mount_path="/s1", managed_root="/s1/dms",
                          backend_type="cephfs", actor="test")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k",
                                payload={"run_as_root": True, "storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 0, "gid": 0, "username": "alice", "groups": [],
                                                    "privileged": True},
                     "candidates": {"primary": ["n1"]}, "process_count": 8, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _volcano(repos, k8s):
    return VolcanoExecutionAdapter(k8s, job_image=lambda: "reg/dms-job:d1", namespace="dms",
                                   storages_lookup=lambda n: repos.storages.get(n),
                                   read_text=lambda p: None, artifact_base="file:///art")


def _fail_once(monkeypatch, obj, attr, times=1):
    real = getattr(obj, attr)
    state = {"left": times}

    def flaky(*a, **k):
        if state["left"]:
            state["left"] -= 1
            raise RuntimeError("server closed the connection unexpectedly")   # DB 장애조치 흉내
        return real(*a, **k)
    monkeypatch.setattr(obj, attr, flaky)


def _events(repos, rid, event_type):
    return [e for e in repos.observability.events_for_request(rid) if e["event_type"] == event_type]


def test_stepper_record_ref_failure_then_next_tick_adopts_the_preflight_pod(db, monkeypatch):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    adapter = _volcano(repos, k8s)
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")

    first = JobStepper(repos, adapter, settings=_Settings()).run_once()
    assert first[jid].startswith("error:")                     # 전이 없이 끝난 틱(step_error)
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.PENDING.value and job["reason_code"] is None
    assert len(k8s.created) == 1                               # 파드는 이미 떠 있다 -- ref 만 없다

    # 다음 틱: 컨트롤러가 재시작했어도 같다(새 어댑터).
    second = JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()
    assert second[jid] == "Preflight"
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.PREFLIGHT.value and job["reason_code"] is None
    assert (job["phase_refs"] or {})["preflight"] == "pod/" + k8s.created[0]
    assert len(k8s.created) == 1                               # 다시 만들지 않았다(409 -> 채택)
    assert _events(repos, rid, "submit_failed") == []
    adopted = _events(repos, rid, "submit_adopted")
    assert len(adopted) == 1 and adopted[0]["severity"] == "info"
    assert adopted[0]["payload"] == {"job_id": jid, "phase": "preflight", "ref": "pod/" + k8s.created[0],
                                     "adopted": [k8s.created[0]]}


def test_stepper_record_ref_failure_on_execution_then_next_tick_runs(db, monkeypatch):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    pod = k8s.created[0]
    k8s.set_status("Pod", pod, {"phase": "Succeeded"})

    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")
    first = JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()
    assert first[jid].startswith("error:")
    assert repos.data_jobs.get_job(jid)["state"] == DataJobState.PREFLIGHT.value
    vcjob = [n for n in k8s.created if n.startswith("dms-scan-execution-")]
    assert len(vcjob) == 1

    second = JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()
    assert second[jid] == "Running"
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.RUNNING.value and job["reason_code"] is None
    assert job["phase_refs"]["execution"] == "vcjob/" + vcjob[0]
    assert [n for n in k8s.created if n.startswith("dms-scan-execution-")] == vcjob
    assert _events(repos, rid, "submit_failed") == []
    assert [e["payload"]["phase"] for e in _events(repos, rid, "submit_adopted")] == ["execution"]


def test_stepper_adoption_is_idempotent_across_repeated_failed_ticks(db, monkeypatch):
    # DB 가 두 틱 연속 끊겼다: 1틱 생성·기록 실패, 2틱 채택·기록 실패, 3틱 채택·기록 성공. 객체는 하나, 이벤트는 기록이 성공한 한 번.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref", times=2)
    for _ in range(2):
        assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid].startswith("error:")
        assert repos.data_jobs.get_job(jid)["state"] == DataJobState.PENDING.value
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    assert len(k8s.created) == 1 and k8s.creates.count(k8s.created[0]) == 3
    assert len(_events(repos, rid, "submit_adopted")) == 1 and _events(repos, rid, "submit_failed") == []


def test_stepper_state_write_failure_after_ref_then_next_tick_adopts(db, monkeypatch):
    # ref 는 남았는데 상태 전이(Pending -> Preflight)가 끊긴 창: 잡은 Pending 이라 다음 틱이 같은 제출을 다시 한다 --
    # 예전엔 이것도 409 -> preflight_submit_failed 로 Rejected 였다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    _fail_once(monkeypatch, repos.data_jobs, "set_job_state")
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid].startswith("error:")
    assert repos.data_jobs.get_job(jid)["state"] == DataJobState.PENDING.value
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    assert len(k8s.created) == 1 and _events(repos, rid, "submit_failed") == []
    # ref 는 기록돼 있었다 -- 「기록하지 못한 객체」 이벤트가 아니다(스냅숏에 있던 ref 의 재시도).
    assert _events(repos, rid, "submit_adopted") == []
    assert [(t["from_state"], t["to_state"]) for t in repos.data_jobs.job_transitions(jid)] == [
        (None, "Pending"), ("Pending", "Preflight")]


def test_stepper_foreign_object_under_the_name_is_still_rejected(db):
    # 채택 판정에 걸리면 종전 그대로: preflight_submit_failed:submit_failed + 진단 로그에 이유. 남의 객체는 지우지 않는다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()
    pod = k8s.created[0]
    # 같은 이름에 다른 잡의 라벨을 단 객체가 있는 상태로 다시 낸다 -- 이름 앞 12자 충돌 흉내.
    k8s.objs[("Pod", pod)]["metadata"]["labels"]["dms.io/job-id"] = "0" * 32
    # 잡을 다시 Pending 제출 경로로 돌린다(ref 기록이 끊긴 것과 같은 모양 -- 이번엔 객체가 남의 것).
    db.execute("UPDATE data_jobs SET state = 'Pending', phase_refs = NULL WHERE job_id = :j", {"j": jid})
    result = JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()
    assert result[jid] == "Rejected"
    job = repos.data_jobs.get_job(jid)
    assert job["reason_code"] == "preflight_submit_failed:submit_failed"
    ev = _events(repos, rid, "submit_failed")
    assert len(ev) == 1 and "라벨 dms.io/job-id 불일치" in ev[0]["message"]
    assert k8s.deleted == [] and _events(repos, rid, "submit_adopted") == []


# ---- stepper: 「모름」은 다음 틱(SubmitUndecided) ----

def test_stepper_get_failure_after_409_keeps_the_job_and_the_next_tick_adopts(db, monkeypatch):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid].startswith("error:")
    k8s.fail_get = RuntimeError("apiserver 503")             # 장애조치와 함께 apiserver 한 대가 빠졌다
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "error:SubmitUndecided"
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.PENDING.value and job["reason_code"] is None
    assert _events(repos, rid, "submit_failed") == []
    steps = [e for e in repos.observability.events_for_request(rid) if e["event_type"] == "step_error"]
    assert any("채택 판정 조회 실패" in e["message"] for e in steps)     # 운영자에겐 보인다(error)
    k8s.fail_get = None
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    assert len(k8s.created) == 1 and len(_events(repos, rid, "submit_adopted")) == 1


def test_stepper_lost_create_response_leaves_no_earlier_submit_note(db):
    # 리뷰 2회차 [4]: apiserver 응답만 잃은 create(이번 틱이 만든 객체)를 다시 읽어 채택했다 -- 사용자 화면에 「이전 제출이 만들고
    # 기록하지 못한 객체」 주석(submit_adopted)이 뜨면 거짓이다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    real_create = k8s.create

    def create(manifest):                 # 첫 create 의 이름(이 잡의 preflight 파드)을 응답 유실 대상으로
        k8s.lose_response.add(manifest["metadata"]["name"])
        k8s.create = real_create
        return real_create(manifest)
    k8s.create = create
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    assert len(k8s.created) == 1 and not k8s.lose_response              # 응답을 잃은 그 create 였다
    assert repos.data_jobs.get_job(jid)["phase_refs"]["preflight"] == f"pod/{k8s.created[0]}"
    assert _events(repos, rid, "submit_adopted") == [] and _events(repos, rid, "submit_failed") == []


# ---- stepper: 이 잡의 객체인데 채택하지 않으면 회수(ExecutionError.strays) ----

def _to_execution_lost(repos, k8s, monkeypatch, rid, jid):
    """scan 잡을 「실행 vcjob 은 만들었고 ref 기록이 끊긴」 상태로 -- 잡은 Preflight, vcjob 은 떠 있다."""
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    k8s.set_status("Pod", k8s.created[0], {"phase": "Succeeded"})
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid].startswith("error:")
    [vcjob] = [n for n in k8s.created if n.startswith("dms-scan-execution-")]
    return vcjob


def test_stepper_reclaims_this_jobs_unadoptable_object_and_says_so(db, monkeypatch):
    # 배포 경계의 옛 객체(지문 annotation 없음) -- 라벨 셋은 이 잡·이 단계다. 채택은 안 하지만 「시작 못 함」으로 끝내며 그대로
    # 두면 ref 없이 돈다(rm 이면 화면 밖에서 지운다) -- stepper 가 지우고 그 사실을 같은 기록에 남긴다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    vcjob = _to_execution_lost(repos, k8s, monkeypatch, rid, jid)
    k8s.objs[("Job", vcjob)]["metadata"].pop("annotations")
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Failed"
    job = repos.data_jobs.get_job(jid)
    assert job["reason_code"] == "execution_submit_failed:submit_failed"
    assert ("Job", vcjob) in k8s.deleted and ("Job", vcjob) not in k8s.objs
    [ev] = _events(repos, rid, "submit_failed")
    assert ev["severity"] == "warning" and "남은 객체 회수" in ev["message"] and "이 잡·단계의 객체" in ev["message"]
    assert ev["payload"]["reclaimed"] == [f"vcjob/{vcjob}"] and "reclaim_failed" not in ev["payload"]
    import json
    assert "남은 객체 회수" in json.loads(repos.data_jobs.get_job(jid)["diag_logs"])["entries"][0]["log"]


def test_stepper_reclaim_failure_is_an_error_that_says_the_object_may_run_untracked(db, monkeypatch):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    vcjob = _to_execution_lost(repos, k8s, monkeypatch, rid, jid)
    k8s.objs[("Job", vcjob)]["metadata"]["annotations"][SPEC_FINGERPRINT_ANNOTATION] = "v1:old"
    k8s.fail_delete = RuntimeError("delete forbidden")
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Failed"
    [ev] = _events(repos, rid, "submit_failed")
    assert ev["severity"] == "error" and "추적 밖에서 돌 수 있음" in ev["message"]
    assert ev["payload"]["reclaim_failed"] == [f"vcjob/{vcjob}"]
    assert repos.data_jobs.get_job(jid)["reason_code"] == "execution_submit_failed:submit_failed"   # 원래 사유 그대로
    # 종단의 라벨 회수(_finalize)는 이미 지우려 한 strays 를 다시 보고하지 않는다(한 객체 한 기록).
    assert _events(repos, rid, "unrecorded_reclaimed") == []


def _sync_settings():
    s = _Settings()
    s.allow_privileged_requesters = True
    s.privileged_requesters = frozenset({"ops"})
    return s


def _pv_sync_job(repos):
    """PV 목적지 nsync(결과 소유 규칙 storage) -- _build_spec 이 매 틱 요청 payload·스토리지·PV 블록에서 contain·result_owner·
    owner_rule 을 다시 짓는 경로(리뷰 B 의 scratch check_sync_adopt.py 를 고정)."""
    for n in ("src", "dst"):
        repos.storages.create(storage_name=n, mount_path=f"/{n}", managed_root=f"/{n}/m", backend_type="nfs", actor="t")
    payload = {"source_storage": "src", "source": "a", "destination_storage": "dst", "destination": "pv/out",
               "run_as_root": True, "options": {},
               "destination_pv": {"cluster": "c", "namespace": "n", "pvc": "p", "storage": "dst", "root": "pv",
                                  "owner_rule": "storage", "owner": "1500:1500"}}
    rid = repos.requests.create(operation="sync", requester_id="ops", actor="ops", resource_key="k",
                                payload=payload, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan = repos.data_jobs.create_plan(rid, actor="planner")
    pool = {"tool": "nsync", "identity": {"uid": 0, "gid": 0, "username": "ops", "groups": [], "privileged": True},
            "candidates": {"source": ["n1"], "destination": ["n2"]},
            "process_count": 2, "queue": "dms-data", "priority_class": "dms-mid"}
    jid = repos.data_jobs.create_job(rid, plan, operation="sync", priority="mid", source_storage="src", source="a",
                                     destination_storage="dst", destination="pv/out", options={}, tool="nsync",
                                     worker_pool=pool, precondition={}, actor="planner")
    return rid, jid


def test_stepper_pv_sync_preview_ref_lost_then_adopted_from_the_rebuilt_spec(db, monkeypatch):
    repos = Repositories(db)
    rid, jid = _pv_sync_job(repos)
    k8s = _StrictK8s()
    tick = lambda: JobStepper(repos, _volcano(repos, k8s), settings=_sync_settings()).run_once()[jid]  # noqa: E731
    assert tick() == "Preflight"
    for n in list(k8s.created):
        k8s.set_status("Pod", n, {"phase": "Succeeded"})
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")
    assert tick().startswith("error:")
    assert tick() == "PreviewRunning"
    job = repos.data_jobs.get_job(jid)
    assert job["reason_code"] is None and job["phase_refs"]["preview"].startswith("vcjob/dms-sync-preview-")
    assert len([n for n in k8s.created if n.startswith("dms-sync-preview-")]) == 1
    assert [e["payload"]["phase"] for e in _events(repos, rid, "submit_adopted")] == ["preview"]
    assert _events(repos, rid, "submit_failed") == []


def test_stepper_composite_preflight_strays_are_reclaimed_not_the_foreign_pod(db):
    # nsync 복합 preflight: dst 이름을 남의 객체가 쥐었다 -> Rejected. 이번 제출이 만든 src 는 stepper 가 지운다(기록되지 않는
    # ref 의 맨 파드 -- pod_gc 는 못 본다). 남의 dst 는 그대로.
    repos = Repositories(db)
    rid, jid = _pv_sync_job(repos)
    probe = _StrictK8s()
    assert JobStepper(repos, _volcano(repos, probe), settings=_sync_settings()).run_once()[jid] == "Preflight"
    src, dst = probe.created
    db.execute("UPDATE data_jobs SET state = 'Pending', phase_refs = NULL WHERE job_id = :j", {"j": jid})
    k8s = _StrictK8s()
    k8s.objs[("Pod", dst)] = copy.deepcopy(probe.objs[("Pod", dst)])
    k8s.objs[("Pod", dst)]["metadata"]["labels"]["dms.io/job-id"] = "f" * 32
    assert JobStepper(repos, _volcano(repos, k8s), settings=_sync_settings()).run_once()[jid] == "Rejected"
    assert k8s.deleted == [("Pod", src)] and ("Pod", dst) in k8s.objs
    [ev] = _events(repos, rid, "submit_failed")
    assert ev["payload"]["reclaimed"] == [f"pod/{src}"]


# ---- stepper: 겹친 컨트롤러의 낡은 스냅숏(빈 자리 CAS·상태 CAS) ----

def _transitions(repos, jid):
    return [(t["from_state"], t["to_state"]) for t in repos.data_jobs.job_transitions(jid)]


def test_stale_pending_snapshot_does_not_drag_a_running_job_back(db):
    # 리뷰 A 재현: B 가 Pending 때 claim 한 스냅숏을 A 가 Running 까지 보낸 뒤 스텝한다. 예전엔 409 채택 -> Running→Preflight.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    stale = repos.data_jobs.claim_steppable()[0]
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    k8s.set_status("Pod", k8s.created[0], {"phase": "Succeeded"})
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Running"
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings())._step_one(stale) == "Running"
    assert repos.data_jobs.get_job(jid)["state"] == "Running"
    assert _transitions(repos, jid) == [(None, "Pending"), ("Pending", "Preflight"), ("Preflight", "Running")]
    assert _events(repos, rid, "submit_adopted") == [] and k8s.deleted == []


def test_benign_overlap_leaves_no_self_transition_and_no_false_adoption_event(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    stale = repos.data_jobs.claim_steppable()[0]
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings())._step_one(stale) == "Preflight"
    assert _transitions(repos, jid) == [(None, "Pending"), ("Pending", "Preflight")]
    assert _events(repos, rid, "submit_adopted") == []      # ref 는 A 가 기록했다 -- 「기록하지 못한 객체」가 아니다


def test_stale_snapshot_fingerprint_drift_does_not_kill_the_other_steppers_object(db):
    # B 의 낡은 스냅숏은 그 사이 바뀐 행 값(여기선 재클램프 흉내 -- 프로세스 수)으로 지어 지문이 다르다. 라벨 셋은 이 잡이라
    # strays 로 오지만, A 가 이 단계를 이미 기록했으므로(_phase_taken) 지우지도 잡을 죽이지도 않는다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Preflight"
    stale = repos.data_jobs.claim_steppable()[0]
    k8s.set_status("Pod", k8s.created[0], {"phase": "Succeeded"})
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Running"
    stale["worker_pool"] = dict(stale["worker_pool"], process_count=4)
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings())._step_one(stale) == "Running"
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Running" and job["reason_code"] is None
    assert k8s.deleted == [] and _events(repos, rid, "submit_failed") == []


def _rm_job(repos):
    repos.storages.create(storage_name="s1", mount_path="/s1", managed_root="/s1/dms",
                          backend_type="cephfs", actor="test")
    rid = repos.requests.create(operation="rm", requester_id="alice", actor="alice", resource_key="k",
                                payload={"run_as_root": True, "storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation="rm", priority="mid", storage_name="s1", target="a", options={}, tool="drm",
        worker_pool={"tool": "drm", "identity": {"uid": 0, "gid": 0, "username": "alice", "groups": [],
                                                  "privileged": True},
                     "candidates": {"primary": ["n1"]}, "process_count": 8, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _rm_volcano(repos, k8s):
    import json
    return VolcanoExecutionAdapter(k8s, job_image=lambda: "reg/dms-job:d1", namespace="dms",
                                   storages_lookup=lambda n: repos.storages.get(n),
                                   read_text=lambda p: json.dumps({"returncode": 0, "files": 3, "bytes": None}),
                                   artifact_base="file:///art")


def _rm_to_preview_done(repos, k8s, jid):
    tick = lambda: JobStepper(repos, _rm_volcano(repos, k8s), settings=_Settings()).run_once().get(jid)  # noqa: E731
    assert tick() == "Preflight"
    k8s.set_status("Pod", k8s.created[0], {"phase": "Succeeded"})
    assert tick() == "PreviewRunning"
    [preview] = [n for n in k8s.created if "-preview-" in n]
    k8s.set_status("Job", preview, {"state": {"phase": "Completed"}})
    return tick


def test_stale_preview_poll_does_not_send_a_confirmed_rm_back_to_confirm_pending(db):
    # 리뷰 A 의 rm 재현 중 마지막 고리: 낡은 PreviewRunning 스냅숏의 ConfirmPending 전이가 컨펌된 Executing 을 되돌렸다.
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _StrictK8s()
    tick = _rm_to_preview_done(repos, k8s, jid)
    stale = repos.data_jobs.claim_steppable()[0]
    assert stale["state"] == "PreviewRunning"
    assert tick() == "ConfirmPending"
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})   # 컨펌
    assert JobStepper(repos, _rm_volcano(repos, k8s), settings=_Settings())._step_one(stale) == "Executing"
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
    assert ("Executing", "ConfirmPending") not in _transitions(repos, jid)


def test_two_stale_execution_submits_leave_a_single_executing_self_transition(db):
    # Executing→Executing 자기 전이는 스케줄 대기 측정이 유일성을 전제한다 -- 같은 실행 제출을 낡은 스냅숏 둘이 하면 상태 CAS
    # 로는 못 막는다(둘 다 Executing). 빈 자리 CAS(record_phase_ref "taken")가 뒤쪽을 멈춘다.
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _StrictK8s()
    tick = _rm_to_preview_done(repos, k8s, jid)
    assert tick() == "ConfirmPending"
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})
    assert tick() == "Executing"                                   # exec_preflight 제출
    [ep] = [n for n in k8s.created if "exec-preflight" in n]
    k8s.set_status("Pod", ep, {"phase": "Succeeded"})
    a, b = repos.data_jobs.claim_steppable()[0], repos.data_jobs.claim_steppable()[0]
    assert "execution" not in a["phase_refs"] and "execution" not in b["phase_refs"]
    assert JobStepper(repos, _rm_volcano(repos, k8s), settings=_Settings())._step_one(a) == "Executing"
    assert JobStepper(repos, _rm_volcano(repos, k8s), settings=_Settings())._step_one(b) == "Executing"
    assert _transitions(repos, jid).count(("Executing", "Executing")) == 1
    assert len([n for n in k8s.created if "-execution-" in n]) == 1
    assert _events(repos, rid, "submit_adopted") == []


def test_stepper_confirmed_rm_exec_preflight_ref_lost_then_adopted_and_execution_follows(db, monkeypatch):
    # 리뷰 [12]: 컨펌 뒤 재검증(exec_preflight -- 상태 전이 없이 Executing 에 머무는 제출)의 ref 가 끊긴 다음 틱. 잡은 Executing
    # 그대로고, 다음 틱이 같은 파드를 채택해 ref 를 남기며, 이어지는 실행 vcjob 은 새로 만든다(채택 이벤트는 exec_preflight 하나).
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _StrictK8s()
    tick = _rm_to_preview_done(repos, k8s, jid)
    assert tick() == "ConfirmPending"
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})   # 컨펌
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")
    assert tick().startswith("error:")
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Executing" and "exec_preflight" not in job["phase_refs"]
    [ep] = [n for n in k8s.created if "exec-preflight" in n]
    assert tick() == "Executing"
    job = repos.data_jobs.get_job(jid)
    assert job["phase_refs"]["exec_preflight"] == f"pod/{ep}" and job["reason_code"] is None
    assert [n for n in k8s.created if "exec-preflight" in n] == [ep]
    k8s.set_status("Pod", ep, {"phase": "Succeeded"})
    assert tick() == "Executing"
    assert repos.data_jobs.get_job(jid)["phase_refs"]["execution"].startswith("vcjob/dms-rm-execution-")
    assert [e["payload"]["phase"] for e in _events(repos, rid, "submit_adopted")] == ["exec_preflight"]
    assert _events(repos, rid, "submit_failed") == []
    assert _transitions(repos, jid).count(("Executing", "Executing")) == 1      # 실행 제출의 자기 전이 하나


def test_stepper_finished_recheck_from_the_lost_ref_window_is_rerun_not_adopted(db, monkeypatch):
    # 리뷰 2회차 [3]: 기록이 끊긴 틱의 재점검 파드가 다음 제출 때 이미 끝나 있다(그 사이 첫 관문 보류로 겹치는 rm 이 먼저 돌았을 수
    # 있다 -- 기록이 없으니 이 잡은 단계 0 으로 보였다). 그 결과로 실행에 가지 않는다: 지우고(이벤트) 다음 틱에 새로 점검한다.
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _StrictK8s()
    tick = _rm_to_preview_done(repos, k8s, jid)
    assert tick() == "ConfirmPending"
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})   # 컨펌
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")
    assert tick().startswith("error:")
    [ep] = [n for n in k8s.created if "exec-preflight" in n]
    k8s.set_status("Pod", ep, {"phase": "Succeeded"})
    assert tick() == "error:SubmitUndecided"
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Executing" and job["reason_code"] is None and "exec_preflight" not in job["phase_refs"]
    assert ("Pod", ep) in k8s.deleted and ("Pod", ep) not in k8s.objs
    [ev] = _events(repos, rid, "unrecorded_reclaimed")
    assert ev["payload"] == {"job_id": jid, "reclaimed": [f"pod/{ep}"], "reclaim_failed": [], "phase": "exec_preflight"}
    assert "이미 끝난 실행 전 재점검" in ev["message"]
    assert tick() == "Executing"                                     # 새 재점검 파드(같은 이름)를 만들고 기록
    assert repos.data_jobs.get_job(jid)["phase_refs"]["exec_preflight"] == f"pod/{ep}"
    assert k8s.created.count(ep) == 2 and k8s.objs[("Pod", ep)].get("status") is None
    k8s.set_status("Pod", ep, {"phase": "Succeeded"})
    assert tick() == "Executing"
    assert repos.data_jobs.get_job(jid)["phase_refs"]["execution"].startswith("vcjob/dms-rm-execution-")
    assert _events(repos, rid, "submit_adopted") == [] and _events(repos, rid, "submit_failed") == []


def test_stepper_does_not_delete_a_finished_recheck_another_stepper_recorded(db):
    # 겹친 컨트롤러: 낡은 스냅숏(재점검 ref 없음)이 제출할 때 그 파드는 다른 stepper 가 기록했고 이미 끝났다 -- 그쪽이 추적하는
    # 결과라 지우지 않는다(_phase_taken). 이 틱은 미결로 끝나고(아무것도 바꾸지 않음) 다음 정상 틱이 이어 간다.
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _StrictK8s()
    tick = _rm_to_preview_done(repos, k8s, jid)
    assert tick() == "ConfirmPending"
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})
    stale = repos.data_jobs.claim_steppable()[0]
    assert tick() == "Executing"
    [ep] = [n for n in k8s.created if "exec-preflight" in n]
    k8s.set_status("Pod", ep, {"phase": "Succeeded"})
    with pytest.raises(SubmitUndecided):
        JobStepper(repos, _rm_volcano(repos, k8s), settings=_Settings())._step_one(stale)
    assert k8s.deleted == [] and _events(repos, rid, "unrecorded_reclaimed") == []
    assert tick() == "Executing"
    assert repos.data_jobs.get_job(jid)["phase_refs"]["execution"].startswith("vcjob/dms-rm-execution-")


# ---- stepper: 성공 아닌 종단은 ref 없이 남은 객체를 라벨로 회수(_finalize -> _reclaim_unrecorded) ----

def test_fail_closed_reclaims_the_object_a_lost_ref_left_behind(db, monkeypatch):
    # 틱 1 이 preflight 파드를 만들고 ref 기록이 끊겼다. 틱 2 는 채택 전에 _build_spec 이 fail-closed(스토리지 행 삭제) --
    # phase_refs 엔 아무것도 없지만 라벨(전체 job_id)로 찾아 지운다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid].startswith("error:")
    [pod] = k8s.created
    db.execute("DELETE FROM storages WHERE storage_name = 's1'")
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Rejected"
    assert repos.data_jobs.get_job(jid)["reason_code"] == "storage_missing_at_step"
    assert k8s.deleted == [("Pod", pod)]
    [ev] = _events(repos, rid, "unrecorded_reclaimed")
    assert ev["payload"] == {"job_id": jid, "reclaimed": [f"pod/{pod}"], "reclaim_failed": []}


def test_batch_cancel_stop_reclaims_the_object_a_lost_ref_left_behind(db, monkeypatch):
    # 틱 1 이 실행 vcjob 을 만들고 ref 기록이 끊겼다. 틱 2 는 배치 취소로 제출 전에 Cancelled -- 그 vcjob 도 회수한다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    vcjob = _to_execution_lost(repos, k8s, monkeypatch, rid, jid)
    stepper = JobStepper(repos, _volcano(repos, k8s), settings=_Settings())
    monkeypatch.setattr(stepper, "_batch_cancelling", lambda job: True)
    assert stepper.run_once()[jid] == "Cancelled"
    assert ("Job", vcjob) in k8s.deleted and ("Pod", k8s.created[0]) not in k8s.deleted   # 기록된 preflight 는 대상 아님
    assert len(_events(repos, rid, "unrecorded_reclaimed")) == 1


def test_vanished_preflight_pod_terminal_reclaims_the_execution_vcjob_a_lost_ref_left_behind(db, monkeypatch):
    # 리뷰 2회차 [1]: 실행 vcjob 을 만들고 ref 기록이 끊긴 뒤, 다음 틱 전에 (이미 성공한) preflight 파드가 사라졌다(노드 소실·수동
    # 삭제). 폴링이 FAILED 로 읽어 preflight_failed 로 끝나는데 -- fail-closed 도 배치 취소도 아닌 _finalize 직행 경로다. 예전엔
    # 잡은 Rejected 인데 실행 vcjob 은 ref 없이 계속 돌았다(pod_gc 는 phase_refs 만 본다).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    vcjob = _to_execution_lost(repos, k8s, monkeypatch, rid, jid)
    k8s.objs.pop(("Pod", k8s.created[0]))
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Rejected"
    assert repos.data_jobs.get_job(jid)["reason_code"] == "preflight_failed"
    assert k8s.deleted == [("Job", vcjob)] and ("Job", vcjob) not in k8s.objs
    [ev] = _events(repos, rid, "unrecorded_reclaimed")
    assert ev["payload"] == {"job_id": jid, "reclaimed": [f"vcjob/{vcjob}"], "reclaim_failed": []}


def test_vanished_exec_preflight_pod_terminal_reclaims_the_rm_execution_vcjob(db, monkeypatch):
    # 같은 구멍의 rm 판(execution_recheck_failed): 실제 drm 실행 vcjob 이 Rejected 잡 밑에서 화면 밖 삭제를 이어 가지 않는다.
    repos = Repositories(db)
    rid, jid = _rm_job(repos)
    k8s = _StrictK8s()
    tick = _rm_to_preview_done(repos, k8s, jid)
    assert tick() == "ConfirmPending"
    db.execute("UPDATE data_jobs SET state = 'Executing' WHERE job_id = :j", {"j": jid})
    assert tick() == "Executing"
    [ep] = [n for n in k8s.created if "exec-preflight" in n]
    k8s.set_status("Pod", ep, {"phase": "Succeeded"})
    _fail_once(monkeypatch, repos.data_jobs, "record_phase_ref")
    assert tick().startswith("error:")
    [ex] = [n for n in k8s.created if "-execution-" in n]
    k8s.objs.pop(("Pod", ep))
    assert tick() == "Rejected"
    assert repos.data_jobs.get_job(jid)["reason_code"] == "execution_recheck_failed"
    assert ("Job", ex) in k8s.deleted and ("Job", ex) not in k8s.objs
    assert len(_events(repos, rid, "unrecorded_reclaimed")) == 1


def test_successful_terminal_does_not_look_for_unrecorded_objects(db):
    # 성공은 마지막 단계(실행)까지 ref 가 기록된 뒤에만 온다 -- 성공 종단마다 라벨 조회(목록 두 번)를 하지 않는다.
    repos = Repositories(db)
    _rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    tick = lambda: JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid]  # noqa: E731
    assert tick() == "Preflight"
    k8s.set_status("Pod", k8s.created[0], {"phase": "Succeeded"})
    assert tick() == "Running"
    k8s.set_status("Job", k8s.created[1], {"state": {"phase": "Completed"}})
    assert tick() == "Succeeded"
    assert k8s.lists == [] and k8s.deleted == []


def test_unrecorded_refs_selects_only_this_jobs_untracked_vcjobs_and_preflight_pods():
    k8s = _StrictK8s()
    a = _adapter(k8s)
    pre = a.submit(_spec("preflight"))
    exe = a.submit(_spec("execution"))
    other = "0123456789ab" + "d" * 20                                          # 앞 12자가 같은 다른 잡
    k8s.objs[("Job", "other-vcjob")] = {"metadata": {"labels": {"dms.io/job-id": other, "dms.io/phase": "preview"}}}
    k8s.objs[("Pod", "other-pod")] = {"metadata": {"labels": {"dms.io/job-id": other, "dms.io/phase": "preflight"}}}
    # vcjob 의 worker 파드(phase 라벨 없음 -- vcjob 을 지우면 함께 간다)
    k8s.objs[("Pod", "w-0")] = {"metadata": {"labels": {"dms.io/job-id": JOB, "dms.io/task": "worker"}}}
    assert sorted(a.unrecorded_refs(JOB, [])) == sorted([pre, exe])
    assert a.unrecorded_refs(JOB, [pre]) == [exe]
    assert a.unrecorded_refs(JOB, [pre, exe]) == []
    k8s.objs[("Job", _names(exe)[0])]["metadata"]["deletionTimestamp"] = "2026-11-03T00:00:01Z"
    assert a.unrecorded_refs(JOB, [pre]) == []                                    # 이미 지워지는 중
    k8s.lists.clear()
    assert a.unrecorded_refs("x,dms.io/phase", []) == [] and k8s.lists == []      # 셀렉터를 넓히는 id 는 묻지도 않는다


def test_reclaim_sweep_failure_is_recorded_and_does_not_block_the_terminal_transition(db, monkeypatch):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()

    def boom(namespace, label_selector):
        raise RuntimeError("list forbidden")
    k8s.list_vcjob_briefs = boom
    db.execute("DELETE FROM storages WHERE storage_name = 's1'")
    assert JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()[jid] == "Rejected"
    assert any("unrecorded sweep" in e["message"] for e in _events(repos, rid, "terminate_failed"))


# ---- 취소: 커밋 뒤 ref 없이 남은 객체도 라벨로 끝낸다(api.cancel._sweep_unrecorded) ----

def _user_cancel(repos, adapter, jid):
    """routes_jobs.cancel_job 의 순서 그대로: 스냅숏 ref 종료 -> Cancelled 커밋 -> 커밋 뒤 회수."""
    from dms.api.cancel import sweep_after_cancel, terminate_job
    snap = repos.data_jobs.get_job(jid)
    terminate_job(adapter, snap)
    repos.data_jobs.set_job_state(jid, DataJobState.CANCELLED, reason_code="cancelled_by_user", actor="alice")
    return sweep_after_cancel(repos, adapter, snap)


def test_user_cancel_inside_the_lost_ref_window_ends_the_untracked_execution_vcjob(db, monkeypatch):
    # 틱 1 이 실행 vcjob 을 만들고 ref 기록이 끊겼다. 다음 틱(채택)보다 사용자 취소가 먼저 왔다 -- 예전엔 phase_refs 에 없어
    # 스냅숏·커밋 뒤 두 읽기 모두 못 보고 vcjob 이 Cancelled 잡 밑에서 끝까지 돌았다(rm 이면 화면 밖 삭제).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    vcjob = _to_execution_lost(repos, k8s, monkeypatch, rid, jid)
    [pre] = [n for n in k8s.created if n.startswith("dms-preflight-")]
    assert _user_cancel(repos, _volcano(repos, k8s), jid) == "Cancelled"
    assert ("Job", vcjob) in k8s.deleted and ("Pod", pre) in k8s.deleted     # 기록된 것은 스냅숏 루프, 창 객체는 스윕
    [ev] = _events(repos, rid, "unrecorded_reclaimed")
    assert ev["payload"] == {"job_id": jid, "reclaimed": [f"vcjob/{vcjob}"], "after_cancel": True}
    # 다음 틱은 종단 잡을 고르지 않는다 -- 다시 만들지도 않는다.
    assert jid not in JobStepper(repos, _volcano(repos, k8s), settings=_Settings()).run_once()
    assert k8s.created.count(vcjob) == 1


def test_cancel_sweep_failure_is_an_error_event_and_the_cancel_still_stands(db, monkeypatch):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    vcjob = _to_execution_lost(repos, k8s, monkeypatch, rid, jid)
    real_delete = k8s.delete

    def delete(kind, name, namespace):          # 창 객체만 못 지운다(기록된 preflight 파드는 지워진다)
        if name == vcjob:
            raise RuntimeError("delete forbidden")
        return real_delete(kind, name, namespace)
    k8s.delete = delete
    assert _user_cancel(repos, _volcano(repos, k8s), jid) == "Cancelled"
    failed = [e for e in _events(repos, rid, "terminate_failed") if (e["payload"] or {}).get("unrecorded")]
    assert len(failed) == 1 and failed[0]["severity"] == "error" and "추적 밖에서 돌 수 있음" in failed[0]["message"]
    assert _events(repos, rid, "unrecorded_reclaimed") == []

    def boom(namespace, label_selector):
        raise RuntimeError("list forbidden")
    k8s.list_vcjob_briefs = boom
    from dms.api.cancel import sweep_after_cancel
    assert sweep_after_cancel(repos, _volcano(repos, k8s), repos.data_jobs.get_job(jid)) == "Cancelled"
    assert any("unrecorded sweep after cancel" in e["message"] for e in _events(repos, rid, "terminate_failed"))


def test_cancel_sweep_does_nothing_when_the_stepper_finished_first(db):
    # 커밋 뒤 다시 읽은 상태가 Cancelled 가 아니면(stepper 가 먼저 종단 -- 종단 가드가 Cancelled 를 삼켰다) 아무것도 끝내지 않는다
    # (거짓 취소 금지 -- 2026-10-12 규칙 그대로). 조회도 하지 않는다.
    from dms.api.cancel import sweep_after_cancel
    repos = Repositories(db)
    _rid, jid = _scan_job(repos)
    k8s = _StrictK8s()
    snap = repos.data_jobs.get_job(jid)
    repos.data_jobs.set_job_state(jid, DataJobState.REJECTED, reason_code="preflight_failed", actor="stepper")
    assert sweep_after_cancel(repos, _volcano(repos, k8s), snap) == "Rejected"
    assert k8s.lists == [] and k8s.deleted == []


# ---- 저장소: 빈 자리 CAS·상태 CAS ----

def test_record_phase_ref_outcomes(db):
    repos = Repositories(db)
    _rid, jid = _scan_job(repos)
    dj = repos.data_jobs
    assert dj.record_phase_ref(jid, "preflight", "pod/a", expect_absent=True) == "recorded"
    assert dj.record_phase_ref(jid, "preflight", "pod/a", expect_absent=True) == "taken"
    assert dj.record_phase_ref(jid, "preflight", "pod/b", expect_absent=False) == "recorded"
    assert dj.get_job(jid)["phase_refs"] == {"preflight": "pod/b"}
    assert dj.record_phase_ref("0" * 32, "preflight", "pod/a", expect_absent=True) == "gone"
    assert dj.set_phase_ref(jid, "execution", "vcjob/x") is True


def test_set_job_state_expected_state_is_a_compare_and_set(db):
    repos = Repositories(db)
    _rid, jid = _scan_job(repos)
    dj = repos.data_jobs
    assert dj.set_job_state(jid, DataJobState.PREFLIGHT, actor="stepper", expected_state="Pending") is True
    assert dj.set_job_state(jid, DataJobState.PREFLIGHT, actor="stepper", expected_state="Pending") is False
    assert dj.set_job_state(jid, DataJobState.RUNNING, actor="stepper",
                            expected_state=DataJobState.PREFLIGHT) is True
    assert [(t["from_state"], t["to_state"]) for t in dj.job_transitions(jid)] == [
        (None, "Pending"), ("Pending", "Preflight"), ("Preflight", "Running")]
    assert dj.set_job_state(jid, DataJobState.FAILED, actor="stepper") is True       # 종전 호출(기대 상태 없음)
    assert dj.set_job_state(jid, DataJobState.RUNNING, actor="stepper") is False      # 종단 가드
