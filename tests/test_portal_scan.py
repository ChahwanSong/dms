"""Portal data-scan orchestrator + router validation.

Scan is READ-ONLY and has no preview/confirm: a batch goes draft -> scanning ->
done via a single :run. These drive `ScanOrchestrator._drive_scan` / `_submit_one`
against in-memory fakes (mirroring the backup-orchestrator tests) to assert the
single-phase contract: registered -> submitted (running) -> reconcile job_id ->
poll to terminal -> batch done (+ release held). Also covers `scan_body` (target,
not source/dest; options whitelisted) and the router's option validation.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from portal.backend.config import Settings
from portal.backend.routers.scan import _validate_options
from portal.backend.scan_orchestrator import ScanOrchestrator, _scan_result, scan_body


class ScanDB:
    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.requests: dict[int, dict[str, Any]] = {}
        self._n = 1

    def add_batch(
        self,
        bid: str,
        status: str,
        priority: str = "Low",
        node_count=None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        b = {"id": bid, "status": status, "options": options or {},
             "requester_id": "root", "created_by": "op", "priority": priority,
             "node_count": node_count}
        self.batches[bid] = b
        return b

    def add_request(self, bid: str, state: str, **kw: Any) -> int:
        rid = self._n
        self._n += 1
        self.requests[rid] = {"id": rid, "batch_id": bid, "state": state,
                              "storage": kw.get("storage", "cephfs-dms"),
                              "path": kw.get("path", "e2e/src"),
                              "dms_job_id": kw.get("dms_job_id"),
                              "dms_request_id": kw.get("dms_request_id"),
                              "result": None, "error": None}
        return rid

    async def scan_requests_in_states(self, bid: str, states: list[str]) -> list[dict[str, Any]]:
        return [dict(r) for r in self.requests.values()
                if r["batch_id"] == bid and r["state"] in states]

    async def list_scan_requests(self, bid: str, *, state: str | None = None,
                                 limit: int = 200, offset: int = 0):
        rs = [dict(r) for r in self.requests.values()
              if r["batch_id"] == bid and (state is None or r["state"] == state)]
        return rs[offset:offset + limit]

    async def update_scan_request(self, rid: int, **fields: Any) -> None:
        self.requests[rid].update(fields)

    async def scan_batch_state_counts(self, bid: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.requests.values():
            if r["batch_id"] == bid:
                out[r["state"]] = out.get(r["state"], 0) + 1
        return out

    async def set_scan_batch_status(self, bid: str, status: str) -> None:
        self.batches[bid]["status"] = status

    async def release_held_scan(self, bid: str) -> int:
        n = 0
        for r in self.requests.values():
            if r["batch_id"] == bid and r["state"] == "held":
                r["state"] = "registered"
                n += 1
        return n


class ScanDms:
    def __init__(self, jobs: dict[str, dict[str, Any]] | None = None,
                 by_req: list[dict[str, Any]] | None = None) -> None:
        self.jobs = jobs or {}
        self.by_req = by_req or []
        self.submitted: list[dict[str, Any]] = []
        self.cancelled: list[str] = []

    async def submit_scan(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        self.submitted.append(body)
        return {"request_id": "req-1"}

    async def get_scan_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        return self.jobs.get(job_id, {"state": "Running"})

    async def list_data_jobs(self, *, actor: str, limit: int = 500, operation=None):
        return self.by_req

    async def cancel_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        self.cancelled.append(job_id)
        return {}


# --- scan_body (DMS DataJobRequest shaping) --------------------------------

def test_scan_body_targets_path_no_source_dest():
    batch = {"requester_id": "root", "options": {}}
    req = {"storage": "cephfs-dms", "path": "e2e/src"}
    body = scan_body(batch, req)
    assert body["target"] == {"storage_name": "cephfs-dms", "path": "e2e/src"}
    assert "source" not in body and "destination" not in body
    assert body["requester_id"] == "root"


def test_scan_body_filters_options_to_scan_keys():
    batch = {"requester_id": "root",
             "options": {"max_depth": 3, "follow_symlinks": True,
                         "delete": True, "chown": "1:1", "bogus": 1}}
    body = scan_body(batch, {"storage": "s", "path": "p"})
    assert body["options"] == {"max_depth": 3, "follow_symlinks": True}


def test_scan_body_omits_empty_options():
    body = scan_body({"requester_id": "root", "options": {}}, {"storage": "s", "path": "p"})
    assert "options" not in body


# --- _submit_one (priority / resources injection) --------------------------

def test_submit_sets_low_priority_by_default():
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning")
        rid = db.add_request("b1", "registered")
        dms = ScanDms()
        orch = ScanOrchestrator(db, dms, Settings())
        await orch._submit_one(batch, db.requests[rid], "mtls:op")
        assert dms.submitted and dms.submitted[0]["priority"] == "Low"
        assert db.requests[rid]["state"] == "running"
        assert db.requests[rid]["dms_request_id"] == "req-1"
    asyncio.run(go())


def test_submit_uses_per_batch_priority():
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning", priority="High")
        rid = db.add_request("b1", "registered")
        dms = ScanDms()
        await ScanOrchestrator(db, dms, Settings())._submit_one(batch, db.requests[rid], "mtls:op")
        assert dms.submitted[0]["priority"] == "High"
    asyncio.run(go())


def test_submit_includes_node_count_resources():
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning", node_count=2)
        rid = db.add_request("b1", "registered")
        dms = ScanDms()
        await ScanOrchestrator(db, dms, Settings())._submit_one(batch, db.requests[rid], "mtls:op")
        assert dms.submitted[0].get("resources") == {"node_count": 2}
    asyncio.run(go())


def test_submit_omits_resources_when_node_count_auto():
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning")
        rid = db.add_request("b1", "registered")
        dms = ScanDms()
        await ScanOrchestrator(db, dms, Settings())._submit_one(batch, db.requests[rid], "mtls:op")
        assert "resources" not in dms.submitted[0]
    asyncio.run(go())


# --- _drive_scan (single-phase lifecycle) ----------------------------------

def test_drive_scan_submits_registered():
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning")
        rid = db.add_request("b1", "registered")
        dms = ScanDms()  # all jobs poll as Running
        await ScanOrchestrator(db, dms, Settings())._drive_scan(batch)
        assert dms.submitted, "submit_scan not called for registered request"
        assert dms.submitted[0]["target"]["path"] == "e2e/src"
        assert db.requests[rid]["state"] == "running"
        assert db.batches["b1"]["status"] == "scanning"  # not done while running
    asyncio.run(go())


def test_drive_scan_reconciles_request_to_job_id():
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning")
        rid = db.add_request("b1", "running", dms_request_id="req-1")
        dms = ScanDms(by_req=[{"request_id": "req-1", "job_id": "job-x"}])
        await ScanOrchestrator(db, dms, Settings())._drive_scan(batch)
        assert db.requests[rid]["dms_job_id"] == "job-x"
    asyncio.run(go())


def test_drive_scan_succeeds_captures_result_and_done():
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning")
        rid = db.add_request("b1", "running", dms_job_id="j1")
        summary = {"file_count": 100, "directory_count": 26, "total_bytes": 3700,
                   "error_count": 0, "scan_root": "dms/e2e/src", "selected_tool": "dscan"}
        dms = ScanDms({"j1": {"state": "Succeeded", "result_summary": {"summary": summary}}})
        await ScanOrchestrator(db, dms, Settings())._drive_scan(batch)
        r = db.requests[rid]
        assert r["state"] == "succeeded"
        assert r["result"]["file_count"] == 100
        assert r["result"]["total_bytes"] == 3700
        assert r["result"]["tool"] == "dscan"
        assert db.batches["b1"]["status"] == "done"
    asyncio.run(go())


def test_drive_scan_failed_records_reason():
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning")
        rid = db.add_request("b1", "running", dms_job_id="j1")
        dms = ScanDms({"j1": {"state": "PreflightFailed",
                              "preflight_result": {"reason": "source_not_found"}}})
        await ScanOrchestrator(db, dms, Settings())._drive_scan(batch)
        assert db.requests[rid]["state"] == "failed"
        assert db.requests[rid]["error"] == "source_not_found"
        assert db.batches["b1"]["status"] == "done"
    asyncio.run(go())


def test_drive_scan_done_releases_held():
    """Selective run: once the selected request finishes, the batch advances to
    done and parked 'held' requests return to 'registered' for a later run."""
    async def go():
        db = ScanDB()
        batch = db.add_batch("b1", "scanning")
        r_done = db.add_request("b1", "running", dms_job_id="j1")
        r_held = db.add_request("b1", "held")
        dms = ScanDms({"j1": {"state": "Succeeded", "result_summary": {"summary": {}}}})
        await ScanOrchestrator(db, dms, Settings())._drive_scan(batch)
        assert db.batches["b1"]["status"] == "done"
        assert db.requests[r_held]["state"] == "registered"
        assert db.requests[r_done]["state"] == "succeeded"
    asyncio.run(go())


def test_scan_result_extracts_summary_fields():
    dj = {"selected_tool": "dscan",
          "result_summary": {"summary": {"file_count": 5, "directory_count": 2,
                                         "total_bytes": 99, "error_count": 1,
                                         "scan_root": "a/b"}}}
    out = _scan_result(dj)
    assert out == {"file_count": 5, "directory_count": 2, "total_bytes": 99,
                   "error_count": 1, "scan_root": "a/b", "tool": "dscan",
                   "atime_histogram": None}


def test_scan_result_captures_atime_histogram():
    hist = [
        {"bucket": "[0d,1d]", "min_age_days": 0, "max_age_days": 1, "bytes": 30720},
        {"bucket": "[31d,90d]", "min_age_days": 31, "max_age_days": 90, "bytes": 512},
    ]
    dj = {"selected_tool": "dscan",
          "result_summary": {"summary": {"file_count": 4, "directory_count": 1,
                                         "total_bytes": 10, "error_count": 0,
                                         "scan_root": "a", "atime_histogram": hist}}}
    out = _scan_result(dj)
    assert out["atime_histogram"] == hist


# --- router option validation ----------------------------------------------

def test_validate_options_keeps_only_scan_keys():
    out = _validate_options({"max_depth": 3, "follow_symlinks": True,
                             "one_file_system": False, "summary_only": True,
                             "delete": True, "unknown": 1})
    assert out == {"max_depth": 3, "follow_symlinks": True,
                   "one_file_system": False, "summary_only": True}


def test_validate_options_rejects_negative_max_depth():
    with pytest.raises(HTTPException) as exc:
        _validate_options({"max_depth": -1})
    assert exc.value.status_code == 422


def test_validate_options_rejects_bool_max_depth():
    # bool is a subclass of int; max_depth must be a real int.
    with pytest.raises(HTTPException) as exc:
        _validate_options({"max_depth": True})
    assert exc.value.status_code == 422


def test_validate_options_rejects_non_bool_flag():
    with pytest.raises(HTTPException) as exc:
        _validate_options({"follow_symlinks": "yes"})
    assert exc.value.status_code == 422
