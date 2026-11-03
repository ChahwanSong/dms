"""스토리지 등록 가드 -- DMS 상태 디렉터리와 겹치는 경로 거부(2026-11-03 ③, 설계 v6 3-1·9 「③ 공유 FS 세부」).

DMS 상태(<SHARED_DIR>/dms -- 아티팩트·PG·레지스트리·백업)는 공용 스토리지 위에 있고, 잡 파드는 스토리지 mount_path 를 root 로
hostPath 마운트한다. 그래서 mount_path·managed_root 가 상태 디렉터리 자신·그 아래·그 상위(SHARED_DIR 포함)면 422
storage_overlaps_dms_state. 상태 디렉터리는 아티팩트 base 가 …/dms/artifacts 꼴이면 그 부모(<SHARED_DIR>/dms), 아니면 base 자신
(배치 규약 밖의 base -- e2e 하네스 <tmp>/artifacts, 옛 DB base -- 의 형제를 막지 않게, 2026-11-03 리뷰)이고, env 값과 DB(포탈 저장)
값 둘 다에서 낸다. 경로를 바꾸지 않는 갱신(비활성화 등 비상 조작)은 가드 이전 행에도 막지 않는다 -- 대신 그런 행은 아티팩트 경로
화면(GET /api/admin/artifact-base 의 dms_state)이 경고로 싣는다. 선두 '//' 표기는 형식 오류(invalid_storage)다.
"""
import dataclasses

import pytest
from dms.api.app import create_app
from dms.artifact_base import dms_state_roots, overlaps_dms_state, state_root_of
from fastapi.testclient import TestClient

ADMIN = {"Authorization": "Bearer tok-shared"}


@pytest.fixture
def shared_client(db, settings):
    st = dataclasses.replace(settings, artifact_base_uri="file:///dms_shared/dms/artifacts")
    return TestClient(create_app(st, db)), db


def _body(name, mount, root=None):
    return {"storage_name": name, "mount_path": mount, "managed_root": root or mount, "backend_type": "nfs"}


# --- 순수 함수 -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("uri,root", [
    ("file:///dms_shared/dms/artifacts", "/dms_shared/dms"),
    ("/srv/x/dms/artifacts/", "/srv/x/dms"),
    ("file:///cephfs/dms/artifacts", "/cephfs/dms"),      # 옛 테스트베드 배치도 같은 규칙
    ("file:///dms/artifacts", "/dms"),
    ("file:////dms_shared/dms/artifacts", "/dms_shared/dms"),   # 선두 '//' 는 '/' 로 접는다
    # 배치 규약(…/dms/artifacts) 밖의 base 는 base 자신만 지킨다 -- 부모를 지키면 형제가 통째로 막힌다.
    ("file:///artifacts", "/artifacts"),
    ("file:///tmp/dms-e2e-abc123/artifacts", "/tmp/dms-e2e-abc123/artifacts"),   # e2e 하네스(global-setup.ts)
    ("file:///cephfs/scratch-art", "/cephfs/scratch-art"),                       # 옛 allowlist 시절의 DB base
    ("file:///artifacts/dms", "/artifacts/dms"),                                 # 코드 기본값(config) 모양
    ("file:///", None), ("relative/x", None), ("", None), (None, None),
])
def test_state_root_is_the_parent_only_for_the_dms_artifacts_layout(uri, root):
    assert state_root_of(uri) == root


@pytest.mark.parametrize("path,hit", [
    ("/dms_shared/dms", True),                 # 같음
    ("/dms_shared/dms/artifacts", True),       # 아래
    ("/dms_shared/dms/pgdata/x", True),
    ("/dms_shared", True),                     # 덮음(SHARED_DIR 자신)
    ("/dms_shared/", True),
    ("/dms_shared/other", False),              # SHARED_DIR 의 다른 하위는 상태가 아니다
    ("/dms_shared/dms2", False),               # 성분 경계(접두 문자열이 아니다)
    ("/dms_shared2/dms", False),
    ("/cephfs", False), ("relative", False), (None, False),
    ("//dms_shared", True), ("//dms_shared/dms", True),   # 선두 '//' 우회 금지(리눅스에서는 '/' 와 같다)
])
def test_overlap_is_equal_under_or_covering_by_path_component(path, hit):
    assert overlaps_dms_state(path, ("/dms_shared/dms",)) is hit


def test_roots_come_from_env_and_effective_db_base(db, settings):
    from dms.repositories import Repositories
    repos = Repositories(db)
    st = dataclasses.replace(settings, artifact_base_uri="file:///dms_shared/dms/artifacts")
    assert dms_state_roots(repos.control, st) == ("/dms_shared/dms",)
    repos.control.set_artifact_base("file:///cephfs/dms/artifacts", actor="ops")
    assert dms_state_roots(repos.control, st) == ("/cephfs/dms", "/dms_shared/dms")


