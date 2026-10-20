"""sync 배치 자동 확인(2026-10-11 사용자 결정 -- "단일 sync 는 승인 필요, 배치 sync 는 자동 승인을 옵션으로(기본 false)",
자격 = 특권 세션 관리자만).

1) 켜기: 생성(특권 3중 게이트) 또는 종단 배치의 실행 설정 변경에서만 -- 명시 true 만(StrictBool), sync 만. 켠 사람이
   auto_confirm_by 로 남는다. 끄기: :auto-confirm-off(어느 상태에서나, require_admin -- 실행을 줄이기만 한다).
2) 동작: PreviewReady 에서 orchestrator 가 사람의 「배치 확인」과 **같은 저장소 함수**(회차·Queued CAS + 도장 + 감사)를
   부른다. 도장·Running 분기 규칙은 그대로라 확인은 여전히 그 회차의 미리보기 전부를 대표한다.
3) 보류: 확인 직전 켠 사람이 특권 목록 밖·비관리자·비활성이거나 옵션이 지금 규칙에 어긋나면 확인 대기에 남는다(사람이
   누른다) -- 경고 이벤트는 회차마다 한 번, 상세 GET 이 같은 술어로 사유를 싣는다.
4) 단건 sync 는 그대로 사람이 컨펌한다(이 경로 밖).
"""
import json

from fastapi.testclient import TestClient

from dms.batch_orchestrator import AUTO_CONFIRM_ACTOR, BatchOrchestrator, auto_confirm_hold
from dms.config import Settings
from dms.domain import DataJobState, RequestState
from dms.repositories import Repositories

TOKEN = {"Authorization": "Bearer tok-shared"}
SYNC_ITEM = {"source_storage": "s1", "source": "a", "destination_storage": "s2", "destination": "b"}
SCAN_ITEM = {"storage": "s1", "target": "a"}


class _S:
    preview_ttl_seconds = 900
    allow_privileged_requesters = True
    privileged_requesters = frozenset({"ops"})


def _app(db, **overrides):
    from dms.api.app import create_app
    base = {"DMS_DATABASE_URL": "unused", "DMS_SHARED_TOKEN": "tok-shared",
            "DMS_ADMIN_TOKEN": "tok-admin", "DMS_SESSION_SECRET": "sess",
            "DMS_ALLOW_PRIVILEGED_REQUESTERS": "true", "DMS_PRIVILEGED_REQUESTERS": "ops", **overrides}
    return create_app(Settings.from_env(base), db)


def _login(client, username, role="admin"):
    # from_env 앱은 라이브 자세(비밀번호 봉인 필수) -- 로그인은 봉인해 보낸다.
    from dms.api.password_transport import seal_with_info
    if client.app.state.repos.accounts.get(username) is None:
        client.app.state.repos.accounts.create(username, "p", role, actor="t")
    info = client.get("/api/auth/transport-key").json()
    r = client.post("/api/auth/login", json={
        "username": username,
        "password_enc": seal_with_info(info, "p", purpose="login", username=username)})
    assert r.status_code == 200, r.text


def _ops(repos, role="admin"):
    if repos.accounts.get("ops") is None:
        repos.accounts.create("ops", "p", role, actor="t")


def _batch(repos, *, status="PreviewReady", auto_by="ops", n=1, op="sync", options=None):
    item = SYNC_ITEM if op == "sync" else SCAN_ITEM
    items = [{**item, ("destination" if op == "sync" else "target"): f"d{i}"} for i in range(n)]
    return repos.batches.create(operation=op, requester_id="ops", actor="ops", max_concurrency=2,
                                options=options or {}, items=items, note=None, status=status,
                                auth_method="session", auto_confirm_by=auto_by)


def _confirmpending_child(repos, bid, seq=0, fp="sha256:fp", expires="2099-01-01T00:00:00Z"):
    """배치 항목 seq 를 materialize 하고 자식 잡을 ConfirmPending 으로 만든다(미리보기 완료 시뮬)."""
    rid = repos.requests.create(operation="sync", requester_id="ops", actor="ops", resource_key=f"k-{bid}-{seq}",
                                payload=dict(SYNC_ITEM), priority="mid", batch_id=bid, auth_method="session")
    repos.batches.set_item_materialized(bid, seq, rid)
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan, operation="sync", priority="mid", source_storage="s1",
                                     destination_storage="s2", source="a", destination="b", options={},
                                     tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint=fp, expires_at=expires, artifact_uri="x",
                                summary={"files": 42, "bytes": None, "returncode": 0})
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    return rid, jid


