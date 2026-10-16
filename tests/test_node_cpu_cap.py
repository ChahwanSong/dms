"""노드 CPU 상한(2026-10-16, src/dms/node_cpu.py 모듈 docstring) -- 워커가 노드당 프로세스 수만큼 CPU 를 요청하므로
planner·stepper 가 노드가 담을 수 있는 만큼으로 줄이고(클램프), 1코어도 못 담는 노드는 빼고, 0대면 거부한다.

층별로 고정한다: ① 수량 파싱·상한 공식 ② 에이전트 보고 모양 ③ placement(제외·사유·거부 코드·fail-open) ④ resolve_fanout
(클램프·실제로 고른 노드 집합·nsync 양면·worker_pool 기록 키) ⑤ planner(worker_pool·이벤트 1회·즉시 거부) ⑥ stepper
(이미 계획된 잡의 재클램프 -- 줄이기만·멱등·fail-closed·fail-open) ⑦ 설정."""
import sys
import types

import pytest

from dms.agent.probes import _read_node_incluster, k8s_node_cpu, probe_k8s_node
from dms.agent.runner import build_report
from dms.config import Settings, SettingsError
from dms.domain import DataJobState, RequestState
from dms.execution import StubExecutionAdapter
from dms.identity import ResolvedIdentity, StubIdentityResolver
from dms.node_cpu import (DEFAULT_NODE_CPU_RESERVE_MILLICORES, LAUNCHER_CPU_REQUEST_MILLICORES, node_cpu_cap,
                          parse_cpu_millicores, per_node_limit)
from dms.placement import PlacementError, eligible_nodes, resolve_fanout, select_tool_and_candidates
from dms.planner import Planner
from dms.repositories import Repositories
from dms.stepper import JobStepper

NOW = "2026-08-02T10:00:00Z"
ALICE = ResolvedIdentity("alice", 10001, 10000, ("dmsusers",), False)
TOOLS = ("dscan", "dsync", "nsync", "drm")
POLICY = {"max_nodes": 3, "procs_per_node": 8, "queue": "dms-data", "default_priority": "mid",
          "max_priority": "high", "execution_timeout_seconds": 3600, "enabled": 1}


def _report(node, storages=("s1",), *, alloc=None, identities=("alice",), tools=TOOLS):
    """alloc: 밀리코어 int(보고된 allocatable) | None(k8s_node 없음 -- 옛 에이전트)."""
    r = {"node_name": node,
         "mounts": [{"storage_name": s, "mount_path": f"/mnt/{s}", "status": "Ready", "writable": True}
                    for s in storages],
         "tools": [{"name": t, "status": "Ready"} for t in tools],
         "identities": [{"username": u, "status": "Ready"} for u in identities]}
    if alloc is not None:
        r["k8s_node"] = {"schedulable": True, "reason": None, "transient": False,
                         "allocatable_cpu_m": alloc, "capacity_cpu_m": alloc + 200}
    return r


def _fresh(*reports):
    return [{"node_name": r["node_name"], "report": r} for r in reports]


def _scan(fresh, **kw):
    return select_tool_and_candidates("scan", fresh, storage_name="s1", owner="alice", privileged=False, **kw)


# ---- ① 수량 파싱·상한 공식 ----

@pytest.mark.parametrize("raw,want", [
    ("1800m", 1800), ("2", 2000), ("1.5", 1500), ("0.5", 500), (".25", 250), ("250u", 0), ("1500000u", 1500),
    ("2000000000n", 2000), ("1e3", 1_000_000), ("1E0", 1000), ("2k", 2_000_000), ("+3", 3000), (" 4 ", 4000),
    ("1Ki", 1_024_000), (2, 2000), (1.5, 1500), ("0", 0),
])
def test_parse_cpu_quantity(raw, want):
    assert parse_cpu_millicores(raw) == want


@pytest.mark.parametrize("raw", [None, "", "abc", "-1", "1.8.0", "1800mm", "m", "1e", "1e99999", "1e30", True, False,
                                 float("nan"), float("inf"), -2, [1], {"cpu": "1"}])
