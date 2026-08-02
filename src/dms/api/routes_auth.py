from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..domain import DomainValidationError, ROLE_ADMIN, ROLE_USER
from .auth import Identity, require_user, tokens_match

router = APIRouter()


class SignupBody(BaseModel):
    username: str
    password: str
    email: str | None = None


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/api/auth/signup", status_code=201)
def signup(body: SignupBody, request: Request):
    # 사내 메일 인증은 더미: email은 기록만 하고 검증 없이 계정 생성 (스펙 §3 인증)
    try:
        request.app.state.repos.accounts.create(
            body.username, body.password, ROLE_USER, email=body.email,
            actor=body.username)
    except DomainValidationError as e:
        raise HTTPException(status_code=409 if e.reason_code == "account_exists" else 422,
                            detail=e.reason_code)
    return {"username": body.username}


@router.post("/api/auth/login")
def login(body: LoginBody, request: Request):
    role = request.app.state.repos.accounts.verify(body.username, body.password)
    if role is None:
        raise HTTPException(status_code=401, detail="invalid_credentials")
    request.session.clear()
    request.session["username"] = body.username
    request.session["role"] = role
    return {"actor": body.username, "role": role}


@router.post("/api/auth/logout")
def logout(request: Request):
    request.session.clear()
    return {"status": "ok"}


@router.get("/api/auth/me")
def me(identity: Identity = Depends(require_user)):
    return {"actor": identity.actor, "role": identity.role}


@router.post("/api/admin/accounts", status_code=201)
def create_admin_account(body: LoginBody, request: Request):
    supplied = request.headers.get("x-admin-token", "")
    if not tokens_match(supplied, request.app.state.settings.admin_token):
        raise HTTPException(status_code=403, detail="admin_token_required")
    try:
        request.app.state.repos.accounts.create(
            body.username, body.password, ROLE_ADMIN, actor="admin-token")
    except DomainValidationError as e:
        raise HTTPException(status_code=409 if e.reason_code == "account_exists" else 422,
                            detail=e.reason_code)
    return {"username": body.username, "role": ROLE_ADMIN}
