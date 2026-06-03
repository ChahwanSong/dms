# DMS Phase 18 Implementation Prompt: Operational Maintenance, Drain, and Recovery Guard

이 문서는 `docs/dms-phase17.md` 완료 이후 열여덟 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 18의 목표는 **maintenance/drain mode의 실제 runtime enforcement, planned shutdown/startup recovery runbook 자동화, long-running worker lease heartbeat renewal과 stale recovery guard**를 단순한 운영 workflow로 닫는 것이다.

이 문서는 `docs/dms-design.md`를 기준으로 하지 않는다. 현재 기준은 `docs/dms-done.md`, Phase 13-17 문서, `src/`, `install/`에 실제 구현된 상태다.

Phase 18은 Data Management live execution을 열기 전 운영 안전 phase다. Phase 17까지 DMS는 live Kubernetes ResourceQuota, CephFS/GPFS filesystem adapter, mTLS 인증 경계, long-running Planner/RM Worker Deployment를 갖췄다. 하지만 운영자가 DMS source update, control cluster reboot, planned shutdown을 수행할 때 runtime을 안전하게 멈추고 재개하는 DMS-level workflow는 아직 없다.

## Phase 18 시작 전 문제

현재 구현 기준:

- `dms_control_state` table은 이미 있다.
  - `maintenance_mode`
  - `drain_mode`
  - `scheduling_blocked`
  - `reason`
  - `changed_by`
  - `changed_at`
- `DmsRepository.control_state()`는 기본 row를 생성하고 조회한다.
- 하지만 control state를 변경하는 API/CLI가 없다.
- `RMWorkerRuntime.run_once()`와 `DMWorkerRuntime.run_once()`는 `dms_control_state`를 보지 않고 `list_claimable_plans()`로 바로 claim한다.
- `list_claimable_plans()`는 `plans.status='Planned'`만 확인한다.
- `DmsRepository.heartbeat_run()`은 존재하지만 worker runtime에서 long backend call 중 주기적으로 호출하지 않는다.
- `mark_stale_runs()`는 expired lease를 `StaleClaim`으로 표시하지만, operator-facing recovery workflow는 `GET /api/v1/operations/runs/stale` 조회 수준이다.
- `install/RUNBOOK.md`의 upgrade 절차는 `dms-planner`와 `dms-rm-worker`를 수동 scale down/up하고, ingress에서 write를 가능하면 막으라고 안내한다.
- `install/scripts/`에는 planned shutdown, startup recovery, drain/resume 자동화 script가 없다.

`docs/dms-done.md`도 Phase 17까지 완료 범위에서 다음을 미구현으로 남긴다.

- maintenance/drain mode의 full operational workflow
- planned shutdown/startup recovery runbook 자동화
- long-running worker lease heartbeat renewal과 stale recovery guard

## Phase 18 원칙

Phase 18은 복잡한 controller를 만들지 않는다. 단순한 해법으로 운영자가 신뢰할 수 있는 stop/resume 경계를 만든다.

핵심 원칙:

1. **DB control state is the source of truth**
   - maintenance/drain 여부는 operational DB의 `dms_control_state`만 본다.
   - Kubernetes Deployment scale 상태나 ingress 상태는 보조 실행 수단이다.

2. **Maintenance is read-only**
   - Phase 18의 maintenance mode는 새 mutating operational request를 reject한다.
   - read/query endpoint와 control endpoint는 계속 허용한다.
   - "maintenance 중 request를 queue에 저장만 하고 나중에 실행" 정책은 Phase 18 범위가 아니다.

3. **Drain blocks new backend side effects**
   - drain mode에서는 새 plan claim이 없어야 한다.
   - 이미 claim되어 실행 중인 run은 강제로 kill하지 않고 완료되도록 둔다.
   - drain readiness는 active run이 사라질 때까지 기다린다.

4. **No automatic replay after stale**
   - expired active run은 자동 재실행하지 않는다.
   - `StaleClaim`, `RecoveryNeeded`, `UnknownAfterSideEffect`는 operator 확인 대상이다.
   - 실제 backend side effect가 있었을 수 있는 run은 action-required로 남긴다.

5. **Heartbeat prevents false stale**
   - long-running backend call 중에는 worker가 run lease를 갱신한다.
   - heartbeat는 stale guard를 보조할 뿐, backend 작업을 취소하거나 성공으로 간주하지 않는다.

