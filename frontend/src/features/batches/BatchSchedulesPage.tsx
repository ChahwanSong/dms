import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { ApiError, reasonText } from "../../lib/api";
import { kstStampBare, untilText } from "../../lib/datetime";
import { useNow } from "../../lib/useNow";
import {
  isDestructiveBatch, outcomeLabel, outcomeTone, scheduleEnabled, scheduleSummary,
} from "../../lib/batchSchedule";
import { storageText } from "../../lib/batchStorage";
import type { BatchSchedule } from "../../lib/types";
import { useMe } from "../auth/useAuth";
import { BatchScheduleDialog } from "./BatchScheduleDialog";
import { BatchScheduleRunsDialog } from "./BatchScheduleRunsDialog";
import { useBatchSchedules, useDeleteBatchSchedule, useToggleBatchSchedule } from "./useBatchSchedules";

// 배치 스케줄러(2026-11-16 사용자 결정): 정해진 KST 시각에 배치 「전체 재실행」을 건다(배치당 하나). 회차는 수동 :rescan 과
// 같은 CAS 경로라 진행 중인 배치는 그 회차를 건너뛰고 기록하며, sync·rm 은 회차마다 기존 확인 게이트를 그대로 거친다.
// 생성·수정·켜기는 배치 생성과 같은 특권 게이트(화면은 me.can_run_as_root 로 버튼을 잠근다 — 진짜 차단은 서버 403),
// 끄기·삭제는 관리자면 된다(유지보수 중에도 멈출 수 있어야 한다).

const BOX = "h-5 w-5 cursor-pointer align-middle";
const LOCKED_HINT = "root 실행 자격이 있는 관리자만 할 수 있습니다";

const labelOf = (s: BatchSchedule) => s.batch?.name ?? s.batch_id.slice(0, 12);
// 서버 판정(행의 최상위 destructive)이 있으면 그것을, 없으면 배치 요약의 연산·옵션으로(옵션을 모르면 sync 는 비파괴로
// 보이지만, 그때는 서버가 422 batch_schedule_ack_required 로 거부하고 화면이 동의 창을 연다 — 아래 enable).
const destructiveOf = (s: BatchSchedule) =>
  typeof s.destructive === "boolean" ? s.destructive : isDestructiveBatch(s.batch?.operation, s.batch?.options);

// 「다음 실행」 칸 — 남은 시간이 흐르므로 이 잎만 30초마다 다시 그린다(useNow 관례: 표 전체를 매 틱 그리지 않는다).
// 날짜 / 시각·남은 시간 두 줄: 회차는 분 단위라 초(늘 :00)는 덜고, 열 폭을 줄여 1280 에서 관리 열까지 한 화면에 든다(실측).
// 파싱 못 하는 값은 kstStampBare 가 원문을 돌려주므로 그대로 한 줄로 보인다(지어내지 않는다).
function NextRun({ iso }: { iso: string }) {
  const now = useNow(30_000, true);
  const stamp = kstStampBare(iso);
  const split = /^\d{4}-\d\d-\d\d \d\d:\d\d/.test(stamp);
  return (<>
    <span className="block tabular-nums">{split ? stamp.slice(0, 10) : stamp}</span>
    <span className="block text-xs text-muted tabular-nums">
      {split ? `${stamp.slice(11, 16)} · ${untilText(iso, now)}` : untilText(iso, now)}
    </span>
  </>);
}

function LastResult({ s }: { s: BatchSchedule }) {
  // null = 아직 판정한 회차가 없다 — 실패가 아니다(null ≠ 실패).
  if (s.last_outcome === null) return <span className="text-muted">아직 없음</span>;
  return (<>
    <span className={`block whitespace-nowrap ${outcomeTone(s.last_outcome)}`}>{outcomeLabel(s.last_outcome)}</span>
    {s.last_reason_code !== null && <span className="block text-xs">{reasonText(s.last_reason_code)}</span>}
    {s.last_run_at !== null && <span className="block text-xs text-muted tabular-nums">{kstStampBare(s.last_run_at)}</span>}
  </>);
}

