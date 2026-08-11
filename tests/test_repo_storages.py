import pytest
from dms.domain import DomainValidationError
from dms.repositories.storages import StoragesRepository

FIELDS = dict(storage_name="ceph-a", mount_path="/mnt/ceph",
              managed_root="/mnt/ceph/dms", backend_type="cephfs")


def test_create_get_list(db):
    repo = StoragesRepository(db)
    repo.create(**FIELDS, actor="admin")
    row = repo.get("ceph-a")
    assert row["managed_root"] == "/mnt/ceph/dms"
    assert row["enabled"] == 1 and row["status"] == "Unknown"
    assert [s["storage_name"] for s in repo.list()] == ["ceph-a"]


@pytest.mark.parametrize("bad", [
    {"managed_root": "/other/root"},          # mount_path 밖
    {"backend_type": "nfs"},                  # 미지원 백엔드
    {"mount_path": "relative/path"},          # 상대 경로
    {"storage_name": "Bad_Name"},             # 이름 규칙 위반
    {"mount_path": "/", "managed_root": "/"},  # 노드 루트(슬라이스 24 §2.2) -- 현행 ACCEPTED
    {"managed_root": "/"},                     # root "/"만 -- 기존에도 mount 밖 규칙으로 거부(명시 고정)
])
def test_invalid_fields_rejected(db, bad):
    repo = StoragesRepository(db)
    with pytest.raises(DomainValidationError) as e:
        repo.create(**{**FIELDS, **bad}, actor="admin")
    assert e.value.reason_code == "invalid_storage"


def test_root_filesystem_rejection_is_explicit(db):
    # "/" 는 일반 경로 규칙(normpath("/") == "/" != "/".rstrip("/") == "")에도 우연히
    # 걸린다 -- 그 우연에 기대면 규칙이 언젠가 느슨해질 때 노드 루트가 조용히 다시
    # 통과한다. detail 까지 고정해 명시 분기(슬라이스 24 §2.2)가 지워지면 빨개지게.
    repo = StoragesRepository(db)
    with pytest.raises(DomainValidationError) as e:
        repo.create(**{**FIELDS, "mount_path": "/", "managed_root": "/"}, actor="admin")
    assert e.value.reason_code == "invalid_storage"
    assert e.value.detail == "root filesystem is not a storage"


def test_paths_are_stored_normalized(db):
    repo = StoragesRepository(db)
    repo.create(storage_name="ceph-b", mount_path="/mnt/ceph/",
                managed_root="/mnt/ceph/dms/", backend_type="cephfs", actor="admin")
    row = repo.get("ceph-b")
    assert row["mount_path"] == "/mnt/ceph"
    assert row["managed_root"] == "/mnt/ceph/dms"


def test_duplicate_create_raises_domain_error(db):
    repo = StoragesRepository(db)
    repo.create(**FIELDS, actor="admin")
    with pytest.raises(DomainValidationError) as e:
        repo.create(**FIELDS, actor="admin")
    assert e.value.reason_code == "storage_exists"


def test_update_and_delete_are_audited(db):
    repo = StoragesRepository(db)
    repo.create(**FIELDS, actor="admin")
    repo.update("ceph-a", mount_path="/mnt/ceph", managed_root="/mnt/ceph/dms2",
                backend_type="cephfs", enabled=False, actor="admin")
    assert repo.get("ceph-a")["enabled"] == 0
    deleted = repo.delete("ceph-a", actor="admin")
    assert deleted["storage_name"] == "ceph-a"
    assert repo.get("ceph-a") is None
    audit = db.query("SELECT operation, before_state, after_state FROM audit_log "
                     "WHERE mutation_class = 'storage' ORDER BY id")
    assert [a["operation"] for a in audit] == ["create", "update", "delete"]
    assert audit[0]["before_state"] is None
    assert audit[2]["after_state"] is None
