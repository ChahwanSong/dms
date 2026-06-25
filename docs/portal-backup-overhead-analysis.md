# DMS Portal 데이터 백업 — 대규모(일 수천 잡) 오버헤드 분석 및 개선안

- 날짜: 2026-06-25
- 대상: `src/portal/backend/orchestrator.py` 외 데이터 백업 오케스트레이션 경로
- 생성: dynamic multi-agent workflow (조사 5병렬 → 합성 → 제안 14 → 적대적 심사 14 → 최종 합성). 모든 주장은 소스 `file:line` 검증.
- 시나리오: 하루 ~5000개 sync 잡이 버스트로 제출되는 상황

> 이 문서는 분석/설계 산출물입니다. 구현은 별도 스펙/플랜으로 진행하세요.

---

# DMS Portal Data-Backup Orchestrator — Final Architecture & Scaling Analysis

**Audience:** DMS Portal senior engineers + DMS backend owner
**Scope:** `src/portal/backend/orchestrator.py`, `db.py`, `dms_client.py`, `config.py`, `app.py`; `src/portal/frontend/.../BackupBatchDetail.tsx`; DMS contract at `src/dms/api/routers/operations.py` + `src/dms/repositories/data_jobs.py`
**Verdict in one sentence:** The backup orchestrator is **correctness-bound before it is performance-bound** — a request_id resolution cliff corrupts batches at ~500 concurrent DMS jobs, while a single-threaded uncapped serial poll loop turns the nominal 5s cadence into minutes under load. Both must be fixed; neither is fixed today.

---

## 1. Executive summary

The data-backup feature drives operator-defined batches of DMS `sync` jobs through DMS's preview→confirm flow, entirely client-side, in **one asyncio task in one BFF pod** (`app.py:83`). It works at the scale tested today (tens to low-hundreds of jobs) but has **two hard ceilings** that bite well before the advertised ~5000-job batch:

1. **Correctness cliff at ~500 concurrent DMS jobs (P0).** Job resolution maps `request_id → job_id` by pulling the newest 500 DMS data-jobs and matching client-side, because **DMS's `list_data_jobs` has no `request_id` filter** (confirmed: operations.py:462-479, data_jobs.py:210-232). Above ~500 in-flight jobs, older `request_id`s fall out of the window, those jobs wedge in `preview_pending` forever, and the crash-recovery path can issue a **second, untracked, destructive sync** on `delete_enabled` batches. This is data loss / data corruption, not just slowness.

2. **Throughput collapse from the uncapped serial poll loop (P0).** The preview and exec poll loops (orchestrator.py:162-182, 226-243) iterate jobs serially with no `asyncio.gather`, no `Semaphore`, no cap. The 5s sleep is added *after* the cycle, so the effective interval is `5s + (jobs polled)×RTT`. There is no `asyncio.create_task` in the loop, no httpx `limits=`, and `claim_jobs` (FOR UPDATE SKIP LOCKED, db.py:296) — the one primitive that would enable HA — has **zero callers**.

The good news: the **highest-leverage fixes are small and mostly portal-side.** The single biggest correctness+perf win is a 4-line additive DMS change (a `request_id` filter on an existing endpoint) plus a portal switch to point-lookup. The biggest perf win is wrapping the poll loop in `gather` under a small `Semaphore`. DB load is genuinely benign (~18 q/s peak) — **this system is HTTP-serialization-bound on a shared event loop, not DB-bound.**

This document corrects two overclaims that surfaced during adversarial review:
- The "P=2000-5000 serial GETs" worst case for the poll loop is **overstated at default settings**: `backup_concurrency=8` caps in-flight `running` jobs per batch, so the *exec* poll is ≤8 deep. The poll-loop danger is real but materializes mainly (a) when `backup_concurrency` is raised, (b) across many active batches (serial batch driving multiplies it), and (c) when the resolution cliff lets `preview_pending` accumulate unbounded. The cliff is the trigger that makes the poll loop dangerous.
- The "~30k→15k UPDATEs" double-write claim is **~2.6× overstated**; the real saving is the per-submit pre-mark (~5,000 UPDATEs on a 5000-job batch, ~17%).

---

## 2. How the orchestration works today (brief)

