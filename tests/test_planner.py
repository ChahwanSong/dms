import pytest
from dms.domain import ROLE_ADMIN, ROLE_USER, RequestState
from dms.planner import Planner
from dms.repositories import Repositories

NOW = "2026-08-02T10:00:00Z"
# 요청자 기본값 = 특권 세션 관리자(dms-k8s 는 k8s 신원 모드 전용 -- 2026-10-26 LDAP 코드 삭제). 맵핑 면제
# (k8s_access.unrestricted_request: admin 계정 + 특권 목록 + 세션)라 직접 경로·scan 이 열리고, 요청은 제출이 늘 싣는
# run_as_root: true 를 가진다. 이 파일은 어드미션·배치·fan-out 이 관심사다 -- 맵핑·PV 재확인은 test_k8s_submit 이 고정한다.
OPS = "ops"


class _Settings:
    agent_report_stale_seconds = 300
    allow_privileged_requesters = True
    privileged_requesters = frozenset({OPS})
    planner_identity_grace_seconds = 300


def _seed_storage(repos, name="s1", status="Ready"):
    repos.storages.create(storage_name=name, mount_path=f"/mnt/{name}",
                          managed_root=f"/mnt/{name}/dms", backend_type="cephfs",
                          actor="admin")
    repos.storages.set_status(name, status, "ready_nodes=1")


def _seed_sync_storages(repos):
    _seed_storage(repos, "src"); _seed_storage(repos, "dst")


def _seed_policy(repos, tool="scan"):
    repos.control.upsert_policy(tool, max_nodes=3, procs_per_node=8, queue="dms-data",
                                default_priority="mid", max_priority="high",
                                preview_timeout_seconds=3600,
                                execution_timeout_seconds=3600, enabled=True,
                                actor="admin")


def _seed_report(repos, node="n1", storage="s1", tools=("dscan", "dsync", "nsync", "drm")):
    repos.agents.ingest(node, {
        "node_name": node,
        "mounts": [{"storage_name": storage, "mount_path": f"/mnt/{storage}",
                    "status": "Ready", "writable": True}],
        "tools": [{"name": t, "status": "Ready"} for t in tools]},
        reported_at="2026-08-02T09:59:00Z")


def _seed_unmounted_report(repos, node, tool="dscan"):
    repos.agents.ingest(node, {"node_name": node, "mounts": [],
        "tools": [{"name": tool, "status": "Ready"}]},
        reported_at="2026-08-02T09:59:00Z")


def _request(repos, operation, payload, *, requester=OPS, key=None, auth_method="session",
             run_as_root=True, batch_id=None):
    body = {"options": {}, "owner_username": None, **payload}
    if run_as_root:
        body["run_as_root"] = True
    return repos.requests.create(
        operation=operation, requester_id=requester, actor=requester,
        resource_key=key or f"data.{operation}:{sorted(payload.items())}", payload=body,
        priority="mid", auth_method=auth_method, batch_id=batch_id)


def _scan_request(repos, requester=OPS, key="data.scan:s1:a:ff", **extra):
    return _request(repos, "scan", {"storage": "s1", "target": "a", **extra}, requester=requester, key=key)


def _sync_request(repos, requester=OPS, key="data.sync:src:a:dst:b:ff", **extra):
    return _request(repos, "sync", {"source_storage": "src", "source": "a", "destination_storage": "dst",
                                    "destination": "b", **extra}, requester=requester, key=key)


def _planner(repos, settings=None):
    # 면제 판정(unrestricted_request)은 **계정 행**을 본다 -- 특권 관리자 계정이 없으면 직접 경로가 raw_path_not_allowed 다.
    if repos.accounts.get(OPS) is None:
        repos.accounts.create(OPS, "pw", ROLE_ADMIN)
    return Planner(repos, settings=settings or _Settings())


def _snapshot(repos, rid):
    return repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]["identity"]


