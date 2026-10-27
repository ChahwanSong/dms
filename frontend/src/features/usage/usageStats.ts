// 사용량 분석의 순수 규칙(화면·CSV 내보내기 공용 한 벌, 2026-10-02 UsageAnalysis.tsx 에서 분리). hot 비율·
// 스택 비중·온도 색·나이 구간 라벨이 화면과 CSV 에서 갈라지면 같은 타깃이 두 숫자를 갖는다.
import type { HistogramBucket, UsagePoint } from "../../lib/types";

// NodesList/JobStats/RequestDetail 의 humanBytes 국소 사본 관례.
export function humanBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let v = bytes, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

// 포인트의 대표 시각(초). 리포트 생성 시각(스캔이 본 파일시스템의 시점)이 1순위,
// 결측(구형 리포트)이면 잡 완료 시각 -- 순서는 서버 정렬과 같은 근거다.
export function pointEpoch(p: UsagePoint): number | null {
  if (typeof p.generated_at_epoch === "number") return p.generated_at_epoch;
  if (p.finished_at) {
    const ms = Date.parse(p.finished_at);
    if (!Number.isNaN(ms)) return Math.floor(ms / 1000);
  }
  return null;
}

// hot 판정 나이 상한(일). 180 = 사용자 결정(2026-08-24, 7일에서 상향) -- dscan
// 나이 버킷 경계([91d,180d] 상한)와 일치해 버킷이 잘리지 않고 통째로 들어간다.
export const HOT_AGE_MAX_DAYS = 180;

// 읽기가 atime 에 반영되는 규칙(2026-10-28 d192 리뷰 portal-batch-27). 사용량 hot 비율 도움말·온도 캡션(사용량 TargetDetail·배치
// 항목)이 같은 문장을 쓴다. 리눅스 기본 relatime 은 읽을 때 「이전 atime 이 mtime·ctime 이하이거나 24시간보다 오래됐으면」
// 갱신한다 -- 오래 안 읽힌(cold) 파일은 읽히면 곧바로 최근 접근이 되고 같은 파일의 반복 읽기만 하루 단위로 접힌다(hot 창
// HOT_AGE_MAX_DAYS 에 비하면 무시할 만하다). 반영하지 않는 것은 noatime 마운트와 일부 파일시스템이다 -- 테스트베드 CephFS 는
// relatime 마운트인데도 노드에서 읽어도 atime 이 그대로였다(CHANGELOG d190, 같은 노드 ext4 는 갱신). 예전 문구는 relatime 을
// 「읽기가 atime 을 바꾸지 않는 곳」에 넣어, relatime 스토리지에서 open_noatime 을 꺼도 통계에 영향이 없다고 읽혔다.
export const ATIME_READ_RULE =
  "리눅스 기본 relatime 은 읽기를 파일마다 많아야 하루에 한 번 반영하고, noatime 마운트나 일부 CephFS 는 반영하지 않습니다";
// 온도(atime) 캡션의 근사 경고 -- 사용량 상세와 배치 항목이 같은 문장을 쓴다(축 안내 앞부분만 화면마다 다르다).
export const ATIME_APPROX_NOTE =
  `읽기가 atime 에 반영되는지는 마운트·스토리지에 따라 달라 근사치입니다(${ATIME_READ_RULE}) — 반영하지 않는 곳에선 `
  + "최근에 쓴 데이터가 hot 으로 보입니다";

// hot 비율: 나이 ≤HOT_AGE_MAX_DAYS 버킷 bytes / 전체 bytes. 전체 0 은 비율 정의
// 불가(null -- cumulativeLayout 의 0-나눗셈 규약). bytes 가 실린 버킷의 hot/cold 를
// 가를 근거가 없으면 null 이다 -- cold 로 접으면(분모에만 넣으면) 비율이 조용히
// 내려간다(모름 ≠ 0, 리뷰). 단 **하한이 180일을 넘는** 구간은 상한이 없어도 확실히 cold 다:
// dscan 의 마지막 구간 [3651d,INF] 는 max_age_days 가 없는데(실측), 예전엔 10년 넘은 파일이
// 1바이트만 있어도 hot 비율이 "—"가 됐다(2026-10-02 리뷰 -- 목록 열·CSV 로 번지기 전 상세 타일의 결함).
const knownCold = (b: HistogramBucket) =>
  typeof b.min_age_days === "number" && b.min_age_days > HOT_AGE_MAX_DAYS;
