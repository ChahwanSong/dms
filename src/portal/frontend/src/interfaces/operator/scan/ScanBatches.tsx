import { Fragment, useEffect, useState } from "react";
import { ApiError, operatorApi, type ScanBatch } from "../../../api";
import { batchStatus } from "./helpers";
import { fmtTime } from "../../../lib/format";
import { OptionChips, SpecGrid, optionEntries } from "./ui";
import InfoHint from "../../../components/InfoHint";
import ScanBatchForm from "./ScanBatchForm";
import ScanBatchDetail from "./ScanBatchDetail";
import Loading from "../../../components/Loading";

// Data scan: register lists of DMS DM scan jobs and run them as read-only
// inventory scans (file/dir/byte counts). Scan has NO preview/approve flow — a
// batch goes 등록 -> 실행 -> 완료. This is the list of batches; click one to open
// its detail (jobs, results, run/rescan/cancel).
type SortKey = "name" | "status" | "created";
type SortState = { key: SortKey; dir: "asc" | "desc" };

const ts = (iso?: string | null): number => (iso ? new Date(iso).getTime() : 0);

// Client-side ordering of the (fully-fetched) batch list. name: localeCompare;
// status: by Korean status label; created: by created_at timestamp.
function sortBatches(rows: ScanBatch[], sort: SortState): ScanBatch[] {
  const sign = sort.dir === "asc" ? 1 : -1;
  return rows.slice().sort((a, b) => {
    let r = 0;
    if (sort.key === "name") r = a.name.localeCompare(b.name);
    else if (sort.key === "status")
      r = batchStatus(a.status).label.localeCompare(batchStatus(b.status).label);
    else r = ts(a.created_at) - ts(b.created_at);
    return sign * r;
  });
}

