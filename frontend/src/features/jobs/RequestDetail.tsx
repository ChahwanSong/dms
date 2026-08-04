import { useParams } from "react-router-dom";
import { useRequest, useRequestJobs, useCancelJob } from "./useJobs";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { isTerminal } from "../../lib/jobState";
import { ConfirmDialog } from "./ConfirmDialog";

export function RequestDetail() {
  const { requestId = "" } = useParams();
  const req = useRequest(requestId); const jobs = useRequestJobs(requestId);
  const cancel = useCancelJob(requestId);
  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">요청 {requestId}</h1>
      <Card>
        <div className="flex items-center gap-3">
          <StatusPill state={req.data?.state ?? "…"} />
          <span className="text-muted text-sm">{req.data?.operation}</span>
        </div>
      </Card>
      <div className="space-y-2">
        {(jobs.data ?? []).map((j) => (
          <Card key={j.job_id}>
            <div className="flex items-center justify-between">
              <span className="text-sm">{j.job_id}</span><StatusPill state={j.state} />
            </div>
            {j.reason_code && <p className="text-bad text-sm mt-1">{j.reason_code}</p>}
            {j.state === "ConfirmPending" && <div className="mt-2"><ConfirmDialog job={j} /></div>}
            {j.state !== "ConfirmPending" && !isTerminal(j.state) && (
              <div className="mt-2">
                <Button variant="ghost" disabled={cancel.isPending}
                        onClick={() => cancel.mutate(j.job_id)}>취소</Button>
              </div>
            )}
          </Card>
        ))}
      </div>
    </section>
  );
}
