"""노드 CPU 상한(2026-10-16, src/dms/node_cpu.py 모듈 docstring) -- 워커가 노드당 프로세스 수만큼 CPU 를 요청하므로
planner·stepper 가 노드가 담을 수 있는 만큼으로 줄이고(클램프), 1코어도 못 담는 노드는 빼고, 0대면 거부한다.

층별로 고정한다: ① 수량 파싱·상한 공식 ② 에이전트 보고 모양(마지막으로 읽은 allocatable 이어 싣기) ③ placement(제외·
사유·거부 코드·fail-open) ④ resolve_fanout(클램프·실제로 고른 노드 집합·고르는 순서·nsync 양면·worker_pool 기록 키)
⑤ planner(worker_pool·이벤트 1회·즉시 거부·부하 분산) ⑥ stepper(이미 계획된 잡의 재클램프 -- 줄이기만·멱등·fail-closed·
fail-open, 제출된 vcjob 의 PENDING 재검사) ⑦ 설정."""
import sys
import types

import pytest

from dms.agent.probes import _read_node_incluster, k8s_node_cpu, probe_k8s_node
from dms.agent.runner import AgentRunner, build_report, carry_node_cpu
from dms.config import Settings, SettingsError
from dms.domain import DataJobState, RequestState
from dms.execution import ExecStatus, StubExecutionAdapter
from dms.identity import ResolvedIdentity, StubIdentityResolver
from dms.node_cpu import (DEFAULT_NODE_CPU_RESERVE_MILLICORES, LAUNCHER_CPU_REQUEST_MILLICORES, node_cpu_cap,
                          parse_cpu_millicores, per_node_limit)
from dms.placement import (PlacementError, dms_node_load, eligible_nodes, resolve_fanout,
                           select_tool_and_candidates)
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


def test_agent_carries_the_last_read_allocatable_when_node_get_fails():
    # 2026-10-16 리뷰: 서버는 노드의 최신 보고 하나만 읽는다 -- Node GET 이 한 번 실패한 보고가 상한을 「모름」으로 만들어,
    # 그 창에 제출된 vcjob 이 클램프 없이 나가 영원히 Pending 이었다. allocatable 은 정적이라 마지막 값을 이어 싣는다.
    ok = {"schedulable": True, "reason": None, "transient": False, "allocatable_cpu_m": 1800, "capacity_cpu_m": 2000}
    failed = {"schedulable": None, "reason": "probe_failed:ReadTimeoutError"}
    node, last = carry_node_cpu(None, ok)
    assert node == ok and last == {"allocatable_cpu_m": 1800, "capacity_cpu_m": 2000}
    node, last2 = carry_node_cpu(last, failed)
    assert node == {**failed, "allocatable_cpu_m": 1800, "capacity_cpu_m": 2000, "allocatable_stale": True}
    assert last2 == last
    assert node["schedulable"] is None                    # 스케줄 판정은 지어내지 않는다(모름은 모름)
    assert node_cpu_cap({"k8s_node": node}, 500) == 1     # 상한은 이어 실은 값으로 안다
    # 이상한 수량(읽었지만 allocatable 모름)도 마지막 값으로
    weird = {**ok, "allocatable_cpu_m": None, "capacity_cpu_m": None}
    assert carry_node_cpu(last, weird)[0]["allocatable_cpu_m"] == 1800
    # 새로 읽으면 그 값이 기억을 바꾼다(노드 크기 변경), 표식은 붙지 않는다
    node, last3 = carry_node_cpu(last, {**ok, "allocatable_cpu_m": 3800, "capacity_cpu_m": 4000})
    assert "allocatable_stale" not in node and last3 == {"allocatable_cpu_m": 3800, "capacity_cpu_m": 4000}
    # 한 번도 못 읽었으면(재시작 직후·클러스터 밖) 그대로 모름(fail-open)
    assert carry_node_cpu(None, failed) == (failed, None)
    assert carry_node_cpu(None, {"schedulable": None, "reason": "not_in_cluster"})[0] == {
        "schedulable": None, "reason": "not_in_cluster"}
    assert carry_node_cpu(last, None) == (None, last)    # 모양이 다르면 손대지 않는다


