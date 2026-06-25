# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

This repo contains two parts:
- **DMS backend** (`src/dms/`) — a control plane for managing filesystem resources, Kubernetes
  namespace storage quotas, and data jobs (`scan`/`sync`/`rm`) across multiple storage backends and
  Kubernetes clusters. A FastAPI service plus background loop processes, backed by two PostgreSQL
  databases (operational + observability), portable to SQLite for tests.
- **Portal** (`src/portal/`) — the operator/user-facing web UI. A FastAPI BFF (`src/portal/backend/`)
  plus a Vite SPA (`src/portal/frontend/`). The portal is a **pure client of the DMS HTTP API**; see
  the Portal section below for the rules that keep backend changes out of portal work.

## Commands

### DMS backend

```bash
# Install for development (editable + test extras; all optional extras for full coverage)
pip install -e ".[test,ldap,kubernetes,postgres]"

# Run the full test suite (pytest config lives in pyproject.toml; src/ is on pythonpath)
pytest

# Run a single test file / test
pytest tests/test_dm_sync_chmod_chown.py
pytest tests/test_dm_sync_chmod_chown.py -k some_case -x

# CLI entrypoint (installed as `dms`, defined at src/dms/cli.py:main)
dms migrate                                  # apply DB migrations to both databases
dms api --host 0.0.0.0 --port 8080           # serve FastAPI (uvicorn factory dms.api:create_app)
dms planner --loop --interval 5              # requests -> plans
dms rm-worker --worker-id rm-1 --loop        # claim RM plans, apply filesystem / k8s quota changes
dms dm-worker --worker-id dm-1 --loop        # claim DM data jobs (scan/sync/rm)
dms sanity-reconciler --loop                 # reconcile storage-mapping sanity status
dms agent-probe --once                       # build one node agent report as JSON (run on a node)
dms agent-loop                               # node agent daemon report loop
```

Tests default to SQLite (no services needed). Configuration is entirely via `DMS_*` environment
variables consumed by `Settings.from_env()` (`src/dms/config.py`); there is no config file.

### Portal

```bash
# BFF (FastAPI). src/portal/backend is the `portal` package via setuptools `packages.find`;
# install in editable mode so it shares the venv with the DMS backend, then serve with uvicorn.
pip install -e ".[portal]"
uvicorn portal.backend.app:create_app --factory --reload --port 8090

# Frontend (Vite SPA) — its own Node toolchain, independent of the Python venv.
cd src/portal/frontend && npm install && npm run dev      # dev server, proxies /api -> BFF
cd src/portal/frontend && npm run build                   # production static assets served by BFF
```

Portal config also comes from env vars (e.g. `PORTAL_DMS_API_URL`, mTLS cert/key/CA paths) — keep
them under a `PORTAL_*` prefix, distinct from `DMS_*`, so the two services never share config state.

## DMS backend architecture

The system is a **request → plan → run** state machine. A request is persisted, the planner turns it
into a plan, and a role-specific worker claims the plan via a lease and applies side effects through
adapters. State is the source of truth; every process is a restartable loop that calls `run_once()`.

### Request lifecycle (the central abstraction)
- DB tables (`src/dms/migrations.py`): `requests` → `plans` → `runs` → `results`, with
  `state_transitions` recording every move. `resources` holds the materialized current state of
  managed resources; `data_jobs` is the DM-specific job record.
- `LifecycleState` and `DataJobState` enums (`src/dms/domain.py`) define the state machines.
  `TERMINAL_LIFECYCLE_STATES` gates ret/cleanup. Workers claim work with time-bounded **leases**
  (`worker_lease_seconds`); stale claims are reaped (`mark_stale_runs`).
- `OperationKind` (`filesystem.*`, `kubernetes.namespace_quota.*`, `data.*`, `identity.*`),
  `ResourceKind`, and `WorkerRole` (RM/DM) classify each request and route it to a worker.

### Layers
- **API** (`src/dms/api/`): `create_app()` (`app.py`) wires `Settings`, repositories, adapters, and
  auth into `AppServices` (`_services.py`), then mounts routers under `/api/v1/`:
  `data-management`, `resource-management`, `agent`, `operations` (read-only queries), `identity`
  (DM denylist). Request-shaping logic lives in `api/_helpers/`. Auth (`src/dms/auth.py`) supports a
  shared bearer token and an mTLS header profile (`AuthVerifier` + `AuthorizationPolicy`); when
  `require_mtls_header` is on, the actor comes from a verified header and `default_actor` is rejected
  at startup.
