import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueueMetrics } from "./useMetrics";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Table } from "../../components/ui/Table";
import type {
  QueueControl, QueuePipeline, QueuePodgroup, QueueStage, QueueWaitEntry, QueueWaitStage,
} from "../../lib/types";

// 큐 현황(2026-10-16 재설계, 사용자 요청 「대시보드에서 큐 현황에 대해 조금만 더 자세히 그리고 개선해서 보여주면
// 좋겠어.」). 옛 카드는 Volcano PodGroup 만 보여 DMS 안쪽의 대기(계획·픽업·사전 점검·확인·배치)가 통째로 안 보였다 --
// 테스트베드 실측으로 Volcano 대기는 늘 5~10초이고 정말 긴 대기는 사람의 확인(p95 7.3h)이다. 그래서 ① 요청이 지나가는
// 단계 띠(건수 + 가장 오래 기다린 시간), ② 기다리는 항목 표(단계 필터)를 그린다. 판정·나이 계산은 전부 서버
// (queue_pipeline)가 했다 -- 여기는 그리기와 색 문턱만 정한다.

// 초 -> "n초"/"n분 n초"/"n시간 n분". RequestDetail 의 durationText 는 시각 2개를
// 받는 다른 시그니처라 재사용 불가(서버가 이미 초를 계산해 준다).
export function waitText(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds) || seconds < 0) return "—";
  if (seconds < 60) return `${seconds}초`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return seconds % 60 === 0 ? `${m}분` : `${m}분 ${seconds % 60}초`;
  const h = Math.floor(m / 60);
  return m % 60 === 0 ? `${h}시간` : `${h}시간 ${m % 60}분`;
}

const QUEUE_STATE_VARIANT: Record<string, "ok" | "bad"> = {
  Open: "ok", Closed: "bad",
};

// running 을 그냥 「실행 중」이라 부르지 않는다: 같은 화면 KPI 타일 「실행 중」은 24h 창 잡 집계이고 확인 대기
// (ConfirmPending)까지 센다 -- 같은 이름에 다른 숫자가 나란히 서면 어느 쪽도 못 믿게 된다. 이 칸은 Volcano 에서
// 실제로 도는 잡이라 「Volcano 대기」와 짝을 맞춘다.
export const STAGE_LABEL: Record<QueueStage, string> = {
  planning: "계획 대기", pickup: "잡 픽업", preflight: "사전 점검", volcano_wait: "Volcano 대기",
  running: "Volcano 실행 중", confirm: "확인 대기", batch: "배치 대기",
};
const STRIP: QueueStage[] = ["planning", "pickup", "preflight", "volcano_wait", "running", "confirm", "batch"];
const WAIT_STAGES: QueueWaitStage[] = ["planning", "pickup", "preflight", "volcano_wait", "confirm", "batch"];
// DMS DB 에서 오는 단계(Volcano 대기만 k8s 에서 온다) -- 「알 수 없음」 안내 문구를 원인별로 가른다.
const DB_STAGES = new Set<QueueStage>(["planning", "pickup", "preflight", "running", "confirm", "batch"]);

// 단계 설명(칸의 title). 주기·유예 값은 기본 설정(config.py)이다.
const STAGE_HELP: Record<QueueStage, string> = {
  planning: "요청이 접수돼 planner 가 도구·노드를 정하기를 기다립니다(10초 주기, 노드 신원 전파 유예 최대 5분).",
  pickup: "계획된 잡을 stepper 가 집기를 기다립니다(5초 주기). 드레인 중이면 여기서 멈춥니다.",
  preflight: "노드 사전 점검 파드가 도는 중입니다.",
  volcano_wait: "Volcano 가 gang(필요한 파드 전부)을 아직 올리지 못했습니다(PodGroup Pending·Inqueue).",
  running: "Volcano 에서 실행 중인 미리보기·실행 잡입니다(대기가 아닙니다).",
  confirm: "미리보기가 끝나 사람의 컨펌(단건) 또는 배치 확인을 기다립니다. 유효기간이 지나면 거부됩니다.",
  batch: "배치 항목이 동시 실행 상한이 풀리기를 기다립니다(아직 요청이 되지 않았습니다).",
};

