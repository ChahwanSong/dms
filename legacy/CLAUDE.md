# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

Two parts:
- **DMS backend** (`src/dms/`) — control plane for a **storage inventory** (storage mappings over
  CephFS/GPFS/WekaFS + their CSI counterparts) and **data jobs** (`scan`/`sync`/`rm`) across multiple
  storage backends and Kubernetes clusters. FastAPI service + background loop processes, backed by two
  PostgreSQL databases (operational + observability), portable to SQLite for tests.
- **Portal** (`src/portal/`) — operator/user web UI: FastAPI BFF (`backend/`) + Vite/React SPA
  (`frontend/`). A **pure client of the DMS HTTP API** — see the Portal rules below.

## Commands

```bash
pip install -e ".[test,ldap,kubernetes,postgres]"   # dev install (extras: + portal)
pytest                                              # full suite (SQLite, no services needed)
pytest tests/test_dm_sync_chmod_chown.py -k some_case -x

# CLI (`dms`, entrypoint src/dms/cli.py:main). Loop processes take --loop [--interval N].
dms migrate                                  # migrate both databases
dms api --host 0.0.0.0 --port 8080           # uvicorn factory dms.api:create_app
dms planner --loop                           # requests -> plans
dms dm-worker --worker-id dm-1 --loop        # claim DM data jobs
dms sanity-reconciler --loop                 # recompute storage-mapping sanity
dms retention --loop                         # prune history tables
dms agent-probe --once | dms agent-loop      # node agent (run on a node)
```

```bash
# Portal BFF — src/portal/backend is the `portal` package; shares the venv with the backend.
pip install -e ".[portal]"
uvicorn portal.backend.app:create_app --factory --reload --port 8090
cd src/portal/frontend && npm install && npm run dev    # SPA dev server, proxies /api -> BFF
```

Config is entirely env vars — `DMS_*` via `Settings.from_env()` (`src/dms/config.py`), `PORTAL_*` via
`src/portal/backend/config.py`. No config file. Keep the two prefixes distinct.

## DMS backend architecture

A **request → plan → run** state machine. A request is persisted, the planner turns it into a plan, a
worker claims the plan via a lease and applies side effects through adapters. State is the source of
truth; every process is a restartable loop calling `run_once()`.

### Request lifecycle
- Tables (`src/dms/migrations.py`): `requests` → `plans` → `runs` → `results`, with
  `state_transitions` recording every move. `resources` holds materialized current state; `data_jobs`
  is the DM-specific job record.
- `LifecycleState` / `DataJobState` (`src/dms/domain.py`) define the state machines;
  `TERMINAL_LIFECYCLE_STATES` marks the states nothing further advances from (used for request
  listing, stuck-request `:resolve`, and attention detection). Workers claim work with time-bounded
  **leases** (`worker_lease_seconds`); stale claims are reaped (`mark_stale_runs`).
- `OperationKind` (`data.scan`/`sync`/`rm`/`cancel`, `identity.*`), `ResourceKind` (`data_job`, the
  only one) and `WorkerRole` (`DM` only) classify and route each request. **Storage-mapping CRUD is
  synchronous in the API** and never enters the request→plan→run machine — hence no ResourceKind of
  its own.

### Layers
- **API** (`src/dms/api/`) — `create_app()` (`app.py`) wires `Settings`, repositories, adapters and
  auth into `AppServices` (`_services.py`), then mounts routers:
  `/api/v1/storage-mappings` (inventory CRUD + sanity `:check`), `/api/v1/data-management`
  (+ nested `/identity-denylist`), `/api/v1/agent`, `/api/v1/operations` (read-only queries, control
  state, stuck-request `:resolve`). Health: `/healthz`. Request shaping lives in `api/_helpers/`.
  Auth (`src/dms/auth.py`): shared bearer token + mTLS header profile (`AuthVerifier` +
  `AuthorizationPolicy`). With `require_mtls_header` on, the actor is derived from the verified
  header — **the full certificate subject DN**, prefixed by `mtls_actor_prefix` — and `default_actor`
  is rejected at startup.
