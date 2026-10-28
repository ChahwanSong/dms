import { useEffect, useState } from "react";
import { TriangleAlert } from "lucide-react";
import type { DataJob } from "../../lib/types";
import { useConfirmJob } from "./useJobs";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { kstStampOrDash } from "../../lib/datetime";
import { kindsText, previewWarnings, readSummary, sumKinds, summaryTool } from "../../lib/toolSummary";
import { humanBytes } from "./format";

type PreviewJob = Pick<DataJob, "preview_summary" | "operation"> & Partial<Pick<DataJob, "tool">>;

// 미리보기(dry-run) 요약 한 줄. null(모름)과 0(정상값)을 뭉개지 않는다 — 러너는 카운트를 못 읽으면 null 을 준다.
// 2026-09-17 이전 잡은 preview_summary 자체가 없다(그때는 result_summary 를 읽어 항상 "(요약 없음)" 이었다 — 실행 결과
// 컬럼이라 컨펌 시점엔 비어 있다).
//
// d192(2026-10-28 dms-ssc 에서 이식, 포탈 괴리 [1]·[5]): 같은 files 가 도구마다 뜻이 다르다 -- nsync 는 「변경 예정」(복사·
// 디렉토리·링크·속성·**삭제** 합, 이미 같은 항목은 빠진다), dsync 는 「소스 전체」 walk 수(변경 수가 아니다), drm 은 지울 대상
// walk 수. 예전엔 셋 다 「복사 대상」이라 nsync 의 삭제 6개가 「복사 대상 7개」로 보였다. 종류별 수는 kindsText(lib/toolSummary)로.
// 크기 표기는 공용 format.humanBytes(예전 국소 사본과 같은 단위·자리수).
export function previewSummaryText(job: PreviewJob): string {
  const raw = job.preview_summary;
  if (raw == null) return "(요약 없음 — 이 잡은 미리보기 요약이 저장되지 않았습니다)";
  const s = readSummary(raw);
  const tool = summaryTool(s, job.tool);
  const n = (x: number | null) => (x === null ? "모름" : `${x.toLocaleString("ko-KR")}개`);
  let head: string;
  let tail: string;
  if (job.operation === "rm") {
    // rm 의 수는 drm walk 항목 수다(2026-10-12): 대상 경로 자신과 그 아래 파일·디렉터리·링크의 합계라 「파일 수」보다
    // 크다 -- 그 뜻을 같이 말한다. 0 은 「빈 대상」이 아니다: walk 는 대상이 있으면 대상 자신을 세므로(빈 디렉터리도 1)
    // 0 = 대상 경로를 찾지 못했다(오타·이미 삭제·권한). 모름(옛 잡)이면 개수가 있는 곳: 미리보기 출력의 walk 요약 줄.
    const files = s.targetItems ?? s.files;
    const rmNote = files === null
      ? " — 미리보기 출력(stdout.log)의 'Walked N items' 줄이 지울 항목 수입니다(목록은 앞·뒤 10개만 찍힙니다)"
      : files === 0 ? " — 대상 경로를 찾지 못했습니다(오타·이미 삭제·권한 — 미리보기 출력의 'Failed to stat' 확인)"
      : "(대상 경로 포함, 파일·디렉터리 합계)";
    head = `삭제 대상 ${n(files)}${rmNote}`;
    tail = s.bytes === null ? "" : ` · ${humanBytes(s.bytes)}`;
  } else if (tool === "nsync") {
    const total = s.files ?? sumKinds(s.planned);
    head = `변경 예정 ${n(total)}${s.planned !== null ? `(${kindsText(s.planned)})` : ""}`;
    const b = s.plannedBytes ?? s.bytes;
    tail = b === null ? " · 크기 모름" : ` · 복사할 크기 ${humanBytes(b)}`;
  } else if (tool === "dsync") {
    head = `소스 전체 ${n(s.sourceItems ?? s.files)}(변경 수는 실행 후 확정)`;
    tail = s.bytes === null ? "" : ` · ${humanBytes(s.bytes)}`;
  } else {
    // 도구를 모르는 옛 잡 -- 뜻을 단정하지 않는 중립 라벨.
    head = `미리보기 항목 ${n(s.files)}`;
    tail = s.bytes === null ? " · 크기 모름" : ` · ${humanBytes(s.bytes)}`;
  }
  // 정상 미리보기는 종료 코드 0 으로만 컨펌 대기에 온다(stepper) -- 0 이 아닌 값은 옛 데이터·스텁에서만 보이고, 그때는 신호다.
  const rc = s.returncode === null || s.returncode === 0 ? "" : ` · 미리보기 종료 코드 ${s.returncode}`;
  return `${head}${tail}${rc}`;
}

