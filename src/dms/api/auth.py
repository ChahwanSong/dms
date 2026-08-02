import hmac
from collections import namedtuple
from fastapi import HTTPException, Request

Identity = namedtuple("Identity", "actor role")


def current_identity(request: Request) -> Identity:
    settings = request.app.state.settings
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        token = auth[len("Bearer "):]
        if hmac.compare_digest(token, settings.shared_token):
            actor = request.headers.get("x-dms-actor", "shared-token")
            return Identity(actor=actor, role="admin")
        raise HTTPException(status_code=401, detail="invalid_token")
    session = request.session
    if session.get("username") and session.get("role"):
        return Identity(actor=session["username"], role=session["role"])
    raise HTTPException(status_code=401, detail="not_authenticated")


def require_user(request: Request) -> Identity:
    return current_identity(request)


def require_admin(request: Request) -> Identity:
    identity = current_identity(request)
    if identity.role != "admin":
        raise HTTPException(status_code=403, detail="admin_required")
    return identity