def test_unparsable_or_absurd_quantity_is_unknown(raw):
    # 모름 = None -- 0(정상값, 「1코어도 없다」)과 다르다. 지수 폭탄은 int 로 만들기 전에 끊는다.
    assert parse_cpu_millicores(raw) is None


def test_cap_formula_floor_of_allocatable_minus_launcher_and_reserve():
    assert LAUNCHER_CPU_REQUEST_MILLICORES == 100 and DEFAULT_NODE_CPU_RESERVE_MILLICORES == 500

    def cap(alloc, reserve=500):
        return node_cpu_cap({"k8s_node": {"allocatable_cpu_m": alloc}}, reserve)
    assert cap(1800) == 1              # 테스트베드: 1800 − 100 − 500 = 1200 → 1
    assert cap(1599) == 0 and cap(1600) == 1
    assert cap(4000) == 3 and cap(64000) == 63
    assert cap(1800, reserve=0) == 1 and cap(2100, reserve=0) == 2
    assert cap(0) == 0 and cap(100) == 0   # 음수로 내려가지 않는다(0 = 1코어도 없음)


@pytest.mark.parametrize("report", [
    None, {}, {"k8s_node": None}, {"k8s_node": {"schedulable": None, "reason": "not_in_cluster"}},
    {"k8s_node": {"allocatable_cpu_m": None}}, {"k8s_node": {"allocatable_cpu_m": "1800m"}},
    {"k8s_node": {"allocatable_cpu_m": True}}, {"k8s_node": {"allocatable_cpu_m": -5}},
    {"k8s_node": {"allocatable_cpu_m": 1800.0}}, {"k8s_node": "x"}, "x",
])
def test_cap_is_unknown_for_missing_or_malformed_reports(report):
    # 보고는 DB 신뢰 경계 밖 -- 모양이 틀리면 모름(fail-open). cpu_count 로 대신하지 않는다.
    assert node_cpu_cap(report, 500) is None


def test_per_node_limit_min_over_known_and_duplicate_nodes_split_the_cap():
    assert per_node_limit(["a", "b"], {"a": 3, "b": 1}) == 1
    assert per_node_limit(["a", "b"], {"a": 3, "b": None}) == 3          # 모르는 노드는 제약을 더하지 않는다
    assert per_node_limit(["a", "b"], {"a": None, "b": None}) is None
    assert per_node_limit(["a", "a", "b"], {"a": 3, "b": 4}) == 1        # a 에 워커 둘(nsync 양면) → 3 // 2
    assert per_node_limit([], {}) is None


# ---- ② 에이전트 보고 ----

def test_agent_reports_allocatable_and_capacity_in_millicores():
    node = {"unschedulable": False, "taints": [], "allocatable_cpu": "1800m", "capacity_cpu": "2"}
    assert k8s_node_cpu(node) == {"allocatable_cpu_m": 1800, "capacity_cpu_m": 2000}
    out = probe_k8s_node("n1", read_node=lambda n: node)
    assert out == {"schedulable": True, "reason": None, "transient": False,
                   "allocatable_cpu_m": 1800, "capacity_cpu_m": 2000}
    # 옛 읽기 함수(키 없음)·이상한 수량은 그 값만 모름 -- 스케줄 판정은 그대로
    out = probe_k8s_node("n1", read_node=lambda n: {"unschedulable": True, "allocatable_cpu": "lots"})
    assert out["schedulable"] is False and out["allocatable_cpu_m"] is None and out["capacity_cpu_m"] is None
    # 실패 경로의 모양은 그대로(CPU 키 없음 = 모름)
    assert probe_k8s_node("n1", environ={}) == {"schedulable": None, "reason": "not_in_cluster"}


def test_incluster_reader_takes_cpu_from_node_status(monkeypatch):
    ns = types.SimpleNamespace
    node = ns(spec=ns(unschedulable=None, taints=None),
              status=ns(allocatable={"cpu": "1800m", "memory": "3Gi"}, capacity={"cpu": "2"}))
    k8s = types.ModuleType("kubernetes")
    k8s.config = ns(load_incluster_config=lambda: None)
    k8s.client = ns(CoreV1Api=lambda: ns(read_node=lambda name, _request_timeout: node))
    monkeypatch.setitem(sys.modules, "kubernetes", k8s)
    raw = _read_node_incluster("n1")
    assert raw == {"unschedulable": False, "taints": [], "allocatable_cpu": "1800m", "capacity_cpu": "2"}
    node.status = None                         # status 가 비면 모름
    assert _read_node_incluster("n1")["allocatable_cpu"] is None