- **Planner** (`src/dms/planner.py`) — `run_once(limit)` reads plannable requests, emits plans +
  `data_jobs` rows, dedups against active requests, gates on storage-mapping sanity/readiness (fail
  closed).
- **Workers** (`src/dms/workers/`) — `DMWorkerRuntime` (`dm.py`, the largest module) runs data jobs
  through the **preview/confirm** flow, resolves the owner's POSIX identity via read-only LDAP,
  enforces a uid/gid floor, supports opt-in root (`allow_root_requester` + `privileged_requesters`),
  and schedules MPI jobs via Volcano.
- **Repositories** (`src/dms/repositories/`) — `DmsRepository` (operational) + `ObservabilityRepository`
  aggregate per-concern repos (`requests`, `data_jobs`, `resources`, `policies`, `identity`,
  `execution`, `operational`, `storage_mappings`). **All SQL lives here**, portable across SQLite and
  PostgreSQL (`src/dms/db.py`).
- **Adapters** (`src/dms/adapters/`) — the only layer touching the outside world: `identity` (LDAP),
  `inventory` (read-only `kubectl`/`ssh-kubectl` StorageClass/CSI/node reads), `volcano` (Job
  scheduling). Live/stub implementations are paired where tests need them; `subprocess` is
  re-exported from `adapters/__init__.py` so tests can monkeypatch it.
- **Placement is backend-agnostic.** Every filesystem is treated as a plain POSIX mount, so there
  is no per-backend adapter: the planner seeds one agent-inventory worker pool from the mapping's
  sanity evidence, and the DM worker replaces it with its own candidate lists on claim.
- **Agent** (`src/dms/agent.py`, `agent_daemon.py`) — node-side DaemonSet probing mounts, tools,
  credentials and identity, then POSTing `AgentReport`s. The DM worker gates on **report freshness**
  (`agent_report_stale_seconds`) before running jobs on a node.

### Conventions
- Large submodules split into `_base.py` (shared imports/helpers, re-exported via
  `from ._base import *`) plus topic files (`workers`, `repositories`). Conversely, a package whose
  split stops earning its keep is flattened back into one module (`planner.py`).
- **Fail closed**: half-configured LDAP yields a per-request `ldap_not_configured` rejection rather
  than crashing the loop; `dm-worker` sets `umask 0o077` so artifact metadata is owner-only.
- Mutating jobs (`sync`/`rm`) require a preview whose fingerprint is confirmed before execution
  (`dm_confirm_require_preview_fingerprint`); `data.cancel` terminates the in-flight MPI job, not just
  the DB record.

## Portal (`src/portal/`)

