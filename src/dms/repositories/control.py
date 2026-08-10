from ..db import Database, dump_json, iso_plus, utc_now_iso
from ..domain import DomainValidationError

POLICY_TOOLS = ("scan", "dsync", "nsync", "rm")
DENY_SUBJECT_TYPES = ("requester", "owner", "group")


class ControlRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, mutation_class, operation, target, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES (:c, :op, :key, :actor, :b, :a, :at)""",
            {"c": mutation_class, "op": operation, "key": target, "actor": actor,
             "b": dump_json(before) if before is not None else None,
             "a": dump_json(after) if after is not None else None, "at": utc_now_iso()})

    # --- policies ---
    def get_policy(self, tool):
        return self._db.query_one("SELECT * FROM policies WHERE tool = :t", {"t": tool})

    def upsert_policy(self, tool, *, max_nodes, procs_per_node, queue,
                      default_priority, max_priority, preview_timeout_seconds,
                      execution_timeout_seconds, enabled, actor):
        if tool not in POLICY_TOOLS:
            raise DomainValidationError("invalid_policy", f"unknown tool {tool!r}")
        before = self.get_policy(tool)
        now = utc_now_iso()
        with self._db.transaction():
            self._db.execute("DELETE FROM policies WHERE tool = :t", {"t": tool})
            self._db.execute(
                """INSERT INTO policies (tool, max_nodes, procs_per_node, queue,
                       default_priority, max_priority, preview_timeout_seconds,
                       execution_timeout_seconds, enabled, updated_at, updated_by)
                   VALUES (:t, :mn, :pp, :q, :dp, :mp, :pt, :et, :e, :now, :actor)""",
                {"t": tool, "mn": max_nodes, "pp": procs_per_node, "q": queue,
                 "dp": default_priority, "mp": max_priority,
                 "pt": preview_timeout_seconds, "et": execution_timeout_seconds,
                 "e": 1 if enabled else 0, "now": now, "actor": actor})
            self._audit("policy", "upsert", tool, before, self.get_policy(tool), actor)

    # --- denylist ---
    def deny(self, subject_type, subject, reason, actor):
        if subject_type not in DENY_SUBJECT_TYPES:
            raise DomainValidationError("invalid_denylist_subject_type", subject_type)
        subject = subject.lower()
        with self._db.transaction():
            self._db.execute(
                """INSERT INTO identity_denylist (subject_type, subject, reason,
                       created_by, created_at)
                   SELECT :t, :s, :r, :actor, :now
                   WHERE NOT EXISTS (SELECT 1 FROM identity_denylist
                                     WHERE subject_type = :t AND subject = :s)""",
                {"t": subject_type, "s": subject, "r": reason,
                 "actor": actor, "now": utc_now_iso()})
            self._audit("denylist", "deny", f"{subject_type}:{subject}", None,
                        {"subject_type": subject_type, "subject": subject, "reason": reason}, actor)

    def allow(self, subject_type, subject, actor):
        subject = subject.lower()
        with self._db.transaction():
            self._db.execute(
                "DELETE FROM identity_denylist WHERE subject_type = :t AND subject = :s",
                {"t": subject_type, "s": subject})
            self._audit("denylist", "allow", f"{subject_type}:{subject}",
                        {"subject_type": subject_type, "subject": subject}, None, actor)

    def is_denied(self, *, requester, owner, groups):
        rows = self._db.query("SELECT subject_type, subject FROM identity_denylist")
        lowered_groups = {g.lower() for g in groups}
        for row in rows:
            subject = row["subject"]
            kind = row["subject_type"]
            if kind == "requester" and subject.lower() == requester.lower():
                return subject
            if kind == "owner" and subject.lower() == owner.lower():
                return subject
            if kind == "group" and subject.lower() in lowered_groups:
                return subject
        return None

    def list_denylist(self):
        return self._db.query(
            """SELECT subject_type, subject, reason FROM identity_denylist
               ORDER BY subject_type, subject""")

    def has_group_denies(self) -> bool:
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM identity_denylist WHERE subject_type = 'group'")
        return bool(row and row["n"])

    # --- control state ---
    def control_state(self):
        return self._db.query_one("SELECT * FROM control_state WHERE id = 1")

    def set_control_state(self, *, maintenance, drain, reason, actor,
                          build_node_name=None):
        before = self.control_state()
        with self._db.transaction():
            self._db.execute(
                """UPDATE control_state SET maintenance = :m, drain = :d, reason = :r,
                       build_node_name = :bn,
                       changed_by = :actor, changed_at = :now WHERE id = 1""",
                {"m": 1 if maintenance else 0, "d": 1 if drain else 0,
                 "r": reason, "bn": build_node_name, "actor": actor,
                 "now": utc_now_iso()})
            self._audit("control_state", "set", "control_state", before,
                        self.control_state(), actor)

    def set_artifact_base(self, uri, *, actor, forced=False, affected_jobs=0):
        """아티팩트 base 전용 UPDATE(슬라이스 18 설계 §2.1). set_control_state 에
        얹지 않는다: 그 UPDATE 는 build_node_name = :bn 을 **무조건** 쓰므로 인자를
        생략한 호출이 기존 값을 조용히 NULL 로 지운다(현재는 라우트가 항상 넘겨
        잠복해 있을 뿐). 같은 자리에 컬럼을 하나 더 얹으면 그 함정이 그대로
        복제된다 -- 이 컬럼 하나만 만지는 UPDATE 로 분리하면 구조적으로
        불가능해진다. changed_by/changed_at 도 만지지 않는다: 그 둘은 유지보수·
        드레인 변경 표시라, base 변경이 덮으면 컨트롤 상태 화면의 변경 이력이
        오염된다(변경자는 감사 로그가 나른다)."""
        before = self.control_state()
        with self._db.transaction():
            self._db.execute(
                "UPDATE control_state SET artifact_base_uri = :uri WHERE id = 1",
                {"uri": uri})
            # force 통과는 반드시 감사에 남는다(설계 §2.3): affected_jobs 건의
            # 아티팩트·로그 열람이 깨진다는 사실이 추적 가능해야 한다.
            self._audit("artifact_base", "set", "artifact_base", before,
                        {"artifact_base_uri": uri, "forced": forced,
                         "affected_jobs": affected_jobs}, actor)

    def set_artifact_base_check(self, *, uri, ok, reason, now_iso=None):
        """컨트롤러 관점 검증 결과(설계 §2.4c) 전용 UPDATE. 주기 기록이라 감사를
        남기지 않고(운영자 변경이 아니다), 검증 4컬럼 밖은 만지지 않는다
        (set_artifact_base 와 같은 분리 원칙)."""
        self._db.execute(
            """UPDATE control_state SET artifact_base_check_uri = :uri,
                   artifact_base_check_ok = :ok, artifact_base_check_reason = :r,
                   artifact_base_check_at = :at WHERE id = 1""",
            {"uri": uri, "ok": 1 if ok else 0, "r": reason,
             "at": now_iso or utc_now_iso()})

    # --- leases ---
    def try_acquire_lease(self, component, holder, lease_seconds,
                          now_iso: str | None = None) -> bool:
        now = now_iso or utc_now_iso()
        expires = iso_plus(now, lease_seconds)
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT holder, expires_at FROM component_leases WHERE component = :c",
                {"c": component})
            if row is None:
                self._db.execute(
                    """INSERT INTO component_leases (component, holder, expires_at)
                       VALUES (:c, :h, :e)""",
                    {"c": component, "h": holder, "e": expires})
                return True
            if row["holder"] != holder and row["expires_at"] > now:
                return False
            self._db.execute(
                """UPDATE component_leases SET holder = :h, expires_at = :e
                   WHERE component = :c""",
                {"c": component, "h": holder, "e": expires})
            return True

    # --- identity probe targets ---
    def register_probe_target(self, username: str, now_iso: str | None = None) -> None:
        now = now_iso or utc_now_iso()
        with self._db.transaction():
            self._db.execute(
                "DELETE FROM identity_probe_targets WHERE username = :u", {"u": username})
            self._db.execute(
                """INSERT INTO identity_probe_targets (username, last_requested_at)
                   VALUES (:u, :at)""",
                {"u": username, "at": now})

    def probe_targets(self, *, ttl_seconds: int, now_iso: str | None = None) -> list[str]:
        now = now_iso or utc_now_iso()
        cutoff = iso_plus(now, -ttl_seconds)
        with self._db.transaction():
            self._db.execute(
                "DELETE FROM identity_probe_targets WHERE last_requested_at < :cutoff",
                {"cutoff": cutoff})
            rows = self._db.query(
                "SELECT username FROM identity_probe_targets ORDER BY username")
        return [r["username"] for r in rows]

    # --- audit ---
    def audit_entries(self, limit: int = 50) -> list[dict]:
        return self._db.query(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT :n", {"n": limit})
