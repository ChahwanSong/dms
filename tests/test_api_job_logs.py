from dms.domain import DataJobState, RequestState
from dms.execution_volcano import VolcanoExecutionAdapter


def _login(client, name="alice"):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _confirmpending_job(app_repos, requester="alice"):
    repos = app_repos
    rid = repos.requests.create(operation="sync", requester_id=requester, actor=requester,
        resource_key="k", payload={"source_storage": "src", "source": "a",
        "destination_storage": "dst", "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid",
        source_storage="src", source="a", destination_storage="dst", destination="b",
        options={}, tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint="sha256:abc",
        expires_at="2099-01-01T00:00:00Z", artifact_uri="file:///art/j")
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return rid, jid


class _FakeK8s:
    """VolcanoExecutionAdapter.read_log rejects non-pod refs before touching
    k8s at all, so this stand-in never needs a real read_pod_log."""


def _volcano_adapter():
    return VolcanoExecutionAdapter(
        _FakeK8s(), job_image="reg/img:1", namespace="dms",
        storages_lookup=lambda n: {"mount_path": "/cephfs"},
        read_text=lambda path: None,
        artifact_base="file:///cephfs/dms/artifacts")


def test_get_preflight_log_returns_entries(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "preflight", "pod/p1")
    client.app.state.execution_adapter.set_log("pod/p1", [("p1", "hello preflight log")])
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "preflight"})
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "preflight"
    assert body["ref"] == "pod/p1"
    assert body["entries"] == [{"pod": "p1", "log": "hello preflight log"}]


def test_exec_preflight_log_is_reachable(client):
    # confirm 후 재검증은 phase="exec_preflight"로 제출되고(stepper._poll_or_submit_execution),
    # 실패하면 execution_recheck_failed로 잡이 거절된다. 그 실패를 진단할 로그가 정확히
    # 이 ref다 — PHASES에 빠져 있어 422로 막히면 운영자가 볼 방법이 아예 없다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "exec_preflight", "pod/p2")
    client.app.state.execution_adapter.set_log("pod/p2", [("p2", "recheck failed: dst full")])
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "exec_preflight"})
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "exec_preflight"
    assert body["entries"] == [{"pod": "p2", "log": "recheck failed: dst full"}]


def test_missing_phase_ref_404(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "preflight"})
    assert r.status_code == 404
    assert r.json()["detail"] == "log_ref_not_found"


def test_vcjob_ref_409_log_not_available(client):
    # StubExecutionAdapter.read_log never raises regardless of ref prefix (see
    # tests/test_execution_read_log.py::test_stub_adapter_read_log) — only the real
    # VolcanoExecutionAdapter enforces "vcjob refs are out of scope for this slice".
    # Swap in a real adapter (with a fake k8s client) to exercise that path.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter = _volcano_adapter()
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "execution"})
    assert r.status_code == 409
    assert r.json()["detail"] == "log_not_available"


def test_invalid_phase_422(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "bogus-phase"})
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_phase"


def test_other_users_job_404(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos, requester="alice")
    repos.data_jobs.set_phase_ref(jid, "preflight", "pod/p1")
    _login(client, "eve")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "preflight"})
    assert r.status_code == 404
    assert r.json()["detail"] == "job_not_found"


def test_multibyte_log_capped_at_max_bytes_stays_valid_text(client):
    # Byte-cap slicing must happen on bytes, not on str length, or a multi-byte
    # character can be split mid-codepoint. Use Korean text (3 bytes/char in UTF-8)
    # long enough to exceed MAX_BYTES, and confirm the response is still valid text
    # capped close to the byte budget (not a naive char-count truncation).
    from dms.api.artifacts import MAX_BYTES

    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "preflight", "pod/p1")
    unit = "가나다라마바사아자차카타파하로그메시지줄"  # 21 chars, 63 bytes in UTF-8
    unit_bytes = len(unit.encode("utf-8"))
    big_log = unit * ((MAX_BYTES // unit_bytes) + 100)
    assert len(big_log.encode("utf-8")) > MAX_BYTES
    client.app.state.execution_adapter.set_log("pod/p1", [("p1", big_log)])
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "preflight"})
    assert r.status_code == 200
    log = r.json()["entries"][0]["log"]
    assert isinstance(log, str)
    encoded = log.encode("utf-8")
    # Small tolerance: a split codepoint at the cut boundary decodes via
    # errors="replace" into one or more U+FFFD (3 bytes each), which can push the
    # re-encoded size a few bytes past MAX_BYTES.
    assert len(encoded) <= MAX_BYTES + 8
    assert len(encoded) < len(big_log.encode("utf-8"))
