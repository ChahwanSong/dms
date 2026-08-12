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
    """VolcanoExecutionAdapter.read_log 는 미지 prefix 를 k8s 에 손대기 전에
    거절하므로(슬라이스 25 이후 vcjob 은 열렸고 이 방어만 남았다) 이 대역은
    read_pod_log·list_pod_briefs 를 갖출 필요가 없다."""


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
    client.app.state.execution_adapter.set_log("pod/p1", [("p1", "hello preflight log", None)])
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "preflight"})
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "preflight"
    assert body["ref"] == "pod/p1"
    assert body["source"] == "live"
    assert body["entries"] == [{"pod": "p1", "log": "hello preflight log",
                                "waiting_reason": None}]


def test_exec_preflight_log_is_reachable(client):
    # confirm 후 재검증은 phase="exec_preflight"로 제출되고(stepper._poll_or_submit_execution),
    # 실패하면 execution_recheck_failed로 잡이 거절된다. 그 실패를 진단할 로그가 정확히
    # 이 ref다 — PHASES에 빠져 있어 422로 막히면 운영자가 볼 방법이 아예 없다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "exec_preflight", "pod/p2")
    client.app.state.execution_adapter.set_log("pod/p2", [("p2", "recheck failed: dst full", None)])
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "exec_preflight"})
    assert r.status_code == 200
    body = r.json()
    assert body["phase"] == "exec_preflight"
    assert body["entries"] == [{"pod": "p2", "log": "recheck failed: dst full",
                                "waiting_reason": None}]


def test_missing_phase_ref_404(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "preflight"})
    assert r.status_code == 404
    assert r.json()["detail"] == "log_ref_not_found"


def test_unknown_prefix_ref_409_log_not_available(client):
    # 슬라이스 25 가 vcjob 로그를 열었다 -- 409 log_not_available 은 알 수 없는
    # ref prefix 방어로만 남는다(설계 §2.5). 실 어댑터로 그 방어를 고정한다.
    # (StubExecutionAdapter.read_log 는 prefix 와 무관하게 던지지 않으므로
    #  실 VolcanoExecutionAdapter 로 갈아끼워야 이 경로가 실행된다.)
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "widget/j1")
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
    client.app.state.execution_adapter.set_log("pod/p1", [("p1", big_log, None)])
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


# ---- 슬라이스 25 §2.5: 라이브 우선, 박제 폴백 ----

def _archived(client, jid, phase="execution", entries=None):
    client.app.state.repos.data_jobs.archive_diag_logs(jid, phase=phase,
        entries=entries if entries is not None else [
            {"pod": "j-launcher-0", "log": "Traceback ...", "truncated": True}])


def test_dead_pods_fall_back_to_archived_copy(client):
    # 파드 소실(라이브 전 항목 null) + 박제 존재 -> archived. 항목별 truncated 가
    # 실려야 화면이 잘림 배지를 그린다(설계 §3).
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [("j-launcher-0", None, None)])
    _archived(client, jid)
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "execution"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "archived"
    assert body["entries"] == [{"pod": "j-launcher-0", "log": "Traceback ...",
                                "truncated": True}]


def test_empty_live_list_falls_back_to_archived(client):
    # vcjob TTL 로 파드가 전멸하면 라이브는 빈 목록이다 -- 0 항목도 폴백 조건
    # (설계 §2.5). 빈 목록과 "전 항목 null"을 다르게 취급할 이유가 없다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [])
    _archived(client, jid)
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "archived"


def test_live_wins_when_any_log_is_present(client):
    # 라이브가 하나라도 실체를 주면 박제를 쓰지 않는다 -- 진행 중 잡의 launcher
    # 라이브 tail 이 이 경로다(설계 §2.1 "공짜").
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log(
        "vcjob/j1", [("j-launcher-0", "live tail", None)])
    _archived(client, jid)
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "live"
    assert body["entries"][0]["log"] == "live tail"


def test_partial_live_logs_win_over_archive(client):
    # 실전의 vcjob 은 로그가 있는 launcher 와 이미 사라진 워커 파드가 섞인다 --
    # 폴백 조건은 "전 항목 null"이지 "한 항목이라도 null"이 아니다. 후자면 파드
    # 하나가 없다는 이유로 살아 있는 launcher 라이브 로그가 통째로 박제 사본에
    # 가려진다. 섞인 목록은 라이브가 이기고, null 항목은 null 로 남는다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log(
        "vcjob/j1", [("j-launcher-0", "live tail", None),
                     ("j-worker-0", None, None)])
    _archived(client, jid)
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "live"
    assert body["entries"] == [
        {"pod": "j-launcher-0", "log": "live tail", "waiting_reason": None},
        {"pod": "j-worker-0", "log": None, "waiting_reason": None}]