def test_agent_report_carries_cpu_fields_under_k8s_node():
    r = build_report("n1", [], [], mountinfo_text="", tools_fn=lambda names: [],
                     identities_fn=lambda t: [], os_fn=lambda *a, **k: {},
                     k8s_node_fn=lambda name: {"schedulable": True, "reason": None, "transient": False,
                                               "allocatable_cpu_m": 1800, "capacity_cpu_m": 2000})
    assert r["k8s_node"]["allocatable_cpu_m"] == 1800 and r["k8s_node"]["capacity_cpu_m"] == 2000


# ---- ③ placement ----

def test_node_without_one_core_leaves_candidates_with_insufficient_cpu():
    out = _scan(_fresh(_report("n1", alloc=1800), _report("n2", alloc=1500), _report("n3")))
    assert out["candidates"] == {"primary": ["n1", "n3"]}        # n3 = 모름 = 막지 않는다(fail-open)
    assert out["rejections"] == {"n2": "insufficient_cpu"}
    assert out["node_cpu_caps"] == {"n1": 1, "n3": None}


def test_cpu_check_is_after_tool_and_exclusion_and_before_identity():
    reports = _fresh(_report("n1", alloc=1800), _report("n2", alloc=900, tools=("dsync",)),
                     _report("n3", alloc=900), _report("n4", alloc=900, identities=()))
    out = _scan(reports, excluded_nodes={"n3"})
    assert out["rejections"] == {"n2": "missing_tool:dscan", "n3": "node_excluded", "n4": "insufficient_cpu"}


def test_reserve_setting_moves_the_cap():
    reports = _fresh(_report("n1", alloc=1800), _report("n2", alloc=1800))
    assert _scan(reports, cpu_reserve_m=0)["node_cpu_caps"] == {"n1": 1, "n2": 1}
    with pytest.raises(PlacementError) as e:
        _scan(reports, cpu_reserve_m=800)                       # 1800 − 100 − 800 = 900 → 0
    assert e.value.reason_code == "node_cpu_insufficient"


@pytest.mark.parametrize("op, kwargs", [
    ("scan", {"storage_name": "s1"}),
    ("rm", {"storage_name": "s1"}),
    ("sync", {"source_storage": "s1", "destination_storage": "s1"}),
])
def test_every_node_too_small_rejects_with_node_cpu_insufficient(op, kwargs):
    with pytest.raises(PlacementError) as e:
        select_tool_and_candidates(op, _fresh(_report("n1", alloc=1000), _report("n2", alloc=1599)),
                                   owner="alice", privileged=False, **kwargs)
    assert e.value.reason_code == "node_cpu_insufficient"


def test_exclusion_is_reported_before_cpu_when_un_excluding_would_work():
    # n1 은 막혔지만 CPU 는 충분, n2 는 CPU 부족 -- 관리자가 풀 수 있는 원인(nodes_excluded)을 먼저 말한다
    reports = _fresh(_report("n1", alloc=1800), _report("n2", alloc=1000))
    with pytest.raises(PlacementError) as e:
        _scan(reports, excluded_nodes={"n1"})
    assert e.value.reason_code == "nodes_excluded"
    # 막힌 노드도 CPU 가 부족하면 원인은 CPU 다
    with pytest.raises(PlacementError) as e:
        _scan(_fresh(_report("n1", alloc=1000), _report("n2", alloc=1000)), excluded_nodes={"n1"})
    assert e.value.reason_code == "node_cpu_insufficient"


def test_identity_pending_node_keeps_the_grace_code():
    # CPU 부족 노드와 신원 대기 노드가 섞여 0대면 기존 코드 -- planner 신원 유예가 걸려야 한다
    with pytest.raises(PlacementError) as e:
        _scan(_fresh(_report("n1", alloc=1000), _report("n2", alloc=1800, identities=())))
    assert e.value.reason_code == "no_eligible_nodes"


