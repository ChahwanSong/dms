import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { KeyboardEvent, ReactNode, RefObject } from "react";
import { Link, useSearchParams } from "react-router-dom";
import {
  AlertTriangle, CheckCircle2, CircleAlert, Columns3, Database, FolderCog, ListFilter, Search, Server,
} from "lucide-react";
import { Card } from "../../components/ui/Card";
import { Skeleton } from "../../components/ui/Skeleton";
import { ApiError, reasonText } from "../../lib/api";
import type { MountBaseCell, MountCell, MountNode } from "../../lib/types";
import { ageText } from "../nodes/NodesList";
import { useMounts } from "./useMounts";
import {
  BASE_LABEL, BASE_PATH_LINE, CELL_LABEL, baseChecksFailed, baseDetail, baseHeadOverridden, baseOf, baseProblem, cellDetail,
  cellOf, cellProblemIn, columnHasProblem, columnNameLines, filterColumns, isStale, nodeHasProblem, nodesOf, pathTail, peersOf,
  reasonShort, rolesOf, storagesOf, summaryOf, tri, type MountPeers, type StorageCol,
} from "./mountView";

// 마운트 현황(2026-11-23 사용자 요청 「스토리지 하위 마운트 현황 메뉴 -- 노드 × 스토리지 마운트 현황 표」). 계기: dms-api 가 공유
// 스토리지 미마운트 노드에 떠 아티팩트를 못 읽은 사고 -- 노드별 마운트를 한눈에 볼 자리가 없었다(노드 화면은 노드마다 상세 창을
// 열어야 했다). 위에서 아래로: 요약 한 줄(노드·보고 끊김·스토리지·문제 칸) → 거르기 줄(「문제만 보기」 행 · 「문제 있는 스토리지만」
// 열 · 스토리지 찾기) + 범례 → 표(첫 열 노드 고정, 스토리지 열들, 마지막 「아티팩트 경로」 열) → 각주. 판정은 서버(routes_mounts --
// DB 만, 칸·아티팩트 경로의 problem)가 하고 화면은 그리기만 한다 -- problem 이 없는 옛 서버만 같은 규칙으로 로컬 판정(mountView).
//
// 표 압축(2026-11-27 dms-k8s 7a172d1d·b8d4395f 이식 -- 「스토리지가 100개가 될 수도 있어」): 스토리지 열은 6.5rem 고정(머리·칸 안쪽
// w-24 상자 -- auto 표에서 퍼센트 max-w·truncate 는 열을 못 묶는다), 표는 내용 폭(w-max)·래퍼는 w-fit max-w-full 이라 넓은 화면에서
// 열이 늘어나지 않는다. 머리는 이름 두 줄(h-8 아래 붙임) · 경로 끝 조각 한 줄 · 꼬리표 한 줄의 **고정 높이 블록**이라 아티팩트 경로
// 머리(제목 · 경로 · 덮는 스토리지)와 이름 줄이 같은 자리에 온다. 래퍼 하나가 가로·세로 스크롤을 다 맡고(높이 = 화면에 남은 높이 --
// 노드가 많아도 가로 스크롤바가 첫 화면 안) 머리 줄은 위에(sticky top), 노드 칸은 왼쪽에(sticky left) 고정된다. 래퍼는 초점을 받아
// (role=region, tabIndex 0) 화살표로 밀고, Home/End 는 가로 처음·끝(래퍼 자신에 초점이 있을 때만).
//
// 고정 칸 쌓임: 고정 칸끼리 겹치므로 z 를 칸마다 하나씩 준다(모서리 3 > 머리 2 > 노드 칸 1 -- 안 주면 DOM 순서상 뒤인 노드 칸이 위로
// 밀린 머리 위에 그려진다). 래퍼의 isolate 가 그 z 를 래퍼 안에 가둬 body 끝 포털(Dialog 덮개 등)을 뚫지 않는다(사용량 TargetTable 의
// z-index 사고 유형 -- dms-k8s 와 같은 해법). 고정 칸 바탕은 불투명(bg-surface)이고 스크롤되는 칸에 쌓임 맥락(opacity·transform)을
// 만들지 않는다. 그래서 「보고 끊김」 행은 CSS opacity 가 아니라 흐린 색(점은 색 알파, 글자는 muted)으로 흐리게 그린다. 표는
// border-collapse 라 고정 머리의 아래 선은 테두리가 아니라 안쪽 그림자(inset)로 그린다(테두리는 고정 칸을 따라가지 않는다).
//
// 375 폭: 표는 래퍼 안에서만 넘치고(문서 가로 넘침 0), 노드 칸이 고정이라 오른쪽 칸을 넘겨도 어느 노드의 값인지 보인다.
//
// 보기 상태는 URL 에 남긴다(공유·새로고침에 유지, replace): ?only=problems(문제 있는 노드만) · ?cols=problems(문제 있는 스토리지 열만)
// · ?q=(스토리지 이름·경로 부분 일치 -- 칸은 바로 거르고 주소는 300ms 뒤에 쓴다). 열 거르기는 표의 칸만 줄이고 요약 줄·「문제만
// 보기」 수는 숨긴 스토리지까지 센 전체 기준이다(안내 줄이 말한다).