6. **Scripts automate the runbook, not the datacenter**
   - DMS script는 DMS API, Kubernetes Deployment scale/rollout, status query를 조합한다.
   - Kubernetes node `cordon/drain/reboot` 자체를 DMS API가 직접 수행하지 않는다.
   - 실제 node drain은 운영자의 cluster operation이며, DMS는 그 전에 안전한 scheduling block과 recovery check를 제공한다.

## Phase 18 목표

Phase 18의 핵심 기능은 다음 일곱 가지다.

1. **Control state API**
2. **Maintenance/drain runtime enforcement**
3. **Drain/readiness and startup recovery checks**
4. **Worker heartbeat renewal**
5. **Stale recovery guard**
6. **Operational plan/run work summary queries**
7. **Install runbook automation scripts and docs**

구현 완료 기준:

- 운영자가 API로 maintenance/drain/resume 상태를 변경할 수 있다.
- maintenance/drain 상태 변경은 `control_mutations`에 기록된다.
- maintenance/drain 중 새 Resource Management/Data Management mutating request는 409로 reject된다.
- maintenance/drain 중 operational query, action-required, stale runs, worker-agent-health, control-state query는 계속 동작한다.
- drain/scheduling blocked 상태에서 RM/DM Worker는 새 plan을 claim하지 않는다.
- Planner는 기존처럼 planning을 수행해도 되지만, Phase 18에서는 maintenance 중 mutating request intake를 reject하므로 새 plan이 계속 쌓이지 않아야 한다.
- running/applying/verifying run은 worker heartbeat로 `lease_expires_at`이 갱신된다.
- worker heartbeat가 없거나 process가 죽은 run은 startup recovery check 또는 worker loop에서 stale/recovery 대상으로 노출된다.
- stale/recovery 대상은 자동 재실행되지 않는다.
- 운영자가 현재 backlog와 active work를 볼 수 있도록 active plan/run 목록과 상태별 count 조회 API를 제공한다.
- work summary query는 drain/readiness script에서도 재사용할 수 있어야 한다.
- planned shutdown script는 drain 진입, active run wait, worker scale down, status evidence 출력까지 수행한다.
- startup recovery script는 DB/control state, stale/recovery run, action-required, worker/agent health를 조회하고 필요 시 resume 전 operator 확인을 요구한다.
- source update runbook은 Phase 18 drain/resume script를 사용하도록 갱신된다.

## API Surface

추가할 endpoint는 operational query/control namespace 아래에 둔다.

### Get Control State

```http
GET /api/v1/operations/control-state
```

Response:

```json
{
  "maintenance_mode": false,
  "drain_mode": false,
  "scheduling_blocked": false,
  "reason": "",
  "changed_by": "system",
  "changed_at": "2026-06-03T00:00:00Z"
}
```

### Enter Maintenance

```http
POST /api/v1/operations/control-state:enter-maintenance
```

Request:

```json
{
  "reason": "source update 2026-06-03",
  "block_scheduling": true
}
```

Behavior:

- set `maintenance_mode=1`
- set `scheduling_blocked=1`
- set `drain_mode=0`
- record `control_mutations.mutation_kind='control.enter_maintenance'`
- keep read/query endpoints available
- reject new mutating operational requests

`block_scheduling=false`는 Phase 18에서 허용하지 않아도 된다. 단순성을 위해 maintenance는 항상 scheduling blocked로 취급한다.

### Begin Drain

```http
POST /api/v1/operations/control-state:begin-drain
```

Request:

```json
{
  "reason": "planned control cluster reboot"
}
```

Behavior:

- set `maintenance_mode=1`
- set `drain_mode=1`
- set `scheduling_blocked=1`
- record `control_mutations.mutation_kind='control.begin_drain'`
- return current active run summary

Response should include enough status for scripts:

```json
{
  "control_state": {
    "maintenance_mode": true,
    "drain_mode": true,
    "scheduling_blocked": true,
    "reason": "planned control cluster reboot"
  },
  "active_runs": {
    "count": 1,
    "states": {
      "Applying": 1
    }
  },
  "ready_for_shutdown": false
}
```

### Drain Status

```http
GET /api/v1/operations/drain-status
```

Response:

```json
{
  "control_state": {
    "maintenance_mode": true,
    "drain_mode": true,
    "scheduling_blocked": true
  },
  "active_runs": {
    "count": 0,
    "runs": []
  },
  "blocked_or_recovery_runs": {
    "count": 0,
    "runs": []
  },
  "action_required": {
    "count": 0
  },
  "ready_for_shutdown": true
}
```

