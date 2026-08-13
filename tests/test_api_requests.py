ADMIN = {"Authorization": "Bearer tok-shared"}


def _login(client, name):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _pending_request(repos, requester="alice"):
    # test_api_request_cancel.py의 동명 헬퍼와 동일한 모양 -- 두 파일이 별개의
    # 테스트 모듈이라 공유하지 않고 그대로 복제한다.
    return repos.requests.create(
        operation="rm", requester_id=requester, actor=requester,
        resource_key="k", payload={"storage": "s", "target": "a"}, priority="mid")


def test_submit_scan_and_poll(client):
    # scan은 관리자 전용 제출이다 (test_scan_submission_is_admin_only 참고) — Bearer로 인증한다.
    r = client.post("/api/user/requests", headers=ADMIN, json={
        "operation": "scan", "storage": "ceph-a", "target": "team/data",
        "options": {"top_k": 5}, "priority": "high"})
    assert r.status_code == 202
    rid = r.json()["request_id"]
    detail = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    assert detail["state"] == "Pending"
    assert detail["operation"] == "scan"
    assert detail["transitions"][0]["to_state"] == "Pending"
    assert detail["resource_key"].startswith("data.scan:ceph-a:team/data:")


def test_validation_maps_to_422(client):
    _login(client, "bob")
    cases = [
        ({"operation": "rm", "storage": "s", "target": "a", "options": {}},
         "rm_recursive_required"),
        ({"operation": "rm", "storage": None, "target": "a",
          "options": {"recursive": True}}, "missing_storage"),
        ({"operation": "sync", "source_storage": "s", "source": "a",
          "destination_storage": "s", "destination": "a/b"},
         "sync_destination_inside_source"),
        ({"operation": "sync", "source_storage": "s", "source": "a",
          "destination": "b"}, "missing_destination_storage"),
    ]
    for body, reason in cases:
        r = client.post("/api/user/requests", json=body)
        assert r.status_code == 422 and r.json()["detail"] == reason, body

    # scan은 관리자 전용 제출이라 admin 인증으로 검증 오류를 확인한다.
    admin_cases = [
        ({"operation": "scan", "storage": "s", "target": "/abs"}, "unsafe_path"),
        ({"operation": "scan", "target": "a"}, "missing_storage"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "options": {"nope": 1}}, "unknown_option"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "priority": "urgent"}, "invalid_priority"),
    ]
    for body, reason in admin_cases:
        r = client.post("/api/user/requests", json=body, headers=ADMIN)
        assert r.status_code == 422 and r.json()["detail"] == reason, body


def test_isolation_between_users_and_admin_sees_all(client):
    _login(client, "alice")
    rid = client.post("/api/user/requests", json={
        "operation": "rm", "storage": "s1", "target": "a",
        "options": {"recursive": True}}).json()["request_id"]
    client.post("/api/auth/logout")
    _login(client, "eve")
    assert client.get(f"/api/user/requests/{rid}").status_code == 404
    assert client.get("/api/user/requests").json() == []
    assert client.get("/api/user/requests", headers=ADMIN).json()[0]["request_id"] == rid


def test_unknown_operation_is_422_not_500(client):
    # scan 관리자 게이트는 422 매핑 try 블록 앞에 있다 — 거기서 Operation(...)을
    # 구성하면 알 수 없는 연산이 ValueError로 새어 500이 된다.
    _login(client, "dave")
    r = client.post("/api/user/requests", json={"operation": "bogus", "storage": "s1"})
    assert r.status_code == 422
    assert r.json()["detail"] == "invalid_operation"


def test_scan_submission_is_admin_only(client):
    _login(client, "carol")
    r = client.post("/api/user/requests", json={
        "operation": "scan", "storage": "s1", "target": "a"})
    assert r.status_code == 403
    assert r.json()["detail"] == "scan_admin_only"

    r = client.post("/api/user/requests", headers=ADMIN, json={
        "operation": "scan", "storage": "s1", "target": "a"})
    assert r.status_code == 202

    # sync와 rm은 non-admin에게 여전히 영향받지 않는다.
    r = client.post("/api/user/requests", json={
        "operation": "rm", "storage": "s1", "target": "a",
        "options": {"recursive": True}})
    assert r.status_code == 202

    r = client.post("/api/user/requests", json={
        "operation": "sync", "source_storage": "s1", "source": "a",
        "destination_storage": "s2", "destination": "b"})
    assert r.status_code == 202


