"""stepper 실행 관문의 경로 겹침 보류(2026-10-27, JobStepper._overlap_hold·overlap_level·overlap_blocks).

planner 1b(path_conflict)가 새 요청을 막지만, 위험한 순간은 확인 뒤 실행 직전이다 -- 이 기능 전에 계획된 행·DB 직접 쓰기·
경합이 함께 실행되지 않게, 실행 측 단계(sync·rm 의 exec_preflight·execution, scan 의 execution)를 내기 직전에 겹치는(한쪽이
rm) 다른 잡이 돌거나 앞서 있으면 **보류**한다(실패시키지 않는다 -- 상대가 끝나면 이어진다). 여기서 고정하는 것:
  - 보류·해제·이벤트 한 번·표식·updated_at 밀기(클레임 앞자리 교착 방지),
  - 단계표(overlap_level)와 기다림 표(overlap_blocks) -- 그리고 그 표의 기다림 그래프에 고리가 없다(전수),
  - 실제 틱 시뮬레이션: 겹치는 잡 여럿이 동시에 실행 단계에 있지 않고, 모두 유한 틱 안에 끝난다(교착 없음).
"""
import itertools
import uuid

import pytest

from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.repositories import Repositories
from dms.stepper import GATE_EXECUTION, GATE_START, JobStepper, _order_key, overlap_blocks, overlap_level


class _Settings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = True
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


_TOOL = {"rm": "drm", "scan": "dscan", "sync": "dsync"}


def _seed_storages(repos):
    for name in ("s1", "s2"):
        if repos.storages.get(name) is None:
            repos.storages.create(storage_name=name, mount_path=f"/{name}", managed_root=f"/{name}/dms",
                                  backend_type="cephfs", actor="test")


def _job(repos, op, state, *, refs=None, tool=None, **paths):
    _seed_storages(repos)
    payload = {"run_as_root": True, **paths}
    if op == "rm":
        payload["options"] = {"recursive": True}
    rid = repos.requests.create(operation=op, requester_id="ops", actor="ops", resource_key=f"k-{uuid.uuid4().hex}",
                                payload=payload, priority="mid", auth_method="session")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(
        rid, plan_id, operation=op, priority="mid", storage_name=paths.get("storage"),
        source_storage=paths.get("source_storage"), source=paths.get("source"),
        destination_storage=paths.get("destination_storage"), destination=paths.get("destination"),
        target=paths.get("target"), options=payload.get("options", {}), tool=tool or _TOOL[op],
        worker_pool={"tool": tool or _TOOL[op], "identity": {"uid": 0, "gid": 0, "username": "ops", "groups": [],
                                                              "privileged": True},
                     "candidates": {"primary": ["n1"]}, "process_count": 8, "queue": "dms-data",
                     "priority_class": "dms-mid"},
        precondition={}, actor="planner")
    if state != DataJobState.PENDING.value:
        repos.data_jobs.set_job_state(jid, DataJobState(state), actor="test")
    for phase in refs or ():
        repos.data_jobs.set_phase_ref(jid, phase, f"stub-{phase}-{jid}")
    return rid, jid


def rm_job(repos, target, state="Executing", storage="s1", **kw):
    return _job(repos, "rm", state, storage=storage, target=target, **kw)


def scan_job(repos, target, state="Preflight", storage="s1", **kw):
    kw.setdefault("refs", ("preflight",))
    return _job(repos, "scan", state, storage=storage, target=target, **kw)


def sync_job(repos, source, destination, state="Executing", **kw):
    return _job(repos, "sync", state, source_storage="s1", source=source, destination_storage="s2",
                destination=destination, **kw)


def _stepper(repos, adapter):
    return JobStepper(repos, adapter, settings=_Settings())


def _phases(adapter, jid):
    return [s.phase for s in adapter.submitted_specs() if s.job_id == jid]


def _events(repos, rid, kind):
    return [e for e in repos.observability.events_for_request(rid) if e["event_type"] == kind]


def _finish(repos, jid, rid):
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="test")
    repos.requests.finalize_from_job(rid, DataJobState.SUCCEEDED, actor="test")


