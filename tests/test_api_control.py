import pytest
ADMIN = {"Authorization": "Bearer tok-shared"}


@pytest.fixture
def session_admin(client):
    """컨트롤 상태 PUT 은 세션 관리자만(admin_session_required, 2026-10-07) -- 공유 토큰은 모든 노드
    에이전트가 쥔 자격이라 배포 경로를 바꿀 수 없다. Bearer 헤더가 세션 쿠키보다 우선하므로 변경 호출엔
    헤더를 싣지 않는다(조회는 토큰으로도 된다). 감사 actor 단언에 쓰도록 사용자명을 돌려준다."""
    client.app.state.repos.accounts.create("opadm", "p", "admin", actor="t")
    assert client.post("/api/auth/login", json={"username": "opadm", "password": "p"}).status_code == 200
    return "opadm"


def test_control_state_requires_admin(client):
    assert client.get("/api/admin/control-state").status_code == 401
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/control-state").status_code == 403


def test_control_state_get_defaults(client):
    body = client.get("/api/admin/control-state", headers=ADMIN).json()
    assert body["maintenance"] == 0
    assert body["drain"] == 0


def test_control_state_put_updates_and_returns_current(client, session_admin):
    res = client.put("/api/admin/control-state",
                     json={"maintenance": True, "drain": False, "reason": "점검"})
    assert res.status_code == 200
    body = res.json()
    assert body["maintenance"] == 1 and body["drain"] == 0
    assert body["reason"] == "점검"
    assert client.get("/api/admin/control-state", headers=ADMIN).json()["maintenance"] == 1


def test_control_state_put_is_audited(client, db, session_admin):
    client.put("/api/admin/control-state",
               json={"maintenance": False, "drain": True, "reason": None})
    rows = db.query("SELECT * FROM audit_log WHERE mutation_class = 'control_state'")
    assert len(rows) == 1
    assert rows[0]["operation"] == "set"


def test_control_state_rejects_unknown_build_node(client, session_admin):
    # I1: build_node_name은 agent_nodes에 보고된 노드 이름 중에서만 골라야 한다 --
    # 자유 입력 오타가 nodeSelector로 새면 빌드 파드가 영원히 Pending이다(C2의 최빈
    # 트리거). 노드를 하나도 등록하지 않은 채로 PUT하면 422로 거절돼야 한다.
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": "dms-w1-typo"})
    assert r.status_code == 422 and r.json()["detail"] == "unknown_build_node"


def test_control_state_accepts_a_reported_agent_node(client, db, session_admin):
    from dms.repositories import Repositories
    Repositories(db).agents.ingest("dms-w1", {})
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": "dms-w1"})
    assert r.status_code == 200 and r.json()["build_node_name"] == "dms-w1"


def test_control_state_put_without_build_node_skips_the_agent_node_check(client, session_admin):
    # build_node_name을 아예 안 주거나 공백만 주면(=미설정으로 정규화) 노드 존재
    # 검사 자체를 건너뛴다 -- 지정 안 함은 항상 허용돼야 한다.
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None})
    assert r.status_code == 200 and r.json()["build_node_name"] is None


# --- 토큰 감사 actor 의미(슬라이스 12·19) ---
# 컨트롤 상태 PUT 은 세션 관리자 전용이 됐으므로(2026-10-07) 토큰 actor 정규화·token: 접두는
# 여전히 토큰이 되는 감사 변경(포탈 서브네임, mutation_class portal_settings)으로 고정한다.
# 의미 자체(auth.current_identity·audit_actor)는 라우트와 무관하게 같다.

def _token_put(client, headers=ADMIN):
    return client.put("/api/admin/portal-settings", json={"subtitle": "SSC"}, headers=headers)


def _last_token_audit_actor(db):
    rows = db.query("SELECT actor FROM audit_log WHERE mutation_class = 'portal_settings' ORDER BY id")
    return rows[-1]["actor"]


