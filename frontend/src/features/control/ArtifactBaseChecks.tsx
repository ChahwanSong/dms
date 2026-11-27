import { useId } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { StatusPill } from "../../components/ui/StatusPill";
import { ApiError, reasonText } from "../../lib/api";
import type { PillVariant } from "../../lib/jobState";
import type { ApiGateState, ArtifactBaseApiCheck, ArtifactBaseApiPod, ArtifactBaseControllerCheck, ArtifactBaseInfo,
              ArtifactBaseNodeCheck } from "../../lib/types";
import { ageText } from "../nodes/NodesList";
import { relTime } from "./ControlStatePage";
import { useRecheckArtifactBase } from "./useArtifactBase";

// 아티팩트 경로 3홉 검증 카드(2026-11-23 개편 -- 사고: dms-api 가 공유 스토리지 미마운트 노드에 떠 아티팩트·scan 리포트를 못
// 읽었는데, 컨트롤러 점검은 자기 노드(마운트됨) 기준이라 「정상」이었고 화면엔 그 둘을 가를 자리가 없었다). 위에서 아래로:
// 요약 배지 + 「지금 다시 점검」 → 두 관점 패널(API 파드 · 컨트롤러 -- sm 2열, 375 는 1열) → 엇갈림 상자 → 다른 API 파드 카드
// → 노드 에이전트 표(가로 넘침은 래퍼 안에서) → 각주. 판정은 서버가 한다(설계 apibase v2 §F) -- 화면은 null(모름)을 실패로
// 접지 않고(「확인 대기」), posture(권한) 사유를 가용성 실패와 섞지 않는다.

// 권한·모양(posture) 사유 -- 「읽을 수 있느냐」가 아니라 「안전하냐」라 엇갈림 상자(노드 마운트 진단)에 넣지 않는다. 옛 서버의
// checks.api 는 availability 없이 ok/reason 하나라 이 집합으로 가른다.
const POSTURE_REASONS = new Set([
  "artifact_base_outside_allowlist", "artifact_base_not_owned", "artifact_base_world_writable",
  "artifact_base_group_writable", "artifact_base_not_traversable",
]);

/** 3홉 요약 배지: 실패가 하나라도 있으면 "문제 N건" -- 대기(모름)와 뭉개지
    않는다(설계 §4). 실패 없이 대기만 있으면 "확인 대기 N건", 다 통과면
    "모두 정상". API 는 응답한 파드의 판정 하나로 센다(다른 파드 카드·시작 점검을 더 세면 같은 장애가 여러 번 잡힌다). */
export function hopSummary(checks: ArtifactBaseInfo["checks"]):
    { label: string; variant: "ok" | "bad" | "busy" } {
  let bad = 0, pending = 0;
  if (!checks.api.ok) bad += 1;
  // 이 응답을 준 파드 밖의 API 파드(fresh) 실패도 센다 -- 롤링 중 Ready 를 기다리는 새 파드가 실패하는데 머리 배지가 「모두
  // 정상」이면 바로 아래 엇갈림 상자·파드 카드와 모순된다(2026-11-23 라이브 확인).
  bad += (checks.api_pods ?? []).filter((p) => !p.this_pod && p.fresh && tri(p.ok) === false).length;
  if (checks.controller.pending) pending += 1;
  else if (!checks.controller.ok) bad += 1;
  for (const n of checks.nodes) {
    if (n.pending) pending += 1;
    else if (!n.exists || !n.writable) bad += 1;
  }
  if (bad > 0) return { label: `문제 ${bad}건`, variant: "bad" };
  if (pending > 0) return { label: `확인 대기 ${pending}건`, variant: "busy" };
  return { label: "모두 정상", variant: "ok" };
}

// 시작 점검(게이트) 상태 문구. tone attn = 사람이 볼 일(Ready 를 막는 중·실패인 채 서비스 시작).
export function gateText(state: ApiGateState | string | null | undefined):
    { text: string; tone: "ok" | "attn" | "muted" } | null {
  switch (state) {
    case "passed": return { text: "통과", tone: "ok" };
    case "waiting": return { text: "Ready 대기 중", tone: "attn" };
    case "started_degraded": return { text: "형제 파드가 없어 점검 실패인 채 서비스 시작", tone: "attn" };
    case "disabled": return { text: "비상구로 꺼짐", tone: "muted" };
    case "off": return { text: "주기 점검 꺼짐", tone: "muted" };
    default: return null;
  }
}
const TONE = { ok: "text-ok", attn: "text-attn", muted: "text-muted" } as const;