const URL_DEBOUNCE_MS = 300;

// 머리 칸. 아래 선은 안쪽 그림자(머리 주석), 노드 칸·모서리의 오른쪽 선은 옆으로 밀렸을 때만(넓은 화면에선 칸 사이 선이 없다).
const HEAD = "sticky top-0 z-[2] bg-surface pb-1.5 pt-2 text-left align-top text-xs font-medium text-muted "
  + "shadow-[inset_0_-1px_0_theme(colors.line)]";
const CORNER_LINE = "shadow-[inset_0_-1px_0_theme(colors.line)]";
const CORNER_LINE_EDGE = "shadow-[inset_0_-1px_0_theme(colors.line),1px_0_0_theme(colors.line)]";
const EDGE = "shadow-[1px_0_0_theme(colors.line)]";

// 상태 점(색 + 모양 -- 보고 없음은 속 빈 원, 경로 없음은 짧은 가로 막대라 색을 못 가려도 구별된다). 흐림은 색 알파로만(머리 주석).
const DOT: Record<MountCell["status"], { on: string; dim: string; text: string; shape?: string }> = {
  ready: { on: "bg-ok", dim: "bg-ok/40", text: "text-ink" },
  readonly: { on: "bg-attn", dim: "bg-attn/40", text: "text-attn" },
  missing: { on: "bg-bad", dim: "bg-bad/40", text: "text-bad" },
  absent: { on: "bg-muted/70", dim: "bg-muted/40", text: "text-muted", shape: "h-0.5 w-2" },
  unreported: { on: "border border-muted bg-surface", dim: "border border-muted/50 bg-surface", text: "text-muted" },
  disabled: { on: "bg-line", dim: "bg-line", text: "text-muted" },
};
// 문제 칸의 옅은 바탕(색 알파 -- 쌓임 맥락 없음). 보고가 살아 있는 행의 **문제 칸**에서만(끊긴 보고의 칸은 옛 사실이고, 쓰기
// 가능으로 붙인 노드가 없는 읽기 전용은 의도된 구성이다). 경로 없음은 그 스토리지를 마운트한 노드가 하나도 없을 때만 문제다.
const TINT: Partial<Record<MountCell["status"], string>> = {
  missing: "bg-badbg/60", readonly: "bg-attnbg/60", absent: "bg-badbg/60",
};

function Dot({ status, dim }: { status: MountCell["status"]; dim: boolean }) {
  const s = DOT[status];
  return <span aria-hidden className={`inline-block ${s.shape ?? "h-2 w-2"} shrink-0 rounded-full ${dim ? s.dim : s.on}`} />;
}

