# DMS Volcano 스케줄러 상태 (서브프로젝트 C) 설계

- 날짜: 2026-06-25
- 상태: 승인됨 (구현 대기)
- 범위: Volcano 잡/큐/스케줄러 상태를 DMS read 엔드포인트로 노출 + 포탈 대시보드 "Volcano 스케줄러" 패널
- DMS 변경: volcano adapter read 메서드 + operations 엔드포인트 + **dms-api SA RBAC(Volcano read)** + DMS 이미지 재배포

## 1. 배경 / 실현가능성 (확인)

- Volcano 라이브: 큐 `dms-data`/`default`/`root`, `volcano-scheduler`/`volcano-controllers`/`volcano-admission` Running(volcano-system).
- `KubernetesVolcanoAdapter`(`src/dms/adapters/volcano.py`)는 plain `["kubectl", ...]` subprocess 사용. create/get/terminate_job만 있음 → **list/queue/scheduler 읽기 신규 추가**.
- `AppServices.volcano_adapter`가 이미 존재하고 create_app이 `volcano_adapter_from_settings(settings)`로 세팅. 라이브 dms-api는 `DMS_DM_KUBERNETES_MODE=cluster` → **real adapter**. dms-api pod는 kubectl + 클러스터 도달 O. **단 dms-api SA가 Volcano 리소스 권한 없음(Forbidden)** → RBAC 부여 필요.
- VolcanoJob(vcjob)은 `dms` 네임스페이스, 큐는 cluster-scoped, scheduler는 `volcano-system`.

## 2. DMS 변경

### 2.1 `src/dms/adapters/volcano.py`
`KubernetesVolcanoAdapter`에 `volcano_status(*, job_namespace="dms", scheduler_namespace="volcano-system") -> dict` 추가. 3개 `kubectl get -o json`을 **섹션별 fail-soft**(타임아웃 ~10s; 실패 시 해당 섹션 빈 list + errors에 사유)로 실행·집계:

- 큐: `kubectl get queues.scheduling.volcano.sh -o json` → `[{name, state, running, pending, inqueue}]` (`.status.{state,running,pending,inqueue}`, 없으면 0/None)
- 잡: `kubectl get vcjob -n <job_namespace> -o json` → `[{name, namespace, queue, phase, running, pending, succeeded, failed, min_available}]` (`.spec.queue`, `.spec.minAvailable`, `.status.state.phase`, `.status.{running,pending,succeeded,failed}`)
- 스케줄러: `kubectl get pods -n <scheduler_namespace> -o json` → `[{name, phase, ready, restarts}]` (`.status.phase`, containerStatuses ready/restartCount)

반환: `{"queues":[...], "jobs":[...], "scheduler":[...], "errors":{"queues":null|str,"jobs":null|str,"scheduler":null|str}}`.
kubectl 실행은 기존 어댑터처럼 `subprocess.run([...], timeout=...)`, `_run_kubectl_json(args)` 헬퍼로 묶어 재사용(returncode≠0 또는 JSON 파싱 실패 → 해당 섹션 에러).

`StubVolcanoAdapter`(테스트/기본)에도 `volcano_status(...)` 추가: 빈 구조(`{"queues":[],"jobs":[],"scheduler":[],"errors":{...:None}}`) 또는 주입된 stub 반환.

### 2.2 `src/dms/api/routers/operations.py`
신규 라우트 `GET /api/v1/operations/volcano`:
```python
@router.get("/volcano")
def volcano_status(request: Request, services: AppServices = Depends(get_services)) -> dict[str, Any]:
    authenticated_actor(request, services)
    return services.volcano_adapter.volcano_status()
```
(기존 operations 패턴: `authenticated_actor` 먼저, `services.volcano_adapter` 사용.)

### 2.3 RBAC (매니페스트 신규 — `install/kubernetes/` 또는 `deploy/kubernetes/`)
dms-api ServiceAccount(`system:serviceaccount:dms:dms-api`)에 **read-only** 부여:
- ClusterRole: `queues.scheduling.volcano.sh`(get/list/watch) — cluster-scoped.
- ClusterRole 또는 Role: `jobs.batch.volcano.sh`(vcjob, get/list, dms ns) + `pods`(get/list, volcano-system ns).
- 해당 ClusterRoleBinding/RoleBinding을 dms-api SA에 연결.
> ⚠️ 보안: dms-api에 Volcano 리소스 **읽기** 권한만 부여(쓰기 없음). scheduler health용 volcano-system pods read 포함.

### 2.4 테스트
- `volcano_status` 집계: fake kubectl runner(주입 가능한 subprocess 또는 `_run_kubectl_json` 몽키패치)로 큐/잡/scheduler JSON → 정형화 검증 + 섹션 fail-soft(한 kubectl 실패 시 그 섹션만 에러, 나머지 정상).
- 라우트: TestClient + StubVolcanoAdapter로 200 + 스키마.

## 3. 포탈
- BFF `GET /api/operator/dashboard/volcano` → `dms.get_volcano_status(actor)`(dms_client 신규, `_OPS_BASE/volcano`). 단일 호출이라 DmsApiError 전파.
- `dms_client.get_volcano_status(*, actor)` 추가.
- api.ts 타입 `VolcanoStatus` + `operatorApi.dashboard.volcano()`.
- 신규 컴포넌트 `dashboard/VolcanoPanel.tsx`(접기형 Section): 큐 테이블(name·state·running·pending·inqueue) + 활성 잡 테이블(name·queue·phase·running/pending) + 스케줄러 컴포넌트 health(pod·phase·ready·restarts). 섹션 errors는 inline 표기.
- `Dashboard.tsx`에 `<VolcanoPanel/>` 추가(RunsTable/RequestsTable 부근).

## 4. 배포 / 검증
- DMS 이미지 재빌드(`dms:volcano` ← 현 main + C) → dms-api `set image` + **RBAC apply** → 재배포.
- 라이브 RBAC 검증: dms-api pod에서 `kubectl get queues`/`vcjob -n dms`/`pods -n volcano-system` 성공(이전 Forbidden 해소).
- `GET /operations/volcano` 200 + 큐 3개(dms-data 등)·scheduler pods·(활성 잡 있으면) 표시.
- 포탈 빌드 + 배포 + 대시보드 "Volcano 스케줄러" 패널 라이브(큐·scheduler health, 콘솔 에러 0).

## 5. 범위 / 위험
- 범위(사용자 선택 "+큐/스케줄러 health"): 큐 + 활성 vcjob + scheduler/controller/admission pod health.
- 위험: RBAC 확대(api Volcano read), 신규 kubectl 호출(섹션 fail-soft·타임아웃), DMS 이미지 재배포.
- 비범위: Volcano 잡 제어(취소 등은 기존 data.cancel 경유), 과거 잡 이력, 노드별 스케줄링 상세.
