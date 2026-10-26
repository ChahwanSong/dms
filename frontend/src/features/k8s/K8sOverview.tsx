import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { InfoCard } from "../../components/ui/InfoCard";
import { Segmented } from "../../components/ui/Segmented";
import { StatusPill } from "../../components/ui/StatusPill";
import { reasonText } from "../../lib/api";
import { useNow } from "../../lib/useNow";
import { field } from "../jobs/formFields";
import { useNodes } from "../nodes/useNodes";
import { ClusterDialog } from "./ClusterDialog";
import { K8sHeader } from "./K8sTabs";
import {
  INTERVAL_PRESETS, MIN_INTERVAL_SECONDS, ROLE_LABEL, TLS_MODE_LABEL, TOKEN_LABEL, TRIGGER_LABEL, callerNodeState,
  clusterStatusMeta, countText, daysUntil, durationText, intervalText, plusSeconds, runStatusMeta, untilText, warningText,
} from "./k8sTerms";
import { Ago, DefList, DefRow, Mono, Stat, Tag } from "./parts";
import {
  useDeleteCluster, useK8sOverview, useSaveK8sSettings, useSyncAllClusters, useSyncCluster,
} from "./useK8s";
import type { CallerNode, ClusterSummary, K8sOverview as Overview, NodeRole } from "./types";

// k8s 연동 「개요」(설계 §7): 호출 노드(primary·secondary — 외부 k8s API 를 부르는 DMS 노드) · 동기화 주기 · 클러스터 카드.
// 폴링은 useK8sOverview(평소 10초, 동기화 중 3초). 「n분 전」은 잎 부품(Ago)이 15초 틱으로 늙힌다.
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

function NodeSlot({ role, name, node, offsetMs }: {
  role: NodeRole; name: string | null; node: CallerNode | undefined; offsetMs: number;
}) {
  const title = role === "primary" ? "주 호출 노드 · primary" : "보조 호출 노드 · secondary";
  if (name === null) {
    return (
      <div className="min-w-0 rounded-card border border-dashed border-line px-4 py-3">
        <p className="text-xs text-ink/70">{title}</p>
        <p className="mt-1 font-medium text-ink/80">지정 안 됨</p>
        <p className="mt-1 text-xs text-ink/70">
          {role === "primary" ? "주 노드가 없으면 정규 동기화는 예정 시각 90초 뒤 보조 노드가 받습니다."
            : "보조 노드가 없으면 주 노드가 막혔을 때 대신 부를 노드가 없습니다."}
        </p>
      </div>
    );
  }
  const st = callerNodeState(node);
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
          <DefRow label="마지막 실패">
            {node?.last_fail_at
              ? <span className="text-bad"><Ago iso={node.last_fail_at} offsetMs={offsetMs} />
                  {node.last_reason ? ` · ${reasonText(node.last_reason)}` : ""}</span>
              : <span className="text-ink/70">없음</span>}
          </DefRow>
          <DefRow label="맡은 클러스터">
            {node && node.serving.length > 0
              ? <span className="flex flex-wrap gap-1">{node.serving.map((c) => <Tag key={c}>{c}</Tag>)}</span>
              : <span className="text-ink/70">없음</span>}
          </DefRow>
          {node && !node.capable && (
            <DefRow label="주의">
              <span className="text-attn">이 노드의 에이전트가 k8s 동기화를 선언하지 않았습니다 — 에이전트 이미지를 올리세요.</span>
            </DefRow>
          )}
        </DefList>
      </div>
    </div>
  );
}

