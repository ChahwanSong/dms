"""사용량 분석 PV 소속 판정(usage_attribution, 2026-10-29) -- 순수 판정. DB 를 거치는 끝에서 끝(테스트베드 모양 픽스처)은
tests/test_api_usage_analysis.py 의 「필터」 절이 본다.

순서: 지금 인벤토리(가장 깊은 PV 루트) > PV 주소 scan 기록(유효한 최신 블록) > 모름(미등록·매핑 없음·미적용 클러스터) / PV 밖.
PV 축은 같은 정체 하나가 모두 만족해야 하고, PV 축이 있으면 구분은 pv 로 본다(outside·unknown 과 함께면 0건)."""
import json
import random
from types import MappingProxyType, SimpleNamespace

import pytest

from dms.repositories.k8s import K8sRepository
from dms.usage_attribution import (FACET_LISTS, Inputs, PvFilter, attribute, collect, covering_root, facet_counts,
                                   matches, parse_block, ranked)

A_DATA = ("dmsk8s", "team-a", "datasets", "pvc-a1", "tb-nfs")
B_SCRATCH = ("dmsk8s", "team-b", "scratch", "pvc-b1", "tb-nfs")
C2_WEIGHTS = ("dmsk8s2", "lab-y", "weights", "pvc-c2", "c2-nfs")
USERS = {("dmsk8s", "team-a"): ["alice", "junwoo.song"], ("dmsk8s", "team-b"): ["alice", "cocoa.song"],
         ("dmsk8s2", "lab-y"): ["cocoa.song"]}


def _inputs(index=None, recorded=None, users=None, rest="outside"):
    return Inputs(index=MappingProxyType(index or {}), recorded=recorded or {}, users=users if users is not None else USERS,
                  rest=rest)


def _payload(**pv):
    block = {"cluster": "dmsk8s", "namespace": "team-d", "pvc": "old", "volume": "pvc-gone", "storage_class": "tb-nfs",
             "root": "team-d/old", "path": "x"}
    block.update(pv)
    return json.dumps({"storage": "k8s-pv-nfs", "target": "team-d/old/x", "pv": block})


# --- 덮는 루트: pv_root_covering 과 같은 판정 -----------------------------------------------------------------------

def _reference(roots):
    """보안 경로(direct_dest_owner)가 쓰는 pv_root_covering 을 같은 루트 집합으로 -- DB 없이(pv_roots 만 바꿔 끼운다)."""
    repo = K8sRepository(None)
    repo.pv_roots = lambda name: tuple(sorted(roots))
    return lambda t: repo.pv_root_covering("s", t)


@pytest.mark.parametrize("target,expected", [
    ("team-a/datasets", "team-a/datasets"),
    ("team-a/datasets/x/y", "team-a/datasets"),
    ("team-a/data", None),                      # 이웃 이름 -- 글자 접두가 아니라 경로 경계
    ("team-a/datasetsX", None),
    ("team-a", None),                           # 루트의 부모는 PV 밖
    ("team-a/datasets/", "team-a/datasets"),    # 비정규 target(DB 신뢰 경계) -- 같은 글자 경계 규칙
    ("team-a/datasets//z", "team-a/datasets"),
    ("/team-a/datasets", None),
    ("", None),
])
def test_covering_root_is_the_path_boundary_rule(target, expected):
    roots = {"team-a/datasets": (A_DATA,)}
    assert covering_root(roots, target) == expected
    assert _reference(roots)(target) == expected


def test_covering_root_matches_pv_root_covering_on_random_paths():
    rng = random.Random(20261029)
    alphabet = ["a", "b", "ab", "a-b", ".", "..", ""]
    for _ in range(300):
        roots = {"/".join(rng.choice(alphabet[:4]) for _ in range(rng.randint(1, 3))) for _ in range(rng.randint(1, 6))}
        index = {r: (A_DATA,) for r in roots}
        ref = _reference(roots)
        for _ in range(20):
            t = "/".join(rng.choice(alphabet) for _ in range(rng.randint(1, 5)))
            assert covering_root(index, t) == ref(t), (sorted(roots), t)
    assert covering_root({"a": ()}, None) is None