def test_request_detail_carries_events(client):
    # events는 state_transitions가 담지 못하는 것 -- 일어나지 않은 전이 -- 를 담는다.
    # 화면에 안 뜨면 운영자는 kubectl logs로 stderr를 뒤져야 한다(파드 재시작 시 소실).
    repos = client.app.state.repos
    rid = _pending_request(repos)
    repos.observability.record_event(
        component="planner", severity="error", event_type="plan_error",
        message="boom", request_id=rid)
    body = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    assert [e["event_type"] for e in body["events"]] == ["plan_error"]
    assert body["events"][0]["message"] == "boom"


def test_request_detail_events_are_scoped(client):
    # 다른 request_id로 기록된 이벤트가 새어 들어오지 않는지 -- 요청 상세는 기존
    # 소유권 검사를 그대로 타므로 여기에 추가 인가 로직을 넣지 않았다. 이 테스트는
    # events 조회가 request_id로 제대로 스코프되는지를 리뷰가 확인할 근거다.
    repos = client.app.state.repos
    rid = _pending_request(repos)
    repos.observability.record_event(component="planner", severity="error",
                                     event_type="other", request_id="someone-else")
    assert client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()["events"] == []


def test_request_detail_marks_truncation_past_the_display_cap(client):
    # events_for_request의 기본 limit=100이 API에 그대로 적용되면 잘린 사실이
    # 화면에 보이지 않는다 -- 조용한 절단. 플래너/스테퍼가 같은 요청에서 매 틱
    # 실패를 반복하면 100건을 넘기는 것도 현실적인 시나리오다(스택된 요청).
    # 잘릴 때 버려지는 건 가장 오래된 한 건(boom0)이어야 한다 -- 최신(boom100)이
    # 사라지면 "지금도 실패 중인지"를 운영자가 놓친다.
    repos = client.app.state.repos
    rid = _pending_request(repos)
    for i in range(101):
        repos.observability.record_event(
            component="planner", severity="error", event_type="plan_error",
            message=f"boom{i}", request_id=rid)
    body = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    messages = [e["message"] for e in body["events"]]
    assert len(body["events"]) == 100
    assert body["events_truncated"] is True
    assert "boom0" not in messages          # 가장 오래된 것이 버려졌다
    assert messages[0] == "boom1"           # 오름차순 유지, 다음으로 오래된 것이 맨 앞
    assert messages[-1] == "boom100"        # 최신이 살아남아 맨 뒤에 온다


def test_request_detail_events_truncated_flag_is_false_under_the_cap(client):
    repos = client.app.state.repos
    rid = _pending_request(repos)
    repos.observability.record_event(
        component="planner", severity="error", event_type="plan_error",
        message="boom", request_id=rid)
    body = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    assert body["events_truncated"] is False


def test_request_detail_events_exactly_at_the_display_cap_is_not_truncated(client):
    # 경계값: 정확히 100건이면 전부 보여야 하고 잘림 플래그도 False여야 한다.
    # len(events) > display_limit 을 >= 로 잘못 쓰면 이 케이스가 깨진다.
    repos = client.app.state.repos
    rid = _pending_request(repos)
    for i in range(100):
        repos.observability.record_event(
            component="planner", severity="error", event_type="plan_error",
            message=f"boom{i}", request_id=rid)
    body = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    messages = [e["message"] for e in body["events"]]
    assert len(body["events"]) == 100
    assert body["events_truncated"] is False
    assert messages[0] == "boom0"
    assert messages[-1] == "boom99"


def test_list_limit_param_is_honored_and_capped(client):
    # 대시보드 「최근 작업」(2026-08-13 조정): 최근 200건을 요청한다. limit 은
    # 쿼리 파라미터로 열되 서버가 1..200 으로 캡한다 -- 무제한이면 전량 SELECT 가
    # 화면 하나에 끌려 나온다. 정렬은 commit_order DESC(최신 요청순, 기존 계약).
    _login(client, "alice")
    rids = []
    for i in range(3):
        rids.append(client.post("/api/user/requests", json={
            "operation": "rm", "storage": "s1", "target": f"t{i}",
            "options": {"recursive": True}}).json()["request_id"])
    rows = client.get("/api/user/requests", params={"limit": 2}).json()
    assert [r["request_id"] for r in rows] == [rids[2], rids[1]]  # 최신순 + limit
    assert client.get("/api/user/requests", params={"limit": 0}).status_code == 422
    assert client.get("/api/user/requests", params={"limit": 201}).status_code == 422
    # 표시에 쓰는 필드가 응답에 실려 있다(SELECT * 계약의 명시화).
    row = rows[0]
    for field in ("requester_id", "operation", "state", "created_at",
                  "updated_at", "payload"):
        assert field in row, field
