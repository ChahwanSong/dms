# Operations 조회 API

DMS의 **operations 라우터**(`/api/v1/operations`)는 컨트롤플레인 **상태를 읽는** 엔드포인트를 모은
곳이다. request→plan→run 상태 머신이 남긴 결과(요청 이력·리소스 상태·워커/에이전트 상태·인벤토리),
storage mapping 조회, 그리고 **컨트롤플레인 제어 상태**(maintenance/drain)를 노출한다. 대부분
**read-only**이며, 예외는 문서 끝의 컨트롤 상태 mutation 몇 개뿐이다.

- API 개요·인증 전반 → [`README.md`](README.md)
- 파일시스템 RM API → [`resource-management-fs.md`](resource-management-fs.md)
- k8s 네임스페이스 쿼터 RM API → [`resource-management-k8s.md`](resource-management-k8s.md)
- DM 데이터 잡 API → [`data-management.md`](data-management.md)
- 운영 절차(점검·업그레이드·`:resolve`·복구) → [`operations-runbook.md`](../operations-runbook.md)

> 이 문서는 **조회 API 사용법**만 다룬다. 컨트롤플레인 배포·mTLS·ingress·migration 등 **설치**는
> [`install/dms-02-core.md`](../../install/dms-02-core.md)를, 환경변수 레퍼런스는
> [`install/dms-06-configuration.md`](../../install/dms-06-configuration.md)를 본다.

---

## 인증 — 운영 프로필 = mTLS-verified header

운영(production) 배포는 **mTLS-verified header 프로필**이다 — control-plane이
`DMS_REQUIRE_MTLS_HEADER=true` + `DMS_REQUIRE_MTLS_VERIFIED_HEADER=true`로 뜬다. 신뢰된 ingress가
클라이언트 인증서를 검증해 그 결과와 subject를 upstream으로 넘기고, DMS는 **인증서 subject**에서
audit actor를 파생한다(prefix `DMS_MTLS_ACTOR_PREFIX`, 기본 `mtls:`). 평문 `x-dms-actor` 헤더는 이
프로필에서 **신뢰하지 않으며**, `DMS_DEFAULT_ACTOR`는 비어 있어야 한다(설정돼 있으면 API가 기동
거부). 선택적으로 shared bearer token(`DMS_AUTH_SHARED_TOKEN`)을 함께 요구할 수 있다.

따라서 모든 curl은 **클라이언트 인증서**로 호출한다(평문 `x-dms-actor` 없음). 아래 예시는 이 배열을
재사용한다:

```bash
DMS_API_URL=https://dms.example.internal
CURL=(curl -sS
  --cert   /etc/dms-client/client.crt
  --key    /etc/dms-client/client.key
  --cacert /etc/dms-client/ca.crt)
# shared token을 함께 요구하도록 배포된 경우에만:
#   CURL+=(-H "authorization: Bearer $DMS_AUTH_SHARED_TOKEN")
```

조회 엔드포인트는 대부분 GET이라 actor를 소비하지 않지만(감사 레코드를 남기지 않음), **인증 자체는
필요**하다 — 인증서 없이 호출하면 mTLS 프로필에서 거부된다. `GET /healthz`만 인증 불요다.

> **부연 — testbed/dev 프로필.** `DMS_REQUIRE_MTLS_VERIFIED_HEADER=false`인 개발/테스트 배포는
> 인증서 없이 평문 Bearer + `x-dms-actor`로 호출한다:
> `curl -H "authorization: Bearer $TOKEN" -H "x-dms-actor: operator" ...`. 아래 예시의 응답 본문
> 형태는 두 프로필에서 동일하므로, 읽기 편의를 위해 그대로 읽으면 된다.

---

## API 요약