def test_nested_roots_take_the_deepest_and_collisions_carry_every_identity():
    index = {"c2": (B_SCRATCH,), "c2/lab-y/weights": (C2_WEIGHTS, A_DATA)}
    inp = _inputs(index)
    deep = attribute("c2/lab-y/weights/run1", inp)
    assert (deep["source"], deep["root"]) == ("inventory", "c2/lab-y/weights")
    assert [(v["cluster"], v["namespace"], v["pvc"]) for v in deep["volumes"]] == [
        ("dmsk8s2", "lab-y", "weights"), ("dmsk8s", "team-a", "datasets")]
    assert deep["users"] == ["alice", "cocoa.song", "junwoo.song"]          # 정체마다의 합집합(정렬·중복 없음)
    shallow = attribute("c2/lab-y", inp)
    assert (shallow["source"], shallow["root"]) == ("inventory", "c2")
    assert attribute("c2/lab-y/weights/run2", inp) is deep                  # 같은 루트 = 같은 판정 객체(메모)


def test_axes_must_hold_on_one_identity_not_across_identities():
    # 루트 충돌(정체 둘)에서 cluster 는 C2 의 것, namespace 는 A 의 것 -- 정체마다 따로 맞으면 섞이지 않는다.
    inp = _inputs({"shared": (C2_WEIGHTS, A_DATA)})
    attr = attribute("shared/x", inp)
    ok = lambda **kw: matches(attr, PvFilter(**kw), inp.users)   # noqa: E731
    assert ok(cluster="dmsk8s2", namespace="lab-y")
    assert ok(cluster="dmsk8s", namespace="team-a", pvc="datasets", storage_class="tb-nfs")
    assert not ok(cluster="dmsk8s2", namespace="team-a")
    assert not ok(pvc="weights", storage_class="tb-nfs")
    # 권한 사용자도 정체 단위: junwoo.song 은 team-a(A) 에만 -- lab-y(C2) 와 함께 물으면 아니다
    assert ok(user="junwoo.song") and ok(user="cocoa.song", cluster="dmsk8s2")
    assert not ok(user="junwoo.song", cluster="dmsk8s2")
    assert not ok(user="nobody")


# --- 기록 대체 ------------------------------------------------------------------------------------------------------

def test_inventory_beats_the_recorded_block_and_recorded_fills_deleted_pvcs():
    inp = _inputs({"team-a/datasets": (A_DATA,)},
                  recorded={"team-a/datasets/x": [_payload(root="team-a/datasets", namespace="team-z")],
                            "team-d/old/x": [_payload()]})
    assert attribute("team-a/datasets/x", inp)["volumes"][0]["namespace"] == "team-a"     # 지금 인벤토리가 이긴다
    rec = attribute("team-d/old/x", inp)
    assert (rec["source"], rec["root"]) == ("recorded", "team-d/old")
    assert rec["volumes"] == [{"cluster": "dmsk8s", "namespace": "team-d", "pvc": "old", "volume": "pvc-gone",
                               "storage_class": "tb-nfs"}]
    assert rec["users"] == []                                   # 지금 바인딩 기준 -- 그 namespace 의 바인딩이 없다


@pytest.mark.parametrize("bad", [
    "{bro", "[" * 5000 + "]" * 5000, "null", "[]", json.dumps({"pv": "x"}), json.dumps({"no": 1}),
    _payload(cluster=""), _payload(namespace=None), _payload(pvc=5), _payload(volume=7), _payload(storage_class=[]),
    _payload(root=""), _payload(root=None), _payload(root="team-d/ol"),          # 대상이 루트 밖(글자 접두만 같음)
    _payload(root="elsewhere"),
])
def test_tampered_blocks_are_ignored_and_the_next_valid_one_is_used(bad):
    assert parse_block(bad, "team-d/old/x") is None
    inp = _inputs(recorded={"team-d/old/x": [bad, _payload(namespace="team-e")]})
    attr = attribute("team-d/old/x", inp)
    assert attr["source"] == "recorded" and attr["volumes"][0]["namespace"] == "team-e"   # 최신이 무효면 다음 유효한 것


def test_valid_block_shapes():
    assert parse_block(_payload(volume=None, storage_class=None), "team-d/old") == (
        "team-d/old", ("dmsk8s", "team-d", "old", None, None))
    assert parse_block(json.loads(_payload()), "team-d/old/x")[0] == "team-d/old"       # dict 도 받는다
    assert parse_block(_payload(), None) is None


