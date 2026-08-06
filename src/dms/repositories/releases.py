from ..db import Database, dump_json, utc_now_iso
from ..domain import DomainValidationError

# 적용 순서. 컨트롤러가 자기 Deployment를 패치하면 롤아웃을 수행하던 파드 자신이
# 죽는다 -- 마지막에 둬야 앞의 둘이 이미 종단이고, 새 컨트롤러 파드는 자기 행 하나만
# 이어받으면 된다(설계 §2).
ROLLOUT_ORDER = ("dms-agent", "dms-api", "dms-controller")

# component -> 워크로드 좌표. 컨테이너 이름은 워크로드 이름에서 유도되지 않는다
# (실측: dms-controller의 컨테이너는 "controller", dms-api는 "api") -- 표로 박아둔다.
# repository는 레지스트리 리포 이름: api/controller는 같은 dms 이미지 계보를 쓴다.
COMPONENTS = {
    "dms-agent": {"kind": "DaemonSet", "workload": "dms-agent",
                  "container": "agent", "repository": "dms-agent",
                  "selector": "app.kubernetes.io/name=dms-agent"},
    "dms-api": {"kind": "Deployment", "workload": "dms-api",
                "container": "api", "repository": "dms",
                "selector": "app.kubernetes.io/name=dms-api"},
    "dms-controller": {"kind": "Deployment", "workload": "dms-controller",
                       "container": "controller", "repository": "dms",
                       "selector": "app.kubernetes.io/name=dms-controller"},
}

_ACTIVE = ("Pending", "Applying")
_TERMINAL = ("Applied", "Failed")


class ReleasesRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, operation, target, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('release', :op, :key, :actor, :b, :a, :at)""",
            {"op": operation, "key": target, "actor": actor,
             "b": dump_json(before) if before is not None else None,
             "a": dump_json(after) if after is not None else None,
             "at": utc_now_iso()})

    def create_batch(self, *, items, actor) -> list[dict]:
        # 순서를 DB에 지속시킨다: 제출 순서가 아니라 ROLLOUT_ORDER가 seq를 정한다.
        # 배치 중간에 죽은 컨트롤러는 seq만 보고 이어가므로, 순서가 행에 없으면
        # 이미 끝낸 패치를 다시 하거나 컨트롤러를 먼저 죽이는 사고가 난다(설계 §2).
        ordered = sorted(items, key=lambda i: ROLLOUT_ORDER.index(i["component"]))
        now = utc_now_iso()
        with self._db.transaction():
            # "동시 롤아웃 1개"의 진짜 가드 -- builds.create()와 같은 관용구로
            # 존재 확인과 INSERT를 같은 트랜잭션 안에서 원자적으로 처리한다.
            if self.active():
                raise DomainValidationError(
                    "rollout_in_progress", "an active rollout already exists")
            row = self._db.query_one("SELECT COALESCE(MAX(seq), 0) AS m FROM releases")
            seq = row["m"]
            first_seq = seq + 1
            for item in ordered:
                seq += 1
                self._db.execute(
                    """INSERT INTO releases (component, image, tag, digest, state,
                           reason_code, seq, actor, applied_at)
                       VALUES (:c, :img, :tag, NULL, 'Pending', NULL, :seq, :actor, :now)""",
                    {"c": item["component"], "img": item["image"],
                     "tag": item["tag"], "seq": seq, "actor": actor, "now": now})
            self._audit("create", f"seq:{first_seq}-{seq}", None,
                        {"items": ordered}, actor)
            rows = self._db.query(
                "SELECT * FROM releases WHERE seq >= :s ORDER BY seq ASC",
                {"s": first_seq})
        return [dict(r) for r in rows]

    def get(self, release_id):
        row = self._db.query_one("SELECT * FROM releases WHERE id = :id",
                                 {"id": release_id})
        return dict(row) if row else None

    def list(self, limit: int = 50):
        rows = self._db.query(
            "SELECT * FROM releases ORDER BY id DESC LIMIT :n", {"n": limit})
        return [dict(r) for r in rows]

    def current(self):
        # "현재 릴리스"는 컴포넌트별 MAX(id)로 유도한다 -- component 유니크 제약이
        # 없고 인덱스가 (component, id)다(설계 §6).
        rows = self._db.query(
            """SELECT r.* FROM releases r
               JOIN (SELECT component, MAX(id) AS mid FROM releases
                     GROUP BY component) m ON r.id = m.mid""")
        return {r["component"]: dict(r) for r in rows}

    def active(self):
        rows = self._db.query(
            """SELECT * FROM releases WHERE state IN ('Pending', 'Applying')
               ORDER BY seq ASC""")
        return [dict(r) for r in rows]

    def mark_applying(self, release_id) -> None:
        # applied_at을 갱신해 "Applying이 된 시각"을 남긴다 -- RolloutWatcher의
        # 나이 기반 회수(벽시계 타임아웃)가 이 값을 기준으로 잰다.
        self._db.execute(
            """UPDATE releases SET state = 'Applying', applied_at = :now
               WHERE id = :id AND state = 'Pending'""",
            {"now": utc_now_iso(), "id": release_id})

    def finish(self, release_id, *, state, reason_code=None) -> None:
        self._db.execute(
            """UPDATE releases SET state = :st, reason_code = :rc, applied_at = :now
               WHERE id = :id AND state NOT IN ('Applied', 'Failed')""",
            {"st": state, "rc": reason_code, "now": utc_now_iso(),
             "id": release_id})

    def abort_pending(self, *, reason_code) -> int:
        # 한 컴포넌트가 Failed면 뒤 Pending들을 종단시켜 배치를 닫는다 -- 안 하면
        # active()가 비지 않아 rollout_in_progress가 영원히 새 롤아웃을 막는다.
        rows = self._db.query(
            "SELECT id FROM releases WHERE state = 'Pending' ORDER BY seq ASC")
        for row in rows:
            self.finish(row["id"], state="Failed", reason_code=reason_code)
        return len(rows)
