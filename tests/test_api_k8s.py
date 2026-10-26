"""k8s 연동 API(routes_k8s·routes_agent 의 k8s-sync, dms-k8s 설계 §4·§5): 권한(조회 admin·변경 세션 admin·:sync admin),
토큰 봉인·평문 규칙·base64 JWT 풀기, CA 검증, API 서버 형식, 주소 변경 시 토큰 재입력, 에이전트 claim·결과(actor·능력 선언·
기한·409), 끝에서 끝(claim → 결과 → 컨트롤러 적용 → 개요·PV 탐색·사용자 권한·사용자 볼륨)."""
import base64
import datetime
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dms.agent.k8s_fetch import project_pv, project_pvc, project_role_binding, project_storage_class
from dms.api import password_transport as pt
from dms.api.app import create_app
from dms.db import iso_plus, utc_now_iso
from dms.k8s_inventory import run_inventory_once
from dms.repositories import Repositories
from dms.secret_box import open_at_rest

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "k8s"
BEARER = {"Authorization": "Bearer tok-shared"}


def _jwt(claims):
    def enc(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{enc({'alg': 'RS256'})}.{enc(claims)}.c2lnbmF0dXJl"


TOKEN = _jwt({"sub": "system:serviceaccount:dms-reader:dms-inventory", "exp": 1793000000})


def _agent(node):
    return {"Authorization": "Bearer tok-shared", "x-dms-actor": f"node:{node}"}


@pytest.fixture
def repos(db):
    return Repositories(db)


def _session(client, repos, username, role):
    if repos.accounts.get(username) is None:
        repos.accounts.create(username, "pw", role)
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"username": username, "password": "pw"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def admin(client, repos):
    """특권 목록 밖 세션 관리자(변경 가능, 맵핑 면제 아님)."""
    return _session(client, repos, "ops.admin", "admin")


def _ca_pem():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    now = datetime.datetime.now(datetime.timezone.utc)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "kubernetes")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    return cert.public_bytes(serialization.Encoding.PEM).decode()


CA = _ca_pem()


def _create(client, **kw):
    body = {"name": "c1", "api_server": "https://10.10.10.20:6443", "ca_pem": CA, "token": TOKEN}
    body.update(kw)
    return client.post("/api/admin/k8s/clusters", json=body)


# --- 권한 -------------------------------------------------------------------------------------------

def test_reads_are_admin_writes_are_session_admin_and_sync_is_admin(client, repos):
    for path in ("/api/admin/k8s/overview", "/api/admin/k8s/clusters", "/api/admin/k8s/runs",
                 "/api/admin/k8s/storage-classes", "/api/admin/k8s/volumes", "/api/admin/k8s/access"):
        assert client.get(path, headers=BEARER).status_code == 200, path
    for method, path, body in (("PUT", "/api/admin/k8s/settings", {"interval_seconds": 300}),
                               ("POST", "/api/admin/k8s/clusters", {"name": "c1"}),
                               ("PUT", "/api/admin/k8s/clusters/c1", {"enabled": False}),
                               ("DELETE", "/api/admin/k8s/clusters/c1", None),
                               ("PUT", "/api/admin/k8s/storage-classes/tb-nfs", {"storage_name": None})):
        r = client.request(method, path, json=body, headers=BEARER)
        assert r.status_code == 403 and r.json()["detail"] == "admin_session_required", (method, path)
    # 일반 사용자는 관리자 조회도 못 한다
    _session(client, repos, "alice", "user")
    assert client.get("/api/admin/k8s/overview").status_code == 403
    assert client.get("/api/user/k8s/volumes").status_code == 200
    client.cookies.clear()
    assert client.get("/api/user/k8s/volumes").status_code == 401
    # :sync 는 토큰 관리자도(스크립트) -- 없는 클러스터는 404
    assert client.post("/api/admin/k8s/clusters/c1:sync", headers=BEARER).json()["detail"] == "k8s_cluster_not_found"
    _session(client, repos, "ops.admin", "admin")
    assert _create(client).status_code == 201
    client.cookies.clear()
    r = client.post("/api/admin/k8s/clusters/c1:sync", headers=BEARER)
    assert r.status_code == 200 and r.json()["sync_requested_at"] is not None


# --- 설정 -------------------------------------------------------------------------------------------