/** 컨펌 창의 주의 목록(삭제 예정·타입 교체 실패 예고·특수 파일·소스 읽기 오류 -- lib/toolSummary.previewWarnings). d193: 삭제
 *  꺼짐 + 비어 있지 않은 디렉토리 경고가 함께면 교체 문장이 「교체를 시도합니다」가 된다(previewWarnings). dms-ssc 의 LDAP 보조 그룹
 *  삭제 경고(groupDeleteWarning)는 dms-k8s 에 보조 그룹이 없어 옮기지 않았다. */
export function confirmWarnings(job: PreviewJob & Partial<Pick<DataJob, "options">>): string[] {
  if (job.preview_summary == null) return [];
  const s = readSummary(job.preview_summary);
  return previewWarnings(s, { op: job.operation, tool: summaryTool(s, job.tool), deleteOn: job.options?.delete === true });
}

// resultOwner(2026-10-26): sync 의 결과 소유 한 줄 -- 요청 상세 「목적지 소유」와 같은 값(requestSpec.ownership: 목적지 스토리지
// 기본 소유자 | 운영자가 고른 PV 루트 소유자(잰 값) | chown 지정값 | 옛 행 문구). 컨펌은 실행 승인이라 「결과가 누구 소유가 되나」를
// 승인하는 자리에서 보인다. 모르면(null·생략) 줄을 그리지 않는다(지어내지 않는다).
export function ConfirmDialog({ job, resultOwner = null }: { job: DataJob; resultOwner?: string | null }) {
  const [open, setOpen] = useState(false);
  const confirm = useConfirmJob(job.request_id);
  // 닫힐 때마다 에러를 비운다. onOpenChange만으로는 부족하다 — "닫기"는 setOpen(false)를
  // 직접 부르고 Radix는 그 경우 onOpenChange를 발화하지 않아 낡은 지문 만료·변경 409가
  // 재오픈 시 남는다(StoragesList DeleteButton 선례와 같은 처방).
  useEffect(() => { if (!open) confirm.reset(); }, [open]);
  const warnings = confirmWarnings(job);
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
        <pre className="bg-canvas rounded-lg p-3 whitespace-pre-wrap [overflow-wrap:anywhere]">{previewSummaryText(job)}</pre>
        {warnings.length > 0 && (
          // 미리보기가 이미 아는 위험(포탈 괴리 [1]·[6]) -- 컨펌 버튼 바로 위가 아니라 요약 바로 아래(읽는 순서 그대로).
          <div className="rounded-lg border border-attn/40 bg-attnbg/60 px-3 py-2">
            <p className="font-semibold text-ink">컨펌 전에 확인하세요</p>
            <ul aria-label="컨펌 전 주의 사항" className="mt-1 space-y-1">
              {warnings.map((w, i) => (
                <li key={i} className="flex items-start gap-1.5 text-ink">
                  <TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-attn" />
                  <span className="min-w-0 break-keep">{w}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {job.operation === "sync" && resultOwner !== null && (
          <p className="text-ink/70">결과 소유: <span className="font-medium text-ink">{resultOwner}</span></p>
        )}
        <p className="text-ink/70 [overflow-wrap:anywhere]">지문(fingerprint): <code>{job.preview_fingerprint}</code></p>
        <p className="text-ink/70">만료: {kstStampOrDash(job.preview_expires_at)}</p>
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
