import { Fragment } from "react";

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

// --- 누적 데이터량(cumulative density) 오버레이: 산식·라벨 기하(순수 함수) ---

// 왼쪽(hot)부터의 running sum 과 총합 대비 비중. 마지막 점 = 총합(100%).
// 총합 0 이면 null -- 0 은 정상값이지만 비중(0/0)은 정의 불가라, 0% 나 거짓
// 100% 선을 그리는 대신 오버레이 자체를 생략한다(null ≠ 0 규약의 오버레이 판).
export interface CumulativePoint { sum: number; frac: number }
export function cumulativeLayout(data: BarDatum[]): CumulativePoint[] | null {
  const total = data.reduce((acc, d) => acc + d.value, 0);
  if (total === 0) return null;
  let run = 0;
  return data.map((d) => { run += d.value; return { sum: run, frac: run / total }; });
}

// 누적 % 라벨의 세로 배치(트랙 하단 기준 %). 막대 값 라벨과 같은 열·같은 중앙
// 정렬이라 세로 충돌만 판정하면 된다: 기본은 점 위, 막대 라벨(막대 위 2px 부터
// 텍스트 높이 밴드)과 겹치면 점 아래, 아래가 트랙 밖이거나 그마저 겹치면(막대
// 라벨이 점 바로 아래) 막대 라벨 위로 올린다. 상수는 트랙 h-20=80px 실측:
// text-[10px] 한 줄 ≈ 13%, 점-라벨 간격 ≈ 3px ≈ 4%, 막대 라벨 들림 2px ≈ 2.5%.
const CUM_LABEL_H = 13;
const CUM_LABEL_PAD = 4;
const BAR_LABEL_LIFT = 2.5;
export function cumulativeLabelBottom(cumPct: number, barPct: number): number {
  const barBottom = barPct + BAR_LABEL_LIFT;
  const clash = (bottom: number) =>
    bottom < barBottom + CUM_LABEL_H && barBottom < bottom + CUM_LABEL_H;
  const above = cumPct + CUM_LABEL_PAD;
  if (!clash(above)) return above;
  const below = cumPct - CUM_LABEL_PAD - CUM_LABEL_H;
  if (below >= 0 && !clash(below)) return below;
  return barBottom + CUM_LABEL_H + 2;
}

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
                           colorOf, cumulative }: {
  data: BarDatum[]; label?: string; emptyText?: string;
  // 값의 사람 표기(bytes 등). 기본은 숫자 그대로 -- 기존 소비자(잡 통계) 무영향
  // 인 하위호환 옵션이다.
  formatValue?: (n: number) => string;
  // 막대별 의미 색(#rrggbb) -- 온도 그라디언트 등. 미지정이면 기존 accent 클래스
  // 그대로(잡 통계 무영향인 하위호환 옵션). 지정 시 막대는 그 색, 트랙은 같은
  // 색의 저채도 판(hexTint)으로 통일한다.
  colorOf?: (index: number, value: number) => string;
  // 누적 데이터량 오버레이(꺾은선+점+% 라벨) -- 저밀도 전용 하위호환 옵션(온도
  // 히스토그램 ≤9버킷 소비자용, 고밀도는 점·라벨이 겹쳐 못 읽으니 범위 밖).
  // format 은 툴팁의 누적값 사람 표기. 미지정 시 기존 렌더 완전 불변.
  cumulative?: { format: (n: number) => string };
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
    const cum = cumulative ? cumulativeLayout(data) : null;
    const n = bars.length;
    // 점 x = 버킷 중심 근사((i+0.5)/n). gap-1.5 는 고정 px 라 % 사상과 최대 반
    // gap(~3px) 어긋나지만 실측 대신 Sparkline 의 viewBox 정규화 선례를 따른다.
    const cx = (i: number) => r2(((i + 0.5) / n) * 100);
    // 점 y 스케일은 막대와 같은 75 헤드룸(SPARSE_HEADROOM) -- 100% 점 위에도
    // 라벨 자리가 남고, 막대 라벨과의 충돌 판정이 같은 좌표계에서 된다.
    const cumPct = (frac: number) => r2(frac * SPARSE_HEADROOM);
    // 트랙(h-20=5rem) 안 bottom% -> 루트 기준 top 오프셋(rem). 오버레이는 열
    // 밖(루트) absolute 라 트랙 % 를 직접 못 쓴다 -- 루트 아래엔 버킷 라벨
    // 행이 더 있어 % 가 트랙과 안 맞는다.
    const topRem = (bottomPct: number) => r2((100 - bottomPct) * 0.05);
    return (
      // 열 폭 상한(max-w-16)이 저밀도의 핵심 -- 버킷 1~2개가 컨테이너를 채우며
      // 괴물 블록이 되는 것을 막고, 남는 폭은 오른쪽 여백으로 둔다.
      // 누적 오버레이 시엔 루트 폭도 열 상한 합(n×4rem + gap 0.375rem)으로
      // 멈춘다 -- 열은 max-w-16 에서 멈추는데 루트가 더 넓으면 % 좌표 오버레이가
      // 빈 오른쪽까지 늘어나 점이 막대를 벗어난다. 첫·끝 % 라벨(중앙 정렬,
      // "100%" 반폭 ≈ 13px)도 반 열(≥16px) 안이라 컨테이너 넘침·가로 스크롤이
      // 없다(e2e L1).
      <div role="img" aria-label={label}
           className={cum ? "relative flex items-start gap-1.5"
                          : "flex items-start gap-1.5"}
           // 4·0.375 는 2진 정확값이라 r2 불요(반올림하면 오히려 8.375→8.38 왜곡)
           style={cum ? { maxWidth: `${n * 4 + (n - 1) * 0.375}rem` } : undefined}>
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
        {cum && (
          <>
            {/* 선·점은 currentColor(부모 text-ink 상속) -- 온도 그라디언트와
                구분되는 중립 톤, 색은 부모가 정하는 Sparkline 관례. SVG 는 보조
                그래픽이라 aria-hidden(차트 aria-label 계약은 루트 role img 가
                유지)·pointer-events 없음(열 툴팁을 가리지 않는다). */}
            <svg aria-hidden="true" viewBox="0 0 100 100" preserveAspectRatio="none"
                 className="pointer-events-none absolute inset-x-0 top-0 h-20 w-full">
              <polyline fill="none" stroke="currentColor" strokeWidth={1.5}
                        opacity={0.55} vectorEffect="non-scaling-stroke"
                        points={cum.map((p, i) =>
                          `${cx(i)},${r2(100 - cumPct(p.frac))}`).join(" ")} />
            </svg>
            {cum.map((p, i) => {
              const pct = Math.round(p.frac * 100);
              return (
                <Fragment key={i}>
                  <span aria-hidden="true"
                        className="pointer-events-none absolute h-1.5 w-1.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-current"
                        style={{ left: `${cx(i)}%`,
                                 top: `${topRem(cumPct(p.frac))}rem` }} />
                  {/* 라벨은 SVG 밖 HTML -- preserveAspectRatio="none" 아래의 SVG
                      텍스트는 가로로 왜곡된다(Sparkline 캡션 선례). 세로 위치는
                      막대 값 라벨과의 충돌 실측(cumulativeLabelBottom). */}
                  <span title={`누적 ${cumulative!.format(p.sum)} (${pct}%)`}
                        className="absolute -translate-x-1/2 -translate-y-full whitespace-nowrap text-[10px] font-medium tabular-nums"
                        style={{ left: `${cx(i)}%`,
                                 top: `${topRem(cumulativeLabelBottom(cumPct(p.frac), bars[i].pct))}rem` }}>
                    {pct}%
                  </span>
                </Fragment>
              );
            })}
          </>
        )}
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