def test_settings_validation(admin, repos):
    repos.agents.ingest("k8s-w1", {"node_name": "k8s-w1"})
    repos.agents.ingest("k8s-w2", {"node_name": "k8s-w2"})
    put = lambda body: admin.put("/api/admin/k8s/settings", json=body)  # noqa: E731
    for body in ({"interval_seconds": 59}, {"interval_seconds": 86401},
                 {"primary_node": "k8s-w1", "secondary_node": "k8s-w1"}):
        r = put(body)
        assert r.status_code == 422 and r.json()["detail"] == "invalid_k8s_sync_settings", body
    r = put({"primary_node": "ghost"})
    assert r.status_code == 422 and r.json()["detail"] == "k8s_node_unknown"
    r = put({"primary_node": "k8s-w1", "secondary_node": "k8s-w2", "interval_seconds": 600})
    assert r.status_code == 200
    expected = {"primary_node": "k8s-w1", "secondary_node": "k8s-w2", "interval_seconds": 600,
                "interval_source": "portal", "interval_default": 300, "poll_seconds": 15, "updated_by": "ops.admin"}
    assert {k: r.json()[k] for k in expected} == expected
    # null 주기 = env 기본값으로
    v = put({"primary_node": "k8s-w1", "secondary_node": None, "interval_seconds": None}).json()
    assert v["interval_seconds"] == 300 and v["interval_source"] == "env" and v["secondary_node"] is None
    assert repos.db.query_one("SELECT COUNT(*) AS n FROM audit_log WHERE mutation_class = 'k8s_sync_settings'")["n"] == 2


# --- 클러스터 등록·편집 ---------------------------------------------------------------------------

@pytest.mark.parametrize("api_server", [
    "http://10.0.0.1:6443", "https://", "https://10.0.0.1:6443/api", "https://10.0.0.1:6443?x=1",
    "https://user:pw@10.0.0.1:6443", "https://10.0.0.1:99999", "https://bad host:6443", "ftp://x", "", None,
    "https://10.0.0.1:6443#frag", "https://-bad-.example:6443", "https://10.0.0.1:"])
def test_api_server_format(admin, api_server):
    r = _create(admin, api_server=api_server)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_k8s_api_server", api_server


@pytest.mark.parametrize("api_server,stored", [
    ("https://10.10.10.20:6443/", "https://10.10.10.20:6443"), ("  https://k8s-cp1:6443  ", "https://k8s-cp1:6443"),
    ("https://[fd00::20]:6443", "https://[fd00::20]:6443"), ("https://api.example.com", "https://api.example.com")])
def test_api_server_accepted_forms(admin, api_server, stored):
    r = _create(admin, api_server=api_server)
    assert r.status_code == 201 and r.json()["api_server"] == stored


@pytest.mark.parametrize("name", ["C1", "-c1", "c1-", "c_1", "a" * 64, "", "c.1"])
def test_cluster_name_rule(admin, name):
    r = _create(admin, name=name)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_k8s_cluster_name"


def test_ca_pem_or_base64_pem_and_invalid(admin, repos):
    r = _create(admin, ca_pem=base64.b64encode(CA.encode()).decode())            # kubeconfig certificate-authority-data
    assert r.status_code == 201 and r.json()["tls_mode"] == "ca" and r.json()["ca_pem"].startswith("-----BEGIN")
    for bad in ("not a cert", base64.b64encode(b"hello").decode(),
                "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----"):
        r = _create(admin, name="c2", ca_pem=bad)
        assert r.status_code == 422 and r.json()["detail"] == "k8s_ca_invalid"
    r = _create(admin, name="c3", ca_pem=None, insecure_skip_tls_verify=True)
    assert r.json()["tls_mode"] == "insecure"
    r = _create(admin, name="c4", ca_pem="")
    assert r.json()["tls_mode"] == "default"
    r = _create(admin, name="c5", tls_server_name="bad name!")
    assert r.status_code == 422 and r.json()["detail"] == "invalid_k8s_api_server"


def test_token_rules_and_no_leak(admin, repos, db):
    r = _create(admin, token=None)
    assert r.status_code == 422 and r.json()["detail"] == "k8s_token_required"
    for bad in ("", "   ", "a b", "line\nbreak"):
        r = _create(admin, token=bad)
        assert r.status_code == 422 and r.json()["detail"] == "k8s_token_required", repr(bad)
    wrapped = base64.b64encode(TOKEN.encode()).decode()                  # kubectl get secret … {.data.token}
    r = _create(admin, token=f"  {wrapped}\n")
    assert r.status_code == 201
    body = r.json()
    assert body["token"] == "set" and body["token_expires_at"] == "2026-10-26T07:33:20Z"
    stored = db.query_one("SELECT token_enc FROM k8s_clusters WHERE name = 'c1'")["token_enc"]
    assert open_at_rest(stored, secret="sess-secret", purpose="k8s_cluster_token") == TOKEN   # 풀어서 저장
    assert _create(admin).json()["detail"] == "k8s_cluster_exists"
    blob = json.dumps([admin.get("/api/admin/k8s/overview").json(), admin.get("/api/admin/k8s/clusters").json(),
                       db.query("SELECT before_state, after_state FROM audit_log")])
    assert TOKEN not in blob and wrapped not in blob and stored not in blob
    # 열 수 없는 봉인(세션 시크릿 교체) → token unreadable
    db.execute("UPDATE k8s_clusters SET token_enc = :t", {"t": "v1:AAAA"})
    c = admin.get("/api/admin/k8s/clusters").json()[0]
    assert c["token"] == "unreadable" and c["status"] == "token_unreadable"
    assert c["status_reason"] == "k8s_sync_token_unreadable"


