"""슬라이스 25 §2.2: 진단 로그 박제의 저장 계약.

파드 로그의 유일 사본은 시한부다(pod GC·vcjob TTL 86400) -- 실패 종단 시점에
DB 로 박제하되, ① write-once(재시도 멱등의 근거), ② 다행 조회는 이 컬럼(최대
64KB/행)을 절대 싣지 않는다(builds I2: 5초 폴링 x 50행 x 64KB = 3.2MB 왕복 사고의
재발 방지) -- 이 두 계약을 이 파일이 고정한다."""
import json

from dms.domain import DataJobState
from dms.repositories import Repositories


def _job(repos, *, key, state=None):
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key=key, payload={"storage": "s1", "target": "a"}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan", worker_pool={},
        precondition={}, actor="planner")
    if state is not None:
        repos.data_jobs.set_job_state(jid, state, actor="test")
    return rid, jid


_ENTRIES = [{"pod": "p-launcher-0", "log": "Traceback ...", "truncated": False}]


def test_archive_stores_phase_at_and_entries(db):
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k1")
    repos.data_jobs.archive_diag_logs(jid, phase="execution", entries=_ENTRIES)
    raw = repos.data_jobs.get_job(jid)["diag_logs"]
    doc = json.loads(raw)                      # get_job 은 원문 TEXT 를 준다(폴백이 직접 파싱)
    assert doc["phase"] == "execution"
    assert doc["entries"] == _ENTRIES
    assert doc["at"]                           # 박제 시각 -- "언제의 사본인지"가 남는다


def test_archive_stores_all_null_entries_honestly(db):
    # 전 항목 log=None 이어도 저장한다 -- "박제 시점에 이미 없었다"는 사실 자체가
    # 진단이다(설계 §2.2). 모름을 뭉개지 않는다.
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k-null")
    repos.data_jobs.archive_diag_logs(jid, phase="preflight",
        entries=[{"pod": "p1", "log": None, "truncated": False}])
    doc = json.loads(repos.data_jobs.get_job(jid)["diag_logs"])
    assert doc["entries"][0]["log"] is None


def test_archive_is_write_once(db):
    # 박제 후 크래시 -> 다음 틱 finalize 재시도(Task 4 의 순서 계약)가 두 번째
    # 박제를 시도한다 -- IS NULL 술어가 첫 사본을 지킨다(mark_exec_submitted 선례).
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k2")
    repos.data_jobs.archive_diag_logs(jid, phase="execution", entries=_ENTRIES)
    repos.data_jobs.archive_diag_logs(jid, phase="execution",
        entries=[{"pod": "attacker", "log": "overwrite", "truncated": False}])
    doc = json.loads(repos.data_jobs.get_job(jid)["diag_logs"])
    assert doc["entries"] == _ENTRIES          # 두 번째 호출은 무변경


def test_archive_does_not_touch_updated_at(db):
    # updated_at 은 클레임 순서(claim_steppable ORDER BY updated_at)와 GC 나이
    # (terminal_jobs_older_than)의 축이다. 박제가 시각을 앞으로 밀면 아직 진행
    # 중인 잡의 클레임 순서가 뒤로 밀리고 GC 가 늦어진다 -- mark_exec_submitted
    # 와 같은 근거로 이 UPDATE 는 updated_at 을 건드리지 않는다.
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k-touch")
    before = repos.data_jobs.get_job(jid)["updated_at"]
    repos.data_jobs.archive_diag_logs(jid, phase="execution", entries=_ENTRIES)
    assert repos.data_jobs.get_job(jid)["updated_at"] == before


def test_multi_row_queries_never_carry_diag_logs_but_get_job_does(db):
    # builds I2 의 그 문제: 큰 컬럼을 다행 조회에 얹으면 5초 폴링마다 최대
    # 50x64KB=3.2MB 가 왕복한다(설계 §1-10). 4곳 전부에서 부재를 고정한다.
    repos = Repositories(db)
    rid, jid = _job(repos, key="k3")                        # Pending -- claim 대상
    _rid2, jid2 = _job(repos, key="k4", state=DataJobState.SUCCEEDED)
    repos.data_jobs.archive_diag_logs(jid2, phase="execution", entries=_ENTRIES)
    assert "diag_logs" in repos.data_jobs.get_job(jid2)     # 단행(/logs 폴백)만 싣는다
    multi = {
        "list_jobs": repos.data_jobs.list_jobs(),
        "list_jobs(request)": repos.data_jobs.list_jobs(request_id=rid),
        "claim_steppable": repos.data_jobs.claim_steppable(),
        "succeeded_scans": repos.data_jobs.succeeded_scans("s1"),
        "terminal_older": repos.data_jobs.terminal_jobs_older_than(
            0, now_iso="2099-01-01T00:00:00Z"),
    }
    for name, rows in multi.items():
        assert rows, name                                    # 공허한 통과 방지 -- 행이 실제로 있다
        assert all("diag_logs" not in r for r in rows), name


def test_column_parity_pins_future_columns(db):
    # 미래에 컬럼을 추가하고 _ROW_COLUMNS_SANS_DIAG 갱신을 잊으면, 다행 조회만
    # 그 컬럼이 조용히 빠진다(SELECT * 시절엔 없던 사고 유형) -- get_job 과의
    # 차집합이 정확히 {diag_logs} 임을 계약으로 고정해 그 누락을 잡는다.
    repos = Repositories(db)
    _rid, jid = _job(repos, key="k5")
    job = repos.data_jobs.get_job(jid)
    row = repos.data_jobs.list_jobs()[0]
    assert set(job) - set(row) == {"diag_logs"}