Active run states:

```text
Claimed
Running
Applying
Verifying
```

Recovery/attention states:

```text
StaleClaim
RecoveryNeeded
UnknownAfterSideEffect
BackendApplyFailed
Blocked
```

`ready_for_shutdown=true` requires:

- `scheduling_blocked=true`
- active run count is zero
- no `UnknownAfterSideEffect`
- no `RecoveryNeeded`
- no `BackendApplyFailed`

`Blocked` may be present for Data Management preview confirm waits. Phase 18 should report it but does not need to block control plane shutdown unless the blocked state implies active external side effect. For RM operations, `Blocked` should be treated as operator attention.

### Resume

```http
POST /api/v1/operations/control-state:resume
```

Request:

```json
{
  "reason": "upgrade completed"
}
```

Behavior:

- reject resume if `UnknownAfterSideEffect`, `RecoveryNeeded`, or `BackendApplyFailed` exists, unless `force=true`
- set `maintenance_mode=0`
- set `drain_mode=0`
- set `scheduling_blocked=0`
- record `control_mutations.mutation_kind='control.resume'`

Optional forced request:

```json
{
  "reason": "operator accepted existing action-required items",
  "force": true
}
```

Forced resume must record `force=true` in `control_mutations.payload`.

### Mark Stale Runs

```http
POST /api/v1/operations/runs:mark-stale
```

Behavior:

- authenticated control operation
- calls the stale/recovery guard path without requiring worker pods to be running
- returns updated stale/recovery summary
- records `control_mutations.mutation_kind='runs.mark_stale'`

This endpoint is for startup recovery automation and planned shutdown status refresh. It must not requeue stale work automatically.

### Work Summary

```http
GET /api/v1/operations/work-summary
```

이 endpoint는 운영자가 현재 DMS가 처리 중이거나 대기 중인 work를 한 번에 볼 수 있게 한다. `drain-status`는 shutdown readiness에 초점을 맞추고, `work-summary`는 평상시 운영 dashboard와 장애 triage에 초점을 맞춘다.

Response:

```json
{
  "plans": {
    "total_active": 3,
    "by_status": {
      "Planned": 2,
      "Claimed": 1
    },
    "by_worker_role": {
      "RM": 2,
      "DM": 1
    }
  },
  "runs": {
    "total_active": 2,
    "by_state": {
      "Applying": 1,
      "Verifying": 1
    },
    "by_worker_role": {
      "RM": 2
    },
    "by_worker_id": {
      "rm-worker-0": 1,
      "rm-worker-1": 1
    },
    "lease_expiring_soon": 1,
    "stale_or_recovery": 0
  },
  "requests": {
    "action_required": 0
  }
}
```

Active plan statuses:

```text
Planned
Claimed
Running
Applying
Verifying
Blocked
```

Active run states:

```text
Claimed
Running
Applying
Verifying
```

Attention/recovery states:

```text
Blocked
StaleClaim
RecoveryNeeded
UnknownAfterSideEffect
BackendApplyFailed
```

`lease_expiring_soon` default window는 60초로 시작한다. Query parameter로 `lease_expiring_within_seconds`를 받을 수 있다.

### Active Plans

```http
GET /api/v1/operations/plans/active
```

Suggested query parameters:

- `status`: optional repeated status filter.
- `worker_role`: `RM` 또는 `DM`.
- `limit`: default 100.

Response item은 운영자가 request/resource와 연결해 판단할 수 있어야 한다.

```json
{
  "plan_id": "plan_...",
  "request_id": "req_...",
  "worker_role": "RM",
  "status": "Planned",
  "operation_kind": "kubernetes.namespace_quota.create",
  "resource_key": "cluster-b:team-a",
  "attempt_count": 0,
  "created_at": "2026-06-03T00:00:00Z",
  "updated_at": "2026-06-03T00:00:00Z"
}
```

### Active Runs

```http
GET /api/v1/operations/runs/active
```

Suggested query parameters:

- `state`: optional repeated state filter.
- `worker_role`: `RM` 또는 `DM`.
- `worker_id`: optional exact worker filter.
- `lease_expiring_within_seconds`: optional integer.
- `limit`: default 100.

Response item은 lease 상태와 plan/request/resource 연결 정보를 포함해야 한다.

