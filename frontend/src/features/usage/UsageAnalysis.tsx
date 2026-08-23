import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Card } from "../../components/ui/Card";
import { MetricTile } from "../../components/ui/MetricTile";
import { Table } from "../../components/ui/Table";
import { TimeSeriesChart } from "../../components/ui/TimeSeriesChart";
import { Button } from "../../components/ui/Button";
import type { ApiError } from "../../lib/api";
import type { HistogramBucket, UsagePoint } from "../../lib/types";
import { useScanHistory, useScanTargets } from "./useUsage";

// NodesList/JobStats/RequestDetail 의 humanBytes 국소 사본 관례.
function humanBytes(bytes: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"];
  let v = bytes, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i += 1; }
  return `${i === 0 ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

// BatchDetail utcStamp 국소 사본(UTC 고정 -- 로컬시간 표기는 운영자·사용자가
// 다른 시각을 말하게 한다).
function utcStamp(epoch: number) {
  return `${new Date(epoch * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC`;
}
// 온도 열 축 라벨: MM-DD 만 -- 열 폭(max-w-12)에 "MM-DD HH:MM" 은 잘려서
// "08-23 1…" 이 됐다(실화면 확인). 분 단위는 열 title 툴팁이 든다.
const dayStamp = (epoch: number) =>
  new Date(epoch * 1000).toISOString().slice(5, 10);

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

// hot 비율: 나이 ≤HOT_AGE_MAX_DAYS 버킷 bytes / 전체 bytes. 전체 0 은 비율 정의
// 불가(null -- cumulativeLayout 의 0-나눗셈 규약). bytes 가 실린 버킷의 나이 필드
// 결측도 null 이다 -- 그 바이트를 hot 도 cold 도 아니라고 말할 근거가 없는데
// cold 로 접으면(분모에만 넣으면) 비율이 조용히 내려간다(모름 ≠ 0, 리뷰).
export function hotRatio(buckets: HistogramBucket[] | undefined): number | null {
  if (!buckets || buckets.length === 0) return null;
  let hot = 0, total = 0;
  for (const b of buckets) {
    if (typeof b.bytes !== "number") return null;
    if (b.bytes > 0 && typeof b.max_age_days !== "number") return null;
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
  return (vals as number[]).map((v) => ({ pct: (v / total) * 100 }));
}

// BatchDetail TEMP_PALETTE/tempColorOf 국소 사본(hot→cold 비례 사상).
const TEMP_PALETTE = ["#dc2626", "#ea580c", "#f59e0b", "#eab308", "#84cc16",
                      "#22c55e", "#06b6d4", "#3b82f6", "#6366f1"];
const tempColorOf = (n: number) => (i: number) =>
  TEMP_PALETTE[n <= 1 ? 0 : Math.round((i / (n - 1)) * (TEMP_PALETTE.length - 1))];

const TEMP_CAPTIONS: Record<string, string> = {
  atime: "각 열 = 스캔 1회. 위(빨강)=hot·최근 접근, 아래(파랑)=cold — atime 기준 용량 비중. relatime/open_noatime 환경에선 근사",
  mtime: "각 열 = 스캔 1회. 위(빨강)=최근 수정, 아래(파랑)=오래됨 — mtime 기준 용량 비중",
  ctime: "각 열 = 스캔 1회. 위(빨강)=최근 변경, 아래(파랑)=오래됨 — ctime 기준 용량 비중",
};

// 온도 추이: 스캔별 100% 스택 열(위=hot). 시간순 정렬은 부모(서버 정렬)가 보장.
function TemperatureTrend({ points, tempKey }: {
  points: UsagePoint[]; tempKey: string;
}) {
  const cols = points.map((p) => ({
    epoch: pointEpoch(p),
    stack: stackLayout(p.time_histograms[tempKey]),
  }));
  if (cols.every((c) => c.stack === null)) {
    return <p className="text-muted text-xs">이 축의 온도 분포가 있는 스캔이 없습니다</p>;
  }
  return (
    // 고밀도(>20열)는 gap 을 줄인다 -- 고정 gap 합이 컨테이너를 넘으면 가로
    // 스크롤(e2e L1 금지)이 생긴다. 열 자체는 flex-1+min-w-0 으로 줄어든다.
    <div role="img" aria-label={`데이터 온도 추이(${tempKey})`}
         className={`flex items-end ${cols.length > 20 ? "gap-0.5" : "gap-1.5"}`}>
      {cols.map((c, i) => (
        <div key={i}
             title={c.epoch === null ? undefined : utcStamp(c.epoch)}
             className="flex min-w-0 max-w-12 flex-1 flex-col items-center gap-1">
          {c.stack === null ? (
            // 분포 없음(빈 트리·구형 리포트) -- 0% 스택으로 그리면 거짓이라 빈 트랙
            <div title="온도 분포 없음"
                 className="h-24 w-full max-w-5 rounded-sm bg-accent/10" />
          ) : (
            <div className="flex h-24 w-full max-w-5 flex-col overflow-hidden rounded-sm">
              {c.stack.map((s, bi) => (
                <div key={bi} style={{ height: `${s.pct}%`,
                                       backgroundColor: tempColorOf(c.stack!.length)(bi) }} />
              ))}
            </div>
          )}
          <span className="w-full truncate text-center text-[10px] text-muted">
            {c.epoch === null ? "—" : dayStamp(c.epoch)}
          </span>
        </div>
      ))}
    </div>
  );
}

export function UsageAnalysis() {
  const navigate = useNavigate();
  // 선택 타깃은 URL 이 진실이다 -- 새로고침·딥링크에서 같은 화면이 나온다.
  const [params, setParams] = useSearchParams();
  const storage = params.get("storage");
  const target = params.get("target");
  const [input, setInput] = useState("");
  const [q, setQ] = useState("");
  // 검색 디바운스 300ms: 타이핑마다 GROUP BY 를 쏘지 않는다.
  useEffect(() => {
    const t = setTimeout(() => setQ(input.trim()), 300);
    return () => clearTimeout(t);
  }, [input]);

  const targets = useScanTargets(q);
  const history = useScanHistory(storage, target);

  const select = (s: string, t: string) =>
    setParams({ storage: s, target: t });

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
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">사용량 분석</h1>
      <p className="text-muted text-sm">
        디렉터리별 scan 이력의 실 사용량·데이터 온도 변화 — 요청자와 무관하게
        모든 성공 scan 을 모아 봅니다.
      </p>
      <Card>
        <div className="mb-3 flex items-center gap-2">
          <input value={input} onChange={(e) => setInput(e.target.value)}
                 aria-label="경로 검색"
                 placeholder="스토리지·경로 검색…"
                 className="w-72 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm" />
          {targets.isFetching && <span className="text-muted text-xs">검색 중…</span>}
        </div>
        {targets.isLoading ? <p className="text-muted">불러오는 중…</p>
         : targets.isError ? <p className="text-bad text-sm">{(targets.error as ApiError).message}</p>
         : (targets.data ?? []).length === 0 ? (
          <p className="text-muted text-sm">
            {q ? "검색과 일치하는 scan 타깃이 없습니다" : "성공한 scan 이 아직 없습니다"}
          </p>
        ) : (
          <Table>
            <thead><tr className="text-muted">
              <th className="py-2">스토리지</th><th>경로</th>
              <th>스캔 횟수</th><th>최근 스캔</th>
            </tr></thead>
            <tbody>
              {(targets.data ?? []).map((r) => {
                const active = r.storage_name === storage && r.target === target;
                return (
                  <tr key={`${r.storage_name}:${r.target}`}
                      className={`border-t border-black/5 ${active ? "bg-accent/5" : ""}`}>
                    <td className="py-2 text-muted">{r.storage_name}</td>
                    {/* 선택은 버튼이다 -- 행 onClick 만 두면 키보드로 못 고른다 */}
                    <td><button type="button"
                                className={`text-left ${active ? "text-accent font-medium" : "text-accent"}`}
                                onClick={() => select(r.storage_name, r.target)}>
                      {r.target}
                    </button></td>
                    <td>{r.scan_count}</td>
                    <td className="text-muted whitespace-nowrap">{r.last_scan_at ?? "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        )}
      </Card>

      {storage !== null && target !== null && (
        <Card>
          <h2 className="text-lg font-semibold">
            {storage}<span className="text-muted">:</span>{target}
          </h2>
          {history.isLoading ? <p className="text-muted mt-2">이력 불러오는 중…</p>
           : history.isError ? <p className="text-bad text-sm mt-2">{(history.error as ApiError).message}</p>
           : points.length === 0 ? (
            <p className="text-muted text-sm mt-2">이 타깃의 성공 scan 이 없습니다</p>
          ) : (<>
            {/* 창 절단 고지(리뷰): 이 화면의 이력은 최신 창이다 -- 타깃 목록의
                스캔 횟수(전수)와 다를 수 있음을 화면이 먼저 말한다. */}
            {history.data?.window_full === true && (
              <p className="text-muted text-xs mt-1">
                최근 {history.data.window_limit}건 창 기준 — 이 타깃의 전체 스캔
                이력이 아닐 수 있습니다(목록의 스캔 횟수는 전수).
              </p>
            )}
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
                label: `${utcStamp(c.epoch)} · ${humanBytes(c.bytes)}`
                  + (c.point.requester ? ` · ${c.point.requester}` : ""),
              }))}
              formatY={humanBytes} formatX={utcStamp}
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
                        {epoch === null ? "—" : utcStamp(epoch)}
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
        </Card>
      )}
    </section>
  );
}
