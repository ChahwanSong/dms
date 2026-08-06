import uuid

from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso

# 의존 순서다 — dms-agent 가 앞의 둘을 FROM 한다. 이 순서로 빌드하지 않으면 실패한다.
BUILD_IMAGES = ("dms-mpifileutils", "dms", "dms-agent")

_TERMINAL = ("Succeeded", "Failed")
_ACTIVE = ("Pending", "Running")
LOG_TEXT_MAX = 64 * 1024


def build_tag(build_id: str) -> str:
    """빌드마다 유일한 태그. 매니페스트가 전부 imagePullPolicy: IfNotPresent 라
    같은 태그를 다시 push 하면 클러스터가 영영 집어오지 않는다 -- 그래서 커밋 SHA 가
    아니라 빌드 고유 id 에서 뽑는다(같은 커밋을 두 번 빌드하는 건 정상 행위다)."""
    return "b" + build_id[:8]


def build_pod_name(build_id: str) -> str:
    return f"dms-build-{build_id[:12]}"[:63]


def _row(row):
    if row is None:
        return None
    out = dict(row)
    out["images"] = load_json(out.get("images")) or []
    return out


class BuildsRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, operation, target, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('build', :op, :key, :actor, :b, :a, :at)""",
            {"op": operation, "key": target, "actor": actor,
             "b": dump_json(before) if before is not None else None,
             "a": dump_json(after) if after is not None else None,
             "at": utc_now_iso()})

    def create(self, *, repo_url, git_ref, images, node_name, actor) -> str:
        build_id = uuid.uuid4().hex
        now = utc_now_iso()
        after = {"build_id": build_id, "git_ref": git_ref, "images": list(images),
                 "node_name": node_name}
        with self._db.transaction():
            # created_at은 초 단위 정밀도라 같은 초에 만들어진 빌드끼리는 정렬이
            # 안 된다(build_id는 uuid4라 순서와 무관) -- requests.commit_order와
            # 같은 방식으로 별도 단조 증가 seq를 둬서 생성 순서를 보장한다.
            row = self._db.query_one("SELECT COALESCE(MAX(seq), 0) AS m FROM builds")
            seq = row["m"] + 1
            self._db.execute(
                """INSERT INTO builds (build_id, seq, repo_url, git_ref, images, node_name,
                       state, created_at)
                   VALUES (:id, :seq, :url, :ref, :imgs, :node, 'Pending', :now)""",
                {"id": build_id, "seq": seq, "url": repo_url, "ref": git_ref,
                 "imgs": dump_json(list(images)), "node": node_name, "now": now})
            self._audit("create", build_id, None, after, actor)
        return build_id

    def get(self, build_id):
        return _row(self._db.query_one(
            "SELECT * FROM builds WHERE build_id = :id", {"id": build_id}))

    def list(self, limit: int = 50):
        rows = self._db.query(
            "SELECT * FROM builds ORDER BY seq DESC LIMIT :n",
            {"n": limit})
        return [_row(r) for r in rows]

    def _by_states(self, states, limit=50):
        # IN 절을 :named 로 만들려면 파라미터를 하나씩 풀어야 한다.
        keys = {f"s{i}": s for i, s in enumerate(states)}
        placeholders = ", ".join(f":{k}" for k in keys)
        rows = self._db.query(
            f"""SELECT * FROM builds WHERE state IN ({placeholders})
                ORDER BY seq ASC LIMIT :n""",
            {**keys, "n": limit})
        return [_row(r) for r in rows]

    def active(self):
        rows = self._by_states(_ACTIVE, limit=1)
        return rows[0] if rows else None

    def pending(self):
        return self._by_states(("Pending",))

    def running(self):
        return self._by_states(("Running",))

    def mark_running(self, build_id) -> None:
        self._db.execute(
            "UPDATE builds SET state = 'Running' WHERE build_id = :id AND state = 'Pending'",
            {"id": build_id})

    def finish(self, build_id, *, state, reason_code=None, commit_sha=None,
               log_text=None) -> None:
        if log_text is not None and len(log_text) > LOG_TEXT_MAX:
            log_text = log_text[-LOG_TEXT_MAX:]
        self._db.execute(
            """UPDATE builds SET state = :st, reason_code = :rc,
                   commit_sha = COALESCE(:sha, commit_sha),
                   log_text = COALESCE(:log, log_text),
                   finished_at = :now
               WHERE build_id = :id AND state NOT IN ('Succeeded', 'Failed')""",
            {"st": state, "rc": reason_code, "sha": commit_sha, "log": log_text,
             "now": utc_now_iso(), "id": build_id})

    def terminal_older_than(self, seconds: int, *, limit: int = 200, now_iso=None):
        now = now_iso or utc_now_iso()
        cutoff = iso_plus(now, -seconds)
        rows = self._db.query(
            """SELECT * FROM builds
               WHERE state IN ('Succeeded', 'Failed') AND finished_at IS NOT NULL
                 AND finished_at < :cutoff
               ORDER BY seq ASC LIMIT :n""",
            {"cutoff": cutoff, "n": limit})
        return [_row(r) for r in rows]
