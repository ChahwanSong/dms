import { useState } from "react";
import { Link } from "react-router-dom";
import { useQueueMetrics } from "./useMetrics";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Table } from "../../components/ui/Table";
import type {
  QueueControl, QueueFlow, QueuePipeline, QueuePodgroup, QueueStage, QueueWaitEntry, QueueWaitStage,
} from "../../lib/types";
import { FlowLegend, QueueFlowHero, flowSummary } from "./QueueFlowHero";
import { STAGE_LABEL, TEXT_TONE, waitText, waitTone, type Tone } from "./queueStage";

// 단계 어휘·색 문턱은 queueStage 로 옮겼다(흐름 히어로와 함께 쓴다) -- 옛 import 경로를 그대로 둔다.
export { STAGE_LABEL, waitText, waitTone };

// 큐 현황(2026-10-16 재설계, 사용자 요청 「대시보드에서 큐 현황에 대해 조금만 더 자세히 그리고 개선해서 보여주면
// 좋겠어.」). 옛 카드는 Volcano PodGroup 만 보여 DMS 안쪽의 대기(계획·픽업·사전 점검·확인·배치)가 통째로 안 보였다 --
// 테스트베드 실측(잡 파드가 BestEffort 이던 때)으로 Volcano 대기는 늘 5~10초였고 정말 긴 대기는 사람의 확인(p95 7.3h)이었다
// -- 2026-10-16 부터 워커가 CPU 를 요청해 Volcano 큐도 실제로 줄을 선다. 그래서 ① 요청이 지나가는
// 단계(건수 + 가장 오래 기다린 시간), ② 기다리는 항목 표(단계 필터)를 그린다. 판정·나이 계산은 전부 서버
// (queue_pipeline)가 했다 -- 여기는 그리기와 색 문턱만 정한다.
// 같은 날 ①은 숫자 띠에서 흐름 히어로(QueueFlowHero -- 「흐르는 파스텔 리본」)로 바뀌었고 이 카드는 대시보드 맨 위로
// 올라갔다(사용자 지시 「3번의 내용이 대시보드의 제일 위에 대표적으로 보여지게 해줘」). 표·칩은 그대로다.

const QUEUE_STATE_VARIANT: Record<string, "ok" | "bad"> = {
  Open: "ok", Closed: "bad",
};

const WAIT_STAGES: QueueWaitStage[] = ["planning", "pickup", "preflight", "volcano_wait", "confirm", "batch"];
// DMS DB 에서 오는 단계(Volcano 대기만 k8s 에서 온다) -- 「알 수 없음」 안내 문구를 원인별로 가른다.
const DB_STAGES = new Set<QueueStage>(["planning", "pickup", "preflight", "running", "confirm", "batch"]);

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
      if (control?.drain) return { text: "드레인 중 — 잡이 진행하지 않습니다", tone: "bad" };
      return d.exec_preflight ? { text: "실행 전 재점검", tone: "none" } : { text: "—", tone: "none" };
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
      const parts: string[] = [];
      if ((d.queued ?? 0) > 0 || !d.held) parts.push(`대기 항목 ${d.queued ?? 0}건`);
      // 미리보기 마친 자식(held) -- 확인된 배치는 실행 슬롯을, 미리보기 중인 배치는 나머지 미리보기를 기다린다.
      if (typeof d.held === "number" && d.held > 0) {
        parts.push(d.batch_status === "Running" ? `확인됨 · 실행 차례 ${d.held}건`
          : d.batch_status === "Previewing" ? `미리보기 마침 ${d.held}건(나머지 미리보기 대기)`
          : `미리보기 마침 ${d.held}건`);
      }
      if (d.max_concurrency != null) parts.push(`동시 실행 상한 ${d.max_concurrency}`);
      if (d.batch_status === "PreviewReady") parts.push("배치 확인 대기 중");
      const exp = expiryText(d.expires_in_seconds);
      if (exp) parts.push(exp);
      return { text: parts.join(" · "), tone: "none" };
    }
  }
}

function controlHelp(what: string, reason: string | null): string {
  return reason ? `${what} 사유: ${reason}` : `${what} 사유는 적혀 있지 않습니다.`;
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
  // 흐름(최근 창의 단계 사이 이동) -- 키 없는 옛 응답·null 은 모름: 입자를 그리지 않는다.
  const flow: QueueFlow | null = q.data?.flow && typeof q.data.flow === "object" && typeof q.data.flow.done === "number"
    && typeof q.data.flow.window_seconds === "number" ? q.data.flow : null;
  const summary = flowSummary(pipeline, flow);
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
      <div className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <h2 className="text-lg font-bold">큐 현황</h2>
        <span className="text-xs text-ink/60">{q.data?.queue?.name ?? "dms-data"}</span>
        {/* 알 수 없으면(큐 null·state null) 배지를 내지 않는다 -- Open 을
            추측하지 않는다(설계 §3). 용량 게이지도 없다: dms-data 큐엔
            capability/deserved 가 없어 표시할 진실이 없다(설계 §2.2). */}
        {state && (
          <StatusPill state={state}
                      variant={QUEUE_STATE_VARIANT[state] ?? "neutral"} />
        )}
        {/* 드레인 = stepper 정지(잡이 픽업·전진하지 않는다), 유지보수 = 신규 제출 503. 켜졌을 때만 -- 꺼짐은 정상이라
            배지를 내지 않는다. 운영자가 적은 사유는 배지의 설명 툴팁(help -- 마우스·포커스)으로 -- 바깥 span 의 title 은
            배지 자신의 title(상태 코드)에 가려 거의 보이지 않았다(검증 지적). */}
        {control?.drain && (
          <StatusPill state="drain" label="드레인 — 잡 진행 멈춤" variant="bad"
                      help={controlHelp("stepper 가 잡을 픽업·전진시키지 않습니다.", control.reason)} />
        )}
        {control?.maintenance && (
          <StatusPill state="maintenance" label="유지보수 — 신규 제출 차단" variant="action"
                      help={controlHelp("새 요청 제출을 받지 않습니다(진행 중인 잡은 계속 돕니다).", control.reason)} />
        )}
        {/* 요약은 한 텍스트 노드 -- 대시보드 KPI 타일의 정확 일치 「대기」·「실행 중」과 겹치지 않는다. break-keep: 한글은
            음절 사이에서도 줄이 바뀐다 -- 마디 안의 줄바꿈 없는 공백(flowSummary)과 함께 「 · 」 뒤에서만 바뀌게. */}
        {!q.isLoading && summary !== null && (
          <span className="flex items-center gap-0.5 text-sm text-ink/70 tabular-nums break-keep lg:ml-auto">
            {summary}
            <FlowLegend />
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
          <QueueFlowHero pipeline={pipeline} flow={flow} control={control} />
          {/* PodGroup phase 카운터(슬라이스 17)는 흐름 아래 한 줄로 남긴다 -- Running 은 실제로 스케줄된 gang 수다.
              모르면(403/CRD 부재) 줄 자체를 내지 않는다: "Pending 0" 은 우리가 모르는 주장이다. */}
          {pods !== null && (
            <p className="mt-2.5 text-xs text-muted tabular-nums">
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
