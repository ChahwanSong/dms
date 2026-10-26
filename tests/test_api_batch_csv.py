"""배치 CSV 라우트(:parse-csv · csv-template · items.csv)와 배치 기록 삭제의 CAS·감사·세션 경계(2026-10-26 BACKLOG C).
해석 규칙 자체는 test_batch_csv.py(순수)."""
import json

import pytest

from dms.api import routes_batches as rb
from dms.batch_csv import MAX_CSV_BYTES, MAX_CSV_ROWS

TOKEN = {"Authorization": "Bearer tok-shared"}
BOM = "﻿"


def _admin(client):
    client.app.state.repos.accounts.create("admin", "pw", "admin", actor="t")
    client.post("/api/auth/login", json={"username": "admin", "password": "pw"})


def _create(client, op, items, **extra):
    body = {"operation": op, "max_concurrency": 1, "note": None, "items": items,
            "options": {"recursive": True} if op == "rm" else {}, **extra}
    return client.post("/api/admin/batches", json=body)


def _completed(client, op="scan", items=None):
    items = items or [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "=b"}]
    bid = _create(client, op, items).json()["batch_id"]
    repo = client.app.state.repos.batches
    repo.set_item_status(bid, 0, "Succeeded")
    repo.set_status(bid, "Completed")
    return bid


def _parse(client, op, text, headers=None, **storages):
    return client.post("/api/admin/batches:parse-csv", json={"operation": op, "csv": text, **storages}, headers=headers)


# --- :parse-csv --------------------------------------------------------------------------------------------------------

def test_parse_returns_items_ready_for_create(client):
    _admin(client)
    r = _parse(client, "sync", "﻿source,destination\r\nteam/a,backup/a\r\n\"x,y\",z\r\n",
               source_storage="s1", destination_storage="s2")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["errors"] == [] and body["error_count"] == 0 and body["rows"] == 2
    assert body["storages"] == {"source_storage": "s1", "destination_storage": "s2"}
    created = _create(client, "sync", body["items"])
    assert created.status_code == 202, created.text


def test_parse_reports_the_same_codes_create_would_raise_with_lines(client):
    # 같은 함수로 판정한다 -- 생성은 배치 단위 첫 사유만, 해석은 줄마다
    _admin(client)
    text = "target\nold/a\nold/a/sub\n/abs\n"
    r = _parse(client, "rm", text, storage="s1").json()
    assert [(e["line"], e["reason_code"]) for e in r["errors"]] == [(3, "batch_rm_targets_overlap"), (4, "unsafe_path")]
    assert r["errors"][0]["related_line"] == 2
    create = _create(client, "rm", r["items"])
    assert create.status_code == 422 and create.json()["detail"] in {"batch_rm_targets_overlap", "unsafe_path"}


def test_parse_shape_errors_and_storage_from_csv(client):
    _admin(client)
    r = _parse(client, "scan", "storage,target\ns1,a\ns2,b\n").json()
    assert r["storages"] == {"storage": "s1"}
    assert [(e["line"], e["row"], e["reason_code"]) for e in r["errors"]] == [(3, 2, "batch_storage_mixed")]
    r = _parse(client, "scan", "a,b\n").json()
    assert [e["reason_code"] for e in r["errors"]] == ["batch_csv_bad_row"] and r["items"] == []


def test_parse_limits_and_bad_operation(client):
    _admin(client)
    big = "a" * (MAX_CSV_BYTES + 1)
    r = _parse(client, "scan", big)
    assert r.status_code == 413 and r.json()["detail"] == "batch_csv_too_large"
    rows = "\n".join(f"p{i}" for i in range(MAX_CSV_ROWS + 1))
    r = _parse(client, "scan", rows)
    assert r.status_code == 422 and r.json()["detail"] == "batch_csv_too_many_rows"
    r = _parse(client, "chmod", "a")
    assert r.status_code == 422 and r.json()["detail"] == "invalid_batch_operation"


def test_parse_is_read_only_admin_route_token_ok_user_forbidden(client):
    assert _parse(client, "scan", "a", headers=TOKEN).status_code == 200          # 읽기 -- 토큰도 된다
    client.post("/api/auth/signup", json={"username": "alice", "password": "pw"})
    client.post("/api/auth/login", json={"username": "alice", "password": "pw"})
    r = _parse(client, "scan", "a")
    assert r.status_code == 403 and r.json()["detail"] == "admin_required"


def test_parse_works_in_maintenance(client):
    _admin(client)
    client.put("/api/admin/control-state", json={"maintenance": True, "drain": False, "reason": None})
    assert _parse(client, "scan", "a").status_code == 200


# --- csv-template ------------------------------------------------------------------------------------------------------

def test_csv_template_route_is_not_shadowed_by_get_batch(client):
    _admin(client)
    r = client.get("/api/admin/batches/csv-template?operation=sync")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="dms-batch-template-sync.csv"' in r.headers["content-disposition"]
    assert r.text == BOM + "source,destination\r\nteam/dataset,backup/dataset\r\n"


