import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, CircleAlert, Database, FolderCog, ListFilter, Server } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { ApiError, reasonText } from "../../lib/api";
import type { MountBaseCell, MountCell, MountNode } from "../../lib/types";
import { ageText } from "../nodes/NodesList";
import { useMounts } from "./useMounts";
import {
  BASE_LABEL, CELL_LABEL, baseChecksFailed, baseDetail, baseHeadOverridden, baseOf, cellDetail, cellOf, isStale, nodeHasProblem,
  nodesOf, reasonShort, rolesOf,
  storagesOf, summaryOf, tri, type StorageCol,
} from "./mountView";

// 마운트 현황(2026-11-23 사용자 요청 「스토리지 하위 마운트 현황 메뉴 -- 노드 × 스토리지 마운트 현황 표」). 계기: dms-api 가 공유
// 스토리지 미마운트 노드에 떠 아티팩트를 못 읽은 사고 -- 노드별 마운트를 한눈에 볼 자리가 없었다(노드 화면은 노드마다 상세 창을
// 열어야 했다). 위에서 아래로: 요약 한 줄(노드·보고 끊김·스토리지·문제 칸) → 「문제만 보기」 + 범례 → 표(첫 열 노드 고정, 스토리지
// 열들, 마지막 「아티팩트 경로」 열) → 각주. 판정은 서버(routes_mounts -- DB 만)가 하고 화면은 그리기만 한다.
//
// 375 폭: 표는 래퍼(Table 의 overflow-x-auto) 안에서만 가로로 넘치고 노드 칸은 고정(sticky left-0)이라 오른쪽 칸을 넘겨도 어느
// 노드의 값인지 보인다. 고정 칸 바탕은 불투명(bg-surface)이고 z-index 를 주지 않으며 스크롤되는 칸에 쌓임 맥락(opacity·transform)을
// 만들지 않는다(TargetTable 머리 주석의 결정 그대로 -- 만들면 그 칸이 고정 칸 위로 올라온다). 그래서 「보고 끊김」 행은 CSS opacity
// 가 아니라 흐린 색(점은 색 알파, 글자는 muted)으로 흐리게 그린다 -- 행 opacity 는 고정 칸까지 반투명하게 만들어 뒤로 지나가는
// 칸이 비친다.

const TH = "px-3 py-2 text-left align-bottom text-xs font-medium text-muted whitespace-nowrap";

// 상태 점(색 + 모양 -- 보고 없음은 속 빈 원이라 색을 못 가려도 구별된다). 흐림은 색 알파로만(머리 주석).
const DOT: Record<MountCell["status"], { on: string; dim: string; text: string }> = {
  ready: { on: "bg-ok", dim: "bg-ok/40", text: "text-ink" },
  readonly: { on: "bg-attn", dim: "bg-attn/40", text: "text-attn" },
  missing: { on: "bg-bad", dim: "bg-bad/40", text: "text-bad" },
  unreported: { on: "border border-muted bg-surface", dim: "border border-muted/50 bg-surface", text: "text-muted" },
  disabled: { on: "bg-line", dim: "bg-line", text: "text-muted" },
};
// 문제 칸의 옅은 바탕(색 알파 -- 쌓임 맥락 없음). 보고가 살아 있는 행에서만(끊긴 보고의 칸은 옛 사실이다).
const TINT: Partial<Record<MountCell["status"], string>> = { missing: "bg-badbg/60", readonly: "bg-attnbg/60" };

function Dot({ status, dim }: { status: MountCell["status"]; dim: boolean }) {
  const s = DOT[status];
  return <span aria-hidden className={`inline-block h-2 w-2 shrink-0 rounded-full ${dim ? s.dim : s.on}`} />;
}

// 짧은 상태 글자 + 점, 긴 설명은 title 과 낭독 문장(sr-only). 이 span 에 relative 를 주지 않는다 -- 위치 지정 요소는 DOM 순서상
// 뒤라 옆으로 밀 때 고정 노드 칸 위에 그려진다(375 실측: 노드 이름 위로 「마운트됨」이 겹쳤다). sr-only(absolute)의 기준 상자는
// 표 래퍼(relative overflow-x-auto)라 문서 폭을 넓히지 않고, 1px 로 잘려 고정 칸 위에 보이지도 않는다.
function StatusMark({ status, label, short, detail, dim }: {
  status: MountCell["status"]; label: string; short: string | null; detail: string; dim: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs" title={detail}>
      <Dot status={status} dim={dim} />
      <span className={dim ? "text-muted" : DOT[status].text}>{label}</span>
      {short !== null && <span className={dim ? "text-muted" : "text-bad/80"}>{`· ${short}`}</span>}
      <span className="sr-only">{`(${detail})`}</span>
    </span>
  );
}

