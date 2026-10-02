import { expect, test } from "vitest";
import { ageLabel, bucketKey, hotRatio } from "./usageStats";
import { agoText, ageDays } from "../../lib/datetime";

// dscan 실측 나이 버킷(9개) -- 툴팁 범례가 사람 말로 읽혀야 한다(2026-10-02 "빨강/초록/파랑이 뭔지")
const DSCAN_AGES: [number, number | undefined, string][] = [
  [0, 1, "1일 이내"], [2, 7, "2~7일"], [8, 30, "8~30일"], [31, 90, "31~90일"], [91, 180, "91~180일"],
  [181, 365, "181일~1년"], [366, 1095, "1~3년"], [1096, 3650, "3~10년"], [3651, undefined, "10년 이상"],
];

test("ageLabel: dscan 나이 버킷 9개를 사람 말로", () => {
  for (const [lo, hi, want] of DSCAN_AGES) {
    expect(ageLabel({ bucket: "x", min_age_days: lo, ...(hi === undefined ? {} : { max_age_days: hi }) }))
      .toBe(want);
  }
  // 나이 필드가 없으면 원문 라벨, 그것도 없으면 — (지어내지 않는다)
  expect(ageLabel({ bucket: "[0d,1d]" })).toBe("[0d,1d]");
  expect(ageLabel({})).toBe("—");
});

test("bucketKey: CSV 열 이름용 구간 키", () => {
  expect(bucketKey({ bucket: "[0d,1d]" }, 0)).toBe("0d-1d");
  expect(bucketKey({ bucket: "[3651d,INF]" }, 8)).toBe("3651d-INF");
  expect(bucketKey({ bucket: "[0,4096]" }, 0)).toBe("0-4096");
  expect(bucketKey({ min_age_days: 2, max_age_days: 7 }, 1)).toBe("2d-7d");
  expect(bucketKey({ lower_inclusive: 4096 }, 1)).toBe("4096-INF");
  expect(bucketKey({}, 3)).toBe("b3");
});

test("ageDays·agoText: 경과 일수 / 한국어 경과(일 단위), 모름은 null·—", () => {
  const now = Date.parse("2026-10-02T00:00:00Z");
  expect(ageDays("2026-09-02T00:00:00Z", now)).toBe(30);
  expect(ageDays(null, now)).toBeNull();
  expect(ageDays("garbage", now)).toBeNull();
  expect(agoText("2026-10-01T23:59:30Z", now)).toBe("방금");
  expect(agoText("2026-10-01T23:15:00Z", now)).toBe("45분 전");
  expect(agoText("2026-10-01T19:00:00Z", now)).toBe("5시간 전");
  expect(agoText("2026-09-02T00:00:00Z", now)).toBe("30일 전");
  expect(agoText("2023-10-02T00:00:00Z", now)).toBe("1,096일 전");
  expect(agoText(undefined, now)).toBe("—");
});


test("hotRatio: dscan 마지막 구간 [3651d,INF](max_age_days 없음)은 확실한 cold -- 비율이 '모름'이 되지 않는다", () => {
  // 실측 리포트 모양(2026-10-02): 10년 넘은 파일이 1바이트라도 있으면 예전엔 null(—)이었다(리뷰 high)
  expect(hotRatio([{ bucket: "[0d,1d]", min_age_days: 0, max_age_days: 1, bytes: 30 },
                   { bucket: "[3651d,INF]", min_age_days: 3651, bytes: 70 }])).toBeCloseTo(0.3);
  // 하한이 180일 이하인데 상한이 없으면 여전히 모름(그 바이트가 hot 일 수도 있다)
  expect(hotRatio([{ bucket: "x", min_age_days: 100, bytes: 5 }])).toBeNull();
});