export function hotRatio(buckets: HistogramBucket[] | undefined): number | null {
  if (!buckets || buckets.length === 0) return null;
  let hot = 0, total = 0;
  for (const b of buckets) {
    if (typeof b.bytes !== "number") return null;
    if (b.bytes > 0 && typeof b.max_age_days !== "number" && !knownCold(b)) return null;
    total += b.bytes;
    if (typeof b.max_age_days === "number"
        && b.max_age_days <= HOT_AGE_MAX_DAYS) hot += b.bytes;
  }
  return total === 0 ? null : hot / total;
}

// 100% 스택 열: 버킷별 bytes 비중. 전체 0 이면 null(빈 트리의 온도는 정의 불가).
export function stackLayout(buckets: HistogramBucket[] | undefined) {
  if (!buckets || buckets.length === 0) return null;
  const vals = buckets.map((b) => (typeof b.bytes === "number" ? b.bytes : null));
  if (vals.some((v) => v === null)) return null;
  const total = (vals as number[]).reduce((a, v) => a + v, 0);
  if (total === 0) return null;
  return (vals as number[]).map((v) => ({ pct: (v / total) * 100, bytes: v }));
}

// BatchDetail TEMP_PALETTE/tempColorOf 국소 사본(hot→cold 비례 사상).
const TEMP_PALETTE = ["#dc2626", "#ea580c", "#f59e0b", "#eab308", "#84cc16",
                      "#22c55e", "#06b6d4", "#3b82f6", "#6366f1"];
export const tempColorOf = (n: number) => (i: number) =>
  TEMP_PALETTE[n <= 1 ? 0 : Math.round((i / (n - 1)) * (TEMP_PALETTE.length - 1))];

// 나이 구간의 사람 말(툴팁 범례, 2026-10-02 "빨강/초록/파랑이 뭔지"): dscan 버킷 [0d,1d]·[181d,365d]·
// [3651d,INF] -> "1일 이내"·"181일~1년"·"10년 이상". 365일 이상은 해 단위(반올림 -- dscan 경계 366·1095·
// 1096·3650·3651 이 1·3·3·10·10 년). 나이 필드가 없으면 원문 라벨, 그것도 없으면 "—"(지어내지 않는다).
export function ageLabel(b: HistogramBucket): string {
  const lo = b.min_age_days, hi = b.max_age_days;
  if (typeof lo !== "number") return b.bucket ?? "—";
  const yr = (d: number) => Math.round(d / 365);
  if (typeof hi !== "number") return lo >= 365 ? `${yr(lo)}년 이상` : `${lo}일 이상`;
  if (lo === 0) return hi >= 365 ? `${yr(hi)}년 이내` : `${hi}일 이내`;
  if (hi < 365) return `${lo}~${hi}일`;
  if (lo >= 365) return `${yr(lo)}~${yr(hi)}년`;
  return `${lo}일~${yr(hi)}년`;
}

// CSV 열 이름용 구간 키: "[0d,1d]" -> "0d-1d", "[0,4096]" -> "0-4096". 라벨이 없으면 나이·하한 필드로, 그것도
// 없으면 순번(열 이름이 비면 열이 합쳐진다).
export function bucketKey(b: HistogramBucket, index: number): string {
  if (typeof b.bucket === "string") {
    const k = b.bucket.replace(/[[\]()\s]/g, "").replace(/,/g, "-");
    if (k !== "") return k;
  }
  if (typeof b.min_age_days === "number")
    return `${b.min_age_days}d-${typeof b.max_age_days === "number" ? `${b.max_age_days}d` : "INF"}`;
  if (typeof b.lower_inclusive === "number")
    return `${b.lower_inclusive}-${typeof b.upper_inclusive === "number" ? b.upper_inclusive : "INF"}`;
  return `b${index}`;
}

// 축 이름의 뜻(툴팁 제목) -- dscan 의 세 타임스탬프.
export const AXIS_MEANING: Record<string, string> = {
  atime: "마지막 접근", mtime: "마지막 수정", ctime: "메타데이터 변경",
};
