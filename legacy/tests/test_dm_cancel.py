"""cancel_data_job: terminate the running Volcano/MPI job BEFORE marking Cancelled.

Regression: cancel used to mark the DB Cancelled first and only then try to delete the
VolcanoJob, so a failed/absent termination left the MPI running while the API reported
Cancelled. Now termination happens first and the DB transition only follows a successful
terminate of every recorded job_ref (preview + execution).
"""

from __future__ import annotations

import pytest

from dms.adapters import DataManagementRuntimeError
from dms.domain import DataJobState
from dms.workers import cancel_data_job


class _FakeRepo:
    def __init__(self, refs):
        self.job = {"job_id": "j1", "request_id": "r1", "volcano_job_ref": refs}
        self.updates: list[dict] = []

    def get_data_job(self, job_id):
        return self.job

    def update_data_job(self, job_id, **kw):
        self.updates.append(kw)

    def get_plan_by_request(self, request_id):
        return None

    def complete_result(self, **kw):
        pass

    @property
    def marked_cancelled(self):
        return any(u.get("state") == DataJobState.CANCELLED for u in self.updates)


class _FakeAdapter:
    def __init__(self, fail_refs=()):
        self.terminated: list[str] = []
        self.fail_refs = set(fail_refs)

    def terminate_job(self, job_ref):
        self.terminated.append(job_ref)
        if job_ref in self.fail_refs:
            raise DataManagementRuntimeError(f"forbidden: {job_ref}")


_REFS = {
    "preview": {"job_ref": "volcano://dms/dms-sync-preview-x"},
    "execution": {"job_ref": "volcano://dms/dms-sync-execution-x"},
}


def test_cancel_terminates_execution_ref_then_marks_cancelled():
    repo, adapter = _FakeRepo(_REFS), _FakeAdapter()
    cancel_data_job(repo, adapter, "j1", actor="operator")
    # the running execution job (and the preview ref) are terminated...
    assert "volcano://dms/dms-sync-execution-x" in adapter.terminated
    assert "volcano://dms/dms-sync-preview-x" in adapter.terminated
    # ...and only then is the job marked Cancelled.
    assert repo.marked_cancelled


def test_cancel_does_not_mark_cancelled_when_terminate_fails():
    # If the running job cannot be terminated (e.g. RBAC), the MPI may still be running,
    # so cancel must raise and must NOT report a false Cancelled.
    adapter = _FakeAdapter(fail_refs={"volcano://dms/dms-sync-execution-x"})
    repo = _FakeRepo(_REFS)
    with pytest.raises(DataManagementRuntimeError):
        cancel_data_job(repo, adapter, "j1", actor="operator")
    assert not repo.marked_cancelled


def test_cancel_with_no_refs_marks_cancelled():
    # No running job (e.g. cancelled during preflight) -> nothing to terminate; marks Cancelled.
    repo, adapter = _FakeRepo({}), _FakeAdapter()
    cancel_data_job(repo, adapter, "j1", actor="operator")
    assert adapter.terminated == []
    assert repo.marked_cancelled
