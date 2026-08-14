import { Fragment, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useBatch, useConfirmBatch, useRerunFailed, useRequestScanStats,
         useCancelBatch, useRescanBatch, useUpdateBatch, useDeleteBatch,
         useUpdateBatchItem, useDeleteBatchItem, useAddBatchItem } from "./useBatches";
import { Card } from "../../components/ui/Card";
import { Dialog } from "../../components/ui/Dialog";
import { StatusPill } from "../../components/ui/StatusPill";
import { BarChart } from "../../components/ui/BarChart";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";
import { reasonText, ApiError } from "../../lib/api";
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

// 옵션 humanize: scan/sync 옵션은 평평한 스칼라 맵(batch_files 등)이라 키=값
// 나열이 JSON 원문보다 읽기 쉽다. 비스칼라 값만 JSON 으로 접는다(방어 — 현행
// 계약엔 없지만 [object Object] 를 찍는 것보다 낫다). 빈 옵션은 "없음" — 빈
// 문자열·"{}" 는 화면에서 "모름"처럼 읽힌다. undefined 는 fixture 호환일 뿐,
// 실서버는 options 를 늘 보낸다(types.ts Batch 주석).
function humanizeOptions(options: Record<string, unknown> | undefined): string {
  const entries = Object.entries(options ?? {});
  if (entries.length === 0) return "없음";
  return entries
    .map(([k, v]) =>
      `${k}=${typeof v === "object" && v !== null ? JSON.stringify(v) : String(v)}`)
    .join(" · ");
}

