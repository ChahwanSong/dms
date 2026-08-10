from ..db import Database, dump_json, utc_now_iso
from ..domain import DomainValidationError

# 적용 순서. 컨트롤러가 자기 Deployment를 패치하면 롤아웃을 수행하던 파드 자신이
# 죽는다 -- 마지막에 둬야 앞의 둘이 이미 종단이고, 새 컨트롤러 파드는 자기 행 하나만
# 이어받으면 된다(설계 §2).
ROLLOUT_ORDER = ("dms-agent", "dms-api", "dms-controller")

# component -> 워크로드 좌표. 컨테이너 이름은 워크로드 이름에서 유도되지 않는다
# (실측: dms-controller의 컨테이너는 "controller", dms-api는 "api") -- 표로 박아둔다.
# repository는 레지스트리 리포 이름: api/controller는 같은 dms 이미지 계보를 쓴다.
#
# init_container: 슬라이스 16이 40-api.yaml/41-controller.yaml에 넣은 migrate
# initContainer의 이름. 롤아웃 패치가 본 컨테이너만 갱신하면 새 파드가 "구 이미지로
# migrate -> 신 앱"을 하게 되어 스키마가 뒤처진 채 앱이 뜬다(슬라이스 14·15의 그 500).
# 반드시 컴포넌트별이어야 한다 -- dms-agent DaemonSet에는 이 initContainer가 없고,
# 없는 것을 strategic merge로 패치하면 병합이 아니라 **새 컨테이너 생성**이 된다.
# 그래서 dms-agent는 값을 None으로 두는 대신 키 자체를 두지 않는다(위 dict 리터럴을
# 통째로 비교하는 계약 테스트가 그 부재를 지킨다). 매니페스트에서 initContainer를
# 빼거나 이름을 바꾸면 이 표도 같이 고쳐야 한다.
COMPONENTS = {
    "dms-agent": {"kind": "DaemonSet", "workload": "dms-agent",
                  "container": "agent", "repository": "dms-agent",
                  "selector": "app.kubernetes.io/name=dms-agent"},
    "dms-api": {"kind": "Deployment", "workload": "dms-api",
                "container": "api", "repository": "dms",
                "selector": "app.kubernetes.io/name=dms-api",
                "init_container": "migrate"},
    "dms-controller": {"kind": "Deployment", "workload": "dms-controller",
                       "container": "controller", "repository": "dms",
                       "selector": "app.kubernetes.io/name=dms-controller",
                       "init_container": "migrate"},
}

# 이 표에서 순서는 곧 "누가 head인가"이고 head만 패치된다 -- 순서가 백엔드마다
# 갈리면 조용히 ROLLOUT_ORDER가 깨진다(컨트롤러를 먼저 죽이는 사고).
#
# seq는 nullable이다: SQLite의 ALTER TABLE ADD COLUMN이 NOT NULL을 못 붙여
# (마이그레이션 주석) 신규 DB의 CREATE TABLE과 구형 DB의 ALTER가 같은 스키마로
# 수렴하려면 제약을 뺄 수밖에 없다. NOT NULL로 바꾸는 대안은 기각한다 -- 두 경로가
# 어긋나거나 SQLite에서 표 재작성이 필요해진다.
#
# 그래서 정렬에서 NULL을 결정적으로 처리한다. `seq ASC`만 두면 SQLite는 NULL을
# 먼저, PostgreSQL은 나중에 놓는다(실측: sqlite 3.45 는 NULL 먼저). `id ASC`
# tiebreaker만으로는 이 갈림이 안 닫힌다 -- 동률 seq만 정리할 뿐이다. `seq IS NULL`을
# 첫 키로 두면 두 백엔드 모두 NULL이 마지막이다(불리언 false < true). 지금 NULL seq를
# 만드는 경로는 없지만(create_batch는 항상 MAX(seq)+1을 넣는다), 구형 DB에서 ALTER로
# 보강된 행이 있다면 그것이 head를 가로채는 것보다 맨 뒤에 놓이는 편이 안전하다.
_ORDER = "ORDER BY seq IS NULL ASC, seq ASC, id ASC"


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
                f"SELECT * FROM releases WHERE seq >= :s {_ORDER}",
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
            f"""SELECT * FROM releases WHERE state IN ('Pending', 'Applying')
                {_ORDER}""")
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

    def note_progress(self, release_id, *, progress, now=None) -> None:
        """진행이 관찰된 롤아웃의 회수 시계를 다시 건다(DaemonSet 전용, I6).

        벽시계가 "시작 이후 흐른 시간"이 아니라 "마지막 진행 이후 정체한 시간"을
        재게 한다. 실 클러스터의 dms-agent는 5노드 × maxUnavailable=1 순차
        롤아웃이라 절대 시각 기준으로는 정상 진행도 600초를 넘기고, dms-agent가
        ROLLOUT_ORDER 첫째라 회수되면 abort_pending이 dms-api/dms-controller까지
        죽인다.

        applied_at을 앞당겨도 재개 판정은 깨지지 않는다: 재개는 "행이 Applying인가"만
        보고(active() + run_once의 Pending 분기) 시각을 보지 않는다. state를
        건드리지 않으므로 record-then-patch 계약에도 영향이 없다. 종단 행에는
        쓰지 않도록 state='Applying' 가드를 둔다.

        부작용 하나: 포탈 이력의 "시각"이 진행 중인 DaemonSet 행에서는 시작 시각이
        아니라 마지막 진행 시각을 보여준다 -- 종단되면 finish()가 다시 덮으므로
        완료된 행에는 영향이 없다.
        """
        self._db.execute(
            """UPDATE releases SET applied_at = :now, progress = :p
               WHERE id = :id AND state = 'Applying'""",
            {"now": now or utc_now_iso(), "p": progress, "id": release_id})

    def abort_pending(self, *, reason_code) -> int:
        # 한 컴포넌트가 Failed면 뒤 Pending들을 종단시켜 배치를 닫는다 -- 안 하면
        # active()가 비지 않아 rollout_in_progress가 영원히 새 롤아웃을 막는다.
        rows = self._db.query(
            f"SELECT id FROM releases WHERE state = 'Pending' {_ORDER}")
        for row in rows:
            self.finish(row["id"], state="Failed", reason_code=reason_code)
        return len(rows)
