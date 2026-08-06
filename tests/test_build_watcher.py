import pytest
from dms.build_watcher import BuildWatcher, parse_commit_sha
from dms.db import Database
from dms.execution import ExecStatus, ExecutionError
from dms.migrations import migrate
from dms.repositories import Repositories


class _Runner:
    def __init__(self, status=ExecStatus.SUCCEEDED, log="DMS_COMMIT_SHA=deadbeef\n"):
        self.status = status
        self.log = log
        self.submitted = []
        self.fail_submit = None

    def submit(self, build):
        if self.fail_submit:
            raise ExecutionError(self.fail_submit, "nope")
        self.submitted.append(build["build_id"])
        return f"buildpod/dms-build-{build['build_id'][:12]}"

    def poll(self, ref):
        return self.status

    def read_log(self, ref):
        return self.log

    def terminate(self, ref):
        pass


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
