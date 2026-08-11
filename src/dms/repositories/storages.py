import posixpath
import re
from ..db import Database, dump_json, utc_now_iso
from ..domain import DomainValidationError

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}$")
_BACKENDS = ("cephfs", "gpfs", "wekafs")


def _validate(storage_name, mount_path, managed_root, backend_type):
    if not _NAME_RE.fullmatch(storage_name):
        raise DomainValidationError("invalid_storage", f"bad name {storage_name!r}")
    if backend_type not in _BACKENDS:
        raise DomainValidationError("invalid_storage", f"bad backend {backend_type!r}")
    for p in (mount_path, managed_root):
        if p == "/":
            # 슬라이스 24 §2.2: CephFS/GPFS/WekaFS 는 노드 루트에 마운트되지
            # 않는다 -- "/" 가 정당한 배포는 없다. 이 값이 살면 mount 검사가 어느
            # 노드에서나 statvfs("/") 로 Ready 가 되고, 잡 파드는 노드 루트를
            # hostPath 로 통째로 마운트하며(_volumes 의 조상-커버 축약이 전부를
            # "/" 하나로 접는다), rm 대상 검증(""/"."만 거부)을 통과한 "etc" 류가
            # 요청자 신원으로 지워진다. 검증은 create/update 에만 발화하므로 이미
            # DB 에 있는 "/" 행은 stepper._abs 의 join 이 2차 방어다(같은 슬라이스).
            raise DomainValidationError("invalid_storage",
                                        "root filesystem is not a storage")
        # 기존 규칙에서 `and p != "/"` 예외 절을 제거했다 -- 그 절의 유일한 존재
        # 이유가 "/" 를 살리는 것이었고, 이제 위에서 명시 거부한다(이중 봉인).
        if not p.startswith("/") or posixpath.normpath(p) != p.rstrip("/"):
            raise DomainValidationError("invalid_storage", f"bad path {p!r}")
    mount = posixpath.normpath(mount_path)
    root = posixpath.normpath(managed_root)
    if root != mount and not root.startswith(mount + "/"):
        raise DomainValidationError("invalid_storage",
                                    "managed_root must be under mount_path")


class StoragesRepository:
    def __init__(self, db: Database):
        self._db = db

    def _audit(self, operation, target, before, after, actor):
        self._db.execute(
            """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                   before_state, after_state, at)
               VALUES ('storage', :op, :key, :actor, :b, :a, :at)""",
            {"op": operation, "key": target, "actor": actor,
             "b": dump_json(before) if before else None,
             "a": dump_json(after) if after else None, "at": utc_now_iso()})

    def create(self, *, storage_name, mount_path, managed_root, backend_type, actor):
        _validate(storage_name, mount_path, managed_root, backend_type)
        mount_path = posixpath.normpath(mount_path)
        managed_root = posixpath.normpath(managed_root)
        now = utc_now_iso()
        with self._db.transaction():
            if self.get(storage_name) is not None:
                raise DomainValidationError("storage_exists", storage_name)
            self._db.execute(
                """INSERT INTO storages (storage_name, mount_path, managed_root,
                       backend_type, enabled, status, created_at, updated_at, updated_by)
                   VALUES (:n, :m, :r, :b, 1, 'Unknown', :now, :now, :actor)""",
                {"n": storage_name, "m": mount_path, "r": managed_root,
                 "b": backend_type, "now": now, "actor": actor})
            after = self.get(storage_name)
            self._audit("create", storage_name, None, after, actor)
        return after

    def update(self, storage_name, *, mount_path, managed_root, backend_type,
               enabled: bool, actor):
        _validate(storage_name, mount_path, managed_root, backend_type)
        mount_path = posixpath.normpath(mount_path)
        managed_root = posixpath.normpath(managed_root)
        before = self.get(storage_name)
        if before is None:
            raise KeyError(storage_name)
        with self._db.transaction():
            self._db.execute(
                """UPDATE storages SET mount_path = :m, managed_root = :r,
                       backend_type = :b, enabled = :e, updated_at = :now,
                       updated_by = :actor
                   WHERE storage_name = :n""",
                {"m": mount_path, "r": managed_root, "b": backend_type,
                 "e": 1 if enabled else 0, "now": utc_now_iso(),
                 "actor": actor, "n": storage_name})
            after = self.get(storage_name)
            self._audit("update", storage_name, before, after, actor)
        return after

    def delete(self, storage_name, actor):
        before = self.get(storage_name)
        if before is None:
            raise KeyError(storage_name)
        with self._db.transaction():
            self._db.execute("DELETE FROM storages WHERE storage_name = :n",
                             {"n": storage_name})
            self._audit("delete", storage_name, before, None, actor)
        return before

    def get(self, storage_name):
        return self._db.query_one(
            "SELECT * FROM storages WHERE storage_name = :n", {"n": storage_name})

    def list(self):
        return self._db.query("SELECT * FROM storages ORDER BY storage_name")

    def set_status(self, storage_name, status, detail=None):
        self._db.execute(
            """UPDATE storages SET status = :s, status_detail = :d, updated_at = :now
               WHERE storage_name = :n""",
            {"s": status, "d": detail, "now": utc_now_iso(), "n": storage_name})