def test_token_auth_audit_actor_is_prefixed(client, db):
    # 공유 토큰은 스크립트다. 사람 admin과 감사 로그에서 구분되지 않으면
    # "누가 이 정책을 바꿨나"에 답할 수 없다.
    assert _token_put(client).status_code == 200
    # 헤더에서 x-dms-actor 를 지운 뒤 토큰 actor 는 shared-token 으로 정규화되고
    # audit_actor 가 token: 접두를 붙인다(감사 표식은 계속 기록된다, 슬라이스 19).
    assert _last_token_audit_actor(db) == "token:shared-token"


def test_token_auth_default_actor_audit_is_not_bare_prefix(client, db):
    # x-dms-actor 헤더가 없는 호출의 기본 actor는 "shared-token"이다 -- 여기에
    # 접두를 붙였을 때 빈 "token:"이 나오면 안 된다.
    assert _token_put(client, {"Authorization": "Bearer tok-shared"}).status_code == 200
    assert _last_token_audit_actor(db) == "token:shared-token"


def test_session_auth_audit_actor_is_bare(client, db):
    # 사람 admin이 세션으로 낸 변경은 접두 없이 순수 로그인 이름 그대로 남아야 한다.
    client.post("/api/admin/accounts", json={"username": "boss", "password": "pw"},
               headers={"x-admin-token": "tok-admin"})
    client.post("/api/auth/login", json={"username": "boss", "password": "pw"})
    client.put("/api/admin/control-state",
               json={"maintenance": False, "drain": False, "reason": None})
    rows = db.query("SELECT * FROM audit_log WHERE mutation_class = 'control_state'")
    assert rows[-1]["actor"] == "boss"
    assert ":" not in rows[-1]["actor"]


def test_reserved_actor_prefix_in_header_is_rejected(client):
    # 접두가 서버 소유의 출처 표식이 아니게 되면 호출자가 자기 출처를 위조할 수 있다.
    # token:alice 는 node:<이름> 이 아니므로 슬라이스 19 의 더 넓은 actor 게이트가
    # 401 이 아니라 400 으로 거절한다(위조 경로는 여전히 닫혀 있다).
    r = _token_put(client, {"Authorization": "Bearer tok-shared", "x-dms-actor": "token:alice"})
    assert r.status_code == 400


def test_empty_actor_header_audit_is_not_bare_prefix(client, db):
    # x-dms-actor 헤더가 "헤더 자체가 없음"이 아니라 빈 문자열로 존재하면
    # request.headers.get(..., "shared-token") 기본값 폴백을 건너뛴다 -- 그대로
    # actor로 쓰면 audit_log에 빈 "token:"이 남아 추적성이 사라진다.
    r = _token_put(client, {"Authorization": "Bearer tok-shared", "x-dms-actor": ""})
    assert r.status_code == 200
    assert _last_token_audit_actor(db) == "token:shared-token"


def test_whitespace_only_actor_header_audit_is_not_bare_prefix(client, db):
    # 빈 문자열만 막고 공백만 있는 값을 방치하면 같은 구멍이 옆문으로 남는다.
    r = _token_put(client, {"Authorization": "Bearer tok-shared", "x-dms-actor": "   "})
    assert r.status_code == 200
    assert _last_token_audit_actor(db) == "token:shared-token"


# ---- 컨트롤 상태 변경 이력(슬라이스 36) ----

def test_control_state_history_returns_before_after_snapshots(client, session_admin):
    client.put("/api/admin/control-state",
               json={"maintenance": True, "drain": False, "reason": "점검"})
    client.put("/api/admin/control-state",
               json={"maintenance": False, "drain": False, "reason": None})
    rows = client.get("/api/admin/control-state/history", headers=ADMIN).json()
    assert len(rows) == 2
    # 최신 우선 -- 마지막 변경(해제)이 맨 앞이다.
    assert rows[0]["before"]["maintenance"] == 1
    assert rows[0]["after"]["maintenance"] == 0
    assert rows[1]["before"]["maintenance"] == 0   # 최초 저장 전 시드(0/0)
    assert rows[1]["after"]["reason"] == "점검"
    assert rows[0]["actor"] == session_admin       # 세션 관리자 이름 그대로(토큰은 이 경로를 못 쓴다)
    assert rows[0]["at"]


