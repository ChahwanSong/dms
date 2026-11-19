// 배치 스케줄러(2026-11-16) 화면 규칙 + 응답 타입. 회차 = 같은 배치의 「전체 재실행」(:rescan 과 같은 경로)이고, 주기는 KST
// 프리셋 넷(매일·매주·매월·N시간마다 — cron 식 없음). 서버 src/dms/batch_scheduler.py 가 kind/spec 검증과 다음 실행 계산의
// 진실이다: 화면은 **다음 실행을 계산하지 않는다**(말일 당기기·interval 슬롯을 따로 구현하면 서버와 어긋난다 — 미리보기는 서버
// :preview). 여기 있는 것은 표시 문구와 저장 전 즉답(서버 422 invalid_batch_schedule 의 거울)뿐이다.
//
// 회차가 쌓는 자식 요청: 스케줄이 있는 배치는 최근 K회차(서버 keep_rounds, 기본 10 — 같은 날 5 → 10)만 남고 오래된 회차는
// 컨트롤러가 자동으로 정리한다(2026-11-16 사용자 결정 — 서버 batch_round_prune). 문구는 keepRoundsNotice 한 벌을 화면 셋이 같이
// 쓰고, 숫자는 늘 서버 값이다(화면에 기본값을 박지 않는다 — 옛 서버면 숫자 없는 문구).
//
// dms-ssc WIP 의 lib/batchSchedule.ts 와 같은 export 이름이다. k8s 는 lib/types.ts 를 배치 목록 작업(#67)이 만지므로 응답
// 타입을 이 파일에 둔다(나중에 합칠 때 types.ts 로 옮긴다).
import { kstStampBare } from "../../lib/datetime";
import { storageLabel } from "../usage/usageTabs";
import { n0 } from "../../lib/format";

// --- 응답 타입(routes_batch_schedules 모듈 docstring) -------------------------------------------------------------------

/** 배치 요약(스케줄 행의 batch). null 필드 = 모름. */
export interface BatchScheduleBatch {
  batch_id?: string;
  // item_count null = 모름(변조된 값) — 0(항목 없음)과 다르다.
  name: string | null; operation: string; status: string; item_count: number | null;
  requester_id?: string | null;
  auto_confirm?: boolean; auto_confirm_by?: string | null;
  options?: Record<string, unknown> | null;
  // 대상 스토리지(첫 항목 payload — 서버가 모양 투영). null = 모름.
  storage?: string | null; source_storage?: string | null; destination_storage?: string | null;
}
/** 자동 확인이 꺼진 sync·rm 의 주기가 미리보기 만료보다 짧거나 같을 때만(batch_scheduler.confirm_wait_note). */
export interface ConfirmWait {
  period_seconds: number; preview_ttl_seconds: number;
  // 아무도 확인하지 않을 때의 실제 발사 간격. null = 간격이 고르지 않아 한 값으로 말할 수 없다(매주 여러 요일).
  unconfirmed_every_seconds: number | null;
}
export interface BatchSchedule {
  schedule_id: string; batch_id: string;
  kind: string; spec: Record<string, unknown> | null;
  enabled: boolean | number;
  next_run_at: string | null;              // UTC — 꺼졌거나 일시정지면 null
  armed_by: string;                        // 마지막으로 만들거나 고치거나 켠 관리자(회차마다 자격을 다시 본다)
  created_by: string; created_at: string; updated_at: string;
  // 마지막 회차 판정. null = 아직 한 번도 판정하지 않았다(실패가 아니다).
  last_run_at: string | null; last_outcome: string | null; last_reason_code: string | null;
  paused_reason_code: string | null;       // 자동 일시정지 사유(사람이 끄면 null)
  acknowledged_irreversible?: boolean;     // 동의 지문이 저장돼 있다(원문은 응답에 없다)
  // 「되돌릴 수 없음」 동의가 필요한 스케줄인가(rm, sync+delete — 서버가 **지금** 배치로 판정). null = 배치 행이 없어 모름.
  destructive?: boolean | null;
  batch: BatchScheduleBatch | null;        // null = 배치 행이 없다
  // k8s: 재실행 이력 포함 자식 요청 수 · 상한 초과로 세기를 멈춤 · 배치 단위 삭제 상한(서버 설정 DMS_BATCH_DELETE_MAX_CHILDREN, 기본 50000 -- 2026-11-19).
  child_count?: number; child_count_capped?: boolean; child_count_limit?: number;
  confirm_wait?: ConfirmWait | null;
  // 회차 보존(2026-11-16 사용자 결정 — 서버 DMS_BATCH_SCHEDULE_KEEP_ROUNDS, 기본 10): 최근 keep_rounds 회차만 남고 오래된 회차의
  // 작업은 컨트롤러가 정리한다. pruned_total null = 아직 정리한 적 없음(0 과 다르다), pruned_at = 마지막으로 정리한 시각(UTC).
  keep_rounds?: number | null; pruned_at?: string | null; pruned_total?: number | null;
}
// 회차 기록(GET /api/admin/batch-schedules/{id}/runs, 최근 순). requeued·prev_* 는 fired 일 때만 값이 있다 — null = 해당 없음
// (0 은 「0개」라는 정상값이다).
export interface BatchScheduleRun {
  run_id: number; schedule_id: string; batch_id: string;
  scheduled_for: string; at: string; outcome: string; reason_code: string | null;
  batch_status: string | null;
  requeued: number | null;
  prev_status: string | null; prev_item_count: number | null; prev_succeeded: number | null; prev_failed: number | null;
}
/** 추가 창 예측(:preview 의 prune_preview — 실제 정리와 같은 경계 계산). would_prune = 지금 스케줄을 걸면 곧 정리될 작업 수
 *  (막힌 작업까지 세는 근사치 — 화면은 「약」), capped = 서버가 세기를 멈췄다(그보다 많다). */
