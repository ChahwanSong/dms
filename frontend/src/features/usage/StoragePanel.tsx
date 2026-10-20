import { useState } from "react";
import { Link } from "react-router-dom";
import { Clock, Download, FolderTree, HardDrive, Layers, ScanSearch, Search, SearchX } from "lucide-react";
import { Button } from "../../components/ui/Button";
import { Skeleton } from "../../components/ui/Skeleton";
import type { ApiError } from "../../lib/api";
import type { Storage } from "../../lib/types";
import { agoText, kstStampOrDash } from "../../lib/datetime";
import { downloadCsv, toCsv } from "../../lib/csvExport";
import { LIST_LIMIT, fetchUsageExport, useScanTargets, usePurgeUnregisteredStorage, type TargetFilter,
         type UsageSort } from "./useUsage";
import { buildUsageCsv, usageCsvFilename } from "./usageCsv";
import { DETAIL_ID, TargetTable } from "./TargetTable";
import { TargetDetail } from "./TargetDetail";
import { UnregisteredBanner } from "./UnregisteredStorages";
import { StorageTotals } from "./StorageTotals";
import { storageLabel, type StorageTab } from "./usageTabs";

// 스토리지 하나의 패널(2026-10-20, 탭 패널 = 카드 하나). 위에서 아래로: 메타 한 줄(마운트 · 타입 · 대상 수 · 최근 스캔) 또는
// 미등록 경고 띠 → 이 스토리지 합계(2026-10-21, StorageTotals) → 툴바(경로 검색 · 건수 · CSV) → 6열 표(행 아래 펼침 상세) →
// 각주 한 줄 → 목록 밖 딥링크 폴백. 페이지가 key={storage} 로 그려 탭을 바꾸면 내보내기 상태가 새로 시작한다. 경로 검색·정렬은 페이지 상태라 탭을 바꿔도 남는다(sync
// 쌍은 같은 트리를 미러하므로 스토리지끼리 같은 경로를 비교하기 쉽다 -- 검색창이 늘 보여 숨은 필터가 되지 않는다).

const METRIC_SORTS: UsageSort[] = ["bytes", "files", "hot"];
const SEP = "\u00a0· ";

// 메타 한 줄: 이름 · 마운트 · 타입 · 대상 수 · 최근 스캔. 항목마다 작은 아이콘 + 글자, 구분 기호 없이 간격만 -- 좁은 폭에서
// 줄이 바뀔 때 「· 최근 스캔」처럼 구분 기호로 시작하는 줄이 생기지 않는다. 이름을 맨 앞에 다시 쓰는 이유: 탭은 긴 이름을
// 말줄임하고(title 은 터치에서 못 본다) 앞부분이 같은 이름끼리는 탭만으로 구별이 안 된다(리뷰).
function MetaLine({ tab, now }: { tab: StorageTab; now: number }) {
  const mount = tab.summary?.mount_path ?? tab.info?.mount_path ?? null;
  const type = tab.summary?.backend_type ?? tab.info?.backend_type ?? null;
  const last = tab.summary?.last_scan_at ?? null;
  const item = (key: string, icon: React.ReactNode, text: string, title?: string) => (
    <span key={key} title={title} className="inline-flex min-w-0 items-center gap-1.5">
      <span aria-hidden className="shrink-0 text-muted/80">{icon}</span>
      <span className="[overflow-wrap:anywhere]">{text}</span>
    </span>
  );
  const ico = "h-3.5 w-3.5";
  const parts = [
    <span key="n" className={`font-medium text-ink [overflow-wrap:anywhere] ${tab.name === "" ? "italic" : ""}`}>
      {storageLabel(tab.name)}</span>,
    mount !== null && item("m", <HardDrive className={ico} />, mount, "마운트 경로"),
    type !== null && item("t", <Layers className={ico} />, type, "스토리지 타입"),
    // 대상 수는 요약이 왔을 때만(모름 ≠ 0). 최근 스캔은 기록이 있을 때만.
    tab.targetCount !== null && item("c", <FolderTree className={ico} />, `대상 ${tab.targetCount.toLocaleString("ko-KR")}개`),
    last !== null && item("l", <Clock className={ico} />, `최근 스캔 ${agoText(last, now)}`, kstStampOrDash(last)),
  ].filter(Boolean);
  return <p className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm text-ink/70">{parts}</p>;
}

// 빈 상태(아이콘 + 한 줄 + 보조 한 줄). 표 자리에 같은 높이감으로.
function Empty({ icon, title, hint, action }: {
  icon: React.ReactNode; title: string; hint?: React.ReactNode; action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-line px-4 py-10 text-center">
      <span aria-hidden className="grid h-10 w-10 place-items-center rounded-full bg-panel text-muted">{icon}</span>
      <p className="text-sm font-medium text-ink break-keep">{title}</p>
      {hint && <p className="max-w-md text-xs text-muted break-keep">{hint}</p>}
      {action}
    </div>
  );
}