// 실행 제어 설정(읽기 전용): 배치 생성 시 고른 값이 상세에서 안 보여 "무슨
// 설정으로 돌았나"를 알 수 없었다. null = 정책 기본(null≠0) — 미지정을 0·빈값
// 으로 뭉개지 않고 명시 문구로 말한다. 접이식(details)을 택한 이유: 상시 참조
// 정보가 아니라(사후 확인용) 항상 펼치면 항목 목록이 설정 행수만큼 매번 밀린다
// — 헤더 카드에 summary 한 줄로 접어 두고 필요할 때 연다. 실행 권한 표기는
// 기존 헤더의 "특권 실행(root)" 문구 재사용 — 여기 중복하지 않는다.
function BatchSettings({ b }: { b: Batch }) {
  return (
    <details className="mt-2">
      <summary className="text-sm text-muted cursor-pointer">실행 설정</summary>
      <dl className="mt-2 grid grid-cols-[9rem_1fr] gap-y-1 text-sm max-w-xl">
        <dt className="text-muted">우선순위</dt>
        <dd>{b.priority ?? "정책 기본"}</dd>
        <dt className="text-muted">노드 수</dt>
        <dd className="tabular-nums">{b.node_count ?? "정책 기본"}</dd>
        <dt className="text-muted">노드당 프로세스</dt>
        <dd className="tabular-nums">{b.procs_per_node ?? "정책 기본"}</dd>
        <dt className="text-muted">동시 실행 상한</dt>
        <dd className="tabular-nums">{b.max_concurrency}</dd>
        <dt className="text-muted">옵션</dt>
        <dd className="font-mono text-xs break-all">{humanizeOptions(b.options)}</dd>
        {/* 소유자 기록은 있을 때만 — 없으면 행 생략(빈칸 소음 방지, 메모 관례) */}
        {b.owner_username && (<>
          <dt className="text-muted">소유자 기록</dt>
          <dd>{b.owner_username}</dd>
        </>)}
      </dl>
    </details>
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
  return (
    <section className="space-y-4">
      {/* 이름이 있으면 이름이 헤더 — 축약 batch_id 는 식별자로 병기한다(사라지면
          운영자가 로그·API 와 대조할 열쇠를 잃는다). 없으면 기존 헤더 유지. */}
      <div className="flex items-baseline gap-3">
        <h1 className="text-2xl font-bold">{b?.name ?? `배치 ${batchId.slice(0,12)}`}</h1>
        {b?.name && <span className="text-muted text-sm font-mono">{batchId.slice(0,12)}</span>}
      </div>
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <StatusPill state={b?.status ?? "…"} />
            <span className="text-muted text-sm">{b?.operation} · 성공 {b?.succeeded_count}/실패 {b?.failed_count}/전체 {b?.item_count}</span>
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
          <div className="flex gap-2">
            {b && !editing && <Button variant="ghost" onClick={startEdit}>이름·메모 편집</Button>}
            {b?.status === "PreviewReady" && <Button disabled={confirm.isPending} onClick={() => confirm.mutate()}>배치 확인</Button>}
            {b?.status === "Completed" && (b?.failed_count ?? 0) > 0 && <Button disabled={rerun.isPending} onClick={() => rerun.mutate()}>실패분 재실행</Button>}
            {/* 전체 재실행(:rescan): 종단 배치 한정(서버 가드 미러) — 성공 item 포함
                전부 재큐잉(성장 모니터링). "실패분 재실행"(실패만)과 공존한다 */}
            {(b?.status === "Completed" || b?.status === "Cancelled") && <Button disabled={rescan.isPending} onClick={() => rescan.mutate()}>전체 재실행</Button>}
            {(b?.status === "Completed" || b?.status === "Cancelled") && <DeleteBatchButton batchId={batchId} />}
            {(b?.status === "Running" || b?.status === "Previewing" || b?.status === "PreviewReady") && <Button variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>취소</Button>}
          </div>
        </div>
        {/* 메모는 편집 밖에서도 보인다 — 없으면 행 자체 생략(빈칸 소음 방지).
            한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(getByText 관례). */}
        {b?.note && !editing && <p className="text-muted text-sm mt-2">{`메모 ${b.note}`}</p>}
        {b && <BatchSettings b={b} />}
        {editing && (
          <div className="mt-3 space-y-2 max-w-md">
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
      {/* 항목: 표 대신 리스트 + 행 펼침. 표(td) 안에 버튼·flex 를 넣으면 e2e L2
          (display=table-cell 불변식)가 무는 함정이라, 펼침 UI 는 표 밖 리스트가
          구조적으로 안전하다. 기본 행은 컴팩트(순번·대상 요약·상태), 상세(사유·
          파일 수·완료 시각·payload·요청 링크)는 펼친 행에만. */}
      <Card>
        <h2 className="font-medium">항목</h2>
        {(b?.items ?? []).map((it) => (
          <div key={it.seq} className="border-t border-black/5 py-2">
            <button type="button" aria-label={`항목 ${it.seq} 상세`}
                    aria-expanded={openSeq === it.seq}
                    onClick={() => setOpenSeq(openSeq === it.seq ? null : it.seq)}
                    className="flex w-full items-center gap-3 text-left">
              <span className="w-8 shrink-0 text-muted text-xs tabular-nums">{it.seq}</span>
              <span className="min-w-0 flex-1 truncate font-mono text-xs">
                {summarizeItem(b?.operation, it.payload)}
              </span>
              <StatusPill state={it.status} />
            </button>
            {openSeq === it.seq && (<>
              <dl className="mt-2 ml-11 grid grid-cols-[7rem_1fr] gap-y-1 text-sm">
                {/* 요청 상태는 항목 상태(배치 시점 판정)와 다른 축 — 자식 요청의
                    현재 상태다. null = 아직 materialize 안 됨. */}
                <dt className="text-muted">요청 상태</dt>
                <dd>{it.request_state ?? "—"}</dd>
                <dt className="text-muted">사유</dt>
                <dd className="text-bad">{it.reason_code ? reasonText(it.reason_code) : "—"}</dd>
                {/* null = 모름(잡 없음/미기록) — 0(파일 없음)은 정상값으로 그대로
                    표기한다(null≠0, ?? 로만 접는다). */}
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
              {/* 편집 가능(종단 배치 or Queued 항목)일 때만 버튼 — 불가 항목은
                  버튼 자체가 없다(표시 게이트, 진짜 차단은 서버 409). */}
              {canEditItem(it) && editSeq !== it.seq && (
                <div className="mt-2 ml-11 flex gap-2">
                  <Button variant="ghost" onClick={() => startItemEdit(it)}>수정</Button>
                  <Button variant="ghost" disabled={deleteItem.isPending}
                          onClick={() => deleteItem.mutate(it.seq)}>삭제</Button>
                </div>
              )}
              {deleteItem.isError && (
                <p className="text-bad text-sm mt-1 ml-11">{(deleteItem.error as ApiError).message}</p>
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
            </>)}
          </div>
        ))}
        {/* 항목 추가: 경로만 입력 — 스토리지는 첫 항목에서 물려받는다(동질성 계약).
            종단 배치에 추가하면 서버가 재활성화(scan→Running/sync→Previewing)하고
            신규 Queued 항목만 실행된다. 항목이 없으면 물려받을 스토리지가 없어
            폼 대신 안내를 보인다(빈 배치는 삭제·재생성이 동선). */}
        {b && ((b.items ?? []).length > 0 ? (
          <div className="border-t border-black/5 mt-2 pt-3 space-y-2 max-w-md">
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
          <p className="text-muted text-sm mt-2">항목이 없어 스토리지를 물려받을 수 없습니다 — 항목 추가는 새 배치로 하세요</p>
        ))}
      </Card>
    </section>
  );
}