const nodeLabel = (n: string | null | undefined) => (n ? `노드 ${n}` : "노드 모름");

// API 패널의 판정: 가용성 실패 = 실패, 가용성은 정상인데 권한(posture)만 문제 = 주의. 옛 서버(availability 없음)는 ok 하나.
function apiVerdict(api: ArtifactBaseApiCheck): { label: string; variant: PillVariant } {
  if (api.availability) {
    if (!api.availability.ok) return { label: "실패", variant: "bad" };
    if (api.posture) return { label: "주의", variant: "action" };
    return { label: "정상", variant: "ok" };
  }
  return api.ok ? { label: "정상", variant: "ok" } : { label: "실패", variant: "bad" };
}
// API 가 「경로를 못 읽는다」(가용성 실패) -- 권한 사유만인 실패는 아니다.
export function apiReadFailed(api: ArtifactBaseApiCheck): boolean {
  if (api.availability) return api.availability.ok === false;
  return api.ok === false && !(api.reason !== null && POSTURE_REASONS.has(api.reason));
}
function controllerReadFailed(c: ArtifactBaseControllerCheck): boolean {
  return !c.pending && c.ok === false && !(c.reason !== null && POSTURE_REASONS.has(c.reason));
}
// 다른 API 파드 행의 ok 는 DB 정수(1/0/NULL)일 수 있다 -- 셋으로만 읽는다(NULL = 아직 점검 전).
const tri = (v: unknown): boolean | null => (v === true || v === 1 ? true : v === false || v === 0 ? false : null);

function Panel({ title, sub, pill, children }: {
  title: string; sub: string; pill: React.ReactNode; children: React.ReactNode;
}) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="min-w-0 rounded-lg border border-line p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 id={id} className="text-sm font-semibold">{title}</h3>
          <p className="text-xs text-muted">{sub}</p>
        </div>
        {pill}
      </div>
      <dl className="mt-3 grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">{children}</dl>
    </section>
  );
}
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (<>
    <dt className="text-muted">{label}</dt>
    <dd className="min-w-0 [overflow-wrap:anywhere]">{children}</dd>
  </>);
}
const Mono = ({ children }: { children: React.ReactNode }) =>
  <span className="font-mono text-xs [overflow-wrap:anywhere]">{children}</span>;

// 파일시스템 칸: fstype + 로컬 디스크 주의(공유 스토리지가 빠진 노드의 빈 로컬 디렉터리 = 사고 서명). 진단 전용(판정에 안 쓴다).
function FsValue({ fstype, local }: { fstype: string | null | undefined; local: boolean | null | undefined }) {
  if (!fstype) return <span className="text-muted">모름</span>;
  return (
    <span>
      <Mono>{fstype}</Mono>
      {local === true && (
        <span className="ml-1.5 text-xs text-attn"
              title="공유 스토리지가 아니라 노드 로컬 파일시스템 위 디렉터리로 보입니다 — 마운트가 빠졌을 수 있습니다">
          · 로컬 디스크로 보임</span>
      )}
    </span>
  );
}

