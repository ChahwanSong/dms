import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type BackupBatch, type BackupPreview, type BackupRequest } from "../../../api";
import { batchStatus, requestState, fmtBytes } from "./helpers";
import { errMsg } from "./BackupBatches";
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

function previewDetail(p: BackupPreview): string {
  const parts: string[] = [];
  if (p.files != null) parts.push(`파일 ${p.files.toLocaleString()}`);
  if (p.dirs != null) parts.push(`디렉터리 ${p.dirs.toLocaleString()}`);
  if (p.bytes != null) parts.push(`크기 ${fmtBytes(p.bytes)}`);
  if (p.errors != null) parts.push(`에러 ${p.errors}`);
  if (p.tool) parts.push(`도구 ${p.tool}`);
  return parts.join(" · ") || "상세 없음";
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
  const [focusReq, setFocusReq] = useState(false);
  const [editingReq, setEditingReq] = useState<BackupRequest | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

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

  async function removeRequest(j: BackupRequest) {
    if (
      !window.confirm(
        `요청을 삭제합니다.\n${j.src_storage}:${j.src_path} → ${j.dst_storage}:${j.dst_path}`,
      )
    )
      return;
    await act(() => operatorApi.backup.deleteRequest(batchId, j.id), "요청을 삭제했습니다.");
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
  const deleteConfirm = "--delete 배치입니다. 승인 항목은 dst에서 src에 없는 파일을 삭제합니다. 실행할까요?";

  // selective approval is available while a batch is previewed or running and
  // still has undecided preview_ready requests.
  const canSelect = (status === "previewed" || status === "running") && (counts.preview_ready ?? 0) > 0;
  const selectedReady = jobs.filter((j) => j.state === "preview_ready" && selected.has(j.id));
  const cols = 5 + (canSelect ? 1 : 0) + 1;

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
              <button
                className="ghost"
                disabled={busy}
                onClick={() => {
                  setFocusReq(false);
                  setShowEdit(true);
                }}
              >
                ✎ 편집
              </button>
              <button
                className="ghost"
                disabled={busy}
                onClick={() => {
                  setFocusReq(true);
                  setShowEdit(true);
                }}
              >
                + 요청
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

      <p className="muted small">
        requester=<code>{batch.requester_id}</code> · owner=원본 소유권 보존 ·{" "}
        {batch.delete_enabled ? <strong className="err-num">--delete 켜짐</strong> : "--delete 꺼짐"}
        {batch.note ? ` · ${batch.note}` : ""}
      </p>

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
                  <tr>
                    {canSelect && (
                      <td>
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
                      {j.src_storage}:{j.src_path}
                    </td>
                    <td data-label="대상" className="mono small">
                      {j.dst_storage}:{j.dst_path}
                    </td>
                    <td data-label="상태">
                      <span className={`san ${s.cls}`}>{s.label}</span>
                    </td>
                    <td data-label="미리보기" className="muted small">
                      {j.preview
                        ? `${(j.preview.files ?? 0).toLocaleString()} · ${fmtBytes(j.preview.bytes)}`
                        : "—"}
                    </td>
                    <td data-label="비고" className="muted small">
                      {j.error || (j.dms_job_id ? j.dms_job_id.slice(0, 14) + "…" : "—")}
                    </td>
                    <td className="row-actions">
                      {status === "draft" ? (
                        <>
                          <button className="mini" onClick={() => setEditingReq(j)}>
                            수정
                          </button>
                          <button className="mini danger" onClick={() => removeRequest(j)} disabled={busy}>
                            삭제
                          </button>
                        </>
                      ) : (
                        <>
                          {j.preview && (
                            <button className="mini" onClick={() => toggleExpand(j.id)}>
                              {expanded.has(j.id) ? "접기" : "상세"}
                            </button>
                          )}
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
                  {expanded.has(j.id) && j.preview && (
                    <tr className="detail-row">
                      <td colSpan={cols} className="muted small">
                        {previewDetail(j.preview)}
                        {j.dms_job_id ? ` · job ${j.dms_job_id}` : ""}
                        {j.error ? ` · ${j.error}` : ""}
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
          focusRequests={focusReq}
          onClose={() => setShowEdit(false)}
          onSaved={({ added }) => {
            setShowEdit(false);
            setNotice(added > 0 ? `저장됨 (요청 ${added}개 추가).` : "저장됨.");
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
