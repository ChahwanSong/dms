import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { BarChart, barLayout, labelStep, cumulativeLayout,
         cumulativeLabelBottom } from "./BarChart";

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

describe("BarChart 고밀도(>10버킷)", () => {
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

describe("BarChart formatValue(하위호환 사람 표기)", () => {
  it("저밀도: 값 라벨·툴팁에 formatValue 를 쓴다", () => {
    render(<BarChart data={[{ label: "[0d,1d]", value: 1024 }]} label="온도"
                     formatValue={(n) => `${n / 1024} KiB`} />);
    expect(screen.getByText("1 KiB")).toBeInTheDocument();
    const chart = screen.getByRole("img", { name: "온도" });
    expect(chart.querySelector("[title]")!.getAttribute("title"))
      .toBe("[0d,1d]: 1 KiB");
  });
  it("고밀도: 최대값 눈금·툴팁에도 적용", () => {
    const many = Array.from({ length: 24 }, (_, i) => (
      { label: `${i}`, value: i === 0 ? 2048 : 0 }));
    render(<BarChart data={many} label="온도밀집"
                     formatValue={(n) => `${n / 1024} KiB`} />);
    expect(screen.getByText("최대 2 KiB")).toBeInTheDocument();
  });
});

describe("BarChart 저밀도 상한(9·10버킷 저밀도 / 11버킷부터 고밀도)", () => {
  it("dscan 시간 히스토그램(9버킷)의 전 라벨·값이 보인다", () => {
    // 데이터 온도 히스토그램이 9버킷 — 고밀도로 떨어지면 라벨이 솎여 구간을
    // 읽을 수 없다. 잡 통계(6버킷) 저밀도·24버킷 고밀도 무영향.
    const nine = Array.from({ length: 9 }, (_, i) => (
      { label: `b${i}`, value: i }));
    render(<BarChart data={nine} label="아홉" />);
    for (const d of nine) expect(screen.getByText(d.label)).toBeInTheDocument();
    // 저밀도 증거: 최대값 눈금(고밀도 전용)이 없다
    expect(screen.queryByText("최대 8")).toBeNull();
  });
  it("dscan 크기 히스토그램(10버킷)도 저밀도 — 전 라벨·값·누적 오버레이", () => {
    // SPARSE_MAX 10 의 근거(실측 d63): dscan file_size_histogram 이 **10버킷**
    // 이다(시간 히스토그램 9버킷과 다르다). 9 였을 땐 고밀도로 떨어져 값 라벨·
    // 누적 오버레이가 통째로 사라지고 축 라벨이 절반만 남았는데, 캡션은 누적선을
    // 설명하고 있어 화면이 거짓말을 했다.
    const ten = Array.from({ length: 10 }, (_, i) => (
      { label: `b${i}`, value: i + 1 }));
    const { container } = render(
      <BarChart data={ten} label="열" cumulative={{ format: (n) => `${n}개` }} />);
    for (const d of ten) expect(screen.getByText(d.label)).toBeInTheDocument();
    expect(screen.getByText("10")).toBeInTheDocument();       // 막대 값 라벨
    expect(screen.queryByText(/^최대 /)).toBeNull();          // 고밀도 눈금 부재
    expect(container.querySelector("polyline")).not.toBeNull();
    expect(container.querySelectorAll(".rounded-full")).toHaveLength(10);
  });
  it("11버킷부터 고밀도 — 경계 반대편도 못으로 박는다", () => {
    const eleven = Array.from({ length: 11 }, (_, i) => (
      { label: `b${i}`, value: i + 1 }));
    const { container } = render(
      <BarChart data={eleven} label="열하나"
                cumulative={{ format: (n) => `${n}개` }} />);
    expect(screen.getByText("최대 11")).toBeInTheDocument();
    expect(container.querySelector("polyline")).toBeNull();
  });
});

describe("BarChart colorOf(막대별 색 — 온도 그라디언트)", () => {
  const TWO = [{ label: "[0d,1d]", value: 4 }, { label: "[1y,)", value: 1 }];
  it("colorOf 미지정: 기존 accent 클래스 렌더 불변(하위호환)", () => {
    const { container } = render(<BarChart data={TWO} label="기본색" />);
    expect(container.getElementsByClassName("bg-accent")).toHaveLength(2);
    expect(container.getElementsByClassName("bg-accent/10")).toHaveLength(2);
  });
  it("저밀도: 막대는 colorOf 색, 트랙은 같은 색의 저채도 판(rgba 0.1)", () => {
    const { container } = render(
      <BarChart data={TWO} label="온도색"
                colorOf={(i) => ["#dc2626", "#3b82f6"][i]} />);
    // colorOf 지정 시 accent 클래스 대신 inline 색 -- 정적 hex(airgap 무관)
    expect(container.getElementsByClassName("bg-accent")).toHaveLength(0);
    expect(container.getElementsByClassName("bg-accent/10")).toHaveLength(0);
    const fills = container.getElementsByClassName("rounded-t");
    expect(fills[0]).toHaveStyle({ backgroundColor: "#dc2626" });
    expect(fills[1]).toHaveStyle({ backgroundColor: "#3b82f6" });
    const tracks = container.getElementsByClassName("rounded-sm");
    expect(tracks[0]).toHaveStyle({ backgroundColor: "rgba(220, 38, 38, 0.1)" });
    expect(tracks[1]).toHaveStyle({ backgroundColor: "rgba(59, 130, 246, 0.1)" });
  });
  it("고밀도(>10버킷)에도 막대·트랙 색이 적용된다", () => {
    const many = Array.from({ length: 12 }, (_, i) => (
      { label: `${i}`, value: i }));
    const { container } = render(
      <BarChart data={many} label="온도밀집" colorOf={() => "#ea580c"} />);
    const fills = container.getElementsByClassName("rounded-t");
    expect(fills).toHaveLength(12);
    expect(fills[3]).toHaveStyle({ backgroundColor: "#ea580c" });
    expect(container.getElementsByClassName("rounded-sm")[0])
      .toHaveStyle({ backgroundColor: "rgba(234, 88, 12, 0.1)" });
    expect(container.getElementsByClassName("bg-accent")).toHaveLength(0);
  });
  it("colorOf 는 index 와 value 를 받는다", () => {
    const seen: [number, number][] = [];
    render(<BarChart data={TWO} label="인자"
                     colorOf={(i, v) => { seen.push([i, v]); return "#dc2626"; }} />);
    expect(seen).toEqual([[0, 4], [1, 1]]);
  });
});

// 누적 산식은 순수 함수로 단언한다(barLayout 관례) -- 왼쪽(hot)부터 running sum,
// 마지막 = 총합. 총합 0 은 null(비중이 정의 불가 -- 0 나눗셈·거짓 100% 방지).
describe("cumulativeLayout", () => {
  it("왼쪽부터 running sum + 총합 대비 비중(마지막 = 총합·100%)", () => {
    expect(cumulativeLayout([
      { label: "a", value: 1024 }, { label: "b", value: 0 },
      { label: "c", value: 3072 },
    ])).toEqual([
      { sum: 1024, frac: 0.25 }, { sum: 1024, frac: 0.25 },
      { sum: 4096, frac: 1 },
    ]);
  });
  it("총합 0 이면 null — 0 은 정상값이되 비중(0/0)은 정의 불가", () => {
    expect(cumulativeLayout([{ label: "a", value: 0 }, { label: "b", value: 0 }]))
      .toBeNull();
  });
});

// 누적 % 라벨의 세로 배치(트랙 하단 기준 %): 막대 값 라벨과 같은 열 중앙 정렬이라
// 세로 충돌만 피하면 된다 -- 기본은 점 위, 겹치면 점 아래, 아래가 트랙 밖이거나
// 그마저 겹치면 막대 라벨 위로 올린다.
describe("cumulativeLabelBottom", () => {
  it("기본: 점 위(막대 라벨과 안 겹치면)", () => {
    expect(cumulativeLabelBottom(75, 2.5)).toBe(79);
  });
  it("점 위가 막대 라벨과 겹치면 점 아래", () => {
    expect(cumulativeLabelBottom(75, 75)).toBe(58);
  });
  it("아래도 막대 라벨과 겹치면 막대 라벨 위로 회피", () => {
    expect(cumulativeLabelBottom(20, 10)).toBe(27.5);
  });
  it("아래가 트랙 밖이면 막대 라벨 위로 회피", () => {
    expect(cumulativeLabelBottom(10, 15)).toBe(32.5);
  });
});

describe("BarChart cumulative(누적 데이터량 오버레이 — 저밀도)", () => {
  // 산식 검증이 쉬운 2버킷: 총합 4096, 누적 25% → 100%
  const DATA = [{ label: "[0d,1d]", value: 1024 }, { label: "[1d,7d]", value: 3072 }];
  const kib = (n: number) => `${n / 1024} KiB`;
  it("미지정: 오버레이 없음 — 기존 마크업 불변(잡 통계·크기 분포 무영향)", () => {
    const { container } = render(<BarChart data={DATA} label="기본" />);
    expect(container.querySelector("svg")).toBeNull();
    expect(container.querySelector(".rounded-full")).toBeNull();
    const chart = screen.getByRole("img", { name: "기본" });
    expect(chart.className).toBe("flex items-start gap-1.5");
    expect(chart.getAttribute("style")).toBeNull();
  });
  it("지정: 꺾은선+버킷별 점 — y=누적 비중(75 스케일), x=버킷 중심", () => {
    const { container } = render(
      <BarChart data={DATA} label="누적" cumulative={{ format: kib }} />);
    const line = container.querySelector("polyline")!;
    expect(line.getAttribute("points")).toBe("25,81.25 75,25");
    expect(line.getAttribute("vector-effect")).toBe("non-scaling-stroke");
    // 보조 그래픽 — 차트의 role img 계약(1개)을 침범하지 않는다
    expect(container.querySelector("svg")!.getAttribute("aria-hidden")).toBe("true");
    const dots = container.querySelectorAll(".rounded-full");
    expect(dots).toHaveLength(2);
    expect((dots[0] as HTMLElement).style.left).toBe("25%");
    expect((dots[0] as HTMLElement).style.top).toBe("4.06rem");  // (100-18.75)·0.05
    expect((dots[1] as HTMLElement).style.top).toBe("1.25rem");  // (100-75)·0.05
  });
  it("값 표기: 점마다 누적 % 라벨 + 툴팁(누적 용량·%)", () => {
    render(<BarChart data={DATA} label="누적" cumulative={{ format: kib }} />);
    expect(screen.getByText("25%").getAttribute("title")).toBe("누적 1 KiB (25%)");
    expect(screen.getByText("100%").getAttribute("title")).toBe("누적 4 KiB (100%)");
  });
  it("% 라벨은 막대 값 라벨과의 충돌을 피해 위/아래로 갈린다", () => {
    render(<BarChart data={DATA} label="누적" cumulative={{ format: kib }} />);
    // 점1(18.75)은 막대1 라벨(25 위)과 겹쳐 점 아래(bottom 1.75 → top 4.91rem)
    expect(screen.getByText("25%").style.top).toBe("4.91rem");
    // 점2(75)는 막대2 라벨(75 위)과 겹쳐 점 아래(bottom 58 → top 2.1rem)
    expect(screen.getByText("100%").style.top).toBe("2.1rem");
  });
  it("루트 폭을 열 상한 합에 맞춰 오버레이(% 좌표)가 막대를 벗어나지 않는다", () => {
    render(<BarChart data={DATA} label="누적" cumulative={{ format: kib }} />);
    const chart = screen.getByRole("img", { name: "누적" });
    // 2열·gap 1개: 2×4rem + 1×0.375rem — 첫·끝 라벨도 이 폭 안(넘침 없음)
    expect(chart.style.maxWidth).toBe("8.375rem");
  });
  it("총합 0 이면 오버레이 생략 — 기존 렌더 그대로(거짓 100% 없음)", () => {
    const { container } = render(
      <BarChart data={[{ label: "a", value: 0 }]} label="영"
                cumulative={{ format: kib }} />);
    expect(container.querySelector("svg")).toBeNull();
    expect(screen.queryByText(/%/)).toBeNull();
    expect(screen.getByRole("img", { name: "영" }).getAttribute("style")).toBeNull();
  });
  it("고밀도(>10버킷)는 범위 밖 — 오버레이를 그리지 않는다", () => {
    const many = Array.from({ length: 12 }, (_, i) => (
      { label: `${i}`, value: 1 }));
    const { container } = render(
      <BarChart data={many} label="밀집" cumulative={{ format: kib }} />);
    expect(container.querySelector("svg")).toBeNull();
  });
});

describe("BarChart 빈 상태", () => {
  it("— 대신 명시 문구", () => {
    render(<BarChart data={[]} />);
    expect(screen.getByText("집계된 잡 없음")).toBeInTheDocument();
  });
});
