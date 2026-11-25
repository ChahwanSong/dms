"""에이전트 리포트 저장소: 이력(agent_reports) + 노드별 최신 1행(agent_nodes).

행은 보고가 올 때만 갈아 끼운다(ingest). 지우는 길은 둘이다: retention(prune_reports -- 이력만, 보존일 밖)과 노드 기록
삭제(forget_node, 2026-11-25 -- OA 사이트에서 호스트 이름을 바꾼 뒤 옛 이름이 「리포트 지연」으로 영원히 남던 문제). 기록 삭제의
규칙(누가·언제 지울 수 있나, in_use 판정, 자동 정리)은 dms.node_forget 모듈 docstring 이 진실이다 -- 여기는 한 트랜잭션의 몸통만
둔다."""
from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso


class NodeForgetRefused(Exception):
    """노드 기록 삭제 거절. code ∈ {"node_not_found", "node_fresh", "node_in_use"}, refs = in_use 근거 토큰
    (dms.node_forget.REF_KINDS 의 부분 집합, 그 순서), requests = active_jobs 근거의 요청 id 일부(판정과 같은 스냅숏 --
    node_forget.forget_node 가 채운다, 최대 node_forget.BLOCKING_REQUESTS_SHOWN). 라우트가 404/409 로, 자동 정리가 이벤트로
    옮긴다."""

    def __init__(self, code: str, refs=(), requests=()):
        super().__init__(code)
        self.code = code
        self.refs = tuple(refs)
        self.requests = tuple(requests)


