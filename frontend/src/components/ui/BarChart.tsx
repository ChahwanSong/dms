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

const SPARSE_MAX = 8;        // 이하면 저밀도 모드(값 직표기 가능한 밀도)
const SPARSE_HEADROOM = 75;  // 저밀도 트랙 안 값 라벨 자리

export function BarChart({ data, label, emptyText = "집계된 잡 없음" }: {
  data: BarDatum[]; label?: string; emptyText?: string;
}) {
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
        {bars.map((b, i) => (
          <div key={i} title={`${b.label}: ${b.value}`}
               className="flex min-w-0 max-w-16 flex-1 flex-col items-center gap-1">
            {/* 트랙은 accent 의 연한 단(동일 계열) -- 값 0 버킷도 "빈 자리"가 아니라
                트랙+0 으로 보인다. 막대 상단만 둥글게(데이터 끝), 밑변은 직각. */}
            <div className="relative h-20 w-full max-w-6 overflow-hidden rounded-sm bg-accent/10">
              <span className="absolute inset-x-0 text-center text-[10px] font-medium tabular-nums text-muted"
                    style={{ bottom: `calc(${b.pct}% + 2px)` }}>{b.value}</span>
              <div className="absolute inset-x-0 bottom-0 rounded-t bg-accent"
                   style={{ height: `${b.pct}%` }} />
            </div>
            <span className="w-full truncate text-center text-[10px] text-muted">{b.label}</span>
          </div>
        ))}
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
      <p className="mb-0.5 text-right text-[10px] tabular-nums text-muted">최대 {max}</p>
      <div className="flex items-end gap-0.5">
        {bars.map((b, i) => (
          <div key={i} title={`${b.label}: ${b.value}`}
               className="relative h-20 min-w-0 flex-1 overflow-hidden rounded-sm bg-accent/10">
            <div className="absolute inset-x-0 bottom-0 rounded-t bg-accent"
                 style={{ height: `${b.pct}%` }} />
          </div>
        ))}
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
