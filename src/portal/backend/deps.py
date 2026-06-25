"""Shared FastAPI dependencies for the portal BFF."""

from __future__ import annotations

from fastapi import HTTPException, Request

from .db import Database
from .dms_client import DmsClient


def get_dms_client(request: Request) -> DmsClient:
    """The shared DmsClient stored on app.state at startup."""
    return request.app.state.dms_client


def get_db(request: Request) -> Database:
    """The shared portal Database; 503 when no PORTAL_DB_URL is configured."""
    db: Database = request.app.state.db
    if not db.configured:
        raise HTTPException(status_code=503, detail="portal_db_not_configured")
    return db