| Method | Path | 동작 |
|---|---|---|
| `GET` | `/inventory` | 클러스터·StorageClass·CSI driver 등 effective 인벤토리 |
| `GET` | `/storage-mappings` | storage mapping 목록(redacted). `?cluster_name=&limit=&offset=` |
| `GET` | `/storage-mappings/{storage_name}` | storage mapping 단건(redacted, `sanity_result`/`rm_candidates` 포함) |
| `GET` | `/requests` | requester별 요청 목록. **`requester_id` 필수**, `?limit=&since=&until=` |
| `GET` | `/request-activity` | **전체** 요청 활동(모든 requester), 최신순·페이지네이션·서버측 검색 |
| `GET` | `/requests/{request_id}` | 요청 단건(상태 + 전이 이력 + 결과 요약) |
| `GET` | `/resources` | 관리 리소스의 materialized 현재 상태 |
| `GET` | `/work-summary` | plan/run/action-required 집계(대시보드용). `?lease_expiring_within_seconds=` |
| `GET` | `/plans/active` | 진행 중 plan. `?status=&worker_role=&limit=` |
| `GET` | `/runs/active` | 진행 중 run(lease 잔여 포함). `?state=&worker_role=&worker_id=&limit=&offset=` |
| `GET` | `/runs/stale` | stale/recovery 대상 run 목록. `?limit=&offset=` |
| `GET` | `/worker-agent-health` | 최근 run + agent report 묶음(빠른 진단용) |
| `GET` | `/action-required` | 조치 필요 항목 집계 |
| `GET` | `/action-required/acks` | 확인(ack) 처리된 항목 목록 |
| `GET` | `/agent-reports` | 노드 agent 리포트. `?freshness=&latest_per_node=&limit=&offset=` |
| `GET` | `/agent-reports/metrics` | 노드 OS 메트릭 샘플(cpu/mem/load/disk). `?since_seconds=` |
| `GET` | `/filesystems/{storage}` · `/{storage}/{dir}` · `/expiring` | 파일시스템 리소스 조회 → [fs API §조회](resource-management-fs.md) |
| `GET` | `/kubernetes/namespace-quotas/{cluster}/{ns}` · `/expiring` | 쿼터 상태(DB↔live) → [k8s API §조회](resource-management-k8s.md) |
| `GET` | `/data-jobs` · `/data-jobs/summary` · `/{job_id}` · `/{job_id}/logs` | DM 잡 조회 → [DM API](data-management.md) |
| `GET` | `/volcano` · `/volcano/job-metrics` | Volcano 스케줄러 상태·잡 메트릭 |
| `GET` | `/diagnostics/{correlation_id}` | correlation_id로 observability 이벤트 추적 |
| `GET` | `/control-state` · `/drain-status` | 컨트롤플레인 제어 상태 조회(아래) |
| `POST` | `/control-state:*` · `/runs:mark-stale` · `/action-required:ack`\|`:unack` | 제어 상태 mutation(절차는 [런북](../operations-runbook.md)) |

> **콜론 액션 주의.** `:enter-maintenance` 같은 콜론 경로는 zsh에서 `"${id}:enter-maintenance"`처럼
> 브레이스로 감싸 호출한다(`"$id:..."`는 수식어로 변형돼 404).

> **페이지네이션.** 이력 테이블(`request-activity`·`data-jobs`·`agent-reports` 등)은 운영 중 수천 행
> 이상으로 자란다. `limit`은 상한이 있고(대개 `le` 제한), 전량 조회 대신 `offset`으로 페이지를
> 넘긴다. 포탈은 이 방식으로 무한 스크롤한다.

---

## 요청 조회

### requester별 목록 — `/requests`

`requester_id`가 **필수**다. `since`/`until`은 `YYYY-MM-DD`(UTC 하루 경계로 확장) 또는 ISO8601을
받는다.

```bash
# 최신 N건
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/requests?requester_id=alice&limit=5" \
  | jq '[.[] | {request_id, operation, resource_key, status, requested_at}]'

# 날짜 범위 — since=2025-05-01 & until=2025-05-31 은 5월 한 달(양끝 포함)
"${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/requests?requester_id=alice&since=2025-05-01&until=2025-05-31" \
  | jq '[.[] | {request_id, status, requested_at}]'
```

### 전체 활동 뷰 — `/request-activity`

`requester_id`를 요구하지 않고 **모든 요청**을 최신순으로 돌려주는 유연한 조회다(운영 액티비티
화면용). 필터를 조합하고, 커지는 이력은 `offset`으로 페이지를 넘긴다.

