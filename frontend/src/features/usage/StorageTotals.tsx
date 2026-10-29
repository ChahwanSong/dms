import { useId } from "react";
import { HelpTip } from "../../components/ui/HelpTip";
import { SizeDistribution } from "../../components/ui/SizeDistribution";
import type { UsageAttrFilter, UsageStorageFilesystem, UsageStorageTotals, UsageTotalMetric } from "../../lib/types";
import { agoText, ageDays, kstStampOrDash } from "../../lib/datetime";
import { pctText } from "../../lib/sizeBuckets";
import { INVALID_NAMES_HELP, INVALID_NAMES_LABEL } from "../../lib/scanCounters";
import { hasAttr, useStorageTotals } from "./useUsage";
import { attrSummary } from "./UsageFilters";
import { HOT_AGE_MAX_DAYS, humanBytes } from "./usageStats";
import { SIZE_HELP, SectionHead, Stat } from "./UsageParts";
import { staleClass } from "./TargetTable";

// 스토리지 합계(2026-10-21 사용자 요청 「스토리지별 사용량 및 정보들의 총합」).
// 정의는 서버(routes_usage.storage_totals): **최상위 대상**(다른 대상 아래가 아닌 것)마다 최근 성공 scan 값의 합 -- 하위 대상은
// 상위 scan 에 이미 들어 있어 더하지 않는다(목록 「파일 수」 열을 그냥 더하면 team ⊃ team/sub 가 이중으로 들어간다). 화면은 그
// 정의와 뺀 것(하위·형식 오류·모름·읽는 중·창)과 scan 시점 범위를 각주 한 줄로 늘 말한다 -- 합계는 한 시점의 스냅숏이 아니다.
// 경로 검색과 무관하다(스토리지 전체 -- 검색어를 요청에 싣지 않는다).
//
// 2026-10-29 배치(사용자 결정 「권장사항대로」 -- 합계 두 벌을 한 줄로): 표 바로 위의 합계 줄(TotalsLine)이 숫자·df·인코딩 경고·
// 「이상」·읽는 중·실패와 다시 시도를 말하고, 이 블록(StorageTotals)은 그 줄의 [자세히] 안에서만 그린다 -- 제목·불러오는 중·실패는
// 줄이 이미 말하므로 여기엔 없다(값이 왔을 때만 그린다). 같은 쿼리 키라 요청이 늘지 않는다.
//
// 모양은 경로 상세의 「최신 scan」 블록과 같은 문법(수치 dl | 파일 크기 분포, lg 에서 나란히) -- 타일·그림자 없이(카드 안의 카드
// 금지, 2026-10-20 정리). null ≠ 0: 값을 모르면 「—」, 모름을 뺀 합은 하한이라 값 뒤에 흐린 「이상」. 대상 0 이면 블록이 없다
// (「0 B」는 「모름」을 0 으로 그리는 거짓 -- 빈 상태 안내가 이미 말한다).
//
// 파일시스템(df)은 에이전트가 노드에서 잰 statvfs -- scan 하지 않은 경로·다른 스토리지·예약 블록이 섞여 scan 합계와 기준이 다르다.
// 비율로 엮지 않고 나란히만 보인다. 여러 스토리지가 한 파일시스템일 수 있어 스토리지끼리 더하지 않는다(서버 계약).
//
// 필터 합계(2026-10-29, attr 이 있을 때): 같은 정의를 **필터에 맞는 경로 집합** 위에서 한다. 맨 위에 적용 필터 한 줄, df 는 없다
// (부분 집합에 파일시스템 사용량은 뜻이 없다). 경로 검색은 반영하지 않는다(합계는 경로 검색과 무관 -- 2026-10-21 규칙). 최상위
// 판정도 걸러진 집합 안에서라, 필터 밖 상위 경로가 있으면 그 하위가 최상위로 센다.

