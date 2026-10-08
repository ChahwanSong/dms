import type { ReactNode } from "react";
import { ShieldAlert } from "lucide-react";
import { Card } from "../../components/ui/Card";
import type { Line, LineTone, NodeGroup, OptionsView, RequestSpec } from "./requestSpec";

// 「요청 내용」 카드(2026-10-08) -- 무엇을 요청했나(대상·옵션) / 누구 권한으로·어떤 자원으로 돌았나(실행 권한·자원)를
// 한 장에 펼친다(접지 않는다 -- 행이 적다). 판정·문구는 전부 requestSpec(순수)에 있고 여기는 그리기만 한다.
//
// 구조 계약(테스트가 기댄다): 행은 dt·dd **형제**(dt.nextElementSibling = dd, div 래퍼는 WHATWG 허용). 대상·절대경로 dd 는
// 텍스트 노드 하나, 「실행 도구」의 도구 요약은 자기만의 span(정확 일치), 「보조 그룹(gid)」 dd 는 값 + 주의문 span 뿐.
// 의미 있는 글자는 ink/70 이상(흰 카드 4.94:1) -- text-muted(3.54:1)는 쓰지 않는다. 버튼·표·aside 없음.
//
// 두 묶음 2열은 xl(1280)부터다 -- 사이드바(240px)가 폭을 먹어 lg(1024)에서 2열이면 값 칸이 약 200px 로 768 의 1열보다
// 좁아져 경로가 4~5줄로 갈렸다(2026-10-08 실측). 로딩 골격(RequestDetail LoadingView)도 같은 중단점이다.

const CHIP = "rounded bg-panel px-1.5 py-0.5 font-mono text-xs text-ink [overflow-wrap:anywhere]";
// 경로 칸: sync 는 [출발, 도착] 조각 사이에서만 줄을 바꾼다 -- 합친 문자열에서 「 → 」를 찾지 않고 구조로 가른다(경로
// 이름에 든 「 → 」에서 갈리면 출발·도착을 잘못 말한다). 경로 안의 줄바꿈 문자는 「⏎」로 보인다(whitespace-pre-line 이
// 그것을 줄바꿈으로 그리면 경로 하나가 두 경로처럼 읽힌다). 화살표 뒤는 줄바꿈 없는 공백(U+00A0) -- 「→」만 한 줄에 남지
// 않는다. dd 는 텍스트 노드 하나이고 공백 정규화(\s 는 U+00A0·\n 포함) 후 글자는 pathSummary·absSummary 그대로다.
// 모르는 조각(「—:—」)뿐이면 두 줄로 나누지 않는다(정보 없는 대시 두 줄).
const split = (parts: string[]) => parts.length > 1 && parts.some((p) => p !== "—:—");
const showPath = (parts: string[]) =>
  parts.map((s) => s.replace(/\r\n|[\r\n]/g, "⏎")).join(split(parts) ? "\n→ " : " → ");
const pathClass = (parts: string[]) => (split(parts) ? "whitespace-pre-line font-mono text-xs" : "font-mono text-xs");
// 요청 상세 전용 문구 -- 제출 폼의 OPEN_NOATIME_NON_ROOT(「root 실행에서만 적용됩니다」)는 앞으로의 안내라, 이미 비 root 로
// 실린 기록에는 맞지 않는다(execution_manifests 는 실행 신원과 무관하게 --open-noatime 을 렌더한다).
const NOATIME_DETAIL =
  "open_noatime 이 비 root 실행에 실렸습니다 — 실행 신원 소유가 아닌 파일은 O_NOATIME 으로 열 수 없어(EPERM) 복사가 실패할 수 있습니다.";