| 파라미터 | 설명 |
|---|---|
| `operation` | 예: `filesystem.create`, `data.sync`, `kubernetes.namespace_quota.create` |
| `resource_kind` | `filesystem` / `kubernetes_namespace_quota` / `data_job` 등 |
| `status` | 요청 상태(아래 표) |
| `requester_id` | 선택 필터(단건 requester로 좁힐 때) |
| `search` | **서버측** 대소문자 무시 부분검색. requester + 대상(`resource_key`/payload)을 훑어 **전체 이력**을 커버 |
| `since` / `until` | 날짜 범위(위와 동일 규칙) |
| `limit` / `offset` | 기본 `limit=200`(최대 2000), `offset` 페이지네이션 |

```bash
# data.sync 요청만, 최근 200건
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/request-activity?operation=data.sync" \
  | jq '[.[] | {request_id, requester_id, resource_key, status, requested_at}]'

# 경로/스토리지/네임스페이스까지 훑는 서버측 검색 (requester·대상 모두 매칭)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/request-activity?search=cephfs-a&limit=100&offset=0" \
  | jq 'length'
```

### 단건 이력 — `/requests/{request_id}`

요청 하나의 상태·**전이 이력(state_transitions)**·결과 요약을 함께 돌려준다. mutating 호출 뒤
`202 Persisted`로 받은 `request_id`를 여기로 폴링해 terminal 상태를 확인한다.

```bash
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/requests/<request_id>" \
  | jq '{status: .request.status,
         results: [.results[] | {terminal_status, error_category, message}],
         transitions: [.transitions[] | {to_state, reason, actor}]}'
```

**요청 상태값:**

| 상태 | 의미 | terminal |
|---|---|---|
| `Persisted` | 접수됨, planner 대기 | |
| `Planned` | planner가 실행 계획 수립 | |
| `Applying` | worker가 백엔드 실행 중 | |
| `Verifying` | 실행 결과 검증 중 | |
| `Succeeded` | **정상 완료** | ✓ |
| `Failed` | 실패 처리됨 | ✓ |
| `BackendApplyFailed` | 백엔드 실행 실패 | ✓ |
| `Rejected` | validation 실패(원인은 `issues[]`) | ✓ |
| `UnknownAfterSideEffect` | side effect 발생 후 결과 불명 → `:resolve` 필요 | ✓ |
| `Conflict` | 동일 resource에 non-terminal 요청 존재 → 선행 해소 필요 | ✓ |

> `UnknownAfterSideEffect`·`Conflict`·`BackendApplyFailed` 같은 stuck 상태의 수동 처리
> (`POST …/requests/{id}:resolve`)는 [운영 런북](../operations-runbook.md)과
> [fs API](resource-management-fs.md)를 본다.

---

## 리소스 상태 조회

관리 리소스의 materialized 현재 상태를 본다. 리소스 종류별 상세 응답 형태는 각 RM 문서에 있으므로
여기서는 진입점만 정리한다.

```bash
# 전체 리소스의 현재 상태 (desired/observed materialized)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/resources" | jq 'length'

# 파일시스템 — storage별 목록 / 단건 / 만료 목록  (→ resource-management-fs.md)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/filesystems/cephfs-a" | jq
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/filesystems/cephfs-a/project1" | jq
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/filesystems/expiring?status=expired" | jq

# k8s 네임스페이스 쿼터 — DB desired + live + diff  (→ resource-management-k8s.md)
"${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/cluster-a/team-alpha" | jq
"${CURL[@]}" \
  "$DMS_API_URL/api/v1/operations/kubernetes/namespace-quotas/expiring?status=expired" | jq
```

- 파일시스템 조회 파라미터·응답 필드(`/filesystems/expiring`의 `status`/`within_seconds`/`brief`
  등) → [`resource-management-fs.md`](resource-management-fs.md).
- 쿼터 상태(`db`/`live`/`diff`, `source=both|db|live`, effective 경고) →
  [`resource-management-k8s.md`](resource-management-k8s.md).

---

## 인벤토리 · 스토리지 매핑

### `/inventory`

등록된 클러스터와 각 클러스터에서 관측된 StorageClass·CSI driver 등 effective 인벤토리를 돌려준다.
k8s 쿼터 mapping 등록 전에 대상 클러스터가 실제로 보이는지 확인하는 용도다.

```bash
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/inventory" \
  | jq '.clusters[] | {cluster_name, storage_classes, csi_drivers}'
```

