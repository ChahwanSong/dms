import { useEffect, useState } from "react";
import type { DataJob } from "../../lib/types";
import { useConfirmJob } from "./useJobs";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { kstStampOrDash } from "../../lib/datetime";

// JobViewer.humanBytes 와 같은 국소 사본 관례("표시 하나를 위해 공용 모듈은 이르다").
const BYTE_UNITS: [string, number][] = [
  ["TiB", 1024 ** 4], ["GiB", 1024 ** 3], ["MiB", 1024 ** 2], ["KiB", 1024],
];
function humanBytes(bytes: number): string {
  for (const [unit, size] of BYTE_UNITS) {
    if (bytes >= size) return `${(bytes / size).toFixed(1)} ${unit}`;
  }
  return `${bytes} B`;
}

// 미리보기(dry-run) 요약 한 줄. null(모름)과 0(정상값)을 뭉개지 않는다 — 러너는
// 카운트를 못 읽으면 null 을 준다(summary.json 계약 {returncode, files, bytes}).
// 2026-09-17 이전 잡은 preview_summary 자체가 없다(그때는 result_summary 를 읽어
// 항상 "(요약 없음)" 이었다 — 실행 결과 컬럼이라 컨펌 시점엔 비어 있다).
export function previewSummaryText(job: Pick<DataJob, "preview_summary" | "operation">): string {
  const s = job.preview_summary;
  if (s == null) return "(요약 없음 — 이 잡은 미리보기 요약이 저장되지 않았습니다)";
  const files = s.files == null ? "모름" : `${s.files.toLocaleString()}개`;
  const bytes = s.bytes == null ? null : humanBytes(s.bytes);
  const head = job.operation === "rm" ? `삭제 대상 ${files}` : `복사 대상 ${files}`;
  const tail = bytes === null ? (job.operation === "rm" ? "" : " · 크기 모름") : ` · ${bytes}`;
  const rc = s.returncode == null ? "" : s.returncode === 0 ? "" : ` · dry-run 종료코드 ${s.returncode}`;
  return `${head}${tail}${rc}`;
}

export function ConfirmDialog({ job }: { job: DataJob }) {
  const [open, setOpen] = useState(false);
  const confirm = useConfirmJob(job.request_id);
  // 닫힐 때마다 에러를 비운다. onOpenChange만으로는 부족하다 — "닫기"는 setOpen(false)를
  // 직접 부르고 Radix는 그 경우 onOpenChange를 발화하지 않아 낡은 지문 만료·변경 409가
  // 재오픈 시 남는다(StoragesList DeleteButton 선례와 같은 처방).
  useEffect(() => { if (!open) confirm.reset(); }, [open]);
  // 문구(사용자 결정 2026-09-17): "미리보기 확인" 은 "미리보기를 열어 본다" 로 읽혀
  // 애매했다 — 이 버튼은 dry-run 결과를 보고 **실행을 승인**하는 행위라 "작업 컨펌".
  return (
    <Dialog open={open} onOpenChange={setOpen} title={`${job.operation} 작업 컨펌`}
            trigger={<Button>작업 컨펌</Button>}>
      <div className="space-y-2 text-sm">
        <p className="text-muted">
          미리보기(dry-run)가 끝났습니다. 아래 결과를 확인하고 컨펌하면 실제 실행이 시작됩니다.
        </p>
        <pre className="bg-canvas rounded-lg p-3 whitespace-pre-wrap">{previewSummaryText(job)}</pre>
        <p className="text-muted">지문(fingerprint): <code>{job.preview_fingerprint}</code></p>
        <p className="text-muted">만료: {kstStampOrDash(job.preview_expires_at)}</p>
        {confirm.isError && <p className="text-bad">{(confirm.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" onClick={() => setOpen(false)}>닫기</Button>
          <Button disabled={confirm.isPending || !job.preview_fingerprint}
                  onClick={() => confirm.mutate(
                    { jobId: job.job_id, fingerprint: job.preview_fingerprint! },
                    { onSuccess: () => setOpen(false) })}>컨펌</Button>
        </div>
      </div>
    </Dialog>
  );
}
