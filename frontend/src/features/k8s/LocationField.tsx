import type { ReactNode } from "react";
import { Segmented } from "../../components/ui/Segmented";
import { field } from "../jobs/formFields";
import { useSearchField } from "../jobs/searchField";
import { Ago, FreshnessLine, Mono } from "./parts";
import { useK8sVolumes } from "./useK8s";
import { SEARCH_LIMIT } from "./usePvCatalog";
import type { PvCatalog, Resolved } from "./usePvCatalog";
import {
  SIDE_LABELS, bindingNameFor, effectiveGroup, freshnessOf, freshnessWarns, groupKey, normalizeSub, pvAbsPath, pvMetaText, pvOptionText, pvSubProblem, readLastGroup,
  refOf, rowKey, sameRef, writeLastGroup,
} from "./pvLocation";
import type { Loc, LocMode, Side } from "./pvLocation";
import { nsLabel } from "./k8sTerms";
import type { VolumeRow } from "./types";

// 단일 작업 화면의 한 쪽(소스·목적지·대상) 위치 입력(dms-k8s 설계 §7).
//   - 맵핑 범위(일반 사용자·목록 밖 관리자, 면제 관리자의 「권한 기준 사용자」 지정): namespace (StorageClass) → PV 이름 → PV 안 경로
//   - 면제 관리자 + 권한 기준 사용자 비움: PV 검색(상한 50) → PV 안 경로
//   - 면제 관리자는 쪽마다 「PV | 직접 경로」(직접 경로 = 기존 스토리지 + 관리 디렉토리 아래 상대경로 -- rawSlot 으로 받는다)
// 「실제 경로」(서버가 해석한 abs_root + 하위경로)와 「n분 전 기준」(그 클러스터의 마지막 성공 동기화)을 늘 보여 사용자가 어디를
// 건드리는지 확신하게 한다. 판정(권한·경로 규칙)은 서버 몫이고, 여기는 서버 응답을 보일 뿐이다.

const MODE_OPTIONS: readonly { value: LocMode; label: string }[] = [
  { value: "pv", label: "PV" }, { value: "path", label: "직접 경로" },
];

export function LocationField({ side, operation, loc, onChange, catalog, search, resolved, allowRaw, rawSlot }: {
  side: Side;
  operation: string;
  loc: Loc;
  onChange: (next: Loc) => void;
  catalog: PvCatalog;
  search: boolean;
  resolved: Resolved;
  /** 면제 관리자 -- 「PV | 직접 경로」 전환을 보인다. */
  allowRaw: boolean;
  /** 직접 경로 입력(스토리지 피커 + 상대경로 + 즉답) -- 제출 화면의 기존 부품을 그대로 받는다. */
  rawSlot: ReactNode;
}) {
  const L = SIDE_LABELS[side];
  const mode: LocMode = allowRaw ? loc.mode : "pv";
  const row = resolved.status === "found" ? resolved.row : null;
  return (
    <div className="flex min-w-0 flex-col gap-3">
      {allowRaw && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm">{L.title}</span>
          <Segmented label={L.mode} options={MODE_OPTIONS} value={mode}
                     onChange={(m) => onChange({ ...loc, mode: m })} />
        </div>
      )}
      {mode === "path" ? rawSlot : (
        <>
          {search
            ? <SearchPicker side={side} loc={loc} onChange={onChange} row={row} />
            : <GroupPicker side={side} loc={loc} onChange={onChange} catalog={catalog} row={row} />}
          <SubField side={side} operation={operation} loc={loc} onChange={onChange} resolved={resolved} />
        </>
      )}
    </div>
  );
}

