import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { StatusPill } from "../../components/ui/StatusPill";
import { Table } from "../../components/ui/Table";
import { reasonText } from "../../lib/api";
import { kstStampBare, kstStampOrDash } from "../../lib/datetime";
import { K8sHeader } from "./K8sTabs";
import {
  CHANGE_LABEL, COUNT_LABEL, ROLE_LABEL, TRIGGER_LABEL, changeNames, countText, durationText, runStatusMeta, warningText,
} from "./k8sTerms";
import { Ago, DefList, DefRow, Mono, SELECT_FIELD, Tag } from "./parts";
import { RUNS_PAGE_SIZE, useInfiniteSyncRuns, useK8sClusters } from "./useK8s";
import type { ChangeEntry, SyncRun } from "./types";

// 동기화 이력(설계 §4·§7): k8s_sync_runs 를 seq 내림차순으로, 다음 쪽은 마지막 seq 를 before 로 당긴다(키셋 커서 — 감사 로그·
// 전체 작업과 같은 IntersectionObserver 무한 스크롤). 상세 창은 추가·삭제 개수와 이름(상위 50), 개수, 경고, 오류 원문.
// 주소의 ?cluster= 로 들어오면(개요 카드의 「이력」) 그 클러스터만 본다.

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

/** 표 「변경」 칸 한 줄 — 「PVC +2 −1 ~1 · 바인딩 +1」(~ = 내용만 바뀜: Pending→Bound 등). 변경이 없으면 「변경 없음」, 정보가
 *  없으면(실패·진행 중) null. 목록 값 null(그 선택 목록을 못 받음)은 건너뛴다 — 상세가 따로 알린다. */
export function changeSummary(changes: Record<string, ChangeEntry | null> | null | undefined): string | null {
  if (!changes) return null;
  const parts: string[] = [];
  for (const [k, e] of Object.entries(changes)) {
    if (!e || typeof e !== "object") continue;
    const add = typeof e.added === "number" ? e.added : 0;
    const rem = typeof e.removed === "number" ? e.removed : 0;
    const chg = typeof e.changed === "number" ? e.changed : 0;
    if (add === 0 && rem === 0 && chg === 0) continue;
    const label = k === "pvcs" ? "PVC" : k === "bindings" || k === "role_bindings" ? "바인딩" : (CHANGE_LABEL[k] ?? k);
    const marks = [add > 0 ? `+${add}` : null, rem > 0 ? `−${rem}` : null, chg > 0 ? `~${chg}` : null].filter(Boolean);
    parts.push(`${label} ${marks.join(" ")}`);
  }
  return parts.length === 0 ? "변경 없음" : parts.join(" · ");
}

function NameList({ title, names, tone }: { title: string; names: string[]; tone: "ok" | "bad" | "neutral" }) {
  if (names.length === 0) return null;
  return (
    <div className="min-w-0">
      <p className="text-xs text-ink/70">{title}</p>
      <div className="mt-1 flex flex-wrap gap-1">
        {names.map((n) => <Tag key={n} tone={tone}><span className="font-mono">{n}</span></Tag>)}
      </div>
    </div>
  );
}