@pytest.fixture
def repos(db):
    return Repositories(db)


# ---- 단계표·기다림 표 --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("row, level", [
    ({"operation": "rm", "state": "Executing", "phase_refs": {"exec_preflight": "r", "execution": "r"}}, 2),
    ({"operation": "rm", "state": "Executing", "phase_refs": {"preflight": "p", "exec_preflight": "r"}}, 1),
    ({"operation": "rm", "state": "Executing", "phase_refs": {"preflight": "p", "preview": "v"}}, 0),
    ({"operation": "rm", "state": "Executing", "phase_refs": None}, 0),
    ({"operation": "sync", "state": "Preflight", "phase_refs": {"preflight": "p"}}, None),   # 초기 preflight
    ({"operation": "scan", "state": "Preflight", "phase_refs": {"preflight": "p"}}, 0),      # 통과하면 곧 실행
    ({"operation": "scan", "state": "Running", "phase_refs": {"preflight": "p", "execution": "e"}}, 2),
    ({"operation": "scan", "state": "Preflight", "phase_refs": {"preflight": "p", "execution": "e"}}, 2),
    ({"operation": "rm", "state": "Running", "phase_refs": {}}, 2),                          # 이상 모양 -- 돈다고 본다
    ({"operation": "rm", "state": "Executing", "phase_refs": "corrupt"}, 2),                 # 깨진 ref -- fail-closed
])
def test_overlap_level(row, level):
    assert overlap_level(row) == level


@pytest.mark.parametrize("gate, level, earlier, blocks", [
    (GATE_START, None, True, False),
    (GATE_START, 2, False, True), (GATE_START, 2, True, True),
    (GATE_START, 1, False, True), (GATE_START, 1, True, True),
    (GATE_START, 0, True, True), (GATE_START, 0, False, False),
    (GATE_EXECUTION, 2, False, True), (GATE_EXECUTION, 2, True, True),
    (GATE_EXECUTION, 1, True, True), (GATE_EXECUTION, 1, False, False),
    (GATE_EXECUTION, 0, True, False), (GATE_EXECUTION, 0, False, False),
])
def test_overlap_blocks_table(gate, level, earlier, blocks):
    assert overlap_blocks(gate, level, earlier) is blocks


def test_order_key_missing_commit_order_counts_as_earliest():
    assert _order_key(None, "x") < _order_key(1, "a")
    assert _order_key(1, "a") < _order_key(2, "a")
    assert _order_key(True, "x") < _order_key(1, "a")      # bool 은 commit_order 가 아니다


def test_wait_graph_has_no_cycle_and_someone_progresses():
    """전수: 서로 겹치는 잡 N 개가 각자 단계(0 = 첫 관문 대기, 1 = 둘째 관문 대기, 2 = 실행 중)와 commit_order 를 가질 때,
    기다림 관계(overlap_blocks)에 고리가 없고, 실행 중(2)이 없으면 적어도 하나는 진행한다."""
    gate_of = {0: GATE_START, 1: GATE_EXECUTION}
    for n in (2, 3, 4):
        for levels in itertools.product((0, 1, 2), repeat=n):
            for order in itertools.permutations(range(n)):
                waits = {i: set() for i in range(n)}
                for i in range(n):
                    if levels[i] == 2:
                        continue                      # 이미 돈다 -- 기다리지 않는다
                    for j in range(n):
                        if i != j and overlap_blocks(gate_of[levels[i]], levels[j], order[j] < order[i]):
                            waits[i].add(j)
                # 고리 없음(DFS)
                state = {}

                def visit(v):
                    state[v] = 1
                    for w in waits[v]:
                        if state.get(w) == 1:
                            raise AssertionError(f"cycle levels={levels} order={order}")
                        if state.get(w) is None:
                            visit(w)
                    state[v] = 2
                for v in range(n):
                    if state.get(v) is None:
                        visit(v)
                if 2 not in levels:
                    assert any(not waits[i] for i in range(n)), (levels, order)