def test_happy_path_plans_scan(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    assert repos.requests.get(rid)["state"] == "Planned"
    jobs = repos.data_jobs.list_jobs(request_id=rid)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["tool"] == "dscan" and job["state"] == "Pending"
    assert job["worker_pool"]["candidates"]["primary"] == ["n1"]
    # 모든 잡은 root(설계 D2) -- 실행 신원 이름은 요청자(owner_username 이 없으면).
    ident = job["worker_pool"]["identity"]
    assert {k: ident[k] for k in ("username", "uid", "gid", "groups", "privileged")} == {
        "username": OPS, "uid": 0, "gid": 0, "groups": [], "privileged": True}
    assert job["worker_pool"]["process_count"] == 8


def test_storage_missing_disabled_not_ready(db):
    repos = Repositories(db)
    _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:storage_missing"
    assert repos.requests.get(rid)["state"] == "Rejected"

    # storage가 존재하지만 status가 Unknown이면 not_ready
    _seed_storage(repos, status="Unknown")
    rid2 = _scan_request(repos, key="data.scan:s1:b:ff")
    assert _planner(repos).run_once(now_iso=NOW)[rid2] == "rejected:storage_not_ready"


def test_conflict_on_prior_active(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    first = _scan_request(repos, key="dup")
    second = _scan_request(repos, key="dup")
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[first] == "planned"
    assert result[second] == "conflict"
    assert repos.requests.get(second)["state"] == "Conflict"


def test_missing_policy_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_report(repos)
    db.execute("DELETE FROM policies WHERE tool = 'scan'")  # 시드된 기본 정책 제거
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:missing_policy"


def test_no_candidates_when_no_fresh_report(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)  # 리포트 없음
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_eligible_nodes"


def test_unmounted_nodes_reject_immediately(db):
    # 노드별 탈락(미마운트)은 기다려도 풀리지 않는다 -- 신원 전파 유예(LDAP 시절, 2026-10-26 삭제) 없이 즉시 거부.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_unmounted_report(repos, "n1")
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:no_eligible_nodes"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_sync_selects_nsync(db):
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")
    _seed_report(repos, "n1", "src", tools=("nsync", "dsync"))
    _seed_report(repos, "n2", "dst", tools=("nsync", "dsync"))
    rid = _sync_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    job = repos.data_jobs.list_jobs(request_id=rid)[0]
    assert job["tool"] == "nsync"
    assert job["worker_pool"]["candidates"]["source"] == ["n1"]
    assert job["worker_pool"]["candidates"]["destination"] == ["n2"]


def test_replan_is_idempotent(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    _planner(repos).run_once(now_iso=NOW)
    # 크래시 흉내: 상태만 Pending으로 되돌림(잡은 남음)
    repos.requests.set_state(rid, RequestState.PENDING, actor="test")
    _planner(repos).run_once(now_iso=NOW)
    assert len(repos.data_jobs.list_jobs(request_id=rid)) == 1
    assert repos.requests.get(rid)["state"] == "Planned"


def test_policy_max_nodes_trims_scan_candidates(db):
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)  # max_nodes=3, procs_per_node=8
    for node in ("n1", "n2", "n3", "n4", "n5"):
        _seed_report(repos, node=node)
    rid = _scan_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["candidates"]["primary"] == ["n1", "n2", "n3"]
    assert len(wp["candidates"]["primary"]) == wp["node_count"] == 3
    assert wp["process_count"] == 24


def test_policy_max_nodes_trims_sync_candidates(db):
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "nsync")  # max_nodes=3, procs_per_node=8
    for node in ("s1", "s2", "s3", "s4"):
        _seed_report(repos, node, "src", tools=("nsync",))
    for node in ("d1", "d2"):
        _seed_report(repos, node, "dst", tools=("nsync",))
    rid = _sync_request(repos)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["candidates"]["source"] == ["s1", "s2", "s3"]
    assert wp["candidates"]["destination"] == ["d1", "d2"]
    assert len(wp["candidates"]["source"]) == wp["source_count"] == 3
    assert len(wp["candidates"]["destination"]) == wp["destination_count"] == 2


# --- 슬라이스 32: payload.node_count — min(정책, 요청) 캡 + fail-closed 방어 ---

def test_payload_node_count_caps_candidates(db):
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)  # max_nodes=3
    for node in ("n1", "n2", "n3", "n4", "n5"):
        _seed_report(repos, node=node)
    rid = _scan_request(repos, node_count=2)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 2                      # min(정책 3, 요청 2)
    assert wp["candidates"]["primary"] == ["n1", "n2"]  # 슬라이스도 축소
    assert wp["process_count"] == 16


