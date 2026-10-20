import { expect, test } from "vitest";
import { pctText, sizeBytesText, sizeDistribution, sizeRangeLabel, sizeRangeTitle } from "./sizeBuckets";
import type { HistogramBucket } from "./types";

// dscan 실측 10구간(테스트베드 scan-history 응답 그대로 -- 마지막 구간은 upper_inclusive 키가 없다).
const DSCAN: [number, number | null, string][] = [
  [0, 4096, "4 KiB 이하"], [4097, 65536, "4–64 KiB"], [65537, 1048576, "64 KiB–1 MiB"],
  [1048577, 16777216, "1–16 MiB"], [16777217, 268435456, "16–256 MiB"], [268435457, 1073741824, "256 MiB–1 GiB"],
  [1073741825, 17179869184, "1–16 GiB"], [17179869185, 274877906944, "16–256 GiB"],
  [274877906945, 4398046511104, "256 GiB–4 TiB"], [4398046511105, null, "4 TiB 초과"],
];
const bucket = (lo: number, hi: number | null, count?: number): HistogramBucket => ({
  bucket: hi === null ? `[${lo},INF]` : `[${lo},${hi}]`, lower_inclusive: lo,
  ...(hi === null ? {} : { upper_inclusive: hi }), ...(count === undefined ? {} : { count }) });

test("sizeRangeLabel: dscan 10구간을 사람 말로(1024 단위, 같은 단위면 앞 단위 생략)", () => {
  DSCAN.forEach(([lo, hi, want], i) => expect(sizeRangeLabel(bucket(lo, hi), i)).toBe(want));
});

test("sizeRangeLabel: 숫자 필드가 없으면 라벨을 읽고, 둘 다 없으면 원 라벨·「구간 N」(지어내지 않는다)", () => {
  expect(sizeRangeLabel({ bucket: "[0,4096]" }, 0)).toBe("4 KiB 이하");
  expect(sizeRangeLabel({ bucket: "[4398046511105,INF]" }, 9)).toBe("4 TiB 초과");
  expect(sizeRangeLabel({ bucket: "small" }, 2)).toBe("small");
  expect(sizeRangeLabel({}, 3)).toBe("구간 4");
  // 정확하지 않은 경계는 근사(정확값은 title)
  expect(sizeRangeLabel({ bucket: "[1000,2000]" }, 1)).toBe("1,000 B–2 KiB");
  expect(sizeRangeLabel({ lower_inclusive: 4096, upper_inclusive: 65535 }, 1)).toBe("4–64 KiB");
  // 하한이 단위 정수배인 열린 구간은 「이상」
  expect(sizeRangeLabel({ lower_inclusive: 1048576 }, 0)).toBe("1 MiB 이상");
  // 변조: 뒤집힌 경계·음수는 경계 모름 -> 원 라벨 또는 구간 N
  expect(sizeRangeLabel({ lower_inclusive: 65536, upper_inclusive: 4096 }, 0)).toBe("구간 1");
  expect(sizeRangeLabel({ bucket: "x", lower_inclusive: -5 }, 0)).toBe("x");
});

test("sizeRangeTitle: 정확한 바이트 경계 -- 첫 구간은 빈 파일 포함, 열린 구간은 「이상」, 모르면 없음", () => {
  expect(sizeRangeTitle(bucket(0, 4096))).toBe("0 B ~ 4,096 B(빈 파일 포함)");
  expect(sizeRangeTitle(bucket(4097, 65536))).toBe("4,097 B ~ 65,536 B");
  expect(sizeRangeTitle(bucket(4398046511105, null))).toBe("4,398,046,511,105 B 이상");
  expect(sizeRangeTitle({ bucket: "small" })).toBeUndefined();
});

test("sizeDistribution: 비중 = 개수/합, 0개 구간도 줄로 남는다", () => {
  const d = sizeDistribution(DSCAN.map(([lo, hi], i) => bucket(lo, hi, [22, 0, 0, 4][i] ?? 0)))!;
  expect(d.total).toBe(26);
  expect(d.rows).toHaveLength(10);
  expect(d.rows[0].pct).toBeCloseTo(84.6, 1);
  expect(d.rows[3].pct).toBeCloseTo(15.4, 1);
  expect(d.rows[1]).toMatchObject({ label: "4–64 KiB", count: 0, pct: 0 });
  expect(d.hasBytes).toBe(false);             // dscan 은 구간별 용량을 내지 않는다
});

test("sizeDistribution: 부재·빈 배열은 null(모름 ≠ 0), 합 0 은 total 0 · 비중 null", () => {
  expect(sizeDistribution(undefined)).toBeNull();
  expect(sizeDistribution(null)).toBeNull();
  expect(sizeDistribution([])).toBeNull();
  const zero = sizeDistribution([bucket(0, 4096, 0), bucket(4097, null, 0)])!;
  expect(zero.total).toBe(0);
  expect(zero.rows.map((r) => r.pct)).toEqual([null, null]);
});

test("sizeDistribution: 한 구간이라도 개수를 모르면 분모 모름 -- 비중 전부 null, 다른 개수는 그대로", () => {
  for (const bad of [undefined, -1, 1.5, Number.NaN]) {
    const d = sizeDistribution([bucket(0, 4096, 5), { ...bucket(4097, null), ...(bad === undefined ? {} : { count: bad }) }])!;
    expect(d.total).toBeNull();
    expect(d.rows.map((r) => r.count)).toEqual([5, null]);
    expect(d.rows.map((r) => r.pct)).toEqual([null, null]);
  }
});

test("sizeDistribution: 모든 구간에 용량이 있을 때만 hasBytes", () => {
  expect(sizeDistribution([{ ...bucket(0, 4096, 1), bytes: 10 }, { ...bucket(4097, null, 0), bytes: 0 }])!.hasBytes).toBe(true);
  expect(sizeDistribution([{ ...bucket(0, 4096, 1), bytes: 10 }, bucket(4097, null, 0)])!.hasBytes).toBe(false);
});

test("pctText·sizeBytesText", () => {
  expect([0, 0.05, 0.3, 9.96, 10, 89.4].map(pctText)).toEqual(["0%", "<0.1%", "0.3%", "10.0%", "10%", "89%"]);
  expect([0, 1023, 1024, 17786075].map(sizeBytesText)).toEqual(["0 B", "1023 B", "1.0 KiB", "17.0 MiB"]);
});