export interface PrunePreview { rounds_recorded: number | null; would_prune: number; would_prune_capped?: boolean }
export interface SchedulePreview {
  next_runs: string[]; confirm_wait?: ConfirmWait | null;
  // 2026-11-16 — 옛 서버엔 없다(옵션). keep_rounds·child_count_limit 은 늘, prune_preview 는 batch_id 를 실었을 때만.
  keep_rounds?: number | null; child_count_limit?: number | null; prune_preview?: PrunePreview | null;
}

// --- 프리셋 · 폼 ------------------------------------------------------------------------------------------------------

export type ScheduleKind = "daily" | "weekly" | "monthly" | "interval";

export const SCHEDULE_KINDS: readonly { value: ScheduleKind; label: string }[] = [
  { value: "daily", label: "매일" },
  { value: "weekly", label: "매주" },
  { value: "monthly", label: "매월" },
  { value: "interval", label: "N시간마다" },
];

// ISO 요일(1=월 … 7=일) — 파이썬 isoweekday() 와 같은 번호라 JS getDay(0=일)와 섞일 일이 없다.
export const WEEKDAYS: readonly { value: number; label: string }[] = [
  { value: 1, label: "월" }, { value: 2, label: "화" }, { value: 3, label: "수" }, { value: 4, label: "목" },
  { value: 5, label: "금" }, { value: 6, label: "토" }, { value: 7, label: "일" },
];

// N시간마다의 N = 24 의 약수만(서버와 같은 집합) — 기준 시각에서 N시간 간격 슬롯이 날마다 같은 시각에 떨어진다.
export const INTERVAL_HOURS = [1, 2, 3, 4, 6, 8, 12] as const;

// 스케줄을 걸 수 있는 연산 = 서버 BATCH_OPERATIONS. 모르는 연산 문자열(DB 신뢰 경계)은 고를 수 없게 한다(서버도 일시정지).
const SCHEDULABLE_OPS: ReadonlySet<string> = new Set(["scan", "sync", "rm"]);
export const schedulableOp = (op: string | null | undefined): boolean => op != null && SCHEDULABLE_OPS.has(op);

