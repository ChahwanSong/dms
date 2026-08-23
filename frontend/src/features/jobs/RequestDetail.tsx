import { useParams } from "react-router-dom";
import { useRequest, useRequestJobs, useCancelJob, useCancelRequest } from "./useJobs";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { isTerminal } from "../../lib/jobState";
import { ConfirmDialog } from "./ConfirmDialog";
import { Timeline } from "./Timeline";
import { JobViewer } from "./JobViewer";
import { ApiError, reasonText } from "../../lib/api";
import { absSummary, pathSummary } from "../../lib/storagePaths";
import { toolSummary } from "../../lib/jobTool";
import { useStorageRoots } from "../storages/useUserStorages";
import type { DataJob, RequestDetail as RequestDetailType } from "../../lib/types";

// 요청 상태의 종단 집합은 잡 상태(jobState.ts의 isTerminal)와 다르다 —
// "Conflict"를 포함하고 "PreviewExpired"는 포함하지 않는다.
const TERMINAL_REQUEST_STATES = new Set(["Succeeded", "Failed", "Rejected", "Conflict", "Cancelled"]);
const isRequestTerminal = (s: string) => TERMINAL_REQUEST_STATES.has(s);

function durationText(from?: string, to?: string): string {
  if (!from || !to) return "—";
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  return s % 60 === 0 ? `${m}분` : `${m}분 ${s % 60}초`;
}

// severity별로 text-bad/text-muted만 쓴다(다른 색 유틸은 이 카드의 몫이 아니다) --
// error만 눈에 띄게, warning/info는 muted로 뭉뚱그린다.
function eventSeverityClass(severity: string): string {
  return severity === "error" ? "text-bad" : "text-muted";
}

// I3: stepper.py의 summary_unreadable은 message가 아예 없이 payload={"ref": ref}만
// 있고, terminate_failed는 message=exc.reason_code가 event_type과 완전히 중복돼서
// 실질적으로 payload의 ref가 운영자가 얻을 수 있는 유일한 단서다. payload를 렌더하지
// 않으면 화면에는 event_type 하나만 뜨고 아무 단서도 없다 -- JSON.stringify를 코드
// 블록으로 보여주는 것으로 충분하다(구조를 안다고 가정하지 않는다: dict/array/스칼라/
// null 어떤 모양이 와도 죽지 않아야 한다).
function EventPayload({ payload }: { payload: unknown }) {
  if (payload === null || payload === undefined) return null;
  let text: string;
  try {
    text = JSON.stringify(payload);
  } catch {
    text = String(payload); // 순환 참조 등 JSON.stringify가 던지는 극단적인 경우의 방어
  }
  if (!text) return null;
  return (
    <pre className="mt-0.5 text-xs text-muted whitespace-pre-wrap break-all">{text}</pre>
  );
}

function DiagnosticEvents({ req }: { req: RequestDetailType }) {
  // 방어적 정규화: events가 배열이 아니거나(백엔드 오응답) 아예 없으면 빈 배열로
  // 취급한다 -- 배열 아닌 페이로드 하나가 SPA 전체를 흰 화면으로 만든 사고가 있었다.
  const events = Array.isArray(req.events) ? req.events : [];
  if (events.length === 0) return null; // 정상 요청에 빈 카드가 뜨면 노이즈다
  return (
    <Card>
      <h2 className="text-sm font-semibold mb-2">진단 이벤트</h2>
      {req.events_truncated && (
        <p className="text-muted text-xs mb-2">
          최근 100건만 표시됩니다 — 그 이전 이벤트는 보이지 않습니다.
        </p>
      )}
      <ul className="space-y-2 text-sm">
        {events.map((e) => (
          <li key={e.id}>
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-muted tabular-nums">{e.at}</span>
              <span className={eventSeverityClass(e.severity)}>{e.event_type}</span>
              <span className="text-muted text-xs">({e.component})</span>
            </div>
            {e.message && <p className="mt-0.5">{e.message}</p>}
            <EventPayload payload={e.payload} />
          </li>
        ))}
      </ul>
    </Card>
  );
}

// 실행 도구 라벨(잡 카드 헤더). **배지가 아니라 중립 텍스트**인 이유: 이 카드의
// 배지 자리는 StatusPill 하나뿐이고 그 색은 상태 판정(ok/bad/busy) 계약이다 —
// 도구에 배지를 하나 더 달면 색이 없는 판정을 만들고(어떤 도구가 "좋은" 도구인가?)
// 두 배지가 서로 상태처럼 읽힌다. 요청 카드의 operation 라벨·배치 헤더의 메타
// 문구와 같은 관례(text-muted 작은 라벨)로 식별자 옆에 붙인다.
// 모름(계획 전)이면 아무것도 그리지 않는다 — "—" 도 거짓 표시다(null≠0).
function ToolLabel({ job }: { job: DataJob }) {
  const text = toolSummary(job);
  if (text === null) return null;
  return <span className="text-muted text-xs">{text}</span>;
}

