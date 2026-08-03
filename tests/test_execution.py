import pytest
from dms.execution import (
    ExecStatus, ExecutionError, JobSpec, StubExecutionAdapter)


def _spec(phase="preflight", job_id="j1", dryrun=False):
    return JobSpec(job_id=job_id, phase=phase, operation="scan", tool="dscan",
                   dryrun=dryrun, identity={"uid": 10001}, paths={"target": "a"},
                   options={}, candidates={"primary": ["n1"]}, process_count=8,
                   queue="dms-data", priority_class="dms-mid",
                   artifact_base="file:///art")


def test_submit_returns_ref_and_records_spec():
    a = StubExecutionAdapter()
    ref = a.submit(_spec(phase="preview"))
    assert ref == "stub-preview-j1"
    assert a.submitted_specs()[0].phase == "preview"


def test_poll_scripted_sequence_then_default():
    a = StubExecutionAdapter()
    ref = a.submit(_spec())
    a.script(ref, [ExecStatus.RUNNING, ExecStatus.RUNNING, ExecStatus.SUCCEEDED])
    assert [a.poll(ref) for _ in range(3)] == [
        ExecStatus.RUNNING, ExecStatus.RUNNING, ExecStatus.SUCCEEDED]


def test_poll_default_is_succeeded():
    a = StubExecutionAdapter()
    ref = a.submit(_spec())
    assert a.poll(ref) == ExecStatus.SUCCEEDED


def test_read_summary_default_and_override():
    a = StubExecutionAdapter()
    ref = a.submit(_spec())
    assert a.read_summary(ref) == {"files": 0, "bytes": 0}
    a.set_summary(ref, {"files": 5})
    assert a.read_summary(ref) == {"files": 5}


def test_terminate_is_idempotent_and_marks_failed():
    a = StubExecutionAdapter()
    ref = a.submit(_spec())
    a.terminate(ref)
    a.terminate(ref)  # 멱등
    a.terminate("nonexistent")  # no-op
    assert a.poll(ref) == ExecStatus.FAILED


def test_fail_submit_and_fail_terminate():
    a = StubExecutionAdapter()
    a.fail_submit("execution")
    with pytest.raises(ExecutionError):
        a.submit(_spec(phase="execution"))
    ref = a.submit(_spec(phase="preflight"))
    a.fail_terminate(ref)
    with pytest.raises(ExecutionError):
        a.terminate(ref)