// 「되돌릴 수 없음」 동의가 필요한 배치 = rm, 또는 delete 옵션을 켠 sync(서버 is_destructive 의 거울). 판정은 지금 배치 값으로
// 한다 — 동의 뒤 배치가 바뀌면 서버가 지문으로 잡아 일시정지한다.
export function isDestructiveBatch(op: string | null | undefined, options: Record<string, unknown> | null | undefined): boolean {
  return op === "rm" || (op === "sync" && options?.delete === true);
}
// 회차마다 사람 확인을 거치는 연산(서버 domain.confirm_gated 의 배치 쪽 — sync·rm).
export const confirmGatedOp = (op: string | null | undefined): boolean => op === "sync" || op === "rm";

// 배치 요약의 대상 스토리지(서버가 첫 항목 payload 에서 뽑아 준다 — 동질성은 서버가 강제). 모르면 「—」: sync 는 한쪽만
// 알아도 아는 쪽을 보여 주고 모르는 쪽은 「?」, 둘 다 모르면 「—」 하나다. 빈 문자열 이름은 사용량과 같은 「(이름 없음)」.
export interface BatchStorageFields {
  storage?: string | null; source_storage?: string | null; destination_storage?: string | null;
}
export function storageText(op: string | null | undefined, f: BatchStorageFields | null | undefined): string {
  if (!f) return "—";
  const str = (v: unknown) => (typeof v === "string" ? storageLabel(v) : null);
  if (op === "sync") {
    const src = str(f.source_storage), dst = str(f.destination_storage);
    if (src === null && dst === null) return "—";
    return `${src ?? "?"} → ${dst ?? "?"}`;
  }
  return str(f.storage) ?? "—";
}

const pad2 = (n: number) => String(n).padStart(2, "0");
// bool 은 JS 에선 number 가 아니라 걸러지지만, JSON 에서 온 1.5·"2" 같은 값은 여기서 정수 검사로 떨어진다.
const intIn = (v: unknown, lo: number, hi: number): v is number =>
  typeof v === "number" && Number.isInteger(v) && v >= lo && v <= hi;

// spec 은 서버 JSON(DB 신뢰 경계) — 깨졌거나 모르는 kind 면 null(「알 수 없는 주기」). 지어낸 기본값으로 메우지 않는다.
interface ParsedSpec { kind: ScheduleKind; hour: number; minute: number; weekdays: number[]; day: number; everyHours: number }
function parseSpec(kind: unknown, spec: unknown): ParsedSpec | null {
  if (spec === null || typeof spec !== "object" || Array.isArray(spec)) return null;
  const s = spec as Record<string, unknown>;
  if (!intIn(s.hour, 0, 23) || !intIn(s.minute, 0, 59)) return null;
  const base = { hour: s.hour, minute: s.minute, weekdays: [] as number[], day: 1, everyHours: 6 };
  if (kind === "daily") return { kind, ...base };
  if (kind === "weekly") {
    const w = s.weekdays;
    if (!Array.isArray(w) || w.length === 0 || !w.every((d) => intIn(d, 1, 7))) return null;
    return { kind, ...base, weekdays: [...new Set(w as number[])].sort((a, b) => a - b) };
  }
  if (kind === "monthly") return intIn(s.day, 1, 31) ? { kind, ...base, day: s.day } : null;
  if (kind === "interval") {
    return (INTERVAL_HOURS as readonly number[]).includes(s.every_hours as number)
      ? { kind, ...base, everyHours: s.every_hours as number } : null;
  }
  return null;
}

/** 주기 한 줄 요약 — 「매주 월·수·금 02:00 (KST)」「매월 31일 02:00 (KST, 없는 달은 말일)」「6시간마다 · 00:30 기준 (KST)」.
 *  깨진 spec·모르는 kind 는 「알 수 없는 주기」(원시 kind 병기) — 모르는 값을 그럴듯한 주기로 지어내지 않는다. */