# --- API -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mount,root", [
    ("/dms_shared", "/dms_shared/data"),            # mount_path 가 덮음(SHARED_DIR 등록)
    ("/dms_shared/dms", None),                      # 상태 디렉터리 자신
    ("/dms_shared/dms/artifacts", None),            # 그 아래
    ("/dms_shared", "/dms_shared/dms/registry"),    # 관리 루트가 그 아래
])
def test_create_refuses_storage_overlapping_the_state_dir(shared_client, mount, root):
    client, _ = shared_client
    r = client.post("/api/admin/storages", json=_body("s1", mount, root), headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "storage_overlaps_dms_state", r.json()
    assert client.get("/api/admin/storages", headers=ADMIN).json() == []


def test_create_allows_siblings_and_other_filesystems(shared_client):
    client, _ = shared_client
    for i, mount in enumerate(("/dms_shared/data", "/dms_shared/dms2", "/cephfs", "/k8s_pv")):
        r = client.post("/api/admin/storages", json=_body(f"s{i}", mount), headers=ADMIN)
        assert r.status_code == 201, (mount, r.json())


@pytest.mark.parametrize("mount", ["/", "//dms_shared", "//dms_shared/dms", "//data", "//dms-artifact-base"])
def test_malformed_paths_still_report_invalid_storage_first(shared_client, mount):
    # 선두 '//' 는 posixpath.normpath 가 보존해 정규형 검사를 통과하던 표기다 -- 문자열 가드 우회 통로라 형식 오류로 막는다.
    client, _ = shared_client
    r = client.post("/api/admin/storages", json=_body("s1", mount), headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_storage", (mount, r.json())


def test_e2e_harness_layout_still_registers_its_sibling_storage(db, settings):
    # frontend/e2e/harness/global-setup.ts: base = <tmp>/artifacts, 스토리지 = <tmp>/storage 를 201 로 기대한다. base 가
    # …/dms/artifacts 꼴이 아니므로 base 자신만 지킨다 -- 형제는 막지 않고, base 자신·그 아래는 여전히 막는다.
    st = dataclasses.replace(settings, artifact_base_uri="file:///tmp/dms-e2e-abc123/artifacts")
    client = TestClient(create_app(st, db))
    r = client.post("/api/admin/storages", json=_body("e2e-ceph", "/tmp/dms-e2e-abc123/storage"), headers=ADMIN)
    assert r.status_code == 201, r.json()
    for bad in ("/tmp/dms-e2e-abc123/artifacts", "/tmp/dms-e2e-abc123"):
        r = client.post("/api/admin/storages", json=_body("e2e-bad", bad), headers=ADMIN)
        assert r.json()["detail"] == "storage_overlaps_dms_state", (bad, r.json())


def test_artifact_base_screen_lists_state_roots_and_preexisting_overlapping_storages(shared_client):
    # 가드는 생성·경로 변경에만 걸린다 -- 그 전에 있던(또는 SHARED_DIR 이행으로 새로 겹치게 된) 행은 이 목록이 드러낸다.
    client, db = shared_client
    from dms.repositories import Repositories
    repos = Repositories(db)
    repos.storages.create(storage_name="legacy", mount_path="/dms_shared", managed_root="/dms_shared/m",
                          backend_type="nfs", actor="seed")
    repos.storages.create(storage_name="fine", mount_path="/dms_shared/data", managed_root="/dms_shared/data",
                          backend_type="nfs", actor="seed")
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["dms_state"] == {
        "roots": ["/dms_shared/dms"],
        "overlapping_storages": [{"storage_name": "legacy", "mount_path": "/dms_shared",
                                  "managed_root": "/dms_shared/m", "enabled": True}]}


def test_db_base_is_guarded_too(shared_client):
    client, db = shared_client
    from dms.repositories import Repositories
    Repositories(db).control.set_artifact_base("file:///cephfs/dms/artifacts", actor="ops")
    r = client.post("/api/admin/storages", json=_body("s1", "/cephfs", "/cephfs/managed"), headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "storage_overlaps_dms_state"


def test_update_guards_path_changes_but_not_toggles_of_a_preexisting_row(shared_client):
    client, db = shared_client
    from dms.repositories import Repositories
    # 가드 이전에 등록된 겹치는 행(저장소 직접 -- 보호 경로를 주지 않으면 검사하지 않는다).
    Repositories(db).storages.create(storage_name="legacy", mount_path="/dms_shared", managed_root="/dms_shared/m",
                                     backend_type="nfs", actor="seed")
    # 경로를 그대로 둔 비활성화(비상 차단)는 통과.
    r = client.put("/api/admin/storages/legacy", json={"mount_path": "/dms_shared", "managed_root": "/dms_shared/m",
                                                       "backend_type": "nfs", "enabled": False}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["enabled"] == 0
    # 경로를 바꾸면 가드가 걸린다(여전히 겹친다).
    r = client.put("/api/admin/storages/legacy", json={"mount_path": "/dms_shared", "managed_root": "/dms_shared/n",
                                                       "backend_type": "nfs", "enabled": False}, headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "storage_overlaps_dms_state"
    # 겹치지 않는 경로로 옮기면 통과.
    r = client.put("/api/admin/storages/legacy", json={"mount_path": "/dms_shared/data", "managed_root": "/dms_shared/data",
                                                       "backend_type": "nfs", "enabled": True}, headers=ADMIN)
    assert r.status_code == 200, r.json()
