import pytest
from dms.domain import RequestState
from dms.repositories import Repositories


@pytest.fixture
def repos(db):
    return Repositories(db)


def _seed_job(db, repos, *, created_at, state="Succeeded", tool="dscan",
              operation="sync", storage="s1", dest_storage=None,
              requester="alice", reason_code=None, updated_at=None, files=None,
              nbytes=None, wait=None, sched=None, exec_at=None, summary=None):
    """data_jobs 한 행을 원하는 상태·시각으로 심는다. set_job_state는 updated_at을
    현재 시각으로 찍으므로 창(window) 테스트가 불가능하다 -- 정상 경로로 만들고
    시각·상태만 UPDATE로 덮는다. wait 는 submit_wait_seconds, sched 는
    sched_wait_seconds, exec_at 은 exec_submitted_at(셋 다 기본 NULL --
    기록 없음/진행 중과 같은 모양). operation 기본은 sync -- 처리량 합계가
    scan(관측)을 제외하므로(2026-08-23), 합계에 잡히는 쪽이 기본이어야 기존
    시나리오들이 의미를 유지한다. summary 는 result_summary -- 실제 저장 경로
    (data_jobs.set_artifact → db.dump_json)로 써서 지표의 v2 판별(LIKE)이 운영과
    같은 글자를 본다(files_count 는 아래 UPDATE 의 files 가 정한다)."""
    rid = repos.requests.create(
        operation=operation, requester_id=requester, actor=requester,
        resource_key=f"k:{created_at}:{tool}:{state}:{requester}", payload={},
        priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    job_id = repos.data_jobs.create_job(
        rid, plan_id, operation=operation, priority="mid", storage_name=storage,
        destination_storage=dest_storage, target="a", options={}, tool=tool,
        worker_pool={}, precondition={}, actor="planner")
    if summary is not None:
        repos.data_jobs.set_artifact(job_id, artifact_uri="x", result_summary=summary,
                                     promote_counts=False)
    db.execute(
        """UPDATE data_jobs SET state = :st, reason_code = :rc, created_at = :c,
               updated_at = :u, files_count = :f, bytes_count = :b,
               submit_wait_seconds = :w, sched_wait_seconds = :sw,
               exec_submitted_at = :x
           WHERE job_id = :j""",
        {"st": state, "rc": reason_code, "c": created_at,
         "u": updated_at or created_at, "f": files, "b": nbytes, "w": wait,
         "sw": sched, "x": exec_at, "j": job_id})
    return job_id


def _seed_rejected(db, repos, *, completed_at, reason="storage_missing",
                   state=RequestState.REJECTED):
    """계획 단계 종단(results 행)을 심는다. planner 의 set_state_with_result 와
    같은 경로라 data_jobs 행이 없다 -- 잡 통계 집계 밖의 그 구간이다.
    record_result 는 completed_at 을 벽시계로 찍으므로 창 테스트를 위해 UPDATE 로
    덮는다(_seed_job 과 같은 타협)."""
    rid = repos.requests.create(
        operation="scan", requester_id="alice", actor="alice",
        resource_key=f"k:rej:{completed_at}:{reason}:{state.value}", payload={},
        priority="mid")
    repos.requests.set_state_with_result(rid, state, reason_code=reason,
                                         actor="planner")
    db.execute("UPDATE results SET completed_at = :c WHERE request_id = :r",
               {"c": completed_at, "r": rid})
    return rid


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
        {"tool": "dsync", "count": 2, "succeeded": 0, "failed": 2, "rejected": 0, "cancelled": 0},
        {"tool": "dscan", "count": 1, "succeeded": 1, "failed": 0, "rejected": 0, "cancelled": 0}]
    assert stats["failure_reasons"] == [
        {"reason_code": "execution_failed", "count": 2}]