const TAG = {
  role: "bg-infobg text-busy",
  bad: "bg-badbg text-bad",
  attn: "bg-attnbg text-attn",
  muted: "border border-line bg-panel text-muted",
} as const;
function Tag({ tone, title, children }: { tone: keyof typeof TAG; title?: string; children: React.ReactNode }) {
  return (
    <span title={title} className={`inline-flex items-center whitespace-nowrap rounded px-1.5 text-[11px] leading-[1.125rem] ${TAG[tone]}`}>
      {children}
    </span>
  );
}

function NodeCell({ n, now }: { n: MountNode; now: number }) {
  const roles = rolesOf(n);
  const stale = isStale(n);
  const tags: React.ReactNode[] = [];
  if (roles.includes("api")) tags.push(<Tag key="api" tone="role" title="이 노드에 API 파드가 떠 있습니다(최근 점검 기록 기준)">API</Tag>);
  if (roles.includes("controller")) {
    tags.push(<Tag key="ctrl" tone="role" title="이 노드에 컨트롤러 파드가 떠 있습니다(최근 점검 기준)">컨트롤러</Tag>);
  }
  if (n.has_agent === false) {
    tags.push(<Tag key="noagent" tone="muted" title="노드 에이전트가 없어 마운트를 보고하지 않는 노드(제어면 파드만 확인됨)">에이전트 없음</Tag>);
  }
  if (stale) {
    const ago = n.reported_at ? ageText(n.reported_at, now) : null;
    tags.push(<Tag key="stale" tone="bad" title={`에이전트 보고가 끊겼습니다${ago ? ` — 마지막 보고 ${ago}` : ""}. 칸의 값은 마지막 보고 기준입니다`}>
      보고 끊김</Tag>);
  }
  if (n.excluded === true) tags.push(<Tag key="ex" tone="bad" title="관리자가 배치에서 제외한 노드(노드 화면에서 다시 포함)">배치 제외</Tag>);
  if (tri(n.schedulable) === false) {
    tags.push(<Tag key="sched" tone="attn" title={`k8s 스케줄 불가(${n.schedulable_reason ?? "사유 모름"})`}>스케줄 불가</Tag>);
  }
  return (
    <div className="flex min-w-0 max-w-[9.5rem] flex-col gap-1 sm:max-w-[16rem]">
      <Link to="/admin/nodes" title={`${n.node_name} — 노드 화면에서 보기`}
            className={`truncate rounded font-medium hover:underline focus-visible:outline-none focus-visible:ring-2
                        focus-visible:ring-accent ${stale ? "text-muted" : "text-accent"}`}>
        {n.node_name}
      </Link>
      {tags.length > 0 && <div className="flex flex-wrap gap-1">{tags}</div>}
    </div>
  );
}

// API·컨트롤러 자체 점검 한 줄(그 노드에 뜬 파드만). 모름(점검 전)은 실패가 아니다 -- 흐린 글자.
function CheckLine({ label, results }: { label: string; results: { ok: boolean | null; title: string }[] }) {
  if (results.length === 0) return null;
  const failed = results.filter((r) => r.ok === false).length;
  const okN = results.filter((r) => r.ok === true).length;
  const state = failed > 0 ? "실패" : okN > 0 ? "정상" : "점검 전";
  const tone = failed > 0 ? "text-bad" : okN > 0 ? "text-ok" : "text-muted";
  const many = results.length > 1 ? ` ${results.length}개` : "";
  return (
    <span className={`whitespace-nowrap ${tone}`} title={results.map((r) => r.title).join("\n")}>
      {`${label}${many} ${state}${failed > 0 && results.length > 1 ? ` ${failed}` : ""}`}
    </span>
  );
}