// 색 문턱(초) -- 가장 오래 기다린 시간이 attn 이상이면 주의(앰버), bad 이상이면 경고(적색). 기계 단계는 주기가 초 단위라
// 5분이면 이미 이상하다(planner 신원 전파 유예 상한도 5분). 확인 대기는 사람이 누르는 단계라 길다: 유효기간(기본 24시간)의
// 1/6·1/2. 배치 대기는 큰 배치가 상한만큼씩 도는 정상 상태라 적색을 쓰지 않는다(알람 피로). 실행 중은 대기가 아니라 색이 없다.
const TONE_LIMITS: Record<QueueStage, { attn: number; bad: number | null } | null> = {
  planning: { attn: 300, bad: 1800 },
  pickup: { attn: 300, bad: 1800 },
  preflight: { attn: 300, bad: 1800 },
  volcano_wait: { attn: 300, bad: 1800 },
  running: null,
  confirm: { attn: 4 * 3600, bad: 12 * 3600 },
  batch: { attn: 3600, bad: null },
};
type Tone = "none" | "attn" | "bad";
export function waitTone(stage: QueueStage, seconds: number | null): Tone {
  const lim = TONE_LIMITS[stage];
  if (lim === null || seconds === null) return "none";
  if (lim.bad !== null && seconds >= lim.bad) return "bad";
  return seconds >= lim.attn ? "attn" : "none";
}
const CELL_TONE: Record<Tone, string> = {
  none: "border-line bg-surface", attn: "border-attn/40 bg-attnbg", bad: "border-bad/40 bg-badbg",
};
const TEXT_TONE: Record<Tone, string> = { none: "text-muted", attn: "text-attn", bad: "text-bad" };

// 계획 유예·오류 이벤트(planner) -- 원문 event_type 은 title 로 남는다.
const PLANNING_NOTE: Record<string, string> = {
  identity_propagating: "노드 신원 전파 대기",
  awaiting_requested_nodes: "요청한 노드 수 대기",
  plan_error: "계획 오류 — 다음 주기에 다시 시도",
};
const PHASE_REF_LABEL: Record<string, string> = { preview: "미리보기", execution: "실행" };

function expiryText(sec: number | null | undefined): string | null {
  if (sec === null || sec === undefined) return null;
  return sec === 0 ? "곧 만료" : `만료까지 ${waitText(sec)}`;
}

function StageCell({ stage, pipeline }: { stage: QueueStage; pipeline: QueuePipeline | null }) {
  const stat = pipeline?.[stage] ?? null;
  const tone = stat && stat.count > 0 ? waitTone(stage, stat.oldest_seconds) : "none";
  // 0 건이면 「없음」, 모르면 「알 수 없음」 -- 둘을 같은 「—」로 접지 않는다(null ≠ 0).
  let sub: string;
  if (stat === null) sub = "알 수 없음";
  else if (stat.count === 0) sub = stage === "running" ? "없음" : "대기 없음";
  else sub = `${stage === "running" ? "가장 긴" : "가장 오래"} ${waitText(stat.oldest_seconds)}`;
  const extra: string[] = [];
  if (pipeline?.confirm && stage === "confirm" && pipeline.confirm.count > 0) {
    // 띠 칸은 좁다(375 에선 2열) -- 「가장 빠른 만료까지 …」는 세 줄로 접혔다. 칸 안에서는 「다음 만료」가 같은 뜻이다.
    const sec = pipeline.confirm.soonest_expiry_seconds;
    if (sec !== null) extra.push(sec === 0 ? "곧 만료" : `다음 만료 ${waitText(sec)} 후`);
  }
  if (pipeline?.batch && stage === "batch" && pipeline.batch.preview_ready_batches > 0) {
    extra.push(`확인 대기 배치 ${pipeline.batch.preview_ready_batches}`);
  }
  if (pipeline?.running && stage === "running" && pipeline.running.includes_volcano_wait) {
    extra.push("Volcano 대기 포함");
  }
  return (
    <div data-testid={`queue-stage-${stage}`} data-tone={tone} title={STAGE_HELP[stage]}
         className={`min-w-0 rounded-lg border px-3 py-2 ${CELL_TONE[tone]}`}>
      <div className="text-xs text-muted break-keep">{STAGE_LABEL[stage]}</div>
      <div className="text-xl font-semibold tabular-nums">{stat === null ? "—" : stat.count}</div>
      <div className={`text-xs break-keep ${TEXT_TONE[tone]}`}>{sub}</div>
      {extra.map((t) => <div key={t} className="text-xs text-muted break-keep">{t}</div>)}
    </div>
  );
}