### `/storage-mappings`

storage mapping을 **redacted**로 조회한다(`weka_credentials.password` 등 비밀은 렌더링하지 않음).
`sanity_status`/`readiness`로 각 축(RM/DM/mutation)의 준비 상태를 본다.

```bash
# 전체 목록
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/storage-mappings" \
  | jq '[.[] | {storage_name, backend_type: .backend_template.backend_type, readiness, sanity_status}]'

# 클러스터 필터
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/storage-mappings?cluster_name=cluster-a" \
  | jq '.[].storage_name'

# 단건 상세 (sanity_result·rm_candidates 포함)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/storage-mappings/cephfs-a" \
  | jq '{storage_name, sanity_status, readiness,
         rm_candidates: [.sanity_result.agent_observed.rm_candidates[]? | {node_name, status}]}'
```

- **읽기 전용이다.** 등록/수정/삭제(`POST`/`PATCH`/`DELETE …/resource-management/storage-mappings`)와
  sanity 재실행(`:check`), Agent ConfigMap 동기화·rollout은 설치·운영 영역이다 →
  [`install/dms-03-rm-filesystem.md`](../../install/dms-03-rm-filesystem.md),
  [운영 런북](../operations-runbook.md).
- CSI/k8s mapping의 sanity는 agent evidence가 아니라 **ResourceQuota mutation transport**로 판정한다.
  `sanity_result.readiness.kubernetes_mutation`·`sanity_result.mutation_observed`로 진단한다(자세히는
  [`resource-management-k8s.md`](resource-management-k8s.md)와 [런북](../operations-runbook.md)).

---

## 작업 · 워커 상태

### `/work-summary` — 집계

plan·run·action-required를 한 번에 집계한다(대시보드/헬스체크 진입점). 총계(`total_active`)는 정확한
`COUNT(*)`라 리스트 cap에 걸리지 않는다.

```bash
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/work-summary" | jq
```

```json
{
  "plans": {
    "total_active": 3,
    "by_status": {"Ready": 2, "Claimed": 1},
    "by_worker_role": {"RM": 2, "DM": 1}
  },
  "runs": {
    "total_active": 2,
    "by_state": {"Running": 1, "Applying": 1},
    "by_worker_role": {"RM": 1, "DM": 1},
    "by_worker_id": {"rm-1": 1, "dm-1": 1},
    "lease_expiring_soon": 0,
    "stale_or_recovery": 0
  },
  "requests": {"action_required": 0}
}
```

- `lease_expiring_soon` — lease 잔여가 `lease_expiring_within_seconds`(기본 60초) 이내인 run 수.
- `stale_or_recovery` — 손봐야 할 run 수(아래 `/runs/stale`와 대응).

### `/plans/active` · `/runs/active` · `/runs/stale`

```bash
# 진행 중 run (lease 잔여·소유 worker 포함)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/runs/active" \
  | jq '[.[] | {run_id, worker_id, worker_role, state, lease_seconds_remaining, lease_expiring_soon}]'

# stale / recovery 대상 (StaleClaim·RecoveryNeeded·Blocked·UnknownAfterSideEffect)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/runs/stale" \
  | jq '[.[] | {run_id, state, resource_key, request_status}]'
```

- `/runs/active`는 `?state=`·`?worker_role=`·`?worker_id=`로 좁힐 수 있다.
- `/runs/stale`은 손봐야 할 run(만료 lease·복구 필요)만 모은다. worker 재시작/업그레이드 전후 stale
  가드(`POST /runs:mark-stale`)와 짝을 이룬다 → 절차는 [런북](../operations-runbook.md).

### `/worker-agent-health`

최근 run 50건 + 최근 agent report 50건을 한 번에 묶어 빠른 진단에 쓴다.

```bash
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/worker-agent-health" \
  | jq '{runs: (.runs | length), agent_reports: (.agent_reports | length)}'
```

---

## 조치 필요 (action-required)

여러 소스(요청 attention · storage mapping · agent 신선도 · k8s 쿼터 · 파일시스템 · 데이터 잡)를 하나의
리스트로 합쳐 **지금 손봐야 할 것**을 돌려준다. 각 항목은 `issue_type` 디스크리미네이터 + 타입별
필드를 가진다.

