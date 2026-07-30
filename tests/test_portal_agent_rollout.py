"""Portal BFF proxies the agent rollout endpoints to DMS.

The Storage Inventory 'agent restart' action calls the BFF, which forwards to the
DMS /api/v1/agent/rollout-restart|status endpoints and re-raises DMS errors.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.dms_client import DmsApiError
from portal.backend.routers.operator import operator_router

OP = "/api/operator"

# DMS ships exactly one agent DaemonSet (AGENT_DAEMONSETS).
DM_AGENT = "dms-dm-agent"


def _ds(**over):
    """One rollout-status DaemonSet entry, converged unless overridden."""
    return {"name": DM_AGENT, "desired": 3, "updated": 3, "ready": 3,
            "available": 3, "unavailable": 0, "rolling": False, **over}


class FakeDms:
    def __init__(self, *, status=None, restart=None, raise_on=None):
        self._status = status or {"namespace": "dms", "daemonsets": [_ds()]}
        self._restart = restart or {
            "namespace": "dms", "restarted_at": "2026-07-02T12:00:00Z",
            "restarted": [DM_AGENT], "errors": {},
        }
        self._raise_on = raise_on or {}
        self.calls: list[tuple[str, str]] = []

    async def rollout_restart_agents(self, *, actor: str | None = None):
        self.calls.append(("restart", actor))
        if "restart" in self._raise_on:
            raise self._raise_on["restart"]
        return self._restart

    async def agent_rollout_status(self, *, actor: str | None = None):
        self.calls.append(("status", actor))
        if "status" in self._raise_on:
            raise self._raise_on["status"]
        return self._status


def make_client(dms: FakeDms) -> TestClient:
    app = FastAPI()
    app.include_router(operator_router())
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    return TestClient(app)


def test_rollout_status_converged_proxied():
    dms = FakeDms()
    c = make_client(dms)
    r = c.get(f"{OP}/agents/rollout-status")
    assert r.status_code == 200
    body = r.json()
    assert body["namespace"] == "dms"
    names = {d["name"]: d for d in body["daemonsets"]}
    assert list(names) == [DM_AGENT]
    assert names[DM_AGENT]["rolling"] is False
    assert names[DM_AGENT]["updated"] == 3
    assert ("status", "op") in dms.calls  # actor forwarded = logged-in operator


def test_rollout_status_rolling_proxied():
    # mid-restart: the DaemonSet's per-pod progress is forwarded verbatim
    dms = FakeDms(status={"namespace": "dms", "daemonsets": [
        _ds(updated=1, ready=1, available=1, unavailable=2, rolling=True),
    ]})
    body = make_client(dms).get(f"{OP}/agents/rollout-status").json()
    ds = body["daemonsets"][0]
    assert ds["name"] == DM_AGENT
    assert ds["rolling"] is True
    assert (ds["updated"], ds["unavailable"]) == (1, 2)


def test_rollout_restart_proxied():
    dms = FakeDms()
    c = make_client(dms)
    r = c.post(f"{OP}/agents/rollout-restart")
    assert r.status_code == 200
    assert r.json()["restarted"] == [DM_AGENT]
    assert ("restart", "op") in dms.calls


def test_dms_error_forwarded_with_status():
    dms = FakeDms(raise_on={"restart": DmsApiError(503, "kubernetes package not installed")})
    c = make_client(dms)
    r = c.post(f"{OP}/agents/rollout-restart")
    assert r.status_code == 503
    assert "kubernetes" in str(r.json()["detail"])


def test_requires_operator_role():
    dms = FakeDms()
    app = FastAPI()
    app.include_router(operator_router())
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    # a non-operator (user) session → 403 via the router role gate
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "u", "role": "user", "method": "ad",
    }
    c = TestClient(app)
    assert c.get(f"{OP}/agents/rollout-status").status_code == 403
    assert c.post(f"{OP}/agents/rollout-restart").status_code == 403
