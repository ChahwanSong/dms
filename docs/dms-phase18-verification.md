# DMS Phase 18 Verification

검증일: 2026-06-03

Phase 18은 maintenance/drain runtime enforcement, worker heartbeat renewal,
stale/recovery guard, operational work query, install runbook script를 구현했다.

## Local Regression

명령:

```bash
cd /home/mason/workspace/dms
python3 -m py_compile src/dms/api.py src/dms/workers.py src/dms/repositories.py src/dms/query.py tests/test_phase18_operational_controls.py tests/test_phase16_mtls_auth.py
bash -n install/scripts/*.sh scripts/verify-phase18-testbed.sh
pytest -q tests/test_phase18_operational_controls.py tests/test_phase16_mtls_auth.py
pytest -q
```

결과:

```text
tests/test_phase18_operational_controls.py tests/test_phase16_mtls_auth.py: 32 passed in 17.62s
full pytest: 136 passed in 81.99s
```

검증한 주요 계약:

- maintenance 중 mutating request는 409이고 request row를 만들지 않는다.
- maintenance 중 control/work/action query는 계속 동작한다.
- drain 중 RM worker는 새 plan을 claim하지 않는다.
- `claim_plan()` transaction 내부에서도 scheduling block을 다시 확인한다.
- `RunHeartbeat`가 long-running run의 `lease_expires_at`을 연장한다.
- expired `Claimed`는 `StaleClaim`, expired `Applying`은 `RecoveryNeeded`로 분류된다.
- `runs:mark-stale`, `work-summary`, `plans/active`, `runs/active`, `drain-status`,
  resume blocker/forced resume API가 동작한다.
- Phase 16 mTLS protected endpoint matrix에 Phase 18 endpoint가 포함된다.

## Install Script Runtime Check

세 install script는 local DMS API를 새 SQLite DB로 띄운 뒤 비파괴 옵션으로 직접 실행했다.

준비:

```bash
cd /home/mason/workspace/dms
rm -f /tmp/dms-phase18-script-operational.db /tmp/dms-phase18-script-observability.db
PYTHONPATH=src \
  DMS_DATABASE_URL=sqlite:////tmp/dms-phase18-script-operational.db \
  DMS_OBSERVABILITY_DATABASE_URL=sqlite:////tmp/dms-phase18-script-observability.db \
  DMS_AUTH_SHARED_TOKEN=phase18-script-token \
  python3 -m dms.cli api --host 127.0.0.1 --port 18018
```

실행:

```bash
DMS_API_URL=http://127.0.0.1:18018 \
  DMS_TOKEN=phase18-script-token \
  DMS_ACTOR=script-operator \
  install/scripts/dms-planned-shutdown.sh \
    --reason 'phase18 script verification' \
    --timeout-seconds 10 \
    --poll-seconds 1 \
    --dry-run

DMS_API_URL=http://127.0.0.1:18018 \
  DMS_TOKEN=phase18-script-token \
  DMS_ACTOR=script-operator \
  install/scripts/dms-startup-recovery-check.sh

DMS_API_URL=http://127.0.0.1:18018 \
  DMS_TOKEN=phase18-script-token \
  DMS_ACTOR=script-operator \
  install/scripts/dms-resume.sh \
    --reason 'phase18 script verification complete' \
    --skip-scale-up
```

결과:

```text
dms-planned-shutdown.sh --dry-run: drain mode entered, ready_for_shutdown=true, work-summary printed, Kubernetes scale down skipped.
dms-startup-recovery-check.sh: runs:mark-stale marked=0, control-state/drain-status/work-summary/runs-stale/action-required/worker-agent-health queried, check passed.
dms-resume.sh --skip-scale-up: maintenance/drain/scheduling_blocked cleared, Kubernetes scale up skipped, work-summary printed.
```

## Testbed Verification

테스트베드 메타데이터:

- testbed: `/home/mason/workspace/testbed`
- Kubernetes: `v1.34.6`
- cluster-a: `c1-control`, `c1-worker`
- cluster-b: `c2-control`, `c2-worker`
- PostgreSQL NodePort: `192.168.56.11:30432`

명령:

```bash
cd /home/mason/workspace/dms
DMS_PHASE18_DB_SUFFIX=20260603_phase18 ./scripts/verify-phase18-testbed.sh
```

결과:

```text
created or reused databases: dms_phase18_20260603_phase18, dms_phase18_obs_20260603_phase18
Phase 18 testbed verification completed with operational DB dms_phase18_20260603_phase18 and observability DB dms_phase18_obs_20260603_phase18
```

테스트베드에서 확인한 내용:

- 두 Kubernetes cluster node가 Ready 상태다.
- testbed PostgreSQL StatefulSet과 NodePort가 Ready 상태다.
- 실제 testbed PostgreSQL DB에 DMS migration을 적용했다.
- `GET /api/v1/operations/control-state` 기본 상태가 normal이다.
- `control-state:enter-maintenance`가 scheduling block을 설정한다.
- maintenance 중 filesystem create request가 409로 거부되고 request row가 생기지 않는다.
- maintenance 중 `work-summary` query가 동작한다.
- `control-state:resume`이 scheduling block을 해제한다.
- testbed PostgreSQL에 request/plan/run fixture를 만들고 `RunHeartbeat` lease renewal을 확인했다.
- expired `Applying` run이 `runs:mark-stale` 후 `RecoveryNeeded`가 됐다.
- `begin-drain`, `drain-status`, `plans/active`, `runs/active`가 동작했다.
- `RecoveryNeeded`가 남아 있으면 resume이 409로 막히고, `force=true` resume만 성공한다.
- `control_mutations`에 `control.enter_maintenance`, `control.resume`,
  `control.begin_drain`, `runs.mark_stale`가 기록됐다.

비고:

- 이번 testbed 검증은 DMS Deployment를 새로 올리지 않았다. 현재 testbed `dms` namespace에는 실행 중인 DMS workload가 없었고, Phase 18의 핵심은 DB-backed control state/API/repository/worker guard이므로 testbed PostgreSQL을 사용하는 비파괴 검증으로 수행했다.
- Kubernetes backend ResourceQuota나 filesystem backend mutation은 Phase 18 검증 대상이 아니다.