# ---- 보류·해제 -----------------------------------------------------------------------------------------------------

def test_rm_holds_while_overlapping_sync_executes_then_proceeds(repos):
    adapter = StubExecutionAdapter()
    k_rid, k_jid = sync_job(repos, "data", "backup", refs=("exec_preflight", "execution"))
    adapter.script(f"stub-execution-{k_jid}", [ExecStatus.RUNNING] * 10)
    j_rid, j_jid = rm_job(repos, "data/old")
    before = repos.data_jobs.get_job(j_jid)["updated_at"]
    results = _stepper(repos, adapter).run_once()
    assert results[j_jid] == "held"
    assert _phases(adapter, j_jid) == []                        # exec_preflight 를 내지 않았다
    job = repos.data_jobs.get_job(j_jid)
    assert job["state"] == "Executing"
    assert job["worker_pool"]["overlap_hold"] == {"request_id": k_rid, "phase": "exec_preflight"}
    assert job["updated_at"] >= before
    holds = _events(repos, j_rid, "path_overlap_hold")
    assert len(holds) == 1
    assert holds[0]["payload"]["conflict_request_id"] == k_rid
    assert holds[0]["payload"]["conflict_job_id"] == k_jid
    assert holds[0]["payload"]["conflict_level"] == 2
    assert k_rid[:8] in holds[0]["message"]
    # 다시 틱 -- 같은 상대면 이벤트를 더 쓰지 않는다.
    assert _stepper(repos, adapter).run_once()[j_jid] == "held"
    assert len(_events(repos, j_rid, "path_overlap_hold")) == 1
    # 상대가 끝나면 이어진다 -- 표식을 지우고 해제 이벤트.
    _finish(repos, k_jid, k_rid)
    assert _stepper(repos, adapter).run_once()[j_jid] == "Executing"
    assert _phases(adapter, j_jid) == ["exec_preflight"]
    assert "overlap_hold" not in repos.data_jobs.get_job(j_jid)["worker_pool"]
    assert len(_events(repos, j_rid, "path_overlap_released")) == 1
    _stepper(repos, adapter).run_once()                          # exec_preflight 성공 → 실행
    assert _phases(adapter, j_jid) == ["exec_preflight", "execution"]


def test_sync_holds_while_overlapping_rm_executes(repos):
    adapter = StubExecutionAdapter()
    k_rid, k_jid = rm_job(repos, "backup", storage="s2", refs=("exec_preflight", "execution"))
    adapter.script(f"stub-execution-{k_jid}", [ExecStatus.RUNNING] * 10)
    j_rid, j_jid = sync_job(repos, "data", "backup/team")
    assert _stepper(repos, adapter).run_once()[j_jid] == "held"
    assert _phases(adapter, j_jid) == []


def test_scan_holds_in_preflight_while_overlapping_rm_executes(repos):
    adapter = StubExecutionAdapter()
    k_rid, k_jid = rm_job(repos, "a/b", refs=("exec_preflight", "execution"))
    adapter.script(f"stub-execution-{k_jid}", [ExecStatus.RUNNING] * 10)
    j_rid, j_jid = scan_job(repos, "a")
    assert _stepper(repos, adapter).run_once()[j_jid] == "held"
    assert repos.data_jobs.get_job(j_jid)["state"] == "Preflight"
    assert _phases(adapter, j_jid) == []
    _finish(repos, k_jid, k_rid)
    assert _stepper(repos, adapter).run_once()[j_jid] == "Running"
    assert _phases(adapter, j_jid) == ["execution"]