// head 는 첫 「 — 」 앞(lead)만 굵게. bad = 전부 빨강(root 로 계획됨), badLead = lead 만 빨강(「요청됨 — 실행되지
// 않았습니다」의 안심 절반까지 경보색으로 칠하지 않는다). [lead, rest]
const HEAD_TONE: Record<LineTone, [string, string]> = {
  bad: ["font-medium text-bad", "text-bad"],
  badLead: ["font-medium text-bad", "text-ink/70"],
  attn: ["font-medium text-attn", "text-attn"],
  sub: ["font-medium text-ink/70", "text-ink/70"],
  plain: ["font-medium", ""],
};
const SUB_TONE: Record<LineTone, string> = {
  bad: "text-bad", badLead: "text-bad", attn: "text-attn", sub: "text-ink/70", plain: "text-ink",
};

function SpecGroup({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <h3 className="border-b border-line pb-1.5 text-xs font-semibold text-ink/70">{title}</h3>
      <dl className="mt-2.5 space-y-2.5 text-sm">{children}</dl>
    </div>
  );
}

const DD_COLOR = (ddClass: string) => (/(^|\s)text-(ink|bad|attn)/.test(ddClass) ? "" : "text-ink");

function SpecRow({ label, ddClass = "", children }: { label: string; ddClass?: string; children: ReactNode }) {
  return (
    // items-baseline: 작은 고정폭 글자(경로) dd 와 dt 가 같은 줄에 앉는다.
    // 라벨 칸 6rem: 가장 긴 dt(「보조 그룹(gid)」 약 85px)가 들어가고 값 칸이 넓어진다(경로가 이름 중간에서 덜 갈린다).
    // 글자색은 ddClass 가 정하면 그것만 -- 같은 요소에 text-ink 와 text-ink/70 을 함께 두면 CSS 순서에 기대게 된다.
    <div className="min-w-0 sm:grid sm:grid-cols-[6rem_minmax(0,1fr)] sm:items-baseline sm:gap-x-3">
      <dt className="text-ink/70">{label}</dt>
      <dd className={`mt-0.5 min-w-0 break-keep [overflow-wrap:anywhere] sm:mt-0 ${DD_COLOR(ddClass)} ${ddClass}`}>{children}</dd>
    </div>
  );
}

function Head({ line }: { line: Line }) {
  const i = line.text.indexOf(" — ");
  const [lead, rest] = i < 0 ? [line.text, ""] : [line.text.slice(0, i), line.text.slice(i)];
  const [leadCls, restCls] = HEAD_TONE[line.tone];
  return (
    <span>
      <span className={leadCls}>{lead}</span>
      {rest !== "" && <span className={restCls}>{rest}</span>}
    </span>
  );
}

/** 실행 노드 줄 -- 이름 하나는 줄바꿈 없이(「dms-」/「w5」로 갈리지 않게). 글자는 requestSpec.nodesText 와 같다. */
function Nodes({ groups }: { groups: NodeGroup[] }) {
  return (
    <span className="mt-0.5 block text-xs text-ink/70">
      {"실행 노드 "}
      {groups.map((g, gi) => (
        <span key={gi}>
          {gi > 0 && " · "}
          {g.side !== null && `${g.side} `}
          {g.names.map((n, ni) => (
            <span key={ni}>{ni > 0 && ", "}<span className="whitespace-nowrap">{n}</span></span>
          ))}
          {g.more > 0 && ` 외 ${g.more}대`}
        </span>
      ))}
    </span>
  );
}

function OptionsCell({ v }: { v: OptionsView }) {
  if (v.state === "missing") return <>기록 없음</>;
  if (v.state === "malformed") return <>옵션 기록을 읽을 수 없습니다</>;
  return (
    <>
      {v.set.length > 0 && (
        // 설명이 붙은 옵션은 한 줄씩(basis-full), 설명 없는 칩은 한 줄에 이어 둔다 -- 칩마다 한 줄이면 칸이 쓸데없이 길다.
        <ul className="flex flex-wrap items-baseline gap-x-1.5 gap-y-1">
          {v.set.map((o) => (
            <li key={o.token} className={o.note !== null ? "basis-full" : undefined}>
              <code className={CHIP}>{o.token}</code>
              {o.note !== null && <span className="text-ink/70"> {o.note}</span>}
            </li>
          ))}
        </ul>
      )}
      {v.set.length === 0 && v.unknown.length === 0 && <p>지정한 옵션 없음</p>}
      {v.defaults.length > 0 && (
        <p className="mt-1 text-xs text-ink/70">기본값: <span className="font-mono">{v.defaults.join(" · ")}</span></p>
      )}
      {v.unknown.length > 0 && (
        <p className="mt-1 text-xs text-ink/70">그 밖에 기록된 값: <span className="break-all font-mono">{v.unknown.join(" · ")}</span></p>
      )}
      {v.noatimeWarn && <p className="mt-1 text-xs text-attn">{NOATIME_DETAIL}</p>}
    </>
  );
}

