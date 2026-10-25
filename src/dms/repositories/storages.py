import posixpath
import re
from ..artifact_base import ARTIFACT_MOUNT
from ..db import Database, dump_json, load_json, utc_now_iso
from ..domain import DomainValidationError
from .k8s import PV_CAPABLE_KINDS, STORAGE_KINDS, delete_maps_for_storage_in_txn, storage_kind

_NAME_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}$")
# 백엔드 식별자는 검증·저장·표시용 라벨이다 -- 마운트 프로브·잡 볼륨·경로 해석은 전부
# mount_path/managed_root 만 보고 백엔드별 분기가 없다(2026-09-15 확인). 값을 더할 때는
# 프런트 StorageDialog.BACKENDS(표시명)와 api.ts invalid_storage 문구도 같이 --
# tests/test_storage_backends.py 가 두 셋의 일치를 고정한다.
# dms-k8s(2026-10-25 설계 D9·D11): backend_type 은 **PV 경로 프리셋 키**이기도 하다(pv_paths.PRESETS -- gpfs·onefs·nfs).
# 직접 경로 해석은 여전히 managed_root 만 본다. onefs = Dell PowerScale, nfs = 일반 NFS(테스트베드 csi-driver-nfs).
_BACKENDS = ("cephfs", "gpfs", "wekafs", "lustre", "purestorage", "netapp", "onefs", "nfs")
# 스토리지 종류(D11): filesystem(직접 경로) · pv(k8s PV 만 -- StorageClass 매핑 대상) · both. NULL = filesystem(컬럼 이전 행).
# 집합의 정의는 repositories.k8s(STORAGE_KINDS·PV_CAPABLE_KINDS·storage_kind) 한 곳이다.


def _validate_kind(kind) -> str:
    if kind not in STORAGE_KINDS:
        raise DomainValidationError("invalid_storage_kind", f"bad kind {kind!r}")
    return kind


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
    if mount == ARTIFACT_MOUNT or mount.startswith(ARTIFACT_MOUNT + "/"):
        # 잡 파드가 아티팩트 base 를 이 경로에 전용 마운트한다(artifact_base 주석) --
        # 같은 mountPath 가 둘이면 파드 스펙이 거부되거나 한쪽이 가려진다.
        raise DomainValidationError("invalid_storage",
                                    f"mount_path collides with {ARTIFACT_MOUNT}")