function GroupPicker({ side, loc, onChange, catalog, row }: {
  side: Side; loc: Loc; onChange: (next: Loc) => void; catalog: PvCatalog; row: VolumeRow | null;
}) {
  const L = SIDE_LABELS[side];
  const ready = catalog.status === "ready";
  const groups = ready ? catalog.groups : [];
  const eg = effectiveGroup(loc, groups, row, readLastGroup(side));
  const groupRows = groups.find((g) => g.key === eg)?.rows ?? [];
  const chosen = loc.ref !== null ? groupRows.find((r) => sameRef(r, loc.ref)) : undefined;
  const firstText = catalog.status === "loading" ? "불러오는 중…"
    : !ready ? "목록 없음" : groups.length === 0 ? "고를 수 있는 namespace 없음" : "선택하세요";
  return (
    <>
      <label className="text-sm">{L.group}
        <select aria-label={L.group} className={field} value={eg} disabled={!ready || groups.length === 0}
                onChange={(e) => {
                  const key = e.target.value;
                  writeLastGroup(side, key);
                  // 다른 묶음으로 옮기면 고른 PV 를 비운다(그 묶음에 없는 PV 를 쥐고 있지 않게).
                  const keep = loc.ref !== null && row !== null
                    && groupKey(row.cluster, row.namespace, row.storage_class) === key;
                  onChange({ ...loc, group: key, ref: keep ? loc.ref : null });
                }}>
          <option value="">{firstText}</option>
          {groups.map((g) => (
            <option key={g.key} value={g.key} disabled={g.empty}>{g.label}</option>
          ))}
        </select>
      </label>
      <label className="text-sm">{L.pvc}
        <select aria-label={L.pvc} className={field} value={chosen !== undefined ? rowKey(chosen) : ""}
                disabled={eg === ""}
                onChange={(e) => {
                  const r = groupRows.find((x) => rowKey(x) === e.target.value);
                  onChange({ ...loc, group: eg, ref: r === undefined ? null : refOf(r) });
                }}>
          <option value="">{eg === "" ? "namespace 를 먼저 고르세요" : "선택하세요"}</option>
          {groupRows.map((r) => (
            <option key={rowKey(r)} value={rowKey(r)} disabled={!r.selectable}>{pvOptionText(r)}</option>
          ))}
        </select>
      </label>
    </>
  );
}

/** 면제 관리자의 전체 PV 검색(권한 기준 사용자 비움). 결과는 라디오 목록(상한 50 -- 넘으면 좁히라고 말한다). */
function SearchPicker({ side, loc, onChange, row }: {
  side: Side; loc: Loc; onChange: (next: Loc) => void; row: VolumeRow | null;
}) {
  const L = SIDE_LABELS[side];
  const s = useSearchField("");
  const q = useK8sVolumes({ q: s.applied, limit: SEARCH_LIMIT });
  const rows = Array.isArray(q.data?.volumes) ? q.data!.volumes : [];
  return (
    <div className="min-w-0">
      <label className="text-sm block">{L.search}
        <input aria-label={L.search} className={field} placeholder="PV 이름 · namespace · 볼륨 · StorageClass"
               {...s.bind} />
      </label>
      {q.isError && q.data === undefined ? (
        <p className="mt-1 text-sm text-bad">{`PV 를 검색하지 못했습니다: ${(q.error as Error).message}`}</p>
      ) : q.data === undefined ? (
        <p className="mt-1 text-xs text-ink/70">불러오는 중…</p>
      ) : rows.length === 0 ? (
        <p className="mt-1 text-xs text-ink/70">
          {s.applied === "" ? "동기화된 PV 가 없습니다." : `「${s.applied}」와 일치하는 PV 가 없습니다.`}
        </p>
      ) : (
        <div role="radiogroup" aria-label={`${L.title} 검색 결과 목록`}
             className="mt-2 max-h-64 overflow-y-auto rounded-lg border border-line">
          {rows.map((r) => (
            <label key={rowKey(r)}
                   className={`flex min-w-0 items-start gap-2 border-b border-line px-3 py-2 text-sm last:border-b-0 ${
                     r.selectable ? "cursor-pointer" : "opacity-60"}`}>
              <input type="radio" name={`pv-search-${side}`} className="mt-1" disabled={!r.selectable}
                     checked={sameRef(loc.ref, refOf(r))}
                     onChange={() => onChange({ ...loc, group: groupKey(r.cluster, r.namespace, r.storage_class),
                                                ref: refOf(r) })} />
              <span className="min-w-0">
                <span className="block [overflow-wrap:anywhere]">
                  <span className="font-medium">{r.pvc}</span>{` — ${nsLabel(r.namespace, r.storage_class)}`}
                </span>
                <span className="block text-xs text-ink/70 [overflow-wrap:anywhere]">
                  {pvMetaText(r)}{` · ${r.cluster}`}
                  {r.volume !== null && <> · <Mono>{r.volume}</Mono></>}
                </span>
              </span>
            </label>
          ))}
        </div>
      )}
      {q.data?.truncated === true && (
        <p className="mt-1 text-xs text-attn">{`결과가 ${SEARCH_LIMIT}개를 넘어 앞의 ${SEARCH_LIMIT}개만 보입니다 — 검색어를 좁히세요.`}</p>
      )}
      {loc.ref !== null && (
        <p className="mt-1 text-xs text-ink/80 [overflow-wrap:anywhere]">
          {"고른 PV: "}
          <span className="font-medium">{loc.ref.pvc}</span>
          {` — ${row !== null ? nsLabel(row.namespace, row.storage_class) : loc.ref.namespace} · ${loc.ref.cluster}`}
        </p>
      )}
    </div>
  );
}

