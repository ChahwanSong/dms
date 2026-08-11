import pytest
from dms.build_runner import BUILD_REF_PREFIX, BuildRunner, StubBuildRunner
from dms.execution import ExecStatus, ExecutionError


class _FakeK8s:
    def __init__(self):
        self.created = []
        self.deleted = []
        self._objs = {}
        self.fail_create = False
        self.log = "hello"

    def create(self, manifest):
        if self.fail_create:
            raise RuntimeError("boom")
        self.created.append(manifest)
        self._objs[(manifest["kind"], manifest["metadata"]["name"])] = manifest

    def set_status(self, name, status):
        self._objs.setdefault(("Pod", name), {"kind": "Pod"})["status"] = status

    def get(self, kind, name, namespace):
        return self._objs.get((kind, name))

    def delete(self, kind, name, namespace):
        self.deleted.append((kind, name))
        self._objs.pop((kind, name), None)

    def read_pod_log(self, name, namespace):
        return self.log


BUILD = {"build_id": "0123456789abcdef0123456789abcdef", "repo_url": "u",
         "git_ref": "main", "images": ["dms"], "node_name": "dms-w1"}


def _runner(k8s, timeout_seconds=7200):
    return BuildRunner(k8s, namespace="dms", registry="pkg-01:5000",
                       builder_image="quay.io/buildah/stable:latest",
                       timeout_seconds=timeout_seconds)


def test_build_ref_prefix_is_buildpod():
    # I5: 4곳(build_runner/build_watcher/pod_gc/routes_builds)이 이 상수 하나를
    # 공유한다 -- 각자 리터럴을 들고 있으면 하나만 드리프트해도 조용히 깨진다.
    assert BUILD_REF_PREFIX == "buildpod"


def test_submit_creates_pod_and_returns_buildpod_ref():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    assert ref == f"{BUILD_REF_PREFIX}/dms-build-0123456789ab"
    assert k8s.created[0]["kind"] == "Pod"


def test_submit_pins_active_deadline_seconds_from_constructor():
    # C2(a): BuildRunner가 받은 timeout_seconds가 실제로 파드 spec까지 전달되는지.
    k8s = _FakeK8s()
    _runner(k8s, timeout_seconds=42).submit(BUILD)
    assert k8s.created[0]["spec"]["activeDeadlineSeconds"] == 42


def test_submit_failure_becomes_execution_error():
    k8s = _FakeK8s()
    k8s.fail_create = True
    with pytest.raises(ExecutionError) as e:
        _runner(k8s).submit(BUILD)
    assert e.value.reason_code == "submit_failed"


def test_submit_manifest_build_failure_becomes_execution_error():
    # build_build_pod(...) 호출 자체가 실패해도(예: build dict에 필수 키가
    # 빠짐) 원시 KeyError가 아니라 ExecutionError(submit_failed)로 나와야
    # 한다 -- 호출자(BuildWatcher)는 ExecutionError만 잡아 빌드를 Failed로
    # 기록하므로, 새어나간 예외는 루프를 죽이고 빌드가 Running에 영원히 남는다.
    k8s = _FakeK8s()
    incomplete = {k: v for k, v in BUILD.items() if k != "images"}
    with pytest.raises(ExecutionError) as e:
        _runner(k8s).submit(incomplete)
    assert e.value.reason_code == "submit_failed"


@pytest.mark.parametrize("phase,expected", [
    ("Pending", ExecStatus.PENDING), ("Running", ExecStatus.RUNNING),
    ("Succeeded", ExecStatus.SUCCEEDED), ("Failed", ExecStatus.FAILED),
    ("Unknown", ExecStatus.FAILED)])
def test_poll_maps_pod_phase(phase, expected):
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    k8s.set_status("dms-build-0123456789ab", {"phase": phase})
    assert _runner(k8s).poll(ref) == expected


