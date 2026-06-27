import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type BackupBatch, type BackupRequest } from "../../../api";
import { batchStatus, requestState, fmtBytes } from "./helpers";
import { errMsg, fmtTime } from "./BackupBatches";
import { SpecGrid, type KV } from "./ui";
import BackupBatchForm from "./BackupBatchForm";
import BackupRequestEdit from "./BackupRequestEdit";

const PAGE = 200;
const STATE_ORDER = [
  "registered",
  "preview_pending",
  "preview_ready",
  "approved",
  "preview_failed",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];
const TERMINAL = ["succeeded", "failed", "cancelled", "preview_failed"];
const EDITABLE = ["registered", "preview_ready", "preview_failed", "failed", "cancelled"];
const RETRYABLE = ["preview_failed", "failed"];

const num = (n?: number | null) => (n == null ? "—" : n.toLocaleString());

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
              <h4>미리보기 (dry-run)</h4>
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

      {j.error && <div className="req-error">{j.error}</div>}
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
  const [hasMore, setHasMore] = useState(false);
  const offsetRef = useRef(0);
  const [showEdit, setShowEdit] = useState(false);
  const [editingReq, setEditingReq] = useState<BackupRequest | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  // storage_name -> managed_root, so the UI can show real absolute paths.
  const [roots, setRoots] = useState<Record<string, string>>({});

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
      const page = await operatorApi.backup.requests(batchId, {
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
      const [b] = await Promise.all([operatorApi.backup.get(batchId)]);
      setBatch(b);
      await loadJobs(true);
    } catch (e) {
      setError(errMsg(e));
    }
  }, [batchId, loadJobs]);

  useEffect(() => {
    reload();
  }, [reload]);

  // Live-poll while a batch is actively previewing or running.
  useEffect(() => {
    if (!batch || (batch.status !== "previewing" && batch.status !== "running")) return;
    const t = setInterval(reload, 4000);
    return () => clearInterval(t);
  }, [batch, reload]);

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
        {error ? <div className="banner err">{error}</div> : <div className="muted">불러오는 중…</div>}
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
  const selectedReady = jobs.filter((j) => j.state === "preview_ready" && selected.has(j.id));
  // columns: chevron + (checkbox?) + 출발/대상/상태/미리보기/비고(5) + actions
  const cols = 5 + (canSelect ? 1 : 0) + 2;

  function approveSelected() {
    const ids = selectedReady.map((j) => j.id);
    if (ids.length === 0) return;
    if (batch!.delete_enabled && !window.confirm(deleteConfirm)) return;
    act(async () => {
      await operatorApi.backup.approve(batchId, { request_ids: ids });
      setSelected(new Set());
    }, `${ids.length}개 승인 — 실행을 시작합니다.`);
  }
  function approveAll() {
    if (batch!.delete_enabled && !window.confirm(deleteConfirm)) return;
    act(async () => {
      await operatorApi.backup.approve(batchId);
      setSelected(new Set());
    }, "전체 승인 — 실행을 시작합니다.");
  }
  function closeBatch() {
    if (!window.confirm("배치를 마감합니다. 승인하지 않은 '미리보기 완료' 항목은 제외됩니다.")) return;
    act(() => operatorApi.backup.close(batchId), "배치를 마감했습니다.");
  }
  async function resetOne(j: BackupRequest) {
    await act(
      () => operatorApi.backup.resetRequests(batchId, { request_ids: [j.id] }),
      "재시도 대기로 되돌렸습니다. '재미리보기'를 누르세요.",
    );
  }
  function retryFailed() {
    const n = (counts.preview_failed ?? 0) + (counts.failed ?? 0);
    if (!window.confirm(`실패 항목 ${n}개를 재시도 대기로 되돌립니다. 이후 '재미리보기'를 누르세요.`)) return;
    act(
      () => operatorApi.backup.resetRequests(batchId, { failed_only: true }),
      "실패 항목을 재시도 대기로 되돌렸습니다.",
    );
  }
  const failedCount = (counts.preview_failed ?? 0) + (counts.failed ?? 0);
  const canRepreview = (status === "previewed" || status === "done") && (counts.registered ?? 0) > 0;

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
                  act(() => operatorApi.backup.preview(batchId), "미리보기를 시작했습니다.")
                }
              >
                미리보기 시작
              </button>
            </>
          )}
          {canSelect && (
            <>
              <button className="primary" disabled={busy || selectedReady.length === 0} onClick={approveSelected}>
                선택 승인 ({selectedReady.length})
              </button>
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
                act(() => operatorApi.backup.preview(batchId), "재미리보기를 시작했습니다.")
              }
            >
              재미리보기
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

      <table className="grid">
        <thead>
          <tr>
            <th className="col-toggle"></th>
            {canSelect && <th></th>}
            <th>출발 (src)</th>
            <th>대상 (dst)</th>
            <th>상태</th>
            <th>미리보기 (파일 · 크기)</th>
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
              const cancellable =
                status !== "draft" && j.state !== "registered" && !TERMINAL.includes(j.state);
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
                    {canSelect && (
                      <td onClick={(e) => e.stopPropagation()}>
                        {j.state === "preview_ready" && (
                          <input
                            type="checkbox"
                            checked={selected.has(j.id)}
                            onChange={() => toggleSelect(j.id)}
                            aria-label="승인 선택"
                          />
                        )}
                      </td>
                    )}
                    <td data-label="출발" className="mono small">
                      {j.src_storage === batchSrc ? j.src_path : `${j.src_storage}:${j.src_path}`}
                    </td>
                    <td data-label="대상" className="mono small">
                      {j.dst_storage === batchDst ? j.dst_path : `${j.dst_storage}:${j.dst_path}`}
                    </td>
                    <td data-label="상태">
                      <span className={`san ${s.cls}`}>{s.label}</span>
                    </td>
                    <td data-label="미리보기" className="muted small">
                      {j.preview
                        ? `${(j.preview.files ?? 0).toLocaleString()} · ${fmtBytes(j.preview.bytes)}`
                        : "—"}
                    </td>
                    <td data-label="비고" className="small">
                      {j.error ? (
                        <span className="err-num">
                          {j.error.length > 40 ? j.error.slice(0, 40) + "…" : j.error}
                        </span>
                      ) : (
                        <span className="muted">
                          {j.result?.summary?.selected_tool || j.preview?.tool || "—"}
                        </span>
                      )}
                    </td>
                    <td className="row-actions" onClick={(e) => e.stopPropagation()}>
                      {status !== "draft" && (
                        <>
                          {EDITABLE.includes(j.state) && (
                            <button className="mini" onClick={() => setEditingReq(j)}>
                              재편집
                            </button>
                          )}
                          {RETRYABLE.includes(j.state) && (
                            <button className="mini" onClick={() => resetOne(j)} disabled={busy}>
                              재시도
                            </button>
                          )}
                          {cancellable && (
                            <button className="mini danger" onClick={() => cancelRow(j)} disabled={busy}>
                              취소
                            </button>
                          )}
                        </>
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
    </div>
  );
}
