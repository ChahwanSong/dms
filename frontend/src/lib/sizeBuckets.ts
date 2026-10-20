// 파일 크기 구간의 사람 말 + 분포 줄(2026-10-21 사용자 요청 「사용량 분석 화면에 최근 스캔 기준 파일의 사이즈별 정보」). 사용량
// 분석의 펼친 상세(대상 하나)와 스토리지 합계(최상위 대상의 합)가 같은 규칙으로 읽는다 -- 라벨이 두 곳에서 갈라지면 같은 구간이
// 두 이름을 갖는다. 배치 상세의 세로 차트 축약 라벨(`4K~64K` -- 축 폭 때문)은 맥락이 달라 그쪽 국소 규칙 그대로다.
//
// dscan 구간은 `[직전 상한+1, 상한]`(10구간, 마지막은 상한 없음 `[4398046511105,INF]` -- 실측) 이고 개수(count)만 싣는다(구간별
// 용량 bytes 는 dscan 이 내지 않는다 -- 실리면 분포가 용량 열을 자동으로 붙인다). 하한-1 이 1024 거듭제곱 단위의 정수배면 그것을
// 「초과」 경계로 읽어 「4–64 KiB」처럼 짧게 적는다(= 4 KiB 초과 64 KiB 이하). 경계의 원천은 숫자 필드 > 라벨 파싱 > 원 라벨 >
// 「구간 N」 -- 모르는 경계를 지어내지 않는다. 숫자는 API 응답(DB 가 신뢰 경계 -- 투영을 거쳤어도 모양을 다시 본다)이라 음수·
// 비유한·뒤집힌 경계는 모름으로 접는다.
import type { HistogramBucket } from "./types";

// 비중 % 표기(2026-10-21 TargetDetail 에서 이동 -- 크기 분포·온도 툴팁 공용). 0 은 「0%」(정상값), 0.1 미만은 「<0.1%」(0 이 아닌
// 극소값을 0% 로 반올림하면 「없다」는 거짓), 10 미만은 소수 한 자리, 그 위는 정수.
export const pctText = (p: number) =>
  p === 0 ? "0%" : p < 0.1 ? "<0.1%" : p < 10 ? `${p.toFixed(1)}%` : `${Math.round(p)}%`;

const UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
interface Unit { v: number; u: string }

// n 이 단위의 정수배면 그 단위(64 KiB), 1024 이상인데 정수배가 아니면 null(근사로 간다). 1024 미만은 바이트 그대로.
function exactUnit(n: number): Unit | null {
  let v = n, i = 0;
  while (v >= 1024 && v % 1024 === 0 && i < UNITS.length - 1) { v /= 1024; i += 1; }
  return i === 0 && n >= 1024 ? null : { v, u: UNITS[i] };
}
function approxUnit(n: number): Unit {
  let v = n, i = 0;
  while (v >= 1024 && i < UNITS.length - 1) { v /= 1024; i += 1; }
  return { v: i === 0 ? Math.round(v) : Number(v.toFixed(1)), u: UNITS[i] };
}
const unitText = (x: Unit) => `${x.v.toLocaleString("ko-KR")} ${x.u}`;
const nonNeg = (x: unknown): number | null =>
  typeof x === "number" && Number.isFinite(x) && x >= 0 ? x : null;

// 라벨 `[a,b]`·`[a,INF]`(BatchDetail RANGE_RE 와 같은 생각, INF·빈 상한 허용).
const RANGE_RE = /^[[(]\s*(\d+)\s*,\s*(\d*|INF)\s*[\])]$/;

function sizeBounds(b: HistogramBucket): { lo: number | null; hi: number | null } {
  const m = typeof b.bucket === "string" ? RANGE_RE.exec(b.bucket) : null;
  const lo = nonNeg(b.lower_inclusive) ?? (m ? Number(m[1]) : null);
  const hi = nonNeg(b.upper_inclusive) ?? (m && /^\d+$/.test(m[2]) ? Number(m[2]) : null);
  // 뒤집힌 경계(변조·오염)는 경계 모름 -- 「64 KiB–4 KiB」 같은 말을 만들지 않는다.
  if (lo !== null && hi !== null && hi < lo) return { lo: null, hi: null };
  return { lo, hi };
}

