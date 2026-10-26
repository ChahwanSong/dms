"""스토리지 기본 소유 uid·gid(2026-10-26 사용자 지시 「각 k8s 스토리지 항목마다 기본 uid,gid 값을 같이 등록 -- 기본 root:root」):
등록 기본값 0:0, 검증(정수·bool 아님·0..4294967294 → 아니면 invalid_storage_owner), 수정 PUT 의 생략 = 유지(옛 클라이언트·목록 화면의
사용 범위 PUT), 응답 정규화(NULL = 0, default_owner "uid:gid"), 감사 행, 읽기 헬퍼 storage_default_owner 의 fail-closed(변조 값을
0 = root 로 뭉개지 않는다), PV 해석(resolve_volume)의 storage_owner 가 볼륨 API·SC 화면까지 흐르는지."""
import json
from pathlib import Path

import pytest

from dms.agent.k8s_fetch import project_pv, project_pvc, project_role_binding, project_storage_class
from dms.db import iso_plus, load_json
from dms.domain import DomainValidationError
from dms.k8s_sync import normalize_snapshot
from dms.repositories import Repositories
from dms.repositories.k8s import OWNER_ID_MAX, resolve_volume, storage_default_owner

ADMIN = {"Authorization": "Bearer tok-shared"}
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "k8s"
T0 = "2026-10-25T12:00:00Z"
MAX = 4294967294


def _body(name="pvs", **kw):
    return {"storage_name": name, "mount_path": f"/k8s_pv/{name}", "managed_root": f"/k8s_pv/{name}",
            "backend_type": "nfs", "kind": "pv", **kw}


def _put(client, name="pvs", **kw):
    return client.put(f"/api/admin/storages/{name}", json={"mount_path": f"/k8s_pv/{name}",
                                                          "managed_root": f"/k8s_pv/{name}",
                                                          "backend_type": "nfs", "enabled": True, **kw},
                      headers=ADMIN)


# --- 읽기 헬퍼 ------------------------------------------------------------------------------------

def test_owner_id_max_matches_the_execution_chown_check():
    # 저장 검증과 실행의 --chown 검사(valid_owner)가 같은 상한이어야 등록에서 통과한 값이 실행에서 거부되지 않는다.
    from dms.execution_manifests import _OWNER_ID_MAX, valid_owner
    assert OWNER_ID_MAX == _OWNER_ID_MAX == MAX
    assert valid_owner(storage_default_owner({"default_uid": MAX, "default_gid": MAX}))
    assert valid_owner(storage_default_owner({"default_uid": 0, "default_gid": 0}))


@pytest.mark.parametrize("row, owner", [
    ({"default_uid": 0, "default_gid": 0}, "0:0"),
    ({"default_uid": 1500, "default_gid": 1501}, "1500:1501"),
    ({"default_uid": MAX, "default_gid": 0}, f"{MAX}:0"),
    ({"default_uid": None, "default_gid": None}, "0:0"),          # 기배포 ALTER 직후(백필 전) = 사용자 기본값 root
    ({}, "0:0"),                                                  # 칸 이전 행(SELECT * 에 칸 자체가 없다)
    ({"default_uid": 1500, "default_gid": None}, "1500:0"),       # 칸마다 따로
])
def test_storage_default_owner_reads_valid_rows(row, owner):
    assert storage_default_owner({"storage_name": "s", **row}) == owner


@pytest.mark.parametrize("uid, gid", [
    (-1, 0), (0, -1), (MAX + 1, 0), (0, MAX + 1), (2**63, 0),
    (True, 0), (0, False),                                        # bool 은 int 의 하위형이지만 uid 가 아니다
    ("1500", 0), (0, "0"), (1.0, 0), (0, 1.5), ([1], 0), ({"x": 1}, 0),
])
def test_storage_default_owner_fails_closed_on_tampered_values(uid, gid):
    # DB 가 신뢰 경계다 -- 변조 값을 0(root)으로 뭉개지 않고 None(무효)으로 돌려 제출·재확인이 거부하게 한다.
    assert storage_default_owner({"storage_name": "s", "default_uid": uid, "default_gid": gid}) is None


def test_storage_default_owner_of_no_row_is_unknown():
    assert storage_default_owner(None) is None


# --- 저장소 -----------------------------------------------------------------------------------------

