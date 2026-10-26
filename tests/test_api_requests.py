import pytest

from dms.domain import DataJobState, RequestState

ADMIN = {"Authorization": "Bearer tok-shared"}


@pytest.fixture(autouse=True)
def _allow_user_sync_pairs(client):
    # 사용자 sync 허용 쌍(2026-09-30, 기본 전부 불가): 이 파일의 사용자 sync 는 목록·격리·필터·
    # 검증이 관심사라 쓰는 쌍을 미리 허용한다(게이트 자체는 test_sync_pairs 가 고정한다). 쌍은
    # 등록된 스토리지끼리만 되므로(SyncPairsRepository.add) 스토리지부터 둔다.
    repos = client.app.state.repos
    for name in ("s1", "s2", "s"):
        repos.storages.create(storage_name=name, mount_path=f"/mnt/{name}",
                              managed_root=f"/mnt/{name}/dms", backend_type="cephfs", actor="test")
    for source, destination in (("s1", "s1"), ("s1", "s2"), ("s", "s")):
        repos.sync_pairs.add(source, destination, actor="test")


def _login(client, name):
    client.post("/api/auth/signup", json={"username": name, "password": "p"})
    client.post("/api/auth/login", json={"username": name, "password": "p"})


def _login_ops(client):
    # 특권 세션 관리자(기본 특권 목록의 admin, role admin, 세션) -- k8s 신원 모드의 면제자라 직접 경로·scan 을 낸다. 공유
    # 토큰(Bearer)은 제출할 수 없다(root 는 세션 요청만 -- identity.root_allowed).
    accounts = client.app.state.repos.accounts
    if accounts.get("admin") is None:
        accounts.create("admin", "p", "admin")
    client.cookies.clear()
    client.post("/api/auth/login", json={"username": "admin", "password": "p"})


def _sync_row(client, requester, source="a", destination="b"):
    # 사용자 요청은 PV 주소로만 제출된다(직접 경로는 면제자만 -- raw_path_not_allowed). 목록·격리·필터가 관심사인 테스트는
    # 요청 행을 직접 만든다(제출 게이트는 test_k8s_submit 이 고정한다).
    return client.app.state.repos.requests.create(
        operation="sync", requester_id=requester, actor=requester,
        resource_key=f"data.sync:s1:{source}:s1:{destination}",
        payload={"source_storage": "s1", "source": source, "destination_storage": "s1",
                 "destination": destination, "options": {}, "owner_username": None, "run_as_root": True},
        priority="mid", auth_method="session")


def _pending_request(repos, requester="alice"):
    # test_api_request_cancel.py의 동명 헬퍼와 동일한 모양 -- 두 파일이 별개의
    # 테스트 모듈이라 공유하지 않고 그대로 복제한다.
    return repos.requests.create(
        operation="rm", requester_id=requester, actor=requester,
        resource_key="k", payload={"storage": "s", "target": "a"}, priority="mid")


def test_submit_scan_and_poll(client):
    # scan은 특권 운영자 전용 제출이다 (test_scan_submission_is_admin_only 참고) — 특권 세션 관리자로 낸다.
    _login_ops(client)
    r = client.post("/api/user/requests", json={
        "operation": "scan", "storage": "ceph-a", "target": "team/data",
        "options": {"broken_limit": 500}, "priority": "high"})
    assert r.status_code == 202
    rid = r.json()["request_id"]
    detail = client.get(f"/api/user/requests/{rid}").json()
    assert detail["state"] == "Pending"
    assert detail["operation"] == "scan"
    assert detail["transitions"][0]["to_state"] == "Pending"
    assert detail["resource_key"].startswith("data.scan:ceph-a:team/data:")


def test_validation_maps_to_422(client):
    # 직접 경로 검증 오류는 면제자(특권 세션 관리자)의 제출에서 본다 -- 일반 사용자의 직접 경로는 검증 전에
    # 403 raw_path_not_allowed 다(맵핑 게이트가 형식 검증보다 앞).
    _login_ops(client)
    admin_cases = [
        ({"operation": "sync", "source_storage": "s", "source": "a",
          "destination_storage": "s", "destination": "a/b"},
         "sync_destination_inside_source"),
        ({"operation": "sync", "source_storage": "s", "source": "a",
          "destination": "b"}, "missing_destination_storage"),
        ({"operation": "rm", "storage": "s", "target": "a", "options": {}},
         "rm_recursive_required"),
        ({"operation": "rm", "storage": None, "target": "a",
          "options": {"recursive": True}}, "missing_storage"),
        ({"operation": "scan", "storage": "s", "target": "/abs"}, "unsafe_path"),
        ({"operation": "scan", "target": "a"}, "missing_storage"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "options": {"nope": 1}}, "unknown_option"),
        ({"operation": "scan", "storage": "s", "target": "a",
          "priority": "urgent"}, "invalid_priority"),
    ]
    for body, reason in admin_cases:
        r = client.post("/api/user/requests", json=body)
        assert r.status_code == 422 and r.json()["detail"] == reason, body