def test_agent_runner_reports_the_carried_allocatable(monkeypatch):
    import json

    import httpx
    from dms.config import AgentSettings
    posted = []

    def handler(request):
        posted.append(json.loads(request.content))
        return httpx.Response(200, json={"storages": [], "identity_probe_targets": [], "report_interval_seconds": 60})
    reads = iter([{"unschedulable": False, "allocatable_cpu": "1800m", "capacity_cpu": "2"}, OSError("timeout")])

    def read_node(name):
        v = next(reads)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_k8s_node", lambda name: probe_k8s_node(name, read_node=read_node))
    settings = AgentSettings(api_url="http://api", shared_token="tok", node_name="n1", mountinfo_path="/x")
    runner = AgentRunner(settings, httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api"))
    state = {"storages": [], "probe_targets": [], "interval": 60}
    runner.run_once(state)
    runner.run_once(state)
    first, second = posted[0]["k8s_node"], posted[1]["k8s_node"]
    assert first["allocatable_cpu_m"] == 1800 and "allocatable_stale" not in first
    assert second["reason"] == "probe_failed:OSError" and second["schedulable"] is None
    assert (second["allocatable_cpu_m"], second["capacity_cpu_m"], second["allocatable_stale"]) == (1800, 2000, True)


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
    # max_nodes 3: 고른 3대만 상한을 본다 -- 고르지 않은 n4 의 작은 상한이 프로세스 수를 줄이면 안 된다(planner 가 굳히는
    # 집합 == 상한을 본 집합).
    cand = {"primary": ["n1", "n2", "n3", "n4"]}
    out = resolve_fanout(POLICY, cand, priority="mid", node_cpu_caps={"n1": 8, "n2": 8, "n3": 8, "n4": 1})
    assert out["candidates"] == {"primary": ["n1", "n2", "n3"]} and out["procs_per_node"] == 8
    assert set(out["node_cpu_caps"]) == {"n1", "n2", "n3"}
    # 2026-10-16 리뷰로 바뀐 기대(의도된 변경): 예전엔 이름순 앞 3대(n1..n3)를 고정으로 골라 n2 의 상한 2 가 잡 전체를
    # 노드당 2 로 끌어내렸다(process_count 6). 이제 요청(8)을 다 담는 노드가 충분하면 상한 미달 노드(n2)는 뒤로 밀린다.
    out = resolve_fanout(POLICY, cand, priority="mid", node_cpu_caps={"n1": 8, "n2": 2, "n3": 8, "n4": 8})
    assert out["candidates"] == {"primary": ["n1", "n3", "n4"]}
    assert (out["procs_per_node"], out["process_count"]) == (8, 24)
    assert set(out["node_cpu_caps"]) == {"n1", "n3", "n4"}
    # 담을 노드가 모자라 작은 노드를 써야 하면 그 노드의 상한으로 줄인다(고른 집합의 최솟값 -- 불변식은 그대로).
    out = resolve_fanout(POLICY, {"primary": ["n1", "n2", "n3"]}, priority="mid",
                         node_cpu_caps={"n1": 8, "n2": 2, "n3": 8})
    assert out["candidates"] == {"primary": ["n1", "n2", "n3"]}
    assert (out["procs_per_node"], out["process_count"]) == (2, 6)


def test_fanout_prefers_nodes_that_hold_the_request_over_a_small_infra_node():
    # 리뷰 재현(이질 클러스터): 이름순 첫 노드가 작은 인프라 노드(allocatable 3800m → 상한 3)이고 큰 노드(상한 126)가 넉넉하면
    # 예전엔 [a-infra, c01, c02, c03] 를 골라 노드당 3(요청 64) · 12 프로세스였다.
    caps = {"a-infra": 3, **{f"c{i:02d}": 126 for i in range(1, 6)}}
    policy = {**POLICY, "max_nodes": 4, "procs_per_node": 64}
    out = resolve_fanout(policy, {"primary": sorted(caps)}, priority="mid", node_cpu_caps=caps)
    assert out["candidates"] == {"primary": ["c01", "c02", "c03", "c04"]}
    assert (out["procs_per_node"], out["procs_per_node_requested"], out["process_count"]) == (64, 64, 256)
    # 모르는 상한(None)은 담는 쪽으로 친다(fail-open -- 모르는 노드를 벌하지 않는다)
    out = resolve_fanout({**policy, "max_nodes": 1}, {"primary": ["a-infra", "z-new"]}, priority="mid",
                         node_cpu_caps={"a-infra": 3, "z-new": None})
    assert out["candidates"] == {"primary": ["z-new"]} and out["cpu_cap_known"] is False


def test_fanout_spreads_by_dms_load_then_name():
    cand = {"primary": ["n1", "n2", "n3"]}
    caps = {"n1": 1, "n2": 1, "n3": 1}
    one = {**POLICY, "max_nodes": 1, "procs_per_node": 1}
    # 부하 없음 = 이름순(동률은 이름으로 -- 결정적)
    for _ in range(3):
        assert resolve_fanout(one, cand, priority="mid", node_cpu_caps=caps)["candidates"] == {"primary": ["n1"]}
    # n1 에 DMS 잡이 1코어 예약 -- 다음 잡은 n2(동률 n2·n3 는 이름순)
    assert resolve_fanout(one, cand, priority="mid", node_cpu_caps=caps,
                          node_load={"n1": 1})["candidates"] == {"primary": ["n2"]}
    assert resolve_fanout(one, cand, priority="mid", node_cpu_caps=caps,
                          node_load={"n1": 1, "n2": 1})["candidates"] == {"primary": ["n3"]}
    # 2대를 고르면 가장 한가한 2대, 굳힌 목록은 이름순
    two = {**one, "max_nodes": 2}
    assert resolve_fanout(two, cand, priority="mid", node_cpu_caps=caps,
                          node_load={"n1": 5, "n2": 0, "n3": 1})["candidates"] == {"primary": ["n2", "n3"]}
    # 상한 충족이 부하보다 먼저다: 한가해도 요청을 못 담는 노드는 바쁜 큰 노드 뒤
    out = resolve_fanout({**one, "procs_per_node": 4}, {"primary": ["big", "small"]}, priority="mid",
                         node_cpu_caps={"big": 8, "small": 2}, node_load={"big": 6})
    assert out["candidates"] == {"primary": ["big"]} and out["procs_per_node"] == 4


def test_fanout_nsync_orders_each_side():
    out = resolve_fanout({**POLICY, "max_nodes": 1}, {"source": ["s1", "s2"], "destination": ["d1", "d2"]},
                         priority="mid", node_cpu_caps={"s1": 8, "s2": 8, "d1": 2, "d2": 8},
                         node_load={"s1": 3})
    assert out["candidates"] == {"source": ["s2"], "destination": ["d2"]}
    assert (out["procs_per_node"], out["process_count"]) == (8, 16)


def test_dms_node_load_counts_procs_per_node_on_every_candidate():
    pools = [
        {"candidates": {"primary": ["n1", "n2"]}, "process_count": 4, "procs_per_node": 2},
        # 이 기능 전에 계획된 잡(procs_per_node 없음) -- process_count // 워커 수
        {"candidates": {"primary": ["n2"]}, "process_count": 3},
        # nsync 양면에 같은 노드 -- 워커가 둘이라 두 번
        {"candidates": {"source": ["n3"], "destination": ["n3"]}, "process_count": 2, "procs_per_node": 1},
    ]
    assert dms_node_load(pools) == {"n1": 2, "n2": 5, "n3": 2}


@pytest.mark.parametrize("bad", [
    None, "x", [], {}, {"candidates": None}, {"candidates": {"primary": []}, "process_count": 2},
    {"candidates": {"primary": ["n1"]}, "process_count": "4"},          # 변조: 문자열
    {"candidates": {"primary": ["n1"]}, "process_count": True},
    {"candidates": {"primary": ["n1"]}, "process_count": 10 ** 9},     # 워커당 1024 초과
    {"candidates": {"primary": ["n1"]}, "procs_per_node": 0, "process_count": 0},
    {"candidates": {"primary": "n1"}, "process_count": 2},             # 목록이 아님
])
def test_dms_node_load_skips_tampered_rows(bad):
    # DB 신뢰 경계: 순서 어림값이라 깨진 행은 조용히 건너뛴다(계획 전체를 막지 않는다).
    assert dms_node_load([bad, {"candidates": {"primary": ["n9"]}, "process_count": 1}]) == {"n9": 1}


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


def _more_scans(repos, n):
    return [repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key=f"k{i}",
                                  payload={"storage": "s1", "target": f"t{i}"}, priority="mid")
            for i in range(2, 2 + n)]


