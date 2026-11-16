// 배치 스케줄러(2026-11-16) 화면 규칙. 회차 = 같은 배치의 「전체 재실행」(:rescan 과 같은 경로)이고, 주기는 KST 프리셋 넷
// (매일·매주·매월·N시간마다 — cron 식 없음). 서버 src/dms/batch_scheduler.py 가 kind/spec 검증과 다음 실행 계산의 진실이다:
// 화면은 **다음 실행을 계산하지 않는다**(말일 당기기·interval 슬롯을 따로 구현하면 서버와 어긋난다 — 미리보기는 서버
// :preview). 여기 있는 것은 표시 문구와 저장 전 즉답(서버 422 invalid_batch_schedule 의 거울)뿐이다.

import { kstStampBare } from "./datetime";

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

// N시간마다의 N = 24 의 약수만(서버와 같은 집합) -- 기준 시각에서 N시간 간격 슬롯이 날마다 같은 시각에 떨어진다.
export const INTERVAL_HOURS = [1, 2, 3, 4, 6, 8, 12] as const;

// 스케줄을 걸 수 있는 연산 = 서버 C4-7 의 집합. 모르는 연산 문자열(DB 신뢰 경계)은 고를 수 없게 한다(서버도 일시정지).
const SCHEDULABLE_OPS: ReadonlySet<string> = new Set(["scan", "sync", "rm"]);
export const schedulableOp = (op: string | null | undefined): boolean => op != null && SCHEDULABLE_OPS.has(op);

// 「되돌릴 수 없음」 동의가 필요한 배치 = rm, 또는 delete 옵션을 켠 sync(서버 파괴적 스케줄 술어의 거울). 판정은 지금
// 배치 값으로 한다 — 동의 뒤 배치가 바뀌면 서버가 지문으로 잡아 일시정지한다.
export function isDestructiveBatch(op: string | null | undefined, options: Record<string, unknown> | null | undefined): boolean {
  return op === "rm" || (op === "sync" && options?.delete === true);
}

const pad2 = (n: number) => String(n).padStart(2, "0");
// bool 은 JS 에선 number 가 아니라 걸러지지만, JSON 에서 온 1.5·"2" 같은 값은 여기서 정수 검사로 떨어진다.
const intIn = (v: unknown, lo: number, hi: number): v is number =>
  typeof v === "number" && Number.isInteger(v) && v >= lo && v <= hi;

// spec 은 서버 JSON(DB 신뢰 경계) -- 깨졌거나 모르는 kind 면 null(「알 수 없는 주기」). 지어낸 기본값으로 메우지 않는다.
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
 *  깨진 spec·모르는 kind 는 「알 수 없는 주기」(원시 kind 병기) -- 모르는 값을 그럴듯한 주기로 지어내지 않는다. */
export function scheduleSummary(kind: unknown, spec: unknown): string {
  const p = parseSpec(kind, spec);
  if (p === null) return typeof kind === "string" && kind !== "" ? `알 수 없는 주기(${kind})` : "알 수 없는 주기";
  const at = `${pad2(p.hour)}:${pad2(p.minute)}`;
  switch (p.kind) {
    case "daily": return `매일 ${at} (KST)`;
    case "weekly": return `매주 ${p.weekdays.map((d) => WEEKDAYS[d - 1].label).join("·")} ${at} (KST)`;
    // 29~31일은 그 날이 없는 달이 있다 -- 서버는 그 달 말일로 당긴다(건너뛰지 않는다). 요약이 그 사실을 같이 말한다.
    case "monthly": return p.day >= 29 ? `매월 ${p.day}일 ${at} (KST, 없는 달은 말일)` : `매월 ${p.day}일 ${at} (KST)`;
    case "interval": return `${p.everyHours}시간마다 · ${at} 기준 (KST)`;
  }
}

// 폼 상태. 시각은 <input type="time"> 의 값("HH:MM", 로캘 무관) 그대로 문자열로 둔다 -- 지우는 중의 빈 값을 0시로 바꾸지
// 않는다(PolicyDialog 의 숫자 문자열 상태와 같은 이유). 주기를 바꿔도 다른 주기의 입력은 남는다(돌아오면 그대로).
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

// 회차 판정 결과(서버 outcome). 모르는 값은 원문 그대로 보인다(지어내지 않는다).
const OUTCOME_LABEL: Record<string, string> = { fired: "시작함", skipped: "건너뜀", missed: "놓침", paused: "일시정지" };
const OUTCOME_TONE: Record<string, string> = { fired: "text-ok", skipped: "text-muted", missed: "text-attn", paused: "text-bad" };
export const outcomeLabel = (o: string): string => OUTCOME_LABEL[o] ?? o;
export const outcomeTone = (o: string): string => OUTCOME_TONE[o] ?? "text-ink";

// 서버 enabled 는 INTEGER(0/1) 컬럼이라 응답이 1 일 수도 true 일 수도 있다 -- 켜짐은 둘 중 하나로만 판정한다(그 밖 = 꺼짐).
export const scheduleEnabled = (v: unknown): boolean => v === true || v === 1;

/** 다음 실행 시각 「YYYY-MM-DD HH:MM」(KST — 회차는 분 단위라 초는 덜어 낸다). 꺼짐·일시정지(next_run_at null)·켜짐이 아님은
 *  「—」, 파싱 못 하는 값은 원문 그대로(kstStampBare 규칙 — 지어내지 않는다). 배치 목록 「반복」 표식·배치 상세 머리가 쓴다. */
export function nextRunStamp(enabled: unknown, iso: string | null | undefined): string {
  if (!scheduleEnabled(enabled) || !iso) return "—";
  const stamp = kstStampBare(iso);
  return /^\d{4}-\d\d-\d\d \d\d:\d\d/.test(stamp) ? stamp.slice(0, 16) : stamp;
}
