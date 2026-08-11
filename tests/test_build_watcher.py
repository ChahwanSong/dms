import pytest
from dms.build_runner import BUILD_REF_PREFIX, StubBuildRunner
from dms.build_watcher import BuildWatcher, parse_commit_sha, parse_preflight_reason
from dms.db import Database, iso_plus, utc_now_iso
from dms.execution import ExecStatus, ExecutionError
from dms.migrations import migrate
from dms.repositories import Repositories


class _Runner:
    """빌드 파드(dms-build-<hex>)와 프로브 파드(dms-build-pf-<hex>)를 ref 로
    판별하는 페어. build_id 는 uuid4 hex 라 'pf' 세그먼트와 절대 충돌하지 않는다."""

    def __init__(self, status=ExecStatus.SUCCEEDED, log="DMS_COMMIT_SHA=deadbeef\n",
                 probe_status=ExecStatus.SUCCEEDED, probe_log="DMS_PREFLIGHT_OK\n"):
        self.status = status
        self.log = log
        self.probe_status = probe_status
        self.probe_log = probe_log
        self.submitted = []
        self.probe_submitted = []
        self.polled = []
        self.terminated = []
        self.fail_submit = None
        self.fail_submit_preflight = None
        self.fail_poll = None  # I6: reason_code로 세팅하면 poll에서 ExecutionError
        self.fail_read_log = None  # reason_code로 세팅하면 read_log에서 ExecutionError

    def _is_probe(self, ref):
        return "/dms-build-pf-" in ref

    def submit(self, build):
        if self.fail_submit:
            raise ExecutionError(self.fail_submit, "nope")
        self.submitted.append(build["build_id"])
        return f"{BUILD_REF_PREFIX}/dms-build-{build['build_id'][:12]}"

    def submit_preflight(self, build):
        if self.fail_submit_preflight:
            raise ExecutionError(self.fail_submit_preflight, "preflight: nope")
        self.probe_submitted.append(build["build_id"])
        return f"{BUILD_REF_PREFIX}/dms-build-pf-{build['build_id'][:12]}"

    def poll(self, ref):
        self.polled.append(ref)
        if self.fail_poll:
            raise ExecutionError(self.fail_poll, "transient")
        return self.probe_status if self._is_probe(ref) else self.status

    def read_log(self, ref):
        if self.fail_read_log:
            raise ExecutionError(self.fail_read_log, "boom")
        return self.probe_log if self._is_probe(ref) else self.log

    def terminate(self, ref):
        self.terminated.append(ref)


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def _mk(repos):
    return repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                               node_name="dms-w1", actor="a")


def test_pending_build_is_submitted_and_becomes_running(repos):
    bid = _mk(repos)
    runner = _Runner(status=ExecStatus.RUNNING)
    out = BuildWatcher(repos, runner).run_once()
    assert out["submitted"] == 1
    assert runner.submitted == [bid]
    assert repos.builds.get(bid)["state"] == "Running"


def test_submit_failure_is_recorded_as_failed_not_raised(repos):
    # 루프 예외는 상위에서 삼켜진다 -- 실패는 반드시 DB 상태로 드러나야 한다
    bid = _mk(repos)
    runner = _Runner()
    runner.fail_submit = "submit_failed"
    BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "submit_failed")