export default function ScanBatches() {
  const [batches, setBatches] = useState<ScanBatch[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<SortState>({ key: "created", dir: "desc" });
  const [busy, setBusy] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      setBatches(await operatorApi.scan.list());
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
  }, []);

  // Keep the selection limited to currently-loaded batches; rows can vanish after
  // a bulk delete / refresh, and stale ids would leave the bulk bar miscounting.
  useEffect(() => {
    setSelected((prev) => {
      if (prev.size === 0) return prev;
      const live = new Set(batches.map((b) => b.id));
      let changed = false;
      const next = new Set<string>();
      for (const id of prev) {
        if (live.has(id)) next.add(id);
        else changed = true;
      }
      return changed ? next : prev;
    });
  }, [batches]);

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  async function remove(b: ScanBatch) {
    if (!window.confirm(`배치 '${b.name}'을(를) 삭제합니다. 계속할까요?`)) return;
    try {
      await operatorApi.scan.remove(b.id);
      setNotice(`'${b.name}' 삭제됨`);
      await load();
    } catch (e) {
      setError(errMsg(e));
    }
  }

  function toggleSelect(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }
  function toggleSelectAll() {
    setSelected((prev) =>
      prev.size === batches.length ? new Set() : new Set(batches.map((b) => b.id)),
    );
  }
  function clearSelection() {
    setSelected(new Set());
  }
  function applySort(key: SortKey) {
    setSort((prev) =>
      prev.key === key ? { key, dir: prev.dir === "asc" ? "desc" : "asc" } : { key, dir: "asc" },
    );
  }

  async function bulkDelete() {
    const ids = [...selected];
    if (!ids.length) return;
    if (!window.confirm(`선택한 ${ids.length}개 배치를 삭제합니다. 진행 중 배치는 건너뜁니다. 계속할까요?`))
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await operatorApi.scan.deleteBatches(ids);
      const skipped = res.skipped.length;
      setNotice(`${res.deleted.length}개 삭제${skipped ? `, ${skipped}개 건너뜀(진행 중)` : ""}`);
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function bulkCancel() {
    const ids = [...selected];
    if (!ids.length) return;
    if (!window.confirm(`선택한 ${ids.length}개 배치를 취소합니다. 진행 중인 스캔도 취소됩니다. 계속할까요?`))
      return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await operatorApi.scan.cancelBatches(ids);
      const skipped = res.skipped.length;
      setNotice(
        `${res.cancelled.length}개 취소 (DMS ${res.dms_cancelled}건)${skipped ? `, ${skipped}개 건너뜀` : ""}`,
      );
      setSelected(new Set());
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  if (openId) {
    return (
      <ScanBatchDetail
        batchId={openId}
        onBack={() => {
          setOpenId(null);
          load();
        }}
      />
    );
  }

  const shown = sortBatches(batches, sort);
  const allSelected = batches.length > 0 && selected.size === batches.length;
  const sortTh = (key: SortKey, label: string) => {
    const active = sort.key === key;
    return (
      <button
        type="button"
        className={"sort-th" + (active ? " active" : "")}
        onClick={() => applySort(key)}
      >
        {label}
        <span className="sort-ind">{active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}</span>
      </button>
    );
  };

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2 className="title-with-hint">
          데이터 스캔
          <InfoHint label="데이터 스캔 설명">
            <strong className="hint-title">데이터 스캔</strong>
            <p className="hint-lead">DMS DM scan으로 스토리지 경로의 파일·디렉터리·용량을 집계합니다.</p>
            <div className="hint-flow">
              <span>등록</span>
              <i className="flow-arrow">→</i>
              <span>실행</span>
              <i className="flow-arrow">→</i>
              <span>완료</span>
            </div>
            <ul className="hint-list">
              <li>
                <b>읽기 전용</b> — 스캔은 데이터를 변경하지 않습니다. Preview·승인 단계가 없습니다.
              </li>
              <li>
                <b>실행</b> — 등록된 경로를 바로 스캔합니다(등록됨 → 실행 중 → 성공).
              </li>
              <li>
                <b>재스캔</b> — 완료된 배치를 다시 실행해 변동을 확인합니다(증가 모니터링).
              </li>
              <li>
                <b>스캔 옵션</b> — summary_only · max_depth · follow_symlinks · one_file_system.
              </li>
            </ul>
          </InfoHint>
        </h2>
        <div className="inv-actions">
          <button className="ghost" onClick={load} disabled={loading}>
            새로고침
          </button>
          <button className="primary" onClick={() => setShowForm(true)}>
            + 새 배치
          </button>
        </div>
      </div>

      {notice && <div className="banner ok">{notice}</div>}
      {error && <div className="banner err">{error}</div>}

      {selected.size > 0 && (
        <div className="bulk-bar">
          <span className="bulk-count">{selected.size}개 선택</span>
          <button className="mini danger" disabled={busy} onClick={bulkCancel}>
            선택 취소
          </button>
          <button className="mini danger" disabled={busy} onClick={bulkDelete}>
            선택 삭제
          </button>
          <button className="ghost mini" onClick={clearSelection}>
            선택 해제
          </button>
        </div>
      )}

      {loading ? (
        <Loading rows={5} />
      ) : batches.length === 0 ? (
        <div className="muted">등록된 스캔 배치가 없습니다. “+ 새 배치”로 시작하세요.</div>
      ) : (
        <table className="grid batch-list">
          <thead>
            <tr>
              <th className="col-toggle"></th>
              <th className="col-check">
                <label className="check-cell">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleSelectAll}
                    disabled={batches.length === 0}
                    aria-label="전체 선택"
                  />
                </label>
              </th>
              <th>{sortTh("name", "이름")}</th>
              <th>{sortTh("status", "상태")}</th>
              <th>성공/실패/취소/요청</th>
              <th>옵션</th>
              <th>{sortTh("created", "생성")}</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((b) => {
              const st = batchStatus(b.status);
              const open = expanded.has(b.id);
              return (
                <Fragment key={b.id}>
                  <tr
                    className={`expandable-row${open ? " row-open" : ""}`}
                    onClick={() => toggle(b.id)}
                    tabIndex={0}
                    aria-expanded={open}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        toggle(b.id);
                      }
                    }}
                  >
                    <td className="col-toggle">
                      <span className={`expand-toggle${open ? " open" : ""}`} aria-hidden="true">
                        ▸
                      </span>
                    </td>
                    <td className="col-check" onClick={(e) => e.stopPropagation()}>
                      <label className="check-cell">
                        <input
                          type="checkbox"
                          checked={selected.has(b.id)}
                          onChange={() => toggleSelect(b.id)}
                          aria-label="배치 선택"
                        />
                      </label>
                    </td>
                    <td data-label="이름" className="col-name">
                      <button
                        className="linklike"
                        onClick={(e) => {
                          e.stopPropagation();
                          setOpenId(b.id);
                        }}
                      >
                        {b.name}
                      </button>
                    </td>
                    <td data-label="상태">
                      <span className={`san ${st.cls}`}>{st.label}</span>
                    </td>
                    <td data-label="성공/실패/취소/요청">
                      <span className="ok-num">{b.succeeded_count ?? 0}</span>
                      <span className="muted"> / </span>
                      <span className="err-num">{b.failed_count ?? 0}</span>
                      <span className="muted"> / </span>
                      <span className="muted">{b.cancelled_count ?? 0}</span>
                      <span className="muted"> / </span>
                      <span>{b.request_count ?? 0}</span>
                    </td>
                    <td data-label="옵션">
                      <OptionChips batch={b} />
                    </td>
                    <td data-label="생성" className="muted small">
                      {fmtTime(b.created_at)}
                    </td>
                    <td className="row-actions" onClick={(e) => e.stopPropagation()}>
                      <button className="mini" onClick={() => setOpenId(b.id)}>
                        열기
                      </button>
                      <button
                        className="mini danger"
                        onClick={() => remove(b)}
                        disabled={b.status === "scanning"}
                      >
                        삭제
                      </button>
                    </td>
                  </tr>
                  {open && (
                    <tr className="detail-row">
                      <td colSpan={8}>
                        <BatchExpand batch={b} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      )}

      {showForm && (
        <ScanBatchForm
          onClose={() => setShowForm(false)}
          onSaved={({ id, added }) => {
            setShowForm(false);
            setNotice(`배치 생성됨 (요청 ${added}개). “스캔 실행”을 시작하세요.`);
            setOpenId(id);
          }}
        />
      )}
    </div>
  );
}

function BatchExpand({ batch }: { batch: ScanBatch }) {
  const opts = optionEntries(batch.options);
  const total = batch.request_count ?? 0;
  const ok = batch.succeeded_count ?? 0;
  const fail = batch.failed_count ?? 0;
  const cancelled = batch.cancelled_count ?? 0;
  const pct = total ? Math.round((ok / total) * 100) : 0;
  return (
    <div className="batch-expand">
      <SpecGrid
        items={[
          { label: "요청 수", value: total.toLocaleString() },
          { label: "우선순위", value: batch.priority ?? "Low" },
          { label: "병렬 노드", value: batch.node_count != null ? String(batch.node_count) : "자동" },
          { label: "requester", value: batch.requester_id, mono: true },
          { label: "생성", value: fmtTime(batch.created_at) },
          { label: "수정", value: fmtTime(batch.updated_at) },
          ...(batch.note ? [{ label: "메모", value: batch.note, span: true }] : []),
        ]}
      />
      {total > 0 && (
        <div className="mini-progress" title={`성공 ${ok} / 실패 ${fail} / 취소 ${cancelled} / 전체 ${total}`}>
          <div className="mini-progress-bar">
            <span className="seg ok" style={{ width: `${pct}%` }} />
            <span className="seg fail" style={{ width: `${total ? (fail / total) * 100 : 0}%` }} />
            <span className="seg cancel" style={{ width: `${total ? (cancelled / total) * 100 : 0}%` }} />
          </div>
          <span className="muted small">
            성공 {ok} · 실패 {fail} · 취소 {cancelled} · 전체 {total}
          </span>
        </div>
      )}
      {opts.length > 0 && (
        <div className="opt-list">
          <span className="opt-list-label">스캔 옵션</span>
          {opts.map((o) => (
            <span className="chip ghost-chip" key={o.k}>
              {o.k} <b>{o.v}</b>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const base = typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
    if (e.status === 503) return `포탈 DB 미연동 (503): ${base}`;
    if (e.status === 409) return `상태 충돌 (409): ${base}`;
    if (e.status === 422) return `유효성 오류 (422): ${base}`;
    return `오류 ${e.status}: ${base}`;
  }
  return e instanceof Error ? e.message : "알 수 없는 오류";
}

// fmtTime is imported from lib/format above; re-exported for scan/* callers.
export { fmtTime };