def test_repo_create_defaults_to_root_and_validates(db):
    repos = Repositories(db)
    row = repos.storages.create(storage_name="s1", mount_path="/m1", managed_root="/m1", backend_type="nfs",
                                actor="ops", kind="pv")
    assert (row["default_uid"], row["default_gid"]) == (0, 0)
    row = repos.storages.create(storage_name="s2", mount_path="/m2", managed_root="/m2", backend_type="nfs",
                                actor="ops", kind="pv", default_uid=MAX, default_gid=1500)
    assert (row["default_uid"], row["default_gid"]) == (MAX, 1500)          # BIGINT -- int4 면 PG 에서 22003
    assert storage_default_owner(repos.storages.get("s2")) == f"{MAX}:1500"
    for bad in (-1, MAX + 1, True, False, "1500", 1.0, None):
        for field in ("default_uid", "default_gid"):
            with pytest.raises(DomainValidationError) as e:
                repos.storages.create(storage_name="s3", mount_path="/m3", managed_root="/m3", backend_type="nfs",
                                      actor="ops", kind="pv", **{field: bad})
            assert e.value.reason_code == "invalid_storage_owner", (field, bad)
    assert repos.storages.get("s3") is None


def test_repo_update_none_keeps_each_field_and_audits(db):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/m1", managed_root="/m1", backend_type="nfs",
                          actor="ops", kind="pv", default_uid=1500, default_gid=1501)
    base = dict(mount_path="/m1", managed_root="/m1", backend_type="nfs", enabled=True, actor="ops")
    row = repos.storages.update("s1", **base)                                   # 필드를 모르는 호출 = 유지
    assert (row["default_uid"], row["default_gid"]) == (1500, 1501)
    row = repos.storages.update("s1", **base, default_gid=0)                    # 칸마다 따로(0 도 정한 값)
    assert (row["default_uid"], row["default_gid"]) == (1500, 0)
    row = repos.storages.update("s1", **base, kind="filesystem")               # 종류와 무관하게 보존(되돌리면 살아난다)
    assert (row["default_uid"], row["default_gid"]) == (1500, 0)
    for bad in (-5, MAX + 1, True, "7"):
        with pytest.raises(DomainValidationError) as e:
            repos.storages.update("s1", **base, default_uid=bad)
        assert e.value.reason_code == "invalid_storage_owner", bad
    assert (repos.storages.get("s1")["default_uid"], repos.storages.get("s1")["default_gid"]) == (1500, 0)
    audit = db.query("""SELECT operation, before_state, after_state FROM audit_log
                        WHERE mutation_class = 'storage' AND target_key = 's1' ORDER BY id""")
    assert [a["operation"] for a in audit] == ["create", "update", "update", "update"]
    created = load_json(audit[0]["after_state"])
    assert (created["default_uid"], created["default_gid"]) == (1500, 1501)
    changed = audit[2]
    assert load_json(changed["before_state"])["default_gid"] == 1501 and load_json(changed["after_state"])[
        "default_gid"] == 0


# --- API --------------------------------------------------------------------------------------------

def test_api_create_defaults_and_echoes_the_owner(client):
    r = client.post("/api/admin/storages", json=_body("pvs"), headers=ADMIN)
    assert r.status_code == 201
    assert (r.json()["default_uid"], r.json()["default_gid"], r.json()["default_owner"]) == (0, 0, "0:0")
    r = client.post("/api/admin/storages", json=_body("big", default_uid=MAX, default_gid=1500), headers=ADMIN)
    assert r.status_code == 201 and r.json()["default_owner"] == f"{MAX}:1500"
    got = {s["storage_name"]: s["default_owner"] for s in client.get("/api/admin/storages", headers=ADMIN).json()}
    assert got == {"pvs": "0:0", "big": f"{MAX}:1500"}


@pytest.mark.parametrize("bad", [-1, MAX + 1, 2**63])
def test_api_out_of_range_is_invalid_storage_owner(client, bad):
    for field in ("default_uid", "default_gid"):
        r = client.post("/api/admin/storages", json=_body("pvs", **{field: bad}), headers=ADMIN)
        assert r.status_code == 422 and r.json()["detail"] == "invalid_storage_owner", (field, bad)
    assert client.get("/api/admin/storages", headers=ADMIN).json() == []


@pytest.mark.parametrize("bad", [True, "1500", 1.5, None, [0]])
def test_api_non_integer_owner_is_rejected_not_coerced(client, bad):
    # StrictInt: "1500"·true 를 정수로 바꾸지 않는다(true → uid 1 은 의도와 다른 소유자다). 형 오류는 FastAPI 의 422 목록이다.
    r = client.post("/api/admin/storages", json=_body("pvs", default_uid=bad), headers=ADMIN)
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)
    assert client.get("/api/admin/storages", headers=ADMIN).json() == []


