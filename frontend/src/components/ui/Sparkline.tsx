// 손수 SVG 스파크라인(설계 §2.1) -- 차트 라이브러리 하나가 수십 개 트랜지티브
// 의존성을 끌고 오는데 필요한 것은 선 하나다. 값 배열만 받는 순수 표현 컴포넌트라
// 값→path 단언으로 테스트한다.
const r2 = (n: number) => Math.round(n * 100) / 100;

export function sparklinePath(
  values: (number | null)[], width: number, height: number,
): string {
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (nums.length === 0) return "";
  const min = Math.min(...nums);
  const span = Math.max(...nums) - min;
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  let d = "";
  let pen = false;
  values.forEach((v, i) => {
    if (v === null || !Number.isFinite(v)) {
      // null은 결측/카운터 리셋 구간이다 -- 선을 끊는다. 0으로 이으면
      // "그때 값이 0이었다"는 거짓말이 된다.
      pen = false;
      return;
    }
    const norm = span === 0 ? 0.5 : (v - min) / span; // 평평한 시리즈는 중앙선
    d += `${pen ? "L" : "M"}${r2(i * step)},${r2(height - norm * height)}`;
    pen = true;
  });
  return d;
}

export function Sparkline({ values, width = 120, height = 32, label }: {
  values: (number | null)[]; width?: number; height?: number; label?: string;
}) {
  const d = sparklinePath(values, width, height);
  if (!d) return <span className="text-muted text-xs">—</span>;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-8"
         preserveAspectRatio="none" role="img" aria-label={label}>
      {/* currentColor -- 색은 부모의 text-* 유틸리티가 정한다(라이트/다크 공통) */}
      <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5}
            vectorEffect="non-scaling-stroke" />
    </svg>
  );
}
