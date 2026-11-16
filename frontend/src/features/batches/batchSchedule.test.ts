import { expect, test } from "vitest";
import {
  childCountWarning, confirmWaitShort, confirmWaitText, durationText, formFromSchedule, isDestructiveBatch,
  scheduleEnabled, scheduleFormErrors, scheduleSummary, specFromForm, storageText, untilText,
  DEFAULT_SCHEDULE_FORM,
} from "./batchSchedule";

test("주기 요약 — 프리셋 넷, 29~31일은 말일 당김을 말한다", () => {
  expect(scheduleSummary("daily", { hour: 2, minute: 0 })).toBe("매일 02:00 (KST)");
  expect(scheduleSummary("weekly", { weekdays: [5, 1, 3], hour: 3, minute: 5 })).toBe("매주 월·수·금 03:05 (KST)");
  expect(scheduleSummary("monthly", { day: 1, hour: 5, minute: 0 })).toBe("매월 1일 05:00 (KST)");
  expect(scheduleSummary("monthly", { day: 31, hour: 5, minute: 0 })).toBe("매월 31일 05:00 (KST, 없는 달은 말일)");
  expect(scheduleSummary("interval", { every_hours: 6, hour: 0, minute: 30 })).toBe("6시간마다 · 00:30 기준 (KST)");
});

test("깨진 spec 은 「알 수 없는 주기」 — 그럴듯한 주기로 지어내지 않는다", () => {
  expect(scheduleSummary("daily", null)).toBe("알 수 없는 주기(daily)");
  expect(scheduleSummary("weekly", { weekdays: [], hour: 1, minute: 0 })).toBe("알 수 없는 주기(weekly)");
  expect(scheduleSummary("interval", { every_hours: 5, hour: 0, minute: 0 })).toBe("알 수 없는 주기(interval)");
  expect(scheduleSummary("daily", { hour: 1.5, minute: 0 })).toBe("알 수 없는 주기(daily)");
  expect(scheduleSummary("", {})).toBe("알 수 없는 주기");
});

test("폼 → 서버 spec: 그 주기의 키만, 요일 정렬·중복 제거, 틀리면 null", () => {
  expect(specFromForm({ ...DEFAULT_SCHEDULE_FORM, kind: "weekly", weekdays: [5, 1, 5], time: "03:15" }))
    .toEqual({ kind: "weekly", spec: { weekdays: [1, 5], hour: 3, minute: 15 } });
  expect(specFromForm({ ...DEFAULT_SCHEDULE_FORM, kind: "interval", everyHours: 8, time: "20:00" }))
    .toEqual({ kind: "interval", spec: { every_hours: 8, hour: 20, minute: 0 } });
  expect(specFromForm({ ...DEFAULT_SCHEDULE_FORM, time: "" })).toBeNull();
  expect(specFromForm({ ...DEFAULT_SCHEDULE_FORM, kind: "weekly", weekdays: [] })).toBeNull();
  expect(scheduleFormErrors({ ...DEFAULT_SCHEDULE_FORM, kind: "weekly", weekdays: [] }).weekdays).toMatch(/요일/);
  expect(scheduleFormErrors({ ...DEFAULT_SCHEDULE_FORM, kind: "interval", everyHours: 5 }).everyHours).toMatch(/간격/);
});

test("저장된 스케줄 → 폼(깨졌으면 기본 폼)", () => {
  expect(formFromSchedule("monthly", { day: 31, hour: 5, minute: 7 }))
    .toEqual({ ...DEFAULT_SCHEDULE_FORM, kind: "monthly", day: 31, time: "05:07" });
  expect(formFromSchedule("weekly", "broken")).toEqual(DEFAULT_SCHEDULE_FORM);
});

test("파괴적 판정·켜짐 판정·대상 스토리지", () => {
  expect(isDestructiveBatch("rm", {})).toBe(true);
  expect(isDestructiveBatch("sync", { delete: true })).toBe(true);
  expect(isDestructiveBatch("sync", { delete: "true" })).toBe(false);
  expect(isDestructiveBatch("scan", { delete: true })).toBe(false);
  expect(scheduleEnabled(1)).toBe(true);
  expect(scheduleEnabled(true)).toBe(true);
  expect(scheduleEnabled(0)).toBe(false);
  expect(scheduleEnabled("1")).toBe(false);
  expect(storageText("sync", { source_storage: "a", destination_storage: "b" })).toBe("a → b");
  expect(storageText("sync", { source_storage: "a", destination_storage: null })).toBe("a → ?");
  expect(storageText("sync", { source_storage: null, destination_storage: null })).toBe("—");
  expect(storageText("rm", { storage: "" })).toBe("(이름 없음)");          // 사용량과 같은 표기
  expect(storageText("scan", null)).toBe("—");
});

test("확인 대기 경고 — 매일 + 24시간 만료 = 이틀에 한 번, 고르지 않은 주기는 한 값으로 말하지 않는다", () => {
  const daily = { period_seconds: 86400, preview_ttl_seconds: 86400, unconfirmed_every_seconds: 172800 };
  expect(confirmWaitText(daily)).toContain("2일에 한 번만 미리보기를 만듭니다");
  expect(confirmWaitText(daily)).toContain("1일 뒤 만료");
  expect(confirmWaitShort(daily)).toBe("확인 없으면 2일에 한 번");
  const uneven = { period_seconds: 86400, preview_ttl_seconds: 86400, unconfirmed_every_seconds: null };
  expect(confirmWaitText(uneven)).toContain("주기보다 드물게");
  expect(confirmWaitShort(uneven)).toBe("확인 없으면 주기보다 드물게");
  expect(confirmWaitText(null)).toBeNull();
  expect(confirmWaitText(undefined)).toBeNull();
  expect(durationText(30 * 3600)).toBe("30시간");
  expect(durationText(0)).toBeNull();
  expect(durationText(null)).toBeNull();
});

test("자식 요청 경고 — 상한의 80% 부터, capped 면 이미 넘음, 모르면 경고 없음(지어내지 않는다)", () => {
  expect(childCountWarning({ child_count: 1599, child_count_capped: false, child_count_limit: 2000 })).toBeNull();
  expect(childCountWarning({ child_count: 1600, child_count_capped: false, child_count_limit: 2000 }))
    .toContain("2,000개를 넘으면 배치 단위로 지울 수 없게 됩니다");
  expect(childCountWarning({ child_count: 2001, child_count_capped: true, child_count_limit: 2000 }))
    .toContain("배치 단위로 지울 수 없습니다");
  expect(childCountWarning({ child_count: 0, child_count_capped: false, child_count_limit: 2000 })).toBeNull();
  expect(childCountWarning({})).toBeNull();
});

test("남은 시간", () => {
  const now = Date.parse("2026-11-16T00:00:00Z");
  expect(untilText("2026-11-16T00:00:30Z", now)).toBe("곧");
  expect(untilText("2026-11-16T00:45:00Z", now)).toBe("45분 뒤");
  expect(untilText("2026-11-16T03:00:00Z", now)).toBe("3시간 뒤");
  expect(untilText("2026-11-16T03:12:00Z", now)).toBe("3시간 12분 뒤");
  expect(untilText("2026-11-18T01:00:00Z", now)).toBe("2일 뒤");
  expect(untilText("garbage", now)).toBe("");
});