- **Planner** (`src/dms/planner/`): `Planner` (split into `_core`, `_filesystem`, `_kubernetes`
  mixins via `from ._base import *`). `run_once(limit)` reads plannable requests and emits plans;
  it dedups against prior active requests and computes filesystem/quota issues and expiries.
- **Workers** (`src/dms/workers/`): `RMWorkerRuntime` (`rm.py`) applies filesystem and k8s-quota
  mutations; `DMWorkerRuntime` (`dm.py`, the largest module) runs data jobs through a
  **preview/confirm** flow (`scan`/`sync`/`rm`), resolves the job owner's POSIX identity via
  read-only LDAP, enforces a uid/gid floor, optional opt-in root (`allow_root_requester` +
  `privileged_requesters`), and schedules MPI jobs via Volcano. Both extend `_base.py`.
- **Repositories** (`src/dms/repositories/`): `DmsRepository` (operational) and
  `ObservabilityRepository` aggregate per-concern repos (`requests`, `data_jobs`, `resources`,
  `policies`, `identity`, `execution`, `operational`, `storage_mappings`). All SQL lives here and is
  kept portable across SQLite and PostgreSQL (`src/dms/db.py` `Database` wrapper).
- **Adapters** (`src/dms/adapters/`): the only layer that touches the outside world — `identity`
  (LDAP), `kubernetes_quota` (live `ResourceQuota` or `kubectl`/SSH), `inventory`, `volcano` (MPIJob
  scheduling), `filesystem`. Each has a `*Live*`/real implementation and a `Stub*` counterpart used
  in tests and default CLI wiring. `subprocess` is re-exported from `adapters/__init__.py` so tests
  can monkeypatch it.
- **Backends** (`src/dms/backends/` + `backend_registry.py`): per-storage-type behavior for
  `cephfs`, `weka`, `gpfs`. `BackendAdapterRegistry` resolves the right filesystem/DM adapter per
  resource from storage mappings, with `enforce_supported_backends` gating.
- **Agent** (`src/dms/agent.py`, `agent_daemon.py`): a node-side daemon (Kubernetes DaemonSet) that
  probes storage mounts, tools, credentials, and identity, then POSTs `AgentReport`s. The DM worker
  checks **report freshness** (`agent_report_stale_seconds`) as a preflight gate before running data
  jobs on a node.

### Conventions
- Submodules that grow large are split into a `_base.py` (shared imports/helpers, re-exported via
  `from ._base import *`) plus topic mixins/files (`planner`, `workers`). Follow this when extending.
- Workers are designed to **fail closed**: half-configured LDAP yields a per-request
  `ldap_not_configured` rejection rather than crashing the loop; the `dm-worker` sets `umask 0o077`
  so artifact metadata is owner-only.
- `data.*` mutating jobs (`sync`/`rm`) require a preview whose fingerprint is confirmed before
  execution (`dm_confirm_require_preview_fingerprint`); `data.cancel` terminates the in-flight MPI
  job, not just the DB record.

## Portal (`src/portal/`)

The portal is developed **without modifying the DMS backend** (`src/dms/`). It is a separate
application that consumes DMS over HTTP only.

### Working rule (non-negotiable for portal work)
- Do **not** edit `src/dms/`, `src/dms/migrations.py`, the DB schema, or DMS env vars while building
  the portal. Treat the DMS `/api/v1/` surface + `domain.py` models as a fixed contract.
- If a portal feature genuinely needs a backend change (missing endpoint, field, filter, or a new
  read query), **stop and raise it as an explicit issue** — describe the gap, the desired API shape,
  and why the portal cannot do it client-side — then co-implement with the backend owner. Don't
  silently reach into repositories or add a DMS route to unblock yourself.

### Integration contract
- **BFF is the only thing that talks to DMS.** The browser SPA calls the BFF; the BFF calls
  `PORTAL_DMS_API_URL` + `/api/v1/...`. The frontend never holds DMS credentials or calls DMS
  directly (avoids exposing the mTLS identity / shared token and sidesteps CORS).
- **Auth = mTLS header profile.** DMS runs with `require_mtls_header` / `require_mtls_verified_header`
  on. The BFF presents its **client certificate** to DMS and sets the verified-actor header
  (`mtls:` prefix per `mtls_actor_prefix`) representing the logged-in portal user. Browser↔BFF
  session auth is the BFF's own concern, separate from BFF↔DMS mTLS.
- **API surface the portal consumes** (all under `/api/v1/`, see `src/dms/api/routers/`):
  `operations` (read-only queries — inventory, storage mappings, work summary, control state),
  `resource-management` (filesystem + k8s quota requests), `data-management` (`scan`/`sync`/`rm`
  with the preview/confirm flow), `agent`, `identity` (DM denylist). Health: `/healthz`.
