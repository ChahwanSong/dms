import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSubmitRequest } from "./useJobs";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

export function SubmitSync() {
  const nav = useNavigate(); const submit = useSubmitRequest();
  const [f, setF] = useState({ ss: "", sp: "", ds: "", dp: "", del: false, priority: "mid" });
  const on = (k: string) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  return (
    <Card className="max-w-xl">
      <h1 className="text-lg font-semibold mb-4">작업 제출 · sync</h1>
      <form className="space-y-3" onSubmit={(e) => {
        e.preventDefault();
        submit.mutate(
          { operation: "sync", source_storage: f.ss, source: f.sp, destination_storage: f.ds, destination: f.dp,
            options: f.del ? { delete: true } : {}, priority: f.priority },
          { onSuccess: (r) => nav(`/jobs/${r.request_id}`) });
      }}>
        <div className="grid grid-cols-2 gap-3">
          <label className="text-sm">소스 스토리지<input aria-label="소스 스토리지" className={field} value={f.ss} onChange={on("ss")} /></label>
          <label className="text-sm">소스 경로<input aria-label="소스 경로" className={field} value={f.sp} onChange={on("sp")} /></label>
          <label className="text-sm">목적지 스토리지<input aria-label="목적지 스토리지" className={field} value={f.ds} onChange={on("ds")} /></label>
          <label className="text-sm">목적지 경로<input aria-label="목적지 경로" className={field} value={f.dp} onChange={on("dp")} /></label>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={f.del} onChange={on("del")} /> --delete (목적지 여분 삭제)
        </label>
        <label className="text-sm block">우선순위
          <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
            <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
          </select>
        </label>
        {submit.isError && <p className="text-bad text-sm">{(submit.error as ApiError).message}</p>}
        <Button type="submit" disabled={submit.isPending}>제출</Button>
      </form>
    </Card>
  );
}
