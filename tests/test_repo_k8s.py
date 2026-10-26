"""k8s 인벤토리 저장소(repositories/k8s.py)와 컨트롤러 k8s-inventory 루프(k8s_inventory.py): claim CAS 경합·receive 기한·
apply diff·superseded·config_rev discarded·failed 롤백·cleanup·보존·볼륨 해석·권한 조회."""
import copy
import json
from pathlib import Path

import pytest

from dms.agent.k8s_fetch import project_pv, project_pvc, project_role_binding, project_storage_class
from dms.db import dump_json, iso_plus, utc_now_iso
from dms.domain import DomainValidationError
from dms.k8s_inventory import prune_runs_once, run_inventory_once
from dms.k8s_sync import normalize_snapshot
from dms.repositories import Repositories
from dms.repositories.k8s import RunClosed, delete_maps_for_storage_in_txn

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "k8s"
T0 = "2026-10-25T12:00:00Z"
OK = lambda _enc: True  # noqa: E731


def _items(name, project):
    return [project(i) for i in json.loads((FIXTURES / name).read_text())["items"]]


def fixture_data():
    return normalize_snapshot({
        "storage_classes": _items("synthetic-storageclasses.json", project_storage_class),
        "pvcs": _items("synthetic-pvcs.json", project_pvc),
        "pvs": _items("synthetic-pvs.json", project_pv),
        "role_bindings": _items("synthetic-rolebindings.json", project_role_binding)})


@pytest.fixture
def repos(db):
    r = Repositories(db)
    r.k8s.update_settings(primary_node="k8s-w1", secondary_node="k8s-w2", interval_seconds=300, actor="ops")
    r.k8s.create_cluster(name="c1", api_server="https://10.10.10.20:6443", tls_server_name=None, ca_pem=None,
                         insecure_skip_tls_verify=False, token_enc="v1:sealed", enabled=True, actor="ops")
    db.execute("UPDATE k8s_clusters SET created_at = :t", {"t": iso_plus(T0, -60)})
    return r


def _claim(repos, node="k8s-w1", role="primary", now=T0, **kw):
    args = dict(node_name=node, role=role, now=now, interval=300, primary_configured=True, token_ok=OK,
                deadline_seconds=120)
    args.update(kw)
    return repos.k8s.claim(**args)


def _sync(repos, data=None, *, now=T0, cluster="c1"):
    got = _claim(repos, now=now)
    assert got is not None and got["cluster"]["name"] == cluster
    run_id = got["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=now, ok=True, data=data or fixture_data(),
                      fingerprint="ks-uid", server_version="v1.34.6", duration_ms=1200)
    outcome = repos.k8s.apply_run(run_id, now=now)
    return run_id, outcome


# --- 설정·클러스터 ---------------------------------------------------------------------------------

def test_settings_and_cluster_audit_never_store_the_sealed_token(repos, db):
    assert repos.k8s.get_settings()["primary_node"] == "k8s-w1"
    with pytest.raises(DomainValidationError) as e:
        repos.k8s.create_cluster(name="c1", api_server="https://x:6443", tls_server_name=None, ca_pem=None,
                                 insecure_skip_tls_verify=False, token_enc="v1:z", enabled=True, actor="ops")
    assert e.value.reason_code == "k8s_cluster_exists"
    repos.k8s.update_cluster("c1", {"token_enc": "v1:other"}, actor="ops")
    rows = db.query("SELECT before_state, after_state FROM audit_log WHERE mutation_class = 'k8s_cluster' ORDER BY id")
    blob = json.dumps(rows)
    assert "v1:sealed" not in blob and "v1:other" not in blob
    last = json.loads(rows[-1]["after_state"])
    assert last["token_change"] == "replaced" and last["token"] == "set" and "token_enc" not in last


def test_config_change_bumps_rev_and_requests_a_sync(repos):
    c = repos.k8s.get_cluster("c1")
    assert c["config_rev"] == 0 and c["sync_requested_at"] is None
    same = repos.k8s.update_cluster("c1", {"api_server": c["api_server"], "enabled": True}, actor="ops")
    assert same["config_rev"] == 0 and same["sync_requested_at"] is None           # 값이 같으면 바뀐 게 아니다
    after = repos.k8s.update_cluster("c1", {"tls_server_name": "dms-k8s.local"}, actor="ops")
    assert after["config_rev"] == 1 and after["sync_requested_at"] is not None
    repos.k8s.update_cluster("c1", {"enabled": False}, actor="ops")
    off = repos.k8s.get_cluster("c1")
    repos.k8s._db.execute("UPDATE k8s_clusters SET sync_requested_at = NULL")
    on = repos.k8s.update_cluster("c1", {"enabled": True}, actor="ops")
    assert off["enabled"] == 0 and on["sync_requested_at"] is not None and on["config_rev"] == 1   # 활성화 = 지금 due

    def guard(before):
        raise RuntimeError("rejected")
    with pytest.raises(RuntimeError):
        repos.k8s.update_cluster("c1", {"api_server": "https://elsewhere:6443"}, actor="ops", guard=guard)
    assert repos.k8s.get_cluster("c1")["api_server"] == "https://10.10.10.20:6443"
    with pytest.raises(KeyError):
        repos.k8s.update_cluster("nope", {"enabled": True}, actor="ops")


