// 손수 시계열 차트(사용량 분석, 2026-08-23). BarChart 의 규약을 시간축으로 확장:
// - 기하는 순수 함수(seriesLayout)로 분리해 단언으로 테스트한다(barLayout 선례).
// - 면·선은 preserveAspectRatio="none" SVG(연속 시간축은 div 열로는 못 그린다).
//   텍스트는 전부 SVG 밖 HTML -- 스트레치 SVG 안 텍스트는 가로로 왜곡된다
//   (Sparkline·BarChart 누적 라벨 선례).
// - Y 축은 항상 0 에서 시작한다: 바닥을 자른 면 차트는 작은 변화를 절벽으로
//   보이게 하는 고전적 거짓말이다(용량 추이에서 특히).
// - 점이 클릭 가능(onPointClick)하므로 루트는 role="img" 가 아니라 group 이다
//   -- img 는 자식을 AT 에서 숨겨 버튼이 닿지 않게 된다(BarChart 와 다른 이유).
// - 호버/포커스 즉시 커스텀 툴팁(2026-08-29 사용자 요청): 브라우저 기본 title 은
//   ~1s 지연·OS 스타일이라 "바로" 안 뜬다. 구조화 행(tooltip)을 지연 없이 렌더한다.
import { useState } from "react";
import { r2 } from "./BarChart";

export interface TimeSeriesPoint {
  t: number;        // epoch seconds -- x 는 시간 비례(등간격이 아니다)
  y: number;
  label: string;    // 접근성(aria-label) 문구 + 툴팁 폴백(호출자가 사람 표기로)
  // 즉시 툴팁의 구조화 행(라벨:값). 미지정이면 label 한 줄로 폴백.
  tooltip?: { k: string; v: string }[];
}

// 값 영역 상한 %(트랙 위 여백) -- 최대점 위에 점·포커스 링 자리가 남아야 한다.
const HEADROOM = 90;

/** 시간 비례 x(0..100)·바닥 기준 y%(0..HEADROOM). 전 구간 동일 시각(단일 점
    포함)은 x=50 가운데 -- 0 나눗셈 없이, "간격 정보 없음"을 중앙 배치로 말한다. */
export function seriesLayout(points: TimeSeriesPoint[]) {
  const ts = points.map((p) => p.t);
  const tMin = Math.min(...ts), tMax = Math.max(...ts);
  const span = tMax - tMin;
  const yMax = Math.max(...points.map((p) => p.y), 1); // 전부 0이어도 0-나눗셈 없음
  return {
    yMax,
    pts: points.map((p) => ({
      x: span === 0 ? 50 : r2(((p.t - tMin) / span) * 100),
      yPct: p.y === 0 ? 0 : r2((p.y / yMax) * HEADROOM),
    })),
  };
}

// 툴팁 수평 정렬: 점이 왼쪽 끝이면 왼끝맞춤(0%), 오른쪽 끝이면 오른끝맞춤
// (-100%), 그 외엔 중앙(-50%). 끝점에서 중앙정렬 툴팁이 컨테이너를 넘어 가로
// 스크롤(e2e L1 금지)을 만드는 걸 막는 결정적 규칙이라 순수 함수로 뺀다.
export function tooltipTranslateX(xPct: number): string {
  return xPct < 15 ? "0%" : xPct > 85 ? "-100%" : "-50%";
}

// 점이 위쪽(값이 큰 쪽)이면 툴팁을 점 아래에 둔다 -- 위에 두면 차트 상단 밖으로
// 잘린다. 임계 60%는 트랙 상단 40% 안의 점을 아래로 보낸다.
export function tooltipPlaceBelow(yPct: number): boolean {
  return yPct > 60;
}

