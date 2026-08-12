import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Sparkline, sparklinePath } from "./Sparkline";

describe("sparklinePath", () => {
  it("값을 viewBox 좌표 path로 사상한다", () => {
    expect(sparklinePath([0, 5, 10], 100, 20)).toBe("M0,20L50,10L100,0");
  });
  it("null은 선을 끊는다 -- 0으로 잇지 않는다", () => {
    // 결측/카운터 리셋 구간을 0으로 이으면 "트래픽이 0이었다"는 거짓말이 된다
    expect(sparklinePath([0, null, 10], 100, 20)).toBe("M0,20M100,0");
  });
  it("평평한 시리즈는 중앙선", () => {
    expect(sparklinePath([3, 3], 100, 20)).toBe("M0,10L100,10");
  });
  it("전부 null이면 빈 path", () => {
    expect(sparklinePath([null, null], 100, 20)).toBe("");
  });
});

describe("Sparkline", () => {
  it("path d를 렌더한다", () => {
    const { container } = render(
      <Sparkline values={[0, 5, 10]} width={100} height={20} label="load1" />);
    expect(container.querySelector("path")!.getAttribute("d"))
      .toBe("M0,20L50,10L100,0");
    expect(container.querySelector("svg")!.getAttribute("viewBox"))
      .toBe("0 0 100 20");
  });
  it("값이 없으면 —", () => {
    render(<Sparkline values={[null, null]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
  it("유효점 1개는 circle 점으로 그린다 -- 첫 리포트 실측값은 결측이 아니다", () => {
    // 값 1개는 bare M path 라 선이 안 보인다 -- "—" 로 접으면 실측값을
    // 결측으로 뭉개는 거짓말이 된다. 점(circle)이 정직한 표현이다.
    const { container } = render(<Sparkline values={[7]} label="load1" />);
    const circle = container.querySelector("circle")!;
    expect(circle).not.toBeNull();
    expect(circle.getAttribute("cx")).toBe("0"); // 단일 값은 step 0 → x=0
    expect(circle.getAttribute("cy")).toBe("16"); // span 0 → 중앙선(height 32 기본)
  });
  it("유효점 1개의 x 는 path 와 같은 step 공식을 따른다", () => {
    // [null, 7, null]: step = 120/(3-1) = 60, 유효점 인덱스 1 → cx 60
    const { container } = render(<Sparkline values={[null, 7, null]} />);
    expect(container.querySelector("circle")!.getAttribute("cx")).toBe("60");
    expect(container.querySelector("circle")!.getAttribute("cy")).toBe("16");
  });
  it("유효점 2개 이상은 path 만 -- circle 없음", () => {
    const { container } = render(
      <Sparkline values={[1, 2]} width={100} height={20} />);
    expect(container.querySelector("path")).not.toBeNull();
    expect(container.querySelector("circle")).toBeNull();
  });
});
