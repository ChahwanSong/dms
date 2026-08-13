import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Sparkline, sparklinePath } from "./Sparkline";

describe("sparklinePath", () => {
  it("값을 viewBox 좌표 path로 사상한다", () => {
    expect(sparklinePath([0, 5, 10], 100, 20)).toBe("M0,20L50,10L100,0");
  });
  it("하한은 항상 0 고정 -- 창 최소가 하한이 아니다", () => {
    // 이전 구현은 창 내 min~max 정규화라 [5,10]의 5가 바닥에 붙었다 -- 0의
    // 위치가 지워지고 좁은 변동이 화면 전체로 펴지는 왜곡. 이제 5는 중앙이다.
    expect(sparklinePath([5, 10], 100, 20)).toBe("M0,10L100,0");
  });
  it("null은 선을 끊는다 -- 0으로 잇지 않는다", () => {
    // 결측/카운터 리셋 구간을 0으로 이으면 "트래픽이 0이었다"는 거짓말이 된다
    expect(sparklinePath([0, null, 10], 100, 20)).toBe("M0,20M100,0");
  });
  it("평평한 0 아닌 시리즈는 상단 -- 값이 곧 창 최대다", () => {
    // 이전의 "평평하면 중앙선" 규칙은 하한이 창 최소였을 때의 임시방편.
    // 0 고정 하한에서는 3이 상한(창 최대)이므로 상단이 실값 위치다.
    expect(sparklinePath([3, 3], 100, 20)).toBe("M0,0L100,0");
  });
  it("전부 0인 시리즈는 바닥 -- 0은 하한의 실값 위치다", () => {
    expect(sparklinePath([0, 0], 100, 20)).toBe("M0,20L100,20");
  });
  it("전부 null이면 빈 path", () => {
    expect(sparklinePath([null, null], 100, 20)).toBe("");
  });
  it("NaN/Infinity 는 null 과 같은 절단이다 -- 좌표 문자열에 NaN 이 새지 않는다", () => {
    // 메트릭 파이프라인이 0/0 이나 오버플로를 흘리면 path d="...NaN..." 이 되어
    // SVG 가 통째로 안 그려진다 -- Number.isFinite 필터(슬라이스 26 통합분)의 그물.
    expect(sparklinePath([0, NaN, 10], 100, 20)).toBe("M0,20M100,0");
    expect(sparklinePath([Infinity, -Infinity], 100, 20)).toBe("");
  });
  it("고정 도메인은 창과 무관한 절대 스케일이다", () => {
    // 메모리 45~55% 구간이 화면 전체로 펴지지 않고 중앙 부근의 좁은 띠로 남는다
    expect(sparklinePath([45, 55], 100, 20, { min: 0, max: 100 }))
      .toBe("M0,11L100,9");
    expect(sparklinePath([0, 50, 100], 100, 20, { min: 0, max: 100 }))
      .toBe("M0,20L50,10L100,0");
  });
  it("고정 도메인의 평평한 시리즈는 실값 위치다 -- 50%는 중앙, 0%는 바닥", () => {
    expect(sparklinePath([50, 50], 100, 20, { min: 0, max: 100 }))
      .toBe("M0,10L100,10");
    expect(sparklinePath([0, 0], 100, 20, { min: 0, max: 100 }))
      .toBe("M0,20L100,20");
  });
  it("고정 상한을 넘는 값은 잘리지 않는다 -- 스케일이 데이터까지 늘어난다", () => {
    // load 는 코어 수를 넘을 수 있다(오버서브스크립션). 상한 2 에 값 4 가 오면
    // 스케일 상한이 4 로 늘어 4 가 상단에 그려진다 -- 포화 초과가 보여야 한다.
    expect(sparklinePath([1, 4], 100, 20, { min: 0, max: 2 }))
      .toBe("M0,15L100,0");
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
  it("고정 상한이 있으면 기준선을 긋는다 -- 데이터가 상한 안이면 상단 라인", () => {
    const { container } = render(
      <Sparkline values={[10, 20]} width={100} height={20}
                 domain={{ min: 0, max: 100 }} />);
    const line = container.querySelector("line")!;
    expect(line).not.toBeNull();
    expect(line.getAttribute("y1")).toBe("0");
    expect(line.getAttribute("y2")).toBe("0");
    expect(line.getAttribute("x2")).toBe("100");
  });
  it("데이터가 상한을 넘으면 기준선이 상단 아래로 내려온다 -- 초과가 보인다", () => {
    // 상한 2, 데이터 최대 4 → 스케일 상한 4. 기준선(2)은 중앙(y=10)에 남는다.
    const { container } = render(
      <Sparkline values={[1, 4]} width={100} height={20}
                 domain={{ min: 0, max: 2 }} />);
    expect(container.querySelector("line")!.getAttribute("y1")).toBe("10");
  });
  it("자연 상한이 없으면(max null/미지정) 기준선도 없다", () => {
    const { container } = render(
      <Sparkline values={[1, 4]} width={100} height={20}
                 domain={{ min: 0, max: null }} />);
    expect(container.querySelector("line")).toBeNull();
    const auto = render(<Sparkline values={[1, 4]} width={100} height={20} />);
    expect(auto.container.querySelector("line")).toBeNull();
  });
  it("유효점 1개는 circle 점으로 그린다 -- 첫 리포트 실측값은 결측이 아니다", () => {
    // 값 1개는 bare M path 라 선이 안 보인다 -- "—" 로 접으면 실측값을
    // 결측으로 뭉개는 거짓말이 된다. 점(circle)이 정직한 표현이다.
    // 좌표는 path 와 같은 스케일: 0 고정 하한에서 단일값 7 은 곧 창 최대 → 상단.
    const { container } = render(<Sparkline values={[7]} label="load1" />);
    const circle = container.querySelector("circle")!;
    expect(circle).not.toBeNull();
    expect(circle.getAttribute("cx")).toBe("0"); // 단일 값은 step 0 → x=0
    expect(circle.getAttribute("cy")).toBe("0"); // 0~7 스케일에서 7 → 상단
  });
  it("유효점 1개도 고정 도메인의 실값 위치를 따른다", () => {
    // 50% 는 0~100 도메인의 중앙(height 32 기본 → cy 16)
    const { container } = render(
      <Sparkline values={[50]} domain={{ min: 0, max: 100 }} />);
    expect(container.querySelector("circle")!.getAttribute("cy")).toBe("16");
  });
  it("유효점 1개의 x 는 path 와 같은 step 공식을 따른다", () => {
    // [null, 7, null]: step = 120/(3-1) = 60, 유효점 인덱스 1 → cx 60
    const { container } = render(<Sparkline values={[null, 7, null]} />);
    expect(container.querySelector("circle")!.getAttribute("cx")).toBe("60");
    expect(container.querySelector("circle")!.getAttribute("cy")).toBe("0");
  });
  it("유효점 2개 이상은 path 만 -- circle 없음", () => {
    const { container } = render(
      <Sparkline values={[1, 2]} width={100} height={20} />);
    expect(container.querySelector("path")).not.toBeNull();
    expect(container.querySelector("circle")).toBeNull();
  });
  it("NaN 옆의 유효점 1개도 circle 로 그린다 -- 좌표는 path 와 같은 step 공식", () => {
    // [NaN, 7]: step = 120/(2-1) = 120, 유효점 인덱스 1 -> cx 120, 0~7 → 상단.
    const { container } = render(<Sparkline values={[NaN, 7]} />);
    expect(container.querySelector("circle")!.getAttribute("cx")).toBe("120");
    expect(container.querySelector("circle")!.getAttribute("cy")).toBe("0");
  });
});
