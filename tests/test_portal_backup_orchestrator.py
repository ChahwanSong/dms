"""Portal backup orchestrator — Phase 2 execute-phase behavior.

Drives `_drive_execute` against in-memory fakes to assert the selective-approval
contract: only 'approved' requests are confirmed, and the batch returns to
'previewed' when undecided preview_ready remain (else 'done').
"""

from __future__ import annotations

import asyncio
from typing import Any

from portal.backend.config import Settings
from portal.backend.orchestrator import BackupOrchestrator


class OrchDB:
    def __init__(self) -> None:
        self.batches: dict[str, dict[str, Any]] = {}
        self.requests: dict[int, dict[str, Any]] = {}
        self._n = 1

    def add_batch(self, bid: str, status: str) -> dict[str, Any]:
        b = {"id": bid, "status": status, "options": {}, "delete_enabled": False,
             "requester_id": "root", "created_by": "op"}
        self.batches[bid] = b
        return b

    def add_request(self, bid: str, state: str, **kw: Any) -> int:
        rid = self._n
        self._n += 1
        self.requests[rid] = {"id": rid, "batch_id": bid, "state": state,
                              "src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b",
                              "dms_job_id": kw.get("dms_job_id"), "dms_request_id": kw.get("dms_request_id"),
                              "fingerprint": kw.get("fingerprint"), "preview": None, "result": None, "error": None}
        return rid

    async def requests_in_states(self, bid: str, states: list[str]) -> list[dict[str, Any]]:
        return [dict(r) for r in self.requests.values() if r["batch_id"] == bid and r["state"] in states]

    async def list_requests(self, bid: str, *, state: str | None = None, limit: int = 200, offset: int = 0):
        rs = [dict(r) for r in self.requests.values()
              if r["batch_id"] == bid and (state is None or r["state"] == state)]
        return rs[offset:offset + limit]

    async def update_request(self, rid: int, **fields: Any) -> None:
        self.requests[rid].update(fields)

    async def batch_state_counts(self, bid: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.requests.values():
            if r["batch_id"] == bid:
                out[r["state"]] = out.get(r["state"], 0) + 1
        return out

    async def set_batch_status(self, bid: str, status: str) -> None:
        self.batches[bid]["status"] = status


class OrchDms:
    def __init__(self, jobs: dict[str, dict[str, Any]]) -> None:
        self.jobs = jobs
        self.confirmed: list[str] = []

    async def confirm_job(self, job_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        self.confirmed.append(job_id)
        return {}

    async def get_sync_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        return self.jobs.get(job_id, {"state": "Running"})


def test_execute_confirms_only_approved_then_done():
    async def go():
        db = OrchDB()
        batch = db.add_batch("b1", "running")
        r1 = db.add_request("b1", "approved", dms_job_id="j1", fingerprint="fp1")
        dms = OrchDms({"j1": {"state": "Succeeded", "result_summary": {"execution": {"ok": True}}}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._drive_execute(batch)
        assert dms.confirmed == ["j1"]
        assert db.requests[r1]["state"] == "succeeded"
        assert db.batches["b1"]["status"] == "done"
    asyncio.run(go())


def test_execute_returns_to_previewed_when_undecided_remain():
    async def go():
        db = OrchDB()
        batch = db.add_batch("b1", "running")
        r1 = db.add_request("b1", "approved", dms_job_id="j1", fingerprint="fp1")
        r2 = db.add_request("b1", "preview_ready")  # undecided
        dms = OrchDms({"j1": {"state": "Succeeded", "result_summary": {"execution": {}}}})
        orch = BackupOrchestrator(db, dms, Settings())
        await orch._drive_execute(batch)
        assert dms.confirmed == ["j1"]              # preview_ready never confirmed
        assert db.requests[r1]["state"] == "succeeded"
        assert db.requests[r2]["state"] == "preview_ready"
        assert db.batches["b1"]["status"] == "previewed"
    asyncio.run(go())
