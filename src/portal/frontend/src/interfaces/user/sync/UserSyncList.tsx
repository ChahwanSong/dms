import { useCallback, useEffect, useState } from "react";
import { userSyncApi, type SyncJob } from "../../../api";
import UserJobDetail from "./UserJobDetail";
import {
  SYNC_STATE_LABEL,
  SYNC_TERMINAL,
  syncStateTone,
  errMsg,
  fmtAgo,
  fmtBytes,
  fmtTime,
} from "./helpers";

const PAGE = 100;

function target(j: SyncJob): string {
  return `${j.src_storage}:${j.src_path} → ${j.dst_storage}:${j.dst_path}`;
}

// 내 Sync 작업 목록. 본인이 요청한 작업만 표시(BFF가 origin='user' + 사용자로 스코프).
// preview_ready → 승인, 진행 중 → 취소, 종료 → 삭제, 언제나 상세.
export default function UserSyncList({ reloadKey }: { reloadKey: number }) {
  const [rows, setRows] = useState<SyncJob[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [detailJob, setDetailJob] = useState<SyncJob | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    userSyncApi
      .list({ offset: 0, limit: PAGE })
      .then((r) => {
        setRows(r.items);
        setTotal(r.total);
      })
      .catch((e) => {
        setRows([]);
        setError(errMsg(e));
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    reload();
  }, [reload, reloadKey]);

  // poll while any loaded job is non-terminal.
  const anyInflight = rows.some((r) => !SYNC_TERMINAL.has(r.state));
  useEffect(() => {
    if (!anyInflight) return;
    const t = setInterval(() => {
      userSyncApi
        .list({ offset: 0, limit: PAGE })
        .then((r) => {
          setRows(r.items);
          setTotal(r.total);
        })
        .catch(() => {});
    }, 4000);
    return () => clearInterval(t);
  }, [anyInflight]);

  async function act(id: number, fn: () => Promise<unknown>, after: "refresh" | "drop") {
    setBusyId(id);
    setError(null);
    try {
      await fn();
      if (after === "drop") {
        setRows((prev) => prev.filter((r) => r.id !== id));
        setTotal((t) => (t == null ? t : Math.max(0, t - 1)));
      } else {
        const fresh = await userSyncApi.get(id);
        setRows((prev) => prev.map((r) => (r.id === id ? fresh : r)));
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  const approve = (j: SyncJob) => act(j.id, () => userSyncApi.approve(j.id), "refresh");
  const cancel = (j: SyncJob) => {
    if (!window.confirm("이 Sync 작업을 취소할까요?")) return;
    act(j.id, () => userSyncApi.cancel(j.id), "refresh");
  };
  const remove = (j: SyncJob) => {
    if (!window.confirm("이 Sync 작업 기록을 삭제할까요?")) return;
    act(j.id, () => userSyncApi.remove(j.id), "drop");
  };

  const shown = rows.length;

  return (
    <section className="ui-card sync-list">
      <div className="ui-card-hd">
        <h3>
          내 Sync 작업
          <span className="hd-cnt">
            {shown}
            {total != null && total > shown ? ` / ${total}` : ""}
          </span>
        </h3>
      </div>
      <div className="ui-card-bd">
        <div className="ui-card-div" />
        {error && <div className="banner err">{error}</div>}

        <table className="grid sync-grid">
          <thead>
            <tr>
              <th>대상</th>
              <th>상태</th>
              <th>정보</th>
              <th>시각</th>
              <th>조치</th>
            </tr>
          </thead>
          <tbody>
            {shown === 0 && !loading ? (
              <tr>
                <td colSpan={5} className="muted reqa-empty">
                  {error ? "불러오지 못했습니다." : "요청한 Sync 작업이 없습니다."}
                </td>
              </tr>
            ) : (
              rows.map((j) => {
                const busy = busyId === j.id;
                const pv = j.preview;
                return (
                  <tr key={j.id} className={SYNC_TERMINAL.has(j.state) ? "" : "sync-live"}>
                    <td data-label="대상" className="mono small sync-target" title={target(j)}>
                      {target(j)}
                      {j.memo && <span className="muted small sync-memo"> · {j.memo}</span>}
                    </td>
                    <td data-label="상태">
                      <span className={`reqa-badge ${syncStateTone(j.state)}`}>
                        {SYNC_STATE_LABEL[j.state] || j.state}
                      </span>
                    </td>
                    <td data-label="정보" className="small sync-info">
                      {j.state === "preview_ready" && pv ? (
                        <span className="muted">
                          복사 예정 {pv.files != null ? `${pv.files.toLocaleString()}개` : "—"}
                          {pv.bytes != null ? ` · ${fmtBytes(pv.bytes)}` : ""}
                        </span>
                      ) : j.error ? (
                        <span className="err-num" title={j.error}>{j.error}</span>
                      ) : j.delete_enabled ? (
                        <span className="err-num small">--delete</span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </td>
                    <td data-label="시각" className="muted small sync-when" title={fmtTime(j.created_at)}>
                      {fmtAgo(j.created_at)}
                    </td>
                    <td data-label="조치" className="sync-actions">
                      {j.state === "preview_ready" && (
                        <button className="mini go" disabled={busy} onClick={() => approve(j)}>
                          승인
                        </button>
                      )}
                      {!SYNC_TERMINAL.has(j.state) && (
                        <button className="mini" disabled={busy} onClick={() => cancel(j)}>
                          취소
                        </button>
                      )}
                      <button className="ghost mini" disabled={busy} onClick={() => setDetailJob(j)}>
                        상세
                      </button>
                      {SYNC_TERMINAL.has(j.state) && (
                        <button className="mini danger" disabled={busy} onClick={() => remove(j)}>
                          삭제
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {detailJob && (
        <UserJobDetail job={detailJob} onClose={() => setDetailJob(null)} />
      )}
    </section>
  );
}
