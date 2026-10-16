import httpx
from dms.config import AgentSettings
from dms.agent.probes import parse_proc_stat_cpu
from dms.agent.runner import AgentRunner, build_report, cpu_utilisation

SETTINGS = AgentSettings(api_url="http://api", shared_token="tok", node_name="node-a",
                         interval_seconds=60, mountinfo_path="/unused")


def test_build_report_shape():
    report = build_report(
        "node-a", [{"storage_name": "s", "mount_path": "/mnt/s"}], ["alice"],
        mountinfo_text="1 1 0:1 / /mnt/s rw - ext4 d rw\n",
        mounts_fn=lambda storages, **k: [{"storage_name": "s", "mount_path": "/mnt/s", "status": "Ready"}],
        tools_fn=lambda names, **k: [{"name": n, "status": "Ready"} for n in names],
        identities_fn=lambda users, **k: [{"username": u, "status": "Ready"} for u in users],
        os_fn=lambda storages, **k: {"load1": 0.1},
    )
    assert report["node_name"] == "node-a"
    assert report["probed_at"].endswith("Z")
    assert report["mounts"][0]["status"] == "Ready"
    assert report["tools"][0]["name"] == "dscan"
    assert report["identities"] == [{"username": "alice", "status": "Ready"}]
    assert report["os"] == {"load1": 0.1}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api")


def test_run_once_posts_and_updates_state(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["actor"] = request.headers["x-dms-actor"]
        return httpx.Response(200, json={
            "storages": [{"storage_name": "s", "mount_path": "/mnt/s",
                          "managed_root": "/mnt/s/dms"}],
            "identity_probe_targets": ["alice"],
            "report_interval_seconds": 15,
        })

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools",
                        lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities",
                        lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    state = runner.run_once({"storages": [], "probe_targets": [], "interval": 60})
    assert seen["url"] == "http://api/api/agent/report"
    assert seen["auth"] == "Bearer tok" and seen["actor"] == "node:node-a"
    assert state["storages"][0]["storage_name"] == "s"
    assert state["probe_targets"] == ["alice"]
    assert state["interval"] == 15


def test_run_once_keeps_state_on_error(monkeypatch, capsys):
    def handler(request):
        return httpx.Response(500, text="boom")

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools",
                        lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities",
                        lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    old = {"storages": [{"storage_name": "keep", "mount_path": "/k"}],
           "probe_targets": ["bob"], "interval": 60}
    assert runner.run_once(old) == old
    assert "agent report failed" in capsys.readouterr().err


def test_run_once_survives_connect_error(monkeypatch, capsys):
    def handler(request):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools",
                        lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities",
                        lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    old = {"storages": [], "probe_targets": [], "interval": 60}
    assert runner.run_once(old) == old


def test_run_once_survives_non_dict_json(monkeypatch, capsys):
    def handler(request):
        return httpx.Response(200, json=[])

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools",
                        lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities",
                        lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    old = {"storages": [], "probe_targets": [], "interval": 60}
    assert runner.run_once(old) == old
    assert "agent report failed" in capsys.readouterr().err


def test_run_once_survives_malformed_field_types(monkeypatch, capsys):
    def handler(request):
        return httpx.Response(200, json={"storages": "oops",
                                         "identity_probe_targets": [],
                                         "report_interval_seconds": 60})

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    old = {"storages": [], "probe_targets": [], "interval": 60}
    assert runner.run_once(old) == old


def test_run_loop_once_probes_with_received_storages(monkeypatch):
    requests = []

    def handler(request):
        import json
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={
            "storages": [{"storage_name": "s", "mount_path": "/mnt/s",
                          "managed_root": "/mnt/s/dms"}],
            "identity_probe_targets": [],
            "report_interval_seconds": 60,
        })

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    from dms.agent.runner import run_loop
    # httpx.Client 자체를 monkeypatch하면 _client() 헬퍼도 같은 전역 심볼을 통해
    # httpx.Client(...)를 호출하므로 무한 재귀에 빠진다 (동일 모듈 객체 공유).
    # run_loop에 client_factory 주입점을 열어 실제 재귀 없이 목 클라이언트를 공급한다.
    run_loop(SETTINGS, once=True, client_factory=lambda: _client(handler))
    assert len(requests) == 2
    assert requests[0]["mounts"] == []                      # 부트스트랩: 빈 상태
    assert [m["storage_name"] for m in requests[1]["mounts"]] == ["s"]  # 본 사이클