```json
{
  "run_id": "run_...",
  "plan_id": "plan_...",
  "request_id": "req_...",
  "worker_id": "rm-worker-0",
  "worker_role": "RM",
  "state": "Applying",
  "lease_expires_at": "2026-06-03T00:05:00Z",
  "heartbeat_at": "2026-06-03T00:03:20Z",
  "lease_seconds_remaining": 100,
  "lease_expiring_soon": false,
  "operation_kind": "filesystem.create",
  "resource_key": "cephfs-a:project-alpha"
}
```

Implementation guidance:

- `work-summary`, `plans/active`, `runs/active`는 read-only operational query다.
- maintenance/drain 중에도 동작해야 한다.
- `runs/active`와 `work-summary`는 `drain-status`와 같은 active/recovery state 분류 helper를 공유한다.
- 가능하면 plan/run 조회는 request와 join해 `operation_kind`, `resource_kind`, `resource_key`, `requester_id`를 함께 반환한다.
- secrets 또는 full payload를 기본 응답에 포함하지 않는다. 필요하면 기존 request history endpoint로 따라가게 한다.

## Mutating Request Enforcement

Phase 18 should add one small gate in API request submission.

Existing mutating operational requests go through `submit_request(...)`. Before persisting the request, check `repository.control_state()`.

Reject when:

```text
maintenance_mode == true OR drain_mode == true OR scheduling_blocked == true
```

Response:

```http
409 Conflict
```

Body:

```json
{
  "detail": {
    "reason": "maintenance_mode_active",
    "control_state": {
      "maintenance_mode": true,
      "drain_mode": true,
      "scheduling_blocked": true
    }
  }
}
```

Do not create request/plan/run/result rows for rejected maintenance intake. It is an API control-plane rejection, not a lifecycle request.

Allowed during maintenance/drain:

- `/healthz`
- authenticated operational query endpoints
- control-state/drain/status/recovery endpoints
- work-summary, active plans, active runs query
- action-required query
- stale runs query
- worker-agent-health query

Disallowed during maintenance/drain:

- Resource Management create/update/block/delete/import/check/sync/audit/sweep requests
- Data Management scan/sync/rm requests
- Data Management confirm/cancel requests if they would start new execution
- storage mapping, identity mapping, default quota policy mutations unless explicitly treated as control-plane maintenance operations

For simplicity, Phase 18 may reject all non-control mutating API calls while maintenance is active. If a specific read-only mutation already exists, document it explicitly instead of creating a broad exception.

## Worker Scheduling Enforcement

Add a simple guard before plan claim.

Recommended repository helper:

```python
def scheduling_blocked(self) -> bool:
    state = self.control_state()
    return bool(
        state["maintenance_mode"]
        or state["drain_mode"]
        or state["scheduling_blocked"]
    )
```

Worker behavior:

```python
def run_once(self) -> int:
    self.repository.mark_stale_runs(actor=self.worker_id)
    if self.repository.scheduling_blocked():
        return 0
    plans = self.repository.list_claimable_plans(...)
    ...
```

Important:

- Do not mark a worker as failed just because scheduling is blocked.
- Do not claim a plan and then discover drain state.
- If control state flips to drain after a plan is already claimed, the active run is allowed to finish.
- If active run fails, existing failure/action-required behavior remains.

To avoid race conditions, `claim_plan()` should also check scheduling state in the same DB transaction before changing `plans.status` to `Claimed`. The worker pre-check is ergonomic; the transaction check is the guard.

Recommended `claim_plan()` rule:

```text
if dms_control_state.scheduling_blocked or maintenance_mode or drain_mode:
    raise SchedulingBlocked
```

The worker should treat `SchedulingBlocked` as a normal no-op and return 0.

## Worker Heartbeat Renewal

Current `DmsRepository.heartbeat_run(run_id, lease_seconds)` exists but is not used in long backend calls. Phase 18 should use it.

Simple implementation:

- Add a tiny `RunHeartbeat` context manager in `workers.py`.
- It starts a daemon thread while a run is active.
- It calls `repository.heartbeat_run(run_id, lease_seconds)` every interval.
- Stop it before writing terminal result.

Recommended interval:

```text
heartbeat_interval = max(5, min(60, lease_seconds // 3))
```

Use it around side-effect windows:

```python
with RunHeartbeat(repository, run_id, lease_seconds, actor=worker_id):
    adapter_result = self._apply(plan)
```

