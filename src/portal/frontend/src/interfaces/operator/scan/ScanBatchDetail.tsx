import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type ScanBatch, type ScanRequest } from "../../../api";
import {
  batchStatus,
  requestState,
  fmtBytes,
  friendlyError,
  pathsToCsv,
  CSV_TEMPLATE_TEXT,
  CSV_FORMAT_HINT,
  type ScanRow,
} from "./helpers";
import { errMsg, fmtTime } from "./ScanBatches";
import { SpecGrid, type KV } from "./ui";
import ScanBatchForm from "./ScanBatchForm";
import ScanRequestEdit from "./ScanRequestEdit";
import ScanResults from "./ScanResults";
import { ScanHistBar, ScanHistFull } from "./ScanHist";
import Loading from "../../../components/Loading";
import ScanCsvModal from "./ScanCsvModal";

const PAGE = 200;
const STATE_ORDER = ["registered", "held", "running", "succeeded", "failed", "cancelled"];
// Path edit allowed on anything not actively running; reset/retry targets the
// fixable terminal states; selective run targets registered/held; only running is
// truly in-flight (scan has no preview/approve states).
const EDITABLE = ["registered", "held", "failed", "cancelled", "succeeded"];
const RESETTABLE = ["failed", "cancelled"];
const RUNNABLE = ["registered", "held"];
const INFLIGHT_STATES = ["running"];

const num = (n?: number | null) => (n == null ? "—" : n.toLocaleString());
const truncate = (s: string, n: number) => (s.length > n ? s.slice(0, n) + "…" : s);

// storage-relative path -> absolute, by prefixing the storage managed_root.
function absPath(root: string | undefined, path: string): string {
  if (!root) return path;
  return `${root.replace(/\/+$/, "")}/${path}`;
}

// True when a request carries anything worth expanding into the detail panel.
function hasDetail(j: ScanRequest): boolean {
  return Boolean(j.result || j.error || j.dms_job_id);
}

