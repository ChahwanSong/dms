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
});