```bash
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/action-required" | jq
```

```json
[
  {"issue_type": "storage_mapping_failed", "storage_name": "cephfs-a",
   "sanity_status": "Failed", "sanity_result": {"errors": [ ... ]}},
  {"issue_type": "agent_report_stale", "report_id": "rep-...", "cluster_name": "cluster-a",
   "node_name": "node3", "worker_role": "DM", "reported_at": "2026-07-03T03:59:00Z"}
]
```

흔한 `issue_type`: `storage_mapping_failed` / `storage_mapping_unknown` / `storage_class_missing` /
`csi_driver_mismatch` / `agent_report_stale` + 요청·k8s 쿼터·파일시스템·데이터 잡 계열.

### 확인 처리 (ack / unack)

항목을 **fingerprint 기준으로 확인 처리**하면 그 뒤 `action_required()`가 **모든 클라이언트에서**
해당 항목을 제외한다(레코드는 지우지 않는 record-preserving 방식). 포탈의 '확인'이 이 경로를 쓴다.

```bash
# 확인(ack) — fingerprint는 목록 항목에서 유도한다
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/operations/action-required:ack" \
  -d '{"items": [{"fingerprint": "<fp>", "issue_type": "storage_mapping_failed", "reason": "조치 예정"}]}' \
  | jq   # → {"acked": 1}

# 확인 해제(unack)
"${CURL[@]}" -X POST -H "content-type: application/json" \
  "$DMS_API_URL/api/v1/operations/action-required:unack" \
  -d '{"fingerprints": ["<fp>"]}' | jq   # → {"unacked": 1}

# 확인된 항목 목록
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/action-required/acks" | jq
```

> ack는 **표시만** 바꾼다 — 근본 원인(sanity Failed, agent stale 등)은 그대로다. 실제 해소 절차는
> [운영 런북](../operations-runbook.md)을 따른다.

---

## 에이전트 리포트

노드 agent(DaemonSet)가 보고한 마운트·도구·자격·신원 evidence와 **신선도(freshness)**를 본다. DM
worker는 잡 실행 전 이 리포트의 신선도를 preflight 게이트로 쓴다.

```bash
# 노드별 최신 1건 + 신선도
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/agent-reports?latest_per_node=true" \
  | jq '[.[] | {node_name, worker_role, freshness_status, reported_at}]'

# Stale 노드만 (freshness 필터, 값은 capitalize 되어 Fresh/Stale로 매칭)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/agent-reports?freshness=stale&latest_per_node=true" \
  | jq '[.[] | {cluster_name, node_name, worker_role, stale_at}]'
```

- 응답의 신선도 필드는 `freshness_status`(`Fresh`/`Stale`)이며 읽는 시점에 계산된다
  (`DMS_AGENT_REPORT_STALE_SECONDS` 기준). `latest_per_node=true`면 `agent_node_current`(노드별 최신
  1행)에서 O(노드 수)로 읽는다 — 침묵한 노드도 마지막 보고가 계속 보인다.
- `/agent-reports/metrics?since_seconds=21600` — 노드 OS 메트릭 샘플(cpu/mem/load/disk)을 시간창으로
  돌려준다(대시보드 노드 워크로드 그래프용, 1m~24h로 clamp).

---

## 데이터 잡 · Volcano · 진단

DM 잡 상태·로그 조회는 조회 API에도 있지만 상세는 [DM 데이터 잡 API](data-management.md)에 있다. 여기서는
진입점만 정리한다.

```bash
# 잡 목록/요약 (필터: requester_id·operation·storage_name·state)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/data-jobs?state=ConfirmPending&limit=20" | jq
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/data-jobs/summary" | jq

# 잡 단건 상태 (+ plan·results 포함)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/data-jobs/<job_id>" | jq

# 잡의 MPI launcher 로그 tail (포탈 로그 뷰가 쓰는 경로)
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/data-jobs/<job_id>/logs?tail=400" | jq '{available, note, pods}'
```