def test_mount_problem_is_not_blamed_on_cpu():
    with pytest.raises(PlacementError) as e:
        _scan(_fresh(_report("n1", storages=(), alloc=1000)))
    assert e.value.reason_code == "no_eligible_nodes"


def test_eligible_nodes_unknown_allocatable_is_fail_open():
    ok, reasons = eligible_nodes(_fresh(_report("n1"), _report("n2", alloc=None)), "s1", tool="dscan",
                                 owner="alice", privileged=False, require_writable=False)
    assert ok == ["n1", "n2"] and reasons == {}


# ---- ④ resolve_fanout ----

def test_fanout_clamps_per_node_to_min_cap_and_records_both_values():
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2"]}, priority="mid", node_cpu_caps={"n1": 4, "n2": 2})
    assert (out["procs_per_node"], out["procs_per_node_requested"], out["process_count"]) == (2, 8, 4)
    assert out["node_cpu_caps"] == {"n1": 4, "n2": 2} and out["cpu_cap_known"] is True
    assert out["candidates"] == {"primary": ["n1", "n2"]}


def test_fanout_does_not_raise_per_node_above_policy_or_request():
    out = resolve_fanout(POLICY, {"primary": ["n1"]}, priority="mid", requested_procs_per_node=2,
                         node_cpu_caps={"n1": 63})
    assert (out["procs_per_node"], out["procs_per_node_requested"], out["process_count"]) == (2, 2, 2)


def test_fanout_unknown_caps_are_fail_open_and_recorded():
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2"]}, priority="mid", node_cpu_caps={"n1": None})
    assert out["procs_per_node"] == 8 and out["process_count"] == 16
    assert out["node_cpu_caps"] == {"n1": None, "n2": None} and out["cpu_cap_known"] is False
    partly = resolve_fanout(POLICY, {"primary": ["n1", "n2"]}, priority="mid", node_cpu_caps={"n1": 3, "n2": None})
    assert partly["procs_per_node"] == 3 and partly["cpu_cap_known"] is False
    # 인자 생략(옛 호출자)도 모름
    assert resolve_fanout(POLICY, {"primary": ["n1"]}, priority="mid")["procs_per_node"] == 8


def test_fanout_caps_only_the_nodes_it_actually_picks():
    # max_nodes 3: 이름순 앞 3대(n1..n3)만 쓴다 -- 잘린 n4 의 작은 상한이 프로세스 수를 줄이면 안 되고,
    # 앞쪽 n2 의 작은 상한은 줄여야 한다(planner 가 굳히는 집합 == 상한을 본 집합).
    cand = {"primary": ["n1", "n2", "n3", "n4"]}
    out = resolve_fanout(POLICY, cand, priority="mid", node_cpu_caps={"n1": 8, "n2": 8, "n3": 8, "n4": 1})
    assert out["candidates"] == {"primary": ["n1", "n2", "n3"]} and out["procs_per_node"] == 8
    assert set(out["node_cpu_caps"]) == {"n1", "n2", "n3"}
    out = resolve_fanout(POLICY, cand, priority="mid", node_cpu_caps={"n1": 8, "n2": 2, "n3": 8, "n4": 8})
    assert out["procs_per_node"] == 2 and out["process_count"] == 6


def test_fanout_nsync_uses_both_sides_and_splits_a_node_on_both_sides():
    out = resolve_fanout(POLICY, {"source": ["a", "b"], "destination": ["c"]}, priority="mid",
                         node_cpu_caps={"a": 4, "b": 8, "c": 3})
    assert out["candidates"] == {"source": ["a", "b"], "destination": ["c"]}
    assert (out["procs_per_node"], out["process_count"]) == (3, 9)
    # 같은 노드가 양면이면 그 노드에 워커가 둘 -- 상한을 나눈다(5 // 2 = 2)
    out = resolve_fanout(POLICY, {"source": ["a"], "destination": ["a"]}, priority="mid", node_cpu_caps={"a": 5})
    assert (out["procs_per_node"], out["process_count"]) == (2, 4)
    with pytest.raises(PlacementError) as e:
        resolve_fanout(POLICY, {"source": ["a"], "destination": ["a"]}, priority="mid", node_cpu_caps={"a": 1})
    assert e.value.reason_code == "node_cpu_insufficient"


