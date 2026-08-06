import pytest
from dms.build_runner import BuildRunner, StubBuildRunner
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


def _runner(k8s):
    return BuildRunner(k8s, namespace="dms", registry="pkg-01:5000",
                       builder_image="quay.io/buildah/stable:latest")


def test_submit_creates_pod_and_returns_buildpod_ref():
    k8s = _FakeK8s()
    ref = _runner(k8s).submit(BUILD)
    assert ref == "buildpod/dms-build-0123456789ab"
    assert k8s.created[0]["kind"] == "Pod"


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
    assert _runner(k8s).poll("buildpod/gone") == ExecStatus.FAILED


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
        _runner(_Boom()).poll("buildpod/dms-build-0123456789ab")
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
    assert ref.startswith("buildpod/")
    assert stub.poll(ref) == ExecStatus.SUCCEEDED
    assert stub.read_log(ref) is not None
    stub.terminate(ref)
