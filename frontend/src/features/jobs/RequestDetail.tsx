import { useParams } from "react-router-dom";
import { useRequest, useRequestJobs } from "./useJobs";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";

export function RequestDetail() {
  const { requestId = "" } = useParams();
  const req = useRequest(requestId); const jobs = useRequestJobs(requestId);
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
          </Card>
        ))}
      </div>
    </section>
  );
}