def _events(db, event_type):
    return db.query("SELECT severity, message FROM events WHERE event_type = :t", {"t": event_type})


def _audit(db, operation):
    return db.query("SELECT target_key, actor, before_state, after_state FROM audit_log "
                    "WHERE mutation_class = 'batch' AND operation = :o", {"o": operation})


# --- 1) 켜기·끄기 API ----------------------------------------------------------------------------------------------

def test_create_records_who_turned_auto_confirm_on_and_default_is_off(db):
    client = TestClient(_app(db))
    _login(client, "ops")
    base = {"operation": "sync", "max_concurrency": 1, "items": [SYNC_ITEM]}
    on = client.post("/api/admin/batches", json={**base, "auto_confirm": True})
    off = client.post("/api/admin/batches", json=base)
    assert on.status_code == 202 and off.status_code == 202, (on.text, off.text)
    b_on = client.get(f"/api/admin/batches/{on.json()['batch_id']}").json()
    b_off = client.get(f"/api/admin/batches/{off.json()['batch_id']}").json()
    assert (b_on["auto_confirm"], b_on["auto_confirm_by"]) == (True, "ops")
    assert (b_off["auto_confirm"], b_off["auto_confirm_by"]) == (False, None)     # 생략 = 꺼짐(기본)
    assert b_off["auto_confirm_hold"] is None


def test_create_accepts_only_an_explicit_boolean_and_only_for_sync(db):
    client = TestClient(_app(db))
    _login(client, "ops")
    base = {"operation": "sync", "max_concurrency": 1, "items": [SYNC_ITEM]}
    for bad in ("yes", 1, "true", None):
        r = client.post("/api/admin/batches", json={**base, "auto_confirm": bad})
        assert r.status_code == 422, (bad, r.text)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1, "items": [SCAN_ITEM],
                                                 "auto_confirm": True})
    assert r.status_code == 422 and r.json()["detail"] == "auto_confirm_sync_only"
    # scan 에 false 는 무해(종전 본문 그대로 받는다)
    r = client.post("/api/admin/batches", json={"operation": "scan", "max_concurrency": 1, "items": [SCAN_ITEM],
                                                 "auto_confirm": False})
    assert r.status_code == 202, r.text
    assert db.query("SELECT COUNT(*) AS n FROM batches WHERE auto_confirm_by IS NOT NULL")[0]["n"] == 0


def test_token_and_admin_outside_allowlist_cannot_create_an_auto_confirm_batch(db):
    client = TestClient(_app(db))
    body = {"operation": "sync", "max_concurrency": 1, "items": [SYNC_ITEM], "auto_confirm": True}
    r = client.post("/api/admin/batches", json=body, headers=TOKEN)
    assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized"
    _login(client, "other")                                            # 관리자지만 특권 목록 밖
    r = client.post("/api/admin/batches", json=body)
    assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized"
    assert db.query("SELECT COUNT(*) AS n FROM batches")[0]["n"] == 0


def test_execution_settings_turn_auto_confirm_on_and_off_on_a_terminal_batch(db):
    client = TestClient(_app(db))
    _login(client, "ops")
    repos = client.app.state.repos
    bid = _batch(repos, status="Completed", auto_by=None)
    r = client.patch(f"/api/admin/batches/{bid}/execution", json={"auto_confirm": True})
    assert r.status_code == 200 and r.json()["auto_confirm"] is True and r.json()["auto_confirm_by"] == "ops"
    rows = _audit(db, "execution_settings")
    assert len(rows) == 1 and json.loads(rows[0]["after_state"]) == {"auto_confirm_by": "ops"}
    assert json.loads(rows[0]["before_state"]) == {"auto_confirm_by": None}
    # 이미 켜져 있으면 변경 없음(감사 행도 없다)
    assert client.patch(f"/api/admin/batches/{bid}/execution", json={"auto_confirm": True}).status_code == 200
    assert len(_audit(db, "execution_settings")) == 1
    r = client.patch(f"/api/admin/batches/{bid}/execution", json={"auto_confirm": False})
    assert r.status_code == 200 and r.json()["auto_confirm"] is False and r.json()["auto_confirm_by"] is None
    assert len(_audit(db, "execution_settings")) == 2
    # null 은 뜻이 없다(options 와 같다), 비 bool 은 모양 오류
    assert client.patch(f"/api/admin/batches/{bid}/execution", json={"auto_confirm": None}).json()["detail"] == "invalid_option"
    assert client.patch(f"/api/admin/batches/{bid}/execution", json={"auto_confirm": "yes"}).status_code == 422


