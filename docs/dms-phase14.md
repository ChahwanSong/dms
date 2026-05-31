# DMS Phase 14 Implementation Prompt

이 문서는 `docs/dms-phase13.md` 완료 이후 열네 번째 구현 에이전트에게 전달할 compact 구현 프롬프트다. Phase 14의 목표는 Data Management 구현으로 넘어가기 전에 Resource Management와 DMS runtime 기본 구현에서 남아 있는 운영 안정성 gap 중 observability write boundary와 backend adapter selection 문제를 먼저 닫는 것이다.

Phase 14는 앞선 점검에서 나온 3, 6번 항목만 다음 구현 범위로 고정한다.

3. Observability write failure가 core lifecycle을 깨뜨릴 수 있는 문제
6. Backend registry fallback stub 위험과 Kubernetes quota live adapter wiring 보강

중요: Phase 14는 Data Management `scan/sync/rm` live execution이나 VolcanoJob 실행 phase가 아니다. 또한 worker lease/heartbeat renewal이나 maintenance/drain enforcement를 구현하지 않는다. 이번 phase는 observability write 실패가 core lifecycle을 흔들지 않게 만들고, live runtime의 backend selection을 fail-closed하게 만드는 좁은 hardening phase다.

## Phase 14 목표

### Observability Safe Write Boundary

현재 DMS는 operational DB와 observability DB를 분리하지만, diagnostic event 기록 실패가 API/worker의 본래 lifecycle 결과를 오염시킬 수 있는 경로가 남아 있을 수 있다. Observability는 운영 진단에는 중요하지만, core lifecycle source of truth는 operational DB여야 한다.

구현 목표:

1. diagnostic event 기록은 best-effort로 감싼다.
2. observability DB write 실패가 request persistence, auth rejection, plan/run/result state transition을 실패시키지 않는다.
3. `record_event` 직접 호출 경로를 점검하고 `safe_record_event` 또는 동등한 wrapper로 통일한다.
4. wrapper는 실패를 process log 또는 fallback metric에 남기되, caller의 domain result를 바꾸지 않는다.
5. 단, operational DB write 실패는 계속 hard failure로 취급한다.

예시 문제:

- auth failure는 원래 401/403이어야 하는데 observability event insert가 실패해 500으로 바뀌면 API contract가 깨진다.
- worker backend apply는 성공했는데 diagnostic event 기록 실패 때문에 run이 `Failed`로 바뀌면 실제 backend state와 DMS lifecycle이 어긋난다.

최소 테스트:

- observability repository가 exception을 던져도 auth failure response는 원래 401/403으로 반환된다.
- worker 성공 경로에서 diagnostic write만 실패하면 request/run/result는 성공으로 남는다.
- action-required event 기록 실패가 operational query 결과를 비우거나 core state를 변경하지 않는다.

### Backend Registry Fail-Closed and Kubernetes Quota Live Wiring

Phase 13 이후 GPFS backend는 stub 성공이 아니라 IBM Storage Scale command adapter를 갖는다. 그러나 live runtime에서 알 수 없는 backend type이나 미완성 adapter가 stub fallback으로 성공하는 경로는 운영상 위험하다. 또한 Kubernetes namespace quota 경로는 live worker에서 실제 Kubernetes adapter를 사용해야 한다.

구현 목표:

1. live Planner/RM Worker runtime은 unknown backend type을 stub으로 fallback하지 않는다.
2. `BackendAdapterRegistry`는 test/dev stub을 명시적으로 요청한 경우에만 stub adapter를 사용한다.
3. storage mapping sanity가 `Ready`여도 backend type이 live registry에서 지원되지 않으면 worker가 fail-closed한다.
4. fail-closed 결과는 `BackendPreconditionError`, `BackendApplyFailed`, `UnsupportedBackend`, 또는 동등한 action-required issue로 노출한다.
5. Kubernetes namespace quota path는 live runtime에서 `KubernetesNamespaceQuotaLiveAdapter.from_settings(settings)` 또는 동등한 live adapter factory를 사용한다.
6. GPFS Kubernetes namespace quota mapping은 기존 CSI ResourceQuota behavior를 유지한다. GPFS fileset quota 구현과 Kubernetes namespace quota 구현을 혼동하지 않는다.
7. test fixture에서 stub registry 사용 여부를 명시해 production path와 test path가 섞이지 않게 한다.

