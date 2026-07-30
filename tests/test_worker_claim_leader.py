"""SKIP LOCKED batch claim + leader-only recovery sweeps (multi-worker scaling).

- ``claim_next_plan`` lets N workers each atomically grab a DISTINCT oldest claimable
  plan (``FOR UPDATE SKIP LOCKED`` on PostgreSQL) instead of all contending for the
  same one (the old ``list_claimable_plans(limit=1)`` + ``claim_plan`` thundering herd).
- ``try_acquire_leader`` elects ONE holder for the periodic recovery sweeps so they are
  not run redundantly by every worker.

SQLite serializes writers, so the anti-contention property (distinct claims) is asserted
here sequentially; the concurrent ``SKIP LOCKED`` path is exercised live on PostgreSQL.
"""

from __future__ import annotations

import time

import pytest

from dms.db import Database
from dms.domain import LifecycleState, WorkerRole
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository

from test_operational_controls import (
    _create_scan_request,
    _register_ready_storage_mapping,
)


@pytest.fixture()
def repo(tmp_path) -> DmsRepository:
    op = Database(f"sqlite:///{tmp_path / 'op.db'}")
    obs = Database(f"sqlite:///{tmp_path / 'obs.db'}")
    migrate_all(op, obs)
    return DmsRepository(op)


def _seed_dm_plans(repo: DmsRepository, n: int) -> None:
    _register_ready_storage_mapping(repo)
    for i in range(n):
        _create_scan_request(repo, f"claimdir-{i:02d}")
    Planner(repo).run_once(limit=100)


# --- Database.is_postgres (dialect detection for conditional SKIP LOCKED) --------
def test_is_postgres_dialect_detection(tmp_path):
    assert Database(f"sqlite:///{tmp_path / 'x.db'}").is_postgres is False
    assert Database("postgresql://u:p@h:5432/d").is_postgres is True
    assert Database("postgres://u:p@h:5432/d").is_postgres is True


# --- claim_next_plan -----------------------------------------------------------
def test_claim_next_plan_claims_distinct_oldest_first(repo):
    _seed_dm_plans(repo, 3)
    a = repo.claim_next_plan(WorkerRole.DM, worker_id="w1", executor_id="w1", lease_seconds=60)
    b = repo.claim_next_plan(WorkerRole.DM, worker_id="w2", executor_id="w2", lease_seconds=60)
    c = repo.claim_next_plan(WorkerRole.DM, worker_id="w3", executor_id="w3", lease_seconds=60)
    assert a and b and c
    ids = {a[0]["plan_id"], b[0]["plan_id"], c[0]["plan_id"]}
    assert len(ids) == 3  # each worker got a DISTINCT plan -- no double-claim
    assert a[0]["created_at"] <= b[0]["created_at"] <= c[0]["created_at"]  # oldest first
    # queue drained
    assert repo.claim_next_plan(WorkerRole.DM, worker_id="w4", executor_id="w4", lease_seconds=60) is None


def test_claim_next_plan_none_when_empty(repo):
    assert repo.claim_next_plan(WorkerRole.DM, worker_id="w1", executor_id="w1", lease_seconds=60) is None


def test_claim_next_plan_creates_run_and_transitions_claimed(repo):
    _seed_dm_plans(repo, 1)
    claimed = repo.claim_next_plan(WorkerRole.DM, worker_id="w1", executor_id="w1", lease_seconds=60)
    assert claimed is not None
    plan, run_id = claimed
    assert repo.get_plan(plan["plan_id"])["status"] == LifecycleState.CLAIMED.value
    assert repo.get_request(plan["request_id"])["status"] == LifecycleState.CLAIMED.value
    assert run_id in {r["run_id"] for r in repo.list_active_runs(limit=10)}
    # returned plan is decoded (execution_metadata is a dict, not a JSON string)
    assert isinstance(plan["execution_metadata"], dict)


# --- try_acquire_leader (single-holder election for recovery sweeps) ------------
def test_try_acquire_leader_single_holder_and_renew(repo):
    assert repo.try_acquire_leader("recovery-sweeper", holder="w1", lease_seconds=60) is True
    assert repo.try_acquire_leader("recovery-sweeper", holder="w2", lease_seconds=60) is False  # w1 holds it
    assert repo.try_acquire_leader("recovery-sweeper", holder="w1", lease_seconds=60) is True  # renew self


def test_try_acquire_leader_takeover_on_expiry(repo):
    assert repo.try_acquire_leader("sweep", holder="w1", lease_seconds=1) is True
    time.sleep(1.2)
    assert repo.try_acquire_leader("sweep", holder="w2", lease_seconds=60) is True  # w1 lease expired
    assert repo.try_acquire_leader("sweep", holder="w1", lease_seconds=60) is False  # w2 holds it now


def test_try_acquire_leader_independent_components(repo):
    assert repo.try_acquire_leader("comp-a", holder="w1", lease_seconds=60) is True
    assert repo.try_acquire_leader("comp-b", holder="w2", lease_seconds=60) is True  # different component
