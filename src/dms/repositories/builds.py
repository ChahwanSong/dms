import uuid

from ..db import Database, dump_json, iso_plus, load_json, utc_now_iso
from ..domain import DomainValidationError

# 의존 순서다 — dms-agent 가 앞의 둘을 FROM 한다. 이 순서로 빌드하지 않으면 실패한다.
BUILD_IMAGES = ("dms-mpifileutils", "dms", "dms-agent")

_ACTIVE = ("Pending", "Running")
LOG_TEXT_MAX = 64 * 1024


def build_tag(build_id: str) -> str:
    """빌드마다 유일한 태그. 매니페스트가 전부 imagePullPolicy: IfNotPresent 라
    같은 태그를 다시 push 하면 클러스터가 영영 집어오지 않는다 -- 그래서 커밋 SHA 가
    아니라 빌드 고유 id 에서 뽑는다(같은 커밋을 두 번 빌드하는 건 정상 행위다)."""
    return "b" + build_id[:8]


def build_pod_name(build_id: str) -> str:
    return f"dms-build-{build_id[:12]}"[:63]


def build_probe_pod_name(build_id: str) -> str:
    # 슬라이스 21 §2.5: 적합성 프로브 파드. 빌드 파드와 같은 결정적 이름 규칙이라
    # 워처가 프로브 상태를 DB 에 두지 않고도(컬럼 0개) "이 빌드의 프로브"를 매 틱
    # 다시 찾을 수 있다 -- 멱등 create + poll 만으로 상태기계가 성립한다.
    # "pf" 세그먼트는 hex(build_id)와 절대 충돌하지 않아 빌드 파드와 판별 가능하다.
    return f"dms-build-pf-{build_id[:12]}"[:63]


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
        # repo_url을 감사에 남긴다 -- 이게 빠지면 admin이 임의 저장소로 이미지를
        # 만들어도(예: repo_url="https://evil/x.git") 감사 기록에 "어느 저장소의"
        # 커밋인지가 안 남는다. commit SHA만으로는 저장소를 특정할 수 없다.
        after = {"build_id": build_id, "repo_url": repo_url, "git_ref": git_ref,
                 "images": list(images), "node_name": node_name}
        with self._db.transaction():
            # "동시에 활성 빌드 하나만" 불변식의 진짜 가드는 여기다 -- active() 존재
            # 확인과 INSERT를 같은 트랜잭션(=Database._lock을 쥔 같은 구간) 안에서
            # 원자적으로 처리한다. 호출자(라우터)가 트랜잭션 밖에서 미리 active()를
            # 체크하는 건 빠른 거절(fail-fast)일 뿐이다 -- 두 요청이 거의 동시에
            # 각자 create()를 부르면 그 사전 체크만으로는 활성 빌드가 2개 생기는 걸
            # 막지 못한다. Database._lock은 threading.RLock이라 같은 프로세스 안
            # 여러 스레드 사이의 경쟁은 막아주지만, api replicas가 2개 이상으로
            # 늘어나면(프로세스가 여러 개) 프로세스 경계를 못 넘으므로 소용없다 --
            # 지금은 replicas=1이라 이걸로 충분하다.
            if self.active() is not None:
                raise DomainValidationError(
                    "build_in_progress", "an active build already exists")
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
        # I2: 목록은 SELECT * 를 쓰지 않는다 -- log_text는 행마다 최대 64KB라
        # limit(기본 50) x 64KB = 최대 3.2MB가 매 폴링(프론트 5초 간격)마다 왕복한다.
        # 로그는 전용 /log 엔드포인트가 따로 있어 목록에서 쓰이지 않는다. seq도
        # 내부 정렬용 컬럼이라 함께 뺀다(정렬 자체는 SELECT 목록에 없어도 동작한다).
        rows = self._db.query(
            """SELECT build_id, repo_url, git_ref, commit_sha, images, node_name,
                      state, reason_code, created_at, finished_at
               FROM builds ORDER BY seq DESC LIMIT :n""",
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
