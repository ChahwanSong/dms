import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useDeny } from "./useDenylist";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";
// 유형 의미(사용자 요청 2026-08-19): identity.resolve_job_identity 판정의 미러 —
// requester=제출 계정, owner=실행 신원(지정 없으면 제출자 본인). 목록 화면(DenylistList)의 설명 패널과 같은 문구를 쓴다.
// group(LDAP 그룹) 유형은 없다(2026-10-26 LDAP 정리 -- 그룹을 풀 디렉터리가 없어 서버가 추가를 422 denylist_group_unsupported
// 로 거부하고 평가하지도 않는다). 옛 group 행은 목록에 「평가 안 됨」으로 남아 해제만 할 수 있다.
// requester 설명(2026-11-11 정정): 판정은 planner 가 계획할 때(resolve_job_identity)라 제출은 201 로 접수된다 -- 옛 문구
// 「이 계정의 모든 제출을 차단」은 목록 패널 머리 문장(「제출은 접수된 뒤 거부로 끝나고」)과 위아래로 맞섰다.
export const SUBJECT_TYPES = [
  { value: "requester",
    desc: "작업을 제출한 계정 기준 — 이 계정이 낸 작업을 계획 단계에서 거부합니다." },
  { value: "owner",
    desc: "실행 신원(권한 기준 사용자) 기준 — 사용자를 지정한 작업은 제출자와 다를 수 있습니다." },
] as const;

export function DenyDialog({ trigger }: { trigger: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [subjectType, setSubjectType] = useState<string>("requester");
  const [subject, setSubject] = useState("");
  const [reason, setReason] = useState("");
  const deny = useDeny();
  useEffect(() => {
    if (!open) { deny.reset(); return; }
    setSubjectType("requester"); setSubject(""); setReason("");
  }, [open]);
  // 대상은 앞뒤 공백을 지워 보낸다(2026-11-11): 「alice 」로 저장된 규칙은 계정 alice 와 맞지 않아 차단이 조용히 무효였고,
  // 빈 대상은 PUT …/requester/ 가 어떤 라우트와도 안 맞아 영문 「Not Found」가 떴다. 빈 값이면 저장을 잠근다(서버도 422).
  const trimmedSubject = subject.trim();
  const subjectEmpty = trimmedSubject === "";
  const submit = () => {
    if (subjectEmpty) return;
    deny.mutate({ subject_type: subjectType, subject: trimmedSubject, reason: reason.trim() === "" ? null : reason },
      { onSuccess: () => setOpen(false) });
  };
  return (
    <Dialog open={open} onOpenChange={setOpen} title="대상 추가" trigger={trigger}>
      <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="block">대상 유형
          <select aria-label="대상 유형" className={field} value={subjectType}
                  onChange={(e) => setSubjectType(e.target.value)}>
            {SUBJECT_TYPES.map((t) => <option key={t.value} value={t.value}>{t.value}</option>)}
          </select>
        </label>
        {/* 선택된 유형의 의미를 그 자리에서 — 둘의 차이(제출자 vs 실행 신원)를 모르면 엉뚱한 유형에 등재해 차단이 새는
            사고가 된다 */}
        <p className="text-xs text-muted">
          {SUBJECT_TYPES.find((t) => t.value === subjectType)?.desc}
        </p>
        <label className="block">대상
          <input aria-label="대상" className={field} value={subject} onChange={(e) => setSubject(e.target.value)} />
          {subjectEmpty && <span className="block text-xs text-muted mt-1">대상을 입력하세요</span>}
        </label>
        <p className="text-xs text-muted">
          대상은 저장 시 앞뒤 공백을 지우고 소문자로 정규화됩니다. 이미 등재된 대상을 다시 추가하면 사유를 새 값으로 바꿉니다.
        </p>
        <label className="block">사유
          <input aria-label="사유" className={field} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {deny.isError && <p className="text-bad">{(deny.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={deny.isPending || subjectEmpty}>저장</Button>
        </div>
      </form>
    </Dialog>
  );
}
