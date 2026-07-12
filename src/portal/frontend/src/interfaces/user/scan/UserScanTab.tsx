import { Fragment, useEffect, useMemo, useState } from "react";
import {
  userScanApi,
  userSyncApi,
  type AtimeBucket,
  type ScanResult,
  type UserScanItem,
  type UserStorage,
} from "../../../api";
import { SpecGrid, type KV } from "../../../components/SpecGrid";
import { ActivityCumulativeChart, ActivityStatsStrip } from "../../../components/AtimeActivity";
import { errMsg, fmtAgo, fmtBytes, fmtTime } from "../sync/helpers";
import { ScanHistBar, ScanHistFull } from "./ScanHist";

// --- status + temperature helpers ------------------------------------------

const STATUS_META: Record<string, { label: string; tone: string }> = {
  succeeded: { label: "성공", tone: "is-ok" },
  failed: { label: "실패", tone: "is-err" },
  running: { label: "스캔 중", tone: "is-info" },
  none: { label: "데이터 없음", tone: "is-neutral" },
};
const statusMeta = (s?: string) => STATUS_META[s || "none"] ?? STATUS_META.none;
const STATUS_RANK: Record<string, number> = { succeeded: 0, failed: 1, running: 2, none: 3 };

// numeric coercion for sorting — DMS result counts can arrive as numeric STRINGS,
// which would otherwise sort lexicographically ("9" > "100"). null when absent/NaN.
function numOrNull(x: unknown): number | null {
  if (x == null) return null;
  const v = typeof x === "number" ? x : Number(x);
  return Number.isFinite(v) ? v : null;
}

// 액티비티 점수: 용량-가중 평균 버킷 인덱스 (0 hot … 8 cold). 낮을수록 활발함(최근 접근).
function tempScore(hist?: AtimeBucket[] | null): number | null {
  if (!hist || hist.length === 0) return null;
  let sum = 0;
  let wsum = 0;
  hist.forEach((b, i) => {
    const by = b.bytes ?? 0;
    sum += by;
    wsum += i * by;
  });
  return sum > 0 ? wsum / sum : null;
}

// 운영자 스캔 결과 항목과 동일한 필드 구성 (집계 + 파생 지표 + scan_root/도구).
function resultKvs(it: UserScanItem, r: ScanResult): KV[] {
  const files = r.file_count ?? 0;
  const dirs = r.directory_count ?? 0;
  const bytes = r.total_bytes ?? 0;
  const avg = files > 0 ? bytes / files : null;
  const fpd = dirs > 0 ? files / dirs : null;
  const kv: KV[] = [
    { label: "경로", value: `${it.storage}:${it.path}`, mono: true, span: true },
  ];
  if (r.file_count != null) kv.push({ label: "파일", value: r.file_count.toLocaleString() });
  if (r.directory_count != null)
    kv.push({ label: "디렉터리", value: r.directory_count.toLocaleString() });
  if (r.total_bytes != null) kv.push({ label: "총 용량", value: fmtBytes(r.total_bytes) });
  if (avg != null) kv.push({ label: "평균 파일 크기", value: fmtBytes(Math.round(avg)) });
  if (fpd != null)
    kv.push({
      label: "디렉터리당 파일",
      value: fpd.toLocaleString(undefined, { maximumFractionDigits: 1 }),
    });
  if (r.error_count != null)
    kv.push({
      label: "오류",
      value: r.error_count.toLocaleString(),
      tone: r.error_count ? "tone-danger-text" : "",
    });
  if (r.tool) kv.push({ label: "도구", value: r.tool });
  if (r.scan_root) kv.push({ label: "scan_root", value: r.scan_root, mono: true, span: true });
  return kv;
}

function idKvs(it: UserScanItem): KV[] {
  const kv: KV[] = [];
  if (it.dms_job_id) kv.push({ label: "job id", value: it.dms_job_id, mono: true, span: true });
  if (it.scanned_at) kv.push({ label: "스캔 시각", value: fmtTime(it.scanned_at) });
  return kv;
}

