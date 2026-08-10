import pytest
from dms.repositories import Repositories


@pytest.fixture
def repos(db):
    return Repositories(db)


def _seed_job(db, repos, *, created_at, state="Succeeded", tool="dscan",
              storage="s1", dest_storage=None, requester="alice",
              reason_code=None, updated_at=None, files=None, nbytes=None,
              wait=None):
    """data_jobs 한 행을 원하는 상태·시각으로 심는다. set_job_state는 updated_at을
    현재 시각으로 찍으므로 창(window) 테스트가 불가능하다 -- 정상 경로로 만들고
    시각·상태만 UPDATE로 덮는다. wait 는 submit_wait_seconds(기본 NULL -- 백필
    공백/진행 중과 같은 모양)."""
    rid = repos.requests.create(
        operation="scan", requester_id=requester, actor=requester,
        resource_key=f"k:{created_at}:{tool}:{state}:{requester}", payload={},
        priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation="scan", priority="mid", storage_name=storage,
        destination_storage=dest_storage, target="a", options={}, tool=tool,
        worker_pool={}, precondition={}, actor="planner")
    db.execute(
        """UPDATE data_jobs SET state = :st, reason_code = :rc, created_at = :c,
               updated_at = :u, files_count = :f, bytes_count = :b,
               submit_wait_seconds = :w
           WHERE job_id = :j""",
        {"st": state, "rc": reason_code, "c": created_at,
         "u": updated_at or created_at, "f": files, "b": nbytes, "w": wait,
         "j": job_id})
    return job_id


# ---- node_series ----

def test_node_series_is_windowed_inclusive_and_ascending(db, repos):
    for i, at in enumerate(["2026-08-09T00:00:00Z", "2026-08-09T01:00:00Z",
                            "2026-08-09T02:00:00Z"]):
        repos.agents.ingest("n1", {"seq": i, "os": {}}, reported_at=at)
    rows = repos.metrics.node_series(
        "n1", start="2026-08-09T00:30:00Z", end="2026-08-09T02:00:00Z")
    # BETWEEN이라 끝 경계 포함, 시간 오름차순(id tiebreak)
    assert [r["reported_at"] for r in rows] == [
        "2026-08-09T01:00:00Z", "2026-08-09T02:00:00Z"]
    assert rows[0]["report"]["seq"] == 1


def test_node_series_is_scoped_to_the_node(db, repos):
    repos.agents.ingest("n1", {"seq": 1}, reported_at="2026-08-09T00:00:00Z")
    repos.agents.ingest("n2", {"seq": 2}, reported_at="2026-08-09T00:00:00Z")
    rows = repos.metrics.node_series(
        "n1", start="2026-08-09T00:00:00Z", end="2026-08-09T01:00:00Z")
    assert [r["report"]["seq"] for r in rows] == [1]


def test_node_series_skips_only_the_corrupt_row(db, repos):
    # 설계 §3 fail-soft: 손상 리포트 하나가 시리즈 전체를 죽이면 안 된다.
    # 저장 경로는 dump_json을 거치므로 손상은 DB 직접 조작으로만 재현된다.
    repos.agents.ingest("n1", {"seq": 0}, reported_at="2026-08-09T00:00:00Z")
    repos.agents.ingest("n1", {"seq": 1}, reported_at="2026-08-09T01:00:00Z")
    db.execute("UPDATE agent_reports SET report = '{broken' WHERE reported_at = :at",
               {"at": "2026-08-09T00:00:00Z"})
    rows = repos.metrics.node_series(
        "n1", start="2026-08-09T00:00:00Z", end="2026-08-09T02:00:00Z")
    assert [r["report"]["seq"] for r in rows] == [1]


# ---- job_stats ----

