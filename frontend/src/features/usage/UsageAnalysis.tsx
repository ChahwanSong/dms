import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ArrowDown, ArrowUp, ChevronRight, Download } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { MetricTile } from "../../components/ui/MetricTile";
import { Table } from "../../components/ui/Table";
import { TimeSeriesChart } from "../../components/ui/TimeSeriesChart";
import { Button } from "../../components/ui/Button";
import { HoverTip, placeBeside, useHoverAnchor, type Rect } from "../../components/ui/HoverTip";
import type { ApiError } from "../../lib/api";
import type { HistogramBucket, UsagePoint } from "../../lib/types";
import { agoText, ageDays, kstStampEpoch, kstDay, kstStampOrDash } from "../../lib/datetime";
import { downloadCsv, toCsv } from "../../lib/csvExport";
import { useStorages } from "../storages/useStorages";
import { fetchUsageExport, useScanHistory, useScanStorages, useScanTargets, type TargetFilter } from "./useUsage";
import {
  AXIS_MEANING, HOT_AGE_MAX_DAYS, ageLabel, hotRatio, humanBytes, pointEpoch, stackLayout, tempColorOf,
} from "./usageStats";
import { buildUsageCsv, usageCsvFilename } from "./usageCsv";

// 순수 규칙은 usageStats(화면·CSV 공용 한 벌, 2026-10-02 분리) -- 기존 import 경로 호환을 위해 다시 내보낸다.
export { HOT_AGE_MAX_DAYS, hotRatio, pointEpoch, stackLayout } from "./usageStats";

// 절대 시각은 공유 datetime 헬퍼(KST)로 통일했다 -- kstStampEpoch(초 -> KST 벽시계),
// kstDay(초 -> KST MM-DD). 온도 열 축 라벨은 폭(max-w-12) 때문에 MM-DD 만 쓰고
// 분 단위는 툴팁이 든다(실화면 확인).

// 이력 표시 창 선택지(사용자 요청 2026-08-24). 서버 상한 60(routes_usage
// _MAX_POINTS)과 발맞춘 고정 선택지 -- 자유 입력 없음(WindowSelect 관례).
const HISTORY_WINDOWS = [30, 60] as const;

const TEMP_CAPTIONS: Record<string, string> = {
  atime: "각 열 = 스캔 1회. 위(빨강)=hot·최근 접근, 아래(파랑)=cold — atime 기준 용량 비중. relatime/open_noatime 환경에선 근사. 열에 마우스를 올리면 구간별 용량",
  mtime: "각 열 = 스캔 1회. 위(빨강)=최근 수정, 아래(파랑)=오래됨 — mtime 기준 용량 비중. 열에 마우스를 올리면 구간별 용량",
  ctime: "각 열 = 스캔 1회. 위(빨강)=최근 변경, 아래(파랑)=오래됨 — ctime 기준 용량 비중. 열에 마우스를 올리면 구간별 용량",
};

// 최근 스캔 경과 색(2026-10-02 사용자 결정): 30일 지나면 주황, 90일 지나면 빨강. 모름은 기본색.
export function staleClass(days: number | null): string {
  if (days === null) return "text-muted";
  if (days >= 90) return "text-bad font-medium";
  if (days >= 30) return "text-orange-600 font-medium";
  return "text-ink";
}

