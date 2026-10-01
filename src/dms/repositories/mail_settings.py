"""포탈 메일 설정(2026-10-01) -- Knox 메일 릴레이 연결값을 포탈(관리 → 메일 설정)에서 바꾼다.

단일 행(id=1). 칸이 NULL 이면 env 기본값(Settings.mailer_backend·mail_relay_*·mail_service_name)을 쓴다 --
해석은 mail_config.resolve_mail_config 하나가 한다(소비자가 행을 직접 해석하지 않는다).

릴레이 토큰은 relay_token_enc 에 secret_box 봉인(v1:...)으로만 둔다. 감사 로그에는 토큰을 남기지 않는다 --
before/after 스냅샷의 relay_token_enc 는 "set"/null 로 바꿔 적는다(봉인도 비밀의 파생물이라 남기지 않는다).
"""
from ..db import Database, dump_json, utc_now_iso

# PUT 이 바꿀 수 있는 칸(토큰 제외). 값 None = 그 칸을 비워 env 기본값으로 되돌림.
FIELDS = ("backend", "relay_scheme", "relay_host", "relay_port", "timeout_seconds", "service_name")
_COLUMNS = FIELDS + ("relay_token_enc",)


def _redacted(row):
    if row is None:
        return None
    out = {k: row.get(k) for k in FIELDS}
    out["relay_token"] = "set" if row.get("relay_token_enc") else None
    out["updated_by"] = row.get("updated_by")
    return out


class MailSettingsRepository:
    def __init__(self, db: Database):
        self._db = db

    def get(self):
        return self._db.query_one("SELECT * FROM mail_settings WHERE id = 1")

    def update(self, changes: dict, *, actor: str, guard=None):
        """changes: _COLUMNS 의 부분집합(값 None = 비움). 바뀐 게 없어도 감사는 남긴다(누가 저장을 눌렀나).

        guard(before_row_or_empty_dict): 쓰기 직전, 잠근 현재 행을 보고 거절(예외)할 기회 -- 라우트의 "주소를
        바꾸면 키를 다시" 검사가 여기서 돈다(2026-10-01 리뷰: 트랜잭션 밖에서 검사하면 다른 관리자의 동시 저장
        사이에 새 키가 그 키를 입력한 적 없는 주소에 묶일 수 있었다). 같은 프로세스는 Database 의 RLock 이,
        레플리카 사이는 PostgreSQL 의 FOR UPDATE 가 직렬화한다(행이 없으면 ON CONFLICT DO NOTHING 으로 먼저
        만든 뒤 잠근다 -- 두 레플리카의 첫 INSERT 가 PK 위반으로 500 이 되지 않게)."""
        unknown = set(changes) - set(_COLUMNS)
        if unknown:
            raise ValueError(f"unknown mail_settings columns: {sorted(unknown)}")
        lock = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
        with self._db.transaction():
            existed = self.get() is not None
            if not existed:
                self._db.execute("INSERT INTO mail_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            current = self._db.query_one(f"SELECT * FROM mail_settings WHERE id = 1{lock}")
            before = current if existed else None
            if guard is not None:
                guard(dict(current))
            params = {"at": utc_now_iso(), "by": actor}
            sets = ["updated_at = :at", "updated_by = :by"]
            for col in _COLUMNS:
                if col in changes:
                    sets.append(f"{col} = :{col}")
                    params[col] = changes[col]
            self._db.execute(f"UPDATE mail_settings SET {', '.join(sets)} WHERE id = 1", params)
            after = self.get()
            b, a = _redacted(before), _redacted(after)
            if "relay_token_enc" in changes:
                # 값 없이 "무엇이 일어났나"만 -- 같은 "set"→"set" 이어도 키가 바뀐 것을 감사에서 구분한다.
                had = bool(before and before.get("relay_token_enc"))
                a["relay_token_change"] = ("cleared" if changes["relay_token_enc"] is None
                                           else "replaced" if had else "set")
            self._db.execute(
                """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                       before_state, after_state, at)
                   VALUES ('mail_settings', 'update', 'mail_settings', :actor, :b, :a, :at)""",
                {"actor": actor, "b": dump_json(b) if b else None, "a": dump_json(a),
                 "at": params["at"]})
        return after