export function TimeSeriesChart({ points, label, formatY, formatX, emptyText,
                                  onPointClick }: {
  points: TimeSeriesPoint[]; label: string;
  formatY: (n: number) => string;
  // x 축 양끝(첫·마지막 시각) 라벨. 미지정이면 축 라벨 없음 -- 폭이 좁은 자리용.
  formatX?: (t: number) => string;
  emptyText: string;
  onPointClick?: (index: number) => void;
}) {
  // 호버/포커스한 점 인덱스(즉시 툴팁·강조). 마우스 없이 키보드 포커스도 뜬다.
  const [active, setActive] = useState<number | null>(null);
  // 빈 배열은 정상값(그릴 포인트 0건) -- BarChart 와 같은 명시 계약.
  if (points.length === 0) return <p className="text-muted text-xs">{emptyText}</p>;
  const { yMax, pts } = seriesLayout(points);
  // 라벨은 실제 최대로 말한다: seriesLayout 의 yMax=1 폴백은 0-나눗셈 방지용
  // 기하 장치일 뿐이라, 전부 0 인 시계열에 「최대 1」 이라고 적으면 축이 없는
  // 값을 지어낸다(리뷰 확인). 0 이면 중간 눈금 라벨도 생략(0 의 절반도 0).
  const realMax = Math.max(...points.map((p) => p.y));
  const svgPts = pts.map((p) => `${p.x},${r2(100 - p.yPct)}`);
  // 면(area)은 선 아래를 바닥(0)까지 채운다 -- 첫·끝 x 로 내려 닫는다.
  const area = [`${pts[0].x},100`, ...svgPts, `${pts[pts.length - 1].x},100`];
  const tipRows = (i: number) =>
    points[i].tooltip ?? [{ k: "", v: points[i].label }];
  return (
    <div aria-label={label} role="group" className="max-w-3xl">
      {/* y 축 구실: 최대값 한 점 + 중간 눈금(점선) -- 매 점 값은 호버 툴팁이 든다 */}
      <p className="mb-0.5 text-right text-[10px] tabular-nums text-muted">
        최대 {formatY(realMax)}
      </p>
      <div className="relative h-40">
        {/* pointer-events-none 필수: SVG 가 트랙 전면을 덮어 실제 마우스 호버가
            점 버튼 대신 SVG 에 잡히면 툴팁이 안 뜬다(실화면 확인). 보조 그래픽이라
            이벤트 대상이 아니다. */}
        <svg aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none"
             className="pointer-events-none absolute inset-0 h-full w-full text-accent">
          {/* 중간 눈금(최대의 절반 자리). HEADROOM 좌표계 -- 점·선과 같은 척도 */}
          <line x1="0" x2="100" y1={100 - HEADROOM / 2} y2={100 - HEADROOM / 2}
                stroke="currentColor" strokeWidth={0.5} opacity={0.15}
                strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
          <polygon points={area.join(" ")} fill="currentColor" opacity={0.12} />
          <polyline points={svgPts.join(" ")} fill="none" stroke="currentColor"
                    strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
          {/* 호버 점 세로 가이드 -- 어느 x 를 보고 있는지 눈이 즉시 잡는다 */}
          {active !== null && (
            <line x1={pts[active].x} x2={pts[active].x} y1="0" y2="100"
                  stroke="currentColor" strokeWidth={0.5} opacity={0.25}
                  vectorEffect="non-scaling-stroke" />
          )}
        </svg>
        {/* 중간 눈금 라벨(HTML -- 스트레치 SVG 밖). 전부 0 이면 생략 -- 0 의
            절반 눈금은 정보가 아니라 소음이다. */}
        {realMax > 0 && (
          <span className="pointer-events-none absolute right-0 text-[10px] tabular-nums text-muted"
                style={{ top: `${100 - HEADROOM / 2}%` }}>
            {formatY(yMax / 2)}
          </span>
        )}
        {pts.map((p, i) => (
          // 점은 버튼(클릭 → 해당 스캔 상세). 호버/포커스 즉시 툴팁을 띄운다
          // (기본 title 은 지연·OS 스타일이라 제거). 히트 영역은 시각 점보다 넓게
          // (h-4 w-4 투명 버튼 + 안쪽 작은 점)라 10px 점을 정조준할 필요가 없다.
          <button key={i} type="button" aria-label={points[i].label}
                  onClick={onPointClick ? () => onPointClick(i) : undefined}
                  onMouseEnter={() => setActive(i)}
                  onMouseLeave={() => setActive((a) => (a === i ? null : a))}
                  onFocus={() => setActive(i)}
                  onBlur={() => setActive((a) => (a === i ? null : a))}
                  className={`absolute grid h-4 w-4 -translate-x-1/2 -translate-y-1/2
                              place-items-center rounded-full
                              ${onPointClick ? "cursor-pointer" : "cursor-default"}`}
                  style={{ left: `${p.x}%`, top: `${100 - p.yPct}%` }}>
            <span className={`block rounded-full border border-surface bg-accent
                              transition-transform
                              ${active === i ? "h-3 w-3 ring-2 ring-accent/30"
                                             : "h-2.5 w-2.5"}`} />
          </button>
        ))}
        {/* 즉시 툴팁: active 일 때만 렌더(조건부라 지연 0). 끝점·상단에서 넘치지
            않게 수평/수직 앵커를 좌표로 결정한다. pointer-events-none 이라 점
            호버를 가로채지 않는다. */}
        {active !== null && (() => {
          const p = pts[active];
          const below = tooltipPlaceBelow(p.yPct);
          const tx = tooltipTranslateX(p.x);
          return (
            <div role="tooltip"
                 className="pointer-events-none absolute z-10 whitespace-nowrap
                            rounded-md border border-line bg-surface px-2.5 py-1.5
                            text-xs shadow-soft"
                 style={{
                   left: `${p.x}%`,
                   top: below ? `calc(${100 - p.yPct}% + 12px)`
                              : `calc(${100 - p.yPct}% - 12px)`,
                   transform: `translate(${tx}, ${below ? "0" : "-100%"})`,
                 }}>
              {tipRows(active).map((r, ri) => (
                <div key={ri} className="flex items-baseline gap-2">
                  {r.k && <span className="text-muted">{r.k}</span>}
                  <span className="ml-auto tabular-nums font-medium">{r.v}</span>
                </div>
              ))}
            </div>
          );
        })()}
      </div>
      {formatX && (
        // 양끝 시각만 -- x 는 시간 비례라 중간 라벨은 점 위치와 안 맞아 오독을
        // 만든다(점별 시각은 호버 툴팁으로). justify-between 이 x=0/100 과 정렬.
        <div className="mt-1 flex justify-between text-[10px] tabular-nums text-muted">
          <span>{formatX(points[0].t)}</span>
          {points.length > 1 && <span>{formatX(points[points.length - 1].t)}</span>}
        </div>
      )}
    </div>
  );
}
