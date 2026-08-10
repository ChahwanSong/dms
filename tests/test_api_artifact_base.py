"""아티팩트 base API(슬라이스 18 설계 §2.5)의 계약. 잠금(§2.3)은 artifact_uri 가
NULL 인 잡(Rejected 등)도 포함해야 한다 -- 그 잡들의 stdout/stderr 가 디스크의
유일한 진단 사본이라, NOT NULL 로 좁히면 정확히 그 증거를 버린다."""
import json

from dms.domain import DataJobState
from dms.repositories import Repositories

ADMIN = {"Authorization": "Bearer tok-shared"}


def _make_rejected_job(repos):
    """artifact_uri 가 NULL 인 잡. stepper 는 성공 경로에서만 artifact_uri 를
    기록하므로 Rejected 잡은 그 컬럼이 NULL 이다 -- 잠금이 이 잡도 세어야 한다는
    것이 이 헬퍼의 존재 이유다."""
    rid = repos.requests.create(operation="scan", requester_id="alice", actor="alice",
        resource_key="k1", payload={"storage": "s1", "target": "a"}, priority="mid")
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan_id, operation="scan", priority="mid",
        storage_name="s1", target="a", options={}, tool="dscan", worker_pool={},
        precondition={}, actor="planner")
    repos.data_jobs.set_job_state(jid, DataJobState.REJECTED, actor="stepper")
    job = repos.data_jobs.get_job(jid)
    assert job["artifact_uri"] is None   # 전제 확인: NOT NULL 축소가 놓칠 잡
    return jid


def test_requires_admin(client):
    assert client.get("/api/admin/artifact-base").status_code == 401


def test_get_reports_env_source_when_db_null(client):
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["source"] == "env"
    assert body["effective"] == "file:///artifacts/dms"
    assert body["db_value"] is None
    assert body["env_value"] == "file:///artifacts/dms"
    assert body["locked_by_jobs"] == 0
    # 기본 env 경로는 테스트 머신에 없다 -- api 홉은 정직하게 실패를 낸다
    assert body["checks"]["api"] == {"ok": False, "reason": "artifact_base_missing"}
    # 컨트롤러는 아직 아무것도 기록하지 않았다 -- 실패가 아니라 "확인 대기 중"
    assert body["checks"]["controller"] == {"pending": True, "ok": None,
                                            "reason": None, "checked_at": None}
    assert body["checks"]["nodes"] == []