# ---- ⑤ planner ----

class _PSettings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    planner_identity_grace_seconds = 300


def _planner_world(db, allocs, *, max_nodes=3, procs_per_node=2, settings=None):
    repos = Repositories(db)
    repos.storages.create(storage_name="s1", mount_path="/mnt/s1", managed_root="/mnt/s1/dms",
                          backend_type="cephfs", actor="admin")
    repos.storages.set_status("s1", "Ready", "ready_nodes=3")
    repos.control.upsert_policy("scan", max_nodes=max_nodes, procs_per_node=procs_per_node, queue="dms-data",
                                default_priority="mid", max_priority="high", preview_timeout_seconds=3600,
                                execution_timeout_seconds=3600, enabled=True, actor="admin")
    for n, alloc in allocs.items():
        repos.agents.ingest(n, _report(n, alloc=alloc), reported_at="2026-08-02T09:59:00Z")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k",
                                payload={"storage": "s1", "target": "t"}, priority="mid")
    planner = Planner(repos, StubIdentityResolver({"alice": ALICE}), settings=settings or _PSettings())
    return repos, rid, planner


def _events(repos, rid, kind):
    return [e for e in repos.observability.events_for_request(rid) if e["event_type"] == kind]


def test_planner_clamps_to_node_cpu_and_records_it_once(db):
    # 테스트베드 모양: 정책 ppn 2, 노드 allocatable 1800m → 상한 1
    repos, rid, planner = _planner_world(db, {"n1": 1800, "n2": 1800, "n3": 1800})
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    wp = job["worker_pool"]
    assert wp["candidates"] == {"primary": ["n1", "n2", "n3"]}
    assert (wp["node_count"], wp["process_count"]) == (3, 3)
    assert (wp["procs_per_node"], wp["procs_per_node_requested"]) == (1, 2)
    assert wp["node_cpu_caps"] == {"n1": 1, "n2": 1, "n3": 1} and wp["cpu_cap_known"] is True
    [ev] = _events(repos, rid, "procs_clamped_to_node_cpu")
    assert ev["component"] == "planner" and ev["severity"] == "info"


def test_planner_without_clamp_records_no_event_and_keeps_policy_value(db):
    repos, rid, planner = _planner_world(db, {"n1": 4000, "n2": 4000})
    planner.run_once(now_iso=NOW)
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    assert (job["worker_pool"]["procs_per_node"], job["worker_pool"]["process_count"]) == (2, 4)
    assert _events(repos, rid, "procs_clamped_to_node_cpu") == []


def test_planner_candidates_and_cap_set_are_the_same_truncated_set(db):
    # n1..n3 고르고(max_nodes 2 → n1, n2) -- n3 의 상한은 계획에 끼지 않는다
    repos, rid, planner = _planner_world(db, {"n1": 4000, "n2": 4000, "n3": 1600}, max_nodes=2)
    planner.run_once(now_iso=NOW)
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    wp = job["worker_pool"]
    assert wp["candidates"] == {"primary": ["n1", "n2"]}
    assert set(wp["node_cpu_caps"]) == {"n1", "n2"} and wp["procs_per_node"] == 2


def test_planner_skips_small_nodes_and_rejects_when_none_fit(db):
    repos, rid, planner = _planner_world(db, {"n1": 1800, "n2": 1000})
    planner.run_once(now_iso=NOW)
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    assert job["worker_pool"]["candidates"] == {"primary": ["n1"]}
    assert job["worker_pool"]["rejections"] == {"n2": "insufficient_cpu"}

    rid2 = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k2",
                                 payload={"storage": "s1", "target": "u"}, priority="mid")
    repos.agents.ingest("n1", _report("n1", alloc=1000), reported_at="2026-08-02T09:59:30Z")
    planner.run_once(now_iso=NOW)
    # 유예 없이 즉시 거부(allocatable 은 기다려도 늘지 않는다)
    assert repos.requests.get(rid2)["state"] == RequestState.REJECTED.value
    assert repos.requests.last_reason_code(rid2) == "node_cpu_insufficient"