def test_csv_template_bad_operation(client):
    _admin(client)
    r = client.get("/api/admin/batches/csv-template?operation=x")
    assert r.status_code == 422 and r.json()["detail"] == "invalid_batch_operation"
    assert client.get("/api/admin/batches/csv-template").status_code == 422     # 연산은 필수


# --- items.csv ---------------------------------------------------------------------------------------------------------

def test_export_items_csv_round_trips_through_parse(client):
    _admin(client)
    bid = _completed(client)
    r = client.get(f"/api/admin/batches/{bid}/items.csv", headers=TOKEN)          # 읽기 -- 토큰도 된다
    assert r.status_code == 200
    assert f'filename="dms-batch-{bid[:12]}-items.csv"' in r.headers["content-disposition"]
    assert r.text.startswith(BOM + "seq,storage,target,status,request_id")
    lines = r.text[1:].split("\r\n")
    assert lines[1].startswith("0,s1,a,Succeeded") and lines[2].startswith("1,s1,'=b,Queued")   # 수식 방어
    back = _parse(client, "scan", r.text).json()
    assert back["errors"] == [] and back["items"] == [{"storage": "s1", "target": "a"}, {"storage": "s1", "target": "=b"}]
    # 같은 파일로 전체 교체(종단 배치) -- 「내보내기 → 고쳐서 다시 올리기」 동선
    put = client.put(f"/api/admin/batches/{bid}/items", json={"items": back["items"]})
    assert put.status_code == 200, put.text


def test_export_items_csv_404(client):
    _admin(client)
    r = client.get("/api/admin/batches/" + "0" * 32 + "/items.csv")
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found"


# --- 배치 기록 삭제: 세션 관리자·CAS·감사 -------------------------------------------------------------------------------

def _audit(db, bid):
    return db.query("SELECT * FROM audit_log WHERE mutation_class = 'batch' AND operation = 'delete_record' "
                    "AND target_key = :k", {"k": bid})


def test_record_delete_requires_a_session_admin(client, db):
    _admin(client)
    bid = _completed(client)
    r = client.request("DELETE", f"/api/admin/batches/{bid}", headers=TOKEN)
    assert r.status_code == 403 and r.json()["detail"] == "admin_session_required"
    assert client.app.state.repos.batches.get(bid) is not None and _audit(db, bid) == []


def test_record_delete_leaves_an_audit_row(client, db):
    _admin(client)
    bid = _completed(client)
    client.patch(f"/api/admin/batches/{bid}", json={"name": "정기 스캔"})
    r = client.request("DELETE", f"/api/admin/batches/{bid}")
    assert r.status_code == 200 and r.json() == {"deleted": bid}
    rows = _audit(db, bid)
    assert len(rows) == 1 and rows[0]["actor"] == "admin" and rows[0]["after_state"] is None
    before = json.loads(rows[0]["before_state"])
    assert before["name"] == "정기 스캔" and before["status"] == "Completed" and before["item_count"] == 2
    assert before["operation"] == "scan"


def test_record_delete_cas_refuses_a_batch_that_restarted_after_the_check(client, db, monkeypatch):
    # 라우트가 종단을 본 뒤 :rescan·항목 추가가 배치를 되살린 경합 -- 저장소가 같은 트랜잭션에서 다시 보고 지우지 않는다
    _admin(client)
    bid = _completed(client)
    repo = client.app.state.repos.batches
    stale = repo.get(bid)
    repo.set_status(bid, "Running")
    monkeypatch.setattr(rb, "_get_batch_or_404", lambda request, batch_id: stale)
    r = client.request("DELETE", f"/api/admin/batches/{bid}")
    assert r.status_code == 409 and r.json()["detail"] == "batch_record_changed"
    assert repo.get(bid)["status"] == "Running" and len(repo.list_items(bid)) == 2 and _audit(db, bid) == []


def test_record_delete_gone_in_between_is_404(client, monkeypatch):
    _admin(client)
    bid = _completed(client)
    repo = client.app.state.repos.batches
    stale = repo.get(bid)
    repo.delete(bid, actor="other")
    monkeypatch.setattr(rb, "_get_batch_or_404", lambda request, batch_id: stale)
    r = client.request("DELETE", f"/api/admin/batches/{bid}")
    assert r.status_code == 404 and r.json()["detail"] == "batch_not_found"


@pytest.mark.parametrize("status", ["Running", "Previewing", "PreviewReady", "Cancelling", "Weird"])
def test_repo_delete_is_a_terminal_only_cas(client, db, status):
    repo = client.app.state.repos.batches
    bid = repo.create(operation="scan", requester_id="admin", actor="admin", max_concurrency=1, options={},
                      note=None, items=[{"storage": "s1", "target": "a"}], status=status)
    assert repo.delete(bid, actor="x") is False
    assert repo.get(bid) is not None and len(repo.list_items(bid)) == 1 and _audit(db, bid) == []
    assert repo.delete("f" * 32, actor="x") is None
    repo.set_status(bid, "Cancelled")
    assert repo.delete(bid, actor="x") is True
    assert repo.get(bid) is None and repo.list_items(bid) == [] and len(_audit(db, bid)) == 1
