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

# ---- 큐 현황 파이프라인(2026-10-16, 사용자 요청 「대시보드에서 큐 현황에 대해 조금만 더 자세히 그리고 개선해서 보여주면
# 좋겠어.」) -- 아래 queue_* 읽기 셋. 시간 창이 없는 라이브 스냅숏이다: 24h created_at 창이면 하루 넘게 확인을 기다린 잡이
# 화면에서 사라진다(실측 ConfirmPending p95 7.3h -- 긴 대기일수록 창 밖으로 먼저 빠진다).
# 비종단 잡 상태(정렬 고정 -- 플레이스홀더 이름이 실행마다 흔들리지 않게).
_LIVE_JOB_STATES = tuple(sorted(
    s.value for s in DataJobState if s not in TERMINAL_DATA_JOB_STATES))
# Volcano 에 제출됐거나 제출될 단계 -- PodGroup 대조와 「실행 중」 단계의 재료. Executing 의 앞부분(확인 직후 실행 전
# 재점검 exec_preflight 파드 -- vcjob 이 아직 없다)은 queue_pipeline 이 phase_refs 로 갈라 사전 점검으로 센다.
_CONFIRM = DataJobState.CONFIRM_PENDING.value
_SUBMITTED_JOB_STATES = tuple(sorted((
    DataJobState.PREVIEW_RUNNING.value, DataJobState.EXECUTING.value, DataJobState.RUNNING.value)))
# 배치 오케스트레이터가 굴리는(또는 기록하러 도는) 배치 상태 = batch_orchestrator._BATCH_DRIVEN 의 거울. 취소·완료된 배치에
# 남은 Queued 항목은 아무도 집지 않으므로 「기다리는」 것이 아니다 -- 세지 않는다.
_DRIVEN_BATCH_STATES = ("PreviewReady", "Previewing", "Running")
# 계획 유예·오류 이벤트(planner._record_defer_event · run_once 의 plan_error) -- 요청이 Pending 에 머무는 이유를 남기는 유일한
# 흔적이다. 고르는 것은 요청마다 가장 최근 하나(MAX(id)).
_PLANNING_NOTE_EVENTS = ("awaiting_requested_nodes", "identity_propagating", "plan_error")
# 「지금 상태에 들어온 시각」. updated_at 은 상태 밖의 쓰기(set_phase_ref·LDAP 보류 touch·배치 확인 도장)로도 움직여 대기
# 시작점이 될 수 없다 -- 그 상태로의 마지막 전이 시각이 정답이다(Executing 의 실행 제출 자기 전이도 그대로 따른다:
# 「실행 중」의 나이는 실행 vcjob 제출부터다). 전이 행이 없는 행(직접 INSERT·옛 데이터)만 updated_at 으로 접는다.
# idx_transitions_entity (entity_kind, entity_id, id) 가 잡마다의 탐색을 인덱스 범위로 만든다.
_ENTERED_AT = """COALESCE((SELECT MAX(t.at) FROM state_transitions t
                           WHERE t.entity_kind = 'data_job' AND t.entity_id = j.job_id
                             AND t.to_state = j.state), j.updated_at)"""
# 제출된 잡 행의 안전 상한(PodGroup 대조용 전량 조회). 동시에 Volcano 에 올라 있는 잡 수는 클러스터 용량에 묶여 이
# 값보다 훨씬 작다 -- 넘으면 가장 오래된 것부터 담아 「실행 중」의 가장 긴 나이는 정확히 유지된다.
_SUBMITTED_ROWS_MAX = 2000


