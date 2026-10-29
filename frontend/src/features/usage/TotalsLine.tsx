import { Fragment, useEffect, useId, useRef } from "react";
import { ChevronDown } from "lucide-react";
import { HelpTip } from "../../components/ui/HelpTip";
import { Skeleton } from "../../components/ui/Skeleton";
import { ApiError } from "../../lib/api";
import type { UsageAttrFilter, UsageStorageFilesystem, UsageStorageTotals } from "../../lib/types";
import { agoText } from "../../lib/datetime";
import { pctText } from "../../lib/sizeBuckets";
import { INVALID_NAMES_HELP, INVALID_NAMES_LABEL } from "../../lib/scanCounters";
import { UsageContractError, hasAttr, useStorageTotals } from "./useUsage";
import { humanBytes } from "./usageStats";
import {
  FILTER_TOTALS_HELP, StorageTotals, TOTALS_HELP, count, countText, fsShape, hotText, isReading, lowerBound, partialSum,
  sumOf,
} from "./StorageTotals";

// 합계 줄(2026-10-29 사용자 결정 「권장사항대로」 -- 「이 스토리지 합계」·「필터 합계」 두 벌 블록을 표 바로 위 한 줄로). 숫자는
// 예전 블록과 같은 쿼리(useStorageTotals -- 무필터 1회, 필터면 필터 합계 + 스토리지 합계 2회, 지금과 같은 비용)다.
//   무필터: 「이 스토리지 합계(?) · 실 사용량 · 파일 · 디렉터리 · hot 비율 · df %(사용 / 전체)」 -- df 90% 이상은 주의색.
//   필터:   「필터 합계(?) 대상 N개 · 실 사용량 · 파일 · 디렉터리 · hot 비율 · 이 스토리지 X」 -- 마지막은 흐린 글자(같은 키라 캐시).
// 줄 안에서 말하는 것: 부분 합 「이상」(값 모름을 뺀 합·창을 넘어 잘린 합 -- 스토리지별 비교와 같은 규칙), 잘못된 인코딩 이름(1개
// 이상일 때만, 주의색), 요약 읽는 중(useStorageTotals 의 1초 이어 읽기), 검색(경로·PV 이름)이 걸렸으면 「합계에 들어가지 않는다」, 실패와
// [다시 시도](첫 실패·재조회 실패 둘 다 -- 오류 중엔 이어 읽기가 멈춘다). 로딩은 줄 높이의 Skeleton.
// 필터일 때 스토리지 전체(2026-10-29 리뷰 -- 예전엔 필터를 걸어도 스토리지 합계 블록이 위에 그대로 남아 제 실패·df 경고를 말했다):
// 그 쿼리의 실패와 [다시 시도](「이 스토리지 합계 다시 시도」 -- 필터 합계의 것과 따로 부른다)와 df 90% 이상 주의(「이 스토리지 df」)는
// 줄이 말하고, 노드 불일치·오래된 보고·인코딩 경고·읽는 중은 [자세히] 의 「이 스토리지 전체」 한 줄이 말한다. 필터 합계의 값이 오기
// 전·실패여도 스토리지 쪽 조각은 따로 보인다(서로 다른 요청).
// 오른쪽 [자세히] = <details>(기본 닫힘, 열림은 기억하지 않는다): 안은 StorageTotals 블록 그대로(KPI·df 미터·크기 분포·각주) --
// 필터면 적용 필터 문장 + 필터 KPI·분포 + 「이 스토리지 전체」 한 줄. 필터를 건 동안 줄어드는 것: 스토리지 전체의 분포를 필터 분포와
// 나란히 보지 못하고, 스토리지 전체 각주(최상위·하위·값 모름·창·scan 시점)는 숫자 뒤 「이상」으로만 남는다 -- 필터를 풀면 본다.
// [자세히] 는 줄 오른쪽 위에 겹쳐 그린다(absolute) -- 펼친 내용이 줄 아래 전체 폭을 쓰게. 줄은 그 자리만큼 오른쪽을 비운다.
// 대상 0 이면 줄이 없다(StorageTotals 와 같은 규칙 -- 빈 상태 안내가 말한다; 필터 0건은 「대상 0개」 + 스토리지 숫자만).
// 줄은 구분 기호 없이 간격만 쓴다(MetaLine 관용 -- 좁은 폭에서 접힌 줄이 「·」로 시작하지 않게).

const n = (x: number) => x.toLocaleString("ko-KR");

// 줄의 한 칸: 흐린 라벨 + 값(한 덩어리 -- 좁으면 칸째 다음 줄로).
function Item({ label, children, title, valueClass = "text-ink" }: {
  label: string; children: React.ReactNode; title?: string; valueClass?: string;
}) {
  return (
    <div className="inline-flex items-baseline gap-1.5 whitespace-nowrap">
      <dt className="text-xs text-muted">{label}</dt>
      <dd className={`tabular-nums ${valueClass}`} title={title}>{children}</dd>
    </div>
  );
}
const More = () => <>{" "}<span className="text-xs text-muted">이상</span></>;