For DM Worker, use the same pattern around preview/execution adapter calls. Phase 18 does not need to implement live VolcanoJob watch, but the heartbeat utility should be reusable when that arrives.

Heartbeat behavior:

- A successful heartbeat extends `lease_expires_at`.
- Heartbeat update should not change request/plan/run lifecycle state.
- Heartbeat errors should be logged through `safe_record_event` when practical, but should not immediately mark the run failed while a backend call may be in progress.
- If the process dies, heartbeat stops and stale recovery guard handles the expired lease.

Tests should use a short lease and a fake slow adapter to prove `lease_expires_at` moves forward while the call is running.

## Stale Recovery Guard

Phase 18 should formalize current stale behavior as a safety contract.

Rules:

- Expired active run is moved to `StaleClaim` or `RecoveryNeeded`.
- No stale run is automatically requeued to `Planned`.
- No new backend side effect is started from a stale run without explicit operator action.
- Query endpoints must expose enough evidence for operator decision.

Recommended state mapping:

```text
Claimed expired
  -> StaleClaim

Running/Applying/Verifying expired
  -> RecoveryNeeded
```

Reason:

- `Claimed` may mean worker died before starting backend side effect.
- `Running`, `Applying`, `Verifying` may have already touched backend state.
- The safe default is operator recovery, not automatic retry.

If changing existing `mark_stale_runs()` from always `StaleClaim` is too broad for Phase 18, use this smaller step:

- keep `StaleClaim` state
- add `verification_summary.recovery_required=true` or action-required issue for expired `Running/Applying/Verifying`
- document that no automatic retry occurs

Preferred implementation is the explicit state split because `RecoveryNeeded` already exists in `LifecycleState`.

Drain and startup recovery should call the same guard path.

## Planned Shutdown Automation

Add script:

```text
install/scripts/dms-planned-shutdown.sh
```

Required env:

```text
DMS_API_URL
DMS_TOKEN
DMS_CLIENT_CERT
DMS_CLIENT_KEY
DMS_CA_CERT
DMS_CONTROL_CONTEXT
DMS_NAMESPACE
```

Suggested optional env:

```text
DMS_DRAIN_TIMEOUT_SECONDS=600
DMS_PLANNER_REPLICAS=0
DMS_RM_WORKER_REPLICAS=0
DMS_DM_WORKER_REPLICAS=0
```

Script flow:

1. POST `control-state:begin-drain`.
2. Poll `GET /api/v1/operations/drain-status`.
3. Exit non-zero on timeout or unresolved recovery issue.
4. Scale `deploy/dms-planner`, `deploy/dms-rm-worker`, `deploy/dms-dm-worker` to 0.
5. Print final evidence:
   - control state
   - planned backlog count
   - active run count
   - stale/recovery run count
   - action-required count
   - work summary
   - deployment replica summary

The script should not drain Kubernetes nodes. After it succeeds, the operator may run cluster-level `kubectl drain`, VM reboot, OS patching, or control cluster maintenance.

## Startup Recovery Automation

Add script:

```text
install/scripts/dms-startup-recovery-check.sh
```

Script flow:

1. Wait for PostgreSQL connectivity indirectly by checking DMS API readiness or by running `dms migrate` Job status if available.
2. Wait for `deploy/dms-api` rollout.
3. POST `/api/v1/operations/runs:mark-stale`.
4. GET `/api/v1/operations/drain-status`.
5. GET `/api/v1/operations/work-summary`.
6. GET `/api/v1/operations/runs/stale`.
7. GET `/api/v1/operations/action-required`.
8. GET `/api/v1/operations/worker-agent-health`.
9. Exit non-zero if `RecoveryNeeded`, `UnknownAfterSideEffect`, or `BackendApplyFailed` exists.
10. Print resume command hint but do not auto-resume unless `--resume` is passed.

Optional `--resume`:

- POST `control-state:resume`.
- Scale `dms-planner` and `dms-rm-worker` back to desired replica count.
- Wait for rollout status.
- Run `install/scripts/verify-install.sh`.

The default should be conservative: check first, resume explicitly.

## Resume Automation

Add script:

```text
install/scripts/dms-resume.sh
```

Script flow:

1. GET drain status.
2. Refuse resume if recovery/action-required blockers exist unless `--force`.
3. Scale workers to configured replicas.
4. POST `control-state:resume`.
5. Wait for rollout status.
6. Run lightweight health checks.

