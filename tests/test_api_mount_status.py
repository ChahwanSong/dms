"""GET /api/admin/mount-status(2026-11-23) -- 권한·응답 계약·k8s 노드 목록 fail-soft·TTL 메모·PV 덧붙임(실 인벤토리).

판정 규칙 자체는 tests/test_mount_status.py(공용 코어)·test_mount_status_k8s.py(덧붙임)가 본다 -- 여기는 라우트가 재료를 제대로
모아 넘기는지(신선도·비활성·base·빌드 노드·배치 제외·클러스터 목록·PV)를 끝에서 끝으로 고정한다."""
import dataclasses

import pytest

from dms.db import iso_plus, utc_now_iso
from dms.repositories import Repositories
from test_k8s_submit import _login, kclient, world  # noqa: F401 -- dmsk8s 실데이터 세계(스토리지 4종·SC 매핑·k8s-w1 보고)

URL = "/api/admin/mount-status"
ADMIN = {"Authorization": "Bearer tok-shared"}
TOP_KEYS = {"generated_at", "stale_seconds", "report_interval_seconds", "shared_dir", "storages", "disabled_storages",
            "nodes", "totals", "cluster", "control_pods"}
CELL_STATES = {"ready", "read_only", "absent", "broken", "pending", "stale"}


def _seed(db, *, base="file:///dms_shared/dms/artifacts"):
    repos = Repositories(db)
    for name, path, enabled in (("ceph", "/cephfs", True), ("sec", "/sec", True), ("off", "/off", False)):
        repos.storages.create(storage_name=name, mount_path=path, managed_root=path, backend_type="cephfs",
                              enabled=enabled, actor="t")
    if base:
        repos.control.set_artifact_base(base, actor="t", forced=False, affected_jobs=0)
    ab = {"path": base.removeprefix("file://") if base else None, "exists": True, "writable": True,
          "mount_point": "/dms_shared", "fstype": "nfs4"}
    repos.agents.ingest("w1", {"node_name": "w1", "probe_mode": "host_root", "artifact_base": ab,
                               "mounts": [{"storage_name": "ceph", "mount_path": "/cephfs", "status": "Ready",
                                           "writable": True},
                                          {"storage_name": "sec", "mount_path": "/sec", "status": "Missing",
                                           "reason": "missing_mount_path"}]}, reported_at=utc_now_iso())
    repos.agents.ingest("w2", {"node_name": "w2", "mounts": []}, reported_at="2026-01-01T00:00:00Z")   # stale
    return repos


def _login_user(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})


def test_requires_admin(client, db):
    _seed(db)
    assert client.get(URL).status_code == 401
    _login_user(client)
    r = client.get(URL)
    assert r.status_code == 403
    assert "/cephfs" not in r.text and "w1" not in r.text           # 일반 사용자에게 경로·노드 이름이 새지 않는다
    client.cookies.clear()
    assert client.get(URL, headers=ADMIN).status_code == 200          # 공유 토큰 GET -- /api/admin/nodes 와 같은 수준


def test_admin_session_gets_200(client, db):
    _seed(db)
    Repositories(db).accounts.create("boss", "pw", "admin")
    assert client.post("/api/auth/login", json={"username": "boss", "password": "pw"}).status_code == 200
    assert client.get(URL).status_code == 200


def test_shape_freshness_disabled_and_base(client, db):
    repos = _seed(db)
    repos.node_exclusions.exclude("w1", reason="점검", actor="t")
    repos.control.set_control_state(maintenance=False, drain=False, reason=None, actor="t", build_node_name="w1")
    body = client.get(URL, headers=ADMIN).json()
    assert set(body) == TOP_KEYS
    assert (body["stale_seconds"], body["report_interval_seconds"]) == (300, 60)
    assert body["shared_dir"] == {"path": "/dms_shared/dms/artifacts", "state_root": "/dms_shared/dms", "majority": None}
    assert [c["storage_name"] for c in body["storages"]] == ["ceph", "sec"]
    assert body["disabled_storages"] == ["off"]
    assert [c["kind"] for c in body["storages"]] == ["filesystem", "filesystem"]
    assert all(c["pv"] is None for c in body["storages"])
    w1, w2 = body["nodes"]
    assert (w1["node_name"], w1["fresh"], w2["node_name"], w2["fresh"]) == ("w1", True, "w2", False)
    assert w1["cells"]["ceph"]["state"] == "ready" and w1["cells"]["sec"]["state"] == "absent"
    assert w1["shared_dir"]["state"] == "ok"
    assert w1["exclusion"]["reason"] == "점검" and w1["roles"] == ["build"]
    assert all(c["state"] == "stale" for c in w2["cells"].values())
    for row in body["nodes"]:
        assert set(row["cells"]) == {"ceph", "sec"}
        assert all(c["state"] in CELL_STATES for c in row["cells"].values())
    t = body["totals"]
    assert (t["nodes"], t["fresh_nodes"], t["stale_nodes"], t["ready"], t["absent"], t["problem_nodes"]) == (
        2, 1, 1, 1, 1, 1)
    # 스텁 백엔드 노드 목록은 빈 목록 -- 「노드 없음」이 아니라 모름
    assert body["cluster"] == {"known": False, "agentless_nodes": [], "reason": "no_nodes_listed", "observed_at": None}
    assert body["control_pods"] == []                                  # 점검 기록이 없으면 꼬리표도 없다(지어내지 않는다)