function BaseCell({ n, dim }: { n: MountNode; dim: boolean }) {
  const b = baseOf(n);
  if (b === null) return <span className="text-xs text-muted">—</span>;
  const api = b.api.map((a) => ({
    ok: tri(a.ok),
    title: `API ${a.instance}: ${tri(a.ok) === null ? "점검 전" : tri(a.ok) ? "정상" : reasonText(a.reason) || "실패"}`,
  }));
  const ctrl = b.controller ? [{
    ok: tri(b.controller.ok),
    title: `컨트롤러: ${tri(b.controller.ok) === null ? "점검 전" : tri(b.controller.ok) ? "정상" : reasonText(b.controller.reason) || "실패"}`,
  }] : [];
  // 에이전트는 정상(또는 보고 없음)인데 그 노드의 API·컨트롤러 자체 점검이 실패하면 칸 머리를 「점검 실패」로 -- 초록 「정상」
  // 아래 빨간 「실패 1」은 한 칸이 두 말을 한다(2026-11-23 라이브 확인; 요약은 이미 이 칸을 문제로 센다). 설명(baseDetail)도
  // 같은 판정으로 실패한 역할을 말한다.
  const shown = baseHeadOverridden(b) ? "missing" : b.status;
  return (
    <div className="flex flex-col gap-0.5">
      <StatusMark status={shown} label={shown !== b.status ? "점검 실패" : BASE_LABEL[b.status]} dim={dim}
                  short={b.status === "missing" ? reasonShort(b.reason) : null} detail={baseDetail(n, b)} />
      {(api.length > 0 || ctrl.length > 0) && (
        <span className="flex flex-wrap gap-x-2 gap-y-0.5 pl-3.5 text-[11px]">
          <CheckLine label="API" results={api} />
          <CheckLine label="컨트롤러" results={ctrl} />
        </span>
      )}
    </div>
  );
}

// 아티팩트 경로 칸 바탕: 문제 칸 규칙(baseProblem)과 같게 -- 점검 실패는 없음과 같은 빨강, 읽기 전용만이면 주의 색.
function baseTint(b: MountBaseCell | null): string {
  if (b === null) return "";
  if (b.status === "missing" || baseChecksFailed(b)) return TINT.missing ?? "";
  return b.status === "readonly" ? TINT.readonly ?? "" : "";
}

function Legend() {
  const items: [MountCell["status"], string][] = [
    ["ready", "마운트됨"], ["readonly", "읽기 전용"], ["missing", "없음(+사유)"], ["unreported", "보고 없음"], ["disabled", "비활성"],
  ];
  return (
    <ul aria-label="범례" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
      {items.map(([s, label]) => (
        <li key={s} className="inline-flex items-center gap-1.5 whitespace-nowrap"><Dot status={s} dim={false} />{label}</li>
      ))}
    </ul>
  );
}

// 표 래퍼가 옆으로 밀렸는지 -- 고정 칸 오른쪽 경계선은 밀렸을 때만(넓은 화면에선 칸 사이 선이 없다, TargetTable 과 같은 규칙).
function useScrolled() {
  const ref = useRef<HTMLDivElement>(null);
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const onScroll = () => setScrolled(el.scrollLeft > 0);
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);
  return { ref, scrolled };
}

function SummaryLine({ nodeCount, freshCount, staleCount, storageCount, problemCells, problemNodes }: ReturnType<typeof summaryOf>) {
  const n = (x: number) => x.toLocaleString("ko-KR");
  const item = "inline-flex items-center gap-1.5";
  const ico = "h-4 w-4 shrink-0";
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 text-sm text-ink/70">
      <span className={item}><Server aria-hidden className={`${ico} text-muted`} />{`노드 ${n(nodeCount)}대`}</span>
      {staleCount > 0 && (
        <span className={`${item} font-medium text-bad`}><AlertTriangle aria-hidden className={ico} />{`보고 끊김 ${n(staleCount)}대`}</span>
      )}
      <span className={item}><Database aria-hidden className={`${ico} text-muted`} />{`스토리지 ${n(storageCount)}개`}</span>
      {problemCells > 0 ? (
        <span className={`${item} font-medium text-bad`}>
          <CircleAlert aria-hidden className={ico} />{`문제 ${n(problemCells)}칸 · 노드 ${n(problemNodes)}대`}</span>
      ) : freshCount === 0 ? (
        // 보고 중인 노드가 하나도 없으면 「정상」이라고 말할 근거가 없다(새 설치·에이전트 전체 장애) -- 2026-11-23 리뷰.
        <span className={`${item} text-muted`}>보고 중인 노드가 없어 마운트 상태를 알 수 없습니다</span>
      ) : (
        // 보고가 끊긴 노드가 있으면 「모든 노드」라고 단정하지 않는다(끊긴 노드의 지금 상태는 모른다).
        <span className={`${item} text-ok`}>
          <CheckCircle2 aria-hidden className={ico} />
          {staleCount > 0 ? "보고 중인 노드는 모두 등록된 스토리지를 정상 마운트하고 있습니다"
            : "모든 노드가 등록된 스토리지를 정상 마운트하고 있습니다"}
        </span>
      )}
    </div>
  );
}

