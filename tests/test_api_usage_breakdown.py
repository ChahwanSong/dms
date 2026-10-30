"""사용량 분석 소속별(GET /api/admin/usage/breakdown, 2026-10-30 사용자 결정 「그래 구현시작해.」) -- 여섯 축·덧붙임·적용 축·순위·상한·
창 넘침·읽기 예산과 **오라클**: 행마다 그 행의 filter 를 건 storage-totals·/facets 의 옵션 수·/targets 의 matched_count 와 같다(포탈이
행을 누르면 그 필터를 건 경로별로 간다 -- 두 화면의 숫자가 어긋나면 안 된다).

세계는 tests/test_api_usage_analysis.py 의 「필터」 절(uw -- 테스트베드 모양 사용량 세계: dmsk8s 실데이터 + 두 번째 클러스터 dmsk8s2,
스토리지 4종)을 그대로 쓴다. DB 는 conftest 의 db 라 DMS_TEST_DB=postgresql 이면 PG 스키마에서도 돈다."""
import json
import os
import random
import uuid
from types import MappingProxyType

import pytest

from dms.api.routes_usage import filter_value_ok
from dms.db import dump_json
from dms.usage_attribution import (GROUP_AXES, PV_GROUP_AXES, Inputs, PvFilter, attribute, breakdown_groups, group_fields,
                                   group_filter, group_keys, key_expressible, matches)
# uw·world 는 픽스처다(같은 이름으로 이 모듈에 있어야 pytest 가 찾는다).
from test_api_usage_analysis import (CEPH, FACETS, GPFS, NFS, NFS_DESC, ONEFS, TARGETS, TOTALS,  # noqa: F401
                                     _bulk_jobs, _client, _login, _pvb, _report, _seed_scan, uw, world)

BREAKDOWN = "/api/admin/usage/breakdown"
_KEYS = ["storage_name", "computed_at", "by", "requested_by", "window_limit", "window_truncated", "target_count", "scope",
         "cluster_count", "storage_total", "rows", "rows_truncated", "extras", "pending_count", "unreadable_count",
         "progressed"]
_VALUE_KEYS = ["target_count", "summed_target_count", "nested_target_count", "total_bytes", "total_files", "hot_ratio",
               "oldest_scan_at", "newest_scan_at"]
_ROW_KEYS = {"user": ["dms_user"], "requester": ["requester_id"], "namespace": ["cluster", "namespace"],
             "pvc": ["cluster", "namespace", "pvc", "storage_class"], "storage_class": ["storage_class"],
             "cluster": ["cluster"]}


def _bd(uw, storage=NFS, **params):
    r = uw.client.get(BREAKDOWN, params={"storage": storage, **params})
    assert r.status_code == 200, r.text
    body = r.json()
    assert list(body) == _KEYS                                       # 계약 모양(포탈 타입과 한 벌)
    for row in body["rows"]:
        assert list(row) == _ROW_KEYS[body["by"]] + _VALUE_KEYS + ["filter"], row
    for ex in body["extras"].values():
        assert ex is None or list(ex) == _VALUE_KEYS + ["filter"]
    return body


def _summary(rows, *keys):
    return [tuple(r[k] for k in keys) + (r["target_count"], r["total_bytes"]["sum"]) for r in rows]


def _totals(uw, storage, **flt):
    r = uw.client.get(TOTALS, params={"storage": storage, **flt})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def bw(uw):
    """uw + 소속별이 따로 보는 모양 셋.
    - 묶음 **안** 겹친 경로: team-a/datasets/dms-proof ⊃ …/dms-proof/sub(alice, 더 새로 스캔) -- 묶음 값은 묶음 안 최상위만 더한다.
    - SC 없는 기록 PV: team-e/gone/run(지운 PVC 의 PV 주소 scan, 블록 storage_class null, 바인딩 없는 namespace) -- StorageClass 축과
      권한 사용자 축의 none.
    - 요청 행 없는 고아 잡: orphan/x(DB 신뢰 경계 -- 어느 요청자의 것도 아니다) -- 요청자 축의 none."""
    _seed_scan(uw.repos, uw.db, uw.art, storage=NFS, target="team-a/datasets/dms-proof/sub", requester="alice",
               at="2026-10-27T01:00:00Z", total=40)
    _seed_scan(uw.repos, uw.db, uw.art, storage=NFS, target="team-e/gone/run", requester="alice",
               at="2026-10-23T00:00:00Z", pv=_pvb("dmsk8s", "team-e", "gone", "pvc-e", None, "team-e/gone"), total=30)
    jid = uuid.uuid4().hex
    uw.db.execute("""INSERT INTO data_jobs (job_id, request_id, operation, tool, storage_name, target, options, priority, state,
                                            worker_pool, created_at, updated_at)
                     VALUES (:j, :r, 'scan', 'dscan', :sn, 'orphan/x', '{}', 'mid', 'Succeeded', :wp, :at, :at)""",
                  {"j": jid, "r": uuid.uuid4().hex, "sn": NFS, "at": "2026-10-22T00:00:00Z",
                   "wp": dump_json({"identity": {"username": "ghost", "uid": os.getuid(), "gid": os.getgid()}})})
    d = uw.art / jid / "execution"
    d.mkdir(parents=True)
    (d / "dscan-report.json").write_text(json.dumps(_report(mtime_bytes=(7,))))
    return uw