function RequestCell({ e }: { e: QueueWaitEntry }) {
  const batchLink = e.batch_id && (
    <Link to={`/admin/batches/${e.batch_id}`} title={e.batch_id}
          className="text-xs text-accent hover:underline break-keep">
      {`배치 ${e.detail.batch_name ?? e.batch_id.slice(0, 12)}`}
    </Link>
  );
  if (e.request_id) {
    return (
      <>
        <Link to={`/jobs/${e.request_id}`} title={e.request_id}
              className="font-mono text-xs text-accent hover:underline">{e.request_id.slice(0, 12)}</Link>
        {batchLink && <div>{batchLink}</div>}
      </>
    );
  }
  if (batchLink) return batchLink;
  // 대조되지 않은 PodGroup -- 요청을 지어내지 않고 PodGroup 이름을 그대로 보인다.
  return <span className="font-mono text-xs text-muted break-all">{e.detail.podgroup ?? "—"}</span>;
}

function noteFor(e: QueueWaitEntry, control: QueueControl | null): { text: string; sub?: string | null; tone: Tone } {
  const d = e.detail;
  switch (e.stage) {
    case "planning":
      return d.note
        ? { text: PLANNING_NOTE[d.note] ?? d.note, sub: d.message, tone: d.note === "plan_error" ? "bad" : "none" }
        : { text: "—", tone: "none" };
    case "pickup":
    case "preflight":
      return control?.drain
        ? { text: "드레인 중 — 잡이 진행하지 않습니다", tone: "bad" }
        : { text: "—", tone: "none" };
    case "volcano_wait": {
      const head = [
        d.phase_ref ? `${PHASE_REF_LABEL[d.phase_ref] ?? d.phase_ref} vcjob` : null,
        d.phase, d.min_member != null ? `gang ${d.min_member}` : null, d.reason,
      ].filter((x): x is string => !!x);
      return { text: head.length ? head.join(" · ") : "—", sub: d.message, tone: "none" };
    }
    case "confirm": {
      const exp = expiryText(d.expires_in_seconds);
      const expTone: Tone = d.expires_in_seconds == null ? "none"
        : d.expires_in_seconds <= 3600 ? "bad" : d.expires_in_seconds <= 4 * 3600 ? "attn" : "none";
      if (e.request_id) return { text: exp ?? "—", tone: expTone };
      const head = d.batch_status === "PreviewReady" ? "배치 확인 대기"
        : d.batch_status === "Previewing" ? "배치 미리보기 진행 중"
        : d.batch_status === "Running" ? "배치 실행 차례 대기" : "배치";
      return { text: [head, `자식 ${d.jobs ?? 0}건`, exp].filter(Boolean).join(" · "), tone: expTone };
    }
    case "batch": {
      const parts = [`대기 항목 ${d.queued ?? 0}건`];
      if (d.max_concurrency != null) parts.push(`동시 실행 상한 ${d.max_concurrency}`);
      if (d.batch_status === "PreviewReady") parts.push("배치 확인 대기 중");
      return { text: parts.join(" · "), tone: "none" };
    }
  }
}

// 일부 단계만 못 읽었을 때의 안내 -- 원인 축이 다르다(Volcano 대기는 k8s 권한·CRD, 나머지는 DMS DB).
function unknownText(stages: QueueWaitStage[]): string {
  const parts = [`알 수 없는 단계: ${stages.map((s) => STAGE_LABEL[s]).join("·")} — 목록에서 빠져 있습니다.`];
  if (stages.includes("volcano_wait")) parts.push("Volcano 대기는 권한(RBAC) 또는 Volcano 설치를 확인하세요.");
  if (stages.some((s) => DB_STAGES.has(s))) parts.push("DMS 단계는 api 로그(metrics/queue read failed)를 확인하세요.");
  return parts.join(" ");
}

type Filter = QueueWaitStage | "all";

function chipCls(on: boolean) {
  return `rounded-full border px-2.5 py-1 text-xs whitespace-nowrap focus-visible:outline-none focus-visible:ring-2
          focus-visible:ring-accent ${on ? "border-accent bg-infobg font-semibold text-accent"
                                         : "border-line text-ink/70 hover:bg-panel"}`;
}

