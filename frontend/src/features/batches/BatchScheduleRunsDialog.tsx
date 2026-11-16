import { useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { Table } from "../../components/ui/Table";
import { ApiError, reasonText } from "../../lib/api";
import { kstStamp, kstStampBare } from "../../lib/datetime";
import { batchStatusLabel } from "../../lib/jobState";
import { outcomeLabel, outcomeTone, type BatchScheduleRun } from "./batchSchedule";
import { useBatchScheduleRuns } from "./useBatchSchedules";

// 회차 기록(최근 50회): 언제(예정 시각) 무엇으로 판정했고(시작함·건너뜀·놓침·일시정지) 왜 그랬는지. 시작한 회차는 리셋이
// 0 으로 만든 직전 회차의 결과를 서버가 스냅숏으로 남겨 두므로(prev_*) 여기서 「직전 회차」로 보인다. 열 때만 읽는다.
export function runDetail(r: BatchScheduleRun): string {
  if (r.outcome === "fired") {
    const parts: string[] = [];
    // null = 기록 없음(해당 없음), 0 = 「0개」 — 둘을 뭉개지 않는다.
    if (r.requeued !== null) parts.push(`항목 ${r.requeued.toLocaleString("ko-KR")}개 다시 실행`);
    if (r.prev_status !== null || r.prev_item_count !== null) {
      const n = (v: number | null) => (v === null ? "?" : v.toLocaleString("ko-KR"));
      parts.push(`직전 회차 ${r.prev_status === null ? "?" : batchStatusLabel(r.prev_status)} · 성공 ${n(r.prev_succeeded)}`
        + ` · 실패 ${n(r.prev_failed)} · 전체 ${n(r.prev_item_count)}`);
    }
    return parts.length > 0 ? parts.join(" · ") : "—";
  }
  return r.batch_status !== null ? `배치 상태 ${batchStatusLabel(r.batch_status)}` : "—";
}

export function BatchScheduleRunsDialog({ scheduleId, label }: { scheduleId: string; label: string }) {
  const [open, setOpen] = useState(false);
  const q = useBatchScheduleRuns(scheduleId, open);
  return (
    <Dialog open={open} onOpenChange={setOpen} title={`실행 기록 — ${label}`} size="lg"
            trigger={<Button variant="ghost" aria-label={`${label} 기록`}>기록</Button>}>
      <p className="mb-3 text-xs text-muted">최근 50회까지 보입니다(오래된 기록은 스케줄당 200회를 넘으면 지워집니다).</p>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p>
       : q.isError ? <p className="text-sm text-bad">{(q.error as ApiError).message}</p>
       : (q.data ?? []).length === 0 ? <p className="text-muted">아직 판정한 회차가 없습니다</p>
       : (
        <Table boxed>
          <thead><tr className="text-muted whitespace-nowrap">
            <th>예정 시각(KST)</th><th>결과</th><th>사유</th><th>세부</th>
          </tr></thead>
          <tbody>
            {(q.data ?? []).map((r) => (
              <tr key={r.run_id}>
                <td className="whitespace-nowrap tabular-nums" title={`판정 ${kstStamp(r.at)}`}>{kstStampBare(r.scheduled_for)}</td>
                <td className={`whitespace-nowrap ${outcomeTone(r.outcome)}`}>{outcomeLabel(r.outcome)}</td>
                <td className="min-w-[12rem]">{r.reason_code === null ? "—" : reasonText(r.reason_code)}</td>
                <td className="min-w-[12rem] text-muted">{runDetail(r)}</td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      <div className="mt-3 flex justify-end">
        <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
      </div>
    </Dialog>
  );
}
