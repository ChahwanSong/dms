import { useEffect, useMemo, useState } from "react";
import { ApiError, operatorApi, type StorageMapping, type FocusTarget } from "../../../api";
import AgentRollout from "./AgentRollout";
import { SanityBadge, ReadinessDots } from "../components/SanityBadge";
import StorageMappingDetail from "./StorageMappingDetail";
import StorageMappingForm from "./StorageMappingForm";
import { backendType, isForPv, isFsBackend, managedRoot, quotaTitle } from "./helpers";
import { fmtTime } from "../../../lib/format";
import Loading from "../../../components/Loading";

type FormState =
  | { mode: "create" }
  | { mode: "edit"; mapping: StorageMapping }
  | null;

// Sort key -> value extractor (string compare, numeric-aware).
const SORT_GETTERS: Record<string, (m: StorageMapping) => string> = {
  storage_name: (m) => m.storage_name,
  cluster_name: (m) => m.cluster_name ?? "",
  backend: (m) => backendType(m),
  storage_class_name: (m) => m.storage_class_name ?? "",
  sanity_status: (m) => m.sanity_status ?? "",
  updated_at: (m) => m.updated_at ?? "",
};

const SANITY_OPTIONS = ["Ready", "Degraded", "Unknown", "Failed"];

export default function StorageInventory({ focus }: {
  focus?: FocusTarget | null;
} = {}) {
  const [mappings, setMappings] = useState<StorageMapping[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cluster, setCluster] = useState<string>("");
  // Stable, global cluster option set — derived only from the UNFILTERED load so
  // selecting a cluster doesn't collapse the dropdown to that single option.
  const [allClusters, setAllClusters] = useState<string[]>([]);
  const [detailName, setDetailName] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [batchBusy, setBatchBusy] = useState(false);
  const [sanityFilter, setSanityFilter] = useState<string>("");
  const [sortKey, setSortKey] = useState<string>("storage_name");
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [search, setSearch] = useState<string>("");

  // deep-link from 조치 필요 "상세": open the referenced mapping's detail + filter to it.
  useEffect(() => {
    if (focus?.kind === "storage" && focus.value) {
      setDetailName(focus.value);
      setSearch(focus.value);
    }
  }, [focus]);

  function toggleSort(key: string) {
    if (sortKey === key) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else {
      setSortKey(key);
      setSortDir("asc");
    }
  }

  // Apply name search + sanity filter, then sort. Derived view (does not refetch).
  const view = useMemo(() => {
    const get = SORT_GETTERS[sortKey] ?? SORT_GETTERS.storage_name;
    const q = search.trim().toLowerCase();
    return mappings
      .filter((m) => !q || m.storage_name.toLowerCase().includes(q))
      .filter((m) => !sanityFilter || m.sanity_status === sanityFilter)
      .slice()
      .sort((a, b) => {
        const cmp = get(a).localeCompare(get(b), undefined, { numeric: true });
        return sortDir === "asc" ? cmp : -cmp;
      });
  }, [mappings, search, sanityFilter, sortKey, sortDir]);

  // Sanity-status breakdown of the loaded (cluster-scoped) mappings, for the top summary.
  const summary = useMemo(() => {
    const by: Record<string, number> = { Ready: 0, Degraded: 0, Unknown: 0, Failed: 0 };
    for (const m of mappings) {
      const s = m.sanity_status || "Unknown";
      by[s] = (by[s] ?? 0) + 1;
    }
    return { total: mappings.length, by };
  }, [mappings]);

  const sortHeader = (key: string, label: string) => (
    <th>
      <button
        type="button"
        className={"sort-th" + (sortKey === key ? " active" : "")}
        onClick={() => toggleSort(key)}
      >
        {label}
        <span className="sort-ind">
          {sortKey === key ? (sortDir === "asc" ? "▲" : "▼") : "↕"}
        </span>
      </button>
    </th>
  );

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const data = await operatorApi.storage.list(cluster || undefined);
      setMappings(data);
      if (cluster === "") {
        const set = new Set<string>();
        data.forEach((m) => m.cluster_name && set.add(m.cluster_name));
        setAllClusters(Array.from(set).sort());
      }
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cluster]);

  async function recheck(name: string) {
    setBusy(name);
    setNotice(null);
    setError(null);
    try {
      const res = await operatorApi.storage.check(name);
      setNotice(`'${name}' 재검사 완료 → ${res.status}`);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  // Re-run sanity on ALL storage mappings (across all clusters, regardless of the
  // current cluster/sanity filter). Sequential so DMS isn't hammered; one failure
  // doesn't abort the batch.
  async function recheckAll() {
    if (!window.confirm("모든 storage_mapping의 sanity를 일괄 재검사합니다. 계속할까요?"))
      return;
    setBatchBusy(true);
    setNotice(null);
    setError(null);
    let items: StorageMapping[];
    try {
      items = await operatorApi.storage.list();
    } catch (e) {
      setError(errMsg(e));
      setBatchBusy(false);
      return;
    }
    let ok = 0;
    const failed: string[] = [];
    for (let i = 0; i < items.length; i++) {
      const name = items[i].storage_name;
      setNotice(`전체 재검사 중… (${i + 1}/${items.length}) ${name}`);
      try {
        await operatorApi.storage.check(name);
        ok++;
      } catch {
        failed.push(name);
      }
    }
    setBatchBusy(false);
    setNotice(
      `전체 재검사 완료: 성공 ${ok}` +
        (failed.length ? ` · 실패 ${failed.length} (${failed.join(", ")})` : ""),
    );
    await load();
  }

  async function remove(name: string) {
    if (
      !window.confirm(
        `스토리지 매핑 '${name}'을(를) 삭제합니다.\n` +
          `이 작업은 되돌릴 수 없는 영구 삭제이며, 해당 스토리지에 진행 중인 작업이 있으면 거부됩니다(409).\n계속할까요?`,
      )
    )
      return;
    setBusy(name);
    setNotice(null);
    setError(null);
    try {
      await operatorApi.storage.remove(name);
      setNotice(`'${name}' 삭제됨`);
      if (detailName === name) setDetailName(null);
      await load();
    } catch (e) {
      setError(errMsg(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>스토리지 인벤토리</h2>
        <div className="inv-actions">
          <input
            type="search"
            className="inv-search"
            placeholder="이름 검색…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            aria-label="스토리지 이름 검색"
          />
          <select value={cluster} onChange={(e) => setCluster(e.target.value)}>
            <option value="">모든 클러스터</option>
            {allClusters.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <select value={sanityFilter} onChange={(e) => setSanityFilter(e.target.value)}>
            <option value="">모든 sanity</option>
            {SANITY_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <button className="ghost" onClick={() => load()} disabled={loading || batchBusy}>
            새로고침
          </button>
          <button className="ghost" onClick={recheckAll} disabled={loading || batchBusy}>
            {batchBusy ? "재검사 중…" : "전체 재검사"}
          </button>
          <button
            className="primary"
            onClick={() => setForm({ mode: "create" })}
            disabled={batchBusy}
          >
            + 새 매핑
          </button>
        </div>
      </div>

      <AgentRollout />

      {!loading && mappings.length > 0 && (
        <div className="inv-summary">
          <button
            type="button"
            className={"sum-chip total" + (sanityFilter === "" ? " active" : "")}
            onClick={() => setSanityFilter("")}
            title="모든 sanity 보기"
          >
            <span className="sum-n">{summary.total}</span>
            <span className="sum-l">전체</span>
          </button>
          {SANITY_OPTIONS.map((s) => (
            <button
              key={s}
              type="button"
              className={
                `sum-chip san-${s.toLowerCase()}` + (sanityFilter === s ? " active" : "")
              }
              onClick={() => setSanityFilter(sanityFilter === s ? "" : s)}
              title={`${s} 필터`}
            >
              <span className="sum-n">{summary.by[s] ?? 0}</span>
              <span className="sum-l">{s}</span>
            </button>
          ))}
        </div>
      )}

      <p className="muted small">
        DMS의 storage_mapping을 조회·등록·수정·sanity 재검사·삭제합니다. 모든 작업은
        DMS HTTP API를 통해 수행됩니다.
      </p>

      {notice && <div className="banner ok">{notice}</div>}
      {error && <div className="banner err">{error}</div>}

      {loading ? (
        <Loading rows={5} />
      ) : mappings.length === 0 ? (
        <div className="muted">표시할 스토리지 매핑이 없습니다.</div>
      ) : (
        <table className="grid">
          <thead>
            <tr>
              {sortHeader("storage_name", "이름")}
              {sortHeader("cluster_name", "클러스터")}
              {sortHeader("backend", "backend")}
              {sortHeader("storage_class_name", "storage class")}
              {sortHeader("sanity_status", "sanity")}
              <th>readiness</th>
              {sortHeader("updated_at", "갱신")}
              <th></th>
            </tr>
          </thead>
          <tbody>
            {view.length === 0 ? (
              <tr>
                <td colSpan={8} className="muted">
                  필터에 맞는 항목이 없습니다.
                </td>
              </tr>
            ) : (
              view.map((m) => (
              <tr key={m.storage_name}>
                <td data-label="이름">
                  <button className="linklike" onClick={() => setDetailName(m.storage_name)}>
                    {m.storage_name}
                  </button>
                  {managedRoot(m) && <div className="cell-sub mono">{managedRoot(m)}</div>}
                </td>
                <td data-label="클러스터">{m.cluster_name || "—"}</td>
                <td data-label="backend">
                  {backendType(m)}
                  {isForPv(m) && <span className="tag tag-pv cell-tag">PV용</span>}
                </td>
                <td data-label="storage class">{m.storage_class_name || "—"}</td>
                <td data-label="sanity">
                  <SanityBadge status={m.sanity_status} />
                </td>
                <td data-label="readiness">
                  {isFsBackend(m) ? (
                    <ReadinessDots
                      readiness={m.readiness || m.sanity_result?.readiness}
                      showRoles
                    />
                  ) : (
                    <ReadinessDots
                      readiness={m.readiness || m.sanity_result?.readiness}
                      quota={{ title: quotaTitle(m) }}
                    />
                  )}
                </td>
                <td data-label="갱신" className="muted small">{fmtTime(m.updated_at)}</td>
                <td className="row-actions">
                  <button
                    className="mini"
                    onClick={() => recheck(m.storage_name)}
                    disabled={busy === m.storage_name || batchBusy}
                  >
                    재검사
                  </button>
                  <button
                    className="mini"
                    onClick={() => setForm({ mode: "edit", mapping: m })}
                    disabled={batchBusy}
                  >
                    수정
                  </button>
                  <button
                    className="mini danger"
                    onClick={() => remove(m.storage_name)}
                    disabled={busy === m.storage_name || batchBusy}
                  >
                    삭제
                  </button>
                </td>
              </tr>
              ))
            )}
          </tbody>
        </table>
      )}

      {detailName && (
        <StorageMappingDetail
          storageName={detailName}
          onClose={() => setDetailName(null)}
          onRecheck={recheck}
          onEdit={(m) => {
            setDetailName(null);
            setForm({ mode: "edit", mapping: m });
          }}
          onDelete={remove}
        />
      )}

      {form && (
        <StorageMappingForm
          mode={form.mode}
          initial={form.mode === "edit" ? form.mapping : undefined}
          onClose={() => setForm(null)}
          onSaved={(name, status) => {
            setForm(null);
            setNotice(`'${name}' 저장됨 → sanity ${status}`);
            load();
          }}
        />
      )}
    </div>
  );
}

function errMsg(e: unknown): string {
  if (e instanceof ApiError) {
    const base = typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail);
    if (e.status === 409) return `진행 중인 작업이 있어 거부됨 (409): ${base}`;
    if (e.status === 422) return `유효성 오류 (422): ${base}`;
    if (e.status === 503) return `DMS 미연동 (503): ${base}`;
    return `오류 ${e.status}: ${base}`;
  }
  return e instanceof Error ? e.message : "알 수 없는 오류";
}

