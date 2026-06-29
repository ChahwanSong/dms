import { useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type BackupBatch, type BackupRequest } from "../../../api";
import VirtualRows from "../../../components/VirtualRows";
import {
  batchStatus,
  requestState,
  fmtBytes,
  friendlyError,
  rowsToCsv,
  CSV_TEMPLATE_TEXT,
  CSV_FORMAT_HINT,
} from "./helpers";
import { errMsg, fmtTime } from "./BackupBatches";
import { SpecGrid, type KV } from "./ui";
import BackupBatchForm from "./BackupBatchForm";
import BackupRequestEdit from "./BackupRequestEdit";
import Loading from "../../../components/Loading";
import BackupCsvModal from "./BackupCsvModal";

const PAGE = 200;
const STATE_ORDER = [
  "registered",
  "held",
  "preview_pending",
  "preview_ready",
  "approved",
  "preview_failed",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];
const EDITABLE = ["registered", "preview_ready", "preview_failed", "failed", "cancelled"];
const RETRYABLE = ["preview_failed", "failed"];

const num = (n?: number | null) => (n == null ? "—" : n.toLocaleString());
const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n) + "…" : s);

// storage-relative path -> absolute, by prefixing the storage managed_root.
function absPath(root: string | undefined, path: string): string {
  if (!root) return path;
  return `${root.replace(/\/+$/, "")}/${path}`;
}

// True when a request carries anything worth expanding into the detail panel.
function hasDetail(j: BackupRequest): boolean {
  return Boolean(j.preview || j.result || j.error || j.dms_job_id);
}

// Rich, structured per-request detail: route, preview vs execution metrics,
// identifiers, and any error. Only fields actually present are shown (dsync
// reports file/byte counts; nsync reports process/pod/node counts).
function RequestDetail({ j, roots }: { j: BackupRequest; roots: Record<string, string> }) {
  const p = j.preview;
  const rs = j.result?.summary;

  const prev: KV[] = [];
  if (p) {
    if (p.files != null) prev.push({ label: "파일", value: p.files.toLocaleString() });
    if (p.dirs != null) prev.push({ label: "디렉터리", value: p.dirs.toLocaleString() });
    if (p.bytes != null) prev.push({ label: "크기", value: fmtBytes(p.bytes) });
    if (p.errors != null)
      prev.push({ label: "에러", value: num(p.errors), tone: p.errors ? "tone-danger-text" : "" });
    if (p.tool) prev.push({ label: "도구", value: p.tool });
  }

  const exec: KV[] = [];
  if (rs) {
    const add = (label: string, v: number | null | undefined, tone?: string) => {
      if (v != null) exec.push({ label, value: v.toLocaleString(), tone });
    };
    add("파일", rs.file_count);
    add("디렉터리", rs.directory_count);
    if (rs.total_bytes != null) exec.push({ label: "크기", value: fmtBytes(rs.total_bytes) });
    add("에러", rs.error_count, rs.error_count ? "tone-danger-text" : "");
    if (rs.selected_tool) exec.push({ label: "도구", value: rs.selected_tool });
    add("프로세스", rs.process_count);
    add("워커 파드", rs.worker_pod_count);
    add("노드당 프로세스", rs.processes_per_node);
    add("출발 노드", rs.source_node_count);
    add("대상 노드", rs.destination_node_count);
  }

  const ids: KV[] = [];
  if (j.dms_job_id) ids.push({ label: "job id", value: j.dms_job_id, mono: true, span: true });
  if (j.dms_request_id)
    ids.push({ label: "request id", value: j.dms_request_id, mono: true, span: true });
  if (j.fingerprint)
    ids.push({ label: "fingerprint", value: j.fingerprint, mono: true, span: true });
  ids.push({ label: "수정", value: fmtTime(j.updated_at) });

  return (
    <div className="req-detail">
      <div className="req-route">
        <span className="route-end">
          <span className="route-eyebrow">출발 · {j.src_storage}</span>
          <code>{absPath(roots[j.src_storage], j.src_path)}</code>
        </span>
        <span className="route-arrow">→</span>
        <span className="route-end">
          <span className="route-eyebrow">대상 · {j.dst_storage}</span>
          <code>{absPath(roots[j.dst_storage], j.dst_path)}</code>
        </span>
      </div>

      {(prev.length > 0 || exec.length > 0) && (
        <div className="req-secs">
          {prev.length > 0 && (
            <section className="req-sec">
              <h4>Preview (dry-run)</h4>
              <SpecGrid items={prev} />
            </section>
          )}
          {exec.length > 0 && (
            <section className="req-sec">
              <h4>실행 결과</h4>
              <SpecGrid items={exec} />
            </section>
          )}
        </div>
      )}

      <section className="req-sec">
        <h4>식별자</h4>
        <SpecGrid items={ids} />
      </section>

      {j.error && (
        <div className="req-error">
          {friendlyError(j.error)}
          {friendlyError(j.error) !== j.error && (
            <span className="req-error-code"> ({j.error})</span>
          )}
        </div>
      )}
    </div>
  );
}

