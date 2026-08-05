import { useParams } from "react-router-dom";
import { useBatch, useConfirmBatch, useRerunFailed, useCancelBatch } from "./useBatches";
import { Card } from "../../components/ui/Card";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
export function BatchDetail() {
  const { batchId = "" } = useParams();
  const q = useBatch(batchId);
  const confirm = useConfirmBatch(batchId);
  const rerun = useRerunFailed(batchId);
  const cancel = useCancelBatch(batchId);
  const b = q.data;
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">배치 {batchId.slice(0,12)}</h1>
      <Card>
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <StatusPill state={b?.status ?? "…"} />
            <span className="text-muted text-sm">{b?.operation} · 성공 {b?.succeeded_count}/실패 {b?.failed_count}/전체 {b?.item_count}</span>
          </div>
          <div className="flex gap-2">
            {b?.status === "PreviewReady" && <Button disabled={confirm.isPending} onClick={() => confirm.mutate()}>배치 확인</Button>}
            {b?.status === "Completed" && (b?.failed_count ?? 0) > 0 && <Button disabled={rerun.isPending} onClick={() => rerun.mutate()}>실패분 재실행</Button>}
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
              <td className="text-bad text-xs">{it.reason_code ?? ""}</td>
            </tr>))}
        </tbody>
      </Table>
    </section>
  );
}