class AgentsRepository:
    def __init__(self, db: Database):
        self._db = db

    def ingest(self, node_name: str, report: dict, reported_at: str | None = None) -> None:
        at = reported_at or utc_now_iso()
        payload = dump_json(report)
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO agent_reports (node_name, report, reported_at)
                   VALUES (:n, :r, :at)""",
                {"n": node_name, "r": payload, "at": at})
            self._db.execute("DELETE FROM agent_nodes WHERE node_name = :n",
                             {"n": node_name})
            self._db.execute(
                """INSERT INTO agent_nodes (node_name, report, reported_at)
                   VALUES (:n, :r, :at)""",
                {"n": node_name, "r": payload, "at": at})

    def list_nodes(self, *, stale_seconds: int, now_iso: str | None = None) -> list[dict]:
        now = now_iso or utc_now_iso()
        threshold = iso_plus(now, -stale_seconds)
        rows = self._db.query(
            "SELECT node_name, report, reported_at FROM agent_nodes ORDER BY node_name")
        return [{
            "node_name": row["node_name"],
            "reported_at": row["reported_at"],
            "fresh": row["reported_at"] > threshold,
            "report": load_json(row["report"]),
        } for row in rows]

    def fresh_reports(self, *, stale_seconds: int, now_iso: str | None = None) -> list[dict]:
        return [n for n in self.list_nodes(stale_seconds=stale_seconds, now_iso=now_iso)
                if n["fresh"]]

    def latest_reports(self, node_names) -> list[dict]:
        """노드별 최신 보고(agent_nodes, 신선도 무관) -- 노드 배치 제외의 k8s 스케줄 불가 판정(node_exclusions.
        blocked_nodes)이 후보 노드만 읽는다."""
        names = list(dict.fromkeys(node_names))
        if not names:
            return []
        params = {f"n{i}": n for i, n in enumerate(names)}
        rows = self._db.query(
            f"SELECT node_name, report, reported_at FROM agent_nodes WHERE node_name IN "
            f"({', '.join(':' + k for k in params)})", params)
        return [{"node_name": r["node_name"], "reported_at": r["reported_at"],
                 "report": load_json(r["report"])} for r in rows]

    def node_names_reported_before(self, before_iso: str) -> list[str]:
        """마지막 보고가 before_iso 이하인 노드 이름(이름순) -- 자동 정리 후보(node_forget.forget_absent_nodes_once). 보고
        JSON 을 읽지 않는 가벼운 조회다(retention 틱마다 부른다)."""
        rows = self._db.query(
            "SELECT node_name FROM agent_nodes WHERE reported_at <= :t ORDER BY node_name",
            {"t": before_iso})
        return [r["node_name"] for r in rows]

    def forget_node(self, node_name: str, *, stale_before_iso: str, actor: str, operation: str,
                    references) -> dict:
        """노드 기록 삭제(2026-11-25) -- agent_nodes 행 + node_exclusions 행(있으면) + 그 노드의 agent_reports 전부 + 감사
        1행을 **한 트랜잭션**으로. 거절은 NodeForgetRefused(바뀐 것 없음 -- 예외가 트랜잭션을 롤백한다).

        순서: 존재(없으면 node_not_found) → stale 인가(reported_at > stale_before_iso 면 node_fresh -- list_nodes 의
        fresh 와 같은 비교라 stale ⇔ <=) → references(node_name)(다른 저장소의 **읽기**를 같은 커넥션·트랜잭션에서 -- 비어
        있지 않으면 node_in_use) → **조건부 DELETE**(reported_at <= 임계). 판정과 삭제 사이에 그 노드가 다시 보고하면
        (ingest 의 DELETE+INSERT) 조건부 DELETE 가 0행이다 -- 다시 읽어 행이 없으면 node_not_found, 있으면 node_fresh.
        SELECT … FOR UPDATE 로 잠그지 않는 이유: PG READ COMMITTED 에서 잠근 옛 행이 동시 ingest 로 사라지면 그 행을
        건너뛰어 「없음」으로 잘못 답한다. 삭제 뒤에 오는 보고는 새 행으로 다시 생긴다(의도 -- 같은 이름이 살아 있으면
        다시 보인다).

        받아들이는 위험(node_forget 모듈 docstring 에도): (a) 이력은 노드당 최대 보존일×하루 보고 수(기본 30일×1440 ≈ 4.3만)
        행이라 한 트랜잭션이 수 초 걸릴 수 있다(API 단일 커넥션이 그동안 막힌다 -- readiness 문턱 안). (b) retention
        prune_reports(id 순 청크)와 같은 행을 반대 순서로 잠그면 드물게 PG 교착 -- PG 가 한쪽을 중단한다(API 쪽이면 500,
        다시 누르면 된다). (c) in_use 판정 뒤 같은 트랜잭션 안에서 다른 프로세스가 그 노드로 새 계획을 커밋할 창 --
        planner 는 fresh 노드만 쓰므로 stale 로 확정된 노드에선 사실상 없다."""
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT reported_at FROM agent_nodes WHERE node_name = :n", {"n": node_name})
            if row is None:
                raise NodeForgetRefused("node_not_found")
            if row["reported_at"] > stale_before_iso:
                raise NodeForgetRefused("node_fresh")
            refs = tuple(references(node_name))
            if refs:
                raise NodeForgetRefused("node_in_use", refs)
            deleted = self._db.execute_count(
                "DELETE FROM agent_nodes WHERE node_name = :n AND reported_at <= :t",
                {"n": node_name, "t": stale_before_iso})
            if deleted == 0:
                again = self._db.query_one(
                    "SELECT reported_at FROM agent_nodes WHERE node_name = :n", {"n": node_name})
                raise NodeForgetRefused("node_not_found" if again is None else "node_fresh")
            exclusion = self._db.query_one(
                "SELECT * FROM node_exclusions WHERE node_name = :n", {"n": node_name})
            if exclusion is not None:
                self._db.execute("DELETE FROM node_exclusions WHERE node_name = :n", {"n": node_name})
            reports_deleted = self._db.execute_count(
                "DELETE FROM agent_reports WHERE node_name = :n", {"n": node_name})
            # 보고 본문은 싣지 않는다(크다 -- 마운트·지표 전부). 무엇을 지웠는지만.
            before = {"reported_at": row["reported_at"],
                      "exclusion": dict(exclusion) if exclusion is not None else None,
                      "reports_deleted": reports_deleted}
            self._db.execute(
                """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                       before_state, after_state, at)
                   VALUES ('agent_node', :op, :key, :actor, :b, NULL, :at)""",
                {"op": operation, "key": node_name, "actor": actor,
                 "b": dump_json(before), "at": utc_now_iso()})
        return {"deleted": node_name, "reported_at": row["reported_at"],
                "reports_deleted": reports_deleted, "exclusion_deleted": exclusion is not None}

    def node_exists(self, node_name: str) -> bool:
        return self._db.query_one(
            "SELECT 1 AS x FROM agent_nodes WHERE node_name = :n",
            {"n": node_name}) is not None

    def node_reports(self, node_name: str, *, limit: int = 200) -> list[dict]:
        rows = self._db.query(
            """SELECT report, reported_at FROM agent_reports
               WHERE node_name = :n ORDER BY id DESC LIMIT :limit""",
            {"n": node_name, "limit": limit})
        return [{"reported_at": r["reported_at"], "report": load_json(r["report"])}
                for r in rows]

    def prune_reports(self, cutoff_iso: str, batch_size: int = 5000) -> int:
        total = 0
        while True:
            with self._db.transaction():
                rows = self._db.query(
                    """SELECT id FROM agent_reports WHERE reported_at < :cutoff
                       ORDER BY id LIMIT :n""",
                    {"cutoff": cutoff_iso, "n": batch_size})
                if not rows:
                    return total
                placeholders = ", ".join(f":i{k}" for k in range(len(rows)))
                params = {f"i{k}": row["id"] for k, row in enumerate(rows)}
                self._db.execute(
                    f"DELETE FROM agent_reports WHERE id IN ({placeholders})", params)
                total += len(rows)
