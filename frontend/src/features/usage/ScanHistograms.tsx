import { BarChart, type BarDatum } from "../../components/ui/BarChart";
import type { HistogramBucket } from "../../lib/types";
import { ATIME_APPROX_NOTE, ageLabel, humanBytes, tempColorOf } from "./usageStats";

// scan 리포트 한 건의 온도·크기 히스토그램(2026-11-16 BatchDetail ItemScanStats 에서 이동 -- 사용량 분석 펼친 상세의 「최신
// scan」과 배치 scan 항목 펼침이 같은 모양을 쓴다: 한 번 익힌 차트가 두 화면에서 같은 뜻으로 읽히게). 가벼운 모듈로 둔다 --
// 배치 상세가 무거운 사용량 TargetDetail 을 import 하지 않게(UsageParts 와 같은 이유). 차트 몸통(BarChart + 캡션)만 갖고, 섹션
// 틀(제목·구분선)·축 전환 컨트롤은 호출측이 갖는다(배치는 PANEL_SECTION/h3 + Button 줄, 사용량은 SectionHead + Segmented).
//
// 값 누락(2026-11-16): 옛 toBars 는 라벨이나 값이 없는 버킷을 조용히 버렸다 -- 막대가 빠지고 누적 % 가 부분합 기준이라
// 부풀려진다(사용량 stackLayout·hotRatio 와 서버 _total_bytes 는 같은 경우를 「모름」으로 접는다). 이제 값(bytes·count)이 하나라도
// 빠졌거나 숫자가 아니면(음수·비유한 포함 -- 서버 투영은 유한 수만 넘기지만 DB 가 신뢰 경계다) 그 히스토그램 통째 「모름」이고
// 빈 히스토그램(집계된 버킷 없음 -- 0건)과 다른 문구로 말한다. 라벨이 없는 버킷(서버가 경로처럼 생긴 라벨을 지운 경우)은 막대를
// 버리지 않고 숫자 필드로 라벨을 다시 짓는다(온도 = ageLabel, 크기 = sizeBucketLabel). 정상 리포트의 모양은 그대로다.

export const TEMP_AXES = ["atime", "mtime", "ctime"] as const;

// 고를 수 있는 온도 축. 배치는 키가 있으면(빈 배열도 -- 「집계된 버킷 없음」을 보인다), 사용량은 버킷이 있는 축만(nonEmpty --
// 여러 포인트의 합집합이라 빈 축 버튼이 「분포 없음」만 보이게 하지 않는다).
export function tempAxes(hists: Record<string, HistogramBucket[] | undefined> | null | undefined,
                         { nonEmpty = false }: { nonEmpty?: boolean } = {}): string[] {
  return TEMP_AXES.filter((k) => {
    const b = hists?.[k];
    return nonEmpty ? Array.isArray(b) && b.length > 0 : b !== undefined;
  });
}

// 버킷 -> 막대. 값이 하나라도 빠지면 null(모름, 머리 주석) -- 0 으로 메우면 「빈 구간」(정상값 0)과 「모름」이 섞인다(null≠0).
export function histBars(buckets: HistogramBucket[] | null | undefined, key: "bytes" | "count",
                         labelOf: (b: HistogramBucket, index: number) => string): BarDatum[] | null {
  const list = buckets ?? [];
  const out: BarDatum[] = [];
  for (let i = 0; i < list.length; i++) {
    const v = list[i][key];
    if (typeof v !== "number" || !Number.isFinite(v) || v < 0) return null;
    out.push({ label: labelOf(list[i], i), value: v });
  }
  return out;
}

// 온도 막대 라벨: 리포트 구간 라벨 그대로(「[0d,1d]」 -- 배치 화면의 툴팁 계약), 없으면 나이 필드의 사람 말(ageLabel).
const tempLabel = (b: HistogramBucket) => (typeof b.bucket === "string" && b.bucket !== "" ? b.bucket : ageLabel(b));