def test_isolation_between_users_and_admin_sees_all(client):
    # 이 테스트의 관심사는 소유권 격리이지 제출 경로가 아니다.
    rid = _sync_row(client, "alice")
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
    # scan 은 특권 운영자만(2026-10-26 (g) -- scan_unrestricted_only 가 allowlist 보다 먼저), rm 은 사용자 연산
    # allowlist(2026-08-20, 기본 sync 만) 밖이라 403.
    _login(client, "carol")
    r = client.post("/api/user/requests", json={"operation": "scan", "storage": "s1", "target": "a"})
    assert (r.status_code, r.json()["detail"]) == (403, "scan_unrestricted_only")
    r = client.post("/api/user/requests", json={"operation": "rm", "storage": "s1", "target": "a",
                                                "options": {"recursive": True}})
    assert (r.status_code, r.json()["detail"]) == (403, "operation_admin_only")

    # sync 는 non-admin 에게 allowlist 로 열려 있다 -- 다음 게이트(직접 경로는 면제자만)가 판정한다.
    r = client.post("/api/user/requests", json={
        "operation": "sync", "source_storage": "s1", "source": "a",
        "destination_storage": "s2", "destination": "b"})
    assert (r.status_code, r.json()["detail"]) == (403, "raw_path_not_allowed")

    # 특권 운영자는 allowlist 와 무관하게 scan·rm 모두 가능
    _login_ops(client)
    r = client.post("/api/user/requests", json={"operation": "scan", "storage": "s1", "target": "a"})
    assert r.status_code == 202
    r = client.post("/api/user/requests", json={
        "operation": "rm", "storage": "s1", "target": "b", "options": {"recursive": True}})
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


def test_request_detail_carries_the_reason_of_a_planning_stage_rejection(client):
    # 잡이 만들어지기 **전** 거부(플래너 어드미션)는 잡 행이 아예 없다 -- 화면이
    # 잡의 reason_code 만 읽던 동안 이 사유들은 통째로 사라졌다(사용자 보고:
    # "사전 점검에 실패했습니다"만 보인다). 요청 자신이 사유를 실어야 한다.
    repos = client.app.state.repos
    rid = _pending_request(repos)
    repos.requests.set_state_with_result(
        rid, RequestState.REJECTED, reason_code="ldap_identity_not_found",
        actor="planner")
    body = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    assert body["reason_code"] == "ldap_identity_not_found"
    assert body["terminal_state"] == "Rejected"
    assert body["completed_at"]


def test_request_detail_carries_the_reason_of_a_preflight_rejection(client):
    # 잡이 있는 경로(스테퍼 finalize)도 같은 자리에서 읽혀야 한다 -- 요청 정보의
    # 「사유」가 잡 카드의 사유와 다른 소스를 보면 둘이 어긋난다.
    repos = client.app.state.repos
    rid = _pending_request(repos)
    repos.requests.finalize_from_job(
        rid, DataJobState.REJECTED, reason_code="source_not_readable", actor="stepper")
    body = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    assert body["reason_code"] == "source_not_readable"
    assert body["terminal_state"] == "Rejected"


def test_request_detail_has_no_reason_while_the_request_is_not_terminal(client):
    # 비종단은 null(아직 모른다)이지 ""(사유 없음)가 아니다 -- 키를 빼지 않고
    # null 로 실어야 프론트가 "아직"과 "없음"을 구분할 수 있다.
    repos = client.app.state.repos
    rid = _pending_request(repos)
    body = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    assert body["reason_code"] is None
    assert body["terminal_state"] is None
    assert body["completed_at"] is None
    assert body["reason_message"] is None


def test_request_detail_falls_back_to_the_terminal_transition_when_results_is_missing(client):
    # 슬라이스 27/30 이전에 종단이 된 요청은 "Rejected 인데 results 행이 없다"가
    # 될 수 있었다(전이와 results 가 별도 커밋이던 시절의 영구 결손). 그 요청의
    # 상세에서도 사유가 사라지면 안 된다 -- 전이 이력이 여전히 사유를 들고 있다.
    repos = client.app.state.repos
    rid = _pending_request(repos)
    repos.requests.set_state(rid, RequestState.REJECTED,
                             reason_code="no_eligible_nodes", actor="planner")
    body = client.get(f"/api/user/requests/{rid}", headers=ADMIN).json()
    assert body["reason_code"] == "no_eligible_nodes"
    # 결과 행 자체가 없다는 사실은 숨기지 않는다 -- 사유만 전이에서 되살린다.
    assert body["terminal_state"] is None
    assert body["completed_at"] is None


