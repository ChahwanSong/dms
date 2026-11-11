import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { CircleCheck, CircleDashed, CircleX, LoaderCircle } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { InfoCard } from "../../components/ui/InfoCard";
import { Segmented } from "../../components/ui/Segmented";
import { StatusPill } from "../../components/ui/StatusPill";
import { reasonText } from "../../lib/api";
import { field } from "../jobs/formFields";
import { useNodes } from "../nodes/useNodes";
import { K8S_CLUSTERS_PATH, K8sHeader, clusterCardHref } from "./K8sTabs";
import {
  INTERVAL_PRESETS, MIN_INTERVAL_SECONDS, TRIGGER_LABEL, callerNodeState, clusterStatusCounts, countText,
  failureResolved, intervalText, isoAfter, pathUnverifiedSince, shortReasonText,
} from "./k8sTerms";
import { Ago, DefList, DefRow, Mono, SignupGuardNote, Stat, Tag } from "./parts";
import { serverOffsetMs, useCheckSecondary, useK8sOverview, useSaveK8sSettings } from "./useK8s";
import type {
  CallerClusterLink, CallerNode, ClusterSummary, K8sOverview as Overview, NodeRole,
} from "./types";

// k8s 연동 「개요」(설계 §7): 합계 · 호출 노드(primary·secondary — 외부 k8s API 를 부르는 DMS 노드) · 동기화 주기. 클러스터
// 카드·등록·지금 동기화는 「클러스터」 탭(ClustersPage — 2026-11-11 사용자 요청으로 분리)이고, 여기서는 「클러스터」 타일(상태별
// 개수)이 그 탭으로, 호출 노드 카드의 클러스터 이름(「마지막 실패」·「클러스터별 연결」)이 그 클러스터 카드로 가는 링크다 — 실패
// 사유 문장이 「클러스터 편집에서 토큰을 넣으세요」처럼 카드의 「편집」을 시키는데 카드가 다른 탭에 있어서다.
// 폴링은 useK8sOverview(평소 10초, 동기화 중 3초, 보조 연결 확인 대기 5초). 「n분 전」은 잎 부품(Ago)이 15초 틱으로 늙힌다.
// 호출 노드 카드는 「이미 해결된 실패」(흐리게·짧게)와 「지금 실패」(빨갛게·조치까지)를 가르고, 클러스터별 연결로 secondary 가
// 닿은 기록이 없는 클러스터(장애 넘김 경로 미확인)를 드러낸다(2026-10-28 사고 분석).
// 상태 문구는 셋을 가른다: null(모름 — 아직 동기화 전·목록 권한 없음) = 「—」·「동기화 전」, 0 = 「0」, 실패 = 사유 문장.

const errText = (e: unknown): string =>
  e instanceof Error && e.message ? e.message : "요청이 실패했습니다 — 잠시 뒤 다시 시도하세요.";

const CUSTOM = "custom";
type IntervalChoice = `${(typeof INTERVAL_PRESETS)[number]}` | typeof CUSTOM;
const INTERVAL_OPTIONS: { value: IntervalChoice; label: string }[] = [
  // 선택지는 분으로 읽는다(설계 §7 「1·5·15·60분」) -- 60분을 「1시간」으로 바꾸면 줄의 단위가 섞인다.
  ...INTERVAL_PRESETS.map((s) => ({ value: String(s) as IntervalChoice, label: `${s / 60}분` })),
  { value: CUSTOM, label: "직접 입력" },
];
const MAX_CUSTOM_MINUTES = 1440;

/** 직접 입력(분) → 초. 1분(60초) 이상 1440분 이하, 소수 허용(1.5분 = 90초). 틀리면 null. */
export function customIntervalSeconds(raw: string): number | null {
  if (raw.trim() === "") return null;
  const m = Number(raw);
  if (!Number.isFinite(m) || m * 60 < MIN_INTERVAL_SECONDS || m > MAX_CUSTOM_MINUTES) return null;
  return Math.round(m * 60);
}