def test_execution_settings_auto_confirm_keeps_the_existing_gates(db):
    client = TestClient(_app(db))
    repos = client.app.state.repos
    _ops(repos)
    done = _batch(repos, status="Completed", auto_by=None)
    scan = _batch(repos, status="Completed", auto_by=None, op="scan")
    live = _batch(repos, status="Previewing", auto_by=None)
    r = client.patch(f"/api/admin/batches/{done}/execution", json={"auto_confirm": True}, headers=TOKEN)
    assert r.status_code == 403 and r.json()["detail"] == "privileged_not_authorized"
    _login(client, "ops")
    assert client.patch(f"/api/admin/batches/{scan}/execution",
                        json={"auto_confirm": True}).json()["detail"] == "auto_confirm_sync_only"
    # 진행 중 배치는 켤 수 없다(종단 한정 -- 미리보기를 이미 시작한 회차에 사후 동의를 얹지 않는다)
    assert client.patch(f"/api/admin/batches/{live}/execution",
                        json={"auto_confirm": True}).json()["detail"] == "batch_settings_locked"
    assert all(repos.batches.get(b)["auto_confirm_by"] is None for b in (done, scan, live))


def test_auto_confirm_off_works_in_any_state_is_idempotent_and_audited(db):
    client = TestClient(_app(db))
    repos = client.app.state.repos
    _ops(repos)
    for status in ("Previewing", "PreviewReady", "Running", "Completed"):
        bid = _batch(repos, status=status)
        # 실행을 줄이기만 하는 라우트 -- 공유 토큰도 끌 수 있다(require_admin, 취소와 같은 규칙)
        r = client.post(f"/api/admin/batches/{bid}:auto-confirm-off", headers=TOKEN)
        assert r.status_code == 200 and r.json()["auto_confirm"] is False, (status, r.text)
        assert repos.batches.get(bid)["status"] == status                  # 상태는 그대로
    rows = _audit(db, "auto_confirm_off")
    assert len(rows) == 4 and all(json.loads(r["before_state"]) == {"auto_confirm_by": "ops"} for r in rows)
    assert {r["actor"] for r in rows} == {"shared-token"}
    r = client.post(f"/api/admin/batches/{bid}:auto-confirm-off", headers=TOKEN)   # 이미 꺼짐 -- 쓰지 않는다
    assert r.status_code == 200 and len(_audit(db, "auto_confirm_off")) == 4
    assert client.post("/api/admin/batches/nope:auto-confirm-off", headers=TOKEN).status_code == 404


# --- 2) orchestrator 자동 확인 ---------------------------------------------------------------------------------------

def test_previewready_auto_batch_is_confirmed_by_the_same_gate_and_stamped(db):
    repos = Repositories(db)
    _ops(repos)
    bid = _batch(repos, n=2)
    _rid, jid = _confirmpending_child(repos, bid, 0)
    repos.batches.set_item_status(bid, 1, "Rejected")
    orch = BatchOrchestrator(repos, settings=_S())
    orch.run_once()
    assert repos.batches.get(bid)["status"] == "Running"
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] == "sha256:fp"          # 사람 확인과 같은 도장
    rows = _audit(db, "confirm")
    assert len(rows) == 1 and rows[0]["actor"] == AUTO_CONFIRM_ACTOR and rows[0]["target_key"] == bid
    after = json.loads(rows[0]["after_state"])
    assert after["auto_confirm_by"] == "ops" and after["previewed"] == 1 and after["items"] == 2
    assert after["stamped"] == 1 and after["preview_round"] == 0
    assert [e["severity"] for e in _events(db, "batch_auto_confirmed")] == ["info"]
    orch.run_once()                                                     # 다음 틱: 도장 찍힌 자식을 실행
    assert repos.data_jobs.get_job(jid)["state"] == DataJobState.EXECUTING.value


