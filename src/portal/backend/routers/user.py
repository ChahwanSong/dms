"""End-user interface API (role: user). Placeholder data for now.

Future: read-only/self-service views over the DMS HTTP API (my storage, my
requests, my data jobs) — the BFF will call DMS here.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..security import ROLE_USER, require_role


def user_router() -> APIRouter:
    router = APIRouter(
        prefix="/api/user",
        tags=["user"],
        dependencies=[Depends(require_role(ROLE_USER))],
    )

    @router.get("/overview")
    def overview(user: dict[str, Any] = Depends(require_role(ROLE_USER))) -> dict[str, Any]:
        return {
            "role": ROLE_USER,
            "username": user["username"],
            "sections": [
                {"key": "my-storage", "title": "내 스토리지", "status": "예정"},
                {"key": "my-requests", "title": "내 요청", "status": "예정"},
                {"key": "my-jobs", "title": "내 데이터 잡", "status": "예정"},
            ],
        }

    return router