@pytest.mark.parametrize("make_other", [
    lambda r: sync_job(r, "data", "backup", refs=("exec_preflight", "execution")),    # sync↔sync
    lambda r: scan_job(r, "data", state="Running", refs=("preflight", "execution")),  # scan↔sync
    lambda r: rm_job(r, "data", storage="s2", refs=("exec_preflight", "execution")),  # 다른 스토리지의 같은 경로
    lambda r: rm_job(r, "datax", refs=("exec_preflight", "execution")),               # 성분 접두(data vs datax)
    lambda r: rm_job(r, "data", state="ConfirmPending", refs=("preflight", "preview")),  # 확인 대기 -- 실행 측 아님
])
def test_no_hold_without_a_conflicting_executing_job(repos, make_other):
    adapter = StubExecutionAdapter()
    other_rid, other_jid = make_other(repos)
    adapter.script(f"stub-execution-{other_jid}", [ExecStatus.RUNNING] * 10)
    j_rid, j_jid = sync_job(repos, "data/sub", "elsewhere")
    assert _stepper(repos, adapter).run_once()[j_jid] == "Executing"
    assert _phases(adapter, j_jid) == ["exec_preflight"]
    assert _events(repos, j_rid, "path_overlap_hold") == []


def test_tampered_tool_drm_counts_as_destructive(repos):
    # 연산은 scan 인데 실행 파일이 drm 인 변조 행 -- drm 꼴로 지우므로 파괴적으로 본다.
    adapter = StubExecutionAdapter()
    k_rid, k_jid = _job(repos, "scan", "Running", refs=("preflight", "execution"), tool="drm", storage="s1",
                        target="data")
    adapter.script(f"stub-execution-{k_jid}", [ExecStatus.RUNNING] * 10)
    j_rid, j_jid = sync_job(repos, "data/sub", "x")
    assert _stepper(repos, adapter).run_once()[j_jid] == "held"


def test_corrupt_phase_refs_of_the_other_hold_fail_closed(repos, db):
    adapter = StubExecutionAdapter()
    k_rid, k_jid = rm_job(repos, "data", state="ConfirmPending")
    db.execute("UPDATE data_jobs SET phase_refs = :p, state = 'Executing' WHERE job_id = :j",
               {"p": "{broken", "j": k_jid})
    j_rid, j_jid = sync_job(repos, "data/sub", "x")
    # (깨진 phase_refs 행은 claim_steppable 의 JSON 해석에서 틱 전체를 멈추므로 관문을 직접 부른다 -- 관문의 후보 질의는
    # 그 행을 "corrupt" 로 싣고 단계 2(돌고 있을 수 있다)로 본다.)
    assert repos.requests.overlap_gate_jobs()[0]["phase_refs"] == "corrupt"
    job = repos.data_jobs.get_job(j_jid)
    assert _stepper(repos, adapter)._overlap_hold(job, GATE_START, "exec_preflight") is True
    assert _events(repos, j_rid, "path_overlap_hold")[0]["payload"]["conflict_level"] == 2


def test_non_dict_worker_pool_is_not_held_but_fails_closed(repos, db):
    # 보류가 변조 행의 fail-closed 거부를 미루지 않는다.
    adapter = StubExecutionAdapter()
    k_rid, k_jid = sync_job(repos, "data", "backup", refs=("exec_preflight", "execution"))
    adapter.script(f"stub-execution-{k_jid}", [ExecStatus.RUNNING] * 10)
    j_rid, j_jid = rm_job(repos, "data/old")
    db.execute("UPDATE data_jobs SET worker_pool = :w WHERE job_id = :j", {"w": "[]", "j": j_jid})
    _stepper(repos, adapter).run_once()
    job = repos.data_jobs.get_job(j_jid)
    # Executing 의 fail-closed 종단은 Failed(_fail_closed 의 _EXEC_STATES 규칙).
    assert job["state"] == "Failed" and job["reason_code"] == "identity_missing_at_step"
    assert _events(repos, j_rid, "path_overlap_hold") == []


# ---- 동률 깨기·교착 없음 --------------------------------------------------------------------------------------------