State lives entirely in Postgres (`portal` schema): `backup_batches` (status: draft→previewing→previewed→running→done) and `backup_jobs` (state: registered→preview_pending→preview_ready→running→succeeded/failed, plus preview_failed/cancelled). The orchestrator is a single `while` loop (orchestrator.py:111-127):

1. `active_batches()` returns all batches in `previewing`/`running` (db.py:246-252).
2. **Serially**, for each batch: `_drive_preview` or `_drive_execute`.
3. Sleep `backup_poll_seconds` (5s) *after* the whole cycle.

**Preview phase** (`_drive_preview`): submit up to `backup_concurrency - len(preview_pending)` registered jobs (each = one DMS POST /sync + two `update_job` writes), resolve their `request_id`s to `job_id`s via the 500-row scan, re-submit any that crashed mid-submit, then serially `GET /sync/jobs/{id}` for every resolved pending job; advance the batch when nothing is registered/pending.

**Execute phase** (`_drive_execute`): after operator approval, confirm up to `backup_concurrency - len(running)` `preview_ready` jobs (each = one DMS POST /confirm), then serially poll running jobs to terminal; advance when nothing is preview_ready/running.

Design assumption, stated in the module docstring (orchestrator.py:18-20): *"Single sequential loop == single writer, so no row locking is needed."* This is the load-bearing invariant that every concurrency/HA proposal must preserve or replace.

The frontend `BackupBatchDetail.tsx` polls `setInterval(reload, 4000)` (line 66); `reload` calls `loadJobs(true)` (line 53), re-fetching the first `PAGE` rows (incl. JSONB) plus `get_batch` → `preview_totals` (JSONB sum) every tick, regardless of tab visibility.

---

## 3. Quantified overhead model & ranked bottlenecks

### Governing equation
```
effective_interval ≈ Σ_batches[ (4–5 DB rt) + (1 list500 GET if any unresolved)
                                + (≤C submit/confirm POSTs) + (P_b poll GETs) ] × RTT
                      + 5s sleep
```
- `C` = `backup_concurrency` (default 8) — caps submit/confirm slots **per batch**, and (via `slots = C - len(in-flight)`) caps per-batch in-flight jobs.
- `P_b` = jobs polled in batch `b` per cycle — **uncapped in code**, but in practice bounded by `C` for `running`, and bounded only by accumulated `preview_pending` (which the resolution cliff can make unbounded).
- Batches are driven **serially**, so the per-cycle cost is the **sum** over active batches, not the max.

DB load is linear and benign: a 5000-job batch over its lifetime is ~30k UPDATEs + ~35k SELECTs ≈ **~18 q/s peak**, well under Postgres capacity. The constraint is the **shared FastAPI event loop**: the orchestrator's serial `await`s (each up to the 15s httpx timeout) run in the same loop that serves operator BFF requests, so a slow cycle degrades UI latency pod-wide.

### Ranked bottlenecks

| # | Bottleneck | Dominant term | Severity | Notes / correction |
|---|-----------|---------------|----------|--------------------|
| **1** | Uncapped serial poll loops (orch:162-182, 226-243) | O(P) × RTT, serial, per batch | **CRITICAL** | Real, but `C=8` caps exec-poll depth at default settings. Becomes catastrophic when `backup_concurrency` is raised, when many batches are active (serial multiplier), or when the cliff lets `preview_pending` grow unbounded. |
| **2** | `list_data_jobs(limit=500)` resolution (orch:274-285) | O(active_batches) × 500 rows/cycle; **hard 500 cap** | **CRITICAL (perf + correctness)** | DMS has no `request_id` filter — confirmed. Pulls newest-500 and matches client-side, per batch per cycle while any job is unresolved. >500 in-flight ⇒ silent permanent loss. Co-dominant and the true trigger for #1. |
| **3** | Serial batch driving (orch:115-119) | O(B) × per-batch I/O | HIGH | Batch N+1 waits on all of batch N's I/O. 10 batches × ~14-26s each multiplies cycle time linearly in batch count. |
| **4** | Single httpx client, no `limits=`, single 15s timeout, shared event loop (dms_client.py:52-56) | per-call serial blocking | HIGH | Default httpx limits (100/20) are irrelevant while the loop is serial; they matter only *after* gather is added. The 15s blanket timeout applies to status GETs too. |
| **5** | No HA / no distributed lock; `claim_jobs` unused (app.py:83, db.py:296) | — | HIGH (ceiling) | A 2nd pod double-submits to DMS. The fix primitive exists but is never wired. |
| **6** | `preview_totals` JSONB sum on every read (db.py:223-233) | O(preview_ready rows) per `get_batch` | LOW–MEDIUM | `(batch_id,state)` index finds rows but can't cover the JSONB detoast. At 2500 rows this is single-digit ms locally — overhead model correctly rates it benign. Triggered by frontend, not the orchestrator. |
| **7** | Frontend `loadJobs(true)` every 4s (BackupBatchDetail.tsx:53,66) | O(PAGE rows) × tabs / 4s | MEDIUM | Re-fetches first-page rows incl. JSONB each tick; rows past the page are invisible during live ops; not visibility-gated. |
| **8** | Double `update_job` + double `jobs_in_states` (orch:198+206, 134+144, 215+223) | constant ~2× on submit-path writes | LOW | Real saving ≈ 5,000 UPDATEs (~17%), **not** the "~30k→15k" originally claimed. |

