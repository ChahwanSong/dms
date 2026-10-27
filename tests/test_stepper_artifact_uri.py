"""Task 4: _poll_execution의 SUCCEEDED 분기가 artifact_uri를 기록하는지 확인.

scan은 preview 게이트가 없으므로 execution 성공 경로가 artifact_uri를 남기는
유일한 지점이다. sync는 preview에서 이미 기록되므로, execution 성공 경로가
그 값을 유지하는지(같은 URI로 COALESCE) 함께 확인한다.
"""
from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import JobStepper


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def _seed_storage(repos, name):
    # 슬라이스 24: _abs 의 결측 폴백(상대경로 반환)이 fail-closed 로 바뀌어
    # (stepper.StorageMissingAtStep) 스텝 가능한 잡은 실제 storage 행이 필요하다.
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def _scan_job(repos):
    _seed_storage(repos, "s1")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k", payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan",
        worker_pool={"tool": "dscan", "identity": {"uid": 10001, "gid": 10000,
            "username": "alice", "groups": [], "privileged": False},
            "candidates": {"primary": ["n1"]}, "process_count": 8,
            "queue": "dms-data", "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    return rid, jid


def _sync_job(repos):
    _seed_storage(repos, "src")
    _seed_storage(repos, "dst")
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


def test_scan_execution_success_records_artifact_uri(db):
    """scan은 preview 게이트를 거치지 않는다 — execution SUCCEEDED 분기가
    artifact_uri를 기록하는 유일한 지점."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-execution-{jid}", {"files": 42, "bytes": 1000})
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight (submit)
    stepper.run_once()   # Preflight poll Succeeded → Running (exec submit)
    stepper.run_once()   # Running poll Succeeded → Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["artifact_uri"] == f"file:///art/{jid}"


def test_sync_execution_success_keeps_preview_artifact_uri(db):
    """sync는 preview에서 먼저 artifact_uri를 기록한다. execution 성공 분기가
    같은 값을 다시 쓰더라도(COALESCE) sync 경로가 흔들리면 안 된다."""
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3})
    stepper = _stepper(repos, adapter)
    stepper.run_once(); stepper.run_once(); stepper.run_once()  # → ConfirmPending
    preview_uri = repos.data_jobs.get_job(jid)["artifact_uri"]
    assert preview_uri == f"file:///art/{jid}"
    # confirm을 흉내: 상태를 Executing으로, confirmed_fingerprint 저장
    fp = repos.data_jobs.get_job(jid)["preview_fingerprint"]
    repos.data_jobs.set_confirmed(jid, fp)
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()  # Executing, ref 없음 → exec_preflight submit (재검증)
    stepper.run_once()  # exec_preflight poll Succeeded → execution submit
    stepper.run_once()  # execution poll Succeeded → Succeeded
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Succeeded"
    assert job["artifact_uri"] == preview_uri == f"file:///art/{jid}"


# ---- 슬라이스 25 §2.4: 실패 잡도 summary 가 있으면 표면화한다 ----

def test_failed_execution_with_summary_records_artifact_and_summary(db):
    # 러너는 도구 비0 종료에도 stdout/stderr/summary 를 쓰고 나서 exit 한다
    # (설계 §1-7) -- returncode 가 카드에 떠야 "왜 실패했나"의 첫 단서가 보인다.
    # 실행 실패의 files/bytes 는 승격된다(d192 -- 실제로 한 일, 아래 부분 처리 테스트).
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    stepper.run_once()                                   # Preflight ok -> Running
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_summary(ref, {"returncode": 2, "files": None, "bytes": None})
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["artifact_uri"] == f"file:///art/{jid}"
    assert job["result_summary"] == {"returncode": 2, "files": None, "bytes": None}


class _NoSummaryAdapter(StubExecutionAdapter):
    # 스텁 기본값은 어떤 ref 에도 {"files": 0, "bytes": 0} 를 준다 -- "요약이
    # 없다"(러너 도달 전 실패)를 재현하려면 None 을 명시로 돌려줘야 한다.
    def read_summary(self, ref):
        return None


def test_failed_execution_without_summary_records_nothing(db):
    # None 은 "모른다"다 -- 지어내지 않는다(설계 §2.4). artifact_uri 를 여기서
    # 합성하면 포탈이 존재하지 않는 아티팩트 디렉터리를 가리킨다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _NoSummaryAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    stepper.run_once()
    adapter.script(f"stub-execution-{jid}", [ExecStatus.FAILED])
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["artifact_uri"] is None
    assert job["result_summary"] is None


def test_failed_execution_with_empty_summary_still_records(db):
    """빈 summary({})는 "요약이 없다"(None)가 아니라 **정상값**이다 -- 러너가
    summary.json 을 썼다는 뜻이고 아티팩트 디렉터리는 실재한다. 이 슬라이스의
    심장인 "null(모름) != 빈 값" 규칙(설계 §4)을 표면화 판정에도 못 박는다:
    `if summary:` 같은 truthy 검사면 여기서 빨개진다."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    stepper.run_once()
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_summary(ref, {})
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["artifact_uri"] == f"file:///art/{jid}"
    assert job["result_summary"] == {}


class _RaisingSummaryAdapter(StubExecutionAdapter):
    # artifact_base 파일시스템을 못 읽는 배포(권한·미마운트)를 흉내낸다.
    def read_summary(self, ref):
        raise OSError("artifact base unreadable")


def test_failed_execution_summary_read_error_is_folded_to_unknown(db):
    """실패 잡의 보강은 best-effort 다 -- read_summary 예외가 새면 run_once 의
    step_error 로 잡혀 종단 전이 자체가 매 틱 재시도 루프에 낀다(잡이 영영
    Running 에 낀다). 예외는 None(모름)으로 접고 종단은 그대로 간다."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _RaisingSummaryAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    stepper.run_once()
    adapter.script(f"stub-execution-{jid}", [ExecStatus.FAILED])
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"          # 종단은 예외에 막히지 않는다
    assert job["artifact_uri"] is None       # 모름 -- 지어내지 않는다
    assert job["result_summary"] is None
    assert repos.requests.get(rid)["state"] == "Failed"


def test_preview_failure_with_summary_records_artifact(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    ref = f"stub-preview-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_summary(ref, {"returncode": 1, "files": None, "bytes": None})
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview FAILED
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["artifact_uri"] == f"file:///art/{jid}"
    assert job["result_summary"] == {"returncode": 1, "files": None, "bytes": None}


# ---- d192: files_count/bytes_count 는 실행·scan 결과만 -- 미리보기 실패는 표시만, 승격하지 않는다 ----

def _to_executing(repos, stepper, jid):
    stepper.run_once(); stepper.run_once(); stepper.run_once()          # -> ConfirmPending
    repos.data_jobs.set_confirmed(jid, repos.data_jobs.get_job(jid)["preview_fingerprint"])
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    stepper.run_once()                                                   # exec_preflight submit
    stepper.run_once()                                                   # exec_preflight ok -> execution submit


def test_failed_execution_promotes_what_actually_took_effect(db):
    # summary v2 의 실행 files/bytes 는 실제로 반영된 수(nsync Completed·dsync 복사 성공)다 -- 실패 전까지의 부분 처리도
    # 실제 변경이라 지표(metrics sync_items_total, 종단 전체 합)에 들어가야 한다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 4, "bytes": 16})
    stepper = _stepper(repos, adapter)
    _to_executing(repos, stepper, jid)
    ref = f"stub-execution-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_summary(ref, {"returncode": 1, "files": 1, "bytes": 5, "detail_version": 2})
    stepper.run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert (job["files_count"], job["bytes_count"]) == (1, 5)
    assert job["result_summary"]["files"] == 1


def test_preview_failure_shows_summary_but_does_not_promote_counts(db):
    # 미리보기 files 는 「할 예정」(nsync 계획 합 등)이다 -- 실패한 미리보기의 4·16 이 컬럼에 들어가면 지표가 아무것도
    # 안 바뀐 잡을 「반영 4개·16 B」로 센다(지표가 이제 Failed 도 더하므로). summary 는 화면 표시용으로 그대로 남긴다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    ref = f"stub-preview-{jid}"
    adapter.script(ref, [ExecStatus.FAILED])
    adapter.set_summary(ref, {"returncode": 1, "files": 4, "bytes": 16})
    stepper.run_once()                                   # Preflight ok -> PreviewRunning
    stepper.run_once()                                   # Preview FAILED
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Failed"
    assert job["result_summary"] == {"returncode": 1, "files": 4, "bytes": 16}
    assert (job["files_count"], job["bytes_count"]) == (None, None)


def test_preview_timeout_shows_summary_but_does_not_promote_counts(db):
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()
    ref = f"stub-preview-{jid}"
    adapter.script(ref, [ExecStatus.TIMED_OUT])
    adapter.set_summary(ref, {"returncode": None, "files": 9, "bytes": 90})
    stepper.run_once()
    stepper.run_once()                                   # Preview TIMED_OUT
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "TimedOut"
    assert job["result_summary"]["files"] == 9
    assert (job["files_count"], job["bytes_count"]) == (None, None)


def test_preflight_failure_records_no_artifact(db):
    """프리플라이트 파드는 아티팩트를 쓰지 않는다 -- 대상이 아니다(설계 §2.4).
    스텁의 read_summary 는 어떤 ref 에도 기본 summary 를 주므로, 표면화를 여기까지
    넓히면 존재하지 않는 아티팩트 디렉터리를 가리키는 URI 가 실제로 박힌다."""
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = StubExecutionAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()                                   # Pending -> Preflight
    adapter.script(f"stub-preflight-{jid}", [ExecStatus.FAILED])
    stepper.run_once()                                   # Preflight FAILED -> Rejected
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "Rejected"
    assert job["artifact_uri"] is None
    assert job["result_summary"] is None