def test_live_policy_requires_a_sealed_token(db, settings, repos):
    live = TestClient(create_app(replace(settings, password_encryption_required=True), db))
    repos.accounts.create("ops.admin", "pw", "admin")
    info = live.get("/api/auth/transport-key").json()
    assert live.post("/api/auth/login", json={"username": "ops.admin", "password_enc": pt.seal_with_info(
        info, "pw", purpose="login", username="ops.admin")}).status_code == 200
    r = _create(live)
    assert r.status_code == 422 and r.json()["detail"] == "password_encryption_required"
    body = {"name": "c1", "api_server": "https://10.10.10.20:6443"}
    for purpose, subject in (("mail_relay_token", "c1"), ("k8s_cluster_token", "c2")):   # 용도·클러스터가 다르면 안 열린다
        r = live.post("/api/admin/k8s/clusters", json={**body, "token_enc": pt.seal_with_info(
            info, TOKEN, purpose=purpose, username=subject)})
        assert r.status_code == 422 and r.json()["detail"] == "password_encryption_invalid"
    sealed = pt.seal_with_info(info, TOKEN, purpose="k8s_cluster_token", username="c1")
    r = live.post("/api/admin/k8s/clusters", json={**body, "token_enc": sealed})
    assert r.status_code == 201 and r.json()["token"] == "set"


def test_changing_the_api_server_requires_the_token_again(admin, db):
    assert _create(admin).status_code == 201
    r = admin.put("/api/admin/k8s/clusters/c1", json={"api_server": "https://evil.example:6443"})
    assert r.status_code == 422 and r.json()["detail"] == "k8s_token_required"
    assert db.query_one("SELECT api_server FROM k8s_clusters")["api_server"] == "https://10.10.10.20:6443"
    # 같은 주소(형식만 다름)는 바뀐 게 아니다 · CA 교체(인증서 갱신)는 토큰 없이 된다(토큰 유지)
    r = admin.put("/api/admin/k8s/clusters/c1", json={"api_server": "https://10.10.10.20:6443/", "ca_pem": ""})
    assert r.status_code == 200 and r.json()["tls_mode"] == "default" and r.json()["token"] == "set"
    # 검증 대상 이름을 바꾸거나 검증을 끄는 쪽은 토큰을 다시 받는다(2026-10-26 보안 리뷰 — 같은 주소에서 가로채기)
    for change in ({"tls_server_name": "k8s-cp1"}, {"insecure_skip_tls_verify": True}):
        r = admin.put("/api/admin/k8s/clusters/c1", json=change)
        assert r.status_code == 422 and r.json()["detail"] == "k8s_token_required", change
    r = admin.put("/api/admin/k8s/clusters/c1", json={"tls_server_name": "k8s-cp1", "token": TOKEN})
    assert r.status_code == 200 and r.json()["tls_server_name"] == "k8s-cp1"
    r = admin.put("/api/admin/k8s/clusters/c1", json={"insecure_skip_tls_verify": True, "token": TOKEN})
    assert r.status_code == 200 and r.json()["tls_mode"] == "insecure"
    # 검증을 다시 켜는 쪽(더 안전해지는 쪽)은 토큰 없이 된다
    r = admin.put("/api/admin/k8s/clusters/c1", json={"insecure_skip_tls_verify": False})
    assert r.status_code == 200 and r.json()["tls_mode"] == "default" and r.json()["token"] == "set"
    rev = db.query_one("SELECT config_rev FROM k8s_clusters")["config_rev"]
    r = admin.put("/api/admin/k8s/clusters/c1", json={"api_server": "https://k8s-cp1:6443", "token": TOKEN + "x"})
    assert r.status_code == 200 and r.json()["api_server"] == "https://k8s-cp1:6443"
    assert db.query_one("SELECT config_rev FROM k8s_clusters")["config_rev"] == rev + 1
    assert admin.put("/api/admin/k8s/clusters/nope", json={"enabled": False}).json()["detail"] == "k8s_cluster_not_found"
    r = admin.put("/api/admin/k8s/clusters/c1", json={"enabled": False})
    assert r.json()["enabled"] is False and r.json()["status"] == "disabled"
    assert admin.delete("/api/admin/k8s/clusters/c1").json() == {"deleted": "c1"}
    assert admin.delete("/api/admin/k8s/clusters/c1").status_code == 404


# --- 에이전트 claim·결과 ---------------------------------------------------------------------------

