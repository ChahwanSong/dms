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
        if not p.startswith("/") or posixpath.normpath(p) != p.rstrip("/") and p != "/":
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