def test_full_cycle_previewing_to_execution_without_a_human(db):
    repos = Repositories(db)
    _ops(repos)
    bid = _batch(repos, status="Previewing")
    _rid, jid = _confirmpending_child(repos, bid, 0)
    orch = BatchOrchestrator(repos, settings=_S())
    orch.run_once()
    b = repos.batches.get(bid)
    assert (b["status"], b["preview_round"]) == ("PreviewReady", 1)     # 확인 대기 전이는 그대로(회차 +1)
    orch.run_once()
    assert repos.batches.get(bid)["status"] == "Running"
    assert json.loads(_audit(db, "confirm")[0]["after_state"])["preview_round"] == 1
    orch.run_once()
    assert repos.data_jobs.get_job(jid)["state"] == DataJobState.EXECUTING.value


def test_batch_without_auto_confirm_still_waits_for_a_human(db):
    repos = Repositories(db)
    _ops(repos)
    bid = _batch(repos, auto_by=None)
    _rid, jid = _confirmpending_child(repos, bid, 0)
    orch = BatchOrchestrator(repos, settings=_S())
    orch.run_once(); orch.run_once()
    assert repos.batches.get(bid)["status"] == "PreviewReady"
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] is None
    assert repos.data_jobs.get_job(jid)["state"] == DataJobState.CONFIRM_PENDING.value
    assert _audit(db, "confirm") == []


def test_auto_confirm_does_not_confirm_over_a_queued_item(db):
    # 확인 CAS 의 Queued 둑은 자동 확인에도 그대로다 -- 확인 대기인데 Queued 가 보이면(라우트 복귀를 놓친 경합)
    # 확인하지 않고 Previewing 으로 돌린다(사람 확인 라우트의 reopen_if_queued 와 같은 자가 치유).
    repos = Repositories(db)
    _ops(repos)
    bid = _batch(repos, n=2)
    _rid, jid = _confirmpending_child(repos, bid, 0)                    # 항목 1 은 Queued 그대로
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.get(bid)["status"] == "Previewing"
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] is None
    assert _audit(db, "confirm") == []


def test_auto_confirm_uses_the_current_round_and_loses_to_a_human_cleanly(db):
    client = TestClient(_app(db))
    _login(client, "ops")
    repos = client.app.state.repos
    bid = _batch(repos)
    _confirmpending_child(repos, bid, 0)
    BatchOrchestrator(repos, settings=_S()).run_once()                  # 자동 확인이 먼저
    r = client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 0})
    assert r.status_code == 409 and r.json()["detail"] == "batch_not_confirmable"
    assert len(_audit(db, "confirm")) == 1                              # 확인은 한 번만 기록된다


def test_human_confirm_first_leaves_nothing_for_auto_confirm(db):
    client = TestClient(_app(db))
    _login(client, "ops")
    repos = client.app.state.repos
    bid = _batch(repos)
    _confirmpending_child(repos, bid, 0)
    assert client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 0}).status_code == 200
    BatchOrchestrator(repos, settings=_S()).run_once()
    rows = _audit(db, "confirm")
    assert len(rows) == 1 and rows[0]["actor"] == "ops"


def test_re_review_after_adding_an_item_is_auto_confirmed_again(db):
    # 켠 사람의 동의는 배치의 설정이다 -- 항목 추가(특권 게이트)로 Previewing 에 돌아간 배치도 새 회차가 확인 대기가
    # 되면 자동 확인된다. 새 미리보기는 새 도장을 받아야 실행된다(Running 분기 규칙 그대로).
    client = TestClient(_app(db))
    _login(client, "ops")
    repos = client.app.state.repos
    bid = _batch(repos)
    _confirmpending_child(repos, bid, 0)
    orch = BatchOrchestrator(repos, settings=_S())
    orch.run_once()
    assert repos.batches.get(bid)["status"] == "Running"
    r = client.post(f"/api/admin/batches/{bid}/items", json=dict(SYNC_ITEM, destination="new"))
    assert r.status_code == 202, r.text
    assert repos.batches.get(bid)["status"] == "Previewing"
    seq = max(i["seq"] for i in repos.batches.list_items(bid))
    _rid2, jid2 = _confirmpending_child(repos, bid, seq, fp="sha256:fp2")
    orch.run_once()                                                     # Previewing → PreviewReady(회차 +1)
    orch.run_once()                                                     # 자동 확인
    assert repos.batches.get(bid)["status"] == "Running"
    assert repos.data_jobs.get_job(jid2)["confirmed_fingerprint"] == "sha256:fp2"
    assert [json.loads(r["after_state"])["preview_round"] for r in _audit(db, "confirm")] == [0, 1]


