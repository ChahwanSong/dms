# DMS API — 개요와 인증

DMS는 파일시스템, Kubernetes 네임스페이스 쿼터, 데이터 잡(`scan`/`sync`/`rm`)을 **하나의
컨트롤플레인**에서 관리하는 HTTP 제어면이다. 이 문서는 **DMS HTTP API 사용 문서의 인덱스**로,
모든 API가 공유하는 개념(request→plan→run 수명주기), 공통 규약(base path·상태 폴링),
그리고 **인증(운영 = mTLS-verified 프로필)** 을 설명한다. 개별 API 상세는 아래
[API 문서 맵](#5-api-문서-맵)의 각 문서로 이어진다.

> 이 문서군은 **사용법(usage)** 만 다룬다. 컨트롤플레인 배포·mTLS·ingress·migration 등
> **설치**는 범위가 아니다 — [`install/`](../../install/README.md)를 참고한다.

---

## 1. API 개요 — request → plan → run

DMS는 **상태 머신**이다. 모든 변경은 요청으로 영속화되고, planner가 이를 plan으로 바꾸며,
역할별 worker가 lease로 plan을 클레임해 어댑터를 통해 side effect를 적용한다.
**상태가 유일한 진실**이므로, 클라이언트는 요청을 제출한 뒤 상태를 **폴링**한다.

```
POST /api/v1/<...>/<operation>   → 202 { request_id, status: "Persisted", … }   (요청만 영속화, side effect 아직 없음)
    → planner : request → plan (worker_role = RM 또는 DM)
    → worker  : plan claim(lease) → 어댑터로 side effect 적용 → run/result 기록
    → GET /api/v1/operations/requests/{request_id}   (.request.status 폴링)
```

- **POST 응답은 `202 Persisted`이며 결과가 아니다.** 반환된 `request_id`(DM 잡은 이후 `job_id`)로
  상태를 폴링한다. 긴 작업은 전부 **비동기** — blocking 호출이 아니다.
- **worker role로 라우팅**된다: `resource-management`(파일시스템·k8s 쿼터)는 **RM worker**가,
  `data-management`(`scan`/`sync`/`rm`)는 **DM worker**가 처리한다.
- **operations** 라우터는 이 상태를 읽는 **조회 API**(inventory, storage mapping, work summary,
  requests/request-activity, resources, data-jobs 등)와 컨트롤플레인 상태 제어(maintenance/drain/
  resume)를 제공한다 — 대부분 read-only다.

---

## 2. 공통 규약

| 항목 | 값 |
|---|---|
| Base path | 모든 엔드포인트는 **`/api/v1/`** 하위 |
| 라우터 prefix | `/api/v1/resource-management`, `/api/v1/data-management`, `/api/v1/operations`, `/api/v1/agent` |
| Health | `GET /healthz` (**인증 불요** — k8s probe용) |
| 제출 응답 | 변경 요청은 `202 { request_id, status: "Persisted", … }` |
| 상태 폴링 | 범용: `GET /api/v1/operations/requests/{request_id}` · DM 잡: `GET /api/v1/data-management/<op>/jobs/{job_id}` |
| 콜론 액션 | `:confirm` / `:cancel` / `:check` / `:resolve` 등. **zsh는 `"${id}:confirm"` 브레이스로 호출**(`"$id:confirm"`은 수식어로 변형돼 404) |
| 검증 실패 | unknown 필드·경로 위반(`/`·`..`)·옵션 위반 등은 `422`/`400`이며 **영속화되지 않는다** |

`agent` 라우터는 노드 agent(DaemonSet)가 리포트를 POST하는 **내부 경로**이며, 운영자가 직접
호출하지 않는다(설치·구성은 `install/` 참고).

---

## 3. 인증 — 운영 프로필 = mTLS-verified header

운영 컨트롤플레인은 **mTLS-verified header 프로필**로 뜬다.
`install/kubernetes/control-plane.yaml`이 `DMS_REQUIRE_MTLS_HEADER=true` +
`DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`를 세팅한다.

동작:

- **클라이언트는 client certificate로 인증**한다. 신뢰된 ingress/edge proxy가 client cert를
  **검증**하고, 그 cert **subject**와 verify 결과를 evidence header로 upstream(DMS)에 전달한다.
- **DMS는 actor를 cert subject에서 파생**한다: `actor = {DMS_MTLS_ACTOR_PREFIX}<subject>`
  (기본 prefix `mtls:`). 즉 audit actor는 클라이언트가 자칭하는 값이 아니라 **검증된 인증서**가
  결정한다.
- **평문 `x-dms-actor`는 이 프로필에서 신뢰하지 않는다.** 보내더라도 파생 actor와 다르면
  `actor_evidence_conflict`로 거부된다 → 운영에서는 **보내지 않는다**.
- **`DMS_DEFAULT_ACTOR`는 비어 있어야 한다.** `DMS_REQUIRE_MTLS_HEADER=true`인데 값이 설정돼
  있으면 **API startup이 실패**한다(fail-closed).
- **shared bearer token**(`DMS_AUTH_SHARED_TOKEN`)을 mTLS 위에 **겹쳐 쓸 수 있다**(선택). 그럴 땐
  `Authorization: Bearer <token>`도 함께 보낸다.

> 인증서 발급·ingress의 cert 검증/evidence header 전달 구성은
> [`install/dms-02-core.md`](../../install/dms-02-core.md), 인증 관련 env 전체 레퍼런스는
> [`install/dms-06-configuration.md`](../../install/dms-06-configuration.md)를 본다.

### curl 인증 패턴

**(A) 운영 (mTLS-verified) — client cert 사용, `x-dms-actor` 안 보냄:**

```bash
curl -sS \
  --cert operator.crt --key operator.key --cacert dms-api-ca.crt \
  -H "authorization: Bearer $DMS_TOKEN" \          # DMS_AUTH_SHARED_TOKEN을 겹쳐 쓸 때만
  https://dms.example.internal/api/v1/operations/work-summary
# actor는 인증서 subject에서 mtls:<subject>로 파생된다 (x-dms-actor 지정 불필요·불가).
```

**(B) 부연 — dev/testbed 프로필(`DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`):** 인증서 없이 평문
Bearer + `x-dms-actor`로 actor를 직접 지정한다. 이후 API 문서의 예시는 요청/응답 **shape을
읽기 쉽게** 하려고 대부분 이 형태로 표기하지만, **운영에서는 (A)를 쓴다.**

```bash
curl -sS \
  -H "authorization: Bearer $DMS_TOKEN" \
  -H "x-dms-actor: operator" \
  http://<api-host>:<port>/api/v1/operations/work-summary
```

---

## 4. preview → confirm (파괴 방지)

`data.sync`·`data.rm`처럼 **파괴적인** DM 잡은 **preview→confirm 게이트**를 통과해야 실제로
실행된다:

- **preview**: 실복제/삭제 없이 dry-run 인벤토리만 산출 → summary + **fingerprint**를 기록 →
  잡 상태 `ConfirmPending`.
- **confirm**: `POST /api/v1/data-management/jobs/{job_id}:confirm`에
  preview fingerprint(`preview_observed_hash`)를 실어 보낸다. **일치할 때만**(중간 변경 감지)
  execution이 시작된다.
- **confirm 없이는 execution이 없다.** 이 게이트를 우회·약화하지 말 것. 전체 흐름은
  [`data-management.md`](./data-management.md).

---

## 5. API 문서 맵

| 문서 | 라우터 prefix | 내용 |
|---|---|---|
| [파일시스템 RM API](./resource-management-fs.md) | `/api/v1/resource-management/filesystems` · `/storage-mappings` | 디렉토리 생성·chmod/chown·quota·block/import/delete, storage mapping CRUD, sanity `:check` |
| [k8s 쿼터 RM API](./resource-management-k8s.md) | `/api/v1/resource-management/kubernetes/namespace-quotas` | 네임스페이스 ResourceQuota 생성·변경·삭제·expiration-sweep·audit |
| [DM 데이터 잡 API](./data-management.md) | `/api/v1/data-management` | `scan`/`sync`/`rm`, preview→confirm, 정책(policies), identity-denylist |
| [operations 조회 API](./operations.md) | `/api/v1/operations` | inventory·storage mapping·work summary·requests·resources·data-jobs 조회, 컨트롤플레인 상태(maintenance/drain) |

운영 절차(점검·유지보수·장애 대응)는 [운영 런북](../operations-runbook.md)에 있다.

---

## 다음 문서

- 파일시스템 RM API — [`docs/api/resource-management-fs.md`](./resource-management-fs.md)
- k8s 쿼터 RM API — [`docs/api/resource-management-k8s.md`](./resource-management-k8s.md)
- DM 데이터 잡 API — [`docs/api/data-management.md`](./data-management.md)
- operations 조회 API — [`docs/api/operations.md`](./operations.md)
- 운영 런북 — [`docs/operations-runbook.md`](../operations-runbook.md)
- 설치(코어·mTLS·ingress) — [`install/dms-02-core.md`](../../install/dms-02-core.md)
- 환경변수 레퍼런스 — [`install/dms-06-configuration.md`](../../install/dms-06-configuration.md)
