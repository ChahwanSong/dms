"""슬라이스 24: 신뢰 경계가 깨진 잡의 fail-closed 종단(설계 §2.1 층1, §2.4).

tool 의 유일한 정상 원천은 placement 의 리터럴 4종인데 create_job 은 무검증
INSERT 다(§1-1) -- 즉 DB 가 신뢰 경계고, 여기 실리는 미지 tool 은 "다섯째 도구
추가 실수" 아니면 "DB 직접 조작"이다. 스토리지도 마찬가지다: 요청 시점엔 있었고
스텝 시점에 없다면 행 삭제/직접 조작이다(§1-8, 컬럼은 NOT NULL). 어느 쪽이든
조용히 관용하면 파괴적 경로(drm 꼴 argv, cwd 기준 상대 삭제)로 흘러가므로
제출 전에 종단시키는 것이 이 파일의 계약이다. Task 2(unknown_tool)와
Task 5(storage_missing_at_step)가 이 파일을 나눠 채운다."""
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


class _TerminateRecordingAdapter(StubExecutionAdapter):
    def __init__(self):
        super().__init__()
        self.terminated = []

    def terminate(self, ref):
        self.terminated.append(ref)
        super().terminate(ref)


def _seed_storage(repos, name):
    # 슬라이스 24: _abs 가 fail-closed 라(Task 5) 스텝 가능한 잡은 실제 storage
    # 행이 필요하다. 이 파일 자신도 그 규칙 위에서 산다.
    if repos.storages.get(name) is None:
        repos.storages.create(storage_name=name, mount_path=f"/{name}",
                              managed_root=f"/{name}/dms", backend_type="cephfs",
                              actor="test")


def _scan_job(repos, *, tool="dscan", storage="s1"):
    _seed_storage(repos, storage)
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key=f"k-{tool}", payload={"storage": storage, "target": "a"},
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


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


# ---- §2.1 층1: 미지 tool 은 제출 전에 종단된다 ----

def test_pending_unknown_tool_is_rejected_without_any_submission(db):
    # create_job 이 무검증이라 "dwalk" 가 그대로 실린다 -- 층1이 없으면 이 잡은
    # preflight 파드를 만들고, 층2 이전 코드라면 drm 꼴 argv 까지 받는다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos, tool="dwalk")
    adapter = StubExecutionAdapter()
    result = _stepper(repos, adapter).run_once()
    assert result[jid] == "Rejected"
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "unknown_tool"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert repos.requests.last_reason_code(rid) == "unknown_tool"
    assert adapter.submitted_specs() == []   # 파드/vcjob 미제출 -- 층1의 존재 이유


def test_running_job_with_mutated_tool_fails_and_reclaims_live_refs(db):
    # 실증 §6-3 의 단위 등가물: 정상 dscan 잡을 Running 까지 보낸 뒤 DB 에서
    # tool 을 변조한다. Executing/Running 은 실행 자원이 이미 붙어 있으므로
    # REJECTED 가 아니라 FAILED(execution_submit_failed 의 기존 대칭)이고,
    # 발급된 phase_refs 는 best-effort 회수돼야 클러스터 고아가 없다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    adapter = _TerminateRecordingAdapter()
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight
    stepper.run_once()   # Preflight Succeeded → Running (execution 제출)
    assert repos.data_jobs.get_job(jid)["state"] == "Running"
    db.execute("UPDATE data_jobs SET tool = 'dwalk' WHERE job_id = :j", {"j": jid})
    result = stepper.run_once()
    assert result[jid] == "Failed"
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "unknown_tool"
    assert repos.requests.get(rid)["state"] == "Failed"
    # preflight ref(이미 끝난 파드 -- 회수 무해)와 execution ref 둘 다 회수 시도.
    assert set(adapter.terminated) == {f"stub-preflight-{jid}",
                                       f"stub-execution-{jid}"}


# ---- §2.4: 스텝 시점 스토리지 결측은 조용한 폴백이 아니라 종단이다 ----

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