def test_request_detail_reason_does_not_leak_to_another_user(client):
    # 사유가 새 필드로 늘었어도 소유권 게이트는 그대로다(타인 요청은 404).
    rid = _sync_row(client, "alice")
    client.app.state.repos.requests.set_state_with_result(
        rid, RequestState.REJECTED, reason_code="identity_denied", actor="planner")
    _login(client, "eve")
    assert client.get(f"/api/user/requests/{rid}").status_code == 404


def test_list_limit_param_is_honored_and_capped(client):
    # 대시보드 「최근 작업」(2026-08-13 조정): 최근 200건을 요청한다. limit 은
    # 쿼리 파라미터로 열되 서버가 1..200 으로 캡한다 -- 무제한이면 전량 SELECT 가
    # 화면 하나에 끌려 나온다. 정렬은 commit_order DESC(최신 요청순, 기존 계약).
    rids = [_sync_row(client, "alice", f"t{i}", f"d{i}") for i in range(3)]
    _login(client, "alice")
    rows = client.get("/api/user/requests", params={"limit": 2}).json()
    assert [r["request_id"] for r in rows] == [rids[2], rids[1]]  # 최신순 + limit
    assert client.get("/api/user/requests", params={"limit": 0}).status_code == 422
    assert client.get("/api/user/requests", params={"limit": 201}).status_code == 422
    # 표시에 쓰는 필드가 응답에 실려 있다(SELECT * 계약의 명시화). commit_order 는
    # 무한 스크롤 커서라 반드시 실려야 한다(슬라이스 39).
    row = rows[0]
    for field in ("requester_id", "operation", "state", "created_at",
                  "updated_at", "payload", "commit_order"):
        assert field in row, field


def test_list_before_cursor_paginates_without_overlap(client):
    # 무한 스크롤(슬라이스 39): before=commit_order 로 그보다 오래된 것만.
    rids = [_sync_row(client, "alice", f"t{i}", f"d{i}") for i in range(5)]
    _login(client, "alice")
    page1 = client.get("/api/user/requests", params={"limit": 2}).json()
    assert [r["request_id"] for r in page1] == [rids[4], rids[3]]
    cursor = page1[-1]["commit_order"]
    page2 = client.get("/api/user/requests",
                       params={"limit": 2, "before": cursor}).json()
    assert [r["request_id"] for r in page2] == [rids[2], rids[1]]   # 겹침·누락 없음
    page3 = client.get("/api/user/requests",
                       params={"limit": 2, "before": page2[-1]["commit_order"]}).json()
    assert [r["request_id"] for r in page3] == [rids[0]]            # 마지막 쪽


def test_list_filters_by_operation_and_state(client, db):
    from dms.domain import RequestState
    sync_rid = _sync_row(client, "alice")
    _sync_row(client, "alice", "c", "d")
    _login(client, "alice")
    # operation 필터
    rows = client.get("/api/user/requests", params={"operation": "sync"}).json()
    assert len(rows) == 2 and all(r["operation"] == "sync" for r in rows)
    assert client.get("/api/user/requests", params={"operation": "rm"}).json() == []
    # state 필터: 하나를 종단 상태로 옮긴다
    client.app.state.repos.requests.set_state(
        sync_rid, RequestState.SUCCEEDED, actor="test")
    done = client.get("/api/user/requests", params={"state": "Succeeded"}).json()
    assert [r["request_id"] for r in done] == [sync_rid]


def test_list_requester_filter_is_admin_only(client):
    # 운영자만 requester 로 남의 작업을 필터할 수 있다. 사용자는 파라미터를 줘도
    # 자기 것만 본다(격리).
    a_rid = _sync_row(client, "alice")
    b_rid = _sync_row(client, "bob", "c", "d")
    _login(client, "bob")
    # bob 이 requester=alice 를 줘도 자기 것만(격리)
    rows = client.get("/api/user/requests", params={"requester": "alice"}).json()
    assert [r["request_id"] for r in rows] == [b_rid]
    # admin 은 requester 로 alice 것만 골라 본다
    rows = client.get("/api/user/requests", headers=ADMIN,
                      params={"requester": "alice"}).json()
    assert [r["request_id"] for r in rows] == [a_rid]