def _picked(repos, rid):
    return repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]["candidates"]["primary"]


def test_planner_spreads_jobs_over_idle_nodes(db):
    # 리뷰 재현(test_f2): 상한 1(테스트베드 allocatable 1800m) · max_nodes 1 에서 같은 틱의 스캔 셋이 전부 이름순 첫 노드에
    # 굳어(required nodeAffinity) 한 줄로 기다리고 나머지 노드는 놀았다. 같은 틱에 계획한 잡도 부하로 센다.
    repos, rid, planner = _planner_world(db, {"n1": 1800, "n2": 1800, "n3": 1800}, max_nodes=1, procs_per_node=1)
    rids = [rid] + _more_scans(repos, 2)
    out = planner.run_once(now_iso=NOW)
    assert all(out[r] == "planned" for r in rids)
    assert [_picked(repos, r) for r in rids] == [["n1"], ["n2"], ["n3"]]
    # 다음 틱: 셋 다 1코어씩 예약 중 -- 동률은 이름순(결정적)
    r4 = repos.requests.create(operation="scan", requester_id="alice", actor="alice", resource_key="k-x",
                               payload={"storage": "s1", "target": "x"}, priority="mid")
    planner.run_once(now_iso=NOW)
    assert _picked(repos, r4) == ["n1"]