def _in(prefix, values):
    """IN (...) 자리표시자와 파라미터. 두 방언 공통(ANY(:list)는 PG 전용)."""
    ph = ", ".join(f":{prefix}{i}" for i in range(len(values)))
    return ph, {f"{prefix}{i}": v for i, v in enumerate(values)}


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

        # 실행시간(슬라이스 31, 방법 A: 스키마 무변경 파생 계산):
        #   epoch(updated_at) - epoch(exec_submitted_at) - sched_wait_seconds
        # = 첫 RUNNING 관측 -> 종단의 근사(오차는 스테퍼 틱 ±2회 -- sched_wait 의
        # "근사" 규약과 동일). duration 과 같은 이유로 앱측에서 뺀다(시각 차의
        # SQL 이식성 없음). 종단 & 두 재료 NOT NULL 인 행만 표본이다 -- 비종단은
        # "아직 실행 중"이지 표본도 제외도 아니다(duration 의 종단 한정과 같은 규칙).
        exec_rows = self._db.query(
            f"""SELECT updated_at, exec_submitted_at, sched_wait_seconds
                FROM data_jobs
                WHERE created_at BETWEEN :s AND :e AND state IN ({term_ph})
                  AND exec_submitted_at IS NOT NULL
                  AND sched_wait_seconds IS NOT NULL
                ORDER BY created_at ASC, job_id ASC""",
            {**params, **term_params})
        exec_runtime_seconds = []
        for row in exec_rows:
            try:
                delta = (_epoch(row["updated_at"]) - _epoch(row["exec_submitted_at"])
                         - row["sched_wait_seconds"])
            except (TypeError, ValueError):
                continue    # 시각이 깨진 행은 지어내지 않고 그 행만 버린다(duration 규칙)
            # 음수(시계 스큐)는 record_sched_wait 규칙 그대로 0 으로 접는다 --
            # 행을 버리면 표본이 조용히 줄고, 1초 해상도 세계에서 0 이 정직하다.
            exec_runtime_seconds.append(max(0.0, delta))
        # 제외(정직 카운트) = 종단인데 재료가 없는 잡: 슬라이스 20 이전 잡(앵커
        # NULL)·실행 미도달(preflight/preview 실패)·한 틱 완료(sched NULL).
        # 이 수를 내야 화면이 "집계 N건 · 제외 M건"으로 공백을 숨기지 못한다.
        exec_excluded = self._db.query_one(
            f"""SELECT COUNT(*) AS c FROM data_jobs
                WHERE created_at BETWEEN :s AND :e AND state IN ({term_ph})
                  AND (exec_submitted_at IS NULL OR sched_wait_seconds IS NULL)""",
            {**params, **term_params})

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

        # 스케줄 대기(슬라이스 20 설계 §2.4): submit_wait 페어와 같은 모양의
        # 2쿼리. 술어는 IS NOT NULL / IS NULL 이다 -- COALESCE(...,0) = 0 같은
        # falsy 검사로 쓰면 0(같은 틱 스케줄이라는 정상값)이 미기록으로 새 나간다.
        # 둘 다 idx_data_jobs_created_sched (created_at, sched_wait_seconds) 가
        # 커버한다 -- 테이블을 건드리지 않는 인덱스 온리 레인지 스캔.
        # excluded 에는 과거 잡(백필 없음 -- 설계 §2.5)·Running 미도달·한 틱 완료·
        # 스텁 백엔드가 모두 들어간다 -- 이 수를 내야 화면이 공백을 숨기지 못한다.
        sched_waits = self._db.query(
            """SELECT sched_wait_seconds AS w FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
                 AND sched_wait_seconds IS NOT NULL""", params)
        sched_excluded = self._db.query_one(
            """SELECT COUNT(*) AS c FROM data_jobs
               WHERE created_at BETWEEN :s AND :e
                 AND sched_wait_seconds IS NULL""", params)

        # scan 제외(2026-08-23): scan 의 bytes_count 는 이제 "관측한 트리의 총
        # 용량"(실 사용량, 사용량 분석의 원천)이라 이동·삭제 처리량이 아니다 --
        # 100 TiB 트리를 스캔만 해도 「처리 바이트」가 +100 TiB 되는 오염을 여기서
        # 막는다(반복 스캔은 사용량 분석이 유도하는 정상 사용이다). files_count 도
        # 같은 이유로 함께 제외한다 -- 지표 라벨(처리 항목/바이트)의 의미는 "잡이
        # 만진 것"이고, scan 은 아무것도 만지지 않는다.
        sums = self._db.query_one(
            """SELECT SUM(files_count) AS files_total, SUM(bytes_count) AS bytes_total
               FROM data_jobs
               WHERE created_at BETWEEN :s AND :e AND state = 'Succeeded'
                 AND operation != 'scan'""", params)

        # 계획 거부: 계획 단계 거부는 data_jobs 행이 생기기 **전**의 종단이라 위의
        # 어떤 data_jobs 집계(by_state·KPI 전부)에도 잡히지 않는다 -- 요청 종단이
        # 남는 곳은 results 다(planner 의 set_state_with_result). 창은 completed_at
        # (거부가 일어난 시각) 기준 -- 이 테이블에 created_at 은 없다. failure_reasons
        # (data_jobs.reason_code, 잡 실패)와 **분리된 필드**다: 섞으면 「실패 사유」
        # 라벨이 거짓말이 된다. 잡을 거쳐 Rejected 로 종단한 요청(preview_expired
        # 계열의 REJECTED/PREVIEW_EXPIRED -> Rejected 매핑)도 이 수에 포함된다 --
        # 정확히는 "Rejected 로 종단한 요청 수"이고 계획 거부가 지배적이다.
        plan_rejected = self._db.query_one(
            """SELECT COUNT(*) AS c FROM results
               WHERE terminal_state = 'Rejected'
                 AND completed_at BETWEEN :s AND :e""", params)
        # 사유 상위는 failure_reasons 와 같은 모양(IS NOT NULL·상위 10) -- 사유 없는
        # 거부는 표에서 빠지지만 총수(plan_rejected)에는 남는다(뭉개지 않는다).
        plan_rejection_reasons = self._db.query(
            """SELECT reason_code, COUNT(*) AS cnt FROM results
               WHERE terminal_state = 'Rejected'
                 AND completed_at BETWEEN :s AND :e
                 AND reason_code IS NOT NULL
               GROUP BY reason_code ORDER BY cnt DESC, reason_code ASC LIMIT 10""",
            params)

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
            "plan_rejected": plan_rejected["c"],
            "plan_rejection_reasons": [
                {"reason_code": r["reason_code"], "count": r["cnt"]}
                for r in plan_rejection_reasons],
            "throughput": [{"bucket": r["bucket"], "count": r["cnt"]}
                           for r in throughput],
            "duration_seconds": duration_seconds,
            "exec_runtime_seconds": exec_runtime_seconds,
            "exec_runtime_excluded": exec_excluded["c"],
            "submit_wait_seconds": [row["w"] for row in waits],
            "submit_wait_excluded": excluded["c"],
            "sched_wait_seconds": [row["w"] for row in sched_waits],
            "sched_wait_excluded": sched_excluded["c"],
            "files_total": sums["files_total"],
            "bytes_total": sums["bytes_total"],
        }

    # ---- 큐 현황 파이프라인(2026-10-16) -- 라우트(metrics_queue)가 축마다 독립 try 로 부른다. 조립은
    # queue_pipeline(순수)이 한다: 여기는 행과 집계만 낸다. 카운트는 COUNT(*) 정수 그대로(0 은 정상값), 시각은
    # 원문 ISO -- 나이 계산은 서버 한 곳(조립 단계)에서 한다.

    def queue_planning(self, *, limit: int) -> dict:
        """계획 대기(requests Pending). rows 는 오래된 순 limit 건(+ 각 요청의 가장 최근 계획 유예·오류 이벤트)."""
        agg = self._db.query_one(
            "SELECT COUNT(*) AS n, MIN(created_at) AS oldest_at FROM requests WHERE state = 'Pending'")
        # commit_order 순 = 접수 순이다(idx_requests_state (state, commit_order) 가 범위 + 정렬을 함께 덮는다).
        rows = self._db.query(
            """SELECT request_id, operation, requester_id, priority, batch_id, created_at
               FROM requests WHERE state = 'Pending' ORDER BY commit_order LIMIT :n""", {"n": limit})
        eph, eparams = _in("e", _PLANNING_NOTE_EVENTS)
        for r in rows:
            # 요청마다 가장 최근 하나 -- 요청별 ORDER BY id DESC LIMIT 1 이라 idx_events_request (request_id, id) 를
            # 역방향으로 타고 첫 일치에서 멈춘다. plan_error 는 틱마다(10s) 억제 없이 쌓인다: 예전의 MAX(id) GROUP BY 는
            # 막힌 요청의 이벤트를 전부 읽었다(검증 실측 PG 16: 막힌 요청 20건 x 1주 ≈ 100ms, 5초 폴링마다 -- 이 방식은
            # 20건 합계 ~1ms).
            ev = self._db.query_one(
                f"""SELECT event_type, message FROM events
                    WHERE request_id = :r AND event_type IN ({eph}) ORDER BY id DESC LIMIT 1""",
                {"r": r["request_id"], **eparams})
            r["note"] = {"event_type": ev["event_type"], "message": ev["message"]} if ev else None
        return {"count": int(agg["n"]), "oldest_at": agg["oldest_at"], "rows": rows}

    def queue_jobs(self, *, limit: int) -> dict:
        """비종단 잡의 상태별 집계 + 대기 단계(Pending·Preflight·ConfirmPending 단건)의 오래된 순 limit 건 + 배치 자식
        ConfirmPending 의 배치별 묶음 + Volcano 에 제출된 잡 전량(phase_refs -- PodGroup 대조용).

        배치 자식의 확인 대기는 배치 단위로 묶는다: sync 배치 하나가 자식 수백 건을 한꺼번에 ConfirmPending 으로 만들므로
        건별로 늘어놓으면 표가 한 배치로 덮여 사람이 확인해야 할 단건 sync 가 묻힌다 -- 배치 확인도 원래 배치 단위다.

        비용: 대기 시작점(_ENTERED_AT)은 잡마다 전이 인덱스 탐색 한 번이다(PG 16 실측 행당 ~4µs). 확인 대기는 사람이
        누를 때까지 쌓이는 유일한 상태라 그 집계는 배치별 묶음 한 번으로 끝낸다(묶음의 NULL 배치 = 단건) -- 상태별
        집계에 다시 넣어 같은 행을 두 번 계산하지 않는다. 확인 대기 3천 + 실행 300 에서 이 메서드 전체 ~30ms(PG 16)."""
        sph, sparams = _in("s", tuple(s for s in _LIVE_JOB_STATES if s != _CONFIRM))
        by_state = {}
        for r in self._db.query(
                f"""SELECT state, COUNT(*) AS n, MIN(entered_at) AS oldest_at
                    FROM (SELECT j.state, {_ENTERED_AT} AS entered_at
                          FROM data_jobs j WHERE j.state IN ({sph})) x
                    GROUP BY state""", sparams):
            by_state[r["state"]] = {"count": int(r["n"]), "oldest_at": r["oldest_at"]}
        # LEFT JOIN requests: 요청 행이 없는 잡(있어선 안 되지만 FK 가 없다)도 세고 목록에 남긴다 -- 요청자만 모름이 된다.
        groups = self._db.query(
            f"""SELECT x.batch_id, b.name, b.status, b.operation, b.requester_id, b.priority, b.max_concurrency,
                       COUNT(*) AS n, MIN(x.entered_at) AS oldest_at, MIN(x.preview_expires_at) AS soonest_expiry
                FROM (SELECT r.batch_id, j.preview_expires_at, {_ENTERED_AT} AS entered_at
                      FROM data_jobs j LEFT JOIN requests r ON r.request_id = j.request_id
                      WHERE j.state = '{_CONFIRM}') x
                LEFT JOIN batches b ON b.batch_id = x.batch_id
                GROUP BY x.batch_id, b.name, b.status, b.operation, b.requester_id, b.priority,
                         b.max_concurrency""")
        for g in groups:
            g["n"] = int(g["n"])
        if groups:
            # 시각은 고정 포맷 ISO-8601 UTC 라 문자열 최솟값 = 가장 이른 시각(SQL MIN 과 같은 비교).
            olds = [g["oldest_at"] for g in groups if g["oldest_at"] is not None]
            exps = [g["soonest_expiry"] for g in groups if g["soonest_expiry"] is not None]
            by_state[_CONFIRM] = {"count": sum(g["n"] for g in groups),
                                  "oldest_at": min(olds) if olds else None,
                                  "soonest_expiry": min(exps) if exps else None}
        cols = f"""j.job_id, j.request_id, j.operation, j.priority, j.state, j.preview_expires_at,
                   r.requester_id, r.batch_id, {_ENTERED_AT} AS entered_at"""
        waiting = {}
        singles = sum(g["n"] for g in groups if g["batch_id"] is None)
        for state, extra, count in (
                ("Pending", "", (by_state.get("Pending") or {}).get("count", 0)),
                ("Preflight", "", (by_state.get("Preflight") or {}).get("count", 0)),
                (_CONFIRM, " AND r.batch_id IS NULL", singles)):
            # 0 건이면 묻지 않는다(5초 폴링의 왕복 절약) -- 빈 목록은 집계가 이미 증명했다.
            waiting[state] = [] if count == 0 else self._db.query(
                f"""SELECT {cols} FROM data_jobs j LEFT JOIN requests r ON r.request_id = j.request_id
                    WHERE j.state = :st{extra} ORDER BY entered_at, j.job_id LIMIT :n""",
                {"st": state, "n": limit})
        bph, bparams = _in("b", _SUBMITTED_JOB_STATES)
        submitted = self._db.query(
            f"""SELECT {cols}, j.phase_refs FROM data_jobs j LEFT JOIN requests r ON r.request_id = j.request_id
                WHERE j.state IN ({bph}) ORDER BY entered_at, j.job_id LIMIT :n""",
            {**bparams, "n": _SUBMITTED_ROWS_MAX})
        for row in submitted:
            # DB 가 신뢰 경계다(CLAUDE.md) -- phase_refs 도 변조·손상될 수 있다. 깨진 JSON 은 그 행의 대조만 포기한다.
            try:
                row["phase_refs"] = load_json(row["phase_refs"])
            except (TypeError, ValueError):
                row["phase_refs"] = None
        return {"by_state": by_state, "waiting": waiting,
                "confirm_singles": next((g for g in groups if g["batch_id"] is None), None),
                "confirm_batches": [g for g in groups if g["batch_id"] is not None],
                "submitted": submitted}

    def queue_batches(self) -> dict:
        """배치 대기: 굴러가는 배치(_DRIVEN_BATCH_STATES)마다 아직 자식 요청이 안 된(Queued) 항목 수와 가장 오래 기다린
        항목의 시각 + 확인 대기(PreviewReady) 배치 수. 항목의 updated_at 이 대기 시작점이다 -- Queued 로 다시 들어오는
        재실행이 updated_at 을 새로 찍는다(생성 시각을 쓰면 재실행 항목이 며칠을 기다린 것처럼 보인다). Queued 상태의
        항목 편집도 찍으므로 그만큼은 짧게 보인다(과대가 아니라 과소 쪽 오차)."""
        dph, dparams = _in("d", _DRIVEN_BATCH_STATES)
        groups = self._db.query(
            f"""SELECT b.batch_id, b.name, b.status, b.operation, b.requester_id, b.priority, b.max_concurrency,
                       COUNT(*) AS queued, MIN(i.updated_at) AS oldest_at
                FROM batches b JOIN batch_items i ON i.batch_id = b.batch_id
                WHERE b.status IN ({dph}) AND i.status = 'Queued'
                GROUP BY b.batch_id, b.name, b.status, b.operation, b.requester_id, b.priority,
                         b.max_concurrency""", dparams)
        for g in groups:
            g["queued"] = int(g["queued"])
        ready = self._db.query_one("SELECT COUNT(*) AS n FROM batches WHERE status = 'PreviewReady'")
        return {"groups": groups, "preview_ready": int(ready["n"])}