def _oracle(uw, storage, body):
    """오라클(usage_breakdown docstring): 창이 넘치지 않았고 요약 캐시가 데워졌으면(pending 0) 행·덧붙임·전체가 경로별의 같은 필터
    합계와 같다 -- 그리고 PV 축의 대상 수 = /facets 의 옵션 수 = /targets 의 matched_count, filter 가 있는 행의 집합 = /facets 의 옵션
    집합. filter null 행 = 키가 필터 파라미터 규칙 밖(경로별에서 걸면 422 -- 링크 아님, /facets 선택지 아님)."""
    by = body["by"]
    assert body["pending_count"] == 0 and body["window_truncated"] is False
    whole = _totals(uw, storage)
    assert body["storage_total"] == {"summed_target_count": whole["summed_target_count"],
                                     "total_bytes": whole["total_bytes"]}
    assert body["target_count"] == whole["target_count"]
    facets = uw.client.get(FACETS, params={"storage": storage}).json()
    assert body["scope"] == facets["scope"]
    # cluster_count 는 창의 모든 클러스터(규칙 밖 이름 포함 -- 포탈의 접미 판정), /facets 는 고를 수 있는 것만
    clusters = (body if by == "cluster" else _bd(uw, storage, by="cluster"))
    cluster_rows = clusters["rows"] if clusters["by"] == "cluster" else []
    assert body["cluster_count"] == len(cluster_rows)
    assert len(facets["clusters"]) == sum(1 for r in cluster_rows if r["filter"] is not None)
    options = {"user": {(u["dms_user"],): u for u in facets["users"]},
               "requester": {(u["requester_id"],): u for u in facets["requesters"]},
               "namespace": {(u["cluster"], u["namespace"]): u for u in facets["namespaces"]},
               "pvc": {(u["cluster"], u["namespace"], u["pvc"]): u for u in facets["pvcs"]},
               "storage_class": {(u["storage_class"],): u for u in facets["storage_classes"]},
               "cluster": {(u["cluster"],): u for u in facets["clusters"]}}[by]
    keys, linked = [], set()
    for row in body["rows"]:
        key = tuple(row[k] for k in _ROW_KEYS[by] if not (by == "pvc" and k == "storage_class"))
        keys.append(key)
        flt = row["filter"]
        if flt is None:
            assert not key_expressible(by, key, filter_value_ok) and key not in options, (by, key)
            r = uw.client.get(TOTALS, params={"storage": storage, **group_filter(by, key)})
            assert r.status_code == 422, (by, key)                      # 그래서 링크가 아니다
            continue
        linked.add(key)
        assert flt == group_filter(by, key)
        t = _totals(uw, storage, **flt)
        assert t["filters"] == {**dict.fromkeys(("cluster", "namespace", "pvc", "storage_class", "user", "scope",
                                                 "requester")), **flt}
        assert t["truncated"] is False and t["pending_count"] == 0
        assert {k: row[k] for k in _VALUE_KEYS} == {k: t[k] for k in _VALUE_KEYS}, (by, key)
        page = uw.client.get(TARGETS, params={"storage": storage, "limit": 200, **flt}).json()
        assert page["matched_count"] == row["target_count"], (by, key)
        assert options[key]["target_count"] == row["target_count"], (by, key)
        if by == "pvc":
            assert row["storage_class"] == options[key]["storage_class"]
    assert linked == set(options) and len(keys) == len(set(keys))
    for name, ex in body["extras"].items():
        if ex is not None and ex["filter"] is not None:
            assert ex["filter"] == {"scope": name}
            t = _totals(uw, storage, scope=name)
            assert {k: ex[k] for k in _VALUE_KEYS} == {k: t[k] for k in _VALUE_KEYS}, name
            assert ex["target_count"] == facets["scope"][name]
        elif name in ("outside", "unknown") and by != "requester":
            assert facets["scope"][name] == 0                            # 대상 0 이면 null


# ---- 축별 값 ----

