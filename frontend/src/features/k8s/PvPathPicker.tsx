import { useState } from "react";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";
import { useSearchField } from "../jobs/searchField";
import { nsLabel } from "./k8sTerms";
import { Mono } from "./parts";
import { normalizeSub, pvAbsPath, pvMetaText, pvSubProblem, rowKey } from "./pvLocation";
import { useK8sVolumes } from "./useK8s";
import { SEARCH_LIMIT } from "./usePvCatalog";
import type { VolumeRow } from "./types";

// 배치 생성의 「PV 에서 경로 찾기」(2026-10-26 사용자 결정 (e) -- 「배치는 직접 경로만 받도록. 단, 등록할 때 단일 작업 등록할
// 때처럼 검색해서 찾아볼 수 있도록」). 배치 바디는 그대로 직접 경로(스토리지 + 관리 디렉토리 기준 상대경로)다 -- 이 패널은
// PV 를 검색해 그 PV 의 직접 경로를 표의 칸에 넣어 줄 뿐이다(PV 주소 블록을 싣지 않는다). 검색은 PV 탐색과 같은 관리자 API
// (배치 생성자는 늘 특권 세션 관리자라 전체 PV 를 본다), 행의 root = 관리 디렉토리 기준 PV 루트(서버가 해석한 값).

export type PvSlot = "a" | "b";   // a = 경로(scan·rm)·소스(sync), b = 목적지(sync)

/** PV 루트(관리 디렉토리 기준) + PV 안 하위경로 → 배치 행에 넣을 상대경로. root 를 모르면 null. */
export function pvBatchPath(root: string | null | undefined, sub: string): string | null {
  if (typeof root !== "string" || root === "") return null;
  const s = normalizeSub(sub);
  const r = root.replace(/\/+$/, "");
  return s === "" ? r : `${r}/${s}`;
}

/** 이 행을 그 칸에 넣을 수 없는 이유(없으면 null). 배치는 칸마다 스토리지가 하나다(scan·rm = 하나, sync = 소스·목적지 쌍). */
export function slotProblem(row: VolumeRow, sub: string, op: string, slot: PvSlot, current: string): string | null {
  if (!row.selectable) return "고를 수 없는 PV 입니다";
  if (pvBatchPath(row.root, sub) === null) return "이 PV 의 경로를 계산할 수 없습니다";
  const problem = pvSubProblem(sub, op === "rm" ? "rm" : op);
  if (problem !== null) return problem;
  if (row.storage_name === null) return "이 PV 의 스토리지가 매핑돼 있지 않습니다";
  if (current !== "" && current !== row.storage_name)
    return `${slot === "b" ? "목적지" : op === "sync" ? "소스" : "배치"} 스토리지(${current})와 다른 스토리지(${row.storage_name})의 PV 입니다`;
  return null;
}

export function PvPathPicker({ op, storages, onInsert }: {
  op: string;
  storages: { a: string; b: string };                 // 지금 배치에 고른 스토리지(칸별)
  onInsert: (slot: PvSlot, storage: string, path: string) => void;
}) {
  const s = useSearchField("");
  const q = useK8sVolumes({ q: s.applied, limit: SEARCH_LIMIT });
  const rows = Array.isArray(q.data?.volumes) ? q.data!.volumes : [];
  const [picked, setPicked] = useState<string | null>(null);
  const [sub, setSub] = useState("");
  const row = rows.find((r) => rowKey(r) === picked) ?? null;
  const slots: { slot: PvSlot; label: string }[] = op === "sync"
    ? [{ slot: "a", label: "소스 칸에 넣기" }, { slot: "b", label: "목적지 칸에 넣기" }]
    : [{ slot: "a", label: "경로 칸에 넣기" }];
  const path = row !== null ? pvBatchPath(row.root, sub) : null;
  const abs = row !== null ? pvAbsPath(row, sub) : null;
  return (
    <section aria-label="PV 에서 경로 찾기" className="rounded-card border border-line bg-surface p-4 shadow-soft">
      <h2 className="text-sm font-bold">PV 에서 경로 찾기</h2>
      <p className="mt-0.5 text-xs text-muted">
        PV 를 검색해 그 PV 의 직접 경로를 표에 넣습니다 — 배치는 직접 경로(스토리지 + 경로)로만 만들어집니다.
      </p>
      <label className="mt-3 block text-sm">PV 검색
        <input aria-label="PV 검색" className={field} placeholder="PV 이름 · namespace · 볼륨 · StorageClass" {...s.bind} />
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
        <div role="radiogroup" aria-label="PV 검색 결과 목록"
             className="mt-2 max-h-64 overflow-y-auto rounded-lg border border-line">
          {rows.map((r) => (
            <label key={rowKey(r)}
                   className={`flex min-w-0 items-start gap-2 border-b border-line px-3 py-2 text-sm last:border-b-0 ${
                     r.selectable ? "cursor-pointer" : "opacity-60"}`}>
              <input type="radio" name="batch-pv-search" className="mt-1" disabled={!r.selectable}
                     checked={picked === rowKey(r)} onChange={() => { setPicked(rowKey(r)); setSub(""); }} />
              <span className="min-w-0">
                <span className="block [overflow-wrap:anywhere]">
                  <span className="font-medium">{r.pvc}</span>{` — ${nsLabel(r.namespace, r.storage_class)}`}
                </span>
                <span className="block text-xs text-ink/70 [overflow-wrap:anywhere]">
                  {pvMetaText(r)}{` · ${r.cluster}`}{r.storage_name !== null && ` · ${r.storage_name}`}
                </span>
              </span>
            </label>
          ))}
        </div>
      )}
      {q.data?.truncated === true && (
        <p className="mt-1 text-xs text-attn">{`결과가 ${SEARCH_LIMIT}개를 넘어 앞의 ${SEARCH_LIMIT}개만 보입니다 — 검색어를 좁히세요.`}</p>
      )}
      {row !== null && (
        <div className="mt-3 space-y-2 border-t border-line pt-3">
          <label className="block text-sm">PV 안 경로(비우면 PV 전체)
            <input aria-label="PV 안 경로" className={field} value={sub} onChange={(e) => setSub(e.target.value)} />
          </label>
          <dl className="space-y-1 text-xs">
            <div className="flex gap-2"><dt className="shrink-0 text-ink/70">스토리지</dt>
              <dd className="min-w-0 [overflow-wrap:anywhere]">{row.storage_name ?? "매핑 없음"}</dd></div>
            <div className="flex gap-2"><dt className="shrink-0 text-ink/70">넣을 경로</dt>
              <dd className="min-w-0"><Mono className="break-all">{path ?? "계산할 수 없음"}</Mono></dd></div>
            <div className="flex gap-2"><dt className="shrink-0 text-ink/70">실제 경로</dt>
              <dd className="min-w-0"><Mono className="break-all">{abs ?? "모름"}</Mono></dd></div>
          </dl>
          <div className="flex flex-wrap gap-2">
            {slots.map(({ slot, label }) => {
              const problem = slotProblem(row, sub, op, slot, slot === "a" ? storages.a : storages.b);
              return (
                <div key={slot} className="min-w-0">
                  <Button type="button" variant="ghost" disabled={problem !== null} title={problem ?? undefined}
                          onClick={() => {
                            if (problem === null && path !== null && row.storage_name !== null)
                              onInsert(slot, row.storage_name, path);
                          }}>{label}</Button>
                  {problem !== null && <p className="mt-1 text-xs text-attn">{problem}</p>}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
