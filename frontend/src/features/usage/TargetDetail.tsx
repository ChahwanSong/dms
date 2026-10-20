import { Fragment, useId, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { Table } from "../../components/ui/Table";
import { TimeSeriesChart } from "../../components/ui/TimeSeriesChart";
import { Segmented } from "../../components/ui/Segmented";
import { HoverTip, placeBeside, useHoverAnchor, type Rect } from "../../components/ui/HoverTip";
import { SizeDistribution } from "../../components/ui/SizeDistribution";
import type { ApiError } from "../../lib/api";
import type { HistogramBucket, UsagePoint } from "../../lib/types";
import { kstStampEpoch, kstDay } from "../../lib/datetime";
import { pctText, sizeDistribution } from "../../lib/sizeBuckets";
import { useScanHistory } from "./useUsage";
import { SIZE_HELP, SectionHead, Stat } from "./UsageParts";
import {
  AXIS_MEANING, HOT_AGE_MAX_DAYS, ageLabel, hotRatio, humanBytes, pointEpoch, stackLayout, tempColorOf,
} from "./usageStats";

// 펼친 타깃 상세(2026-09-14 행 아래 펼침 → 2026-10-20 UsageAnalysis 에서 분리·정리). 2026-10-20 정리(사용자 요청 「깔끔하고
// 심플하고 직관적으로」): 요약 타일 4개(카드 안의 카드) → 한 줄 수치(dl), 표시 창·온도 축은 공용 Segmented(aria-pressed),
// 긴 캡션은 제목 옆 HelpTip, 스캔 이력 표는 <details> 기본 닫힘 -- 이력 29건 타깃의 펼침 높이가 1,823px 였다.
//
// 절대 시각은 공유 datetime 헬퍼(KST)로 통일했다 -- kstStampEpoch(초 -> KST 벽시계), kstDay(초 -> KST MM-DD). 온도 열 축
// 라벨은 폭(max-w-12) 때문에 MM-DD 만 쓰고 분 단위는 툴팁이 든다(실화면 확인).

// 이력 표시 창 선택지(사용자 요청 2026-08-24). 서버 상한 60(routes_usage _MAX_POINTS)과 발맞춘 고정 선택지 -- 자유 입력 없음.
// 선택된 「최근 N건」이 곧 상시 창 안내다(조건부로만 말하면 「이게 전체 이력」이라는 오독이 기본값이 된다 -- 같은 날 요청).
const HISTORY_WINDOWS = [{ value: 30, label: "최근 30건" }, { value: 60, label: "최근 60건" }] as const;

const TEMP_CAPTIONS: Record<string, string> = {
  atime: "각 열 = 스캔 1회. 위(빨강)=hot·최근 접근, 아래(파랑)=cold — atime 기준 용량 비중. relatime/open_noatime 환경에선 근사. 열에 마우스를 올리면 구간별 용량",
  mtime: "각 열 = 스캔 1회. 위(빨강)=최근 수정, 아래(파랑)=오래됨 — mtime 기준 용량 비중. 열에 마우스를 올리면 구간별 용량",
  ctime: "각 열 = 스캔 1회. 위(빨강)=최근 변경, 아래(파랑)=오래됨 — ctime 기준 용량 비중. 열에 마우스를 올리면 구간별 용량",
};

// 온도 열 툴팁(2026-10-02). 예전엔 열 번호 비율(i/n)로 **차트 전체 폭** 위에 놓았는데, 열은 폭 상한(max-w-12)
// 때문에 왼쪽에 몰려 있어 툴팁이 열에서 수백 px 떨어진 곳에 떴다(실측: 첫 열 x=299 인데 툴팁 x=407, 끝 열 x=515 인데
// 1082). 이제 열의 실제 화면 사각형 옆(HoverTip placeBeside -- 키가 큰 범례라 위보다 옆)에 붙고, 펼친 행 안(표
// 래퍼 overflow-x-auto)에 갇혀 잘리지 않게 포털로 뜬다.
function TempTooltip({ point, buckets, tempKey, anchor, onClose }: {
  point: UsagePoint; buckets: HistogramBucket[]; tempKey: string;
  anchor: Rect; onClose: () => void;
}) {
  const epoch = pointEpoch(point);
  const stack = stackLayout(buckets);
  // 비율 두 가지(리뷰): 지금 보는 축의 180일 이내 비중, 그리고 목록·수치 줄·CSV 와 같은 정의의 hot 비율(atime).
  // 축이 atime 이면 둘은 같은 수라 한 행만.
  const axisRecent = hotRatio(buckets);
  const atimeHot = hotRatio(point.time_histograms["atime"]);
  const files = point.summary?.["total_files"];
  const color = tempColorOf(buckets.length);
  const lastHot = buckets.reduce((acc, b, i) =>
    (typeof b.max_age_days === "number" && b.max_age_days <= HOT_AGE_MAX_DAYS ? i : acc), -1);
  return (
    <HoverTip anchor={anchor} onClose={onClose} place={placeBeside} className="w-72 p-3">
      <div className="font-semibold">{epoch === null ? "시각 모름" : kstStampEpoch(epoch)}</div>
      <dl className="mt-1.5 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
        <dt className="text-muted">요청자</dt>
        <dd className="text-right">{point.requester ?? "—"}</dd>
        <dt className="text-muted">실 사용량</dt>
        <dd className="text-right tabular-nums font-medium">
          {typeof point.total_bytes === "number" ? humanBytes(point.total_bytes) : "—"}</dd>
        <dt className="text-muted">파일 수</dt>
        <dd className="text-right tabular-nums">
          {typeof files === "number" ? `${files.toLocaleString("ko-KR")}개` : "—"}</dd>
        <dt className="text-muted">{`hot 비율(atime ${HOT_AGE_MAX_DAYS}일)`}</dt>
        <dd className="text-right tabular-nums font-medium">
          {atimeHot === null ? "—" : pctText(atimeHot * 100)}</dd>
        {tempKey !== "atime" && (<>
          <dt className="text-muted">{`${tempKey} ${HOT_AGE_MAX_DAYS}일 이내`}</dt>
          <dd className="text-right tabular-nums">{axisRecent === null ? "—" : pctText(axisRecent * 100)}</dd>
        </>)}
      </dl>
      {stack === null ? (
        <p className="mt-2 border-t border-line pt-2 text-muted">이 축의 온도 분포 없음</p>
      ) : (
        <div className="mt-2 border-t border-line pt-2">
          <p className="mb-1 text-muted">{`${tempKey}(${AXIS_MEANING[tempKey] ?? tempKey}) 기준 나이별 용량`}</p>
          {/* 색 = 막대의 그 칸. 위(빨강)가 최근, 아래(파랑)로 갈수록 오래됨 -- 사용자 요청 2026-10-02 "빨강/초록/
              파랑이 뭔지와 구체적인 수치". 180일 경계 아래에 점선(hot 비율의 기준). */}
          <div className="grid grid-cols-[auto_1fr_auto_auto] items-center gap-x-2 gap-y-0.5"
               aria-label="구간별 용량">
            {buckets.map((b, bi) => (
              <Fragment key={bi}>
                <span aria-hidden className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: color(bi) }} />
                <span>{ageLabel(b)}</span>
                <span className="text-right tabular-nums">
                  {typeof b.bytes === "number" ? humanBytes(b.bytes) : "—"}</span>
                <span className="w-12 text-right tabular-nums text-muted">{pctText(stack[bi].pct)}</span>
                {bi === lastHot && bi < buckets.length - 1 && (
                  <span aria-hidden className="col-span-4 my-0.5 border-t border-dashed border-line" />
                )}
              </Fragment>
            ))}
          </div>
          <p className="mt-1.5 text-[10px] text-muted">
            {`빨강 → 파랑 = 최근 → 오래됨 · 점선 위 = ${HOT_AGE_MAX_DAYS}일 이내`}</p>
        </div>
      )}
    </HoverTip>
  );
}