def test_control_state_history_respects_limit(client, session_admin):
    for i in range(4):
        client.put("/api/admin/control-state",
                   json={"maintenance": bool(i % 2), "drain": False, "reason": None})
    rows = client.get("/api/admin/control-state/history?limit=2",
                      headers=ADMIN).json()
    assert len(rows) == 2


def test_control_state_history_is_admin_only(client):
    client.post("/api/auth/signup", json={"username": "u9", "password": "p"})
    client.post("/api/auth/login", json={"username": "u9", "password": "p"})
    assert client.get("/api/admin/control-state/history").status_code in (401, 403)


# --- 빌드 노드 프록시(2026-09-08) ---

def _put(client, **extra):
    # 세션 관리자로 부른다(호출 테스트가 session_admin 을 받는다) -- 토큰은 403 이다.
    body = {"maintenance": False, "drain": False, "reason": None, **extra}
    return client.put("/api/admin/control-state", json=body)


def test_build_proxy_is_stored_normalized_and_returned(client, db, session_admin):
    r = _put(client, build_http_proxy=" http://proxy.corp:3128/ ",
             build_https_proxy="", build_no_proxy=" .corp.example , 10.0.0.0/8,, ")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["build_http_proxy"] == "http://proxy.corp:3128"
    assert body["build_https_proxy"] is None          # 빈 값 = 없음(HTTP 값을 따른다)
    assert body["build_no_proxy"] == ".corp.example,10.0.0.0/8"
    # 감사 이력(before/after 스냅샷)에 실린다
    hist = client.get("/api/admin/control-state/history", headers=ADMIN).json()
    assert hist[0]["after"]["build_http_proxy"] == "http://proxy.corp:3128"
    # BuildRunner 가 읽는 모양
    assert client.app.state.repos.control.build_proxy() == {
        "http_proxy": "http://proxy.corp:3128", "https_proxy": None,
        "no_proxy": ".corp.example,10.0.0.0/8", "host_network": False, "ca_path": None}


def test_build_host_network_switch_is_stored_and_exposed_to_the_runner(client, session_admin):
    r = _put(client, build_http_proxy="http://10.9.9.9:3128", build_host_network=True)
    assert r.status_code == 200 and r.json()["build_host_network"] == 1
    assert client.app.state.repos.control.build_proxy()["host_network"] is True
    r = _put(client, build_http_proxy="http://10.9.9.9:3128")        # 생략 = 끔
    assert r.json()["build_host_network"] == 0
    assert client.app.state.repos.control.build_proxy()["host_network"] is False


def test_build_proxy_ca_path_is_stored_and_validated(client, session_admin):
    r = _put(client, build_http_proxy="http://10.9.9.9:3128",
             build_proxy_ca_path=" /etc/pki/corp-proxy-ca.pem ")
    assert r.status_code == 200 and r.json()["build_proxy_ca_path"] == "/etc/pki/corp-proxy-ca.pem"
    assert client.app.state.repos.control.build_proxy()["ca_path"] == "/etc/pki/corp-proxy-ca.pem"
    for bad in ("relative/ca.pem", "/etc/../root/ca.pem", "/etc/ca dir/ca.pem", "/etc/pki/", "/etc/$(id).pem"):
        r = _put(client, build_proxy_ca_path=bad)
        assert (r.status_code, r.json()["detail"]) == (422, "invalid_proxy_ca_path"), bad
    assert _put(client).json()["build_proxy_ca_path"] is None       # 생략 = 해제