// Rich, structured per-request detail: scan path, result metrics, identifiers,
// and any error. Only fields actually present are shown.
function RequestDetail({ j, roots }: { j: ScanRequest; roots: Record<string, string> }) {
  const r = j.result;

  const res: KV[] = [];
  if (r) {
    if (r.file_count != null) res.push({ label: "파일", value: r.file_count.toLocaleString() });
    if (r.directory_count != null)
      res.push({ label: "디렉터리", value: r.directory_count.toLocaleString() });
    if (r.total_bytes != null) res.push({ label: "크기", value: fmtBytes(r.total_bytes) });
    if (r.error_count != null)
      res.push({ label: "오류", value: num(r.error_count), tone: r.error_count ? "tone-danger-text" : "" });
    if (r.tool) res.push({ label: "도구", value: r.tool });
    if (r.scan_root) res.push({ label: "scan_root", value: r.scan_root, mono: true, span: true });
  }

  const ids: KV[] = [];
  if (j.dms_job_id) ids.push({ label: "job id", value: j.dms_job_id, mono: true, span: true });
  if (j.dms_request_id)
    ids.push({ label: "request id", value: j.dms_request_id, mono: true, span: true });
  ids.push({ label: "수정", value: fmtTime(j.updated_at) });

  return (
    <div className="req-detail">
      <div className="req-route">
        <span className="route-end">
          <span className="route-eyebrow">스토리지 · {j.storage}</span>
          <code>{absPath(roots[j.storage], j.path)}</code>
        </span>
      </div>

      {res.length > 0 && (
        <div className="req-secs">
          <section className="req-sec">
            <h4>스캔 결과</h4>
            <SpecGrid items={res} />
          </section>
        </div>
      )}

      {r?.atime_histogram && r.atime_histogram.length > 0 && (
        <section className="req-sec">
          <h4>atime 데이터 온도 (hot → cold)</h4>
          <ScanHistFull hist={r.atime_histogram} />
        </section>
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

export default function ScanBatchDetail({
  batchId,
  onBack,
}: {
  batchId: string;
  onBack: () => void;
}) {
  const [batch, setBatch] = useState<ScanBatch | null>(null);
  const [jobs, setJobs] = useState<ScanRequest[]>([]);
  const [stateFilter, setStateFilter] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const offsetRef = useRef(0);
  const [showEdit, setShowEdit] = useState(false);
  const [editingReq, setEditingReq] = useState<ScanRequest | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // storage_name -> managed_root, so the UI can show real absolute paths.
  const [roots, setRoots] = useState<Record<string, string>>({});
  // inline "add row" editor + CSV/text popup
  const [showAdd, setShowAdd] = useState(false);
  const [addPath, setAddPath] = useState("");
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

  const loadJobs = useCallback(
    async (reset: boolean) => {
      const offset = reset ? 0 : offsetRef.current;
      const page = await operatorApi.scan.requests(batchId, {
        state: stateFilter || undefined,
        limit: PAGE,
        offset,
      });
      offsetRef.current = offset + page.length;
      setHasMore(page.length === PAGE);
      setJobs((prev) => (reset ? page : [...prev, ...page]));
    },
    [batchId, stateFilter],
  );

  const reload = useCallback(async () => {
    try {
      const b = await operatorApi.scan.get(batchId);
      setBatch(b);
      await loadJobs(true);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [batchId, loadJobs]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Live-poll while a batch is actively scanning.
  useEffect(() => {
    if (!batch || batch.status !== "scanning") return;
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [batch, reload]);

  // Keep the selection limited to currently-visible rows. Rows can vanish after a
  // delete / CSV replace / filter change; stale ids would otherwise leave the bulk
  // bar showing a "N개 선택" count that no action can act on.
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

  async function cancelRow(j: ScanRequest) {
    if (!window.confirm(`요청을 취소합니다.\n${j.storage}:${j.path}`)) return;
    await act(() => operatorApi.scan.cancelRequest(batchId, j.id), "요청을 취소했습니다.");
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
  const rt = batch.result_totals;
  const status = batch.status;
  // Storage is batch-level (uniform across rows); derive from the loaded requests.
  const batchStorage = jobs[0]?.storage;

  // columns: chevron + checkbox + 경로/상태/결과/온도/비고(5) + actions
  const cols = 8;

  const mutable = status !== "scanning"; // request set editable when not scanning
  const hasStorage = Boolean(batchStorage);
  const registeredCount = counts.registered ?? 0;
  const failedCount = counts.failed ?? 0;
  const canRun = (status === "draft" || status === "done") && registeredCount > 0;
  const canRunSel = status === "draft" || status === "done";

  function runAll() {
    act(async () => {
      await operatorApi.scan.run(batchId);
      setSelected(new Set());
    }, "스캔을 시작했습니다.");
  }
  function rescan() {
    if (!window.confirm("완료·실패 항목을 포함한 모든 요청을 다시 스캔합니다. 계속할까요?")) return;
    act(async () => {
      await operatorApi.scan.rescan(batchId);
      setSelected(new Set());
    }, "재스캔을 시작했습니다.");
  }
  function retryFailed() {
    if (!window.confirm(`실패 항목 ${failedCount}개를 재시도 대기로 되돌립니다. 이후 '스캔 실행'을 누르세요.`)) return;
    act(
      () => operatorApi.scan.resetRequests(batchId, { failed_only: true }),
      "실패 항목을 재시도 대기로 되돌렸습니다.",
    );
  }
  async function resetOne(j: ScanRequest) {
    await act(
      () => operatorApi.scan.resetRequests(batchId, { request_ids: [j.id] }),
      "재시도 대기로 되돌렸습니다. '스캔 실행'을 누르세요.",
    );
  }

  // --- bulk selection -------------------------------------------------------
  const selectedJobs = jobs.filter((j) => selected.has(j.id));
  const selRunnable = selectedJobs.filter((j) => RUNNABLE.includes(j.state));
  const selResettable = selectedJobs.filter((j) => RESETTABLE.includes(j.state));
  const selDeletable = selectedJobs.filter((j) => !INFLIGHT_STATES.includes(j.state));
  const selCancelable = selectedJobs.filter((j) => INFLIGHT_STATES.includes(j.state));
  const allSelected = jobs.length > 0 && selected.size === jobs.length;

  function toggleSelectAll() {
    setSelected((prev) => (prev.size === jobs.length ? new Set() : new Set(jobs.map((j) => j.id))));
  }
  function clearSelection() {
    setSelected(new Set());
  }
  function bulkRun() {
    const ids = selRunnable.map((j) => j.id);
    if (!ids.length) return;
    act(async () => {
      await operatorApi.scan.run(batchId, { request_ids: ids });
      clearSelection();
    }, `${ids.length}개 항목 스캔을 시작합니다.`);
  }
  function bulkRetry() {
    const ids = selResettable.map((j) => j.id);
    if (!ids.length) return;
    act(async () => {
      await operatorApi.scan.resetRequests(batchId, { request_ids: ids });
      clearSelection();
    }, `${ids.length}개를 재시도 대기로 되돌렸습니다. '스캔 실행'을 누르세요.`);
  }
  function bulkDelete() {
    const ids = selDeletable.map((j) => j.id);
    if (!ids.length) return;
    if (!window.confirm(`선택한 ${ids.length}개 항목을 삭제합니다. 계속할까요?`)) return;
    act(async () => {
      await operatorApi.scan.deleteRequests(batchId, ids);
      clearSelection();
    }, `${ids.length}개 항목을 삭제했습니다.`);
  }
  function bulkCancel() {
    const ids = selCancelable.map((j) => j.id);
    if (!ids.length) return;
    if (!window.confirm(`선택한 ${ids.length}개 진행 중 항목을 취소합니다. 계속할까요?`)) return;
    act(async () => {
      await operatorApi.scan.cancelRequests(batchId, ids);
      clearSelection();
    }, `${ids.length}개 항목을 취소했습니다.`);
  }
  async function deleteRow(j: ScanRequest) {
    if (!window.confirm(`항목을 삭제합니다.\n${j.path}`)) return;
    await act(() => operatorApi.scan.deleteRequests(batchId, [j.id]), "항목을 삭제했습니다.");
  }
  async function addRow() {
    if (!batchStorage) return;
    if (!addPath.trim()) {
      setError("스캔 경로를 입력하세요.");
      return;
    }
    await act(async () => {
      await operatorApi.scan.addRequests(batchId, [{ storage: batchStorage, path: addPath.trim() }]);
      setAddPath("");
      setShowAdd(false);
    }, "항목을 추가했습니다.");
  }
  // "현재 항목" — copyable CSV/text of the current request set (no download).
  function openCurrent() {
    const rows = jobs.map((j) => ({ path: j.path }));
    setCsvModal({
      mode: "view",
      title: "현재 항목 (CSV / 텍스트)",
      hint: `${jobs.length}개 항목. 아래 텍스트를 복사해 다른 배치에 붙여넣을 수 있습니다.`,
      text: pathsToCsv(rows),
    });
  }
  function openTemplate() {
    setCsvModal({
      mode: "view",
      title: "CSV / 텍스트 템플릿 (예시)",
      hint: CSV_FORMAT_HINT,
      text: CSV_TEMPLATE_TEXT,
    });
  }
  function openUpload() {
    setCsvModal({ mode: "replace", title: "텍스트 붙여넣기 → 전체 교체", hint: CSV_FORMAT_HINT, text: "" });
  }
  function replaceFromRows(rows: ScanRow[]) {
    if (!batchStorage) {
      setError("기존 항목이 없어 스토리지를 알 수 없습니다 — '배치 편집' 폼을 사용하세요.");
      return;
    }
    if (
      !window.confirm(
        `${rows.length}개 항목으로 전체 교체합니다.\n기존 ${jobs.length}개는 모두 대체됩니다(등록됨 → 재실행 필요). 계속할까요?`,
      )
    )
      return;
    const reqs = rows.map((r) => ({ storage: batchStorage, path: r.path }));
    act(() => operatorApi.scan.replaceRequests(batchId, reqs), `${reqs.length}개 항목으로 교체했습니다.`);
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
          {mutable && (
            <button className="ghost" disabled={busy} onClick={() => setShowEdit(true)}>
              ✎ 편집
            </button>
          )}
          {status === "done" && failedCount > 0 && (
            <button className="ghost" disabled={busy} onClick={retryFailed}>
              실패 재시도 ({failedCount})
            </button>
          )}
          {canRun && (
            <button className={status === "draft" ? "primary" : "ghost"} disabled={busy} onClick={runAll}>
              스캔 실행{status === "done" ? ` (${registeredCount})` : ""}
            </button>
          )}
          {status === "done" && (
            <button className="primary" disabled={busy} onClick={rescan}>
              재스캔
            </button>
          )}
          {status === "scanning" && (
            <button
              className="ghost danger"
              disabled={busy}
              onClick={() => {
                if (!window.confirm("배치를 취소합니다. 진행 중인 스캔도 취소됩니다.")) return;
                act(() => operatorApi.scan.cancel(batchId), "배치를 취소했습니다.");
              }}
            >
              배치 취소
            </button>
          )}
        </div>
      </div>

      <div className="batch-meta">
        {batchStorage && (
          <div className="batch-route">
            <span className="route-end">
              <span className="route-eyebrow">스토리지</span>
              <code>{batchStorage}</code>
              {roots[batchStorage] && <span className="route-path">{roots[batchStorage]}</span>}
            </span>
          </div>
        )}
        <SpecGrid
          items={[
            { label: "requester", value: batch.requester_id, mono: true },
            { label: "우선순위", value: batch.priority ?? "Low" },
            {
              label: "병렬 노드",
              value: batch.node_count != null ? String(batch.node_count) : "자동",
            },
            ...(batch.note ? [{ label: "메모", value: batch.note, span: true } as KV] : []),
          ]}
        />
      </div>

      {rt && (counts.succeeded ?? 0) > 0 && <ScanResults totals={rt} jobs={jobs} />}

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
          <span className="muted small mono">{batchStorage}:</span>
          <input
            className="add-input"
            placeholder="스캔 경로 (예: projects/teamA)"
            value={addPath}
            onChange={(e) => setAddPath(e.target.value)}
          />
          <button className="primary mini" disabled={busy} onClick={addRow}>
            추가
          </button>
          <button
            className="ghost mini"
            onClick={() => {
              setShowAdd(false);
              setAddPath("");
            }}
          >
            취소
          </button>
        </div>
      )}
      {selected.size > 0 && (
        <div className="bulk-bar">
          <span className="bulk-count">{selected.size}개 선택</span>
          {canRunSel && (
            <button
              className="primary mini"
              disabled={busy || selRunnable.length === 0}
              onClick={bulkRun}
              title="선택한 등록됨 항목만 스캔 (나머지는 보류)"
            >
              선택 실행 ({selRunnable.length})
            </button>
          )}
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

      <table className="grid scan-grid">
        <thead>
          <tr>
            <th className="col-toggle"></th>
            <th className="col-check">
              <label className="check-cell">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  disabled={jobs.length === 0}
                  aria-label="전체 선택"
                />
              </label>
            </th>
            <th>경로 (path)</th>
            <th>상태</th>
            <th>결과 (파일 · 크기)</th>
            <th title="atime 데이터 온도 — hot(최근 접근) → cold(오래 미접근)">온도 (atime)</th>
            <th>비고</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 ? (
            <tr>
              <td colSpan={cols} className="muted">
                {stateFilter ? "해당 상태의 요청이 없습니다." : "요청이 없습니다."}
              </td>
            </tr>
          ) : (
            jobs.map((j) => {
              const s = requestState(j.state);
              const cancellable = INFLIGHT_STATES.includes(j.state); // in-flight only
              return (
                <Fragment key={j.id}>
                  <tr
                    className={
                      hasDetail(j)
                        ? `expandable-row${expanded.has(j.id) ? " row-open" : ""}`
                        : undefined
                    }
                    onClick={hasDetail(j) ? () => toggleExpand(j.id) : undefined}
                    tabIndex={hasDetail(j) ? 0 : undefined}
                    aria-expanded={hasDetail(j) ? expanded.has(j.id) : undefined}
                    onKeyDown={
                      hasDetail(j)
                        ? (e) => {
                            if (e.key === "Enter" || e.key === " ") {
                              e.preventDefault();
                              toggleExpand(j.id);
                            }
                          }
                        : undefined
                    }
                  >
                    <td className="col-toggle">
                      {hasDetail(j) && (
                        <span
                          className={`expand-toggle${expanded.has(j.id) ? " open" : ""}`}
                          aria-hidden="true"
                        >
                          ▸
                        </span>
                      )}
                    </td>
                    <td className="col-check" onClick={(e) => e.stopPropagation()}>
                      <label className="check-cell">
                        <input
                          type="checkbox"
                          checked={selected.has(j.id)}
                          onChange={() => toggleSelect(j.id)}
                          aria-label="항목 선택"
                        />
                      </label>
                    </td>
                    <td data-label="경로" className="mono small col-path">
                      {j.path}
                    </td>
                    <td data-label="상태">
                      <span className={`san ${s.cls}`}>{s.label}</span>
                    </td>
                    <td data-label="결과" className="muted small">
                      {j.result
                        ? `${(j.result.file_count ?? 0).toLocaleString()} · ${fmtBytes(j.result.total_bytes)}`
                        : "—"}
                    </td>
                    <td data-label="온도" className="col-hist">
                      <ScanHistBar hist={j.result?.atime_histogram} />
                    </td>
                    <td data-label="비고" className="small">
                      {j.error ? (
                        <span className="err-num" title={j.error}>
                          {truncate(friendlyError(j.error) || j.error, 30)}
                        </span>
                      ) : (
                        <span className="muted">{j.result?.tool || "—"}</span>
                      )}
                    </td>
                    <td className="row-actions" onClick={(e) => e.stopPropagation()}>
                      {mutable && EDITABLE.includes(j.state) && (
                        <button className="mini" onClick={() => setEditingReq(j)} disabled={busy}>
                          편집
                        </button>
                      )}
                      {mutable && RESETTABLE.includes(j.state) && (
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
                    </td>
                  </tr>
                  {expanded.has(j.id) && hasDetail(j) && (
                    <tr className="detail-row">
                      <td colSpan={cols}>
                        <RequestDetail j={j} roots={roots} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })
          )}
        </tbody>
      </table>
      {hasMore && (
        <button className="ghost" onClick={() => loadJobs(false)} disabled={busy}>
          더 보기
        </button>
      )}

      {showEdit && (
        <ScanBatchForm
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
        <ScanRequestEdit
          batchId={batchId}
          request={editingReq}
          onClose={() => setEditingReq(null)}
          onSaved={() => {
            setEditingReq(null);
            setNotice("스캔 경로를 수정했습니다.");
            reload();
          }}
        />
      )}
      {csvModal && (
        <ScanCsvModal
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