# --- 모름 / PV 밖 ---------------------------------------------------------------------------------------------------

class _FakeRepos:
    """collect 가 묻는 저장소 메서드만 가진 가짜. calls 에 부른 것을 남긴다."""

    def __init__(self, *, storage=None, index=None, maps=True, unsynced=False, recorded=None):
        self.calls = []
        rec = recorded or {}
        log = self.calls.append
        self.storages = SimpleNamespace(get=lambda n: (log("storage"), storage)[1])
        self.k8s = SimpleNamespace(
            pv_root_index=lambda n: (log("index"), MappingProxyType(index or {}))[1],
            maps_for_storage=lambda n: (log("maps"), [{"storage_class": "tb-nfs"}] if maps else [])[1],
            unsynced_cluster_exists=lambda: (log("unsynced"), unsynced)[1],
            users_by_namespace=lambda: (log("users"), USERS)[1])
        self.data_jobs = SimpleNamespace(
            has_recorded_pv_scans=lambda n: (log("has_recorded"), bool(rec))[1],
            recorded_pv_blocks=lambda n, targets: (log(("recorded", tuple(targets))),
                                                   {t: rec[t] for t in targets if t in rec})[1])


PV = {"storage_name": "k8s-pv-nfs", "kind": "pv"}
BOTH = {"storage_name": "k8s-pv-gpfs", "kind": "both"}
FS = {"storage_name": "cephfs-dms", "kind": "filesystem"}
HEADS = [{"target": "team-a/datasets/x"}, {"target": "proof-d9/alice/latest"}, {"target": "team-d/old/x"}]


@pytest.mark.parametrize("storage,maps,unsynced,rest", [
    (None, True, False, "unknown"),          # 등록돼 있지 않다(지운 스토리지의 기록)
    (PV, False, False, "unknown"),           # PV 를 담는데 SC 매핑이 없다
    (BOTH, False, False, "unknown"),
    (PV, True, True, "unknown"),             # 한 번도 적용되지 않은 클러스터가 있다 -- 그 PV 를 모른다
    (PV, True, False, "outside"),
    (BOTH, True, False, "outside"),
    (FS, False, True, "outside"),            # filesystem 은 PV 를 담지 않는다(클러스터 상태와 무관)
    ({"storage_name": "x", "kind": None}, False, True, "outside"),     # NULL 종류 = filesystem
])
def test_unknown_versus_outside(storage, maps, unsynced, rest):
    repos = _FakeRepos(storage=storage, index={"team-a/datasets": (A_DATA,)}, maps=maps, unsynced=unsynced,
                       recorded={"team-d/old/x": [_payload()]})
    inp = collect(repos, "s", HEADS)
    pv_capable = storage is not None and storage.get("kind") in ("pv", "both")
    assert dict(inp.index) == ({"team-a/datasets": (A_DATA,)} if pv_capable else {})
    assert attribute("proof-d9/alice/latest", inp) == {"source": rest, "root": None, "volumes": [], "users": []}
    assert attribute("team-d/old/x", inp)["source"] == "recorded"           # 기록은 모름·밖보다 앞선다
    if pv_capable:
        assert attribute("team-a/datasets/x", inp)["source"] == "inventory"
    # 기록 블록은 인벤토리가 덮지 않은 대상만 묻는다
    asked = [c for c in repos.calls if isinstance(c, tuple)]
    assert asked == [("recorded", ("proof-d9/alice/latest", "team-d/old/x") if pv_capable
                      else ("team-a/datasets/x", "proof-d9/alice/latest", "team-d/old/x"))]


def test_collect_skips_record_and_user_lookups_when_there_is_nothing_to_attribute():
    repos = _FakeRepos(storage=FS)
    inp = collect(repos, "cephfs-dms", HEADS)
    assert inp.rest == "outside" and dict(inp.users) == {}
    assert repos.calls == ["storage", "has_recorded"]                       # 인덱스·매핑·바인딩을 묻지 않는다


# --- 구분과 PV 축의 결합 -------------------------------------------------------------------------------------------

