ADMIN = {"Authorization": "Bearer tok-shared", "x-dms-actor": "ops"}


def _set_build_node(client, node="dms-w1"):
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": node},
                   headers=ADMIN)
    assert r.status_code == 200
    return r


def test_submit_requires_build_node(client):
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "build_node_not_set"


def test_submit_rejected_during_maintenance(client):
    # 유지보수 창에서는 build_node_name이 설정돼 있어도 신규 제출을 막는다 --
    # reject_when_maintenance는 다른 제출 경로와 동일하게 적용돼야 한다(admin도 예외 없음).
    _set_build_node(client)
    client.put("/api/admin/control-state",
               json={"maintenance": True, "drain": False, "reason": "정비",
                     "build_node_name": "dms-w1"},
               headers=ADMIN)
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 503 and r.json()["detail"] == "maintenance_mode"


def test_submit_accepted_once_node_is_set(client):
    _set_build_node(client)
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 202
    body = r.json()
    assert body["state"] == "Pending" and body["build_id"]


def test_second_concurrent_submit_is_rejected(client):
    _set_build_node(client)
    client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
               headers=ADMIN)
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 409 and r.json()["detail"] == "build_in_progress"


def test_unknown_image_is_rejected(client):
    _set_build_node(client)
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["nope"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_image"


def test_empty_images_is_rejected(client):
    # build_build_pod는 images를 BUILD_IMAGES와 교집합으로 필터링한다 -- 빈 목록을
    # 허용하면 파드가 아무것도 빌드하지 않고 조용히 성공으로 끝난다.
    _set_build_node(client)
    r = client.post("/api/admin/builds", json={"git_ref": "main", "images": []},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "unknown_image"


def test_bad_git_ref_is_rejected(client):
    _set_build_node(client)
    r = client.post("/api/admin/builds", json={"git_ref": "ma in", "images": ["dms"]},
                    headers=ADMIN)
    assert r.status_code == 422 and r.json()["detail"] == "invalid_git_ref"


def test_detail_exposes_the_tag_that_will_be_pushed(client):
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    r = client.get(f"/api/admin/builds/{bid}", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["tag"] == "b" + bid[:8]
    assert r.json()["images"] == ["dms"]


def test_missing_build_is_404(client):
    assert client.get("/api/admin/builds/nope", headers=ADMIN).status_code == 404
    assert client.get("/api/admin/builds/nope/log", headers=ADMIN).status_code == 404


def test_list_is_admin_only(client):
    client.post("/api/auth/signup", json={"username": "u1", "password": "p"})
    client.post("/api/auth/login", json={"username": "u1", "password": "p"})
    assert client.get("/api/admin/builds").status_code in (401, 403)


def test_list_orders_newest_first(client):
    _set_build_node(client)
    first = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                        headers=ADMIN).json()["build_id"]
    # 두 번째 빌드를 걸려면 첫 빌드를 종단 상태로 만들어야 한다(active 하나 제약).
    # 라우터에는 종료 엔드포인트가 없다 -- 그건 별도 컨트롤러의 몫이므로 리포지토리를
    # 직접 써서 시뮬레이션한다.
    client.app.state.repos.builds.finish(first, state="Succeeded", log_text="ok")
    second = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                         headers=ADMIN).json()["build_id"]
    listed = client.get("/api/admin/builds", headers=ADMIN).json()
    ids = [b["build_id"] for b in listed]
    assert ids.index(second) < ids.index(first)


def test_log_uses_log_text_when_build_is_terminal(client):
    # 진행 중이면 파드에서 실시간으로 읽지만, 종단이면 파드가 GC 되어 사라질 수 있어
    # DB에 박제된 log_text가 진실이다.
    _set_build_node(client)
    bid = client.post("/api/admin/builds", json={"git_ref": "main", "images": ["dms"]},
                      headers=ADMIN).json()["build_id"]
    client.app.state.repos.builds.finish(bid, state="Succeeded", log_text="line1\nline2\nline3\n")
    r = client.get(f"/api/admin/builds/{bid}/log", headers=ADMIN)
    assert r.status_code == 200
    assert r.json() == {"build_id": bid, "log": "line1\nline2\nline3\n"}
    tailed = client.get(f"/api/admin/builds/{bid}/log", headers=ADMIN, params={"tail": 1})
    assert tailed.json()["log"] == "line3"


def test_control_state_accepts_build_node(client):
    r = client.put("/api/admin/control-state",
                   json={"maintenance": False, "drain": False, "reason": None,
                         "build_node_name": "dms-w2"},
                   headers=ADMIN)
    assert r.status_code == 200 and r.json()["build_node_name"] == "dms-w2"
