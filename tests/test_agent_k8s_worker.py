"""에이전트의 k8s 동기화 워커 스레드(agent/runner.K8sSyncWorker, dms-k8s 설계 §4): 능력 선언·역할 반영·claim 루프(보고
루프와 분리)·결과 업로드·토큰 비보존."""
import threading
import time

import httpx

from dms.agent.runner import AgentRunner, K8sSyncWorker, build_report
from dms.config import AgentSettings

SETTINGS = AgentSettings(api_url="http://api", shared_token="tok-shared", node_name="k8s-w1")


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api")


def _report_kw():
    return dict(mountinfo_text="", mounts_fn=lambda s, **k: [], tools_fn=lambda n, **k: [], os_fn=lambda s, **k: {}, k8s_node_fn=lambda n: None)


def test_capability_is_declared_only_with_a_worker():
    plain = build_report("k8s-w1", [], **_report_kw())
    assert "k8s_sync" not in plain                           # --once·옛 에이전트: 선언 없음 → 서버가 run 을 주지 않는다
    worker = K8sSyncWorker(SETTINGS, fetch=lambda run: {})
    report = build_report("k8s-w1", [], k8s_sync_status=worker.status(), **_report_kw())
    assert report["k8s_sync"] == {"v": 1, "worker": "idle", "role": None, "run_id": None, "last": None}


def test_report_response_sets_the_role():
    worker = K8sSyncWorker(SETTINGS, fetch=lambda run: {})
    seen = {}

    def handler(request):
        import json
        seen["report"] = json.loads(request.content)
        return httpx.Response(200, json={"storages": [], "report_interval_seconds": 60,
                                         "artifact_base_path": None,
                                         "k8s_sync": {"role": "primary", "poll_seconds": 7}})
    runner = AgentRunner(SETTINGS, _client(handler), k8s_worker=worker)
    runner.run_once({"storages": [], "interval": 60, "artifact_base_path": None})
    assert seen["report"]["k8s_sync"]["v"] == 1
    assert worker.role == "primary" and worker._poll == 7

    def old_server(request):        # 응답에 k8s_sync 가 없으면(옛 서버) 역할 없음
        return httpx.Response(200, json={"storages": [], "report_interval_seconds": 60})
    AgentRunner(SETTINGS, _client(old_server), k8s_worker=worker).run_once(
        {"storages": [], "interval": 60, "artifact_base_path": None})
    assert worker.role is None
    # 보고가 실패하면 역할을 그대로 둔다(직전 state 유지 규칙과 같다)
    worker.configure({"role": "secondary", "poll_seconds": 15})

    def broken(request):
        return httpx.Response(500)
    AgentRunner(SETTINGS, _client(broken), k8s_worker=worker).run_once(
        {"storages": [], "interval": 60, "artifact_base_path": None})
    assert worker.role == "secondary"
    worker.configure({"role": "boss"})
    assert worker.role is None


def test_run_cycle_claims_fetches_and_uploads():
    posted = []
    runs = [{"run_id": "r1", "cluster": "c1", "token": "SECRET", "api_server": "https://x:6443"}]

    def handler(request):
        import json
        assert request.headers["authorization"] == "Bearer tok-shared"
        assert request.headers["x-dms-actor"] == "node:k8s-w1"
        if request.url.path == "/api/agent/k8s-sync/claim":
            assert json.loads(request.content) == {"node_name": "k8s-w1"}
            run = runs.pop(0) if runs else None
            return httpx.Response(200, json={"run": run, "role": "primary", "poll_seconds": 15})
        posted.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json={"status": "received"})

    fetched = []

    def fetch(run):
        fetched.append(dict(run))
        return {"ok": True, "data": {"pvcs": [], "role_bindings": []}, "duration_ms": 5}

    worker = K8sSyncWorker(SETTINGS, fetch=fetch)
    with _client(handler) as client:
        assert worker.run_cycle(client) is True
        assert worker.run_cycle(client) is False                 # 다음 claim 에는 run 이 없다
    assert fetched[0]["token"] == "SECRET"
    assert posted == [("/api/agent/k8s-sync/runs/r1", {"ok": True, "data": {"pvcs": [], "role_bindings": []},
                                                       "duration_ms": 5, "node_name": "k8s-w1"})]
    status = worker.status()
    assert status["worker"] == "idle" and status["last"]["ok"] is True and status["last"]["delivered"] == 200
    assert "SECRET" not in repr(status)


def test_claim_response_demotes_the_worker():
    def handler(request):
        return httpx.Response(200, json={"run": None, "role": None, "poll_seconds": 15})
    worker = K8sSyncWorker(SETTINGS, fetch=lambda run: {})
    worker.configure({"role": "primary"})
    with _client(handler) as client:
        assert worker.run_cycle(client) is False
    assert worker.role is None


def test_worker_thread_waits_without_a_role_and_wakes_when_assigned(capsys):
    calls = []
    done = threading.Event()

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/claim"):
            if len(calls) == 1:
                return httpx.Response(200, json={"run": {"run_id": "r9", "cluster": "c", "token": "TOK"},
                                                 "role": "primary", "poll_seconds": 30})
            if len(calls) == 3:
                raise httpx.ConnectError("boom TOK")      # 실패해도 스레드는 산다(메시지 대신 유형만 찍는다)
            done.set()
            return httpx.Response(200, json={"run": None, "role": "primary", "poll_seconds": 30})
        return httpx.Response(200, json={"status": "received"})

    worker = K8sSyncWorker(SETTINGS, client_factory=lambda: _client(handler),
                           fetch=lambda run: {"ok": False, "reason": "k8s_sync_unreachable"})
    worker._poll = 30
    worker.start()
    try:
        time.sleep(0.2)
        assert calls == []                                    # 역할 없음 → claim 하지 않는다
        worker.configure({"role": "primary", "poll_seconds": 30})   # 지정되면 바로 깬다
        for _ in range(50):
            if len(calls) >= 2:
                break
            time.sleep(0.05)
        assert calls[:2] == ["/api/agent/k8s-sync/claim", "/api/agent/k8s-sync/runs/r9"]
        # run 을 처리한 뒤엔 기다리지 않고 바로 다시 claim -- 세 번째 호출(예외)은 poll 30초 대기로 이어진다
        for _ in range(50):
            if len(calls) >= 3:
                break
            time.sleep(0.05)
        assert calls[2] == "/api/agent/k8s-sync/claim"
    finally:
        worker.stop()
    err = capsys.readouterr().err
    assert "ConnectError" in err and "TOK" not in err
