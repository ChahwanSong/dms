"""Portal BFF — GET /backup/node-policy surfaces the "자동" default node count.

nsync is a ROLE-BASED DM policy (separate source/destination node pools, with
default_worker_nodes intentionally null per the DMS policy schema). The backup
form has a single "병렬 노드 수" field, so the cross-storage default must fall back
to default_source_nodes — otherwise the form shows a blank policy default for
src != dst backups (the bug this covers).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.routers.backup import backup_router


class FakeDms:
    def __init__(self, policies: list[dict[str, Any]]) -> None:
        self._policies = policies

    async def list_data_management_policies(self, *, actor: str) -> list[dict[str, Any]]:
        return self._policies


def make_client(dms: FakeDms) -> TestClient:
    app = FastAPI()
    app.include_router(backup_router(Settings()))
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    return TestClient(app)


def test_node_policy_nsync_falls_back_to_source_nodes() -> None:
    # dsync = worker-based (single pool); nsync = role-based (source/destination pools,
    # worker_nodes null). Only nsync exercises the fallback.
    dms = FakeDms(
        [
            {"operation": "dsync", "default_worker_nodes": 3, "max_worker_nodes": 3,
             "default_source_nodes": None, "max_source_nodes": None},
            {"operation": "nsync", "default_worker_nodes": None, "max_worker_nodes": None,
             "default_source_nodes": 2, "max_source_nodes": 3},
        ]
    )
    body = make_client(dms).get("/api/operator/backup/node-policy").json()
    # same-storage (dsync) keeps its worker_nodes default unchanged
    assert body["dsync"]["default_worker_nodes"] == 3
    # cross-storage (nsync) has no worker_nodes -> fall back to source_nodes so the single
    # "병렬 노드 수" field still shows what "자동" resolves to.
    assert body["nsync"]["default_worker_nodes"] == 2
    assert body["nsync"]["max_worker_nodes"] == 3


def test_node_policy_missing_operation_is_null() -> None:
    body = make_client(FakeDms([])).get("/api/operator/backup/node-policy").json()
    assert body == {"dsync": None, "nsync": None}