function RunDetail({ r }: { r: SyncRun }) {
  const [open, setOpen] = useState(false);
  const meta = runStatusMeta(r.status);
  const changes = Object.entries(r.changes ?? {});
  const counts = Object.entries(r.counts ?? {});
  return (
    <Dialog open={open} onOpenChange={setOpen} size="lg" title={`동기화 #${r.seq} — ${r.cluster_name}`}
            trigger={<Button variant="ghost" className="whitespace-nowrap px-2.5 py-1 text-xs">상세</Button>}>
      <div className="space-y-4 text-sm">
        <DefList>
          {/* 창 안에서는 설명 툴팁(help)을 달지 않는다 -- 포커스 가능한 배지가 창의 첫 포커스가 되어 열자마자 툴팁이 제목을
              덮었다(실 Chrome 확인). 설명은 옆에 글로 둔다. */}
          <DefRow label="상태">
            <StatusPill state={r.status} label={meta.label} variant={meta.variant} />
            <span className="ml-2 text-xs text-ink/70">{meta.help}</span>
          </DefRow>
          <DefRow label="run"><Mono>{r.run_id}</Mono></DefRow>
          <DefRow label="호출 노드">{`${r.node_name ?? "모름"}${r.role ? ` (${ROLE_LABEL[r.role] ?? r.role})` : ""} · ${TRIGGER_LABEL[r.trigger] ?? r.trigger}`}</DefRow>
          <DefRow label="시각">
            {`가져감 ${kstStampOrDash(r.claimed_at)} · 끝남 ${kstStampOrDash(r.finished_at)}`}
            {r.applied_at && ` · 적용 ${kstStampOrDash(r.applied_at)}`}
          </DefRow>
          <DefRow label="소요">{durationText(r.duration_ms)}</DefRow>
          {r.server_version && <DefRow label="k8s 버전">{r.server_version}</DefRow>}
          {(r.reason || r.error) && (
            <DefRow label="사유">
              <span className="text-bad">{r.reason ? reasonText(r.reason) : "—"}</span>
              {(r.phase || r.http_status !== null) && (
                <span className="block text-xs text-ink/70">
                  {[r.phase ? `단계 ${r.phase}` : null, r.http_status !== null ? `HTTP ${r.http_status}` : null].filter(Boolean).join(" · ")}
                </span>
              )}
              {r.error && <Mono className="mt-1 block text-ink/80">{r.error}</Mono>}
            </DefRow>
          )}
        </DefList>

        {(r.warnings ?? []).length > 0 && (
          <ul className="space-y-0.5 rounded-lg bg-attnbg/50 px-3 py-2 text-xs text-attn">
            {(r.warnings ?? []).map((w, i) => <li key={i}>{`경고: ${warningText(w)}`}</li>)}
          </ul>
        )}

        <div>
          <h3 className="mb-1.5 text-sm font-semibold">변경</h3>
          {r.changes === null ? <p className="text-ink/70">변경 정보가 없습니다(적용되지 않은 run)</p>
            : changes.length === 0 ? <p className="text-ink/70">변경 없음</p> : (
              <div className="space-y-3">
                {changes.map(([k, e]) => {
                  // null = 이번 run 이 그 선택 목록(SC·PV)을 받지 못함 — 이전 값을 그대로 둔다(경고 줄이 사유를 말한다).
                  if (!e || typeof e !== "object") {
                    return (
                      <div key={k} className="rounded-lg border border-line px-3 py-2">
                        <p className="font-medium">
                          {CHANGE_LABEL[k] ?? k}
                          <span className="ml-2 font-normal text-ink/70">이번에 받지 못함(목록 권한 없음 등 — 이전 값 유지)</span>
                        </p>
                      </div>
                    );
                  }
                  const names = changeNames(e);
                  const listed = names.added.length + names.removed.length + names.other.length;
                  return (
                    <div key={k} className="rounded-lg border border-line px-3 py-2">
                      <p className="font-medium">
                        {CHANGE_LABEL[k] ?? k}
                        <span className="ml-2 font-normal tabular-nums text-ink/80">
                          {`추가 ${countText(e.added)} · 삭제 ${countText(e.removed)}`}
                          {e.changed !== undefined && ` · 바뀜 ${countText(e.changed)}`}
                        </span>
                      </p>
                      <div className="mt-1.5 space-y-2">
                        <NameList title="추가" names={names.added} tone="ok" />
                        <NameList title="삭제" names={names.removed} tone="bad" />
                        <NameList title="바뀜(내용만 — 상태·바인딩 대상 등)" names={names.other} tone="neutral" />
                      </div>
                      {(e.added ?? 0) + (e.removed ?? 0) + (e.changed ?? 0) > listed && listed > 0 && (
                        <p className="mt-1 text-xs text-ink/70">이름은 종류마다 상위 50개까지만 기록합니다</p>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
        </div>

        {counts.length > 0 && (
          <div>
            <h3 className="mb-1.5 text-sm font-semibold">받은 개수</h3>
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {counts.map(([k, v]) => (
                <div key={k} className="rounded-lg bg-panel px-3 py-2">
                  <div className="text-[11px] text-ink/70">{COUNT_LABEL[k] ?? k}</div>
                  <div className="font-semibold tabular-nums">{countText(v)}</div>
                </div>
              ))}
            </div>
            <p className="mt-1 text-xs text-ink/70">— 는 그 목록을 받지 못함(권한 없음 등 — 이전 값 유지)입니다.</p>
          </div>
        )}
        <div className="flex justify-end border-t border-line pt-3">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
        </div>
      </div>
    </Dialog>
  );
}

export function SyncHistoryPage() {
  const [params, setParams] = useSearchParams();
  const cluster = params.get("cluster") ?? "";
  const node = params.get("node") ?? "";
  const filter = useMemo(() => ({ cluster: cluster || undefined, node: node || undefined }), [cluster, node]);
  const q = useInfiniteSyncRuns(filter);
  const clustersQ = useK8sClusters();
  const rows = useMemo(() => q.data?.pages.flat() ?? [], [q.data]);
  const setFilter = (key: "cluster" | "node", value: string) => {
    const next = new URLSearchParams(params);
    if (value === "") next.delete(key); else next.set(key, value);
    setParams(next, { replace: true });
  };

  // 노드 선택지는 본 행에서 모은다(누적 — 노드로 걸러도 선택지가 사라지지 않게).
  const [seenNodes, setSeenNodes] = useState<string[]>([]);
  useEffect(() => {
    setSeenNodes((prev) => {
      const s = new Set(prev);
      for (const r of rows) if (r.node_name) s.add(r.node_name);
      if (node) s.add(node);
      return s.size === prev.length ? prev : [...s].sort();
    });
  }, [rows, node]);
  const clusterNames = useMemo(() => {
    const s = new Set((clustersQ.data ?? []).map((c) => c.name));
    if (cluster) s.add(cluster);
    return [...s].sort();
  }, [clustersQ.data, cluster]);

  // 무한 스크롤(감사 로그 관례): 감시 노드가 보이면 다음 쪽. 다음 쪽 실패는 자동 재시도하지 않는다 -- 버튼으로만.
  const { hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage } = q;
  const sentinel = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && hasNextPage && !isFetchingNextPage && !isFetchNextPageError) fetchNextPage();
    }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [hasNextPage, isFetchingNextPage, isFetchNextPageError, fetchNextPage, rows.length]);

  return (
    <section className="space-y-4">
      <K8sHeader title="동기화 이력"
                 description={`호출 노드가 클러스터 목록을 받아 온 기록입니다 — 최근 것부터 ${RUNS_PAGE_SIZE}건씩, 아래로 스크롤하면 이어서 불러옵니다. 30일이 지난 기록은 정리되지만 클러스터마다 최근 50건과 마지막 성공은 남깁니다.`} />
      <div className="flex flex-wrap items-center gap-2">
        <select aria-label="클러스터 필터" className={SELECT_FIELD} value={cluster} onChange={(e) => setFilter("cluster", e.target.value)}>
          <option value="">클러스터 전체</option>
          {clusterNames.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
        <select aria-label="호출 노드 필터" className={SELECT_FIELD} value={node} onChange={(e) => setFilter("node", e.target.value)}>
          <option value="">호출 노드 전체</option>
          {seenNodes.map((n) => <option key={n} value={n}>{n}</option>)}
        </select>
      </div>
      {q.isError && rows.length === 0 ? <p role="alert" className="text-sm text-bad">{`이력을 불러오지 못했습니다 — ${errText(q.error)}`}</p> : (
        <Card>
          <Table>
            <thead>
              <tr className="whitespace-nowrap text-ink/70">
                <th className="py-2 pr-3 text-right">#</th><th className="pr-3">가져간 시각(KST)</th><th className="pr-3">클러스터</th>
                <th className="pr-3">호출 노드</th><th className="pr-3">계기</th><th className="pr-3">상태</th>
                <th className="pr-3 text-right">소요</th><th className="pr-3">결과</th><th>작업</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const meta = runStatusMeta(r.status);
                const summary = changeSummary(r.changes);
                return (
                  <tr key={r.run_id} className="border-t border-line align-top">
                    <td className="py-2 pr-3 text-right tabular-nums text-ink/70">{r.seq}</td>
                    <td className="whitespace-nowrap py-2 pr-3 tabular-nums">
                      {r.claimed_at ? kstStampBare(r.claimed_at) : "—"}
                      <span className="block text-xs text-ink/70"><Ago iso={r.claimed_at} /></span>
                    </td>
                    <td className="whitespace-nowrap py-2 pr-3 font-medium">{r.cluster_name}</td>
                    <td className="whitespace-nowrap py-2 pr-3">
                      {r.node_name ?? "—"}
                      {r.role && <span className="ml-1 text-xs text-ink/70">{ROLE_LABEL[r.role] ?? r.role}</span>}
                    </td>
                    <td className="whitespace-nowrap py-2 pr-3">{TRIGGER_LABEL[r.trigger] ?? r.trigger}</td>
                    <td className="py-2 pr-3"><StatusPill state={r.status} label={meta.label} variant={meta.variant} help={meta.help} /></td>
                    <td className="whitespace-nowrap py-2 pr-3 text-right tabular-nums">{durationText(r.duration_ms)}</td>
                    <td className="max-w-[22rem] py-2 pr-3">
                      {r.reason && r.status !== "applied"
                        ? <span className="text-bad">{reasonText(r.reason)}</span>
                        : summary !== null ? <span className="tabular-nums">{summary}</span>
                        : <span className="text-ink/70">—</span>}
                      {(r.warnings ?? []).length > 0 && <span className="block text-xs text-attn">{`경고 ${(r.warnings ?? []).length}건`}</span>}
                    </td>
                    <td className="py-2"><RunDetail r={r} /></td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          {q.isLoading ? <p className="pt-3 text-ink/70">불러오는 중…</p>
            : rows.length === 0 ? (
              <p className="pt-3 text-ink/70">
                {cluster || node ? "조건에 맞는 동기화 기록이 없습니다" : "아직 동기화 기록이 없습니다 — 호출 노드가 지정되고 클러스터가 등록되면 쌓입니다."}
              </p>
            ) : (
              <div ref={sentinel} className="pt-3 text-center text-xs text-ink/70" aria-live="polite">
                {isFetchNextPageError ? (
                  <span className="text-bad">
                    {`이전 기록을 더 불러오지 못했습니다: ${errText(q.error)} `}
                    <Button variant="ghost" onClick={() => fetchNextPage()}>다시 시도</Button>
                  </span>
                ) : isFetchingNextPage ? "더 불러오는 중…"
                  : hasNextPage ? `${rows.length}건 표시 — 스크롤하면 더 불러옵니다`
                  : `마지막 기록입니다 — 전체 ${rows.length}건`}
                {q.isRefetchError && (
                  <p className="text-bad">{`최신 기록을 다시 읽지 못했습니다(표시는 직전 값): ${errText(q.error)}`}</p>
                )}
              </div>
            )}
        </Card>
      )}
    </section>
  );
}