def test_running_build_finishes_and_captures_commit_and_log(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    runner = _Runner(status=ExecStatus.SUCCEEDED,
                     log="=== building ===\nDMS_COMMIT_SHA=deadbeef1234\nDMS_BUILD_OK\n")
    out = BuildWatcher(repos, runner).run_once()
    assert out["finished"] == 1
    row = repos.builds.get(bid)
    assert row["state"] == "Succeeded"
    assert row["commit_sha"] == "deadbeef1234"
    assert "DMS_BUILD_OK" in row["log_text"]


def test_failed_pod_becomes_failed_build(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    BuildWatcher(repos, _Runner(status=ExecStatus.FAILED)).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_failed")


def test_running_build_stays_running_while_pod_runs(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    out = BuildWatcher(repos, _Runner(status=ExecStatus.RUNNING)).run_once()
    assert out["finished"] == 0
    assert repos.builds.get(bid)["state"] == "Running"


def test_run_once_is_idempotent_on_terminal_builds(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    w = BuildWatcher(repos, _Runner())
    w.run_once()
    before = repos.builds.get(bid)
    w.run_once()
    assert repos.builds.get(bid) == before


def test_running_build_is_polled_with_the_exported_ref_prefix(repos):
    # I5: 폴링에 실제로 전달되는 ref가 build_pod_name 기반, buildpod/ 접두인지 --
    # 이걸 확인 안 하면 접두 리터럴이 어긋나도(예: "pod/"로 오타) 테스트가 못 잡는다.
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    runner = _Runner(status=ExecStatus.RUNNING)
    BuildWatcher(repos, runner).run_once()
    assert runner.polled == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]


@pytest.mark.parametrize("text,expected", [
    ("DMS_COMMIT_SHA=abc123\n", "abc123"),
    ("noise\nDMS_COMMIT_SHA=abc123\nmore\n", "abc123"),
    ("no marker here", None),
    ("DMS_COMMIT_SHA=\n", None),
])
def test_parse_commit_sha(text, expected):
    assert parse_commit_sha(text) == expected


def test_parse_commit_sha_handles_none():
    assert parse_commit_sha(None) is None


# ---- C2(b): 나이 기반 회수 -- kubelet의 activeDeadlineSeconds는 스케줄된 뒤에만
# 발화하므로, nodeSelector 오타 등으로 영원히 Pending인 파드는 이 경로로만 잡힌다.

def test_running_build_past_deadline_is_terminated_and_marked_timeout(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    created_at = repos.builds.get(bid)["created_at"]
    # 로그가 있는 러너로 만든다 -- 회수 시점에도 이 로그가 박제돼야 한다(아래 단언).
    runner = _Runner(status=ExecStatus.RUNNING,
                     log="=== building pkg-01:5000/dms:b01234567 ===\nnpm install...\n")
    now = iso_plus(created_at, 7201)  # 기본 타임아웃(7200)을 막 넘겼다
    out = BuildWatcher(repos, runner, timeout_seconds=7200).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_timeout")
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
    # 슬라이스 21 §2.1: 회수 전에 딱 한 번 poll 해 PENDING(스케줄 불가)을 구분한다.
    # 이 테스트의 파드는 RUNNING 이므로 generic build_timeout 이 맞다.
    assert runner.polled == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
    assert out["finished"] == 1
    # 타임아웃은 원인 규명이 가장 필요한 실패(어디서 멈췄나 -- clone? npm? make? push?)인데,
    # read_log 없이 terminate만 하면 파드가 즉시 사라져 유일한 증거가 없어진다. 성공/실패
    # 종단 경로와 똑같이 회수 경로도 로그를 박제해야 한다.
    assert row["log_text"] == runner.log


def test_reclaim_survives_read_log_failure_and_still_marks_timeout(repos):
    # read_log가 예외를 던져도(파드가 이미 사라졌거나 네트워크 문제) 회수 자체는
    # 반드시 끝나야 한다 -- 안 그러면 회수가 스킵돼 빌드가 다시 Running에 갇힌다.
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner(status=ExecStatus.RUNNING)
    runner.fail_read_log = "log_read_failed"
    now = iso_plus(created_at, 7201)
    out = BuildWatcher(repos, runner, timeout_seconds=7200).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_timeout")
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
    assert out["finished"] == 1
    assert row["log_text"] is None  # 못 읽었을 뿐 -- 크래시도, COALESCE로 값이 지워지지도 않는다


def test_running_build_before_deadline_is_left_alone(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner(status=ExecStatus.RUNNING)
    now = iso_plus(created_at, 100)  # 마감(7200)에 한참 못 미침
    out = BuildWatcher(repos, runner, timeout_seconds=7200).run_once(now_iso=now)
    assert repos.builds.get(bid)["state"] == "Running"
    assert runner.terminated == []
    assert out["finished"] == 0


def test_timeout_disabled_by_default_regardless_of_age(repos):
    # timeout_seconds를 안 주면(기존 호출자와의 하위호환) 아무리 오래돼도 회수 안 한다.
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    runner = _Runner(status=ExecStatus.RUNNING)
    far_future = iso_plus(utc_now_iso(), 10_000_000)
    BuildWatcher(repos, runner).run_once(now_iso=far_future)
    assert repos.builds.get(bid)["state"] == "Running"
    assert runner.terminated == []


def test_terminate_failure_during_reclaim_still_marks_failed(repos):
    # terminate가 실패해도(파드가 이미 사라졌다거나) 타임아웃 판정 자체는 지켜야 한다 --
    # 안 그러면 이미 죽은 파드를 가리키는 빌드가 Running에 영영 낀다.
    class _BoomTerminate(_Runner):
        def terminate(self, ref):
            raise ExecutionError("terminate_failed", "boom")
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    created_at = repos.builds.get(bid)["created_at"]
    now = iso_plus(created_at, 7201)
    BuildWatcher(repos, _BoomTerminate(), timeout_seconds=7200).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_timeout")


# ---- I6: poll의 일시적 오류 하나가 빌드를 영구 실패로 만들면 안 된다 -- stepper.py처럼
# 빌드별로 예외를 격리해 로그만 남기고 상태는 그대로 둬서 다음 틱이 재시도하게 한다.
# C2(b)의 나이 기반 회수가 없으면 이 격리는 영구 오류 시 Running에 가두는 것과 같으므로
# 반드시 함께 검증한다(위 타임아웃 테스트들이 그 짝이다).

def test_transient_poll_error_does_not_fail_the_build(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    runner = _Runner()
    runner.fail_poll = "poll_failed"
    out = BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert row["state"] == "Running"        # Failed로 못박히지 않는다
    assert row["reason_code"] is None
    assert out["finished"] == 0


def test_run_once_does_not_raise_when_poll_errors(repos):
    # run_once 자체가 예외를 내면 controller.run_all_once가 이 틱의 build-watcher
    # 루프 전체(=제출 처리까지)를 건너뛴다 -- 빌드별 격리는 이걸 막는다.
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    runner = _Runner()
    runner.fail_poll = "poll_failed"
    BuildWatcher(repos, runner).run_once()  # 예외 없이 반환돼야 한다


def test_transient_poll_error_is_retried_and_succeeds_next_tick(repos):
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    runner = _Runner()
    runner.fail_poll = "poll_failed"
    BuildWatcher(repos, runner).run_once()
    assert repos.builds.get(bid)["state"] == "Running"

    runner.fail_poll = None
    runner.status = ExecStatus.SUCCEEDED
    BuildWatcher(repos, runner).run_once()
    assert repos.builds.get(bid)["state"] == "Succeeded"


# ---- 슬라이스 21 §2.5: 프리플라이트 상태기계 (Pending -> 프로브 -> 빌드 제출) ----

def test_pending_build_creates_probe_and_waits_while_probe_runs(repos):
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.RUNNING)
    out = BuildWatcher(repos, runner).run_once()
    assert runner.probe_submitted == [bid]
    assert runner.submitted == []            # 프로브가 끝나기 전엔 빌드 제출 금지
    assert repos.builds.get(bid)["state"] == "Pending"
    assert out == {"submitted": 0, "finished": 0}


def test_probe_success_with_ok_marker_submits_build_and_marks_running(repos):
    bid = _mk(repos)
    runner = _Runner(status=ExecStatus.RUNNING)   # 프로브 OK, 빌드 파드는 아직 돈다
    out = BuildWatcher(repos, runner).run_once()
    assert runner.probe_submitted == [bid]
    assert runner.submitted == [bid]
    assert repos.builds.get(bid)["state"] == "Running"
    assert out["submitted"] == 1


def test_probe_failure_adopts_whitelisted_marker_and_freezes_probe_log(repos):
    bid = _mk(repos)
    log = ("unreachable_443=github.com,quay.io\n"
           "DMS_PREFLIGHT_REASON=build_node_no_egress\n")
    runner = _Runner(probe_status=ExecStatus.FAILED, probe_log=log)
    out = BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_node_no_egress")
    # 실패 호스트 목록이 /log 로 보인다 -- "어느 호스트가 막혔나"가 첫 질문이다.
    assert "unreachable_443=github.com" in row["log_text"]
    assert runner.submitted == []
    assert out["finished"] == 1


def test_probe_marker_outside_whitelist_folds_to_build_preflight_failed(repos):
    # 파드 로그는 신뢰 입력이 아니다(설계 §4) -- 임의 문자열이 사유 코드로 승격되면
    # 프론트 매핑에 없는 코드가 지어내진다.
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.FAILED,
                     probe_log="DMS_PREFLIGHT_REASON=totally_made_up\n")
    BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_preflight_failed")


def test_probe_failure_without_any_marker_is_build_preflight_failed(repos):
    # 프로브가 스케줄은 됐는데 로그 없이 죽은 경우(OOM 등) -- 코드를 지어내지 않고
    # 접는다. log_text=None 은 COALESCE 라 기존 값을 지우지 않는다.
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.FAILED, probe_log=None)
    BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_preflight_failed")
    assert row["log_text"] is None


def test_probe_success_without_ok_marker_waits_for_next_tick(repos):
    # Succeeded 인데 로그가 아직 안 읽히면(일시 결손) 실패를 지어내지 않는다 --
    # 다음 틱이 재시도하고, 영영 안 오면 프리플라이트 타임아웃이 최후 회수다.
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.SUCCEEDED, probe_log=None)
    out = BuildWatcher(repos, runner).run_once()
    assert repos.builds.get(bid)["state"] == "Pending"
    assert runner.submitted == []
    assert out == {"submitted": 0, "finished": 0}


def test_probe_submit_failure_is_recorded_as_failed(repos):
    # 프로브 생성 실패(k8s API 오류)는 기존 submit_failed 재사용(설계 §4).
    bid = _mk(repos)
    runner = _Runner()
    runner.fail_submit_preflight = "submit_failed"
    BuildWatcher(repos, runner).run_once()
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "submit_failed")


def test_transient_probe_poll_error_leaves_build_pending(repos):
    # I6 관용구를 프로브에도: 일시 오류로 Failed 못박지 않는다 -- 다음 틱 재시도,
    # 영구 오류는 프리플라이트 타임아웃이 회수한다.
    bid = _mk(repos)
    runner = _Runner()
    runner.fail_poll = "poll_failed"
    out = BuildWatcher(repos, runner).run_once()   # 예외 없이 반환돼야 한다
    assert repos.builds.get(bid)["state"] == "Pending"
    assert out == {"submitted": 0, "finished": 0}


def test_pending_build_past_preflight_deadline_is_reclaimed(repos):
    # 프로브가 스케줄조차 안 되는 경우(노드 다운)의 유일한 탈출구 -- 로그 없이
    # build_preflight_timeout 이 잡는다(설계 §4).
    bid = _mk(repos)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner(probe_status=ExecStatus.PENDING, probe_log="scheduling...\n")
    now = iso_plus(created_at, 181)   # 기본 프리플라이트 창(180)을 막 넘겼다
    out = BuildWatcher(repos, runner,
                       preflight_timeout_seconds=180).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_preflight_timeout")
    assert row["log_text"] == "scheduling...\n"    # 회수 전에 로그부터 박제
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-pf-{bid[:12]}"]
    assert runner.probe_submitted == []            # 회수 틱에 새 프로브를 만들지 않는다
    assert out["finished"] == 1


def test_pending_build_within_preflight_deadline_waits(repos):
    bid = _mk(repos)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner(probe_status=ExecStatus.RUNNING)
    now = iso_plus(created_at, 100)   # 창(180)에 못 미침
    BuildWatcher(repos, runner, preflight_timeout_seconds=180).run_once(now_iso=now)
    assert repos.builds.get(bid)["state"] == "Pending"
    assert runner.terminated == []


def test_preflight_timeout_disabled_by_default_regardless_of_age(repos):
    # preflight_timeout_seconds 를 안 주면(기존 호출자 하위호환) 회수하지 않는다 --
    # timeout_seconds 와 같은 규칙. 실제 배선(controller.py)은 항상 settings 를 넘긴다.
    bid = _mk(repos)
    runner = _Runner(probe_status=ExecStatus.RUNNING)
    far_future = iso_plus(utc_now_iso(), 10_000_000)
    BuildWatcher(repos, runner).run_once(now_iso=far_future)
    assert repos.builds.get(bid)["state"] == "Pending"
    assert runner.terminated == []


# ---- 슬라이스 21 §2.1: 회수 분기의 Pending 구분 ----

def test_running_build_stuck_pending_past_deadline_gets_its_own_code(repos):
    # activeDeadlineSeconds 는 스케줄 후에만 발화한다(§1-9) -- 2시간째 파드가
    # PENDING 이면 "노드에 자리가 없다/노드 문제"라는 뜻이고, generic build_timeout
    # 으로 접으면 운영자가 원인을 모른다.
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner(status=ExecStatus.PENDING, log="0/6 nodes are available...\n")
    now = iso_plus(created_at, 7201)
    out = BuildWatcher(repos, runner, timeout_seconds=7200).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_stuck_pending")
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
    assert out["finished"] == 1


def test_reclaim_poll_failure_falls_back_to_generic_build_timeout(repos):
    # 구분용 poll 이 실패해도 회수 자체는 막히면 안 된다 -- generic 코드로 폴백.
    bid = _mk(repos)
    repos.builds.mark_running(bid)
    created_at = repos.builds.get(bid)["created_at"]
    runner = _Runner()
    runner.fail_poll = "poll_failed"
    now = iso_plus(created_at, 7201)
    BuildWatcher(repos, runner, timeout_seconds=7200).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_timeout")


# ---- 스텁 경로 계약 + 마커 파서 ----

def test_stub_runner_full_flow_with_preflight_succeeds_without_a_cluster(repos):
    # 로컬·CI 계약(설계 §4): StubBuildRunner 는 프리플라이트 포함 **한 틱에**
    # Succeeded 다(pending 루프가 제출·mark_running 하면 같은 run_once 의 running
    # 재조회가 종단시킨다 -- 기존 흐름과 동일).
    bid = _mk(repos)
    out = BuildWatcher(repos, StubBuildRunner(), timeout_seconds=7200,
                       preflight_timeout_seconds=180).run_once()
    row = repos.builds.get(bid)
    assert row["state"] == "Succeeded"
    assert row["commit_sha"] == "stubcommit"
    assert out == {"submitted": 1, "finished": 1}


@pytest.mark.parametrize("text,expected", [
    ("DMS_PREFLIGHT_REASON=build_node_no_egress\n", "build_node_no_egress"),
    ("noise\nDMS_PREFLIGHT_REASON=build_node_disk_low\nmore\n", "build_node_disk_low"),
    ("no marker here", None),
    ("DMS_PREFLIGHT_REASON=\n", None),
    (None, None),
])
def test_parse_preflight_reason(text, expected):
    assert parse_preflight_reason(text) == expected
