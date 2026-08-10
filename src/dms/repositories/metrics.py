"""대시보드 집계 저장소(읽기 전용). 두 데이터원을 다룬다(설계 §2.2):
agent_reports(JSON blob 시계열 -- 앱측 파싱)와 data_jobs(typed 컬럼 -- SQL GROUP BY).
blob은 dual-dialect(SQLite/PostgreSQL)라 json_extract에 기댈 수 없어 여기서 파싱하고,
GROUP BY는 typed 컬럼에만 건다."""
from ..db import Database, load_json
# metrics_series 와 같은 승격(슬라이스 17) -- 별칭 유지로 호출부 무변경.
from ..db import iso_epoch as _epoch
from ..domain import DataJobState, TERMINAL_DATA_JOB_STATES

# 실패로 세는 종단 상태 = 종단 전체 - Succeeded. sorted로 고정해 플레이스홀더
# 순서(파라미터 이름)가 실행마다 흔들리지 않게 한다.
_FAILED_STATES = tuple(sorted(
    s.value for s in TERMINAL_DATA_JOB_STATES if s is not DataJobState.SUCCEEDED))
_TERMINAL_STATES = tuple(sorted(s.value for s in TERMINAL_DATA_JOB_STATES))


class MetricsRepository:
    def __init__(self, db: Database):
        self._db = db

    def node_series(self, node_name: str, *, start: str, end: str) -> list[dict]:
        """[{"reported_at", "report"}] 시간 오름차순. idx_agent_reports_node
        (node_name, reported_at)가 커버한다. 같은 reported_at이 두 행일 수 있어
        id를 tiebreak으로 둔다(정렬 결정성). JSON이 깨진 행은 그 행만 버린다
        (fail-soft, 설계 §3) -- 한 행이 시리즈 전체를 죽이면 안 된다."""
        rows = self._db.query(
            """SELECT report, reported_at FROM agent_reports
               WHERE node_name = :n AND reported_at BETWEEN :s AND :e
               ORDER BY reported_at ASC, id ASC""",
            {"n": node_name, "s": start, "e": end})
        out = []
        for row in rows:
            try:
                report = load_json(row["report"])
            except ValueError:
                continue
            out.append({"reported_at": row["reported_at"], "report": report})
        return out

    def job_stats(self, *, start: str, end: str, bucket_chars: int = 13) -> dict:
        params = {"s": start, "e": end}
        fail_ph = ", ".join(f":f{i}" for i in range(len(_FAILED_STATES)))
        fail_params = {f"f{i}": v for i, v in enumerate(_FAILED_STATES)}

        def agg(prefix=""):
            # SUM(CASE ...)는 두 방언 공통 -- FILTER (WHERE ...)는 PG 전용이라 안 쓴다.
            return (f"COUNT(*) AS cnt, "
                    f"SUM(CASE WHEN {prefix}state = 'Succeeded' THEN 1 ELSE 0 END) AS ok, "
                    f"SUM(CASE WHEN {prefix}state IN ({fail_ph}) THEN 1 ELSE 0 END) AS bad")

        by_state = self._db.query(
            """SELECT state, COUNT(*) AS cnt FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
               GROUP BY state ORDER BY state""", params)

        by_tool = self._db.query(
            f"""SELECT COALESCE(tool, '') AS k, {agg()} FROM data_jobs
                WHERE created_at BETWEEN :s AND :e
                GROUP BY COALESCE(tool, '') ORDER BY cnt DESC, k ASC""",
            {**params, **fail_params})

        # sync 잡은 storage_name이 NULL이고 도착지가 destination_storage에 있다 --
        # "그 스토리지에서 일어난 일"의 대표로 도착지를 쓴다. NULL 정렬은 방언마다
        # 달라(SQLite ASC는 NULL 먼저, PG는 나중) ''로 접고 앱측에서 None으로 되돌린다.
        by_storage = self._db.query(
            f"""SELECT COALESCE(storage_name, destination_storage, '') AS k, {agg()}
                FROM data_jobs WHERE created_at BETWEEN :s AND :e
                GROUP BY COALESCE(storage_name, destination_storage, '')
                ORDER BY cnt DESC, k ASC""",
            {**params, **fail_params})

        by_requester = self._db.query(
            f"""SELECT r.requester_id AS k, {agg('d.')}
                FROM data_jobs d JOIN requests r ON r.request_id = d.request_id
                WHERE d.created_at BETWEEN :s AND :e
                GROUP BY r.requester_id ORDER BY cnt DESC, k ASC""",
            {**params, **fail_params})

        failure_reasons = self._db.query(
            f"""SELECT reason_code, COUNT(*) AS cnt FROM data_jobs
                WHERE created_at BETWEEN :s AND :e AND reason_code IS NOT NULL
                  AND state IN ({fail_ph})
                GROUP BY reason_code ORDER BY cnt DESC, reason_code ASC LIMIT 10""",
            {**params, **fail_params})

        # ISO-8601 UTC 고정 포맷이라 SUBSTR 접두가 곧 시간 버킷이다(13자=시간, 10자=일)
        throughput = self._db.query(
            """SELECT SUBSTR(created_at, 1, :c) AS bucket, COUNT(*) AS cnt
               FROM data_jobs WHERE created_at BETWEEN :s AND :e
               GROUP BY SUBSTR(created_at, 1, :c) ORDER BY bucket ASC""",
            {**params, "c": bucket_chars})

        # 수행시간은 문자열 시각의 차라 SQL로는 이식성 있게 못 뺀다(julianday는
        # SQLite 전용, EXTRACT(EPOCH)는 PG 전용) -- 행을 가져와 앱측에서 계산한다.
        # 종단 잡만: 비종단의 updated_at은 "지금까지"일 뿐 수행시간이 아니다.
        term_ph = ", ".join(f":t{i}" for i in range(len(_TERMINAL_STATES)))
        term_params = {f"t{i}": v for i, v in enumerate(_TERMINAL_STATES)}
        rows = self._db.query(
            f"""SELECT created_at, updated_at FROM data_jobs
                WHERE created_at BETWEEN :s AND :e AND state IN ({term_ph})
                ORDER BY created_at ASC, job_id ASC""",
            {**params, **term_params})
        duration_seconds = []
        for row in rows:
            try:
                delta = _epoch(row["updated_at"]) - _epoch(row["created_at"])
            except (TypeError, ValueError):
                continue                     # 시각이 깨진 행은 그 행만 버린다
            if delta >= 0:
                duration_seconds.append(delta)

        # 제출 대기(슬라이스 17 설계 §2.3): NULL(백필 불가분·아직 Pending)은 집계에서
        # 제외하고 제외 건수를 함께 낸다 -- 백필 공백을 화면에서 숨기지 않는다(설계
        # §3). 술어는 IS NOT NULL / IS NULL 이다: COALESCE(...,0) = 0 같은 falsy
        # 검사로 쓰면 0(같은 초 픽업이라는 정상값)이 미기록으로 새 나간다.
        # 두 쿼리 모두 idx_data_jobs_created (created_at, submit_wait_seconds)
        # 가 커버한다 -- 테이블을 건드리지 않는 인덱스 온리 레인지 스캔이라,
        # 슬라이스 14 가 (B) 를 금지했던 근거(전기간 풀스캔)가 성립하지 않는다.
        waits = self._db.query(
            """SELECT submit_wait_seconds AS w FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
                 AND submit_wait_seconds IS NOT NULL""", params)
        excluded = self._db.query_one(
            """SELECT COUNT(*) AS c FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
                 AND submit_wait_seconds IS NULL""", params)

        sums = self._db.query_one(
            """SELECT SUM(files_count) AS files_total, SUM(bytes_count) AS bytes_total
               FROM data_jobs
               WHERE created_at BETWEEN :s AND :e AND state = 'Succeeded'""", params)

        def fold(rows_, key_name):
            # COALESCE로 접은 ''를 None으로 되돌리고 내부 별칭을 응답 이름으로 바꾼다
            return [{key_name: (r["k"] or None), "count": r["cnt"],
                     "succeeded": r["ok"], "failed": r["bad"]} for r in rows_]

        return {
            "by_state": [{"state": r["state"], "count": r["cnt"]} for r in by_state],
            "by_tool": fold(by_tool, "tool"),
            "by_storage": fold(by_storage, "storage"),
            "by_requester": [{"requester_id": r["k"], "count": r["cnt"],
                              "succeeded": r["ok"], "failed": r["bad"]}
                             for r in by_requester],
            "failure_reasons": [{"reason_code": r["reason_code"], "count": r["cnt"]}
                                for r in failure_reasons],
            "throughput": [{"bucket": r["bucket"], "count": r["cnt"]}
                           for r in throughput],
            "duration_seconds": duration_seconds,
            "submit_wait_seconds": [row["w"] for row in waits],
            "submit_wait_excluded": excluded["c"],
            "files_total": sums["files_total"],
            "bytes_total": sums["bytes_total"],
        }