def test_requested_node_count_beyond_eligible_plans_with_available(db):
    # 요청 2대인데 적격 1대(나머지는 미마운트) -- 있는 만큼으로 즉시 계획한다(LDAP 신원 전파 대기 시절의 「요청 수 대기」는
    # 2026-10-26 삭제 -- 기다려서 늘어날 노드가 없다).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_report(repos, node="n1")
    _seed_unmounted_report(repos, "n2")
    rid = _scan_request(repos, node_count=2)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 1
    assert wp["candidates"]["primary"] == ["n1"]


def test_payload_node_count_tampered_rejects(db):
    # DB 는 신뢰 경계 — payload 는 무검증 INSERT 로 변조될 수 있다. 값이 있는데
    # 비정상(str)이면 fail-closed 거부(stepper 층1 unknown_tool 관례).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos, node_count="8")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:invalid_node_count"
    assert repos.requests.get(rid)["state"] == "Rejected"
    # _reject 는 set_state_with_result 경로 — results 행이 함께 남는다.
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": rid})
    assert (row["terminal_state"], row["reason_code"]) == ("Rejected",
                                                           "invalid_node_count")


# --- payload.procs_per_node — node_count 와 같은 min-캡 + fail-closed 방어 미러 ---

def test_payload_procs_per_node_caps_process_count(db):
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)  # procs_per_node=8
    for node in ("n1", "n2", "n3", "n4", "n5"):
        _seed_report(repos, node=node)
    rid = _scan_request(repos, procs_per_node=2)
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "planned"
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["node_count"] == 3                      # 노드 수는 불변(정책 max_nodes)
    assert wp["process_count"] == 6                   # 3노드 × min(정책 8, 요청 2)


def test_payload_procs_per_node_tampered_rejects(db):
    # DB 는 신뢰 경계 — payload 는 무검증 INSERT 로 변조될 수 있다. 값이 있는데
    # 비정상(str)이면 fail-closed 거부(stepper 층1 unknown_tool 관례).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos, procs_per_node="8")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:invalid_procs_per_node"
    assert repos.requests.get(rid)["state"] == "Rejected"
    # _reject 는 set_state_with_result 경로 — results 행이 함께 남는다.
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": rid})
    assert (row["terminal_state"], row["reason_code"]) == ("Rejected",
                                                           "invalid_procs_per_node")