# --- claim ----------------------------------------------------------------------------------------

def test_claim_creates_a_run_and_blocks_a_second_claim_while_active(repos):
    got = _claim(repos)
    run = got["run"]
    assert (run["status"], run["role"], run["trigger"], run["node_name"]) == ("claimed", "primary", "schedule", "k8s-w1")
    assert run["deadline_at"] == iso_plus(T0, 150)                      # 120 + 업로드 유예 30
    c = repos.k8s.get_cluster("c1")
    assert c["last_attempt_at"] == T0 and c["sync_requested_at"] is None
    # 활성 run 이 있는 동안은 아무도(지금 동기화를 눌러도) 새 run 을 못 만든다
    assert repos.k8s.request_sync("c1") is True and repos.k8s.request_sync("ghost") is False
    repos.k8s._db.execute("UPDATE k8s_clusters SET sync_requested_at = :t", {"t": T0})   # due = T0
    assert repos.k8s.active_cluster_names(iso_plus(T0, 150)) == {"c1"}
    assert _claim(repos, now=iso_plus(T0, 10)) is None
    assert _claim(repos, node="k8s-w2", role="secondary", now=iso_plus(T0, 120)) is None
    # 기한(150)이 지나면 활성이 아니다(컨트롤러가 expired 로 닫기 전이라도) -- 지금 동기화가 바로 다시 잡힌다
    assert repos.k8s.active_cluster_names(iso_plus(T0, 151)) == set()
    again = _claim(repos, now=iso_plus(T0, 151))
    assert again["run"]["trigger"] == "manual"


def test_claim_cas_lets_only_one_of_two_racing_nodes_win(repos, monkeypatch):
    k8s = repos.k8s
    stale = k8s._db.query("SELECT * FROM k8s_clusters WHERE enabled = 1 ORDER BY name")
    late = iso_plus(T0, 200)                    # 둘 다 자격(secondary 의 90초 유예도 지남)
    assert _claim(repos, now=late) is not None   # primary 가 먼저 CAS 를 이긴다
    # secondary 는 primary 가 쓰기 전에 읽은 행(stale)을 들고 같은 순간 CAS 를 시도한다
    orig = k8s._db.query

    def query(sql, params=None):
        if sql.startswith("SELECT * FROM k8s_clusters WHERE enabled = 1"):
            return copy.deepcopy(stale)
        return orig(sql, params)
    monkeypatch.setattr(k8s._db, "query", query)
    monkeypatch.setattr(k8s, "active_cluster_names", lambda now: set())
    assert _claim(repos, node="k8s-w2", role="secondary", now=late) is None
    monkeypatch.undo()
    assert len(k8s.list_runs()) == 1


def test_claim_skips_disabled_and_unopenable_tokens_and_orders_failover_first(repos, db):
    assert _claim(repos, token_ok=lambda enc: False) is None
    db.execute("UPDATE k8s_clusters SET token_enc = NULL")
    assert _claim(repos) is None
    db.execute("UPDATE k8s_clusters SET token_enc = :t, enabled = 0", {"t": "v1:sealed"})
    assert _claim(repos) is None
    db.execute("UPDATE k8s_clusters SET enabled = 1")
    repos.k8s.create_cluster(name="c0", api_server="https://a:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:x", enabled=True, actor="ops")
    db.execute("UPDATE k8s_clusters SET created_at = :t, last_attempt_at = :t", {"t": iso_plus(T0, -1000)})
    db.execute("UPDATE k8s_clusters SET failover_after = :f WHERE name = 'c1'", {"f": iso_plus(T0, -5)})
    got = _claim(repos, node="k8s-w2", role="secondary")
    assert got["cluster"]["name"] == "c1" and got["run"]["trigger"] == "failover"
    assert repos.k8s.get_cluster("c1")["failover_after"] is None          # 대체는 1회


# --- receive --------------------------------------------------------------------------------------