def test_poll_treats_missing_pod_as_failed():
    # 어댑터 규약과 같다: 사라진 객체는 '모름'이 아니라 실패다
    k8s = _FakeK8s()
    assert _runner(k8s).poll(f"{BUILD_REF_PREFIX}/gone") == ExecStatus.FAILED


def test_read_log_returns_text_and_none_when_unavailable():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    assert _runner(k8s).read_log(ref) == "hello"

    class _Boom(_FakeK8s):
        def read_pod_log(self, name, namespace):
            raise RuntimeError("gone")
    assert _runner(_Boom()).read_log(ref) is None


def test_terminate_is_idempotent():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    r = _runner(k8s)
    r.terminate(ref)
    r.terminate(ref)
    assert k8s.deleted[0] == ("Pod", "dms-build-0123456789ab")


def test_non_buildpod_ref_is_rejected():
    with pytest.raises(ExecutionError) as e:
        _runner(_FakeK8s()).poll("vcjob/whatever")
    assert e.value.reason_code == "invalid_build_ref"


def test_poll_wraps_k8s_get_exception_as_execution_error():
    # k8s.get이 404가 아니라 5xx/네트워크 예외를 던지면 원시 예외가 아니라
    # ExecutionError여야 한다 -- BuildWatcher는 ExecutionError만 잡아 빌드를
    # Failed로 기록하므로, 새어나간 원시 예외는 run_once()를 죽이고
    # (running()이 seq ASC라) 그 뒤 모든 빌드가 매 틱 처리되지 않는다.
    class _Boom(_FakeK8s):
        def get(self, kind, name, namespace):
            raise RuntimeError("connection reset")
    with pytest.raises(ExecutionError) as e:
        _runner(_Boom()).poll(f"{BUILD_REF_PREFIX}/dms-build-0123456789ab")
    assert e.value.reason_code == "poll_failed"


def test_poll_invalid_ref_is_not_reported_as_poll_failed():
    # _name(ref)가 던지는 invalid_build_ref는 k8s.get을 부르기도 전에 나야
    # 한다 -- get이 호출되면 "should not be called"이 RuntimeError로 새어나와
    # poll_failed로 둔갑했을 것이므로, get을 절대 안 부르는지까지 확인한다.
    class _Boom(_FakeK8s):
        def get(self, kind, name, namespace):
            raise RuntimeError("should not be called")
    with pytest.raises(ExecutionError) as e:
        _runner(_Boom()).poll("vcjob/whatever")
    assert e.value.reason_code == "invalid_build_ref"


def test_submit_is_idempotent_when_pod_already_exists():
    # 재시도 시나리오: submit 성공 직후 mark_running 전에 프로세스가 죽으면
    # 다음 틱이 같은 build_id로 다시 submit한다. 파드 이름이 build_id에서
    # 결정적으로 나오므로 k8s.create가 AlreadyExists류로 실패해도 그 이름의
    # 파드가 이미 있다면 그건 이 빌드 자신의 파드다 -- k8s.delete가 404를
    # 삼켜 멱등한 것과 같은 계약으로, 존재를 확인해 성공으로 취급해야 한다.
    # 안 그러면 잘 도는 빌드를 Failed로 오기록한다.
    k8s = _FakeK8s()
    ref1 = _runner(k8s).submit(BUILD)
    k8s.fail_create = True  # 재시도: create가 실패(예: AlreadyExists)
    ref2 = _runner(k8s).submit(BUILD)
    assert ref2 == ref1


def test_submit_failure_with_no_existing_pod_is_still_submit_failed():
    # create가 실패했는데 그 이름의 파드도 없다면(진짜 실패) 여전히
    # submit_failed여야 한다 -- 존재 확인 폴백이 진짜 실패까지 삼키면 안 된다.
    k8s = _FakeK8s()
    k8s.fail_create = True
    with pytest.raises(ExecutionError) as e:
        _runner(k8s).submit(BUILD)
    assert e.value.reason_code == "submit_failed"