def test_two_confirmed_overlapping_rms_earlier_goes_first(repos, db):
    adapter = StubExecutionAdapter()
    a_rid, a_jid = rm_job(repos, "a")           # 앞선 요청
    b_rid, b_jid = rm_job(repos, "a/b")         # 뒤 요청
    # 뒤 요청이 먼저 클레임되게(updated_at 이 더 오래됨) -- 처리 순서와 무관하게 앞선 쪽이 이겨야 한다.
    db.execute("UPDATE data_jobs SET updated_at = '2000-01-01T00:00:00Z' WHERE job_id = :j", {"j": b_jid})
    results = _stepper(repos, adapter).run_once()
    assert results[b_jid] == "held"
    assert results[a_jid] == "Executing"
    assert _phases(adapter, a_jid) == ["exec_preflight"] and _phases(adapter, b_jid) == []
    _stepper(repos, adapter).run_once()           # a: exec_preflight → execution, b: a 가 단계 1·2 라 보류
    assert _phases(adapter, a_jid) == ["exec_preflight", "execution"]
    assert _phases(adapter, b_jid) == []
    _stepper(repos, adapter).run_once()           # a: 실행 성공 → 종단(같은 틱에 b 가 a 뒤에 처리되면 b 가 바로 출발)
    assert repos.data_jobs.get_job(a_jid)["state"] == "Succeeded"
    assert _phases(adapter, b_jid) in ([], ["exec_preflight"])
    for _ in range(3):
        _stepper(repos, adapter).run_once()
    assert _phases(adapter, b_jid) == ["exec_preflight", "execution"]
    assert repos.data_jobs.get_job(b_jid)["state"] == "Succeeded"


def test_legacy_both_past_first_gate_earlier_executes_later_waits(repos):
    # 이 기능 전에 둘 다 exec_preflight 를 냈다(단계 1 둘) -- 둘째 관문에서 앞선 쪽만 실행한다.
    adapter = StubExecutionAdapter()
    a_rid, a_jid = rm_job(repos, "a", refs=("exec_preflight",))
    b_rid, b_jid = sync_job(repos, "x", "y", refs=("exec_preflight",))
    b2_rid, b2_jid = rm_job(repos, "a/b/c", refs=("exec_preflight",))
    adapter.script(f"stub-execution-{a_jid}", [ExecStatus.RUNNING, ExecStatus.RUNNING])
    results = _stepper(repos, adapter).run_once()
    assert results[a_jid] == "Executing" and _phases(adapter, a_jid) == ["execution"]
    assert results[b2_jid] == "held" and _phases(adapter, b2_jid) == []
    assert _phases(adapter, b_jid) == ["execution"]               # 겹치지 않는 sync 는 그대로
    hold = _events(repos, b2_rid, "path_overlap_hold")[0]
    assert hold["payload"]["phase"] == "execution" and hold["payload"]["conflict_request_id"] == a_rid
    for _ in range(4):
        _stepper(repos, adapter).run_once()
    assert repos.data_jobs.get_job(a_jid)["state"] == "Succeeded"
    assert _phases(adapter, b2_jid) == ["execution"]


def test_scan_earlier_at_gate_goes_before_later_rm(repos, db):
    adapter = StubExecutionAdapter()
    s_rid, s_jid = scan_job(repos, "data")                  # 앞선 scan(Preflight -- 단계 0)
    adapter.script(f"stub-preflight-{s_jid}", [ExecStatus.RUNNING, ExecStatus.RUNNING])
    r_rid, r_jid = rm_job(repos, "data/tmp")                # 뒤 rm(확인 뒤 Executing -- 단계 0)
    # rm 은 앞선 scan(단계 0)을 기다린다 -- scan 의 preflight 가 아직 돌아도.
    assert _stepper(repos, adapter).run_once()[r_jid] == "held"
    assert _stepper(repos, adapter).run_once()[r_jid] == "held"
    # scan preflight 성공 → scan 은 뒤 rm(단계 0)을 기다리지 않고 실행한다.
    results = _stepper(repos, adapter).run_once()
    assert results[s_jid] == "Running"
    assert results[r_jid] == "held"
    assert _phases(adapter, r_jid) == []                    # scan 이 도는 동안 rm 은 아무것도 내지 않았다
    _stepper(repos, adapter).run_once()                     # scan 실행 성공(같은 틱에 scan 뒤 처리되면 rm 이 바로 출발)
    assert repos.data_jobs.get_job(s_jid)["state"] == "Succeeded"
    for _ in range(3):
        _stepper(repos, adapter).run_once()
    assert _phases(adapter, r_jid) == ["exec_preflight", "execution"]