function Empty({ title, hint, action }: { title: string; hint?: React.ReactNode; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-line px-4 py-10 text-center">
      <span aria-hidden className="grid h-10 w-10 place-items-center rounded-full bg-panel text-muted"><Server className="h-5 w-5" /></span>
      <p className="text-sm font-medium text-ink break-keep">{title}</p>
      {hint && <p className="max-w-md text-xs text-muted break-keep">{hint}</p>}
      {action}
    </div>
  );
}

function MountsTable({ rows, cols, covering, basePath, now }: {
  rows: MountNode[]; cols: StorageCol[]; covering: string | null; basePath: string | null; now: number;
}) {
  const wrap = useScrolled();
  const edge = wrap.scrolled ? "shadow-[1px_0_0_theme(colors.line)]" : "";
  return (
    // 공용 Table 대신 같은 모양의 래퍼를 직접 쓴다: 래퍼가 relative 여야 칸 안 sr-only 의 기준 상자가 스크롤 영역이 된다(StatusMark).
    <div ref={wrap.ref} className="relative overflow-x-auto">
      <table className="w-full text-left text-sm">
      <thead><tr>
        <th className={`${TH} sticky left-0 bg-surface pl-2 ${edge}`}>노드</th>
        {cols.map((c) => (
          <th key={c.storage_name} className={TH}>
            <span className={`flex items-center gap-1 ${c.enabled ? "text-ink" : "text-muted"}`}>
              {c.storage_name}
              {covering === c.storage_name && (
                <span title="아티팩트 경로가 이 스토리지 아래에 있습니다" className="inline-flex text-muted">
                  <FolderCog aria-hidden className="h-3.5 w-3.5" /><span className="sr-only">(아티팩트 경로를 덮는 스토리지)</span>
                </span>
              )}
              {!c.enabled && <span className="font-normal">· 비활성</span>}
            </span>
            <span title={c.mount_path} className="block max-w-[14rem] truncate font-mono text-[11px] font-normal text-muted">
              {c.mount_path || "—"}</span>
          </th>
        ))}
        <th className={TH}>
          <span className="block text-ink">아티팩트 경로</span>
          <span className="block max-w-[16rem] truncate font-mono text-[11px] font-normal text-muted" title={basePath ?? undefined}>
            {basePath ?? "—"}</span>
          <span className="block text-[11px] font-normal text-muted">
            {covering !== null ? `덮는 스토리지 ${covering}` : "덮는 등록 스토리지 없음"}</span>
        </th>
      </tr></thead>
      <tbody>
        {rows.map((n) => {
          const dim = isStale(n);
          return (
            <tr key={n.node_name} className="border-t border-line">
              <td className={`sticky left-0 bg-surface py-2 pl-2 pr-3 align-top ${edge}`}><NodeCell n={n} now={now} /></td>
              {cols.map((c) => {
                const cell = cellOf(n, c);
                return (
                  <td key={c.storage_name} className={`px-3 py-2 align-top ${dim ? "" : TINT[cell.status] ?? ""}`}>
                    <StatusMark status={cell.status} label={CELL_LABEL[cell.status]} dim={dim}
                                short={cell.status === "missing" ? reasonShort(cell.reason) : null}
                                detail={cellDetail(c, cell, n)} />
                  </td>
                );
              })}
              <td className={`px-3 py-2 align-top ${dim ? "" : baseTint(baseOf(n))}`}><BaseCell n={n} dim={dim} /></td>
            </tr>
          );
        })}
      </tbody>
      </table>
    </div>
  );
}