- Mirror DMS's request/preview/confirm semantics in the UI: mutating data jobs require showing the
  preview and confirming its fingerprint before execution; long operations are async (poll
  request/job state), not blocking calls.

### Role model & interface separation
The portal has two distinct interfaces, selected by **role**, and login method maps directly to role:
- **`operator`** — signs in with an **id/password** (operator-only; multiple operator accounts via
  `PORTAL_OPERATOR_USERS`, `user:pw,user2:pw2`). Gets the operator/admin console.
- **`user`** — signs in with a **company AD account** (currently a dummy stand-in). Gets the
  end-user interface.

Role lives in the signed session cookie (`request.session["user"]`) and is the single source of
truth, enforced on **both** sides:
- Backend: `backend/security.py` (`ROLE_*`, `require_role(...)` dependency). Role-scoped routers under
  `backend/routers/` — `user_router` (`/api/user/*`) and `operator_router` (`/api/operator/*`), each
  gated so the other role gets 403. Shared auth in `backend/auth.py` (`/api/auth/login` → operator,
  `/api/auth/login/ad` → user).
- Frontend: `App.tsx` switches on `user.role` into entirely separate interface trees under
  `frontend/src/interfaces/operator/` and `frontend/src/interfaces/user/`; each calls only its own
  `/api/<role>/*` surface. Shared chrome in `frontend/src/components/`, shared login in `pages/`.

Keep the two interfaces separate as they grow — add operator features under `interfaces/operator` +
`/api/operator`, user features under `interfaces/user` + `/api/user`.

### Portal layout
- `src/portal/backend/` — FastAPI BFF (`app.py` `create_app` factory, a typed DMS HTTP client, routers,
  per-user session/auth). Reuse the repo's conventions: env-driven `Settings`, `create_app` factory.
- `src/portal/frontend/` — Vite SPA (React/Vue) with its own `package.json`; built assets are served
  by the BFF in production and proxied to it in dev.
- `src/portal/deploy/` — `Dockerfile` (multi-stage: node build → python runtime) and
  `kubernetes/portal.yaml` (namespace `dms-portal`, Deployment, NodePort).

### DMS integration (BFF)
- `backend/dms_client.py` (`DmsClient`) is the **only** thing that talks to DMS — an httpx async
  client created once on `app.state`. Operator routes call it via the `get_dms_client` dependency and
  re-raise `DmsApiError` as `HTTPException` (DMS status + detail forwarded to the SPA verbatim).
- Config (env, `PORTAL_*`): `PORTAL_DMS_API_URL` (in-cluster `http://dms-api.dms.svc.cluster.local` on
  the testbed), `PORTAL_DMS_ACTOR` (default actor; the BFF overrides per-request with the logged-in
  operator's username for DMS audit). Testbed auth = bearer + `x-dms-actor`.
- **All portal Secret values are placeholders (`REPLACE_WITH_*`) in `kubernetes/portal.yaml`** —
  `PORTAL_SESSION_SECRET`, `PORTAL_OPERATOR_USERS`, `PORTAL_DMS_TOKEN` are injected into the live Secret
  out-of-band via `kubectl -n dms-portal patch secret portal-secrets`; never commit working
  credentials. `kubectl apply` overwrites them back to placeholders, so re-patch all three + rollout
  restart after every apply.
- Storage inventory (`interfaces/operator/storage/`) is the reference DMS-backed feature: full CRUD over
  the DMS storage-mapping API. Watch the DMS contract quirks — PATCH takes the FULL `StorageMappingInput`
  (round-trip the whole current state, don't send partials), only `weka_credentials.password` is
  redacted (never render secrets; DMS merges omitted secrets back on upsert), DELETE returns the mapping
  UN-redacted so the BFF must return only a minimal confirmation, and there is no disable/enable
  endpoint (delete is a hard delete).
- Security defaults: `PORTAL_SESSION_HTTPS_ONLY` defaults true (the plain-HTTP testbed sets it false);
  `create_app` refuses to boot on the dev-default session secret unless `PORTAL_ALLOW_INSECURE_DEFAULTS=1`.

## Install / deployment docs

`install/` is the operational source of truth (numbered guides `1`–`5` — `5.dms-portal-setup.md` covers
the portal — plus `CONFIGURATION.md`, `RUNBOOK.md`, Kubernetes manifests, PostgreSQL `init.sql`, helper
scripts, and Dockerfiles including `Dockerfile.mpifileutils` for the DM job image). Much of it is written
in Korean. When changing runtime settings, env-var names, or API request options, update the relevant
`install/` doc to match.
