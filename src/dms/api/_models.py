"""Request body models used across DMS API routers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class MutatingBody(BaseModel):
    requester_id: str
    payload: dict[str, Any] = {}


class ControlStateBody(BaseModel):
    reason: str | None = None
    block_scheduling: bool = True
    force: bool = False


class DisableIdentityMappingBody(BaseModel):
    reason: str | None = None


class ConfirmDataJobBody(BaseModel):
    requester_id: str | None = None
    confirm: bool = False
    preview_observed_hash: str | None = None
    memo: str | None = None