/** PV 안 경로 + 「실제 경로」·「n분 전 기준」·경고. */
function SubField({ side, operation, loc, onChange, resolved }: {
  side: Side; operation: string; loc: Loc; onChange: (next: Loc) => void; resolved: Resolved;
}) {
  const L = SIDE_LABELS[side];
  const rm = operation === "rm";
  const problem = loc.sub.trim() === "" && !rm ? null : pvSubProblem(loc.sub, operation);
  const row = resolved.status === "found" ? resolved.row : null;
  const abs = row !== null && problem === null ? pvAbsPath(row, loc.sub) : null;
  const fresh = row !== null && resolved.status === "found" ? freshnessOf(resolved.clusters, row.cluster) : null;
  const whole = normalizeSub(loc.sub) === "";
  return (
    <div className="min-w-0">
      <label className="text-sm block">{L.sub}
        <input aria-label={L.sub} className={field} value={loc.sub}
               placeholder={rm ? "예: sample/old — PV 전체는 삭제할 수 없습니다" : "비우면 PV 전체 · 예: sample/nested"}
               onChange={(e) => onChange({ ...loc, sub: e.target.value })} />
      </label>
      {resolved.status === "missing" && loc.ref !== null && (
        <p className="mt-1 text-sm text-bad [overflow-wrap:anywhere]">
          {`고른 PV(${loc.ref.namespace}/${loc.ref.pvc} · ${loc.ref.cluster})를 목록에서 찾을 수 없습니다 — 다시 고르세요(권한이 회수됐거나 PV 가 지워졌을 수 있습니다).`}
        </p>
      )}
      {row !== null && !row.selectable && (
        <p className="mt-1 text-sm text-bad">{`이 PV 는 지금 고를 수 없습니다 — ${pvMetaText(row)}`}</p>
      )}
      {problem !== null && (rm && loc.sub === "" ? (
        <p className="mt-1 text-xs text-ink/70">rm 은 PV 안의 하위 경로가 필요합니다 — PV 전체(루트)는 삭제할 수 없습니다.</p>
      ) : (
        <p className="mt-1 text-sm text-bad">{problem}</p>
      ))}
      {abs !== null && (
        <p className="mt-1 text-xs text-ink/80 [overflow-wrap:anywhere]">
          {`실제 경로: ${abs}`}{whole && !rm ? " (PV 전체)" : ""}
        </p>
      )}
      {loc.sub !== loc.sub.trim() && problem === null && (
        <p className="mt-1 text-xs text-ink/70">앞뒤 공백도 경로 이름에 그대로 들어갑니다.</p>
      )}
      {row !== null && (
        <p className="mt-1 text-xs text-ink/70 [overflow-wrap:anywhere]">
          {row.volume !== null && <>볼륨 <Mono>{row.volume}</Mono> · </>}
          {row.storage_name !== null && `스토리지 ${row.storage_name} · `}
          {`${row.cluster} `}
          {fresh === null || fresh.synced_at === null
            ? "동기화 시각 모름"
            : <><Ago iso={fresh.synced_at} /> 기준</>}
          {freshnessWarns(fresh) && (
            <span className="text-attn">
              {` · 목록이 오래됐을 수 있습니다(동기화 ${fresh?.status === "failing" ? "실패 중" : "지연"}) — 그 뒤의 권한·PV 변경은 `
                + "반영되지 않았을 수 있습니다. 제출은 그대로 됩니다"}
            </span>
          )}
        </p>
      )}
      {side === "destination" && row !== null && whole && problem === null && (
        <p className="mt-1 text-xs text-attn">
          목적지가 PV 전체(루트)라 PV 루트 디렉터리의 권한·시각이 소스에 맞춰 바뀝니다(소유자는 PV 루트 소유자 그대로) — 하위 디렉터리로 받으려면 경로를 적으세요.
        </p>
      )}
    </div>
  );
}

