"""마운트 현황의 dms-k8s 덧붙임(mount_status_k8s.decorate, 2026-11-23) -- 순수 판정. 끝에서 끝(실 인벤토리)은
tests/test_api_mount_status.py 가 본다."""
from types import MappingProxyType

from dms.mount_status import build
from dms.mount_status_k8s import decorate

STORAGES = [
    {"storage_name": "ceph", "mount_path": "/cephfs", "enabled": 1, "user_enabled": 1, "kind": None},
    {"storage_name": "fs", "mount_path": "/fs", "enabled": 1, "user_enabled": 1, "kind": "filesystem"},
    {"storage_name": "nfs", "mount_path": "/k8s_pv/nfs", "enabled": 1, "user_enabled": 1, "kind": "pv"},
    {"storage_name": "gpfs", "mount_path": "/k8s_pv/gpfs", "enabled": 1, "user_enabled": 1, "kind": "both"},
    {"storage_name": "weird", "mount_path": "/w", "enabled": 1, "user_enabled": 1, "kind": "zzz"},
]
A = ("dmsk8s", "team-a", "datasets", "pvc-a", "tb-nfs")
B = ("dmsk8s", "team-b", "scratch", "pvc-b", "tb-nfs")
C = ("dmsk8s2", "lab-y", "weights", "pvc-c", "c2-nfs")
NFS_INDEX = MappingProxyType({
    "team-a/datasets": (A,),
    "team-a/datasets-alias": (A,),          # 한 PVC 가 여러 루트를 내도(템플릿 덮어쓰기) 한 번만 센다
    "team-b/scratch": (B, C),               # 한 루트에 여러 PVC(루트 충돌)도 각각 센다
})
MAPS = {"nfs": [{"storage_class": "tb-nfs"}, {"storage_class": "c2-nfs"}], "gpfs": []}


def _body(nodes=()):
    return build(list(nodes), STORAGES, exclusions={}, artifact_base_path=None, state_root=None, build_node=None,
                 node_list=None)


def _decorate(body, *, unsynced=False, settings=None, index=None):
    indexes = {"nfs": index if index is not None else NFS_INDEX}
    decorate(body, storages=STORAGES, k8s_settings=settings,
             pv_index=lambda name: indexes.get(name, MappingProxyType({})),
             maps_for_storage=lambda name: MAPS.get(name, []), unsynced=unsynced)
    return {c["storage_name"]: c for c in body["storages"]}


def test_kind_is_normalized_and_only_pv_capable_columns_get_pv():
    cols = _decorate(_body())
    assert {n: c["kind"] for n, c in cols.items()} == {
        "ceph": "filesystem", "fs": "filesystem", "nfs": "pv", "gpfs": "both", "weird": "filesystem"}
    assert cols["ceph"]["pv"] is None and cols["fs"]["pv"] is None and cols["weird"]["pv"] is None
    assert cols["nfs"]["pv"] == {"count": 3, "clusters": ["dmsk8s", "dmsk8s2"], "storage_classes": ["c2-nfs", "tb-nfs"]}


def test_unsynced_cluster_makes_the_count_unknown_not_zero():
    cols = _decorate(_body(), unsynced=True)
    assert cols["nfs"]["pv"]["count"] is None
    assert cols["nfs"]["pv"]["clusters"] == ["dmsk8s", "dmsk8s2"]
    assert cols["gpfs"]["pv"]["count"] is None


def test_no_mapping_is_zero_and_empty_lists():
    cols = _decorate(_body())
    assert cols["gpfs"]["pv"] == {"count": 0, "clusters": [], "storage_classes": []}


def test_pv_index_is_only_read():
    before = dict(NFS_INDEX)
    _decorate(_body())
    assert dict(NFS_INDEX) == before


def test_k8s_caller_roles_join_the_build_role():
    nodes = [{"node_name": n, "reported_at": "2026-11-23T00:00:00Z", "fresh": True, "report": {"mounts": []}}
             for n in ("k8s-w1", "k8s-w2", "k8s-w3")]
    body = build(nodes, STORAGES, exclusions={}, artifact_base_path=None, state_root=None, build_node="k8s-w1",
                 node_list=None)
    _decorate(body, settings={"primary_node": "k8s-w1", "secondary_node": "k8s-w2"})
    assert {r["node_name"]: r["roles"] for r in body["nodes"]} == {
        "k8s-w1": ["build", "k8s_primary"], "k8s-w2": ["k8s_secondary"], "k8s-w3": []}
    # 설정 행이 없으면(None) 꼬리표도 없다
    body = _body(nodes)
    _decorate(body, settings=None)
    assert all(r["roles"] == [] for r in body["nodes"])