def test_planner_unknown_allocatable_plans_unclamped(db):
    repos, rid, planner = _planner_world(db, {"n1": None, "n2": None})
    planner.run_once(now_iso=NOW)
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    wp = job["worker_pool"]
    assert (wp["procs_per_node"], wp["process_count"], wp["cpu_cap_known"]) == (2, 4, False)
    assert wp["node_cpu_caps"] == {"n1": None, "n2": None}


def test_planner_reads_the_reserve_setting(db):
    class _Reserve(_PSettings):
        node_cpu_reserve_millicores = 0
    repos, rid, planner = _planner_world(db, {"n1": 3000}, settings=_Reserve())   # (3000-100-0)//1000 = 2
    planner.run_once(now_iso=NOW)
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    assert job["worker_pool"]["procs_per_node"] == 2                               # 기본 500 이었으면 1


# ---- ⑥ stepper: 이미 계획된 잡 ----

class _SSettings:
    agent_report_stale_seconds = 300
    preview_ttl_seconds = 86400
    artifact_base_uri = "file:///art"
    allow_privileged_requesters = False
    privileged_requesters = frozenset()
    vcjob_ttl_seconds = 86400


def _planned_scan(repos, nodes=("n1", "n2"), *, process_count=16, state=None, extra_wp=None):
    """이 기능 전에 계획된 잡 모양(procs_per_node 키 없음, 노드당 8)."""
    if repos.storages.get("s1") is None:
        repos.storages.create(storage_name="s1", mount_path="/s1", managed_root="/s1/dms",
                              backend_type="cephfs", actor="test")
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
                                resource_key=f"k-{len(repos.requests.list_pending())}",
                                payload={"storage": "s1", "target": "a"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    wp = {"tool": "dscan", "identity": {"uid": 10001, "gid": 10000, "username": "alice",
                                        "groups": [], "privileged": False},
          "candidates": {"primary": list(nodes)}, "node_count": len(nodes), "process_count": process_count,
          "queue": "dms-data", "priority_class": "dms-mid", **(extra_wp or {})}
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid", storage_name="s1",
                                     target="a", options={}, tool="dscan", worker_pool=wp,
                                     precondition={}, actor="planner")
    if state is not None:
        repos.data_jobs.set_job_state(jid, state, actor="stepper")
    return rid, jid


def _ingest(repos, allocs):
    for n, alloc in allocs.items():
        repos.agents.ingest(n, _report(n, alloc=alloc), reported_at="2026-08-02T09:59:00Z")


def test_stepper_reclamps_a_job_planned_before_the_cap_and_writes_it_back(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos)
    _ingest(repos, {"n1": 1800, "n2": 4000})
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_SSettings())
    assert stepper.run_once()[jid] == "Preflight"
    [spec] = adapter.submitted_specs()
    assert spec.process_count == 2                                 # 2노드 × 상한 1
    wp = repos.data_jobs.get_job(jid)["worker_pool"]
    assert (wp["process_count"], wp["procs_per_node"], wp["procs_per_node_requested"]) == (2, 1, 8)
    assert wp["node_cpu_caps"] == {"n1": 1, "n2": 3} and wp["cpu_cap_known"] is True
    [ev] = _events(repos, rid, "procs_clamped_to_node_cpu")
    assert ev["component"] == "stepper" and ev["payload"]["phase"] == "preflight"
    # 다음 제출(실행)은 줄어든 값을 그대로 읽는다 -- 다시 줄일 것이 없어 이벤트가 늘지 않는다(멱등)
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    specs = adapter.submitted_specs()
    assert [s.phase for s in specs] == ["preflight", "execution"] and specs[1].process_count == 2
    assert len(_events(repos, rid, "procs_clamped_to_node_cpu")) == 1


def test_stepper_never_raises_processes(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count=2)               # 노드당 1
    _ingest(repos, {"n1": 64000, "n2": 64000})
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    assert adapter.submitted_specs()[0].process_count == 2
    assert "procs_per_node" not in repos.data_jobs.get_job(jid)["worker_pool"]   # 쓰지 않았다
    assert _events(repos, rid, "procs_clamped_to_node_cpu") == []