def test_default_is_user_and_ranks_by_real_usage_with_extras(uw):
    b = _bd(uw)
    assert (b["storage_name"], b["by"], b["requested_by"], b["window_limit"], b["window_truncated"],
            b["target_count"]) == (NFS, "user", None, 10000, False, 8)
    assert b["scope"] == {"inventory": 4, "recorded": 1, "outside": 3, "unknown": 0} and b["cluster_count"] == 2
    # 전체 = 창의 최상위 합(c2/lab-y/weights 는 c2/lab-y 의 하위) -- 비중 막대의 분모
    assert b["storage_total"] == {"summed_target_count": 7, "total_bytes": {"sum": 2850, "known": 7, "unknown": 0}}
    # 같은 실 사용량·같은 대상 수(alice·junwoo.song -- 둘 다 team-a 바인딩)는 이름순
    assert _summary(b["rows"], "dms_user") == [("alice", 3, 1300), ("junwoo.song", 3, 1300), ("cocoa.song", 1, 900)]
    alice = b["rows"][0]
    assert alice["filter"] == {"user": "alice"}
    assert (alice["summed_target_count"], alice["nested_target_count"]) == (3, 0)
    assert alice["total_files"] == {"sum": 21, "known": 3, "unknown": 0}
    assert alice["hot_ratio"] == {"value": 1.0, "hot_bytes": 150, "total_bytes": 150, "known": 3, "unknown": 0}
    assert (alice["oldest_scan_at"], alice["newest_scan_at"]) == ("2026-10-25T01:00:00Z", "2026-10-26T03:00:00Z")
    assert b["rows_truncated"] is False
    ex = b["extras"]
    assert ex["unknown"] is None                                       # 대상 0 = null(0 행이 아니다)
    assert (ex["outside"]["filter"], ex["outside"]["target_count"], ex["outside"]["total_bytes"]["sum"]) == (
        {"scope": "outside"}, 3, 1500)
    # 권한 사용자 없는 PV 안 대상 = 지운 PVC(team-d/old)의 기록 -- 경로별에 그 필터가 없어 filter null
    assert (ex["none"]["filter"], ex["none"]["target_count"], ex["none"]["total_bytes"]["sum"]) == (None, 1, 50)
    assert (b["pending_count"], b["unreadable_count"], b["progressed"]) == (0, 0, True)
    again = _bd(uw)
    assert again["progressed"] is False and again["rows"] == b["rows"]


def test_each_axis_groups_whole_targets_by_its_key(uw):
    req = _bd(uw, by="requester")
    assert (req["by"], req["requested_by"]) == ("requester", "requester")
    # mason 의 c2/lab-y/weights 는 묶음 안에서 c2/lab-y 의 하위라 더하지 않는다
    assert _summary(req["rows"], "requester_id") == [("mason", 6, 2300), ("alice", 2, 550)]
    assert (req["rows"][0]["summed_target_count"], req["rows"][0]["nested_target_count"]) == (5, 1)
    assert req["rows"][0]["filter"] == {"requester": "mason"}
    assert req["extras"] == {"outside": None, "unknown": None, "none": None}   # 요청자 축에는 PV 구분이 없다
    ns = _bd(uw, by="namespace")
    assert _summary(ns["rows"], "cluster", "namespace") == [("dmsk8s", "team-a", 3, 1300), ("dmsk8s2", "lab-y", 1, 900),
                                                             ("dmsk8s", "team-d", 1, 50)]
    assert ns["rows"][1]["filter"] == {"cluster": "dmsk8s2", "namespace": "lab-y"}
    assert ns["extras"]["none"] is None and ns["extras"]["outside"]["target_count"] == 3
    pvc = _bd(uw, by="pvc")
    assert _summary(pvc["rows"], "cluster", "namespace", "pvc", "storage_class") == [
        ("dmsk8s", "team-a", "datasets", "tb-nfs", 3, 1300), ("dmsk8s2", "lab-y", "weights", "c2-nfs", 1, 900),
        ("dmsk8s", "team-d", "old", "tb-nfs", 1, 50)]
    assert pvc["rows"][0]["filter"] == {"cluster": "dmsk8s", "namespace": "team-a", "pvc": "datasets"}
    sc = _bd(uw, by="storage_class")
    assert _summary(sc["rows"], "storage_class") == [("tb-nfs", 4, 1350), ("c2-nfs", 1, 900)]
    assert sc["rows"][0]["filter"] == {"storage_class": "tb-nfs"} and sc["extras"]["none"] is None
    cl = _bd(uw, by="cluster")
    assert _summary(cl["rows"], "cluster") == [("dmsk8s", 4, 1350), ("dmsk8s2", 1, 900)]
    # 묶음 값은 **묶음 안의** 최상위 합 -- dmsk8s2 의 weights 는 전체에선 c2/lab-y 의 하위지만 이 묶음에선 최상위다
    assert (cl["rows"][1]["summed_target_count"], cl["rows"][1]["nested_target_count"]) == (1, 0)
    # 한 대상이 여러 요청자 묶음에 든다(onefs 의 한 대상을 alice·junwoo.song 이 scan) -- 같은 값이면 이름순
    one = _bd(uw, ONEFS, by="requester")
    assert _summary(one["rows"], "requester_id") == [("alice", 1, 20), ("junwoo.song", 1, 20)]
    # PVC 이름은 namespace 마다만 유일 -- models 둘이 따로 선다
    g = _bd(uw, GPFS, by="pvc")
    assert [(r["namespace"], r["pvc"], r["total_bytes"]["sum"]) for r in g["rows"]] == [("team-b", "models", 2),
                                                                                      ("team-a", "models", 1)]
    assert g["extras"]["outside"]["target_count"] == 1 and g["cluster_count"] == 1