// 상태 글자 + 점(첫 줄), 짧은 사유(둘째 줄 -- 고정 폭 칸에서 「없음 · 마운트 아님」이 한 줄에 안 들어간다, 넘치면 말줄임), 긴 설명은
// title 과 낭독 문장(sr-only). 이 span 에 relative 를 주지 않는다 -- 위치 지정 요소는 스크롤되는 칸에서 쌓임 순서를 흔든다. sr-only
// (absolute)의 기준 상자는 표 래퍼(relative overflow-auto)라 문서 폭을 넓히지 않고, 1px 로 잘려 보이지도 않는다. 문제가 아닌 읽기
// 전용은 주의 색 글자를 쓰지 않는다(점은 상태 그대로). 문제인 경로 없음(어느 노드에도 마운트 안 됨)은 빨간 글자.
function StatusMark({ status, label, short, detail, dim, problem, className = "" }: {
  status: MountCell["status"]; label: string; short: string | null; detail: string; dim: boolean; problem: boolean;
  className?: string;
}) {
  const tone = dim ? "text-muted" : status === "readonly" && !problem ? "text-ink"
    : status === "absent" && problem ? "text-bad" : DOT[status].text;
  return (
    <span className={`flex min-w-0 flex-col text-xs ${className}`} title={detail}>
      <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
        <Dot status={status} dim={dim} />
        <span className={tone}>{label}</span>
      </span>
      {short !== null && (
        <span className={`truncate pl-3.5 text-[11px] leading-4 ${dim ? "text-muted" : "text-bad/80"}`}>{short}</span>
      )}
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
function Tag({ tone, title, children }: { tone: keyof typeof TAG; title?: string; children: ReactNode }) {
  return (
    <span title={title} className={`inline-flex items-center whitespace-nowrap rounded px-1.5 text-[11px] leading-[1.125rem] ${TAG[tone]}`}>
      {children}
    </span>
  );
}

function NodeCell({ n, now }: { n: MountNode; now: number }) {
  const roles = rolesOf(n);
  const stale = isStale(n);
  const tags: ReactNode[] = [];
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

function BaseCell({ n, b, dim, problem, baseWritable }: {
  n: MountNode; b: MountBaseCell | null; dim: boolean; problem: boolean; baseWritable: boolean;
}) {
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
    <div className="flex w-48 flex-col gap-0.5">
      <StatusMark status={shown} label={shown !== b.status ? "점검 실패" : BASE_LABEL[b.status]} dim={dim} problem={problem}
                  short={b.status === "missing" ? reasonShort(b.reason) : null} detail={baseDetail(n, b, problem, baseWritable)} />
      {(api.length > 0 || ctrl.length > 0) && (
        <span className="flex flex-wrap gap-x-2 gap-y-0.5 pl-3.5 text-[11px]">
          <CheckLine label="API" results={api} />
          <CheckLine label="컨트롤러" results={ctrl} />
        </span>
      )}
    </div>
  );
}

// 아티팩트 경로 칸 바탕: 문제 칸(baseProblem)만 -- 점검 실패는 없음과 같은 빨강, 쓰기 불가만이면 주의 색.
function baseTint(b: MountBaseCell | null, problem: boolean): string {
  if (b === null || !problem) return "";
  if (b.status === "missing" || baseChecksFailed(b)) return TINT.missing ?? "";
  return b.status === "readonly" ? TINT.readonly ?? "" : "";
}

function Legend() {
  const items: [MountCell["status"], string][] = [
    ["ready", "마운트됨"], ["readonly", "읽기 전용"], ["missing", "없음(+사유)"], ["absent", "경로 없음(제공 안 함)"],
    ["unreported", "보고 없음"], ["disabled", "비활성"],
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

// 래퍼 최대 높이 = 화면에 남은 높이(dms-k8s 7a172d1d useFitMaxHeight 이식): 노드가 많아도 가로 스크롤바와 표 바닥이 첫 화면 안에
// 온다(예전엔 표가 문서를 따라 길어져 가로 스크롤바가 맨 아래 노드 뒤에 있었다). 하한 min 이라 좁은 화면에서도 몇 행은 보인다.
// 문서 스크롤 기준(window -- AppShell main 은 overflow 를 두지 않는다)으로 재고, 창 크기·문서 높이가 바뀌면 다시 잰다.
function useFitMaxHeight(ref: RefObject<HTMLElement>, min = 352, gap = 16): number | null {
  const [h, setH] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (el === null) return;
    const measure = () => {
      const top = el.getBoundingClientRect().top + window.scrollY;
      const next = Math.max(min, Math.floor(window.innerHeight - top - gap));
      setH((prev) => (prev === next ? prev : next));
    };
    measure();
    window.addEventListener("resize", measure);
    const ro = typeof ResizeObserver === "function" ? new ResizeObserver(measure) : null;
    ro?.observe(document.body);
    return () => {
      window.removeEventListener("resize", measure);
      ro?.disconnect();
    };
  }, [ref, min, gap]);
  return h;
}

function SummaryLine({ nodeCount, freshCount, staleCount, storageCount, problemCells, problemNodes, anyAbsent }:
  ReturnType<typeof summaryOf> & { anyAbsent: boolean }) {
  const n = (x: number) => x.toLocaleString("ko-KR");
  const item = "inline-flex items-center gap-1.5";
  const ico = "h-4 w-4 shrink-0";
  // 경로 없음(제공 안 함) 칸이 있으면 「등록된 스토리지를 모두」가 아니다 -- 각 노드가 제공하는 스토리지 기준이라고 말한다.
  const what = anyAbsent ? "제공하는" : "등록된";
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
          {staleCount > 0 ? `보고 중인 노드는 모두 ${what} 스토리지를 정상 마운트하고 있습니다`
            : `모든 노드가 ${what} 스토리지를 정상 마운트하고 있습니다`}
        </span>
      )}
    </div>
  );
}