/** 목록 수준의 상태 문구(구획 머리, 쪽마다 반복하지 않는다) -- 조회 실패 · 아직 동기화 안 됨 · 권한 namespace 0개를 서로 다른 문장으로
 *  가른다(null(모름) ≠ 0: 한 번도 동기화되지 않은 클러스터의 권한은 「없음」이 아니라 「모름」이다). 제출 잠금은 SubmitJob 의
 *  blocked 하나가 맡고, 요약의 짧은 사유(hints)는 다른 문장이다. */
export function CatalogNotice({ catalog, search, actor, scopeUser }: {
  catalog: PvCatalog; search: boolean; actor: string; scopeUser: string;
}) {
  if (search) {
    return (
      <p className="text-xs text-ink/70">
        권한 기준 사용자를 비워 두면 모든 클러스터의 PV 를 검색해 고릅니다(결과는 {SEARCH_LIMIT}개까지).
      </p>
    );
  }
  if (catalog.status === "off" || catalog.status === "loading") return null;
  if (catalog.status === "error") {
    return <p className="text-sm text-bad">{`PV 목록을 불러오지 못했습니다: ${catalog.message}`}</p>;
  }
  const { clusters, namespaces, rows, truncated } = catalog;
  const synced = clusters.filter((c) => c.synced_at !== null);
  const unsynced = clusters.filter((c) => c.synced_at === null);
  if (synced.length === 0) {
    return (
      <p className="text-sm text-attn">
        아직 k8s 권한·PV 목록이 동기화되지 않았습니다 — 관리자가 클러스터 동기화를 마치면 PV 를 고를 수 있습니다.
      </p>
    );
  }
  return (
    <div className="space-y-1">
      {namespaces !== null && namespaces.length === 0 ? (
        <p className="text-sm text-bad [overflow-wrap:anywhere]">
          {`rolebinding ${bindingNameFor(actor)}(또는 namespace 소유자 namespaceAdmin)이 있는 namespace 가 없습니다 — 관리자에게 요청하세요`}
        </p>
      ) : namespaces === null && rows.length === 0 ? (
        <p className="text-sm text-bad [overflow-wrap:anywhere]">
          {`${scopeUser} 에게 권한 있는 PV 가 없습니다 — 「k8s 연동 → 사용자 권한」에서 확인하세요`}
        </p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-attn">{`권한 있는 namespace(${namespaces?.length ?? 0}개)에 PV 가 없습니다.`}</p>
      ) : null}
      {unsynced.length > 0 && (
        <p className="text-xs text-attn [overflow-wrap:anywhere]">
          {`아직 동기화되지 않은 클러스터(${unsynced.map((c) => c.name).join(", ")})의 PV·권한은 알 수 없어 목록에 없습니다.`}
        </p>
      )}
      {truncated && (
        <p className="text-xs text-attn">목록이 길어 일부만 보입니다 — 관리자에게 문의하세요.</p>
      )}
      <FreshnessLine clusters={clusters} />
    </div>
  );
}