def storage_open_to_users(row) -> bool:
    """일반 사용자 작업에 쓸 수 있나(2026-09-30 사용 범위). 세 상태:
      - enabled=1, user_enabled=1 → 전체 사용
      - enabled=1, user_enabled=0 → 관리자 전용("사용자에게만 비활성")
      - enabled=0                 → 완전 비활성(누구도 새 작업 불가, planner storage_disabled)
    user_enabled NULL 은 컬럼 이전(기배포 ALTER 직후) 행이다 -- 종전 동작(활성이면 누구나)으로
    읽는다(migrations._backfill_storage_user_enabled 가 곧 1 로 채운다; 모름 ≠ 거부)."""
    return row["enabled"] == 1 and row.get("user_enabled") != 0


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

    def create(self, *, storage_name, mount_path, managed_root, backend_type, actor,
               enabled: bool = True, user_enabled: bool = True, kind: str = "filesystem"):
        _validate(storage_name, mount_path, managed_root, backend_type)
        _validate_kind(kind)
        mount_path = posixpath.normpath(mount_path)
        managed_root = posixpath.normpath(managed_root)
        now = utc_now_iso()
        with self._db.transaction():
            if self.get(storage_name) is not None:
                raise DomainValidationError("storage_exists", storage_name)
            self._db.execute(
                """INSERT INTO storages (storage_name, mount_path, managed_root,
                       backend_type, enabled, user_enabled, status, created_at,
                       updated_at, updated_by, kind)
                   VALUES (:n, :m, :r, :b, :e, :u, 'Unknown', :now, :now, :actor, :k)""",
                {"n": storage_name, "m": mount_path, "r": managed_root,
                 "b": backend_type, "e": 1 if enabled else 0,
                 "u": 1 if user_enabled else 0, "now": now, "actor": actor, "k": kind})
            after = self.get(storage_name)
            self._audit("create", storage_name, None, after, actor)
        return after

    def update(self, storage_name, *, mount_path, managed_root, backend_type,
               enabled: bool, actor, user_enabled: bool | None = None, kind: str | None = None):
        """user_enabled=None 은 "건드리지 않음"(현재 값 유지) -- 필드를 모르는 옛 클라이언트가
        경로만 고치는 PUT 이 관리자 전용 스토리지를 조용히 사용자에게 다시 열지 않게 한다. kind=None 도 같다(유지).

        종류 전환(dms-k8s 설계 D10): StorageClass 매핑이 걸린 스토리지를 PV 를 담지 않는 종류(filesystem)로 바꾸면
        storage_has_sc_mappings -- 매핑이 「PV 를 담지 않는 스토리지」를 가리킨 채 매달리지 않게(해석은 그 매핑을 unmapped 로
        읽는다). 판정은 스토리지 행을 잠근 같은 트랜잭션에서 한다(repositories.k8s.set_sc_map 이 같은 행을 잠그고 종류를
        본다 -- 둘이 엇갈려 「filesystem 인데 매핑 있음」이 생기지 않게)."""
        _validate(storage_name, mount_path, managed_root, backend_type)
        if kind is not None:
            _validate_kind(kind)
        mount_path = posixpath.normpath(mount_path)
        managed_root = posixpath.normpath(managed_root)
        before = self.get(storage_name)
        if before is None:
            raise KeyError(storage_name)
        if user_enabled is None:
            user_flag = 0 if before.get("user_enabled") == 0 else 1
        else:
            user_flag = 1 if user_enabled else 0
        lock = " FOR UPDATE" if self._db.dialect == "postgresql" else ""
        with self._db.transaction():
            current = self._db.query_one(f"SELECT * FROM storages WHERE storage_name = :n{lock}",
                                         {"n": storage_name})
            if current is None:
                raise KeyError(storage_name)
            new_kind = kind if kind is not None else storage_kind(current)
            if new_kind not in PV_CAPABLE_KINDS and self._db.query_one(
                    "SELECT 1 AS x FROM k8s_storage_class_maps WHERE storage_name = :n", {"n": storage_name}):
                raise DomainValidationError("storage_has_sc_mappings", storage_name)
            self._db.execute(
                """UPDATE storages SET mount_path = :m, managed_root = :r,
                       backend_type = :b, enabled = :e, user_enabled = :u, kind = :k,
                       updated_at = :now, updated_by = :actor
                   WHERE storage_name = :n""",
                {"m": mount_path, "r": managed_root, "b": backend_type,
                 "e": 1 if enabled else 0, "u": user_flag, "k": new_kind, "now": utc_now_iso(),
                 "actor": actor, "n": storage_name})
            after = self.get(storage_name)
            self._audit("update", storage_name, before, after, actor)
        return after

    def delete(self, storage_name, actor):
        before = self.get(storage_name)
        if before is None:
            raise KeyError(storage_name)
        with self._db.transaction():
            # 사용자 sync 허용 쌍(repositories/sync_pairs.py)도 같은 트랜잭션에서 지운다 -- 같은
            # 이름으로 다시 등록된 스토리지가 옛 허용을 물려받지 않게. 트랜잭션이 중첩되지 않아
            # (db.transaction) SyncPairsRepository.remove 대신 여기서 직접 지우고 감사를 남긴다.
            pairs = self._db.query(
                """SELECT * FROM sync_pairs
                   WHERE source_storage = :n OR destination_storage = :n""", {"n": storage_name})
            self._db.execute(
                "DELETE FROM sync_pairs WHERE source_storage = :n OR destination_storage = :n",
                {"n": storage_name})
            for p in pairs:
                self._db.execute(
                    """INSERT INTO audit_log (mutation_class, operation, target_key, actor,
                           before_state, after_state, at)
                       VALUES ('sync_pair', 'remove', :key, :actor, :b, NULL, :at)""",
                    {"key": f"{p['source_storage']}->{p['destination_storage']}",
                     "actor": actor, "b": dump_json(p), "at": utc_now_iso()})
            # StorageClass → 스토리지 매핑(dms-k8s 설계 D10)도 같은 트랜잭션에서 지우고 감사를 남긴다 -- 같은 이름으로 다시
            # 등록한 스토리지가 옛 매핑을 조용히 물려받지 않게(sync_pairs 와 같은 이유).
            delete_maps_for_storage_in_txn(self._db, storage_name, actor)
            self._db.execute("DELETE FROM storages WHERE storage_name = :n",
                             {"n": storage_name})
            self._audit("delete", storage_name, before, None, actor)
        return before

    def get(self, storage_name):
        return self._db.query_one(
            "SELECT * FROM storages WHERE storage_name = :n", {"n": storage_name})

    def last_known_config(self, storage_name) -> "dict | None":
        """등록 목록에 없는 스토리지(삭제됨)의 마지막 설정(backend_type·mount_path·managed_root) -- 사용량 CSV 가 미등록
        스토리지 행의 그 열을 비우지 않게(2026-10-12 사용자 보고: 「두번째 행부터는 backend type, mount path 등이 누락」).
        잡·요청은 스토리지 설정을 스냅숏하지 않는다(절대경로는 stepper 가 단계마다 현재 행으로 짓는다) -- 그래서 원천은
        이 저장소가 생성·수정·삭제마다 남기는 감사 행이다(_audit: 행 전체 JSON). 가장 최근 행 하나: delete 면
        before_state, create·update 면 after_state. 감사 행이 없으면(감사 이전 시대·DB 직접 삽입) None = 모름.

        「마지막으로 알려진」 값이지 그 scan 이 돌던 때의 값이라는 보장은 없다(그 사이 수정됐을 수 있다) -- 호출자가 출처를
        함께 말한다. DB 가 신뢰 경계라 문자열 값만 싣는다(모양 투영)."""
        row = self._db.query_one(
            """SELECT operation, before_state, after_state FROM audit_log
               WHERE mutation_class = 'storage' AND target_key = :n
                     AND operation IN ('create', 'update', 'delete')
               ORDER BY id DESC LIMIT 1""", {"n": storage_name})
        if row is None:
            return None
        raw = row["before_state"] if row["operation"] == "delete" else row["after_state"]
        try:
            state = load_json(raw) if isinstance(raw, str) else None
        except (ValueError, TypeError, RecursionError):
            state = None
        if not isinstance(state, dict):
            return None
        out = {k: (state.get(k) if isinstance(state.get(k), str) else None)
               for k in ("backend_type", "mount_path", "managed_root")}
        return out if any(v is not None for v in out.values()) else None

    def list(self):
        return self._db.query("SELECT * FROM storages ORDER BY storage_name")

    def set_status(self, storage_name, status, detail=None):
        self._db.execute(
            """UPDATE storages SET status = :s, status_detail = :d, updated_at = :now
               WHERE storage_name = :n""",
            {"s": status, "d": detail, "now": utc_now_iso(), "n": storage_name})