// 재조회 단추 -- 오류 중엔 이어 읽기가 멈췄을 수 있다(폴링하지 않는다): 창 포커스·30초를 기다리지 않고 다시 부를 수 있게. 필터
// 합계와 스토리지 합계가 함께 실패하면 단추가 둘이라 스토리지 쪽은 이름을 달리 붙인다(보이는 글자를 이름 안에 둔다). 성공하면
// 단추가 사라진다 -- 포커스는 줄(구역)로 옮긴다(TotalsLine 의 refocus).
function Retry({ onRetry, label }: { onRetry: () => void; label?: string }) {
  return (
    <button type="button" aria-label={label} onClick={onRetry}
            className="rounded text-xs text-accent hover:underline focus-visible:outline-none focus-visible:ring-2
                       focus-visible:ring-accent">다시 시도</button>
  );
}

// 오류의 덧붙일 사유: 옛 API(경로 없음)는 SPA index.html 을 200 으로 줘서 오류가 JSON 파서 문장이다 -- 서버가 준 사유(ApiError)·배포
// 틈 판정(UsageContractError -- 필터 합계의 메아리 대조)만 덧붙인다.
const reason = (e: unknown) =>
  ((e instanceof ApiError || e instanceof UsageContractError) && e.message ? ` — ${e.message}` : "");

// df 한 칸: 「85% (399.3 GiB / 467.3 GiB)」. 노드 불일치·오래된 보고는 「*」 + 낭독 문장(자세히 안의 미터가 말로 풀어 쓴다).
function dfParts(fs: UsageStorageFilesystem, now: number) {
  const pct = (fs.used_bytes / fs.total_bytes) * 100;
  const caveat = [fs.consistent === false && "노드마다 다름", fs.fresh === false && `보고 ${agoText(fs.reported_at, now)}`]
    .filter(Boolean).join(", ");
  const title = `${fs.used_bytes.toLocaleString("ko-KR")} B / ${fs.total_bytes.toLocaleString("ko-KR")} B · ${fs.node_name} 보고`
    + (caveat !== "" ? ` · ${caveat}` : "");
  return { pct, caveat, title };
}
const Caveat = ({ text }: { text: string }) => (
  text === "" ? null : <span className="text-xs text-attn"><span aria-hidden>*</span><span className="sr-only">{`(${text})`}</span></span>);

/** 「이 스토리지 전체」 한 줄(필터 합계의 [자세히] 맨 아래) -- 숫자 다섯 + 스토리지 전체의 경고(df 90% 이상 주의색·노드 불일치·
 *  오래된 보고, 잘못된 인코딩 이름, 읽는 중). */
function BaseSummary({ t, now, reading }: { t: UsageStorageTotals; now: number; reading: boolean }) {
  const bytes = sumOf(t.total_bytes), files = sumOf(t.total_files), dirs = sumOf(t.total_directories);
  const more = (m: Parameters<typeof partialSum>[1], s: number | null) => (partialSum(t, m, s) ? " 이상" : "");
  const fs = fsShape(t.filesystem);
  const df = fs !== null ? dfParts(fs, now) : null;
  const invalid = sumOf(t.invalid_name_entries);
  const parts: React.ReactNode[] = [
    `${bytes === null ? "—" : humanBytes(bytes)}${more(t.total_bytes, bytes)}`,
    `파일 ${countText(files)}${more(t.total_files, files)}`,
    `디렉터리 ${countText(dirs)}${more(t.total_directories, dirs)}`,
    `hot ${hotText(t)}`,
    fs !== null && df !== null && (<>
      {`df ${humanBytes(fs.used_bytes)} / ${humanBytes(fs.total_bytes)} `}
      <span className={df.pct >= 90 ? "font-medium text-attn" : undefined} title={df.title}>{`(${pctText(df.pct)})`}</span>
      {df.caveat !== "" && <span className="text-attn">{` — ${df.caveat}`}</span>}
    </>),
    invalid !== null && invalid > 0 && (
      <span className="text-attn" title={INVALID_NAMES_HELP}>
        {`${INVALID_NAMES_LABEL} ${n(invalid)}개${lowerBound(t.invalid_name_entries, invalid) ? " 이상" : ""}`}</span>),
    reading && `요약 읽는 중 ${n(count(t.pending_count))}개`,
  ].filter((p) => p !== false);
  return (
    <p className="text-xs text-muted [overflow-wrap:anywhere]">
      이 스토리지 전체: {parts.map((p, i) => <Fragment key={i}>{i > 0 && " · "}{p}</Fragment>)}
    </p>
  );
}

