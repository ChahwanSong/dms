import { expect, test } from "vitest";
import { pctText } from "./sizeBuckets";

// 크기 구간 라벨·분포의 null 판정은 ScanHistograms(sizeBucketLabel·histBars -- ScanHistograms.test.tsx)가 한 벌이다(2026-11-16 두 번째
// 구현 SizeDistribution 을 지웠다). 여기엔 아직 쓰는 비중 표기만 남는다.
test("pctText: 0 은 0%, 0 이 아닌 극소값은 <0.1%(반올림으로 「없다」를 만들지 않는다), 10 미만 한 자리, 그 위 정수", () => {
  expect([0, 0.05, 0.3, 9.96, 10, 89.4].map(pctText)).toEqual(["0%", "<0.1%", "0.3%", "10.0%", "10%", "89%"]);
});
