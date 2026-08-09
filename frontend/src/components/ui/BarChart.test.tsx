import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BarChart, barRects } from "./BarChart";

describe("barRects", () => {
  it("값을 최대값 대비 높이로 사상한다", () => {
    expect(barRects([{ label: "a", value: 1 }, { label: "b", value: 4 }], 100, 80))
      .toEqual([
        { x: 5, y: 60, width: 40, height: 20, label: "a", value: 1 },
        { x: 55, y: 0, width: 40, height: 80, label: "b", value: 4 },
      ]);
  });
  it("전부 0이어도 0으로 나누지 않는다", () => {
    const rects = barRects([{ label: "a", value: 0 }], 100, 80);
    expect(rects[0].height).toBe(0);
  });
});

describe("BarChart", () => {
  it("rect와 title 툴팁을 렌더한다", () => {
    const { container } = render(
      <BarChart data={[{ label: "10시", value: 2 }, { label: "11시", value: 4 }]}
                width={100} height={80} />);
    const rects = container.querySelectorAll("rect");
    expect(rects).toHaveLength(2);
    expect(rects[1].getAttribute("height")).toBe("80");
    expect(container.querySelectorAll("title")[0].textContent).toBe("10시: 2");
  });
  it("빈 데이터는 —", () => {
    render(<BarChart data={[]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
