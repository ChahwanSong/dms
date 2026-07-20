"""dms-voc — 사용자 문의/요청(VOC) 등록·처리 API.

역할 분리(포탈 관례): 사용자는 자기 VOC를 등록/조회/삭제(`/api/user/voc`), 운영자는
전체 VOC를 미처리(open)/처리완료(resolved) 서브탭으로 보고 처리한다
(`/api/operator/voc`). 순수 포탈 도메인 — DMS API는 관여하지 않고 포탈 DB(`vocs`)에
저장한다. status: open(미처리) → resolved(처리완료, answer/처리자/시각 기록), 오처리는
reopen으로 복귀.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ..db import Database
from ..deps import get_db
from ..security import ROLE_OPERATOR, ROLE_USER, require_role

# 분류는 자유 확장 가능하되 목록은 서버가 고정(클라이언트 임의값 저장 방지).
VOC_CATEGORIES = ("문의", "요청", "버그", "기타")
_MAX_TITLE = 200
_MAX_BODY = 4000
_MAX_ANSWER = 4000


class VocCreate(BaseModel):
    category: str | None = None
    title: str = Field(min_length=1, max_length=_MAX_TITLE)
    body: str = Field(min_length=1, max_length=_MAX_BODY)


class VocResolve(BaseModel):
    answer: str | None = Field(default=None, max_length=_MAX_ANSWER)


def _actor(user: dict[str, Any], fallback: str) -> str:
    return str(user.get("username") or fallback)


def _clean_category(category: str | None) -> str | None:
    c = (category or "").strip()
    if not c:
        return None
    if c not in VOC_CATEGORIES:
        raise HTTPException(
            status_code=422,
            detail=f"category must be one of {', '.join(VOC_CATEGORIES)}",
        )
    return c


def user_voc_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/user/voc",
        tags=["user-voc"],
        dependencies=[Depends(require_role(ROLE_USER))],
    )

    @router.get("/categories")
    async def categories() -> dict[str, Any]:
        return {"categories": list(VOC_CATEGORIES)}

    @router.post("", status_code=201)
    async def create(
        payload: VocCreate,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        title = payload.title.strip()
        body = payload.body.strip()
        if not title or not body:
            raise HTTPException(status_code=422, detail="title/body required")
        return await db.create_voc(
            username=_actor(user, ROLE_USER),
            category=_clean_category(payload.category),
            title=title,
            body=body,
        )

    @router.get("")
    async def list_mine(
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        items = await db.list_vocs(username=_actor(user, ROLE_USER), limit=500)
        return {"items": items}

    @router.delete("/{voc_id}")
    async def delete_mine(
        voc_id: int,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_USER)),
    ) -> dict[str, Any]:
        # 본인 소유 + 아직 미처리(open)인 것만 회수 가능. 처리완료 건은 기록 보존(409).
        row = await db.delete_voc(
            voc_id=voc_id, username=_actor(user, ROLE_USER), only_open=True
        )
        if row is None:
            mine = await db.get_voc(voc_id)
            if mine and mine.get("username") == _actor(user, ROLE_USER):
                raise HTTPException(status_code=409, detail="처리완료된 VOC는 삭제할 수 없습니다")
            raise HTTPException(status_code=404, detail="voc_not_found")
        return {"deleted": voc_id}

    return router


def operator_voc_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/operator/voc",
        tags=["operator-voc"],
        dependencies=[Depends(require_role(ROLE_OPERATOR))],
    )

    @router.get("")
    async def list_all(
        status: str = Query(default="open"),
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=100, ge=1, le=500),
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        if status not in ("open", "resolved"):
            raise HTTPException(status_code=422, detail="status must be open|resolved")
        items = await db.list_vocs(status=status, limit=limit, offset=offset)
        counts = await db.voc_counts()  # 서브탭 뱃지 (미처리/처리완료 건수)
        return {"items": items, "counts": counts}

    @router.post("/{voc_id}:resolve")
    async def resolve(
        voc_id: int,
        payload: VocResolve,
        db: Database = Depends(get_db),
        user: dict[str, Any] = Depends(require_role(ROLE_OPERATOR)),
    ) -> dict[str, Any]:
        answer = (payload.answer or "").strip() or None
        row = await db.resolve_voc(
            voc_id=voc_id, answer=answer, resolved_by=_actor(user, ROLE_OPERATOR)
        )
        if row is None:
            if await db.get_voc(voc_id) is None:
                raise HTTPException(status_code=404, detail="voc_not_found")
            raise HTTPException(status_code=409, detail="이미 처리완료된 VOC입니다")
        return row

    @router.post("/{voc_id}:reopen")
    async def reopen(
        voc_id: int,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        row = await db.reopen_voc(voc_id=voc_id)
        if row is None:
            if await db.get_voc(voc_id) is None:
                raise HTTPException(status_code=404, detail="voc_not_found")
            raise HTTPException(status_code=409, detail="미처리 상태의 VOC입니다")
        return row

    @router.delete("/{voc_id}")
    async def delete_any(
        voc_id: int,
        db: Database = Depends(get_db),
    ) -> dict[str, Any]:
        row = await db.delete_voc(voc_id=voc_id)
        if row is None:
            raise HTTPException(status_code=404, detail="voc_not_found")
        return {"deleted": voc_id}

    return router
