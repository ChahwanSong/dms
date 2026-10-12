import { useEffect, useRef, useState } from "react";
import { Trash2, TriangleAlert } from "lucide-react";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { reasonText, type ApiError } from "../../lib/api";
import { agoText, kstStampOrDash } from "../../lib/datetime";
import type { UnregisteredStorage } from "../../lib/types";
import { useUnregisteredStorages, usePurgeUnregisteredStorage, type StorageHistoryPurgeSummary } from "./useUsage";

// 사용량 분석의 미등록 스토리지 정리(2026-10-12 사용자 요청: 「미등록 스토리지 항목들은 스토리지 항목별로 전체 clean
// up 할수있도록」). 미등록 = 등록 목록에 없는(삭제된) 스토리지 이름으로 남은 scan 기록 -- 판정·규모는 서버
// (GET /api/admin/usage/unregistered-storages)가 한다. 정리는 그 스토리지의 scan 작업 기록 전부(성공·실패·취소, 그
// 스토리지만 담은 scan 배치는 배치째)를 일반 작업 삭제와 같은 몸통으로 지운다(서버가 회차마다 미등록·순수성을 다시 판정).
//
// 정리 mutation 은 **패널이 소유한다**: 다 지우면 그 스토리지 행(과 행 안의 확인 창)이 목록 재조회로 사라진다 -- 창이
// 소유하면 결과를 말할 자리도 함께 사라진다. 결과 줄(role=status)은 패널에 남고, 창이 사라져 포커스가 <body> 로 떨어지면
// 결과 줄로 옮긴다(작업 삭제 창의 focusAfterDelete 와 같은 정신).
//
// 본문 글자는 ink/70(AA) · 한국어 문단 break-keep · 이름·경로는 overflow-wrap:anywhere(작업 삭제 창 관례).

const SKIP_PREVIEW = 10;

function lastKnownLine(row: UnregisteredStorage): string {
  const k = row.last_known;
  if (k === null) return "마지막 설정 기록 없음";
  return `마지막 설정 ${[k.backend_type, k.mount_path].filter((v) => v !== null).join(" · ") || "—"}`;
}

function scaleLine(row: UnregisteredStorage): string {
  const nb = row.batches.length;
  const nm = row.mixed_batches.length;
  return `대상 ${row.target_count}개 · 성공 scan ${row.succeeded_scan_count}개 · 정리할 작업 ${row.request_count}개`
    + (nb > 0 ? `(배치 ${nb}개 포함)` : "")
    + (nm > 0 ? ` · 섞인 배치 ${nm}개는 정리하지 않음` : "");
}

function PurgeResult({ r, innerRef }: { r: StorageHistoryPurgeSummary; innerRef: React.RefObject<HTMLDivElement> }) {
  const rest = r.skipped.length - SKIP_PREVIEW;
  return (
    <div ref={innerRef} tabIndex={-1} role="status" aria-label="정리 결과"
         className="space-y-1 rounded-lg border border-line bg-panel px-3 py-2 text-sm text-ink/70 break-keep outline-none focus-visible:ring-2 focus-visible:ring-accent">
      <p className="font-medium text-ink [overflow-wrap:anywhere]">
        {`${r.storage_name}: 작업 기록 ${r.deletedRequests}개 삭제됨`
          + (r.deletedBatches > 0 ? `(배치 ${r.deletedBatches}개 포함)` : "")
          + (r.skipped.length > 0 ? ` · ${r.skipped.length}건 제외` : "")}
      </p>
      {r.skipped.length > 0 && (
        <ul aria-label="제외된 항목" className="space-y-0.5">
          {r.skipped.slice(0, SKIP_PREVIEW).map((s) => (
            <li key={`${s.kind}:${s.id}`} className="[overflow-wrap:anywhere]">
              <span className="font-mono text-xs text-ink">{`${s.kind === "batch" ? "배치" : "작업"} ${s.id.slice(0, 12)}`}</span>
              {`: ${reasonText(s.reason)}`}
              {s.kind === "batch" && s.request_id !== null && (
                <span className="font-mono text-xs">{` (작업 ${s.request_id.slice(0, 12)})`}</span>
              )}
            </li>
          ))}
          {rest > 0 && <li>{`외 ${rest}건`}</li>}
        </ul>
      )}
      {r.error !== null && <p className="text-bad">{`중간에 멈췄습니다 — ${r.error}. 다시 정리하면 남은 기록을 이어서 지웁니다.`}</p>}
      {/* 아직 시도하지 않은 기록(서버가 센 remaining_untried -- 회차 상한에 닿아 멈춘 경우). 건너뛴 것은 위 목록이 사유와
          함께 말한다(그 스토리지 행은 기록이 남는 동안 목록에 남아 다시 정리할 수 있다). */}
      {r.error === null && r.untried !== null && r.untried > 0 && (
        <p>{`아직 정리하지 않은 기록 ${r.untried}개 — 다시 정리하면 이어서 지웁니다.`}</p>
      )}
      {r.deletedRequests > 0 && <p>결과 파일·파드 정리는 백그라운드에서 이어집니다.</p>}
    </div>
  );
}

