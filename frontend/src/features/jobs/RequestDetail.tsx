import { useParams } from "react-router-dom";
import { useRequest, useRequestJobs, useCancelJob } from "./useJobs";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { isTerminal } from "../../lib/jobState";
import { ConfirmDialog } from "./ConfirmDialog";
import { Timeline } from "./Timeline";
import { JobViewer } from "./JobViewer";
import { ApiError } from "../../lib/api";

function durationText(from?: string, to?: string): string {
  if (!from || !to) return "—";
  const ms = new Date(to).getTime() - new Date(from).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}초`;
  const m = Math.floor(s / 60);
  return s % 60 === 0 ? `${m}분` : `${m}분 ${s % 60}초`;
}

function ResultSummary({ summary }: { summary: unknown }) {
  if (summary == null) return null;
  if (typeof summary === "object") {
    const entries = Object.entries(summary as Record<string, unknown>);
    if (!entries.length) return null;
    return (
      <dl className="text-sm grid grid-cols-[auto_1fr] gap-x-2 gap-y-0.5 mt-1">
        {entries.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd>{String(v)}</dd>
          </div>
        ))}
      </dl>
    );
  }
  return <p className="text-sm mt-1">{String(summary)}</p>;
}

export function RequestDetail() {
  const { requestId = "" } = useParams();
  const req = useRequest(requestId);
  const jobs = useRequestJobs(requestId);
  const cancel = useCancelJob(requestId);

  if (req.isLoading || jobs.isLoading) {
    return (
      <section className="space-y-4">
        <h1 className="text-lg font-semibold">요청 {requestId}</h1>
        <p className="text-muted">불러오는 중…</p>
      </section>
    );
  }

  if (req.isError) {
    return (
      <section className="space-y-4">
        <h1 className="text-lg font-semibold">요청 {requestId}</h1>
        <p className="text-bad">{(req.error as ApiError).message}</p>
      </section>
    );
  }

  const data = req.data;
  if (!data) return null;
  const transitions = data.transitions;
  const end = transitions[transitions.length - 1]?.at ?? data.updated_at;

  return (
    <section className="space-y-4">
      <h1 className="text-lg font-semibold">요청 {requestId}</h1>
      <Card>
        <div className="flex items-center gap-3">
          <StatusPill state={data.state} />
          <span className="text-muted text-sm">{data.operation}</span>
        </div>
        <dl className="text-sm grid grid-cols-[auto_1fr] gap-x-2 gap-y-1 mt-3">
          <dt className="text-muted">요청자</dt><dd>{data.requester_id}</dd>
          <dt className="text-muted">수행시간</dt><dd>{durationText(data.created_at, end)}</dd>
        </dl>
      </Card>
      <Card>
        <h2 className="text-sm font-semibold mb-2">전이 이력</h2>
        <Timeline transitions={transitions} />
      </Card>
      <div className="space-y-2">
        {(jobs.data ?? []).map((j) => (
          <Card key={j.job_id}>
            <div className="flex items-center justify-between">
              <span className="text-sm">{j.job_id}</span><StatusPill state={j.state} />
            </div>
            {j.reason_code && <p className="text-bad text-sm mt-1">{j.reason_code}</p>}
            <ResultSummary summary={j.result_summary} />
            {j.state === "ConfirmPending" && <div className="mt-2"><ConfirmDialog job={j} /></div>}
            {j.state !== "ConfirmPending" && !isTerminal(j.state) && (
              <div className="mt-2">
                <Button variant="ghost" disabled={cancel.isPending}
                        onClick={() => cancel.mutate(j.job_id)}>취소</Button>
              </div>
            )}
            <div className="mt-3">
              <Timeline transitions={j.transitions} />
            </div>
            <JobViewer jobId={j.job_id} />
          </Card>
        ))}
      </div>
    </section>
  );
}
