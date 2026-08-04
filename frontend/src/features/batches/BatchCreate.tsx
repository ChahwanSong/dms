import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateBatch } from "./useBatches";
import { parseBatchCsv } from "../../lib/csv";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";
export function BatchCreate() {
  const nav = useNavigate(); const create = useCreateBatch();
  const [op, setOp] = useState<"scan"|"sync">("scan");
  const [csv, setCsv] = useState(""); const [mc, setMc] = useState(2);
  const [note, setNote] = useState("");
  const { rows, errors } = parseBatchCsv(op, csv);
  return (
    <Card className="max-w-2xl">
      <h1 className="text-lg font-semibold mb-4">배치 생성</h1>
      <form className="space-y-3" onSubmit={(e) => { e.preventDefault();
        if (errors.length || rows.length === 0) return;
        create.mutate({ operation: op, max_concurrency: mc, options: {}, note: note || null, items: rows },
          { onSuccess: (r) => nav(`/admin/batches/${r.batch_id}`) }); }}>
        <label className="text-sm block">작업
          <select aria-label="작업" className={field} value={op}
                  onChange={(e) => setOp(e.target.value as "scan"|"sync")}>
            <option value="scan">scan</option><option value="sync">sync</option>
          </select></label>
        <label className="text-sm block">CSV ({op === "scan" ? "storage,target" : "source_storage,source,destination_storage,destination"})
          <textarea aria-label="CSV" className={`${field} h-40 font-mono`} value={csv}
                    onChange={(e) => setCsv(e.target.value)} /></label>
        <label className="text-sm block">동시 실행 상한
          <input aria-label="동시 실행 상한" type="number" min={1} className={field} value={mc}
                 onChange={(e) => setMc(Number(e.target.value))} /></label>
        <label className="text-sm block">메모
          <input aria-label="메모" className={field} value={note} onChange={(e) => setNote(e.target.value)} /></label>
        <div className="text-sm text-muted">파싱된 행: {rows.length}
          {errors.length > 0 && <span className="text-bad"> · 오류 {errors.length}: {errors[0]}</span>}</div>
        {create.isError && <p className="text-bad text-sm">{(create.error as ApiError).message}</p>}
        <Button type="submit" disabled={create.isPending || errors.length>0 || rows.length===0}>배치 생성</Button>
      </form>
    </Card>
  );
}
