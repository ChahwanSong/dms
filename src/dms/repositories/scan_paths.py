"""사용자 scan 경로 등록. 커버 판정은 DB 필드(스토리지 상대 경로)만으로 하고,
아티팩트는 매치된 잡 1건만 읽는다 (읽기는 api/artifacts.py 헬퍼가 담당)."""
import posixpath

from ..db import Database, utc_now_iso
from ..domain import DomainValidationError


def covers(scan_target: str, registered_path: str) -> bool:
    """스캔 대상이 등록 경로의 조상-또는-동일인가. 둘 다 스토리지 상대 경로다."""
    t = posixpath.normpath(scan_target or "")
    p = posixpath.normpath(registered_path or "")
    return t == p or p.startswith(t + "/")


class UserScanPathsRepository:
    def __init__(self, db: Database):
        self._db = db

    def list_for(self, username: str) -> list[dict]:
        return self._db.query(
            """SELECT * FROM user_scan_paths WHERE username = :u
               ORDER BY storage_name, path""", {"u": username})

    def add(self, username: str, storage_name: str, path: str) -> int:
        existing = self._db.query_one(
            """SELECT id FROM user_scan_paths
               WHERE username = :u AND storage_name = :s AND path = :p""",
            {"u": username, "s": storage_name, "p": path})
        if existing is not None:
            raise DomainValidationError("scan_path_exists", path)
        self._db.execute(
            """INSERT INTO user_scan_paths (username, storage_name, path, created_at)
               VALUES (:u, :s, :p, :now)""",
            {"u": username, "s": storage_name, "p": path, "now": utc_now_iso()})
        row = self._db.query_one(
            """SELECT id FROM user_scan_paths
               WHERE username = :u AND storage_name = :s AND path = :p""",
            {"u": username, "s": storage_name, "p": path})
        return row["id"]

    def get_owned(self, path_id: int, username: str) -> "dict | None":
        return self._db.query_one(
            "SELECT * FROM user_scan_paths WHERE id = :i AND username = :u",
            {"i": path_id, "u": username})

    def delete_owned(self, path_id: int, username: str) -> bool:
        if self.get_owned(path_id, username) is None:
            return False
        self._db.execute(
            "DELETE FROM user_scan_paths WHERE id = :i AND username = :u",
            {"i": path_id, "u": username})
        return True
