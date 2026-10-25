"""스토리지 종류(storages.kind, dms-k8s 설계 D10·D11, 2026-10-25): 등록·수정·목록 응답·종류 집합 검증, StorageClass 매핑이 걸린
스토리지의 filesystem 전환 거부(storage_has_sc_mappings), 스토리지 삭제 시 같은 트랜잭션의 매핑 정리 + 감사(sync_pairs 선례 --
같은 이름으로 다시 등록한 스토리지가 옛 매핑을 물려받지 않게)."""
import pytest

from dms.domain import DomainValidationError
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared"}


def _body(name="pvs", **kw):
    return {"storage_name": name, "mount_path": f"/k8s_pv/{name}", "managed_root": f"/k8s_pv/{name}",
            "backend_type": "nfs", **kw}


def _put(client, name="pvs", **kw):
    return client.put(f"/api/admin/storages/{name}", json={"mount_path": f"/k8s_pv/{name}",
                                                          "managed_root": f"/k8s_pv/{name}",
                                                          "backend_type": "nfs", "enabled": True, **kw},
                      headers=ADMIN)


def test_kind_defaults_to_filesystem_and_is_validated(client):
    r = client.post("/api/admin/storages", json=_body("plain"), headers=ADMIN)
    assert r.status_code == 201 and r.json()["kind"] == "filesystem"
    r = client.post("/api/admin/storages", json=_body("pvs", kind="pv"), headers=ADMIN)
    assert r.status_code == 201 and r.json()["kind"] == "pv"
    for bad in ("PV", "block", ""):
        r = client.post("/api/admin/storages", json=_body("x1", kind=bad), headers=ADMIN)
        assert r.status_code == 422 and r.json()["detail"] == "invalid_storage_kind", bad
    r = _put(client, kind="nope")
    assert r.status_code == 422 and r.json()["detail"] == "invalid_storage_kind"


def test_lists_carry_a_normalized_kind(client, db):
    client.post("/api/admin/storages", json=_body("pvs", kind="both"), headers=ADMIN)
    client.post("/api/admin/storages", json=_body("legacy"), headers=ADMIN)
    db.execute("UPDATE storages SET kind = NULL WHERE storage_name = 'legacy'")      # 컬럼 이전 행
    rows = {r["storage_name"]: r["kind"] for r in client.get("/api/admin/storages", headers=ADMIN).json()}
    assert rows == {"pvs": "both", "legacy": "filesystem"}
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    user_rows = {r["storage_name"]: r["kind"] for r in client.get("/api/user/storages").json()}
    assert user_rows == {"pvs": "both", "legacy": "filesystem"}


def test_put_without_kind_keeps_it(client):
    client.post("/api/admin/storages", json=_body("pvs", kind="pv"), headers=ADMIN)
    r = _put(client)                                          # 필드를 모르는 옛 클라이언트
    assert r.status_code == 200 and r.json()["kind"] == "pv"
    r = _put(client, kind="both")
    assert r.status_code == 200 and r.json()["kind"] == "both"


def test_mapped_storage_cannot_become_filesystem(client):
    repos = client.app.state.repos
    client.post("/api/admin/storages", json=_body("pvs", kind="pv"), headers=ADMIN)
    repos.k8s.set_sc_map("tb-nfs", storage_name="pvs", path_template=None, actor="ops")
    r = _put(client, kind="filesystem")
    assert r.status_code == 422 and r.json()["detail"] == "storage_has_sc_mappings"
    assert repos.storages.get("pvs")["kind"] == "pv"
    assert _put(client, kind="both").status_code == 200       # PV 를 담는 종류끼리는 자유
    repos.k8s.delete_sc_map("tb-nfs", actor="ops")
    assert _put(client, kind="filesystem").json()["kind"] == "filesystem"


def test_filesystem_storage_cannot_be_a_mapping_target(db):
    repos = Repositories(db)
    repos.storages.create(storage_name="fs", mount_path="/fs", managed_root="/fs", backend_type="cephfs", actor="o")
    with pytest.raises(DomainValidationError) as e:
        repos.k8s.set_sc_map("tb-nfs", storage_name="fs", path_template=None, actor="ops")
    assert e.value.reason_code == "storage_not_pv_capable"


def test_deleting_a_storage_removes_its_mappings_in_the_same_transaction(client, db):
    repos = client.app.state.repos
    client.post("/api/admin/storages", json=_body("pvs", kind="pv"), headers=ADMIN)
    client.post("/api/admin/storages", json=_body("other", kind="pv"), headers=ADMIN)
    repos.k8s.set_sc_map("tb-nfs", storage_name="pvs", path_template="{subdir}", actor="ops")
    repos.k8s.set_sc_map("tb-gpfs", storage_name="pvs", path_template=None, actor="ops")
    repos.k8s.set_sc_map("tb-onefs", storage_name="other", path_template=None, actor="ops")
    assert client.delete("/api/admin/storages/pvs", headers=ADMIN).status_code == 200
    assert sorted(repos.k8s.sc_maps()) == ["tb-onefs"]
    audit = db.query("""SELECT operation, target_key FROM audit_log WHERE mutation_class = 'k8s_storage_class_map'
                        AND operation = 'remove' ORDER BY target_key""")
    assert [(a["operation"], a["target_key"]) for a in audit] == [("remove", "tb-gpfs"), ("remove", "tb-nfs")]
    # 같은 이름으로 다시 등록해도 옛 매핑을 물려받지 않는다
    client.post("/api/admin/storages", json=_body("pvs", kind="pv"), headers=ADMIN)
    assert repos.k8s.maps_for_storage("pvs") == []