export function MountsPage() {
  const q = useMounts();
  const [problemsOnly, setProblemsOnly] = useState(false);
  const d = q.data;
  const cols = storagesOf(d);
  const nodes = nodesOf(d);
  const summary = summaryOf(d, nodes, cols);
  const problemRows = nodes.filter((n) => nodeHasProblem(n, cols));
  const rows = problemsOnly ? problemRows : nodes;
  const covering = typeof d?.artifact_base?.covering_storage === "string" ? d.artifact_base.covering_storage : null;
  const basePath = d?.artifact_base?.path ?? d?.artifact_base?.uri ?? null;
  const now = q.dataUpdatedAt || Date.now();

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">마운트 현황</h1>
      <p className="text-sm text-muted break-keep">
        노드마다 등록 스토리지와 아티팩트 경로가 마운트돼 있는지 봅니다(노드 에이전트 60초 주기 보고 · 화면 10초마다 갱신). 스토리지가
        빠진 노드엔 그 스토리지 작업이 배치되지 않고, 아티팩트 경로가 빠진 노드에 뜬 API·잡 파드는 작업 기록을 읽거나 쓰지 못합니다.
      </p>
      {q.isLoading ? (
        <Card><div className="space-y-2.5"><span className="sr-only">불러오는 중…</span>
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}</div></Card>
      ) : q.isError && d === undefined ? (
        <Card><p className="text-sm text-bad">{(q.error as ApiError).message}</p></Card>
      ) : (<>
        <SummaryLine {...summary} />
        <Card className="space-y-3">
          {nodes.length === 0 ? (
            <Empty title="보고한 노드가 없습니다" hint="노드 에이전트(DaemonSet dms-agent)가 떠 있는지 확인하세요." />
          ) : (<>
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <button type="button" aria-pressed={problemsOnly} onClick={() => setProblemsOnly((v) => !v)}
                      className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs text-ink
                                 hover:bg-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent
                                 aria-pressed:border-accent aria-pressed:bg-infobg aria-pressed:text-accent sm:h-7">
                <ListFilter aria-hidden className="h-3.5 w-3.5" />
                문제만 보기
                <span className="tabular-nums text-muted">{problemRows.length}</span>
              </button>
              <Legend />
            </div>
            {cols.length === 0 && (
              <p className="text-xs text-muted">
                등록된 스토리지가 없습니다 — <Link to="/admin/storages" className="text-accent hover:underline">스토리지</Link>{" "}
                화면에서 등록하면 열이 생깁니다.
              </p>
            )}
            {rows.length === 0 ? (
              <Empty title="문제가 있는 노드가 없습니다"
                     action={<button type="button" onClick={() => setProblemsOnly(false)}
                                     className="mt-1 rounded text-sm text-accent hover:underline focus-visible:outline-none
                                                focus-visible:ring-2 focus-visible:ring-accent">전체 보기</button>} />
            ) : (
              <MountsTable rows={rows} cols={cols} covering={covering} basePath={basePath} now={now} />
            )}
            {q.isError && (
              <p className="text-xs text-bad">{`새로 고치지 못했습니다 — ${(q.error as ApiError).message} (마지막으로 받은 값을 표시합니다)`}</p>
            )}
            <div className="space-y-1 text-xs text-muted break-keep">
              <p>칸 = 에이전트가 노드에서 본 마운트(경로 존재 · 마운트포인트 · 읽기 · 쓰기). 보고 없음 = 그 노드의 보고에 이 스토리지가
                없음(에이전트 없음 · 옛 에이전트 · 마운트 경로를 바꾼 직후). 보고가 끊긴 노드는 마지막 보고 기준이라 흐리게 표시합니다.</p>
              <p>아티팩트 경로 = 그 경로를 덮는 스토리지의 마운트와 에이전트의 경로 확인, 그리고 그 노드에 뜬 API·컨트롤러의 자체 점검 —
                자세한 점검은 <Link to="/admin/artifact-base" className="text-accent hover:underline">아티팩트 경로</Link> 화면.</p>
            </div>
          </>)}
        </Card>
      </>)}
    </section>
  );
}
