import pytest
from dms.db import Database
from dms.domain import DomainValidationError
from dms.migrations import migrate
from dms.repositories import Repositories
from dms.db import load_json
from dms.repositories.builds import (BUILD_IMAGES, build_pod_name, build_tag,
                                     effective_tag)


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def test_create_returns_pending_build_with_list_images(repos):
    bid = repos.builds.create(source_path="/home/mason/dms-dev/dms",
                              images=["dms"], node_name="dms-w1", actor="admin")
    row = repos.builds.get(bid)
    assert row["state"] == "Pending"
    assert row["images"] == ["dms"]          # 리스트로 되돌아온다 (JSON 문자열 아님)
    assert row["node_name"] == "dms-w1"
    assert row["commit_sha"] is None


def test_create_writes_audit_row(repos):
    repos.builds.create(source_path="/home/mason/dms-dev/dms",
                        images=["dms"], node_name="dms-w1", actor="ops")
    entries = repos.control.audit_entries(limit=5)
    assert any(e["mutation_class"] == "build" and e["actor"] == "ops" for e in entries)


def test_create_audit_after_state_includes_source_path(repos):
    # I3: 감사 로그에 소스 경로가 빠져 있으면 admin이 임의 경로(변조된 트리)로 만든
    # 이미지가 push돼도 감사 기록에 "어느 소스의" 빌드인지가 안 남는다 -- commit
    # SHA만으로는 소스 위치를 특정할 수 없다.
    repos.builds.create(source_path="/home/mason/dms-dev/dms",
                        images=["dms"], node_name="dms-w1", actor="ops")
    entries = repos.control.audit_entries(limit=5)
    entry = next(e for e in entries if e["mutation_class"] == "build")
    assert load_json(entry["after_state"])["source_path"] == "/home/mason/dms-dev/dms"


def test_active_sees_pending_and_running_only(repos):
    bid = repos.builds.create(source_path="/src/dms", images=["dms"],
                              node_name="dms-w1", actor="a")
    assert repos.builds.active()["build_id"] == bid
    repos.builds.mark_running(bid)
    assert repos.builds.active()["build_id"] == bid
    repos.builds.finish(bid, state="Succeeded")
    assert repos.builds.active() is None


def test_create_raises_when_active_build_exists(repos):
    # "동시에 활성 빌드 하나만" 불변식의 진짜 가드는 create() 트랜잭션 안에 있다 --
    # 여기서 이 가드 자체를 저장소 레벨에서 직접 검증한다(API 레벨의 409 테스트는
    # 라우터의 사전 active() "빠른 거절"만 확인할 뿐, 트랜잭션 안 가드까지는 못 짚는다).
    repos.builds.create(source_path="/src/dms", images=["dms"],
                        node_name="dms-w1", actor="a")
    with pytest.raises(DomainValidationError) as exc_info:
        repos.builds.create(source_path="/src/dms", images=["dms"],
                            node_name="dms-w1", actor="a")
    assert exc_info.value.reason_code == "build_in_progress"
    # 실패한 시도는 행을 남기지 않는다 -- 활성 빌드는 여전히 하나뿐.
    assert len(repos.builds.list(limit=10)) == 1


def test_finish_records_reason_commit_and_log(repos):
    bid = repos.builds.create(source_path="/src/dms", images=["dms"],
                              node_name="dms-w1", actor="a")
    repos.builds.finish(bid, state="Failed", reason_code="build_failed",
                        commit_sha="abc123", log_text="boom")
    row = repos.builds.get(bid)
    assert (row["state"], row["reason_code"]) == ("Failed", "build_failed")
    assert row["commit_sha"] == "abc123" and row["log_text"] == "boom"
    assert row["finished_at"] is not None


def test_list_excludes_log_text_and_seq(repos):
    # I2: list()는 목록 화면용이다 -- 프론트는 log_text를 목록에서 쓰지 않는데
    # (로그는 전용 /log 엔드포인트로 받는다), SELECT *를 쓰면 행마다 최대 64KB가
    # 딸려 온다(limit 기본 50 x 64KB = 최대 3.2MB). seq도 내부 정렬용 컬럼이라
    # 밖으로 새면 안 된다.
    bid = repos.builds.create(source_path="/src/dms", images=["dms"],
                              node_name="dms-w1", actor="x")
    repos.builds.finish(bid, state="Succeeded", log_text="x" * 1000)
    row = repos.builds.list(limit=10)[0]
    assert "log_text" not in row
    assert "seq" not in row
    assert row["build_id"] == bid  # 나머지 컬럼은 그대로 살아있다


def test_list_is_newest_first(repos):
    a = repos.builds.create(source_path="/src/dms", images=["dms"],
                            node_name="dms-w1", actor="x")
    repos.builds.finish(a, state="Succeeded")
    b = repos.builds.create(source_path="/src/dms", images=["dms"],
                            node_name="dms-w1", actor="x")
    assert [r["build_id"] for r in repos.builds.list(limit=10)][:2] == [b, a]


def test_terminal_older_than_excludes_running(repos):
    a = repos.builds.create(source_path="/src/dms", images=["dms"],
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


def test_control_state_carries_build_source_path(repos):
    repos.control.set_control_state(maintenance=False, drain=False, reason=None,
                                    build_node_name="dms-w1",
                                    build_source_path="/home/mason/dms-dev/dms",
                                    actor="ops")
    assert (repos.control.control_state()["build_source_path"]
            == "/home/mason/dms-dev/dms")


def test_source_path_rides_repo_url_column_with_local_marker(repos):
    # 컬럼 재사용의 계약을 고정한다: repo_url(NOT NULL, rename 불가 -- migrations
    # 주석)이 소스 경로를 담고, git_ref 는 상수 'local'(옛 git 시절 행과의 판별자).
    bid = repos.builds.create(source_path="/home/mason/dms-dev/dms",
                              images=["dms"], node_name="dms-w1", actor="a")
    row = repos.builds.get(bid)
    assert row["repo_url"] == "/home/mason/dms-dev/dms"
    assert row["git_ref"] == "local"


def test_effective_tag_prefers_the_operator_tag_over_derivation(repos):
    a = repos.builds.create(source_path="/src/dms", images=["dms"],
                            node_name="dms-w1", actor="a", tag="d73")
    row = repos.builds.get(a)
    assert row["tag"] == "d73"
    assert effective_tag(row) == "d73"
    repos.builds.finish(a, state="Succeeded")
    b = repos.builds.create(source_path="/src/dms", images=["dms"],
                            node_name="dms-w1", actor="a")
    row_b = repos.builds.get(b)
    assert row_b["tag"] is None
    assert effective_tag(row_b) == build_tag(b)


def test_list_includes_the_tag_column(repos):
    # 목록이 tag 를 빼면 이력 화면의 태그가 전부 파생값으로 보인다 -- 운영자 지정
    # 태그(d73)가 화면에서 사라지는 조용한 회귀를 여기서 잡는다.
    bid = repos.builds.create(source_path="/src/dms", images=["dms"],
                              node_name="dms-w1", actor="a", tag="d73")
    row = repos.builds.list(limit=5)[0]
    assert row["build_id"] == bid and row["tag"] == "d73"


def test_derived_names_are_deterministic_and_dns1123():
    bid = "0123456789abcdef0123456789abcdef"
    assert build_tag(bid) == "b01234567"
    name = build_pod_name(bid)
    assert name == "dms-build-0123456789ab"
    assert "_" not in name and len(name) <= 63


def test_build_images_are_in_dependency_order():
    assert BUILD_IMAGES == ("dms-mpifileutils", "dms", "dms-agent")