def test_empty_string_log_is_a_real_value_not_a_fallback_trigger(client):
    # 빈 launcher 로그("")는 정상값이다(§1-3) -- truthy 검사로 뭉개면 실체가 있는
    # 라이브 응답이 archived 로 조용히 강등된다. null 만이 "모름"이다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [("j-launcher-0", "", None)])
    _archived(client, jid)
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "live"
    assert body["entries"] == [{"pod": "j-launcher-0", "log": "",
                                "waiting_reason": None}]


def test_no_archive_keeps_the_null_live_contract(client):
    # 파드 소실 + 박제 없음(배포 전 종단 잡, §7 "백필하지 않는다") -- 기존 null
    # 계약 그대로. null 을 지어낸 문구로 채우지 않는다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "preflight", "pod/p1")
    client.app.state.execution_adapter.set_log("pod/p1", [("p1", None, None)])
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "preflight"}).json()
    assert body["source"] == "live"
    assert body["entries"] == [{"pod": "p1", "log": None, "waiting_reason": None}]


def test_waiting_reason_is_surfaced_on_live_entries(client):
    # waiting_reason 은 "왜 로그가 없는지"의 별 채널이다 -- null 을 합성 문구로
    # 뭉개지 않고 그대로 실어 화면이 병기하게 한다(설계 §1-16 ③).
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log(
        "vcjob/j1", [("j-launcher-0", None, "ImagePullBackOff")])
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "live"
    assert body["entries"] == [{"pod": "j-launcher-0", "log": None,
                                "waiting_reason": "ImagePullBackOff"}]


def test_archived_phase_mismatch_stays_live(client):
    # 박제는 실패한 그 phase 하나뿐이다 -- 다른 phase 요청에 그 사본을 내밀면
    # 사람을 오도한다(preflight 로그 자리에 execution 로그).
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "preflight", "pod/p1")
    client.app.state.execution_adapter.set_log("pod/p1", [("p1", None, None)])
    _archived(client, jid, phase="execution")
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "preflight"}).json()
    assert body["source"] == "live"


def test_corrupt_diag_json_returns_live_and_records_event(client, db):
    # 깨진 사본으로 응답을 지어내지 않는다(설계 §4) -- 라이브 결과 그대로 +
    # 경고 이벤트. 조용한 강등 금지. client 픽스처는 conftest 의 같은 db 로
    # 조립되므로(conftest.py:22-24) db 인자로 직접 오염시킬 수 있다 --
    # 리포지토리는 깨진 값을 만들 수 없다(archive 가 dump_json 을 쓴다).
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [("p", None, None)])
    db.execute(
        "UPDATE data_jobs SET diag_logs = '{broken' WHERE job_id = :j", {"j": jid})
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "live"
    assert body["entries"] == [{"pod": "p", "log": None, "waiting_reason": None}]
    events = repos.observability.events_for_request(rid)
    assert "diag_logs_corrupt" in [e["event_type"] for e in events]


def test_wrong_shaped_diag_json_is_corrupt_not_a_500(client, db):
    # 문법은 맞지만 모양이 틀린 사본(entries 가 리스트가 아님)도 "깨짐"이다 --
    # 그대로 렌더하려 들면 /logs 가 500 이 되어 라이브 열람까지 같이 죽는다.
    # 깨진 사본은 언제나 라이브+경고로 접는다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [("p", None, None)])
    db.execute("""UPDATE data_jobs SET diag_logs = '{"phase": "execution",
                  "entries": "oops"}' WHERE job_id = :j""", {"j": jid})
    _login(client, "alice")
    r = client.get(f"/api/user/jobs/{jid}/logs", params={"phase": "execution"})
    assert r.status_code == 200
    assert r.json()["source"] == "live"
    events = repos.observability.events_for_request(rid)
    assert "diag_logs_corrupt" in [e["event_type"] for e in events]


def test_archived_entries_respect_tail_param(client):
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [])
    _archived(client, jid, entries=[
        {"pod": "p", "log": "l1\nl2\nl3", "truncated": False}])
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution", "tail": 1}).json()
    assert body["source"] == "archived"
    assert body["entries"][0]["log"] == "l3"


def test_archived_null_log_stays_null(client):
    # 박제 시점에 이미 로그가 없었다는 사실 자체가 진단이다(§2.2) -- 폴백은
    # 그 null 을 빈 문자열로 뭉개지 않고 그대로 내보낸다.
    repos = client.app.state.repos
    rid, jid = _confirmpending_job(repos)
    repos.data_jobs.set_phase_ref(jid, "execution", "vcjob/j1")
    client.app.state.execution_adapter.set_log("vcjob/j1", [])
    _archived(client, jid, entries=[{"pod": "p", "log": None, "truncated": False}])
    _login(client, "alice")
    body = client.get(f"/api/user/jobs/{jid}/logs",
                      params={"phase": "execution"}).json()
    assert body["source"] == "archived"
    assert body["entries"] == [{"pod": "p", "log": None, "truncated": False}]