def test_nested_paths_sc_less_records_and_orphan_jobs(bw):
    user = _bd(bw)
    alice = next(r for r in user["rows"] if r["dms_user"] == "alice")
    # dms-proof ⊃ dms-proof/sub: 묶음 안 최상위만(하위의 40 은 더하지 않는다), 시점 범위도 더한 대상들의 것
    assert (alice["target_count"], alice["summed_target_count"], alice["nested_target_count"]) == (4, 3, 1)
    assert alice["total_bytes"]["sum"] == 1300 and alice["newest_scan_at"] == "2026-10-26T03:00:00Z"
    # 권한 사용자 없는 PV 안 = 지운 PVC 둘(team-d/old·team-e/gone), 고아 orphan/x 는 PV 밖
    assert (user["extras"]["none"]["target_count"], user["extras"]["none"]["total_bytes"]["sum"]) == (2, 80)
    assert user["extras"]["outside"]["target_count"] == 4
    sc = _bd(bw, by="storage_class")
    assert (sc["extras"]["none"]["target_count"], sc["extras"]["none"]["total_bytes"]["sum"]) == (1, 30)
    assert _summary(sc["rows"], "storage_class") == [("tb-nfs", 5, 1350), ("c2-nfs", 1, 900)]
    pvc = _bd(bw, by="pvc")
    gone = next(r for r in pvc["rows"] if r["pvc"] == "gone")
    assert gone["storage_class"] is None and gone["filter"] == {"cluster": "dmsk8s", "namespace": "team-e", "pvc": "gone"}
    req = _bd(bw, by="requester")
    assert _summary(req["rows"], "requester_id") == [("mason", 6, 2300), ("alice", 4, 580)]
    assert (req["extras"]["none"]["filter"], req["extras"]["none"]["target_count"],
            req["extras"]["none"]["total_bytes"]["sum"]) == (None, 1, 7)
    assert (req["extras"]["outside"], req["extras"]["unknown"]) == (None, None)


def test_every_row_equals_the_filtered_storage_totals_facets_and_targets(bw):
    """오라클 -- 모든 스토리지 × 모든 축(+ 기본)."""
    for storage in (NFS, ONEFS, GPFS, CEPH):
        for by in (None,) + GROUP_AXES:
            body = _bd(bw, storage, **({"by": by} if by else {}))
            _oracle(bw, storage, body)


def test_unknown_scope_is_its_own_extra_and_matches_its_filter(uw):
    uw.repos.k8s.create_cluster(name="dmsk8s3", api_server="https://10.10.10.30:6443", tls_server_name=None, ca_pem=None,
                                insecure_skip_tls_verify=False, token_enc="v1:x", enabled=False, actor="ops")
    b = _bd(uw, by="namespace")
    # 한 번도 적용되지 않은 클러스터가 있으면 PV 밖은 「모름」(null ≠ 0) -- 덧붙임도 outside 가 아니라 unknown
    assert b["scope"] == {"inventory": 4, "recorded": 1, "outside": 0, "unknown": 3}
    assert b["extras"]["outside"] is None
    assert (b["extras"]["unknown"]["filter"], b["extras"]["unknown"]["target_count"]) == ({"scope": "unknown"}, 3)
    _oracle(uw, NFS, b)


# ---- 필터 파라미터로 표현 못 하는 키(2026-10-30 리뷰) ----

def _seed_unexpressible(uw):
    """DB 가 신뢰 경계라 생기는, 경로별 필터 파라미터(_filter_params -- 길이 1..253(요청자 512)·제어 문자 없음) 규칙 밖 키들. parse_block
    은 비어 있지 않은 문자열만 보고, users_by_namespace 는 NOT NULL 만 보고, requester_id 는 무검증 INSERT 다."""
    seed = lambda target, requester, at, pv, total: _seed_scan(  # noqa: E731
        uw.repos, uw.db, uw.art, storage=NFS, target=target, requester=requester, at=at, pv=pv, total=total)
    seed("team-t/x/run", "alice", "2026-10-27T00:00:00Z", _pvb("dmsk8s", "team\tt", "x", "pvc-t", "sc\x01bad", "team-t/x"), 11)
    seed("team-l/y/run", "alice", "2026-10-27T01:00:00Z", _pvb("dmsk8s", "team-l", "p" * 300, "pvc-l", "tb-nfs", "team-l/y"), 12)
    seed("weird-c/z", "mason", "2026-10-27T02:00:00Z", _pvb("c\x01x", "ns-z", "z", "pvc-z", "tb-nfs", "weird-c/z"), 13)
    seed("direct/bad", "bad\nq", "2026-10-27T03:00:00Z", None, 14)
    seed("direct/long", "r" * 600, "2026-10-27T04:00:00Z", None, 15)
    for name, user in (("blank-clusterrole-edit", ""), ("ctl-clusterrole-edit", "u\x01x")):
        uw.db.execute("""INSERT INTO k8s_role_bindings (cluster_name, namespace, name, role_kind, role_name, subjects, dms_user,
                                                        access, subject_mismatch)
                         VALUES ('dmsk8s', 'team-a', :n, 'ClusterRole', 'edit', '[]', :u, 'edit', 0)""", {"n": name, "u": user})