예시 문제:

- storage mapping typo로 `backend_type=cephfss`가 들어갔는데 stub이 성공하면 실제 filesystem은 생성되지 않았는데 DMS만 성공으로 기록될 수 있다.
- RM Worker Deployment가 settings 없이 registry를 만들면 Kubernetes namespace quota가 live adapter 대신 stub을 사용해 ResourceQuota가 생성되지 않는다.

최소 테스트:

- unknown backend type request는 stub success 없이 fail-closed된다.
- live RM Worker registry construction은 settings를 받아 Kubernetes quota live adapter를 생성한다.
- CLI `dms rm-worker --loop` path가 settings-aware registry를 사용한다.
- test-only stub registry는 명시적 helper나 flag에서만 생성된다.
- GPFS fileset quota와 Kubernetes namespace quota가 서로 다른 adapter path를 탄다는 regression test를 추가한다.

## Testbed Live Verification

Phase 14 live verification은 기존 Vagrant multi-cluster testbed를 사용한다.

기본 전제:

- fresh PostgreSQL operational/observability DB를 사용한다.
- DMS API, DMS Agent DaemonSet, Planner Deployment, RM Worker Deployment를 배포한다.
- Phase 13 long-running RM Worker verifier를 재사용할 수 있다.
- verifier는 `Planner.run_once()` 또는 `RMWorkerRuntime.run_once()`를 직접 호출하지 않는다.

검증 항목:

1. Phase 13 quota/import smoke flow가 여전히 long-running Planner/RM Worker 경유로 성공한다.
2. observability DB outage 또는 failing observability repository 상황에서도 operational 결과가 유지된다.
3. unknown backend mapping/request는 stub success 없이 fail-closed된다.
4. Kubernetes namespace quota request가 live RM Worker Deployment에서 실제 ResourceQuota를 생성/수정한다.

검증 결과는 `docs/dms-phase14-verification.md`와 `docs/dms-done.md`에 기록한다.

## Phase 14에서 하지 않을 것

다음은 Phase 14 범위가 아니다.

- Data Management `scan/sync/rm` live execution
- VolcanoJob create/watch/terminate
- DM Worker long-running runtime 검증
- worker lease/heartbeat renewal과 stale recovery guard
- maintenance/drain enforcement와 control API/CLI
- mpifileutils image build 또는 live execution
- automatic quota drift cron/controller
- automatic expiration sweep cron/controller
- production Helm chart 완성
- GPFS live testbed 구축
- WekaFS/Lustre live implementation

## Phase 14 이후 다음 작업 리스트

`docs/dms-phase13.md`에 있던 Data Management 후보는 Phase 14 hardening 이후 다음 작업으로 유지한다. Phase 번호는 새 hardening phase를 끼워 넣었으므로 Phase 15로 밀어 쓴다.

### Phase 15A: Data Management Read-only Scan Preflight

- filesystem resource boundary를 read-only scan target으로 사용
- DM Agent report 기반 candidate pool
- POSIX identity/mount/tool preflight
- VolcanoJob 이전 local scan preflight 검증

### Phase 15B: DM Worker Runtime and VolcanoJob Skeleton

- `dms dm-worker --loop` Deployment
- VolcanoJob create/watch/delete skeleton
- job lease/recovery
- artifact URI and preview lifecycle

### Phase 15C: Filesystem Policy and Initialize

- filesystem default quota policy
- `filesystem.initialize`
- `reset_quota_to_default=true`
- quota clear/unlimited lifecycle

권장 순서는 Phase 14 hardening을 먼저 닫고, 그 다음 Phase 15A로 Data Management read-only scan preflight를 구현한 뒤, Phase 15B로 DM Worker/VolcanoJob live execution을 여는 것이다. Phase 15C는 filesystem lifecycle 정책 확장이므로 Data Management preflight와 분리해도 된다.