def test_build_report_threads_net_dev_path_to_os_probe():
    seen = {}

    def os_fn(storages, **kw):
        seen.update(kw)
        return {}

    build_report("node-a", [], [], mountinfo_text="",
                 mounts_fn=lambda s, **k: [], tools_fn=lambda n, **k: [],
                 identities_fn=lambda u, **k: [], os_fn=os_fn,
                 net_dev_path="/host/proc/1/net/dev")
    assert seen["net_dev_path"] == "/host/proc/1/net/dev"


def test_build_report_threads_virtual_net_path_to_os_probe():
    seen = {}

    def os_fn(storages, **kw):
        seen.update(kw)
        return {}

    build_report("node-a", [], [], mountinfo_text="",
                 mounts_fn=lambda s, **k: [], tools_fn=lambda n, **k: [],
                 identities_fn=lambda u, **k: [], os_fn=os_fn,
                 virtual_net_path="/host/sys/devices/virtual/net")
    assert seen["virtual_net_path"] == "/host/sys/devices/virtual/net"


def test_run_once_uses_settings_net_dev_path(monkeypatch):
    # 설정 -> run_once -> build_report -> probe 배선이 한 군데라도 끊기면 기본
    # /proc/net/dev(veth)로 조용히 되돌아간다 -- 배선 자체를 고정한다.
    seen = {}

    def handler(request):
        return httpx.Response(200, json={"storages": [],
                                         "identity_probe_targets": [],
                                         "report_interval_seconds": 60})

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_os_metrics",
                        lambda storages, **k: seen.update(k) or {})
    settings = AgentSettings(api_url="http://api", shared_token="tok",
                             node_name="node-a", interval_seconds=60,
                             mountinfo_path="/unused",
                             net_dev_path="/host/proc/1/net/dev",
                             virtual_net_path="/host/sys/devices/virtual/net")
    AgentRunner(settings, _client(handler)).run_once(
        {"storages": [], "probe_targets": [], "interval": 60})
    assert seen["net_dev_path"] == "/host/proc/1/net/dev"
    assert seen["virtual_net_path"] == "/host/sys/devices/virtual/net"


def test_build_report_carries_artifact_base_outside_mounts():
    # 슬라이스 18 최대의 함정(설계 §2.4b): reconciler 는 mounts 를 순회해
    # storages.status 로 매핑한다 -- base 프로브가 mounts 로 새면 유령 스토리지가
    # 생긴다. 별도 최상위 필드를 계약으로 고정한다.
    report = build_report(
        "node-a", [], [], mountinfo_text="",
        mounts_fn=lambda storages, **k: [],
        tools_fn=lambda names, **k: [],
        identities_fn=lambda users, **k: [],
        os_fn=lambda storages, **k: {},
        artifact_base_path="/cephfs/dms/artifacts",
        artifact_base_fn=lambda p, **k: {"path": p, "exists": True, "writable": True})
    assert report["artifact_base"] == {"path": "/cephfs/dms/artifacts",
                                       "exists": True, "writable": True}
    assert report["mounts"] == []          # mounts 에 섞이지 않았다


def test_build_report_artifact_base_none_when_target_unknown():
    # 서버가 아직 대상을 내리지 않았으면(부트스트랩 첫 사이클) 프로브도 없다 --
    # 서버 쪽에서 이 None 은 "확인 대기 중"으로 렌더된다(설계 §4).
    report = build_report("node-a", [], [], mountinfo_text="",
                          mounts_fn=lambda s, **k: [], tools_fn=lambda n, **k: [],
                          identities_fn=lambda u, **k: [], os_fn=lambda s, **k: {})
    assert report["artifact_base"] is None


