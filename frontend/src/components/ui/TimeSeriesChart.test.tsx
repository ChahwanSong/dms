import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { TimeSeriesChart, seriesLayout, tooltipTranslateX,
         tooltipPlaceBelow } from "./TimeSeriesChart";

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

// ---- 즉시 호버 툴팁(2026-08-29) ----

test("tooltipTranslateX: 끝점은 끝맞춤, 그 외 중앙 (넘침 방지)", () => {
  expect(tooltipTranslateX(0)).toBe("0%");     // 왼끝
  expect(tooltipTranslateX(10)).toBe("0%");
  expect(tooltipTranslateX(50)).toBe("-50%");  // 중앙
  expect(tooltipTranslateX(90)).toBe("-100%"); // 오른끝
  expect(tooltipTranslateX(100)).toBe("-100%");
});

test("tooltipPlaceBelow: 위쪽 점(값 큰 쪽)은 툴팁을 아래로 (상단 클리핑 방지)", () => {
  expect(tooltipPlaceBelow(90)).toBe(true);   // 최고점 부근 -> 아래
  expect(tooltipPlaceBelow(20)).toBe(false);  // 낮은 점 -> 위
});

const TP = [
  { t: 1000, y: 1024, label: "p1",
    tooltip: [{ k: "시간", v: "2026-08-04 10:12:42 KST" },
              { k: "요청자", v: "alice" }, { k: "실 사용량", v: "1.0 KiB" }] },
  { t: 2000, y: 2048, label: "p2",
    tooltip: [{ k: "시간", v: "2026-08-05 10:12:42 KST" },
              { k: "요청자", v: "bob" }, { k: "실 사용량", v: "2.0 KiB" }] },
];

test("호버 즉시 구조화 툴팁(시간·요청자·용량) 표시, 벗어나면 사라진다", async () => {
  render(<TimeSeriesChart points={TP} label="추이" formatY={String} emptyText="-" />);
  // 호버 전엔 툴팁 없음
  expect(screen.queryByRole("tooltip")).toBeNull();
  const dot = screen.getByRole("button", { name: "p1" });
  await userEvent.hover(dot);
  const tip = screen.getByRole("tooltip");
  expect(tip).toHaveTextContent("시간");
  expect(tip).toHaveTextContent("2026-08-04 10:12:42 KST");
  expect(tip).toHaveTextContent("요청자");
  expect(tip).toHaveTextContent("alice");
  expect(tip).toHaveTextContent("실 사용량");
  expect(tip).toHaveTextContent("1.0 KiB");
  await userEvent.unhover(dot);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("키보드 포커스도 툴팁을 띄운다(마우스 없는 접근성)", () => {
  render(<TimeSeriesChart points={TP} label="추이" formatY={String} emptyText="-" />);
  fireEvent.focus(screen.getByRole("button", { name: "p2" }));
  expect(screen.getByRole("tooltip")).toHaveTextContent("bob");
});

test("tooltip 미지정이면 label 한 줄로 폴백", async () => {
  render(<TimeSeriesChart points={P} label="추이" formatY={String} emptyText="-" />);
  await userEvent.hover(screen.getByRole("button", { name: "p1: 1.0 KiB" }));
  expect(screen.getByRole("tooltip")).toHaveTextContent("p1: 1.0 KiB");
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
