"""2026-11-23 API 파드 자체 artifact base 점검 모니터(api/base_monitor.py)와 readyz 시작 게이트.

게이트 계약(설계 v2 §C): 시작 래치 + 형제 조건. 막는 것은 (첫 회차 미완료 ∧ 시작 60초 이내) 또는 (마지막 회차 실패 ∧ 서비스
중인 형제 파드)뿐이고, 한 번 200 을 낸 프로세스는 base 때문에 다시 503 하지 않는다. 막는 503 은 readyz 자기 종료 카운터를
올리지 않는다."""
import dataclasses
import inspect
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from dms.api.app import create_app
from dms.config import Settings
from dms.db import iso_plus, utc_now_iso
from dms.domain import DataJobState, RequestState
from dms.execution_volcano import KubernetesClient
from dms.rollout_runner import RolloutRunner, StubRolloutRunner

J1 = "a" * 32


class _Rollout:
    """rollout_runner 대역: try_pod_briefs 는 None(k8s 를 못 봄) 또는 목록, pod_briefs 는 노드 이름 조회용."""
    def __init__(self, briefs=None):
        self.briefs = briefs

    def try_pod_briefs(self, *, selector):
        return self.briefs

    def pod_briefs(self, *, selector):
        return self.briefs or []


def _app(db, base, *, interval=60, gate=True, rollout=None, exit_calls=None, threshold=30, hostname="api-new"):
    settings = Settings(database_url="unused", shared_token="tok-shared", admin_token="tok-admin",
                        session_secret="sess-secret", artifact_base_uri=f"file://{base}",
                        api_artifact_base_check_seconds=interval, api_artifact_base_startup_gate=gate,
                        readyz_exit_failures=threshold)
    app = create_app(settings, db, exit_fn=(lambda: exit_calls.append(1)) if exit_calls is not None else None)
    # 잡 디렉터리 교차 확인은 실 실행 백엔드에서만 돈다(identity_job_ids -- stub 은 디렉터리를 만들지 않는다). 이 파일은 그
    # 판정을 검증하므로 판정이 읽는 설정만 volcano 로 바꾼다(실행 어댑터는 create_app 이 이미 stub 으로 만들었다).
    vol = dataclasses.replace(settings, execution_backend="volcano")
    app.state.settings = vol
    mon = app.state.base_monitor
    mon._settings = vol
    mon.instance = hostname
    mon._node._hostname = hostname
    if rollout is not None:
        mon._rollout_runner = rollout
        mon._node._runner = rollout
    return app, mon


def _base(tmp_path, name="artifacts"):
    base = tmp_path / name
    base.mkdir()
    base.chmod(0o755)
    return base


def _ready(client):
    r = client.get("/readyz")
    return r.status_code, r.json()


# ---- 게이트 행렬 ------------------------------------------------------------------------------------------------------

def test_unstarted_monitor_means_no_gate_and_unchanged_readyz(db, tmp_path):
    # 테스트의 create_app 은 모니터를 띄우지 않는다 -- readyz 는 기존 동작 그대로(본문에 artifact_base 키도 없다).
    app, mon = _app(db, tmp_path / "missing")
    assert not mon.started and mon.gate_state() == "off" and not mon.gate_blocking()
    code, body = _ready(TestClient(app))
    assert code == 200 and "artifact_base" not in body


def test_first_round_pending_blocks_only_within_the_grace(db, tmp_path):
    t = [1000.0]
    app, mon = _app(db, _base(tmp_path))
    mon._clock = lambda: t[0]
    mon.start(spawn=False)
    client = TestClient(app)
    assert _ready(client) == (503, {"status": "starting"})
    assert mon.gate_state() == "waiting"
    t[0] += 61                                  # 첫 회차가 60초 안에 안 끝나면 결과 없이도 막지 않는다
    code, body = _ready(client)
    assert code == 200 and "artifact_base" not in body
    assert mon.gate_state() == "started_degraded"


def test_failing_without_ready_sibling_starts_degraded(db, tmp_path):
    # replicas 1 의 서지 없는 재시작·새 설치·CephFS 전체 장애: 부분 장애(아티팩트 읽기)를 포탈 전면 장애로 키우지 않는다.
    app, mon = _app(db, tmp_path / "missing", rollout=_Rollout(briefs=[]))
    mon.start(spawn=False)
    snap = mon.tick()
    assert snap["ok"] is False and snap["reason"] == "artifact_base_missing" and snap["peer_ready"] is False
    code, body = _ready(TestClient(app))
    assert code == 200 and body["artifact_base"] == "degraded"
    assert mon.gate_state() == "started_degraded"
    assert set(body) == {"status", "reconnects", "last_reconnect_at", "artifact_base"}   # 사유·노드는 공개하지 않는다