function Empty({ title, hint, action }: { title: string; hint?: ReactNode; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-line px-4 py-10 text-center">
      <span aria-hidden className="grid h-10 w-10 place-items-center rounded-full bg-panel text-muted"><Server className="h-5 w-5" /></span>
      <p className="text-sm font-medium text-ink break-keep">{title}</p>
      {hint && <p className="max-w-md text-xs text-muted break-keep">{hint}</p>}
      {action}
    </div>
  );
}

// 스토리지 열 머리: 이름(두 줄 고정 높이, 아래 붙임) · 경로(끝 조각) · 꼬리표(덮는 스토리지 아이콘 · 비활성) -- 세 블록 모두 고정
// 높이라 모든 열의 이름 줄·경로 줄이 같은 자리다. 보이는 조각이 원문과 다르면 조각은 aria-hidden, 낭독은 sr-only 전체 한 번.
function StorageHead({ c, covering }: { c: StorageCol; covering: string | null }) {
  const lines = columnNameLines(c.storage_name);
  const tail = pathTail(c.mount_path);
  const tone = c.enabled ? "text-ink" : "text-muted";
  return (
    <th scope="col" className={`${HEAD} w-[6.5rem] min-w-[6.5rem] px-1`}>
      <div className="w-24">
        <div title={c.storage_name} className="flex h-8 flex-col justify-end font-mono text-[12px] font-semibold leading-4">
          {lines.length === 1 ? <span className={`truncate ${tone}`}>{c.storage_name}</span> : (<>
            <span className="sr-only">{c.storage_name}</span>
            {lines.map((line, k) => <span key={k} aria-hidden className={`truncate ${tone}`}>{line}</span>)}
          </>)}
        </div>
        <div title={c.mount_path || undefined} className="h-4 truncate font-mono text-[11px] font-normal leading-4 text-muted">
          {tail === null ? (c.mount_path || "—") : (
            <><span className="sr-only">{c.mount_path}</span><span aria-hidden>{`…${tail}`}</span></>
          )}
        </div>
        <div className="flex h-4 items-center gap-1 text-[11px] font-normal leading-4 text-muted">
          {covering === c.storage_name && (
            <span title="아티팩트 경로가 이 스토리지 아래에 있습니다" className="inline-flex">
              <FolderCog aria-hidden className="h-3.5 w-3.5" /><span className="sr-only">(아티팩트 경로를 덮는 스토리지)</span>
            </span>
          )}
          {!c.enabled && <span>비활성</span>}
        </div>
      </div>
    </th>
  );
}