// 크기 버킷 축 라벨용 초단축 표기(1024 단위). humanBytes 와 같은 단위 체계지만
// 소수·공백·"iB" 를 떼서 축 라벨 폭을 줄인다 -- 원본 라벨("[1073741824,4294967296]")
// 은 열 폭(max-w-16 = 4rem)에서 잘려 어느 구간인지 못 읽는다(사용자 지적). 축약이
// KiB 계열임은 차트 캡션이 말한다(K 를 1000 으로 읽는 오독 방지).
//
// 반올림 규칙: 한 자리 소수로 적되 그 자리가 "X.0" 이 될 값은 정수로 적는다.
// 실측(d63) dscan 버킷은 [직전 상한+1, 상한] 이라 하한이 늘 2의 거듭제곱+1 이다
// -- 그대로 한 자리로 적으면 "64.0K~1M" 처럼 의미 없는 .0 이 붙어 라벨만 넓어진다
// (1 바이트 차이를 소수 한 자리로는 어차피 못 보인다). 진짜 중간값(1.5K)은 그대로
// "1.5K" 로 남는다 -- 반올림 폭이 표기 해상도(0.05)를 넘지 않는 값만 접는다.
const LABEL_UNITS: [string, number][] = [
  ["T", 1024 ** 4], ["G", 1024 ** 3], ["M", 1024 ** 2], ["K", 1024],
];
export function shortBytes(bytes: number): string {
  for (const [unit, size] of LABEL_UNITS) {
    if (bytes >= size) {
      const v = bytes / size;
      const round = Math.round(v);
      return `${Math.abs(v - round) < 0.05 ? round : v.toFixed(1)}${unit}`;
    }
  }
  return String(Math.round(bytes));
}
// 하한·상한은 서버 투영이 넘기는 수치 필드가 1순위다(모양 검사를 통과한 숫자).
// 없으면 구간 라벨("[0,4096]")에서 읽고, 그마저 아니면 원본 라벨을 그대로 둔다 --
// 모르는 모양을 추측해 다시 쓰면 화면이 없는 사실을 지어낸다. 상한 없음(열린
// 마지막 구간 "[1G,)"·dscan 의 "[4398046511105,INF]")은 "1G~" -- 0(정상값)이나 임의 상한으로
// 뭉개지 않는다. 라벨도 숫자 필드도 없으면 「구간 N」. 숫자 필드는 API 응답(DB 가 신뢰 경계)이라 음수·비유한은 없는 것으로,
// 뒤집힌 경계(상한 < 하한 -- 변조·오염)는 경계 모름으로 접는다(2026-11-16 -- 사용량 목록 라벨 lib/sizeBuckets 의 규칙을 이 한
// 벌로 합칠 때 옮겼다: 「64K~4K」 같은 말을 만들지 않는다).
const RANGE_RE = /^[[(]\s*(\d+)\s*,\s*(\d*|INF)\s*[\])]$/;
const nonNeg = (x: unknown): number | null => (typeof x === "number" && Number.isFinite(x) && x >= 0 ? x : null);
export function sizeBucketLabel(b: HistogramBucket, index = 0): string {
  const raw = typeof b.bucket === "string" && b.bucket !== "" ? b.bucket : null;
  const m = raw !== null ? RANGE_RE.exec(raw) : null;
  const lower = nonNeg(b.lower_inclusive) ?? (m ? Number(m[1]) : null);
  const upper = nonNeg(b.upper_inclusive) ?? (m && /^\d+$/.test(m[2]) ? Number(m[2]) : null);
  if (lower === null || (upper !== null && upper < lower)) return raw ?? `구간 ${index + 1}`;
  return upper === null ? `${shortBytes(lower)}~`
                        : `${shortBytes(lower)}~${shortBytes(upper)}`;
}

// 시간축별 캡션: 색의 의미(왼쪽 빨강=hot, 오른쪽 파랑=cold)를 축마다 그 축의
// 언어로 말한다. atime 만 근사 경고 -- mtime/ctime 은 마운트 옵션과 무관해 경고가 거짓이 된다.
// atime 경고(2026-10-27 d192, 괴리 [C3]): 읽기가 atime 에 반영되는지는 마운트·스토리지에 달렸다(noatime 은 반영 안 함,
// 테스트베드 CephFS 는 relatime 인데도 노드에서 읽어도 atime 이 바뀌지 않았다 -- CHANGELOG d190). 그런 곳에선 「hot」이 최근에
// 읽힌 것이 아니라 최근에 쓰인 것에 가깝다. relatime 자체는 읽기를 하루 단위로 반영한다(2026-10-28 리뷰 portal-batch-27 --
// 예전 문구는 relatime 을 반영 안 하는 쪽에 넣었다). 문장은 사용량 TargetDetail 과 같은 한 출처(usageStats ATIME_APPROX_NOTE).
// 사용량 상세는 이 문장을 제목 옆 설명(HelpTip)에 두고 본문엔 싣지 않는다(caption="none" -- 긴 캡션을 본문에 깔지 않는 그 화면의
// 2026-10-20 정리), 배치 항목은 본문에 늘 보인다.
export const HIST_TEMP_CAPTIONS: Record<string, string> = {
  atime: `왼쪽(빨강)=hot·최근 접근, 오른쪽(파랑)=cold — atime 기준 용량 비중. ${ATIME_APPROX_NOTE}`,
  mtime: "왼쪽(빨강)=최근 수정, 오른쪽(파랑)=오래됨 — mtime 기준 용량 비중",
  ctime: "왼쪽(빨강)=최근 변경, 오른쪽(파랑)=오래됨 — ctime 기준 용량 비중",
};

