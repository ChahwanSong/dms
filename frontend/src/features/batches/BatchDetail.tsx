import { Fragment, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useBatch, useBatchScanStats, useConfirmBatch, useRerunFailed,
         useCancelBatch, useRescanBatch, useUpdateBatch } from "./useBatches";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { BarChart } from "../../components/ui/BarChart";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";
import { reasonText, ApiError } from "../../lib/api";
import type { HistogramBucket } from "../../lib/types";

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

// 시간축별 캡션: atime 만 근사 경고(relatime/open_noatime 는 접근이 atime 을
// 안 갱신할 수 있다) -- mtime/ctime 은 마운트 옵션과 무관해 경고가 거짓이 된다.
const TEMP_CAPTIONS: Record<string, string> = {
  atime: "최근 접근(atime) 기준 용량 비중 — hot(왼쪽)일수록 최근 접근. relatime/open_noatime 환경에선 근사",
  mtime: "수정 시각(mtime) 기준 용량 비중 — 왼쪽일수록 최근 수정",
  ctime: "메타데이터 변경(ctime) 기준 용량 비중 — 왼쪽일수록 최근 변경",
};

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
  // 기본이라 목록이 컴팩트하고, 상세는 펼친 행에만 렌더된다.
  const [openSeq, setOpenSeq] = useState<number | null>(null);
  // 데이터 온도: scan 배치일 때만 조회(훅이 enabled 로 끊는다). 9버킷 × 3축을
  // 나란히 두면 과밀이라 축(atime 기본/mtime/ctime)은 토글로 전환한다.
  const stats = useBatchScanStats(batchId, b).data;
  const [tempKey, setTempKey] = useState("atime");
  const tempKeys = ["atime", "mtime", "ctime"]
    .filter((k) => (stats?.time_histograms ?? {})[k] !== undefined);
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
            {(b?.status === "Running" || b?.status === "Previewing" || b?.status === "PreviewReady") && <Button variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>취소</Button>}
          </div>
        </div>
        {/* 메모는 편집 밖에서도 보인다 — 없으면 행 자체 생략(빈칸 소음 방지).
            한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(getByText 관례). */}
        {b?.note && !editing && <p className="text-muted text-sm mt-2">{`메모 ${b.note}`}</p>}
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
      {/* 데이터 온도(hot/cold): scan 배치 + 집계 ≥1 일 때만 — 집계 0 은 "아직
          합산할 리포트가 없다"라 섹션 자체가 소음이다(skipped 만 있는 경우 포함). */}
      {b?.operation === "scan" && stats && stats.aggregated >= 1 && (
        <Card className="space-y-3">
          <div>
            <h2 className="font-medium">데이터 온도(hot/cold)</h2>
            {/* 정직 카운트: 제외(못 읽은 리포트)를 숨기면 합산이 전체인 척한다.
                한 개의 템플릿 리터럴 = 한 개의 텍스트 노드(getByText 관례). */}
            <p className="text-muted text-xs">
              {`합산 리포트 ${stats.aggregated}건 · 제외 ${stats.skipped}건`}
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
            <BarChart data={toBars(stats.time_histograms[tempKey], "bytes")}
                      label={`데이터 온도(${tempKey}) 히스토그램`}
                      formatValue={humanBytes}
                      emptyText="집계된 버킷 없음" />
            {TEMP_CAPTIONS[tempKey] && (
              <p className="text-muted text-xs mt-1">{TEMP_CAPTIONS[tempKey]}</p>
            )}
          </div>
          <div>
            <h3 className="font-medium mb-2 text-sm">파일 크기 분포(개수)</h3>
            <BarChart data={toBars(stats.file_size_histogram, "count")}
                      label="파일 크기 분포" emptyText="집계된 버킷 없음" />
          </div>
          <div>
            <h3 className="font-medium mb-2 text-sm">요약 합계</h3>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm max-w-md">
              {Object.entries(stats.summary).map(([k, v]) => (
                <Fragment key={k}>
                  <dt className="text-muted">{k}</dt>
                  <dd className="tabular-nums">{v}</dd>
                </Fragment>
              ))}
            </dl>
            {/* null = 전 리포트 구형(총계 미기록) — 0(파손 없음)과 구분해 말한다 */}
            <p className="text-muted text-sm mt-2">
              {typeof stats.broken_paths_total === "number"
                ? `파손 경로 합계 ${stats.broken_paths_total}건`
                : "파손 경로: 기록 없음(구형 리포트)"}
            </p>
          </div>
        </Card>
      )}
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
            {openSeq === it.seq && (
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
            )}
          </div>
        ))}
      </Card>
    </section>
  );
}