function PurgeDialog({ row, purge, progress, setProgress, onResultFocus }: {
  row: UnregisteredStorage;
  purge: ReturnType<typeof usePurgeUnregisteredStorage>;
  progress: number | null;
  setProgress: (n: number | null) => void;
  onResultFocus: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [snap, setSnap] = useState<UnregisteredStorage>(row);
  const [ack, setAck] = useState(false);
  // 같은 틱 두 번 클릭 가드(작업 삭제 창과 같은 이유 -- isPending 은 예약된 재렌더 뒤에야 보인다).
  const sending = useRef(false);
  const succeeded = useRef(false);
  const mine = purge.variables?.storage_name === snap.storage_name;
  const busy = purge.isPending && mine;
  const onOpenChange = (o: boolean) => {
    if (!o && busy) return;                       // 진행 중엔 닫지 않는다(Esc·바깥 클릭)
    if (o) { setSnap(row); setAck(false); succeeded.current = false; if (purge.isError) purge.reset(); }
    setOpen(o);
  };
  const nb = snap.batches.length;
  const children = snap.batches.reduce((a, b) => a + b.request_count, 0);
  const nm = snap.mixed_batches.length;
  const confirm = () => {
    if (sending.current || purge.isPending) return;
    sending.current = true;
    setProgress(0);
    purge.mutate({ storage_name: snap.storage_name, onProgress: setProgress }, {
      onSuccess: () => { succeeded.current = true; setOpen(false); },
      onSettled: () => { sending.current = false; setProgress(null); },
    });
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg" title="미등록 스토리지 기록 정리" busy={busy}
            onCloseAutoFocus={(e) => { if (succeeded.current) { e.preventDefault(); onResultFocus(); } }}
            trigger={
              <Button variant="danger" className="gap-1.5" disabled={row.request_count === 0 || purge.isPending}
                      aria-label={`${row.storage_name} 기록 정리`}
                      title={row.request_count === 0
                        ? "여기서 정리할 수 있는 작업 기록이 없습니다(섞인 배치는 전체 작업에서 배치 단위로)" : undefined}>
                <Trash2 aria-hidden="true" className="h-4 w-4 shrink-0" />기록 정리
              </Button>}>
      <div className="space-y-3 text-sm text-ink/70 break-keep">
        <p className="font-medium text-ink [overflow-wrap:anywhere]">
          {`${snap.storage_name} 의 scan 작업 기록 ${snap.request_count}개를 영구 삭제합니다. 되돌릴 수 없습니다.`}
        </p>
        <p>
          {`단일 작업 ${snap.single_count}개` + (nb > 0 ? ` · 배치 ${nb}개(작업 ${children}개)` : "")
            + " — 성공·실패·취소된 scan 을 모두 지웁니다."}
        </p>
        <div role="note" className="flex items-start gap-2 rounded-lg border border-attn/30 bg-attnbg px-3 py-2 text-ink">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-attn" aria-hidden />
          <p className="min-w-0 flex-1 break-keep">
            {`사용량 분석(목록·이력 차트·CSV)에서 이 스토리지의 대상 ${snap.target_count}개(성공 scan `
              + `${snap.succeeded_scan_count}개)가 사라집니다.`}
          </p>
        </div>
        {nb > 0 && <p>배치는 배치 기록(항목 목록·이름·실행 설정)과 함께 지워집니다.</p>}
        {nm > 0 && (
          <p>
            {`다른 스토리지 항목이나 다른 작업이 섞인 배치 ${nm}개(작업 `
              + `${snap.mixed_batches.reduce((a, b) => a + b.request_count, 0)}개)는 지우지 않습니다 — 배치 상세에서 확인한 뒤 `
              + "전체 작업에서 배치 단위로 지우세요."}
          </p>
        )}
        <div className="space-y-1">
          <p>
            함께 지워지는 것: 요청·잡 기록(상태 이력·결과·진단 이벤트·실패 시 보관한 로그), 결과 파일(scan 리포트·실행 출력),
            남아 있는 파드와 그 로그. 대시보드 잡 통계에서도 빠지고, 감사 로그에는 누가·언제·무엇을 지웠는지 남습니다.
          </p>
          <p className="font-medium text-ink">
            스토리지의 실제 데이터는 건드리지 않습니다 — 이 스토리지 이름의 rm·sync 작업 기록도 그대로 남습니다.
          </p>
        </div>
        <label className="flex items-center gap-2 text-ink">
          <input type="checkbox" className="h-4 w-4" checked={ack} disabled={busy}
                 onChange={(e) => setAck(e.target.checked)} />
          되돌릴 수 없음을 확인했습니다
        </label>
        {purge.isError && mine && <p role="alert" className="text-bad">{(purge.error as ApiError).message}</p>}
        <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
          <span role="status" className="sr-only">{busy ? `삭제 중… ${progress ?? 0}개` : ""}</span>
          <span aria-hidden className="mr-auto" />
          <Button variant="ghost" disabled={busy} onClick={() => setOpen(false)}>닫기</Button>
          <Button disabled={!ack || snap.request_count === 0} aria-disabled={busy || undefined}
                  className={busy ? "cursor-not-allowed opacity-50" : ""} onClick={confirm}>
            {busy ? `삭제 중… ${progress ?? 0}개` : `${snap.request_count}개 영구 삭제`}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

export function UnregisteredStoragesPanel() {
  const q = useUnregisteredStorages();
  const purge = usePurgeUnregisteredStorage();
  const [progress, setProgress] = useState<number | null>(null);
  const resultRef = useRef<HTMLDivElement>(null);
  const focusResult = () => resultRef.current?.focus();
  // 다 지워 그 행(과 열린 확인 창)이 목록 재조회로 사라지면 포커스가 <body> 로 떨어진다 -- 결과 줄로 옮긴다. 창이 살아
  // 있으면(일부 제외 등) 창이 닫히며 onCloseAutoFocus 가 같은 일을 한다. **정리 한 번에 한 번만**(submittedAt): 뒤의
  // 재조회(창 포커스·다른 관리자의 변경)마다 결과 줄로 끌어올려 읽던 표에서 화면이 튀지 않게(리뷰).
  const focusedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!purge.isSuccess || focusedFor.current === purge.submittedAt) return;
    focusedFor.current = purge.submittedAt;
    if (document.activeElement === null || document.activeElement === document.body) focusResult();
  }, [purge.isSuccess, purge.submittedAt]);  // eslint-disable-line react-hooks/exhaustive-deps
  const rows = q.data ?? [];
  if (q.isLoading) return null;
  // 목록을 한 번도 못 받았을 때만 칸 전체를 오류로 -- 받은 뒤의 재조회 실패는 마지막 목록과 결과를 그대로 두고 한 줄로
  // 말한다(정리 결과·열린 확인 창이 재조회 실패 한 번에 사라지지 않게, 리뷰).
  if (q.data === undefined) {
    return (
      <Card>
        <p className="text-sm text-bad">{`미등록 스토리지 목록을 불러오지 못했습니다 — ${(q.error as ApiError | null)?.message ?? ""}`}</p>
      </Card>
    );
  }
  // 첫 회차 전체 실패(409 storage_registered·503 등)인데 그 행이 재조회로 이미 사라졌으면 창 안 오류가 보일 자리가 없다 --
  // 칸이 말한다(행이 있으면 창이 말한다). 예: 다른 관리자가 같은 이름을 다시 등록해 목록에서 빠졌다.
  const failedName = purge.isError ? purge.variables?.storage_name ?? null : null;
  const orphanError = failedName !== null && !rows.some((r) => r.storage_name === failedName);
  if (rows.length === 0 && !purge.data && !orphanError) return null;
  const now = Date.now();
  return (
    <Card>
      <section aria-labelledby="unregistered-storages-title" className="space-y-3">
        <div>
          <h2 id="unregistered-storages-title" className="text-base font-semibold">미등록 스토리지</h2>
          <p className="mt-1 text-sm text-ink/70 break-keep">
            스토리지 목록에서 삭제된 스토리지의 scan 기록이 사용량 분석에 남아 있습니다. 스토리지별로 기록을 한 번에
            정리할 수 있습니다.
          </p>
        </div>
        {q.isError && (
          <p className="text-sm text-bad">{`목록을 새로 고치지 못했습니다 — ${(q.error as ApiError).message}`}</p>
        )}
        {orphanError && (
          <p role="alert" className="text-sm text-bad [overflow-wrap:anywhere]">
            {`${failedName}: ${(purge.error as ApiError).message}`}</p>
        )}
        {purge.data && <PurgeResult r={purge.data} innerRef={resultRef} />}
        {rows.length > 0 && (
          <ul aria-label="미등록 스토리지 목록" className="divide-y divide-line rounded-lg border border-line">
            {rows.map((row) => (
              <li key={row.storage_name} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5">
                <div className="min-w-0 flex-1 space-y-0.5">
                  <p className="font-medium text-ink [overflow-wrap:anywhere]">{row.storage_name}</p>
                  <p className="text-xs text-ink/70 [overflow-wrap:anywhere]">{lastKnownLine(row)}</p>
                  <p className="text-xs text-ink/70 break-keep">
                    {scaleLine(row)}
                    <span className="ml-2" title={kstStampOrDash(row.last_scan_at)}>
                      {`최근 스캔 ${agoText(row.last_scan_at, now)}`}</span>
                  </p>
                </div>
                <PurgeDialog row={row} purge={purge} progress={progress} setProgress={setProgress}
                             onResultFocus={focusResult} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </Card>
  );
}