def test_run_once_adopts_artifact_base_path_from_response(monkeypatch):
    # 에이전트는 ConfigMap 을 envFrom 으로 받지 않는다(50-agent-daemonset.yaml
    # 머리 주석) -- base 를 아는 유일한 경로가 리포트 응답이다(설계 §2.4b).
    def handler(request):
        return httpx.Response(200, json={
            "storages": [], "identity_probe_targets": [],
            "report_interval_seconds": 60,
            "artifact_base_path": "/cephfs/dms/artifacts"})

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    state = runner.run_once({"storages": [], "probe_targets": [], "interval": 60})
    assert state["artifact_base_path"] == "/cephfs/dms/artifacts"


def test_run_once_keeps_artifact_base_path_when_response_lacks_it(monkeypatch):
    # 구버전 서버 호환: 키가 없으면 기존 값을 유지한다 -- 빈 값으로 지워 다음
    # 리포트에서 프로브가 사라지게 하지 않는다.
    def handler(request):
        return httpx.Response(200, json={"storages": [], "identity_probe_targets": [],
                                         "report_interval_seconds": 60})

    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    runner = AgentRunner(SETTINGS, _client(handler))
    state = runner.run_once({"storages": [], "probe_targets": [], "interval": 60,
                             "artifact_base_path": "/keep"})
    assert state["artifact_base_path"] == "/keep"


# ---- CPU 사용률(2026-10-16 사용자 보고 「코어수가 128이어도 200이 넘어가고」): 러너가 /proc/stat 차분으로 계산 ------

def _cpu(total, idle, iowait):
    return {"cpu_total_jiffies": total, "cpu_idle_jiffies": idle, "cpu_iowait_jiffies": iowait}


def test_cpu_utilisation_is_non_idle_share_of_the_interval():
    # Δtotal 1000, Δidle(idle+iowait) 600, Δiowait 50 -> 사용 40%, I/O 대기 5%
    assert cpu_utilisation(_cpu(1000, 800, 100), _cpu(2000, 1400, 150)) == {
        "cpu_used_pct": 40.0, "cpu_iowait_pct": 5.0}
    # 소수 1자리 반올림: 1/3 -> 33.3
    assert cpu_utilisation(_cpu(0, 0, 0), _cpu(3, 2, 0))["cpu_used_pct"] == 33.3


def test_cpu_utilisation_zero_is_a_value_not_unknown():
    # 완전 유휴 구간은 0.0 -- None(모름)과 다르다(null ≠ 0)
    out = cpu_utilisation(_cpu(1000, 1000, 0), _cpu(2000, 2000, 0))
    assert out["cpu_used_pct"] == 0.0 and out["cpu_used_pct"] is not None
    assert out["cpu_iowait_pct"] == 0.0 and out["cpu_iowait_pct"] is not None


def test_cpu_utilisation_first_sample_and_missing_counters_are_none():
    none = {"cpu_used_pct": None, "cpu_iowait_pct": None}
    assert cpu_utilisation(None, _cpu(2000, 1400, 150)) == none        # 시작 후 첫 표본
    assert cpu_utilisation(_cpu(1000, 800, 100), _cpu(None, None, None)) == none   # 이번 표본 파싱 실패
    assert cpu_utilisation(_cpu(None, None, None), _cpu(2000, 1400, 150)) == none
    # bool 은 int 의 서브클래스 -- True 가 1 jiffy 로 새면 안 된다
    assert cpu_utilisation(_cpu(True, 0, 0), _cpu(2000, 1400, 150)) == none
    assert cpu_utilisation({}, {}) == none and cpu_utilisation("x", 5) == none


def test_cpu_utilisation_zero_delta_and_counter_reset_are_none():
    none = {"cpu_used_pct": None, "cpu_iowait_pct": None}
    assert cpu_utilisation(_cpu(1000, 800, 100), _cpu(1000, 800, 100)) == none   # Δtotal 0(같은 틱)
    # total 감소 = 재부팅 리셋 -- 음수·거대 사용률을 그리느니 모름
    assert cpu_utilisation(_cpu(10_000, 8000, 500), _cpu(500, 300, 10)) == none