def test_scope_and_pv_axes_combine():
    inp = _inputs({"team-a/datasets": (A_DATA,)}, recorded={"team-d/old/x": [_payload()]})
    inv, rec = attribute("team-a/datasets/x", inp), attribute("team-d/old/x", inp)
    out = attribute("elsewhere", inp)
    unk = attribute("elsewhere", _inputs(rest="unknown"))
    m = lambda a, **kw: matches(a, PvFilter(**kw), inp.users)   # noqa: E731
    assert all(m(a) for a in (inv, rec, out, unk))                          # 필터 없음 = 전부
    assert [m(a, scope="pv") for a in (inv, rec, out, unk)] == [True, True, False, False]
    assert [m(a, scope="outside") for a in (inv, rec, out, unk)] == [False, False, True, False]
    assert [m(a, scope="unknown") for a in (inv, rec, out, unk)] == [False, False, False, True]
    assert [m(a, cluster="dmsk8s") for a in (inv, rec, out, unk)] == [True, True, False, False]   # 축 = pv 로 간주
    assert not m(out, scope="outside", cluster="dmsk8s")                     # 밖 + 축 = 0건(정상값)
    assert not m(unk, scope="unknown", namespace="team-a")
    assert not m(inv, scope="outside", namespace="team-a")
    assert m(inv, scope="pv", namespace="team-a") and not m(inv, scope="pv", namespace="team-b")
    assert m(inv, namespace="team-a") and not m(rec, namespace="team-a")    # namespace 단독 = 모든 클러스터의 그 이름
    assert PvFilter().active is False and PvFilter(scope="pv").pv_axes is False and PvFilter(user="a").pv_axes


# --- facets ---------------------------------------------------------------------------------------------------------

def test_facets_count_each_target_once_per_option():
    index = {"team-a/datasets": (A_DATA,), "shared": (A_DATA, C2_WEIGHTS), "team-b/scratch": (B_SCRATCH,)}
    inp = _inputs(index, recorded={"team-a/old": [_payload(namespace="team-a", pvc="datasets", storage_class="sc-old",
                                                           root="team-a/old")]})
    # 기록(옛 SC)을 먼저 본다 -- 그래도 PVC 선택지의 SC 는 인벤토리 판정의 값이다
    targets = ["team-a/old", "team-a/datasets/x", "team-a/datasets/y", "shared/z", "team-b/scratch", "elsewhere"]
    f = facet_counts([attribute(t, inp) for t in targets])
    assert f["scope"] == {"inventory": 4, "recorded": 1, "outside": 1, "unknown": 0}
    assert f["clusters"] == [{"cluster": "dmsk8s", "target_count": 5}, {"cluster": "dmsk8s2", "target_count": 1}]
    assert f["namespaces"] == [{"cluster": "dmsk8s", "namespace": "team-a", "target_count": 4},
                               {"cluster": "dmsk8s", "namespace": "team-b", "target_count": 1},
                               {"cluster": "dmsk8s2", "namespace": "lab-y", "target_count": 1}]
    # 같은 PVC 는 한 선택지 -- SC 는 인벤토리 판정의 값(기록의 옛 SC 로 두 줄이 되지 않는다)
    assert f["pvcs"][0] == {"cluster": "dmsk8s", "namespace": "team-a", "pvc": "datasets", "storage_class": "tb-nfs",
                            "target_count": 4}
    assert len(f["pvcs"]) == 3
    assert f["storage_classes"] == [{"storage_class": "tb-nfs", "target_count": 4},
                                    {"storage_class": "c2-nfs", "target_count": 1},
                                    {"storage_class": "sc-old", "target_count": 1}]
    assert f["users"] == [{"dms_user": "alice", "target_count": 5}, {"dms_user": "junwoo.song", "target_count": 4},
                          {"dms_user": "cocoa.song", "target_count": 2}]
    assert f["lists_truncated"] == dict.fromkeys(FACET_LISTS, False)


def test_facet_lists_are_capped_by_count_then_name():
    rows, cut = ranked({"b": 2, "a": 2, "c": 9, "d": 1}, lambda k, n: (k, n), cap=3)
    assert rows == [("c", 9), ("a", 2), ("b", 2)] and cut is True
    assert ranked({}, lambda k, n: k, cap=3) == ([], False)
    inp = _inputs({f"r{i}": ((f"c{i}", "ns", "p", "v", "sc"),) for i in range(3)})
    f = facet_counts([attribute(f"r{i}", inp) for i in range(3)], cap=2)
    assert len(f["clusters"]) == 2 and f["lists_truncated"]["clusters"] is True
    assert f["lists_truncated"]["storage_classes"] is False
