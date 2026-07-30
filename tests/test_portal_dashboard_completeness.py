"""Portal BFF dashboard — completeness without fetch-all.

These hit the real dashboard router via TestClient against an in-memory FakeDms
(mirroring tests/test_portal_batch_bulk.py). They assert the contract that removes
SILENT truncation while keeping the polled paths cheap:

  - /runs single-fetches high caps (active 1000 / stale 2000) and flags each section
    `truncated` ONLY when the cap was actually hit.
  - /requests (data jobs = GROWING history) keeps a CAPPED page and returns
    {jobs, total, truncated}; the exact total comes from the uncapped COUNT summary
    and is fetched ONLY when the page is truncated (never a fetch-all).
  - node health reads agent-reports with latest_per_node=true (complete + cheap).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.routers.dashboard import dashboard_router

DASH = "/api/operator/dashboard"


class FakeDms:
    """Records every call's kwargs so tests can assert single-fetch limits and that
    no history-scanning fetch-all happens on the polled paths."""

    def __init__(
        self,
        *,
        active_runs: list[dict[str, Any]] | None = None,
        stale_runs: list[dict[str, Any]] | None = None,
        mappings: list[dict[str, Any]] | None = None,
        data_jobs: list[dict[str, Any]] | None = None,
        summary: dict[str, Any] | None = None,
        reports: list[dict[str, Any]] | None = None,
    ) -> None:
        self.active_runs = active_runs or []
        self.stale_runs = stale_runs or []
        self.mappings = mappings or []
        self.data_jobs = data_jobs or []
        self.summary = summary or {}
        self.reports = reports or []
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _rec(self, name: str, **kw: Any) -> None:
        self.calls.append((name, kw))

    async def list_active_runs(self, *, actor: str, limit: int = 1000, offset: int = 0):
        self._rec("list_active_runs", limit=limit, offset=offset)
        return list(self.active_runs[:limit])

    async def list_stale_runs(self, *, actor: str, limit: int = 2000, offset: int = 0):
        self._rec("list_stale_runs", limit=limit, offset=offset)
        return list(self.stale_runs[:limit])

    async def list_storage_mappings(
        self, *, actor: str | None = None, cluster_name: str | None = None,
        limit: int = 10000, offset: int = 0,
    ):
        self._rec("list_storage_mappings", limit=limit, cluster_name=cluster_name)
        return list(self.mappings[:limit])

    async def list_data_jobs(
        self, *, actor: str, limit: int = 500, offset: int = 0,
        state: str | None = None, operation: str | None = None,
        storage_name: str | None = None,
    ):
        self._rec("list_data_jobs", limit=limit, state=state,
                  operation=operation, storage_name=storage_name)
        return list(self.data_jobs[:limit])

    async def get_data_job_summary(self, *, actor: str):
        self._rec("get_data_job_summary")
        return dict(self.summary)

    async def list_agent_reports(
        self, *, actor: str, freshness: str | None = None,
        latest_per_node: bool = False, limit: int | None = None,
        offset: int | None = None,
    ):
        self._rec("list_agent_reports", latest_per_node=latest_per_node,
                  freshness=freshness, limit=limit)
        return list(self.reports)

    async def get_control_state(self, *, actor: str):
        return {"maintenance_mode": False, "drain_mode": False}

    async def get_work_summary(self, *, actor: str):
        return {"plans": {}, "runs": {}, "requests": {}}

    async def get_volcano_status(self, *, actor: str):
        return {"queues": [], "jobs": [], "scheduler": [], "errors": {}}

    def limit_for(self, method: str) -> int | None:
        for name, kw in self.calls:
            if name == method:
                return kw.get("limit")
        return None

    def called(self, method: str) -> bool:
        return any(name == method for name, _ in self.calls)


def make_client(dms: FakeDms) -> TestClient:
    app = FastAPI()
    app.include_router(dashboard_router(Settings()))
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    return TestClient(app)



def _job(i: int, op: str = "data.sync", state: str = "Running") -> dict[str, Any]:
    return {"job_id": f"j{i}", "operation": op, "storage_name": "s", "state": state}


# --- runs --------------------------------------------------------------------

def test_runs_single_fetch_high_caps_and_truncation():
    dms = FakeDms(
        active_runs=[{"run_id": str(i)} for i in range(1000)],
        stale_runs=[{"run_id": "s1"}],
    )
    body = make_client(dms).get(f"{DASH}/runs").json()
    assert dms.limit_for("list_active_runs") == 1000
    assert dms.limit_for("list_stale_runs") == 2000
    assert len(body["active"]["data"]) == 1000
    assert body["active"]["truncated"] is True   # hit the 1000 cap
    assert body["stale"]["truncated"] is False


def test_runs_not_truncated_for_small_sets():
    dms = FakeDms(active_runs=[{"run_id": "a"}], stale_runs=[])
    body = make_client(dms).get(f"{DASH}/runs").json()
    assert body["active"]["truncated"] is False
    assert body["stale"]["truncated"] is False



# --- data jobs (growing history) --------------------------------------------

def test_data_jobs_not_truncated_total_is_count_no_summary_call():
    dms = FakeDms(data_jobs=[_job(0), _job(1), _job(2)], summary={"total": 999})
    body = make_client(dms).get(f"{DASH}/requests?limit=10").json()
    assert body["truncated"] is False
    assert body["total"] == 3                 # we have every matching row
    assert len(body["jobs"]) == 3
    # the COUNT summary is NOT fetched on the cheap, non-truncated path
    assert not dms.called("get_data_job_summary")


def test_data_jobs_truncated_uses_summary_grand_total():
    dms = FakeDms(
        data_jobs=[_job(i) for i in range(5)],
        summary={"total": 12345, "by_state": {"Running": 42},
                 "by_operation": {"data.sync": 77}},
    )
    body = make_client(dms).get(f"{DASH}/requests?limit=2").json()
    assert body["truncated"] is True
    assert len(body["jobs"]) == 2
    assert body["total"] == 12345             # exact grand total, no fetch-all
    assert dms.called("get_data_job_summary")


def test_data_jobs_truncated_state_filter_total_from_by_state():
    dms = FakeDms(data_jobs=[_job(i) for i in range(5)],
                  summary={"total": 100, "by_state": {"Running": 42}})
    body = make_client(dms).get(f"{DASH}/requests?limit=2&state=Running").json()
    assert body["truncated"] is True
    assert body["total"] == 42


def test_data_jobs_truncated_storage_filter_total_unknown():
    dms = FakeDms(data_jobs=[_job(i) for i in range(5)], summary={"total": 100})
    body = make_client(dms).get(f"{DASH}/requests?limit=2&storage_name=x").json()
    assert body["truncated"] is True
    assert body["total"] is None              # not derivable from marginals


# --- node health (latest_per_node) ------------------------------------------

def test_summary_uses_latest_per_node_reports():
    reports = [
        {"cluster_name": "c", "node_name": "n1", "worker_role": "DM",
         "freshness_status": "Fresh", "reported_at": "2026-01-02"},
        {"cluster_name": "c", "node_name": "n2", "worker_role": "DM",
         "freshness_status": "Stale", "reported_at": "2026-01-02"},
    ]
    dms = FakeDms(reports=reports, summary={"total": 0})
    body = make_client(dms).get(f"{DASH}/summary").json()
    assert body["nodes"]["data"]["fresh"] == 1
    assert body["nodes"]["data"]["stale"] == 1
    assert body["nodes"]["data"]["by_role"] == {"DM": {"fresh": 1, "stale": 1}}
    # node health requested as one-row-per-node (no history scan)
    assert any(name == "list_agent_reports" and kw["latest_per_node"] is True
               for name, kw in dms.calls)
    # /summary must NOT fetch storage mappings: node health comes from agent reports
    # alone, and the polled path stays as cheap as possible.
    assert not any(name == "list_storage_mappings" for name, _ in dms.calls)


def test_nodes_endpoint_requests_latest_per_node():
    reports = [{"cluster_name": "c", "node_name": "n1", "worker_role": "DM",
                "freshness_status": "Fresh", "reported_at": "2026-01-02", "report": {}}]
    dms = FakeDms(reports=reports)
    r = make_client(dms).get(f"{DASH}/nodes")
    assert r.status_code == 200
    assert any(name == "list_agent_reports" and kw["latest_per_node"] is True
               for name, kw in dms.calls)
