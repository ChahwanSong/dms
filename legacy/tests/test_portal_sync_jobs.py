"""Portal data-sync (데이터 Sync 탭) orchestrator + validation.

One-shot data.sync jobs go through preview -> 승인(confirm) -> execute, driven by
SyncOrchestrator. These drive its methods against in-memory fakes (mirroring the
backup/scan orchestrator tests) to assert the single-shot contract:
  registered -> submit -> preview_pending -> (poll) preview_ready(+fingerprint)
  -> (approve) confirm -> running -> (poll) succeeded/failed.
Also covers `sync_body` (delete_enabled -> options.delete), the concurrency cap,
the no-job preview timeout backstop, and the router path validator.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from portal.backend.config import Settings
from portal.backend.dms_client import DmsApiError
from portal.backend.routers.syncjob import _clean_rel
from portal.backend.sync_orchestrator import SyncOrchestrator, sync_body


class SyncDB:
    def __init__(self) -> None:
        self.jobs: dict[int, dict[str, Any]] = {}
        self._n = 1

    def add_job(self, state: str, **kw: Any) -> int:
        jid = self._n
        self._n += 1
        self.jobs[jid] = {
            "id": jid, "state": state,
            "src_storage": kw.get("src_storage", "cephfs-dms"),
            "src_path": kw.get("src_path", "e2e/src"),
            "dst_storage": kw.get("dst_storage", "cephfs-dms"),
            "dst_path": kw.get("dst_path", "e2e/dst"),
            "requester_id": kw.get("requester_id", "root"),
            "owner_username": kw.get("owner_username"),
            "options": kw.get("options", {}),
            "delete_enabled": kw.get("delete_enabled", False),
            "priority": kw.get("priority", "Low"),
            "node_count": kw.get("node_count"),
            "approved": kw.get("approved", False),
            "dms_request_id": kw.get("dms_request_id"),
            "dms_job_id": kw.get("dms_job_id"),
            "fingerprint": kw.get("fingerprint"),
            "preview": None, "result": None, "error": None,
            "created_by": kw.get("created_by", "op"),
            "updated_at": kw.get("updated_at"),
        }
        return jid

    async def sync_jobs_in_states(self, states: list[str]) -> list[dict[str, Any]]:
        return [dict(j) for j in self.jobs.values() if j["state"] in states]

    async def update_sync_job(self, job_id: int, **fields: Any) -> None:
        self.jobs[job_id].update(fields)


class SyncDms:
    def __init__(self, poll: dict[str, dict[str, Any]] | None = None,
                 by_request: list[dict[str, Any]] | None = None) -> None:
        self.poll = poll or {}
        self.by_request = by_request or []
        self.submitted: list[dict[str, Any]] = []
        self.confirmed: list[tuple[str, dict[str, Any]]] = []
        self._submit_error: DmsApiError | None = None

    def fail_submit(self, exc: DmsApiError) -> None:
        self._submit_error = exc

    async def submit_sync(self, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        if self._submit_error is not None:
            raise self._submit_error
        self.submitted.append(body)
        return {"request_id": f"req-{len(self.submitted)}"}

    async def confirm_job(self, job_id: str, body: dict[str, Any], *, actor: str) -> dict[str, Any]:
        self.confirmed.append((job_id, body))
        return {}

    async def get_sync_job(self, job_id: str, *, actor: str) -> dict[str, Any]:
        return self.poll.get(job_id, {"state": "Running"})

    async def list_data_jobs(self, *, actor: str, limit: int = 500, operation: str | None = None):
        return self.by_request


def _orch(db: SyncDB, dms: SyncDms) -> SyncOrchestrator:
    return SyncOrchestrator(db, dms, Settings())


# --- sync_body ---------------------------------------------------------

def test_sync_body_source_dest_and_defaults():
    body = sync_body({
        "src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b",
        "requester_id": "root", "options": {}, "delete_enabled": False,
    })
    assert body["source"] == {"storage_name": "s", "path": "a"}
    assert body["destination"] == {"storage_name": "d", "path": "b"}
    assert body["requester_id"] == "root"
    assert "options" not in body  # empty options omitted
    assert "owner_username" not in body


def test_sync_body_delete_enabled_folds_into_options():
    body = sync_body({
        "src_storage": "s", "src_path": "a", "dst_storage": "d", "dst_path": "b",
        "options": {"contents": True}, "delete_enabled": True,
    })
    assert body["options"]["delete"] is True
    assert body["options"]["contents"] is True  # existing options preserved


# --- submit ------------------------------------------------------------

def test_submit_sets_preview_pending_with_request_id():
    async def go():
        db, dms = SyncDB(), SyncDms()
        jid = db.add_job("registered")
        await _orch(db, dms)._submit_one(db.jobs[jid])
        assert dms.submitted and dms.submitted[0]["priority"] == "Low"
        assert db.jobs[jid]["state"] == "preview_pending"
        assert db.jobs[jid]["dms_request_id"] == "req-1"
    asyncio.run(go())


def test_submit_per_job_priority_and_node_count():
    async def go():
        db, dms = SyncDB(), SyncDms()
        jid = db.add_job("registered", priority="High", node_count=3)
        await _orch(db, dms)._submit_one(db.jobs[jid])
        assert dms.submitted[0]["priority"] == "High"
        assert dms.submitted[0]["resources"] == {"node_count": 3}
    asyncio.run(go())


def test_submit_omits_resources_when_node_count_auto():
    async def go():
        db, dms = SyncDB(), SyncDms()
        jid = db.add_job("registered")  # node_count None
        await _orch(db, dms)._submit_one(db.jobs[jid])
        assert "resources" not in dms.submitted[0]
    asyncio.run(go())


def test_submit_failure_marks_preview_failed():
    async def go():
        db, dms = SyncDB(), SyncDms()
        dms.fail_submit(DmsApiError(422, "bad path"))
        jid = db.add_job("registered")
        await _orch(db, dms)._submit_one(db.jobs[jid])
        assert db.jobs[jid]["state"] == "preview_failed"
        assert "bad path" in str(db.jobs[jid]["error"])
    asyncio.run(go())


# --- preview poll ------------------------------------------------------

def test_preview_ready_captures_fingerprint_and_metrics():
    async def go():
        db = SyncDB()
        dms = SyncDms(poll={"j1": {
            "state": "ConfirmPending",
            "result_summary": {"preview": {
                "fingerprint": "fp-x",
                "summary": {"file_count": 12, "total_bytes": 345, "selected_tool": "dsync"},
            }},
        }})
        jid = db.add_job("preview_pending", dms_job_id="j1")
        await _orch(db, dms)._poll_preview(db.jobs[jid])
        j = db.jobs[jid]
        assert j["state"] == "preview_ready"
        assert j["fingerprint"] == "fp-x"
        assert j["preview"]["files"] == 12 and j["preview"]["bytes"] == 345
    asyncio.run(go())


def test_preview_failed_state_marks_preview_failed():
    async def go():
        db = SyncDB()
        dms = SyncDms(poll={"j1": {"state": "PreflightFailed",
                                   "preflight_result": {"reason": "target_not_found"}}})
        jid = db.add_job("preview_pending", dms_job_id="j1")
        await _orch(db, dms)._poll_preview(db.jobs[jid])
        assert db.jobs[jid]["state"] == "preview_failed"
        assert db.jobs[jid]["error"] == "target_not_found"
    asyncio.run(go())


# --- confirm / execute -------------------------------------------------

def test_confirm_requires_fingerprint():
    async def go():
        db, dms = SyncDB(), SyncDms()
        jid = db.add_job("preview_ready", dms_job_id="j1")  # no fingerprint
        await _orch(db, dms)._confirm_one(db.jobs[jid])
        assert db.jobs[jid]["state"] == "failed"
        assert not dms.confirmed
    asyncio.run(go())


def test_confirm_sends_fingerprint_and_sets_running():
    async def go():
        db, dms = SyncDB(), SyncDms()
        jid = db.add_job("preview_ready", dms_job_id="j1", fingerprint="fp-x")
        await _orch(db, dms)._confirm_one(db.jobs[jid])
        assert db.jobs[jid]["state"] == "running"
        assert dms.confirmed[0][0] == "j1"
        assert dms.confirmed[0][1]["preview_observed_hash"] == "fp-x"
        assert dms.confirmed[0][1]["confirm"] is True
    asyncio.run(go())


def test_exec_poll_succeeded_and_failed():
    async def go():
        db = SyncDB()
        dms = SyncDms(poll={
            "j1": {"state": "Succeeded", "result_summary": {"execution": {"ok": True}}},
            "j2": {"state": "Failed", "preflight_result": {"reason": "io_error"}},
        })
        j1 = db.add_job("running", dms_job_id="j1")
        j2 = db.add_job("running", dms_job_id="j2")
        orch = _orch(db, dms)
        await orch._poll_exec(db.jobs[j1])
        await orch._poll_exec(db.jobs[j2])
        assert db.jobs[j1]["state"] == "succeeded"
        assert db.jobs[j1]["result"] == {"ok": True}
        assert db.jobs[j2]["state"] == "failed" and db.jobs[j2]["error"] == "io_error"
    asyncio.run(go())


# --- full drive + cap + timeout ---------------------------------------

def test_drive_full_flow_registered_to_running():
    """registered -> submit -> preview_pending, then resolve job_id + poll ready,
    then (approved) confirm -> running in the SAME cycle after re-fetch."""
    async def go():
        db = SyncDB()
        # DMS: after submit request_id=req-1 maps to job j1 which is ConfirmPending.
        dms = SyncDms(
            poll={"j1": {"state": "ConfirmPending",
                         "result_summary": {"preview": {"fingerprint": "fp1", "summary": {}}}}},
            by_request=[{"request_id": "req-1", "job_id": "j1"}],
        )
        db.add_job("registered")
        orch = _orch(db, dms)
        await orch._drive()  # submit -> preview_pending (+request_id), resolve job, poll -> preview_ready
        job = next(iter(db.jobs.values()))
        assert job["state"] == "preview_ready" and job["fingerprint"] == "fp1"
        # operator approves; next cycle confirms -> running
        job["approved"] = True
        await orch._drive()
        assert db.jobs[job["id"]]["state"] == "running"
        assert dms.confirmed and dms.confirmed[0][0] == "j1"
    asyncio.run(go())


def test_concurrency_cap_limits_submissions():
    async def go():
        db, dms = SyncDB(), SyncDms()
        settings = Settings()
        # fill the cap with in-flight jobs, then a registered one must NOT submit.
        for _ in range(settings.backup_concurrency):
            db.add_job("running", dms_job_id="jx")
        reg = db.add_job("registered")
        orch = SyncOrchestrator(db, dms, settings)
        await orch._drive()
        assert dms.submitted == []  # no slots -> registered stays put
        assert db.jobs[reg]["state"] == "registered"
    asyncio.run(go())


def test_preview_timeout_backstop_fails_stuck_no_job():
    async def go():
        db, dms = SyncDB(), SyncDms()
        # preview_pending with a request_id but no job, aged past the timeout.
        jid = db.add_job("preview_pending", dms_request_id="req-9",
                         updated_at="2000-01-01T00:00:00+00:00")
        await _orch(db, dms)._drive()
        assert db.jobs[jid]["state"] == "preview_failed"
        assert "timeout" in db.jobs[jid]["error"]
    asyncio.run(go())


# --- router path validation -------------------------------------------

def test_clean_rel_rejects_traversal_and_empty():
    assert _clean_rel("/e2e/src/") == "e2e/src"
    for bad in ["", "   ", "../etc", "a/../b", "a/\x00b"]:
        with pytest.raises(ValueError):
            _clean_rel(bad)
