"""PodGarbageCollector: 종단 잡이 남긴 preflight Pod(pod/, pods/)와 실패 vcjob(vcjob/) 정리.

가장 중요한 단언(behavior 2): **비종단 잡은 절대 대상이 아니다** — 대상 잡이면 stepper가
아직 진행 중으로 보는 잡의 파드가 사라져 stepper가 그 잡을 실패로 오인한다.

빌드 파드 GC(build_runner가 주어졌을 때만 동작)에도 같은 단언이 적용된다: 종단
빌드만 대상이고, 비종단 빌드의 파드가 사라지면 BuildRunner.poll이 FAILED로 오인한다."""
from dms.build_runner import BUILD_REF_PREFIX
from dms.db import iso_plus, utc_now_iso
from dms.domain import DataJobState
from dms.execution import StubExecutionAdapter
from dms.pod_gc import PodGarbageCollector
from dms.repositories import Repositories
from dms.repositories.builds import build_pod_name, build_probe_pod_name


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


def _aged(repos, jid, seconds):
    return iso_plus(repos.data_jobs.get_job(jid)["updated_at"], seconds)


def test_vcjob_ref_of_a_failed_job_is_a_target(db):
    """4. 실패 잡의 vcjob/ ref 는 대상이다(2026-10-16): Volcano 의 ttlSecondsAfterFinished 는 Completed 만 치워, Aborted
    vcjob 과 그 Error 파드가 61일째 남아 있었다(테스트베드). 창은 max(after_seconds, vcjob TTL)."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid = _make_job(repos, resource_key="k4", refs={"preflight": "pod/p1", "execution": "vcjob/j1"})
    repos.data_jobs.set_job_state(jid, DataJobState.FAILED, actor="stepper")

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600, vcjob_ttl_seconds=7200)
    assert gc.run_once(now_iso=_aged(repos, jid, 3601)) == {"deleted": 0}     # TTL(7200) 전 -- Pod 도 함께 기다린다
    assert gc.run_once(now_iso=_aged(repos, jid, 7201)) == {"deleted": 2}
    assert sorted(adapter.terminated) == ["pod/p1", "vcjob/j1"]


def test_vcjob_of_a_succeeded_job_is_left_to_the_volcano_ttl(db):
    """Completed vcjob 은 Volcano TTL 의 몫 -- 운영자가 TTL 을 늘려 로그를 오래 두는 것을 pod-gc 가 앞질러 지우지 않는다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid = _make_job(repos, resource_key="k4s", refs={"preflight": "pod/p1", "execution": "vcjob/j1"})
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    gc = PodGarbageCollector(repos, adapter, after_seconds=3600, vcjob_ttl_seconds=7 * 86400)
    assert gc.run_once(now_iso=_aged(repos, jid, 3601)) == {"deleted": 1}
    assert adapter.terminated == ["pod/p1"]


def test_vcjob_ttl_zero_keeps_every_vcjob(db):
    """TTL 0 = 「vcjob 을 지우지 않는다」(_apply_ttl 이 ttlSecondsAfterFinished 를 뺀다) -- 실패 vcjob 도 둔다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jid = _make_job(repos, resource_key="k4z", refs={"preflight": "pod/p1", "execution": "vcjob/j1"})
    repos.data_jobs.set_job_state(jid, DataJobState.FAILED, actor="stepper")
    gc = PodGarbageCollector(repos, adapter, after_seconds=3600, vcjob_ttl_seconds=0)
    assert gc.run_once(now_iso=_aged(repos, jid, 3601)) == {"deleted": 1}
    assert adapter.terminated == ["pod/p1"]


def test_collected_jobs_drop_out_so_later_jobs_are_reached(db):
    """완료 표시(pods_gc_at, 2026-10-16): 예전엔 표시가 없어 오래된 순 LIMIT 이 매 틱 같은 앞쪽 묶음만 다시 지우고 그 뒤의
    잡에 영영 닿지 못했다. 치운 잡은 빠지고, 다음 틱이 다음 잡을 본다. updated_at 은 그대로다."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jids = []
    for i in range(3):
        jid = _make_job(repos, resource_key=f"kc{i}", refs={"preflight": f"pod/p{i}"})
        repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
        db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j",
                   {"t": f"2026-01-0{i + 1}T00:00:00Z", "j": jid})
        jids.append(jid)
    gc = PodGarbageCollector(repos, adapter, after_seconds=3600, limit=2)
    now = "2026-02-01T00:00:00Z"
    gc.run_once(now_iso=now)
    assert adapter.terminated == ["pod/p0", "pod/p1"]
    gc.run_once(now_iso=now)
    assert adapter.terminated[2:] == ["pod/p2"]           # 예전엔 p0·p1 만 되풀이
    gc.run_once(now_iso=now)
    assert adapter.terminated[3:] == []                    # 다 치웠다 -- 다시 지우지 않는다
    assert repos.data_jobs.get_job(jids[0])["updated_at"] == "2026-01-01T00:00:00Z"
    assert repos.data_jobs.get_job(jids[0])["pods_gc_at"] == now