def test_job_stats_by_state_tool_and_failure_reasons(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z")
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", state="Failed",
              tool="dsync", reason_code="execution_failed")
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z", state="Failed",
              tool="dsync", reason_code="execution_failed")
    _seed_job(db, repos, created_at="2026-07-01T00:00:00Z", tool="drm")  # 창 밖
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["by_state"] == [{"state": "Failed", "count": 2},
                                 {"state": "Succeeded", "count": 1}]
    assert stats["by_tool"] == [
        {"tool": "dsync", "count": 2, "succeeded": 0, "failed": 2},
        {"tool": "dscan", "count": 1, "succeeded": 1, "failed": 0}]
    assert stats["failure_reasons"] == [
        {"reason_code": "execution_failed", "count": 2}]


def test_job_stats_storage_falls_back_to_destination(db, repos):
    # sync 잡은 storage_name이 NULL이고 도착지가 destination_storage에 있다
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", storage=None,
              dest_storage="s2", tool="nsync")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["by_storage"] == [
        {"storage": "s2", "count": 1, "succeeded": 1, "failed": 0}]


def test_job_stats_requester_comes_from_requests_join(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", requester="alice")
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", requester="bob",
              state="Failed", reason_code="execution_failed")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["by_requester"] == [
        {"requester_id": "alice", "count": 1, "succeeded": 1, "failed": 0},
        {"requester_id": "bob", "count": 1, "succeeded": 0, "failed": 1}]


def test_job_stats_throughput_buckets_by_iso_prefix(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:10:00Z")
    _seed_job(db, repos, created_at="2026-08-09T01:50:00Z")
    _seed_job(db, repos, created_at="2026-08-09T02:05:00Z")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z", bucket_chars=13)
    assert stats["throughput"] == [{"bucket": "2026-08-09T01", "count": 2},
                                   {"bucket": "2026-08-09T02", "count": 1}]
    daily = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z", bucket_chars=10)
    assert daily["throughput"] == [{"bucket": "2026-08-09", "count": 3}]


def test_job_stats_duration_only_from_terminal_jobs(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z",
              updated_at="2026-08-09T01:00:30Z")
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", state="Pending")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["duration_seconds"] == [30.0]   # 비종단(Pending)은 진행 중 -- 제외


def test_job_stats_files_bytes_sum_only_succeeded_and_null_safe(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", files=10, nbytes=100)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z")            # NULL
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z", state="Failed",
              reason_code="execution_failed", files=5, nbytes=50)      # 실패분 제외
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert (stats["files_total"], stats["bytes_total"]) == (10, 100)


def test_job_stats_files_bytes_all_null_is_none(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["files_total"] is None and stats["bytes_total"] is None


def test_job_stats_submit_wait_excludes_null_and_surfaces_the_gap(db, repos):
    # NULL(백필 불가분·아직 Pending)을 0 으로 세면 평균·분포가 통째로 왜곡된다 --
    # 제외하되, 제외 건수를 함께 내 화면이 공백을 숨기지 못하게 한다(설계 §3).
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", wait=5)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", wait=45)
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z")            # NULL
    _seed_job(db, repos, created_at="2026-07-01T00:00:00Z", wait=999)  # 창 밖
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert sorted(stats["submit_wait_seconds"]) == [5, 45]
    assert stats["submit_wait_excluded"] == 1


def test_job_stats_submit_wait_zero_is_counted_not_excluded(db, repos):
    # 시각 해상도가 1초라 스테퍼가 같은 초 안에 픽업하면 0 이 **정상 기록**이다.
    # NULL 과 0 을 가르는 술어가 falsy 검사로 새면(`COALESCE(w,0) = 0`,
    # `NOT w`, 앱측 `if not w`) 0 인 잡이 "미기록"으로 분류돼 집계 대상에서
    # 빠지고 제외 건수가 부풀어, 가장 빨랐던 잡들이 분포에서 사라진다.
    # 슬라이스 17 태스크 5 가 백필 재실행에서 잡았던 것과 같은 함정이다.
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", wait=0)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", wait=7)
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z")            # NULL
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert sorted(stats["submit_wait_seconds"]) == [0, 7]  # 0 이 원자료에 남는다
    assert stats["submit_wait_excluded"] == 1              # NULL 만 제외 -- 0 은 아니다
