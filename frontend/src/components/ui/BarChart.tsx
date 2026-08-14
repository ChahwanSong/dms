// 손수 막대 차트(설계 §2.1) -- 라이브러리 없이, div+Tailwind 로 그린다. 이전 SVG
// 구현은 preserveAspectRatio="none" 스트레치라 버킷 1~2개면 막대 하나가 화면
// 반쪽짜리 회색 블록이 됐다(실화면 확인) -- div 는 좌표 왜곡이 없고 텍스트
// 라벨·테마 토큰이 자연스러워 갈아탔다. 기하는 barLayout(순수 함수)으로 분리해
// 단언으로 테스트한다.
export const r2 = (n: number) => Math.round(n * 100) / 100;

export interface BarDatum { label: string; value: number }

// 0 이 아닌 막대의 최소 높이 %. 트랙 h-20=80px 기준 2px -- 극소값이 픽셀 0 으로
// 뭉개지면 화면이 "빈 버킷"이라고 거짓말한다(0 은 정상값, 소량은 소량).
const MIN_PCT = 2.5;

// 값 -> 트랙 대비 % 높이. maxPct 는 트랙 위 여백 -- 저밀도 모드는 막대 위 값
// 라벨이 트랙 안에 살므로 75 로 눌러 라벨 자리를 남긴다(100 이면 최대 막대에서
// 라벨이 트랙 밖으로 잘린다).
export function barLayout(data: BarDatum[], maxPct = 100) {
  const max = Math.max(...data.map((d) => d.value), 1); // 전부 0이어도 0-나눗셈 없음
  return data.map((d) => ({
    label: d.label, value: d.value,
    pct: d.value === 0 ? 0 : Math.max(r2((d.value / max) * maxPct), MIN_PCT),
  }));
}

// X축 라벨 솎기 간격: 버킷 ≤8 이면 전부(히스토그램 6·7d 7·저밀도 창), 그 위로는
// ~6개만 남긴다(24h 시간 버킷 24개를 다 쓰면 겹쳐서 아무것도 못 읽는다).
export function labelStep(n: number): number {
  return n <= 8 ? 1 : Math.ceil(n / 6);
}

// 저밀도 상한 8→9 근거: dscan 시간 히스토그램(데이터 온도)이 9버킷 -- 고밀도로
// 떨어지면 라벨이 솎여 구간을 읽을 수 없다. 기존 소비자 무영향: 잡 통계
// 히스토그램(6버킷)은 원래 저밀도, 24h 처리량(24버킷)은 여전히 고밀도다.
const SPARSE_MAX = 9;        // 이하면 저밀도 모드(값 직표기 가능한 밀도)
const SPARSE_HEADROOM = 75;  // 저밀도 트랙 안 값 라벨 자리