def test_api_put_omission_keeps_values_and_bad_values_change_nothing(client):
    client.post("/api/admin/storages", json=_body("pvs", default_uid=1500, default_gid=1501), headers=ADMIN)
    # 목록 화면의 사용 범위 PUT·옛 클라이언트는 소유 칸을 보내지 않는다 -- 유지.
    r = _put(client, user_enabled=False)
    assert r.status_code == 200 and (r.json()["default_uid"], r.json()["default_gid"]) == (1500, 1501)
    r = _put(client, default_uid=2000)
    assert r.status_code == 200 and r.json()["default_owner"] == "2000:1501"
    r = _put(client, default_uid=None, default_gid=0)                          # null = 유지
    assert r.status_code == 200 and r.json()["default_owner"] == "2000:0"
    r = _put(client, default_gid=-1)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_storage_owner"
    r = _put(client, default_uid=True)
    assert r.status_code == 422 and isinstance(r.json()["detail"], list)
    assert client.app.state.repos.storages.get("pvs")["default_uid"] == 2000
    assert client.app.state.repos.storages.get("pvs")["default_gid"] == 0


def test_api_list_normalizes_legacy_null_and_flags_tampered_rows(client, db):
    # 기배포 DB 의 ALTER 경로 모양(NULL 허용 BIGINT 칸 -- 백필 전 창)을 그대로 재현한다(신규 DB 는 NOT NULL DEFAULT 0 이다).
    db.execute("DROP TABLE storages")
    db.execute("""CREATE TABLE storages (storage_name TEXT PRIMARY KEY, mount_path TEXT NOT NULL,
        managed_root TEXT NOT NULL, backend_type TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
        user_enabled INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'Unknown', status_detail TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by TEXT NOT NULL, kind TEXT,
        default_uid BIGINT, default_gid BIGINT)""")
    for name, uid, gid in (("legacy", None, None), ("bad", -1, 1500), ("big", 0, MAX + 1)):
        db.execute("""INSERT INTO storages (storage_name, mount_path, managed_root, backend_type, created_at,
            updated_at, updated_by, kind, default_uid, default_gid)
            VALUES (:n, '/m', '/m', 'nfs', 't', 't', 'x', 'pv', :u, :g)""", {"n": name, "u": uid, "g": gid})
    rows = {r["storage_name"]: r for r in client.get("/api/admin/storages", headers=ADMIN).json()}
    assert (rows["legacy"]["default_uid"], rows["legacy"]["default_gid"], rows["legacy"]["default_owner"]) == (
        0, 0, "0:0")
    # 범위 밖 정수는 그대로 보이고(무엇이 틀렸는지) default_owner 는 None(무효) -- 0 으로 뭉개지 않는다.
    assert (rows["bad"]["default_uid"], rows["bad"]["default_owner"]) == (-1, None)
    assert (rows["big"]["default_gid"], rows["big"]["default_owner"]) == (MAX + 1, None)
    # 소유 칸을 보내지 않는 PUT 은 무효 저장값을 고쳐 쓰지 않는다(관리자가 새 값을 줄 때까지 드러난다).
    r = client.put("/api/admin/storages/bad", json={"mount_path": "/m", "managed_root": "/m", "backend_type": "nfs",
                                                    "enabled": True}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["default_owner"] is None
    r = client.put("/api/admin/storages/bad", json={"mount_path": "/m", "managed_root": "/m", "backend_type": "nfs",
                                                    "enabled": True, "default_uid": 7}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["default_owner"] == "7:1500"


# --- PV 해석 -----------------------------------------------------------------------------------------

def _pvc(**kw):
    return {"cluster_name": "c1", "namespace": "team-a", "name": "data", "volume_name": "pvc-1", "phase": "Bound",
            "storage_class": "sc1", **kw}


def _storage(**kw):
    return {"storage_name": "s", "kind": "pv", "backend_type": "nfs", "managed_root": "/k8s_pv/nfs",
            "default_uid": 1500, "default_gid": 1501, **kw}


MAP = {"storage_name": "s", "path_template": "{namespace}/{pvc}"}


def test_resolve_volume_carries_the_storage_owner():
    r = resolve_volume(_pvc(), None, MAP, _storage())
    assert r["selectable"] and r["storage_owner"] == "1500:1501"
    assert resolve_volume(_pvc(), None, MAP, _storage(kind="both"))["storage_owner"] == "1500:1501"
    assert resolve_volume(_pvc(), None, MAP, _storage(default_uid=None, default_gid=None))["storage_owner"] == "0:0"
    # 모름·PV 를 담지 않는 스토리지는 None(목적지 소유자가 없다)
    assert resolve_volume(_pvc(phase="Pending"), None, MAP, _storage())["storage_owner"] is None
    assert resolve_volume(_pvc(), None, None, _storage())["storage_owner"] is None
    assert resolve_volume(_pvc(), None, MAP, None)["storage_owner"] is None
    assert resolve_volume(_pvc(), None, MAP, _storage(kind="filesystem"))["storage_owner"] is None
    # 변조 값: 고르기는 막지 않는다(원본으로는 쓸 수 있다) -- 소유자만 None(목적지 제출이 409 로 거부한다)
    tampered = resolve_volume(_pvc(), None, MAP, _storage(default_gid=True))
    assert tampered["selectable"] is True and tampered["storage_owner"] is None
    # 경로를 못 풀어도 스토리지 소유자는 안다(PV 를 담는 스토리지에 매핑됐다)
    unresolved = resolve_volume(_pvc(), None, {"storage_name": "s", "path_template": None},
                                _storage(backend_type="cephfs"))
    assert unresolved["reason"] == "pv_path_unresolved" and unresolved["storage_owner"] == "1500:1501"


def _items(name, project):
    return [project(i) for i in json.loads((FIXTURES / name).read_text())["items"]]


def _synced_repos(db):
    repos = Repositories(db)
    repos.k8s.update_settings(primary_node="k8s-w1", secondary_node="k8s-w2", interval_seconds=300, actor="ops")
    repos.k8s.create_cluster(name="c1", api_server="https://10.10.10.20:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:sealed", enabled=True, actor="ops")
    db.execute("UPDATE k8s_clusters SET created_at = :t", {"t": iso_plus(T0, -60)})
    got = repos.k8s.claim(node_name="k8s-w1", role="primary", now=T0, interval=300, primary_configured=True,
                          token_ok=lambda _enc: True, deadline_seconds=120)
    data = normalize_snapshot({
        "storage_classes": _items("synthetic-storageclasses.json", project_storage_class),
        "pvcs": _items("synthetic-pvcs.json", project_pvc),
        "pvs": _items("synthetic-pvs.json", project_pv),
        "role_bindings": _items("synthetic-rolebindings.json", project_role_binding)})
    run_id = got["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=T0, ok=True, data=data, fingerprint="ks-uid",
                      server_version="v1.34.6", duration_ms=1200)
    assert repos.k8s.apply_run(run_id, now=T0)["outcome"] == "applied"
    return repos


def _session(client, repos, username, role):
    if repos.accounts.get(username) is None:
        repos.accounts.create(username, "pw", role)
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"username": username, "password": "pw"}).status_code == 200
    return client