def test_unexpressible_keys_are_rows_without_a_filter_and_not_facet_options(uw):
    """규칙 밖 키의 묶음은 행으로 남되(실 사용량이다 -- 숨기면 조용한 누락) filter 가 null 이다(경로별에 실으면 422 -- 포탈은 링크로
    그리지 않는다). /facets 는 같은 술어로 그 선택지를 뺀다(고르는 순간 422 이던 선택지). 나머지 행은 오라클 그대로."""
    _seed_unexpressible(uw)
    nulls = {}
    for by in GROUP_AXES:
        b = _bd(uw, by=by)
        assert b["by"] == by
        nulls[by] = sorted(tuple(r[k] for k in _ROW_KEYS[by] if not (by == "pvc" and k == "storage_class"))
                           for r in b["rows"] if r["filter"] is None)
        _oracle(uw, NFS, b)
    assert nulls == {"user": [("",), ("u\x01x",)], "requester": [("bad\nq",), ("r" * 600,)],
                     "namespace": [("c\x01x", "ns-z"), ("dmsk8s", "team\tt")],
                     "pvc": [("c\x01x", "ns-z", "z"), ("dmsk8s", "team\tt", "x"), ("dmsk8s", "team-l", "p" * 300)],
                     "storage_class": [("sc\x01bad",)], "cluster": [("c\x01x",)]}
    # 행은 순위 안에 그대로 선다(값이 있는 묶음) -- 빈 dms_user 는 team-a 의 대상 셋(alice 와 같은 값)
    blank = next(r for r in _bd(uw)["rows"] if r["dms_user"] == "")
    assert (blank["filter"], blank["target_count"], blank["total_bytes"]["sum"]) == (None, 3, 1300)
    assert _bd(uw, by="namespace")["cluster_count"] == 3                  # 접미 판정은 모든 클러스터로
    f = uw.client.get(FACETS, params={"storage": NFS}).json()
    assert [c["cluster"] for c in f["clusters"]] == ["dmsk8s", "dmsk8s2"]
    assert ("dmsk8s", "team-l") in {(n["cluster"], n["namespace"]) for n in f["namespaces"]}    # 규칙 안 값은 그대로
    assert {u["dms_user"] for u in f["users"]} == {"alice", "junwoo.song", "cocoa.song"}
    assert {r["requester_id"] for r in f["requesters"]} == {"alice", "mason"}
    assert [s["storage_class"] for s in f["storage_classes"]] == ["tb-nfs", "c2-nfs"]
    assert f["lists_truncated"] == dict.fromkeys(("clusters", "namespaces", "pvcs", "storage_classes", "users",
                                                  "requesters"), False)


def test_a_lone_surrogate_in_a_recorded_block_is_ignored_not_a_500(uw):
    """기록 pv 블록의 JSON 이스케이프 외톨이 서로게이트(TEXT 에는 ASCII \\ud800 로 들어간다 -- DB 가 신뢰 경계)는 무효 블록이다
    (usage_attribution.parse_block, 2026-10-30 리뷰): 풀린 문자열이 응답 인코딩에서 UnicodeEncodeError 라 세 엔드포인트가 500 이었다."""
    _seed_scan(uw.repos, uw.db, uw.art, storage=NFS, target="sur/x/run", requester="alice", at="2026-10-27T00:00:00Z",
               pv=_pvb("dmsk8s", "ns-ok", "x", "pvc-s", "tb-nfs", "sur/x"), total=5)
    row = uw.db.query_one("SELECT request_id, payload FROM requests WHERE payload LIKE :p", {"p": "%ns-ok%"})
    uw.db.execute("UPDATE requests SET payload = :p WHERE request_id = :r",
                  {"p": row["payload"].replace("ns-ok", "\\ud800"), "r": row["request_id"]})
    for url, params in ((FACETS, {}), (TARGETS, {}), (BREAKDOWN, {"by": "namespace"})):
        assert uw.client.get(url, params={"storage": NFS, **params}).status_code == 200
    hit = next(r for r in uw.client.get(TARGETS, params={"storage": NFS, "q": "sur/x"}).json()["rows"])
    assert hit["pv"]["source"] != "recorded"                   # 블록이 무효라 기록 소속이 아니다