export default function BackupBatchDetail({
  batchId,
  onBack,
}: {
  batchId: string;
  onBack: () => void;
}) {
  const [batch, setBatch] = useState<BackupBatch | null>(null);
  const [jobs, setJobs] = useState<BackupRequest[]>([]);
  const [stateFilter, setStateFilter] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  // guards overlapping page loads (endReached can fire repeatedly while scrolling)
  const loadingRef = useRef(false);
  // mirror of `jobs` so the load callbacks can read the loaded count without
  // re-creating themselves on every append.
  const jobsRef = useRef<BackupRequest[]>([]);
  const [showEdit, setShowEdit] = useState(false);
  const [editingReq, setEditingReq] = useState<BackupRequest | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // storage_name -> managed_root, so the UI can show real absolute paths.
  const [roots, setRoots] = useState<Record<string, string>>({});
  // inline "add row" editor + CSV/text popup
  const [showAdd, setShowAdd] = useState(false);
  const [addSrc, setAddSrc] = useState("");
  const [addDst, setAddDst] = useState("");
  const [csvModal, setCsvModal] = useState<{
    mode: "view" | "replace";
    title: string;
    hint?: string;
    text: string;
  } | null>(null);

  useEffect(() => {
    operatorApi.storage
      .list()
      .then((list) => {
        const m: Record<string, string> = {};
        for (const s of list) {
          const mr = (s.backend_template as Record<string, unknown>)?.managed_root;
          if (typeof mr === "string") m[s.storage_name] = mr;
        }
        setRoots(m);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    jobsRef.current = jobs;
  }, [jobs]);

  // Load a page of requests. "reset" replaces the list (initial load, filter
  // change, post-mutation reload); "append" adds the next PAGE for infinite
  // scroll. The append path is guarded so overlapping endReached calls don't
  // double-load the same page.
  const loadPage = useCallback(
    async (mode: "reset" | "append") => {
      if (mode === "append" && loadingRef.current) return;
      loadingRef.current = true;
      if (mode === "append") setLoadingMore(true);
      try {
        const offset = mode === "reset" ? 0 : jobsRef.current.length;
        const page = await operatorApi.backup.requests(batchId, {
          state: stateFilter || undefined,
          limit: PAGE,
          offset,
        });
        setJobs((prev) => (mode === "reset" ? page : [...prev, ...page]));
      } finally {
        loadingRef.current = false;
        if (mode === "append") setLoadingMore(false);
      }
    },
    [batchId, stateFilter],
  );

  // Re-fetch the currently-loaded rows in place (offset 0, same count) so live
  // polling refreshes states/results WITHOUT collapsing the list back to page 1
  // or jumping the scroll. Selection/expand are keyed by id, so they survive the
  // replace. Skipped while a page load is in flight.
  const refreshInPlace = useCallback(async () => {
    if (loadingRef.current) return;
    const limit = Math.min(Math.max(jobsRef.current.length, PAGE), 2000);
    const page = await operatorApi.backup.requests(batchId, {
      state: stateFilter || undefined,
      limit,
      offset: 0,
    });
    setJobs(page);
  }, [batchId, stateFilter]);

  const reload = useCallback(async () => {
    try {
      const b = await operatorApi.backup.get(batchId);
      setBatch(b);
      await loadPage("reset");
    } catch (e) {
      setError(errMsg(e));
    }
  }, [batchId, loadPage]);

  // Background poll tick: refresh batch aggregates + loaded rows in place.
  const poll = useCallback(async () => {
    try {
      const b = await operatorApi.backup.get(batchId);
      setBatch(b);
      await refreshInPlace();
    } catch {
      /* ignore transient poll errors */
    }
  }, [batchId, refreshInPlace]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Live-poll while a batch is actively previewing or running.
  useEffect(() => {
    if (!batch || (batch.status !== "previewing" && batch.status !== "running")) return;
    const t = setInterval(poll, 4000);
    return () => clearInterval(t);
  }, [batch, poll]);

  // Keep the selection ⊆ the LOADED rows (`jobs` holds every loaded page, not
  // just the on-screen virtual window — so selection survives scrolling and
  // appends). Rows can still vanish after a delete / CSV replace / filter change;
  // those stale ids are pruned so the bulk bar count stays actionable.
  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev;
      const live = new Set(jobs.map((j) => j.id));
      let changed = false;
      const next = new Set<number>();
      for (const id of prev) {
        if (live.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [jobs]);

  async function act(fn: () => Promise<unknown>, ok: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await fn();
      setNotice(ok);
      await reload();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }
  function toggleExpand(id: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function cancelRow(j: BackupRequest) {
    if (
      !window.confirm(
        `요청을 취소합니다.\n${j.src_storage}:${j.src_path} → ${j.dst_storage}:${j.dst_path}`,
      )
    )
      return;
    await act(() => operatorApi.backup.cancelRequest(batchId, j.id), "요청을 취소했습니다.");
  }

  if (!batch) {
    return (
      <div className="inventory">
        <button className="ghost mini" onClick={onBack}>
          ← 목록
        </button>
        {error ? <div className="banner err">{error}</div> : <Loading rows={3} />}
      </div>
    );
  }

  const st = batchStatus(batch.status);
  const counts = batch.state_counts || {};
  const totals = batch.preview_totals;
  const status = batch.status;
  // Storage is batch-level (uniform across rows); derive from the loaded requests.
  const batchSrc = jobs[0]?.src_storage;
  const batchDst = jobs[0]?.dst_storage;
  // ownership/permission overrides (sync options) for the header summary.
  const bopts = (batch.options || {}) as Record<string, unknown>;
  const chownVal =
    typeof bopts.chown === "string" && bopts.chown.trim() ? bopts.chown.trim() : null;
  const chmodVal =
    typeof bopts.chmod === "string" && bopts.chmod.trim() ? bopts.chmod.trim() : null;
  const deleteConfirm = "--delete 배치입니다. 승인 항목은 dst에서 src에 없는 파일을 삭제합니다. 실행할까요?";

  // selective approval is available while a batch is previewed or running and
  // still has undecided preview_ready requests.
  const canSelect = (status === "previewed" || status === "running") && (counts.preview_ready ?? 0) > 0;
  // total rows for the active view (state_counts is authoritative): drives the
  // infinite-load stop condition + the "loaded / total" footer.
  const total = stateFilter
    ? counts[stateFilter] ?? 0
    : Object.values(counts).reduce((a, b) => a + b, 0) || jobs.length;

  function approveAll() {
    if (batch!.delete_enabled && !window.confirm(deleteConfirm)) return;
    act(async () => {
      await operatorApi.backup.approve(batchId);
      setSelected(new Set());
    }, "전체 승인 — 실행을 시작합니다.");
  }
  function closeBatch() {
    if (!window.confirm("배치를 마감합니다. 승인하지 않은 'Preview 완료' 항목은 제외됩니다.")) return;
    act(() => operatorApi.backup.close(batchId), "배치를 마감했습니다.");
  }
  async function resetOne(j: BackupRequest) {
    await act(
      () => operatorApi.backup.resetRequests(batchId, { request_ids: [j.id] }),
      "재시도 대기로 되돌렸습니다. 'Re-preview'를 누르세요.",
    );
  }
  function retryFailed() {
    const n = (counts.preview_failed ?? 0) + (counts.failed ?? 0);
    if (!window.confirm(`실패 항목 ${n}개를 재시도 대기로 되돌립니다. 이후 'Re-preview'를 누르세요.`)) return;
    act(
      () => operatorApi.backup.resetRequests(batchId, { failed_only: true }),
      "실패 항목을 재시도 대기로 되돌렸습니다.",
    );
  }
  const failedCount = (counts.preview_failed ?? 0) + (counts.failed ?? 0);
  const canRepreview = (status === "previewed" || status === "done") && (counts.registered ?? 0) > 0;

  // --- item management (add / delete / CSV) + general bulk selection -------
  const INFLIGHT_STATES = ["preview_pending", "approved", "running"];
  const mutable = status !== "previewing" && status !== "running"; // request set editable
  const hasStorage = Boolean(batchSrc && batchDst);
  const selectedJobs = jobs.filter((j) => selected.has(j.id));
  const selReady = selectedJobs.filter((j) => j.state === "preview_ready");
  const selResettable = selectedJobs.filter((j) => EDITABLE.includes(j.state));
  const selDeletable = selectedJobs.filter((j) => !INFLIGHT_STATES.includes(j.state));
  const selCancelable = selectedJobs.filter((j) => INFLIGHT_STATES.includes(j.state));
  const selPreviewable = selectedJobs.filter((j) => j.state === "registered");
  // Preview can only start from a settled batch (not while previewing/running).
  const canPreviewSel = status === "draft" || status === "previewed" || status === "done";
  const allSelected = jobs.length > 0 && selected.size === jobs.length;

  function toggleSelectAll() {
    setSelected((prev) =>
      prev.size === jobs.length ? new Set() : new Set(jobs.map((j) => j.id)),
    );
  }
  function clearSelection() {
    setSelected(new Set());
  }
  function bulkPreview() {
    const ids = selPreviewable.map((j) => j.id);
    if (!ids.length) return;
    act(async () => {
      await operatorApi.backup.preview(batchId, { request_ids: ids });
      clearSelection();
    }, `${ids.length}개 항목 Preview를 시작합니다.`);
  }
  function bulkApprove() {
    const ids = selReady.map((j) => j.id);
    if (!ids.length) return;
    if (batch!.delete_enabled && !window.confirm(deleteConfirm)) return;
    act(async () => {
      await operatorApi.backup.approve(batchId, { request_ids: ids });
      clearSelection();
    }, `${ids.length}개 승인 — 실행을 시작합니다.`);
  }
  function bulkRetry() {
    const ids = selResettable.map((j) => j.id);
    if (!ids.length) return;
    act(async () => {
      await operatorApi.backup.resetRequests(batchId, { request_ids: ids });
      clearSelection();
    }, `${ids.length}개를 재시도 대기로 되돌렸습니다. Re-preview를 누르세요.`);
  }
  function bulkDelete() {
    const ids = selDeletable.map((j) => j.id);
    if (!ids.length) return;
    if (!window.confirm(`선택한 ${ids.length}개 항목을 삭제합니다. 계속할까요?`)) return;
    act(async () => {
      await operatorApi.backup.deleteRequests(batchId, ids);
      clearSelection();
    }, `${ids.length}개 항목을 삭제했습니다.`);
  }
  function bulkCancel() {
    const ids = selCancelable.map((j) => j.id);
    if (!ids.length) return;
    if (!window.confirm(`선택한 ${ids.length}개 진행 중 항목을 취소합니다. 계속할까요?`)) return;
    act(async () => {
      await operatorApi.backup.cancelRequests(batchId, ids);
      clearSelection();
    }, `${ids.length}개 항목을 취소했습니다.`);
  }
  async function deleteRow(j: BackupRequest) {
    if (!window.confirm(`항목을 삭제합니다.\n${j.src_path} → ${j.dst_path}`)) return;
    await act(() => operatorApi.backup.deleteRequests(batchId, [j.id]), "항목을 삭제했습니다.");
  }
  async function addRow() {
    if (!batchSrc || !batchDst) return;
    if (!addSrc.trim() || !addDst.trim()) {
      setError("출발/대상 경로를 모두 입력하세요.");
      return;
    }
    await act(async () => {
      await operatorApi.backup.addRequests(batchId, [
        { src_storage: batchSrc, src_path: addSrc.trim(), dst_storage: batchDst, dst_path: addDst.trim() },
      ]);
      setAddSrc("");
      setAddDst("");
      setShowAdd(false);
    }, "항목을 추가했습니다.");
  }
  // "현재 항목" — copyable CSV/text of the current request set (no download).
  function openCurrent() {
    const rows = jobs.map((j) => ({ src_path: j.src_path, dst_path: j.dst_path }));
    setCsvModal({
      mode: "view",
      title: "현재 항목 (CSV / 텍스트)",
      hint: `${jobs.length}개 항목. 아래 텍스트를 복사해 다른 배치에 붙여넣을 수 있습니다.`,
      text: rowsToCsv(rows),
    });
  }
  // "템플릿" — example version of the current-items text.
  function openTemplate() {
    setCsvModal({
      mode: "view",
      title: "CSV / 텍스트 템플릿 (예시)",
      hint: CSV_FORMAT_HINT,
      text: CSV_TEMPLATE_TEXT,
    });
  }
  // "업로드 (전체 교체)" — paste text (or load a file) then replace-all.
  function openUpload() {
    setCsvModal({ mode: "replace", title: "텍스트 붙여넣기 → 전체 교체", hint: CSV_FORMAT_HINT, text: "" });
  }
  function replaceFromRows(rows: { src_path: string; dst_path: string }[]) {
    if (!batchSrc || !batchDst) {
      setError("기존 항목이 없어 스토리지를 알 수 없습니다 — '배치 편집' 폼을 사용하세요.");
      return;
    }
    if (
      !window.confirm(
        `${rows.length}개 항목으로 전체 교체합니다.\n기존 ${jobs.length}개는 모두 대체됩니다(등록됨 → Re-preview 필요). 계속할까요?`,
      )
    )
      return;
    const reqs = rows.map((r) => ({
      src_storage: batchSrc,
      src_path: r.src_path,
      dst_storage: batchDst,
      dst_path: r.dst_path,
    }));
    act(() => operatorApi.backup.replaceRequests(batchId, reqs), `${reqs.length}개 항목으로 교체했습니다.`);
    setCsvModal(null);
  }

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2 style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          <button className="ghost mini" onClick={onBack}>
            ← 목록
          </button>
          {batch.name} <span className={`san ${st.cls}`}>{st.label}</span>
        </h2>
        <div className="inv-actions">
          <button className="ghost" onClick={reload} disabled={busy}>
            새로고침
          </button>
          {status === "draft" && (
            <>
              <button className="ghost" disabled={busy} onClick={() => setShowEdit(true)}>
                ✎ 편집
              </button>
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  act(() => operatorApi.backup.preview(batchId), "Preview를 시작했습니다.")
                }
              >
                Preview
              </button>
            </>
          )}
          {canSelect && (
            <>
              <button className="ghost" disabled={busy} onClick={approveAll}>
                전체 승인
              </button>
              <button className="ghost" disabled={busy} onClick={closeBatch}>
                마감
              </button>
            </>
          )}
          {canRepreview && (
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                act(() => operatorApi.backup.preview(batchId), "Re-preview를 시작했습니다.")
              }
            >
              Re-preview
            </button>
          )}
          {status !== "draft" && status !== "previewing" && failedCount > 0 && (
            <button className="ghost" disabled={busy} onClick={retryFailed}>
              실패 재시도 ({failedCount})
            </button>
          )}
          {(status === "previewing" || status === "running" || status === "previewed") && (
            <button
              className="ghost danger"
              disabled={busy}
              onClick={() => {
                if (!window.confirm("배치를 취소합니다. 진행 중인 요청도 취소됩니다.")) return;
                act(() => operatorApi.backup.cancel(batchId), "배치를 취소했습니다.");
              }}
            >
              배치 취소
            </button>
          )}
        </div>
      </div>

      <div className="batch-meta">
        {batchSrc && (
          <div className="batch-route">
            <span className="route-end">
              <span className="route-eyebrow">출발 스토리지</span>
              <code>{batchSrc}</code>
              {roots[batchSrc] && <span className="route-path">{roots[batchSrc]}</span>}
            </span>
            <span className="route-arrow">→</span>
            <span className="route-end">
              <span className="route-eyebrow">대상 스토리지</span>
              <code>{batchDst}</code>
              {batchDst && roots[batchDst] && (
                <span className="route-path">{roots[batchDst]}</span>
              )}
            </span>
          </div>
        )}
        <SpecGrid
          items={[
            { label: "requester", value: batch.requester_id, mono: true },
            {
              label: "소유권",
              value: chownVal ? `변경 → ${chownVal}` : "원본 보존",
              tone: chownVal ? "tone-warn-text" : "",
              mono: !!chownVal,
            },
            ...(chmodVal ? [{ label: "권한(chmod)", value: chmodVal, mono: true } as KV] : []),
            {
              label: "--delete",
              value: batch.delete_enabled ? "켜짐 (완전 미러)" : "꺼짐",
              tone: batch.delete_enabled ? "tone-danger-text" : "",
            },
            { label: "우선순위", value: batch.priority ?? "Low" },
            {
              label: "병렬 노드",
              value: batch.node_count != null ? String(batch.node_count) : "자동",
            },
            ...(batch.note ? [{ label: "메모", value: batch.note, span: true } as KV] : []),
          ]}
        />
      </div>

      {/* progress / aggregate summary */}
      <div className="inv-summary">
        {STATE_ORDER.filter((s) => counts[s]).map((s) => {
          const j = requestState(s);
          return (
            <button
              key={s}
              className={`sum-chip ${j.cls}${stateFilter === s ? " active" : ""}`}
              onClick={() => setStateFilter(stateFilter === s ? "" : s)}
            >
              <span className="sum-n">{counts[s]}</span>
              <span className="sum-l">{j.label}</span>
            </button>
          );
        })}
        {totals && (counts.preview_ready ?? 0) > 0 && (
          <span className="sum-chip" style={{ cursor: "default" }}>
            <span className="sum-n">{totals.files.toLocaleString()}</span>
            <span className="sum-l">파일 · {fmtBytes(totals.bytes)}</span>
          </span>
        )}
      </div>

      {notice && <div className="banner ok">{notice}</div>}
      {error && <div className="banner err">{error}</div>}

      {mutable && (
        <div className="item-toolbar">
          <button
            className="ghost mini"
            disabled={busy || !hasStorage}
            onClick={() => setShowAdd((v) => !v)}
            title={hasStorage ? undefined : "기존 항목이 없어 스토리지를 알 수 없음 — '배치 편집' 사용"}
          >
            + 행 추가
          </button>
          <span className="tb-sep" aria-hidden="true" />
          <span className="muted small">CSV / 텍스트</span>
          <button className="ghost mini" onClick={openTemplate}>
            템플릿
          </button>
          <button className="ghost mini" disabled={!jobs.length} onClick={openCurrent}>
            현재 항목
          </button>
          <button className="ghost mini" disabled={busy || !hasStorage} onClick={openUpload}>
            업로드 (전체 교체)
          </button>
        </div>
      )}
      {showAdd && hasStorage && (
        <div className="add-row">
          <span className="muted small mono">{batchSrc}:</span>
          <input
            className="add-input"
            placeholder="출발 경로 (예: e2e/src)"
            value={addSrc}
            onChange={(e) => setAddSrc(e.target.value)}
          />
          <span className="route-arrow">→</span>
          <span className="muted small mono">{batchDst}:</span>
          <input
            className="add-input"
            placeholder="대상 경로 (예: backup_x)"
            value={addDst}
            onChange={(e) => setAddDst(e.target.value)}
          />
          <button className="primary mini" disabled={busy} onClick={addRow}>
            추가
          </button>
          <button
            className="ghost mini"
            onClick={() => {
              setShowAdd(false);
              setAddSrc("");
              setAddDst("");
            }}
          >
            취소
          </button>
        </div>
      )}
      {selected.size > 0 && (
        <div className="bulk-bar">
          <span className="bulk-count">{selected.size}개 선택</span>
          {canPreviewSel && (
            <button
              className="primary mini"
              disabled={busy || selPreviewable.length === 0}
              onClick={bulkPreview}
              title="선택한 등록됨 항목만 Preview (나머지는 보류 후 복원)"
            >
              Preview ({selPreviewable.length})
            </button>
          )}
          <button className="primary mini" disabled={busy || selReady.length === 0} onClick={bulkApprove}>
            승인 ({selReady.length})
          </button>
          <button className="ghost mini" disabled={busy || selResettable.length === 0} onClick={bulkRetry}>
            재시도 ({selResettable.length})
          </button>
          {mutable && (
            <button className="mini danger" disabled={busy || selDeletable.length === 0} onClick={bulkDelete}>
              삭제 ({selDeletable.length})
            </button>
          )}
          <button className="mini danger" disabled={busy || selCancelable.length === 0} onClick={bulkCancel}>
            취소 ({selCancelable.length})
          </button>
          <button className="ghost mini" onClick={clearSelection}>
            선택 해제
          </button>
        </div>
      )}

      <VirtualRows
        items={jobs}
        computeItemKey={(_i, j) => j.id}
        endReached={() => {
          if (!loadingRef.current && jobs.length < total) loadPage("append");
        }}
        empty={stateFilter ? "해당 상태의 요청이 없습니다." : "요청이 없습니다."}
        header={
          <div className="vrow vgrid-bk vhead">
            <div className="vcell vc-toggle" />
            <div className="vcell vc-check">
              <label className="check-cell">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  disabled={jobs.length === 0}
                  aria-label="전체 선택"
                />
              </label>
            </div>
            <div className="vcell">출발 (src)</div>
            <div className="vcell">대상 (dst)</div>
            <div className="vcell">상태</div>
            <div className="vcell">Preview (파일 · 크기)</div>
            <div className="vcell">비고</div>
            <div className="vcell" />
          </div>
        }
        footer={
          loadingMore ? (
            <div className="vfoot">
              <Loading label="불러오는 중…" />
            </div>
          ) : jobs.length < total ? (
            <div className="vfoot muted small">
              스크롤하면 더 불러옵니다… ({jobs.length.toLocaleString()} / {total.toLocaleString()})
            </div>
          ) : null
        }
        itemContent={(_i, j) => {
          const s = requestState(j.state);
          const expandable = hasDetail(j);
          const isOpen = expanded.has(j.id);
          const cancellable = INFLIGHT_STATES.includes(j.state); // in-flight only
          return (
            <div className={`vitem${isOpen ? " open" : ""}`}>
              <div
                className={`vrow vgrid-bk${expandable ? " expandable" : ""}${isOpen ? " row-open" : ""}`}
                onClick={expandable ? () => toggleExpand(j.id) : undefined}
                tabIndex={expandable ? 0 : undefined}
                aria-expanded={expandable ? isOpen : undefined}
                onKeyDown={
                  expandable
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          toggleExpand(j.id);
                        }
                      }
                    : undefined
                }
              >
                <div className="vcell vc-toggle">
                  {expandable && (
                    <span
                      className={`expand-toggle${isOpen ? " open" : ""}`}
                      aria-hidden="true"
                    >
                      ▸
                    </span>
                  )}
                </div>
                <div className="vcell vc-check" onClick={(e) => e.stopPropagation()}>
                  <label className="check-cell">
                    <input
                      type="checkbox"
                      checked={selected.has(j.id)}
                      onChange={() => toggleSelect(j.id)}
                      aria-label="항목 선택"
                    />
                  </label>
                </div>
                <div className="vcell vc-primary mono small" data-label="출발">
                  {j.src_storage === batchSrc ? j.src_path : `${j.src_storage}:${j.src_path}`}
                </div>
                <div className="vcell mono small" data-label="대상">
                  {j.dst_storage === batchDst ? j.dst_path : `${j.dst_storage}:${j.dst_path}`}
                </div>
                <div className="vcell" data-label="상태">
                  <span className={`san ${s.cls}`}>{s.label}</span>
                </div>
                <div className="vcell muted small" data-label="Preview">
                  {j.preview
                    ? `${(j.preview.files ?? 0).toLocaleString()} · ${fmtBytes(j.preview.bytes)}`
                    : "—"}
                </div>
                <div className="vcell small" data-label="비고">
                  {j.error ? (
                    <span className="err-num" title={j.error}>
                      {truncate(friendlyError(j.error) || j.error, 30)}
                    </span>
                  ) : (
                    <span className="muted">
                      {j.result?.summary?.selected_tool || j.preview?.tool || "—"}
                    </span>
                  )}
                </div>
                <div className="vcell row-actions" onClick={(e) => e.stopPropagation()}>
                  {EDITABLE.includes(j.state) && (
                    <button className="mini" onClick={() => setEditingReq(j)} disabled={busy}>
                      편집
                    </button>
                  )}
                  {RETRYABLE.includes(j.state) && (
                    <button className="mini" onClick={() => resetOne(j)} disabled={busy}>
                      재시도
                    </button>
                  )}
                  {mutable && !INFLIGHT_STATES.includes(j.state) && (
                    <button className="mini danger" onClick={() => deleteRow(j)} disabled={busy}>
                      삭제
                    </button>
                  )}
                  {cancellable && (
                    <button className="mini danger" onClick={() => cancelRow(j)} disabled={busy}>
                      취소
                    </button>
                  )}
                </div>
              </div>
              {isOpen && expandable && (
                <div className="vdetail">
                  <RequestDetail j={j} roots={roots} />
                </div>
              )}
            </div>
          );
        }}
      />

      {showEdit && (
        <BackupBatchForm
          mode="edit"
          initial={batch}
          onClose={() => setShowEdit(false)}
          onSaved={({ added }) => {
            setShowEdit(false);
            setNotice(`저장됨 (요청 ${added}개).`);
            reload();
          }}
        />
      )}
      {editingReq && (
        <BackupRequestEdit
          batchId={batchId}
          request={editingReq}
          onClose={() => setEditingReq(null)}
          onSaved={() => {
            setEditingReq(null);
            setNotice("요청 경로를 수정했습니다.");
            reload();
          }}
        />
      )}
      {csvModal && (
        <BackupCsvModal
          title={csvModal.title}
          hint={csvModal.hint}
          initialText={csvModal.text}
          mode={csvModal.mode}
          busy={busy}
          onReplace={replaceFromRows}
          onClose={() => setCsvModal(null)}
        />
      )}
    </div>
  );
}
