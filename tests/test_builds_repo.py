import pytest
from dms.db import Database
from dms.migrations import migrate
from dms.repositories import Repositories
from dms.repositories.builds import BUILD_IMAGES, build_pod_name, build_tag


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def test_create_returns_pending_build_with_list_images(repos):
    bid = repos.builds.create(repo_url="https://example/r.git", git_ref="main",
                              images=["dms"], node_name="dms-w1", actor="admin")
    row = repos.builds.get(bid)
    assert row["state"] == "Pending"
    assert row["images"] == ["dms"]          # 리스트로 되돌아온다 (JSON 문자열 아님)
    assert row["node_name"] == "dms-w1"
    assert row["commit_sha"] is None


def test_create_writes_audit_row(repos):
    repos.builds.create(repo_url="https://example/r.git", git_ref="main",
                        images=["dms"], node_name="dms-w1", actor="ops")
    entries = repos.control.audit_entries(limit=5)
    assert any(e["mutation_class"] == "build" and e["actor"] == "ops" for e in entries)


def test_active_sees_pending_and_running_only(repos):
    bid = repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                              node_name="dms-w1", actor="a")
    assert repos.builds.active()["build_id"] == bid
    repos.builds.mark_running(bid)
    assert repos.builds.active()["build_id"] == bid
    repos.builds.finish(bid, state="Succeeded")
    assert repos.builds.active() is None


def test_finish_records_reason_commit_and_log(repos):
    bid = repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                              node_name="dms-w1", actor="a")
    repos.builds.finish(bid, state="Failed", reason_code="build_failed",
                        commit_sha="abc123", log_text="boom")
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_failed")
    assert row["commit_sha"] == "abc123" and row["log_text"] == "boom"
    assert row["finished_at"] is not None


def test_list_is_newest_first(repos):
    a = repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                            node_name="dms-w1", actor="x")
    repos.builds.finish(a, state="Succeeded")
    b = repos.builds.create(repo_url="u", git_ref="dev", images=["dms"],
                            node_name="dms-w1", actor="x")
    assert [r["build_id"] for r in repos.builds.list(limit=10)][:2] == [b, a]


def test_terminal_older_than_excludes_running(repos):
    a = repos.builds.create(repo_url="u", git_ref="main", images=["dms"],
                            node_name="dms-w1", actor="x")
    repos.builds.mark_running(a)
    assert repos.builds.terminal_older_than(0, now_iso="2999-01-01T00:00:00Z") == []
    repos.builds.finish(a, state="Succeeded")
    got = repos.builds.terminal_older_than(0, now_iso="2999-01-01T00:00:00Z")
    assert [r["build_id"] for r in got] == [a]


def test_control_state_carries_build_node(repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1", actor="ops")
    assert repos.control.control_state()["build_node_name"] == "dms-w1"


def test_derived_names_are_deterministic_and_dns1123():
    bid = "0123456789abcdef0123456789abcdef"
    assert build_tag(bid) == "b01234567"
    name = build_pod_name(bid)
    assert name == "dms-build-0123456789ab"
    assert "_" not in name and len(name) <= 63


def test_build_images_are_in_dependency_order():
    assert BUILD_IMAGES == ("dms-mpifileutils", "dms", "dms-agent")
