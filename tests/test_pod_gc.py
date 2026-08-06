"""PodGarbageCollector: 종단 잡이 남긴 preflight Pod(pod/, pods/) 정리.

가장 중요한 단언(behavior 2): **비종단 잡은 절대 대상이 아니다** — 대상 잡이면 stepper가
아직 진행 중으로 보는 잡의 파드가 사라져 stepper가 그 잡을 실패로 오인한다.

빌드 파드 GC(build_runner가 주어졌을 때만 동작)에도 같은 단언이 적용된다: 종단
빌드만 대상이고, 비종단 빌드의 파드가 사라지면 BuildRunner.poll이 FAILED로 오인한다."""
from dms.db import iso_plus, utc_now_iso
from dms.domain import DataJobState
from dms.execution import StubExecutionAdapter
from dms.pod_gc import PodGarbageCollector
from dms.repositories import Repositories
from dms.repositories.builds import build_pod_name


class _BuildRunnerSpy:
    """실제 terminate 호출을 관찰하기 위한 스파이 -- BuildRunner/StubBuildRunner의
    submit/poll/read_log는 pod GC 경로에서 쓰이지 않아 구현하지 않는다."""

    def __init__(self):
        self.terminated = []

    def terminate(self, ref):
        self.terminated.append(ref)


class _TerminateRecordingAdapter(StubExecutionAdapter):
    """실제 terminate 호출을 관찰하기 위한 스파이. StubExecutionAdapter의 멱등/
    fail_terminate 동작은 그대로 위임한다."""

    def __init__(self):
        super().__init__()
        self.terminated = []

    def terminate(self, ref):
        self.terminated.append(ref)
        super().terminate(ref)


def _make_job(repos, *, resource_key, refs: dict) -> str:
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key=resource_key, payload={"storage": "s", "target": "a"}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s", target="a", options={}, tool="dscan",
        worker_pool={}, precondition={}, actor="planner")
    for phase, ref in refs.items():
        repos.data_jobs.set_phase_ref(jid, phase, ref)
    return jid


def test_terminal_job_pod_and_pods_refs_are_terminated(db):
    """1. 종단 잡의 pod/ · pods/ ref가 종료된다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid = _make_job(repos, resource_key="k1",
                     refs={"preflight": "pod/p1", "exec_preflight": "pods/p2,p3"})
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    updated_at = repos.data_jobs.get_job(jid)["updated_at"]
    now = iso_plus(updated_at, 3601)  # after_seconds(3600)를 넘겨 대상이 되게 함

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600)
    result = gc.run_once(now_iso=now)

    assert result == {"deleted": 2}
    assert sorted(adapter.terminated) == ["pod/p1", "pods/p2,p3"]


def test_non_terminal_job_is_never_gcd(db):
    """2. 가장 중요한 단언: 비종단 잡은 절대 대상이 아니다.

    아무리 '오래돼 보여도'(now_iso를 아주 먼 미래로 줘도) 비종단 잡의 파드는 지워지지
    않아야 한다 — 지우면 stepper가 진행 중인 잡을 실패로 오인한다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid = _make_job(repos, resource_key="k2", refs={"preflight": "pod/p1"})
    repos.data_jobs.set_job_state(jid, DataJobState.PREFLIGHT, actor="stepper")
    updated_at = repos.data_jobs.get_job(jid)["updated_at"]
    far_future = iso_plus(updated_at, 10_000_000)  # 나이 필터를 통과하고도 남을 만큼 미래

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600)
    result = gc.run_once(now_iso=far_future)

    assert result == {"deleted": 0}
    assert adapter.terminated == []
    # 잡 상태 자체가 조회 대상에서 아예 빠진다(레포 쿼리 레벨에서도 재확인).
    assert repos.data_jobs.terminal_jobs_older_than(3600, now_iso=far_future) == []
    assert repos.data_jobs.get_job(jid)["state"] == DataJobState.PREFLIGHT.value


def test_terminal_job_younger_than_after_seconds_not_yet_targeted(db):
    """3. 종단이 된 지 after_seconds 미만이면 아직 대상이 아니다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid = _make_job(repos, resource_key="k3", refs={"preflight": "pod/p1"})
    repos.data_jobs.set_job_state(jid, DataJobState.FAILED, actor="stepper")
    updated_at = repos.data_jobs.get_job(jid)["updated_at"]
    now = iso_plus(updated_at, 3599)  # after_seconds(3600) 미달

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600)
    result = gc.run_once(now_iso=now)

    assert result == {"deleted": 0}
    assert adapter.terminated == []


def test_vcjob_ref_is_not_a_target(db):
    """4. vcjob/ ref는 대상이 아니다 — Volcano의 ttlSecondsAfterFinished가 처리한다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid = _make_job(repos, resource_key="k4", refs={"execution": "vcjob/j1"})
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    updated_at = repos.data_jobs.get_job(jid)["updated_at"]
    now = iso_plus(updated_at, 3601)

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600)
    result = gc.run_once(now_iso=now)

    assert result == {"deleted": 0}
    assert adapter.terminated == []