function CallerNodesCard({ ov, offsetMs }: { ov: Overview; offsetMs: number }) {
  const byName = new Map(ov.nodes.map((n) => [n.node_name, n]));
  const { primary_node: p, secondary_node: s } = ov.settings;
  const others = ov.nodes.filter((n) => n.capable && n.node_name !== p && n.node_name !== s).map((n) => n.node_name);
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
        <NodeSlot role="primary" name={p} node={p ? byName.get(p) : undefined} offsetMs={offsetMs} />
        <NodeSlot role="secondary" name={s} node={s ? byName.get(s) : undefined} offsetMs={offsetMs} />
      </div>
      {others.length > 0 && (
        <p className="text-xs text-ink/70">{`그 밖에 k8s 동기화를 선언한 노드: ${others.join(", ")}`}</p>
      )}
      <div className="border-t border-line pt-4"><SettingsForm ov={ov} /></div>
    </Card>
  );
}

function Until({ iso, offsetMs }: { iso: string | null; offsetMs: number }) {
  const now = useNow(15_000, true);
  return <span>{untilText(iso, now + offsetMs)}</span>;
}

function DeleteClusterButton({ name }: { name: string }) {
  const [open, setOpen] = useState(false);
  const del = useDeleteCluster();
  useEffect(() => { if (!open) del.reset(); }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Dialog open={open} onOpenChange={setOpen} title="클러스터 삭제" busy={del.isPending}
            trigger={<Button variant="ghost" className="whitespace-nowrap">삭제</Button>}>
      <div className="space-y-3 text-sm">
        <p>{`${name} 클러스터 등록을 지울까요?`}</p>
        <p className="text-ink/70">
          저장된 토큰과 이 클러스터에서 받은 PV·권한 스냅숏도 정리됩니다. 이 클러스터의 PV 주소로 낸 작업은 진행 중이면
          재확인에서 「PV 를 찾을 수 없음」으로 멈춥니다. 대상 클러스터의 리더 계정은 지우지 않습니다.
        </p>
        {del.isError && <p role="alert" className="text-bad">{errText(del.error)}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>취소</Button>
          <Button variant="danger" disabled={del.isPending}
                  onClick={() => del.mutate(name, { onSuccess: () => setOpen(false) })}>삭제 확인</Button>
        </div>
      </div>
    </Dialog>
  );
}

/** 카드 안 수치 한 칸. null = 모름(「—」), 0 = 0. */
function Count({ label, value, sub }: { label: string; value: number | null | undefined; sub?: string | null }) {
  return (
    <div className="min-w-0 rounded-lg bg-panel px-3 py-2">
      <div className="text-[11px] text-ink/70">{label}</div>
      <div className="text-base font-semibold tabular-nums">{countText(value)}</div>
      {sub && <div className="text-[11px] text-ink/70">{sub}</div>}
    </div>
  );
}

function ClusterCard({ c, offsetMs }: { c: ClusterSummary; offsetMs: number }) {
  const sync = useSyncCluster();
  const meta = clusterStatusMeta(c.status);
  const counts = c.counts;
  const run = c.last_run;
  const failing = c.status === "failing" || c.status === "token_unreadable"
    || (c.last_reason !== null && c.consecutive_failures > 0);
  const canSync = c.enabled && c.token === "set" && c.status !== "syncing";
  const syncHint = !c.enabled ? "동기화가 꺼진 클러스터입니다"
    : c.token !== "set" ? "토큰이 있어야 동기화합니다" : c.status === "syncing" ? "이미 동기화 중입니다" : undefined;
  const nextDue = c.enabled ? plusSeconds(c.last_attempt_at, c.interval_seconds) : null;
  const expDays = daysUntil(c.token_expires_at, Date.now() + offsetMs);
  const warnings = run?.warnings ?? [];

  return (
    <Card className="flex min-w-0 flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold [overflow-wrap:anywhere]">{c.name}</h3>
          <Mono className="text-ink/80">{c.api_server}</Mono>
        </div>
        <StatusPill state={c.status} label={meta.label} variant={meta.variant} help={meta.help} />
      </div>

      <div className="flex flex-wrap gap-1.5">
        <Tag tone={c.tls_mode === "insecure" ? "bad" : "neutral"}>{`TLS ${TLS_MODE_LABEL[c.tls_mode] ?? c.tls_mode}`}</Tag>
        {c.tls_server_name && <Tag>{`서버 이름 ${c.tls_server_name}`}</Tag>}
        <Tag tone={c.token === "set" ? (expDays !== null && expDays < 14 ? "warn" : "neutral") : "bad"}>
          {`토큰 ${TOKEN_LABEL[c.token] ?? c.token}${expDays !== null && c.token === "set"
            ? expDays < 0 ? " · 만료됨" : ` · ${Math.floor(expDays)}일 뒤 만료` : ""}`}
        </Tag>
        {c.server_version && <Tag>{`k8s ${c.server_version}`}</Tag>}
        {!c.enabled && <Tag>동기화 꺼짐</Tag>}
      </div>

      <dl className="grid grid-cols-3 gap-2 text-sm">
        <div className="min-w-0">
          <dt className="text-xs text-ink/70">마지막 성공</dt>
          <dd className={c.last_success_at ? "font-medium" : "text-ink/70"}>
            <Ago iso={c.last_success_at} empty="아직 없음" offsetMs={offsetMs} />
          </dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-ink/70">다음 예정</dt>
          <dd className="font-medium">{!c.enabled ? <span className="text-ink/70">꺼짐</span>
            : c.token !== "set" ? <span className="text-ink/70">토큰 필요</span>
            : <Until iso={nextDue} offsetMs={offsetMs} />}</dd>
        </div>
        <div className="min-w-0">
          <dt className="text-xs text-ink/70">연속 실패</dt>
          <dd className={`font-medium tabular-nums ${c.consecutive_failures > 0 ? "text-bad" : ""}`}>
            {`${c.consecutive_failures}회`}
          </dd>
        </div>
      </dl>

      {counts === null ? (
        <p className="rounded-lg bg-panel px-3 py-2 text-sm text-ink/70">
          아직 받은 목록이 없습니다 — 개수는 모름(첫 동기화가 성공하면 채워집니다).
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Count label="namespace" value={counts.namespaces} />
          <Count label="PV 이름(PVC)" value={counts.pvcs}
                 sub={counts.pvcs_bound === null ? null : `Bound ${countText(counts.pvcs_bound)}`} />
          <Count label="권한 사용자" value={counts.users} />
          <Count label="RoleBinding" value={counts.bindings}
                 sub={counts.recognized_bindings === null ? null : `인식 ${countText(counts.recognized_bindings)}`} />
          <Count label="StorageClass" value={counts.storage_classes}
                 sub={counts.storage_classes === null ? "목록 권한 없음·모름" : null} />
          <Count label="볼륨(PV)" value={counts.pvs} sub={counts.pvs === null ? "목록 권한 없음·모름" : null} />
        </div>
      )}

      {run && (
        <p className="text-xs text-ink/80">
          <span className="text-ink/70">마지막 실행 </span>
          {`${run.node_name ?? "노드 모름"}${run.role ? `(${ROLE_LABEL[run.role] ?? run.role})` : ""} · `}
          {`${TRIGGER_LABEL[run.trigger] ?? run.trigger} · ${durationText(run.duration_ms)} · `}
          <span className={runStatusMeta(run.status).variant === "bad" ? "text-bad" : ""}>{runStatusMeta(run.status).label}</span>
          {run.reason && run.status !== "applied" ? ` · ${reasonText(run.reason)}` : ""}
        </p>
      )}

      {failing && c.last_reason && (
        <div className="rounded-lg border border-bad/30 bg-badbg/40 px-3 py-2 text-sm">
          <p className="font-medium text-bad">{reasonText(c.last_reason)}</p>
          {c.last_error && <Mono className="mt-1 block text-ink/80">{c.last_error}</Mono>}
          <p className="mt-1 text-xs text-ink/70">
            {c.last_success_at ? "권한·PV 판정은 마지막 성공 스냅숏 기준으로 계속됩니다."
              : "한 번도 성공하지 않아 이 클러스터의 PV 권한은 「모름」입니다 — PV 작업을 받지 않습니다."}
          </p>
        </div>
      )}

      {warnings.length > 0 && (
        <ul className="space-y-0.5 text-xs text-attn">
          {warnings.map((w, i) => <li key={i}>{`경고: ${warningText(w)}`}</li>)}
        </ul>
      )}

      {c.duplicate_of && (
        <p className="rounded-lg border border-attn/40 bg-attnbg/50 px-3 py-2 text-xs text-attn">
          {`같은 클러스터로 보입니다(kube-system uid 일치): ${c.duplicate_of} — 같은 PVC 가 두 번 보여 사용자가 헷갈릴 수 있습니다.`}
        </p>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-line pt-3">
        <Button variant="outline" className="whitespace-nowrap" disabled={!canSync || sync.isPending} title={syncHint}
                onClick={() => sync.mutate(c.name)}>
          {sync.isPending ? "요청 중…" : "지금 동기화"}
        </Button>
        <ClusterDialog mode="edit" cluster={c}
                       trigger={<Button variant="ghost" className="whitespace-nowrap">편집</Button>} />
        <Link to={`/admin/k8s/history?cluster=${encodeURIComponent(c.name)}`}
              className="whitespace-nowrap px-1 text-sm text-accent hover:underline">이력</Link>
        <span className="ml-auto"><DeleteClusterButton name={c.name} /></span>
      </div>
      {(sync.isError || sync.isSuccess) && (
        <p role="status" className={`text-xs ${sync.isError ? "text-bad" : "text-ink/70"}`}>
          {sync.isError ? errText(sync.error) : "요청했습니다 — 호출 노드가 다음 확인(수 초 안)에 가져갑니다"}
        </p>
      )}
    </Card>
  );
}

/** 가입 신원 경고(2026-10-26 보안 리뷰): 권한이 「계정 아이디 == RoleBinding 의 ID」로 묶여, 인증 메일이 실제로 가지
 *  않으면(stub — 코드를 화면에 보여 줌, open — 인증 꺼짐) 아직 계정이 없는 사람의 아이디로 누구나 먼저 가입해 그 사람의 PV 권한을
 *  가져갈 수 있다. 기동을 막지는 않는다(테스트베드는 stub 로 돈다) — 운영자가 보게만 한다. */
export function SignupGuardNote({ guard }: { guard: string | undefined }) {
  if (guard !== "stub" && guard !== "open") return null;
  return (
    <div role="note" aria-label="가입 신원 경고" className="rounded-lg border border-attn/40 bg-attnbg/50 px-4 py-3 text-sm text-attn">
      <p className="font-medium">
        {guard === "stub"
          ? "가입 인증 메일이 실제로 발송되지 않습니다(stub — 인증번호를 화면에 보여 줍니다)"
          : "가입 인증이 꺼져 있습니다(DMS_ACCOUNT_VERIFICATION_REQUIRED=false)"}
      </p>
      <p className="mt-1 text-ink/80">
        k8s 연동에서는 계정 아이디가 곧 RoleBinding 의 ID 라, 아직 DMS 계정이 없는 사람의 아이디로 누구나 먼저 가입하면 그 사람의 PV
        권한을 가져갑니다. 운영에서는 <Link to="/admin/mail" className="text-accent hover:underline">메일 설정</Link>에서 Knox 릴레이를
        켜 <span className="font-mono">아이디@회사 도메인</span> 메일로 본인을 확인하게 하세요(테스트베드라면 무시해도 됩니다).
      </p>
    </div>
  );
}

function EmptyClusters() {
  return (
    <InfoCard className="space-y-3">
      <p className="font-medium">등록된 클러스터가 없습니다</p>
      <ol className="list-decimal space-y-1 pl-5 text-ink/80">
        <li>대상 클러스터에 읽기 전용 계정(ServiceAccount·ClusterRole·토큰 Secret)을 만듭니다 — 등록 창에 YAML 이 있습니다.</li>
        <li>위에서 호출 노드(주·보조)를 지정하고, 대상 API 서버 방화벽에 그 노드 IP 를 허용합니다.</li>
        <li>클러스터를 등록하면 다음 주기(또는 「지금 동기화」)에 PVC·RoleBinding 을 받아 옵니다.</li>
        <li>「StorageClass」 탭에서 StorageClass 를 DMS 스토리지에 연결하면 PV 경로가 정해집니다.</li>
      </ol>
      <ClusterDialog mode="create" trigger={<Button>클러스터 등록</Button>} />
    </InfoCard>
  );
}

export function K8sOverview() {
  const q = useK8sOverview();
  const syncAll = useSyncAllClusters();
  const ov = q.data;
  // 서버 시계 - 브라우저 시계: 「n분 전」을 서버 기준으로 잰다(브라우저 시계가 틀려도 거짓 나이를 만들지 않게).
  const offsetMs = useMemo(() => {
    const t = ov ? Date.parse(ov.now) : NaN;
    return Number.isNaN(t) || q.dataUpdatedAt === 0 ? 0 : t - q.dataUpdatedAt;
  }, [ov, q.dataUpdatedAt]);
  const clusters = Array.isArray(ov?.clusters) ? ov!.clusters : [];
  const syncable = clusters.filter((c) => c.enabled && c.token === "set" && c.status !== "syncing").map((c) => c.name);
  const t = ov?.totals;

  return (
    <section className="space-y-4">
      <K8sHeader
        title="k8s 연동"
        description="k8s 클러스터의 PVC·RoleBinding 을 주기적으로 받아 PV 경로와 사용자 권한을 판정합니다. 권한·경로는 마지막으로 성공한 동기화 스냅숏 기준입니다."
        actions={<>
          {clusters.length > 0 && (
            <Button variant="outline" className="whitespace-nowrap" disabled={syncable.length === 0 || syncAll.isPending}
                    onClick={() => syncAll.mutate(syncable)}>
              {syncAll.isPending ? "요청 중…" : "모두 지금 동기화"}
            </Button>
          )}
          <ClusterDialog mode="create" trigger={<Button className="whitespace-nowrap">클러스터 등록</Button>} />
        </>} />

      <SignupGuardNote guard={ov?.signup_guard} />

      {q.isError && (
        <p role="alert" className="text-sm text-bad">
          {ov ? `개요를 다시 불러오지 못했습니다 — 아래는 마지막으로 받은 값입니다(${errText(q.error)})`
            : `개요를 불러오지 못했습니다 — ${errText(q.error)}`}
        </p>
      )}
      {syncAll.data && (
        <p role="status" className={`text-sm ${syncAll.data.failed.length > 0 ? "text-bad" : "text-ink/70"}`}>
          {syncAll.data.failed.length > 0
            ? `${syncAll.data.requested}개 요청 · 실패 ${syncAll.data.failed.join(", ")}`
            : `${syncAll.data.requested}개 클러스터 동기화를 요청했습니다`}
        </p>
      )}

      {q.isLoading ? <p className="text-ink/70">불러오는 중…</p> : ov && (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label="클러스터" value={countText(t?.clusters)} />
            <Stat label="namespace" value={countText(t?.namespaces)} />
            <Stat label="PV 이름(PVC)" value={countText(t?.pvcs)} />
            <Stat label="권한 바인딩" value={countText(t?.bindings)} />
            <Stat label="권한 사용자" value={countText(t?.users)} />
          </div>

          <CallerNodesCard ov={ov} offsetMs={offsetMs} />

          <div className="space-y-3">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold">클러스터</h2>
              <span className="text-xs text-ink/70">
                {`${intervalText(ov.settings.interval_seconds)}마다 동기화 · 화면은 ${clusters.some((c) => c.status === "syncing") ? 3 : 10}초마다 새로 읽습니다`}
              </span>
            </div>
            {clusters.length === 0 ? <EmptyClusters /> : (
              <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">
                {clusters.map((c) => <ClusterCard key={c.name} c={c} offsetMs={offsetMs} />)}
              </div>
            )}
          </div>
        </>
      )}
    </section>
  );
}