def test_stepper_unknown_allocatable_is_fail_open(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos)
    _ingest(repos, {"n1": None, "n2": 1800})                        # n2 만 알면 그 상한으로
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    assert adapter.submitted_specs()[0].process_count == 2
    rid2, jid2 = _planned_scan(repos, nodes=("n3",), process_count=8)   # 보고 없음(옛 에이전트) → 그대로
    adapter2 = StubExecutionAdapter()
    JobStepper(repos, adapter2, settings=_SSettings()).run_once()
    assert [s.process_count for s in adapter2.submitted_specs() if s.job_id == jid2] == [8]


def test_stepper_fails_closed_when_an_assigned_node_cannot_hold_one_worker(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos)
    _ingest(repos, {"n1": 1800, "n2": 1000})                         # n2 상한 0
    adapter = StubExecutionAdapter()
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Rejected"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_cpu_insufficient_at_step"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert adapter.submitted_specs() == []
    [ev] = _events(repos, rid, "node_cpu_insufficient_at_step")
    assert ev["payload"]["node_cpu_caps"] == {"n1": 1, "n2": 0}


def test_stepper_executing_job_fails_with_the_execution_state_convention(db):
    # 컨펌 뒤(Executing) 실행 전 재점검 제출 직전 -- 실행 상태의 fail-closed 는 Failed
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, state=DataJobState.EXECUTING)
    _ingest(repos, {"n1": 1000, "n2": 1000})
    adapter = StubExecutionAdapter()
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_cpu_insufficient_at_step"
    assert adapter.submitted_specs() == []


def test_stepper_keeps_the_planner_recorded_request_value(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count=4,
                             extra_wp={"procs_per_node": 2, "procs_per_node_requested": 8})
    _ingest(repos, {"n1": 1800, "n2": 1800})
    JobStepper(repos, StubExecutionAdapter(), settings=_SSettings()).run_once()
    wp = repos.data_jobs.get_job(jid)["worker_pool"]
    assert (wp["procs_per_node"], wp["procs_per_node_requested"], wp["process_count"]) == (1, 8, 2)


def test_stepper_leaves_tampered_process_count_to_the_builder_path(db):
    # 변조 행은 재클램프가 건드리지 않는다 -- 빌더가 ValueError → submit_failed 로 접는 기존 경로(여기선 스텁이라 그대로).
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count="16")
    _ingest(repos, {"n1": 1000, "n2": 1000})
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    assert repos.data_jobs.get_job(jid)["worker_pool"]["process_count"] == "16"
    assert _events(repos, rid, "node_cpu_insufficient_at_step") == []


def test_set_worker_pool_is_conditional_on_state(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos)
    wp = repos.data_jobs.get_job(jid)["worker_pool"]
    before = repos.data_jobs.get_job(jid)["updated_at"]
    assert repos.data_jobs.set_worker_pool(jid, {**wp, "process_count": 2}, expected_state="Running") is False
    assert repos.data_jobs.get_job(jid)["worker_pool"]["process_count"] == 16
    assert repos.data_jobs.set_worker_pool(jid, {**wp, "process_count": 2}, expected_state="Pending") is True
    job = repos.data_jobs.get_job(jid)
    assert job["worker_pool"]["process_count"] == 2 and job["updated_at"] == before


# ---- ⑦ 설정 ----

_VALID = {"DMS_DATABASE_URL": "postgresql://x/y", "DMS_SHARED_TOKEN": "s", "DMS_ADMIN_TOKEN": "a",
          "DMS_SESSION_SECRET": "z"}


def test_reserve_setting_default_override_and_validation():
    assert Settings.from_env(_VALID).node_cpu_reserve_millicores == 500
    assert Settings.from_env({**_VALID, "DMS_NODE_CPU_RESERVE_MILLICORES": "0"}).node_cpu_reserve_millicores == 0
    assert Settings.from_env({**_VALID, "DMS_NODE_CPU_RESERVE_MILLICORES": "1500"}).node_cpu_reserve_millicores == 1500
    for bad in ("-1", "half"):
        with pytest.raises(SettingsError):
            Settings.from_env({**_VALID, "DMS_NODE_CPU_RESERVE_MILLICORES": bad})