/** 머리말의 root 배지(h1 밖). root 의 어휘·색은 포탈 전체와 같다(「root(특권)」, 빨강). 모서리는 rounded -- 상태 pill
    (rounded-full)과 모양이 달라 상태로 읽히지 않는다. 글자 + 방패 + 빨강 세 겹이라 색에만 기대지 않는다. */
export function RootBadge({ kind }: { kind: "run" | "requested" }) {
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded border border-bad/30 bg-badbg px-1.5 py-0.5 text-xs font-semibold text-bad">
      <ShieldAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />
      {kind === "run" ? "root(특권) 실행" : "root(특권) 요청"}
    </span>
  );
}

export function RequestSpecCard({ spec }: { spec: RequestSpec }) {
  const { privilege: pv, runAs, tool } = spec;
  return (
    <section aria-labelledby="spec-h">
      <Card>
        <h2 id="spec-h" className="text-sm font-semibold">요청 내용</h2>
        <div className="mt-3 grid gap-y-5 xl:grid-cols-2 xl:gap-x-10">
          <SpecGroup title="대상·옵션">
            <SpecRow label="대상" ddClass={pathClass(spec.target)}>{showPath(spec.target)}</SpecRow>
            {spec.abs !== null && (
              <SpecRow label="절대경로" ddClass={`${pathClass(spec.abs)} text-ink/70`}>{showPath(spec.abs)}</SpecRow>
            )}
            <SpecRow label="옵션"><OptionsCell v={spec.options} /></SpecRow>
            <SpecRow label="우선순위">{spec.priority}</SpecRow>
            {spec.nodeRequest !== null && <SpecRow label="노드 지정">{spec.nodeRequest}</SpecRow>}
          </SpecGroup>
          <SpecGroup title="실행 권한·자원">
            <SpecRow label="실행 권한">
              <Head line={pv.head} />
              {pv.subs.map((s, i) => (
                <span key={i} className={`mt-0.5 block text-xs ${SUB_TONE[s.tone]}`}>{s.text}</span>
              ))}
            </SpecRow>
            <SpecRow label="실행 신원">
              <span className="font-medium">{runAs.name}</span>{runAs.relation}
              {runAs.tail !== "" && <span className="text-ink/70">{runAs.tail}</span>}
            </SpecRow>
            {spec.groups !== null && (
              <SpecRow label="보조 그룹(gid)">
                {spec.groups.text}
                {spec.groups.caveats.map((c) => <span key={c} className="block text-xs text-ink/70">{c}</span>)}
              </SpecRow>
            )}
            {spec.ownership !== null && <SpecRow label="목적지 소유">{spec.ownership}</SpecRow>}
            {tool !== null && (
              <SpecRow label="실행 도구">
                {tool.summary !== null && <span>{tool.summary}</span>}
                {tool.summary !== null && tool.tail !== null && <span className="text-ink/70">{` · ${tool.tail}`}</span>}
                {tool.fallback !== null && <span className="text-ink/70">{tool.fallback}</span>}
                {tool.nodes !== null && <Nodes groups={tool.nodes} />}
              </SpecRow>
            )}
          </SpecGroup>
        </div>
        {spec.multiJob && (
          <p className="mt-4 text-xs text-ink/70 break-keep">
            작업이 여러 개입니다 — 실행 권한·신원·보조 그룹은 가장 최근 작업 기준이고, 실행 도구는 각 작업 카드에 있습니다.
          </p>
        )}
      </Card>
    </section>
  );
}