def test_planner_two_multi_node_jobs_take_disjoint_nodes(db):
    repos, rid, planner = _planner_world(db, {f"n{i}": 1800 for i in range(1, 5)}, max_nodes=2, procs_per_node=1)
    planner.run_once(now_iso=NOW)
    [rid2] = _more_scans(repos, 1)
    planner.run_once(now_iso=NOW)                 # 다른 틱 -- DB 의 비종단 잡에서 부하를 읽는다
    assert _picked(repos, rid) == ["n1", "n2"] and _picked(repos, rid2) == ["n3", "n4"]


def test_planner_load_counts_only_non_terminal_jobs(db):
    repos, rid, planner = _planner_world(db, {"n1": 1800, "n2": 1800}, max_nodes=1, procs_per_node=1)
    planner.run_once(now_iso=NOW)
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    assert _picked(repos, rid) == ["n1"]
    repos.data_jobs.set_job_state(job["job_id"], DataJobState.SUCCEEDED, actor="stepper")   # 끝난 잡은 예약이 없다
    [rid2] = _more_scans(repos, 1)
    planner.run_once(now_iso=NOW)
    assert _picked(repos, rid2) == ["n1"]


def test_planner_skips_a_small_infra_node_when_bigger_nodes_fit(db):
    repos, rid, planner = _planner_world(db, {"a-infra": 3800, "c01": 127000, "c02": 127000},
                                         max_nodes=2, procs_per_node=64)
    planner.run_once(now_iso=NOW)
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["candidates"] == {"primary": ["c01", "c02"]}
    assert (wp["procs_per_node"], wp["process_count"]) == (64, 128)
    assert _events(repos, rid, "procs_clamped_to_node_cpu") == []