def test_job_stats_storage_falls_back_to_destination(db, repos):
    # sync 잡은 storage_name이 NULL이고 도착지가 destination_storage에 있다
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", storage=None,
              dest_storage="s2", tool="nsync")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["by_storage"] == [
        {"storage": "s2", "count": 1, "succeeded": 1, "failed": 0, "rejected": 0, "cancelled": 0}]


def test_job_stats_requester_comes_from_requests_join(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", requester="alice")
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", requester="bob",
              state="Failed", reason_code="execution_failed")
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["by_requester"] == [
        {"requester_id": "alice", "count": 1, "succeeded": 1, "failed": 0, "rejected": 0, "cancelled": 0},
        {"requester_id": "bob", "count": 1, "succeeded": 0, "failed": 1, "rejected": 0, "cancelled": 0}]


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


def _totals(stats):
    return (stats["sync_items_total"], stats["sync_bytes_total"], stats["rm_items_total"])


_EXEC = "2026-08-09T00:30:00Z"      # 실행 vcjob 제출 시각(exec_submitted_at) -- 합계는 실행까지 간 잡만 센다


def _v2(files, nbytes=None, **extra):
    """d192 러너의 실행 summary 모양(detail_version 2 -- files 는 실제로 일어난 일)."""
    return {"returncode": 1, "files": files, "bytes": nbytes, "detail_version": 2, "tool": "nsync",
            "dryrun": False, **extra}


def test_job_stats_sync_rm_totals_cover_every_terminal_state_and_null_safe(db, repos):
    # d192: files_count 는 실행에서 실제로 일어난 일만 담는다(미리보기 실패는 승격 안 함) -- 실패·취소된 실행이 이미
    # 복사·삭제한 항목도 실제 변경이라 종단 전체를 더한다(예전엔 Succeeded 만: 실패분 5·50 이 빠졌다). sync 와 rm 은
    # 따로 -- 예전 files_total 은 sync 반영 수와 rm 삭제 수를 한 숫자로 더했다. 성공이 아닌 행은 summary v2 일 때만
    # 센다(옛 뜻의 실패 행은 아래 test_job_stats_totals_count_failed_runs_only_with_the_v2_meaning).
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", files=10, nbytes=100, exec_at=_EXEC)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", exec_at=_EXEC)       # NULL -- 모름
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z", state="Failed",
              reason_code="sync_items_failed", files=5, nbytes=50, exec_at=_EXEC,
              summary=_v2(5, 50))                                                # 부분 처리도 실제
    _seed_job(db, repos, created_at="2026-08-09T04:00:00Z", state="Cancelled",
              reason_code="cancelled", files=2, nbytes=20, exec_at=_EXEC, summary=_v2(2, 20))
    _seed_job(db, repos, created_at="2026-08-09T05:00:00Z", state="Executing",
              files=99, nbytes=990, exec_at=_EXEC, summary=_v2(99, 990))         # 비종단 -- 아직 안 셈
    _seed_job(db, repos, created_at="2026-08-09T06:00:00Z", operation="rm", tool="drm",
              files=7, nbytes=1, exec_at=_EXEC)                                  # rm 바이트는 sync 에 안 섞인다
    _seed_job(db, repos, created_at="2026-08-09T07:00:00Z", operation="rm", tool="drm",
              state="Failed", reason_code="rm_items_failed", files=3, exec_at=_EXEC,
              summary=_v2(3, tool="drm"))
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert _totals(stats) == (17, 170, 10)
    assert "files_total" not in stats and "bytes_total" not in stats


def test_job_stats_totals_skip_jobs_that_never_reached_execution(db, repos):
    # d192 전 미리보기 실패는 계획 수(nsync 계획 합·dsync 소스 전체·drm 대상)를 컬럼에 올렸다 -- 그 행이 창 안에 남아
    # 있으면 종단 전체 합에 안 한 일이 섞인다. 실행 vcjob 을 제출한 적 없는 잡(exec_submitted_at NULL)은 세지 않는다.
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", files=10, nbytes=100, exec_at=_EXEC)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", state="Failed",
              reason_code="preview_failed", files=400, nbytes=4000)               # 옛 미리보기 실패 승격
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z", operation="rm", tool="drm", state="TimedOut",
              reason_code="preview_timed_out", files=50)
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert _totals(stats) == (10, 100, None)