// nulls-last comparator (missing값은 방향과 무관하게 항상 뒤로).
function cmp(a: number | string | null, b: number | string | null, dir: "asc" | "desc"): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1;
  if (b == null) return -1;
  const r = typeof a === "number" && typeof b === "number" ? a - b : String(a).localeCompare(String(b));
  return dir === "asc" ? r : -r;
}

type SortKey = "path" | "status" | "files" | "bytes" | "temp" | "scanned";

// --- component --------------------------------------------------------------

export default function UserScanTab() {
  const [items, setItems] = useState<UserScanItem[]>([]);
  const [storages, setStorages] = useState<UserStorage[]>([]);
  const [storage, setStorage] = useState("");
  const [path, setPath] = useState("");
  const [memo, setMemo] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("scanned");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("desc");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  function reload() {
    setLoading(true);
    userScanApi
      .list()
      .then((r) => setItems(r.items))
      .catch((e) => setError(errMsg(e)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    reload();
    userSyncApi.storages().then(setStorages).catch(() => {});
  }, []);

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const toggleSelect = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  function setSort(k: SortKey) {
    if (k === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(k);
      setSortDir(k === "path" || k === "status" ? "asc" : "desc");
    }
  }

  async function register() {
    setError(null);
    setOk(null);
    if (!storage) return setError("스토리지를 선택하세요.");
    if (!path.trim()) return setError("스캔 경로를 입력하세요.");
    setBusy(true);
    try {
      await userScanApi.create({ storage, path: path.trim(), memo: memo.trim() || null });
      setOk("항목이 등록되었습니다.");
      setPath("");
      setMemo("");
      reload();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(false);
    }
  }

  async function refreshOne(id: number) {
    setBusyId(id);
    try {
      const fresh = await userScanApi.get(id);
      setItems((prev) => prev.map((it) => (it.id === id ? fresh : it)));
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(id: number) {
    if (!window.confirm("이 항목을 삭제할까요?")) return;
    setBusyId(id);
    try {
      await userScanApi.remove(id);
      setItems((prev) => prev.filter((it) => it.id !== id));
      setSelected((prev) => {
        const n = new Set(prev);
        n.delete(id);
        return n;
      });
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusyId(null);
    }
  }

  // filter (검색) + sort (정렬)
  const view = useMemo(() => {
    const q = search.trim().toLowerCase();
    let list = items;
    if (q)
      list = list.filter(
        (it) =>
          `${it.storage}:${it.path}`.toLowerCase().includes(q) ||
          (it.memo ?? "").toLowerCase().includes(q),
      );
    const val = (it: UserScanItem): number | string | null => {
      switch (sortKey) {
        case "path":
          return `${it.storage}:${it.path}`;
        case "status":
          return STATUS_RANK[it.status || "none"] ?? 3;
        case "files":
          return numOrNull(it.result?.file_count);
        case "bytes":
          return numOrNull(it.result?.total_bytes);
        case "temp":
          return tempScore(it.result?.atime_histogram);
        case "scanned": {
          const t = it.scanned_at ? Date.parse(it.scanned_at) : NaN;
          return Number.isNaN(t) ? null : t;
        }
      }
    };
    return [...list].sort((a, b) => cmp(val(a), val(b), sortDir));
  }, [items, search, sortKey, sortDir]);

  const selectedInView = view.filter((it) => selected.has(it.id));
  const allSelected = view.length > 0 && selectedInView.length === view.length;

  function toggleAll() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (allSelected) view.forEach((it) => next.delete(it.id));
      else view.forEach((it) => next.add(it.id));
      return next;
    });
  }

  async function deleteSelected() {
    const ids = view.filter((it) => selected.has(it.id)).map((it) => it.id);
    if (ids.length === 0) return;
    if (!window.confirm(`선택한 ${ids.length}개 항목을 삭제할까요?`)) return;
    setBulkBusy(true);
    setError(null);
    const results = await Promise.allSettled(ids.map((id) => userScanApi.remove(id)));
    const okIds = ids.filter((_, i) => results[i].status === "fulfilled");
    setItems((prev) => prev.filter((it) => !okIds.includes(it.id)));
    // clear ONLY the successfully-deleted ids from selection (keep failed ones
    // selected for retry; don't drop selections hidden by the current search).
    setSelected((prev) => new Set([...prev].filter((id) => !okIds.includes(id))));
    const failed = ids.length - okIds.length;
    if (failed > 0) setError(`${failed}개 항목 삭제에 실패했습니다.`);
    setBulkBusy(false);
  }

  function Th({ k, label, className }: { k: SortKey; label: string; className?: string }) {
    const active = sortKey === k;
    return (
      <th
        className={className}
        aria-sort={active ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
      >
        <button
          type="button"
          className={"sort-th" + (active ? " active" : "")}
          onClick={() => setSort(k)}
        >
          {label}
          <span className="sort-ind">{active ? (sortDir === "asc" ? "▲" : "▼") : "↕"}</span>
        </button>
      </th>
    );
  }

  const COLS = 8;

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>데이터 스캔</h2>
      </div>

      <section className="ui-card">
        <div className="ui-card-hd">
          <h3>
            스캔 항목 등록{" "}
            <span className="muted small">
              운영자가 실행한 스캔 결과를 항목별로 조회 · 실제 스캔은 운영자가 배치로 실행
            </span>
          </h3>
        </div>
        <div className="ui-card-bd">
          <div className="ui-card-div" />
          <div className="form">
            <div className="storage-row">
              <label>
                <span>스토리지 *</span>
                <select value={storage} onChange={(e) => setStorage(e.target.value)}>
                  <option value="">선택…</option>
                  {storages.map((m) => (
                    <option key={m.storage_name} value={m.storage_name}>
                      {m.storage_name} ({m.filesystem_subtype === "pv" ? "PVC" : "FS"})
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span>스캔 경로 (path) *</span>
                <input
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  placeholder="예: projects/teamA"
                />
              </label>
            </div>
            <label>
              <span>메모 (선택)</span>
              <input value={memo} onChange={(e) => setMemo(e.target.value)} placeholder="예: 팀A 데이터 현황" />
            </label>
            {error && <div className="banner err">{error}</div>}
            {ok && <div className="banner ok">{ok}</div>}
            <div className="modal-actions">
              <button className="primary" onClick={register} disabled={busy}>
                {busy ? "등록 중…" : "항목 등록"}
              </button>
            </div>
          </div>
        </div>
      </section>

      <section className="ui-card">
        <div className="ui-card-hd">
          <h3>
            내 스캔 항목
            <span className="hd-cnt">{items.length}</span>
          </h3>
          <div className="scan-toolbar">
            <input
              className="scan-search"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="경로 · 메모 검색…"
            />
            {selectedInView.length > 0 && (
              <button className="mini danger" onClick={deleteSelected} disabled={bulkBusy}>
                {bulkBusy ? "삭제 중…" : `선택 삭제 (${selectedInView.length})`}
              </button>
            )}
            <button className="ghost mini" onClick={reload} disabled={loading}>
              새로고침
            </button>
          </div>
        </div>
        <div className="ui-card-bd">
          <div className="ui-card-div" />
          {items.length === 0 && !loading ? (
            <p className="muted">등록된 스캔 항목이 없습니다.</p>
          ) : view.length === 0 ? (
            <p className="muted">검색 결과가 없습니다.</p>
          ) : (
            <table className="grid uscan-grid">
              <thead>
                <tr>
                  <th className="scan-sel">
                    <input
                      type="checkbox"
                      checked={allSelected}
                      onChange={toggleAll}
                      aria-label="전체 선택"
                    />
                  </th>
                  <Th k="path" label="경로" />
                  <Th k="status" label="상태" />
                  <Th k="files" label="파일" className="num" />
                  <Th k="bytes" label="용량" className="num" />
                  <Th k="temp" label="액티비티" />
                  <Th k="scanned" label="최근 수행" />
                  <th className="scan-act-h">조치</th>
                </tr>
              </thead>
              <tbody>
                {view.map((it) => {
                  const open = expanded.has(it.id);
                  const r = it.result;
                  const sm = statusMeta(it.status);
                  // expandable when there's a result to show OR a failure to explain
                  // (a running/failed re-scan can still carry a prior good result).
                  const canExpand = it.has_result || it.status === "failed";
                  return (
                    <Fragment key={it.id}>
                      <tr
                        className={canExpand ? "scan-row expandable" : "scan-row"}
                        onClick={() => canExpand && toggle(it.id)}
                      >
                        <td
                          className="scan-sel"
                          data-label="선택"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={selected.has(it.id)}
                            onChange={() => toggleSelect(it.id)}
                            aria-label="항목 선택"
                          />
                        </td>
                        <td data-label="경로" className="scan-path-cell">
                          {canExpand && (
                            <span className="scan-caret" aria-hidden>
                              {open ? "▾" : "▸"}
                            </span>
                          )}
                          <code className="mono small">
                            {it.path}
                            <span className="muted"> · {it.storage}</span>
                          </code>
                          {it.memo && <span className="muted small scan-memo"> · {it.memo}</span>}
                        </td>
                        <td data-label="상태">
                          <span className={`reqa-badge ${sm.tone}`} title={it.error || undefined}>
                            {sm.label}
                          </span>
                        </td>
                        <td data-label="파일" className="num">
                          {r?.file_count != null ? r.file_count.toLocaleString() : "—"}
                        </td>
                        <td data-label="용량" className="num">
                          {r?.total_bytes != null ? fmtBytes(r.total_bytes) : "—"}
                        </td>
                        <td data-label="액티비티" className="scan-temp-cell">
                          <ScanHistBar hist={r?.atime_histogram} />
                        </td>
                        <td data-label="최근 수행" className="muted small" title={it.scanned_at ? fmtTime(it.scanned_at) : undefined}>
                          {it.scanned_at ? fmtAgo(it.scanned_at) : "—"}
                        </td>
                        <td
                          data-label="조치"
                          className="scan-act"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <button
                            className="ghost mini"
                            disabled={busyId === it.id}
                            onClick={() => refreshOne(it.id)}
                          >
                            새로고침
                          </button>
                          <button
                            className="mini danger"
                            disabled={busyId === it.id}
                            onClick={() => remove(it.id)}
                          >
                            삭제
                          </button>
                        </td>
                      </tr>
                      {open && canExpand && (
                        <tr className="scan-detail-row">
                          <td colSpan={COLS}>
                            <div className="scan-item-detail">
                              {it.status === "failed" && (
                                <div className="req-error">
                                  최근 스캔 실패: {it.error || "알 수 없는 사유"}
                                  {r && (
                                    <span className="muted small"> · 아래 결과는 직전 성공 스캔 기준</span>
                                  )}
                                </div>
                              )}
                              {r ? (
                                <>
                                  <section className="req-sec">
                                    <h4>스캔 결과</h4>
                                    <SpecGrid items={resultKvs(it, r)} />
                                  </section>
                                  {r.atime_histogram && r.atime_histogram.length > 0 && (
                                    <section className="req-sec">
                                      <h4>데이터 액티비티 · 용량 (hot → cold)</h4>
                                      <div className="activity-viz">
                                        <div>
                                          <div className="activity-col-sub muted small">
                                            접근 경과 구간별 용량
                                          </div>
                                          <ScanHistFull hist={r.atime_histogram} />
                                        </div>
                                        <div>
                                          <div className="activity-col-sub muted small">
                                            누적 용량 (전체 대비)
                                          </div>
                                          <ActivityCumulativeChart hist={r.atime_histogram} />
                                        </div>
                                      </div>
                                      <ActivityStatsStrip hist={r.atime_histogram} />
                                    </section>
                                  )}
                                  {idKvs(it).length > 0 && (
                                    <section className="req-sec">
                                      <h4>식별자</h4>
                                      <SpecGrid items={idKvs(it)} />
                                    </section>
                                  )}
                                </>
                              ) : (
                                idKvs(it).length > 0 && <SpecGrid items={idKvs(it)} />
                              )}
                            </div>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          )}
          <p className="muted small scan-foot-hint">
            결과 없는 항목은 운영자에게 해당 경로의 스캔을 요청하세요. 운영자가 배치 스캔을 실행하면 다음
            새로고침에 자동 반영됩니다.
          </p>
        </div>
      </section>
    </div>
  );
}
