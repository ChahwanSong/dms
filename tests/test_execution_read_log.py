import logging

import pytest
from dms.execution import ExecutionError, StubExecutionAdapter
from dms.execution_volcano import VolcanoExecutionAdapter


class _FakeK8s:
    def __init__(self):
        self._logs = {}
        self._fail_pods = set()
        self._briefs = {}          # label_selector -> list[brief]
        self._briefs_error = None
        self.asked_selectors = []

    def read_pod_log(self, name, namespace):
        if name in self._fail_pods:
            raise RuntimeError("pod not found")
        return self._logs.get(name, "")

    def list_pod_briefs(self, namespace, label_selector):
        self.asked_selectors.append(label_selector)
        if self._briefs_error is not None:
            raise self._briefs_error
        return self._briefs.get(label_selector, [])

    def set_log(self, name, text):
        self._logs[name] = text

    def fail_log(self, name):
        self._fail_pods.add(name)

    def set_briefs(self, selector, briefs):
        self._briefs[selector] = briefs

    def fail_briefs(self, exc):
        self._briefs_error = exc


def _adapter(k8s):
    return VolcanoExecutionAdapter(
        k8s, job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs", "managed_root": "/cephfs/dms"},
        read_text=lambda path: None,
        artifact_base="file:///cephfs/dms/artifacts")


def test_read_log_single_pod_ref():
    k8s = _FakeK8s()
    k8s.set_log("p1", "hello log")
    a = _adapter(k8s)
    assert a.read_log("pod/p1") == [("p1", "hello log", None)]


def test_read_log_dual_pods_ref():
    k8s = _FakeK8s()
    k8s.set_log("p1", "log1")
    k8s.set_log("p2", "log2")
    a = _adapter(k8s)
    assert a.read_log("pods/p1,p2") == [("p1", "log1", None), ("p2", "log2", None)]


def test_read_log_missing_pod_yields_none():
    k8s = _FakeK8s()
    k8s.set_log("p1", "log1")
    k8s.fail_log("p2")
    a = _adapter(k8s)
    assert a.read_log("pods/p1,p2") == [("p1", "log1", None), ("p2", None, None)]


def test_read_log_failure_leaves_a_trace(caplog):
    # bare except가 RBAC 거부·설정 오류·프로그래밍 버그를 전부 "파드가 GC됐다"와
    # 똑같이 렌더한다. 반환 계약(log=None)은 그대로 두되, 최소한 흔적은 남겨야 한다.
    k8s = _FakeK8s()
    k8s.set_log("p1", "log1")
    k8s.fail_log("p2")
    a = _adapter(k8s)
    with caplog.at_level(logging.WARNING, logger="dms.execution_volcano"):
        assert a.read_log("pods/p1,p2") == [("p1", "log1", None), ("p2", None, None)]
    assert any("p2" in r.getMessage() for r in caplog.records), caplog.text


def test_read_log_rejects_unknown_prefix():
    # vcjob 은 이제 열렸다(슬라이스 25) -- 409 log_not_available 은 미지 prefix
    # 방어로만 남는다(설계 §2.5, 문구 무변경).
    a = _adapter(_FakeK8s())
    with pytest.raises(ExecutionError) as exc_info:
        a.read_log("widget/j1")
    assert exc_info.value.reason_code == "log_not_available"


# ---- 슬라이스 25 §2.1: vcjob 로그 -- launcher 항상 + Failed 파드만 ----

_SEL = "volcano.sh/job-name=dms-sync-execution-abc"


def _vcjob_k8s():
    k8s = _FakeK8s()
    k8s.set_briefs(_SEL, [
        {"name": "dms-sync-execution-abc-worker-0", "phase": "Succeeded",
         "waiting_reason": None},
        {"name": "dms-sync-execution-abc-worker-1", "phase": "Failed",
         "waiting_reason": None},
        {"name": "dms-sync-execution-abc-launcher-0", "phase": "Failed",
         "waiting_reason": None},
    ])
    k8s.set_log("dms-sync-execution-abc-launcher-0", "Traceback ...")
    k8s.set_log("dms-sync-execution-abc-worker-1", "worker died")
    return k8s


