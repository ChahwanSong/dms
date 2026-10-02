"""포탈 표시 이름(2026-10-02 사용자 요청: "포탈 메인 이름의 서브네임을 포탈 운영자가 설정 -- 예: AI Storage
Portal - SSC, DAI-CAE, DAI-OA").

  GET /api/portal-info            공개(로그인 전 화면도 그린다) -- {"subtitle": str | null}
  PUT /api/admin/portal-settings  관리자 -- {"subtitle": str | null}, 빈 값/null = 서브네임 없음

메인 이름("AI Storage Portal")은 프런트 상수다(features/portal/usePortal.ts). 서브네임은 control_state.
portal_subtitle 한 칸이고 사이드바·로그인 화면·브라우저 탭 제목이 "메인 - 서브" 로 그린다. 비밀이 아니라
공개 조회가 안전하다.
"""
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import Identity, audit_actor, require_admin

public_router = APIRouter()
router = APIRouter(dependencies=[Depends(require_admin)])

# 사이드바 폭(약 160px 의 두 번째 줄)·탭 제목에 들어가는 길이. 사이트 약칭이라 넉넉하다.
SUBTITLE_MAX = 40


def _subtitle(repos):
    row = repos.control.control_state() or {}
    return row.get("portal_subtitle") or None


def validate_subtitle(value) -> "str | None":
    """앞뒤 공백 제거, 빈 값 = None(없음). 제어·서식 문자(개행·탭·zero-width 등)는 거부 -- 한 줄 표시
    이름이고, 보이지 않는 문자로 같은 이름 둘을 만들 수 없게."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="invalid_portal_subtitle")
    v = value.strip()
    if v == "":
        return None
    if len(v) > SUBTITLE_MAX or any(unicodedata.category(c).startswith("C") for c in v):
        raise HTTPException(status_code=422, detail="invalid_portal_subtitle")
    return v


class PortalSettingsBody(BaseModel):
    subtitle: "str | None" = None


@public_router.get("/api/portal-info")
def portal_info(request: Request):
    return {"subtitle": _subtitle(request.app.state.repos)}


@router.put("/api/admin/portal-settings")
def put_portal_settings(body: PortalSettingsBody, request: Request,
                        identity: Identity = Depends(require_admin)):
    subtitle = validate_subtitle(body.subtitle)
    repos = request.app.state.repos
    repos.control.set_portal_subtitle(subtitle, actor=audit_actor(identity))
    return {"subtitle": _subtitle(repos)}
