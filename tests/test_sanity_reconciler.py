"""Unit tests for the storage-mapping readiness reconciler (auto-refresh) and the
planner's DM-only staleness gate. These exercise the NEW orchestration/gate logic with
light stubs; the underlying sanity computation is the same code path already covered by
the inventory/data-management tests (no logic fork)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from dms.domain import WorkerRole
from dms.planner import Planner
from dms.sanity_reconciler import (
    readiness_is_stale,
    recompute_storage_readiness,
    reconcile_once,
)


# --------------------------------------------------------------------------- stubs
class _StubSanity:
    """Returns a canned sanity result per storage; can be told to raise for one."""

    def __init__(self, readiness_by_storage, raise_for=None):
        self._readiness = readiness_by_storage
        self._raise_for = raise_for or set()

    def check_mapping(self, mapping):
        name = mapping["storage_name"]
        if name in self._raise_for:
            raise RuntimeError(f"boom:{name}")
        return {
            "status": "Ready",
            "errors": [],
            "warnings": [],
            "readiness": self._readiness[name],
        }


class _StubRepo:
    def __init__(self, mappings):
        # mappings: {name: {"storage_name", "readiness", ...}}
        self._mappings = mappings
        self.updates: list[tuple] = []

    def list_storage_mappings(self, limit=100, *, cluster_name=None):
        return list(self._mappings.values())

    def get_storage_mapping(self, storage_name):
        return self._mappings.get(storage_name)

    def update_storage_mapping_sanity(self, storage_name, *, sanity_result, readiness, actor):
        self._mappings[storage_name]["readiness"] = readiness
        self._mappings[storage_name]["sanity_result"] = sanity_result
        self.updates.append((storage_name, readiness, actor))
        return self._mappings[storage_name]


class _StubObs:
    def __init__(self):
        self.events: list[dict] = []

    def safe_record_event(self, **kwargs):
        self.events.append(kwargs)


# --------------------------------------------------------------------- readiness_is_stale
def test_readiness_is_stale_old_fresh_and_missing():
    now = datetime.now(UTC)
    old = {"sanity_checked_at": (now - timedelta(seconds=999)).isoformat()}
    fresh = {"sanity_checked_at": now.isoformat()}
    assert readiness_is_stale(old, ttl_seconds=120, now=now) is True
    assert readiness_is_stale(fresh, ttl_seconds=120, now=now) is False
    # never checked / unparseable -> treated as stale (fail-safe)
    assert readiness_is_stale({}, ttl_seconds=120, now=now) is True
    assert readiness_is_stale({"sanity_checked_at": "not-a-date"}, ttl_seconds=120, now=now) is True


# ----------------------------------------------------------- reconcile flips & isolates
def test_reconcile_flips_missing_to_ready_and_emits_event_only_on_change():
    repo = _StubRepo(
        {
            "fs-a": {"storage_name": "fs-a", "readiness": {"data_management": "Missing"}},
            "fs-b": {"storage_name": "fs-b", "readiness": {"data_management": "Ready"}},
        }
    )
    sanity = _StubSanity(
        {
            "fs-a": {"data_management": "Ready"},  # changes -> event
            "fs-b": {"data_management": "Ready"},  # unchanged -> no event
        }
    )
    obs = _StubObs()
    changed = reconcile_once(repo, sanity, observability=obs)
    assert changed == 1
    assert repo._mappings["fs-a"]["readiness"]["data_management"] == "Ready"
    # exactly one readiness-changed event (fs-a), none for the unchanged fs-b
    changed_events = [e for e in obs.events if e["event_type"] == "storage_mapping_readiness_changed"]
    assert len(changed_events) == 1
    assert changed_events[0]["payload"]["storage_name"] == "fs-a"


def test_reconcile_isolates_per_storage_failure():
    repo = _StubRepo(
        {
            "bad": {"storage_name": "bad", "readiness": {"data_management": "Missing"}},
            "good": {"storage_name": "good", "readiness": {"data_management": "Missing"}},
        }
    )
    sanity = _StubSanity({"good": {"data_management": "Ready"}}, raise_for={"bad"})
    obs = _StubObs()
    # must not raise; "good" still processed despite "bad" blowing up
    changed = reconcile_once(repo, sanity, observability=obs)
    assert changed == 1
    assert repo._mappings["good"]["readiness"]["data_management"] == "Ready"
    assert any(e["event_type"] == "storage_mapping_readiness_reconcile_failed" for e in obs.events)


def test_reconcile_writes_heartbeat(tmp_path):
    hb = tmp_path / "heartbeat.json"
    repo = _StubRepo({"fs-a": {"storage_name": "fs-a", "readiness": {"data_management": "Ready"}}})
    sanity = _StubSanity({"fs-a": {"data_management": "Ready"}})
    reconcile_once(repo, sanity, heartbeat_path=str(hb))
    payload = json.loads(hb.read_text())
    assert payload["total"] == 1 and "ts" in payload


def test_recompute_single_storage_returns_changed():
    repo = _StubRepo({"fs-a": {"storage_name": "fs-a", "readiness": {"data_management": "Missing"}}})
    sanity = _StubSanity({"fs-a": {"data_management": "Ready"}})
    changed, readiness = recompute_storage_readiness(repo, sanity, "fs-a")
    assert changed is True
    assert readiness == {"data_management": "Ready"}
    # missing storage -> no-op
    assert recompute_storage_readiness(repo, sanity, "nope") == (False, None)


# --------------------------------------------------------------- planner DM-only gate
def _stale_mapping():
    return {
        "storage_name": "fs-a",
        "sanity_checked_at": (datetime.now(UTC) - timedelta(seconds=999)).isoformat(),
        "readiness": {"data_management": "Ready", "resource_management": "Ready"},
    }


def test_planner_gate_disabled_by_default():
    planner = Planner(repository=None)  # sanity_ttl_seconds None -> gate off
    assert planner._dm_readiness_is_stale(WorkerRole.DM, _stale_mapping()) is False


def test_planner_gate_dm_only_when_enabled():
    planner = Planner(repository=None, sanity_ttl_seconds=120)
    # DM + stale -> gated
    assert planner._dm_readiness_is_stale(WorkerRole.DM, _stale_mapping()) is True
    # RM is never gated (RM behaviour unchanged)
    assert planner._dm_readiness_is_stale(WorkerRole.RM, _stale_mapping()) is False
    # DM but fresh -> not gated
    fresh = dict(_stale_mapping(), sanity_checked_at=datetime.now(UTC).isoformat())
    assert planner._dm_readiness_is_stale(WorkerRole.DM, fresh) is False