def test_planner_load_read_failure_falls_back_to_name_order(db, monkeypatch):
    repos, rid, planner = _planner_world(db, {"n1": 1800, "n2": 1800}, max_nodes=1, procs_per_node=1)

    def boom(**kw):
        raise RuntimeError("db hiccup")
    monkeypatch.setattr(repos.data_jobs, "active_worker_pools", boom)
    assert planner.run_once(now_iso=NOW)[rid] == "planned"    # 순서 어림값 읽기 실패가 계획을 막지 않는다
    assert _picked(repos, rid) == ["n1"]


def test_active_worker_pools_reads_non_terminal_rows_and_skips_bad_json(db):
    repos = Repositories(db)
    _, live = _planned_scan(repos, nodes=("n1",), process_count=1)
    _, done = _planned_scan(repos, nodes=("n2",), process_count=1)
    _, bad = _planned_scan(repos, nodes=("n3",), process_count=1)
    repos.data_jobs.set_job_state(done, DataJobState.FAILED, actor="stepper")
    db.execute("UPDATE data_jobs SET worker_pool = :w WHERE job_id = :j", {"w": "{not json", "j": bad})
    pools = repos.data_jobs.active_worker_pools()
    assert [p["candidates"] for p in pools] == [{"primary": ["n1"]}]
    assert repos.data_jobs.active_worker_pools(limit=0) == []


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


# ---- ⑥b stepper: 제출된 vcjob 의 PENDING 재검사(2026-10-16 리뷰) ----

def _unknown(repos, nodes):
    """옛 에이전트·Node 읽기 실패 보고 -- 상한 모름(fail-open)."""
    for n in nodes:
        repos.agents.ingest(n, _report(n), reported_at="2026-08-02T09:59:00Z")


def _cap_learned(repos, nodes, alloc=1800):
    for n in nodes:
        repos.agents.ingest(n, _report(n, alloc=alloc), reported_at="2026-08-02T10:01:00Z")


NEW_WP = {"procs_per_node": 2, "procs_per_node_requested": 2, "cpu_cap_known": False}


def test_pending_execution_fails_closed_when_the_cap_learned_after_submit_is_smaller(db):
    # 리뷰 재현(test_f2_pending_no_recheck): 상한을 모르는 창에 노드당 2(cpu "2")로 제출된 실행 vcjob 은, 상한 1 을 알게 된
    # 뒤에도 PENDING 폴링이 CPU 를 다시 보지 않아 영원히 기다리며 Inqueue 예약을 쥐었다.
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count=4, extra_wp=NEW_WP)
    _unknown(repos, ("n1", "n2"))
    adapter = StubExecutionAdapter()
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Preflight"
    adapter.script(f"stub-execution-{jid}", [ExecStatus.PENDING] * 50)
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Running"
    assert adapter.submitted_specs()[-1].process_count == 4              # 모르는 창 -- 클램프 없이 나갔다
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Running"   # 아직 모름 = 기다린다
    _cap_learned(repos, ("n1", "n2"))
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_cpu_insufficient_at_step"
    assert adapter.poll(f"stub-execution-{jid}") == ExecStatus.FAILED           # vcjob 을 회수했다(terminate)
    assert len(adapter.submitted_specs()) == 2                                  # 줄여서 다시 내지 않는다
    [ev] = _events(repos, rid, "node_cpu_insufficient_at_step")
    assert ev["payload"]["phase"] == "execution" and ev["payload"]["submitted_procs_per_node"] == 2
    assert ev["payload"]["node_cpu_caps"] == {"n1": 1, "n2": 1}