@pytest.mark.parametrize("param", ["cluster", "namespace", "pvc", "storage_class", "user", "requester"])
def test_filter_value_ok_is_exactly_the_route_validation(uw, param):
    """술어 = 경로별 필터 파라미터 검증(같은 값을 storage-totals 에 실었을 때 200 ⇔ True). 길이는 글자 수(바이트 아님), 제어 문자는
    C0·DEL 만(C1·줄 구분자는 받는다), 끝 개행도 거절."""
    limit = 512 if param == "requester" else 253
    values = ["", "a", "a b", " a ", "%&=+#?", "é", "가" * limit, "가" * (limit + 1), "a" * limit, "a" * (limit + 1),
              "a\tb", "a\nb", "ab\n", "\nab", "a\x01", "\x1f", "\x7f", "a\x00b", "\x80", "\u0085", " ", "﻿"]
    got = {}
    for v in values:
        r = uw.client.get(TOTALS, params={"storage": NFS, param: v})
        assert r.status_code in (200, 422), (v, r.text)
        got[v] = (r.status_code == 200, filter_value_ok(param, v))
    assert all(route == pred for route, pred in got.values()), {repr(v): g for v, g in got.items() if g[0] != g[1]}
    assert got["a" * limit] == (True, True) and got["a" * (limit + 1)] == (False, False) and got[""] == (False, False)
    assert filter_value_ok(param, None) is False and filter_value_ok(param, 7) is False


# ---- 적용 축 ----

def test_without_pv_targets_the_requester_axis_is_applied(uw):
    for by in (None, "user", "namespace", "pvc", "storage_class", "cluster"):
        b = _bd(uw, CEPH, **({"by": by} if by else {}))
        # filesystem 스토리지: PV 안 대상이 없다 -- 422 가 아니라 요청자로 적용하고 받은 값을 돌려준다
        assert (b["by"], b["requested_by"]) == ("requester", by)
        assert b["scope"] == {"inventory": 0, "recorded": 0, "outside": 2, "unknown": 0} and b["cluster_count"] == 0
        assert _summary(b["rows"], "requester_id") == [("alice", 1, 5), ("mason", 1, 4)]
        assert b["extras"] == {"outside": None, "unknown": None, "none": None}
    # PV 안 대상이 있으면 명시한 PV 축 그대로
    assert (_bd(uw, by="cluster")["by"], _bd(uw, GPFS, by="user")["by"]) == ("cluster", "user")


def test_an_empty_storage_is_zero_rows_and_unknown_totals_not_zero(uw):
    for storage in ("", "never-scanned"):
        b = _bd(uw, storage)
        assert (b["by"], b["target_count"], b["rows"], b["rows_truncated"], b["cluster_count"]) == (
            "requester", 0, [], False, 0)
        assert b["scope"] == {"inventory": 0, "recorded": 0, "outside": 0, "unknown": 0}
        assert b["storage_total"] == {"summed_target_count": 0, "total_bytes": {"sum": None, "known": 0, "unknown": 0}}
        assert b["extras"] == {"outside": None, "unknown": None, "none": None}
        assert (b["pending_count"], b["unreadable_count"], b["progressed"]) == (0, 0, False)


# ---- 순위·상한 ----

def test_rank_is_bytes_then_targets_then_key_with_unknown_last(uw, monkeypatch):
    seed = lambda target, requester, at, total: _seed_scan(  # noqa: E731
        uw.repos, uw.db, uw.art, storage=CEPH, target=target, requester=requester, at=at, total=total)
    seed("b/one", "bob", "2026-10-27T01:00:00Z", 10)
    seed("b/two", "bob", "2026-10-27T02:00:00Z", 20)
    seed("c/one", "carol", "2026-10-27T03:00:00Z", 30)
    seed("z/none", "zed", "2026-10-27T04:00:00Z", None)          # 리포트 없음 -- 실 사용량 모름(bytes_count 도 없다)
    seed("y/zero", "yan", "2026-10-27T05:00:00Z", 0)             # 빈 트리 -- 0 은 값이다
    b = _bd(uw, CEPH, by="requester")
    # 같은 30: 대상이 많은 bob 이 먼저. 0 은 모름(None)보다 앞이다(null ≠ 0 -- 모름은 맨 뒤)
    assert _summary(b["rows"], "requester_id") == [("bob", 2, 30), ("carol", 1, 30), ("alice", 1, 5), ("mason", 1, 4),
                                                   ("yan", 1, 0), ("zed", 1, None)]
    assert b["rows"][-1]["total_bytes"] == {"sum": None, "known": 0, "unknown": 1}
    assert b["unreadable_count"] == 1
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "FACET_LIST_CAP", 2)
    capped = _bd(uw, CEPH, by="requester")
    # 상한은 모든 묶음을 계산한 뒤 순위에서 자른다 -- 조용히 자르지 않는다
    assert ([r["requester_id"] for r in capped["rows"]], capped["rows_truncated"]) == (["bob", "carol"], True)
    monkeypatch.setattr(routes_usage, "FACET_LIST_CAP", 6)
    assert _bd(uw, CEPH, by="requester")["rows_truncated"] is False


# ---- 창·읽기 예산 ----