- `/data-jobs/{job_id}/logs`는 아직 스케줄되지 않았거나 pod가 GC됐으면 500이 아니라
  `{"available": false, "note": "..."}`를 돌려준다. 이 엔드포인트는 dms-api에 **추가 RBAC**
  (`pods/log` + volcano read)가 필요하다 — `install/kubernetes/dms-api-volcano-rbac.yaml`이며
  control-plane.yaml에 포함돼 있지 않으니 **별도로 적용**해야 한다(→
  [`install/dms-05-dm-jobs.md`](../../install/dms-05-dm-jobs.md)).
- `/volcano` — Volcano 스케줄러/큐 상태. `/volcano/job-metrics?limit=1000` — 잡별 lifecycle 메트릭
  (타임스탬프·지연·상태 카운트, 대시보드 throughput/latency용).
- `/diagnostics/{correlation_id}` — correlation_id로 observability 이벤트 타임라인을 모아 본다.

---

## 컨트롤플레인 제어 상태

DMS는 스케줄링을 막는 단일 **control state**(maintenance / drain / scheduling_blocked)를 둔다.
점검·업그레이드 시 이 상태를 읽어 안전하게 워커를 멈춘다. **조회는 read-only**, 상태를 바꾸는
mutation은 절차(런북)를 따른다.

### 조회 — `/control-state`, `/drain-status`

```bash
# 현재 제어 상태
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/control-state" | jq
```

```json
{
  "singleton_id": "default",
  "maintenance_mode": false,
  "drain_mode": false,
  "scheduling_blocked": false,
  "reason": "resume",
  "changed_by": "mtls:CN=operator,O=dms",
  "changed_at": "2026-07-03T04:12:00Z"
}
```

```bash
# drain 진행 상황 — 활성 run이 빠지고 shutdown 준비가 됐는지
"${CURL[@]}" "$DMS_API_URL/api/v1/operations/drain-status" \
  | jq '{scheduling_blocked: .control_state.scheduling_blocked,
         active_runs: .active_runs.count,
         blocked_or_recovery: .blocked_or_recovery_runs.count,
         ready_for_shutdown}'
```

`ready_for_shutdown`은 **`scheduling_blocked`이면서 활성 run이 0이고 hard blocker
(`RecoveryNeeded`/`UnknownAfterSideEffect`/`BackendApplyFailed`)가 없을 때만** `true`가 된다. worker를
scale-down하기 전에 이 값을 확인한다.

### mutation — 절차는 런북에서

아래 엔드포인트는 상태를 바꾸며 감사 레코드를 남긴다. **직접 호출하지 말고** 헬퍼 스크립트가 감싸는
정해진 순서를 따른다(planned shutdown → 작업 → recovery check → resume).

| Method / Path | 동작 |
|---|---|
| `POST /control-state:enter-maintenance` | maintenance 진입(스케줄링 차단). `block_scheduling`은 항상 true여야 함 |
| `POST /control-state:begin-drain` | drain 진입(+ 즉시 `active_runs`/`ready_for_shutdown` 반환) |
| `POST /control-state:resume` | 정상 복귀. hard blocker가 있으면 `409`(검토 후 `force:true`로만 강행) |
| `POST /runs:mark-stale` | 만료 lease run을 `StaleClaim`/`RecoveryNeeded`로 표시(자동 재실행 안 함) |

> 실제 명령·헬퍼 스크립트(`dms-planned-shutdown.sh`·`dms-startup-recovery-check.sh`·`dms-resume.sh`)와
> worker 재시작/업그레이드/rollback 순서는 전부 [운영 런북](../operations-runbook.md)에 있다. 여기서는
> API 표면만 소개한다.

---

## 다음 문서

- [`README.md`](README.md) — DMS API 개요와 인증(mTLS 운영 프로필).
- [`resource-management-fs.md`](resource-management-fs.md) — 파일시스템 RM API(리소스 상세 응답 형태).
- [`resource-management-k8s.md`](resource-management-k8s.md) — k8s 쿼터 RM API(쿼터 상태 DB↔live).
- [`data-management.md`](data-management.md) — DM 데이터 잡 API(잡 상태·로그·preview/confirm).
- [`operations-runbook.md`](../operations-runbook.md) — 운영 런북(점검·업그레이드·drain/resume 절차·`:resolve`).
- [`install/dms-06-configuration.md`](../../install/dms-06-configuration.md) — 환경변수 레퍼런스
  (`DMS_AGENT_REPORT_STALE_SECONDS`·mTLS actor·lease 등).