export const TOTALS_HELP = "각 최상위 대상의 최근 성공 scan 값을 더합니다. 다른 대상 아래에 있는 대상(예: team 아래 team/sub1)은 "
  + "상위 대상의 scan 에 이미 들어 있어 더하지 않습니다. 대상마다 scan 시점이 달라 한 시점의 스냅숏은 아닙니다. 경로 검색과 "
  + "무관하게 스토리지 전체이고, 값을 모르는 대상이 있으면 아는 대상만 더해 값 뒤에 「이상」을 붙입니다.";
export const FILTER_TOTALS_HELP = "경로 검색은 반영하지 않습니다. 필터에 맞는 경로 중 최상위만 더합니다 — 필터 밖 상위 경로가 "
  + "있으면 그 하위가 최상위로 셉니다. 대상마다 scan 시점이 달라 한 시점의 스냅숏은 아니고, 값을 모르는 대상이 있으면 아는 대상만 "
  + "더해 값 뒤에 「이상」을 붙입니다.";
export const DF_HELP = "에이전트가 이 스토리지가 마운트된 노드에서 잰 파일시스템 사용량(df)입니다. scan 하지 않은 경로·다른 "
  + "스토리지의 데이터·예약 블록이 섞여 scan 합계와 기준이 다릅니다. 여러 스토리지가 한 파일시스템을 쓰면 같은 값이 보입니다.";

// 응답 값 읽기(DB 가 신뢰 경계 -- 구형·부분 응답이어도 화면이 죽지 않게 모양을 다시 본다).
export const count = (x: unknown): number => (typeof x === "number" && Number.isFinite(x) && x > 0 ? x : 0);
export const sumOf = (m: UsageTotalMetric | null | undefined): number | null =>
  typeof m?.sum === "number" && Number.isFinite(m.sum) ? m.sum : null;
export const lowerBound = (m: { unknown?: number } | null | undefined, sum: number | null) =>
  sum !== null && count(m?.unknown) > 0;
// 부분 합(값 뒤 「이상」): 값 모름을 뺀 합이거나 대상이 창(window_limit)을 넘어 최근 스캔된 것만 더한 합. 스토리지별 비교·합계 줄·
// [자세히] 블록이 같은 규칙이다(2026-10-29 -- 예전 패널 블록은 모름만 봤고 창 잘림은 각주로만 말했다).
export const partialSum = (t: UsageStorageTotals, m: { unknown?: number } | null | undefined, sum: number | null) =>
  sum !== null && (t.truncated === true || lowerBound(m, sum));
export const countText = (n: number | null) => (n === null ? "—" : `${n.toLocaleString("ko-KR")}개`);
export const hotText = (t: UsageStorageTotals) => {
  const v = t.hot_ratio?.value;
  return typeof v === "number" && Number.isFinite(v) ? `${Math.round(v * 100)}%` : "—";
};
// 읽는 중 = 서버가 이번 호출에서 진전했고 남은 것이 있다(useStorageTotals 가 1초 뒤 다시 부른다). 응답 값만 보는 판정이다 --
// 화면은 여기에 「재조회가 실패하지 않았다」를 더한다(StorageTotals 의 reading): 재조회가 실패하면 폴링이 멈추는데(useStorageTotals
// -- 오류 중엔 멈춘다) 캐시에 남은 응답은 여전히 읽는 중이라 말해, 「요약 읽는 중 240개」가 아무것도 읽지 않는 동안 남았다(리뷰).
export const isReading = (t: UsageStorageTotals) => count(t.pending_count) > 0 && t.progressed === true;

// 「값 모름」 대상 수: 지표마다의 unknown 중 가장 큰 것. 읽는 중이면 아직 안 읽은 대상(pending -- 리포트 값 모름으로 세어 있다)을
// 따로 「요약 읽는 중」으로 말하므로 뺀다. 진전이 멈추면(재조회 실패 포함) 그것도 모름이다.
function unknownTargets(t: UsageStorageTotals, reading: boolean): number {
  const max = Math.max(...[t.total_bytes, t.total_files, t.total_directories, t.hot_ratio, t.file_size_histogram]
    .map((m) => count(m?.unknown)));
  return reading ? Math.max(0, max - count(t.pending_count)) : max;
}

const SEP = "\u00a0· ";