def test_window_truncation_follows_the_facets_window(uw, monkeypatch):
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_WINDOW_LIMIT", 5)
    b = _bd(uw)
    assert (b["window_truncated"], b["target_count"], b["window_limit"]) == (True, 5, 5)
    # 창 = 최근 스캔된 5개(c2/lab-y·proof-ov·selfnl·d9·weights)
    assert _summary(b["rows"], "dms_user") == [("cocoa.song", 1, 900), ("alice", 1, 700), ("junwoo.song", 1, 700)]
    f = uw.client.get(FACETS, params={"storage": NFS}).json()
    assert b["scope"] == f["scope"] and {r["dms_user"]: r["target_count"] for r in b["rows"]} == {
        u["dms_user"]: u["target_count"] for u in f["users"]}
    # PV 축은 창이 넘쳐도 필터 합계와 같다(같은 판정 창 -- 둘 다 「최근 스캔된 N개 기준」 부분 값)
    for row in b["rows"]:
        t = _totals(uw, NFS, **row["filter"])
        assert t["truncated"] is True and {k: row[k] for k in _VALUE_KEYS} == {k: t[k] for k in _VALUE_KEYS}
    # 요청자 축은 기준이 다르다: 여기는 스토리지 창 안에서 묶고, 필터 합계는 그 요청자의 대상만으로 창을 짓는다 -- 창(5개 모두
    # mason)에 없는 alice 는 행이 없지만 alice 필터 합계는 그의 대상 둘이다(docstring 「요청자 축만 창이 넘치면 다르다」)
    req = _bd(uw, by="requester")
    assert _summary(req["rows"], "requester_id") == [("mason", 5, 1000 + 700 + 200 + 300)]     # weights 는 c2/lab-y 의 하위
    assert _totals(uw, NFS, requester="alice")["target_count"] == 2
    assert NFS_DESC[:5] == ["c2/lab-y", "team-a/datasets/proof-ov", "proof-nl/a/selfnl", "proof-d9/alice/latest",
                            "c2/lab-y/weights"]


def test_read_budget_spans_every_group_top_and_progresses(uw, monkeypatch):
    # 모든 묶음·덧붙임·전체의 최상위 합집합을 한 예산으로 읽는다 -- 전체에선 하위인 weights 도 cocoa.song 묶음의 최상위라 읽는다
    # (합집합 = 8개). 예산 2 면 네 번에 걸쳐 차고, 다 읽은 뒤의 호출은 progressed False(포탈의 멈춤 신호).
    from dms.api import routes_usage
    monkeypatch.setattr(routes_usage, "_TOTALS_READ_BUDGET", 2)
    seen = []
    for _ in range(5):
        b = _bd(uw)
        seen.append((b["pending_count"], b["progressed"]))
        if len(seen) == 1:
            # 첫 호출은 최근 스캔 순 앞의 둘(c2/lab-y·proof-ov)만 읽었다 -- 나머지는 이번 응답에서 모름(0 으로 접지 않는다)
            alice = next(r for r in b["rows"] if r["dms_user"] == "alice")
            assert alice["total_bytes"] == {"sum": 700, "known": 1, "unknown": 2}
            assert b["storage_total"]["total_bytes"] == {"sum": 1700, "known": 2, "unknown": 5}
    assert seen == [(6, True), (4, True), (2, True), (0, True), (0, False)]
    _oracle(uw, NFS, b)


# ---- 입력 검증·권한 ----

@pytest.mark.parametrize("params", [{}, {"storage": "a\x00b"}, {"storage": NFS, "by": ""}, {"storage": NFS, "by": "users"},
                                    {"storage": NFS, "by": "User"}, {"storage": NFS, "by": "scope"},
                                    {"storage": "s" * 513}])
def test_bad_parameters_are_422(uw, params):
    assert uw.client.get(BREAKDOWN, params=params).status_code == 422, params


def test_breakdown_needs_an_admin(uw):
    uw.client.cookies.clear()
    assert uw.client.get(BREAKDOWN, params={"storage": NFS}).status_code == 401
    assert uw.client.post("/api/auth/login", json={"username": "alice", "password": "pw"}).status_code == 200
    assert uw.client.get(BREAKDOWN, params={"storage": NFS}).status_code == 403


def test_requester_lookup_keeps_every_id_list_within_the_real_chunk(tmp_path, monkeypatch):
    # 실제 상수(500)로 1,100 대상: 요청자 조회(target IN)도 한 문장의 자리표시자가 500 을 넘지 않는다. 반은 요청 행이 있고(alice)
    # 반은 고아(요청자 모름 -- none).
    import re
    art = tmp_path / "artifacts"
    client = _client(tmp_path, art)
    _login(client, "admin", "admin")
    repos = client.app.state.repos
    _bulk_jobs(repos.db, 1100, reports_for=0, art=art)
    rows = repos.db.query("SELECT request_id, target FROM data_jobs ORDER BY target")
    with repos.db.transaction():
        for i, r in enumerate(rows[:550]):
            repos.db.execute(
                """INSERT INTO requests (request_id, commit_order, operation, requester_id, actor, resource_key, priority,
                                         payload, state, created_at, updated_at)
                   VALUES (:r, :o, 'scan', 'alice', 'alice', :k, 'mid', '{}', 'Succeeded', :at, :at)""",
                {"r": r["request_id"], "o": i + 1, "k": f"k{i}", "at": "2026-09-01T00:00:00Z"})
    widest = []
    real_query = repos.db.query

    def query(sql, params=None):
        widest.append(max(len(set(re.findall(rf":{p}\d+", sql))) for p in ("j", "t", "d")))
        return real_query(sql, params)
    monkeypatch.setattr(repos.db, "query", query)
    b = client.get(BREAKDOWN, params={"storage": "s1", "by": "requester"}).json()
    assert [(r["requester_id"], r["target_count"]) for r in b["rows"]] == [("alice", 550)]
    assert b["extras"]["none"]["target_count"] == 550
    assert b["storage_total"]["total_bytes"]["known"] == 1100        # 못 읽은·지연 대상도 실 사용량은 bytes_count
    assert (b["unreadable_count"], b["pending_count"], b["progressed"]) == (500, 600, True)
    assert max(widest) == 500


