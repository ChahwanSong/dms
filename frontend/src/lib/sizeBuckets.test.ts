import { expect, test } from "vitest";
import { pctText } from "./sizeBuckets";

test("pctText: 0 은 0%(정상값), 0 이 아닌 극소값은 <0.1%, 10 미만은 소수 한 자리, 그 위는 정수", () => {
  expect([0, 0.05, 0.3, 9.96, 10, 89.4].map(pctText)).toEqual(["0%", "<0.1%", "0.3%", "10.0%", "10%", "89%"]);
});
