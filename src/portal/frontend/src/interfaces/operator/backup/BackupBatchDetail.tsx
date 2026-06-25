import { useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type BackupBatch, type BackupJob } from "../../../api";
import { batchStatus, jobState, fmtBytes } from "./helpers";
import { errMsg } from "./BackupBatches";

const PAGE = 200;
const STATE_ORDER = [
  "registered",
  "preview_pending",
  "preview_ready",
  "preview_failed",
  "running",
  "succeeded",
  "failed",
  "cancelled",
];

export default function BackupBatchDetail({
  batchId,
  onBack,
}: {
  batchId: string;
  onBack: () => void;
}) {
  const [batch, setBatch] = useState<BackupBatch | null>(null);
  const [jobs, setJobs] = useState<BackupJob[]>([]);
  const [stateFilter, setStateFilter] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const offsetRef = useRef(0);

  const loadJobs = useCallback(
    async (reset: boolean) => {
      const offset = reset ? 0 : offsetRef.current;
      const page = await operatorApi.backup.jobs(batchId, {
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
          {(status === "draft" || status === "previewed") && (
            <button
              className="primary"
              disabled={busy}
              onClick={() =>
                act(() => operatorApi.backup.preview(batchId), "미리보기를 시작했습니다.")
              }
            >
              {status === "previewed" ? "다시 미리보기" : "미리보기 시작"}
            </button>
          )}
          {status === "previewed" && (
            <button
              className="primary"
              disabled={busy || !counts.preview_ready}
              onClick={() => {
                if (
                  batch.delete_enabled &&
                  !window.confirm(
                    "--delete 배치입니다. 승인하면 dst에서 src에 없는 파일이 삭제됩니다. 실행할까요?",
                  )
                )
                  return;
                act(() => operatorApi.backup.approve(batchId), "승인 — 실행을 시작합니다.");
              }}
            >
              승인 후 실행
            </button>
          )}
          {(status === "previewing" || status === "running" || status === "previewed") && (
            <button
              className="ghost danger"
              disabled={busy}
              onClick={() => {
                if (!window.confirm("배치를 취소합니다. 진행 중인 잡도 취소됩니다.")) return;
                act(() => operatorApi.backup.cancel(batchId), "배치를 취소했습니다.");
              }}
            >
              취소
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
          const j = jobState(s);
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
            <th>출발 (src)</th>
            <th>대상 (dst)</th>
            <th>상태</th>
            <th>미리보기 (파일 · 크기)</th>
            <th>비고</th>
          </tr>
        </thead>
        <tbody>
          {jobs.length === 0 ? (
            <tr>
              <td colSpan={5} className="muted">
                {stateFilter ? "해당 상태의 잡이 없습니다." : "잡이 없습니다."}
              </td>
            </tr>
          ) : (
            jobs.map((j) => {
              const s = jobState(j.state);
              return (
                <tr key={j.id}>
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
                </tr>
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
    </div>
  );
}
