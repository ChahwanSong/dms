"""dms-voc 라우터 테스트 — 사용자 등록/회수, 운영자 미처리·처리완료 탭/처리/복귀.

HTTP(TestClient) + in-memory FakeDB. 소유권(사용자는 자기 것만), 상태 전이
(open→resolved→open), 탭 카운트, 역할 게이트(사용자→운영자 API 403)를 고정한다.
"""

from __future__ import annotations

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
            "resolved_at": None, "created_at": "t", "updated_at": "t",
        }
        self.rows[vid] = row
        return dict(row)

    async def list_vocs(self, *, username=None, status=None, limit=200, offset=0):
        out = [
            dict(r) for r in self.rows.values()
            if (username is None or r["username"] == username)
            and (status is None or r["status"] == status)
        ]
        out.sort(key=lambda r: r["id"], reverse=True)
        return out[offset:offset + limit]

    async def voc_counts(self):
        c = {"open": 0, "resolved": 0}
        for r in self.rows.values():
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