def test_requester_disabled_account_rejects(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    repos.accounts.create("alice", "pw", ROLE_USER, actor="admin")
    repos.accounts.set_disabled("alice", True, actor="admin")
    rid = _scan_request(repos, requester="alice")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:requester_disabled"
    assert repos.requests.get(rid)["state"] == "Rejected"


def test_requester_enabled_account_plans_normally(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    planner = _planner(repos)                 # 활성 admin 계정(OPS)
    assert repos.accounts.get(OPS)["disabled"] in (0, False)
    assert planner.run_once(now_iso=NOW)[rid] == "planned"


def test_requester_without_account_row_is_not_exempt(db):
    # 포탈 계정이 없는 요청자(DB 직접 삽입·지워진 계정)는 「무판정」으로 통과하지만(requester_disabled 아님) 맵핑 면제는
    # 아니다(fail-closed -- k8s_access.unrestricted_request 는 계정 행을 본다): 직접 경로 sync 는 raw_path_not_allowed.
    repos = Repositories(db)
    _seed_sync_storages(repos); _seed_policy(repos, "nsync")
    _seed_report(repos, "n1", "src", tools=("nsync",)); _seed_report(repos, "n2", "dst", tools=("nsync",))
    assert repos.accounts.get("ghost") is None
    rid = _sync_request(repos, requester="ghost")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:raw_path_not_allowed"


def test_unexpected_exception_records_plan_error_event(db, monkeypatch):
    # 배선 회귀 가드: run_once의 항목별 except Exception이 stderr만 찍고 끝나면
    # 이 실패는 전이도, 이벤트도 안 남는다 -- events 테이블을 직접 검사한다.
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    planner = _planner(repos)

    def _boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(repos.accounts, "get", _boom)

    result = planner.run_once(now_iso=NOW)
    assert rid not in result  # 예외 분기는 results에 아무 것도 안 남긴다
    events = repos.observability.events_for_request(rid)
    assert len(events) == 1
    assert events[0]["component"] == "planner"
    assert events[0]["event_type"] == "plan_error"
    assert events[0]["severity"] == "error"
    assert "RuntimeError" in events[0]["message"] and "boom" in events[0]["message"]


def test_worker_pool_records_rejections(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos)
    _seed_report(repos, node="n1")  # 적격
    _seed_report(repos, node="n2", tools=("dsync",))  # dscan 없음 → 탈락 사유 기록돼야
    rid = _scan_request(repos)
    _planner(repos).run_once(now_iso=NOW)
    wp = repos.data_jobs.list_jobs(request_id=rid)[0]["worker_pool"]
    assert wp["rejections"] == {"n2": "missing_tool:dscan"}


def test_token_request_is_never_planned(db):
    # 설계 §2.2-2 심층 방어: 토큰 인증 요청(DB 직접 삽입 -- API 는 k8s 모드에서 토큰 제출을 403 으로 끊는다)은 맵핑 면제도
    # root 도 아니다. 면제가 먼저 끊고(scan 은 특권 운영자만), 신원 게이트(root_allowed = 세션)가 백스톱이다(test_identity).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _request(repos, "scan", {"storage": "s1", "target": "a"}, auth_method="token", key="k-token")
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:scan_unrestricted_only"
    assert repos.data_jobs.list_jobs(request_id=rid) == []


def test_privileged_batch_child_plans_for_a_creator_without_a_listed_owner(db):
    """배치 특권 실행의 종단 통합: 로컬 admin 이 만든 세션 배치의 자식이 상속된 auth_method="session" + allowlist 로
    root(IF_ELIGIBLE)로 계획되고, 자식의 실행 신원 지정(owner alice)이 스냅숏 이름이 된다."""
    from dms.batch_orchestrator import BatchOrchestrator

    class _AdminPrivSettings(_Settings):
        privileged_requesters = frozenset({"admin"})

    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    # 배치는 관리자 전용 라우트에서만 생긴다 -- 자식의 실행 신원 지정(owner alice)은 planner 가 API 와 같은 술어(계정 역할 +
    # 목록)로 다시 본다(2026-10-07).
    repos.accounts.create("admin", "pw", "admin")
    bid = repos.batches.create(
        operation="scan", requester_id="admin", actor="admin", max_concurrency=1,
        options={}, note=None, items=[{"storage": "s1", "target": "a"}],
        status="Running", owner_username="alice", auth_method="session")
    BatchOrchestrator(repos, settings=_AdminPrivSettings()).run_once()
    rid = repos.batches.list_items(bid)[0]["request_id"]
    result = _planner(repos, settings=_AdminPrivSettings()).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    ident = _snapshot(repos, rid)
    assert (ident["username"], ident["uid"], ident["privileged"]) == ("alice", 0, True)


def test_reject_crash_between_writes_leaves_request_pending(db):
    """비원자 쌍(BACKLOG §2.4, 슬라이스 27 발견): set_state(REJECTED) 커밋 후
    record_result 전에 죽으면 요청은 Rejected 인데 results 가 없다 -- Rejected 는
    종단이라 고아 스윕 시야 밖, 결손이 영구다(finalize 의 슬라이스 24 실증과
    동일 계열). 원자화 후엔 둘 다 롤백 -> Pending 잔존, run_once 의 요청별 예외
    격리가 plan_error 로 남기고 다음 틱 재계획이 완주한다."""
    repos = Repositories(db)
    rid = _scan_request(repos)      # 스토리지 미시드 -> storage_missing 거부 경로

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom     # 인스턴스 속성이 메서드를 가린다
    _planner(repos).run_once(now_iso=NOW)    # 요청별 try/except 가 삼킨다(무전파)
    del repos.requests.record_result         # 원복 -- 클래스 메서드가 되살아난다
    # 전부-또는-전무: Pending 그대로, Rejected 전이도 results 도 없다.
    assert repos.requests.get(rid)["state"] == "Pending"
    assert all(t["to_state"] != "Rejected" for t in repos.requests.transitions(rid))
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": rid}) == []
    # 다음 틱: 정상 완주 -- 거부 상태와 results 행이 함께 남는다.
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "rejected:storage_missing"
    assert repos.requests.get(rid)["state"] == "Rejected"
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": rid})
    assert (row["terminal_state"], row["reason_code"]) == ("Rejected",
                                                           "storage_missing")


def test_conflict_crash_between_writes_leaves_request_pending(db):
    # 비원자 쌍의 두 번째(conflict 경로) -- _reject 와 같은 계약. 첫 요청은 정상
    # 계획(planned 경로는 record_result 를 안 부른다), 둘째가 conflict 에서 크래시.
    repos = Repositories(db)
    _seed_storage(repos)
    _seed_policy(repos)
    _seed_report(repos)
    first = _scan_request(repos)
    second = _scan_request(repos)   # 같은 resource_key -- 뒤가 conflict

    def _boom(*_args, **_kwargs):
        raise RuntimeError("crash before record_result")

    repos.requests.record_result = _boom
    _planner(repos).run_once(now_iso=NOW)
    del repos.requests.record_result
    assert repos.requests.get(first)["state"] == "Planned"     # 격리: 옆은 무사
    assert repos.requests.get(second)["state"] == "Pending"
    assert db.query("SELECT request_id FROM results WHERE request_id = :r",
                    {"r": second}) == []
    assert _planner(repos).run_once(now_iso=NOW)[second] == "conflict"
    assert repos.requests.get(second)["state"] == "Conflict"
    row = db.query_one("SELECT terminal_state, reason_code FROM results"
                       " WHERE request_id = :r", {"r": second})
    assert (row["terminal_state"], row["reason_code"]) == ("Conflict",
                                                           "resource_conflict")


# owner_username 자격 재확인(API 와 같은 술어) -- DB 직접 쓰기 방어

def test_owner_override_by_a_non_admin_creator_is_rejected(db):
    # DB 직접 쓰기: 비관리자 bob 의 배치 자식(배치는 맵핑 면제)의 payload 만 owner=alice -- 다른 실행 신원 지정은 관리자 +
    # 특권 목록만이다(identity.owner_override_allowed, API 와 같은 술어).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    repos.accounts.create("bob", "pw", ROLE_USER)
    rid = _request(repos, "scan", {"storage": "s1", "target": "a", "owner_username": "alice"},
                   requester="bob", key="k-owner", batch_id="b" * 32, run_as_root=False)
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "rejected:privileged_not_authorized"
    assert repos.requests.get(rid)["state"] == "Rejected"
    assert repos.data_jobs.list_jobs(request_id=rid) == []


def test_owner_override_by_eligible_admin_plans(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos, owner_username="alice")
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    assert _snapshot(repos, rid)["username"] == "alice"
    assert _snapshot(repos, rid)["uid"] == 0


@pytest.mark.parametrize("owner", ["", " alice", 123, ["x"], "bad name", "Alice", "1234"])
def test_malformed_owner_rejected_invalid_owner_username(db, owner):
    # 예전: "" 는 resolve 가 '본인'으로 읽었고, 비문자열은 .strip() AttributeError 로 매 틱 plan_error·영구 Pending.
    # 모양은 계정 아이디 규칙(domain._ACCOUNT_ID_RE -- 대문자·숫자만 거부).
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos, owner_username=owner)
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "rejected:invalid_owner_username"


def test_explicit_chown_by_an_operator_plans(db):
    # 명시 chown 은 특권 운영자만(k8s_access.recheck_request -- chown_not_allowed) -- 운영자의 root sync 는 그대로 계획된다.
    repos = Repositories(db)
    _seed_sync_storages(repos)
    _seed_policy(repos, "dsync")
    repos.agents.ingest("n1", {"node_name": "n1",
        "mounts": [{"storage_name": s, "mount_path": f"/mnt/{s}", "status": "Ready", "writable": True}
                   for s in ("src", "dst")],
        "tools": [{"name": "dsync", "status": "Ready"}]}, reported_at="2026-08-02T09:59:00Z")
    rid = _sync_request(repos, options={"chown": "0:30000"})
    result = _planner(repos).run_once(now_iso=NOW)
    assert result[rid] == "planned"
    assert _snapshot(repos, rid)["privileged"] is True


# ---- 2026-10-08 요청 삭제 선행 보강(작업 삭제 스펙 §6.2): emit = 한 트랜잭션 + 조건부 Pending→Planned ----
# planner 는 Pending 요청을 읽은 뒤 어드미션·노드 선택을 거쳐 emit 한다. 그 창에서 취소된 요청을 Planned 로 되살리거나(예전
# set_state 에 종단 가드 없음), 지워진 요청에 실행될 Pending 잡을 남기면 안 된다.

_EMIT = dict(operation="scan", priority="mid", storage_name="s1", target="a", options={},
             tool="dscan", worker_pool={}, precondition={}, actor="planner")


def _hook_mid_plan(repos, hook):
    """계획 도중(요청을 읽은 뒤, emit 전 -- 노드 보고 조회) hook 을 한 번 부른다."""
    real = repos.agents.fresh_reports
    pending = [hook]

    def wrapped(*a, **k):
        if pending:
            pending.pop()()
        return real(*a, **k)
    repos.agents.fresh_reports = wrapped


def _rows(db, table, rid):
    return db.query(f"SELECT * FROM {table} WHERE request_id = :r", {"r": rid})


def _cancel(repos, rid):
    from dms.domain import DataJobState
    repos.requests.finalize_from_job(rid, DataJobState.CANCELLED, reason_code="cancelled_by_user",
                                     actor=OPS)


def test_cancel_during_planning_neither_resurrects_nor_emits(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    planner = _planner(repos)
    _hook_mid_plan(repos, lambda: _cancel(repos, rid))
    result = planner.run_once(now_iso=NOW)
    assert result[rid] == "gone"
    assert repos.requests.get(rid)["state"] == "Cancelled"                 # 부활 없음
    assert [t["to_state"] for t in repos.requests.transitions(rid)] == ["Pending", "Cancelled"]
    assert _rows(db, "plans", rid) == [] and _rows(db, "data_jobs", rid) == []
    assert repos.observability.events_for_request(rid) == []               # plan_error 없음
    assert _rows(db, "results", rid)[0]["terminal_state"] == "Cancelled"


def test_delete_during_planning_leaves_no_runnable_job(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    planner = _planner(repos)

    def cancel_then_delete():
        _cancel(repos, rid)
        with db.transaction():
            db.execute("DELETE FROM state_transitions WHERE entity_kind = 'request' AND entity_id = :r",
                       {"r": rid})
            for table in ("results", "requests"):
                db.execute(f"DELETE FROM {table} WHERE request_id = :r", {"r": rid})

    _hook_mid_plan(repos, cancel_then_delete)
    result = planner.run_once(now_iso=NOW)
    assert result[rid] == "gone"
    for table in ("requests", "plans", "data_jobs", "results", "events"):
        assert _rows(db, table, rid) == [], table
    assert db.query("SELECT 1 AS x FROM state_transitions") == []
    assert repos.data_jobs.claim_steppable() == []                         # stepper 가 실행할 고아 잡이 없다


def test_request_deleted_after_listing_is_gone_without_plan_error(db):
    # list_pending 이 집은 뒤 행이 사라졌다 -- 예전엔 req["payload"] TypeError 로 지워진 id 의 plan_error 를 남겼다.
    repos = Repositories(db)
    rid = _scan_request(repos)
    stale = repos.requests.list_pending()
    db.execute("DELETE FROM requests WHERE request_id = :r", {"r": rid})
    repos.requests.list_pending = lambda limit=50: stale
    assert _planner(repos).run_once(now_iso=NOW)[rid] == "gone"
    assert db.query("SELECT 1 AS x FROM events") == []


def test_idempotent_branch_does_not_resurrect_a_cancelled_request(db):
    # 옛 비원자 emit 의 크래시 잔재(잡은 있고 요청은 Pending)를 낡은 목록이 집었는데 그 사이 취소됐다.
    repos = Repositories(db)
    rid = _scan_request(repos)
    plan_id = repos.data_jobs.create_plan(rid, actor="planner")
    repos.data_jobs.create_job(rid, plan_id, **_EMIT)
    _cancel(repos, rid)
    assert _planner(repos)._plan_one(rid, NOW) == "gone"
    assert repos.requests.get(rid)["state"] == "Cancelled"
    assert [t["to_state"] for t in repos.requests.transitions(rid)] == ["Pending", "Cancelled"]


def test_emit_keeps_transition_order_and_actor(db):
    repos = Repositories(db)
    _seed_storage(repos); _seed_policy(repos); _seed_report(repos)
    rid = _scan_request(repos)
    planner = _planner(repos)
    assert planner.run_once(now_iso=NOW)[rid] == "planned"
    rows = db.query("SELECT entity_kind, from_state, to_state, actor FROM state_transitions ORDER BY id")
    assert [(r["entity_kind"], r["from_state"], r["to_state"], r["actor"]) for r in rows] == [
        ("request", None, "Pending", OPS),
        ("plan", None, "Planned", "planner"),
        ("data_job", None, "Pending", "planner"),
        ("request", "Pending", "Planned", "planner")]
    [job] = repos.data_jobs.list_jobs(request_id=rid)
    assert [p["job_id"] for p in _rows(db, "plans", rid)] == [job["job_id"]]


def test_create_plan_and_job_requires_a_pending_request(db):
    repos = Repositories(db)
    assert repos.data_jobs.create_plan_and_job("0" * 32, **_EMIT) is None   # 행 없음
    rid = _scan_request(repos)
    _cancel(repos, rid)
    assert repos.data_jobs.create_plan_and_job(rid, **_EMIT) is None        # 종단
    assert db.query("SELECT 1 AS x FROM plans") == [] and db.query("SELECT 1 AS x FROM data_jobs") == []
    assert repos.requests.get(rid)["state"] == "Cancelled"


def test_create_plan_and_job_rolls_back_when_the_cas_loses(db):
    # sqlite 에는 행 잠금이 없다 -- SELECT 와 조건부 UPDATE 사이의 경쟁 커밋을 같은 트랜잭션 안의 UPDATE 로 흉내 낸다.
    # 조건부 UPDATE 영향 0 이면 plan·job INSERT 까지 전부 롤백(흉내 낸 상태 변경도 같은 트랜잭션이라 함께 되돌아간다).
    repos = Repositories(db)
    rid = _scan_request(repos)
    real = repos.data_jobs._insert_job

    def insert_then_lose(*a, **k):
        real(*a, **k)
        db.execute("UPDATE requests SET state = 'Cancelled' WHERE request_id = :r", {"r": rid})
    repos.data_jobs._insert_job = insert_then_lose
    assert repos.data_jobs.create_plan_and_job(rid, **_EMIT) is None
    assert _rows(db, "plans", rid) == [] and _rows(db, "data_jobs", rid) == []
    assert db.query("SELECT 1 AS x FROM state_transitions WHERE entity_kind IN ('plan', 'data_job')") == []


def test_create_plan_and_job_is_all_or_nothing_on_crash(db):
    repos = Repositories(db)
    rid = _scan_request(repos)
    real = repos.data_jobs._record_transition

    def crash_on_request(kind, *a, **k):
        if kind == "request":
            raise RuntimeError("crash before the request transition")
        return real(kind, *a, **k)
    repos.data_jobs._record_transition = crash_on_request
    with pytest.raises(RuntimeError):
        repos.data_jobs.create_plan_and_job(rid, **_EMIT)
    del repos.data_jobs._record_transition
    assert repos.requests.get(rid)["state"] == "Pending"
    assert _rows(db, "plans", rid) == [] and _rows(db, "data_jobs", rid) == []