export function scheduleSummary(kind: unknown, spec: unknown): string {
  const p = parseSpec(kind, spec);
  if (p === null) return typeof kind === "string" && kind !== "" ? `알 수 없는 주기(${kind})` : "알 수 없는 주기";
  const at = `${pad2(p.hour)}:${pad2(p.minute)}`;
  switch (p.kind) {
    case "daily": return `매일 ${at} (KST)`;
    case "weekly": return `매주 ${p.weekdays.map((d) => WEEKDAYS[d - 1].label).join("·")} ${at} (KST)`;
    // 29~31일은 그 날이 없는 달이 있다 — 서버는 그 달 말일로 당긴다(건너뛰지 않는다). 요약이 그 사실을 같이 말한다.
    case "monthly": return p.day >= 29 ? `매월 ${p.day}일 ${at} (KST, 없는 달은 말일)` : `매월 ${p.day}일 ${at} (KST)`;
    case "interval": return `${p.everyHours}시간마다 · ${at} 기준 (KST)`;
  }
}

// 폼 상태. 시각은 <input type="time"> 의 값("HH:MM", 로캘 무관) 그대로 문자열로 둔다 — 지우는 중의 빈 값을 0시로 바꾸지
// 않는다. 주기를 바꿔도 다른 주기의 입력은 남는다(돌아오면 그대로).
export interface ScheduleForm { kind: ScheduleKind; time: string; weekdays: number[]; day: number; everyHours: number }
export const DEFAULT_SCHEDULE_FORM: ScheduleForm = { kind: "daily", time: "02:00", weekdays: [1], day: 1, everyHours: 6 };

export const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** 저장 전 즉답(서버 422 invalid_batch_schedule 의 거울 — pydantic 422 는 일반 문구라 화면이 먼저 말한다). */
export function scheduleFormErrors(f: ScheduleForm): { time: string | null; weekdays: string | null; day: string | null; everyHours: string | null } {
  return {
    time: TIME_RE.test(f.time) ? null : "실행 시각을 HH:MM(00:00~23:59)으로 입력하세요",
    weekdays: f.kind === "weekly" && !f.weekdays.some((d) => intIn(d, 1, 7)) ? "요일을 하나 이상 고르세요" : null,
    day: f.kind === "monthly" && !intIn(f.day, 1, 31) ? "날짜는 1~31일입니다" : null,
    everyHours: f.kind === "interval" && !(INTERVAL_HOURS as readonly number[]).includes(f.everyHours)
      ? "간격은 1·2·3·4·6·8·12시간 중 하나입니다" : null,
  };
}
export const scheduleFormInvalid = (f: ScheduleForm): boolean =>
  Object.values(scheduleFormErrors(f)).some((e) => e !== null);

/** 폼 → 서버 {kind, spec}. 그 주기가 쓰는 키만 싣는다(서버는 모르는 키를 거부한다). 요일은 중복 없이 정렬. 폼이 틀렸으면 null. */
export function specFromForm(f: ScheduleForm): { kind: ScheduleKind; spec: Record<string, unknown> } | null {
  if (scheduleFormInvalid(f)) return null;
  const hour = Number(f.time.slice(0, 2)), minute = Number(f.time.slice(3, 5));
  switch (f.kind) {
    case "daily": return { kind: "daily", spec: { hour, minute } };
    case "weekly": return { kind: "weekly", spec: { weekdays: [...new Set(f.weekdays)].sort((a, b) => a - b), hour, minute } };
    case "monthly": return { kind: "monthly", spec: { day: f.day, hour, minute } };
    case "interval": return { kind: "interval", spec: { every_hours: f.everyHours, hour, minute } };
  }
}