// #rrggbb -> rgba(r,g,b,alpha). colorOf 계약은 정적 6자리 hex 다(airgap: 번들 밖
// 리소스가 아니라 그냥 문자열이다). 트랙(연한 단)을 막대 색의 저채도 판으로
// 파생하기 위한 헬퍼 -- 별도 트랙 색 prop 을 받는 것보다 "막대와 트랙은 같은
// 계열"(기존 accent/accent∕10 관계)이라는 시각 규약이 구조적으로 유지된다.
export function hexTint(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
// 트랙 알파는 기존 bg-accent/10 과 같은 농도 -- 색만 바뀌고 밀도 문법은 유지.
const TRACK_ALPHA = 0.1;

export function BarChart({ data, label, emptyText = "집계된 잡 없음", formatValue,
                           colorOf }: {
  data: BarDatum[]; label?: string; emptyText?: string;
  // 값의 사람 표기(bytes 등). 기본은 숫자 그대로 -- 기존 소비자(잡 통계) 무영향
  // 인 하위호환 옵션이다.
  formatValue?: (n: number) => string;
  // 막대별 의미 색(#rrggbb) -- 온도 그라디언트 등. 미지정이면 기존 accent 클래스
  // 그대로(잡 통계 무영향인 하위호환 옵션). 지정 시 막대는 그 색, 트랙은 같은
  // 색의 저채도 판(hexTint)으로 통일한다.
  colorOf?: (index: number, value: number) => string;
}) {
  const fmt = formatValue ?? ((n: number) => String(n));
  // 트랙/막대의 클래스·inline 스타일 한 벌 -- 저밀도·고밀도 렌더가 같은 규칙을
  // 공유해야 색 지원이 한쪽만 되는 드리프트가 안 생긴다. colorOf 는 막대당 1회만
  // 부른다(트랙 색은 그 반환값에서 파생).
  const trackClass = (base: string) => (colorOf ? base : `${base} bg-accent/10`);
  const trackStyle = (color: string | undefined) =>
    color ? { backgroundColor: hexTint(color, TRACK_ALPHA) } : undefined;
  const fillClass = colorOf ? "" : " bg-accent";
  const fillStyle = (color: string | undefined) =>
    color ? { backgroundColor: color } : undefined;
  // 빈 배열은 "이 창에 집계된 잡이 0건"이라는 정상값 -- "—"(모름) 으로 뭉개지
  // 않고 명시한다(null ≠ 0 규약).
  if (data.length === 0) return <p className="text-muted text-xs">{emptyText}</p>;
  const sparse = data.length <= SPARSE_MAX;
  const bars = barLayout(data, sparse ? SPARSE_HEADROOM : 100);
  if (sparse) {
    return (
      // 열 폭 상한(max-w-16)이 저밀도의 핵심 -- 버킷 1~2개가 컨테이너를 채우며
      // 괴물 블록이 되는 것을 막고, 남는 폭은 오른쪽 여백으로 둔다.
      <div role="img" aria-label={label} className="flex items-start gap-1.5">
        {bars.map((b, i) => {
          const color = colorOf?.(i, b.value);
          return (
          <div key={i} title={`${b.label}: ${fmt(b.value)}`}
               className="flex min-w-0 max-w-16 flex-1 flex-col items-center gap-1">
            {/* 트랙은 막대 색의 연한 단(동일 계열: 기본 accent/10, colorOf 시
                hexTint) -- 값 0 버킷도 "빈 자리"가 아니라 트랙+0 으로 보인다.
                막대 상단만 둥글게(데이터 끝), 밑변은 직각. */}
            <div className={trackClass("relative h-20 w-full max-w-6 overflow-hidden rounded-sm")}
                 style={trackStyle(color)}>
              <span className="absolute inset-x-0 text-center text-[10px] font-medium tabular-nums text-muted"
                    style={{ bottom: `calc(${b.pct}% + 2px)` }}>{fmt(b.value)}</span>
              <div className={`absolute inset-x-0 bottom-0 rounded-t${fillClass}`}
                   style={{ height: `${b.pct}%`, ...fillStyle(color) }} />
            </div>
            <span className="w-full truncate text-center text-[10px] text-muted">{b.label}</span>
          </div>
          );
        })}
      </div>
    );
  }
  const max = Math.max(...data.map((d) => d.value));
  const step = labelStep(data.length);
  return (
    // max-w-xl: 넓은 화면에서 슬롯이 24px 를 넘어 뚱뚱해지는 것을 막는다. 솎은
    // 라벨은 절대배치(% left)라 막대 열과 같은 컨테이너 폭을 공유해야 정렬된다.
    <div role="img" aria-label={label} className="max-w-xl">
      {/* 막대 위 값 대신 y축 구실을 하는 최대값 한 점 -- 고밀도에서 매 막대 값은
          겹쳐서 소음이고, 개별 값은 툴팁이 든다. */}
      <p className="mb-0.5 text-right text-[10px] tabular-nums text-muted">최대 {fmt(max)}</p>
      <div className="flex items-end gap-0.5">
        {bars.map((b, i) => {
          const color = colorOf?.(i, b.value);
          return (
          <div key={i} title={`${b.label}: ${fmt(b.value)}`}
               className={trackClass("relative h-20 min-w-0 flex-1 overflow-hidden rounded-sm")}
               style={trackStyle(color)}>
            <div className={`absolute inset-x-0 bottom-0 rounded-t${fillClass}`}
                 style={{ height: `${b.pct}%`, ...fillStyle(color) }} />
          </div>
          );
        })}
      </div>
      <div className="relative mt-1 h-4">
        {bars.map((b, i) => i % step === 0 && (
          // 라벨은 버킷 시작점에 왼끝 정렬 -- 가운데 정렬은 첫/끝 라벨이 컨테이너
          // 밖으로 넘쳐 가로 스크롤(e2e L1)을 유발할 수 있다.
          <span key={i}
                className="absolute top-0 whitespace-nowrap text-[10px] tabular-nums text-muted"
                style={{ left: `${r2((i / data.length) * 100)}%` }}>{b.label}</span>
        ))}
      </div>
    </div>
  );
}