function ApiPanel({ api, now }: { api: ArtifactBaseApiCheck; now: number }) {
  const v = apiVerdict(api);
  const avail = api.availability ?? null;
  const gate = api.gate ? gateText(api.gate.state) : null;
  // 확인 시각: 주기 점검이면 「23초 전 확인 · 60초 주기」, 즉석이면 그 사실. 옛 서버(source 없음)는 말하지 않는다(지어내지 않는다).
  const every = typeof api.interval_seconds === "number" && api.interval_seconds > 0 ? `${api.interval_seconds}초 주기` : null;
  const ago = api.checked_at ? ageText(api.checked_at, now) : null;
  const when = api.source === "instant" ? "조회 시 즉석 확인"
    : api.source === "monitor" ? [ago !== null ? `${ago} 확인` : null, every].filter(Boolean).join(" · ") || null
    : null;
  return (
    <Panel title="API 파드 관점" sub="포탈 조회 · 아티팩트 열람 · scan 리포트"
           pill={<StatusPill state={v.label} variant={v.variant} />}>
      {avail ? (
        <Row label="읽기">
          {avail.ok ? <span className="text-ok">가능</span> : <span className="text-bad">{reasonText(avail.reason)}</span>}
          {/* 최근 작업 기록 디렉터리(자연 센티넬). jobs_found null 은 두 경우다 -- 확인할 잡이 없음(정상 판정에서 생략, 모름 ≠
              실패)과 그 단계 전에 실패(디렉터리 열기·입출력 오류·시간 초과). 뒤쪽에 「확인할 작업 없음」을 붙이면 DB 에 최근
              작업이 없다는 거짓말이 된다(2026-11-23 리뷰) -- 그땐 덧붙이지 않는다. */}
          {avail.jobs_found !== null ? (
            <span className="ml-1.5 text-xs text-muted">{`· 최근 작업 기록 ${avail.jobs_found}/${avail.jobs_checked}개 확인`}</span>
          ) : avail.ok && avail.jobs_checked === 0 ? (
            <span className="ml-1.5 text-xs text-muted">· 확인할 최근 작업 없음</span>
          ) : null}
        </Row>
      ) : api.reason !== null && (
        <Row label="사유"><span className="text-bad">{reasonText(api.reason)}</span></Row>
      )}
      {api.instance !== undefined && <Row label="파드">{api.instance ? <Mono>{api.instance}</Mono> : "모름"}</Row>}
      {api.node_name !== undefined && <Row label="노드">{api.node_name ? <Mono>{api.node_name}</Mono> : "모름"}</Row>}
      {(api.fstype !== undefined || api.local_fs !== undefined) && (
        <Row label="파일시스템"><FsValue fstype={api.fstype} local={api.local_fs} /></Row>
      )}
      {when !== null && <Row label="확인"><span className="text-muted">{when}</span></Row>}
      {gate !== null && (
        <Row label="시작 점검">
          <span className={TONE[gate.tone]}>{gate.text}</span>
          {api.gate?.at && <span className="ml-1.5 text-xs text-muted">{`· ${relTime(api.gate.at, now) || "—"}`}</span>}
        </Row>
      )}
      {api.posture && (
        <Row label="권한"><span className="text-attn">{reasonText(api.posture)}</span></Row>
      )}
    </Panel>
  );
}

function ControllerPanel({ c, now }: { c: ArtifactBaseControllerCheck; now: number }) {
  // null(모름)과 실패를 뭉개지 않는다(설계 §4) -- 확인 대기는 실패가 아니다.
  const pill = c.pending ? <StatusPill state="확인 대기" variant="busy" />
    : c.ok ? <StatusPill state="정상" variant="ok" /> : <StatusPill state="실패" variant="bad" />;
  return (
    <Panel title="컨트롤러 관점" sub="잡 진행 · 결과 요약 읽기" pill={pill}>
      {!c.pending && c.reason !== null && (
        <Row label="사유"><span className="text-bad">{reasonText(c.reason)}</span></Row>
      )}
      {c.node_name !== undefined && <Row label="노드">{c.node_name ? <Mono>{c.node_name}</Mono> : "모름"}</Row>}
      <Row label="확인">
        <span className="text-muted">
          {c.pending ? "새 경로를 아직 점검하지 않았습니다"
            : [relTime(c.checked_at, now) ? `${relTime(c.checked_at, now)} 확인` : null, "30초 주기"].filter(Boolean).join(" · ")}
        </span>
      </Row>
    </Panel>
  );
}

