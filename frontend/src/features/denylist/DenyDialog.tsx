import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useDeny } from "./useDenylist";

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";
const SUBJECT_TYPES = ["requester", "owner", "group"] as const;

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
            {SUBJECT_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
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
