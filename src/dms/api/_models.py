"""Request body models used across DMS API routers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ControlStateBody(BaseModel):
    reason: str | None = None
    block_scheduling: bool = True
    force: bool = False


class ActionAckItem(BaseModel):
    fingerprint: str
    issue_type: str | None = None
    reason: str | None = None


class ActionAckBody(BaseModel):
    items: list[ActionAckItem] = []


class ActionUnackBody(BaseModel):
    fingerprints: list[str] = []



class ConfirmDataJobBody(BaseModel):
    requester_id: str | None = None
    confirm: bool = False
    preview_observed_hash: str | None = None
    memo: str | None = None