// 두 관점 엇갈림(양방향). 사고의 모양 그대로 -- 한쪽 노드만 공유 스토리지를 못 본다. 권한 사유는 노드 마운트 문제가 아니라 뺀다.
// 다른 API 파드(fresh 행)의 읽기 실패도 API 쪽에 넣는다(여러 replica 면 이 응답을 준 파드는 정상일 수 있다).
function MismatchNote({ checks }: { checks: ArtifactBaseInfo["checks"] }) {
  const api = checks.api, c = checks.controller;
  const otherFails = (checks.api_pods ?? []).filter((p) => !p.this_pod && p.fresh && tri(p.ok) === false
    && !(p.reason !== null && POSTURE_REASONS.has(p.reason)));
  const apiNodes = [...(apiReadFailed(api) ? [api.node_name ?? null] : []), ...otherFails.map((p) => p.node_name)];
  const apiDown = apiNodes.length > 0;
  const ctrlDown = controllerReadFailed(c);
  const ctrlOk = !c.pending && c.ok === true;
  const apiOk = !apiReadFailed(api) && (api.availability ? api.availability.ok === true : api.ok === true);
  let text: string | null = null;
  if (apiDown && !ctrlDown && ctrlOk) {
    const known = [...new Set(apiNodes.filter((n): n is string => !!n))];
    const who = known.length > 0 ? known.map((n) => `노드 ${n}`).join(", ") : "노드 모름";
    // 실패한 API 파드의 노드마다 같은 노드에서 정상인 파드(컨트롤러·이 파드·다른 API 파드)가 있으면 노드 마운트가 아니라 그 파드
    // 쪽(hostPath·마운트 전파 — 파드가 공유 스토리지 마운트보다 먼저 떴는지)이다(dms-k8s e25a2dfb d40 실증 이식: 같은 노드의
    // 컨트롤러·옛 API 파드는 정상인데 「노드 … 에 공유 스토리지가 마운트돼 있는지」라고 했다).
    const healthy = new Set<string>();
    if (c.node_name) healthy.add(c.node_name);
    if (apiOk && api.node_name) healthy.add(api.node_name);
    for (const p of checks.api_pods ?? []) if (!p.this_pod && p.fresh && tri(p.ok) === true && p.node_name) healthy.add(p.node_name);
    // 노드를 모르는 실패 파드가 하나라도 있으면 파드 쪽이라고 단정하지 않는다(그 파드가 문구에서 사라진다).
    const podSide = apiNodes.length > 0 && apiNodes.every((n) => typeof n === "string" && n !== "" && healthy.has(n));
    // 같은 노드의 정상 파드가 컨트롤러일 때만 컨트롤러 이름을 댄다 -- 컨트롤러가 다른 노드면 정상인 쪽은 다른 API 파드다.
    const peer = c.node_name && known.includes(c.node_name) ? `컨트롤러 ${nodeLabel(c.node_name)}` : "다른 API 파드";
    text = podSide
      ? `API 파드(${who})만 아티팩트 경로를 읽지 못합니다. 같은 노드의 다른 DMS 파드(${peer})는 정상입니다 — `
        + "노드 마운트가 아니라 그 파드의 hostPath·마운트 전파(파드가 공유 스토리지 마운트보다 먼저 떴는지)를 확인하세요"
      : `API 파드(${who})만 아티팩트 경로를 읽지 못합니다. 컨트롤러(${nodeLabel(c.node_name)})는 같은 경로를 정상으로 봅니다 — `
        + `${known.length > 0 ? `${known.map((n) => `노드 ${n}`).join(", ")} 에` : "API 파드가 뜬 노드에"} 공유 스토리지가 마운트돼 있는지 확인하세요`;
  } else if (ctrlDown && apiOk && !apiDown) {
    text = `컨트롤러(${nodeLabel(c.node_name)})만 아티팩트 경로를 읽지 못합니다. API 파드(${nodeLabel(api.node_name)})는 같은 경로를 `
      + `정상으로 봅니다 — ${c.node_name ? `노드 ${c.node_name} 에` : "컨트롤러가 뜬 노드에"} 공유 스토리지가 마운트돼 있는지 확인하세요`;
  }
  if (text === null) return null;
  return (
    <div role="note" className="mt-3 flex items-start gap-2 rounded-lg border border-attn/30 bg-attnbg px-3 py-2.5 text-sm text-attn">
      <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
      <p className="min-w-0 break-keep">
        {text}{" "}
        <Link to="/admin/mounts" className="whitespace-nowrap font-medium underline-offset-2 hover:underline">마운트 현황 보기</Link>
      </p>
    </div>
  );
}

function podVerdict(p: ArtifactBaseApiPod): { label: string; variant: PillVariant } {
  const ok = tri(p.ok);
  if (ok === null) return { label: "점검 전", variant: "neutral" };
  return ok ? { label: "정상", variant: "ok" } : { label: "실패", variant: "bad" };
}

