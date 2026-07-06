"""Portal BFF — rm/scan node-policy must read the DMS OPERATION name.

The DMS policy is keyed by operation ("rm", "scan"), not the CLI tool name
("drm", "dscan"). The BFF used the tool name, so the forms showed a blank
"자동 (정책 기본값)". These lock the operation-name lookup.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.routers.rmjob import rm_router
from portal.backend.routers.scan import scan_router


class FakeDms:
    def __init__(self, policies: list[dict[str, Any]]) -> None:
        self._policies = policies

    async def list_data_management_policies(self, *, actor: str) -> list[dict[str, Any]]:
        return self._policies


def _client(router_factory, dms: FakeDms) -> TestClient:
    app = FastAPI()
    app.include_router(router_factory(Settings()))
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    return TestClient(app)


# Note the mismatch: operation "rm"/"scan" vs the tool names "drm"/"dscan".
POLICIES = [
    {"operation": "rm", "default_worker_nodes": 3, "max_worker_nodes": 3},
    {"operation": "scan", "default_worker_nodes": 2, "max_worker_nodes": 4},
    {"operation": "dsync", "default_worker_nodes": 9, "max_worker_nodes": 9},
]


def test_rm_node_policy_reads_rm_operation() -> None:
    body = _client(rm_router, FakeDms(POLICIES)).get("/api/operator/rm-jobs/node-policy").json()
    assert body["drm"]["default_worker_nodes"] == 3  # from the "rm" operation policy


def test_scan_node_policy_reads_scan_operation() -> None:
    body = _client(scan_router, FakeDms(POLICIES)).get("/api/operator/scan/node-policy").json()
    assert body["dscan"]["default_worker_nodes"] == 2  # from the "scan" operation policy


def test_node_policy_missing_operation_is_null() -> None:
    assert _client(rm_router, FakeDms([])).get("/api/operator/rm-jobs/node-policy").json() == {
        "drm": None
    }
    assert _client(scan_router, FakeDms([])).get("/api/operator/scan/node-policy").json() == {
        "dscan": None
    }