export function QueueSection() {
  const q = useQueueMetrics();
  const [filter, setFilter] = useState<Filter>("all");
  // null = 알 수 없음(403/CRD 부재), [] = 비었음 -- asArray/?? [] 로 접지 않는다.
  // 접으면 권한 누락이 "큐가 한가함"으로 렌더된다(설계 §4). 배열도 null 도 아닌
  // 페이로드(파싱 사고)는 "비었음"이 아니라 "알 수 없음" 쪽으로 접는다 -- 모르는
  // 것을 0 이라고 말하지 않는다는 같은 규칙이다. 새 키(2026-10-16)도 같다: 키가 없는
  // 옛 응답(undefined)은 모름이다.
  const raw = q.data?.podgroups;
  const pods: QueuePodgroup[] | null = Array.isArray(raw) ? raw : null;
  const waiting: QueueWaitEntry[] | null = Array.isArray(q.data?.waiting) ? q.data.waiting : null;
  const pipeline: QueuePipeline | null =
    q.data?.pipeline && typeof q.data.pipeline === "object" ? q.data.pipeline : null;
  const control: QueueControl | null = q.data?.control ?? null;
  const truncated = new Set(Array.isArray(q.data?.waiting_truncated) ? q.data.waiting_truncated : []);
  const state = q.data?.queue?.state ?? null;

  const counts = { Pending: 0, Inqueue: 0, Running: 0 };
  for (const pg of pods ?? []) {
    if (pg.phase === "Pending" || pg.phase === "Inqueue" || pg.phase === "Running") counts[pg.phase] += 1;
  }
  // 목록에서 빠진(못 읽은) 대기 단계 -- 표만 보고 「없음」으로 읽지 않게 따로 알린다.
  const unknown = WAIT_STAGES.filter((s) => pipeline?.[s] == null);
  const byStage = (s: QueueWaitStage) => (waiting ?? []).filter((e) => e.stage === s);
  const rows = filter === "all" ? (waiting ?? []) : byStage(filter);

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <h2 className="font-medium">큐 현황</h2>
        <span className="text-muted text-xs">{q.data?.queue?.name ?? "dms-data"}</span>
        {/* 알 수 없으면(큐 null·state null) 배지를 내지 않는다 -- Open 을
            추측하지 않는다(설계 §3). 용량 게이지도 없다: dms-data 큐엔
            capability/deserved 가 없어 표시할 진실이 없다(설계 §2.2). */}
        {state && (
          <StatusPill state={state}
                      variant={QUEUE_STATE_VARIANT[state] ?? "neutral"} />
        )}
        {/* 드레인 = stepper 정지(잡이 픽업·전진하지 않는다), 유지보수 = 신규 제출 503. 켜졌을 때만 -- 꺼짐은 정상이라
            배지를 내지 않는다. 사유는 운영자가 적은 문장이라 title 로 둔다. */}
        {control?.drain && (
          <span title={control.reason ?? undefined}>
            <StatusPill state="drain" label="드레인 — 잡 진행 멈춤" variant="bad" />
          </span>
        )}
        {control?.maintenance && (
          <span title={control.reason ?? undefined}>
            <StatusPill state="maintenance" label="유지보수 — 신규 제출 차단" variant="action" />
          </span>
        )}
      </div>
      {/* 첫 응답 전에도 data 는 undefined 라 아래 「알 수 없음」과 모양이 같다.
          구분하지 않으면 매 첫 페인트가 "권한(RBAC)을 확인하세요"로 깜빡이며
          없는 원인을 가리킨다 -- 잡 통계 카드와 같은 로딩 문구를 먼저 낸다. */}
      {q.isLoading ? (
        <p className="text-muted text-sm">불러오는 중…</p>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
            {STRIP.map((s) => <StageCell key={s} stage={s} pipeline={pipeline} />)}
          </div>
          {/* PodGroup phase 카운터(슬라이스 17)는 띠 아래 한 줄로 남긴다 -- Running 은 실제로 스케줄된 gang 수다.
              모르면(403/CRD 부재) 줄 자체를 내지 않는다: "Pending 0" 은 우리가 모르는 주장이다. */}
          {pods !== null && (
            <p className="mt-2 text-xs text-muted tabular-nums">
              {`Volcano PodGroup — Pending ${counts.Pending} · Inqueue ${counts.Inqueue} · Running ${counts.Running}`}
            </p>
          )}
          {/* KPI 타일의 「대기」는 24h 창 DB 집계, 이 카드는 무윈도 라이브 -- 두 숫자는 어긋날 수 있고 그래야
              정상이다. 라벨이 그 사실을 드러낸다(설계 §3 주의). */}
          <div className="mt-4 mb-2 flex flex-wrap items-center gap-2">
            <h3 className="font-medium text-sm mr-1">지금 대기 중</h3>
            {waiting !== null && (
              <div role="group" aria-label="단계 필터" className="flex flex-wrap gap-1.5">
                <button type="button" aria-pressed={filter === "all"} className={chipCls(filter === "all")}
                        onClick={() => setFilter("all")}>{`전체 ${waiting.length}`}</button>
                {WAIT_STAGES.map((s) => {
                  const n = pipeline?.[s] == null ? "—" : `${byStage(s).length}${truncated.has(s) ? "+" : ""}`;
                  return (
                    <button key={s} type="button" aria-pressed={filter === s} className={chipCls(filter === s)}
                            onClick={() => setFilter(s)}>{`${STAGE_LABEL[s]} ${n}`}</button>
                  );
                })}
              </div>
            )}
          </div>
          {waiting === null ? (
            <p className="text-muted text-sm break-keep">
              대기 정보를 알 수 없습니다 — 권한(RBAC) 또는 Volcano 설치를 확인하세요.
            </p>
          ) : (
            <>
              {unknown.length > 0 && (
                <p className="mb-2 text-xs text-attn break-keep">{unknownText(unknown)}</p>
              )}
              {truncated.size > 0 && (
                <p className="mb-2 text-xs text-muted break-keep">
                  {`단계마다 가장 오래 기다린 20건만 보입니다: ${[...truncated].map((s) => STAGE_LABEL[s]).join("·")}`}
                </p>
              )}
              {rows.length === 0 ? (
                <p className="text-muted text-sm">
                  {filter === "all" ? "대기 중인 잡이 없습니다."
                    : pipeline?.[filter] == null ? `${STAGE_LABEL[filter]} 정보를 알 수 없습니다.`
                    : `${STAGE_LABEL[filter]} 단계에 대기 중인 항목이 없습니다.`}
                </p>
              ) : (
                <div className="max-h-[28rem] overflow-y-auto">
                  <Table>
                    <thead>
                      <tr className="text-muted">
                        <th className="py-1 pr-3 whitespace-nowrap">단계</th>
                        <th className="pr-3 whitespace-nowrap">요청</th>
                        <th className="pr-3 whitespace-nowrap">요청자</th>
                        <th className="pr-3 whitespace-nowrap">작업</th>
                        <th className="pr-3 whitespace-nowrap">우선순위</th>
                        <th className="pr-3 whitespace-nowrap">대기 시간</th>
                        <th className="whitespace-nowrap">사유/메모</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((e) => {
                        const note = noteFor(e, control);
                        const tone = waitTone(e.stage, e.wait_seconds);
                        const key = `${e.stage}:${e.job_id ?? e.request_id ?? e.batch_id ?? e.detail.podgroup}`;
                        return (
                          <tr key={key} className="border-t border-black/5 align-top">
                            <td className="py-1 pr-3 whitespace-nowrap text-xs">{STAGE_LABEL[e.stage]}</td>
                            <td className="py-1 pr-3"><RequestCell e={e} /></td>
                            <td className="py-1 pr-3 whitespace-nowrap">{e.requester ?? "—"}</td>
                            <td className="py-1 pr-3">{e.operation ?? "—"}</td>
                            <td className="py-1 pr-3 whitespace-nowrap">
                              {e.priority ?? (e.batch_id && !e.request_id ? "정책 기본" : "—")}
                            </td>
                            <td className={`py-1 pr-3 whitespace-nowrap tabular-nums ${tone === "none" ? "" : TEXT_TONE[tone]}`}>
                              {waitText(e.wait_seconds)}
                            </td>
                            <td className="py-1 min-w-[12rem] text-xs break-keep">
                              <span className={note.tone === "none" ? "" : TEXT_TONE[note.tone]}
                                    title={e.stage === "planning" && e.detail.note ? e.detail.note : undefined}>
                                {note.text}
                              </span>
                              {note.sub && <div className="text-muted break-words">{note.sub}</div>}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </Table>
                </div>
              )}
            </>
          )}
        </>
      )}
    </Card>
  );
}
