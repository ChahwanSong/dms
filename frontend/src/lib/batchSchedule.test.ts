import { describe, expect, test } from "vitest";
import {
  DEFAULT_SCHEDULE_FORM, SCHEDULE_DELETE_HINT, formFromSchedule, isDestructiveBatch, keepRoundsKnown, keepRoundsNotice,
  outcomeLabel, outcomeTone, projectedChildrenWarning, pruneParts, prunePreviewText, pruneText, scheduleDeleteNotice,
  scheduleEnabled, nextRunStamp, scheduleFormErrors, scheduleFormInvalid, scheduleSummary, schedulableOp, specFromForm,
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

// 회차 보존(2026-11-16 사용자 결정 「최근 10회차만 누적」 — dms-k8s 9166b8e2·0ec5f33b 이식). K·상한은 서버가 실어 준 값만 쓴다.
describe("회차 보존 문구 — 서버 keep_rounds·child_count_limit·prune_preview 로만", () => {
  test("보존 안내는 1 이상 정수일 때만 만든다(옛 서버·변조 값이면 null — 지어내지 않는다)", () => {
    const t = keepRoundsNotice(10)!;
    expect(t).toContain("스케줄이 있는 배치는 최근 10회차만 보존합니다");
    expect(t).toContain("첫 실행·전체 재실행·스케줄 회차를 회차로 세고(실패분·선택 재실행은 그 회차에 속합니다)");
    expect(t).toContain("10회차를 넘으면 가장 오래된 회차의 작업(자식 요청·잡·결과 파일)을 곧 자동으로 정리합니다");
    expect(t).toContain("사용량 분석의 scan 이력(온도·용량 추이)도 이 배치 몫은 최근 10회차만 남습니다");
    expect(t).toContain("스케줄을 꺼도 정리는 계속되고, 스케줄을 지우면 멈춥니다");
    for (const k of [null, undefined, 0, -1, 1.5, "10", true]) {
      expect(keepRoundsNotice(k as number | null | undefined)).toBeNull();
      expect(keepRoundsKnown(k)).toBe(false);
    }
    expect(keepRoundsKnown(1)).toBe(true);
  });

  test("정리 결과 — null(아직 없음)과 0 을 가른다(null ≠ 0), 표 칸 조각과 한 줄 글이 같은 재료", () => {
    expect(pruneText({ pruned_total: null, pruned_at: null })).toBe("아직 정리 없음");
    expect(pruneText({})).toBe("아직 정리 없음");
    expect(pruneText({ pruned_total: 0, pruned_at: null })).toBe("정리 누적 0건");
    expect(pruneText({ pruned_total: 1234, pruned_at: "2026-11-16T09:30:00Z" }))
      .toBe("정리 누적 1,234건 · 마지막 2026-11-16 18:30:00");
    // 시각만 있고 누적이 null 이면 「아직 없음」 — 시각만으로 정리 수를 지어내지 않는다
    expect(pruneParts({ pruned_total: null, pruned_at: "2026-11-16T09:30:00Z" })).toBeNull();
    expect(pruneParts({ pruned_total: 0, pruned_at: null })).toEqual({ total: "정리 누적 0건", at: null });
    expect(pruneParts({ pruned_total: 1234, pruned_at: "2026-11-16T09:30:00Z" }))
      .toEqual({ total: "정리 누적 1,234건", at: "2026-11-16 18:30:00" });
  });

  test("「항목 × K > 상한」 예측 경고 — 셋 다 알 때만, 같으면 경고 없음(상한 = 서버 설정, 기본 50000 -- 2026-11-19)", () => {
    // 2026-11-19 상한 1000 → 설정 DMS_BATCH_DELETE_MAX_CHILDREN(기본 50000): 기본 10회차면 항목 5,001개부터 경고.
    expect(projectedChildrenWarning(5000, 10, 50000)).toBeNull();             // 5000 × 10 = 50000 — 상한과 같으면 지울 수 있다
    expect(projectedChildrenWarning(5001, 10, 50000)).toBe(
      "항목 5,001개 × 10회차 = 작업 최대 50,010개 — 정리 뒤에도 배치 단위 삭제 상한(50,000개)을 넘어 이 배치는 배치 단위로 지울 수 "
      + "없습니다(실패분 재실행이 더해지면 더 많습니다)");
    // 사이트가 상한을 낮춰도 서버 값 그대로
    expect(projectedChildrenWarning(101, 10, 1000)).toContain("상한(1,000개)을 넘어");
    expect(projectedChildrenWarning(0, 10, 50000)).toBeNull();
    expect(projectedChildrenWarning(null, 10, 50000)).toBeNull();
    expect(projectedChildrenWarning(undefined, 10, 50000)).toBeNull();
    expect(projectedChildrenWarning(500, null, 50000)).toBeNull();
    expect(projectedChildrenWarning(500, 10, undefined)).toBeNull();           // 옛 서버 — 상한을 지어내지 않는다
    expect(projectedChildrenWarning(500, 10, 0)).toBeNull();
  });

  test("추가 창 예측 문구 — 0·모름이면 없음, capped 면 「이상」, 회차 수를 모르면 그 조각을 뺀다", () => {
    expect(prunePreviewText({ rounds_recorded: 11, would_prune: 8 }))
      .toBe("저장하면 기록된 회차 11개 중 오래된 회차의 작업 약 8개가 곧 정리됩니다");
    expect(prunePreviewText({ rounds_recorded: 11, would_prune: 10001, would_prune_capped: true }))
      .toBe("저장하면 기록된 회차 11개 중 오래된 회차의 작업 10,000개 이상이 곧 정리됩니다");
    expect(prunePreviewText({ rounds_recorded: null, would_prune: 3 })).toBe("저장하면 오래된 회차의 작업 약 3개가 곧 정리됩니다");
    expect(prunePreviewText({ rounds_recorded: 2, would_prune: 0 })).toBeNull();
    expect(prunePreviewText(null)).toBeNull();
    expect(prunePreviewText(undefined)).toBeNull();
  });

  test("삭제 문구의 「기록」 — 스케줄 삭제는 실행 기록만, 배치 삭제는 실행 기록·보존 회차 기록까지(맨 「회차 기록」은 쓰지 않는다)", () => {
    expect(SCHEDULE_DELETE_HINT)
      .toBe("실행 기록도 함께 지웁니다 — 보존 회차 셈과 이미 남은 작업은 그대로, 정리는 멈춥니다(감사 기록은 남습니다)");
    expect(scheduleDeleteNotice({ kind: "daily", spec: { hour: 2, minute: 0 }, enabled: 1 }))
      .toBe("이 배치의 스케줄(매일 02:00 (KST) · 사용 중)과 실행 기록·보존 회차 기록도 함께 삭제됩니다(감사 기록은 남습니다).");
    expect(scheduleDeleteNotice({ kind: "daily", spec: { hour: 2, minute: 0 }, enabled: 0 }))
      .toBe("이 배치의 스케줄(매일 02:00 (KST))과 실행 기록·보존 회차 기록도 함께 삭제됩니다(감사 기록은 남습니다).");
    for (const t of [SCHEDULE_DELETE_HINT, scheduleDeleteNotice({ kind: "daily", spec: { hour: 2, minute: 0 }, enabled: 0 })]) {
      expect(t).not.toMatch(/(?<!보존 )회차 기록/);
    }
  });
});
