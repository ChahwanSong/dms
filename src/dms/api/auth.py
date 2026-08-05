import hmac
from collections import namedtuple
from fastapi import HTTPException, Request

Identity = namedtuple("Identity", "actor role")


def tokens_match(supplied: str, expected: str) -> bool:
    return hmac.compare_digest(
        supplied.encode("utf-8", "surrogateescape"),
        expected.encode("utf-8", "surrogateescape"))


def current_identity(request: Request) -> Identity:
    settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):]
        if tokens_match(token, settings.shared_token):
            actor = request.headers.get("x-dms-actor", "shared-token")
            return Identity(actor=actor, role="admin")
        raise HTTPException(status_code=401, detail="invalid_token")
    session = request.session
    username = session.get("username")
    if username:
        account = request.app.state.repos.accounts.get(username)
        if account is None or account["disabled"]:
            request.session.clear()
            raise HTTPException(status_code=401, detail="account_disabled")
        return Identity(actor=username, role=account["role"])
    raise HTTPException(status_code=401, detail="not_authenticated")


def require_user(request: Request) -> Identity:
    return current_identity(request)


def require_admin(request: Request) -> Identity:
    identity = current_identity(request)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return identity
