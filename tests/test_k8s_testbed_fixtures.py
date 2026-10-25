"""dmsk8s 테스트베드 실데이터(kubectl --context dmsk8s get --raw 로 받은 목록, 토큰 없음 -- tests/fixtures/k8s/testbed-*.json)로
투영(§4.1)·맵핑(D5)·경로 프리셋(D9)·적용·권한 조회를 끝에서 끝으로 고정한다. 설계 §8 의 픽스처 구성: SC tb-nfs·tb-gpfs·tb-onefs
(csi-driver-nfs, 호스트 export /data/k8s_pv → 워커 /k8s_pv), namespace team-a·b·c, 인식 바인딩 5 + 대조군."""
import json
import posixpath
from pathlib import Path

import httpx

from dms.agent.k8s_fetch import (LISTS, fetch_inventory, project_pv, project_pvc, project_role_binding,
                                 project_storage_class)
from dms.k8s_rbac import derive_binding
from dms.k8s_sync import normalize_snapshot
from dms.pv_paths import PRESETS, render
from dms.repositories import Repositories

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "k8s"
_FILES = {"storage_classes": "testbed-storageclasses.json", "pvcs": "testbed-pvcs.json", "pvs": "testbed-pvs.json",
          "role_bindings": "testbed-rolebindings.json"}
_PROJECT = {"storage_classes": project_storage_class, "pvcs": project_pvc, "pvs": project_pv,
            "role_bindings": project_role_binding}
# 테스트베드 스토리지 등록(설계 §8): 워커 마운트 /k8s_pv/{nfs,gpfs,onefs} = 호스트 export /data/k8s_pv/{…}
STORAGES = (("k8s-pv-nfs", "/k8s_pv/nfs", "nfs", "pv"), ("k8s-pv-gpfs", "/k8s_pv/gpfs", "gpfs", "both"),
            ("k8s-pv-onefs", "/k8s_pv/onefs", "onefs", "pv"))
SC_TO_STORAGE = {"tb-nfs": "k8s-pv-nfs", "tb-gpfs": "k8s-pv-gpfs", "tb-onefs": "k8s-pv-onefs"}


def _raw(key):
    return json.loads((FIXTURES / _FILES[key]).read_text())


def _data():
    return normalize_snapshot({key: [_PROJECT[key](i) for i in _raw(key)["items"]] for key in _FILES})


def test_testbed_lists_project_and_pass_server_validation():
    data = _data()
    assert len(data["storage_classes"]) == 3 and len(data["pvcs"]) == 7 and len(data["pvs"]) == 6
    assert {(p["namespace"], p["name"], p["phase"]) for p in data["pvcs"]} >= {
        ("team-a", "models", "Bound"), ("team-a", "datasets", "Bound"), ("team-a", "kraken-kv-cache", "Bound"),
        ("team-b", "models", "Bound"), ("team-b", "scratch", "Bound"), ("team-c", "archive", "Bound"),
        ("team-a", "checkpoints", "Pending")}
    kraken = next(p for p in data["pvcs"] if p["name"] == "kraken-kv-cache")
    assert kraken["volume_name"].startswith("k8s-") and len(kraken["volume_name"]) == 14     # OneFS 꼴 k8s-<10hex>


def test_testbed_rolebindings_mapping_matches_the_design_fixture_plan():
    derived = {(p["namespace"], p["name"]): derive_binding(p)
               for p in (project_role_binding(i) for i in _raw("role_bindings")["items"])}
    recognized = {k: (v["dms_user"], v["access"], v["subject_mismatch"]) for k, v in derived.items() if v["dms_user"]}
    assert recognized == {
        ("team-a", "junwoo-song-clusterrole-admin"): ("junwoo.song", "admin", 0),
        ("team-a", "alice-clusterrole-edit"): ("alice", "edit", 0),
        ("team-b", "alice-clusterrole-admin"): ("alice", "admin", 0),
        ("team-b", "cocoa-song-clusterrole-edit"): ("cocoa.song", "edit", 0),
        ("team-b", "user-yo-hong-example-com-clusterrole-edit"): ("user.yo.hong.example.com", "edit", 1),
    }
    # 대조군(인식 안 됨): namespaceAdmin·default-editor(SA)·kubeflow-view·roleRef 가 기본 edit 인 이름 규칙 일치 바인딩
    for key in (("team-a", "namespaceAdmin"), ("team-a", "default-editor"), ("team-b", "bob-clusterrole-view"),
                ("team-a", "bob-clusterrole-edit")):
        assert derived[key]["dms_user"] is None, key
    # 시스템 namespace 의 바인딩(cilium·kube-system·dms 등)은 하나도 사용자 권한이 되지 않는다
    assert all(not v["dms_user"] for (ns, _), v in derived.items() if not ns.startswith("team-"))


