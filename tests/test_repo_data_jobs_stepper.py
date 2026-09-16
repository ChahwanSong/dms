from dms.domain import DataJobState
from dms.repositories import Repositories


def _job(repos, state="Pending"):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key="k", payload={}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={}, precondition={}, actor="planner")
    if state != "Pending":
        repos.data_jobs.set_job_state(jid, DataJobState(state), actor="test")
    return jid


def test_claim_steppable_selects_active_states(db):
    repos = Repositories(db)
    j_pending = _job(repos, "Pending")
    j_preflight = _job(repos, "Preflight")
    j_confirm = _job(repos, "ConfirmPending")   # 제외
    j_done = _job(repos, "Succeeded")            # 제외
    ids = {j["job_id"] for j in repos.data_jobs.claim_steppable()}
    assert j_pending in ids and j_preflight in ids
    assert j_confirm not in ids and j_done not in ids


def test_set_phase_ref_merges(db):
    repos = Repositories(db)
    jid = _job(repos)
    repos.data_jobs.set_phase_ref(jid, "preflight", "ref-pf")
    repos.data_jobs.set_phase_ref(jid, "execution", "ref-ex")
    assert repos.data_jobs.get_job(jid)["phase_refs"] == {
        "preflight": "ref-pf", "execution": "ref-ex"}


def test_set_preview_and_confirmed(db):
    repos = Repositories(db)
    jid = _job(repos)
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
                                expires_at="2026-08-03T10:00:00Z",
                                artifact_uri="file:///art/j",
                                summary={"returncode": 0, "files": 3, "bytes": 12})
    repos.data_jobs.set_confirmed(jid, "sha256:abc")
    job = repos.data_jobs.get_job(jid)
    assert job["preview_fingerprint"] == "sha256:abc"
    assert job["confirmed_fingerprint"] == "sha256:abc"
    assert job["preview_expires_at"] == "2026-08-03T10:00:00Z"
    # 2026-09-17: 미리보기 요약 사본은 JSON 컬럼(_JSON_COLUMNS)이라 dict 로 돌아온다.
    assert job["preview_summary"] == {"returncode": 0, "files": 3, "bytes": 12}
    # summary 생략(구 호출자·읽기 실패)은 NULL = 모름 -- 지어내지 않는다.
    repos.data_jobs.set_preview(jid, fingerprint="sha256:def",
                                expires_at="2026-08-03T11:00:00Z", artifact_uri=None)
    assert repos.data_jobs.get_job(jid)["preview_summary"] is None


def test_set_artifact(db):
    repos = Repositories(db)
    jid = _job(repos)
    repos.data_jobs.set_artifact(jid, artifact_uri="file:///art/j",
                                 result_summary={"files": 3})
    job = repos.data_jobs.get_job(jid)
    assert job["artifact_uri"] == "file:///art/j"
    assert job["result_summary"] == {"files": 3}


def test_expire_previews(db):
    repos = Repositories(db)
    jid = _job(repos, "ConfirmPending")
    repos.data_jobs.set_preview(jid, fingerprint="f", expires_at="2026-08-02T09:00:00Z",
                                artifact_uri=None)
    expired = repos.data_jobs.expire_previews(now_iso="2026-08-02T10:00:00Z")
    assert expired == [jid]
    assert repos.data_jobs.get_job(jid)["state"] == "PreviewExpired"
    # 만료 안 된 것은 그대로
    j2 = _job(repos, "ConfirmPending")
    repos.data_jobs.set_preview(j2, fingerprint="f", expires_at="2026-08-02T11:00:00Z",
                                artifact_uri=None)
    assert repos.data_jobs.expire_previews(now_iso="2026-08-02T10:00:00Z") == []