const pctText = (p: number) =>
  p === 0 ? "0%" : p < 0.1 ? "<0.1%" : p < 10 ? `${p.toFixed(1)}%` : `${Math.round(p)}%`;

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
  // 비율 두 가지(리뷰): 지금 보는 축의 180일 이내 비중, 그리고 목록·타일·CSV 와 같은 정의의 hot 비율(atime).
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
  const cols = points.map((p) => ({
    epoch: pointEpoch(p),
    buckets: p.time_histograms[tempKey] ?? [],
    stack: stackLayout(p.time_histograms[tempKey]),
    point: p,
  }));
  if (cols.every((c) => c.stack === null)) {
    return <p className="text-muted text-xs">이 축의 온도 분포가 있는 스캔이 없습니다</p>;
  }
  const n = cols.length;
  // 창 전환(30↔60)·재조회로 열이 줄면 사라진 열은 mouseleave 를 내지 않는다 -- 범위 밖이면 툴팁 없음(BarChart 와 같은 가드).
  const hovered = active !== null && active.key < n ? active : null;
  return (
    <div>
      {/* 고밀도(>20열)는 gap 을 줄인다 -- 고정 gap 합이 컨테이너를 넘으면 가로
          스크롤(e2e L1 금지)이 생긴다. 열 자체는 flex-1+min-w-0 으로 줄어든다. */}
      <div role="img" aria-label={`데이터 온도 추이(${tempKey})`}
           className={`flex items-end ${n > 20 ? "gap-0.5" : "gap-1.5"}`}>
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
            <span className="w-full truncate text-center text-[10px] text-muted">
              {c.epoch === null ? "—" : kstDay(c.epoch)}
            </span>
          </button>
        ))}
      </div>
      {hovered !== null && (
        <TempTooltip point={cols[hovered.key].point} buckets={cols[hovered.key].buckets}
                     tempKey={tempKey} anchor={hovered.rect} onClose={close} />
      )}
    </div>
  );
}