def test_terminate_exception_does_not_kill_the_loop(db):
    """5. terminate가 예외를 던져도 루프가 죽지 않고 나머지를 계속 처리한다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid1 = _make_job(repos, resource_key="k5a", refs={"preflight": "pod/boom"})
    jid2 = _make_job(repos, resource_key="k5b", refs={"preflight": "pod/ok"})
    repos.data_jobs.set_job_state(jid1, DataJobState.SUCCEEDED, actor="stepper")
    repos.data_jobs.set_job_state(jid2, DataJobState.SUCCEEDED, actor="stepper")
    adapter.fail_terminate("pod/boom")
    updated_at = repos.data_jobs.get_job(jid2)["updated_at"]
    now = iso_plus(updated_at, 3601)

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600)
    result = gc.run_once(now_iso=now)  # 예외 없이 반환돼야 한다

    assert result == {"deleted": 1}
    assert "pod/boom" in adapter.terminated  # 시도는 했다(실패했을 뿐)
    assert "pod/ok" in adapter.terminated


def test_run_once_returns_total_deleted_count(db):
    """6. run_once가 처리 건수를 반환한다(여러 잡·여러 ref 합산)."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid1 = _make_job(repos, resource_key="k6a",
                      refs={"preflight": "pod/a1", "exec_preflight": "pods/a2,a3"})
    jid2 = _make_job(repos, resource_key="k6b", refs={"preflight": "pod/b1"})
    repos.data_jobs.set_job_state(jid1, DataJobState.SUCCEEDED, actor="stepper")
    repos.data_jobs.set_job_state(jid2, DataJobState.CANCELLED, actor="stepper")
    updated_at = repos.data_jobs.get_job(jid2)["updated_at"]
    now = iso_plus(updated_at, 3601)

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600)
    result = gc.run_once(now_iso=now)

    # jid1: pod/a1 + pods/a2,a3(콤마는 어댑터 내부 형식, 1건으로 카운트) = 2건, jid2: pod/b1 = 1건
    assert result == {"deleted": 3}


def _make_build(repos, *, node="dms-w1"):
    return repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                               node_name=node, actor="a")


def test_terminal_build_pod_is_terminated_when_build_runner_given(db):
    """7. build_runner가 주어지면 종단 빌드의 빌드 파드도 같은 창(after_seconds)으로
    수거된다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    build_runner = _BuildRunnerSpy()
    bid = _make_build(repos)
    repos.builds.mark_running(bid)
    repos.builds.finish(bid, state="Succeeded")
    finished_at = repos.builds.get(bid)["finished_at"]
    now = iso_plus(finished_at, 3601)  # after_seconds(3600)를 넘겨 대상이 되게 함

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600, build_runner=build_runner)
    result = gc.run_once(now_iso=now)

    assert result == {"deleted": 1}
    assert build_runner.terminated == [f"buildpod/{build_pod_name(bid)}"]
    assert adapter.terminated == []  # 이 테스트엔 잡 파드가 없다


def test_non_terminal_build_pod_is_never_gcd(db):
    """8. 가장 중요한 단언: 비종단 빌드는 절대 대상이 아니다.

    아무리 '오래돼 보여도'(now_iso를 아주 먼 미래로 줘도) Running 빌드의 파드는
    지워지지 않아야 한다 -- 지우면 BuildRunner.poll이 객체 없음을 FAILED로 오인한다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    build_runner = _BuildRunnerSpy()
    bid = _make_build(repos)
    repos.builds.mark_running(bid)  # 아직 Running -- finish 호출 안 함(finished_at NULL)
    far_future = iso_plus(utc_now_iso(), 10_000_000)

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600, build_runner=build_runner)
    result = gc.run_once(now_iso=far_future)

    assert result == {"deleted": 0}
    assert build_runner.terminated == []
    assert repos.builds.get(bid)["state"] == "Running"


def test_build_runner_none_leaves_legacy_preflight_gc_unaffected(db):
    """9. build_runner=None(기본값)이면 빌드 파드 블록 자체가 스킵되고, 기존
    preflight pod GC 경로는 그대로 동작한다 -- builds 테이블에 종단 빌드가 있어도."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid = _make_job(repos, resource_key="kb1", refs={"preflight": "pod/p1"})
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    bid = _make_build(repos)
    repos.builds.mark_running(bid)
    repos.builds.finish(bid, state="Succeeded")
    updated_at = repos.data_jobs.get_job(jid)["updated_at"]
    now = iso_plus(updated_at, 3601)

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600)  # build_runner 기본값 None
    result = gc.run_once(now_iso=now)

    assert result == {"deleted": 1}  # job pod만 -- 빌드 파드는 대상 밖(러너가 없어서)
    assert adapter.terminated == ["pod/p1"]


def test_build_runner_none_skips_the_builds_query_entirely(db):
    """9b. build_runner=None이면 repos.builds.terminal_older_than 조회 자체를 안 부른다.

    (9)만으로는 부족하다 -- per-build terminate 호출을 감싼 except Exception이
    None.terminate() 의 AttributeError까지 삼켜버려서, "if self._build_runner is
    not None" 가드를 통째로 지워도 (9)의 단언(job pod 1건만 지워짐)은 여전히
    통과한다(빌드 파드 쪽은 예외로 조용히 실패할 뿐 deleted 카운트에 안 잡히므로).
    가드가 실제로 조회 자체를 막는지까지 봐야 진짜 회귀 가드가 된다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()

    def _boom(*args, **kwargs):
        raise AssertionError(
            "build_runner=None인데 builds.terminal_older_than이 호출됨")
    repos.builds.terminal_older_than = _boom

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600)
    result = gc.run_once(now_iso=iso_plus(utc_now_iso(), 10))

    assert result == {"deleted": 0}