def test_no_artifact_base_means_no_shared_column(client, db):
    _seed(db, base=None)
    client.app.state.settings = dataclasses.replace(client.app.state.settings, artifact_base_uri="")
    body = client.get(URL, headers=ADMIN).json()
    assert body["shared_dir"] is None
    assert all(r["shared_dir"] is None for r in body["nodes"])


def test_cluster_node_list_is_memoized_and_marks_agentless_nodes(client, db):
    _seed(db)
    calls = []

    def lister():
        calls.append(1)
        return [{"name": "w1", "ip": "10.0.0.1", "control_plane": False},
                {"name": "cp1", "ip": "10.0.0.9", "control_plane": True}]
    client.app.state.node_lister = lister
    body = client.get(URL, headers=ADMIN).json()
    assert body["cluster"]["known"] is True and body["cluster"]["reason"] is None
    assert body["cluster"]["observed_at"]
    assert body["cluster"]["agentless_nodes"] == [{"node_name": "cp1", "control_plane": True}]
    w1, w2 = body["nodes"]
    assert (w1["in_cluster"], w1["control_plane"]) == (True, False)
    assert (w2["in_cluster"], w2["control_plane"]) == (False, None)        # k8s 에 없다 -- 퇴역 노드의 옛 보고
    client.get(URL, headers=ADMIN)
    assert len(calls) == 1                                                  # TTL 메모 -- k8s API 를 매번 두드리지 않는다