export function sizeRangeLabel(b: HistogramBucket, index: number): string {
  const { lo, hi } = sizeBounds(b);
  if (lo === null) return typeof b.bucket === "string" && b.bucket !== "" ? b.bucket : `구간 ${index + 1}`;
  const edge = lo - 1 >= 1024 ? exactUnit(lo - 1) : null;          // 「초과」 경계
  if (hi === null) return edge ? `${unitText(edge)} 초과` : `${unitText(exactUnit(lo) ?? approxUnit(lo))} 이상`;
  const top = exactUnit(hi) ?? approxUnit(hi);
  if (lo === 0) return `${unitText(top)} 이하`;
  const bot = edge ?? exactUnit(lo) ?? approxUnit(lo);
  return bot.u === top.u ? `${bot.v.toLocaleString("ko-KR")}–${unitText(top)}` : `${unitText(bot)}–${unitText(top)}`;
}

// 줄의 title(정확한 바이트 경계). 경계를 모르면 없음(지어내지 않는다).
export function sizeRangeTitle(b: HistogramBucket): string | undefined {
  const { lo, hi } = sizeBounds(b);
  if (lo === null) return undefined;
  const n = (x: number) => `${x.toLocaleString("ko-KR")} B`;
  return hi === null ? `${n(lo)} 이상` : `${n(lo)} ~ ${n(hi)}${lo === 0 ? "(빈 파일 포함)" : ""}`;
}

// 구간 용량 표기(용량 열 -- dscan 이 구간별 bytes 를 낼 때만 보인다). usageStats.humanBytes 와 같은 규칙의 국소 사본
// (NodesList/JobStats 관례 -- lib 가 features 를 import 하지 않게).
export function sizeBytesText(bytes: number): string {
  const x = approxUnit(bytes);
  return `${x.u === "B" ? x.v : x.v.toFixed(1)} ${x.u}`;
}

export interface SizeRow {
  label: string; title?: string;
  count: number | null;            // null = 그 구간 개수 모름(결측·음수·비정수)
  bytes: number | null;
  pct: number | null;              // 전체 대비 비중(%). 분모를 모르거나 0 이면 null
}
export interface SizeDist {
  rows: SizeRow[];
  total: number | null;            // 개수 합. 한 구간이라도 모르면 null(분모 모름 -- 비중 전부 null)
  hasBytes: boolean;               // 모든 구간에 용량이 있다(CSV usageCsv.sizeHasBytes 와 같은 판정)
}

// 히스토그램 -> 분포 줄. 부재·빈 배열은 null(구형 리포트·못 읽음 -- 0 막대 10줄로 그리면 「파일이 없다」는 거짓). 합 0 은
// total 0(빈 트리 -- 정상값, 비중은 정의 불가라 null). 0개 구간도 줄로 남긴다(「큰 파일 없음」도 정보).
export function sizeDistribution(buckets: HistogramBucket[] | null | undefined): SizeDist | null {
  if (!Array.isArray(buckets) || buckets.length === 0) return null;
  const counts = buckets.map((b) => {
    const c = nonNeg(b.count);
    return c !== null && Number.isInteger(c) ? c : null;
  });
  const total = counts.some((c) => c === null) ? null : (counts as number[]).reduce((a, c) => a + c, 0);
  return {
    total,
    hasBytes: buckets.every((b) => nonNeg(b.bytes) !== null),
    rows: buckets.map((b, i) => {
      const c = counts[i];
      return {
        label: sizeRangeLabel(b, i), title: sizeRangeTitle(b), count: c, bytes: nonNeg(b.bytes),
        pct: total !== null && total > 0 && c !== null ? (c / total) * 100 : null,
      };
    }),
  };
}