def test_vcjob_read_log_selects_launcher_first_then_failed_workers_only():
    # 성공 워커의 sshd 로그는 노이즈고(설계 §1-2 에 따라 대개 이미 없다), 남는
    # 파드는 실패 원인 파드다. launcher 가 앞이어야 박제 상한(항목 4, Task 4)이
    # 잘라도 launcher 가 산다.
    k8s = _vcjob_k8s()
    out = _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert out == [
        ("dms-sync-execution-abc-launcher-0", "Traceback ...", None),
        ("dms-sync-execution-abc-worker-1", "worker died", None),
    ]
    # 셀렉터는 ref 의 이름으로 조립된다 -- vcjob GET 이 없어도 된다(설계 §2.1).
    assert k8s.asked_selectors == [_SEL]


def test_vcjob_launcher_is_included_even_when_succeeded():
    # Completed 잡도 launcher-0 은 잔존한다(설계 §1-1 실측). 진행 중/성공 launcher
    # 의 라이브 tail 이 이 분기로 공짜다 -- phase 로 launcher 를 거르면 안 된다.
    k8s = _FakeK8s()
    k8s.set_briefs(_SEL, [
        {"name": "dms-sync-execution-abc-launcher-0", "phase": "Succeeded",
         "waiting_reason": None},
    ])
    k8s.set_log("dms-sync-execution-abc-launcher-0", "")
    out = _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert out == [("dms-sync-execution-abc-launcher-0", "", None)]   # 빈 로그는 정상값


def test_vcjob_empty_log_is_not_confused_with_missing_log():
    # 빈 문자열("")과 null(얻을 수 없었다)은 다른 값이다 -- 이 슬라이스의 심장.
    # 위 테스트의 == 단언은 ""와 None 을 구분하지만, "구분한다"는 계약 자체를
    # 명시적으로 박아 두지 않으면 truthy 접기(if log:)가 슬며시 들어온다.
    k8s = _FakeK8s()
    k8s.set_briefs(_SEL, [
        {"name": "dms-sync-execution-abc-launcher-0", "phase": "Failed",
         "waiting_reason": None},
        {"name": "dms-sync-execution-abc-worker-0", "phase": "Failed",
         "waiting_reason": None},
    ])
    k8s.set_log("dms-sync-execution-abc-launcher-0", "")   # 기동은 했는데 출력이 없다
    k8s.fail_log("dms-sync-execution-abc-worker-0")        # 로그를 얻을 수 없었다
    out = _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert out[0][1] == "" and out[0][1] is not None
    assert out[1][1] is None


def test_vcjob_waiting_reason_rides_alongside_null_log():
    # ImagePullBackOff 파드는 로그가 없다 -- "없다"(null)와 "왜 없는지"
    # (waiting_reason)는 별 채널이다. null 을 합성 문자열로 뭉개지 않는다(설계 §2.1).
    k8s = _FakeK8s()
    k8s.set_briefs(_SEL, [
        {"name": "dms-sync-execution-abc-launcher-0", "phase": "Pending",
         "waiting_reason": "ImagePullBackOff"},
    ])
    k8s.fail_log("dms-sync-execution-abc-launcher-0")
    out = _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert out == [("dms-sync-execution-abc-launcher-0", None, "ImagePullBackOff")]


def test_vcjob_with_no_pods_left_returns_empty_list_not_error():
    # vcjob TTL·pod GC 로 파드가 전멸하면 브리프가 0건이다 -- 이건 실패가 아니라
    # "0 항목"이라는 정보고, 박제 폴백(Task 6)의 조건이다. 조회 계층 실패
    # (poll_failed)와 절대 섞이면 안 된다.
    k8s = _FakeK8s()
    out = _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert out == []
    assert k8s.asked_selectors == [_SEL]


def test_vcjob_list_failure_raises_poll_failed_instead_of_empty():
    # per-pod 실패(null 접기)와 조회 계층 실패는 다르다: RBAC 403 이 "로그 없음"
    # 으로 렌더된 사고(execution_volcano.py:393-398 교훈)를 반복하지 않는다 --
    # list 예외는 409 로 표면화한다(설계 §2.1).
    k8s = _FakeK8s()
    k8s.fail_briefs(RuntimeError("forbidden"))
    with pytest.raises(ExecutionError) as exc_info:
        _adapter(k8s).read_log("vcjob/dms-sync-execution-abc")
    assert exc_info.value.reason_code == "poll_failed"


def test_stub_adapter_read_log_is_three_tuple():
    a = StubExecutionAdapter()
    assert a.read_log("pod/p1") == [("pod/p1", "", None)]
    a.set_log("pod/p1", [("p1", "custom log", None)])
    assert a.read_log("pod/p1") == [("p1", "custom log", None)]