def test_cluster_node_list_failure_is_fail_soft(client, db):
    _seed(db)

    def boom():
        raise RuntimeError("forbidden")
    client.app.state.node_lister = boom
    r = client.get(URL, headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["cluster"] == {"known": False, "agentless_nodes": [], "reason": "list_failed", "observed_at": None}
    assert all(row["in_cluster"] is None for row in r.json()["nodes"])


class _NoK8s:
    """rollout_runner 대역 -- 파드 꼬리표는 DB 기록만 읽는다(요청마다 k8s 파드 목록을 부르면 여기서 터진다)."""
    def __getattr__(self, name):
        raise AssertionError(f"mount-status must not call rollout_runner.{name}")


def _api_row(repos, instance, node, *, ok=True, reason=None, uri="file:///dms_shared/dms/artifacts", at=None,
             gate_state="passed"):
    at = at or utc_now_iso()
    repos.api_base_checks.upsert({"instance": instance, "node_name": node, "uri": uri, "ok": ok, "reason": reason,
                                  "posture": None, "fstype": "nfs4", "gate_state": gate_state, "started_at": at,
                                  "checked_at": at, "first_ok_at": at, "ready_at": at})


def test_api_and_controller_pod_tags_come_from_fresh_db_records(client, db):
    # 사고 서명의 두 반쪽: w1 의 dms-api 가 아티팩트 경로를 못 읽는다(에이전트는 공용 디렉터리를 정상으로 본다 -- 파드 쪽 문제),
    # 컨트롤러는 w1, 표 밖 노드(cp1 -- 에이전트 없음)에 다른 dms-api. 죽은 파드의 옛 행은 꼬리표가 되지 않는다.
    repos = _seed(db)
    client.app.state.rollout_runner = _NoK8s()
    _api_row(repos, "dms-api-new", "w1", ok=False, reason="artifact_base_jobs_missing", gate_state="waiting")
    _api_row(repos, "dms-api-old", "cp1")
    _api_row(repos, "dms-api-dead", "w2", at=iso_plus(utc_now_iso(), -3600))
    repos.control.set_artifact_base_check(uri="file:///dms_shared/dms/artifacts", ok=True, reason=None, node="w1")
    body = client.get(URL, headers=ADMIN).json()
    w1, w2 = body["nodes"]
    assert w1["roles"] == ["api_pod", "controller_pod"] and w2["roles"] == []
    assert [(p["role"], p["instance"], p["node_name"], p["ok"], p["read_failed"], p["in_table"], p["gate_state"])
            for p in body["control_pods"]] == [
        ("api_pod", "dms-api-old", "cp1", True, False, False, "passed"),
        ("api_pod", "dms-api-new", "w1", False, True, True, "waiting"),
        ("controller_pod", None, "w1", True, False, True, None)]
    assert body["control_pods"][1]["reason"] == "artifact_base_jobs_missing"
    # 공용 디렉터리 칸은 에이전트 보고 그대로(정상)지만 이 노드의 dms-api 가 못 읽으므로 「문제 있는 노드」에 든다(공용 열 한 칸)
    assert (w1["shared_dir"]["state"], w1["problem"], w1["problem_count"]) == ("ok", True, 1)
    assert body["totals"]["problem_nodes"] == 2 and body["totals"]["shared_problem"] == 0


def test_stale_controller_record_and_other_base_results(client, db):
    repos = _seed(db)
    repos.control.set_artifact_base_check(uri="file:///dms_shared/dms/artifacts", ok=False,
                                          reason="artifact_base_jobs_missing", node="w1",
                                          now_iso=iso_plus(utc_now_iso(), -600))   # 멈춘·옮겨 간 컨트롤러의 옛 기록
    _api_row(repos, "dms-api-a", "w1", ok=False, reason="artifact_base_jobs_missing", uri="file:///old/base")
    body = client.get(URL, headers=ADMIN).json()
    assert [(p["role"], p["ok"], p["reason"], p["read_failed"]) for p in body["control_pods"]] == [
        ("api_pod", None, None, False)]                                  # 옛 base 를 본 결과는 모름 -- 꼬리표만
    w1 = body["nodes"][0]
    assert (w1["roles"], w1["problem_count"]) == (["api_pod"], 0)


def test_pv_storages_carry_selectable_pv_counts_and_caller_roles(kclient, world):
    # dmsk8s 실데이터: tb-nfs → k8s-pv-nfs(team-a/datasets·team-b/scratch·team-c/archive), tb-gpfs → k8s-pv-gpfs(models 둘),
    # tb-onefs → k8s-pv-onefs(kraken-kv-cache). checkpoints(Pending)는 고를 수 없어 세지 않는다.
    body = kclient.get(URL, headers=ADMIN).json()
    cols = {c["storage_name"]: c for c in body["storages"]}
    assert cols["k8s-pv-nfs"]["kind"] == "pv"
    assert cols["k8s-pv-nfs"]["pv"] == {"count": 3, "clusters": ["dmsk8s"], "storage_classes": ["tb-nfs"]}
    assert cols["k8s-pv-gpfs"]["kind"] == "both" and cols["k8s-pv-gpfs"]["pv"]["count"] == 2
    assert cols["k8s-pv-onefs"]["pv"]["count"] == 1
    assert cols["cephfs-dms"]["kind"] == "filesystem" and cols["cephfs-dms"]["pv"] is None
    w1 = next(r for r in body["nodes"] if r["node_name"] == "k8s-w1")
    assert "k8s_primary" in w1["roles"]
    assert all(c["state"] == "ready" for c in w1["cells"].values())


def test_unsynced_cluster_turns_pv_count_unknown(kclient, world):
    world.k8s.create_cluster(name="fresh-c", api_server="https://10.10.10.30:6443", tls_server_name=None, ca_pem=None,
                             insecure_skip_tls_verify=False, token_enc="v1:x", enabled=True, actor="ops")
    cols = {c["storage_name"]: c for c in kclient.get(URL, headers=ADMIN).json()["storages"]}
    assert cols["k8s-pv-nfs"]["pv"]["count"] is None              # 모름 -- 0 이 아니다
    assert cols["k8s-pv-nfs"]["pv"]["storage_classes"] == ["tb-nfs"]


@pytest.mark.parametrize("path", ["/api/admin/mount-status/", "/api/admin/storages/mounts"])
def test_no_lookalike_get_routes(client, path):
    # 독립 경로 하나 -- 스토리지 이름 경로(/api/admin/storages/{name})와 겹치는 GET 을 만들지 않았다
    assert client.get(path, headers=ADMIN).status_code in (404, 405)