def _planned_sync(repos, *, nodes=("n1",), process_count=2, extra_wp=None):
    for name in ("src", "dst"):
        if repos.storages.get(name) is None:
            repos.storages.create(storage_name=name, mount_path=f"/{name}", managed_root=f"/{name}/dms",
                                  backend_type="cephfs", actor="test")
    rid = repos.requests.create(operation="sync", requester_id="alice", actor="alice", resource_key="ks",
                                payload={"source_storage": "src", "source": "a", "destination_storage": "dst",
                                         "destination": "b"}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    repos.requests.set_state(rid, RequestState.RUNNING, actor="planner")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    wp = {"tool": "dsync", "identity": {"uid": 10001, "gid": 10000, "username": "alice", "groups": [],
                                        "privileged": False},
          "candidates": {"primary": list(nodes)}, "node_count": len(nodes), "process_count": process_count,
          "queue": "dms-data", "priority_class": "dms-mid", **(extra_wp or {})}
    jid = repos.data_jobs.create_job(rid, plan_id, operation="sync", priority="mid", source_storage="src",
                                     source="a", destination_storage="dst", destination="b", options={},
                                     tool="dsync", worker_pool=wp, precondition={}, actor="planner")
    return rid, jid


def test_pending_preview_is_rejected_when_the_cap_learned_after_submit_is_smaller(db):
    repos = Repositories(db)
    rid, jid = _planned_sync(repos, extra_wp=NEW_WP)
    _unknown(repos, ("n1",))
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_SSettings())
    assert stepper.run_once()[jid] == "Preflight"
    adapter.script(f"stub-preview-{jid}", [ExecStatus.PENDING] * 50)
    assert stepper.run_once()[jid] == "PreviewRunning"
    assert stepper.run_once()[jid] == "PreviewRunning"
    _cap_learned(repos, ("n1",))
    # 미리보기 단계의 fail-closed 는 Rejected(실행 자원이 붙기 전 -- _fail_closed 관례)
    assert stepper.run_once()[jid] == "Rejected"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_cpu_insufficient_at_step"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert adapter.poll(f"stub-preview-{jid}") == ExecStatus.FAILED
    [ev] = _events(repos, rid, "node_cpu_insufficient_at_step")
    assert ev["payload"]["phase"] == "preview"


def test_pending_execution_after_confirm_fails_with_the_execution_state_convention(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count=4, extra_wp=NEW_WP, state=DataJobState.EXECUTING)
    _unknown(repos, ("n1", "n2"))
    adapter = StubExecutionAdapter()
    stepper = JobStepper(repos, adapter, settings=_SSettings())
    assert stepper.run_once()[jid] == "Executing"           # 실행 전 재점검 제출
    adapter.script(f"stub-execution-{jid}", [ExecStatus.PENDING] * 50)
    assert stepper.run_once()[jid] == "Executing"           # 재점검 통과 → 실행 제출(PENDING)
    assert [s.phase for s in adapter.submitted_specs()] == ["exec_preflight", "execution"]
    _cap_learned(repos, ("n1", "n2"))
    assert stepper.run_once()[jid] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_cpu_insufficient_at_step"


def test_pending_vcjob_that_fits_or_is_unknown_keeps_waiting(db):
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count=2, extra_wp={**NEW_WP, "procs_per_node": 1})
    _unknown(repos, ("n1", "n2"))
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    adapter.script(f"stub-execution-{jid}", [ExecStatus.PENDING] * 50)
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    _cap_learned(repos, ("n1", "n2"))                    # 상한 1 == 노드당 1 -- 정상 큐 대기다
    for _ in range(3):
        assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Running"
    _unknown(repos, ("n1",))                             # 모르게 되어도(옛 에이전트) 기다린다
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Running"
    assert _events(repos, rid, "node_cpu_insufficient_at_step") == []