/** 저장된 스케줄 → 수정 폼. 깨진 spec 이면 기본 폼(주기는 사람이 다시 고른다 — 저장 전까지 아무것도 바뀌지 않는다). */
export function formFromSchedule(kind: unknown, spec: unknown): ScheduleForm {
  const p = parseSpec(kind, spec);
  if (p === null) return { ...DEFAULT_SCHEDULE_FORM };
  return {
    kind: p.kind, time: `${pad2(p.hour)}:${pad2(p.minute)}`,
    weekdays: p.kind === "weekly" ? p.weekdays : DEFAULT_SCHEDULE_FORM.weekdays,
    day: p.kind === "monthly" ? p.day : DEFAULT_SCHEDULE_FORM.day,
    everyHours: p.kind === "interval" ? p.everyHours : DEFAULT_SCHEDULE_FORM.everyHours,
  };
}

// --- 판정 결과 · 경고 문구 ---------------------------------------------------------------------------------------------

// 회차 판정 결과(서버 outcome). 모르는 값은 원문 그대로 보인다(지어내지 않는다).
const OUTCOME_LABEL: Record<string, string> = { fired: "시작함", skipped: "건너뜀", missed: "놓침", paused: "일시정지" };
const OUTCOME_TONE: Record<string, string> = { fired: "text-ok", skipped: "text-muted", missed: "text-attn", paused: "text-bad" };
export const outcomeLabel = (o: string): string => OUTCOME_LABEL[o] ?? o;
export const outcomeTone = (o: string): string => OUTCOME_TONE[o] ?? "text-ink";

// 서버 enabled 는 INTEGER(0/1) 컬럼이라 응답이 1 일 수도 true 일 수도 있다 — 켜짐은 둘 중 하나로만 판정한다(그 밖 = 꺼짐).
export const scheduleEnabled = (v: unknown): boolean => v === true || v === 1;

// 「기록」 낱말(d29 실증 뒤 다듬기 — 같은 화면에서 「회차」가 두 가지를 가리켜 헷갈렸다): 「실행 기록」 = 스케줄 회차 판정 이력
// (batch_schedule_runs — 행의 「기록」 창 이름도 「실행 기록」), 「보존 회차」 = 최근 K회차 보존의 회차 경계(batch_rounds — 배치에 속한다).
// 스케줄만 지우면 실행 기록만 지워지고 보존 회차 경계·남은 작업은 그대로라(정리는 멈춘다) 다시 만들면 쌓인 기록으로 곧 정리가 돈다.
// 배치를 지우면 둘 다 지워진다.

/** 스케줄 행 「삭제」·「삭제 확인」 버튼의 title — 스케줄 DELETE(BatchSchedulesRepository.delete)가 지우는 것과 남기는 것. */
export const SCHEDULE_DELETE_HINT =
  "실행 기록도 함께 지웁니다 — 보존 회차 셈과 이미 남은 작업은 그대로, 정리는 멈춥니다(감사 기록은 남습니다)";

/** 배치 삭제 확인 창의 스케줄 안내 한 줄 — 배치 삭제의 두 경로(배치 상세의 기록 삭제 = BatchesRepository.delete, 작업 목록의 배치
 *  단위 삭제 = request_purges.delete_batch)가 모두 같은 트랜잭션에서 그 배치의 스케줄·실행 기록·보존 회차 경계를 지운다
 *  (BATCH_DELETE_TABLES). 두 창이 같은 말을 하도록 한 벌로 둔다. 감사 기록(스케줄 삭제 행)은 남는다. */
export const scheduleDeleteNotice = (s: { kind: unknown; spec: unknown; enabled: unknown }): string =>
  `이 배치의 스케줄(${scheduleSummary(s.kind, s.spec)}${scheduleEnabled(s.enabled) ? " · 사용 중" : ""})과 실행 기록·보존 회차 `
  + "기록도 함께 삭제됩니다(감사 기록은 남습니다).";

