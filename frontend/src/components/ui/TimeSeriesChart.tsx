// 손수 시계열 차트(사용량 분석, 2026-08-23). BarChart 의 규약을 시간축으로 확장:
// - 기하는 순수 함수(seriesLayout)로 분리해 단언으로 테스트한다(barLayout 선례).
// - 면·선은 preserveAspectRatio="none" SVG(연속 시간축은 div 열로는 못 그린다).
//   텍스트는 전부 SVG 밖 HTML -- 스트레치 SVG 안 텍스트는 가로로 왜곡된다
//   (Sparkline·BarChart 누적 라벨 선례).
// - Y 축은 항상 0 에서 시작한다: 바닥을 자른 면 차트는 작은 변화를 절벽으로
//   보이게 하는 고전적 거짓말이다(용량 추이에서 특히).
// - 점이 클릭 가능(onPointClick)하므로 루트는 role="img" 가 아니라 group 이다
//   -- img 는 자식을 AT 에서 숨겨 버튼이 닿지 않게 된다(BarChart 와 다른 이유).
import { r2 } from "./BarChart";

export interface TimeSeriesPoint {
  t: number;        // epoch seconds -- x 는 시간 비례(등간격이 아니다)
  y: number;
  label: string;    // 툴팁·접근성 문구(시각+값을 호출자가 사람 표기로)
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

export function TimeSeriesChart({ points, label, formatY, formatX, emptyText,
                                  onPointClick }: {
  points: TimeSeriesPoint[]; label: string;
  formatY: (n: number) => string;
  // x 축 양끝(첫·마지막 시각) 라벨. 미지정이면 축 라벨 없이(점 title 이 시각을
  // 든다) -- 폭이 좁은 자리용 하위호환.
  formatX?: (t: number) => string;
  emptyText: string;
  onPointClick?: (index: number) => void;
}) {
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
  return (
    <div aria-label={label} role="group" className="max-w-3xl">
      {/* y 축 구실: 최대값 한 점 + 중간 눈금(점선) -- 매 점 값은 title 이 든다 */}
      <p className="mb-0.5 text-right text-[10px] tabular-nums text-muted">
        최대 {formatY(realMax)}
      </p>
      <div className="relative h-40">
        <svg aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none"
             className="absolute inset-0 h-full w-full text-accent">
          {/* 중간 눈금(최대의 절반 자리). HEADROOM 좌표계 -- 점·선과 같은 척도 */}
          <line x1="0" x2="100" y1={100 - HEADROOM / 2} y2={100 - HEADROOM / 2}
                stroke="currentColor" strokeWidth={0.5} opacity={0.15}
                strokeDasharray="2 2" vectorEffect="non-scaling-stroke" />
          <polygon points={area.join(" ")} fill="currentColor" opacity={0.12} />
          <polyline points={svgPts.join(" ")} fill="none" stroke="currentColor"
                    strokeWidth={1.5} vectorEffect="non-scaling-stroke" />
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
          // 점은 버튼(클릭 → 해당 스캔 상세). title 툴팁은 BarChart 관례.
          <button key={i} type="button" title={points[i].label}
                  aria-label={points[i].label}
                  onClick={onPointClick ? () => onPointClick(i) : undefined}
                  className={`absolute h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full
                              border border-surface bg-accent
                              ${onPointClick ? "cursor-pointer hover:scale-125" : "cursor-default"}`}
                  style={{ left: `${p.x}%`, top: `${100 - p.yPct}%` }} />
        ))}
      </div>
      {formatX && (
        // 양끝 시각만 -- x 는 시간 비례라 중간 라벨은 점 위치와 안 맞아 오독을
        // 만든다(점별 시각은 title 로). justify-between 이 x=0/100 과 정렬된다.
        <div className="mt-1 flex justify-between text-[10px] tabular-nums text-muted">
          <span>{formatX(points[0].t)}</span>
          {points.length > 1 && <span>{formatX(points[points.length - 1].t)}</span>}
        </div>
      )}
    </div>
  );
}