// 각주 한 줄(해당하는 조각만). 조각은 줄바꿈 없는 공백 + 「· 」로 잇는다 -- 접힌 줄이 「·」로 시작하지 않는다(StoragePanel 관례).
function Footnote({ t, now, reading }: { t: UsageStorageTotals; now: number; reading: boolean }) {
  const n = (x: number) => x.toLocaleString("ko-KR");
  const unknown = unknownTargets(t, reading);
  const parts: React.ReactNode[] = [
    `최상위 대상 ${n(count(t.summed_target_count))}개 기준`,
    count(t.nested_target_count) > 0 && `하위 대상 ${n(count(t.nested_target_count))}개 제외`,
    count(t.invalid_target_count) > 0 && `형식이 잘못된 대상 ${n(count(t.invalid_target_count))}개 제외`,
    unknown > 0 && `값 모름 ${n(unknown)}개`,
    reading && `요약 읽는 중 ${n(count(t.pending_count))}개`,
    t.truncated === true && `최근 스캔된 ${n(count(t.window_limit))}개 대상 기준`,
  ];
  // 잘못된 인코딩 이름(2026-10-26, 사용자 결정 「카운터만 둠」 -- 파손 경로와 따로 센다): 합이 1개 이상일 때만 주의색 한 조각.
  // 구형 리포트 대상은 모름이라 아는 대상만 더한 값이면 「이상」(다른 지표와 같은 하한 표기). 0·모름은 말하지 않는다(각주가
  // 늘 길어진다 -- 대상별 값은 CSV 의 invalid_name_entries 열).
  const invalid = sumOf(t.invalid_name_entries);
  if (invalid !== null && invalid > 0) {
    parts.push(<span className="text-attn" title={INVALID_NAMES_HELP}>
      {`${INVALID_NAMES_LABEL} ${n(invalid)}개${lowerBound(t.invalid_name_entries, invalid) ? " 이상" : ""}`}</span>);
  }
  // scan 시점 범위: 오래된 쪽 글자색은 표의 경과 색(30일 주의 · 90일 빨강)과 같다. 같은 날이면 하나만.
  const when = (iso: string) => (
    <span className={staleClass(ageDays(iso, now))} title={kstStampOrDash(iso)}>{agoText(iso, now)}</span>);
  const oldest = t.oldest_scan_at ?? null, newest = t.newest_scan_at ?? null;
  if (oldest !== null && newest !== null) {
    parts.push(agoText(oldest, now) === agoText(newest, now)
      ? <>scan 시점 {when(newest)}</>
      : <>scan 시점 <span className="whitespace-nowrap">{when(oldest)} ~ {when(newest)}</span></>);
  }
  const shown = parts.filter((p) => p !== false && p !== null);
  return (
    <p className="text-xs text-muted break-keep">
      {shown.map((p, i) => <span key={i}>{i > 0 && SEP}{p}</span>)}
    </p>
  );
}

// df 블록의 모양 검사(에이전트 보고가 원천 -- 서버가 걸렀어도 다시 본다): 정수·0 < total·used ≤ total 이 아니면 없는 것으로.
export function fsShape(fs: UsageStorageFilesystem | null | undefined): UsageStorageFilesystem | null {
  if (!fs || typeof fs !== "object") return null;
  const ok = (x: unknown) => typeof x === "number" && Number.isFinite(x) && x >= 0;
  return ok(fs.total_bytes) && ok(fs.used_bytes) && fs.total_bytes > 0 && fs.used_bytes <= fs.total_bytes ? fs : null;
}