/** 초 → 「2일」「30시간」「90분」 — 경고 문구용(정확히 나눠떨어지는 가장 큰 단위). 0 이하·비정수는 null(지어내지 않는다). */
export function durationText(seconds: number | null | undefined): string | null {
  if (typeof seconds !== "number" || !Number.isInteger(seconds) || seconds <= 0) return null;
  if (seconds % 86_400 === 0) return `${(seconds / 86_400).toLocaleString("ko-KR")}일`;
  if (seconds % 3_600 === 0) return `${(seconds / 3_600).toLocaleString("ko-KR")}시간`;
  if (seconds % 60 === 0) return `${(seconds / 60).toLocaleString("ko-KR")}분`;
  return `${seconds.toLocaleString("ko-KR")}초`;
}

/** 확인 대기 경고(설계 비판 #8 — 서버 confirm_wait). 주기가 미리보기 만료보다 짧거나 같아, 아무도 확인하지 않으면 회차가 주기대로
 *  돌지 않는다는 사실을 그대로 말한다(판정은 그대로 건너뜀 기록 — 이것은 안내다). 없으면 null. */
export function confirmWaitText(w: ConfirmWait | null | undefined): string | null {
  if (!w) return null;
  const ttl = durationText(w.preview_ttl_seconds) ?? `${w.preview_ttl_seconds}초`;
  const every = durationText(w.unconfirmed_every_seconds);
  const tail = every !== null
    ? `확인하지 않으면 실제로는 ${every}에 한 번만 미리보기를 만듭니다`
    : "확인하지 않으면 주기보다 드물게 미리보기만 만듭니다";
  return `회차마다 사람이 「배치 확인」을 눌러야 실행됩니다. 미리보기는 ${ttl} 뒤 만료되고 그동안의 회차는 건너뜁니다 — ${tail}.`;
}
/** 목록 칸에 들어갈 짧은 꼴(전문은 title 로). */
export function confirmWaitShort(w: ConfirmWait | null | undefined): string | null {
  if (!w) return null;
  const every = durationText(w.unconfirmed_every_seconds);
  return every !== null ? `확인 없으면 ${every}에 한 번` : "확인 없으면 주기보다 드물게";
}

// 배치 단위 삭제 상한(서버 child_count_limit -- 2026-11-19 부터 설정 DMS_BATCH_DELETE_MAX_CHILDREN, 기본 50000)의 80% 경고. 오래된 회차의 작업은 자동 정리되지만(최근 K회차 보존 —
// 2026-11-16 사용자 결정) 항목 수 × 보존 회차가 상한보다 크면 줄지 않는다. capped = 서버가 상한 + 1 에서 세기를 멈췄다(이미 넘었다).
export const CHILD_WARN_RATIO = 0.8;
export function childCountWarning(s: Pick<BatchSchedule, "child_count" | "child_count_capped" | "child_count_limit">): string | null {
  const limit = s.child_count_limit;
  if (typeof limit !== "number" || limit <= 0) return null;
  if (s.child_count_capped === true) {
    return `자식 요청이 ${n0(limit)}개를 넘어 지금은 배치 단위로 지울 수 없습니다 — 오래된 회차는 자동 정리되지만 항목 수 × 보존 `
      + "회차가 상한보다 크면 줄지 않습니다";
  }
  const n = s.child_count;
  if (typeof n !== "number" || n < limit * CHILD_WARN_RATIO) return null;
  return `자식 요청 ${n0(n)}개 — 상한 ${n0(limit)}개의 80%를 넘었습니다`;
}

const keepOk = (k: unknown): k is number => typeof k === "number" && Number.isInteger(k) && k >= 1;

/** 회차 보존 안내(2026-11-16 사용자 결정 「최근 5회차만 남기고 정리」, 같은 날 기본 10 — 서버 batch_round_prune). keep 이
 *  숫자가 아니면(옛 서버) 문장을 만들지 않는다(null — 지어내지 않는다). */
