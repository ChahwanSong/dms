"""슬라이스 25 §2.2: 실패 종단 시 파드 로그를 diag_logs 에 박제한다.

파드가 남아 있어도 시한부다(pod GC 86400·vcjob TTL 86400) -- 스테퍼가 실패
종단을 관측하는 순간이 로그가 확실히 존재하는 마지막 지점이므로 거기서 박제한다.
순서가 계약이다: 박제 -> set_job_state. 박제 후 크래시하면 다음 틱이 finalize 를
재시도하고(IS NULL 이 중복을 막는다), 역순이면 종단 잡은 다시 스텝되지 않아 박제
기회가 영영 사라진다."""
import json

from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, ExecutionError, StubExecutionAdapter
from dms.repositories import Repositories
from dms.repositories.builds import LOG_TEXT_MAX
from dms.stepper import DIAG_MAX_ENTRIES, DIAG_TAIL_BYTES, JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _seed_storage(repos, name):
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def _scan_job(repos, *, tool="dscan", storage="s1", key=None):
    _seed_storage(repos, storage)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key=key or f"k-{tool}", payload={"storage": storage, "target": "a"},
        priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name=storage, target="a", options={}, tool=tool,
        worker_pool={"tool": tool, "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _sync_job(repos):
    _seed_storage(repos, "src")
    _seed_storage(repos, "dst")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice",
        resource_key="k-sync", payload={"source_storage": "src", "source": "a",
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


def _diag(repos, jid):
    raw = repos.data_jobs.get_job(jid)["diag_logs"]
    return None if raw is None else json.loads(raw)


# ---- 실패 종단 4경로 각각이 박제한다 ----

def test_preflight_failure_archives_the_pod_log(db):
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    ref = f"stub-preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("p1", "DMS_PREFLIGHT_REASON=target_not_readable", None)])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    doc = _diag(repos, jid)
    assert doc["phase"] == "preflight"
    assert doc["entries"] == [{"pod": "p1",
        "log": "DMS_PREFLIGHT_REASON=target_not_readable", "truncated": False}]


def test_execution_failure_archives_launcher_log(db):
    # 러너 도달 전 실패(파이썬 트레이스백)의 유일한 증거가 여기 남는다 -- 이
    # 슬라이스의 존재 이유다(설계 서두).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> Running
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("j-launcher-0", "Traceback (most recent call last) ...", None)])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    doc = _diag(repos, jid)
    assert doc["phase"] == "execution"
    assert doc["entries"][0]["pod"] == "j-launcher-0"
    assert "Traceback" in doc["entries"][0]["log"]


def test_preview_timeout_archives_with_preview_phase(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    ref = f"stub-preview-{jid}"
    adapter.script(ref, [ExecStatus.TIMED_OUT])
    adapter.set_log(ref, [("pv-launcher-0", "", None)])
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview TIMED_OUT
    assert repos.data_jobs.get_job(jid)["state"] == "TimedOut"
    doc = _diag(repos, jid)
    assert doc["phase"] == "preview"
    assert doc["entries"][0]["log"] == ""                # 빈 로그는 정상값 -- null 이 아니다


def test_recheck_failure_archives_exec_preflight_phase(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3, "bytes": 9})
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview ok -> ConfirmPending
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()                                   # exec_preflight 제출
    ref = f"stub-exec_preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("re-pf", "DMS_PREFLIGHT_REASON=source_not_readable", None)])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    doc = _diag(repos, jid)
    assert doc["phase"] == "exec_preflight"
    assert "source_not_readable" in doc["entries"][0]["log"]


# ---- 상한·정직성·격리 ----

def test_caps_four_entries_and_16kb_tails_total_64kb(db):
    # 상한이 없으면 DB 가 부푼다 -- 파드당 16KB 꼬리 + 항목 4(launcher 우선,
    # 어댑터가 앞에 놓는다) = 총 64KB, builds LOG_TEXT_MAX 와 같은 총량(설계 §2.2).
    assert DIAG_MAX_ENTRIES * DIAG_TAIL_BYTES == LOG_TEXT_MAX
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preflight-{jid}"
    big = "x" * (DIAG_TAIL_BYTES + 1000) + "TAIL-MARKER"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [(f"p{i}", big, None) for i in range(6)])
    stepper.run_once()
    doc = _diag(repos, jid)
    assert len(doc["entries"]) == DIAG_MAX_ENTRIES
    assert [e["pod"] for e in doc["entries"]] == ["p0", "p1", "p2", "p3"]  # 앞 우선
    for e in doc["entries"]:
        assert e["truncated"] is True
        assert len(e["log"].encode()) <= DIAG_TAIL_BYTES
        assert e["log"].endswith("TAIL-MARKER")           # 머리가 아니라 꼬리를 남긴다
    assert len(json.dumps(doc).encode()) <= LOG_TEXT_MAX + 4096  # 봉투(키·pod명) 여유


