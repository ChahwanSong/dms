# Worker-node dashboard (종합 대시보드) — design

Date: 2026-06-28
Status: approved (brainstorming)

## Goal

Rebuild the **워커 노드** section of the operator 종합 대시보드 so an operator can, at a
glance, understand each worker node's **current functional status** (RM / DM) and its
**workload over time** (CPU / memory). Also tighten the **CSI control host** panel to
status-only. Worker-node section moves to the **top** of the dashboard.

This iteration covers the worker-node section + CSI control-host adjustments only.

## Decisions (from brainstorming)

- **History source**: DMS already stores per-node agent reports as a time-series in
  `agent_reports` (one row per report, every 60s, no pruning), but no API exposes
  per-node history. → add **one read-only DMS endpoint**. Use the **1-minute data
  as-is** (no 5-min downsampling).
- **Default window**: **last 6 hours** (≈360 points/node at 1-min).
- **Layout**: **wide table + inline sparklines** (one row per node), with generous row
  height and sizable sparklines (~80×28px) to satisfy "칸 크게".
- **Worker-node section first** on the dashboard.
- **Drop the 툴 column.** Keep **마운트** as a small muted column.
- **CSI control host**: status only (reachability + can-i). Remove the 비고 text column;
  move the "ResourceQuota mutation transport reachable" explanation to an **(i) tooltip**.

## Architecture

Three layers, each independently testable:

```
DMS  GET /api/v1/operations/agent-reports/metrics?since_seconds=  (NEW, read-only)
        → flat per-report metric samples in the window
BFF  GET /api/operator/dashboard/node-metrics                     (NEW)
        → groups samples by node → {current, cpu_series, mem_series, rm/dm status}
SPA  NodesTable (rewritten): table + RM/DM badges + CPU/MEM sparklines + load/disk
     ControlHostsTable: status-only + (i) tooltip
     Dashboard: reorder (nodes first)
```

### 1. DMS endpoint (read-only, minimal)

`GET /api/v1/operations/agent-reports/metrics?since_seconds=<int>` (default 21600 = 6h;
clamp to a sane max e.g. 86400).

- Router: `src/dms/api/routers/operations.py` — new route next to `agent-reports`,
  `authenticated_actor` gated, read-only.
- Repository: the operational repository that already owns agent_reports (same place as
  `list_agent_reports`; verify operational vs observability DB during impl) —
  new method `list_agent_metric_samples(since_iso, limit)`:
  `SELECT cluster_name, node_name, node_uid, worker_role, reported_at, report_json
   FROM agent_reports WHERE reported_at >= %s ORDER BY node_name, reported_at`.
  Parse `report_json` in Python (portable across SQLite/PG — no JSON SQL functions),
  extract `os_metrics` → emit compact rows:
  `{cluster_name, node_name, worker_role, reported_at, cpu_percent, cpu_cores,
    mem_used_pct, mem_total_kb, load1, disk_used_pct}` (omit/null missing fields).
- `since_iso` computed from `now - since_seconds` (server clock). Bound the row count
  with a hard `limit` (e.g. 20000) to protect the API; `log()` if truncated.
- No writes, no migration (table already exists).

### 2. BFF endpoint

`GET /api/operator/dashboard/node-metrics` (operator-gated) in
`src/portal/backend/routers/dashboard.py`:

- `DmsClient.list_agent_metric_samples(since_seconds, actor)` → calls the DMS route.
- Group samples by `node_name` (+cluster). For each node:
  - `cpu_series` / `mem_series`: bucket samples **per minute** (a node with both RM and
    DM agents emits ~2 samples/min; take the **latest sample in each minute bucket**) →
    `[{t: <iso>, v: <pct|null>}]` sorted by time.
  - `current`: the most-recent sample's `cpu_percent, mem_used_pct, cpu_cores,
    mem_total_kb, load1, disk_used_pct, reported_at`.
  - `rm_status` / `dm_status`: derive from the **current snapshot** endpoint
    (`/dashboard/nodes`) freshness per role — OR include worker_role freshness here.
    Decision: keep RM/DM status from the existing `/dashboard/nodes` data (already has
    `freshness_status` + `worker_role` + `capability_summary.mounts`); `node-metrics`
    supplies only the time-series + current resource numbers. The frontend joins the two
    by `node_name`.
- Returns `{ nodes: [{cluster_name, node_name, current, cpu_series, mem_series}], window_seconds }`.
- Partial-failure tolerant (DmsApiError → empty/{error}), consistent with other
  dashboard routes.

### 3. Frontend

- **Sparkline** component (`dashboard/Sparkline.tsx`): inline SVG area/line for a
  `{t,v}[]` series, fixed size (~80×28), 0–100 y-domain, amber/red tint as values climb,
  gaps for null. No deps.
- **NodesTable** rewrite: fetch both `/dashboard/nodes` (status/mounts) and
  `/dashboard/node-metrics` (series/current), join by `node_name`. Columns:
  `노드/클러스터 · RM · DM · CPU(sparkline + %/cores) · 메모리(sparkline + %/총GB) ·
  load · 디스크% · 마운트(muted, small) · 보고`. RM/DM badges: Fresh=green, Stale=red,
  absent="—". Taller rows. Freshness filter kept.
- **Dashboard.tsx**: reorder so `<NodesTable/>` is first, then `<ControlHostsTable/>`,
  then `<StatusCards/>` + `<VolcanoPanel/>`.
- **ControlHostsTable**: drop the 비고 `<td>`; add an InfoHint (i) tooltip on the panel
  title (or the 도달 header) explaining "비고/transport: ResourceQuota mutation transport
  (RM 워커가 (ssh-)kubectl로 control host에 도달해 ResourceQuota를 적용할 수 있는지)".
  Reuse the existing `components/InfoHint.tsx`.

## Data semantics

- A **node** = unique `node_name`. RM and DM are separate agent reports for the same
  host; their os_metrics describe the same machine, so resources are **node-level**
  (dedupe; per-minute bucket takes the latest sample regardless of role).
- CPU% (`cpu.percent`) and MEM% (`memory.used_pct`) are already percentages of node max;
  `cpu.cores` and `memory.total_kb` shown as context. y-axis is 0–100% of node max.
- Missing metric (fail-soft probe) → null point → gap in the sparkline.

## Testing

- DMS: unit test for `list_agent_metric_samples` (seed agent_reports rows with os_metrics
  JSON across timestamps/roles → assert window filter + parsed fields). Portable (SQLite).
- BFF: test the grouping/bucketing (fake DMS client returns flat samples → assert per-node
  per-minute series + current + RM/DM join).
- Frontend: `npm run build` (tsc) + Playwright live verify (table, sparklines render,
  CSI tooltip, ordering).

## Out of scope (this iteration)

- 1h/6h/24h window selector (fixed 6h now; endpoint already param-driven for later).
- agent_reports retention/pruning (table grows unbounded; separate ops concern).
- Reworking StatusCards / Volcano panels (only reordered).
- Tool column (removed).