function choiceFor(seconds: number): { choice: IntervalChoice; custom: string } {
  const preset = (INTERVAL_PRESETS as readonly number[]).includes(seconds);
  return preset ? { choice: String(seconds) as IntervalChoice, custom: "" }
    : { choice: CUSTOM, custom: String(Math.round((seconds / 60) * 100) / 100) };
}

/** 노드 선택지 — 개요의 호출 노드 목록 ∪ 노드 화면의 보고 노드(∪ 지금 지정값). 오타가 nodeSelector 처럼 새지 않게 자유 입력은
 *  두지 않는다(빌드 노드 선례). 보고 노드의 능력은 report.k8s_sync 선언으로 안다. */
interface NodeChoice { name: string; fresh: boolean | null; capable: boolean | null }
function nodeChoices(ov: Overview, reported: { node_name: string; fresh: boolean; report: unknown }[] | undefined,
                     current: (string | null)[]): NodeChoice[] {
  const map = new Map<string, NodeChoice>();
  for (const n of reported ?? []) {
    const rep = n.report !== null && typeof n.report === "object" ? (n.report as Record<string, unknown>) : {};
    map.set(n.node_name, { name: n.node_name, fresh: n.fresh,
                           capable: rep.k8s_sync !== undefined && rep.k8s_sync !== null });
  }
  for (const n of ov.nodes) map.set(n.node_name, { name: n.node_name, fresh: n.fresh, capable: n.capable });
  for (const c of current) if (c && !map.has(c)) map.set(c, { name: c, fresh: null, capable: null });
  return [...map.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function choiceLabel(c: NodeChoice): string {
  const notes = [c.fresh === null ? "보고 없음" : c.fresh ? null : "보고 지연",
                 c.capable === false ? "k8s 동기화 미지원 에이전트" : null].filter(Boolean);
  return notes.length > 0 ? `${c.name} — ${notes.join(" · ")}` : c.name;
}

function SettingsForm({ ov }: { ov: Overview }) {
  const save = useSaveK8sSettings();
  const nodesQ = useNodes();
  const s = ov.settings;
  const base = { primary: s.primary_node ?? "", secondary: s.secondary_node ?? "", ...choiceFor(s.interval_seconds) };
  const [draft, setDraft] = useState(base);
  const [baseline, setBaseline] = useState(base);
  const [saved, setSaved] = useState(false);
  // 서버 값이 바뀌면(저장·다른 관리자) 손대지 않은 칸만 따라간다 -- 편집 중인 칸은 남긴다(메일 설정 관례).
  const key = `${s.primary_node}|${s.secondary_node}|${s.interval_seconds}`;
  useEffect(() => {
    const next = { primary: s.primary_node ?? "", secondary: s.secondary_node ?? "", ...choiceFor(s.interval_seconds) };
    setDraft((d) => ({
      primary: d.primary === baseline.primary ? next.primary : d.primary,
      secondary: d.secondary === baseline.secondary ? next.secondary : d.secondary,
      choice: d.choice === baseline.choice && d.custom === baseline.custom ? next.choice : d.choice,
      custom: d.choice === baseline.choice && d.custom === baseline.custom ? next.custom : d.custom,
    }));
    setBaseline(next);
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  const choices = nodeChoices(ov, nodesQ.data, [s.primary_node, s.secondary_node]);
  const seconds = draft.choice === CUSTOM ? customIntervalSeconds(draft.custom) : Number(draft.choice);
  const sameNode = draft.primary !== "" && draft.primary === draft.secondary;
  const customBad = draft.choice === CUSTOM && seconds === null;
  const dirty = draft.primary !== baseline.primary || draft.secondary !== baseline.secondary
    || seconds !== s.interval_seconds;
  const blocked = !dirty || sameNode || customBad || save.isPending;
  const set = (patch: Partial<typeof draft>) => { setSaved(false); save.reset(); setDraft((d) => ({ ...d, ...patch })); };

  const submit = () => {
    if (blocked || seconds === null) return;
    save.mutate({ primary_node: draft.primary === "" ? null : draft.primary,
                  secondary_node: draft.secondary === "" ? null : draft.secondary, interval_seconds: seconds },
                { onSuccess: () => setSaved(true) });
  };

  const select = (role: NodeRole, label: string) => (
    <label className="block min-w-0 text-sm">{label}
      <select aria-label={label} className={field} value={role === "primary" ? draft.primary : draft.secondary}
              onChange={(e) => set(role === "primary" ? { primary: e.target.value } : { secondary: e.target.value })}>
        <option value="">지정 안 함</option>
        {choices.map((c) => <option key={c.name} value={c.name}>{choiceLabel(c)}</option>)}
      </select>
    </label>
  );

  return (
    <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); submit(); }}>
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        {select("primary", "주(primary) 호출 노드")}
        {select("secondary", "보조(secondary) 호출 노드")}
        <div className="min-w-0 text-sm">
          <span className="block">동기화 주기</span>
          <div className="mt-1 flex flex-wrap items-center gap-2">
            <Segmented label="동기화 주기" options={INTERVAL_OPTIONS} value={draft.choice}
                       onChange={(v) => set({ choice: v, custom: v === CUSTOM && draft.custom === ""
                         ? String(Math.round((s.interval_seconds / 60) * 100) / 100) : draft.custom })} />
            {draft.choice === CUSTOM && (
              <span className="inline-flex items-center gap-1">
                <input aria-label="직접 입력 주기(분)" inputMode="decimal" value={draft.custom}
                       onChange={(e) => set({ custom: e.target.value })}
                       aria-invalid={customBad}
                       className="w-20 rounded-lg border border-line bg-surface px-2 py-1 text-sm tabular-nums" />
                <span className="text-xs text-ink/70">분</span>
              </span>
            )}
          </div>
        </div>
      </div>
      {sameNode && <p className="text-sm text-bad">주·보조에 같은 노드를 지정할 수 없습니다 — 보조는 주 노드가 막혔을 때 대신 부르는 노드입니다.</p>}
      {customBad && <p className="text-sm text-bad">주기는 1분 이상 1440분 이하로 입력하세요(소수 가능 — 1.5분 = 90초).</p>}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={blocked}>{save.isPending ? "저장 중…" : "설정 저장"}</Button>
        <span role="status" className="text-sm">
          {save.isError ? <span className="text-bad">{errText(save.error)}</span>
            : saved && !dirty ? <span className="text-ok">저장했습니다</span> : null}
        </span>
        <span className="ml-auto text-xs text-ink/70">
          {`지금: 주 ${s.primary_node ?? "없음"} · 보조 ${s.secondary_node ?? "없음"} · ${intervalText(s.interval_seconds)}마다 · 노드 확인 ${s.poll_seconds}초 간격`}
        </span>
      </div>
      <p className="text-xs text-ink/70">
        정규 동기화는 주 노드만 가져갑니다. 예정 시각부터 90초가 지나도 주 노드가 가져가지 않으면 보조 노드도 받고, 주 노드가
        연결 문제(접속 불가·DNS·시간 초과·기한 초과)로 실패하면 30초 뒤 보조 노드로 한 번 다시 시도합니다. 둘 다 없으면 다른
        노드로 넘기지 않습니다(방화벽은 이 두 노드에만 열려 있다는 전제). 저장·노드 지정은 포탈에 로그인한 관리자만 할 수
        있습니다(API 토큰으로는 조회만).
      </p>
    </form>
  );
}

/** 클러스터 이름 → 클러스터 탭의 그 카드(주소의 #cluster-<이름> — 카드가 강조·포커스된다). 개요의 실패 문구가 시키는 「클러스터
 *  편집」으로 바로 가는 길이다. */
function ClusterName({ name }: { name: string }) {
  return <Link to={clusterCardHref(name)} className="font-medium text-accent hover:underline">{name}</Link>;
}

/** 「마지막 실패」 칸. 그 뒤 이 노드가 성공했으면 이미 해결된 일이라 흐린 글씨로 짧게(조치 문구 없이), 아니면 지금처럼 빨간 전체
 *  사유. 어느 클러스터·계기의 실패인지 앞에 적는다(2026-10-28 사고 — 실험 클러스터 재시작 2분 동안의 수동 동기화 1건이 하루 뒤에도
 *  방화벽 오류처럼 보였다). 「그 뒤 성공」은 **그 클러스터**에 대한 이 노드의 성공으로 잰다 — 배지와 같은 판정(k8sTerms.
 *  failureResolved): 다른 클러스터의 성공으로 이 클러스터의 실패를 「해결됨」이라 하지 않는다. 클러스터 이름은 그 카드로 가는
 *  링크(ClusterName). */
function LastFailure({ node, clusters, offsetMs }: {
  node: CallerNode | undefined; clusters: ClusterSummary[]; offsetMs: number;
}) {
  if (!node?.last_fail_at) return <span className="text-ink/70">없음</span>;
  const trigger = node.last_fail_trigger ? (TRIGGER_LABEL[node.last_fail_trigger] ?? node.last_fail_trigger) : null;
  const cluster = node.last_fail_cluster;
  const where = cluster || trigger ? (
    <>
      {" · "}
      {cluster && <ClusterName name={cluster} />}
      {cluster && trigger ? " " : ""}
      {trigger}
    </>
  ) : null;
  if (failureResolved(node)) {
    return (
      <span className="text-ink/70">
        <Ago iso={node.last_fail_at} offsetMs={offsetMs} />
        {where}
        {node.last_reason ? ` · ${shortReasonText(node.last_reason)}` : ""}
        {" · 이후 성공해 해결됨"}
      </span>
    );
  }
  // 「그 뒤 확인되지 않음」은 secondary 의 대체·정규 실패에만(primary 는 매 주기 다시 부르고, 확인 run 의 실패는 방금 확인한
  // 것이다 — k8sTerms.pathUnverifiedSince). 배지의 「재확인 필요」와 같은 조건.
  const recovered = pathUnverifiedSince(node, clusters);
  return (
    <>
      <span className="text-bad">
        <Ago iso={node.last_fail_at} offsetMs={offsetMs} />
        {where}
        {node.last_reason ? ` · ${reasonText(node.last_reason)}` : ""}
      </span>
      {recovered && (
        <span className="block text-xs text-ink/70">
          {`클러스터 ${node.last_fail_cluster} 는 이후 다른 노드로 회복했습니다 — 이 노드의 경로는 그 뒤 확인되지 않았습니다.`}
        </span>
      )}
    </>
  );
}

/** 「클러스터별 연결」 한 줄 — 이 노드가 그 클러스터의 지금 설정에 낸 마지막 run. 상태는 글자로도 읽힌다(아이콘·색만으로 가르지
 *  않는다). null = 이 노드의 그 클러스터 호출 기록 없음(모름 — 실패가 아니다). 부른 적이 없을 수도, 주소·토큰을 바꾼 뒤 아직일
 *  수도, 보존 정리(기본 30일)가 지웠을 수도 있어 「부른 적 없음」으로 단정하지 않는다. secondary 면 어느 쪽이든 「장애 넘김 경로
 *  미확인」이다. 「클러스터는 이후 회복」은 그 실패가 클러스터 상태를 실패로 만든 경우에만 붙인다 — 보조 연결 확인(check)의
 *  실패는 클러스터 상태를 (노드성 사유면) 바꾸지 않으니, 클러스터가 실패했다가 회복한 것처럼 읽히지 않게 뺀다(2026-10-28 2차
 *  리뷰; 「마지막 실패」 칸의 pathUnverifiedSince 와 같은 거름). */
function ClusterLinkRow({ link, role, cluster, offsetMs }: {
  link: CallerClusterLink; role: NodeRole; cluster: ClusterSummary | undefined; offsetMs: number;
}) {
  const st = link.last_status;
  let icon = <CircleDashed aria-hidden="true" className="mt-[3px] h-3.5 w-3.5 shrink-0 text-ink/50" />;
  let body: ReactNode;
  if (st === "ok") {
    icon = <CircleCheck aria-hidden="true" className="mt-[3px] h-3.5 w-3.5 shrink-0 text-ok" />;
    body = <span className="text-ok">성공 · <Ago iso={link.last_ok_at} offsetMs={offsetMs} /></span>;
  } else if (st === "failed") {
    icon = <CircleX aria-hidden="true" className="mt-[3px] h-3.5 w-3.5 shrink-0 text-bad" />;
    const recovered = link.last_trigger !== "check" && isoAfter(cluster?.last_success_at, link.last_fail_at);
    body = (
      <>
        <span className="text-bad">
          실패 · <Ago iso={link.last_fail_at} offsetMs={offsetMs} />
          {link.last_reason ? ` · ${shortReasonText(link.last_reason)}` : ""}
        </span>
        {recovered && <span className="text-ink/70"> — 클러스터는 이후 회복</span>}
      </>
    );
  } else if (st === "running") {
    icon = <LoaderCircle aria-hidden="true" className="mt-[3px] h-3.5 w-3.5 shrink-0 text-busy" />;
    const t = link.last_trigger ? TRIGGER_LABEL[link.last_trigger] ?? link.last_trigger : null;
    body = <span className="text-busy">{`진행 중${t ? `(${t})` : ""}`}</span>;
  } else if (st === null || st === undefined) {
    body = (
      <>
        <span className="text-ink/70">호출 기록 없음</span>
        {role === "secondary" && cluster?.enabled !== false && <span className="text-attn"> — 장애 넘김 경로 미확인</span>}
      </>
    );
  } else {
    body = <span className="text-ink/70">{`상태 모름(${st})`}</span>;
  }
  return (
    <li className="flex min-w-0 items-start gap-1.5">
      {icon}
      <span className="min-w-0 [overflow-wrap:anywhere]">
        <ClusterName name={link.cluster} />
        {cluster?.enabled === false && <span className="text-ink/70"> (동기화 꺼짐)</span>}
        {" "}{body}
      </span>
    </li>
  );
}

/** 보조 노드 「연결 확인」 — 장애 넘김 경로(방화벽에 이 노드 IP 허용)를 실제로 필요해지기 전에 확인한다(k8s_sync 「배정」 (3)).
 *  가져갈 노드가 없으면(보고 없음·지연·미지원) 잠근다 — 서버도 409 k8s_sync_secondary_not_ready. 이미 걸린 요청이 있는데 보고가
 *  끊겼거나 에이전트가 k8s 동기화를 지원하지 않으면 「다음 폴링 안에」라고 하지 않고 못 가져가는 이유를 적는다(빠른 재조회도
 *  멈춘다 — useK8s.overviewPollMs, 2026-10-28 2차 리뷰). primary 가 없으면 보조 노드가 정규 노드라 정규 동기화가 곧 확인이다
 *  (서버 409 k8s_sync_no_primary). */
function CheckSecondary({ node, pollSeconds, primarySet }: {
  node: CallerNode | undefined; pollSeconds: number; primarySet: boolean;
}) {
  const check = useCheckSecondary();
  const pending = node?.check_pending ?? [];
  const blockedWhy = node === undefined || node.reported === false
    ? "보조 노드의 에이전트 보고가 없어 확인할 수 없습니다"
    : !node.capable ? "보조 노드의 에이전트가 k8s 동기화를 지원하지 않아 확인할 수 없습니다"
    : !node.fresh ? "보조 노드의 보고가 지연돼 확인할 수 없습니다 — 에이전트가 다시 보고하면 누를 수 있습니다"
    : !primarySet ? "주 노드가 지정되지 않아 보조 노드가 정규 동기화를 맡고 있습니다 — 정규 동기화가 곧 연결 확인입니다"
    : null;
  const disabled = blockedWhy !== null || pending.length > 0 || check.isPending;
  const left = `남은 클러스터 ${pending.join(", ")}`;
  return (
    <div className="min-w-0 space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" className="whitespace-nowrap" disabled={disabled} title={blockedWhy ?? undefined}
                onClick={() => check.mutate()}>
          {check.isPending ? "요청 중…" : "연결 확인"}
        </Button>
        <span role="status" className="min-w-0 text-xs [overflow-wrap:anywhere]">
          {pending.length > 0 && node?.fresh && node.capable
            ? <span className="text-ink/80">{`확인 요청됨 — 보조 노드가 다음 폴링(${pollSeconds}초) 안에 가져갑니다 · ${left}`}</span>
            : pending.length > 0 && node?.fresh
            ? <span className="text-attn">{`확인 요청됨 — 보조 노드의 에이전트가 k8s 동기화를 지원하지 않아 가져가지 못합니다 · ${left}`}</span>
            : pending.length > 0
            ? <span className="text-attn">{`확인 요청됨 — 보조 노드가 보고하지 않아 아직 가져가지 못했습니다 · ${left}`}</span>
            : check.isError ? <span className="text-bad">{errText(check.error)}</span>
            : blockedWhy !== null ? <span className="text-ink/70">{blockedWhy}</span>
            : check.isSuccess ? <span className="text-ink/70">요청했습니다 — 결과는 위 「클러스터별 연결」에 보입니다</span>
            : null}
        </span>
      </div>
      <p className="text-xs text-ink/70">
        보조 노드가 모든 클러스터 API 에 닿는지 지금 확인합니다(정규 동기화 1회와 같음, 연결 실패는 클러스터 상태를 바꾸지 않음).
      </p>
    </div>
  );
}

function NodeSlot({ role, name, node, clusters, pollSeconds, primarySet, offsetMs }: {
  role: NodeRole; name: string | null; node: CallerNode | undefined; clusters: ClusterSummary[]; pollSeconds: number;
  primarySet: boolean; offsetMs: number;
}) {
  const title = role === "primary" ? "주 호출 노드 · primary" : "보조 호출 노드 · secondary";
  if (name === null) {
    return (
      <div className="min-w-0 rounded-card border border-dashed border-line px-4 py-3">
        <p className="text-xs text-ink/70">{title}</p>
        <p className="mt-1 font-medium text-ink/80">지정 안 됨</p>
        <p className="mt-1 text-xs text-ink/70">
          {/* k8s_sync.claim_trigger: 90초 유예는 주 노드가 지정돼 있을 때만 붙는다(2026-11-11 — 예전 문구는 반대로 말했다) */}
          {role === "primary" ? "주 노드가 없으면 보조 노드가 정규 동기화를 예정 시각에 바로 맡습니다(90초 유예는 주 노드가 있을 때만)."
            : "보조 노드가 없으면 주 노드가 막혔을 때 대신 부를 노드가 없습니다."}
        </p>
      </div>
    );
  }
  const st = callerNodeState(node, clusters);
  const byCluster = new Map(clusters.map((c) => [c.name, c]));
  const links = node?.clusters ?? null;
  return (
    <div className="min-w-0 rounded-card border border-line px-4 py-3">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-xs text-ink/70">{title}</p>
          <p className="mt-0.5 font-semibold [overflow-wrap:anywhere]">{name}</p>
        </div>
        <StatusPill state={st.label} label={st.label} variant={st.variant} />
      </div>
      <div className="mt-2">
        <DefList>
          <DefRow label="IP">
            {node?.internal_ip ? <><Mono>{node.internal_ip}</Mono><span className="block text-xs text-ink/70">대상 API 서버 방화벽에 이 IP 를 허용하세요</span></>
              : <span className="text-ink/70">모름(노드 보고에 없음)</span>}
          </DefRow>
          <DefRow label="마지막 호출"><Ago iso={node?.last_claim_at} empty="아직 없음" offsetMs={offsetMs} /></DefRow>
          <DefRow label="마지막 성공"><Ago iso={node?.last_ok_at} empty="아직 없음" offsetMs={offsetMs} /></DefRow>
          <DefRow label="마지막 실패"><LastFailure node={node} clusters={clusters} offsetMs={offsetMs} /></DefRow>
          {/* 옛 서버 응답(clusters 칸 없음)이면 줄 자체를 그리지 않는다 -- 「호출 기록 없음」으로 지어내지 않게. */}
          {links !== null && (
            <DefRow label="클러스터별 연결">
              {links.length === 0 ? <span className="text-ink/70">등록된 클러스터 없음</span> : (
                <ul className="space-y-1">
                  {links.map((l) => (
                    <ClusterLinkRow key={l.cluster} link={l} role={role} cluster={byCluster.get(l.cluster)}
                                    offsetMs={offsetMs} />
                  ))}
                </ul>
              )}
            </DefRow>
          )}
          <DefRow label="맡은 클러스터">
            {node && node.serving.length > 0
              // 클러스터 이름은 63자까지 -- 꼬리표(whitespace-nowrap) 안에서도 줄을 바꿔 좁은 폭(390)을 뚫지 않게.
              ? <span className="flex min-w-0 flex-wrap gap-1">{node.serving.map((c) => (
                  <Tag key={c}><span className="min-w-0 whitespace-normal [overflow-wrap:anywhere]">{c}</span></Tag>
                ))}</span>
              : <span className="text-ink/70">없음</span>}
          </DefRow>
          {node && !node.capable && (
            <DefRow label="주의">
              <span className="text-attn">이 노드의 에이전트가 k8s 동기화를 선언하지 않았습니다 — 에이전트 이미지를 올리세요.</span>
            </DefRow>
          )}
        </DefList>
      </div>
      {role === "secondary" && (
        <div className="mt-3 border-t border-line pt-3">
          <CheckSecondary node={node} pollSeconds={pollSeconds} primarySet={primarySet} />
        </div>
      )}
    </div>
  );
}

function CallerNodesCard({ ov, offsetMs }: { ov: Overview; offsetMs: number }) {
  const byName = new Map(ov.nodes.map((n) => [n.node_name, n]));
  const { primary_node: p, secondary_node: s } = ov.settings;
  const others = ov.nodes.filter((n) => n.capable && n.node_name !== p && n.node_name !== s).map((n) => n.node_name);
  const clusters = Array.isArray(ov.clusters) ? ov.clusters : [];
  const poll = ov.settings.poll_seconds;
  return (
    <Card className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold">호출 노드와 주기</h2>
        <p className="mt-0.5 text-sm text-ink/70">
          k8s API 는 DMS 서버가 아니라 여기서 지정한 노드의 에이전트가 부릅니다 — 대상 클러스터 방화벽은 이 노드들의 IP 만
          허용하면 됩니다.
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <NodeSlot role="primary" name={p} node={p ? byName.get(p) : undefined} clusters={clusters} pollSeconds={poll}
                  primarySet={Boolean(p)} offsetMs={offsetMs} />
        <NodeSlot role="secondary" name={s} node={s ? byName.get(s) : undefined} clusters={clusters} pollSeconds={poll}
                  primarySet={Boolean(p)} offsetMs={offsetMs} />
      </div>
      {others.length > 0 && (
        <p className="text-xs text-ink/70">{`그 밖에 k8s 동기화를 선언한 노드: ${others.join(", ")}`}</p>
      )}
      <div className="border-t border-line pt-4"><SettingsForm ov={ov} /></div>
    </Card>
  );
}

/** 개요의 「클러스터」 타일 부제 — 상태별 개수(「정상 1 · 실패 1」). 실패색 상태는 빨간 글자, 조치가 필요한 상태(토큰 없음·지연)는
 *  주의색 — 색만이 아니라 라벨 글자로도 읽힌다. 카드는 클러스터 탭에 있어 타일이 그리로 가는 링크다. */
function ClusterHealth({ clusters }: { clusters: ClusterSummary[] }) {
  const parts = clusterStatusCounts(clusters);
  if (parts.length === 0) return null;
  return (
    <span className="flex flex-wrap gap-x-1">
      {parts.map((s, i) => (
        <span key={s.status} className={s.variant === "bad" ? "text-bad" : s.variant === "action" ? "text-attn" : undefined}>
          {`${s.label} ${s.count}${i < parts.length - 1 ? " ·" : ""}`}
        </span>
      ))}
    </span>
  );
}

/** 클러스터가 하나도 없을 때 개요에 거는 한 줄 — 등록은 클러스터 탭에서 한다. 호출 노드 지정(아래 카드)이 먼저라 그 순서를 적는다. */
function NoClustersHint() {
  return (
    <InfoCard className="flex flex-wrap items-center justify-between gap-3">
      <p className="min-w-0 text-ink/80">
        아직 등록된 클러스터가 없습니다 — 아래 「호출 노드와 주기」에서 주·보조 호출 노드를 지정한 뒤 클러스터를 등록하세요.
      </p>
      <Link to={K8S_CLUSTERS_PATH}
            className="inline-flex shrink-0 items-center justify-center whitespace-nowrap rounded-lg border border-accent bg-surface px-3 py-2 text-sm font-medium text-accent hover:bg-infobg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
        클러스터 탭에서 등록
      </Link>
    </InfoCard>
  );
}

export function K8sOverview() {
  const q = useK8sOverview();
  const ov = q.data;
  const offsetMs = serverOffsetMs(ov?.now, q.dataUpdatedAt);
  const clusters = Array.isArray(ov?.clusters) ? ov!.clusters : [];
  const t = ov?.totals;

  // 머리 동작(「클러스터 등록」·「모두 지금 동기화」)은 클러스터 탭으로 옮겼다(2026-11-11) -- 결과(카드의 상태·요청 문구)가 보이는
  // 화면에서 누르게. 개요는 다른 하위 탭처럼 동작 없는 머리다.
  return (
    <section className="space-y-4">
      <K8sHeader
        title="k8s 연동"
        description="k8s 클러스터의 PVC·RoleBinding 을 주기적으로 받아 PV 경로와 사용자 권한을 판정합니다. 권한·경로는 마지막으로 성공한 동기화 스냅숏 기준입니다." />

      <SignupGuardNote guard={ov?.signup_guard} />

      {q.isError && (
        <p role="alert" className="text-sm text-bad">
          {ov ? `개요를 다시 불러오지 못했습니다 — 아래는 마지막으로 받은 값입니다(${errText(q.error)})`
            : `개요를 불러오지 못했습니다 — ${errText(q.error)}`}
        </p>
      )}

      {q.isLoading ? <p className="text-ink/70">불러오는 중…</p> : ov && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label="클러스터" value={countText(t?.clusters)} to={K8S_CLUSTERS_PATH}
                  sub={clusters.length > 0 ? <ClusterHealth clusters={clusters} /> : undefined} />
            <Stat label="namespace" value={countText(t?.namespaces)} />
            <Stat label="PV 이름(PVC)" value={countText(t?.pvcs)} />
            <Stat label="권한 바인딩" value={countText(t?.bindings)} />
            <Stat label="권한 사용자" value={countText(t?.users)} />
          </div>

          {clusters.length === 0 && <NoClustersHint />}

          <CallerNodesCard ov={ov} offsetMs={offsetMs} />
        </>
      )}
    </section>
  );
}