def test_held_jobs_rotate_out_of_the_claim_front(repos, db):
    """보류 잡 10건 이상이 클레임 앞자리(updated_at 오름차순 LIMIT 10)를 차지해도, 보류마다 updated_at 을 밀어 풀어 줄
    잡(실행 중인 상대)이 결국 클레임된다 -- 그렇지 않으면 영원히 보류다."""
    adapter = StubExecutionAdapter()
    k_rid, k_jid = sync_job(repos, "data", "backup", refs=("exec_preflight", "execution"))
    adapter.script(f"stub-execution-{k_jid}", [ExecStatus.RUNNING])   # 한 번 더 폴링되면 성공
    held = [rm_job(repos, f"data/d{i}") for i in range(12)]
    db.execute("UPDATE data_jobs SET updated_at = '2000-01-01T00:00:00Z' WHERE job_id <> :k", {"k": k_jid})
    for _ in range(6):
        _stepper(repos, adapter).run_once()
        if repos.data_jobs.get_job(k_jid)["state"] == "Succeeded":
            break
    assert repos.data_jobs.get_job(k_jid)["state"] == "Succeeded"


def test_simulation_no_two_overlapping_jobs_execute_together_and_all_finish(repos):
    """서로 겹치는 rm·sync·scan 여럿(일부는 이 기능 전 모양 -- 이미 exec_preflight)이 같이 확인된 상태에서 틱을 돌린다: 어느
    순간에도 겹치는(한쪽 rm) 두 잡이 동시에 실행 단계(execution ref, 비종단)에 있지 않고, 모두 유한 틱 안에 끝난다."""
    adapter = StubExecutionAdapter()
    jobs = [
        rm_job(repos, "p"),
        sync_job(repos, "p/q", "out", refs=("exec_preflight",)),
        rm_job(repos, "p/q/r", refs=("exec_preflight",)),
        scan_job(repos, "p/q"),
        sync_job(repos, "other", "out2"),
        rm_job(repos, "p/z"),
    ]
    for _rid, jid in jobs:
        adapter.script(f"stub-execution-{jid}", [ExecStatus.RUNNING, ExecStatus.RUNNING])
    from dms.domain import destructive_operation, overlap_sides, path_conflict

    def sides(job):
        return overlap_sides(job["operation"], storage=job["storage_name"], target=job["target"],
                             source_storage=job["source_storage"], source=job["source"],
                             destination_storage=job["destination_storage"], destination=job["destination"])

    for _tick in range(60):
        _stepper(repos, adapter).run_once()
        live = [repos.data_jobs.get_job(j) for _r, j in jobs]
        running = [j for j in live if "execution" in (j["phase_refs"] or {})
                   and j["state"] not in ("Succeeded", "Failed", "Rejected", "Cancelled", "TimedOut")]
        for a, b in itertools.combinations(running, 2):
            assert path_conflict(destructive_operation(a["operation"]), sides(a),
                                 destructive_operation(b["operation"]), sides(b)) is None, (a["job_id"], b["job_id"])
        if all(j["state"] == "Succeeded" for j in live):
            break
    assert [repos.data_jobs.get_job(j)["state"] for _r, j in jobs] == ["Succeeded"] * len(jobs)
    # 시뮬레이션이 실제로 보류를 거쳤다(겹침이 없어 우연히 통과한 것이 아니다).
    assert sum(len(_events(repos, r, "path_overlap_hold")) for r, _j in jobs) >= 3
    # 보류한 잡은 모두 풀렸다(해제 이벤트 ≥ 1, 표식 없음). 보류 이벤트는 상대가 바뀔 때마다 하나라 해제보다 많을 수 있다.
    for r, j in jobs:
        if _events(repos, r, "path_overlap_hold"):
            assert _events(repos, r, "path_overlap_released")
        assert "overlap_hold" not in repos.data_jobs.get_job(j)["worker_pool"]