# --- 3) 보류 ------------------------------------------------------------------------------------------------------

def _held_case(db, *, settings=_S(), role="admin", disabled=False, account=True, options=None):
    repos = Repositories(db)
    if account:
        _ops(repos, role=role)
        if disabled:
            db.execute("UPDATE accounts SET disabled = 1 WHERE username = 'ops'")
    bid = _batch(repos, options=options)
    _rid, jid = _confirmpending_child(repos, bid, 0)
    # 컨트롤러처럼 틱마다 새 인스턴스(controller.py 의 Loop 람다) -- 인스턴스 속성 중복 방지는 매 틱 비었다(리뷰).
    for _ in range(3):
        BatchOrchestrator(repos, settings=settings).run_once()          # 세 틱 -- 경고는 회차마다 한 번
    assert repos.batches.get(bid)["status"] == "PreviewReady"
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] is None
    assert _audit(db, "confirm") == []
    held = _events(db, "batch_auto_confirm_held")
    assert len(held) == 1 and held[0]["severity"] == "warning"
    return auto_confirm_hold(repos, settings, repos.batches.get(bid)), held[0]["message"]


def test_held_when_the_enabler_left_the_privileged_list(db):
    class Off(_S):
        privileged_requesters = frozenset({"someone-else"})
    reason, message = _held_case(db, settings=Off())
    assert reason == "auto_confirm_not_privileged" and "auto_confirm_not_privileged" in message


def test_held_when_privileged_execution_is_turned_off(db):
    # 사이트 전체 특권 실행이 꺼졌으면 켠 사람 탓이 아니다 -- 별도 사유(문구가 다르다).
    class Off(_S):
        allow_privileged_requesters = False
    assert _held_case(db, settings=Off())[0] == "auto_confirm_privileged_disabled"


def test_turning_auto_off_mid_tick_stops_the_confirm(db, monkeypatch):
    # 리뷰 재현(2026-10-11): 틱이 배치를 읽은 뒤(항목을 훑는 동안) 운영자가 「자동 실행 끄기」를 눌러 200 을 받았는데,
    # 루프가 옛 읽기로 확인해 root 로 돌았다. 확인 UPDATE 가 켠 사람을 같은 문장에서 다시 본다 -- 0 행, 확인 대기 그대로.
    repos = Repositories(db)
    _ops(repos)
    bid = _batch(repos)
    _rid, jid = _confirmpending_child(repos, bid, 0)
    real = repos.batches.list_items

    def list_items_then_off(batch_id):
        rows = real(batch_id)
        repos.batches.disable_auto_confirm(batch_id, actor="ops")
        return rows
    monkeypatch.setattr(repos.batches, "list_items", list_items_then_off)
    BatchOrchestrator(repos, settings=_S()).run_once()
    b = repos.batches.get(bid)
    assert (b["status"], b["auto_confirm"]) == ("PreviewReady", False)
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] is None
    assert _audit(db, "confirm") == []


def test_demotion_mid_tick_stops_the_confirm(db, monkeypatch):
    # 술어(auto_confirm_hold)를 통과한 뒤 켠 사람이 강등돼도 같은 UPDATE 문장의 계정 조건이 막는다.
    repos = Repositories(db)
    _ops(repos)
    bid = _batch(repos)
    _rid, jid = _confirmpending_child(repos, bid, 0)
    import dms.batch_orchestrator as orch_mod
    real_hold = orch_mod.auto_confirm_hold

    def hold_then_demote(*a, **kw):
        out = real_hold(*a, **kw)
        db.execute("UPDATE accounts SET role = 'user' WHERE username = 'ops'")
        return out
    monkeypatch.setattr(orch_mod, "auto_confirm_hold", hold_then_demote)
    BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.get(bid)["status"] == "PreviewReady"
    assert repos.data_jobs.get_job(jid)["confirmed_fingerprint"] is None