def test_failing_with_ready_sibling_blocks_until_the_check_passes(db, tmp_path):
    # 롤링 서지: 옛 파드가 Ready 로 서비스 중이면 나쁜 노드의 새 파드는 Ready 가 되지 않는다(옛 파드가 남는다).
    base = tmp_path / "artifacts"
    sibling = [{"name": "api-old", "deleting": False, "ready": True}]
    app, mon = _app(db, base, rollout=_Rollout(briefs=sibling))
    mon.start(spawn=False)
    mon.tick()
    client = TestClient(app)
    assert _ready(client) == (503, {"status": "starting"})
    base.mkdir()
    base.chmod(0o755)                           # 늦은 마운트(HostToContainer) -- 재시작 없이 다음 회차가 통과
    assert mon.tick()["ok"] is True
    code, body = _ready(client)
    assert code == 200 and "artifact_base" not in body
    assert mon.gate_state() == "passed"


@pytest.mark.parametrize("briefs", [
    [{"name": "api-new", "deleting": False, "ready": True}],         # 자기 자신은 형제가 아니다
    [{"name": "api-old", "deleting": True, "ready": True}],          # 종료 중
    [{"name": "api-old", "deleting": False, "ready": False}],        # Ready 아님
    [],
])
def test_only_a_ready_non_terminating_sibling_counts(db, tmp_path, briefs):
    app, mon = _app(db, tmp_path / "missing", rollout=_Rollout(briefs=briefs))
    mon.start(spawn=False)
    mon.tick()
    assert mon.gate_blocking() is False


def _peer_row(db, *, uri, ok=True, ready=True, checked_at=None, instance="api-old"):
    from dms.repositories import Repositories
    now = checked_at or utc_now_iso()
    Repositories(db).api_base_checks.upsert({
        "instance": instance, "node_name": "n1", "uri": uri, "ok": ok, "reason": None, "gate_state": "passed",
        "started_at": now, "checked_at": now, "first_ok_at": now, "ready_at": now if ready else None})


def test_db_fallback_peer_when_k8s_is_unknown(db, tmp_path):
    # k8s 를 못 보면(스텁·조회 실패 → None) 다른 인스턴스의 신선한 ok 행(같은 uri, ready_at 있음)으로 대신 판정한다.
    base = tmp_path / "missing"
    uri = f"file://{base}"
    app, mon = _app(db, base, rollout=_Rollout(briefs=None))
    mon.start(spawn=False)
    _peer_row(db, uri=uri)
    mon.tick()
    assert mon.gate_blocking() is True


def test_ready_sibling_that_is_itself_failing_does_not_block(db, tmp_path):
    # 2026-11-23 리뷰: Ready ≠ 건강 -- 한 번 Ready 가 된 옛 파드는 base 가 깨져도 Ready 다. 그 파드의 신선 행이 같은 uri 에서
    # 실패 중이면 막아서 지킬 「정상 옛 파드」가 없고 릴리스만 멈춘다 -- 세지 않는다. 행이 없는(옛 버전) 형제는 Ready 로 센다.
    base = tmp_path / "missing"
    uri = f"file://{base}"
    app, mon = _app(db, base, rollout=_Rollout(briefs=[{"name": "api-old", "deleting": False, "ready": True}]))
    mon.start(spawn=False)
    _peer_row(db, uri=uri, ok=False)
    mon.tick()
    assert mon.gate_blocking() is False
    _peer_row(db, uri="file:///elsewhere", ok=False)        # 다른 uri 의 실패 행은 지금 판정과 무관 -- Ready 로 센다
    mon.tick()
    assert mon.gate_blocking() is True


@pytest.mark.parametrize("variant", ["stale", "other_uri", "not_ready", "not_ok"])
def test_db_fallback_ignores_stale_or_unrelated_rows(db, tmp_path, variant):
    base = tmp_path / "missing"
    uri = f"file://{base}"
    app, mon = _app(db, base, rollout=_Rollout(briefs=None))
    mon.start(spawn=False)
    kwargs = {"stale": {"uri": uri, "checked_at": iso_plus(utc_now_iso(), -600)},
              "other_uri": {"uri": "file:///elsewhere"},
              "not_ready": {"uri": uri, "ready": False},
              "not_ok": {"uri": uri, "ok": False}}[variant]
    _peer_row(db, **kwargs)
    mon.tick()
    assert mon.gate_blocking() is False