// 온도 추이: 스캔별 100% 스택 열(위=hot). 시간순 정렬은 부모(서버 정렬)가 보장.
// 호버/포커스 즉시 툴팁(2026-08-29 사용자 요청): 열에 커서를 대면 바로 뜬다(기본 title 지연 제거).
// 2026-10-02: 툴팁을 열 옆에 붙이고(placeTooltip), 시각·요청자·용량·파일 수·hot 비율 + 색별 구간·용량·비중 범례.
function TemperatureTrend({ points, tempKey }: {
  points: UsagePoint[]; tempKey: string;
}) {
  const { active, bind, close } = useHoverAnchor<number>();
  const box = useRef<HTMLDivElement>(null);
  const [boxWidth, setBoxWidth] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el) return;
    const measure = () => setBoxWidth(el.clientWidth > 0 ? el.clientWidth : null);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  const cols = points.map((p) => ({
    epoch: pointEpoch(p),
    buckets: p.time_histograms[tempKey] ?? [],
    stack: stackLayout(p.time_histograms[tempKey]),
    point: p,
  }));
  const n = cols.length;
  // 창 전환(30↔60)·재조회로 열이 줄면 사라진 열은 mouseleave 를 내지 않는다 -- 범위 밖이면 툴팁 없음(BarChart 와 같은 가드).
  const hovered = active !== null && active.key < n ? active : null;
  // 열이 좁으면(열당 32px 미만 -- 「MM-DD」 한 개가 안 들어간다) 열마다의 날짜는 「0」 한 글자로 잘렸다(375 의 30열 = 열당
  // 8px) -- 첫·끝 열 날짜만 축 줄에 둔다(실 사용량 차트의 x 축과 같은 모양). 열마다의 시각은 버튼 이름·툴팁이 늘 말한다.
  // 폭을 모르면(jsdom) 열 수로만 가른다.
  const sparse = boxWidth !== null ? boxWidth / n < 32 : n > 12;
  const day = (c: (typeof cols)[number]) => (c.epoch === null ? "—" : kstDay(c.epoch));
  const gap = n > 20 ? "gap-0.5" : "gap-1.5";
  // 폭을 재는 상자는 빈 상태에서도 같은 요소다(축 전환으로 빈 상태 ↔ 차트가 바뀌어도 관찰 대상이 남는다).
  if (cols.every((c) => c.stack === null)) {
    return <div ref={box}><p className="text-muted text-xs">이 축의 온도 분포가 있는 스캔이 없습니다</p></div>;
  }
  return (
    <div ref={box}>
      {/* 고밀도(>20열)는 gap 을 줄인다 -- 고정 gap 합이 컨테이너를 넘으면 가로
          스크롤(e2e L1 금지)이 생긴다. 열 자체는 flex-1+min-w-0 으로 줄어든다. */}
      <div role="img" aria-label={`데이터 온도 추이(${tempKey})`}
           className={`flex items-end ${gap}`}>
        {cols.map((c, i) => (
          // 열은 버튼(호버·포커스 즉시 툴팁). 기본 title 은 지연이라 제거.
          <button key={i} type="button"
                  aria-label={`${c.epoch === null ? "" : kstStampEpoch(c.epoch) + " · "}`
                    + `${c.point.requester ?? "—"}`}
                  {...bind(i)}
                  className="flex min-w-0 max-w-12 flex-1 cursor-default flex-col
                             items-center gap-1">
            {c.stack === null ? (
              // 분포 없음(빈 트리·구형 리포트) -- 0% 스택으로 그리면 거짓이라 빈 트랙
              <div className={`h-24 w-full max-w-5 rounded-sm bg-accent/10
                               ${active?.key === i ? "ring-2 ring-accent/40" : ""}`} />
            ) : (
              <div className={`flex h-24 w-full max-w-5 flex-col overflow-hidden
                               rounded-sm ${active?.key === i ? "ring-2 ring-accent/40" : ""}`}>
                {c.stack.map((s, bi) => (
                  <div key={bi} style={{ height: `${s.pct}%`,
                                         backgroundColor: tempColorOf(c.stack!.length)(bi) }} />
                ))}
              </div>
            )}
            {!sparse && (
              <span className="w-full truncate text-center text-[10px] text-muted">{day(c)}</span>
            )}
          </button>
        ))}
      </div>
      {sparse && (
        // 축 줄은 열과 같은 칸 구조(flex-1 max-w-12)라 첫·끝 날짜가 그 열 아래에 선다. 끝 날짜는 오른쪽 맞춤으로 칸 안쪽(왼쪽)으로
        // 넘친다 -- 차트 밖으로 새어 가로 스크롤을 만들지 않게.
        <div aria-hidden className={`mt-1 flex h-3.5 ${gap}`}>
          {cols.map((c, i) => (
            <span key={i} className="relative min-w-0 max-w-12 flex-1">
              {(i === 0 || i === n - 1) && (
                <span className={`absolute top-0 whitespace-nowrap text-[10px] tabular-nums text-muted ${
                  i === 0 ? "left-0" : "right-0"}`}>{day(c)}</span>
              )}
            </span>
          ))}
        </div>
      )}
      {hovered !== null && (
        <TempTooltip point={cols[hovered.key].point} buckets={cols[hovered.key].buckets}
                     tempKey={tempKey} anchor={hovered.rect} onClose={close} />
      )}
    </div>
  );
}