// 다른 API 파드(24h 행 -- 롤링 중 Ready 를 기다리는 새 파드, 다른 replica). 표 대신 파드별 카드(375 에서 쌓인다). 보고가 끊긴
// 행(fresh=false -- 사라진 파드)은 흐리게: 고친 뒤에도 24시간 경고처럼 보이지 않게. 응답한 파드 하나뿐이면 위 패널과 같은 내용이라
// 그리지 않는다.
function ApiPods({ pods, now }: { pods: ArtifactBaseApiPod[] | undefined; now: number }) {
  const list = Array.isArray(pods) ? pods : [];
  if (!list.some((p) => !p.this_pod)) return null;
  return (
    <section className="mt-4">
      <h3 className="text-sm font-semibold">API 파드별 점검</h3>
      <p className="text-xs text-muted">최근 24시간 동안 점검을 기록한 API 파드 — 보고가 끊긴 파드는 흐리게 표시합니다</p>
      <ul className="mt-2 grid gap-2 sm:grid-cols-2">
        {list.map((p) => {
          const v = podVerdict(p);
          const gate = gateText(p.gate_state);
          const ok = tri(p.ok);
          return (
            <li key={p.instance} aria-label={`API 파드 ${p.instance}`}
                className={`min-w-0 rounded-lg border border-line p-3 text-sm ${p.fresh ? "" : "opacity-60"}`}>
              <div className="flex items-start justify-between gap-2">
                <Mono>{p.instance}</Mono>
                <span className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                  {p.this_pod && <StatusPill state="이 응답" variant="neutral" />}
                  {!p.fresh && <StatusPill state="보고 끊김" variant="neutral" />}
                  <StatusPill state={v.label} variant={v.variant} />
                </span>
              </div>
              <dl className="mt-2 grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-1">
                <Row label="노드">{p.node_name ? <Mono>{p.node_name}</Mono> : "모름"}</Row>
                {ok === false && p.reason !== null && (
                  <Row label="사유"><span className="text-bad">{reasonText(p.reason)}</span></Row>
                )}
                {p.posture && <Row label="권한"><span className="text-attn">{reasonText(p.posture)}</span></Row>}
                <Row label="파일시스템"><FsValue fstype={p.fstype} local={p.local_fs} /></Row>
                <Row label="시작 점검">
                  {gate ? <span className={TONE[gate.tone]}>{gate.text}</span> : <span className="text-muted">모름</span>}
                  <span className="ml-1.5 text-xs text-muted">{p.ready_at ? "· Ready" : "· Ready 전"}</span>
                </Row>
                <Row label="확인">
                  <span className="text-muted">{p.checked_at ? `${ageText(p.checked_at, now) ?? "—"} 확인` : "아직 점검 전"}</span>
                </Row>
              </dl>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function NodeRow({ n, now }: { n: ArtifactBaseNodeCheck; now: number }) {
  return (
    <tr>
      <td className="py-1 pr-6 whitespace-nowrap">{n.node_name}</td>
      <td className="py-1 pr-6 whitespace-nowrap">{n.pending ? "확인 대기 중" : n.exists ? "있음" : "없음"}</td>
      <td className="py-1 pr-6 whitespace-nowrap">{n.pending ? "확인 대기 중" : n.writable ? "가능" : "불가"}</td>
      {/* 보고 시각이 있어야 "확인 대기 중"이 왜 대기인지 보인다 -- 오래된 보고
          (fresh=false)는 에이전트가 죽었다는 신호라 실패색으로 구분한다 */}
      <td className="py-1 text-muted whitespace-nowrap">
        {relTime(n.reported_at, now) || "—"}
        {!n.fresh && <span className="text-bad"> · 오래됨</span>}
      </td>
    </tr>
  );
}

export function ArtifactBaseChecks({ d, now }: { d: ArtifactBaseInfo; now: number }) {
  const recheck = useRecheckArtifactBase();
  const summary = hopSummary(d.checks);
  return (
    <Card>
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <h2 className="font-medium">3홉 검증</h2>
        <StatusPill state={summary.label} variant={summary.variant} />
        {/* 주기 점검 스냅숏은 최대 한 주기 묵은 값이다 -- 마운트를 고친 뒤 바로 확인할 수 있게(서버가 이 파드에서 한 회차를 돌린다). */}
        <Button type="button" variant="outline" className="ml-auto gap-1.5 py-1.5"
                disabled={recheck.isPending} onClick={() => recheck.mutate()}>
          <RefreshCw aria-hidden className={`h-4 w-4 ${recheck.isPending ? "motion-safe:animate-spin" : ""}`} />
          {recheck.isPending ? "점검 중…" : "지금 다시 점검"}
        </Button>
      </div>
      <p className="mb-3 text-xs text-muted">
        같은 경로를 세 관점에서 확인합니다 — API 파드(주기·시작 점검: 디렉터리 읽기 + 최근 작업 기록 디렉터리 확인) ·
        컨트롤러(30초 주기, 쓰기 왕복) · 노드 에이전트(60초 주기, 디렉터리 존재·쓰기 권한). API·컨트롤러는 root(uid 0, 권한
        없음)로 돌므로 root:root 경로가 정상이며, 허용 접두(DMS_ARTIFACT_BASE_ALLOWED_PREFIXES) 밖 경로는 저장되지 않습니다.
      </p>
      {recheck.isError && (
        <p role="alert" className="mb-3 text-sm text-bad">
          {`다시 점검하지 못했습니다 — ${(recheck.error as ApiError).message}`}</p>
      )}
      <div className="grid gap-3 sm:grid-cols-2">
        <ApiPanel api={d.checks.api} now={now} />
        <ControllerPanel c={d.checks.controller} now={now} />
      </div>
      <MismatchNote checks={d.checks} />
      <ApiPods pods={d.checks.api_pods} now={now} />
      {d.checks.nodes.length > 0 && (
        <section className="mt-4">
          <h3 className="text-sm font-semibold">노드 에이전트 관점</h3>
          {/* 375 에서 「확인 대기 중」 두 칸 + 노드 이름이면 표가 카드 폭을 넘는다 -- 넘침은 래퍼 안에서만(페이지 가로 넘침 0). */}
          <div className="mt-1 overflow-x-auto">
            <table className="text-sm">
              <thead><tr className="text-left text-muted">
                <th className="pr-6 font-medium whitespace-nowrap">노드</th>
                <th className="pr-6 font-medium whitespace-nowrap">디렉터리 존재</th>
                <th className="pr-6 font-medium whitespace-nowrap">쓰기</th>
                <th className="font-medium whitespace-nowrap">보고</th></tr></thead>
              <tbody>{d.checks.nodes.map((n) =>
                <NodeRow key={n.node_name} n={n} now={now} />)}</tbody>
            </table>
          </div>
        </section>
      )}
      {/* 정직한 한계 표기(설계 §2.4b): W_OK 판정 주체를 화면에 그대로 적는다 */}
      <p className="mt-2 text-xs text-muted">쓰기 가능 여부는 에이전트 프로세스(uid) 기준입니다 — 잡 파드 요청자 권한과 다를 수 있습니다</p>
      {/* 이 표는 존재·쓰기만 보인다 -- 공유 스토리지가 빠진 노드의 빈 로컬 디렉터리(hostPath DirectoryOrCreate)도 「있음·가능」이다
          (2026-11-23 사고). 2026-11-27~ 에이전트는 경로를 덮는 마운트(mount_point·fstype)도 보고하고, 마운트 현황(routes_mounts)이
          그것이 노드 루트 '/' 면 「로컬 디렉터리」로 짚는다(dms-k8s d14ee402 이식) -- 그 에이전트 이미지가 나가기 전·옛 에이전트는
          여전히 존재·쓰기뿐이라 한계 표기를 남긴다. */}
      <p className="mt-1 text-xs text-muted break-keep">
        이 표는 존재·쓰기만 보입니다 — 덮는 마운트를 보고하는 새 에이전트면{" "}
        <Link to="/admin/mounts" className="text-accent hover:underline">마운트 현황</Link> 화면이 경로를 덮는 마운트까지 보고, 그것이
        노드 루트(/)면(공유 스토리지 미마운트) 「로컬 디렉터리」로 짚습니다. 옛 에이전트는 존재·쓰기만 확인하므로 마운트가 빠진 노드의
        빈 로컬 디렉터리도 정상으로 보일 수 있음
      </p>
    </Card>
  );
}