### Dominant scaling term
**The resolution cliff (#2) is the trigger; the serial poll loop (#1) is the amplifier; serial batch driving (#3) is the multiplier.** Per-cycle wall time grows ~O(total in-flight jobs) once the cliff lets `preview_pending` accumulate, and again ~O(batch count) because batches are serial. DB is not the constraint.

---

## 4. Correctness ceilings (what breaks before perf does)

These cap the system far below 5000 jobs/day regardless of loop speed.

1. **The >500-job request_id resolution cliff (the headline failure).** `_data_jobs_by_request` (orch:274-285) calls `list_data_jobs(limit=500)` newest-first and dict-matches client-side. **Confirmed:** `list_data_jobs` accepts `requester_id/operation/storage_name/state` but **no `request_id`** (operations.py:465-479; data_jobs.py:214-232). At `C=8`, 500 new DMS jobs accumulate in ~5 minutes of steady submitting. Any job whose `request_id` ages past the newest-500 window is never matched (orch:283 keeps only the first match per rid), stays `preview_pending` with no `dms_job_id`, is **skipped** by the poll loop (orch:163-164), and has **no timeout/escalation**. Worse: the crash-recovery branch (orch:157-159) re-submits any pending job with no `dms_request_id` — and for `delete_enabled=True` batches a genuine resubmit creates a **second DMS sync to the same dst with `delete:True`, untracked**, running to completion. **This is a data-correctness failure that triggers at ~500 concurrent jobs — well under one 5000-job batch.**

2. **No atomic claim ⇒ double-submit on restart or 2nd pod.** A crash between the POST (orch:200) and the `dms_request_id` write (orch:206-207) leaves a `registered`/half-marked row that is re-submitted next cycle. A second pod reading the same row submits in parallel. `claim_jobs` (db.py:296, FOR UPDATE SKIP LOCKED) would close this but is unused.

3. **No terminal-state timeout ⇒ permanently wedged batches.** If a poll errors (DMS lost the job, transient 5xx), the loop `continue`s (orch:167-169, 231-233); the job never reaches terminal, `batch_state_counts` never zeroes, and the batch is wedged in `previewing`/`running` forever. No max-retry exists anywhere.

4. **Null-fingerprint ⇒ unrecoverable confirm failure.** A `preview_ready` job with a null fingerprint is hard-failed at confirm (orch:251-254); recovery requires rebuilding the batch.

5. **No CHECK constraint on `state`** (db.py:87). A typo'd state string silently orphans a row from every filter query.

**Burst failure mode (5000 jobs at once):** submission alone takes ⌈5000/8⌉ ≈ 625 cycles ≈ **52 min floor if cycles stayed 5s** — they don't. At ~5 min the resolution cliff fires; a growing cohort wedges and some get duplicate-submitted (destructive on delete batches). As `preview_pending` climbs, the uncapped poll loop stretches each cycle to minutes, starving the shared event loop and degrading the operator UI. Net: a multi-hour, partially-corrupted run with silently-lost and/or double-executed jobs, no timeout to surface them, and no second pod able to help.

---

## 5. Recommendations (grouped by priority)

Format per item: **Problem · Change + files · requires_dms_change · Impact · Effort · Risks**. Adversarial verdicts folded in; rejected ideas noted at the end of the section.

> **Ordering principle from the reviews:** fix the **correctness cliff (#2)** and the **poll-loop throughput (#1)** *first and together* — they are independent failure modes (corruption vs. collapse) and both are required for the system to be correct *and* scalable. Everything else is hardening or constant-factor cleanup.

### P0 — Must fix before any large batch runs

**P0.1 — Add a `request_id` filter to `GET /api/v1/operations/data-jobs`; switch portal to point-lookup**
- **Problem:** Resolution cliff (Ceiling #1, Bottleneck #2). >500 in-flight ⇒ permanent stalls + duplicate destructive syncs.
- **Change:** *DMS:* add `request_id: str | None = None` to the route (operations.py:463-479) and to `DmsRepository.list_data_jobs` (data_jobs.py:210), reusing the existing `WHERE data_jobs.request_id = ?` pattern from `get_data_job_by_request` (data_jobs.py:91-95) and the existing `idx_data_jobs_request` index — expose it as an exact `LIMIT 1` lookup, not a general list filter, to keep semantics explicit. *Portal:* replace the 500-row scan in `_data_jobs_by_request` (orch:274-285) with one point-lookup per unresolved job; **preserve "empty result = planner not yet run, retry next cycle"** (`if not dj: continue`) as a named invariant — do not mark `preview_failed` on empty.
- **requires_dms_change:** **yes** (additive, backward-compatible, read-only).
- **Impact:** Eliminates the ~312,500 fetched-and-discarded rows per 5000-job batch *and* closes the correctness cliff. Highest combined leverage on the list.
- **Effort:** S (4-line DMS change + small portal change). Main cost is DMS deploy coordination.
- **Risks:** The planner race window (job row created asynchronously by the planner, _core.py) means the lookup returns empty briefly after submit — must keep the retry-next-cycle behavior, or every job fails on first lookup (a regression *worse* than the cliff).

**P0.2 — Parallelize the poll GETs under a bounded `Semaphore`**
- **Problem:** Uncapped serial poll loop (Bottleneck #1).
- **Change:** In orchestrator.py, gather only the `get_sync_job` coroutines, then process results serially: `results = await asyncio.gather(*[self._dms.get_sync_job(j["dms_job_id"], actor=actor) for j in pollable], return_exceptions=True)`, bounded by an `asyncio.Semaphore`. **Keep the following `update_job` writes serial** to preserve the single-writer invariant and avoid DB-pool exhaustion.
- **requires_dms_change:** no.
- **Impact:** Turns O(P)×RTT into ⌈P/concurrency⌉×RTT for the poll phase.
- **Effort:** S.
- **Risks (from review, adopted):** Set the semaphore **small (~4, env `PORTAL_BACKUP_POLL_CONCURRENCY`)** because the pool is `max_size=10` autocommit (db.py:120,124) and the trailing serial writes still need connections. **Must ship with P0.3 (httpx limits)** or, post-gather, the default httpx pool can fan out 100 connections to DMS. The original sem=16-32 suggestion is too high.

**P0.3 — Set httpx `limits=` and a poll-specific timeout (ship with P0.2)**
- **Problem:** No `limits=`; single 15s timeout on all calls (dms_client.py:52-56).
- **Change:** Add `httpx.Limits(max_connections=N, max_keepalive_connections=N//2)` sized to the poll semaphore (`PORTAL_DMS_MAX_CONNECTIONS`, shared/linked with `PORTAL_BACKUP_POLL_CONCURRENCY`). Add a per-call `timeout` param to `_request` and pass a shorter read timeout for status GETs (e.g. `httpx.Timeout(connect=5, read=8)`); keep 15s for submit/confirm.
- **requires_dms_change:** no.
- **Impact:** Bounds connection fan-out; stops one slow GET from holding a 15s slot on the shared loop. The timeout split has standalone value even before gather.
- **Effort:** S.
- **Risks:** Too-short a timeout turns normal slow responses into poll errors that `continue` forever (compounds Ceiling #3) — so pair with P1.2. Limits set below the semaphore silently re-serialize the gathered polls.

### P1 — Required for correct, bounded behavior under load

**P1.1 — Cap polled jobs per cycle to an oldest-first slice**
- **Problem:** Even parallelized, polling *all* in-flight jobs is unbounded; `backup_concurrency` gates only submit/confirm (Bottleneck #1 co-driver).
- **Change:** Add `PORTAL_BACKUP_POLL_BATCH` (default 100). **Split `jobs_in_states`:** keep an uncapped `COUNT(*)` for the slot calculation, and add a `LIMIT`-bearing poll-iteration query ordered by a new nullable **`last_polled_at`** column (`ORDER BY last_polled_at ASC NULLS FIRST`), updated on every poll attempt. Add index `(batch_id, state, last_polled_at)`.
- **requires_dms_change:** no (portal schema; needs a migration story — see Open Questions).
- **Impact:** Per-cycle poll cost becomes O(cap), constant in total in-flight.
- **Effort:** S–M.
- **Risks (adopted from review):** *Do not* reuse the capped query for slot counting (would mis-count slots and over-submit if cap < concurrency). `ORDER BY updated_at` is wrong (freshly-submitted jobs share a timestamp) — hence `last_polled_at`. **Watch DMS `PreviewExpired`:** `ConfirmPending` is latency-critical; size `cap × RTT_p99 << preview_TTL / num_batches` or a deprioritized job's preview expires before the portal polls it, converting a DMS success into a portal failure. Ship **with** P0.2, not alone — the cap bounds, gather provides throughput.

**P1.2 — Per-job and per-batch terminal-state timeouts**
- **Problem:** Wedged jobs/batches are invisible and permanent (Ceiling #3).
- **Change:** Add a **`submitted_at` column** (do *not* reuse `updated_at`, which bumps on every field write). On exceeding `PORTAL_BACKUP_PREVIEW_TIMEOUT_SECONDS` (default 3600) / `PORTAL_BACKUP_EXEC_TIMEOUT_SECONDS` (default 86400), attempt `data.cancel` in DMS first (when `dms_job_id` is known), then mark the job failed with a timeout reason and log any portal/DMS divergence. Add a `stalled` batch status surfaced in the dashboard; exclude it from `active_batches()`. Guard batch advancement so timed-out jobs don't silently flip a batch to `previewed`/`done`.
- **requires_dms_change:** no (uses existing `data.cancel`).
- **Impact:** Turns silent multi-hour wedges into surfaced, actionable failures.
- **Effort:** M.
- **Risks:** A timed-out job that DMS later completes (esp. `delete_enabled`) leaves divergence; re-drive must not blind-resubmit a destructive sync. **Sequence after P0.1** — before the cliff is fixed, timing out jobs with no `dms_job_id` orphans DMS jobs you can't cancel.

### P2 — Scaling, HA, and second-order load

**P2.1 — Drive active batches concurrently with `asyncio.gather`**
- **Problem:** Serial batch driving (Bottleneck #3).
- **Change:** Replace the serial `for batch` loop (orch:115-119) with `asyncio.gather(*tasks, return_exceptions=True)` over per-batch drivers, bounded by an outer semaphore; log per-task exceptions (the current single try/except must become per-task to keep isolation).
- **requires_dms_change:** no.
- **Impact:** Cycle time → slowest single batch instead of the sum.
- **Effort:** M.
- **Risks (adopted):** `backup_concurrency` is read **per batch** (orch:137,218) — gathering B batches opens **B×C** concurrent DMS submits/confirms. Either refactor to a **global in-flight counter** or bound `outer_sem × C` explicitly. Multiple batches writing concurrently weakens the single-writer invariant (safe only because batches touch disjoint rows) — pair with P2.2 if multi-pod. **Sequence after P0.1/P0.2** (otherwise B× the cliff fan-out and B× the serial poll).

**P2.2 — Wire `claim_jobs` (FOR UPDATE SKIP LOCKED) for HA + atomic submit**
- **Problem:** Single-pod throughput cap; double-submit hole (Ceiling #2, Bottleneck #5).
- **Change:** Replace the read-then-mark submit/confirm path with `claim_jobs(batch_id, "registered", "preview_pending", slots)` and `claim_jobs(batch_id, "preview_ready", "running", slots)` (db.py:296 — note signature is `from_state, to_state`). Add a **stale-claim reaper** (revert `preview_pending`/`running` with no progress past a lease back to the prior state) and **batch ownership** (advisory lock per `batch_id` or a `claimed_by`/`lease_until` column). Fix the slot TOCTOU by computing slots inside the claim.
- **requires_dms_change:** no.
- **Impact:** Unlocks horizontal scaling and closes double-submit-on-restart/2nd-pod.
- **Effort:** M.
- **Risks:** Without the reaper, a crashed pod's claims wedge permanently — *worse* than today. Without batch ownership, multi-pod = duplicate work + N× cliff/poll DMS load. **Sequence after P0.1/P0.2.**

**P2.3 — Adaptive per-job poll backoff**
- **Problem:** Long-running dsyncs are polled every 5s for their whole life (~720 pointless GETs/hr).
- **Change:** In-memory `dict[job_id, next_poll_at]`; 5s → 20s (preview cap) / 60s (exec cap); clear the entry immediately on `update_job` state change. No DB column (lost-on-restart is the intended recovery path).
- **requires_dms_change:** no.
- **Impact:** ~5-10× fewer steady-state GETs for long jobs — but only reduces **DMS load**; cycle wall-time only improves if P0.2/P1.1 are already in.
- **Effort:** M.
- **Risks:** Backoff = detection latency; bound it. **Sequence after P0.2/P1.1** — it does not attack the dominant term alone.

**P2.4 — `MAX_ACTIVE_BATCHES` operational safety cap**
- **Problem:** Nothing limits how many batches are driven at once.
- **Change:** `PORTAL_BACKUP_MAX_ACTIVE_BATCHES`; FIFO-slice `active_batches()` by `created_at`; surface `queue_position` in `list_batches`.
- **requires_dms_change:** no.
- **Impact:** A safety net against an operator approving 20 batches at once. Not a perf fix.
- **Effort:** S.
- **Risks:** Queued batches with no UI signal look stuck; needs `queue_position` exposed. Pair with P1.2 so a capped-out batch can't wedge invisibly.

### P3 — Constant-factor cleanup / low marginal value

**P3.1 — Drop the redundant double `update_job` / double `jobs_in_states`**
- **Problem:** Submit path pre-marks then re-marks (orch:198,206) and re-queries after submit/confirm (orch:144,223).
- **Change:** Replace the post-submit re-query with local append of successfully-submitted jobs (decrement the local slot counter on `DmsApiError`). Fold the double `update_job` into the `claim_jobs` work (P2.2), which removes the pre-mark entirely.
- **requires_dms_change:** no.
- **Impact:** ~5,000 fewer UPDATEs (~17%) on a 5000-job batch — **not** the "~30k→15k" originally claimed (corrected). Eases pool contention only.
- **Effort:** S.
- **Risks:** Merging the double UPDATE *standalone* widens the crash-resubmit window for `delete_enabled` batches; do it with P2.2, not before.

**P3.2 — `preview_totals` as a partial functional index (not counter columns)**
- **Problem:** JSONB sum on every `get_batch` (Bottleneck #6).
- **Change:** `CREATE INDEX IF NOT EXISTS backup_jobs_preview_totals ON backup_jobs(batch_id, (CAST(preview->>'files' AS bigint)), (CAST(preview->>'bytes' AS bigint))) WHERE state='preview_ready'` in `_ddl()` (db.py:59-99) → index-only scan, no detoast. **Reject** counter columns: `update_job` is a generic dispatcher (db.py:333) and the pool is autocommit (db.py:124), so atomic counter increments would require a transaction refactor and risk unrecoverable drift on crash.
- **requires_dms_change:** no.
- **Impact:** O(rows) JSONB scan → index-only. Genuinely last-order; near-zero once P3.3 lands.
- **Effort:** S.
- **Risks:** None material (idempotent bootstrap).

**P3.3 — Frontend: visibility-gated, decoupled polling**
- **Problem:** `loadJobs(true)` every 4s refetches first-page rows + JSONB regardless of tab visibility (Bottleneck #7).
- **Change:** Gate the interval on `document.visibilityState`; decouple the lightweight summary poll (`get_batch` + state_counts + preview_totals) from the row-page fetch (refresh rows on user action / filter change only). Handle the `reload` deps / interval-recreation interaction explicitly to avoid double listeners.
- **requires_dms_change:** no.
- **Impact:** ~2-2.5× fewer BFF/DB hits per open tab; less event-loop pressure. Small absolute win (operators rarely keep many tabs open).
- **Effort:** M.
- **Risks:** Summary-only polling can briefly desync the chips from the row table — keep an explicit refresh button (already present, line 111). Does **not** fix "rows past the page are invisible" — that needs keyset pagination (out of scope here).

### Rejected ideas (and why)

- **Return `job_id` in the sync submit 202 response (→ drop resolution entirely).** **Rejected (P3 at best).** The `data_jobs` row is created **asynchronously by the planner in a separate process** — the API handler cannot return a `job_id` that does not yet exist. A best-effort lookup would return null ~100% of the time, requiring the full fallback anyway (dead code, two paths to test). Its claimed crash-window fix is also wrong: that window is a portal DB-atomicity issue, not a response-content issue. **Use P0.1 instead.** (If DMS wants `job_id` in the 202 later as a UI convenience, that's unrelated cosmetic work.)
- **Bulk job-status endpoint built on `data_job_status` logic.** **Partially rejected.** As specified ("generalize `data_job_status` to a set") it issues ~4 DB queries *per job_id*, so a 200-id call = ~800 DMS queries — it **moves** DMS DB load, not reduces it, while P0.2 (gather) already collapses RTT with no DMS change. *Salvageable part:* a `job_ids` IN-filter on `list_data_jobs` could serve resolution, but P0.1 already does that more simply. Only pursue a **lightweight-projection** bulk endpoint (state + result_summary + preflight_result only) if profiling after P0.2 still shows RTT fan-out as the bottleneck.
- **Global in-flight cap as a submit/confirm gate (part of the backpressure proposal).** **Rejected.** It gates only *new* submits; it does not touch the poll loop, so it doesn't reduce the dominant term. P1.1 (per-cycle poll cap) is the correct mechanism. Keep only the `MAX_ACTIVE_BATCHES` half (P2.4).
- **`preview_totals` counter columns.** **Rejected** in favor of the functional index (P3.2) — drift risk + autocommit/transaction refactor + no migration framework.

---

## 6. Phased rollout plan

**Phase 0 — Correctness + throughput floor (ship together, gated behind a flag).**
- P0.1 (request_id filter + point-lookup) — coordinate the DMS deploy.
- P0.2 + P0.3 (poll gather under small semaphore + httpx limits/timeout split) — one PR.
- *Exit criteria:* a 1,000-job `delete_enabled=false` batch completes with zero wedged `preview_pending` jobs and zero duplicate DMS jobs; cycle time stays bounded.

**Phase 1 — Bound and surface.**
- P1.1 (poll cap + `last_polled_at` split queries) — needs the portal migration story resolved first.
- P1.2 (terminal timeouts + `submitted_at` + `stalled` status + cancel-before-fail).
- *Exit criteria:* inject a poll failure / lost `dms_job_id` and confirm the job/batch surfaces as `stalled`/failed within the timeout, not forever.

**Phase 2 — Scale and HA.**
- P2.1 (batch gather, with global concurrency accounting), P2.2 (`claim_jobs` + reaper + batch ownership), P2.4 (`MAX_ACTIVE_BATCHES`).
- Optionally P2.3 (adaptive backoff) once the loop is parallel.
- *Exit criteria:* two BFF pods run concurrently against the same batches with zero double-submits; throughput scales ~linearly.

**Phase 3 — Hygiene.**
- P3.1 (fold double-writes into claim_jobs), P3.2 (functional index), P3.3 (frontend polling).

**Migration prerequisite (blocks P1.1/P1.2):** the portal DB has **no migration framework** — `_bootstrap()` runs `CREATE TABLE IF NOT EXISTS` only (db.py:136-139) and never `ALTER`. Adding `last_polled_at`/`submitted_at`/`stalled` to live testbed data needs an out-of-band `ALTER TABLE` step or a minimal versioned-migration shim. Decide this before Phase 1.

---

## 7. Open questions / things to measure

1. **Measure real RTT (p50/p99) BFF→DMS in-cluster.** The entire model is RTT-sensitive; 50ms vs 200ms changes every threshold (poll cap size, semaphore size, whether gather is even necessary). *Measure before tuning.*
2. **What is DMS's preview-confirmation TTL?** P1.1's poll-cap sizing depends on it (`PreviewExpired` is in `_PREVIEW_FAILED`, orch:38). If the TTL is short, an aggressive cap converts DMS successes into portal failures. Get the exact value from the DMS owner.
3. **How fast does the planner materialize a `data_jobs` row after submit?** This sets the empty-lookup retry window for P0.1 and bounds how long jobs sit unresolved.
4. **Realistic batch shape.** Is the target really one 5000-job batch, or many small batches? If many batches, P2.1 (batch gather) matters more than P0.2; if one huge batch, the reverse. This reorders P2.
5. **Operator tab concurrency.** P3.3's payoff scales with simultaneous open tabs — confirm whether that's 1-2 (low value) or many.
6. **`backup_concurrency` operational intent.** Is 8 the production value? Raising it (e.g. to 100) is exactly what makes Bottleneck #1 catastrophic — if ops plans to raise it, P0.2/P1.1 become mandatory, not optional.
7. **Profile after Phase 0** to decide whether the lightweight-projection bulk endpoint is worth a DMS change, or whether gather already made RTT fan-out a non-issue.
8. **DMS DM-worker capacity.** Even with a perfect portal, DMS itself runs `sync` jobs through DM workers with leases/quotas. Confirm DMS can absorb the submit rate before optimizing the portal to submit faster, or we just move the queue.

---

**Bottom line for the team:** Do **P0.1 + P0.2 + P0.3 in one coordinated change** — that closes the data-corruption cliff and the throughput collapse at small effort. Treat P1.1/P1.2 as the immediate follow-up (gated on the migration-shim decision). Everything in P2/P3 is real but secondary; sequence it strictly after P0 because most of it (batch gather, claim_jobs, timeouts) is *unsafe or N×-amplifying* if shipped before the cliff and the poll loop are fixed.

**Relevant files (all absolute):**
- `/home/mason/dms-dev/dms/src/portal/backend/orchestrator.py` — loop 111-127; preview driver 131-208; exec driver 212-248; resolution `_data_jobs_by_request` 274-285; submit double-write 198+206; confirm 250-270; failure buckets 37-40
- `/home/mason/dms-dev/dms/src/portal/backend/db.py` — pool 117-128 (`max_size=10`, autocommit); `preview_totals` JSONB sum 223-233; `list_jobs` 279-294; **unused `claim_jobs` 296-307**; `jobs_in_states` 309-318; generic `update_job` 333-346; DDL/index 59-99 (no `submitted_at`/`last_polled_at`, single `(batch_id,state)` index, no migration path)
- `/home/mason/dms-dev/dms/src/portal/backend/dms_client.py` — client 52-56 (no `limits=`, single 15s timeout)
- `/home/mason/dms-dev/dms/src/portal/backend/config.py` — `dms_timeout_seconds` 72, `backup_concurrency` 94, `backup_poll_seconds` 95, `backup_actor_prefix` 92
- `/home/mason/dms-dev/dms/src/portal/backend/app.py:83` — single `create_task`
- `/home/mason/dms-dev/dms/src/portal/frontend/src/interfaces/operator/backup/BackupBatchDetail.tsx` — `loadJobs` 34-49, `reload`→`loadJobs(true)` 49-57, `setInterval(reload, 4000)` 66
- `/home/mason/dms-dev/dms/src/dms/api/routers/operations.py:462-479` — `list_data_jobs` route, **no `request_id` param** (confirmed)
- `/home/mason/dms-dev/dms/src/dms/repositories/data_jobs.py` — `get_data_job_by_request` 91-95 (reusable SQL); `list_data_jobs` 210-252 (filters lack `request_id`)