// 파괴적 스케줄을 목록에서 켤 때의 동의 창(스케줄 창의 경고와 같은 문구 축). 열 때마다 동의가 풀린다.
function EnableAckDialog({ target, onClose, onConfirm, busy, error }: {
  target: BatchSchedule | null; onClose: () => void; onConfirm: () => void; busy: boolean; error: string | null;
}) {
  const [ack, setAck] = useState(false);
  useEffect(() => { setAck(false); }, [target]);
  // 닫힐 때 포커스(2026-11-16 리뷰): 이 창의 트리거는 포커스를 받을 수 없는 자리표시 span 이라 Radix 의 「트리거로 되돌리기」가
  // <body> 로 떨어진다 — 키보드·스크린리더 사용자가 표에서 자리를 잃는다. 창을 연 행의 「사용」 체크박스로 직접 돌려준다(닫힐
  // 때 target 은 이미 null 이라 마지막 대상의 id 를 기억한다). 행을 id 로 찾는다 — 이름(aria-label)은 겹칠 수 있다.
  const lastId = useRef<string | null>(null);
  useEffect(() => { if (target !== null) lastId.current = target.schedule_id; }, [target]);
  const restoreFocus = (e: Event) => {
    e.preventDefault();
    Array.from(document.querySelectorAll<HTMLInputElement>("input[data-schedule-toggle]"))
      .find((el) => el.dataset.scheduleToggle === lastId.current)?.focus();
  };
  const rm = target?.batch?.operation === "rm";
  return (
    <Dialog open={target !== null} onOpenChange={(o) => { if (!o && !busy) onClose(); }} title="스케줄 켜기"
            trigger={<span aria-hidden="true" />} busy={busy} onCloseAutoFocus={restoreFocus}>
      {target !== null && (
        <div className="space-y-3 text-sm">
          <p>{`${labelOf(target)} — ${scheduleSummary(target.kind, target.spec)}`}</p>
          <div role="note" aria-label="반복 삭제 경고"
               className="rounded-lg border border-bad/30 bg-badbg px-3 py-2 text-bad">
            <p className="font-medium">{rm
              ? "회차마다 이 배치의 대상 전체를 root 권한으로 다시 삭제합니다 — 되돌릴 수 없습니다"
              : "회차마다 delete 옵션으로 목적지에서 소스에 없는 항목을 root 권한으로 삭제합니다 — 되돌릴 수 없습니다"}</p>
            <p className="mt-1 text-xs">켜면 지금 배치의 대상·옵션에 동의한 것으로 기록되고, 다음 실행은 지금 이후 첫 회차입니다.</p>
            <label className="mt-2 flex items-start gap-2 font-medium">
              <input type="checkbox" className="mt-0.5" aria-required="true" checked={ack}
                     onChange={(e) => setAck(e.target.checked)} />
              <span>반복 실행이 되돌릴 수 없는 삭제를 만든다는 것을 이해했습니다(필수)</span>
            </label>
          </div>
          {error !== null && <p role="alert" className="text-bad">{error}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" disabled={busy} onClick={onClose}>취소</Button>
            <Button disabled={!ack || busy} onClick={onConfirm}>켜기</Button>
          </div>
        </div>
      )}
    </Dialog>
  );
}

export function BatchSchedulesPage() {
  const q = useBatchSchedules();
  const me = useMe();
  const canArm = me.data?.can_run_as_root === true;
  const toggle = useToggleBatchSchedule();
  const del = useDeleteBatchSchedule();
  const [params, setParams] = useSearchParams();
  const [createOpen, setCreateOpen] = useState(false);
  const [prefillBatch, setPrefillBatch] = useState<string | null>(null);
  const [editId, setEditId] = useState<string | null>(null);
  // 요청 중인 토글(스케줄 id → 원하는 값): 응답·재조회가 올 때까지 그 값을 그리고 칸을 잠근다(SyncPairsPanel 관례).
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [ackTarget, setAckTarget] = useState<BatchSchedule | null>(null);
  const [ackError, setAckError] = useState<string | null>(null);
  // 삭제 2단 확인(BatchDetail armedSeq 관례): 1단이 무장, 같은 행의 2단이 쏜다 — 단일 상태라 동시 무장이 없다.
  const [armedId, setArmedId] = useState<string | null>(null);

  const rows = q.data ?? [];
  const scheduledBatchIds = useMemo(() => new Set((q.data ?? []).map((s) => s.batch_id)), [q.data]);

  // ?batch=<id>(배치 상세의 「스케줄 추가」 링크): 목록이 도착한 뒤 한 번 처리한다 — 그 배치에 스케줄이 있으면 그 스케줄의
  // 수정 창, 없으면 그 배치를 고른 추가 창. 자격이 없으면 창을 열지 않는다(버튼과 같은 잠금). 처리하면 파라미터를 지운다
  // (닫은 뒤 새로고침·폴링이 창을 다시 열지 않게).
  const wanted = params.get("batch");
  useEffect(() => {
    if (wanted === null || q.data === undefined || me.data === undefined) return;
    if (canArm) {
      const existing = q.data.find((s) => s.batch_id === wanted);
      if (existing) setEditId(existing.schedule_id);
      else { setPrefillBatch(wanted); setCreateOpen(true); }
    }
    setParams((p) => { p.delete("batch"); return p; }, { replace: true });
  }, [wanted, q.data, me.data, canArm, setParams]);

  async function setEnabled(s: BatchSchedule, next: boolean, acknowledge = false) {
    const id = s.schedule_id;
    setActionError(null);
    setPending((p) => ({ ...p, [id]: next }));
    try {
      await toggle.mutateAsync({ id, enabled: next, acknowledge });
      return true;
    } catch (e) {
      const err = e as ApiError;
      // 화면이 파괴적이라고 몰랐던 스케줄(배치 옵션을 모르는 응답) — 서버가 동의를 요구하면 동의 창으로 넘긴다.
      if (next && !acknowledge && err.code === "batch_schedule_ack_required") { setAckError(null); setAckTarget(s); }
      else if (acknowledge) setAckError(err.message);
      else setActionError(`${labelOf(s)} ${next ? "켜기" : "끄기"} 실패: ${err.message}`);
      return false;
    } finally {
      setPending((p) => { const rest = { ...p }; delete rest[id]; return rest; });
    }
  }
  const onToggle = (s: BatchSchedule, next: boolean) => {
    if (next && destructiveOf(s)) { setAckError(null); setAckTarget(s); return; }
    void setEnabled(s, next);
  };
  const ackBusy = ackTarget !== null && ackTarget.schedule_id in pending;

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">배치 스케줄러</h1>
        <BatchScheduleDialog open={createOpen} scheduledBatchIds={scheduledBatchIds} initialBatchId={prefillBatch}
                             onOpenChange={(o) => { setCreateOpen(o); if (!o) setPrefillBatch(null); }}
                             trigger={<Button disabled={!canArm} title={canArm ? undefined : LOCKED_HINT}>스케줄 추가</Button>} />
      </div>
      <InfoPanel>
        <ul className="list-disc space-y-1 pl-5">
          <li>정해진 한국시간(KST)에 배치를 <span className="font-medium">「전체 재실행」</span>합니다 — 배치당 스케줄 하나.</li>
          <li>그 시각에 배치가 진행 중이면 그 회차는 건너뛰고 기록합니다(밀린 회차를 몰아서 실행하지 않습니다).</li>
          <li>sync·rm 은 회차마다 미리보기 → 「배치 확인」을 거칩니다(미리보기 후 자동 실행 배치는 자동). 확인 대기 중에는 이후
            회차도 건너뜁니다.</li>
          <li>만들거나 켠 관리자가 root 실행 자격을 잃거나, 삭제 배치(rm, delete 옵션 sync)의 대상·옵션이 바뀌면 자동으로
            일시정지됩니다 — 다시 켤 때 확인합니다.</li>
        </ul>
        {!canArm && me.data !== undefined && (
          <p className="mt-2 text-muted">{`스케줄 추가·수정·켜기는 ${LOCKED_HINT} — 끄기·삭제는 모든 관리자가 할 수 있습니다.`}</p>
        )}
      </InfoPanel>
      {actionError !== null && <p role="alert" className="text-sm text-bad">{actionError}</p>}
      {del.isError && <p role="alert" className="text-sm text-bad">{`삭제 실패: ${(del.error as ApiError).message}`}</p>}
      <Card>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p>
       : q.isError ? (
        // 오류 ≠ 빈 상태: 조회 실패를 「스케줄이 없습니다」로 접으면 장애를 무소식으로 오독한다.
        <p className="text-sm text-bad">{(q.error as ApiError).message}</p>
       ) : rows.length === 0 ? (
        <p className="text-muted">스케줄이 없습니다 — 「스케줄 추가」로 배치를 정해진 시각에 다시 돌릴 수 있습니다</p>
       ) : (
        <Table>
          {/* 머리줄은 한 줄(좁은 열에서 「배/치」로 꺾이지 않게) — 넘치면 Table 래퍼 안에서 가로 스크롤. */}
          <thead><tr className="text-muted whitespace-nowrap">
            <th className="py-2 pr-2">배치</th><th className="pr-2">작업</th><th className="pr-2">대상 스토리지</th>
            <th className="pr-2">주기</th><th className="pr-2">다음 실행(KST)</th><th className="pr-2">마지막 결과</th>
            <th className="pr-2">사용</th><th className="pr-2">소유</th><th>관리</th>
          </tr></thead>
          <tbody>
            {rows.map((s) => {
              const on = scheduleEnabled(s.enabled);
              const busy = s.schedule_id in pending;
              const checked = busy ? pending[s.schedule_id] : on;
              const label = labelOf(s);
              // 켜기는 특권 게이트라 자격이 없으면 잠근다. 끄기(이미 켜진 행)는 관리자면 된다.
              const lockOn = !on && !canArm;
              return (
                <tr key={s.schedule_id} className="border-t border-black/5 align-top">
                  {/* 최소 폭: 표가 좁아지면 긴 배치 이름 열이 한 글자 폭으로 접혀 세로로 늘어선다(실측) — JobsList 대상 열 관례. */}
                  <td className="py-2 pr-2 min-w-[8rem]">
                    {s.batch === null
                      ? <span className="text-muted">{`기록 없음 ${s.batch_id.slice(0, 12)}`}</span>
                      : <Link className="text-accent [overflow-wrap:anywhere]" to={`/admin/batches/${s.batch_id}`}>{label}</Link>}
                  </td>
                  <td className="py-2 pr-2 whitespace-nowrap">{s.batch?.operation ?? "—"}{s.batch?.auto_confirm === true && (
                    <span className={`ml-1 text-xs ${s.batch.operation === "rm" ? "text-bad" : "text-accent"}`}
                          title={s.batch.operation === "rm" ? "미리보기가 끝나면 「배치 확인」 없이 삭제"
                            : "미리보기가 끝나면 「배치 확인」 없이 실행"}>자동</span>
                  )}</td>
                  <td className="py-2 pr-2 min-w-[6rem] break-all">{storageText(s.batch?.operation, s.batch)}</td>
                  <td className="py-2 pr-2 min-w-[8rem]">{scheduleSummary(s.kind, s.spec)}</td>
                  <td className="py-2 pr-2 whitespace-nowrap">
                    {on && s.next_run_at !== null ? <NextRun iso={s.next_run_at} /> : "—"}
                  </td>
                  <td className="py-2 pr-2 min-w-[8rem]"><LastResult s={s} /></td>
                  <td className="py-2 pr-2">
                    <input type="checkbox" className={BOX} aria-label={`${label} 사용`}
                           data-schedule-toggle={s.schedule_id}
                           checked={checked} disabled={busy || lockOn}
                           title={lockOn ? `켜기는 ${LOCKED_HINT}` : undefined}
                           onChange={(e) => onToggle(s, e.target.checked)} />
                    {!on && s.paused_reason_code !== null && (
                      <span className="block break-keep text-xs text-bad" title={reasonText(s.paused_reason_code)}>자동 일시정지</span>
                    )}
                  </td>
                  <td className="py-2 pr-2 whitespace-nowrap" title={`만든 사람 ${s.created_by}`}>{s.armed_by}</td>
                  {/* td 를 flex 로 만들지 않는다(e2e L2) — 안쪽 div 만 flex, 버튼은 한 줄(L4). */}
                  <td className="py-2">
                    <div className="flex items-center gap-2 whitespace-nowrap">
                      <BatchScheduleDialog open={editId === s.schedule_id} schedule={s} scheduledBatchIds={scheduledBatchIds}
                                           onOpenChange={(o) => setEditId(o ? s.schedule_id : null)}
                                           trigger={<Button variant="ghost" aria-label={`${label} 수정`} disabled={!canArm}
                                                            title={canArm ? undefined : LOCKED_HINT}>수정</Button>} />
                      <BatchScheduleRunsDialog scheduleId={s.schedule_id} label={label} />
                      {armedId === s.schedule_id
                        ? <Button aria-label={`${label} 삭제 확인`} disabled={del.isPending}
                                  onClick={() => del.mutate(s.schedule_id, { onSettled: () => setArmedId(null) })}>삭제 확인</Button>
                        : <Button variant="ghost" aria-label={`${label} 삭제`}
                                  onClick={() => { del.reset(); setArmedId(s.schedule_id); }}>삭제</Button>}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </Table>)}
      </Card>
      <EnableAckDialog target={ackTarget} busy={ackBusy} error={ackError}
                       onClose={() => setAckTarget(null)}
                       onConfirm={() => {
                         if (ackTarget === null) return;
                         const t = ackTarget;
                         void setEnabled(t, true, true).then((ok) => { if (ok) setAckTarget(null); });
                       }} />
    </section>
  );
}