def test_failed_jobs_back_off_and_do_not_hold_the_window(db):
    """지우기가 실패한 잡은 표시하지 않고 RETRY_BACKOFF_SECONDS 뒤에 다시 -- 그 사이 묶음에서 건너뛰고 더 읽어 뒤의 잡에
    닿는다. 실 어댑터는 실패를 전부 ExecutionError 로 접어 일시·영구를 가를 수 없다(검증 지적: 「영구 실패는 표시」 분기는
    실 어댑터로는 도달 불가였고, 계속 실패하는 잡 limit 개가 창을 다시 막았다)."""
    from dms.pod_gc import RETRY_BACKOFF_SECONDS
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()
    jids = []
    for i in range(3):
        jid = _make_job(repos, resource_key=f"kb{i}", refs={"preflight": f"pod/b{i}"})
        repos.data_jobs.set_job_state(jid, DataJobState.FAILED, actor="stepper")
        db.execute("UPDATE data_jobs SET updated_at = :t WHERE job_id = :j",
                   {"t": f"2026-01-0{i + 1}T00:00:00Z", "j": jid})
        jids.append(jid)
    adapter.fail_terminate("pod/b0")
    adapter.fail_terminate("pod/b1")
    gc = PodGarbageCollector(repos, adapter, after_seconds=3600, limit=2)
    now = "2026-02-01T00:00:00Z"
    gc.run_once(now_iso=now)
    assert adapter.terminated == ["pod/b0", "pod/b1"]
    gc.run_once(now_iso=now)                                  # 둘 다 재시도 대기 -- 건너뛰고 뒤의 잡
    assert adapter.terminated[2:] == ["pod/b2"]
    assert repos.data_jobs.get_job(jids[2])["pods_gc_at"] == now
    assert [repos.data_jobs.get_job(j)["pods_gc_at"] for j in jids[:2]] == [None, None]
    adapter._fail_terminate_refs.clear()
    later = iso_plus(now, RETRY_BACKOFF_SECONDS + 1)
    gc.run_once(now_iso=later)                                # 대기가 끝나면 다시 -- 이번엔 성공해 표시
    assert adapter.terminated[3:] == ["pod/b0", "pod/b1"]
    assert [repos.data_jobs.get_job(j)["pods_gc_at"] for j in jids[:2]] == [later, later]


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
    return repos.builds.create(source_path="/home/mason/dms-dev/dms", images=["dms"],
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

    # 슬라이스 21: 종단 빌드는 빌드 파드 + 프리플라이트 프로브 파드 2개를 남긴다 --
    # 같은 창으로 함께 수거한다(프로브 로그도 종단 후 after_seconds 동안은 증거로
    # 보존된다 -- 20-config.yaml 의 GC 창 주석과 같은 이유).
    assert result == {"deleted": 2}
    # I5: 리터럴 "buildpod" 대신 build_runner.py가 export하는 상수를 쓴다 -- 네 곳
    # (build_runner/build_watcher/pod_gc/routes_builds)이 각자 리터럴을 들고 있으면
    # 한 곳만 드리프트해도 조용히 깨진다.
    assert build_runner.terminated == [
        f"{BUILD_REF_PREFIX}/{build_pod_name(bid)}",
        f"{BUILD_REF_PREFIX}/{build_probe_pod_name(bid)}",
    ]
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


def test_build_pod_terminate_failure_still_reclaims_the_probe_pod(db):
    """10. ref 별 예외 격리: 빌드 파드 terminate 가 죽어도 프로브 파드 수거는
    계속된다(기존 잡 파드 GC 의 per-ref 격리와 같은 계약)."""
    repos = Repositories(db)
    adapter = _TerminateRecordingAdapter()

    class _BoomOnBuildPod:
        def __init__(self):
            self.terminated = []

        def terminate(self, ref):
            self.terminated.append(ref)
            if "/dms-build-pf-" not in ref:
                raise RuntimeError("boom")

    build_runner = _BoomOnBuildPod()
    bid = _make_build(repos)
    repos.builds.mark_running(bid)
    repos.builds.finish(bid, state="Succeeded")
    finished_at = repos.builds.get(bid)["finished_at"]
    now = iso_plus(finished_at, 3601)

    gc = PodGarbageCollector(repos, adapter, after_seconds=3600,
                             build_runner=build_runner)
    result = gc.run_once(now_iso=now)

    assert result == {"deleted": 1}   # 프로브만 성공 카운트
    assert build_runner.terminated == [
        f"{BUILD_REF_PREFIX}/{build_pod_name(bid)}",
        f"{BUILD_REF_PREFIX}/{build_probe_pod_name(bid)}",
    ]
    # 하나라도 실패했으면 완료 표시를 찍지 않는다(2026-10-26) -- 대기 뒤 다시 본다.
    assert repos.builds.get(bid)["pods_gc_at"] is None



def _finished_builds(repos, db, n, *, start="2026-01-01T00:00:00Z"):
    """종단 빌드 n 개(seq 순 = 완료 순). 활성 빌드 1개 가드 때문에 하나씩 끝내고 다음을 만든다."""
    bids = []
    for i in range(n):
        bid = _make_build(repos)
        repos.builds.finish(bid, state="Succeeded" if i % 2 == 0 else "Failed")
        db.execute("UPDATE builds SET finished_at = :t WHERE build_id = :id",
                   {"t": iso_plus(start, i), "id": bid})
        bids.append(bid)
    return bids


def test_more_than_200_terminal_builds_are_all_collected_over_ticks(db):
    """11. 빌드 완료 표시(builds.pods_gc_at, 2026-10-26 BACKLOG 「빌드 파드 GC 도 200건 창 고착」): 예전엔 표시가 없어
    종단 빌드가 limit(200)을 넘으면 매 틱 같은 앞쪽 200건만 다시 지우고 그 뒤 빌드의 파드에 영영 닿지 못했다. 이제 치운
    빌드는 빠지고 다음 틱이 다음 묶음을 본다 -- 250건이 두 틱에 전부, 셋째 틱은 아무것도 다시 지우지 않는다."""
    repos = Repositories(db)
    build_runner = _BuildRunnerSpy()
    bids = _finished_builds(repos, db, 250)
    gc = PodGarbageCollector(repos, _TerminateRecordingAdapter(), after_seconds=3600,
                             build_runner=build_runner)
    now = "2026-02-01T00:00:00Z"
    assert gc.run_once(now_iso=now) == {"deleted": 400}            # 200 빌드 × (빌드 + 프로브)
    assert gc.run_once(now_iso=now) == {"deleted": 100}            # 나머지 50 빌드 -- 예전엔 앞 200 을 되풀이
    assert gc.run_once(now_iso=now) == {"deleted": 0}              # 다 치웠다
    pods = {r.split("/", 1)[1] for r in build_runner.terminated}
    assert len(build_runner.terminated) == 500 and len(pods) == 500  # 빠짐도 중복도 없다
    assert all(build_pod_name(b) in pods and build_probe_pod_name(b) in pods for b in bids)
    assert {repos.builds.get(b)["pods_gc_at"] for b in bids} == {now}
    assert repos.builds.terminal_older_than(3600, now_iso=now) == []


def test_failed_build_cleanup_backs_off_and_does_not_hold_the_window(db):
    """12. 파드 지우기가 실패한 빌드는 표시하지 않고 RETRY_BACKOFF_SECONDS 뒤에 다시 -- 그 사이 묶음에서 건너뛰고 더 읽어
    뒤의 빌드에 닿는다(잡 파드 GC 와 같은 규칙). 한 파드만 실패해도 표시하지 않는다(다른 파드는 지운다)."""
    from dms.pod_gc import RETRY_BACKOFF_SECONDS
    repos = Repositories(db)
    b0, b1, b2 = _finished_builds(repos, db, 3)
    bad = {f"{BUILD_REF_PREFIX}/{build_probe_pod_name(b0)}", f"{BUILD_REF_PREFIX}/{build_pod_name(b1)}"}

    class _Flaky:
        def __init__(self):
            self.terminated = []
            self.broken = set(bad)

        def terminate(self, ref):
            self.terminated.append(ref)
            if ref in self.broken:
                raise RuntimeError("apiserver down")

    runner = _Flaky()
    gc = PodGarbageCollector(repos, _TerminateRecordingAdapter(), after_seconds=3600, limit=2,
                             build_runner=runner)
    now = "2026-02-01T00:00:00Z"
    assert gc.run_once(now_iso=now) == {"deleted": 2}               # b0·b1 각 한 파드만 성공
    assert [repos.builds.get(b)["pods_gc_at"] for b in (b0, b1)] == [None, None]
    runner.terminated.clear()
    assert gc.run_once(now_iso=now) == {"deleted": 2}               # 둘 다 대기 -- 건너뛰고 b2
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/{build_pod_name(b2)}",
                                 f"{BUILD_REF_PREFIX}/{build_probe_pod_name(b2)}"]
    assert repos.builds.get(b2)["pods_gc_at"] == now
    runner.broken.clear()
    runner.terminated.clear()
    later = iso_plus(now, RETRY_BACKOFF_SECONDS + 1)
    assert gc.run_once(now_iso=later) == {"deleted": 4}             # 대기가 끝나면 다시 -- 이번엔 성공해 표시
    assert [repos.builds.get(b)["pods_gc_at"] for b in (b0, b1)] == [later, later]


def test_build_retry_memory_is_separate_from_the_job_one(db):
    """13. 잡 재시도 기억과 빌드 재시도 기억은 따로다 -- 잡 쪽에 같은 id 가 대기 중이어도 빌드 정리는 막히지 않는다."""
    repos = Repositories(db)
    (bid,) = _finished_builds(repos, db, 1)
    gc = PodGarbageCollector(repos, _TerminateRecordingAdapter(), after_seconds=3600,
                             build_runner=_BuildRunnerSpy())
    gc._retry_at[bid] = 10 ** 12
    now = "2026-02-01T00:00:00Z"
    assert gc.run_once(now_iso=now) == {"deleted": 2}
    assert repos.builds.get(bid)["pods_gc_at"] == now