def test_pending_besteffort_vcjob_from_before_worker_cpu_requests_is_not_killed(db):
    # 배포 순간: 워커 CPU 요청 이전 코드가 낸 vcjob(BestEffort)은 상한과 무관하게 앉는다 -- 표식(procs_per_node·
    # worker_cpu_requested) 없는 잡의 PENDING 은 끊지 않는다(일괄 거부 "강화" 금지).
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count=4, state=DataJobState.PREFLIGHT)
    repos.data_jobs.set_phase_ref(jid, "execution", f"stub-execution-{jid}")
    repos.data_jobs.set_job_state(jid, DataJobState.RUNNING, actor="stepper")
    adapter = StubExecutionAdapter()
    adapter.script(f"stub-execution-{jid}", [ExecStatus.PENDING] * 50)
    _cap_learned(repos, ("n1", "n2"))                    # 상한 1 < 노드당 2
    for _ in range(3):
        assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Running"
    assert _events(repos, rid, "node_cpu_insufficient_at_step") == []


def test_legacy_job_submitted_by_this_code_is_marked_and_rechecked(db):
    # 이 기능 전에 계획된 잡(procs_per_node 없음)이라도 실행 vcjob 을 이 코드가 내면 워커가 CPU 를 요청한다 -- 표식을 남기고
    # PENDING 재검사 대상이 된다. preflight(맨 Pod, BestEffort)는 표식을 남기지 않는다.
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count=4)
    _unknown(repos, ("n1", "n2"))
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    assert "worker_cpu_requested" not in repos.data_jobs.get_job(jid)["worker_pool"]
    adapter.script(f"stub-execution-{jid}", [ExecStatus.PENDING] * 50)
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Running"
    wp = repos.data_jobs.get_job(jid)["worker_pool"]
    assert wp["worker_cpu_requested"] is True and "procs_per_node" not in wp
    _cap_learned(repos, ("n1", "n2"))
    assert JobStepper(repos, adapter, settings=_SSettings()).run_once()[jid] == "Failed"
    assert repos.data_jobs.job_transitions(jid)[-1]["reason_code"] == "node_cpu_insufficient_at_step"


def test_new_planner_job_is_not_restamped(db):
    # procs_per_node 키가 있는 잡(새 planner)은 그 키가 곧 근거다 -- 미리보기·실행 제출마다 추가 쓰기를 하지 않는다.
    repos = Repositories(db)
    rid, jid = _planned_scan(repos, process_count=2, extra_wp={**NEW_WP, "procs_per_node": 1})
    _unknown(repos, ("n1", "n2"))
    adapter = StubExecutionAdapter()
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    JobStepper(repos, adapter, settings=_SSettings()).run_once()
    assert [s.phase for s in adapter.submitted_specs()] == ["preflight", "execution"]
    assert "worker_cpu_requested" not in repos.data_jobs.get_job(jid)["worker_pool"]


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


def test_pick_nodes_prefers_nodes_whose_remaining_room_fits_on_mixed_size_clusters():
    """크기가 섞인 클러스터(재검토 지적): 작은 노드(상한 3)는 워커(노드당 2) 하나만 담는다 -- 예약이 같다는 이유로 큰 노드와
    번갈아 뽑히면 둘째 잡부터 작은 노드에 줄을 섰다. 남은 여유가 노드당 프로세스를 못 담는 노드는 뒤로."""
    from dms.placement import _pick_nodes
    caps = {"a-infra": 3, "c01": 126}
    load = {}
    picks = []
    for _ in range(4):
        [node] = _pick_nodes(["a-infra", "c01"], 1, per_node=2, caps=caps, load=load)
        picks.append(node)
        load[node] = load.get(node, 0) + 2
    assert picks == ["a-infra", "c01", "c01", "c01"]       # 작은 노드는 하나만, 나머지는 큰 노드
    # 모름(None)은 fail-open -- 다 담는 쪽으로 본다. 동률은 이름순(결정적).
    assert _pick_nodes(["n2", "n1"], 1, per_node=2, caps={}, load={}) == ["n1"]