def test_ready_latch_never_goes_back_to_503(db, tmp_path):
    # 한 번 Ready 를 낸 프로세스는 런타임 base 실패로 내리지 않는다(경고만) -- 형제가 Ready 여도.
    base = _base(tmp_path)
    app, mon = _app(db, base, rollout=_Rollout(briefs=[{"name": "api-old", "deleting": False, "ready": True}]))
    mon.start(spawn=False)
    mon.tick()
    client = TestClient(app)
    assert _ready(client)[0] == 200
    base.rename(tmp_path / "gone")
    assert mon.tick()["ok"] is False
    code, body = _ready(client)
    assert code == 200 and body["artifact_base"] == "degraded"
    assert mon.gate_state() == "passed"         # 래치 당시의 상태


def test_env_break_glass_disables_only_the_gate(db, tmp_path):
    app, mon = _app(db, tmp_path / "missing", gate=False,
                    rollout=_Rollout(briefs=[{"name": "api-old", "deleting": False, "ready": True}]))
    mon.start(spawn=False)
    assert mon.gate_blocking() is False and mon.gate_state() == "disabled"
    mon.tick()                                  # 점검·기록은 계속
    assert mon.snapshot()["ok"] is False
    assert _ready(TestClient(app))[0] == 200 and mon.gate_state() == "disabled"


def test_interval_zero_turns_the_monitor_off(db, tmp_path):
    app, mon = _app(db, tmp_path / "missing", interval=0)
    mon.start()
    assert not mon.enabled and not mon.started and mon.gate_state() == "off"
    assert _ready(TestClient(app))[0] == 200


def test_gate_503_does_not_count_toward_self_termination(db, tmp_path, monkeypatch):
    # 나쁜 노드에서 자기 종료는 같은 노드에서 다시 뜰 뿐이다 -- 게이트 503 은 카운터를 올리지 않고, DB 성공으로 리셋된다.
    calls = []
    app, mon = _app(db, tmp_path / "missing", exit_calls=calls, threshold=2,
                    rollout=_Rollout(briefs=[{"name": "api-old", "deleting": False, "ready": True}]))
    mon.start(spawn=False)
    mon.tick()
    client = TestClient(app)
    for _ in range(10):
        assert _ready(client) == (503, {"status": "starting"})
    assert calls == []
    state = {"fail": False}
    real = db.query_one

    def query_one(sql, params=None):
        if state["fail"]:
            raise RuntimeError("db down")
        return real(sql, params)

    monkeypatch.setattr(db, "query_one", query_one)
    state["fail"] = True
    assert _ready(client)[0] == 503             # DB 실패 1
    state["fail"] = False
    assert _ready(client) == (503, {"status": "starting"})      # DB 성공 → 카운터 0, 게이트 503
    state["fail"] = True
    assert client.get("/readyz").json() == {"status": "degraded"}   # DB 실패 1(연속 아님)
    assert calls == []


# ---- 회차의 부수효과: 스냅숏·events·api_base_checks ------------------------------------------------------------------------

def _events(db):
    return db.query("SELECT event_type, severity, component, payload FROM events "
                    "WHERE component = 'api-artifact-base' ORDER BY id")


def test_transitions_are_recorded_once_per_change(db, tmp_path):
    base = _base(tmp_path)
    app, mon = _app(db, base)
    mon.start(spawn=False)
    mon.tick()
    assert _events(db) == []                    # 첫 회차가 정상이면 남기지 않는다
    base.rename(tmp_path / "gone")
    mon.tick()
    mon.tick()                                  # 같은 실패는 다시 남기지 않는다
    (tmp_path / "gone").rename(base)
    mon.tick()
    rows = _events(db)
    assert [(r["event_type"], r["severity"]) for r in rows] == [
        ("api_artifact_base_unavailable", "warning"), ("api_artifact_base_recovered", "info")]
    assert '"instance": "api-new"' in rows[0]["payload"] and "artifact_base_missing" in rows[0]["payload"]