def _capable(client, node, *, v=1):
    report = {"node_name": node}
    if v is not None:
        report["k8s_sync"] = {"v": v, "worker": "idle"}
    r = client.post("/api/agent/report", json=report, headers=_agent(node))
    assert r.status_code == 200
    return r.json()


def _setup(admin, repos):
    _capable(admin, "k8s-w1")
    _capable(admin, "k8s-w2")
    _capable(admin, "dms-w9", v=None)
    assert admin.put("/api/admin/k8s/settings", json={"primary_node": "k8s-w1", "secondary_node": "k8s-w2",
                                                      "interval_seconds": 300}).status_code == 200
    assert _create(admin).status_code == 201


def _claim(client, node):
    return client.post("/api/agent/k8s-sync/claim", json={"node_name": node}, headers=_agent(node))


def _data():
    def items(name, project):
        return [project(i) for i in json.loads((FIXTURES / name).read_text())["items"]]
    return {"storage_classes": items("synthetic-storageclasses.json", project_storage_class),
            "pvcs": items("synthetic-pvcs.json", project_pvc), "pvs": items("synthetic-pvs.json", project_pv),
            "role_bindings": items("synthetic-rolebindings.json", project_role_binding)}


def test_report_response_carries_the_role_without_secrets(admin, repos):
    _setup(admin, repos)
    assert _capable(admin, "k8s-w1")["k8s_sync"] == {"role": "primary", "poll_seconds": 15}
    assert _capable(admin, "k8s-w2")["k8s_sync"]["role"] == "secondary"
    body = _capable(admin, "dms-w9", v=None)
    assert body["k8s_sync"] == {"role": None, "poll_seconds": 15} and TOKEN not in json.dumps(body)


def test_claim_gates_and_run_contents(admin, repos, db):
    _setup(admin, repos)
    db.execute("UPDATE k8s_clusters SET sync_requested_at = NULL, created_at = :t", {"t": iso_plus(utc_now_iso(), -5)})
    # actor 와 본문 노드가 다르면 403, 노드 이름 규칙 위반 422
    r = admin.post("/api/agent/k8s-sync/claim", json={"node_name": "k8s-w1"}, headers=_agent("k8s-w2"))
    assert r.status_code == 403 and r.json()["detail"] == "agent_node_identity_mismatch"
    assert admin.post("/api/agent/k8s-sync/claim", json={"node_name": "bad name"},
                      headers=_agent("k8s-w1")).status_code == 422
    # 지정되지 않은 노드·능력 미선언 노드는 run 없음
    assert _claim(admin, "dms-w9").json() == {"run": None, "role": None, "poll_seconds": 15}
    _capable(admin, "k8s-w1", v=None)
    assert _claim(admin, "k8s-w1").json()["run"] is None
    _capable(admin, "k8s-w1")
    # secondary 는 due + 90초 전에는 못 받는다
    assert _claim(admin, "k8s-w2").json()["run"] is None
    body = _claim(admin, "k8s-w1").json()
    run = body["run"]
    assert body["role"] == "primary"
    assert {k: run[k] for k in ("cluster", "api_server", "ca_pem", "insecure_skip_tls_verify", "token",
                                "page_limit", "deadline_seconds", "max_bytes", "role", "trigger")} == {
        "cluster": "c1", "api_server": "https://10.10.10.20:6443", "ca_pem": CA, "insecure_skip_tls_verify": False,
        "token": TOKEN, "page_limit": 500, "deadline_seconds": 120, "max_bytes": 33554432, "role": "primary",
        "trigger": "schedule"}
    assert _claim(admin, "k8s-w1").json()["run"] is None                         # 활성 run 동안 다시 안 준다
    c = admin.get("/api/admin/k8s/clusters").json()[0]
    assert c["status"] == "syncing" and c["last_run"]["node_name"] == "k8s-w1"


def test_result_rules(admin, repos, db):
    _setup(admin, repos)
    run_id = _claim(admin, "k8s-w1").json()["run"]["run_id"]
    post = lambda node, body, rid=run_id: admin.post(f"/api/agent/k8s-sync/runs/{rid}",  # noqa: E731
                                                     json={"node_name": node, **body}, headers=_agent(node))
    r = admin.post(f"/api/agent/k8s-sync/runs/{run_id}", json={"node_name": "k8s-w1", "ok": True},
                   headers=_agent("k8s-w2"))
    assert r.status_code == 403
    r = post("k8s-w2", {"ok": True, "data": _data()})                           # 다른 노드의 run
    assert r.status_code == 409 and r.json()["detail"] == "k8s_sync_expired"
    r = admin.post(f"/api/agent/k8s-sync/runs/{run_id}", content=b"not json",
                   headers={**_agent("k8s-w1"), "content-type": "application/json"})
    assert r.status_code == 422 and r.json()["detail"] == "k8s_sync_bad_response"
    r = post("k8s-w1", {"ok": True, "data": _data(), "server_version": "v1.34.6", "fingerprint": "ks",
                        "duration_ms": 1234, "warnings": [{"phase": "persistentvolumes", "reason": "nope"}]})
    assert r.status_code == 200 and r.json() == {"status": "received"}
    assert post("k8s-w1", {"ok": True, "data": _data()}).status_code == 409      # 두 번은 못 받는다
    assert post("k8s-w1", {"ok": True, "data": _data()}, rid="ghost").status_code == 409
    run = repos.k8s.get_run(run_id)
    assert run["status"] == "received" and json.loads(run["warnings"]) == [
        {"phase": "persistentvolumes", "reason": None, "http_status": None}]


