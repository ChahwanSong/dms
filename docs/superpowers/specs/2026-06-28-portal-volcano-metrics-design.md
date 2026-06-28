# Volcano scheduler observability (처리량·대기열·지연·Top offenders) — design

Date: 2026-06-28
Status: approved (brainstorming)

## Goal

Give operators visibility into the Volcano scheduler beyond counts: **throughput**,
**queue state**, **latency** across the job lifecycle, and **Top offenders**. Surfaced
in the existing Volcano panel on the 종합 대시보드.

## Decisions (from brainstorming)

- **Collection = live snapshot, no storage.** The timestamps needed are already in the
  vcjob/pod objects, and the testbed retains completed vcjobs/pods, so latencies +
  throughput are computed from a live `kubectl get` snapshot. No new table / migration /
  collector. (If k8s GC later shrinks the window, add a persistent collector — out of
  scope now.)
- **Windows**: 1h / 6h / 24h / 72h.
- **Stats**: mean, p50, p95, p99.
- **Top offenders (top-5 each)**: longest-Pending, most-failed, most-resources-requested.

## Latencies (per job, from the snapshot)

A vcjob's worker pods are correlated by the `volcano.sh/job-name` label (pods in the
`dms` namespace). Per job:

1. **Job 생성 → Pod 생성**: `vcjob.metadata.creationTimestamp` → `min(pod.creationTimestamp)`
2. **Pod 생성 → Scheduled**: pod `creationTimestamp` → `conditions[type=PodScheduled].lastTransitionTime`; aggregate per job = max across its pods (gang-scheduled together)
3. **실행 (시작 → 완료)**: `min(pod.status.startTime)` → `max(container .state.terminated.finishedAt)` (terminal jobs only)

Missing pieces (pod GC'd, not yet terminal) → that latency is null and the job is
excluded from that stat.

## Architecture (mirrors node-metrics)

```
DMS  GET /api/v1/operations/volcano/job-metrics?limit=300   (NEW, read-only)
        → per-job timestamps + latencies + status counts + requested resources
BFF  GET /api/operator/dashboard/volcano-metrics            (NEW)
        → windowed stats (mean/p50/p95/p99) + throughput + top offenders
SPA  VolcanoPanel extended: 처리량 · 지연(윈도우 선택) · Top offenders (+ 기존 큐/컴포넌트)
```

### 1. DMS endpoint (read-only)

`GET /api/v1/operations/volcano/job-metrics?limit=300` — in `operations.py`, backed by a
new `volcano_adapter.volcano_job_metrics(limit)` in `src/dms/adapters/volcano.py`
(reuses the existing kubectl wrapper). Steps:

- `kubectl get vcjob -n <dms ns> -o json` and `kubectl get pods -n <dms ns> -o json`
  (the existing `_kubectl_get_items` returns full JSON).
- Group pods by `metadata.labels["volcano.sh/job-name"]`.
- For each vcjob (newest first, capped at `limit`): emit
  ```
  {name, queue, phase,
   created_at, pod_created_at, scheduled_at, started_at, finished_at,
   running, pending, succeeded, failed, min_available,
   req_cpu_cores, req_mem_bytes, req_pods,
   latencies: {job_to_pod_s, pod_to_sched_s, run_s}}   # null where unavailable
  ```
- **Resource requests**: sum over `spec.tasks[]` of `replicas × Σ containers
  resources.requests` (cpu `"500m"`→0.5, `"2"`→2; mem `"2Gi"`/`"512Mi"`→bytes). `req_pods`
  = Σ replicas (or min_available).
- Read-only, no writes, no migration. Helper parsers (`_parse_cpu`, `_parse_mem`,
  `_parse_k8s_ts`) kept pure/testable.

### 2. BFF endpoint

`GET /api/operator/dashboard/volcano-metrics` in `dashboard.py`:

- `DmsClient.volcano_job_metrics(actor)` → calls the DMS route.
- Compute, **per window** (1h/6h/24h/72h, membership by `finished_at` for completed jobs):
  - **throughput**: `{completed, succeeded, failed}` counts.
  - **latency**: for each stage, `{mean, p50, p95, p99, count}` over jobs in-window with
    that latency present. (Plain sorted-array percentiles; small N.)
- **Top offenders** (current snapshot, bounded to last 72h for relevance):
  - `longest_pending`: non-terminal jobs with no `finished_at`, sorted by age
    (`now − created_at`) desc — top 5 (name, queue, pending_s, phase).
  - `most_failed`: sorted by `failed` desc (failed>0) — top 5 (name, failed, phase).
  - `most_resources`: sorted by `req_cpu_cores` desc — top 5 (name, cpu, mem, pods).
- Returns `{windows: {"1h": {...}, "6h": ..., "24h": ..., "72h": ...}, top: {...},
  generated_at}`. Partial-failure tolerant.

### 3. Frontend (VolcanoPanel extension)

Add above the existing 큐/컴포넌트 tables:
- **처리량 / 지연** with a window toggle (1h · 6h · 24h · 72h). For the selected window:
  - throughput line: `완료 N (성공 X · 실패 Y)`
  - latency table: rows = 3 stages, cols = mean · p50 · p95 · p99 (+ n). Human durations
    (s/m/h via a `fmtDur` helper).
- **Top offenders**: three compact top-5 lists (최장 Pending · 최다 실패 · 최대 리소스).
- Keep the existing 큐 / 스케줄러 컴포넌트 / 활성 잡 tables.

## Testing

- DMS: unit-test the pure parsers (`_parse_cpu`/`_parse_mem`/ts) and
  `volcano_job_metrics` against fake vcjob/pod JSON → per-job latencies + resource sums +
  pod correlation.
- BFF: test windowed aggregation (mean/p50/p95/p99 + throughput) and top-offenders
  ordering against a fake DMS client.
- Frontend: `npm run build` + Playwright live (window toggle, tables render).

## Out of scope

- Persistent historical collector (snapshot only; revisit if GC shrinks the window).
- Per-stage time-series charts (stats tables now; charts later if wanted).
- Changing how DMS submits jobs / priorities.