def test_put_normalizes_validates_and_saves(client, db, tmp_path):
    r = client.put("/api/admin/artifact-base",
                   json={"uri": f"file://{tmp_path}/"}, headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "db"
    assert body["db_value"] == f"file://{tmp_path}"     # 후행 슬래시 제거(정규형)
    assert body["checks"]["api"] == {"ok": True, "reason": None}
    row = db.query_one("SELECT artifact_base_uri FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] == f"file://{tmp_path}"


def test_put_rejects_bad_input_with_reason_codes(client, db, tmp_path):
    cases = [("relative/x", "artifact_base_not_absolute"),
             ("/a/../b", "artifact_base_traversal"),
             (f"{tmp_path}/file://x", "artifact_base_scheme_in_path"),
             (f"{tmp_path}/nope", "artifact_base_missing")]
    for uri, code in cases:
        r = client.put("/api/admin/artifact-base", json={"uri": uri}, headers=ADMIN)
        assert (r.status_code, r.json()["detail"]) == (422, code), uri
    # 422 는 곧 "저장 안 됨"(설계 §2.4a) -- 어느 실패도 DB 를 만지지 않았다
    row = db.query_one("SELECT artifact_base_uri FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] is None


def test_put_locked_when_any_job_exists_even_artifact_null(client, db, tmp_path):
    repos = Repositories(db)
    _make_rejected_job(repos)                  # artifact_uri NULL 인 잡 1건
    r = client.put("/api/admin/artifact-base",
                   json={"uri": f"file://{tmp_path}"}, headers=ADMIN)
    assert r.status_code == 409
    assert r.json()["detail"] == "artifact_base_locked"
    assert client.get("/api/admin/artifact-base",
                      headers=ADMIN).json()["locked_by_jobs"] == 1
    # 잠금은 저장을 막았다
    row = db.query_one("SELECT artifact_base_uri FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] is None


def test_put_force_passes_and_audits(client, db, tmp_path):
    repos = Repositories(db)
    _make_rejected_job(repos)
    r = client.put("/api/admin/artifact-base",
                   json={"uri": f"file://{tmp_path}", "force": True}, headers=ADMIN)
    assert r.status_code == 200
    entry = db.query(
        "SELECT * FROM audit_log WHERE mutation_class = 'artifact_base'")[-1]
    after = json.loads(entry["after_state"])
    assert after["forced"] is True and after["affected_jobs"] == 1


def test_validate_does_not_save(client, db, tmp_path):
    r = client.post("/api/admin/artifact-base/validate",
                    json={"uri": f"file://{tmp_path}"}, headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"normalized": f"file://{tmp_path}", "ok": True}
    row = db.query_one("SELECT artifact_base_uri FROM control_state WHERE id = 1")
    assert row["artifact_base_uri"] is None
    r2 = client.post("/api/admin/artifact-base/validate",
                     json={"uri": "/a/../b"}, headers=ADMIN)
    assert r2.status_code == 422 and r2.json()["detail"] == "artifact_base_traversal"


def test_node_hop_distinguishes_pending_from_failure(client, db, tmp_path):
    # 설계 §4: null(모름)과 실패를 뭉개지 않는다. 옛 base 를 프로브한 노드와
    # 프로브 자체가 없는 노드는 "확인 대기 중"(pending)이지 실패가 아니다.
    repos = Repositories(db)
    path = str(tmp_path)
    client.put("/api/admin/artifact-base", json={"uri": f"file://{path}"},
               headers=ADMIN)
    repos.agents.ingest("w1", {"node_name": "w1",
                               "artifact_base": {"path": path, "exists": True,
                                                 "writable": False}})
    repos.agents.ingest("w2", {"node_name": "w2",
                               "artifact_base": {"path": "/old/base",
                                                 "exists": True, "writable": True}})
    repos.agents.ingest("w3", {"node_name": "w3"})    # 프로브 없음(구버전 에이전트)
    nodes = {n["node_name"]: n for n in client.get(
        "/api/admin/artifact-base", headers=ADMIN).json()["checks"]["nodes"]}
    # w1: 현재 base 를 프로브했고 writable=False -- 실패는 실패로 보인다.
    # probe_mounts 의 status 는 writable 을 반영하지 않으므로 판정은 status 가
    # 아니라 writable 필드 직접이다(설계 §2.4b).
    assert nodes["w1"]["pending"] is False
    assert nodes["w1"]["exists"] is True and nodes["w1"]["writable"] is False
    # w2: 옛 base 의 결과 -- "확인 대기 중"이고 exists/writable 은 null
    assert nodes["w2"]["pending"] is True
    assert nodes["w2"]["exists"] is None and nodes["w2"]["writable"] is None
    assert nodes["w3"]["pending"] is True


def test_controller_hop_pending_until_checked_for_current_base(client, db, tmp_path):
    repos = Repositories(db)
    client.put("/api/admin/artifact-base", json={"uri": f"file://{tmp_path}"},
               headers=ADMIN)
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["checks"]["controller"]["pending"] is True
    # 옛 base 의 검증 결과는 pending 을 풀지 못한다(오독 방지 -- check_uri 대조)
    repos.control.set_artifact_base_check(uri="file:///old", ok=True, reason=None,
                                          now_iso="2026-08-10T00:00:00Z")
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["checks"]["controller"]["pending"] is True
    # 현재 base 를 검증하면 풀린다
    repos.control.set_artifact_base_check(uri=f"file://{tmp_path}", ok=True,
                                          reason=None,
                                          now_iso="2026-08-10T00:01:00Z")
    body = client.get("/api/admin/artifact-base", headers=ADMIN).json()
    assert body["checks"]["controller"] == {
        "pending": False, "ok": True, "reason": None,
        "checked_at": "2026-08-10T00:01:00Z"}


def test_controller_loop_unblocks_api_controller_hop(client, db, tmp_path):
    # 저장 직후 "확인 대기 중" -> 컨트롤러 한 틱 -> 정상, 의 수렴을 API 수준에서
    # 고정한다(설계 §2.4 닭-달걀 회피: 저장 전엔 (a)만 강제, (b)(c)는 저장 후
    # 폴링으로 수렴).
    from dms.artifact_base import controller_check_once
    repos = Repositories(db)
    client.put("/api/admin/artifact-base", json={"uri": f"file://{tmp_path}"},
               headers=ADMIN)
    assert client.get("/api/admin/artifact-base", headers=ADMIN).json()[
        "checks"]["controller"]["pending"] is True
    controller_check_once(repos, client.app.state.settings)
    ctl = client.get("/api/admin/artifact-base", headers=ADMIN).json()[
        "checks"]["controller"]
    assert ctl["pending"] is False and ctl["ok"] is True