def test_held_when_the_enabler_is_demoted_disabled_or_missing(db, tmp_path):
    assert _held_case(db, role="user")[0] == "auto_confirm_not_admin"


def test_held_when_the_enabler_is_disabled(db):
    assert _held_case(db, disabled=True)[0] == "auto_confirm_not_admin"


def test_held_when_the_enabler_account_is_gone(db):
    assert _held_case(db, account=False)[0] == "auto_confirm_not_admin"


def test_held_when_options_no_longer_validate(db):
    # 규칙이 바뀐 옛 배치(이름 chown -- 2026-10-01) -- 사람의 확인 라우트도 422 로 거부하는 옵션이다.
    assert _held_case(db, options={"chown": "alice:staff"})[0] == "chown_name_not_supported"


def test_held_auto_batch_can_still_be_confirmed_by_a_human(db):
    class Off(_S):
        privileged_requesters = frozenset({"someone-else"})
    client = TestClient(_app(db))
    _login(client, "ops")
    repos = client.app.state.repos
    bid = _batch(repos)
    _confirmpending_child(repos, bid, 0)
    BatchOrchestrator(repos, settings=Off()).run_once()
    assert repos.batches.get(bid)["status"] == "PreviewReady"
    assert client.post(f"/api/admin/batches/{bid}:confirm", json={"preview_round": 0}).status_code == 200


def test_batch_detail_reports_the_hold_with_the_same_predicate(db):
    client = TestClient(_app(db, DMS_PRIVILEGED_REQUESTERS="someone-else"))
    repos = client.app.state.repos
    _ops(repos)
    held = _batch(repos)
    fine_app = TestClient(_app(db))
    assert client.get(f"/api/admin/batches/{held}", headers=TOKEN).json()["auto_confirm_hold"] \
        == "auto_confirm_not_privileged"
    assert fine_app.get(f"/api/admin/batches/{held}", headers=TOKEN).json()["auto_confirm_hold"] is None
    # 끝난 배치·꺼진 배치는 판정할 확인이 없다(null)
    done = _batch(repos, status="Completed")
    off = _batch(repos, auto_by=None)
    assert client.get(f"/api/admin/batches/{done}", headers=TOKEN).json()["auto_confirm_hold"] is None
    assert client.get(f"/api/admin/batches/{off}", headers=TOKEN).json()["auto_confirm_hold"] is None


def test_rm_batches_never_turn_auto_confirm_on(db):
    # rm 은 되돌릴 수 없는 root 삭제라 늘 사람이 「배치 확인」을 누른다(2026-10-21 결정) -- 생성·실행 설정 변경 모두 422.
    client = TestClient(_app(db))
    repos = client.app.state.repos
    _login(client, "ops")
    rm = {"operation": "rm", "max_concurrency": 1, "options": {"recursive": True},
          "items": [{"storage": "s1", "target": "old"}]}
    r = client.post("/api/admin/batches", json={**rm, "auto_confirm": True})
    assert r.status_code == 422 and r.json()["detail"] == "auto_confirm_sync_only"
    bid = client.post("/api/admin/batches", json={**rm, "auto_confirm": False}).json()["batch_id"]
    repos.batches.set_item_status(bid, 0, "Succeeded")
    repos.batches.set_status(bid, "Completed")
    r = client.patch(f"/api/admin/batches/{bid}/execution", json={"auto_confirm": True})
    assert r.status_code == 422 and r.json()["detail"] == "auto_confirm_sync_only"
    assert db.query("SELECT COUNT(*) AS n FROM batches WHERE auto_confirm_by IS NOT NULL")[0]["n"] == 0