// 파일시스템(df) 사용: used / total + 얇은 막대. 90% 이상이면 주의색(용량 경고 -- 글자 %가 같은 뜻을 말한다).
export function FilesystemUse({ fs, now, compact = false }: { fs: UsageStorageFilesystem; now: number; compact?: boolean }) {
  const pct = fs.total_bytes > 0 ? (fs.used_bytes / fs.total_bytes) * 100 : null;
  const exact = `${fs.used_bytes.toLocaleString("ko-KR")} B / ${fs.total_bytes.toLocaleString("ko-KR")} B · ${fs.node_name} 보고`
    + (fs.consistent === false ? " · 노드마다 다름" : "") + (fs.fresh === false ? ` · 보고 ${agoText(fs.reported_at, now)}` : "");
  const fill = pct !== null && pct >= 90 ? "bg-attn" : "bg-ink/40";
  const width = pct === null ? 0 : Math.min(100, Math.max(pct, pct > 0 ? 1 : 0));
  if (compact) {
    // 비교 표의 칸: 한 줄(사용 / 전체 · 짧은 막대 · %) -- 행 높이가 칸마다 들쭉날쭉하지 않게. 노드 불일치·오래된 보고는 「*」 +
    // 낭독 문장(표 각주가 「*」를 말한다), 정확한 값은 title. sr-only 는 relative 칸 안에 가둔다(가로 스크롤 표 밖으로 새지 않게).
    const caveat = [fs.consistent === false && "노드마다 다름", fs.fresh === false && `보고 ${agoText(fs.reported_at, now)}`]
      .filter(Boolean).join(", ");
    return (
      <span className="relative inline-flex items-center gap-2 whitespace-nowrap tabular-nums" title={exact}>
        <span><span className="text-ink">{humanBytes(fs.used_bytes)}</span>
          <span className="text-muted">{` / ${humanBytes(fs.total_bytes)}`}</span></span>
        {pct !== null && (<>
          <span aria-hidden className="h-1.5 w-12 overflow-hidden rounded-full bg-line/70">
            <span className={`block h-full rounded-full ${fill}`} style={{ width: `${width}%` }} /></span>
          <span className={`w-10 text-right text-xs ${pct >= 90 ? "font-medium text-attn" : "text-muted"}`}>{pctText(pct)}</span>
        </>)}
        {caveat !== "" && (<span className="text-xs text-attn"><span aria-hidden>*</span>
          <span className="sr-only">{`(${caveat})`}</span></span>)}
      </span>
    );
  }
  return (
    <div className="min-w-0" title={exact}>
      <div className="flex items-baseline gap-1.5 whitespace-nowrap text-base tabular-nums">
        <span className="font-medium text-ink">{humanBytes(fs.used_bytes)}</span>
        <span className="text-muted">{`/ ${humanBytes(fs.total_bytes)}`}</span>
        {pct !== null && <span className="ml-auto pl-2 text-xs text-muted">{pctText(pct)}</span>}
      </div>
      {pct !== null && (
        <div aria-hidden className="mt-1 h-1.5 overflow-hidden rounded-full bg-line/70">
          <div className={`h-full rounded-full ${fill}`} style={{ width: `${width}%` }} />
        </div>
      )}
      {(fs.consistent === false || fs.fresh === false) && (
        <p className="mt-1 text-xs text-muted break-keep">
          {fs.consistent === false && `노드마다 다름 — ${fs.node_name} 기준`}
          {fs.consistent === false && fs.fresh === false && SEP}
          {fs.fresh === false && <span className="text-attn">{`보고 ${agoText(fs.reported_at, now)}`}</span>}
        </p>
      )}
    </div>
  );
}

// 수치 칸 배치(리뷰: 768~860px 에서 4칸 고정 칸이 ~93px 라 「123,456,789개」가 옆 칸 숫자에 붙거나 겹쳤다 -- 사이드바가 패널을
// 좁히는 md 폭). sm~lg 는 칸 최소 폭(9.5rem -- 9자리 개수 + 「이상」이 한 줄)으로 칸 수를 정한다(auto-fit: 넓으면 4칸, 좁으면 3·2칸).
// lg 의 왼쪽 열은 18rem 이상 + 내용 폭(max-content)이라 2×2 가 큰 수에서도 「이상」을 한 줄에 둔다(분포 열이 나머지를 쓴다).
// 375 는 2칸 그대로(칸 사이를 좁혀 10자리 개수까지 말줄임 없이). 그래도 넘치는 값(13자리 등)은 Stat 이 칸 안에서 말줄임하고
// title 이 정확한 값을 준다 -- 옆 칸을 덮지 않는다.
const LAYOUT = "grid gap-x-8 gap-y-5 lg:grid-cols-[minmax(18rem,max-content)_minmax(0,1fr)]";
const STATS = "grid grid-cols-2 content-start gap-x-4 gap-y-3 sm:grid-cols-[repeat(auto-fit,minmax(9.5rem,1fr))] sm:gap-x-6 lg:grid-cols-2";