def test_job_stats_totals_count_failed_runs_only_with_the_v2_meaning(db, repos):
    # d192 리뷰(control-9/38): d192 전 **실행** 실패도 files_count 를 올렸지만 옛 뜻이다(nsync = 계획 동작 합, drm = 삭제
    # 시도 수) -- 계획 1,000,000 중 10개만 복사하고 실패한 옛 잡이 「반영 1,000,000」으로 더해지면 안 된다. 성공이 아닌
    # 행은 result_summary 가 v2(detail_version 2)일 때만 센다. 성공 행은 옛 summary 여도 센다(옛 뜻도 실제 결과와 같다).
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", files=10, nbytes=100, exec_at=_EXEC,
              summary={"returncode": 0, "files": 10, "bytes": 100})               # 옛 성공 -- 센다
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", state="Failed", reason_code="execution_failed",
              files=1_000_000, nbytes=10 ** 9, exec_at=_EXEC,
              summary={"returncode": 1, "files": 1_000_000, "bytes": 10 ** 9})    # 옛 실패(계획 합) -- 안 센다
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z", state="TimedOut", reason_code="execution_failed",
              files=500, exec_at=_EXEC)                                           # summary 없음 -- 모름, 안 센다
    _seed_job(db, repos, created_at="2026-08-09T04:00:00Z", operation="rm", tool="drm", state="Failed",
              reason_code="execution_failed", files=40, exec_at=_EXEC,
              summary={"returncode": 1, "files": 40, "bytes": None})               # 옛 drm 시도 수 -- 안 센다
    # v2 러너가 상세 파싱에 실패해 핵심 3키만 쓴 실패 행도 뜻을 장담할 수 없다 -- 안 센다(모르면 더하지 않는 쪽).
    _seed_job(db, repos, created_at="2026-08-09T05:00:00Z", state="Failed", reason_code="execution_failed",
              files=70, exec_at=_EXEC, summary={"returncode": 1, "files": 70, "bytes": None})
    # 도구 문자열(failure_samples[].path)에 표식 글자를 심어도 json 이 따옴표를 이스케이프해 v2 로 읽히지 않는다.
    _seed_job(db, repos, created_at="2026-08-09T06:00:00Z", state="Failed", reason_code="execution_failed",
              files=900, exec_at=_EXEC,
              summary={"returncode": 1, "files": 900, "bytes": None,
                       "failure_samples": [{"op": "copy", "path": '/x/"detail_version": 2, "y', "errno": 13}]})
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z", end="2026-08-09T23:59:59Z")
    assert _totals(stats) == (10, 100, None)
    # 같은 실패 행이 v2 뜻이면(실제로 복사·삭제한 수) 더한다.
    _seed_job(db, repos, created_at="2026-08-09T07:00:00Z", state="Failed", reason_code="sync_items_failed",
              files=4, nbytes=8, exec_at=_EXEC, summary=_v2(4, 8))
    _seed_job(db, repos, created_at="2026-08-09T08:00:00Z", operation="rm", tool="drm", state="TimedOut",
              reason_code="execution_failed", files=6, exec_at=_EXEC, summary=_v2(6, tool="drm"))
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z", end="2026-08-09T23:59:59Z")
    assert _totals(stats) == (14, 108, 6)


def test_job_stats_v2_marker_matches_the_stored_summary_text(db, repos):
    # 판별은 저장된 TEXT 의 글자에 기댄다 -- 저장 직렬화(db.dump_json)가 바뀌면 v2 실패 행이 조용히 빠진다. 실제 저장 경로로
    # 쓴 글자에 표식이 들어 있는지 직접 본다(SQL LIKE 를 거치지 않는 두 번째 그물).
    from dms.repositories import metrics as m
    jid = _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", state="Failed", files=1, exec_at=_EXEC,
                    summary=_v2(1, copy_failed=2, failures={"total": 2, "by_op": {"copy": 2}}))
    text = db.query_one("SELECT result_summary FROM data_jobs WHERE job_id = :j", {"j": jid})["result_summary"]
    assert '"detail_version": 2,' in text
    assert m._SUMMARY_V2_LIKE == '%"detail\\_version": 2,%'