export function keepRoundsNotice(keep: number | null | undefined): string | null {
  if (!keepOk(keep)) return null;
  return `스케줄이 있는 배치는 최근 ${keep}회차만 보존합니다 — 첫 실행·전체 재실행·스케줄 회차를 회차로 세고(실패분·선택 재실행은 그 `
    + `회차에 속합니다), ${keep}회차를 넘으면 가장 오래된 회차의 작업(자식 요청·잡·결과 파일)을 곧 자동으로 정리합니다. 사용량 분석의 `
    + `scan 이력(온도·용량 추이)도 이 배치 몫은 최근 ${keep}회차만 남습니다. 스케줄을 꺼도 정리는 계속되고, 스케줄을 지우면 멈춥니다.`;
}

/** 스케줄 행의 정리 결과 조각 — null = 아직 정리 없음(0 과 다르다, null ≠ 0). at = 마지막 정리 시각(KST, 모르면 null). 표 칸은
 *  시각을 한 덩어리(whitespace-nowrap)로 그리려고 조각을 쓰고, 한 줄 글은 pruneText 가 같은 조각으로 만든다(둘이 갈라지지 않게). */
export function pruneParts(s: Pick<BatchSchedule, "pruned_total" | "pruned_at">): { total: string; at: string | null } | null {
  const n = s.pruned_total;
  if (typeof n !== "number") return null;
  return { total: `정리 누적 ${n0(n)}건`, at: typeof s.pruned_at === "string" ? kstStampBare(s.pruned_at) : null };
}

/** 스케줄 행의 정리 결과 한 줄 — null(아직 정리 없음)과 0 을 가른다(null ≠ 0). */
export function pruneText(s: Pick<BatchSchedule, "pruned_total" | "pruned_at">): string {
  const p = pruneParts(s);
  if (p === null) return "아직 정리 없음";
  return p.at === null ? p.total : `${p.total} · 마지막 ${p.at}`;
}

/** 「항목 × K > 상한」 예측 경고 — 보존 회차를 다 채우면 정리 뒤에도 배치 단위 삭제 상한을 넘는다. 셋 중 하나라도 모르면 null. */
export function projectedChildrenWarning(itemCount: number | null | undefined, keep: number | null | undefined,
                                         limit: number | null | undefined): string | null {
  if (typeof itemCount !== "number" || !keepOk(keep) || typeof limit !== "number" || limit <= 0) return null;
  const projected = itemCount * keep;
  if (projected <= limit) return null;
  return `항목 ${n0(itemCount)}개 × ${keep}회차 = 작업 최대 ${n0(projected)}개 — 정리 뒤에도 배치 단위 삭제 상한(${n0(limit)}개)을 `
    + "넘어 이 배치는 배치 단위로 지울 수 없습니다(실패분 재실행이 더해지면 더 많습니다)";
}

/** 추가 창의 「저장하면 곧 정리될 작업」 안내(prune_preview). 0·모름이면 null. */
export function prunePreviewText(p: PrunePreview | null | undefined): string | null {
  if (!p || typeof p.would_prune !== "number" || p.would_prune <= 0) return null;
  // capped = 서버가 상한 + 1 에서 세기를 멈췄다 — 「상한 이상」으로 말한다(지어낸 정확한 수가 아니다).
  const n = p.would_prune_capped === true ? `${n0(p.would_prune - 1)}개 이상이` : `약 ${n0(p.would_prune)}개가`;
  const r = typeof p.rounds_recorded === "number" ? `기록된 회차 ${n0(p.rounds_recorded)}개 중 ` : "";
  return `저장하면 ${r}오래된 회차의 작업 ${n} 곧 정리됩니다`;
}

/** 남은 시간 한국어(「3시간 12분 뒤」·「2일 뒤」·「곧」). 파싱 불가면 빈 문자열(지어내지 않는다). */
export function untilText(iso: string, nowMs: number): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "";
  const minutes = Math.floor((ms - nowMs) / 60_000);
  if (minutes < 1) return "곧";
  if (minutes < 60) return `${minutes}분 뒤`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 === 0 ? `${hours}시간 뒤` : `${hours}시간 ${minutes % 60}분 뒤`;
  return `${Math.floor(hours / 24).toLocaleString("ko-KR")}일 뒤`;
}