def test_receive_rules(repos):
    run_id = _claim(repos)["run"]["run_id"]
    with pytest.raises(RunClosed):
        repos.k8s.receive(run_id, node_name="k8s-w2", now=T0, ok=True, data=fixture_data())   # 다른 노드
    with pytest.raises(RunClosed):
        repos.k8s.receive(run_id, node_name="k8s-w1", now=iso_plus(T0, 151), ok=True, data=fixture_data())  # 기한 뒤
    with pytest.raises(RunClosed):
        repos.k8s.receive("missing", node_name="k8s-w1", now=T0, ok=True, data=fixture_data())
    res = repos.k8s.receive(run_id, node_name="k8s-w1", now=iso_plus(T0, 150), ok=True, data=fixture_data(),
                            warnings=[{"phase": "persistentvolumes", "reason": "k8s_sync_forbidden"}])
    assert res["status"] == "received"
    run = repos.k8s.get_run(run_id, with_payload=True)
    assert run["status"] == "received" and json.loads(run["counts"])["pvcs"] == 7 and run["payload"]
    with pytest.raises(RunClosed):                                                   # 두 번은 못 받는다
        repos.k8s.receive(run_id, node_name="k8s-w1", now=T0, ok=True, data=fixture_data())


def test_failed_result_updates_cluster_summary_and_schedules_failover(repos):
    run_id = _claim(repos)["run"]["run_id"]
    res = repos.k8s.receive(run_id, node_name="k8s-w1", now=T0, ok=False, reason="k8s_sync_unreachable",
                            phase="storageclasses", error="x" * 900, duration_ms=5000)
    assert res == {"status": "failed", "cluster": "c1", "previous_failures": 0, "reason": "k8s_sync_unreachable"}
    c = repos.k8s.get_cluster("c1")
    assert (c["last_reason"], c["consecutive_failures"]) == ("k8s_sync_unreachable", 1)
    assert c["failover_after"] == iso_plus(T0, 30) and len(c["last_error"]) == 500
    assert repos.k8s.get_run(run_id)["status"] == "failed"
    # 대체 run(secondary)이 401 로 끝나면 다시 대체하지 않고, 알 수 없는 사유는 bad_response 로 접는다
    fo = _claim(repos, node="k8s-w2", role="secondary", now=iso_plus(T0, 31))
    assert fo["run"]["trigger"] == "failover"
    res = repos.k8s.receive(fo["run"]["run_id"], node_name="k8s-w2", now=iso_plus(T0, 40), ok=False,
                            reason="made_up", http_status=500)
    assert res["reason"] == "k8s_sync_bad_response" and res["previous_failures"] == 1
    c = repos.k8s.get_cluster("c1")
    assert c["consecutive_failures"] == 2 and c["failover_after"] is None


def test_failure_after_a_config_change_does_not_touch_the_summary(repos):
    run_id = _claim(repos)["run"]["run_id"]
    repos.k8s.update_cluster("c1", {"token_enc": "v1:new"}, actor="ops")            # 토큰을 고쳤다
    res = repos.k8s.receive(run_id, node_name="k8s-w1", now=T0, ok=False, reason="k8s_sync_unauthorized")
    assert res["previous_failures"] is None
    c = repos.k8s.get_cluster("c1")
    assert c["consecutive_failures"] == 0 and c["last_reason"] is None


# --- apply ----------------------------------------------------------------------------------------

def test_first_apply_inserts_everything_and_marks_success(repos, db):
    run_id, outcome = _sync(repos)
    assert outcome["outcome"] == "applied" and outcome["previous_failures"] == 0
    ch = outcome["changes"]
    assert ch["pvcs"]["added"] == 7 and ch["bindings"]["added"] == 9 and ch["pvs"]["added"] == 7
    assert ch["storage_classes"]["added"] == 3 and ch["pvcs"]["removed"] == 0
    assert "team-a/models" in ch["pvcs"]["names"]["added"]
    run = repos.k8s.get_run(run_id, with_payload=True)
    assert run["status"] == "applied" and run["payload"] is None and json.loads(run["changes"])["pvcs"]["added"] == 7
    c = repos.k8s.get_cluster("c1")
    assert c["last_success_at"] == T0 and c["applied_run_seq"] == run["seq"] and c["fingerprint"] == "ks-uid"
    assert c["server_version"] == "v1.34.6" and c["consecutive_failures"] == 0
    rb = db.query_one("SELECT * FROM k8s_role_bindings WHERE name = 'junwoo-song-clusterrole-admin'")
    assert (rb["dms_user"], rb["access"], rb["subject_mismatch"]) == ("junwoo.song", "admin", 0)
    counts = repos.k8s.cluster_counts()["c1"]
    assert counts == {"storage_classes": 3, "pvs": 7, "pvcs": 7, "pvcs_bound": 6, "namespaces": 3, "bindings": 9,
                      "recognized_bindings": 5, "users": 4}
    assert repos.k8s.totals() == {"clusters": 1, "namespaces": 3, "pvcs": 7, "bindings": 9, "users": 4}


