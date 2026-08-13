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
    # 슬라이스 33(H): 로그인 성공 시 프로브 타깃 조건부 선등록. 신원 전파는
    # register_probe_target -> 에이전트 리포트 응답 -> 노드별 getpwnam 프로브 ->
    # 다음 리포트 상행의 왕복(~130s)이라, 첫 데이터 요청 시점에 시작하면 그만큼
    # 적격 노드가 늦게 늘어난다. 로그인 시점에 미리 시작해 예열한다.
    # LDAP 에 해석되는 계정만 등록한다 -- 로컬 전용 계정(mason 류)을 넣으면 전
    # 노드가 영원히 status Missing 프로브만 쌓는다(예열 이득 0, 프로브 낭비만).
    resolver = request.app.state.identity_resolver
    if resolver is not None:
        try:
            if resolver.resolve(body.username) is not None:
                request.app.state.repos.control.register_probe_target(body.username)
        except Exception:
            # 선등록은 예열 최적화일 뿐 로그인의 전제가 아니다 -- LDAP 불가
            # (IdentityUnavailable)를 포함한 어떤 실패도 로그인을 막으면 안 되므로
            # 전부 삼킨다(fail-soft). 진짜 신원 판정은 planner 의
            # resolve_job_identity 가 fail-closed 로 다시 한다.
            pass
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
        # M8: actor="admin-token"을 그대로 쓰면 accounts._USERNAME_RE가 "admin-token"을
        # 유효한 사용자명으로 허용하고 /api/auth/signup은 무인증이라, 누구나 그 이름으로
        # 셀프 가입해 이 부트스트랩 경로가 남긴 감사 행과 구분 안 되는 행을 만들 수
        # 있다. ':'는 사용자명에 금지돼 있어(auth.py의 audit_actor()가 감사 actor에
        # token: 접두를 붙일 때 쓰는 것과 같은 예약 네임스페이스) 어떤 사용자도 절대
        # 이 값에 도달할 수 없다.
        request.app.state.repos.accounts.create(
            body.username, body.password, ROLE_ADMIN, actor="token:admin-token")
    except DomainValidationError as e:
        raise HTTPException(status_code=409 if e.reason_code == "account_exists" else 422,
                            detail=e.reason_code)
    return {"username": body.username, "role": ROLE_ADMIN}