def test_cpu_utilisation_decrease_is_judged_per_metric():
    # iowait 단독 감소(nohz 커널의 알려진 흔들림, proc(5)): I/O 대기만 모름, 사용률은 idle+iowait 합으로 산다
    assert cpu_utilisation(_cpu(1000, 800, 300), _cpu(2000, 1500, 250)) == {
        "cpu_used_pct": 30.0, "cpu_iowait_pct": None}
    # idle(합) 감소는 사용률만 모름
    assert cpu_utilisation(_cpu(1000, 800, 100), _cpu(2000, 700, 150)) == {
        "cpu_used_pct": None, "cpu_iowait_pct": 5.0}
    # Δidle > Δtotal(다른 성분이 줄어든 오염) -> 음수 사용률은 모름
    assert cpu_utilisation(_cpu(1000, 500, 0), _cpu(1100, 700, 0))["cpu_used_pct"] is None


def test_cpu_utilisation_does_not_double_count_guest_time():
    # 구간 동안 user +500 중 400 이 guest(커널이 user 에도 센다). 사용 = 500/1000 = 50%.
    # guest 를 total 에 더하면 Δtotal 1400, 사용 900/1400 = 64.3% 로 부풀려진다.
    prev = parse_proc_stat_cpu("cpu  1000 0 0 9000 0 0 0 0 0 0\n")
    cur = parse_proc_stat_cpu("cpu  1500 0 0 9500 0 0 0 0 400 0\n")
    assert cpu_utilisation(prev, cur) == {"cpu_used_pct": 50.0, "cpu_iowait_pct": 0.0}


def _ok_response():
    return httpx.Response(200, json={"storages": [], "identity_probe_targets": [],
                                     "report_interval_seconds": 60})


def _patch_probes(monkeypatch, samples):
    it = iter(samples)
    monkeypatch.setattr("dms.agent.runner._read_text", lambda path: "")
    monkeypatch.setattr("dms.agent.runner.probe_tools", lambda names, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_identities", lambda users, **k: [])
    monkeypatch.setattr("dms.agent.runner.probe_os_metrics",
                        lambda storages, **k: {"load1": 213.45, **next(it)})


def test_run_once_reports_cpu_utilisation_from_the_previous_probe(monkeypatch):
    import json
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["os"])
        return _ok_response()

    _patch_probes(monkeypatch, [_cpu(1000, 800, 100), _cpu(2000, 1400, 150), _cpu(500, 300, 10)])
    runner = AgentRunner(SETTINGS, _client(handler))
    state = {"storages": [], "probe_targets": [], "interval": 60}
    for _ in range(3):
        state = runner.run_once(state)
    # 시작 후 첫 보고는 모름(0 아님) -- 키는 싣는다
    assert sent[0]["cpu_used_pct"] is None and sent[0]["cpu_iowait_pct"] is None
    assert "cpu_used_pct" in sent[0]
    # 둘째: 직전 표본 대비 40% / 5%, 원 카운터·load 는 그대로 함께 간다(load 는 사용률이 아니다)
    assert (sent[1]["cpu_used_pct"], sent[1]["cpu_iowait_pct"]) == (40.0, 5.0)
    assert sent[1]["cpu_total_jiffies"] == 2000 and sent[1]["load1"] == 213.45
    # 셋째: 카운터 감소(재부팅) -> 모름
    assert sent[2]["cpu_used_pct"] is None and sent[2]["cpu_iowait_pct"] is None


def test_failed_report_still_advances_the_cpu_baseline(monkeypatch):
    # 보고 실패 후 첫 성공 보고의 값은 "직전 보고 주기"의 사용률이어야 한다 -- 실패 동안의 긴 구간 평균이 아니다.
    import json
    calls = {"n": 0}
    sent = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 2:
            return httpx.Response(500, text="boom")
        sent.append(json.loads(request.content)["os"])
        return _ok_response()

    _patch_probes(monkeypatch, [_cpu(0, 0, 0), _cpu(1000, 1000, 0), _cpu(2000, 1100, 0)])
    runner = AgentRunner(SETTINGS, _client(handler))
    state = {"storages": [], "probe_targets": [], "interval": 60}
    for _ in range(3):
        state = runner.run_once(state)
    # 실패한 둘째 표본(1000, 1000) 대비 90% -- 첫 표본(0, 0) 대비였다면 (2000-1100)/2000 = 45%
    assert sent[-1]["cpu_used_pct"] == 90.0
