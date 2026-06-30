"""Backup orchestrator recovery: (A) execute approved items mid-preview, and
(B) auto-resolve a preview_pending whose DMS request produced no data_job.

These drive the orchestrator against in-memory fakes (no DMS, no DB) and cover the
adversarial conflict cases that left the live "테스트 배치 1" stuck: a re-submit that
DMS rejects with a resource-key 'Conflict' against a prior, still-non-terminal
request for the same path.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from portal.backend.config import Settings
from portal.backend.orchestrator import BackupOrchestrator


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OrchDB:
    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.requests: dict[int, dict[str, Any]] = {}
        self._n = 1

    def add_batch(self, bid: str, status: str) -> dict[str, Any]:
        b = {"id": bid, "status": status, "options": {}, "delete_enabled": False,
             "requester_id": "root", "created_by": "op", "priority": "Low",
             "node_count": None}
        self.batches[bid] = b
        return b

    def add_request(self, bid: str, state: str, *, updated_at: datetime | None = None,
                    **kw: Any) -> int:
        rid = self._n
        self._n += 1
        self.requests[rid] = {
            "id": rid, "batch_id": bid, "state": state,
            "src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b",
            "dms_job_id": kw.get("dms_job_id"), "dms_request_id": kw.get("dms_request_id"),
            "fingerprint": kw.get("fingerprint"), "preview": None, "result": None,
            "error": None, "updated_at": updated_at or _now(),
        }
        return rid

    async def requests_in_states(self, bid, states):
        return [dict(r) for r in self.requests.values()
                if r["batch_id"] == bid and r["state"] in states]

    async def list_requests(self, bid, *, state=None, limit=200, offset=0):
        rs = [dict(r) for r in self.requests.values()
              if r["batch_id"] == bid and (state is None or r["state"] == state)]
        return rs[offset:offset + limit]

    async def update_request(self, rid, **fields):
        self.requests[rid].update(fields)

    async def batch_state_counts(self, bid):
        out: dict[str, int] = {}
        for r in self.requests.values():
            if r["batch_id"] == bid:
                out[r["state"]] = out.get(r["state"], 0) + 1
        return out

    async def set_batch_status(self, bid, status):
        self.batches[bid]["status"] = status

    async def release_held(self, bid):
        n = 0
        for r in self.requests.values():
            if r["batch_id"] == bid and r["state"] == "held":
                r["state"] = "registered"
                n += 1
        return n


class OrchDms:
    """Configurable DMS double. ``jobs`` maps job_id->job dict (get_sync_job),
    ``data_jobs`` is the list_data_jobs result (request_id->job resolution), and
    ``requests`` maps request_id->request-history dict (get_request, for B)."""

    def __init__(self, *, jobs=None, data_jobs=None, requests=None) -> None:
        self.jobs = jobs or {}
        self.data_jobs = data_jobs or []
        self.requests = requests or {}
        self.confirmed: list[str] = []
        self.submitted: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.request_calls: list[str] = []

    async def submit_sync(self, body, *, actor):
        self.submitted.append(body)
        return {"request_id": f"req-{len(self.submitted)}"}

    async def confirm_job(self, job_id, body, *, actor):
        self.confirmed.append(job_id)
        return {}

    async def cancel_job(self, job_id, *, actor):
        self.cancelled.append(job_id)
        return {}

    async def get_sync_job(self, job_id, *, actor):
        return self.jobs.get(job_id, {"state": "Running"})

    async def list_data_jobs(self, *, actor, limit=500, offset=0, operation=None,
                             state=None, storage_name=None):
        return list(self.data_jobs)

    async def get_request(self, request_id, *, actor):
        self.request_calls.append(request_id)
        return self.requests.get(request_id, {"request": {"status": "Persisted"}})


def _confirm_pending_job(job_id: str, fp: str) -> dict[str, Any]:
    return {
        "job_id": job_id, "state": "ConfirmPending",
        "result_summary": {"preview": {"fingerprint": fp, "summary": {
            "file_count": 3, "directory_count": 1, "total_bytes": 9, "error_count": 0,
            "selected_tool": "dsync"}}},
    }


# --- (A) execute approved items while OTHER items are still previewing -------

def test_preview_phase_confirms_approved_and_stays_previewing():
    async def go():
        db = OrchDB()
        batch = db.add_batch("b1", "previewing")
        r_app = db.add_request("b1", "approved", dms_job_id="j1", fingerprint="fp1")
        # sibling still previewing: request persisted, no data_job yet, fresh.
        r_pend = db.add_request("b1", "preview_pending", dms_request_id="reqP")
        dms = OrchDms(jobs={"j1": {"state": "Running"}},
                      requests={"reqP": {"request": {"status": "Persisted"}}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._drive_preview(batch)
        assert dms.confirmed == ["j1"], "approved item must be confirmed mid-preview"
        assert db.requests[r_app]["state"] == "running"
        assert db.requests[r_pend]["state"] == "preview_pending"
        # still previewing (a sibling is mid-preview) -> NOT forced to running/done.
        assert db.batches["b1"]["status"] == "previewing"
    asyncio.run(go())


def test_preview_advances_to_running_when_only_approved_remain():
    async def go():
        db = OrchDB()
        batch = db.add_batch("b1", "previewing")
        db.add_request("b1", "approved", dms_job_id="j1", fingerprint="fp1")
        dms = OrchDms(jobs={"j1": {"state": "Running"}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._drive_preview(batch)
        assert dms.confirmed == ["j1"]
        # nothing registered/pending + an approved/running item -> enter execute phase
        assert db.batches["b1"]["status"] == "running"
    asyncio.run(go())


# --- (B) auto-resolve a preview_pending with no data_job ---------------------

def test_conflict_adopts_prior_confirm_pending_preview():
    """The exact live case: re-submit -> 'Conflict' (prior request for the same
    resource_key still ConfirmPending). Adopt the prior's valid preview; the normal
    poll then marks it preview_ready with the fingerprint."""
    async def go():
        db = OrchDB()
        batch = db.add_batch("b1", "previewing")
        r = db.add_request("b1", "preview_pending", dms_request_id="reqNew")
        dms = OrchDms(
            jobs={"jOld": _confirm_pending_job("jOld", "fpOld")},
            data_jobs=[{"request_id": "reqOld", "job_id": "jOld", "state": "ConfirmPending"}],
            requests={"reqNew": {"request": {"status": "Conflict"}, "results": [
                {"terminal_status": "Conflict",
                 "verification_summary": {"prior_request_id": "reqOld"}}]}},
        )
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._drive_preview(batch)
        row = db.requests[r]
        assert row["state"] == "preview_ready"
        assert row["dms_job_id"] == "jOld"
        assert row["dms_request_id"] == "reqOld"
        assert row["fingerprint"] == "fpOld"
    asyncio.run(go())


def test_conflict_reregisters_when_prior_is_terminal():
    """Blocker gone (prior preview cancelled, e.g. by C on re-preview) -> drop the
    conflicted request and re-register for a fresh submit."""
    async def go():
        db = OrchDB()
        batch = db.add_batch("b1", "previewing")
        r = db.add_request("b1", "preview_pending", dms_request_id="reqNew")
        dms = OrchDms(
            data_jobs=[{"request_id": "reqOld", "job_id": "jOld", "state": "Cancelled"}],
            requests={"reqNew": {"request": {"status": "Conflict"}, "results": [
                {"terminal_status": "Conflict",
                 "verification_summary": {"prior_request_id": "reqOld"}}]}},
        )
        orch = BackupOrchestrator(db, dms, Settings())
        # resolve in isolation (don't let the same cycle re-submit and mutate state)
        await orch._resolve_stuck_preview(dict(db.requests[r]), "mtls:op",
                                          {"reqOld": dms.data_jobs[0]})
        row = db.requests[r]
        assert row["state"] == "registered"
        assert row["dms_request_id"] is None
        assert row["dms_job_id"] is None
    asyncio.run(go())


def test_conflict_waits_while_prior_preview_still_running():
    """Adversarial: prior request exists but its preview is still Running (not yet
    ConfirmPending) -> wait (no premature fail), within the timeout."""
    async def go():
        db = OrchDB()
        db.add_batch("b1", "previewing")
        r = db.add_request("b1", "preview_pending", dms_request_id="reqNew")
        by_req = {"reqOld": {"request_id": "reqOld", "job_id": "jOld", "state": "Running"}}
        dms = OrchDms(requests={"reqNew": {"request": {"status": "Conflict"}, "results": [
            {"terminal_status": "Conflict",
             "verification_summary": {"prior_request_id": "reqOld"}}]}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._resolve_stuck_preview(dict(db.requests[r]), "mtls:op", by_req)
        assert db.requests[r]["state"] == "preview_pending"  # unchanged, still waiting
    asyncio.run(go())


def test_terminal_request_without_job_fails_preview():
    async def go():
        db = OrchDB()
        db.add_batch("b1", "previewing")
        r = db.add_request("b1", "preview_pending", dms_request_id="reqNew")
        dms = OrchDms(requests={"reqNew": {"request": {"status": "Failed"}}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._resolve_stuck_preview(dict(db.requests[r]), "mtls:op", {})
        assert db.requests[r]["state"] == "preview_failed"
        assert "Failed" in (db.requests[r]["error"] or "")
    asyncio.run(go())


def test_stuck_pending_times_out():
    async def go():
        db = OrchDB()
        db.add_batch("b1", "previewing")
        old = _now() - timedelta(seconds=1000)  # > default 900s
        r = db.add_request("b1", "preview_pending", dms_request_id="reqNew", updated_at=old)
        # non-terminal request, never produced a job
        dms = OrchDms(requests={"reqNew": {"request": {"status": "Planned"}}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._resolve_stuck_preview(dict(db.requests[r]), "mtls:op", {})
        assert db.requests[r]["state"] == "preview_failed"
        assert "timeout" in (db.requests[r]["error"] or "")
    asyncio.run(go())


def test_young_pending_without_job_is_left_alone():
    async def go():
        db = OrchDB()
        db.add_batch("b1", "previewing")
        r = db.add_request("b1", "preview_pending", dms_request_id="reqNew")  # fresh
        dms = OrchDms(requests={"reqNew": {"request": {"status": "Persisted"}}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._resolve_stuck_preview(dict(db.requests[r]), "mtls:op", {})
        assert db.requests[r]["state"] == "preview_pending"  # transient -> patient
    asyncio.run(go())


def test_conflict_no_prior_job_waits_then_times_out():
    """Conflict but the prior request has no data_job yet (by_req empty): wait while
    young, fail once past the timeout."""
    async def go():
        db = OrchDB()
        db.add_batch("b1", "previewing")
        old = _now() - timedelta(seconds=1000)
        r = db.add_request("b1", "preview_pending", dms_request_id="reqNew", updated_at=old)
        dms = OrchDms(requests={"reqNew": {"request": {"status": "Conflict"}, "results": [
            {"terminal_status": "Conflict",
             "verification_summary": {"prior_request_id": "reqOld"}}]}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._resolve_stuck_preview(dict(db.requests[r]), "mtls:op", {})
        # prior job unknown + past timeout -> fail rather than wait forever
        assert db.requests[r]["state"] == "preview_failed"
    asyncio.run(go())
