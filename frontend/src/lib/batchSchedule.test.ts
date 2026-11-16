import { describe, expect, test } from "vitest";
import {
  DEFAULT_SCHEDULE_FORM, formFromSchedule, isDestructiveBatch, outcomeLabel, outcomeTone, scheduleEnabled,
  nextRunStamp, scheduleFormErrors, scheduleFormInvalid, scheduleSummary, schedulableOp, specFromForm,
  type ScheduleForm,
} from "./batchSchedule";
import { batchStorageLabel, storageText } from "./batchStorage";

describe("scheduleSummary — 주기 한 줄 요약(KST)", () => {
  test("네 주기의 문구", () => {
    expect(scheduleSummary("daily", { hour: 2, minute: 0 })).toBe("매일 02:00 (KST)");
    expect(scheduleSummary("weekly", { weekdays: [1, 3, 5], hour: 2, minute: 0 })).toBe("매주 월·수·금 02:00 (KST)");
    expect(scheduleSummary("monthly", { day: 15, hour: 23, minute: 5 })).toBe("매월 15일 23:05 (KST)");
    expect(scheduleSummary("interval", { every_hours: 6, hour: 0, minute: 30 })).toBe("6시간마다 · 00:30 기준 (KST)");
  });

  test("29~31일은 「없는 달은 말일」을 같이 말한다(서버가 말일로 당긴다), 28일은 말하지 않는다", () => {
    expect(scheduleSummary("monthly", { day: 31, hour: 2, minute: 0 })).toBe("매월 31일 02:00 (KST, 없는 달은 말일)");
    expect(scheduleSummary("monthly", { day: 29, hour: 2, minute: 0 })).toBe("매월 29일 02:00 (KST, 없는 달은 말일)");
    expect(scheduleSummary("monthly", { day: 28, hour: 2, minute: 0 })).toBe("매월 28일 02:00 (KST)");
  });

  test("요일은 ISO 순서(월→일)로 정렬·중복 제거해 보인다 — 7=일", () => {
    expect(scheduleSummary("weekly", { weekdays: [7, 1, 1], hour: 9, minute: 0 })).toBe("매주 월·일 09:00 (KST)");
  });

  test("0시 0분은 정상값이다(0 을 「없음」으로 뭉개지 않는다)", () => {
    expect(scheduleSummary("daily", { hour: 0, minute: 0 })).toBe("매일 00:00 (KST)");
  });

  test("깨진 spec·모르는 kind 는 「알 수 없는 주기」 — 그럴듯한 주기를 지어내지 않는다(DB 신뢰 경계)", () => {
    expect(scheduleSummary("cron", { hour: 1, minute: 0 })).toBe("알 수 없는 주기(cron)");
    expect(scheduleSummary("daily", null)).toBe("알 수 없는 주기(daily)");
    expect(scheduleSummary("daily", "02:00")).toBe("알 수 없는 주기(daily)");
    expect(scheduleSummary("daily", [2, 0])).toBe("알 수 없는 주기(daily)");
    expect(scheduleSummary("daily", { hour: 24, minute: 0 })).toBe("알 수 없는 주기(daily)");
    expect(scheduleSummary("daily", { hour: 1.5, minute: 0 })).toBe("알 수 없는 주기(daily)");
    expect(scheduleSummary("daily", { hour: true, minute: 0 })).toBe("알 수 없는 주기(daily)");
    expect(scheduleSummary("daily", { hour: "2", minute: 0 })).toBe("알 수 없는 주기(daily)");
    expect(scheduleSummary("weekly", { weekdays: [], hour: 1, minute: 0 })).toBe("알 수 없는 주기(weekly)");
    expect(scheduleSummary("weekly", { weekdays: [0], hour: 1, minute: 0 })).toBe("알 수 없는 주기(weekly)");
    expect(scheduleSummary("monthly", { day: 32, hour: 1, minute: 0 })).toBe("알 수 없는 주기(monthly)");
    expect(scheduleSummary("interval", { every_hours: 5, hour: 1, minute: 0 })).toBe("알 수 없는 주기(interval)");
    expect(scheduleSummary(null, { hour: 1, minute: 0 })).toBe("알 수 없는 주기");
  });
});