def test_job_stats_sums_exclude_scan_observation(db, repos):
    # scan 의 bytes_count 는 "관측한 트리 총 용량"(실 사용량, 2026-08-23)이지
    # 처리량이 아니다 -- 100 TiB 트리를 스캔만 해도 「처리 바이트」가 부풀면
    # sync 처리량이 스캔 관측량에 파묻힌다(반복 스캔은 사용량 분석의 정상 사용).
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", files=10, nbytes=100, exec_at=_EXEC)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", operation="scan",
              files=7, nbytes=10 ** 12, exec_at=_EXEC)
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert _totals(stats) == (10, 100, None)     # rm 이 없으면 0 이 아니라 모름


def test_job_stats_totals_all_null_is_none_but_zero_is_zero(db, repos):
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", exec_at=_EXEC)
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert _totals(stats) == (None, None, None)
    # 0 은 정상값(아무것도 바꾸지 않은 sync) -- 모름(None)과 뭉개지 않는다.
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", files=0, nbytes=0, exec_at=_EXEC)
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert _totals(stats) == (0, 0, None)


def test_job_stats_breakdown_separates_failed_rejected_cancelled(db, repos):
    # d192: 예전엔 「실패 = 종단 - Succeeded」라 사람이 취소한 잡·만료된 미리보기·점검 거부가 실패율에 섞였다.
    for i, (state, reason) in enumerate([
            ("Succeeded", None), ("Failed", "execution_failed"), ("TimedOut", "preview_timed_out"),
            ("Rejected", "empty_preview"), ("Cancelled", "cancelled"),
            ("PreviewExpired", "preview_expired")]):
        _seed_job(db, repos, created_at=f"2026-08-09T0{i + 1}:00:00Z", tool="nsync",
                  state=state, reason_code=reason)
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    row = {"count": 6, "succeeded": 1, "failed": 2, "rejected": 1, "cancelled": 2}
    assert stats["by_tool"] == [{"tool": "nsync", **row}]
    assert stats["by_storage"] == [{"storage": "s1", **row}]
    assert stats["by_requester"] == [{"requester_id": "alice", **row}]
    # 실패 사유 상위는 실패·시간초과·거부만 -- 취소·만료 사유는 원인 분석 대상이 아니다.
    assert stats["failure_reasons"] == [
        {"reason_code": "empty_preview", "count": 1},
        {"reason_code": "execution_failed", "count": 1},
        {"reason_code": "preview_timed_out", "count": 1}]


def test_job_stats_outcome_groups_partition_non_success_terminal_states():
    # 넷(성공·실패·거부·취소)이 종단 전체를 겹침 없이 나눈다 -- 새 종단 상태가 생기면 어느 열에도 안 잡히는 일이 없게.
    from dms.domain import TERMINAL_DATA_JOB_STATES
    from dms.repositories import metrics as m
    groups = [set(m._FAILED_STATES), set(m._REJECTED_STATES), set(m._CANCELLED_STATES)]
    union = set().union(*groups)
    assert sum(len(g) for g in groups) == len(union)
    assert union == {s.value for s in TERMINAL_DATA_JOB_STATES} - {"Succeeded"}


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


