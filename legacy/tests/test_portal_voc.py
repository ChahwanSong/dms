"""dms-voc 라우터 테스트 — 사용자 등록/회수, 운영자 미처리·처리완료 탭/처리/복귀.

HTTP(TestClient) + in-memory FakeDB. 소유권(사용자는 자기 것만), 상태 전이
(open→resolved→open), 탭 카운트, 역할 게이트(사용자→운영자 API 403)를 고정한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.routers.voc import operator_voc_router, user_voc_router


class FakeDB:
    def __init__(self) -> None:
        self.rows: dict[int, dict[str, Any]] = {}
        self._n = 1

    async def create_voc(self, *, username, category, title, body):
        vid = self._n
        self._n += 1
        row = {
            "id": vid, "username": username, "category": category, "title": title,
            "body": body, "status": "open", "answer": None, "resolved_by": None,
            "resolved_at": None,
            "created_at": datetime.now(timezone.utc), "updated_at": "t",
        }
        self.rows[vid] = row
        return dict(row)

    def _scope(self, r, username, status, since_days, search):
        if username is not None and r["username"] != username:
            return False
        if status is not None and r["status"] != status:
            return False
        if since_days is not None and r["created_at"] < datetime.now(timezone.utc) - timedelta(days=since_days):
            return False
        if search:
            fields = (r["title"], r["body"], r["username"])
            if not any(search.lower() in f.lower() for f in fields):
                return False
        return True

    async def list_vocs(self, *, username=None, status=None, since_days=None,
                        search=None, order="desc", cursor=None, limit=200):
        out = [dict(r) for r in self.rows.values()
               if self._scope(r, username, status, since_days, search)]
        out.sort(key=lambda r: r["id"], reverse=(order != "asc"))
        if cursor is not None:
            out = [r for r in out if (r["id"] < cursor if order != "asc" else r["id"] > cursor)]
        return out[:limit]

    async def voc_counts(self, *, since_days=None, search=None):
        c = {"open": 0, "resolved": 0}
        for r in self.rows.values():
            if self._scope(r, None, None, since_days, search):
                c[r["status"]] += 1
        return c

    async def get_voc(self, voc_id):
        r = self.rows.get(voc_id)
        return dict(r) if r else None

    async def resolve_voc(self, *, voc_id, answer, resolved_by):
        r = self.rows.get(voc_id)
        if not r or r["status"] != "open":
            return None
        r.update(status="resolved", answer=answer, resolved_by=resolved_by, resolved_at="t")
        return dict(r)

    async def reopen_voc(self, *, voc_id):
        r = self.rows.get(voc_id)
        if not r or r["status"] != "resolved":
            return None
        r.update(status="open", resolved_by=None, resolved_at=None)
        return dict(r)

    async def delete_voc(self, *, voc_id, username=None, only_open=False):
        r = self.rows.get(voc_id)
        if not r:
            return None
        if username is not None and r["username"] != username:
            return None
        if only_open and r["status"] != "open":
            return None
        return self.rows.pop(voc_id)


def make_client(db: FakeDB, username: str, role: str) -> TestClient:
    app = FastAPI()
    app.include_router(user_voc_router())
    app.include_router(operator_voc_router())
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": username, "role": role, "method": "ad" if role == "user" else "local",
    }
    return TestClient(app)


@pytest.fixture
def db() -> FakeDB:
    return FakeDB()


@pytest.fixture
def alice(db) -> TestClient:
    return make_client(db, "alice", "user")


@pytest.fixture
def op(db) -> TestClient:
    return make_client(db, "admin", "operator")


U = "/api/user/voc"
O = "/api/operator/voc"


# --- 사용자 ------------------------------------------------------------------


def test_user_create_and_list_own(alice, db):
    r = alice.post(U, json={"category": "요청", "title": "스캔 요청", "body": "teamA 경로 스캔 부탁"})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["username"] == "alice" and v["status"] == "open" and v["category"] == "요청"
    items = alice.get(U).json()["items"]
    assert len(items) == 1 and items[0]["title"] == "스캔 요청"


def test_user_list_scoped_to_self(db, alice):
    bob = make_client(db, "bob", "user")
    alice.post(U, json={"title": "a", "body": "b"})
    bob.post(U, json={"title": "bob-voc", "body": "b"})
    assert [v["title"] for v in alice.get(U).json()["items"]] == ["a"]
    assert [v["title"] for v in bob.get(U).json()["items"]] == ["bob-voc"]


def test_user_invalid_category_422(alice):
    assert alice.post(U, json={"category": "spam", "title": "t", "body": "b"}).status_code == 422


def test_user_blank_title_422(alice):
    assert alice.post(U, json={"title": "   ", "body": "b"}).status_code == 422


def test_user_delete_own_open_only(db, alice, op):
    vid = alice.post(U, json={"title": "t", "body": "b"}).json()["id"]
    # 처리완료 후엔 회수 불가(409)
    op.post(f"{O}/{vid}:resolve", json={"answer": "done"})
    assert alice.delete(f"{U}/{vid}").status_code == 409
    # 남의 VOC는 404
    bob = make_client(db, "bob", "user")
    assert bob.delete(f"{U}/{vid}").status_code == 404
    # open 상태 본인 것은 삭제 가능
    vid2 = alice.post(U, json={"title": "t2", "body": "b"}).json()["id"]
    assert alice.delete(f"{U}/{vid2}").status_code == 200


def test_user_cannot_access_operator_api(alice):
    assert alice.get(O).status_code == 403


# --- 운영자 ------------------------------------------------------------------


def test_operator_tabs_and_resolve_flow(db, alice, op):
    a = alice.post(U, json={"title": "one", "body": "b"}).json()["id"]
    alice.post(U, json={"title": "two", "body": "b"})
    r = op.get(O, params={"status": "open"}).json()
    assert r["counts"] == {"open": 2, "resolved": 0}
    assert {v["title"] for v in r["items"]} == {"one", "two"}

    # 처리: 답변과 함께 resolved로 이동, resolved_by 기록
    res = op.post(f"{O}/{a}:resolve", json={"answer": "처리했습니다"})
    assert res.status_code == 200
    assert res.json()["status"] == "resolved" and res.json()["resolved_by"] == "admin"

    r = op.get(O, params={"status": "resolved"}).json()
    assert r["counts"] == {"open": 1, "resolved": 1}
    assert r["items"][0]["answer"] == "처리했습니다"

    # 사용자 화면에도 답변 반영
    mine = alice.get(U).json()["items"]
    assert {v["status"] for v in mine} == {"open", "resolved"}


def test_operator_double_resolve_409_and_reopen(db, alice, op):
    vid = alice.post(U, json={"title": "t", "body": "b"}).json()["id"]
    assert op.post(f"{O}/{vid}:resolve", json={}).status_code == 200
    assert op.post(f"{O}/{vid}:resolve", json={}).status_code == 409
    assert op.post(f"{O}/{vid}:reopen").json()["status"] == "open"
    assert op.post(f"{O}/{vid}:reopen").status_code == 409
    assert op.post(f"{O}/999:resolve", json={}).status_code == 404


def test_operator_delete_any_and_bad_status_param(db, alice, op):
    vid = alice.post(U, json={"title": "t", "body": "b"}).json()["id"]
    assert op.delete(f"{O}/{vid}").status_code == 200
    assert op.delete(f"{O}/{vid}").status_code == 404
    assert op.get(O, params={"status": "junk"}).status_code == 422


def test_operator_cannot_use_user_api(op):
    assert op.get(U).status_code == 403


# --- 운영자: 정렬/기간/검색/페이징 -------------------------------------------


def test_operator_time_order_asc_desc(db, alice, op):
    for t in ("one", "two", "three"):  # 등록 순서 = 요청 시간 순서 (id 단조 증가)
        alice.post(U, json={"title": t, "body": "b"})
    desc = [v["title"] for v in op.get(O, params={"order": "desc"}).json()["items"]]
    asc = [v["title"] for v in op.get(O, params={"order": "asc"}).json()["items"]]
    assert desc == ["three", "two", "one"]
    assert asc == ["one", "two", "three"]
    assert op.get(O, params={"order": "sideways"}).status_code == 422


def test_operator_period_filter_counts_match(db, alice, op):
    for t in ("recent", "old-45d", "ancient-400d"):
        alice.post(U, json={"title": t, "body": "b"})
    now = datetime.now(timezone.utc)
    db.rows[2]["created_at"] = now - timedelta(days=45)
    db.rows[3]["created_at"] = now - timedelta(days=400)
    m1 = op.get(O, params={"period": "1m"}).json()
    y1 = op.get(O, params={"period": "1y"}).json()
    al = op.get(O, params={"period": "all"}).json()
    assert [v["title"] for v in m1["items"]] == ["recent"]
    assert {v["title"] for v in y1["items"]} == {"recent", "old-45d"}
    assert len(al["items"]) == 3
    # 탭 카운트도 기간 필터를 따라간다 (목록과 숫자 일치)
    assert m1["counts"]["open"] == 1 and y1["counts"]["open"] == 2 and al["counts"]["open"] == 3
    assert op.get(O, params={"period": "junk"}).status_code == 422


def test_operator_search_title_body_username(db, alice, op):
    bob = make_client(db, "bob", "user")
    alice.post(U, json={"title": "스캔 요청", "body": "teamA 경로"})
    bob.post(U, json={"title": "권한 문의", "body": "teamB 접근"})
    hit = lambda q: [v["title"] for v in op.get(O, params={"search": q}).json()["items"]]
    assert hit("스캔") == ["스캔 요청"]      # 제목
    assert hit("teamB") == ["권한 문의"]      # 본문
    assert hit("bob") == ["권한 문의"]        # 사용자
    r = op.get(O, params={"search": "스캔"}).json()
    assert r["counts"]["open"] == 1           # 카운트도 검색 반영


def test_operator_cursor_paging(db, alice, op):
    for i in range(5):
        alice.post(U, json={"title": f"v{i}", "body": "b"})
    r1 = op.get(O, params={"limit": 2}).json()
    assert [v["title"] for v in r1["items"]] == ["v4", "v3"] and r1["next_cursor"] == 4
    r2 = op.get(O, params={"limit": 2, "cursor": r1["next_cursor"]}).json()
    assert [v["title"] for v in r2["items"]] == ["v2", "v1"] and r2["next_cursor"] == 2
    r3 = op.get(O, params={"limit": 2, "cursor": r2["next_cursor"]}).json()
    assert [v["title"] for v in r3["items"]] == ["v0"] and r3["next_cursor"] is None


def test_operator_cursor_survives_concurrent_removal(db, alice, op):
    # 스크롤 도중 이미 로드된 행이 처리(제거)되어도 다음 페이지가 밀리지 않는다
    # (offset 페이징이었다면 v1이 건너뛰어짐).
    for i in range(6):
        alice.post(U, json={"title": f"v{i}", "body": "b"})
    r1 = op.get(O, params={"limit": 3}).json()          # v5 v4 v3, cursor=4
    assert [v["title"] for v in r1["items"]] == ["v5", "v4", "v3"]
    op.post(f"{O}/6:resolve", json={})                   # 로드된 영역의 v5를 처리(제거)
    r2 = op.get(O, params={"limit": 3, "cursor": r1["next_cursor"]}).json()
    assert [v["title"] for v in r2["items"]] == ["v2", "v1", "v0"]  # 누락 없음


def test_operator_asc_cursor_direction(db, alice, op):
    for i in range(4):
        alice.post(U, json={"title": f"v{i}", "body": "b"})
    r1 = op.get(O, params={"order": "asc", "limit": 2}).json()
    assert [v["title"] for v in r1["items"]] == ["v0", "v1"] and r1["next_cursor"] == 2
    r2 = op.get(O, params={"order": "asc", "limit": 2, "cursor": r1["next_cursor"]}).json()
    assert [v["title"] for v in r2["items"]] == ["v2", "v3"]


# --- 실 SQL 빌더(_voc_scope) 회귀 고정 — FakeDB와 별개로 이스케이프/파라미터 순서 검증


def test_voc_scope_sql_and_params():
    from portal.backend.db import Database
    where, params = Database._voc_scope("alice", "open", 30, r"50%_done\x")
    assert where.startswith(" WHERE ")
    assert "username=%s" in where and "status=%s" in where
    assert "make_interval(days => %s)" in where
    assert "(title ILIKE %s OR body ILIKE %s OR username ILIKE %s)" in where
    like = params[3]
    # LIKE 메타문자 이스케이프: % _ \ 가 리터럴 매치되도록
    assert like == r"%50\%\_done\\x%"
    assert params == ["alice", "open", 30, like, like, like]


def test_voc_scope_empty():
    from portal.backend.db import Database
    where, params = Database._voc_scope(None, None, None, None)
    assert where == "" and params == []



def test_voc_list_sql_cursor_directions():
    from portal.backend.db import Database
    sql, params = Database._voc_list_sql(None, "open", None, None, "desc", 42, 50)
    assert "status=%s" in sql and "id < %s" in sql
    assert sql.endswith("ORDER BY id DESC LIMIT %s") and params == ["open", 42, 50]
    sql, params = Database._voc_list_sql(None, None, None, None, "asc", 7, 10)
    assert "id > %s" in sql and "ORDER BY id ASC" in sql and params == [7, 10]
    sql, params = Database._voc_list_sql(None, None, 30, None, "desc", None, 10)
    assert "make_interval(days => %s)" in sql and "id <" not in sql and params == [30, 10]