const TEMP_UNKNOWN = "이 축의 온도 분포를 알 수 없습니다(리포트 값 누락)";
const SIZE_UNKNOWN = "크기 분포를 알 수 없습니다(리포트 값 누락)";

// 온도 히스토그램: 값 = 구간별 용량(bytes), 막대 색 = hot→cold 그라디언트(막대 수와 무관하게 첫 막대 빨강·끝 막대 파랑 --
// usageStats.tempColorOf 비례 사상), 누적선 = hot 쪽부터의 용량 비중. 막대 위 용량 글자는 빼고 누적 % 만(2026-10-02 사용자 요청 --
// 용량 글자가 길면 좁은 열에서 잘리거나 안 보였다). 버킷별 용량·비중은 막대에 마우스를 올리면 보인다(BarChart valueLabels=false
// 툴팁). 누적 캡션은 오버레이와 같은 조건(총합>0)으로만 -- 총합 0 이면 선이 없는데 "선 = ..." 은 거짓 캡션이 된다. 총 용량
// 값(막대 합)도 여기서 함께 말한다(선의 100% 가 몇 바이트인지).
export function TempHistogram({ buckets, axis, emptyText = "집계된 버킷 없음", caption = "inline" }: {
  buckets: HistogramBucket[] | null | undefined; axis: string; emptyText?: string;
  caption?: "inline" | "none";
}) {
  const bars = histBars(buckets, "bytes", tempLabel);
  if (bars === null) return <p className="text-muted text-xs">{TEMP_UNKNOWN}</p>;
  const total = bars.reduce((acc, b) => acc + b.value, 0);
  return (<>
    <BarChart data={bars}
              label={`데이터 온도(${axis}) 히스토그램`}
              formatValue={humanBytes}
              colorOf={tempColorOf(bars.length)}
              cumulative={{ format: humanBytes }}
              valueLabels={false}
              emptyText={emptyText} />
    {caption === "inline" && HIST_TEMP_CAPTIONS[axis] && (
      <p className="text-muted text-xs mt-1">{HIST_TEMP_CAPTIONS[axis]}</p>
    )}
    {total > 0 && (
      <p className="text-muted text-xs mt-1">
        {`선 = hot쪽부터의 누적 용량 비중 · 총 ${humanBytes(total)} · 막대에 마우스를 올리면 구간별 용량`}
      </p>
    )}
  </>);
}

// 크기 분포: 온도 차트와 같은 문법(누적 오버레이·툴팁 값)을 쓰되 **색은 안 물려받는다**: 크기는 hot/cold 축이 아니라 온도
// 그라디언트를 입히면 "작은 파일=hot"이라는 거짓 의미가 생긴다. 크기 자체를 색으로 다시 말할 이유도 없다 — 양은 막대 높이가,
// 순서는 x축이 이미 말한다. 그래서 단색 accent(BarChart 기본)다. 누적은 용량이 아니라 **개수** 비중이라 format 도 개수 표기다.
// 총 개수는 호출측 제목이 말한다(sizeHistogramTotal -- 2026-10-02 사용자 요청: 회색 캡션에 묻혀 있었다).
export function SizeHistogram({ buckets, emptyText = "집계된 버킷 없음" }: {
  buckets: HistogramBucket[] | null | undefined; emptyText?: string;
}) {
  const bars = histBars(buckets, "count", sizeBucketLabel);
  if (bars === null) return <p className="text-muted text-xs">{SIZE_UNKNOWN}</p>;
  const total = bars.reduce((acc, b) => acc + b.value, 0);
  return (<>
    <BarChart data={bars} label="파일 크기 분포"
              cumulative={{ format: (n) => `${n.toLocaleString("ko-KR")}개` }}
              valueLabels={false}
              emptyText={emptyText} />
    {bars.length > 0 && (
      <p className="text-muted text-xs mt-1">가로축 = 파일 크기 구간(K=KiB·M=MiB·G=GiB·T=TiB, 1024 단위)</p>
    )}
    {total > 0 && (
      <p className="text-muted text-xs mt-1">
        선 = 작은 파일부터의 누적 개수 비중 · 막대에 마우스를 올리면 구간별 개수
      </p>
    )}
  </>);
}

// 크기 분포의 총 파일 수(제목용). null = 모름 -- 분포가 없거나(빈 배열·부재: 「기록 없음」이지 0개가 아니다) 개수가 빠진 구간이
// 있다(부분합을 총계라 하지 않는다). 0 은 정상값(빈 트리).
export function sizeHistogramTotal(buckets: HistogramBucket[] | null | undefined): number | null {
  if (!buckets || buckets.length === 0) return null;
  const bars = histBars(buckets, "count", () => "");
  return bars === null ? null : bars.reduce((acc, b) => acc + b.value, 0);
}
