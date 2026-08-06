import pytest
from dms.build_runner import BUILD_REF_PREFIX
from dms.build_watcher import BuildWatcher, parse_commit_sha
from dms.db import Database, iso_plus, utc_now_iso
from dms.execution import ExecStatus, ExecutionError
from dms.migrations import migrate
from dms.repositories import Repositories


class _Runner:
    def __init__(self, status=ExecStatus.SUCCEEDED, log="DMS_COMMIT_SHA=deadbeef\n"):
        self.status = status
        self.log = log
        self.submitted = []
        self.polled = []
        self.terminated = []
        self.fail_submit = None
        self.fail_poll = None  # I6: reason_code로 세팅하면 poll에서 ExecutionError

    def submit(self, build):
        if self.fail_submit:
            raise ExecutionError(self.fail_submit, "nope")
        self.submitted.append(build["build_id"])
        return f"{BUILD_REF_PREFIX}/dms-build-{build['build_id'][:12]}"

    def poll(self, ref):
        self.polled.append(ref)
        if self.fail_poll:
            raise ExecutionError(self.fail_poll, "transient")
        return self.status

    def read_log(self, ref):
        return self.log

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
    runner = _Runner(status=ExecStatus.RUNNING)  # poll에 도달하면 안 된다 -- 마감이 우선
    now = iso_plus(created_at, 7201)  # 기본 타임아웃(7200)을 막 넘겼다
    out = BuildWatcher(repos, runner, timeout_seconds=7200).run_once(now_iso=now)
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_timeout")
    assert runner.terminated == [f"{BUILD_REF_PREFIX}/dms-build-{bid[:12]}"]
    assert runner.polled == []  # 마감 넘긴 빌드는 poll을 부르지 않고 바로 회수한다
    assert out["finished"] == 1


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
