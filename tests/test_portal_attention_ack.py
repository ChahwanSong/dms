"""Portal BFF 조치 필요 ACK ↔ DMS server-side wiring.

The '확인(처리완료)' close-out delegates to the DMS server-side ack (record-preserving,
cross-client): action_required() then excludes it for every client. '숨김' stays a
portal-local view preference and never reaches DMS. The 처리 내역 list MERGES the
portal mirror rows with the DMS ack list so an ack is always reflected — even one
made by another client. These hit the real dashboard router via TestClient against
an in-memory FakeDms + FakeDb.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from portal.backend import deps, security
from portal.backend.config import Settings
from portal.backend.routers.dashboard import dashboard_router

DASH = "/api/operator/dashboard"


class FakeDms:
    """Records ack/unack calls; keeps an in-memory server-side ack set that ack/unack
    mutate and list_action_acks / list_action_required read (so cross-client
    suppression is exercised end to end)."""

    def __init__(
        self,
        *,
        action_required: list[dict[str, Any]] | None = None,
        acks: list[dict[str, Any]] | None = None,
    ) -> None:
        self._items = action_required or []
        self.acks: dict[str, dict[str, Any]] = {a["fingerprint"]: a for a in (acks or [])}
        self.calls: list[tuple[str, Any]] = []

    async def list_action_required(self, *, actor: str):
        # DMS excludes server-side acked fingerprints for ALL clients.
        return [i for i in self._items if i.get("fingerprint") not in self.acks]

    async def ack_action_required(self, items, *, actor: str):
        self.calls.append(("ack", items))
        for it in items:
            self.acks[it["fingerprint"]] = {
                "fingerprint": it["fingerprint"],
                "issue_type": it.get("issue_type"),
                "reason": it.get("reason"),
                "acked_by": actor,
                "acked_at": "2026-07-01T00:00:00Z",
            }
        return {"acked": len(items)}

    async def unack_action_required(self, fingerprints, *, actor: str):
        self.calls.append(("unack", fingerprints))
        n = 0
        for f in fingerprints:
            if self.acks.pop(f, None) is not None:
                n += 1
        return {"unacked": n}

    async def list_action_acks(self, *, actor: str):
        return list(self.acks.values())

    def acked(self, kind: str) -> bool:
        return any(name == kind for name, _ in self.calls)


class FakeDb:
    """In-memory attention_dismissals mirror (fingerprint -> record)."""

    configured = True

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def list_dismissals(self, *, limit: int = 1000):
        return [dict(r) for r in self.rows.values() if not r.get("archived")][:limit]

    async def dismissed_fingerprints(self, subset=None):
        fps = set(self.rows.keys())
        return fps & set(subset) if subset is not None else fps

    async def add_dismissals(self, items, dismissed_by):
        for i in items:
            fp = i["fingerprint"]
            prev = self.rows.get(fp, {})
            self.rows[fp] = {
                **i,
                "dismissed_by": dismissed_by,
                "archived": prev.get("archived", False),
            }
        return len(items)

    async def remove_dismissals(self, fingerprints):
        n = 0
        for f in fingerprints:
            if self.rows.pop(f, None) is not None:
                n += 1
        return n

    async def archive_dismissals(self, fingerprints, *, archived_by="operator"):
        for f in fingerprints:
            if f in self.rows:
                self.rows[f]["archived"] = True
            else:
                self.rows[f] = {
                    "fingerprint": f, "kind": "ack",
                    "archived": True, "dismissed_by": archived_by,
                }
        return len(fingerprints)


def make_client(dms: FakeDms, db: FakeDb) -> TestClient:
    app = FastAPI()
    app.state.db = db  # the /attention GET reads request.app.state.db directly
    app.include_router(dashboard_router(Settings()))
    app.dependency_overrides[deps.get_dms_client] = lambda: dms
    app.dependency_overrides[deps.get_db] = lambda: db
    app.dependency_overrides[security.require_authenticated] = lambda: {
        "username": "op", "role": "operator", "method": "local",
    }
    return TestClient(app)


def _ack_item(fp="request_attention|r1", issue="request_attention", reason=None):
    return {"fingerprint": fp, "issue_type": issue, "reason": reason, "kind": "ack"}


# --- ack delegates to DMS + mirrors locally ---------------------------------

def test_ack_delegates_to_dms_and_mirrors_once():
    dms = FakeDms()
    db = FakeDb()
    c = make_client(dms, db)

    r = c.post(f"{DASH}/attention/dismiss", json={"items": [_ack_item(reason="cleaned")]})
    assert r.status_code == 200

    # forwarded to DMS server-side ack with fingerprint/issue_type/reason
    assert dms.acked("ack")
    sent = [items for name, items in dms.calls if name == "ack"][0]
    assert sent[0]["fingerprint"] == "request_attention|r1"
    assert sent[0]["reason"] == "cleaned"
    assert "request_attention|r1" in dms.acks

    # portal mirror stored (kind ack) + 처리 내역 shows it exactly once (not doubled
    # by the DMS merge, since the fingerprint is tracked locally).
    listed = c.get(f"{DASH}/attention/dismissed").json()
    fps = [d["fingerprint"] for d in listed]
    assert fps.count("request_attention|r1") == 1
    assert listed[0]["kind"] == "ack"
    assert listed[0]["in_dms"] is True  # reflected server-side (all clients)


def test_dismiss_stays_portal_only():
    dms = FakeDms()
    db = FakeDb()
    c = make_client(dms, db)

    item = {"fingerprint": "storage_mapping_failed|s1", "issue_type": "storage_mapping_failed",
            "kind": "dismissed"}
    r = c.post(f"{DASH}/attention/dismiss", json={"items": [item]})
    assert r.status_code == 200
    # a 숨김 is NEVER sent to DMS
    assert not dms.acked("ack")
    assert "storage_mapping_failed|s1" not in dms.acks
    # but it is mirrored locally (portal-only hide)
    assert "storage_mapping_failed|s1" in db.rows


def test_ack_then_attention_excludes_it_and_undismiss_restores():
    dms = FakeDms(action_required=[
        {"issue_type": "request_attention", "fingerprint": "request_attention|r1",
         "status": "BackendApplyFailed", "request_id": "r1"},
    ])
    db = FakeDb()
    c = make_client(dms, db)

    # ack → DMS excludes it from action_required for all clients (portal reads DMS)
    c.post(f"{DASH}/attention/dismiss", json={"items": [_ack_item()]})
    assert c.get(f"{DASH}/attention").json() == []

    # undismiss → DMS un-ack + mirror removed → item reappears, 처리 내역 empty
    r = c.post(f"{DASH}/attention/undismiss", json={"fingerprints": ["request_attention|r1"]})
    assert r.status_code == 200
    assert dms.acked("unack")
    assert "request_attention|r1" not in dms.acks
    assert db.rows == {}
    assert len(c.get(f"{DASH}/attention").json()) == 1
    assert c.get(f"{DASH}/attention/dismissed").json() == []


# --- 처리 내역 merges the DMS ack list (completeness) ------------------------

def test_dismissed_merges_dms_only_ack():
    # a server-side ack that was NOT made through this portal (no local mirror)
    dms = FakeDms(acks=[{
        "fingerprint": "data_job_failed|j9", "issue_type": "data_job_failed",
        "reason": None, "acked_by": "cli-user", "acked_at": "2026-06-30T10:00:00Z",
    }])
    db = FakeDb()
    c = make_client(dms, db)

    listed = c.get(f"{DASH}/attention/dismissed").json()
    assert len(listed) == 1
    row = listed[0]
    assert row["fingerprint"] == "data_job_failed|j9"
    assert row["kind"] == "ack"
    assert row["dismissed_by"] == "cli-user"   # synthesized from the DMS ack
    assert row["item_at"] == "2026-06-30T10:00:00Z"
    assert row["in_dms"] is True


def test_legacy_portal_only_ack_marked_not_in_dms():
    """An ack recorded before the server-side wiring (mirror only, no DMS ack) is
    reported in_dms=False so the UI can label it '확인됨 · 로컬', not '· 전체'."""
    dms = FakeDms()
    db = FakeDb()
    db.rows["filesystem_soft_deleted|d1"] = {
        "fingerprint": "filesystem_soft_deleted|d1",
        "issue_type": "filesystem_soft_deleted", "kind": "ack", "archived": False,
    }
    c = make_client(dms, db)
    listed = c.get(f"{DASH}/attention/dismissed").json()
    assert len(listed) == 1
    assert listed[0]["kind"] == "ack"
    assert listed[0]["in_dms"] is False


def test_dismissed_does_not_duplicate_or_resurrect_archived():
    dms = FakeDms(acks=[{
        "fingerprint": "data_job_failed|j9", "issue_type": "data_job_failed",
        "reason": None, "acked_by": "cli-user", "acked_at": "2026-06-30T10:00:00Z",
    }])
    db = FakeDb()
    # locally archived ('이전 정리') — must NOT be re-synthesized by the DMS merge
    db.rows["data_job_failed|j9"] = {
        "fingerprint": "data_job_failed|j9", "kind": "ack", "archived": True,
    }
    c = make_client(dms, db)
    assert c.get(f"{DASH}/attention/dismissed").json() == []


# --- archive keeps the server-side ack in effect ----------------------------

def test_archive_keeps_dms_ack_and_hides_from_list():
    dms = FakeDms()
    db = FakeDb()
    c = make_client(dms, db)

    c.post(f"{DASH}/attention/dismiss", json={"items": [_ack_item()]})
    assert "request_attention|r1" in dms.acks

    # 이전 정리: archive → dropped from 처리 내역 but the DMS ack stays (still suppressed)
    r = c.post(f"{DASH}/attention/archive", json={"fingerprints": ["request_attention|r1"]})
    assert r.status_code == 200
    assert "request_attention|r1" in dms.acks          # NOT un-acked
    assert db.rows["request_attention|r1"]["archived"] is True
    assert c.get(f"{DASH}/attention/dismissed").json() == []  # gone from the list