def test_testbed_presets_point_at_the_same_directory_the_csi_driver_mounts():
    # DMS 실제 경로(/k8s_pv/<x>/<템플릿>)를 호스트 export 쪽(/data + 그 경로)으로 옮기면 csi-driver-nfs 가 마운트하는
    # <share>/<subDir>(정적 OneFS PV 는 share 그 자체)와 정확히 같아야 한다 -- 세 벤더 프리셋의 실증 대응.
    pvcs = {p["volume_name"]: p for p in _data()["pvcs"] if p["volume_name"]}
    scs = {"tb-nfs": ("/k8s_pv/nfs", "nfs"), "tb-gpfs": ("/k8s_pv/gpfs", "gpfs"), "tb-onefs": ("/k8s_pv/onefs", "onefs")}
    checked = 0
    for pv in _data()["pvs"]:
        root, backend = scs[pv["storage_class"]]
        claim = pvcs[pv["name"]]
        rel = render(PRESETS[backend], volume=pv["name"], pvc=claim["name"], namespace=claim["namespace"],
                     storage_class=pv["storage_class"], volume_attributes=pv["volume_attributes"],
                     volume_handle=pv["volume_handle"])
        attrs = pv["volume_attributes"]
        mounted = posixpath.join(attrs["share"], attrs["subDir"]) if "subDir" in attrs else attrs["share"]
        assert "/data" + posixpath.join(root, rel) == mounted, pv["name"]
        checked += 1
    assert checked == 6


def test_testbed_inventory_through_the_agent_fetch_and_the_repository(db):
    raw = {path: _raw(key) for key, _phase, path, _opt in LISTS}

    def handler(request):
        if request.url.path == "/version":
            return httpx.Response(200, json=json.loads((FIXTURES / "testbed-version.json").read_text()))
        if request.url.path == "/api/v1/namespaces/kube-system":
            return httpx.Response(200, json=json.loads((FIXTURES / "testbed-namespace-kube-system.json").read_text()))
        return httpx.Response(200, json=raw[request.url.path])

    out = fetch_inventory({"run_id": "r", "api_server": "https://10.10.10.20:6443", "token": "t", "page_limit": 500,
                           "deadline_seconds": 30, "max_bytes": 33554432}, transport=httpx.MockTransport(handler))
    assert out["ok"] is True and out["server_version"] == "v1.34.6"
    assert out["fingerprint"] == "ca1f5a86-f738-4d2e-905c-5dd019970a50"
    repos = Repositories(db)
    repos.k8s.update_settings(primary_node="k8s-w1", secondary_node="k8s-w2", interval_seconds=None, actor="ops")
    repos.k8s.create_cluster(name="dmsk8s", api_server="https://10.10.10.20:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:x", enabled=True, actor="ops")
    db.execute("UPDATE k8s_clusters SET created_at = '2026-10-25T00:00:00Z'")
    got = repos.k8s.claim(node_name="k8s-w1", role="primary", now="2026-10-25T13:50:00Z", interval=300,
                          primary_configured=True, token_ok=lambda e: True, deadline_seconds=120)
    run_id = got["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now="2026-10-25T13:50:05Z", ok=True,
                      data=normalize_snapshot(out["data"]), fingerprint=out["fingerprint"],
                      server_version=out["server_version"])
    assert repos.k8s.apply_run(run_id)["outcome"] == "applied"
    for name, root, backend, kind in STORAGES:
        db.execute("""INSERT INTO storages (storage_name, mount_path, managed_root, backend_type, enabled, user_enabled,
                      status, created_at, updated_at, updated_by, kind)
                      VALUES (:n, :r, :r, :b, 1, 1, 'Ready', 't', 't', 'x', :k)""",
                   {"n": name, "r": root, "b": backend, "k": kind})
    for sc, storage in SC_TO_STORAGE.items():
        repos.k8s.set_sc_map(sc, storage_name=storage, path_template=None, actor="ops")
    rows, _ = repos.k8s.volumes(dms_user="alice")
    assert {(r["namespace"], r["pvc"], r["access"], r["selectable"]) for r in rows} == {
        ("team-a", "checkpoints", "edit", False), ("team-a", "datasets", "edit", True),
        ("team-a", "kraken-kv-cache", "edit", True), ("team-a", "models", "edit", True),
        ("team-b", "models", "admin", True), ("team-b", "scratch", "admin", True)}
    by = {(r["namespace"], r["pvc"]): r for r in rows}
    assert by[("team-a", "kraken-kv-cache")]["abs_root"] == "/k8s_pv/onefs/data/csi/k8s-3fa9c2d71e"
    assert by[("team-a", "datasets")]["abs_root"] == "/k8s_pv/nfs/team-a/datasets"
    vol = by[("team-b", "models")]["volume"]
    assert by[("team-b", "models")]["abs_root"] == f"/k8s_pv/gpfs/{vol}/{vol}-data"
    assert by[("team-a", "checkpoints")]["reason"] == "pv_not_bound"
    # team-c 에는 사용자 바인딩이 없다(namespaceAdmin 은 인식하지 않는다) -- 아무에게도 안 보인다
    assert repos.k8s.access_for("ops.lead", "dmsk8s", "team-c") is None
    assert repos.k8s.cluster_counts()["dmsk8s"]["users"] == 4
