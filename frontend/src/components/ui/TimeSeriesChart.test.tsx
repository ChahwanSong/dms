import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { TimeSeriesChart, seriesLayout } from "./TimeSeriesChart";

// ---- seriesLayout (순수 기하 -- barLayout 테스트 선례) ----

test("seriesLayout: x 는 시간 비례, y 는 최대 기준 90 헤드룸", () => {
  const { yMax, pts } = seriesLayout([
    { t: 0, y: 0, label: "a" },
    { t: 50, y: 50, label: "b" },
    { t: 200, y: 100, label: "c" },
  ]);
  expect(yMax).toBe(100);
  expect(pts.map((p) => p.x)).toEqual([0, 25, 100]);   // 등간격이 아니라 시간 비례
  expect(pts.map((p) => p.yPct)).toEqual([0, 45, 90]);
});

test("seriesLayout: 단일 점·동일 시각은 x=50 (0-나눗셈 없음)", () => {
  expect(seriesLayout([{ t: 7, y: 3, label: "a" }]).pts[0].x).toBe(50);
  const two = seriesLayout([{ t: 7, y: 1, label: "a" }, { t: 7, y: 2, label: "b" }]);
  expect(two.pts.map((p) => p.x)).toEqual([50, 50]);
});

test("seriesLayout: 전부 0 이어도 yMax=1 로 0-나눗셈 없음, 0 은 0%", () => {
  const { yMax, pts } = seriesLayout([{ t: 0, y: 0, label: "a" },
                                      { t: 1, y: 0, label: "b" }]);
  expect(yMax).toBe(1);
  expect(pts.every((p) => p.yPct === 0)).toBe(true);
});

// ---- 렌더 ----

const P = [
  { t: 1000, y: 1024, label: "p1: 1.0 KiB" },
  { t: 2000, y: 2048, label: "p2: 2.0 KiB" },
];

test("빈 포인트는 emptyText (0건은 정상값 -- BarChart 계약)", () => {
  render(<TimeSeriesChart points={[]} label="추이" formatY={String}
                          emptyText="포인트 없음" />);
  expect(screen.getByText("포인트 없음")).toBeInTheDocument();
});

test("점은 라벨 달린 버튼, 클릭이 인덱스로 온다", async () => {
  const onClick = vi.fn();
  render(<TimeSeriesChart points={P} label="추이" formatY={String}
                          emptyText="-" onPointClick={onClick} />);
  const dot = screen.getByRole("button", { name: "p2: 2.0 KiB" });
  await userEvent.click(dot);
  expect(onClick).toHaveBeenCalledWith(1);
});

test("전부 0 인 시계열은 최대 0 을 말하고 눈금을 지어내지 않는다", () => {
  // seriesLayout 의 yMax=1 은 0-나눗셈 방지 장치일 뿐 -- 라벨이 「최대 1」로
  // 새면 축이 없는 값을 지어낸다(리뷰 확인).
  render(<TimeSeriesChart points={[{ t: 1, y: 0, label: "a" },
                                   { t: 2, y: 0, label: "b" }]}
                          label="추이" formatY={(n) => `${n}B`} emptyText="-" />);
  expect(screen.getByText("최대 0B")).toBeInTheDocument();
  expect(screen.queryByText("0.5B")).toBeNull();   // 중간 눈금 생략
});

test("y 축 구실: 최대값 + 중간 눈금, x 축은 양끝 시각", () => {
  render(<TimeSeriesChart points={P} label="추이" formatY={(n) => `${n}B`}
                          formatX={(t) => `t${t}`} emptyText="-" />);
  expect(screen.getByText("최대 2048B")).toBeInTheDocument();
  expect(screen.getByText("1024B")).toBeInTheDocument();   // 중간 눈금(최대/2)
  expect(screen.getByText("t1000")).toBeInTheDocument();
  expect(screen.getByText("t2000")).toBeInTheDocument();
});