def test_job_stats_sched_wait_excludes_null_and_surfaces_the_gap(db, repos):
    # NULL(과거 잡·Running 미도달·한 틱 완료·스텁 백엔드)을 0 으로 세면 분포가
    # 통째로 왜곡된다 -- 제외하되 제외 건수를 함께 낸다(설계 §2.5·§2.6: 백필이
    # 없으므로 도입 직후 화면은 "집계 0건 · 제외 N건"이 정상이고, 그 수가 보여야
    # 공백이 숨지 않는다).
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", sched=5)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", sched=45)
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z")            # NULL
    _seed_job(db, repos, created_at="2026-07-01T00:00:00Z", sched=999) # 창 밖
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert sorted(stats["sched_wait_seconds"]) == [5, 45]
    assert stats["sched_wait_excluded"] == 1


def test_job_stats_exec_runtime_derived_from_anchor_pair(db, repos):
    # 실행시간(슬라이스 31, 방법 A: 파생 계산 -- 스키마 무변경):
    #   epoch(updated_at) - epoch(exec_submitted_at) - sched_wait_seconds.
    # 종단 & 두 재료 NOT NULL 인 행만 표본이다. 비종단(Pending)은 "아직 실행 중"
    # 이지 표본도 제외 건수도 아니다 -- duration_seconds 의 종단 한정과 같은 규칙.
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z",
              exec_at="2026-08-09T01:00:20Z", sched=5,
              updated_at="2026-08-09T01:01:25Z")          # 65 - 5 = 60
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", state="Pending",
              exec_at="2026-08-09T02:00:10Z", sched=3)    # 비종단 -- 표본 아님
    _seed_job(db, repos, created_at="2026-07-01T00:00:00Z",
              exec_at="2026-07-01T00:00:01Z", sched=0)    # 창 밖
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["exec_runtime_seconds"] == [60.0]
    assert stats["exec_runtime_excluded"] == 0


def test_job_stats_exec_runtime_excludes_missing_anchor_or_sched(db, repos):
    # 제외(정직 카운트) = 종단인데 재료가 없는 잡: 슬라이스 20 이전 잡(앵커 NULL),
    # 실행 미도달(preflight/preview 실패 -- 앵커 NULL), 첫 RUNNING 관측 전에 끝난
    # 한 틱 완료 잡(sched NULL). sched_wait 캡션의 「집계 N건 · 제외 M건」 관례.
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z",
              exec_at="2026-08-09T01:00:10Z", sched=2,
              updated_at="2026-08-09T01:00:30Z")          # 표본: 20 - 2 = 18
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", state="Failed",
              reason_code="preflight_failed")             # 앵커·sched 둘 다 NULL
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z",
              exec_at="2026-08-09T03:00:10Z")             # 한 틱 완료: sched NULL
    _seed_job(db, repos, created_at="2026-08-09T04:00:00Z", sched=1)  # 앵커만 NULL
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["exec_runtime_seconds"] == [18.0]
    assert stats["exec_runtime_excluded"] == 3


def test_job_stats_exec_runtime_zero_is_a_value_and_negative_folds_to_zero(db, repos):
    # 0 = 첫 RUNNING 관측 틱에 종단까지 간 잡(정상값 -- 표본에 남는다). 음수
    # (시계 스큐)는 record_sched_wait 규칙 그대로 0 으로 접는다 -- 1초 해상도
    # 세계에서 0 이 정직하고, 행을 버리면 표본이 조용히 준다.
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z",
              exec_at="2026-08-09T01:00:10Z", sched=0,
              updated_at="2026-08-09T01:00:10Z")          # 정확히 0
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z",
              exec_at="2026-08-09T02:00:10Z", sched=30,
              updated_at="2026-08-09T02:00:20Z")          # 10 - 30 = -20 -> 0
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["exec_runtime_seconds"] == [0.0, 0.0]
    assert stats["exec_runtime_excluded"] == 0