describe("폼 검증·직렬화", () => {
  const f = (over: Partial<ScheduleForm>): ScheduleForm => ({ ...DEFAULT_SCHEDULE_FORM, ...over });

  test("기본 폼은 유효하고 매일 02:00", () => {
    expect(scheduleFormInvalid(DEFAULT_SCHEDULE_FORM)).toBe(false);
    expect(specFromForm(DEFAULT_SCHEDULE_FORM)).toEqual({ kind: "daily", spec: { hour: 2, minute: 0 } });
  });

  test("시각은 HH:MM(00:00~23:59)만 — 빈 값을 0시로 바꾸지 않는다", () => {
    expect(scheduleFormErrors(f({ time: "" })).time).not.toBeNull();
    expect(scheduleFormErrors(f({ time: "24:00" })).time).not.toBeNull();
    expect(scheduleFormErrors(f({ time: "2:00" })).time).not.toBeNull();
    expect(scheduleFormErrors(f({ time: "23:59" })).time).toBeNull();
    expect(scheduleFormErrors(f({ time: "00:00" })).time).toBeNull();
    expect(specFromForm(f({ time: "" }))).toBeNull();
  });

  test("매주는 요일이 하나 이상이어야 한다 — 다른 주기에선 요일을 보지 않는다", () => {
    expect(scheduleFormErrors(f({ kind: "weekly", weekdays: [] })).weekdays).toBe("요일을 하나 이상 고르세요");
    expect(scheduleFormErrors(f({ kind: "daily", weekdays: [] })).weekdays).toBeNull();
  });

  test("서버가 쓰는 키만 싣는다 — 요일은 정렬·중복 제거, interval 은 every_hours", () => {
    expect(specFromForm(f({ kind: "weekly", weekdays: [5, 1, 3, 1], time: "02:30" })))
      .toEqual({ kind: "weekly", spec: { weekdays: [1, 3, 5], hour: 2, minute: 30 } });
    expect(specFromForm(f({ kind: "monthly", day: 31, time: "00:00" })))
      .toEqual({ kind: "monthly", spec: { day: 31, hour: 0, minute: 0 } });
    expect(specFromForm(f({ kind: "interval", everyHours: 12, time: "00:30" })))
      .toEqual({ kind: "interval", spec: { every_hours: 12, hour: 0, minute: 30 } });
  });

  test("간격·날짜 범위 밖은 오류(서버 422 의 거울)", () => {
    expect(scheduleFormErrors(f({ kind: "interval", everyHours: 5 })).everyHours).not.toBeNull();
    expect(scheduleFormErrors(f({ kind: "monthly", day: 0 })).day).not.toBeNull();
  });

  test("formFromSchedule ↔ specFromForm 왕복, 깨진 spec 은 기본 폼", () => {
    for (const [kind, spec] of [
      ["daily", { hour: 7, minute: 5 }], ["weekly", { weekdays: [2, 6], hour: 0, minute: 0 }],
      ["monthly", { day: 30, hour: 12, minute: 59 }], ["interval", { every_hours: 1, hour: 23, minute: 0 }],
    ] as const) {
      expect(specFromForm(formFromSchedule(kind, spec))).toEqual({ kind, spec });
    }
    expect(formFromSchedule("weekly", { weekdays: "mon" })).toEqual(DEFAULT_SCHEDULE_FORM);
  });
});

describe("배치 판정 도우미", () => {
  test("반복 실행 가능한 연산 = scan·sync·rm", () => {
    expect(["scan", "sync", "rm"].every(schedulableOp)).toBe(true);
    expect(schedulableOp("chmod")).toBe(false);
    expect(schedulableOp(undefined)).toBe(false);
  });

  test("파괴적 = rm, 또는 delete 를 켠 sync(명시 true 만)", () => {
    expect(isDestructiveBatch("rm", {})).toBe(true);
    expect(isDestructiveBatch("sync", { delete: true })).toBe(true);
    expect(isDestructiveBatch("sync", { delete: false })).toBe(false);
    expect(isDestructiveBatch("sync", { delete: "true" })).toBe(false);
    expect(isDestructiveBatch("sync", undefined)).toBe(false);
    expect(isDestructiveBatch("scan", { delete: true })).toBe(false);
  });

  test("대상 스토리지: scan·rm 은 storage, sync 는 src → dst, 모르면 —", () => {
    expect(storageText("scan", { storage: "ceph" })).toBe("ceph");
    expect(storageText("sync", { source_storage: "a", destination_storage: "b" })).toBe("a → b");
    // 모르는 쪽은 배치 목록 「대상 스토리지」 열과 같은 「—」(두 화면이 한 규칙 — lib/batchStorage).
    expect(storageText("sync", { source_storage: "a", destination_storage: null })).toBe("a → —");
    expect(storageText("sync", { source_storage: 7 as never, destination_storage: "b" })).toBe("— → b");
    expect(storageText("scan", { storage: "" })).toBe("—");
    expect(storageText("sync", { source_storage: null, destination_storage: null })).toBe("—");
    expect(storageText("rm", { storage: null })).toBe("—");
    expect(storageText("rm", null)).toBe("—");
    // 하나도 모르면 null(호출측이 「—」·「항목이 없습니다」로 말한다 — 배치 목록 열)
    expect(batchStorageLabel("sync", { source_storage: null, destination_storage: null })).toBeNull();
    expect(batchStorageLabel("scan", {})).toBeNull();
    expect(batchStorageLabel("sync", { source_storage: "a", destination_storage: "b" })).toBe("a → b");
  });

  test("판정 라벨·색, 모르는 값은 원문", () => {
    expect(outcomeLabel("fired")).toBe("시작함");
    expect(outcomeLabel("skipped")).toBe("건너뜀");
    expect(outcomeLabel("missed")).toBe("놓침");
    expect(outcomeLabel("paused")).toBe("일시정지");
    expect(outcomeLabel("weird")).toBe("weird");
    expect(outcomeTone("paused")).toBe("text-bad");
  });

  test("enabled 는 true 또는 1 만 켜짐(INTEGER 컬럼)", () => {
    expect(scheduleEnabled(true)).toBe(true);
    expect(scheduleEnabled(1)).toBe(true);
    expect(scheduleEnabled(0)).toBe(false);
    expect(scheduleEnabled(false)).toBe(false);
    expect(scheduleEnabled("1")).toBe(false);
    expect(scheduleEnabled(null)).toBe(false);
  });

  test("다음 실행 표기: KST 분까지, 꺼짐·null 은 —, 파싱 못 하면 원문(배치 목록 「반복」·상세 머리)", () => {
    expect(nextRunStamp(true, "2026-11-16T17:00:00Z")).toBe("2026-11-17 02:00");
    expect(nextRunStamp(1, "2026-11-16T17:00:00Z")).toBe("2026-11-17 02:00");
    expect(nextRunStamp(false, "2026-11-16T17:00:00Z")).toBe("—");       // 꺼진 스케줄은 예정이 없다
    expect(nextRunStamp(true, null)).toBe("—");
    expect(nextRunStamp(true, "garbage")).toBe("garbage");
  });
});