def test_second_apply_writes_only_the_diff(repos, db):
    _sync(repos)
    data = fixture_data()
    data["pvcs"] = [p for p in data["pvcs"] if not (p["namespace"] == "team-c" and p["name"] == "archive")]
    data["pvcs"].append({**data["pvcs"][0], "namespace": "team-d", "name": "new-one", "uid": "u-new"})
    data["role_bindings"] = [rb for rb in data["role_bindings"] if rb["name"] != "alice-clusterrole-edit"]
    data["pvcs"][0] = {**data["pvcs"][0], "capacity": "9Gi"}
    writes = []
    orig = db.execute

    def spy(sql, params=None):
        if sql.lstrip().split()[0] in ("INSERT", "UPDATE", "DELETE") and "k8s_" in sql and "k8s_sync_runs" not in sql \
                and "k8s_clusters" not in sql:
            writes.append(sql.lstrip().split()[0])
        return orig(sql, params)
    db.execute = spy
    run_id = _claim(repos, now=iso_plus(T0, 400))["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=iso_plus(T0, 400), ok=True, data=data)
    outcome = repos.k8s.apply_run(run_id)
    db.execute = orig
    ch = outcome["changes"]
    assert (ch["pvcs"]["added"], ch["pvcs"]["removed"], ch["pvcs"]["changed"]) == (1, 1, 1)
    assert ch["pvcs"]["names"] == {"added": ["team-d/new-one"], "removed": ["team-c/archive"],
                                   "changed": ["team-a/models"]}
    assert (ch["bindings"]["removed"], ch["bindings"]["added"]) == (1, 0)
    assert ch["pvs"] == {"added": 0, "removed": 0, "changed": 0, "names": {"added": [], "removed": [], "changed": []}}
    assert sorted(writes) == ["DELETE", "DELETE", "INSERT", "UPDATE"]          # 바뀐 행만 쓴다
    assert db.query_one("SELECT capacity FROM k8s_pvcs WHERE namespace = 'team-a' AND name = 'models'")["capacity"] == "9Gi"


def test_partial_lists_keep_previous_values(repos, db):
    _sync(repos)
    data = fixture_data()
    data["pvs"] = None
    data["storage_classes"] = None
    run_id = _claim(repos, now=iso_plus(T0, 400))["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=iso_plus(T0, 400), ok=True, data=data)
    outcome = repos.k8s.apply_run(run_id)
    assert outcome["changes"]["pvs"] is None and outcome["changes"]["storage_classes"] is None
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_pvs")["n"] == 7
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_storage_classes")["n"] == 3
    counts = repos.k8s.cluster_counts()["c1"]                       # 이전 값을 보인다(한 번 받은 적이 있다)
    assert (counts["storage_classes"], counts["pvs"]) == (3, 7)


def test_never_received_optional_lists_count_as_unknown(repos, db):
    """토큰에 SC·PV list 권한이 처음부터 없으면 개수는 0 이 아니라 None(모름 -- 개요의 「목록 권한 없음·모름」, 2026-10-26)."""
    data = fixture_data()
    data["pvs"] = None
    data["storage_classes"] = None
    _sync(repos, data)
    counts = repos.k8s.cluster_counts()["c1"]
    assert counts["storage_classes"] is None and counts["pvs"] is None and counts["pvcs"] == 7


def test_empty_lists_are_applied_as_zero(repos, db):
    _sync(repos)
    data = {"storage_classes": [], "pvs": [], "pvcs": [], "role_bindings": []}
    run_id = _claim(repos, now=iso_plus(T0, 400))["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=iso_plus(T0, 400), ok=True, data=data)
    repos.k8s.apply_run(run_id)
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_role_bindings")["n"] == 0          # 0 은 정상값(권한 회수)
    assert repos.k8s.cluster_counts()["c1"]["pvcs"] == 0


def test_only_the_newest_received_run_is_applied(repos, db):
    k8s = repos.k8s
    a = _claim(repos)["run"]["run_id"]
    k8s.receive(a, node_name="k8s-w1", now=T0, ok=True, data=fixture_data())
    # 두 번째 run(같은 클러스터)이 received 로 쌓인 상황을 만든다(롤아웃 겹침·지연 적용)
    db.execute("""INSERT INTO k8s_sync_runs (run_id, cluster_name, node_name, role, trigger, status, claimed_at,
                  deadline_at, finished_at, config_rev, payload)
                  VALUES ('b', 'c1', 'k8s-w2', 'secondary', 'schedule', 'received', :t, :t, :t, 0, :p)""",
               {"t": iso_plus(T0, 100), "p": dump_json({"fingerprint": None, "data": {
                   "storage_classes": [], "pvs": [], "pvcs": [], "role_bindings": []}})})
    stats = run_inventory_once(repos, None, now=iso_plus(T0, 101))
    assert stats["applied"] == 1 and stats["superseded"] == 1
    assert k8s.get_run(a)["status"] == "superseded" and k8s.get_run("b")["status"] == "applied"
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_pvcs")["n"] == 0
    # 이미 적용한 run 보다 오래된 run 이 늦게 received 로 남아 있어도 과거로 되돌리지 않는다
    db.execute("UPDATE k8s_sync_runs SET status = 'received', payload = :p WHERE run_id = :r",
               {"r": a, "p": dump_json({"fingerprint": None, "data": fixture_data()})})
    assert k8s.apply_run(a)["outcome"] == "superseded"
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_pvcs")["n"] == 0
    assert k8s.apply_run(a)["outcome"] == "skipped"                                   # CAS: 두 번째 적용은 무동작


def test_config_change_discards_and_deleted_cluster_discards(repos, db):
    k8s = repos.k8s
    run_id = _claim(repos)["run"]["run_id"]
    k8s.receive(run_id, node_name="k8s-w1", now=T0, ok=True, data=fixture_data())
    k8s.update_cluster("c1", {"api_server": "https://other:6443"}, actor="ops")
    assert k8s.apply_run(run_id)["outcome"] == "discarded"
    run = k8s.get_run(run_id, with_payload=True)
    assert run["status"] == "discarded" and run["payload"] is None and "settings changed" in run["error"]
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_pvcs")["n"] == 0
    later = iso_plus(utc_now_iso(), 1)                 # 설정 변경이 세운 「지금 동기화」(실제 시각) 뒤
    run2 = _claim(repos, now=later)["run"]["run_id"]
    assert repos.k8s.get_run(run2)["trigger"] == "manual"
    k8s.receive(run2, node_name="k8s-w1", now=later, ok=True, data=fixture_data())
    k8s.delete_cluster("c1", actor="ops")
    # 삭제가 진행 중 run 을 그 자리에서 닫는다(2026-10-26 리뷰) -- 적용은 무동작, payload 도 지운다
    run = k8s.get_run(run2, with_payload=True)
    assert run["status"] == "discarded" and run["payload"] is None and run["error"] == "cluster was deleted"
    assert k8s.apply_run(run2)["outcome"] == "skipped"


def test_recreated_cluster_does_not_inherit_the_deleted_ones_runs(repos, db):
    """같은 이름으로 다시 등록해도(config_rev 가 다시 0) 옛 클러스터의 진행 중 run 결과가 새 클러스터에 적용·실패 기록되지
    않고, 활성 run 판정이 새 클러스터의 첫 동기화를 막지 않는다(2026-10-26 리뷰 재현 시나리오)."""
    k8s = repos.k8s
    old = _claim(repos)["run"]["run_id"]
    k8s.delete_cluster("c1", actor="ops")
    k8s.create_cluster(name="c1", api_server="https://new:6443", tls_server_name=None, ca_pem=None,
                       insecure_skip_tls_verify=False, token_enc="v1:new", enabled=True, actor="ops")
    with pytest.raises(RunClosed):
        k8s.receive(old, node_name="k8s-w1", now=T0, ok=True, data=fixture_data())
    with pytest.raises(RunClosed):
        k8s.receive(old, node_name="k8s-w1", now=T0, ok=False, reason="k8s_sync_unauthorized")
    c = k8s.get_cluster("c1")
    assert c["consecutive_failures"] == 0 and c["last_reason"] is None
    assert "c1" not in k8s.active_cluster_names(T0)
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_pvcs")["n"] == 0


def test_apply_failure_rolls_back_and_marks_the_run_failed(repos, db, monkeypatch):
    _sync(repos)
    data = fixture_data()
    data["pvcs"] = data["pvcs"][:2]
    run_id = _claim(repos, now=iso_plus(T0, 400))["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=iso_plus(T0, 400), ok=True, data=data)

    def boom(*a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(repos.k8s, "_insert_many", boom)
    data["pvcs"].append({**data["pvcs"][0], "name": "brand-new"})
    stats = run_inventory_once(repos, None, now=iso_plus(T0, 401))
    assert stats["failed"] == 1
    run = repos.k8s.get_run(run_id, with_payload=True)
    assert run["status"] == "failed" and run["reason"] == "k8s_sync_apply_failed" and run["payload"] is None
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_pvcs")["n"] == 7                # 롤백 -- 스냅숏 그대로
    c = repos.k8s.get_cluster("c1")
    assert c["consecutive_failures"] == 1 and c["last_reason"] == "k8s_sync_apply_failed" and c["last_success_at"] == T0
    events = db.query("SELECT event_type FROM events WHERE component = 'k8s-sync'")
    assert [e["event_type"] for e in events] == ["k8s_sync_failing"]


def test_partial_diff_failure_leaves_nothing_half_applied(repos, db, monkeypatch):
    _sync(repos)
    data = fixture_data()
    data["pvcs"] = data["pvcs"][:3]                     # PVC 4건 삭제 → 그다음 바인딩 단계에서 터진다
    data["role_bindings"] = data["role_bindings"][:2] + [{**data["role_bindings"][0], "name": "zz-clusterrole-edit"}]
    run_id = _claim(repos, now=iso_plus(T0, 400))["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=iso_plus(T0, 400), ok=True, data=data)
    orig = repos.k8s._insert_many

    def fail_on_bindings(table, *a, **k):
        if table == "k8s_role_bindings":
            raise RuntimeError("constraint")
        return orig(table, *a, **k)
    monkeypatch.setattr(repos.k8s, "_insert_many", fail_on_bindings)
    run_inventory_once(repos, None, now=iso_plus(T0, 401))
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_pvcs")["n"] == 7               # PVC 삭제도 함께 롤백
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_role_bindings")["n"] == 9


def test_recovery_event_after_failures(repos, db):
    run_id = _claim(repos)["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=T0, ok=False, reason="k8s_sync_tls_failed")
    db.execute("UPDATE k8s_clusters SET last_attempt_at = :t", {"t": iso_plus(T0, -1000)})
    run2 = _claim(repos, now=iso_plus(T0, 10))["run"]["run_id"]
    repos.k8s.receive(run2, node_name="k8s-w1", now=iso_plus(T0, 10), ok=True, data=fixture_data())
    run_inventory_once(repos, None, now=iso_plus(T0, 11))
    assert [e["event_type"] for e in db.query("SELECT event_type FROM events WHERE component = 'k8s-sync'")] == [
        "k8s_sync_recovered"]
    assert repos.k8s.get_cluster("c1")["consecutive_failures"] == 0


# --- 기한·정리·보존 ------------------------------------------------------------------------------

def test_expired_claim_schedules_failover_and_records_event(repos, db):
    run_id = _claim(repos)["run"]["run_id"]
    assert run_inventory_once(repos, None, now=iso_plus(T0, 150))["expired"] == 0      # 기한 = 150 (포함)
    stats = run_inventory_once(repos, None, now=iso_plus(T0, 151))
    assert stats["expired"] == 1
    run = repos.k8s.get_run(run_id)
    assert run["status"] == "expired" and run["reason"] == "k8s_sync_expired"
    c = repos.k8s.get_cluster("c1")
    assert c["failover_after"] == iso_plus(T0, 181) and c["consecutive_failures"] == 1
    assert db.query_one("SELECT event_type FROM events WHERE component = 'k8s-sync'")["event_type"] == "k8s_sync_failing"
    with pytest.raises(RunClosed):
        repos.k8s.receive(run_id, node_name="k8s-w1", now=iso_plus(T0, 152), ok=True, data=fixture_data())


def test_deleted_cluster_snapshot_is_hidden_then_cleaned(repos, db):
    _sync(repos)
    repos.k8s.delete_cluster("c1", actor="ops")
    assert repos.k8s.volumes()[0] == [] and repos.k8s.totals()["pvcs"] == 0          # 지운 순간부터 안 보인다
    total = 0
    for _ in range(10):
        n = repos.k8s.cleanup_orphans(batch=5)
        total += n
        if n == 0:
            break
    assert total == 3 + 7 + 7 + 9
    for table in ("k8s_storage_classes", "k8s_pvs", "k8s_pvcs", "k8s_role_bindings"):
        assert db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0


def test_recreating_a_cluster_purges_the_leftover_snapshot_rows(repos, db):
    """정리(cleanup_orphans)가 끝나기 전에 같은 이름으로 다시 등록하면 남은 행이 더는 고아가 아니라 정리가 멈춘다 -- 등록이
    그 자리에서 지운다(SC·PV 목록이 403 인 토큰이면 diff 가 덮지 못해 옛 PV 가 경로 렌더에 쓰였다, 2026-10-26 리뷰)."""
    _sync(repos)
    repos.k8s.delete_cluster("c1", actor="ops")
    repos.k8s.create_cluster(name="c1", api_server="https://10.10.10.20:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:x", enabled=True, actor="ops")
    assert repos.k8s.volumes()[0] == [] and repos.k8s.cluster_synced("c1") is False
    for table in ("k8s_storage_classes", "k8s_pvs", "k8s_pvcs", "k8s_role_bindings"):
        assert db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0
    # 새 클러스터의 첫 동기화가 SC·PV 를 못 받아도(403) 옛 PV 가 남지 않는다
    data = fixture_data()
    data["storage_classes"] = None
    data["pvs"] = None
    later = iso_plus(utc_now_iso(), 1)
    run_id = _claim(repos, now=later)["run"]["run_id"]
    repos.k8s.receive(run_id, node_name="k8s-w1", now=later, ok=True, data=data)
    assert repos.k8s.apply_run(run_id, now=later)["outcome"] == "applied"
    assert db.query_one("SELECT COUNT(*) AS n FROM k8s_pvs")["n"] == 0


def test_prune_runs_keeps_recent_and_last_success(repos, db):
    _sync(repos)                                     # seq 1 = applied
    old = iso_plus(T0, -40 * 86400)
    db.execute("UPDATE k8s_sync_runs SET claimed_at = :t", {"t": old})
    for i in range(60):
        db.execute("""INSERT INTO k8s_sync_runs (run_id, cluster_name, node_name, role, trigger, status, claimed_at,
                      deadline_at, config_rev) VALUES (:r, 'c1', 'k8s-w1', 'primary', 'schedule', 'failed', :t, :t, 0)""",
                   {"r": f"f{i}", "t": old})
    db.execute("""INSERT INTO k8s_sync_runs (run_id, cluster_name, node_name, role, trigger, status, claimed_at,
                  deadline_at, config_rev) VALUES ('live', 'c1', 'k8s-w1', 'primary', 'schedule', 'claimed', :t, :t, 0)""",
               {"t": old})
    removed = prune_runs_once(repos, retention_days=30, now_iso=T0)
    left = {r["run_id"] for r in db.query("SELECT run_id FROM k8s_sync_runs")}
    # 62건(applied 1 + failed 60 + claimed 1) 중 최근 50건(seq 13..62)과 마지막 applied(seq 1)를 남긴다 → seq 2..12 삭제
    assert removed == 11 and len(left) == 51 and "live" in left
    applied = db.query_one("SELECT run_id FROM k8s_sync_runs WHERE status = 'applied'")
    assert applied is not None                                                       # 마지막 성공 run 은 남는다


# --- 해석·권한 ------------------------------------------------------------------------------------

def _storages(db):
    rows = [("k8s-pv-nfs", "/k8s_pv/nfs", "nfs", "pv"), ("k8s-pv-gpfs", "/k8s_pv/gpfs", "gpfs", "both"),
            ("k8s-pv-onefs", "/k8s_pv/onefs", "onefs", "pv"), ("cephfs-dms", "/cephfs", "cephfs", None)]
    for name, root, backend, kind in rows:
        db.execute("""INSERT INTO storages (storage_name, mount_path, managed_root, backend_type, enabled,
                      user_enabled, status, created_at, updated_at, updated_by, kind)
                      VALUES (:n, :r, :r, :b, 1, 1, 'Ready', 't', 't', 'x', :k)""",
                   {"n": name, "r": root, "b": backend, "k": kind})


def test_volume_resolution_with_three_vendor_presets(repos, db):
    _sync(repos)
    _storages(db)
    k8s = repos.k8s
    with pytest.raises(DomainValidationError) as e:
        k8s.set_sc_map("tb-nfs", storage_name="cephfs-dms", path_template=None, actor="ops")
    assert e.value.reason_code == "storage_not_pv_capable"
    with pytest.raises(DomainValidationError) as e:
        k8s.set_sc_map("tb-nfs", storage_name="ghost", path_template=None, actor="ops")
    assert e.value.reason_code == "storage_missing"
    k8s.set_sc_map("tb-nfs", storage_name="k8s-pv-nfs", path_template=None, actor="ops")
    k8s.set_sc_map("tb-gpfs", storage_name="k8s-pv-gpfs", path_template=None, actor="ops")
    k8s.set_sc_map("tb-onefs", storage_name="k8s-pv-onefs", path_template=None, actor="ops")
    rows, truncated = k8s.volumes()
    assert truncated is False and len(rows) == 7
    by = {(r["namespace"], r["pvc"]): r for r in rows}
    vol = "pvc-5c25f53f-1111-4aaa-8bbb-000000000001"
    assert by[("team-a", "models")]["abs_root"] == f"/k8s_pv/gpfs/{vol}/{vol}-data"
    assert by[("team-a", "models")]["template_source"] == "preset" and by[("team-a", "models")]["selectable"]
    assert by[("team-a", "datasets")]["root"] == "team-a/datasets"
    assert by[("team-a", "kraken-kv-cache")]["abs_root"] == "/k8s_pv/onefs/data/csi/k8s-44c29ccbde"
    pending = by[("team-c", "waiting")]
    assert (pending["selectable"], pending["reason"], pending["volume"]) == (False, "pv_not_bound", None)
    # SC 덮어쓰기가 프리셋을 이긴다
    k8s.set_sc_map("tb-nfs", storage_name="k8s-pv-nfs", path_template="{namespace}/{pvc}", actor="ops")
    r = k8s.resolve_pvc("c1", "team-b", "scratch")
    assert r["root"] == "team-b/scratch" and r["template_source"] == "storageclass"
    assert k8s.resolve_pvc("c1", "team-b", "nope") is None
    k8s.delete_sc_map("tb-onefs", actor="ops")
    assert k8s.resolve_pvc("c1", "team-a", "kraken-kv-cache")["reason"] == "pv_storageclass_unmapped"
    # 매핑된 스토리지를 파일시스템으로 바꾼(DB 직접) 경우도 해석하지 않는다(fail-closed)
    db.execute("UPDATE storages SET kind = 'filesystem' WHERE storage_name = 'k8s-pv-gpfs'")
    assert k8s.resolve_pvc("c1", "team-a", "models")["reason"] == "pv_storageclass_unmapped"
    # 스토리지 삭제 트랜잭션 안의 매핑 정리(D10)
    with db.transaction():
        removed = delete_maps_for_storage_in_txn(db, "k8s-pv-gpfs", "ops")
    assert [m["storage_class"] for m in removed] == ["tb-gpfs"] and k8s.get_sc_map("tb-gpfs") is None
    assert db.query_one("SELECT COUNT(*) AS n FROM audit_log WHERE mutation_class = 'k8s_storage_class_map' "
                        "AND operation = 'remove'")["n"] == 2


def test_pv_without_synced_pv_rows_cannot_render_subdir(repos, db):
    data = fixture_data()
    data["pvs"] = None                                    # PV 목록 403 이 처음부터
    _sync(repos, data)
    _storages(db)
    repos.k8s.set_sc_map("tb-nfs", storage_name="k8s-pv-nfs", path_template=None, actor="ops")
    repos.k8s.set_sc_map("tb-onefs", storage_name="k8s-pv-onefs", path_template=None, actor="ops")
    r = repos.k8s.resolve_pvc("c1", "team-a", "datasets")
    assert r["reason"] == "pv_path_unresolved" and "PV not synced" in r["problem"]
    assert repos.k8s.resolve_pvc("c1", "team-a", "kraken-kv-cache")["selectable"]   # {volume} 만 쓰면 풀린다


def test_access_queries(repos, db):
    _sync(repos)
    k8s = repos.k8s
    assert k8s.access_for("alice", "c1", "team-a") == "edit"
    assert k8s.access_for("alice", "c1", "team-b") == "admin"
    assert k8s.access_for("alice", "c1", "team-c") is None
    assert k8s.access_for("bob", "c1", "team-a") is None                     # view·roleRef 불일치 바인딩은 권한 아님
    assert k8s.access_for(None, "c1", "team-a") is None
    rows, _ = k8s.volumes(dms_user="alice")
    assert {(r["namespace"], r["pvc"], r["access"]) for r in rows} == {
        ("team-a", "models", "edit"), ("team-a", "datasets", "edit"), ("team-a", "kraken-kv-cache", "edit"),
        ("team-b", "models", "admin"), ("team-b", "scratch", "admin")}
    assert k8s.volumes(dms_user="nobody")[0] == []
    rows, truncated = k8s.volumes(limit=2)
    assert len(rows) == 2 and truncated is True
    assert [r["pvc"] for r in k8s.volumes(q="KRAKEN")[0]] == ["kraken-kv-cache"]
    assert {r["pvc"] for r in k8s.volumes(q="team-b mod")[0]} == {"models"}
    assert [r["pvc"] for r in k8s.volumes(q="100%")[0]] == []                 # 와일드카드는 리터럴
    assert k8s.users_by_namespace()[("c1", "team-a")] == ["alice", "junwoo.song", "user.yo.hong.example.com"]
    unrecognized = k8s.bindings(recognized=False)
    assert sorted(b["name"] for b in unrecognized) == ["bob-clusterrole-edit", "bob-clusterrole-view",
                                                       "default-editor", "namespaceAdmin"]
    assert [b["name"] for b in k8s.bindings(recognized=True, q="cocoa")] == ["cocoa-song-clusterrole-edit"]
    assert k8s.pvc_counts_by_namespace()[("c1", "team-a")] == 3
    assert k8s.cluster_synced("c1") and not k8s.cluster_synced("ghost")
    assert k8s.storage_class_known("tb-missing") and k8s.storage_class_known("tb-nfs")
    assert not k8s.storage_class_known("nope")


def test_storage_class_rows_include_names_seen_only_on_pvcs(repos, db):
    data = fixture_data()
    data["storage_classes"] = None
    _sync(repos, data)
    rows = {r["name"]: r for r in repos.k8s.storage_class_rows()}
    assert set(rows) == {"tb-gpfs", "tb-nfs", "tb-onefs", "tb-missing"}
    assert rows["tb-nfs"]["provisioner"] is None and rows["tb-nfs"]["pvc_count"] == 3
    assert rows["tb-missing"]["pvc_count"] == 1
    assert repos.k8s.sample_volume("c1", "tb-gpfs")["namespace"] == "team-a"
    assert repos.k8s.sample_volume("c1", "tb-missing") is None                 # Bound PVC 없음