def test_malformed_data_becomes_a_failed_run(admin, repos, db):
    _setup(admin, repos)
    run_id = _claim(admin, "k8s-w1").json()["run"]["run_id"]
    bad = _data()
    bad["pvcs"].append(dict(bad["pvcs"][0]))                                    # 키 중복
    r = admin.post(f"/api/agent/k8s-sync/runs/{run_id}", json={"node_name": "k8s-w1", "ok": True, "data": bad},
                   headers=_agent("k8s-w1"))
    assert r.json() == {"status": "failed"}
    run = repos.k8s.get_run(run_id)
    assert run["reason"] == "k8s_sync_bad_response" and "duplicate pvc" in run["error"]
    c = repos.k8s.get_cluster("c1")
    assert c["consecutive_failures"] == 1 and c["failover_after"] is None       # 노드성 사유가 아니다
    assert db.query_one("SELECT event_type FROM events WHERE component = 'k8s-sync'")["event_type"] == "k8s_sync_failing"


def test_failed_result_with_a_node_reason_schedules_secondary(admin, repos, db):
    _setup(admin, repos)
    run_id = _claim(admin, "k8s-w1").json()["run"]["run_id"]
    r = admin.post(f"/api/agent/k8s-sync/runs/{run_id}",
                   json={"node_name": "k8s-w1", "ok": False, "reason": "k8s_sync_unreachable",
                         "phase": "storageclasses", "error": f"ConnectError {TOKEN}"[:40], "duration_ms": 5000},
                   headers=_agent("k8s-w1"))
    assert r.json() == {"status": "failed"}
    c = admin.get("/api/admin/k8s/clusters").json()[0]
    assert c["status"] == "failing" and c["last_reason"] == "k8s_sync_unreachable" and c["failover_after"]
    db.execute("UPDATE k8s_clusters SET failover_after = :t", {"t": iso_plus(utc_now_iso(), -1)})
    run = _claim(admin, "k8s-w2").json()["run"]
    assert run["trigger"] == "failover" and run["role"] == "secondary"
    assert db.query_one("SELECT COUNT(*) AS n FROM events WHERE event_type = 'k8s_sync_failover'")["n"] == 1


def test_signup_guard_tells_whether_signup_proves_the_id(db, settings, repos):
    """k8s 모드는 「계정 이름 == RoleBinding 의 ID」라, 인증 메일이 실제로 가야 남의 ID 선점을 막는다 -- 개요가 그 상태를 싣는다."""
    for kw, want in (({"account_verification_required": True, "mailer_backend": "stub"}, "stub"),
                     ({"account_verification_required": True, "mailer_backend": "knox_relay"}, "mail"),
                     ({"account_verification_required": False, "mailer_backend": "knox_relay"}, "open")):
        c = TestClient(create_app(replace(settings, **kw), db))
        _session(c, repos, "ops.admin", "admin")
        assert c.get("/api/admin/k8s/overview").json()["signup_guard"] == want, kw