Reason for scaling before or after resume:

- Scaling before resume is safe because scheduling remains blocked until control state is cleared.
- Once pods are ready, `resume` allows immediate claim.
- This avoids a window where scheduling is open but no workers are ready.

## Source Update Workflow After Phase 18

Update `install/RUNBOOK.md` upgrade section to use Phase 18 scripts.

Recommended workflow:

```bash
install/scripts/dms-planned-shutdown.sh

pg_dump "$DMS_DATABASE_URL" > dms-operational-$(date +%Y%m%d%H%M%S).sql
pg_dump "$DMS_OBSERVABILITY_DATABASE_URL" > dms-observability-$(date +%Y%m%d%H%M%S).sql

kubectl --context "$DMS_CONTROL_CONTEXT" -n "$DMS_NAMESPACE" delete job dms-migrate --ignore-not-found=true
kubectl --context "$DMS_CONTROL_CONTEXT" apply -f /tmp/dms-control-plane.yaml
kubectl --context "$DMS_CONTROL_CONTEXT" -n "$DMS_NAMESPACE" wait --for=condition=complete job/dms-migrate --timeout=180s

kubectl --context "$DMS_CONTROL_CONTEXT" -n "$DMS_NAMESPACE" set image deploy/dms-api api="$NEW_DMS_IMAGE"
kubectl --context "$DMS_CONTROL_CONTEXT" -n "$DMS_NAMESPACE" set image deploy/dms-planner planner="$NEW_DMS_IMAGE"
kubectl --context "$DMS_CONTROL_CONTEXT" -n "$DMS_NAMESPACE" set image deploy/dms-rm-worker rm-worker="$NEW_DMS_IMAGE"

kubectl --context "$DMS_CONTROL_CONTEXT" -n "$DMS_NAMESPACE" rollout status deploy/dms-api --timeout=180s
install/scripts/dms-startup-recovery-check.sh
install/scripts/dms-resume.sh
```

API availability:

- `dms-api` remains `replicas: 2`.
- API rollout can be rolling.
- Maintenance mode rejects new mutating requests but query/status endpoints remain available.
- Backend side effects are stopped by scheduling block, not by hoping workers are scaled down fast enough.

Manifest improvements recommended in Phase 18:

- Add explicit rolling update strategy for `dms-api`:

```yaml
strategy:
  type: RollingUpdate
  rollingUpdate:
    maxUnavailable: 0
    maxSurge: 1
```

- Add `PodDisruptionBudget` for `dms-api` with `minAvailable: 1`.
- Keep worker PDB out of scope unless workers are scaled above 1. Worker safety comes from DB state, lease, and scheduling guard.

## Install Documentation Updates

Update:

- `install/RUNBOOK.md`
  - Replace manual upgrade drain guidance with Phase 18 scripts.
  - Add planned control cluster reboot procedure.
  - Add startup recovery check procedure.
- `install/README.md`
  - Mention control-state API and scripts.
  - Keep production mTLS/token invocation style.
- `install/CONFIGURATION.md`
  - Add optional script env vars.
- `install/scripts/verify-install.sh`
  - Include `GET /api/v1/operations/control-state`.
  - Include `GET /api/v1/operations/work-summary`.
  - Include `GET /api/v1/operations/plans/active` and `GET /api/v1/operations/runs/active`.

Do not claim production Helm/Kustomize packaging is complete. Phase 18 may improve raw manifests and scripts only.

## Tests

Required unit/regression coverage:

1. `GET /api/v1/operations/control-state` returns default normal state.
2. `POST control-state:enter-maintenance` sets maintenance and scheduling blocked.
3. `POST control-state:begin-drain` sets maintenance, drain, and scheduling blocked.
4. `POST control-state:resume` clears all flags.
5. control state mutations are recorded in `control_mutations`.
6. mutating RM request returns 409 during maintenance and creates no request row.
7. operational query endpoint still works during maintenance.
8. RM Worker does not claim a `Planned` plan while scheduling is blocked.
9. `claim_plan()` transaction refuses claim if scheduling becomes blocked between list and claim.
10. RM Worker resumes claim after control-state resume.
11. heartbeat context extends `lease_expires_at` during a slow fake backend call.
12. expired `Claimed` run becomes `StaleClaim`.
13. expired `Applying` or `Verifying` run becomes `RecoveryNeeded` or equivalent action-required guarded state.
14. stale/recovery run is not automatically requeued to `Planned`.
15. `/api/v1/operations/runs:mark-stale` works without worker pods.
16. drain status reports active run counts and readiness accurately.
17. resume without `force` refuses when recovery blockers exist.
18. `GET /api/v1/operations/work-summary` reports plan/run counts by status, worker role, and worker id.
19. `GET /api/v1/operations/plans/active` lists active plans with request/resource metadata and supports status/worker-role filters.
20. `GET /api/v1/operations/runs/active` lists active runs with lease metadata and supports state/worker-role/worker-id filters.
21. operational work query endpoints remain available during maintenance/drain.
22. script shell syntax checks pass for all new scripts.

