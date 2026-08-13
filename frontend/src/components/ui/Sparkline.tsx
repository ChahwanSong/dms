// 손수 SVG 스파크라인(설계 §2.1) -- 차트 라이브러리 하나가 수십 개 트랜지티브
// 의존성을 끌고 오는데 필요한 것은 선 하나다. 값 배열만 받는 순수 표현 컴포넌트라
// 값→path 단언으로 테스트한다.
const r2 = (n: number) => Math.round(n * 100) / 100;

// min~max 고정 스케일 도메인. max: null 은 "자연 상한 없음(모름)" -- 하한만 min
// 으로 고정하고 상한은 창 최대를 따른다(네트워크, cpu_count 없는 load 폴백).
// null 을 0 같은 거짓 상한으로 뭉개지 않는 원칙의 도메인 판.
export interface SparklineDomain { min: number; max: number | null }

const AUTO: SparklineDomain = { min: 0, max: null };

// path·단일점 circle·기준선이 같은 스케일을 공유한다 -- 셋이 다른 공식을 쓰면
// 점과 선과 기준선이 어긋난다.
// 하한: 도메인 min(기본 0) 고정. 이전의 "창 최소 = 하한"은 메모리 45~55% 구간도
// 화면 전체 높이로 펴고 0 의 위치를 지우는 이중 왜곡이었다.
// 상한: 고정 도메인이 있어도 데이터가 넘으면 데이터를 따른다(max(도메인, 창 최대)).
// 넘친 값을 잘라내면 load 의 코어 수 초과(오버서브스크립션) 같은 정직한 신호가
// 사라진다 -- 기준선만 도메인 상한 자리에 남아 "얼마나 넘었나"가 보인다.
function scaleOf(nums: number[], domain: SparklineDomain) {
  const lo = Math.min(domain.min, ...nums);
  const hi = Math.max(domain.max ?? domain.min, ...nums);
  return { lo, span: hi - lo };
}

// span 0 은 모든 값이 하한과 같을 때뿐(예: 전부 0) -- 값이 곧 하한이므로 바닥이
// 실값 위치다. 이전의 "평평하면 중앙선(0.5)" 규칙은 하한이 창 최소였을 때의
// 임시방편이라 고정 하한에선 거짓 위치가 된다(0% 를 중앙에 그린다).
const yOf = (v: number, lo: number, span: number, height: number) =>
  r2(height - (span === 0 ? 0 : (v - lo) / span) * height);

export function sparklinePath(
  values: (number | null)[], width: number, height: number,
  domain: SparklineDomain = AUTO,
): string {
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  if (nums.length === 0) return "";
  const { lo, span } = scaleOf(nums, domain);
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
    d += `${pen ? "L" : "M"}${r2(i * step)},${yOf(v, lo, span, height)}`;
    pen = true;
  });
  return d;
}

export function Sparkline({ values, width = 120, height = 32, label, domain = AUTO }: {
  values: (number | null)[]; width?: number; height?: number; label?: string;
  domain?: SparklineDomain;
}) {
  const d = sparklinePath(values, width, height, domain);
  if (!d) return <span className="text-muted text-xs">—</span>;
  const nums = values.filter((v): v is number => v !== null && Number.isFinite(v));
  const { lo, span } = scaleOf(nums, domain);
  // 유효점이 정확히 1개면 path 는 bare M 이라 아무것도 안 보인다. "—" 로 접지
  // 않는 이유: 첫 리포트 1점은 실측값이지 결측이 아니다 -- 0 과 null 을 뭉개지
  // 않는 원칙의 SVG 판. 좌표는 path 와 같은 규칙(step 공식, scaleOf 스케일).
  const validIdx = values
    .map((v, i) => (v !== null && Number.isFinite(v) ? i : null))
    .filter((i): i is number => i !== null);
  const step = values.length > 1 ? width / (values.length - 1) : 0;
  // 상한 기준선: 고정 상한(100%·코어 수)이 있을 때만. 데이터가 상한 안이면 상단
  // 라인이고, 넘으면 스케일이 늘어나 기준선이 상단 아래로 내려온다(위 scaleOf).
  // 상한 라벨(수치)은 SVG 밖 캡션이 맡는다 -- preserveAspectRatio="none" 아래의
  // SVG 텍스트는 가로로 왜곡된다. span 0(퇴화 도메인)이면 자리가 없어 긋지 않는다.
  const capY = domain.max !== null && span > 0
    ? yOf(domain.max, lo, span, height) : null;
  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-8"
         preserveAspectRatio="none" role="img" aria-label={label}>
      {/* currentColor -- 색은 부모의 text-* 유틸리티가 정한다(라이트/다크 공통) */}
      {capY !== null && (
        <line x1={0} y1={capY} x2={width} y2={capY} stroke="currentColor"
              strokeWidth={1} opacity={0.25} vectorEffect="non-scaling-stroke" />
      )}
      <path d={d} fill="none" stroke="currentColor" strokeWidth={1.5}
            vectorEffect="non-scaling-stroke" />
      {validIdx.length === 1 && (
        <circle cx={r2(validIdx[0] * step)}
                cy={yOf(values[validIdx[0]] as number, lo, span, height)}
                r={1.5} fill="currentColor" />
      )}
    </svg>
  );
}
