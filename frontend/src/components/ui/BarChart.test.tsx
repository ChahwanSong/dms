import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BarChart, barLayout, labelStep } from "./BarChart";

// 기하는 barLayout(순수 함수)으로 단언한다 -- 이전 barRects(SVG rect 좌표)와 같은
// 정신이되, div 막대로 바뀌며 계약이 「트랙 대비 % 높이」로 단순해졌다.
describe("barLayout", () => {
  it("값을 최대값 대비 % 높이로 사상한다", () => {
    expect(barLayout([{ label: "a", value: 1 }, { label: "b", value: 4 }]))
      .toEqual([
        { label: "a", value: 1, pct: 25 },
        { label: "b", value: 4, pct: 100 },
      ]);
  });
  it("maxPct 로 위 여백(값 라벨 자리)을 남긴다", () => {
    expect(barLayout([{ label: "a", value: 4 }], 75)[0].pct).toBe(75);
  });
  it("전부 0이어도 0으로 나누지 않는다", () => {
    expect(barLayout([{ label: "a", value: 0 }])[0].pct).toBe(0);
  });
  it("0 이 아닌 극소값은 최소 높이를 받아 0 과 구분된다", () => {
    // 0 은 정상값, 소량은 소량 -- 극소 막대가 픽셀 0 으로 뭉개지면 화면이
    // "빈 버킷"이라고 거짓말한다(트랙 h-20=80px 기준 2px ≈ 2.5%).
    expect(barLayout([{ label: "a", value: 1 }, { label: "b", value: 1000 }])[0].pct)
      .toBe(2.5);
  });
});

describe("labelStep", () => {
  it("8개 이하면 전부(간격 1)", () => {
    expect(labelStep(6)).toBe(1);
    expect(labelStep(8)).toBe(1);
  });
  it("많으면 ~6개만 남는 간격", () => {
    expect(labelStep(24)).toBe(4);   // 00·04·08·12·16·20시 -> 6개
    expect(labelStep(168)).toBe(28); // 7d 를 시간 버킷으로 봐도 6개
  });
});

const HIST = [
  { label: "<10s", value: 2 }, { label: "10-30s", value: 1 },
  { label: "30-60s", value: 0 }, { label: "1-5m", value: 0 },
  { label: "5-30m", value: 0 }, { label: ">30m", value: 0 },
];

describe("BarChart 저밀도(≤8버킷)", () => {
  it("버킷마다 트랙·accent 막대·값·라벨·툴팁을 그린다", () => {
    const { container } = render(<BarChart data={HIST} label="제출 대기 분포" />);
    const chart = screen.getByRole("img", { name: "제출 대기 분포" });
    const cols = chart.querySelectorAll("[title]");
    expect(cols).toHaveLength(6);
    expect(cols[1].getAttribute("title")).toBe("10-30s: 1");
    // 히스토그램 라벨은 솎지 않는다 -- 전 버킷이 축에 보인다
    for (const d of HIST) expect(screen.getByText(d.label)).toBeInTheDocument();
    // 잉크색 회색 대신 accent 막대가 연한 동일 계열 트랙 위에 선다
    expect(container.getElementsByClassName("bg-accent")).toHaveLength(6);
    expect(container.getElementsByClassName("bg-accent/10")).toHaveLength(6);
  });
  it("최대 막대는 값 라벨 자리(75%)를 남기고, 0 버킷도 트랙+0 으로 보인다", () => {
    const { container } = render(<BarChart data={HIST} label="h" />);
    const fills = container.getElementsByClassName("bg-accent");
    expect((fills[0] as HTMLElement).style.height).toBe("75%");
    expect((fills[2] as HTMLElement).style.height).toBe("0%");
    expect(screen.getAllByText("0")).toHaveLength(4);
  });
  it("막대 폭에 상한이 있어 버킷 1개가 화면을 채우지 않는다", () => {
    const { container } = render(
      <BarChart data={[{ label: "01시", value: 2 }]} label="처리량" />);
    expect(container.getElementsByClassName("max-w-16")).toHaveLength(1);
  });
});

describe("BarChart 고밀도(>8버킷)", () => {
  const hours = Array.from({ length: 24 }, (_, i) => (
    { label: `${String(i).padStart(2, "0")}시`, value: i === 3 ? 5 : 0 }));
  it("값 표기는 접고 라벨을 솎으며 최대값 눈금을 단다", () => {
    render(<BarChart data={hours} label="처리량" />);
    const chart = screen.getByRole("img", { name: "처리량" });
    expect(chart.querySelectorAll("[title]")).toHaveLength(24);
    // 라벨은 4간격 6개만 -- 24개를 다 쓰면 겹쳐서 아무것도 못 읽는다
    expect(screen.getByText("00시")).toBeInTheDocument();
    expect(screen.getByText("04시")).toBeInTheDocument();
    expect(screen.queryByText("03시")).toBeNull();
    // 막대 위 값 대신 y축 구실을 하는 최대값 한 점
    expect(screen.getByText("최대 5")).toBeInTheDocument();
    // 값 5 자체는 텍스트 노드로 그리지 않는다(툴팁만) -- "최대 5" 하나여야 한다
    expect(screen.getAllByText(/5/)).toHaveLength(1);
  });
});

describe("BarChart 빈 상태", () => {
  it("— 대신 명시 문구", () => {
    render(<BarChart data={[]} />);
    expect(screen.getByText("집계된 잡 없음")).toBeInTheDocument();
  });
});