function MountsTable({ rows, cols, covering, basePath, now, peers }: {
  rows: MountNode[]; cols: StorageCol[]; covering: string | null; basePath: string | null; now: number; peers: MountPeers;
}) {
  const wrap = useScrolled();
  const maxHeight = useFitMaxHeight(wrap.ref);
  const edge = wrap.scrolled ? EDGE : "";
  const baseTailText = basePath === null ? null : pathTail(basePath, BASE_PATH_LINE);
  // Home/End = 가로 처음·끝(dms-k8s b8d4395f: 화살표 한 번에 40px 라 100열 끝까지 수백 번이었고 Home/End 는 페이지를 세로로
  // 움직였다). 래퍼 자신에 초점이 있을 때만 -- 안쪽 노드 링크의 키는 건드리지 않는다.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget || e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    if (e.key === "Home") { e.currentTarget.scrollLeft = 0; e.preventDefault(); }
    else if (e.key === "End") { e.currentTarget.scrollLeft = e.currentTarget.scrollWidth; e.preventDefault(); }
  };
  return (
    // 공용 Table 대신 래퍼를 직접 쓴다: 가로·세로 스크롤(고정 머리)과 초점·키가 필요하고, 래퍼가 relative 여야 칸 안 sr-only 의
    // 기준 상자가 스크롤 영역이 된다(StatusMark).
    <div ref={wrap.ref} role="region" tabIndex={0} onKeyDown={onKeyDown}
         aria-label="노드별 마운트 표 — 가로·세로로 스크롤(Home·End 로 가로 처음·끝)"
         className="relative isolate w-fit max-w-full overflow-auto overscroll-x-contain rounded focus-visible:outline-none
                    focus-visible:ring-2 focus-visible:ring-accent"
         style={maxHeight === null ? undefined : { maxHeight }}>
      <table className="w-max text-left text-sm">
      <thead><tr>
        <th scope="col" className={`sticky left-0 top-0 z-[3] bg-surface pb-1.5 pl-2 pr-3 text-left align-bottom text-xs font-medium
                                    text-muted ${wrap.scrolled ? CORNER_LINE_EDGE : CORNER_LINE}`}>노드</th>
        {cols.map((c) => <StorageHead key={c.storage_name} c={c} covering={covering} />)}
        <th scope="col" className={`${HEAD} px-3`}>
          <div className="w-48">
            <div className="flex h-8 flex-col justify-end text-xs font-semibold leading-4 text-ink">아티팩트 경로</div>
            <div title={basePath ?? undefined} className="h-4 truncate font-mono text-[11px] font-normal leading-4 text-muted">
              {baseTailText === null ? (basePath ?? "—") : (
                <><span className="sr-only">{basePath}</span><span aria-hidden>{`…${baseTailText}`}</span></>
              )}
            </div>
            <div className="h-4 truncate text-[11px] font-normal leading-4 text-muted">
              {covering !== null ? `덮는 스토리지 ${covering}` : "덮는 등록 스토리지 없음"}</div>
          </div>
        </th>
      </tr></thead>
      <tbody>
        {rows.map((n, i) => {
          const dim = isStale(n);
          const b = baseOf(n);
          const bp = baseProblem(b, peers.baseWritable);
          return (
            <tr key={n.node_name} className={i === 0 ? "" : "border-t border-line"}>
              <td className={`sticky left-0 z-[1] bg-surface py-2 pl-2 pr-3 align-top ${edge}`}><NodeCell n={n} now={now} /></td>
              {cols.map((c) => {
                const cell = cellOf(n, c);
                // 끊긴 보고의 칸은 판정하지 않는다(옛 사실 -- 서버도 problem=false).
                const problem = n.fresh === true && cellProblemIn(n, c, peers);
                return (
                  <td key={c.storage_name} className={`px-1 py-2 align-top ${!dim && problem ? TINT[cell.status] ?? "" : ""}`}>
                    <StatusMark status={cell.status} label={CELL_LABEL[cell.status]} dim={dim} problem={problem} className="w-24"
                                short={cell.status === "missing" ? reasonShort(cell.reason) : null}
                                detail={cellDetail(c, cell, n, problem)} />
                  </td>
                );
              })}
              <td className={`px-3 py-2 align-top ${dim ? "" : baseTint(b, bp)}`}>
                <BaseCell n={n} b={b} dim={dim} problem={bp} baseWritable={peers.baseWritable} />
              </td>
            </tr>
          );
        })}
      </tbody>
      </table>
    </div>
  );
}