// 타깃 1건의 상세(요약 타일·실 사용량 추이·온도 추이·스캔 이력). 2026-09-14 부터
// 목록의 **선택한 행 바로 아래에 펼쳐진다**(expand). 표시 창·온도 축 state 는 여기
// 국소라 타깃을 바꾸면 초기화된다 -- 다른 타깃의 잔류 선택이 새 타깃에 묻어 가지
// 않는다. showTitle 은 목록 밖 폴백 카드(선택 타깃이 현재 목록에 없을 때)에서만 켠다.
function TargetDetail({ storage, target, showTitle }: {
  storage: string; target: string; showTitle: boolean;
}) {
  const navigate = useNavigate();
  const [windowLimit, setWindowLimit] = useState<number>(HISTORY_WINDOWS[0]);
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

  const latest = charted.length > 0 ? charted[charted.length - 1] : null;
  const prev = charted.length > 1 ? charted[charted.length - 2] : null;
  const delta = latest !== null && prev !== null ? latest.bytes - prev.bytes : null;
  const latestHot = latest !== null
    ? hotRatio(latest.point.time_histograms["atime"]) : null;

  const [tempKey, setTempKey] = useState("atime");
  const tempKeys = ["atime", "mtime", "ctime"].filter(
    (k) => points.some((p) => p.time_histograms[k]?.length));
  // 선택 축이 이 타깃에 없으면(atime 없는 이력·타깃 전환 잔류 -- 리뷰 확인)
  // 첫 가용 축으로 대체한다. state 를 직접 고치는 useEffect 대신 파생값:
  // 렌더 한 번으로 정합하고, 사용자가 고른 값은 가용해지는 순간 되살아난다.
  const effectiveTempKey = tempKeys.includes(tempKey)
    ? tempKey : (tempKeys[0] ?? tempKey);

  return (
    <div>
      <div className="flex flex-wrap items-center justify-between gap-2">
        {showTitle ? (
          <h2 className="text-lg font-semibold">
            {storage}<span className="text-muted">:</span>{target}
          </h2>
        ) : (
          // 펼침 행 안에서는 행 자체가 제목이다 -- 창 안내만 왼쪽에 둔다.
          <p className="text-muted text-xs">
            최근 {history.data?.window_limit ?? windowLimit}건까지만 표시합니다
            {history.data?.window_full === true
              && " — 이 타깃은 그 이전 스캔 이력도 있습니다(목록의 스캔 횟수가 전수)"}
          </p>
        )}
        {/* 표시 창 선택(WindowSelect 시각 관례의 국소판 -- 그쪽은 시간 단위라
            재사용 대신 건수 선택지로 새로 둔다). */}
        <div className="flex items-center gap-1" role="group"
             aria-label="이력 표시 창">
          {HISTORY_WINDOWS.map((n) => (
            <button key={n} type="button"
                    onClick={() => setWindowLimit(n)}
                    className={`rounded px-2 py-1 text-xs border ${
                      windowLimit === n
                        ? "font-semibold border-black/30"
                        : "text-muted border-black/10"}`}>
              최근 {n}건
            </button>
          ))}
        </div>
      </div>
      {/* 상시 창 안내(사용자 요청 2026-08-24): 조건부(창 꽉 참)로만 말하면
          "이게 전체 이력"이라는 오독이 기본값이 된다 -- 항상 말한다(제목형은 여기). */}
      {showTitle && (
        <p className="text-muted text-xs mt-1">
          최근 {history.data?.window_limit ?? windowLimit}건까지만 표시합니다
          {history.data?.window_full === true
            && " — 이 타깃은 그 이전 스캔 이력도 있습니다(목록의 스캔 횟수가 전수)"}
        </p>
      )}
      {history.isLoading ? <p className="text-muted mt-2">이력 불러오는 중…</p>
       : history.isError ? <p className="text-bad text-sm mt-2">{(history.error as ApiError).message}</p>
       : points.length === 0 ? (
        <p className="text-muted text-sm mt-2">이 타깃의 성공 scan 이 없습니다</p>
      ) : (<>
        <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
          {/* null ≠ 0: 모름은 "—" -- 0 B 로 그리면 "다 지워졌다"는 거짓이 된다 */}
          <MetricTile label="최신 실 사용량"
                      value={latest !== null ? humanBytes(latest.bytes) : "—"} />
          <MetricTile label="직전 스캔 대비"
                      value={delta === null ? "—"
                        : `${delta >= 0 ? "+" : "−"}${humanBytes(Math.abs(delta))}`} />
          <MetricTile label={`hot 비율(atime ≤${HOT_AGE_MAX_DAYS}d)`}
                      value={latestHot === null ? "—" : `${Math.round(latestHot * 100)}%`} />
          <MetricTile label="스캔 이력 수" value={points.length} />
        </div>

        <h3 className="mt-5 font-semibold text-sm">실 사용량 추이</h3>
        <p className="text-muted text-xs mb-2">
          점 = 성공 scan 1회(클릭하면 해당 요청 상세). x 축은 시간 비례 —
          점 간격이 곧 스캔 간격입니다.
        </p>
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
        {(unknownCount > 0 || (history.data?.skipped_unreadable ?? 0) > 0) && (
          <p className="text-muted text-xs mt-1">
            {unknownCount > 0 && `용량 미상 ${unknownCount}건은 차트에서 제외. `}
            {(history.data?.skipped_unreadable ?? 0) > 0
              && `리포트를 읽지 못한 스캔 ${history.data!.skipped_unreadable}건 제외.`}
          </p>
        )}

        <h3 className="mt-5 font-semibold text-sm">데이터 온도 추이</h3>
        <div className="my-2 flex gap-2">
          {tempKeys.map((k) => (
            <Button key={k} type="button"
                    variant={effectiveTempKey === k ? "outline" : "ghost"}
                    onClick={() => setTempKey(k)}>{k}</Button>
          ))}
        </div>
        <TemperatureTrend points={points} tempKey={effectiveTempKey} />
        {TEMP_CAPTIONS[effectiveTempKey] && (
          <p className="text-muted text-xs mt-2">{TEMP_CAPTIONS[effectiveTempKey]}</p>
        )}

        <h3 className="mt-5 font-semibold text-sm">스캔 이력</h3>
        <Table>
          <thead><tr className="text-muted">
            {/* nowrap: 좁은 폭에서 「파일 수」가 세로로 꺾여 표가 흔들린다 */}
            <th className="py-2 whitespace-nowrap">시각</th>
            <th className="whitespace-nowrap">실 사용량</th>
            <th className="whitespace-nowrap">파일 수</th>
            <th className="whitespace-nowrap">hot 비율</th>
            <th className="whitespace-nowrap">요청자</th><th>요청</th>
          </tr></thead>
          <tbody>
            {[...points].reverse().map((p) => {
              const epoch = pointEpoch(p);
              const hot = hotRatio(p.time_histograms["atime"]);
              return (
                <tr key={p.job_id} className="border-t border-black/5">
                  <td className="py-2 whitespace-nowrap">
                    {epoch === null ? "—" : kstStampEpoch(epoch)}
                  </td>
                  <td className="whitespace-nowrap">
                    {p.total_bytes === null ? "—" : humanBytes(p.total_bytes)}
                  </td>
                  <td>{p.summary["total_files"] ?? "—"}</td>
                  <td>{hot === null ? "—" : `${Math.round(hot * 100)}%`}</td>
                  <td className="text-muted">{p.requester ?? "—"}</td>
                  <td><Link className="text-accent" to={`/jobs/${p.request_id}`}>
                    {p.request_id.slice(0, 12)}
                  </Link></td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </>)}
    </div>
  );
}

const DETAIL_ID = "usage-target-detail";

export function UsageAnalysis() {
  // 선택 타깃은 URL 이 진실이다 -- 새로고침·딥링크에서 같은 화면이 나온다.
  const [params, setParams] = useSearchParams();
  const storage = params.get("storage");
  const target = params.get("target");
  // 필터(2026-10-02 사용자 요청 "스토리지 또는 경로 → 두 개 다"): 스토리지(정확히 일치)와 경로(부분 문자열)가
  // 함께 걸린다. 경로 입력은 300ms 디바운스 -- 타이핑마다 GROUP BY 를 쏘지 않는다.
  const [storageFilter, setStorageFilter] = useState("");
  const [input, setInput] = useState("");
  const [pathQ, setPathQ] = useState("");
  // 최근 스캔 정렬(2026-10-02): 기본 내림차순(최근 것 위). 서버가 limit 전에 정렬한다(useUsage 주석).
  const [order, setOrder] = useState<"desc" | "asc">("desc");
  useEffect(() => {
    const t = setTimeout(() => setPathQ(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);
  const filter: TargetFilter = { storage: storageFilter, path: pathQ, order };

  const targets = useScanTargets(filter);
  const storagesQ = useStorages();
  const scanStoragesQ = useScanStorages();
  // 선택지 = 등록 스토리지 ∪ 스캔 기록이 남은 이름(삭제·개명된 스토리지의 이력도 좁힐 수 있게, 리뷰). 미등록은 표시.
  const registered = new Set((storagesQ.data ?? []).map((s) => s.storage_name));
  const storageOptions = [...new Set([...registered, ...(scanStoragesQ.data ?? [])])].sort();
  const rows = targets.data ?? [];
  const now = Date.now();
  const activeKey = storage !== null && target !== null ? `${storage}:${target}` : null;
  // 펼침은 한 번에 하나(사용자 요청 2026-09-14): 같은 행 재클릭 = 접기(URL 파라미터
  // 제거), 다른 행 클릭 = 펼침 이동. URL 이 진실이라 뒤로가기·새로고침도 같은 상태.
  const toggle = (s: string, t: string) =>
    (s === storage && t === target) ? setParams({}) : setParams({ storage: s, target: t });
  // 딥링크·검색 필터로 선택 타깃 행이 지금 목록에 없으면 펼칠 행이 없다 -- 표 아래
  // 카드로 폴백해 링크가 죽지 않게 한다(목록 로딩 중엔 깜빡임 방지로 보류).
  const listed = rows.some((r) => `${r.storage_name}:${r.target}` === activeKey);
  const fallback = activeKey !== null && !targets.isLoading && !listed;
  const filtered = storageFilter !== "" || pathQ !== "";
  const COLS = 7;

  // CSV 내보내기(2026-10-02 "사용량 분석 전체의 모든 정보, 항목당 한 줄"): 지금 필터·정렬 그대로, 행 상한 없이
  // (서버 상한을 넘으면 잘렸다고 말한다). 조립은 usageCsv(화면과 같은 hot 비율·구간 규칙).
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const exportCsv = async () => {
    setExporting(true); setExportMsg(null);
    try {
      const body = await fetchUsageExport(filter);
      const at = Date.now();
      const { columns, records } = buildUsageCsv(body.rows, storagesQ.data, at);
      downloadCsv(usageCsvFilename(at), toCsv(columns, records));
      setExportMsg({ ok: true, text: body.truncated
        ? `상한 ${body.count.toLocaleString("ko-KR")}개까지만 내보냈습니다 — 필터로 범위를 좁히세요`
        : `${body.count.toLocaleString("ko-KR")}개 항목을 내보냈습니다` });
    } catch (e) {
      setExportMsg({ ok: false, text: (e as ApiError).message ?? "내보내기에 실패했습니다" });
    } finally {
      setExporting(false);
    }
  };

  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">사용량 분석</h1>
      <p className="text-muted text-sm">
        디렉터리별 scan 이력의 실 사용량·데이터 온도 변화 — 요청자와 무관하게
        모든 성공 scan 을 모아 봅니다. 경로를 클릭하면 그 아래에 상세가 펼쳐집니다.
      </p>
      <Card>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <select aria-label="스토리지 필터" value={storageFilter}
                  onChange={(e) => setStorageFilter(e.target.value)}
                  className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm">
            <option value="">전체 스토리지</option>
            {storageOptions.map((name) => (
              <option key={name} value={name}>
                {registered.has(name) || storagesQ.data === undefined ? name : `${name} (미등록)`}</option>
            ))}
          </select>
          <input value={input} onChange={(e) => setInput(e.target.value)}
                 aria-label="경로 검색"
                 placeholder="경로 검색…"
                 className="w-72 max-w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm" />
          {targets.isFetching && <span className="text-muted text-xs">검색 중…</span>}
          <Button type="button" variant="outline" className="ml-auto" disabled={exporting}
                  onClick={() => { void exportCsv(); }}>
            <Download aria-hidden className="mr-1 inline h-4 w-4" />
            {exporting ? "내보내는 중…" : "CSV 내보내기"}
          </Button>
        </div>
        {exportMsg && (
          <p role={exportMsg.ok ? "status" : "alert"}
             className={`mb-2 text-sm ${exportMsg.ok ? "text-muted" : "text-bad"}`}>{exportMsg.text}</p>
        )}
        {targets.isLoading ? <p className="text-muted">불러오는 중…</p>
         : targets.isError ? <p className="text-bad text-sm">{(targets.error as ApiError).message}</p>
         : rows.length === 0 ? (
          <p className="text-muted text-sm">
            {filtered ? "검색과 일치하는 scan 타깃이 없습니다" : "성공한 scan 이 아직 없습니다"}
          </p>
        ) : (
          <Table>
            <thead><tr className="text-muted">
              <th className="py-2">스토리지</th><th>경로</th>
              {/* 2026-10-02 목록 컬럼: 최신 성공 scan 기준(서버 latest). 모름은 "—"(null≠0). */}
              <th className="whitespace-nowrap text-right">실 사용량</th>
              <th className="whitespace-nowrap text-right">파일 수</th>
              <th className="whitespace-nowrap text-right" title={`atime 기준 ${HOT_AGE_MAX_DAYS}일 이내 용량 비중`}>
                {`hot 비율(${HOT_AGE_MAX_DAYS}일)`}</th>
              <th className="whitespace-nowrap text-right">스캔 횟수</th>
              <th className="whitespace-nowrap" aria-sort={order === "desc" ? "descending" : "ascending"}>
                {/* 정렬 토글(오름·내림). 서버가 정렬하므로 limit 밖의 오래된 타깃도 오름차순에서 보인다. */}
                <button type="button" aria-label={`최근 스캔 정렬: ${order === "desc" ? "최신순" : "오래된순"}`}
                        onClick={() => setOrder((o) => (o === "desc" ? "asc" : "desc"))}
                        className="inline-flex items-center gap-1 hover:text-ink">
                  최근 스캔
                  {order === "desc"
                    ? <ArrowDown aria-hidden className="h-3.5 w-3.5" />
                    : <ArrowUp aria-hidden className="h-3.5 w-3.5" />}
                </button>
              </th>
            </tr></thead>
            <tbody>
              {rows.map((r) => {
                const key = `${r.storage_name}:${r.target}`;
                const active = key === activeKey;
                const p = r.latest ?? null;
                const files = p?.summary?.["total_files"];
                const hot = hotRatio(p?.time_histograms?.["atime"]);
                const age = ageDays(r.last_scan_at, now);
                return (
                  <Fragment key={key}>
                    <tr className={`border-t border-black/5 ${active ? "bg-accent/5" : ""}`}>
                      <td className="py-2 text-muted">{r.storage_name}</td>
                      {/* 선택은 버튼이다 -- 행 onClick 만 두면 키보드로 못 고른다.
                          aria-expanded/controls 로 "펼침 버튼"임을 보조기기에 알린다. */}
                      <td><button type="button"
                                  aria-expanded={active}
                                  aria-controls={active ? DETAIL_ID : undefined}
                                  className={`inline-flex items-center gap-1 text-left ${
                                    active ? "text-accent font-medium" : "text-accent"}`}
                                  onClick={() => toggle(r.storage_name, r.target)}>
                        <ChevronRight aria-hidden="true"
                                      className={`h-3.5 w-3.5 shrink-0 transition-transform ${
                                        active ? "rotate-90" : ""}`} />
                        {r.target}
                      </button></td>
                      <td className="whitespace-nowrap text-right tabular-nums"
                          title={typeof p?.total_bytes === "number"
                            ? `${p.total_bytes.toLocaleString("ko-KR")} B` : undefined}>
                        {typeof p?.total_bytes === "number" ? humanBytes(p.total_bytes) : "—"}
                        {/* 리포트를 못 읽은 최신 스캔: 용량은 러너가 DB 에 남긴 값(같은 규칙)이고 파일 수·hot 은 모름이다.
                            펼친 상세의 "최신" 타일은 읽을 수 있는 리포트 기준이라 다를 수 있다 -- 그 사실을 표시한다(리뷰). */}
                        {p?.report_readable === false && typeof p.total_bytes === "number" && (
                          <span className="ml-1 text-[10px] text-muted"
                                title="최신 스캔 리포트를 읽지 못해 잡 기록(DB)의 용량을 표시합니다">(DB)</span>
                        )}
                      </td>
                      <td className="whitespace-nowrap text-right tabular-nums">
                        {typeof files === "number" ? files.toLocaleString("ko-KR") : "—"}
                      </td>
                      <td className="whitespace-nowrap text-right tabular-nums">
                        {hot === null ? "—" : `${Math.round(hot * 100)}%`}
                      </td>
                      <td className="text-right tabular-nums">{r.scan_count}</td>
                      {/* 경과(2026-10-02): 지금부터 얼마나 전인지 + 30일 주황 · 90일 빨강. 정확한 시각은 옆에. */}
                      <td className="whitespace-nowrap">
                        <span className={staleClass(age)} aria-label="최근 스캔 경과">
                          {agoText(r.last_scan_at, now)}</span>
                        <span className="ml-2 text-xs text-muted">{kstStampOrDash(r.last_scan_at)}</span>
                      </td>
                    </tr>
                    {active && (
                      // 펼침 행: 선택 행 바로 아래, 표 전체 폭. 왼쪽 강조선이 "이 행에
                      // 속한 상세"임을 시각적으로 묶는다.
                      <tr className="border-t border-black/5 bg-accent/[0.03]">
                        <td colSpan={COLS} className="p-0">
                          <div id={DETAIL_ID} role="region"
                               aria-label={`${r.storage_name}:${r.target} 상세`}
                               className="border-l-2 border-accent/40 px-4 py-3">
                            <TargetDetail storage={r.storage_name} target={r.target}
                                          showTitle={false} />
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </Table>
        )}
        <p className="mt-2 text-xs text-muted">
          {`실 사용량·파일 수·hot 비율 = 최근 성공 scan 기준(hot = atime ${HOT_AGE_MAX_DAYS}일 이내 용량 비중). `
            + "최근 스캔이 30일 지나면 주황, 90일 지나면 빨강."}
        </p>
      </Card>

      {fallback && storage !== null && target !== null && (
        <Card>
          <p className="text-muted text-xs mb-2">
            선택한 타깃이 현재 목록(검색 결과)에 없어 아래에 표시합니다.
          </p>
          <div id={DETAIL_ID} role="region" aria-label={`${storage}:${target} 상세`}>
            <TargetDetail storage={storage} target={target} showTitle />
          </div>
        </Card>
      )}
    </section>
  );
}