def test_proxy_hints_list_site_values_and_worker_node_ips(client):
    client.app.state.node_lister = lambda: [
        {"name": "dms-cp1", "ip": "10.10.10.10", "control_plane": True},
        {"name": "dms-w1", "ip": "10.10.10.11", "control_plane": False},
        {"name": "dms-w2", "ip": None, "control_plane": False},      # IP 모름 -> 생략
        {"name": "dms-w3", "ip": "10.10.10.13", "control_plane": False},
    ]
    body = client.get("/api/admin/control-state/proxy-hints", headers=ADMIN).json()
    assert body["registry"] == "pkg-01:5000" and body["registry_host"] == "pkg-01"
    assert body["nodes"] == [{"name": "dms-w1", "ip": "10.10.10.11"},
                             {"name": "dms-w3", "ip": "10.10.10.13"}]
    assert body["nodes_known"] is True
    assert body["suggested_no_proxy"] == ["pkg-01", "pkg-01:5000", "localhost", "127.0.0.1",
                                          ".svc", ".cluster.local", "10.10.10.11", "10.10.10.13"]
    assert body["auto_added"] == ["pkg-01:5000", "pkg-01", "localhost", "127.0.0.1"]


def test_proxy_hints_survive_node_listing_failure(client):
    def boom():
        raise RuntimeError("forbidden")
    client.app.state.node_lister = boom
    body = client.get("/api/admin/control-state/proxy-hints", headers=ADMIN).json()
    assert body["nodes"] == [] and body["nodes_known"] is False
    assert body["suggested_no_proxy"][:6] == ["pkg-01", "pkg-01:5000", "localhost", "127.0.0.1",
                                              ".svc", ".cluster.local"]


def test_proxy_hints_stub_backend_has_no_nodes(client):
    # conftest 앱은 스텁 백엔드 -- 노드 조회가 빈 목록(모름이 아니라 "없음")이다.
    body = client.get("/api/admin/control-state/proxy-hints", headers=ADMIN).json()
    assert body["nodes"] == [] and body["nodes_known"] is True


def test_proxy_hints_is_admin_only(client):
    assert client.get("/api/admin/control-state/proxy-hints").status_code == 401


def test_build_proxy_defaults_to_none_and_clears_when_omitted(client, session_admin):
    assert _put(client, build_http_proxy="http://p:1").status_code == 200
    body = _put(client).json()                        # 프록시 필드 생략 = 해제(무조건 UPDATE)
    assert (body["build_http_proxy"], body["build_https_proxy"],
            body["build_no_proxy"]) == (None, None, None)
    assert client.app.state.repos.control.build_proxy() == {
        "http_proxy": None, "https_proxy": None, "no_proxy": None, "host_network": False,
        "ca_path": None}


@pytest.mark.parametrize("bad", [
    "proxy.corp:3128",                    # 스킴 없음
    "socks5://proxy.corp:1080",           # http(s) 만
    "http://user:secret@proxy.corp:3128", # 자격증명 평문 저장 금지
    "http://proxy.corp:3128/path",
    "http://proxy.corp:notaport",
    "http://",
])
def test_invalid_proxy_url_is_rejected_without_saving(client, bad, session_admin):
    r = _put(client, build_https_proxy=bad)
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_proxy_url")
    assert client.get("/api/admin/control-state",
                      headers=ADMIN).json()["build_https_proxy"] is None


@pytest.mark.parametrize("bad", ["a b", "host;rm", "'x'", "$(id)"])
def test_invalid_no_proxy_is_rejected(client, bad, session_admin):
    r = _put(client, build_no_proxy=bad)
    assert (r.status_code, r.json()["detail"]) == (422, "invalid_no_proxy")


# --- 배포 경로는 세션 관리자만(admin_session_required, 2026-10-07) ---