// 스토리지 찾기(?q=): 칸 입력은 바로 거르고(클라이언트 거르기라 서버를 묻지 않는다), 주소는 300ms 멈춘 뒤 replace 로 쓴다(타자마다
// 주소를 바꾸면 브라우저가 history 갱신을 막는다). last = 이 화면이 마지막으로 본(또는 쓴) 주소 값 -- 자기가 쓴 주소 변화로는 칸을
// 되돌리지 않고, 밖에서 주소가 바뀌면(사이드바 링크·뒤로 가기) 칸을 주소에 맞춘다.
function useColumnSearch(urlQ: string, write: (q: string | null) => void) {
  const [input, setInput] = useState(urlQ);
  const last = useRef(urlQ.trim());
  const writeRef = useRef(write);
  useEffect(() => { writeRef.current = write; });
  useEffect(() => {
    const u = urlQ.trim();
    if (u === last.current) return;
    last.current = u;
    setInput(urlQ);
  }, [urlQ]);
  const applied = input.trim();
  useEffect(() => {
    if (applied === last.current) return;
    const t = setTimeout(() => {
      last.current = applied;
      writeRef.current(applied === "" ? null : applied);
    }, URL_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [applied]);
  const reset = () => { last.current = ""; setInput(""); };
  return { input, setInput, applied, reset };
}

const TOGGLE = "inline-flex h-8 items-center gap-1.5 rounded-lg border border-line px-2.5 text-xs text-ink hover:bg-panel "
  + "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent aria-pressed:border-accent aria-pressed:bg-infobg "
  + "aria-pressed:text-accent sm:h-7";

export function MountsPage() {
  const q = useMounts();
  const [params, setParams] = useSearchParams();
  const problemsOnly = params.get("only") === "problems";
  const problemCols = params.get("cols") === "problems";
  const setParam = useCallback((key: string, value: string | null) => setParams((p) => {
    const next = new URLSearchParams(p);
    if (value === null) next.delete(key); else next.set(key, value);
    return next;
  }, { replace: true }), [setParams]);
  const search = useColumnSearch(params.get("q") ?? "", (v) => setParam("q", v));
  const clearColumnFilter = () => {
    search.reset();
    setParams((p) => {
      const next = new URLSearchParams(p);
      next.delete("q");
      next.delete("cols");
      return next;
    }, { replace: true });
  };

  const d = q.data;
  const cols = useMemo(() => storagesOf(d), [d]);
  const nodes = useMemo(() => nodesOf(d), [d]);
  const peers = useMemo(() => peersOf(nodes, cols), [nodes, cols]);
  const summary = summaryOf(d, nodes, cols, peers);
  const problemRows = useMemo(() => nodes.filter((n) => nodeHasProblem(n, cols, peers)), [nodes, cols, peers]);
  const rows = problemsOnly ? problemRows : nodes;
  const problemColCount = useMemo(() => cols.filter((c) => columnHasProblem(c, nodes, peers)).length, [cols, nodes, peers]);
  const visibleCols = useMemo(() => filterColumns(cols, nodes, peers, { q: search.applied, problemsOnly: problemCols }),
                              [cols, nodes, peers, search.applied, problemCols]);
  const colFiltered = search.applied !== "" || problemCols;
  const anyAbsent = nodes.some((n) => n.fresh === true && cols.some((c) => cellOf(n, c).status === "absent"));
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
        <SummaryLine {...summary} anyAbsent={anyAbsent} />
        <Card className="space-y-3">
          {nodes.length === 0 ? (
            <Empty title="보고한 노드가 없습니다" hint="노드 에이전트(DaemonSet dms-agent)가 떠 있는지 확인하세요." />
          ) : (<>
            <div className="flex flex-wrap items-center gap-2">
              <button type="button" aria-pressed={problemsOnly} className={TOGGLE}
                      onClick={() => setParam("only", problemsOnly ? null : "problems")}>
                <ListFilter aria-hidden className="h-3.5 w-3.5" />
                문제만 보기
                <span className="tabular-nums text-muted">{problemRows.length}</span>
              </button>
              {cols.length > 0 && (<>
                <button type="button" aria-pressed={problemCols} className={TOGGLE}
                        onClick={() => setParam("cols", problemCols ? null : "problems")}>
                  <Columns3 aria-hidden className="h-3.5 w-3.5" />
                  문제 있는 스토리지만
                  <span className="tabular-nums text-muted">{problemColCount}</span>
                </button>
                <label className="relative min-w-0 basis-full sm:basis-56">
                  <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted" />
                  <input type="search" aria-label="스토리지 찾기" maxLength={128} placeholder="스토리지 이름·경로 찾기"
                         value={search.input} onChange={(e) => search.setInput(e.target.value)}
                         className="h-8 w-full rounded-lg border border-line bg-surface pl-8 pr-2 text-xs placeholder:text-muted
                                    focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 sm:h-7" />
                </label>
                {colFiltered && (
                  <span className="whitespace-nowrap text-xs tabular-nums text-muted">{`스토리지 ${visibleCols.length}/${cols.length}`}</span>
                )}
              </>)}
            </div>
            <Legend />
            {colFiltered && (
              // 열 거르기는 표의 칸만 줄인다 -- 요약 줄·「문제만 보기」 수는 숨긴 스토리지까지 센다(이 줄이 없으면 문제 행에 정상
              // 칸만 보이는 보기가 모순처럼 읽힌다 -- dms-k8s 7a172d1d 리뷰).
              <p className="text-xs text-muted break-keep">
                스토리지를 거른 보기입니다 — 표에는 고른 스토리지 열만 그리고, 요약 줄과 「문제만 보기」 수는 숨긴 스토리지까지 센 전체
                기준입니다.
              </p>
            )}
            {cols.length === 0 && (
              <p className="text-xs text-muted">
                등록된 스토리지가 없습니다 — <Link to="/admin/storages" className="text-accent hover:underline">스토리지</Link>{" "}
                화면에서 등록하면 열이 생깁니다.
              </p>
            )}
            {cols.length > 0 && colFiltered && visibleCols.length === 0 && (
              // 열이 0개여도 표는 노드·아티팩트 경로 열로 계속 그린다(아티팩트 경로 문제는 열 거르기와 무관하게 보인다).
              <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted break-keep">
                <span>
                  {"조건에 맞는 스토리지가 없습니다 — "
                    + [search.applied !== "" ? `「${search.applied}」` : null, problemCols ? "문제 있는 스토리지만" : null]
                      .filter((x) => x !== null).join(" · ")}
                </span>
                <button type="button" onClick={clearColumnFilter}
                        className="rounded text-accent hover:underline focus-visible:outline-none focus-visible:ring-2
                                   focus-visible:ring-accent">조건 지우기</button>
              </p>
            )}
            {rows.length === 0 ? (
              <Empty title="문제가 있는 노드가 없습니다"
                     action={<button type="button" onClick={() => setParam("only", null)}
                                     className="mt-1 rounded text-sm text-accent hover:underline focus-visible:outline-none
                                                focus-visible:ring-2 focus-visible:ring-accent">전체 보기</button>} />
            ) : (
              <MountsTable rows={rows} cols={visibleCols} covering={covering} basePath={basePath} now={now} peers={peers} />
            )}
            {q.isError && (
              <p className="text-xs text-bad">{`새로 고치지 못했습니다 — ${(q.error as ApiError).message} (마지막으로 받은 값을 표시합니다)`}</p>
            )}
            <div className="space-y-1 text-xs text-muted break-keep">
              <p>칸 = 에이전트가 노드에서 본 마운트(경로 존재 · 마운트포인트 · 읽기 · 쓰기). 경로 없음 = 그 노드에 스토리지 경로도
                호스트 마운트도 없음(그 스토리지를 제공하지 않는 노드 — 문제로 세지 않습니다. 단 그 스토리지를 마운트한 노드가 하나도
                없으면 문제. 호스트엔 마운트가 있는데 경로가 안 보이는 끊긴 마운트는 없음). 읽기 전용은 같은 스토리지를 쓰기 가능으로 붙인 노드가 있을
                때만 문제로 셉니다(모든 노드가 읽기 전용이면 의도된 구성). 보고 없음 = 그 노드의 보고에 이 스토리지가 없음(에이전트 없음 ·
                옛 에이전트 · 마운트 경로를 바꾼 직후). 보고가 끊긴 노드는 마지막 보고 기준이라 흐리게 표시합니다.</p>
              <p>아티팩트 경로 = 그 경로를 덮는 스토리지의 마운트와 에이전트의 경로 확인(새 에이전트는 덮는 마운트도 보고해, 그것이 노드
                루트 「/」면 공유 스토리지가 빠진 「로컬 디렉터리」로 짚습니다), 그리고 그 노드에 뜬 API·컨트롤러의 자체 점검 — 자세한
                점검은 <Link to="/admin/artifact-base" className="text-accent hover:underline">아티팩트 경로</Link> 화면.</p>
            </div>
          </>)}
        </Card>
      </>)}
    </section>
  );
}
