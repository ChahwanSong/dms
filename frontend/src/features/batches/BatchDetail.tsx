import { useParams } from "react-router-dom";
import { useBatch, useConfirmBatch, useRerunFailed, useCancelBatch, useRescanBatch } from "./useBatches";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { reasonText } from "../../lib/api";
export function BatchDetail() {
  const { batchId = "" } = useParams();
  const q = useBatch(batchId);
  const confirm = useConfirmBatch(batchId);
  const rerun = useRerunFailed(batchId);
  const cancel = useCancelBatch(batchId);
  const rescan = useRescanBatch(batchId);
  const b = q.data;
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">배치 {batchId.slice(0,12)}</h1>
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <StatusPill state={b?.status ?? "…"} />
            <span className="text-muted text-sm">{b?.operation} · 성공 {b?.succeeded_count}/실패 {b?.failed_count}/전체 {b?.item_count}</span>
            {/* 있을 때만 -- null/부재 = 비특권 현행. 한 개의 템플릿 리터럴 = 한 개의
                텍스트 노드(getByText 가 통으로 찾도록, 대시보드 관례). */}
            {b?.owner_username && (
              <span className="text-bad text-sm">{`소유자(특권) ${b.owner_username}`}</span>
            )}
          </div>
          <div className="flex gap-2">
            {b?.status === "PreviewReady" && <Button disabled={confirm.isPending} onClick={() => confirm.mutate()}>배치 확인</Button>}
            {b?.status === "Completed" && (b?.failed_count ?? 0) > 0 && <Button disabled={rerun.isPending} onClick={() => rerun.mutate()}>실패분 재실행</Button>}
            {/* 전체 재실행(:rescan): 종단 배치 한정(서버 가드 미러) — 성공 item 포함
                전부 재큐잉(성장 모니터링). "실패분 재실행"(실패만)과 공존한다 */}
            {(b?.status === "Completed" || b?.status === "Cancelled") && <Button disabled={rescan.isPending} onClick={() => rescan.mutate()}>전체 재실행</Button>}
            {(b?.status === "Running" || b?.status === "Previewing" || b?.status === "PreviewReady") && <Button variant="ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>취소</Button>}
          </div>
        </div>
      </Card>
      <Table>
        <thead><tr className="text-muted"><th className="py-2">#</th><th>대상</th><th>상태</th><th>사유</th></tr></thead>
        <tbody>
          {(b?.items ?? []).map((it) => (
            <tr key={it.seq} className="border-t border-black/5">
              <td className="py-2">{it.seq}</td>
              <td className="text-muted font-mono text-xs">{JSON.stringify(it.payload)}</td>
              <td><StatusPill state={it.status} /></td>
              <td className="text-bad text-xs">{reasonText(it.reason_code)}</td>
            </tr>))}
        </tbody>
      </Table>
    </section>
  );
}
