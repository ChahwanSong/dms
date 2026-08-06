import pytest
from dms.db import Database
from dms.migrations import migrate
from dms.repositories import Repositories


@pytest.fixture
def repos(tmp_path):
    db = Database.connect(f"sqlite:///{tmp_path}/t.db")
    migrate(db)
    return Repositories(db)


def test_record_and_read_back_with_payload(repos):
    repos.observability.record_event(
        component="planner", severity="error", event_type="plan_error",
        message="boom", payload={"exc": "KeyError"}, request_id="r1")
    rows = repos.observability.events_for_request("r1")
    assert len(rows) == 1
    assert rows[0]["component"] == "planner"
    assert rows[0]["event_type"] == "plan_error"
    assert rows[0]["payload"] == {"exc": "KeyError"}   # dict 로 복원된다


def test_record_event_never_raises(repos):
    # 진단 기록 실패가 업무 경로를 죽이면 안 된다 -- 이것이 이 저장소의 유일한 계약이다.
    class _Boom:
        def execute(self, *a, **k): raise RuntimeError("db down")
        def query(self, *a, **k): raise RuntimeError("db down")
        def query_one(self, *a, **k): raise RuntimeError("db down")
    from dms.repositories.observability import ObservabilityRepository
    ObservabilityRepository(_Boom()).record_event(
        component="planner", severity="error", event_type="x")   # 예외가 나오면 실패


def test_events_are_scoped_to_the_request(repos):
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="a", request_id="r1")
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="b", request_id="r2")
    assert [e["event_type"] for e in repos.observability.events_for_request("r1")] == ["a"]


def test_null_request_id_is_allowed_and_excluded_from_request_scope(repos):
    repos.observability.record_event(component="stepper", severity="info",
                                     event_type="loop_tick")
    assert repos.observability.events_for_request("r1") == []


def test_events_for_request_truncation_drops_the_oldest_not_the_newest(repos):
    # 잘라야 한다면 오래된 쪽을 버린다 -- 장애 진단에 필요한 것은 최신이다(가장 최근에도
    # 실패가 계속되고 있는지가 운영자의 관심사). limit보다 많이 쌓이면 최신 limit건만
    # 남되, 반환은 여전히 시간 오름차순이어야 화면(오래된 것 위, 최신 것 아래)이
    # 자연스럽다.
    for i in range(5):
        repos.observability.record_event(component="planner", severity="error",
                                         event_type=f"e{i}", request_id="r1")
    events = repos.observability.events_for_request("r1", limit=3)
    assert [e["event_type"] for e in events] == ["e2", "e3", "e4"]


def test_prune_events_removes_only_old_rows(repos):
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="old", request_id="r1")
    assert repos.observability.prune_events("2999-01-01T00:00:00Z") == 1
    assert repos.observability.events_for_request("r1") == []


def test_prune_events_exhausts_all_batches_in_a_single_call(repos):
    # 회귀 가드: 예전 구현은 배치 1개만 지우고 리턴했다 -- 유입량이
    # batch_size/retention_interval_seconds를 넘으면 purge가 영원히 못
    # 따라잡는다. batch_size(2)보다 많은(5) 오래된 행을 넣고, 호출 한 번으로
    # 전부 지워지는지(= 내부에서 배치를 소진할 때까지 도는지) 고정한다.
    for i in range(5):
        repos.observability.record_event(component="planner", severity="error",
                                         event_type=f"old{i}", request_id="r1")
    assert repos.observability.prune_events("2999-01-01T00:00:00Z", batch_size=2) == 5
    assert repos.observability.events_for_request("r1") == []