### Working rule (non-negotiable)
Do **not** edit `src/dms/`, the DB schema, or DMS env vars while building the portal. Treat the
`/api/v1/` surface + `domain.py` models as a fixed contract. If a portal feature genuinely needs a
backend change, **stop and raise it as an explicit issue** (the gap, the desired API shape, why it
can't be done client-side), then co-implement with the backend owner.

### Integration contract
- **BFF is the only thing that talks to DMS.** `backend/dms_client.py` (`DmsClient`) — one httpx async
  client on `app.state`. The SPA calls the BFF; the BFF calls `PORTAL_DMS_API_URL` + `/api/v1/...`.
  The frontend never holds DMS credentials. Operator routes go through the `get_dms_client` dependency
  and re-raise `DmsApiError` as `HTTPException` (DMS status + detail forwarded verbatim).
- **Auth = internal shared-token plane, NOT mTLS.** External `dms-api` runs the mTLS-verified profile,
  which pins the actor to the (single) client-cert subject and would collapse every operator into one
  audit identity. So the BFF targets **`dms-api-internal`** (mTLS off + shared token + NetworkPolicy) —
  `PORTAL_DMS_API_URL` defaults to `http://dms-api-internal.dms.svc.cluster.local`, and
  `PORTAL_DMS_TOKEN` must equal DMS's `DMS_AUTH_SHARED_TOKEN`. **`dms_client.py` does not load a client
  certificate.** Per-user identity rides in `x-dms-actor`; DM job routes prefix it with
  `PORTAL_BACKUP_ACTOR_PREFIX` (default `mtls:`), other routes send the bare username.
- Mirror DMS semantics in the UI: mutating data jobs must show the preview and confirm its fingerprint
  before execution; long operations are async (poll request/job state), never blocking calls.

### Role model & interface separation
Two interfaces selected by **role**. Both roles log in with an id/password, so the **account store
decides the role**, never the login method (`backend/security.py`):
- **`operator`** — matched in `portal.operator_users` (seeded from `PORTAL_OPERATOR_USERS`
  `user:pw,user2:pw2`; created/reset with `PORTAL_ADMIN_TOKEN`). Operator/admin console.
- **`user`** — matched in `portal.user_accounts`; id = company-mail local part, self-service signup /
  reset via a 6-digit code mailed to `<id>@PORTAL_EMAIL_DOMAIN`. End-user interface.

Role lives in the signed session cookie and is enforced on both sides:
- Backend: `backend/security.py` (`ROLE_*`, `require_role(...)`). Role-scoped routers —
  `/api/user/*` and `/api/operator/*`, each 403 for the other role. Shared auth in `backend/auth.py`
  (`/api/auth/login` → operator, `/api/auth/user/login` → user); each route hard-codes its own role.
- Frontend: `App.tsx` switches on `user.role` into separate trees under `frontend/src/interfaces/
  {operator,user}/`, each calling only its own `/api/<role>/*`. Shared chrome in `components/`,
  shared login in `pages/`.

Keep the two separate as they grow.

### DMS contract quirks (storage inventory is the reference feature)
- PATCH takes the **full** `StorageMappingInput` — round-trip the whole current state, never partials.
- `backend_template` carries no secrets — DMS authenticates to no filesystem. Responses still run a
  generic redactor over secret-shaped keys (`password`/`secret`/`token`/…) as a safety net; there is
  no merge-back on upsert. Never render whatever it masks.
- DELETE is a **hard** delete (no disable/enable endpoint) and returns the deleted mapping.
- **All portal Secret values are `REPLACE_WITH_*` placeholders** in `kubernetes/portal.yaml`
  (`PORTAL_SESSION_SECRET`, `PORTAL_OPERATOR_USERS`, `PORTAL_DMS_TOKEN`, `PORTAL_ADMIN_TOKEN`,
  `PORTAL_DB_URL`) — injected out-of-band via `kubectl -n dms-portal patch secret portal-secrets`.
  Never commit working credentials. `kubectl apply` overwrites them back, so re-patch + rollout
  restart after every apply.
- Security defaults: `PORTAL_SESSION_HTTPS_ONLY` defaults true (plain-HTTP testbeds set it false);
  `create_app` refuses to boot on the dev-default session secret, or with
  `PORTAL_EMAIL_DELIVERY=log`, unless `PORTAL_ALLOW_INSECURE_DEFAULTS=1`.

### Portal layout
`backend/` FastAPI BFF (`create_app` factory, env-driven `Settings`) · `frontend/` Vite/React SPA
(built assets served by the BFF in production, proxied in dev) · `deploy/` multi-stage `Dockerfile` +
`kubernetes/portal.yaml` (namespace `dms-portal`, Deployment, NodePort 30090).

## Install / deployment docs

`install/` is the operational source of truth, mostly Korean. Numbered guides `dms-01`–`dms-06`
(prerequisites, core, storage-mappings, dm-jobs, configuration/env reference, ingress+metallb), plus
`portal-01-setup.md` / `portal-02-user-auth.md`, `redeploy.md` (rebuild → rollout) and
`migration-rm-removal.md` (one-time upgrade cleanup). Alongside: `install/kubernetes/` manifests,
`install/config/` examples, PostgreSQL `init.sql`, helper scripts, and Dockerfiles including
`Dockerfile.mpifileutils` for the DM job image. Runtime/API usage lives in `docs/` (`docs/api/`,
`docs/operations-runbook.md`).

**When changing runtime settings, env-var names, or API request options, update the matching
`install/` doc.**
