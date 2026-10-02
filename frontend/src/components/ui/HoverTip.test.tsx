import { expect, test } from "vitest";
import { placeAbove, placeBeside } from "./HoverTip";

const col = { left: 100, right: 120, top: 300, bottom: 400 };

test("placeAbove: 앵커 위 가운데, 화면 가장자리에선 안으로 클램프, 위 공간 없으면 아래", () => {
  expect(placeAbove(col, 80, 40, 1000, 800)).toEqual({ left: 70, top: 254 });
  expect(placeAbove({ ...col, left: 0, right: 10 }, 80, 40, 1000, 800).left).toBe(8);
  expect(placeAbove({ ...col, left: 990, right: 1000 }, 80, 40, 1000, 800).left).toBe(912);
  expect(placeAbove({ ...col, top: 20, bottom: 60 }, 80, 40, 1000, 800).top).toBe(66);
});

test("placeBeside: 오른쪽 우선 → 왼쪽 → 둘 다 안 되면 아래, 세로는 화면 안", () => {
  // 2026-10-02 결함 회귀 방지: 툴팁은 열 바로 옆이다(차트 전체 폭 비율이 아니라)
  expect(placeBeside(col, 288, 260, 1440, 900)).toEqual({ left: 128, top: 300 });
  expect(placeBeside({ ...col, left: 1300, right: 1320 }, 288, 260, 1440, 900)).toEqual({ left: 1004, top: 300 });
  expect(placeBeside(col, 288, 260, 375, 900)).toEqual({ left: 79, top: 404 });
  expect(placeBeside({ ...col, top: 800, bottom: 900 }, 288, 260, 1440, 900).top).toBe(632);
});


test("placeBeside 좁은 화면: 아래가 모자라면 위로, 둘 다 모자라면 화면 안으로", () => {
  // 375x667, 툴팁 288x340: 앵커가 아래쪽이면 위로 뒤집는다(리뷰: 범례 아래쪽이 화면 밖에 그려졌다)
  expect(placeBeside({ left: 100, right: 120, top: 500, bottom: 600 }, 288, 340, 375, 667))
    .toEqual({ left: 79, top: 156 });
  // 위·아래 모두 모자라면 화면 안으로 클램프
  expect(placeBeside({ left: 100, right: 120, top: 200, bottom: 300 }, 288, 400, 375, 667).top).toBe(259);
});