export function TotalsLine({ storage, now, attr, searchQ }: {
  storage: string; now: number; attr: UsageAttrFilter; searchQ: string;
}) {
  const filtered = hasAttr(attr);
  const q = useStorageTotals(storage, filtered ? attr : undefined);
  // 필터일 때 줄 끝의 「이 스토리지 X」와 [자세히] 의 전체 한 줄 -- 무필터면 q 와 같은 키(요청 하나).
  const baseQ = useStorageTotals(storage);
  const labelId = useId();
  // [다시 시도] 포커스 지킴(2026-10-29 2차 리뷰 -- StoragePanel refocus 관용): 재조회가 성공하면 단추가 오류 문구째 사라져 브라우저가
  // 포커스를 <body> 로 떨어뜨리고 화면 낭독기는 자리를 잃었다. 단추를 누른 뒤 포커스가 <body> 로 떨어져 있으면 이 줄(구역,
  // tabIndex -1 -- 이름 = 합계 라벨, 새 숫자를 읽는다)로 옮긴다(스크롤 없이). 재조회가 끝났는데 포커스가 살아 있으면(다시 실패해
  // 단추가 남았다·다른 곳으로 옮겼다) 건드리지 않고 잊는다.
  const sectionRef = useRef<HTMLElement>(null);
  const refocus = useRef(false);
  const retryWith = (refetch: () => Promise<unknown>) => () => { refocus.current = true; void refetch(); };
  const settled = !q.isFetching && !baseQ.isFetching;
  useEffect(() => {
    if (!refocus.current) return;
    const a = document.activeElement;
    if (a === null || a === document.body) { refocus.current = false; sectionRef.current?.focus({ preventScroll: true }); return; }
    if (settled) refocus.current = false;
  });
  const t = q.data;
  // 대상 0: 무필터면 줄이 없다(빈 상태 안내가 말한다). 필터에 맞는 대상 0 이면 줄은 「대상 0개」와 스토리지 전체 숫자만 남긴다 --
  // 예전엔 필터 합계 블록만 사라지고 스토리지 합계 블록은 남았다(그 숫자를 잃지 않게).
  const empty = t !== undefined && count(t.target_count) === 0;
  if (empty && !filtered) return null;

  const label = filtered ? "필터 합계" : "이 스토리지 합계";
  const retry = <Retry onRetry={retryWith(q.refetch)} />;

  // 필터일 때 스토리지 전체 조각(머리 주석): 값 「이 스토리지 X」 + df 90% 이상이면 「이 스토리지 df」(주의색), 실패면 그 사실과
  // 따로 부르는 [다시 시도]. 읽는 중(오류가 아닐 때)은 [자세히] 의 전체 한 줄이 말한다.
  const baseT = filtered ? baseQ.data : undefined;
  const baseKnown = baseT !== undefined && count(baseT.target_count) > 0 ? baseT : undefined;
  const baseBytes = baseKnown !== undefined ? sumOf(baseKnown.total_bytes) : null;
  const baseFs = baseKnown !== undefined ? fsShape(baseKnown.filesystem) : null;
  const baseDf = baseFs !== null ? dfParts(baseFs, now) : null;
  const baseItems = baseKnown !== undefined && (baseBytes !== null || (baseDf !== null && baseDf.pct >= 90)) && (
    <dl className="flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1">
      {baseBytes !== null && (
        <Item label="이 스토리지" valueClass="text-muted">
          {humanBytes(baseBytes)}{partialSum(baseKnown, baseKnown.total_bytes, baseBytes) && <More />}</Item>
      )}
      {baseDf !== null && baseDf.pct >= 90 && (
        <Item label="이 스토리지 df" title={baseDf.title} valueClass="font-medium text-attn">
          {pctText(baseDf.pct)}<Caveat text={baseDf.caveat} /></Item>
      )}
    </dl>
  );
  const baseError = filtered && baseQ.isError && (
    <span className="inline-flex flex-wrap items-baseline gap-x-2 text-xs">
      {baseQ.data === undefined
        ? <span className="text-bad [overflow-wrap:anywhere]">{`이 스토리지 합계를 불러오지 못했습니다${reason(baseQ.error)}`}</span>
        : <span className="text-muted">이 스토리지 합계를 새로 고치지 못했습니다</span>}
      <Retry onRetry={retryWith(baseQ.refetch)} label="이 스토리지 합계 다시 시도" />
    </span>
  );

  let body: React.ReactNode;
  if (t === undefined) {
    if (q.isError) {
      body = (<span className="inline-flex flex-wrap items-baseline gap-x-2">
        <span className="text-bad [overflow-wrap:anywhere]">{`합계를 불러오지 못했습니다${reason(q.error)}`}</span>
        {retry}
      </span>);
    } else {
      body = (<span className="flex min-w-[12rem] flex-1 items-center">
        <span className="sr-only">합계 불러오는 중…</span><Skeleton className="h-4 w-full max-w-md" /></span>);
    }
    body = <>{body}{baseItems}{baseError}</>;
  } else {
    const bytes = sumOf(t.total_bytes), files = sumOf(t.total_files), dirs = sumOf(t.total_directories);
    const fs = filtered ? null : fsShape(t.filesystem);
    const reading = isReading(t) && !q.isError;
    const invalid = sumOf(t.invalid_name_entries);
    const df = fs !== null ? dfParts(fs, now) : null;
    body = (<>
      {filtered && <span className="whitespace-nowrap text-xs text-muted">{`대상 ${n(count(t.target_count))}개`}</span>}
      {(!empty || df !== null) && (
        <dl className="flex min-w-0 flex-wrap items-baseline gap-x-4 gap-y-1">
          {!empty && (<>
            <Item label="실 사용량" valueClass="font-semibold text-ink"
                  title={bytes === null ? undefined : `${bytes.toLocaleString("ko-KR")} B`}>
              {bytes === null ? "—" : humanBytes(bytes)}{partialSum(t, t.total_bytes, bytes) && <More />}</Item>
            <Item label="파일" title={files === null ? undefined : countText(files)}>
              {countText(files)}{partialSum(t, t.total_files, files) && <More />}</Item>
            <Item label="디렉터리" title={dirs === null ? undefined : countText(dirs)}>
              {countText(dirs)}{partialSum(t, t.total_directories, dirs) && <More />}</Item>
            <Item label="hot 비율">{hotText(t)}</Item>
          </>)}
          {df !== null && fs !== null && (
            <Item label="df" title={df.title} valueClass={df.pct >= 90 ? "font-medium text-attn" : "text-ink"}>
              {pctText(df.pct)}
              <span className="text-muted">{` (${humanBytes(fs.used_bytes)} / ${humanBytes(fs.total_bytes)})`}</span>
              <Caveat text={df.caveat} />
            </Item>
          )}
        </dl>
      )}
      {baseItems}
      {/* 잘못된 인코딩 이름(2026-10-26 「카운터만 둠」): 1개 이상일 때만 주의색 한 조각 -- 모르는 대상이 있으면 「이상」. */}
      {invalid !== null && invalid > 0 && (
        <span className="whitespace-nowrap text-xs text-attn" title={INVALID_NAMES_HELP}>
          {`${INVALID_NAMES_LABEL} ${n(invalid)}개${lowerBound(t.invalid_name_entries, invalid) ? " 이상" : ""}`}</span>
      )}
      {reading && <span className="whitespace-nowrap text-xs text-muted">{`요약 읽는 중 ${n(count(t.pending_count))}개`}</span>}
      {searchQ !== "" && <span className="text-xs text-muted break-keep">검색은 합계에 들어가지 않습니다</span>}
      {q.isError && (
        <span className="inline-flex flex-wrap items-baseline gap-x-2 text-xs text-muted">
          합계를 새로 고치지 못했습니다{retry}</span>
      )}
      {baseError}
    </>);
  }

  return (
    <section ref={sectionRef} tabIndex={-1} aria-labelledby={labelId}
             className="relative rounded-card border border-line bg-surface px-4 py-2.5 outline-none">
      <div className={`flex min-h-[1.75rem] flex-wrap items-center gap-x-4 gap-y-1 text-sm ${t !== undefined ? "pr-20" : ""}`}>
        <span className="inline-flex items-center gap-0.5 whitespace-nowrap font-medium text-ink">
          <span id={labelId}>{label}</span>
          <HelpTip label={`${label} 설명`} head={label} help={filtered ? FILTER_TOTALS_HELP : TOTALS_HELP} className="-my-1" />
        </span>
        {body}
      </div>
      {t !== undefined && (
        <details className="group/totals">
          <summary className="absolute right-3 top-2 inline-flex cursor-pointer list-none items-center gap-1 rounded-lg border
                              border-line bg-surface px-2 py-1 text-xs text-ink hover:bg-panel focus-visible:outline-none
                              focus-visible:ring-2 focus-visible:ring-accent [&::-webkit-details-marker]:hidden">
            자세히
            <ChevronDown aria-hidden className="h-3.5 w-3.5 text-muted transition-transform group-open/totals:rotate-180" />
          </summary>
          <div className="mt-3 space-y-3 border-t border-line pt-3">
            <StorageTotals storage={storage} now={now} attr={filtered ? attr : undefined} />
            {baseKnown !== undefined && (
              <BaseSummary t={baseKnown} now={now} reading={isReading(baseKnown) && !baseQ.isError} />
            )}
          </div>
        </details>
      )}
    </section>
  );
}