Existing tests to preserve:

- Phase 13 duplicate claim safety.
- Phase 14 observability safe-write behavior.
- Phase 16 mTLS auth coverage for operational query endpoints.
- Phase 17 backend-neutral Kubernetes ResourceQuota adapter behavior.

## Testbed Verification

Use the existing Vagrant multi-cluster testbed. Keep it resource-efficient.

Recommended live verification:

1. Deploy DMS API, Planner, RM Worker, Agent DaemonSets as in Phase 13/15/16.
2. Confirm normal control state.
3. Submit a small Kubernetes namespace quota or filesystem check request and confirm long-running worker path still works.
4. Enter drain mode.
5. Submit a mutating RM request and verify HTTP 409 with no operational request row.
6. Create or leave a `Planned` fixture and verify RM Worker does not claim it while scheduling is blocked.
7. Query `work-summary`, `plans/active`, and `runs/active`; verify backlog/active counts match the fixture state.
8. Query drain status and verify active counts.
9. Resume and verify the same worker can claim new work.
10. Create an expired run fixture and call `runs:mark-stale`; verify stale/recovery query and action-required exposure.
11. Run `dms-planned-shutdown.sh --dry-run` or equivalent non-destructive mode if provided.
12. Scale worker Deployment down/up through script and verify API remains queryable.
13. Run `dms-startup-recovery-check.sh` and `dms-resume.sh` in the testbed profile.

If testbed resources are tight, avoid running large filesystem mutation loops. One small ResourceQuota request plus DB fixtures is enough to verify the new operational workflow.

Record final evidence in:

```text
docs/dms-phase18-verification.md
docs/dms-done.md
```

## Out Of Scope

Phase 18 does not implement:

- Kubernetes node `cordon/drain/reboot` controller inside DMS
- automatic OS patching or VM reboot orchestration
- queued-write maintenance policy
- automatic replay of stale backend work
- exact backend-specific repair workflows for every `RecoveryNeeded` case
- production Helm/Kustomize chart completion
- JWT/OIDC/RBAC policy schema
- Data Management live VolcanoJob create/watch/terminate
- mpifileutils image build or live execution
- filesystem expiry cron/controller
- Kubernetes quota drift/usage pressure cron/controller
- full database expand/contract migration framework

## Phase 18 이후 다음 작업 리스트

Phase 18로 maintenance/drain/recovery guard를 닫은 뒤, Data Management 후보는 다시 Phase 19로 진행한다. 구체 구현 프롬프트는 `docs/dms-phase19.md`에 정리한다.

### Phase 19: Data Management Scan and Volcano Runtime Foundation

- filesystem resource boundary를 read-only scan target으로 사용
- DM Agent report 기반 candidate pool
- POSIX identity/mount/tool preflight
- pinned mpifileutils image/tool metadata
- VolcanoJob live adapter skeleton
- read-only `scan` live `dscan` execution
- `sync`/`rm` preflight and preview-safe lifecycle
- `sync`/`rm` confirmed destructive execution은 다음 phase로 defer

### Phase 20A: Sync/Rm Confirmed Execution

- `sync` confirmed `dsync`/`nsync` execution
- `rm` confirmed `drm` execution
- dry-run/preview result hash validation
- destructive operation recovery policy

### Phase 20B: Filesystem Policy and Initialize

- filesystem default quota policy
- `filesystem.initialize`
- `reset_quota_to_default=true`
- quota clear/unlimited lifecycle

권장 순서는 Phase 18 운영 안전 workflow를 먼저 닫고, 그 다음 Phase 19A Data Management read-only scan preflight로 진행하는 것이다. Data Management live execution은 worker heartbeat와 startup recovery guard가 있어야 운영 중단/재시작 상황에서 안전하게 다룰 수 있다.
