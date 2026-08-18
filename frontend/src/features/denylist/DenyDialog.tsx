import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useDeny } from "./useDenylist";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";
// 유형 의미(사용자 요청 2026-08-19): identity.resolve_job_identity 판정의 미러 —
// requester=제출 계정, owner=실행 신원(지정 없으면 제출자 본인), group=실행
// 신원의 LDAP 그룹. 목록 화면(DenylistList)의 설명 패널과 같은 문구를 쓴다.
export const SUBJECT_TYPES = [
  { value: "requester",
    desc: "작업을 제출한 계정 기준 — 이 계정의 모든 제출을 차단합니다." },
  { value: "owner",
    desc: "파일을 다루는 실행 신원 기준 — 실행 신원을 지정한 작업은 제출자와 다를 수 있습니다." },
  { value: "group",
    desc: "실행 신원이 속한 LDAP 그룹 기준 — 그룹 구성원 전체를 한 번에 차단합니다." },
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
  const submit = () => {
    deny.mutate({ subject_type: subjectType, subject, reason: reason.trim() === "" ? null : reason },
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
        {/* 선택된 유형의 의미를 그 자리에서 — 셋의 차이(제출자 vs 실행 신원 vs
            그룹)를 모르면 엉뚱한 유형에 등재해 차단이 새는 사고가 된다 */}
        <p className="text-xs text-muted">
          {SUBJECT_TYPES.find((t) => t.value === subjectType)?.desc}
        </p>
        <label className="block">대상
          <input aria-label="대상" className={field} value={subject} onChange={(e) => setSubject(e.target.value)} />
        </label>
        <p className="text-xs text-muted">대상은 저장 시 소문자로 정규화됩니다.</p>
        <label className="block">사유
          <input aria-label="사유" className={field} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {deny.isError && <p className="text-bad">{(deny.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={deny.isPending}>저장</Button>
        </div>
      </form>
    </Dialog>
  );
}