# ---- 순수 판정: 묶음 키 = 필터 판정 ----

def test_group_keys_are_exactly_the_filter_judgement():
    """키 k 가 group_keys(attr, by) 에 있다 ⇔ matches(attr, PvFilter(**group_filter(by, k)), users) -- 루트 충돌(정체 여럿)·SC 없음/빈
    값·바인딩 없는 namespace·PV 밖·모름·기록 소속을 섞은 무작위 판정으로."""
    rng = random.Random(20261030)
    clusters, namespaces, pvcs, classes = ["c1", "c2"], ["n1", "n2", "n3"], ["p1", "p2"], ["s1", "s2", None, ""]
    for _ in range(60):
        users = {(c, ns): rng.sample(["u1", "u2", "u3"], rng.randint(0, 3)) for c in clusters for ns in namespaces
                 if rng.random() < 0.7}
        idents = lambda: tuple(sorted({(rng.choice(clusters), rng.choice(namespaces), rng.choice(pvcs), "v",  # noqa: E731
                                        rng.choice(classes)) for _ in range(rng.randint(1, 3))}, key=repr))
        index = {f"r{i}": idents() for i in range(4)}
        rec = {"rec/x": [json.dumps({"pv": {"cluster": rng.choice(clusters), "namespace": rng.choice(namespaces),
                                            "pvc": rng.choice(pvcs), "volume": None, "storage_class": rng.choice(classes),
                                            "root": "rec"}})]}
        inp = Inputs(index=MappingProxyType(index), recorded=rec, users=users, rest=rng.choice(["outside", "unknown"]))
        attrs = [attribute(t, inp) for t in ["r0/a", "r1", "r2/b/c", "r3/d", "rec/x", "elsewhere"]]
        candidates = {"user": [(u,) for u in ("u1", "u2", "u3")], "cluster": [(c,) for c in clusters],
                      "namespace": [(c, ns) for c in clusters for ns in namespaces],
                      "pvc": [(c, ns, p) for c in clusters for ns in namespaces for p in pvcs],
                      "storage_class": [("s1",), ("s2",)]}
        for a in attrs:
            for by in PV_GROUP_AXES:
                got = group_keys(a, by)
                assert len(got) == len(set(got))
                for key in candidates[by]:
                    assert (key in got) == matches(a, PvFilter(**group_filter(by, key)), users), (a, by, key)
                assert set(got) <= set(candidates[by])
    assert group_fields("pvc", ("c", "n", "p")) == {"cluster": "c", "namespace": "n", "pvc": "p"}
    assert group_filter("user", ("alice",)) == {"user": "alice"} and group_fields("user", ("alice",)) == {"dms_user": "alice"}
    assert group_filter("requester", ("bob",)) == {"requester": "bob"}


def test_breakdown_groups_keep_window_order_and_each_target_once_per_group():
    inp = Inputs(index=MappingProxyType({"a": (("c", "n", "p1", "v", "s"), ("c", "n", "p2", "v", "s"))}), recorded={},
                 users={("c", "n"): ["u1", "u2", "u1"]}, rest="outside")
    heads = [{"target": t, "job_id": t} for t in ("a/x", "z", "a", "a/y")]
    attrs = [attribute(h["target"], inp) for h in heads]
    groups, extras = breakdown_groups(heads, attrs, "namespace")
    # 루트 충돌로 정체가 둘이어도 같은 (cluster, namespace) 묶음엔 한 번, 머리 순서 그대로
    assert {k: [h["target"] for h in v] for k, v in groups.items()} == {("c", "n"): ["a/x", "a", "a/y"]}
    assert ([h["target"] for h in extras["outside"]], extras["unknown"], extras["none"]) == (["z"], [], None)
    groups, _ = breakdown_groups(heads, attrs, "pvc")
    assert list(groups) == [("c", "n", "p1"), ("c", "n", "p2")]
    groups, extras = breakdown_groups(heads, attrs, "requester", {"a/x": ["q", "q", "r"], "a": ["r"]})
    assert {k: [h["target"] for h in v] for k, v in groups.items()} == {("q",): ["a/x"], ("r",): ["a/x", "a"]}
    assert ([h["target"] for h in extras["none"]], extras["outside"], extras["unknown"]) == (["z", "a/y"], None, None)
    with pytest.raises(ValueError):
        breakdown_groups(heads, attrs, "scope")
