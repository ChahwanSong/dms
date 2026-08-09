// 손수 SVG 막대(설계 §2.1). barRects를 순수 함수로 분리해 값→rect 기하를
// 단언으로 테스트한다 -- 렌더 스냅샷보다 회귀를 정확히 잡는다.
const r2 = (n: number) => Math.round(n * 100) / 100;

export interface BarDatum { label: string; value: number }

export function barRects(data: BarDatum[], width: number, height: number) {
  const max = Math.max(...data.map((d) => d.value), 1); // 전부 0이어도 0-나눗셈 없음
  const slot = width / data.length;
  return data.map((d, i) => {
    const h = r2((d.value / max) * height);
    return { x: r2(i * slot + slot * 0.1), y: r2(height - h),
             width: r2(slot * 0.8), height: h, label: d.label, value: d.value };
  });
}

export function BarChart({ data, width = 240, height = 80, label }: {
  data: BarDatum[]; width?: number; height?: number; label?: string;
}) {
  if (data.length === 0) return <span className="text-muted text-xs">—</span>;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full"
         preserveAspectRatio="none" role="img" aria-label={label}>
      {barRects(data, width, height).map((r, i) => (
        <rect key={i} x={r.x} y={r.y} width={r.width} height={r.height}
              fill="currentColor" opacity={0.85}>
          {/* 축 라벨 대신 title 툴팁 -- 스파크라인급 밀도에서 텍스트 축은 겹친다 */}
          <title>{`${r.label}: ${r.value}`}</title>
        </rect>
      ))}
    </svg>
  );
}