def test_job_stats_exec_runtime_drops_only_the_corrupt_timestamp_row(db, repos):
    # 시각 파싱 실패(iso_epoch ValueError)는 지어내지 않고 그 행만 버린다 --
    # duration_seconds 의 fail-soft 와 같은 규칙(NULL 이 아니라 제외 카운트에도
    # 안 잡힌다: 손상은 결측이 아니라 오염이다).
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z",
              exec_at="broken-timestamp", sched=1,
              updated_at="2026-08-09T01:00:30Z")
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z",
              exec_at="2026-08-09T02:00:10Z", sched=2,
              updated_at="2026-08-09T02:00:15Z")          # 5 - 2 = 3
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["exec_runtime_seconds"] == [3.0]
    assert stats["exec_runtime_excluded"] == 0


def test_job_stats_plan_rejected_counts_and_reasons_by_completed_at(db, repos):
    # 계획 거부는 data_jobs 행이 생기기 전의 종단이라 위의 어떤 data_jobs 집계에도
    # 안 잡힌다 -- results(terminal_state='Rejected')에서 세고, 창은 completed_at
    # (거부가 일어난 시각) 기준이다.
    _seed_rejected(db, repos, completed_at="2026-08-09T01:00:00Z",
                   reason="ldap_identity_not_found")
    _seed_rejected(db, repos, completed_at="2026-08-09T02:00:00Z",
                   reason="ldap_identity_not_found")
    _seed_rejected(db, repos, completed_at="2026-08-09T03:00:00Z",
                   reason="storage_missing")
    _seed_rejected(db, repos, completed_at="2026-07-01T00:00:00Z",
                   reason="storage_missing")                       # 창 밖
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["plan_rejected"] == 3
    assert stats["plan_rejection_reasons"] == [
        {"reason_code": "ldap_identity_not_found", "count": 2},
        {"reason_code": "storage_missing", "count": 1}]


def test_job_stats_plan_rejected_separate_from_failure_reasons(db, repos):
    # 잡 실패(data_jobs.reason_code)와 계획 거부(results.reason_code)를 한 필드로
    # 섞으면 「실패 사유」 라벨이 거짓말이 된다 -- 두 집계는 분리된 필드다.
    # Conflict 종단(results 행 있음)은 Rejected 가 아니므로 세지 않는다.
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", state="Failed",
              reason_code="execution_failed")
    _seed_rejected(db, repos, completed_at="2026-08-09T02:00:00Z",
                   reason="ldap_identity_not_found")
    _seed_rejected(db, repos, completed_at="2026-08-09T03:00:00Z",
                   reason="resource_conflict", state=RequestState.CONFLICT)
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["plan_rejected"] == 1
    assert stats["plan_rejection_reasons"] == [
        {"reason_code": "ldap_identity_not_found", "count": 1}]
    assert stats["failure_reasons"] == [
        {"reason_code": "execution_failed", "count": 1}]


def test_job_stats_plan_rejected_zero_is_zero_not_none(db, repos):
    # 거부 0건은 정상값 0 이다 -- null(모름)로 뭉개면 화면이 "값 없음"으로
    # 거짓말한다(null≠0).
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert stats["plan_rejected"] == 0
    assert stats["plan_rejection_reasons"] == []


def test_job_stats_sched_wait_zero_is_counted_not_excluded(db, repos):
    # 0 = 같은 틱 안에 스케줄된 가장 건강한 잡(설계 §2.4). 집계 술어가
    # IS NOT NULL / IS NULL 대신 falsy 검사(> 0, COALESCE(...,0) <> 0)로 퇴행하면
    # 여기서 잡힌다 -- submit_wait 의 동명 테스트와 같은 계약.
    _seed_job(db, repos, created_at="2026-08-09T01:00:00Z", sched=0)
    _seed_job(db, repos, created_at="2026-08-09T02:00:00Z", sched=7)
    _seed_job(db, repos, created_at="2026-08-09T03:00:00Z")            # NULL
    stats = repos.metrics.job_stats(start="2026-08-09T00:00:00Z",
                                    end="2026-08-09T23:59:59Z")
    assert sorted(stats["sched_wait_seconds"]) == [0, 7]   # 0 이 원자료에 남는다
    assert stats["sched_wait_excluded"] == 1               # NULL 만 제외 -- 0 은 아니다