def test_shared_token_cannot_drive_deploy_routes(client, db, tmp_path, monkeypatch):
    # 공유 토큰은 모든 노드 에이전트가 쥔 role admin 자격이다 -- 그것으로 릴리스를 수정 전 이미지로
    # 롤백하거나 빌드 소스·노드를 바꿔 root 로 도는 제어면·잡 이미지를 갈아 끼울 수 있으면 계정·배치 특권
    # 게이트가 통째로 무효가 된다. 변경 경로(작업 삭제 포함 일곱)는 토큰(에이전트의 node:* actor 포함)이면 403 이고 아무것도
    # 바뀌지 않는다. 조회는 토큰도 그대로 된다(포탈 밖 스크립트·모니터링).
    repos = client.app.state.repos
    repos.agents.ingest("dms-w1", {})                       # 세션이었다면 통과했을 조건을 갖춰 둔다
    done = repos.builds.create(source_path="/src/dms", images=["dms"], node_name="dms-w1", actor="t")
    repos.builds.finish(done, state="Succeeded", log_text="ok")          # 지울 수 있는 종단 빌드
    monkeypatch.setattr("dms.api.routes_releases.fetch_repo_tags", lambda registry, repo: ["d23"])
    touched = []                                           # 레지스트리를 아예 건드리지 않아야 한다
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.manifest_digest",
                        lambda *a: touched.append("digest"))
    monkeypatch.setattr("dms.api.routes_registry.registry_mod.delete_manifest",
                        lambda *a: touched.append("delete"))
    assert client.get("/api/admin/control-state", headers=ADMIN).status_code == 200
    state_before = repos.control.control_state()
    audits_before = db.query("SELECT COUNT(*) AS n FROM audit_log")[0]["n"]
    calls = (
        ("PUT", "/api/admin/control-state",
         {"maintenance": True, "drain": True, "reason": "x",
          "build_node_name": "dms-w1", "build_source_path": "/elsewhere/src"}),
        ("POST", "/api/admin/builds", {"images": ["dms"]}),
        ("DELETE", f"/api/admin/builds/{done}", None),
        ("POST", "/api/admin/releases", {"items": [{"component": "dms-api", "tag": "d23"},
                                                   {"component": "job-image", "tag": "d23"}]}),
        ("DELETE", "/api/admin/registry/images/dms/d23", None),
        ("PUT", "/api/admin/artifact-base", {"uri": f"file://{tmp_path}", "force": True}),
        # 작업(요청) 삭제(2026-10-08): 컨펌·취소 기록과 root 실행 산출물을 없애는 증거 삭제 -- 같은 세션 전용 경계.
        ("POST", "/api/admin/requests:delete", {"request_ids": ["0" * 32]}),
    )
    for headers in (ADMIN, {**ADMIN, "x-dms-actor": "node:storage-01"}):
        for method, path, body in calls:
            r = client.request(method, path, json=body, headers=headers)
            assert (r.status_code, r.json()["detail"]) == (403, "admin_session_required"), (method, path)
    assert repos.control.control_state() == state_before   # 유지보수·빌드 노드·소스·base·잡 이미지 그대로
    assert [b["build_id"] for b in repos.builds.list()] == [done]
    assert repos.releases.list() == []
    assert touched == []
    assert db.query("SELECT COUNT(*) AS n FROM audit_log")[0]["n"] == audits_before
    for path in ("/api/admin/control-state", "/api/admin/control-state/history",
                 "/api/admin/builds", "/api/admin/releases", "/api/admin/artifact-base",
                 "/api/admin/request-purges"):
        assert client.get(path, headers=ADMIN).status_code == 200, path
    # 같은 변경이 세션 관리자에겐 열려 있다 -- 403 은 경로 고장이 아니라 인증 방식 때문이다.
    client.app.state.repos.accounts.create("opadm", "p", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "opadm", "password": "p"})
    r = client.put("/api/admin/control-state", json=calls[0][2])
    assert r.status_code == 200 and r.json()["maintenance"] == 1
