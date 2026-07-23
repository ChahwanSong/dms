"""Portal BFF /api/operator/control-cluster proxies the DMS control cluster name.

The storage form uses it to default a filesystem mapping's *agent* cluster (the
cluster whose RM/DM agents report the storage) instead of the static "cluster-a"
skeleton placeholder. The value is static DMS config, so the route caches it.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.routers import operator as operator_mod
from portal.backend.routers.operator import operator_router

OP = "/api/operator"


class FakeDms:
    def __init__(self, inventory: dict[str, Any] | None = None):
        self._inventory = (
            inventory
            if inventory is not None
            else {"clusters": {}, "worker_roles": {}, "control_cluster_name": "dms"}
        )
        self.inventory_calls = 0

    async def get_inventory(self, *, actor: str | None = None):
        self.inventory_calls += 1
        return self._inventory


def _reset_cache() -> None:
    operator_mod._control_cluster_cache["value"] = None
    operator_mod._control_cluster_cache["at"] = 0.0


def make_client(dms: FakeDms, *, role: str = "operator") -> TestClient:
    _reset_cache()  # module-level cache must not leak between tests
    app = FastAPI()
    app.include_router(operator_router())
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op" if role == "operator" else "u",
        "role": role,
        "method": "local",
    }
    return TestClient(app)


def test_control_cluster_returned():
    dms = FakeDms()
    c = make_client(dms)
    r = c.get(f"{OP}/control-cluster")
    assert r.status_code == 200
    assert r.json() == {"control_cluster_name": "dms"}
    assert dms.inventory_calls == 1


def test_control_cluster_cached():
    dms = FakeDms()
    c = make_client(dms)
    c.get(f"{OP}/control-cluster")
    c.get(f"{OP}/control-cluster")
    # second call served from the module cache — DMS inventory fetched only once
    assert dms.inventory_calls == 1


def test_control_cluster_missing_field_is_null():
    dms = FakeDms(inventory={"clusters": {}, "worker_roles": {}})
    c = make_client(dms)
    r = c.get(f"{OP}/control-cluster")
    assert r.status_code == 200
    assert r.json() == {"control_cluster_name": None}


def test_requires_operator_role():
    dms = FakeDms()
    c = make_client(dms, role="user")  # a user session must be rejected
    assert c.get(f"{OP}/control-cluster").status_code == 403
