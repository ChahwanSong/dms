import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ChevronDown } from "lucide-react";
import { useBatch, useConfirmBatch, useRerunFailed, useRequestScanStats,
         useCancelBatch, useRescanBatch, useUpdateBatch, useDeleteBatch,
         useUpdateBatchItem, useDeleteBatchItem, useAddBatchItem,
         useReplaceBatchItems } from "./useBatches";
import { parseItemsCsv, serializeItemsCsv,
         type ScanRow, type SyncRow } from "../../lib/csv";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { StatusPill } from "../../components/ui/StatusPill";
import { BarChart, r2 } from "../../components/ui/BarChart";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";
import { reasonText, ApiError } from "../../lib/api";
import { batchPillVariant } from "../../lib/jobState";
import type { Batch, BatchItem, HistogramBucket } from "../../lib/types";

// NodesList/JobStats/NodeMetrics 의 humanBytes 국소 사본 관례 -- 값이 bytes 대
// 전역(B~TiB)이라 KiB 단을 포함한다(NodeMetricsSection 판과 같은 단위 집합).
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2], ["KiB", 1024],
];
function humanBytes(bytes: number): string {
  for (const [unit, size] of BYTE_UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${Math.round(bytes)} B`;
}

// 투영 버킷 -> BarChart 데이터. 라벨·값이 실존하는 버킷만 그린다 -- 값 없는
// 버킷을 0 으로 그리면 "빈 버킷"(정상값 0)과 "모름"이 섞인다(null≠0).
function toBars(buckets: HistogramBucket[] | undefined,
                key: "bytes" | "count") {
  return (buckets ?? []).flatMap((b) =>
    typeof b.bucket === "string" && typeof b[key] === "number"
      ? [{ label: b.bucket, value: b[key] as number }] : []);
}

// 시간축별 캡션: 색의 의미(왼쪽 빨강=hot, 오른쪽 파랑=cold)를 축마다 그 축의
// 언어로 말한다. atime 만 근사 경고(relatime/open_noatime 는 접근이 atime 을
// 안 갱신할 수 있다) -- mtime/ctime 은 마운트 옵션과 무관해 경고가 거짓이 된다.
const TEMP_CAPTIONS: Record<string, string> = {
  atime: "왼쪽(빨강)=hot·최근 접근, 오른쪽(파랑)=cold — atime 기준 용량 비중. relatime/open_noatime 환경에선 근사",
  mtime: "왼쪽(빨강)=최근 수정, 오른쪽(파랑)=오래됨 — mtime 기준 용량 비중",
  ctime: "왼쪽(빨강)=최근 변경, 오른쪽(파랑)=오래됨 — ctime 기준 용량 비중",
};

// 9버킷 hot→cold 온도 팔레트(웜→쿨 자연 그라디언트). 정적 hex inline style --
// airgap 무관(런타임 외부 로드가 아니라 그냥 번들 안 문자열이다).
const TEMP_PALETTE = ["#dc2626", "#ea580c", "#f59e0b", "#eab308", "#84cc16",
                      "#22c55e", "#06b6d4", "#3b82f6", "#6366f1"];
// 첫 막대=빨강(hot)·끝 막대=파랑(cold)이 막대 수와 무관하게 유지되게 비례 사상
// 한다 -- 팔레트 인덱스 직결이면 9 미만 히스토그램이 전부 웜톤이 된다.
const tempColorOf = (n: number) => (i: number) =>
  TEMP_PALETTE[n <= 1 ? 0 : Math.round((i / (n - 1)) * (TEMP_PALETTE.length - 1))];

/** 리포트 생성 시각은 UTC로만 보여준다(ScanPaths 국소 사본 관례) — 스토리지·잡은
 *  UTC로 기록되고, 로컬시간으로 바꾸면 운영자·사용자가 다른 시각을 말하게 된다. */
function utcStamp(epoch: number) {
  return `${new Date(epoch * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC`;
}

// payload 필드 결손 방어 + 대상 요약: 대시보드 RecentRequestsSection 의 summarize
// 관례 미러(scan/rm: storage:target, sync: src → dst). ?? 로만 접는다 — truthy
// 검사는 ""(빈 경로)를 "모름"으로 뭉갠다.
const part = (v: unknown) => String(v ?? "—");
function summarizeItem(operation: string | undefined,
                       p: Record<string, unknown>): string {
  const body = operation === "sync"
    ? `${part(p.source_storage)}:${part(p.source)} → ${part(p.destination_storage)}:${part(p.destination)}`
    : `${part(p.storage)}:${part(p.target)}`;
  return `${operation ?? "—"} · ${body}`;
}

// 항목 목록 표시 상수: 클라이언트 페이지네이션(20개/페이지)과 상태 필터.
// 서버가 items 전량을 한 응답에 주므로(상세 GET) 클라이언트에서 자르는 것이
// 정직하다 — RecentRequestsSection 의 페이지네이션 관례 미러.
const PAGE_SIZE = 20;
// 필터 축은 항목 상태 실측 6종(Queued/Materialized/Succeeded/Failed/Rejected/
// Cancelled)의 사용자 언어 묶음이다. Cancelled 는 어느 묶음에도 안 넣는다 —
// 취소는 실패도 대기도 아니라(전체에서만 보인다) 묶으면 라벨이 거짓이 된다.
const ITEM_FILTERS: { key: string; label: string; match: (s: string) => boolean }[] = [
  { key: "all", label: "전체", match: () => true },
  { key: "pending", label: "대기", match: (s) => s === "Queued" || s === "Materialized" },
  { key: "failed", label: "실패", match: (s) => s === "Failed" || s === "Rejected" },
  { key: "ok", label: "성공", match: (s) => s === "Succeeded" },
];
// 행 좌측 보더 = 상태색(온도색과 무관한 상태 축 — text-ok/bad 토큰 계열).
// Cancelled·대기는 회색(line) — 취소는 실패가 아니다(batchPillVariant 관례).
const itemEdge = (s: string) =>
  s === "Succeeded" ? "border-ok"
  : s === "Failed" || s === "Rejected" ? "border-bad" : "border-line";

// 진행 요약(사이드 최상단): 성공/실패/나머지 스택 바 — JobStatsSection
// SuccessRateBar 의 스택 미터 패턴 미러(세그먼트 틈 2px, r2 원비율 폭).
// "대기" = 전체−성공−실패(카운터에 안 잡히는 Cancelled 포함 — 배치 행은
// 성공·실패 카운터만 가진다). 전체 0 이면 바 생략 — 0 항목 배치에 0폭 바를
// 그리면 "표본 없음"이 안 보인다(null≠0). 카운트 문구는 정상값 0 으로 유지.
function BatchProgress({ b }: { b: Batch }) {
  const total = b.item_count;
  const ok = b.succeeded_count;
  const bad = b.failed_count;
  const rest = Math.max(0, total - ok - bad);
  return (
    <Card>
      <h2 className="font-medium">진행 요약</h2>
      {/* 한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(getByText 관례) */}
      <p className="text-muted text-sm mt-3 tabular-nums">
        {`성공 ${ok} · 실패 ${bad} · 대기 ${rest} / 전체 ${total}`}
      </p>
      {total > 0 && (
        <div role="img" aria-label="배치 진행"
             className="mt-2 flex h-2 gap-0.5 overflow-hidden rounded-full">
          {ok > 0 && <div className="bg-ok"
                          style={{ width: `${r2((ok / total) * 100)}%` }}
                          title={`성공 ${ok}`} />}
          {bad > 0 && <div className="bg-bad"
                           style={{ width: `${r2((bad / total) * 100)}%` }}
                           title={`실패 ${bad}`} />}
          {rest > 0 && <div className="bg-line"
                            style={{ width: `${r2((rest / total) * 100)}%` }}
                            title={`대기 ${rest}`} />}
        </div>
      )}
    </Card>
  );
}

// 옵션 값 표기: scan/sync 옵션은 평평한 스칼라 맵이라 키=값 칩이 JSON 원문보다
// 읽기 쉽다. 비스칼라 값만 JSON 으로 접는다(방어 — 현행 계약엔 없지만
// [object Object] 를 찍는 것보다 낫다).
const optText = (v: unknown) =>
  typeof v === "object" && v !== null ? JSON.stringify(v) : String(v);

// 실행 설정(읽기 전용, 사이드 패널 칩 행): 접이(details)에서 상시 칩으로 재구성
// — 사이드 패널로 빠져 항목 목록을 밀지 않으므로 접을 이유가 사라졌다. null =
// 정책 기본(null≠0) — 미지정을 0·빈값으로 뭉개지 않고 칩 문구로 명시한다.
// 빈 옵션도 "옵션 없음" 칩 — 아무것도 안 그리면 "모름"처럼 읽힌다(정상값 0).
// root 실행 칩은 헤더 "특권 실행(root)" 문구의 요약 재표기(통일 게이트 후 배치는
// 전부 특권 실행) — 사이드만 보는 시선에도 실행 권한이 보이게 한다.
function BatchSettings({ b }: { b: Batch }) {
  const chip = "inline-flex items-center rounded-full bg-panel px-2.5 py-1 text-xs";
  const opts = Object.entries(b.options ?? {});
  return (
    <Card>
      <h2 className="font-medium">실행 설정</h2>
      {/* 칩 문구는 한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(getByText 관례) */}
      <div className="mt-3 flex flex-wrap gap-2">
        <span className={chip}>{`우선순위 ${b.priority ?? "정책 기본"}`}</span>
        <span className={`${chip} tabular-nums`}>{`노드 ${b.node_count ?? "정책 기본"}`}</span>
        <span className={`${chip} tabular-nums`}>{`프로세스 ${b.procs_per_node ?? "정책 기본"}`}</span>
        <span className={`${chip} tabular-nums`}>{`동시 ${b.max_concurrency}`}</span>
        <span className={`${chip} text-bad`}>root 실행</span>
        {opts.length === 0
          ? <span className={`${chip} text-muted`}>옵션 없음</span>
          : opts.map(([k, v]) => (
              <span key={k} className={`${chip} font-mono break-all`}>{`${k}=${optText(v)}`}</span>))}
        {/* 소유자 기록은 있을 때만 — 없으면 칩 생략(빈칸 소음 방지, 메모 관례) */}
        {b.owner_username && (
          <span className={chip}>{`소유자 기록 ${b.owner_username}`}</span>)}
      </div>
    </Card>
  );
}

// 항목별 데이터 온도 섹션: 펼친 항목에서만 마운트된다(= lazy 조회의 1차 게이트,
// 훅 enabled 가 2차로 성공 요청만 통과시킨다). 성공 요청만 리포트를 가지므로
// 미성공 항목은 조회 없이 "리포트 없음"이 정직하고, 404 no_scan_report(성공인데
// 리포트 부재·유실)도 화면엔 같은 사실이라 같은 문구다 — 그 외 오류(503 등)는
// 사유 문구를 그대로 보인다. 온도 토글 상태는 컴포넌트 지역(단일 펼침이라 행
// 전환 시 atime 기본으로 리셋되는 게 자연스럽다).
function ItemScanStats({ requestId, succeeded }: {
  requestId: string | null; succeeded: boolean;
}) {
  const q = useRequestScanStats(requestId, succeeded);
  const [tempKey, setTempKey] = useState("atime");
  const noReport = !succeeded
    || (q.isError && (q.error as ApiError).code === "no_scan_report");
  if (noReport) return <p className="text-muted text-sm mt-3 ml-11">리포트 없음</p>;
  if (q.isError) {
    return <p className="text-bad text-sm mt-3 ml-11">{(q.error as ApiError).message}</p>;
  }
  const stats = q.data;
  if (!stats) return null;               // 로딩 — 짧은 창이라 문구 없이 둔다
  const tempKeys = ["atime", "mtime", "ctime"]
    .filter((k) => stats.time_histograms[k] !== undefined);
  const bars = toBars(stats.time_histograms[tempKey], "bytes");
  const cumTotal = bars.reduce((acc, b) => acc + b.value, 0);
  return (
    <div className="mt-3 ml-11 space-y-3">
      <div>
        <h3 className="font-medium text-sm">데이터 온도(hot/cold)</h3>
        {/* 언제 찍힌 숫자인지 없이 보여주는 건 부정직이다(ScanPaths 관례 미러) */}
        <p className="text-muted text-xs">
          {typeof stats.generated_at_epoch === "number"
            ? `scan 리포트 생성: ${utcStamp(stats.generated_at_epoch)}`
            : "scan 리포트 생성 시각을 알 수 없습니다"}
        </p>
      </div>
      <div>
        <div className="flex gap-2 mb-2">
          {tempKeys.map((k) => (
            <Button key={k} type="button"
                    variant={tempKey === k ? "outline" : "ghost"}
                    onClick={() => setTempKey(k)}>{k}</Button>
          ))}
        </div>
        <BarChart data={bars}
                  label={`데이터 온도(${tempKey}) 히스토그램`}
                  formatValue={humanBytes}
                  colorOf={tempColorOf(bars.length)}
                  cumulative={{ format: humanBytes }}
                  emptyText="집계된 버킷 없음" />
        {TEMP_CAPTIONS[tempKey] && (
          <p className="text-muted text-xs mt-1">{TEMP_CAPTIONS[tempKey]}</p>
        )}
        {/* 누적 캡션은 오버레이와 같은 조건(총합>0)으로만 -- 총합 0 이면 선이
            없는데 "선 = ..." 은 거짓 캡션이 된다. 총 용량 값(bars 합)도 여기서
            함께 말한다(선의 100% 가 몇 바이트인지). */}
        {cumTotal > 0 && (
          <p className="text-muted text-xs mt-1">
            {`선 = hot쪽부터의 누적 용량 비중 · 총 ${humanBytes(cumTotal)}`}
          </p>
        )}
      </div>
      <div>
        {/* 크기 분포는 온도가 아니다 — 온도 색을 입히면 "작은 파일=hot"이라는
            거짓 의미가 생겨 기본 accent 를 유지한다. */}
        <h4 className="font-medium mb-2 text-sm">파일 크기 분포(개수)</h4>
        <BarChart data={toBars(stats.file_size_histogram, "count")}
                  label="파일 크기 분포" emptyText="집계된 버킷 없음" />
      </div>
      <div>
        <h4 className="font-medium mb-2 text-sm">요약</h4>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm max-w-md">
          {Object.entries(stats.summary).map(([k, v]) => (
            <Fragment key={k}>
              <dt className="text-muted">{k}</dt>
              <dd className="tabular-nums">{v}</dd>
            </Fragment>
          ))}
        </dl>
        {/* null = 구형 리포트(총계 미기록) — 0(파손 없음)과 구분해 말한다 */}
        <p className="text-muted text-sm mt-2">
          {typeof stats.broken_paths_total === "number"
            ? `파손 경로 ${stats.broken_paths_total}건`
            : "파손 경로: 기록 없음(구형 리포트)"}
        </p>
      </div>
    </div>
  );
}

// CSV 붙여넣기로 항목 전체 교체(종단 배치만 노출 — 진짜 가드는 서버
// batch_items_not_replaceable 409). 교체 후에도 배치는 종단 유지 — 교체가 곧
// 실행은 아니다(재실행은 기존 「전체 재실행」 버튼 몫). 파일 업로드가 아니라
// textarea 붙여넣기인 이유: 운영 환경 브라우저는 파일 업로드가 불가하다(환경
// 제약) — 생성 위저드(BatchCreate)의 CSV 붙여넣기 패턴을 미러한다. 동선:
// 붙여넣기 → parseItemsCsv 파싱(입력에서 즉시 파생) → 미리보기(행 수·오류) →
// 「교체」 확인 클릭 → PUT. 오류가 하나라도 있으면 교체 버튼 잠금(부분 반영
// 금지 — BatchCreate applyParsed 와 같은 계약). 빈 입력(초기 상태)은 미리보기·
// 버튼 자체를 안 그린다 — "아직 안 붙여넣음"과 "0행 파싱"(헤더만)의 구분.
// 배치 레벨 스토리지: 배치 행엔 스토리지 메타가 없다(실측 — batches 테이블은
// 실행 제어·옵션만). 종단 배치는 항목이 반드시 있으므로 첫 항목 payload 에서
// 물려받는다(항목 추가 submitAdd 와 같은 동질성 계약) — 항목 0개면 호출측이
// 이 컴포넌트 자체를 안 그린다(물려받을 스토리지가 없다).
function ReplaceItemsCsv({ batchId, operation, firstPayload }: {
  batchId: string; operation: "scan" | "sync";
  firstPayload: Record<string, unknown>;
}) {
  const replace = useReplaceBatchItems(batchId);
  const [text, setText] = useState("");
  // 상태가 아니라 파생: 입력이 곧 진실이라 파싱 결과를 별도 상태로 들고 있으면
  // 둘이 어긋날 수 있다. null = 빈 입력(미리보기 없음).
  const parsed = text.trim() === "" ? null : parseItemsCsv(operation, text);
  const rows = parsed?.rows ?? [];
  const errors = parsed?.errors ?? [];
  const items = rows.map((r) => operation === "sync"
    ? { source_storage: firstPayload.source_storage, source: (r as SyncRow).source,
        destination_storage: firstPayload.destination_storage,
        destination: (r as SyncRow).destination }
    : { storage: firstPayload.storage, target: (r as ScanRow).target });
  return (
    <div className="space-y-2">
      <label className="text-sm block">
        {`CSV로 전체 교체 (${operation === "scan" ? "행당 경로 1개" : "행당 source,destination"})`}
        <textarea aria-label="교체 CSV" className={`${field} h-40 font-mono`}
                  placeholder={operation === "scan"
                    ? "team\nprojects/alpha"
                    : "team/dataset,backup/dataset\nprojects/alpha,backup/alpha"}
                  value={text}
                  onChange={(e) => { setText(e.target.value);
                                     replace.reset(); /* 이전 시도 잔상 제거 */ }} />
      </label>
      {/* 미리보기(행 수)와 확인 버튼은 입력이 있을 때만 — 무엇으로 바꾸는지
          보여주기 전의 교체 버튼은 오클릭 유도다. 0행(헤더만)도 잠금 — 서버
          empty_batch 재확인 전에 화면에서 정직하게 막는다. */}
      {parsed !== null && (
        <p className="text-sm text-muted">{`${rows.length}행 파싱됨`}</p>
      )}
      {errors.length > 0 && (
        <ul className="text-bad text-sm space-y-1">
          {errors.map((err, i) => <li key={i}>{err}</li>)}
        </ul>
      )}
      {parsed !== null && (
        <Button disabled={errors.length > 0 || rows.length === 0 || replace.isPending}
                onClick={() => replace.mutate(items)}>교체</Button>
      )}
      {replace.isError && (
        <p className="text-bad text-sm">{(replace.error as ApiError).message}</p>
      )}
    </div>
  );
}

// 현재 항목 CSV(읽기 전용): 항목들을 serializeItemsCsv 텍스트로 노출해 사용자가
// 직접 선택·복사한다. 「복사 버튼」이 아닌 이유: 운영 포탈은 http 비보안
// 컨텍스트라 navigator.clipboard 가 부재한다(BatchCreate downloadCsv 와 같은
// 제약 — clipboard API 금지). onFocus 전체 선택은 DOM Selection 이라 어디서나
// 동작한다. 접이식 유지 — 상시 참조 정보가 아니다. 헤더 포함 직렬화라 교체
// textarea 에 그대로 붙여넣으면 같은 항목이 복원된다(왕복 계약).
function CurrentItemsCsv({ operation, items }: {
  operation: "scan" | "sync"; items: BatchItem[];
}) {
  const text = serializeItemsCsv(operation, items.map((it) => operation === "sync"
    ? { source: String(it.payload.source ?? ""),
        destination: String(it.payload.destination ?? "") }
    : { target: String(it.payload.target ?? "") }));
  return (
    <details>
      <summary className="text-sm text-muted cursor-pointer">현재 항목 CSV</summary>
      <textarea aria-label="현재 항목 CSV" readOnly value={text}
                className={`${field} h-40 font-mono`}
                onFocus={(e) => e.currentTarget.select()} />
    </details>
  );
}

// 배치 삭제(종단 배치만 노출 — 진짜 가드는 서버 batch_not_deletable). 확인
// 다이얼로그 필수: 배치 행·항목 행이 사라지는 비가역 동작이다(자식 요청·잡은
// 감사 이력으로 보존 — repo.delete 주석). 성공 시 목록으로 이동 — 삭제된
// 배치의 상세는 404 라 머무를 곳이 아니다.
function DeleteBatchButton({ batchId }: { batchId: string }) {
  const [open, setOpen] = useState(false);
  const del = useDeleteBatch(batchId);
  const navigate = useNavigate();
  // 닫힐 때마다 에러를 비운다(StoragesList DeleteButton 선례) — "취소"는
  // setOpen(false)를 직접 불러 Radix onOpenChange 가 발화하지 않는다.
  useEffect(() => { if (!open) del.reset(); }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="배치 삭제"
            trigger={<Button variant="ghost">배치 삭제</Button>}>
      <p className="text-sm text-muted mb-3">배치와 항목 목록이 삭제됩니다. 자식 요청·잡 이력은 남습니다.</p>
      {del.isError && <p className="text-bad text-sm mb-2">{(del.error as ApiError).message}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setOpen(false)}>취소</Button>
        <Button disabled={del.isPending}
                onClick={() => del.mutate(undefined,
                  { onSuccess: () => navigate("/admin/batches") })}>삭제 확인</Button>
      </div>
    </Dialog>
  );
}

export function BatchDetail() {
  const { batchId = "" } = useParams();
  const q = useBatch(batchId);
  const confirm = useConfirmBatch(batchId);
  const rerun = useRerunFailed(batchId);
  const cancel = useCancelBatch(batchId);
  const rescan = useRescanBatch(batchId);
  const update = useUpdateBatch(batchId);
  const b = q.data;
  // 이름·메모 인라인 편집: 열 때 현재값을 드래프트로 복사한다 — 폴링 리페치가
  // 편집 중 입력을 덮어쓰지 않게 편집 상태는 서버 상태와 분리한다.
  const [editing, setEditing] = useState(false);
  const [nameDraft, setNameDraft] = useState("");
  const [noteDraft, setNoteDraft] = useState("");
  const startEdit = () => {
    setNameDraft(b?.name ?? ""); setNoteDraft(b?.note ?? ""); setEditing(true);
  };
  const save = () => update.mutate({ name: nameDraft, note: noteDraft },
                                   { onSuccess: () => setEditing(false) });
  // 항목 행 펼침: NodeMetricsSection 의 open 토글 관례 미러(단일 펼침) — 접힘이
  // 기본이라 목록이 컴팩트하고, 상세는 펼친 행에만 렌더된다. 데이터 온도도 펼친
  // 행 안에서 그 항목(요청) 단위로만 조회·렌더한다 — 배치 합산은 제거됐다
  // (사용자 결정: 배치 전체 집계는 필요 없다).
  const [openSeq, setOpenSeq] = useState<number | null>(null);
  // 항목 목록 필터·페이지: 클라이언트 사이드(RecentRequestsSection 관례 미러).
  // 필터 변경은 setPage(1) 동반 — 옛 페이지 번호는 무의미하다.
  const [filter, setFilter] = useState("all");
  const [page, setPage] = useState(1);
  // 항목 편집(수정·삭제·추가): 노출 조건은 표시 게이트일 뿐 — 진짜 차단은 서버다
  // (활성 배치는 Queued 만 SQL 원자 가드로 허용, 경합 패배·비허용은 409). 종단
  // 배치(Completed/Cancelled)는 전 항목 편집 가능 — legacy "편집 후 재실행" 흐름
  // (전체 재실행과 결합)이다.
  const terminal = b?.status === "Completed" || b?.status === "Cancelled";
  const canEditItem = (it: BatchItem) => terminal || it.status === "Queued";
  const isSync = b?.operation === "sync";
  const updateItem = useUpdateBatchItem(batchId);
  const deleteItem = useDeleteBatchItem(batchId);
  const addItem = useAddBatchItem(batchId);
  // 드래프트는 서버 상태와 분리(이름·메모 편집과 같은 이유 — 폴링 리페치가 입력을
  // 덮지 않는다). pathDraft = scan target / sync source.
  const [editSeq, setEditSeq] = useState<number | null>(null);
  // 행 삭제 2단 확인: 1단 클릭이 armedSeq 를 무장시키고, 같은 행의 2단 클릭이
  // DELETE 를 쏜다(오삭제 방지). 다른 행을 무장시키면 이전 무장은 자연 해제 —
  // 단일 상태라 동시 무장이 구조적으로 불가능하다. 배치 삭제의 Dialog 관례
  // 대신 2단 클릭을 쓴 이유: 행마다 Dialog 는 무겁고, 항목 삭제는 배치 삭제와
  // 달리 행 요약(순번·대상)이 바로 옆에 보여 확인 문맥이 이미 화면에 있다.
  const [armedSeq, setArmedSeq] = useState<number | null>(null);
  const [pathDraft, setPathDraft] = useState("");
  const [dstDraft, setDstDraft] = useState("");
  const [addPath, setAddPath] = useState("");
  const [addDst, setAddDst] = useState("");
  // 스토리지는 입력받지 않는다 — 배치는 단일 스토리지 동질성 계약이라 항목의
  // 기존 payload(수정) 또는 첫 항목 payload(추가)에서 물려받는 것이 정직하다.
  const itemBody = (p: Record<string, unknown>, path: string, dst: string) => isSync
    ? { source_storage: p.source_storage, source: path,
        destination_storage: p.destination_storage, destination: dst }
    : { storage: p.storage, target: path };
  const startItemEdit = (it: BatchItem) => {
    setPathDraft(String((isSync ? it.payload.source : it.payload.target) ?? ""));
    setDstDraft(String(it.payload.destination ?? ""));
    setEditSeq(it.seq);
  };
  const saveItem = (it: BatchItem) => updateItem.mutate(
    { seq: it.seq, item: itemBody(it.payload, pathDraft, dstDraft) },
    { onSuccess: () => setEditSeq(null) });
  const firstPayload = b?.items?.[0]?.payload;
  const submitAdd = () => { if (firstPayload) addItem.mutate(
    itemBody(firstPayload, addPath, addDst),
    { onSuccess: () => { setAddPath(""); setAddDst(""); } }); };
  // 필터·페이지 파생(상태 아님 — items 가 진실이라 파생이 어긋날 수 없다).
  // 필터로 페이지 수가 줄면 마지막 페이지로 클램프 — 빈 페이지에 갇히지 않는다.
  const items = b?.items ?? [];
  const activeFilter = ITEM_FILTERS.find((f) => f.key === filter) ?? ITEM_FILTERS[0];
  const filtered = items.filter((it) => activeFilter.match(it.status));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pages);
  const visible = filtered.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);
  return (
    <section className="space-y-6">
      {/* 이름이 있으면 이름이 헤더 — 축약 batch_id 는 식별자로 병기한다(사라지면
          운영자가 로그·API 와 대조할 열쇠를 잃는다). 없으면 기존 헤더 유지. */}
      <div className="flex items-baseline gap-3">
        <h1 className="text-2xl font-bold">{b?.name ?? `배치 ${batchId.slice(0,12)}`}</h1>
        {b?.name && <span className="text-muted text-sm font-mono">{batchId.slice(0,12)}</span>}
      </div>
      {/* 헤더 카드: 상태·실행 권한·소유자 + 동작 버튼 행. 성공/실패 카운트는
          사이드의 진행 요약 카드로 이동했다(중복 표기 제거). 버튼 위계: 다음
          동작(배치 확인·실패분 재실행)=primary, 보조 재실행(전체)=outline,
          중립(메모 편집·취소)=ghost. 배치 삭제는 사이드 위험 영역으로 분리. */}
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex flex-wrap items-center gap-3">
            {/* 배치 상태 전용 색(batchPillVariant — Completed=초록·진행=busy·
                Cancelled=neutral). 항목 pill 은 요청/잡 판정 축이라 기존 공유
                pillVariant 를 그대로 쓴다(variant 미지정). */}
            <StatusPill state={b?.status ?? "…"}
                        variant={b ? batchPillVariant(b.status) : undefined} />
            <span className="text-muted text-sm">{b?.operation}</span>
            {/* 특권 실행 문구는 로드되면 항상 -- 통일 게이트 후 배치는 전부 관리자
                특권(root) 실행이다. 행별 auth_method/owner 유무로 재판정하지 않는다:
                프론트는 allowlist 를 모르므로 판정 흉내가 더 큰 거짓말이 된다(구형
                비특권 행에는 이 단순 표시가 과잉이지만, 거짓 판정보다 낫다).
                한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(getByText 관례). */}
            {b && <span className="text-bad text-sm">특권 실행(root)</span>}
            {b?.owner_username && (
              <span className="text-muted text-sm">{`소유자 ${b.owner_username}`}</span>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {/* 라벨은 "메모 편집"(사용자 지시 — "이름·메모"의 이름 축약). 폼은
                여전히 이름·메모 둘 다 편집한다(PATCH {name, note} 계약 무변경) —
                폼 안 입력 라벨("배치 이름"/"메모")이 실기능을 말한다. */}
            {b && !editing && <Button variant="ghost" onClick={startEdit}>메모 편집</Button>}
            {b?.status === "PreviewReady" && <Button disabled={confirm.isPending} onClick={() => confirm.mutate()}>배치 확인</Button>}
            {b?.status === "Completed" && (b?.failed_count ?? 0) > 0 && <Button disabled={rerun.isPending} onClick={() => rerun.mutate()}>실패분 재실행</Button>}
            {/* 전체 재실행(:rescan): 종단 배치 한정(서버 가드 미러) — 성공 item 포함
                전부 재큐잉(성장 모니터링). "실패분 재실행"(실패만)과 공존한다.
                outline = 보조 동작(실패분 재실행이 primary 인 화면에서 등급 구분). */}
            {terminal && <Button variant="outline" disabled={rescan.isPending} onClick={() => rescan.mutate()}>전체 재실행</Button>}
            {(b?.status === "Running" || b?.status === "Previewing" || b?.status === "PreviewReady") && <Button variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>취소</Button>}
          </div>
        </div>
        {/* 메모는 편집 밖에서도 보인다 — 없으면 행 자체 생략(빈칸 소음 방지).
            한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(getByText 관례). */}
        {b?.note && !editing && <p className="text-muted text-sm mt-3">{`메모 ${b.note}`}</p>}
        {editing && (
          <div className="mt-4 space-y-2 max-w-md">
            <label className="text-sm block">배치 이름
              <input aria-label="배치 이름" maxLength={120} className={field}
                     value={nameDraft} onChange={(e) => setNameDraft(e.target.value)} />
            </label>
            <label className="text-sm block">메모
              <input aria-label="메모" className={field}
                     value={noteDraft} onChange={(e) => setNoteDraft(e.target.value)} />
            </label>
            <div className="flex gap-2">
              <Button disabled={update.isPending} onClick={save}>저장</Button>
              {/* 배치 취소 버튼("취소")과 라벨이 겹치지 않게 "편집 취소" */}
              <Button variant="ghost" onClick={() => setEditing(false)}>편집 취소</Button>
            </div>
            {update.isError && (
              <p className="text-bad text-sm">{(update.error as ApiError).message}</p>
            )}
          </div>
        )}
      </Card>
      {/* 2단 그리드: 좌 2칸 = 항목 목록(주 콘텐츠), 우 1칸 = 사이드 패널(진행
          요약→실행 설정→항목 편집→위험 영역). md 이하 1열 스택 — 사이드가 목록
          아래로 내려간다. items-start: 사이드 카드가 목록 높이로 안 늘어난다. */}
      <div className="grid gap-6 items-start lg:grid-cols-3">
        <div className="space-y-6 lg:col-span-2">
          {/* 항목: 표 대신 리스트 + 행 펼침. 표(td) 안에 버튼·flex 를 넣으면 e2e
              L2(display=table-cell 불변식)가 무는 함정이라, 펼침 UI 는 표 밖
              리스트가 구조적으로 안전하다. 기본 행은 컴팩트(chevron·순번·대상
              요약·상태), 상세(사유·파일 수·완료 시각·payload·요청 링크)는 펼친
              행에만. */}
          <Card>
            <div className="flex flex-wrap items-center justify-between gap-4">
              <h2 className="font-medium">항목</h2>
              <div className="flex gap-2">
                {ITEM_FILTERS.map((f) => (
                  <Button key={f.key} type="button"
                          variant={filter === f.key ? "outline" : "ghost"}
                          aria-pressed={filter === f.key}
                          onClick={() => { setFilter(f.key); setPage(1); }}>
                    {f.label}
                  </Button>
                ))}
              </div>
            </div>
            <ul aria-label="배치 항목 목록" className="mt-4 space-y-1">
              {visible.map((it) => (
                <li key={it.seq} className={`border-l-2 ${itemEdge(it.status)} pl-3`}>
                  {/* 행 = 펼침 토글 버튼 + 삭제 버튼의 flex 형제. 토글이 행 전체를
                      덮는 버튼이었는데, 버튼 안에 버튼은 불가(HTML)라 삭제를 행에
                      올리려면 토글을 flex-1 로 줄이고 삭제를 옆에 둔다. hover 배경
                      은 행 머리에만 — 펼침 패널까지 물들이면 hover 가 시끄럽다. */}
                  <div className="flex items-center gap-4 rounded-lg px-2 py-3 hover:bg-panel transition-colors">
                    <button type="button" aria-label={`항목 ${it.seq} 상세`}
                            aria-expanded={openSeq === it.seq}
                            onClick={() => setOpenSeq(openSeq === it.seq ? null : it.seq)}
                            className="flex min-w-0 flex-1 items-center gap-3 text-left">
                      {/* 사이드바 Group 토글의 ChevronDown 관례: 접힘 = -rotate-90
                          (오른쪽 향함), 펼침 = 회전 해제(아래 향함). 전환은 짧은
                          transition 만(과한 애니메이션 금지). */}
                      <ChevronDown className={`h-4 w-4 shrink-0 text-muted transition-transform ${
                        openSeq === it.seq ? "" : "-rotate-90"}`} aria-hidden />
                      <span className="w-8 shrink-0 text-muted text-xs tabular-nums">{it.seq}</span>
                      <span className="min-w-0 flex-1 truncate font-mono text-xs">
                        {summarizeItem(b?.operation, it.payload)}
                      </span>
                      <StatusPill state={it.status} />
                    </button>
                    {/* 삭제는 펼치지 않아도 보이는 행 버튼. 노출 조건은 기존
                        canEditItem 재사용(표시 게이트 — 진짜 차단은 서버 409).
                        aria-label 로 행별 이름을 부여해 테스트·스크린리더가 어느
                        항목인지 안다. ml-2 = 토글과의 추가 여백(오클릭 방지). */}
                    {canEditItem(it) && (armedSeq === it.seq
                      ? <Button aria-label={`항목 ${it.seq} 삭제 확인`}
                                className="ml-2 shrink-0"
                                disabled={deleteItem.isPending}
                                onClick={() => deleteItem.mutate(it.seq,
                                  { onSettled: () => setArmedSeq(null) })}>삭제 확인</Button>
                      : <Button variant="ghost" aria-label={`항목 ${it.seq} 삭제`}
                                className="ml-2 shrink-0"
                                onClick={() => setArmedSeq(it.seq)}>삭제</Button>)}
                  </div>
                  {/* 삭제 실패 사유는 시도한 행 밑에만 — variables(마지막 mutate 의
                      seq)로 행을 특정한다(전 행에 도배하면 어느 삭제가 실패했는지
                      모른다). */}
                  {deleteItem.isError && deleteItem.variables === it.seq && (
                    <p className="text-bad text-sm mt-1 ml-11">{(deleteItem.error as ApiError).message}</p>
                  )}
                  {openSeq === it.seq && (<div className="pb-3">
                    <dl className="mt-1 ml-11 grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
                      {/* 요청 상태는 항목 상태(배치 시점 판정)와 다른 축 — 자식
                          요청의 현재 상태다. null = 아직 materialize 안 됨. */}
                      <dt className="text-muted">요청 상태</dt>
                      <dd>{it.request_state ?? "—"}</dd>
                      <dt className="text-muted">사유</dt>
                      <dd className="text-bad">{it.reason_code ? reasonText(it.reason_code) : "—"}</dd>
                      {/* null = 모름(잡 없음/미기록) — 0(파일 없음)은 정상값으로
                          그대로 표기한다(null≠0, ?? 로만 접는다). */}
                      <dt className="text-muted">파일 수</dt>
                      <dd className="tabular-nums">{it.files_count ?? "—"}</dd>
                      <dt className="text-muted">완료 시각</dt>
                      <dd className="text-muted">{it.completed_at ?? "—"}</dd>
                      <dt className="text-muted">payload</dt>
                      <dd className="font-mono text-xs break-all">{JSON.stringify(it.payload)}</dd>
                      <dt className="text-muted">요청</dt>
                      <dd>{it.request_id
                        ? <Link className="text-accent" to={`/jobs/${it.request_id}`}>요청 상세</Link>
                        : "—"}</dd>
                    </dl>
                    {/* 수정은 펼침에 남긴다(삭제만 행): 수정은 payload 입력 폼이
                        필요해 펼침 패널의 공간이 자연스럽고, 어떤 payload 를
                        고치는지 확인하려면 어차피 펼친다. 노출 조건은 기존
                        canEditItem 재사용(표시 게이트, 진짜 차단은 서버 409). */}
                    {canEditItem(it) && editSeq !== it.seq && (
                      <div className="mt-2 ml-11 flex gap-2">
                        <Button variant="ghost" onClick={() => startItemEdit(it)}>수정</Button>
                      </div>
                    )}
                    {editSeq === it.seq && (
                      <div className="mt-2 ml-11 space-y-2 max-w-md">
                        {isSync ? (<>
                          <label className="text-sm block">소스 경로
                            <input aria-label="소스 경로" className={field} value={pathDraft}
                                   onChange={(e) => setPathDraft(e.target.value)} />
                          </label>
                          <label className="text-sm block">목적지 경로
                            <input aria-label="목적지 경로" className={field} value={dstDraft}
                                   onChange={(e) => setDstDraft(e.target.value)} />
                          </label>
                        </>) : (
                          <label className="text-sm block">대상 경로
                            <input aria-label="대상 경로" className={field} value={pathDraft}
                                   onChange={(e) => setPathDraft(e.target.value)} />
                          </label>
                        )}
                        <div className="flex gap-2">
                          <Button disabled={updateItem.isPending} onClick={() => saveItem(it)}>항목 저장</Button>
                          {/* 배치 취소("취소")·이름 편집("편집 취소")과 라벨이 겹치지 않게 */}
                          <Button variant="ghost" onClick={() => setEditSeq(null)}>항목 편집 취소</Button>
                        </div>
                        {updateItem.isError && (
                          <p className="text-bad text-sm">{(updateItem.error as ApiError).message}</p>
                        )}
                      </div>
                    )}
                    {/* 항목별 데이터 온도: scan 배치만 — sync 항목엔 dscan 리포트가
                        존재할 수 없어 섹션 자체가 거짓 약속이 된다. */}
                    {b?.operation === "scan" && (
                      <ItemScanStats requestId={it.request_id}
                                     succeeded={it.request_state === "Succeeded"} />
                    )}
                  </div>)}
                </li>
              ))}
            </ul>
            {/* 빈 목록의 두 사실을 구분해 말한다: 항목 자체가 없다 vs 필터에 걸린
                항목이 없다 — 뭉개면 "배치가 비었다"는 거짓 인상을 준다. */}
            {b && items.length === 0 && (
              <p className="text-muted text-sm mt-4">항목이 없습니다</p>
            )}
            {items.length > 0 && filtered.length === 0 && (
              <p className="text-muted text-sm mt-4">해당 상태의 항목이 없습니다</p>
            )}
            {/* 페이지네이션은 목록 밖(카드 안) — RecentRequestsSection 관례 미러 */}
            <div className="flex items-center justify-end gap-3 mt-5 text-sm">
              <Button variant="ghost" disabled={current <= 1}
                      onClick={() => setPage(current - 1)}>이전</Button>
              <span className="text-muted tabular-nums">{`${current} / ${pages} 페이지`}</span>
              <Button variant="ghost" disabled={current >= pages}
                      onClick={() => setPage(current + 1)}>다음</Button>
            </div>
          </Card>
        </div>
        <aside className="space-y-6">
          {b && (<>
            <BatchProgress b={b} />
            <BatchSettings b={b} />
            {/* 항목 편집 도구 묶음: 추가 → CSV 전체 교체 → 현재 항목 CSV. 조건부
                노출(종단만 교체 등)은 카드 안에서 나타나고 사라진다 — 본문(항목
                목록) 레이아웃이 널뛰지 않는다. */}
            <Card>
              <h2 className="font-medium">항목 편집</h2>
              {/* 항목 추가: 경로만 입력 — 스토리지는 첫 항목에서 물려받는다(동질성
                  계약). 종단 배치에 추가하면 서버가 재활성화(scan→Running/sync→
                  Previewing)하고 신규 Queued 항목만 실행된다. 항목이 없으면 물려
                  받을 스토리지가 없어 폼 대신 안내를 보인다(빈 배치는 삭제·재생성
                  이 동선). */}
              {items.length > 0 ? (
                <div className="mt-4 space-y-2">
                  {isSync ? (<>
                    <label className="text-sm block">추가할 소스 경로
                      <input aria-label="추가할 소스 경로" className={field} value={addPath}
                             onChange={(e) => setAddPath(e.target.value)} />
                    </label>
                    <label className="text-sm block">추가할 목적지 경로
                      <input aria-label="추가할 목적지 경로" className={field} value={addDst}
                             onChange={(e) => setAddDst(e.target.value)} />
                    </label>
                  </>) : (
                    <label className="text-sm block">추가할 대상 경로
                      <input aria-label="추가할 대상 경로" className={field} value={addPath}
                             onChange={(e) => setAddPath(e.target.value)} />
                    </label>
                  )}
                  <Button disabled={addItem.isPending} onClick={submitAdd}>항목 추가</Button>
                  {addItem.isError && (
                    <p className="text-bad text-sm">{(addItem.error as ApiError).message}</p>
                  )}
                </div>
              ) : (
                <p className="text-muted text-sm mt-3">항목이 없어 스토리지를 물려받을 수 없습니다 — 항목 추가는 새 배치로 하세요</p>
              )}
              {/* CSV 전체 교체: 종단 배치 + 항목 존재(스토리지 상속원)일 때만 —
                  종단 배치는 항목이 있는 게 정상이라 사실상 종단 게이트다. */}
              {terminal && firstPayload && (
                <div className="border-t border-line mt-5 pt-5">
                  <ReplaceItemsCsv batchId={batchId}
                                   operation={isSync ? "sync" : "scan"}
                                   firstPayload={firstPayload} />
                </div>
              )}
              {/* 현재 항목 CSV: 항목이 있을 때만 — 빈 목록의 헤더 한 줄 CSV 는
                  소음이다 */}
              {items.length > 0 && (
                <div className="border-t border-line mt-5 pt-5">
                  <CurrentItemsCsv operation={isSync ? "sync" : "scan"}
                                   items={items} />
                </div>
              )}
            </Card>
            {/* 위험 영역: 비가역 동작(배치 삭제)을 일반 동작 버튼 행과 물리적으로
                분리한다 — 종단 배치만(활성 배치는 취소 먼저가 동선). */}
            {terminal && (
              <Card>
                <h2 className="font-medium text-bad">위험 영역</h2>
                <p className="text-muted text-sm mt-2">배치를 삭제하면 항목 목록도 함께 사라집니다(비가역).</p>
                <div className="border-t border-line mt-4 pt-4">
                  <DeleteBatchButton batchId={batchId} />
                </div>
              </Card>
            )}
          </>)}
        </aside>
      </div>
    </section>
  );
}