def test_a_forged_rm_row_with_auto_confirm_is_not_confirmed_by_the_loop(db):
    # DB 가 신뢰 경계 -- rm 배치 행에 auto_confirm_by 를 직접 써도 루프는 확인하지 않는다(PreviewReady 분기의 sync 검사 +
    # auto_confirm_hold 두 겹). 확인 대기에 그대로 남고, 감사 행·도장·실행 없음.
    repos = Repositories(db)
    _ops(repos)
    bid = repos.batches.create(operation="rm", requester_id="ops", actor="ops", max_concurrency=2,
                               options={"recursive": True}, items=[{"storage": "s1", "target": "old"}], note=None,
                               status="PreviewReady", auth_method="session", auto_confirm_by="ops")
    rid = repos.requests.create(operation="rm", requester_id="ops", actor="ops", resource_key=f"k-{bid}",
                                payload={"storage": "s1", "target": "old", "options": {"recursive": True}},
                                priority="mid", batch_id=bid, auth_method="session")
    repos.batches.set_item_materialized(bid, 0, rid)
    plan = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan, operation="rm", priority="mid", storage_name="s1", target="old",
                                     options={"recursive": True}, tool="drm", worker_pool={}, precondition={},
                                     actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint="sha256:rm", expires_at="2099-01-01T00:00:00Z", artifact_uri="x",
                                summary={"files": 6, "bytes": None, "returncode": 0})
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    assert auto_confirm_hold(repos, _S(), repos.batches.get(bid)) == "auto_confirm_sync_only"
    for _ in range(3):
        BatchOrchestrator(repos, settings=_S()).run_once()
    assert repos.batches.get(bid)["status"] == "PreviewReady"
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == "ConfirmPending" and job["confirmed_fingerprint"] is None
    assert _audit(db, "confirm") == []


def test_a_forged_scan_row_with_auto_confirm_is_not_confirmed(db):
    # DB 가 신뢰 경계 -- scan 배치 행에 auto_confirm_by 를 직접 써도 확인 경로가 없다(확인 단계 자체가 sync 만).
    repos = Repositories(db)
    _ops(repos)
    bid = _batch(repos, op="scan", status="Running")
    assert auto_confirm_hold(repos, _S(), repos.batches.get(bid)) == "auto_confirm_sync_only"


# --- 4) 단건 sync 는 그대로 사람이 컨펌한다 ---------------------------------------------------------------------------

def test_single_sync_job_is_never_auto_confirmed_by_the_orchestrator(db):
    repos = Repositories(db)
    _ops(repos)
    rid = repos.requests.create(operation="sync", requester_id="ops", actor="ops", resource_key="k-single",
                                payload=dict(SYNC_ITEM), priority="mid", auth_method="session")
    repos.requests.set_state(rid, RequestState.PLANNED, actor="planner")
    plan = repos.data_jobs.create_plan(rid, actor="planner")
    jid = repos.data_jobs.create_job(rid, plan, operation="sync", priority="mid", source_storage="s1",
                                     destination_storage="s2", source="a", destination="b", options={},
                                     tool="dsync", worker_pool={}, precondition={}, actor="planner")
    repos.data_jobs.set_preview(jid, fingerprint="sha256:x", expires_at="2099-01-01T00:00:00Z",
                                artifact_uri="x", summary={"files": 1})
    repos.data_jobs.set_job_state(jid, DataJobState.CONFIRM_PENDING, actor="stepper")
    BatchOrchestrator(repos, settings=_S()).run_once()
    job = repos.data_jobs.get_job(jid)
    assert job["state"] == DataJobState.CONFIRM_PENDING.value and job["confirmed_fingerprint"] is None


def test_migrate_adds_auto_confirm_by_to_existing_batches(db):
    # 구형 batches 흉내: 새 컬럼을 빼고 재생성 -- 한쪽(CREATE)만 넣으면 기배포 DB 에서만 컬럼이 없다(슬라이스 14 교훈).
    db.execute("DROP TABLE batches")
    db.execute("""CREATE TABLE batches (batch_id TEXT PRIMARY KEY, operation TEXT NOT NULL,
        requester_id TEXT NOT NULL, actor TEXT NOT NULL, status TEXT NOT NULL,
        max_concurrency INTEGER NOT NULL, options TEXT NOT NULL, note TEXT,
        item_count INTEGER NOT NULL DEFAULT 0, succeeded_count INTEGER NOT NULL DEFAULT 0,
        failed_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("""INSERT INTO batches (batch_id, operation, requester_id, actor, status, max_concurrency, options,
        created_at, updated_at) VALUES ('old', 'sync', 'ops', 'ops', 'PreviewReady', 1, '{}', 'x', 'x')""")
    from dms.migrations import migrate, _column_exists
    migrate(db)
    assert _column_exists(db, "batches", "auto_confirm_by")
    b = Repositories(db).batches.get("old")
    assert b["auto_confirm"] is False and b["auto_confirm_by"] is None     # 기배포 행 = 꺼짐(종전 동작)