def test_tail_cut_does_not_break_utf8_or_exceed_the_byte_cap(db):
    # 상한은 **바이트** 기준이고 한국어 로그에서도 진짜여야 한다. 꼬리를 그냥
    # raw[-N:] 로 자르면 경계에서 글자가 쪼개지고, errors="replace" 가 그 조각을
    # U+FFFD(3바이트)로 부풀려 결과가 도리어 N 을 넘는다 -- 상한이 상한이 아니게
    # 된다. 자르기는 코드포인트 경계로 물러나야 한다(진단 로그에 없던 깨진 글자를
    # 심지 않는 효과도 같이 얻는다).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preflight-{jid}"
    # "가" 는 3바이트고 DIAG_TAIL_BYTES(16384)는 3의 배수가 아니라 꼬리 경계가
    # 반드시 글자 가운데를 지난다.
    log = "가" * (DIAG_TAIL_BYTES // 3 + 100) + "끝"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("p-utf8", log, None)])
    stepper.run_once()
    entry = _diag(repos, jid)["entries"][0]
    assert entry["truncated"] is True
    assert len(entry["log"].encode()) <= DIAG_TAIL_BYTES
    assert "�" not in entry["log"]                   # 없던 깨진 글자를 만들지 않는다
    assert entry["log"].endswith("끝")


def test_all_null_logs_still_archived(db):
    # "박제 시점에 이미 없었다"는 사실 자체가 진단이다 -- 저장을 건너뛰면
    # /logs 폴백이 "박제 자체가 없었다"와 구분할 수 없게 된다(설계 §2.2).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_log(ref, [("p1", None, None)])
    stepper.run_once()
    doc = _diag(repos, jid)
    assert doc is not None
    assert doc["entries"] == [{"pod": "p1", "log": None, "truncated": False}]


class _LogRaisingAdapter(StubExecutionAdapter):
    def read_log(self, ref):
        raise ExecutionError("poll_failed", "apiserver down")


def test_archive_failure_records_event_and_still_finalizes(db):
    # 박제 실패가 종단 전이를 막으면 잡이 낀다 -- 조용한 실패도 금지라 이벤트로
    # 표면화한다(설계 §4).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _LogRaisingAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    stepper.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"       # 종단은 됐다
    assert _diag(repos, jid) is None
    kinds = [e["event_type"] for e in repos.observability.events_for_request(rid)]
    assert "diag_archive_failed" in kinds


def test_success_terminal_does_not_archive(db):
    # 성공 잡의 로그는 아티팩트가 이미 영구 사본이다(설계 §7).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    stepper.run_once()                                   # Preflight ok -> Running
    stepper.run_once()                                   # execution SUCCEEDED
    assert repos.data_jobs.get_job(jid)["state"] == "Succeeded"
    assert _diag(repos, jid) is None


def test_fail_closed_paths_do_not_archive(db):
    # 슬라이스 24 신설 종단(unknown_tool 등)은 박제 비대상이다: Pending 종단은
    # 파드가 없고, 진행 중 종단은 _fail_closed 가 refs 를 회수하는 경로라 증거가
    # 파드 로그가 아니라 DB 행 자체다(플랜 §1 재확인의 판정을 계약으로 박제).
    repos = Repositories(db)
    rid, jid = _scan_job(repos, tool="dwalk", key="k-fc")
    adapter = StubExecutionAdapter()
    _stepper(repos, adapter).run_once()
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert _diag(repos, jid) is None


# ---- 순서 계약: 박제 -> set_job_state ----

def test_crash_between_archive_and_state_write_replays_idempotently(db):
    """순서가 뒤집히면(종단 먼저) 크래시 창에서 박제 기회가 영영 사라진다 --
    박제가 먼저면 다음 틱 재폴링이 finalize 를 재시도하고 IS NULL 이 중복을
    막는다(설계 §2.2). set_job_state 를 1회 실패시키는 크래시 주입으로 그 순서를
    직접 증명한다."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preflight-{jid}"
    adapter.script(ref, [ExecStatus.FAILED, ExecStatus.FAILED])   # 두 틱 다 실패 관측
    adapter.set_log(ref, [("p1", "first copy", None)])
    original = repos.data_jobs.set_job_state
    state = {"raised": False}

    def crash_once(job_id, to_state, **kwargs):
        from dms.domain import TERMINAL_DATA_JOB_STATES
        if (job_id == jid and to_state in TERMINAL_DATA_JOB_STATES
                and not state["raised"]):
            state["raised"] = True
            raise RuntimeError("crash after archive, before state write")
        return original(job_id, to_state, **kwargs)

    repos.data_jobs.set_job_state = crash_once
    stepper.run_once()                                   # 박제됨 + 종단 전이는 크래시
    assert repos.data_jobs.get_job(jid)["state"] == "Preflight"   # 아직 비종단
    first = _diag(repos, jid)
    assert first is not None                             # 박제가 먼저였다 -- 순서의 증거
    adapter.set_log(ref, [("p1", "second copy", None)])  # 재시도 시점의 로그는 달라졌다
    stepper.run_once()                                   # 재시도: finalize 완주
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert _diag(repos, jid)["entries"] == first["entries"]       # 첫 사본 불변(write-once)
