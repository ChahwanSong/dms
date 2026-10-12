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
  // rm 의 수는 drm walk 항목 수다(2026-10-12 -- 예전엔 dry-run 이 삭제 요약을 찍지 않아 늘 「모름」이었다): 대상 경로
  // 자신과 그 아래 파일·디렉터리·링크의 합계라 「파일 수」보다 크다 -- 그 뜻을 같이 말한다. 모름(옛 잡)이면 목록이 있는 곳을.
  // 0 은 「빈 대상」이 아니다: drm walk 는 대상이 있으면 대상 자신을 세므로(빈 디렉터리도 1) 0 = 대상 경로를 찾지
  // 못했다(오타·이미 삭제·권한) -- 지울 것이 없다는 사실과 그 이유를 함께 말한다(2026-10-12 리뷰).
  // 모름(옛 잡)이면 개수가 있는 곳: 미리보기 출력의 walk 요약 줄(목록은 앞·뒤 10개만 찍혀 셀 수 없다).
  const rmNote = s.files == null
    ? " — 미리보기 출력(stdout.log)의 'Walked N items' 줄이 지울 항목 수입니다(목록은 앞·뒤 10개만 찍힙니다)"
    : s.files === 0 ? " — 대상 경로를 찾지 못했습니다(오타·이미 삭제·권한 — 미리보기 출력의 'Failed to stat' 확인)"
    : "(대상 경로 포함, 파일·디렉터리 합계)";
  const head = job.operation === "rm" ? `삭제 대상 ${files}${rmNote}` : `복사 대상 ${files}`;
  const tail = bytes === null ? (job.operation === "rm" ? "" : " · 크기 모름") : ` · ${bytes}`;
  const rc = s.returncode == null ? "" : s.returncode === 0 ? "" : ` · dry-run 종료코드 ${s.returncode}`;
  return `${head}${tail}${rc}`;
}

// 보조 그룹 삭제 경고(2026-10-07 D15): 잡이 LDAP 보조 그룹을 달고 돌면 그 그룹 쓰기 권한이 있는 디렉터리의 남의
// 파일도 지울 수 있다(디렉터리 엔트리 삭제는 파일 소유가 아니라 디렉터리 쓰기 권한 -- sticky 가 없으면). 삭제가
// 일어나는 실행(rm, sync --delete)이고 그룹이 실제로 실렸을 때(applied)만 말한다 -- 배포 전 잡·그룹 없음엔 해당 없음.
export function groupDeleteWarning(job: Pick<DataJob, "operation" | "options" | "worker_pool">): boolean {
  if (job.worker_pool?.identity?.supplementary_gids_status !== "applied") return false;
  return job.operation === "rm" || job.options?.delete === true;
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
  // 본문 글자색은 ink/70(흰 4.94:1, AA). text-muted(#888, 3.54:1)는 장식 전용이다(요청 상세 스펙 §9) -- 이 창은
  // 컨펌하는 유일한 자리라 안내·지문·만료는 읽혀야 하는 글자다(2026-10-08 리뷰 3차, 스펙의 「무변경」 예외).
  return (
    <Dialog open={open} onOpenChange={setOpen} title={`${job.operation} 작업 컨펌`}
            trigger={<Button>작업 컨펌</Button>}>
      <div className="space-y-2 text-sm">
        <p className="text-ink/70">
          미리보기(dry-run)가 끝났습니다. 아래 결과를 확인하고 컨펌하면 실제 실행이 시작됩니다.
        </p>
        <pre className="bg-canvas rounded-lg p-3 whitespace-pre-wrap">{previewSummaryText(job)}</pre>
        <p className="text-ink/70">지문(fingerprint): <code>{job.preview_fingerprint}</code></p>
        <p className="text-ink/70">만료: {kstStampOrDash(job.preview_expires_at)}</p>
        {groupDeleteWarning(job) && (
          <p className="text-bad">보조 그룹으로 쓰기 권한을 받은 디렉토리에서는 다른 사용자의 파일도 지워질 수 있습니다.</p>
        )}
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