// NodesList/JobStats 관례의 국소 사본(공용 모듈은 이르다) — 요약의 bytes 를
// 사람 표기로. scan 도 이제 bytes(실 사용량)를 낸다(2026-08-23 파서 확장).
function humanBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let v = bytes, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? v : v.toFixed(1)} ${units[i]}`;
}

function ResultSummary({ summary }: { summary: unknown }) {
  if (summary == null) return null;
  if (typeof summary === "object") {
    const entries = Object.entries(summary as Record<string, unknown>);
    if (!entries.length) return null;
    return (
      <dl className="text-sm grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 mt-1">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            {/* String(null)은 "null" -- rm 은 설계상 bytes 가 없어(도구가 미보고)
                null 이 정상이다. 대시보드와 같은 "—" 규약으로 비운다. bytes 는
                사람 표기 + 원값(정밀도 손실 없이 검증 가능하게). */}
            <dd>{v === null ? "—"
              : k === "bytes" && typeof v === "number"
                ? `${humanBytes(v)} (${v} B)`
                : String(v)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return <p className="text-sm mt-1">{String(summary)}</p>;
}

export function RequestDetail() {
  const { requestId = "" } = useParams();
  const req = useRequest(requestId);
  const jobs = useRequestJobs(requestId);
  const cancel = useCancelJob(requestId);
  const cancelRequest = useCancelRequest(requestId);
  // 스토리지 뿌리 맵(관리자 응답에만 managed_root 가 실린다 — 비관리자는 빈 맵)
  const roots = useStorageRoots();

  if (req.isLoading || jobs.isLoading) {
    return (
      <section className="space-y-4">
        <h1 className="text-2xl font-bold">요청 {requestId}</h1>
        <p className="text-muted">불러오는 중…</p>
      </section>
    );
  }

  // 잡 조회 실패를 삼키면 "잡이 0개"와 구별되지 않는다 — 요청 조회 실패와 같게 보여준다.
  if (req.isError || jobs.isError) {
    const err = (req.isError ? req.error : jobs.error) as ApiError;
    return (
      <section className="space-y-4">
        <h1 className="text-2xl font-bold">요청 {requestId}</h1>
        <p className="text-bad">{err.message}</p>
      </section>
    );
  }

  const data = req.data;
  if (!data) return null;
  const abs = absSummary(data.operation, data.payload, roots);
  // M5: 방어적 정규화 -- 배열이 아닌(또는 없는) transitions 페이로드 하나가 화면
  // 전체를 무방어 인덱싱(`transitions[len-1]`)으로 죽인 적이 있다. ErrorBoundary가
  // 있어도 애초에 안 죽는 편이 낫다(경계는 최후의 방어선이지 정상 경로가 아니다).
  const transitions = Array.isArray(data.transitions) ? data.transitions : [];
  // 제출 대기(슬라이스 17이 슬라이스 14의 「큐 대기」 라벨을 정정 -- 설계 §2.4):
  // 이 값은 요청 Pending -> 첫 비-Pending 전이(플래너 픽업)의 지연이지 Volcano 큐
  // 대기가 아니다. 진짜 Volcano 대기는 대시보드 큐 카드(라이브 PodGroup)에만 있다.
  const firstPickup = transitions.find((t) => t.to_state !== "Pending");
  const end = transitions[transitions.length - 1]?.at ?? data.updated_at;
  // 잡이 하나도 없을 때만 요청 단위 취소를 보여준다 — 잡이 있으면 잡 단위 취소가 그 역할을 한다.
  const canCancelRequest = !isRequestTerminal(data.state) && (jobs.data ?? []).length === 0;

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">요청 {requestId}</h1>
      <Card>
        <div className="flex items-center gap-3">
          <StatusPill state={data.state} />
          <span className="text-muted text-sm">{data.operation}</span>
        </div>
        <dl className="text-sm grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 mt-3">
          {/* 사유(사용자 보고 2026-08-16): 실패한 요청에서 사용자가 가장 먼저 찾는
              값이라 dl 의 첫 줄이다(상태 pill 바로 아래 = 상태→사유 순). 소스는
              **요청 자신의** 종단 사유(백엔드가 results 행에서, 없으면 마지막 전이
              에서 싣는다) — 잡의 reason_code 가 아니다: 잡이 만들어지기 전에 거부된
              요청(플래너 어드미션)은 잡이 아예 없어 사유를 실을 곳이 없었다.
              없으면(비종단·사유 없는 종단·구버전 응답) 줄 자체를 그리지 않는다 --
              "—" 도 거짓 표시다(BatchDetail 항목 패널은 표 정렬 때문에 "—" 를 쓰지만
              여기 dl 은 줄 수가 가변이라 생략이 더 정직하다). */}
          {data.reason_code && (<>
            <dt className="text-muted">사유</dt>
            <dd className="text-bad">{reasonText(data.reason_code)}</dd>
          </>)}
          {/* 대상: payload 가 담은 그대로(스토리지:상대경로). 완료된 작업을 볼 때
              화면 어디에도 무엇을 대상으로 돌았는지 없었다(사용자 보고). */}
          <dt className="text-muted">대상</dt>
          <dd className="font-mono text-xs break-all">
            {pathSummary(data.operation, data.payload)}
          </dd>
          {/* 절대경로는 **지금의** managed_root 로 조합한다 — payload 에 박아 두면
              스토리지 경로가 바뀐 뒤 존재하지 않는 경로를 사실처럼 보인다. 뿌리를
              모르면(비관리자·조회 실패) 줄 자체를 안 그린다(거짓 경로 금지). */}
          {abs !== null && (<>
            <dt className="text-muted">절대경로</dt>
            <dd className="font-mono text-xs break-all text-muted">{abs}</dd>
          </>)}
          <dt className="text-muted">요청자</dt><dd>{data.requester_id}</dd>
          <dt className="text-muted">제출 대기</dt>
          <dd>{durationText(data.created_at, firstPickup?.at)}</dd>
          <dt className="text-muted">수행시간</dt><dd>{durationText(data.created_at, end)}</dd>
        </dl>
        {canCancelRequest && (
          <div className="mt-3">
            <Button variant="ghost" disabled={cancelRequest.isPending}
                    onClick={() => cancelRequest.mutate()}>요청 취소</Button>
            {cancelRequest.isError && (
              <p className="text-bad text-sm mt-1">{(cancelRequest.error as ApiError).message}</p>
            )}
          </div>
        )}
      </Card>
      <Card>
        <h2 className="text-sm font-semibold mb-2">전이 이력</h2>
        <Timeline transitions={transitions} />
      </Card>
      <DiagnosticEvents req={data} />
      <div className="space-y-2">
        {(jobs.data ?? []).map((j) => (
          <Card key={j.job_id}>
            {/* 헤더 = 식별자 · 실행 도구 · 상태pill. 도구·식별자 묶음은 flex-wrap
                이다 — job_id(UUID)와 도구 문구가 좁은 폭에서 한 줄을 넘기면 문서
                가로 스크롤(e2e L1)이 되므로 줄바꿈으로 흡수한다. */}
            <div className="flex items-center justify-between gap-3">
              <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
                <span className="text-sm">{j.job_id}</span>
                <ToolLabel job={j} />
              </div>
              <StatusPill state={j.state} />
            </div>
            {j.reason_code && <p className="text-bad text-sm mt-1">{reasonText(j.reason_code)}</p>}
            {j.artifact_uri && (
              <p className="text-muted text-xs mt-1 break-all">아티팩트 {j.artifact_uri}</p>
            )}
            <ResultSummary summary={j.result_summary} />
            {j.state === "ConfirmPending" && <div className="mt-2"><ConfirmDialog job={j} /></div>}
            {j.state !== "ConfirmPending" && !isTerminal(j.state) && (
              <div className="mt-2">
                <Button variant="ghost" disabled={cancel.isPending}
                        onClick={() => cancel.mutate(j.job_id)}>취소</Button>
                {/* useCancelJob 은 요청 단위 훅 하나를 모든 잡 카드가 공유한다 --
                    variables(마지막 mutate 인자 = jobId)로 한정하지 않으면 한 잡의
                    취소 실패가 모든 카드에 도배된다. */}
                {cancel.isError && cancel.variables === j.job_id && (
                  <p className="text-bad text-sm mt-1">{(cancel.error as ApiError).message}</p>
                )}
              </div>
            )}
            <div className="mt-3">
              <Timeline transitions={j.transitions} />
            </div>
            <JobViewer jobId={j.job_id} phaseRefs={j.phase_refs} />
          </Card>
        ))}
      </div>
    </section>
  );
}