def test_rows_are_upserted_and_pruned(db, tmp_path):
    from dms.repositories import Repositories
    repos = Repositories(db)
    base = _base(tmp_path)
    app, mon = _app(db, base, rollout=_Rollout(briefs=[{"name": "api-new", "node": "w5"}]))
    mon.start(spawn=False)
    _peer_row(db, uri="file:///x", instance="ancient", checked_at=iso_plus(utc_now_iso(), -25 * 3600))
    mon.tick()
    rows = {r["instance"]: r for r in repos.api_base_checks.list_recent()}
    assert set(rows) == {"api-new"}             # 24시간 지난 행은 회차가 지운다
    row = rows["api-new"]
    assert (row["ok"], row["reason"], row["node_name"], row["gate_state"], row["ready_at"]) == (
        True, None, "w5", "waiting", None)
    assert row["first_ok_at"] is not None and row["uri"] == f"file://{base}"
    TestClient(app).get("/readyz")              # 래치 → 다음 회차가 ready_at·passed 를 남긴다
    mon.tick()
    row = repos.api_base_checks.list_recent()[0]
    assert row["gate_state"] == "passed" and row["ready_at"] is not None


def test_db_failure_skips_the_round_and_keeps_the_snapshot(db, tmp_path, monkeypatch):
    app, mon = _app(db, _base(tmp_path))
    mon.start(spawn=False)
    first = mon.tick()
    monkeypatch.setattr(app.state.repos.control, "control_state",
                        lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert mon.tick() is None
    assert mon.snapshot()["checked_at"] == first["checked_at"] and mon.last_db_error_at is not None


def test_jobs_missing_fails_the_api_hop(db, tmp_path):
    # 미마운트 노드의 빈 로컬 디렉터리 -- DB 의 최근 성공 잡 디렉터리가 하나도 없다.
    from dms.repositories import Repositories
    repos = Repositories(db)
    base = _base(tmp_path)
    rid = repos.requests.create(operation="scan", requester_id="a", actor="a", resource_key="k",
                                payload={}, priority="mid")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    jid = repos.data_jobs.create_job(rid, repos.data_jobs.create_plan(rid, actor="p"), operation="scan",
                                     priority="mid", storage_name="s1", target="t", options={}, tool="dscan",
                                     worker_pool={}, precondition={}, actor="p")
    repos.data_jobs.set_artifact(jid, artifact_uri=f"file://{base}/{jid}", result_summary={})
    repos.data_jobs.set_job_state(jid, DataJobState.SUCCEEDED, actor="stepper")
    app, mon = _app(db, base)
    mon.start(spawn=False)
    snap = mon.tick()
    assert (snap["ok"], snap["reason"], snap["jobs_checked"], snap["jobs_found"]) == (
        False, "artifact_base_jobs_missing", 1, 0)


def test_check_now_waits_for_a_fresh_round(db, tmp_path):
    app, mon = _app(db, _base(tmp_path))
    mon.start()
    try:
        assert mon.check_now(timeout=5) is True
        done = mon._seq_done
        assert mon.check_now(timeout=5) is True and mon._seq_done > done
    finally:
        mon.stop()


def test_reports_timeout_only_for_fresh_current_uri(db, tmp_path):
    app, mon = _app(db, _base(tmp_path))
    mon._snap = {"uri": "file:///b", "ok": False, "reason": "artifact_base_check_timeout",
                 "checked_mono": mon._clock(), "node_name": None}
    assert mon.reports_timeout("file:///b") and not mon.reports_timeout("file:///other")
    mon._snap["checked_mono"] -= 10_000
    assert not mon.reports_timeout("file:///b")


# ---- 설정(관대 파싱)·배선 ------------------------------------------------------------------------------------------------

_REQUIRED = {"DMS_DATABASE_URL": "sqlite://", "DMS_SHARED_TOKEN": "a", "DMS_ADMIN_TOKEN": "b",
             "DMS_SESSION_SECRET": "c"}


@pytest.mark.parametrize("raw,expected", [
    (None, 60), ("0", 0), ("5", 10), ("120", 120), ("99999", 3600), ("-3", 10), ("abc", 60), ("", 60)])
def test_check_seconds_is_parsed_leniently(raw, expected, capsys):
    env = dict(_REQUIRED)
    if raw is not None:
        env["DMS_API_ARTIFACT_BASE_CHECK_SECONDS"] = raw
    assert Settings.from_env(env).api_artifact_base_check_seconds == expected   # SettingsError 금지(api·controller·migrate 공용)


@pytest.mark.parametrize("raw,expected", [
    (None, True), ("1", True), ("0", False), ("off", False), ("TRUE", True), ("nope", True)])
def test_startup_gate_flag_is_parsed_leniently(raw, expected, capsys):
    env = dict(_REQUIRED)
    if raw is not None:
        env["DMS_API_ARTIFACT_BASE_STARTUP_GATE"] = raw
    assert Settings.from_env(env).api_artifact_base_startup_gate is expected
    if raw == "nope":
        assert "DMS_API_ARTIFACT_BASE_STARTUP_GATE" in capsys.readouterr().err


def test_only_dms_api_starts_the_monitor(tmp_path, monkeypatch):
    # cli.py `dms api` 만 start_base_monitor=True -- 테스트의 create_app 은 미시작(게이트 없음).
    from dms import cli
    seen = {}
    monkeypatch.setenv("DMS_DATABASE_URL", f"sqlite:///{tmp_path}/c.db")
    monkeypatch.setenv("DMS_SHARED_TOKEN", "t")
    monkeypatch.setenv("DMS_ADMIN_TOKEN", "a")
    monkeypatch.setenv("DMS_SESSION_SECRET", "s")
    monkeypatch.setattr("dms.api.app.create_app", lambda settings, db, **kw: seen.update(kw) or "app")
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(app=app))
    assert cli.main(["api"]) == 0
    assert seen["start_base_monitor"] is True and seen["app"] == "app"
    assert inspect.signature(create_app).parameters["start_base_monitor"].default is False