// 이력에서 가장 늦은 포인트: 시각(pointEpoch)이 가장 늦은 것, 같으면 서버 순서(완료 시각 오름차순)의 뒤. 시각을 아는 포인트가
// 하나도 없으면 서버 순서의 마지막.
function newestPoint(points: UsagePoint[]): UsagePoint | null {
  let best: UsagePoint | null = null, bestEpoch: number | null = null;
  for (const p of points) {
    const e = pointEpoch(p);
    if (best === null || (e !== null ? bestEpoch === null || e >= bestEpoch : bestEpoch === null)) {
      best = p; bestEpoch = e;
    }
  }
  return best;
}

// 타깃 1건의 상세(「최신 scan」 블록 = 수치 줄 | 파일 크기 분포 → 실 사용량 추이 → 온도 추이 → 스캔 이력). 목록의 **선택한 행
// 바로 아래에 펼쳐진다**(expand). 표시 창·온도 축 state 는 여기 국소라 타깃을 바꾸면 초기화된다 -- 다른 타깃의 잔류 선택이 새
// 타깃에 묻어 가지 않는다. showTitle 은 목록 밖 폴백 칸(선택 타깃이 지금 목록에 없을 때)에서만 켠다 -- 펼침 행 안에서는 행
// 자체가 제목이다. latest = 목록 행의 최신 성공 scan(크기 분포의 기준 -- 폴백 칸엔 행이 없어 undefined).
export function TargetDetail({ storage, target, showTitle, latest }: {
  storage: string; target: string; showTitle: boolean; latest?: UsagePoint | null;
}) {
  const navigate = useNavigate();
  const [windowLimit, setWindowLimit] = useState<number>(HISTORY_WINDOWS[0].value);
  const history = useScanHistory(storage, target, windowLimit);

  const points = history.data?.points ?? [];
  // 차트는 용량을 아는 포인트만 -- null(모름)을 0 으로 그리면 거짓 절벽이 된다.
  // epoch 로 재정렬한다: 서버 정렬 1차 키는 완료 시각(finished_at)인데 차트 x 는
  // 리포트 생성 시각이라, 생성→종단 지연이 스캔 간격을 넘으면(전 요청자 통합
  // 화면에선 근접 스캔이 정상) x 가 비단조가 되어 선이 뒤로 꺾인다(리뷰 확인).
  const charted = useMemo(
    () => points.flatMap((p) => {
      const epoch = pointEpoch(p);
      return p.total_bytes !== null && epoch !== null
        ? [{ point: p, epoch, bytes: p.total_bytes }] : [];
    }).sort((a, b) => a.epoch - b.epoch),
    [points]);
  const unknownCount = points.length - charted.length;

  const latestCharted = charted.length > 0 ? charted[charted.length - 1] : null;
  const prev = charted.length > 1 ? charted[charted.length - 2] : null;
  const delta = latestCharted !== null && prev !== null ? latestCharted.bytes - prev.bytes : null;
  const latestHot = latestCharted !== null
    ? hotRatio(latestCharted.point.time_histograms["atime"]) : null;

  // 파일 크기 분포(2026-10-21)의 기준 = 목록 행의 최신 성공 scan(표의 「파일 수」와 같은 scan -- 사용자 요청 「최근 스캔 기준」).
  // 행이 없거나(목록 밖 폴백) 행에 최신 정보가 없으면 이력에서 가장 늦은 포인트(시각을 모르면 서버 순서의 마지막). 최신 scan 의
  // 리포트를 못 읽었으면 더 오래된 scan 의 분포로 대신하지 않는다 -- 「최근」이라는 말이 거짓이 된다(표의 (DB) 표시와 같은 사실).
  const snapshot = useMemo(() => latest ?? newestPoint(points), [latest, points]);
  const sizeTotal = sizeDistribution(snapshot?.file_size_histogram)?.total ?? null;
  const sizeHeadId = useId();

  const [tempKey, setTempKey] = useState("atime");
  const tempKeys = ["atime", "mtime", "ctime"].filter(
    (k) => points.some((p) => p.time_histograms[k]?.length));
  // 선택 축이 이 타깃에 없으면(atime 없는 이력·타깃 전환 잔류 -- 리뷰 확인)
  // 첫 가용 축으로 대체한다. state 를 직접 고치는 useEffect 대신 파생값:
  // 렌더 한 번으로 정합하고, 사용자가 고른 값은 가용해지는 순간 되살아난다.
  const effectiveTempKey = tempKeys.includes(tempKey)
    ? tempKey : (tempKeys[0] ?? tempKey);
  const skipped = history.data?.skipped_unreadable ?? 0;

  return (
    // 폭 상한 = 실 사용량 차트(TimeSeriesChart max-w-3xl) -- 소제목 줄의 컨트롤이 차트 오른쪽 끝에 맞는다.
    <div className="max-w-3xl space-y-5">
      {showTitle && (
        <h3 className="text-base font-medium text-ink [overflow-wrap:anywhere]">{target}</h3>
      )}
      {history.isLoading ? <p className="text-sm text-muted">이력 불러오는 중…</p>
       : history.isError ? <p className="text-sm text-bad">{(history.error as ApiError).message}</p>
       : points.length === 0 ? (
        // 리포트를 못 읽어 빠진 스캔만 있으면 「scan 이 없다」가 아니다(목록 행은 DB 용량으로 보인다) -- 그대로 말한다.
        <p className="text-sm text-muted">{skipped > 0
          ? `성공 scan ${skipped}건의 리포트를 읽지 못해 표시할 이력이 없습니다`
          : "이 타깃의 성공 scan 이 없습니다"}</p>
      ) : (<>
        {/* 「최신 scan」 블록(2026-10-21): 수치 줄 | 파일 크기 분포. lg 에선 나란히(수치 2×2) -- 상세가 늘어나는 높이를 분포
            높이로 묶는다. sm~lg 는 수치 4칸 한 줄 + 분포 아래(지금까지의 모양), 그 아래 폭은 2칸. 스토리지 합계와 같은 문법. */}
        <div className="grid gap-x-8 gap-y-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
          {/* null ≠ 0: 모름은 "—" -- 0 B 로 그리면 "다 지워졌다"는 거짓이 된다 */}
          <dl aria-label="최신 scan 요약"
              className="grid grid-cols-2 content-start gap-x-6 gap-y-3 sm:grid-cols-4 lg:grid-cols-2">
            <Stat label="최신 실 사용량" value={latestCharted !== null ? humanBytes(latestCharted.bytes) : "—"} />
            <Stat label="직전 스캔 대비"
                  value={delta === null ? "—" : `${delta >= 0 ? "+" : "−"}${humanBytes(Math.abs(delta))}`} />
            {/* 라벨은 짧게(375 의 두 칸 배치에서 한 줄) -- atime 기준이라는 정의는 표 머리글 설명·온도 툴팁이 말한다. */}
            <Stat label={`hot 비율(${HOT_AGE_MAX_DAYS}일)`}
                  value={latestHot === null ? "—" : `${Math.round(latestHot * 100)}%`} />
            <Stat label="스캔 이력" value={`${points.length}건`} />
          </dl>
          <section aria-labelledby={sizeHeadId} className="min-w-0 space-y-2">
            <SectionHead id={sizeHeadId} title="파일 크기 분포" help={SIZE_HELP}>
              {sizeTotal !== null && sizeTotal > 0 && (
                <span className="text-xs text-muted">{`최신 scan · 파일 ${sizeTotal.toLocaleString("ko-KR")}개`}</span>
              )}
            </SectionHead>
            <SizeDistribution buckets={snapshot?.file_size_histogram} labelledBy={sizeHeadId}
                              missingText={snapshot?.report_readable === false
                                ? "최신 scan 의 리포트를 읽지 못해 크기 분포를 알 수 없습니다"
                                : "크기 분포 기록이 없습니다(구형 리포트)"} />
          </section>
        </div>

        <section className="space-y-2">
          <SectionHead title="실 사용량 추이"
                       help="점 = 성공 scan 1회 — 누르면 그 요청 상세로 갑니다. x 축은 시간 비례라 점 간격이 곧 스캔 간격입니다.">
            {history.data?.window_full === true && (
              <span className="text-xs text-muted">이전 이력 있음</span>
            )}
            <Segmented label="이력 표시 창" options={HISTORY_WINDOWS} value={windowLimit} onChange={setWindowLimit} />
          </SectionHead>
          <TimeSeriesChart
            label="실 사용량 추이"
            points={charted.map((c) => ({
              t: c.epoch, y: c.bytes,
              // aria-label(폴백)은 한 줄, 호버 툴팁은 구조화 3행(시간·요청자·용량)
              label: `${kstStampEpoch(c.epoch)} · ${humanBytes(c.bytes)}`
                + (c.point.requester ? ` · ${c.point.requester}` : ""),
              tooltip: [
                { k: "시간", v: kstStampEpoch(c.epoch) },
                { k: "요청자", v: c.point.requester ?? "—" },
                { k: "실 사용량", v: humanBytes(c.bytes) },
              ],
            }))}
            formatY={humanBytes} formatX={kstStampEpoch}
            emptyText="용량을 아는 포인트가 없습니다"
            onPointClick={(i) => navigate(`/jobs/${charted[i].point.request_id}`)} />
          {(unknownCount > 0 || skipped > 0) && (
            <p className="text-xs text-muted">
              {unknownCount > 0 && `용량 미상 ${unknownCount}건은 차트에서 제외. `}
              {skipped > 0 && `리포트를 읽지 못한 스캔 ${skipped}건 제외.`}
            </p>
          )}
        </section>

        <section className="space-y-3">
          <SectionHead title="데이터 온도 추이" help={TEMP_CAPTIONS[effectiveTempKey]}>
            {tempKeys.length > 0 && (
              <Segmented label="온도 축" options={tempKeys.map((k) => ({ value: k, label: k }))}
                         value={effectiveTempKey} onChange={setTempKey} />
            )}
          </SectionHead>
          <TemperatureTrend points={points} tempKey={effectiveTempKey} />
        </section>

        {/* 이력 표는 기본 접힘 -- 차트 점이 이미 요청 상세로 가는 동선이고, 30건이 늘 펼쳐져 상세 높이를 세 배로 키웠다. */}
        <details className="group">
          <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded text-sm font-medium
                              text-ink hover:text-accent focus-visible:outline-none focus-visible:ring-2
                              focus-visible:ring-accent [&::-webkit-details-marker]:hidden">
            <ChevronRight aria-hidden className="h-4 w-4 text-muted transition-transform group-open:rotate-90" />
            {`스캔 이력 ${points.length}건`}
          </summary>
          <div className="mt-2">
            <Table>
              <thead><tr className="text-muted">
                {/* nowrap: 좁은 폭에서 「파일 수」가 세로로 꺾여 표가 흔들린다 */}
                <th className="py-2 pr-4 text-xs font-medium whitespace-nowrap">시각</th>
                <th className="pr-4 text-right text-xs font-medium whitespace-nowrap">실 사용량</th>
                <th className="pr-4 text-right text-xs font-medium whitespace-nowrap">파일 수</th>
                <th className="pr-4 text-right text-xs font-medium whitespace-nowrap">hot 비율</th>
                <th className="pr-4 text-xs font-medium whitespace-nowrap">요청자</th>
                <th className="text-xs font-medium whitespace-nowrap">요청</th>
              </tr></thead>
              <tbody>
                {[...points].reverse().map((p) => {
                  const epoch = pointEpoch(p);
                  const hot = hotRatio(p.time_histograms["atime"]);
                  const files = p.summary["total_files"];
                  return (
                    <tr key={p.job_id} className="border-t border-line">
                      <td className="py-1.5 pr-4 whitespace-nowrap tabular-nums">
                        {epoch === null ? "—" : kstStampEpoch(epoch)}
                      </td>
                      <td className="pr-4 text-right whitespace-nowrap tabular-nums">
                        {p.total_bytes === null ? "—" : humanBytes(p.total_bytes)}
                      </td>
                      <td className="pr-4 text-right tabular-nums">
                        {typeof files === "number" ? files.toLocaleString("ko-KR") : "—"}</td>
                      <td className="pr-4 text-right tabular-nums">{hot === null ? "—" : `${Math.round(hot * 100)}%`}</td>
                      <td className="pr-4 text-muted">{p.requester ?? "—"}</td>
                      <td><Link className="font-mono text-xs text-accent hover:underline" to={`/jobs/${p.request_id}`}>
                        {p.request_id.slice(0, 12)}
                      </Link></td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          </div>
        </details>
      </>)}
    </div>
  );
}
