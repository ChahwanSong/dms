"""에이전트 리포트 저장소: 이력(agent_reports) + 노드별 최신 1행(agent_nodes)."""
from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso


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