def test_result_body_cap(db, settings, repos):
    small = TestClient(create_app(replace(settings, k8s_sync_max_bytes=10), db))
    r = small.post("/api/agent/k8s-sync/runs/x", content=b"x" * (1048576 + 100),
                   headers={**_agent("k8s-w1"), "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"] == "k8s_sync_too_large"

    # 길이 머리 없는 chunked 본문도 상한까지만 받고 끊는다(다 받아 둔 뒤 재지 않는다 -- 2026-10-26 리뷰)
    def chunked():
        for _ in range(40):
            yield b"x" * 65536
    r = small.post("/api/agent/k8s-sync/runs/x", content=chunked(),
                   headers={**_agent("k8s-w1"), "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"] == "k8s_sync_too_large"


# --- 끝에서 끝 -------------------------------------------------------------------------------------

def _storages(db):
    for name, root, backend, kind in (("k8s-pv-nfs", "/k8s_pv/nfs", "nfs", "pv"),
                                      ("k8s-pv-gpfs", "/k8s_pv/gpfs", "gpfs", "both"),
                                      ("k8s-pv-onefs", "/k8s_pv/onefs", "onefs", "pv"),
                                      ("cephfs-dms", "/cephfs", "cephfs", None)):
        db.execute("""INSERT INTO storages (storage_name, mount_path, managed_root, backend_type, enabled, user_enabled,
                      status, created_at, updated_at, updated_by, kind)
                      VALUES (:n, :r, :r, :b, 1, 1, 'Ready', 't', 't', 'x', :k)""",
                   {"n": name, "r": root, "b": backend, "k": kind})


def _full_sync(admin, repos):
    _setup(admin, repos)
    run_id = _claim(admin, "k8s-w1").json()["run"]["run_id"]
    r = admin.post(f"/api/agent/k8s-sync/runs/{run_id}",
                   json={"node_name": "k8s-w1", "ok": True, "data": _data(), "server_version": "v1.34.6",
                         "fingerprint": "ks-uid", "duration_ms": 900}, headers=_agent("k8s-w1"))
    assert r.json() == {"status": "received"}
    stats = run_inventory_once(repos, None)
    assert stats["applied"] == 1
    return run_id


def test_inventory_loop_maps_namespace_admin_owners_with_the_account_email_domain(admin, repos):
    """적용 루프는 설정의 계정 메일 도메인을 넘긴다(2026-10-26 (a)) -- 합성 픽스처의 team-a namespaceAdmin owner@example.com
    이 owner 의 admin 이 된다. 설정이 없으면(종전 호출) 인식하지 않는다."""
    from types import SimpleNamespace
    _setup(admin, repos)
    run_id = _claim(admin, "k8s-w1").json()["run"]["run_id"]
    admin.post(f"/api/agent/k8s-sync/runs/{run_id}", json={"node_name": "k8s-w1", "ok": True, "data": _data()},
               headers=_agent("k8s-w1"))
    assert run_inventory_once(repos, SimpleNamespace(account_email_domain="example.com"))["applied"] == 1
    cluster = repos.k8s.list_clusters()[0]["name"]
    assert repos.k8s.access_for("owner", cluster, "team-a") == "admin"


def test_end_to_end_overview_runs_and_freshness(admin, repos, db):
    run_id = _full_sync(admin, repos)
    ov = admin.get("/api/admin/k8s/overview").json()
    c = ov["clusters"][0]
    assert c["status"] == "ok" and c["counts"]["pvcs"] == 7 and c["counts"]["users"] == 4
    assert c["last_run"]["status"] == "applied" and c["server_version"] == "v1.34.6" and c["fingerprint"] == "ks-uid"
    assert ov["totals"] == {"clusters": 1, "namespaces": 3, "pvcs": 7, "bindings": 9, "users": 4}
    # 가입이 신원을 증명하는가(화면 경고의 근거) -- 이 픽스처는 무인증 가입(open), ldap 모드
    assert ov["signup_guard"] == "open" and ov["identity_mode"] == "k8s"
    nodes = {n["node_name"]: n for n in ov["nodes"]}
    assert nodes["k8s-w1"]["role"] == "primary" and nodes["k8s-w1"]["capable"] and nodes["k8s-w1"]["serving"] == ["c1"]
    assert nodes["k8s-w1"]["last_ok_at"] and nodes["k8s-w2"]["role"] == "secondary" and nodes["k8s-w2"]["serving"] == []
    assert nodes["dms-w9"]["capable"] is False and nodes["dms-w9"]["role"] is None
    assert [n["node_name"] for n in ov["nodes"]][:2] == ["k8s-w1", "k8s-w2"]       # 지정 노드가 먼저
    runs = admin.get("/api/admin/k8s/runs").json()
    assert runs[0]["run_id"] == run_id and runs[0]["changes"]["pvcs"]["added"] == 7 and "payload" not in runs[0]
    assert admin.get(f"/api/admin/k8s/runs?before={runs[0]['seq']}").json() == []
    assert admin.get("/api/admin/k8s/runs?cluster=nope").json() == []
    # 동일 fingerprint 의 두 번째 등록 → 중복 경고(같은 클러스터를 도메인 주소로 한 번 더)
    _create(admin, name="c1-dns", api_server="https://k8s-cp1:6443")
    db.execute("UPDATE k8s_clusters SET fingerprint = 'ks-uid' WHERE name = 'c1-dns'")
    by = {c["name"]: c for c in admin.get("/api/admin/k8s/clusters").json()}
    assert by["c1-dns"]["duplicate_of"] == "c1" and by["c1"]["duplicate_of"] == "c1-dns"
    assert by["c1-dns"]["status"] == "never" and by["c1-dns"]["counts"] is None    # 모름 ≠ 0


def test_storage_class_mapping_routes(admin, repos, db):
    _full_sync(admin, repos)
    _storages(db)
    put = lambda sc, body: admin.put(f"/api/admin/k8s/storage-classes/{sc}", json=body)  # noqa: E731
    assert put("nope", {"storage_name": "k8s-pv-nfs"}).json()["detail"] == "storage_class_not_found"
    assert put("tb-nfs", {"storage_name": "ghost"}).json()["detail"] == "storage_missing"
    assert put("tb-nfs", {"storage_name": "cephfs-dms"}).json()["detail"] == "invalid_pv_path_template"  # 프리셋 없음
    r = put("tb-nfs", {"storage_name": "cephfs-dms", "path_template": "{namespace}/{pvc}"})
    assert r.status_code == 422 and r.json()["detail"] == "storage_not_pv_capable"
    assert put("tb-nfs", {"storage_name": "k8s-pv-nfs", "path_template": "/abs/{volume}"}).json()["detail"] == \
        "invalid_pv_path_template"
    assert put("tb-nfs", {"storage_name": "k8s-pv-nfs"}).status_code == 200
    assert put("tb-gpfs", {"storage_name": "k8s-pv-gpfs", "path_template": ""}).json()["path_template"] is None
    assert put("tb-onefs", {"storage_name": "k8s-pv-onefs"}).status_code == 200
    # PVC 에만 보이는 이름(SC 객체 없음)도 매핑할 수 있다
    assert put("tb-missing", {"storage_name": "k8s-pv-nfs"}).status_code == 200
    rows = {(r["cluster"], r["name"]): r for r in admin.get("/api/admin/k8s/storage-classes").json()}
    gpfs = rows[("c1", "tb-gpfs")]
    assert gpfs["storage_name"] == "k8s-pv-gpfs" and gpfs["storage_kind"] == "both"
    assert gpfs["effective_template"] == "{volume}/{volume}-data" and gpfs["template_source"] == "preset"
    vol = "pvc-5c25f53f-1111-4aaa-8bbb-000000000001"
    assert gpfs["sample"]["abs_root"] == f"/k8s_pv/gpfs/{vol}/{vol}-data" and gpfs["pvc_count"] == 2
    assert rows[("c1", "tb-nfs")]["sample"]["root"] == "team-a/datasets"
    assert rows[("c1", "tb-missing")]["sample"] is None and rows[("c1", "tb-missing")]["pvc_count"] == 1
    assert put("tb-missing", {"storage_name": None}).json() == {"storage_class": "tb-missing", "storage_name": None,
                                                                "path_template": None}
    assert repos.k8s.get_sc_map("tb-missing") is None


def test_admin_volumes_access_and_user_volumes(client, repos, db):
    admin = _session(client, repos, "ops.admin", "admin")
    _full_sync(admin, repos)
    _storages(db)
    for sc, st in (("tb-nfs", "k8s-pv-nfs"), ("tb-gpfs", "k8s-pv-gpfs"), ("tb-onefs", "k8s-pv-onefs")):
        assert admin.put(f"/api/admin/k8s/storage-classes/{sc}", json={"storage_name": st}).status_code == 200
    v = admin.get("/api/admin/k8s/volumes").json()
    assert v["truncated"] is False and len(v["volumes"]) == 7 and v["clusters"][0]["synced_at"] is not None
    row = next(x for x in v["volumes"] if x["namespace"] == "team-a" and x["pvc"] == "models")
    assert row["users"] == ["alice", "junwoo.song", "user.yo.hong.example.com"] and row["selectable"]
    assert row["access"] is None                                              # 권한 기준 사용자를 묻지 않았다
    assert admin.get("/api/admin/k8s/volumes?limit=2").json()["truncated"] is True
    alice_view = admin.get("/api/admin/k8s/volumes?user=alice").json()["volumes"]
    assert {(x["namespace"], x["access"]) for x in alice_view} == {("team-a", "edit"), ("team-b", "admin")}
    assert [x["pvc"] for x in admin.get("/api/admin/k8s/volumes?q=kraken").json()["volumes"]] == ["kraken-kv-cache"]
    # 사용자 권한 화면
    repos.accounts.create("alice", "pw", "user")
    acc = admin.get("/api/admin/k8s/access").json()
    users = {u["dms_user"]: u for u in acc["users"]}
    assert users["alice"]["account_exists"] is True and users["junwoo.song"]["account_exists"] is False
    assert users["alice"]["volume_count"] == 5 and len(users["alice"]["namespaces"]) == 2
    yo = users["user.yo.hong.example.com"]["namespaces"][0]
    assert yo["subject_mismatch"] is True and yo["binding"] == "user-yo-hong-example-com-clusterrole-edit"
    assert sorted(b["name"] for b in acc["unrecognized"]) == ["bob-clusterrole-edit", "bob-clusterrole-view",
                                                             "default-editor", "namespaceAdmin"]
    assert [u["dms_user"] for u in admin.get("/api/admin/k8s/access?q=cocoa").json()["users"]] == ["cocoa.song"]
    # 일반 사용자: 자기 맵핑 범위만
    _session(client, repos, "alice", "user")
    mine = client.get("/api/user/k8s/volumes").json()
    assert mine["unrestricted"] is False
    assert {(x["namespace"], x["pvc"]) for x in mine["volumes"]} == {
        ("team-a", "models"), ("team-a", "datasets"), ("team-a", "kraken-kv-cache"), ("team-b", "models"),
        ("team-b", "scratch")}
    assert mine["namespaces"] == [{"cluster": "c1", "namespace": "team-a", "access": "edit"},
                                  {"cluster": "c1", "namespace": "team-b", "access": "admin"}]
    assert "users" not in mine["volumes"][0]
    # 바인딩이 없는 사용자: 0개(아직 동기화 안 됨과 다르다 -- clusters.synced_at 이 있다)
    _session(client, repos, "nobody", "user")
    none = client.get("/api/user/k8s/volumes").json()
    assert none["volumes"] == [] and none["namespaces"] == [] and none["clusters"][0]["synced_at"]
    # 특권 목록 밖 관리자는 일반 사용자처럼 자기 맵핑만, 목록 안 세션 관리자(admin)는 전체(면제)
    _session(client, repos, "ops.admin", "admin")
    assert client.get("/api/user/k8s/volumes").json()["volumes"] == []
    _session(client, repos, "admin", "admin")
    full = client.get("/api/user/k8s/volumes").json()
    assert full["unrestricted"] is True and full["namespaces"] is None and len(full["volumes"]) == 7
    # 공유 토큰(세션 아님)은 면제가 아니다
    client.cookies.clear()
    assert client.get("/api/user/k8s/volumes", headers=BEARER).json()["unrestricted"] is False


def test_pv_picker_marks_unusable_storages_and_finds_a_pvc_exactly(client, repos, db):
    """PV 고르기(2026-10-26 리뷰): 매핑 스토리지가 관리자 전용이면 비관리자에게, 비활성이면 누구에게나 고를 수 없게 보인다(제출·
    planner 가 어차피 거부한다 -- 직접 경로 피커와 같은 술어). pvc 는 이름 정확 일치(부분 일치 q 는 상한에 잘릴 수 있다)."""
    admin = _session(client, repos, "ops.admin", "admin")
    _full_sync(admin, repos)
    _storages(db)
    for sc, st in (("tb-nfs", "k8s-pv-nfs"), ("tb-gpfs", "k8s-pv-gpfs"), ("tb-onefs", "k8s-pv-onefs")):
        assert admin.put(f"/api/admin/k8s/storage-classes/{sc}", json={"storage_name": st}).status_code == 200
    exact = admin.get("/api/admin/k8s/volumes?cluster=c1&namespace=team-a&pvc=models").json()["volumes"]
    assert [(x["namespace"], x["pvc"]) for x in exact] == [("team-a", "models")]
    db.execute("UPDATE storages SET user_enabled = 0 WHERE storage_name = 'k8s-pv-gpfs'")
    db.execute("UPDATE storages SET enabled = 0 WHERE storage_name = 'k8s-pv-nfs'")
    _session(client, repos, "alice", "user")
    rows = {(x["namespace"], x["pvc"]): x for x in client.get("/api/user/k8s/volumes").json()["volumes"]}
    assert (rows[("team-a", "models")]["selectable"], rows[("team-a", "models")]["reason"]) == (
        False, "storage_admin_only")
    assert (rows[("team-a", "datasets")]["selectable"], rows[("team-a", "datasets")]["reason"]) == (
        False, "storage_disabled")
    # 관리자(면제)는 관리자 전용 스토리지를 그대로 고른다 -- 비활성은 누구도 못 고른다. 면제 관리자의 실제 고르기 경로(관리자
    # 볼륨 API — 검색·권한 기준 사용자)도 같다(2차 리뷰).
    _session(client, repos, "admin", "admin")
    for url in ("/api/user/k8s/volumes", "/api/admin/k8s/volumes?q=team-a", "/api/admin/k8s/volumes?user=alice"):
        rows = {(x["namespace"], x["pvc"]): x for x in client.get(url).json()["volumes"]}
        assert rows[("team-a", "models")]["selectable"] is True, url
        assert rows[("team-a", "datasets")]["reason"] == "storage_disabled", url


def test_never_synced_cluster_is_unknown_not_empty(admin, repos):
    _setup(admin, repos)
    _session(admin, repos, "alice", "user")
    body = admin.get("/api/user/k8s/volumes").json()
    assert body["volumes"] == [] and body["clusters"] == [
        {"name": "c1", "synced_at": None, "stale": None, "status": "never"}]