def test_healthz_runs_on_the_event_loop(db, tmp_path):
    # 멈춘 마운트의 요청이 스레드풀을 고갈시켜도 liveness 는 답한다(async def).
    app, _ = _app(db, tmp_path)
    route = next(r for r in app.routes if getattr(r, "path", None) == "/healthz")
    assert inspect.iscoroutinefunction(route.endpoint)
    assert TestClient(app).get("/healthz").json() == {"status": "ok"}


# ---- k8s 파드 Ready·try_pod_briefs ---------------------------------------------------------------------------------------

def _pod(name, conditions):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, deletion_timestamp=None),
        spec=SimpleNamespace(node_name="w1", containers=[SimpleNamespace(name="api", image="i")]),
        status=SimpleNamespace(phase="Running", container_statuses=[], conditions=conditions))


def test_list_pod_briefs_reports_the_ready_condition():
    cond = lambda t, s: SimpleNamespace(type=t, status=s)    # noqa: E731
    pods = [_pod("a", [cond("PodScheduled", "True"), cond("Ready", "True")]),
            _pod("b", [cond("Ready", "False")]), _pod("c", None),
            SimpleNamespace(metadata=SimpleNamespace(name="d", deletion_timestamp=None),
                            spec=SimpleNamespace(node_name="w1", containers=[]),
                            status=SimpleNamespace(phase="Pending", container_statuses=[]))]   # conditions 속성 없음
    c = KubernetesClient("dms")
    c._ensure = lambda: None
    c._core = SimpleNamespace(list_namespaced_pod=lambda ns, **kw: SimpleNamespace(items=pods))
    assert [(b["name"], b["ready"]) for b in c.list_pod_briefs("dms", "x")] == [
        ("a", True), ("b", False), ("c", False), ("d", False)]


def test_try_pod_briefs_tells_failure_apart_from_empty():
    class K8s:
        def __init__(self, fail):
            self.fail = fail

        def list_pod_briefs(self, ns, sel):
            if self.fail:
                raise RuntimeError("rbac")
            return []

    assert RolloutRunner(K8s(True), namespace="dms").try_pod_briefs(selector="x") is None
    assert RolloutRunner(K8s(True), namespace="dms").pod_briefs(selector="x") == []   # 옛 계약 그대로
    assert RolloutRunner(K8s(False), namespace="dms").try_pod_briefs(selector="x") == []
    assert StubRolloutRunner().try_pod_briefs(selector="x") is None


def test_monitor_thread_is_a_daemon(db, tmp_path):
    app, mon = _app(db, _base(tmp_path))
    mon.start()
    try:
        assert mon._thread.daemon and mon._thread.name == "dms-api-base-monitor"
        assert threading.active_count() >= 2
    finally:
        mon.stop()
