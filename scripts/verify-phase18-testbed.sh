#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
testbed_dir="${DMS_TESTBED_DIR:-/home/mason/workspace/testbed}"
suffix="${DMS_PHASE18_DB_SUFFIX:-$(date +%Y%m%d%H%M%S)}"
operational_db="${DMS_PHASE18_OPERATIONAL_DB:-dms_phase18_${suffix}}"
observability_db="${DMS_PHASE18_OBSERVABILITY_DB:-dms_phase18_obs_${suffix}}"
postgres_host="${DMS_PHASE18_POSTGRES_HOST:-192.168.56.11}"
postgres_port="${DMS_PHASE18_POSTGRES_PORT:-30432}"
postgres_user="${DMS_PHASE18_POSTGRES_USER:-appuser}"
auth_token="${DMS_PHASE18_AUTH_TOKEN:-phase18-testbed-token}"
python_bin="${DMS_PYTHON:-}"

if [[ -z "$python_bin" ]]; then
  for candidate in python3 /tmp/dms-phase3-venv/bin/python3 /tmp/dms-phase2-venv/bin/python3; do
    if "$candidate" - <<'PY' >/dev/null 2>&1
import fastapi
import httpx
import psycopg
PY
    then
      python_bin="$candidate"
      break
    fi
  done
fi
python_bin="${python_bin:-python3}"

printf '== Testbed metadata ==\n'
sed -n '1,80p' "${testbed_dir}/testbed-summary.json"

printf '== Cluster and PostgreSQL readiness ==\n'
ssh c1-control 'kubectl get nodes -o wide'
ssh c2-control 'kubectl get nodes -o wide'
ssh c1-control 'kubectl -n postgresql get svc,pod,statefulset'

postgres_password="$(
  ssh c1-control "kubectl -n postgresql get secret postgresql-auth -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d"
)"

export POSTGRES_PASSWORD="${postgres_password}"
export PHASE18_OPERATIONAL_DB="${operational_db}"
export PHASE18_OBSERVABILITY_DB="${observability_db}"
export PHASE18_POSTGRES_HOST="${postgres_host}"
export PHASE18_POSTGRES_PORT="${postgres_port}"
export PHASE18_POSTGRES_USER="${postgres_user}"
export PHASE18_AUTH_TOKEN="${auth_token}"

"$python_bin" - <<'PY'
import os
import re

import psycopg
from psycopg import sql

db_names = [os.environ["PHASE18_OPERATIONAL_DB"], os.environ["PHASE18_OBSERVABILITY_DB"]]
for db_name in db_names:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", db_name):
        raise SystemExit(f"unsafe database name: {db_name}")

common = {
    "host": os.environ["PHASE18_POSTGRES_HOST"],
    "port": int(os.environ["PHASE18_POSTGRES_PORT"]),
    "user": os.environ["PHASE18_POSTGRES_USER"],
    "password": os.environ["POSTGRES_PASSWORD"],
}
with psycopg.connect(dbname="postgres", autocommit=True, **common) as connection:
    for db_name in db_names:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (db_name,),
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))
print(f"created or reused databases: {', '.join(db_names)}")
PY

db_urls="$(
  "$python_bin" - <<'PY'
import os
from urllib.parse import quote

encoded_password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
print(
    f"postgresql://{os.environ['PHASE18_POSTGRES_USER']}:{encoded_password}"
    f"@{os.environ['PHASE18_POSTGRES_HOST']}:{os.environ['PHASE18_POSTGRES_PORT']}"
    f"/{os.environ['PHASE18_OPERATIONAL_DB']}"
)
print(
    f"postgresql://{os.environ['PHASE18_POSTGRES_USER']}:{encoded_password}"
    f"@{os.environ['PHASE18_POSTGRES_HOST']}:{os.environ['PHASE18_POSTGRES_PORT']}"
    f"/{os.environ['PHASE18_OBSERVABILITY_DB']}"
)
PY
)"
export DMS_DATABASE_URL="$(sed -n '1p' <<<"$db_urls")"
export DMS_OBSERVABILITY_DATABASE_URL="$(sed -n '2p' <<<"$db_urls")"
export DMS_AUTH_SHARED_TOKEN="$auth_token"
export DMS_WORKER_LEASE_SECONDS=2

printf '== Phase 18 API/repository verification on testbed PostgreSQL ==\n'
PYTHONPATH="${repo_dir}/src" "$python_bin" - <<'PY'
from __future__ import annotations

import os
import time

from fastapi.testclient import TestClient

from dms.adapters import StubFilesystemBackendAdapter, StubKubernetesNamespaceQuotaAdapter
from dms.api import create_app
from dms.config import Settings
from dms.db import Database
from dms.domain import LifecycleState, OperationKind, ResourceKind, StorageMappingInput
from dms.migrations import migrate_all
from dms.planner import Planner
from dms.repositories import DmsRepository, ObservabilityRepository
from dms.workers import RunHeartbeat

settings = Settings.from_env()
operational = Database(settings.database_url)
observability_db = Database(settings.observability_database_url)
migrate_all(operational, observability_db)
repository = DmsRepository(operational)
observability = ObservabilityRepository(observability_db)
client = TestClient(
    create_app(
        settings=settings,
        repository=repository,
        observability=observability,
        kubernetes_quota=StubKubernetesNamespaceQuotaAdapter(),
    )
)
headers = {"authorization": f"Bearer {os.environ['PHASE18_AUTH_TOKEN']}", "x-dms-actor": "phase18-operator"}