def test_missing_storage_terminates_pending_job_instead_of_retry_looping(db):
    # 현행 폴백은 상대경로를 조용히 돌려준다 -- dsync 목적지가 상대로 남으면
    # 도구는 launcher cwd 기준 컨테이너 오버레이에 복사하고 SUCCEEDED 로 끝난다:
    # 데이터는 파드와 함께 증발하는데 사용자는 "성공한 sync" 를 믿는다(설계 §2.4).
    # fail-closed 는 종단 + 이벤트다. run_once 의 step_error(예외 루프 -- 매 틱
    # 재시도로 영구히 낀다) 경로와도 구분돼야 한다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    repos.storages.delete("s1", actor="test")   # 리포지토리 직접 호출 = 라우트 가드 우회 경로
    adapter = StubExecutionAdapter()
    result = _stepper(repos, adapter).run_once()
    assert result[jid] == "Rejected"            # error:... 가 아니다 -- 종단이다
    assert repos.data_jobs.get_job(jid)["state"] == "Rejected"
    assert (repos.data_jobs.job_transitions(jid)[-1]["reason_code"]
            == "storage_missing_at_step")
    assert adapter.submitted_specs() == []      # 어떤 파드도 만들지 않았다
    kinds = [e["event_type"] for e in repos.observability.events_for_request(rid)]
    assert kinds == ["storage_missing_at_step"]  # step_error 가 아니라 전용 이벤트
    events = repos.observability.events_for_request(rid)
    assert "s1" in events[0]["message"]          # "어느 스토리지"가 이벤트에 남는다


def test_missing_storage_mid_flight_fails_executing_job_and_reclaims_refs(db):
    # confirm 뒤(Executing) 목적지 스토리지가 사라진 경우: 실행 자원이 붙은
    # 상태라 FAILED 갈림이고, 발급돼 있던 preflight/preview ref 는 회수된다.
    repos = Repositories(db)
    rid, jid = _sync_job(repos)
    adapter = _TerminateRecordingAdapter()
    adapter.set_summary(f"stub-preview-{jid}", {"files": 3, "bytes": 9})
    stepper = _stepper(repos, adapter)
    stepper.run_once()   # Pending → Preflight
    stepper.run_once()   # Preflight ok → PreviewRunning
    stepper.run_once()   # Preview ok → ConfirmPending
    assert repos.data_jobs.get_job(jid)["state"] == "ConfirmPending"
    # confirm 게이트 통과를 최소 재현(라우트 없이): Executing 으로 직접 전이.
    repos.data_jobs.set_job_state(jid, DataJobState.EXECUTING, actor="test")
    repos.storages.delete("dst", actor="test")
    result = stepper.run_once()                  # exec_preflight 제출 시도 → 결측
    assert result[jid] == "Failed"
    assert repos.data_jobs.get_job(jid)["state"] == "Failed"
    assert (repos.data_jobs.job_transitions(jid)[-1]["reason_code"]
            == "storage_missing_at_step")
    assert set(adapter.terminated) == {f"stub-preflight-{jid}",
                                       f"stub-preview-{jid}"}
    events = [e for e in repos.observability.events_for_request(rid)
              if e["event_type"] == "storage_missing_at_step"]
    assert len(events) == 1 and "dst" in events[0]["message"]


def test_legacy_root_slash_row_joins_without_double_slash(db):
    # 검증(§2.2)은 create/update 에만 발화한다 -- 그 이전에 DB 에 남은 root "/"
    # 행은 _abs 가 2차 방어로 흡수해야 한다. f-string 결합은 "//team/data" 를
    # 만들고 POSIX 는 "//" 를 구현 정의로 둔다. normpath 후처리는 "//x" 를
    # **보존**해서(실측) 대안이 못 된다 -- posixpath.join 만이 "/team/data" 를 준다.
    repos = Repositories(db)
    db.execute(
        """INSERT INTO storages (storage_name, mount_path, managed_root,
               backend_type, enabled, status, created_at, updated_at, updated_by)
           VALUES ('rootfs', '/', '/', 'cephfs', 1, 'Ready',
                   '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'legacy')""")
    rid, jid = _scan_job(repos, storage="rootfs")
    adapter = StubExecutionAdapter()
    _stepper(repos, adapter).run_once()
    target = adapter.submitted_specs()[0].paths["target"]
    assert target == "/a"
    assert not target.startswith("//")


def test_absolute_target_in_db_cannot_escape_managed_root(db):
    # join 치환의 함정: posixpath.join(root, "/etc") == "/etc" -- 둘째 인자가
    # 절대경로면 root 가 통째로 버려진다. 요청 경로는 validate_relative_path 가
    # 절대경로를 막지만 create_job 은 무검증 INSERT 라(§1-1) DB 변조가 남는다.
    # 기존 f-string 결합은 "//etc" 를 managed_root 안에 가뒀었고, "//" 를 없애는
    # 수정이 그 봉쇄까지 걷어내면 drm 이 managed_root 밖을 지운다.
    repos = Repositories(db)
    rid, jid = _scan_job(repos)
    db.execute("UPDATE data_jobs SET target = '/etc' WHERE job_id = :j", {"j": jid})
    adapter = StubExecutionAdapter()
    _stepper(repos, adapter).run_once()
    assert adapter.submitted_specs()[0].paths["target"] == "/s1/dms/etc"
