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