def test_volume_apis_and_storage_class_view_carry_the_storage_owner(client, db):
    repos = _synced_repos(db)
    client.post("/api/admin/storages", json=_body("k8s-pv-nfs", default_uid=1500, default_gid=1501), headers=ADMIN)
    client.post("/api/admin/storages", json={**_body("k8s-pv-gpfs", kind="both"), "backend_type": "gpfs"},
                headers=ADMIN)                                                  # 생략 = 0:0
    repos.k8s.set_sc_map("tb-nfs", storage_name="k8s-pv-nfs", path_template=None, actor="ops")
    repos.k8s.set_sc_map("tb-gpfs", storage_name="k8s-pv-gpfs", path_template=None, actor="ops")
    volumes = client.get("/api/admin/k8s/volumes", headers=ADMIN).json()["volumes"]
    rows = {(x["namespace"], x["pvc"]): x for x in volumes}
    assert rows[("team-a", "datasets")]["storage_owner"] == "1500:1501"
    assert rows[("team-a", "models")]["storage_owner"] == "0:0"
    assert rows[("team-a", "kraken-kv-cache")]["storage_owner"] is None          # tb-onefs 매핑 없음
    sc = {r["name"]: r for r in client.get("/api/admin/k8s/storage-classes", headers=ADMIN).json() if r["cluster"]}
    assert (sc["tb-nfs"]["storage_owner"], sc["tb-gpfs"]["storage_owner"], sc["tb-onefs"]["storage_owner"]) == (
        "1500:1501", "0:0", None)
    # 일반 사용자의 PV 고르기에도 실린다(결과 소유 안내의 근거) -- 제출 해석(resolve_pvc)도 같은 함수다.
    _session(client, repos, "alice", "user")
    mine = {(x["namespace"], x["pvc"]): x for x in client.get("/api/user/k8s/volumes").json()["volumes"]}
    assert mine[("team-a", "datasets")]["storage_owner"] == "1500:1501"
    assert repos.k8s.resolve_pvc("c1", "team-a", "datasets")["storage_owner"] == "1500:1501"
    # 관리자가 기본 소유자를 바꾸면 해석이 곧바로 따른다(재확인이 비교하는 「현재 값」)
    db.execute("UPDATE storages SET default_uid = 2000 WHERE storage_name = 'k8s-pv-nfs'")
    assert repos.k8s.resolve_pvc("c1", "team-a", "datasets")["storage_owner"] == "2000:1501"
    db.execute(f"UPDATE storages SET default_gid = {MAX + 1} WHERE storage_name = 'k8s-pv-nfs'")
    tampered = repos.k8s.resolve_pvc("c1", "team-a", "datasets")
    assert tampered["selectable"] is True and tampered["storage_owner"] is None
