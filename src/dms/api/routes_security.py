"""관리 → 보안 이벤트(2026-11-03, 설계 v6 §5 「운영자 구제」) -- 로그인·인증번호 보호 장치의 현재 값, 인증번호 잠금 해제
(아이디별·전체), 최근 보안 이벤트.

- 조회(GET)는 관리자면 토큰도 된다(require_admin, 라우터 의존성). 해제(POST)는 **세션 관리자만**(403
  accounts_session_required -- routes_accounts._require_session 과 같은 축): 공유 토큰은 모든 노드 에이전트가 가진 admin
  자격이라, 그것으로 잠금을 풀면 무차별 대입 상한을 스크립트로 무효화할 수 있다. 특권 계정의 잠금은 특권 세션 관리자만
  (guard_privileged_account).
- 상태는 프로세스 메모리 값이다(api/login_guard.py·login_limiter.py) -- 응답의 started_at 이 「이 시각 이후 값, 재시작하면
  비워짐」의 기준이다. 0 은 「끔」, null 은 「없음·모름」이고 둘을 섞지 않는다(CLAUDE.md null ≠ 0).
- 해제는 감사 로그(mutation_class security)와 이벤트에 누가 했는지 남긴다.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..repositories.accounts import (VERIFICATION_FAILURE_LIMIT, VERIFICATION_FAILURE_WINDOW_SECONDS,
                                     VERIFICATION_MAX_ATTEMPTS, VERIFICATION_PURPOSES,
                                     VERIFICATION_TTL_SECONDS)
from ..repositories.text_search import search_tokens
from .auth import Identity, audit_actor, request_origin, require_admin
from .routes_accounts import _require_session, guard_privileged_account
from .routes_auth import VERIFICATION_GLOBAL_KEY

router = APIRouter(dependencies=[Depends(require_admin)])

# 보안 이벤트 화면이 보이는 event_type 전부(순서 = 화면 그룹 순서). 프런트 features/security/eventLabels.ts 의
# SECURITY_EVENT_LABELS 키 집합과 **정확히** 같아야 한다(tests/test_api_security 가 대조) -- 새 보안 이벤트를 내면 여기와
# 그 파일 둘 다에 넣는다. verification_client_locked 는 더는 내지 않지만(2026-11-03 IP 축 삭제) 보존 기간(30일) 동안 옛
# 행을 보이려고 남긴다.
SECURITY_EVENT_TYPES = (
    "login_rate_limited",
    "login_attempt_limited",
    "login_failure_alarm",
    "login_attempt_alarm",
    "login_source_alarm",
    "login_pacing_engaged",
    "login_busy",
    "login_pacing_enabled",
    "verification_locked",
    "verification_global_locked",
    "verification_client_locked",
    "verification_unlocked",
    "verification_global_unlocked",
    "password_admin_reset",
    "verification_email_throttled",
    "verification_email_failed",
    # 릴레이가 응답 전에 끊김(relay_no_response -- 코드는 저장, 메일은 갔을 수도). 빠져 있던 동안(~2026-11-11) 이 경고는 포탈
    # 어디에도 보이지 않았다 -- 「메일이 안 왔다」 문의에서 「메일」 묶음으로 걸러 보는 행이다.
    "verification_email_uncertain",
    "account_notice_sent",
    "account_notice_stub",
    "account_notice_failed",
    "account_notice_skipped",
)
SECURITY_EVENT_SEVERITIES = ("info", "warning", "error")
# 인증번호 자릿수 -- repositories.accounts.new_verification_code 와 같은 값(상태 화면 표시용).
VERIFICATION_CODE_DIGITS = 6
# 해제 대상 아이디 길이 상한. 형식(정규식)은 보지 않는다 -- 숫자 아이디·예약 이름의 옛 계정 재설정 행도 풀 수 있어야 한다.
_UNLOCK_USERNAME_MAX = 128


@router.get("/api/admin/security/status")
def security_status(request: Request):
    state = request.app.state
    settings = state.settings
    pacer = state.login_pacer
    gate = state.login_gate.snapshot()
    pace = pacer.snapshot()
    alarms = state.login_alarms
    recent = alarms.snapshot()
    guess = state.verification_guess_limiter
    with state.verification_guess_lock:
        guess_failures = guess.failures(VERIFICATION_GLOBAL_KEY)
        guess_wait = guess.retry_after(VERIFICATION_GLOBAL_KEY)
    return {
        "started_at": state.started_at,
        "login": {
            "id_failure_limit": settings.login_rate_limit_attempts,
            "id_failure_window_seconds": settings.login_rate_limit_window_seconds,
            "id_attempt_limit": settings.login_id_attempt_limit,
            "id_attempt_window_seconds": settings.login_id_attempt_window_seconds,
            "scrypt_slots": settings.login_scrypt_slots,
            "scrypt_max_wait_seconds": settings.login_scrypt_max_wait_seconds,
            "scrypt_in_use": gate["in_use"],
            "scrypt_waiting": gate["waiting"],
            "pacing": {"enabled": pacer.enabled, "limit": settings.login_global_pace_limit,
                       "window_seconds": settings.login_global_pace_window_seconds,
                       "max_wait_seconds": settings.login_global_pace_max_wait_seconds,
                       "tokens": pace["tokens"], "waiting": pace["waiting"]},
        },
        "alarms": {"window_seconds": settings.login_alarm_window_seconds,
                   "failures": settings.login_alarm_failures,
                   "attempts": settings.login_alarm_attempts,
                   "source_failures": settings.login_alarm_source_failures,
                   "recent_failures": recent["failures"], "recent_attempts": recent["attempts"]},
        "verification": {
            "code_digits": VERIFICATION_CODE_DIGITS,
            "code_ttl_seconds": VERIFICATION_TTL_SECONDS,
            "per_code_attempts": VERIFICATION_MAX_ATTEMPTS,
            "per_id_failure_limit": VERIFICATION_FAILURE_LIMIT,
            "per_id_failure_window_seconds": VERIFICATION_FAILURE_WINDOW_SECONDS,
            "global_guess_limit": settings.verification_guess_global_limit,
            "global_guess_window_seconds": settings.verification_guess_global_window_seconds,
            "global_guess_failures": guess_failures,
            "global_locked": guess_wait is not None,
            "global_retry_after_seconds": guess_wait,
        },
    }


@router.get("/api/admin/security/verification-locks")
def verification_locks(request: Request, limit: int = Query(200, ge=1, le=1000)):
    items, truncated = request.app.state.repos.accounts.verification_lock_rows(limit=limit)
    return {"items": items, "truncated": truncated, "limit": limit}


class UnlockBody(BaseModel):
    username: str
    purpose: str | None = None


@router.post("/api/admin/security/verification-unlock")
def verification_unlock(body: UnlockBody, request: Request, identity: Identity = Depends(require_admin)):
    """아이디별 인증 잠금 해제: 그 아이디의 verification_failures 행(purpose null = 두 용도 모두)을 지운다. 계정이 없어도
    된다(가입 잠금은 아직 없는 아이디의 것). 지운 행이 0 이어도 200 cleared 0 이고 감사는 남는다(그 사이 저절로 풀렸거나
    성공했을 수 있다 -- 누가 언제 풀려고 했는지가 기록의 본질)."""
    _require_session(identity)
    username = body.username
    if not username.strip() or len(username) > _UNLOCK_USERNAME_MAX:
        raise HTTPException(status_code=422, detail="invalid_username")
    if body.purpose is not None and body.purpose not in VERIFICATION_PURPOSES:
        raise HTTPException(status_code=422, detail="invalid_verification_purpose")
    guard_privileged_account(request, identity, username)
    repos = request.app.state.repos
    actor = audit_actor(identity)
    rows = repos.accounts.clear_verification_failures(username, body.purpose, actor=actor)
    repos.observability.record_event(
        component="accounts", severity="info", event_type="verification_unlocked",
        message=(f"{actor} cleared the verification lock of {username} "
                 f"({body.purpose or 'all purposes'}) -- {len(rows)} row(s)"),
        payload={"username": username, "purpose": body.purpose, "cleared": len(rows), "actor": actor,
                 **request_origin(request)})
    return {"username": username, "purpose": body.purpose, "cleared": len(rows)}


@router.post("/api/admin/security/verification-global-unlock")
def verification_global_unlock(request: Request, identity: Identity = Depends(require_admin)):
    """인증번호 전체 오답 상한(메모리) 초기화. 본문은 받지 않는다. 공격이 계속되면 곧 다시 잠긴다 -- 화면이 그 사실과 출발지를
    먼저 보라고 안내한다."""
    _require_session(identity)
    state = request.app.state
    with state.verification_guess_lock:
        cleared = state.verification_guess_limiter.failures(VERIFICATION_GLOBAL_KEY)
        state.verification_guess_limiter.clear(VERIFICATION_GLOBAL_KEY)
    actor = audit_actor(identity)
    state.repos.accounts.audit_security("verification_global_unlock", "verification_guess_global",
                                        {"failures": cleared}, {"failures": 0}, actor=actor)
    state.repos.observability.record_event(
        component="accounts", severity="info", event_type="verification_global_unlocked",
        message=f"{actor} reset the global wrong-verification-code counter ({cleared} failure(s) cleared)",
        payload={"cleared_failures": cleared, "actor": actor, **request_origin(request)})
    return {"cleared_failures": cleared}


@router.get("/api/admin/security/events")
def security_events(request: Request,
                    limit: int = Query(50, ge=1, le=200),
                    before: int | None = Query(None, ge=1),
                    type: str | None = Query(None),
                    severity: str | None = Query(None),
                    q: str | None = Query(None, max_length=200)):
    # 빈 값은 「필터 없음」으로 읽는다(포탈은 빈 필터를 쿼리에서 빼지만, 손으로 친 ?type= 를 422 로 돌려보낼 이유가 없다).
    event_type = type or None
    severity = severity or None
    if event_type is not None and event_type not in SECURITY_EVENT_TYPES:
        raise HTTPException(status_code=422, detail="invalid_event_filter")
    if severity is not None and severity not in SECURITY_EVENT_SEVERITIES:
        raise HTTPException(status_code=422, detail="invalid_event_filter")
    return request.app.state.repos.observability.security_events(
        SECURITY_EVENT_TYPES, limit=limit, before=before, event_type=event_type, severity=severity,
        q_tokens=search_tokens(q))
