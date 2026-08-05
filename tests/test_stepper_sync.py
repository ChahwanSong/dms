from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper, _summary_fingerprint


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _sync_job(repos):
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync",
        worker_pool={"tool": "dsync", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def test_summary_fingerprint():
    assert _summary_fingerprint({}) is None
    assert _summary_fingerprint(None) is None
    fp = _summary_fingerprint({"files": 3, "bytes": 9})
    assert fp.startswith("sha256:") and len(fp) == 71  # "sha256:" + 64


def test_sync_reaches_confirm_pending_with_fingerprint(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3, "bytes": 9})
    stepper = _stepper(repos, adapter)
    stepper.run_once()  # Pending → Preflight
    stepper.run_once()  # Preflight succeeded → PreviewRunning (preview submit, dryrun)
    assert repos.data_jobs.get_job(jid)["state"] == "PreviewRunning"
    preview_spec = [s for s in adapter.submitted_specs() if s.phase == "preview"][0]
    assert preview_spec.dryrun is True
    stepper.run_once()  # PreviewRunning succeeded → ConfirmPending
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "ConfirmPending"
    assert job["preview_fingerprint"].startswith("sha256:")
    assert job["preview_expires_at"] is not None
    # ConfirmPending은 스텝퍼가 더 안 건드림
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"


def test_empty_preview_rejects(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {})  # 빈 summary → 지문 없음
    stepper = _stepper(repos, adapter)
    stepper.run_once(); stepper.run_once(); stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert repos.data_jobs.get_job(jid)["reason_code"] == "empty_preview"


def test_confirmed_job_executes(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3})
    stepper = _stepper(repos, adapter)
    stepper.run_once(); stepper.run_once(); stepper.run_once()  # → ConfirmPending
    # confirm을 흉내: 상태를 Executing으로, confirmed_fingerprint 저장
    fp = repos.data_jobs.get_job(jid)["preview_fingerprint"]
    repos.data_jobs.set_confirmed(jid, fp)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()  # Executing, ref 없음 → exec_preflight submit (재검증)
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
    stepper.run_once()  # exec_preflight poll Succeeded → execution submit
    assert repos.data_jobs.get_job(jid)["state"] == "Executing"
    assert [s for s in adapter.submitted_specs() if s.phase == "execution"]
    stepper.run_once()  # execution poll Succeeded → Succeeded
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    assert repos.requests.get(rid)["state"] == "Succeeded"