export function StoragePanel({
  tab, windowLimit, input, setInput, pathQ, sort, order, onSort, activeTarget, onToggle, storages,
  purge, progress, setProgress, onResultFocus, now,
}: {
  tab: StorageTab;
  windowLimit: number | null;
  input: string; setInput: (s: string) => void; pathQ: string;
  sort: UsageSort; order: "desc" | "asc"; onSort: (c: UsageSort) => void;
  activeTarget: string | null; onToggle: (target: string) => void;
  storages: Storage[] | undefined;
  purge: ReturnType<typeof usePurgeUnregisteredStorage>;
  progress: number | null; setProgress: (n: number | null) => void; onResultFocus: () => void;
  now: number;
}) {
  const storage = tab.name;
  const filter: TargetFilter = { storage, path: pathQ, order, sort };
  const targets = useScanTargets(filter);
  const rows = targets.data ?? [];

  // CSV 내보내기(2026-10-02 "사용량 분석 전체의 모든 정보, 항목당 한 줄" → 2026-10-20 탭 하나씩): 지금 탭의 스토리지 + 경로
  // 검색 + 정렬 그대로, 행 상한 없이(서버 상한을 넘으면 잘렸다고 말한다). 조립은 usageCsv(화면과 같은 hot 비율·구간 규칙).
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const exportCsv = async () => {
    setExporting(true); setExportMsg(null);
    try {
      const body = await fetchUsageExport(filter);
      const at = Date.now();
      const { columns, records } = buildUsageCsv(body.rows, storages, at);
      downloadCsv(usageCsvFilename(at, storage), toCsv(columns, records));
      setExportMsg({ ok: true, text: body.truncated
        ? `상한 ${body.count.toLocaleString("ko-KR")}개까지만 내보냈습니다 — 경로 검색으로 범위를 좁히세요`
        : `${body.count.toLocaleString("ko-KR")}개 항목을 내보냈습니다` });
    } catch (e) {
      setExportMsg({ ok: false, text: (e as ApiError).message ?? "내보내기에 실패했습니다" });
    } finally {
      setExporting(false);
    }
  };

  // 건수 문구: 검색이 없으면 「9개」, 검색 중이면 「‘team’ 2개」, 목록 상한(100)에 닿으면 「상위 100개 표시 · 전체 N개」(전체 =
  // 요약의 대상 수 -- 모르면 생략). 따로 있던 「상위 100개만」 문단을 대신한다.
  const full = rows.length >= LIST_LIMIT;
  const total = tab.targetCount;
  const countText = targets.data === undefined ? null
    : pathQ !== "" ? `‘${pathQ}’ ${full ? `상위 ${LIST_LIMIT}개 표시` : `${rows.length}개`}`
    : full ? `상위 ${LIST_LIMIT}개 표시${total !== null && total > rows.length ? ` · 전체 ${total.toLocaleString("ko-KR")}개` : ""}`
    : `${rows.length}개`;

  // 딥링크·검색으로 선택 타깃 행이 지금 목록에 없으면 펼칠 행이 없다 -- 표 아래로 폴백해 링크가 죽지 않게 한다(목록
  // 로딩 중엔 깜빡임 방지로 보류).
  const listed = activeTarget !== null && rows.some((r) => r.target === activeTarget);
  const fallback = activeTarget !== null && targets.data !== undefined && !targets.isPlaceholderData && !listed;
  const metricSort = METRIC_SORTS.includes(sort);
  // 창 안 정렬 각주: 서버는 경로 검색을 건 **뒤의** 대상에서 창을 만든다. 검색이 없으면 스토리지 대상 수로 확정할 수 있고,
  // 검색 중엔 결과가 목록 상한(100)보다 적으면 창에 닿지 않은 게 확실하다 -- 그 밖(검색 결과가 많음)은 조건부로 말한다(리뷰:
  // 검색으로 좁힌 결과에도 「최근 스캔된 N개 안에서」라고 단정했다).
  const overWindow = windowLimit !== null && total !== null && total > windowLimit;
  const windowNote = !overWindow ? null
    : pathQ === "" ? `최근 스캔된 ${windowLimit!.toLocaleString("ko-KR")}개 안에서 정렬`
    : full ? `검색 결과가 ${windowLimit!.toLocaleString("ko-KR")}개를 넘으면 최근 스캔된 ${windowLimit!.toLocaleString("ko-KR")}개 안에서 정렬`
    : null;
  const failedOnly = tab.unregistered !== null && tab.unregistered.target_count === 0;
  const noRecords = targets.data !== undefined && !targets.isPlaceholderData && rows.length === 0
    && pathQ === "" && input === "";

  return (
    <div className="space-y-4">
      {tab.registered ? <MetaLine tab={tab} now={now} /> : (
        <UnregisteredBanner name={tab.name} row={tab.unregistered} summary={tab.summary} purge={purge} progress={progress}
                            setProgress={setProgress} onResultFocus={onResultFocus} now={now} />
      )}

      {/* 스토리지 합계(2026-10-21) -- 기록이 있다고 알거나(대상 수 > 0) 모를 때(요약 실패 -- null)만. 0 이면 빈 상태가 말한다
          (실패 기록만 남은 미등록 탭도 0). 미등록 탭은 띠 아래 같은 블록(옛 기록의 합계, df 는 없음). */}
      {tab.targetCount !== 0 && <StorageTotals storage={storage} now={now} />}

      {/* 기록이 하나도 없으면(검색 없이 빈 목록) 검색·CSV 는 할 일이 없다 -- 빈 상태 안내만. */}
      {!noRecords && <div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <label className="relative min-w-0 flex-1 basis-full sm:max-w-sm sm:basis-auto">
            <span className="sr-only">경로 검색</span>
            <Search aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
            <input value={input} onChange={(e) => setInput(e.target.value)} type="search"
                   placeholder="이 스토리지에서 경로 검색…"
                   className="w-full rounded-lg border border-line bg-surface py-1.5 pl-8 pr-3 text-sm
                              placeholder:text-muted focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20" />
          </label>
          {/* 검색·정렬을 바꾸는 동안(이전 행이 자리표시) 건수는 옛 목록의 것이라 말하지 않는다. */}
          <span className="whitespace-nowrap text-sm text-muted">
            {targets.isPlaceholderData ? "검색 중…" : countText}
          </span>
          <Button type="button" variant="outline" className="ml-auto gap-1.5 whitespace-nowrap py-1.5"
                  disabled={exporting} onClick={() => { void exportCsv(); }}>
            <Download aria-hidden className="h-4 w-4" />
            {exporting ? "내보내는 중…" : "CSV 내보내기"}
          </Button>
        </div>
        {exportMsg && (
          <p role={exportMsg.ok ? "status" : "alert"}
             className={`mt-2 text-right text-xs ${exportMsg.ok ? "text-muted" : "text-bad"}`}>{exportMsg.text}</p>
        )}
      </div>}

      {targets.isLoading || (targets.data === undefined && !targets.isError) ? (
        <div className="space-y-2.5 py-1">
          <span className="sr-only">불러오는 중…</span>
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}
        </div>
      ) : targets.isError && targets.data === undefined ? (
        <p className="text-sm text-bad">{(targets.error as ApiError).message}</p>
      ) : rows.length === 0 ? (
        pathQ !== "" ? (
          <Empty icon={<SearchX className="h-5 w-5" />} title={`‘${pathQ}’ 검색 결과가 없습니다`}
                 action={<Button type="button" variant="ghost" className="mt-1 py-1.5"
                                 onClick={() => setInput("")}>검색 지우기</Button>} />
        ) : failedOnly ? (
          <Empty icon={<ScanSearch className="h-5 w-5" />} title="성공한 scan 기록이 없습니다"
                 hint="실패·취소된 작업 기록만 남아 있습니다 — 위의 「기록 정리」로 지울 수 있습니다." />
        ) : (
          <Empty icon={<ScanSearch className="h-5 w-5" />} title="이 스토리지에는 아직 성공한 scan 이 없습니다"
                 hint={<>scan 작업이 끝나면 경로별 실 사용량과 데이터 온도가 여기에 쌓입니다.{" "}
                   <Link to="/jobs/new" className="text-accent hover:underline">단일 작업</Link></>} />
        )
      ) : (
        <TargetTable storage={storage} rows={rows} activeTarget={activeTarget} onToggle={onToggle}
                     sort={sort} order={order} onSort={onSort} now={now} />
      )}
      {targets.isError && targets.data !== undefined && (
        <p className="text-sm text-bad">{`목록을 새로 고치지 못했습니다 — ${(targets.error as ApiError).message}`}</p>
      )}

      {/* 각주 한 줄: 값의 기준 + 경과 색 범례(글자색이 곧 범례). 지표 정렬이면 모름의 자리, 그 스토리지의 대상 수가 서버 창
          (window_limit)을 넘으면 창 안에서 정렬한다는 사실 -- 숫자는 서버 값이다(하드코딩한 「5,000」이 상한 변경과 어긋났다).
          구분 기호 앞은 줄바꿈 없는 공백이라 접힌 줄이 「·」로 시작하지 않는다. */}
      {rows.length > 0 && (
        <p className="text-xs text-muted break-keep">
          최근 성공 scan 기준{SEP}최근 스캔 <span className="font-medium text-attn">30일↑</span>
          {SEP}<span className="font-medium text-bad">90일↑</span>
          {metricSort && <>{SEP}<span>값 모름(—)은 맨 뒤</span></>}
          {metricSort && windowNote !== null && <>{SEP}<span>{windowNote}</span></>}
        </p>
      )}

      {fallback && (
        <section className="space-y-3 border-t border-line pt-5">
          <p className="text-xs text-muted break-keep">
            선택한 경로가 지금 목록(검색·상위 {LIST_LIMIT}개)에 없어 아래에 표시합니다.
          </p>
          <div id={DETAIL_ID} role="region" aria-label={`${storage}:${activeTarget} 상세`}>
            <TargetDetail storage={storage} target={activeTarget!} showTitle />
          </div>
        </section>
      )}
    </div>
  );
}
