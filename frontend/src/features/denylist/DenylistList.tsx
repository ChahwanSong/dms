import { useEffect, useState } from "react";
import { useDenylist, useAllow } from "./useDenylist";
import { DenyDialog, SUBJECT_TYPES } from "./DenyDialog";
import { Table } from "../../components/ui/Table";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { ApiError } from "../../lib/api";
import type { DenyEntry } from "../../lib/types";

function ReleaseButton({ e }: { e: DenyEntry }) {
  const [open, setOpen] = useState(false);
  const allow = useAllow();
  useEffect(() => { if (!open) allow.reset(); }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="해제"
            trigger={<Button variant="ghost">해제</Button>}>
      <p className="text-sm text-muted mb-3">{e.subject} 을(를) denylist에서 해제할까요?</p>
      {allow.isError && <p className="text-bad text-sm mb-2">{(allow.error as ApiError).message}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setOpen(false)}>취소</Button>
        <Button onClick={() => allow.mutate({ subject_type: e.subject_type, subject: e.subject }, { onSuccess: () => setOpen(false) })}
                disabled={allow.isPending}>해제 확인</Button>
      </div>
    </Dialog>
  );
}

export function DenylistList() {
  const q = useDenylist();
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">denylist</h1>
        <DenyDialog trigger={<Button>대상 추가</Button>} />
      </div>
      {/* 유형 의미(사용자 요청 2026-08-19): 문구는 DenyDialog.SUBJECT_TYPES 가
          단일 진실 — 다이얼로그의 선택 캡션과 여기가 같은 데이터를 그린다 */}
      <InfoPanel>
        <p className="mb-2">
          등재된 대상의 <span className="font-medium">새 작업 제출을 차단</span>합니다
          — 제출 시점에 판정하며, 특권 실행 경로보다 먼저 적용됩니다.
        </p>
        <dl className="space-y-1 text-muted">
          {SUBJECT_TYPES.map((t) => (
            <div key={t.value} className="flex gap-2">
              <dt className="w-20 shrink-0 font-mono">{t.value}</dt>
              <dd>{t.desc}</dd>
            </div>
          ))}
        </dl>
      </InfoPanel>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : q.isError ? (
        <p className="text-bad">{(q.error as ApiError).message}</p>
      ) : (q.data ?? []).length === 0 ? (
        <p className="text-muted">등재된 대상이 없습니다</p>
      ) : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">대상 유형</th><th>대상</th><th>사유</th><th>작업</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((e) => (
              <tr key={`${e.subject_type}:${e.subject}`} className="border-t border-black/5">
                <td className="py-2">{e.subject_type}</td>
                <td>{e.subject}</td>
                <td className="text-muted">{e.reason ?? "—"}</td>
                <td className="py-2"><ReleaseButton e={e} /></td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
    </section>
  );
}