def test_stub_runner_runs_without_a_cluster():
    stub = StubBuildRunner()
    ref = stub.submit(BUILD)
    assert ref.startswith(f"{BUILD_REF_PREFIX}/")
    assert stub.poll(ref) == ExecStatus.SUCCEEDED
    assert stub.read_log(ref) is not None
    stub.terminate(ref)


# ---- 슬라이스 21 §2.5: submit_preflight (프로브 파드 멱등 제출) ----

def _pf_runner(k8s):
    return BuildRunner(k8s, namespace="dms", registry="pkg-01:5000",
                       builder_image="quay.io/buildah/stable:latest",
                       timeout_seconds=7200,
                       job_image="pkg-01:5000/dms-mpifileutils:d27",
                       preflight_timeout_seconds=180)


PF_BUILD = {**BUILD, "repo_url": "https://github.com/ChahwanSong/dms.git"}


def test_submit_preflight_creates_probe_pod_under_the_buildpod_ref():
    k8s = _FakeK8s()
    runner = _pf_runner(k8s)
    ref = runner.submit_preflight(PF_BUILD)
    assert ref == f"{BUILD_REF_PREFIX}/dms-build-pf-0123456789ab"
    pod = k8s.created[0]
    assert pod["metadata"]["name"] == "dms-build-pf-0123456789ab"
    assert pod["spec"]["containers"][0]["image"] == "pkg-01:5000/dms-mpifileutils:d27"
    assert pod["spec"]["activeDeadlineSeconds"] == 180
    # 같은 buildpod/ ref 계약이라 poll/read_log/terminate 가 공짜다 -- 이게
    # 프로브에 별도 러너를 만들지 않은 이유다.
    k8s.set_status("dms-build-pf-0123456789ab", {"phase": "Succeeded"})
    assert runner.poll(ref) == ExecStatus.SUCCEEDED


def test_submit_preflight_is_idempotent_when_probe_already_exists():
    # 워처가 매 틱 재호출한다 -- AlreadyExists 를 실패로 접으면 두 번째 틱부터
    # 멀쩡한 빌드가 전부 Failed 다(submit 의 관용 선례와 같은 계약).
    k8s = _FakeK8s()
    ref1 = _pf_runner(k8s).submit_preflight(PF_BUILD)
    k8s.fail_create = True
    ref2 = _pf_runner(k8s).submit_preflight(PF_BUILD)
    assert ref2 == ref1


def test_submit_preflight_unparseable_repo_url_is_submit_failed():
    # 라우트가 제출 시점에 invalid_repo_url 로 거르지만(§2.5 동기), 검증 전에
    # 만들어진 구형 Pending 행이 남아 있을 수 있다 -- 원시 ValueError 가 아니라
    # ExecutionError(submit_failed)로 나와야 워처가 Failed 로 기록한다.
    with pytest.raises(ExecutionError) as e:
        _pf_runner(_FakeK8s()).submit_preflight({**BUILD, "repo_url": "not a url"})
    assert e.value.reason_code == "submit_failed"
    assert e.value.detail.startswith("preflight:")   # 빌드 파드 제출 실패와 구분


def test_submit_preflight_create_failure_without_existing_pod_raises():
    k8s = _FakeK8s()
    k8s.fail_create = True
    with pytest.raises(ExecutionError) as e:
        _pf_runner(k8s).submit_preflight(PF_BUILD)
    assert e.value.reason_code == "submit_failed"


def test_stub_submit_preflight_is_immediately_ok_without_a_cluster():
    # 스텁 경로 계약(설계 §4): 프리플라이트 포함 즉시 성공 -- poll 은 어떤 ref 든
    # SUCCEEDED 이므로 OK 마커 로그만 있으면 워처가 같은 틱에 빌드 제출로 넘어간다.
    stub = StubBuildRunner()
    ref = stub.submit_preflight(BUILD)
    assert ref == f"{BUILD_REF_PREFIX}/dms-build-pf-0123456789ab"
    assert stub.poll(ref) == ExecStatus.SUCCEEDED
    assert "DMS_PREFLIGHT_OK" in stub.read_log(ref)
