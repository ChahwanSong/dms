ADMIN = {"Authorization": "Bearer tok-shared"}

SRC = "/home/mason/dms-dev/dms"


def _set_build_node(client, node="dms-w1", source_path=SRC):
    # I1: build_node_name은 이제 자유 입력이 아니라 agent_nodes에 보고된 노드
    # 이름 중에서만 골라야 한다(422 unknown_build_node) -- PUT 전에 노드를
    # 보고해 둔다. 소스 경로도 같은 화면(컨트롤 상태)이 단일 진실이다.
    client.app.state.repos.agents.ingest(node, {})
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": node, "build_source_path": source_path},
                   headers=ADMIN)
    assert r.status_code == 200
    return r


def test_submit_requires_build_node(client):
    r = client.post("/api/admin/builds", json={"images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "build_node_not_set"


def test_submit_requires_source_path(client):
    # 노드는 있는데 소스 경로가 미설정이면 파드를 만들기 전에 즉답한다 -- 사유가
    # "고칠 화면"(컨트롤 상태)을 가리켜야 하므로 unknown_image 등보다 먼저 검사한다.
    _set_build_node(client, source_path=None)
    r = client.post("/api/admin/builds", json={"images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "build_source_not_set"


def test_submit_rejected_during_maintenance(client):
    # 유지보수 창에서는 build_node_name이 설정돼 있어도 신규 제출을 막는다 --
    # reject_when_maintenance는 다른 제출 경로와 동일하게 적용돼야 한다(admin도 예외 없음).
    _set_build_node(client)
    client.put("/api/admin/control-state",
               json={"maintenance": True, "drain": False, "reason": "정비",
                     "build_node_name": "dms-w1", "build_source_path": SRC},
               headers=ADMIN)
    r = client.post("/api/admin/builds", json={"images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 503 and r.json()["detail"] == "maintenance_mode"


def test_submit_accepted_once_node_and_source_are_set(client):
    _set_build_node(client)
    r = client.post("/api/admin/builds", json={"images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "Pending" and body["build_id"]


def test_second_concurrent_submit_is_rejected(client):
    _set_build_node(client)
    client.post("/api/admin/builds", json={"images": ["dms"]},
               headers=ADMIN)
    r = client.post("/api/admin/builds", json={"images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "build_in_progress"


def test_unknown_image_is_rejected(client):
    _set_build_node(client)
    r = client.post("/api/admin/builds", json={"images": ["nope"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_image"


def test_empty_images_is_rejected(client):
    # build_build_pod는 images를 BUILD_IMAGES와 교집합으로 필터링한다 -- 빈 목록을
    # 허용하면 파드가 아무것도 빌드하지 않고 조용히 성공으로 끝난다.
    _set_build_node(client)
    r = client.post("/api/admin/builds", json={"images": []},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_image"


def test_bad_tag_is_rejected(client):
    # 태그는 컨테이너 태그 문법의 보수적 부분집합이다 -- 공백·선행 특수문자가
    # push ref 로 흘러가면 buildah 깊숙한 곳에서 알 수 없는 오류로 죽는다.
    _set_build_node(client)
    for bad in ("has space", "-lead", ".lead", "x" * 65):
        r = client.post("/api/admin/builds", json={"images": ["dms"], "tag": bad},
                        headers=ADMIN)
        assert r.status_code == 422 and r.json()["detail"] == "invalid_build_tag", bad


def test_operator_tag_is_stored_and_exposed(client):
    # 지정 태그(d73)는 파생 태그(b+8hex)를 대체한다 -- 상세의 tag 가 그대로
    # push 태그다(러너도 같은 effective_tag 를 쓴다).
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"], "tag": "d73"},
                      headers=ADMIN).json()["build_id"]
    r = client.get(f"/api/admin/builds/{bid}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["tag"] == "d73"


def test_blank_tag_falls_back_to_the_derived_tag(client):
    # 빈 문자열·공백 태그는 "미지정"과 같다 -- 파생 태그가 쓰인다.
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"], "tag": "  "},
                      headers=ADMIN).json()["build_id"]
    assert client.get(f"/api/admin/builds/{bid}",
                      headers=ADMIN).json()["tag"] == "b" + bid[:8]


def test_detail_exposes_the_tag_that_will_be_pushed(client):
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    r = client.get(f"/api/admin/builds/{bid}", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["tag"] == "b" + bid[:8]
    assert r.json()["images"] == ["dms"]


def test_detail_exposes_the_source_path(client):
    # 컬럼명(repo_url)은 스키마 수렴 제약의 산물이다 -- API 경계에서 source_path
    # 라는 제 이름으로 나가야 프론트가 컬럼 사정을 몰라도 된다.
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    detail = client.get(f"/api/admin/builds/{bid}", headers=ADMIN).json()
    assert detail["source_path"] == SRC
    assert detail["git_ref"] == "local"


def test_missing_build_is_404(client):
    assert client.get("/api/admin/builds/nope", headers=ADMIN).status_code == 404
    assert client.get("/api/admin/builds/nope/log", headers=ADMIN).status_code == 404


# ---- 빌드 이력 삭제(슬라이스 34) ----

def test_delete_terminal_build_removes_the_row(client):
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    client.app.state.repos.builds.finish(bid, state="Succeeded", log_text="ok")
    r = client.delete(f"/api/admin/builds/{bid}", headers=ADMIN)
    assert r.status_code == 200 and r.json()["deleted"] == bid
    assert client.get(f"/api/admin/builds/{bid}", headers=ADMIN).status_code == 404
    assert client.get("/api/admin/builds", headers=ADMIN).json() == []


def test_delete_active_build_is_409(client):
    # 활성(Pending) 빌드를 지우면 active() 가 읽는 행이 사라져 두 번째 빌드가
    # 파드가 도는 중에 시작될 수 있다 -- 종단 전에는 거절한다.
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    r = client.delete(f"/api/admin/builds/{bid}", headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "build_not_deletable"
    assert client.get(f"/api/admin/builds/{bid}", headers=ADMIN).status_code == 200


def test_delete_missing_build_is_404(client):
    assert client.delete("/api/admin/builds/nope", headers=ADMIN).status_code == 404


def test_delete_is_admin_only(client):
    client.post("/api/auth/signup", json={"username": "u2", "password": "p"})
    client.post("/api/auth/login", json={"username": "u2", "password": "p"})
    assert client.delete("/api/admin/builds/x").status_code in (401, 403)


def test_list_is_admin_only(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/builds").status_code in (401, 403)


def test_list_orders_newest_first(client):
    _set_build_node(client)
    first = client.post("/api/admin/builds", json={"images": ["dms"]},
                        headers=ADMIN).json()["build_id"]
    # 두 번째 빌드를 걸려면 첫 빌드를 종단 상태로 만들어야 한다(active 하나 제약).
    # 라우터에는 종료 엔드포인트가 없다 -- 그건 별도 컨트롤러의 몫이므로 리포지토리를
    # 직접 써서 시뮬레이션한다.
    client.app.state.repos.builds.finish(first, state="Succeeded", log_text="ok")
    second = client.post("/api/admin/builds", json={"images": ["dms"]},
                         headers=ADMIN).json()["build_id"]
    listed = client.get("/api/admin/builds", headers=ADMIN).json()
    ids = [b["build_id"] for b in listed]
    assert ids.index(second) < ids.index(first)


def test_list_response_never_carries_log_text_or_seq(client):
    # I2: 목록 응답은 로그 텍스트를 실어 나르지 않는다 -- 전용 /log 엔드포인트가
    # 있고, 프론트는 목록 화면에서 log_text를 쓰지 않는다. seq도 내부 컬럼이다.
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    client.app.state.repos.builds.finish(bid, state="Succeeded", log_text="x" * 1000)
    listed = client.get("/api/admin/builds", headers=ADMIN).json()
    assert "log_text" not in listed[0]
    assert "seq" not in listed[0]


def test_detail_response_never_carries_log_text_or_seq(client):
    # 상세 화면도 로그는 /log를 따로 부른다 -- 여기 실으면 상세 조회마다 최대 64KB가
    # 불필요하게 왕복한다.
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    client.app.state.repos.builds.finish(bid, state="Succeeded", log_text="x" * 1000)
    detail = client.get(f"/api/admin/builds/{bid}", headers=ADMIN).json()
    assert "log_text" not in detail
    assert "seq" not in detail


def test_log_uses_log_text_when_build_is_terminal(client):
    # 진행 중이면 파드에서 실시간으로 읽지만, 종단이면 파드가 GC 되어 사라질 수 있어
    # DB에 박제된 log_text가 진실이다.
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    client.app.state.repos.builds.finish(bid, state="Succeeded", log_text="line1\nline2\nline3\n")
    r = client.get(f"/api/admin/builds/{bid}/log", headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"build_id": bid, "log": "line1\nline2\nline3\n"}
    tailed = client.get(f"/api/admin/builds/{bid}/log", headers=ADMIN, params={"tail": 1})
    assert tailed.json()["log"] == "line3"


def test_whitespace_only_build_node_is_treated_as_unset(client):
    # build_node_name은 k8s nodeSelector로 그대로 흘러간다 -- 공백만 있는 값이 저장되면
    # 파드가 스케줄되지 않고 조용히 Pending에 머문다. PUT 시점에 trim해 빈 문자열은
    # None(미설정)으로 정규화되는지 확인한다.
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": "   ", "build_source_path": SRC},
                   headers=ADMIN)
    assert r.status_code == 200 and r.json()["build_node_name"] is None
    r2 = client.post("/api/admin/builds", json={"images": ["dms"]},
                     headers=ADMIN)
    assert r2.status_code == 422 and r2.json()["detail"] == "build_node_not_set"


def test_log_reads_from_runner_with_the_exported_ref_prefix_while_active(client):
    # I5: 진행 중(Pending/Running)이면 라우터가 buildpod/<pod-name> ref를 직접
    # 구성해 runner.read_log를 부른다(routes_builds.py). 이 리터럴이 build_runner.py의
    # BUILD_REF_PREFIX와 어긋나면 조회가 조용히 miss돼 로그가 안 보인다 -- StubBuildRunner의
    # 내부 로그를 정확한 ref로 시딩해 라우터가 그 ref를 그대로 구성하는지 검증한다.
    from dms.build_runner import BUILD_REF_PREFIX
    from dms.repositories.builds import build_pod_name
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    assert client.get(f"/api/admin/builds/{bid}", headers=ADMIN).json()["state"] == "Pending"
    ref = f"{BUILD_REF_PREFIX}/{build_pod_name(bid)}"
    client.app.state.build_runner._log[ref] = "still building...\n"
    r = client.get(f"/api/admin/builds/{bid}/log", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["log"] == "still building...\n"


def test_control_state_accepts_build_node(client):
    client.app.state.repos.agents.ingest("dms-w2", {})
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": "dms-w2"},
                   headers=ADMIN)
    assert r.status_code == 200 and r.json()["build_node_name"] == "dms-w2"


# ---- 로컬 소스 빌드(슬라이스 33): 소스 경로 검증 -- 저장(PUT)과 제출(POST) ----

def test_control_state_rejects_a_relative_source_path(client):
    # 상대 경로·'..'·제어문자는 hostPath 대상으로 흘러가면 안 된다 -- 저장 시점에
    # 모양을 거른다(실재 여부는 프리플라이트가 노드 위에서 검사).
    for bad in ("relative/path", "/a/../b", "/a\npath"):
        r = client.put("/api/admin/control-state",
                       json={"maintenance": False, "drain": False, "reason": None,
                             "build_node_name": None, "build_source_path": bad},
                       headers=ADMIN)
        assert r.status_code == 422 and r.json()["detail"] == "invalid_source_path", bad


def test_control_state_normalizes_blank_source_path_to_unset(client):
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": None, "build_source_path": "   "},
                   headers=ADMIN)
    assert r.status_code == 200 and r.json()["build_source_path"] is None


def test_submit_revalidates_a_tampered_source_path(client):
    # DB 는 신뢰 경계다 -- 저장 검증을 우회해 심긴 값(직접 UPDATE)이 hostPath 로
    # 흘러가기 전에 제출 시점 재검증이 fail-closed 로 막아야 한다.
    _set_build_node(client)
    client.app.state.repos.control._db.execute(
        "UPDATE control_state SET build_source_path = 'rel/../etc' WHERE id = 1", {})
    r = client.post("/api/admin/builds", json={"images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_source_path"


def test_stale_build_node_report_is_rejected_at_the_exact_threshold(client):
    # fresh 판정은 reported_at > (now - stale_seconds) 엄격 부등호다(agents.py
    # list_nodes) -- 정확히 문턱 나이의 리포트는 stale 이다. 경계값을 고정해 두면
    # 부등호가 >= 로 바뀌는 회귀도 잡힌다(라우트 호출 시점의 now 는 ingest 시점
    # 이상이므로 어느 쪽이든 문턱 리포트는 stale 로 판정돼야 한다).
    from dms.db import iso_plus, utc_now_iso
    node = "dms-w1"
    stale = client.app.state.settings.agent_report_stale_seconds
    _set_build_node(client, node=node)
    # 마지막 리포트를 정확히 문턱 나이로 교체 -- ingest 는 노드당 1행 교체라
    # agent_nodes 의 유일 행이 이 시각이 된다.
    client.app.state.repos.agents.ingest(
        node, {}, reported_at=iso_plus(utc_now_iso(), -stale))
    r = client.post("/api/admin/builds", json={"images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "build_node_report_stale"


def test_fresh_build_node_report_passes_the_stale_gate(client):
    _set_build_node(client)   # ingest 가 지금 막 리포트를 넣는다 -- fresh
    r = client.post("/api/admin/builds", json={"images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 202
