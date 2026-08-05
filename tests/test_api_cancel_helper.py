import pytest
from dms.api.cancel import terminate_job
from dms.execution import ExecutionError


class _Adapter:
    def __init__(self, fail_on=None):
        self.terminated = []
        self._fail_on = fail_on

    def terminate(self, ref):
        if ref == self._fail_on:
            raise ExecutionError("terminate_failed", ref)
        self.terminated.append(ref)


def test_terminates_every_phase_ref():
    job = {"state": "Executing",
           "phase_refs": {"preflight": "pod/a", "execution": "vcjob/b"}}
    a = _Adapter()
    terminate_job(a, job)
    assert sorted(a.terminated) == ["pod/a", "vcjob/b"]


def test_terminal_job_is_untouched():
    a = _Adapter()
    terminate_job(a, {"state": "Succeeded", "phase_refs": {"execution": "vcjob/b"}})
    assert a.terminated == []


def test_missing_or_empty_refs_are_skipped():
    a = _Adapter()
    terminate_job(a, {"state": "Executing", "phase_refs": None})
    terminate_job(a, {"state": "Executing", "phase_refs": {"execution": None}})
    assert a.terminated == []


def test_failure_propagates():
    a = _Adapter(fail_on="vcjob/b")
    with pytest.raises(ExecutionError):
        terminate_job(a, {"state": "Executing", "phase_refs": {"execution": "vcjob/b"}})