// 합계 줄 [자세히] 안의 블록(머리 주석): 적용 필터(필터 합계) → 수치 dl(+ df) | 파일 크기 분포 → 각주. 값이 오기 전·실패는
// 줄이 말하므로 여기는 그리지 않는다.
export function StorageTotals({ storage, now, attr }: { storage: string; now: number; attr?: UsageAttrFilter }) {
  const filtered = attr !== undefined && hasAttr(attr);
  const q = useStorageTotals(storage, filtered ? attr : undefined);
  const sizeId = useId();
  const t = q.data;
  // 대상 0(요약과 합계 사이의 경합·빈 스토리지·필터에 맞는 경로 없음)은 블록 없음 -- 패널이 이미 tab.targetCount === 0 이면
  // 그리지 않고, 필터 0건은 목록의 빈 상태가 말한다.
  if (t === undefined || count(t.target_count) === 0) return null;

  const title = filtered ? "필터 합계" : "이 스토리지 합계";
  const bytes = sumOf(t.total_bytes), files = sumOf(t.total_files), dirs = sumOf(t.total_directories);
  const size = t.file_size_histogram;
  // 필터 합계엔 df 가 없다(서버도 null -- 부분 집합의 파일시스템 사용량은 뜻이 없다). 서버가 실어 와도 그리지 않는다.
  const fs = filtered ? null : fsShape(t.filesystem);
  const reading = isReading(t) && !q.isError;
  const exact = (n: number | null) => (n === null ? undefined : countText(n));
  return (<div className="space-y-3">
    {filtered && <p className="text-xs text-ink/70 [overflow-wrap:anywhere]">{`적용 필터: ${attrSummary(attr!)}`}</p>}
    <div className={LAYOUT}>
      <dl aria-label={title} className={STATS}>
        <Stat label="실 사용량" value={bytes === null ? "—" : humanBytes(bytes)}
              lowerBound={partialSum(t, t.total_bytes, bytes)}
              title={bytes === null ? undefined : `${bytes.toLocaleString("ko-KR")} B`} />
        <Stat label="파일" value={countText(files)} lowerBound={partialSum(t, t.total_files, files)} title={exact(files)} />
        <Stat label="디렉터리" value={countText(dirs)} lowerBound={partialSum(t, t.total_directories, dirs)}
              title={exact(dirs)} />
        {/* hot 은 비율이라 하한이 아니다(모름 대상은 분자·분모 모두에서 빠진다) -- 「이상」을 붙이지 않고 각주가 모름 수를 말한다. */}
        <Stat label={`hot 비율(${HOT_AGE_MAX_DAYS}일)`} value={hotText(t)} />
        {fs !== null && (
          <div className="col-span-2 min-w-0">
            <dt className="flex items-center gap-0.5 text-xs text-muted">
              파일시스템 사용(df)
              <HelpTip label="파일시스템 사용 설명" head="파일시스템 사용(df)" help={DF_HELP} className="-my-1" />
            </dt>
            <dd className="mt-0.5"><FilesystemUse fs={fs} now={now} /></dd>
          </div>
        )}
      </dl>
      <section aria-labelledby={sizeId} className="min-w-0 space-y-2">
        <SectionHead id={sizeId} title="파일 크기 분포" help={SIZE_HELP}>
          {count(size?.unknown) > 0 && count(size?.known) > 0 && (
            <span className="text-xs text-muted">{`대상 ${count(size?.known).toLocaleString("ko-KR")}개 기준`}</span>
          )}
        </SectionHead>
        <SizeDistribution buckets={size?.buckets} labelledBy={sizeId}
                          missingText={reading && count(size?.known) === 0
                            ? "요약을 읽는 중입니다…" : "크기 분포를 아는 대상이 없습니다"} />
      </section>
    </div>
    <Footnote t={t} now={now} reading={reading} />
  </div>);
}
