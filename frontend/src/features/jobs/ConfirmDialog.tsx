import { useState } from "react";
import type { DataJob } from "../../lib/types";
import { useConfirmJob } from "./useJobs";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

export function ConfirmDialog({ job }: { job: DataJob }) {
  const [open, setOpen] = useState(false);
  const confirm = useConfirmJob(job.request_id);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="sync 미리보기 확인"
            trigger={<Button>미리보기 확인</Button>}>
      <div className="space-y-2 text-sm">
        <p className="text-muted">아래 dry-run 결과를 확인하고 실행하세요.</p>
        <pre className="bg-canvas rounded-lg p-3 whitespace-pre-wrap">{
          job.result_summary == null ? "(요약 없음)"
            : typeof job.result_summary === "string" ? job.result_summary
            : JSON.stringify(job.result_summary, null, 2)
        }</pre>
        <p className="text-muted">지문(fingerprint): <code>{job.preview_fingerprint}</code></p>
        <p className="text-muted">만료: {job.preview_expires_at}</p>
        {confirm.isError && <p className="text-bad">{(confirm.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
          <Button disabled={confirm.isPending || !job.preview_fingerprint}
                  onClick={() => confirm.mutate(
                    { jobId: job.job_id, fingerprint: job.preview_fingerprint! },
                    { onSuccess: () => setOpen(false) })}>확인</Button>
        </div>
      </div>
    </Dialog>
  );
}