default_state = client.get("/api/v1/operations/control-state", headers=headers)
assert default_state.status_code == 200, default_state.text
assert default_state.json()["scheduling_blocked"] is False

enter = client.post(
    "/api/v1/operations/control-state:enter-maintenance",
    json={"reason": "phase18 testbed maintenance"},
    headers=headers,
)
assert enter.status_code == 200, enter.text
assert enter.json()["control_state"]["scheduling_blocked"] is True

blocked = client.post(
    "/api/v1/resource-management/filesystems",
    json={
        "requester_id": "phase18-testbed",
        "payload": {
            "storage_name": "cephfs-a",
            "directory_name": "maintenance-blocked",
            "resource_type": "user",
            "users": ["alice", "bob"],
            "expires_at": "2099-01-01T00:00:00Z",
        },
    },
    headers=headers,
)
assert blocked.status_code == 409, blocked.text
assert repository.list_requests(requester_id="phase18-testbed") == []
assert client.get("/api/v1/operations/work-summary", headers=headers).status_code == 200

resume = client.post(
    "/api/v1/operations/control-state:resume",
    json={"reason": "phase18 testbed resume"},
    headers=headers,
)
assert resume.status_code == 200, resume.text

sanity = {
    "storage_name": "cephfs-a",
    "status": "Ready",
    "checked_at": "2026-06-03T00:00:00+00:00",
    "readiness": {
        "resource_management": "Ready",
        "data_management": "Ready",
        "inventory": "Ready",
    },
    "checks": [],
    "warnings": [],
    "errors": [],
}
repository.upsert_storage_mapping(
    StorageMappingInput(
        storage_name="cephfs-a",
        backend_template={"backend_type": "cephfs"},
        cluster_name="cluster-a",
        storage_class_name="testbed-cephfs",
    ),
    actor="phase18-operator",
    sanity_result=sanity,
    readiness=sanity["readiness"],
)
request_id = repository.create_request(
    requester_id="phase18-testbed",
    actor="phase18-operator",
    operation=OperationKind.FILESYSTEM_CREATE.value,
    resource_kind=ResourceKind.FILESYSTEM.value,
    resource_key="cephfs-a:phase18-heartbeat",
    payload={
        "storage_name": "cephfs-a",
        "directory_name": "phase18-heartbeat",
        "resource_type": "user",
        "users": ["alice", "bob"],
        "expires_at": "2099-01-01T00:00:00Z",
    },
)
assert Planner(repository).run_once() == 1
plan = repository.get_plan_by_request(request_id)
run_id = repository.claim_plan(
    plan_id=plan["plan_id"],
    worker_id="phase18-rm-worker",
    executor_id="phase18-rm-worker",
    lease_seconds=1,
)
repository.update_run_state(
    run_id,
    LifecycleState.APPLYING,
    reason="phase18 heartbeat fixture",
    actor="phase18-rm-worker",
)
before = repository.list_active_runs(limit=1)[0]["lease_expires_at"]
with RunHeartbeat(
    repository=repository,
    observability=observability,
    run_id=run_id,
    worker_id="phase18-rm-worker",
    lease_seconds=1,
    interval_seconds=0.05,
):
    time.sleep(0.16)
after = repository.list_active_runs(limit=1)[0]["lease_expires_at"]
assert after > before

with repository.database.connect() as connection:
    connection.execute(
        "UPDATE runs SET lease_expires_at = ? WHERE run_id = ?",
        ("2000-01-01T00:00:00+00:00", run_id),
    )
marked = client.post("/api/v1/operations/runs:mark-stale", headers=headers)
assert marked.status_code == 200, marked.text
assert marked.json()["marked"] == 1
run = repository.list_runs(limit=1)[0]
assert run["state"] == LifecycleState.RECOVERY_NEEDED.value

begin_drain = client.post(
    "/api/v1/operations/control-state:begin-drain",
    json={"reason": "phase18 testbed drain"},
    headers=headers,
)
assert begin_drain.status_code == 200, begin_drain.text
drain_status = client.get("/api/v1/operations/drain-status", headers=headers)
assert drain_status.status_code == 200, drain_status.text
assert drain_status.json()["ready_for_shutdown"] is False
assert client.get("/api/v1/operations/plans/active", headers=headers).status_code == 200
assert client.get("/api/v1/operations/runs/active", headers=headers).status_code == 200

blocked_resume = client.post(
    "/api/v1/operations/control-state:resume",
    json={"reason": "blocked resume"},
    headers=headers,
)
assert blocked_resume.status_code == 409, blocked_resume.text
forced_resume = client.post(
    "/api/v1/operations/control-state:resume",
    json={"reason": "operator accepted recovery fixture", "force": True},
    headers=headers,
)
assert forced_resume.status_code == 200, forced_resume.text

mutations = repository.list_control_mutations(limit=10)
mutation_kinds = {mutation["mutation_kind"] for mutation in mutations}
assert {
    "control.enter_maintenance",
    "control.resume",
    "control.begin_drain",
    "runs.mark_stale",
}.issubset(mutation_kinds)

print(
    {
        "request_id": request_id,
        "plan_id": plan["plan_id"],
        "run_id": run_id,
        "final_control_state": repository.control_state(),
        "work_summary": client.get("/api/v1/operations/work-summary", headers=headers).json(),
    }
)
PY

printf 'Phase 18 testbed verification completed with operational DB %s and observability DB %s\n' \
  "$operational_db" "$observability_db"
