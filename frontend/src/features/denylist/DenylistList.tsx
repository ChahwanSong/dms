import { useEffect, useState } from "react";
import { useDenylist, useAllow } from "./useDenylist";
import { DenyDialog, SUBJECT_TYPES } from "./DenyDialog";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { ApiError } from "../../lib/api";
import type { DenyEntry } from "../../lib/types";

function ReleaseButton({ e }: { e: DenyEntry }) {
  const [open, setOpen] = useState(false);
  const allow = useAllow();
  useEffect(() => { if (!open) allow.reset(); }, [open]);
  // 표 안 버튼은 nowrap(9fbef86 규약, 2026-11-04 기능 점검 #3 같은 부류) -- 좁은 폭에서 「해제」가 음절마다 꺾이지 않게.
  return (
    <Dialog open={open} onOpenChange={setOpen} title="해제"
            trigger={<Button variant="ghost" className="whitespace-nowrap">해제</Button>}>
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
          단일 진실 — 다이얼로그의 선택 캡션과 여기가 같은 데이터를 그린다.
          판정 시점(2026-11-11 정정): is_denied 는 identity.resolve_job_identity 안에서만 돌고 그 호출자는 planner 다 -- 제출
          (routes_requests.submit)은 보지 않고, 컨펌·stepper 도 다시 보지 않는다. 옛 문구 「제출 시점에 판정」은 사실과 달랐다. */}
      <InfoPanel>
        <p className="mb-2">
          등재된 대상의 <span className="font-medium">작업을 계획 단계에서 거부</span>합니다
          — 작업 계획 시점에 판정하므로 제출은 접수된 뒤 거부(identity_denied)로 끝나고, 등재 전에 낸 아직 계획되지 않은
          요청에도 걸립니다. 이미 계획돼 확인 대기·실행 중인 작업은 막지 않으며, 특권 실행 경로보다 먼저 적용됩니다.
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
      {/* Card 구획(2026-08-19): 운영 화면들과 같은 서피스 — 관리 그룹 일관화 */}
      <Card>
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
                <td className="py-2">{e.subject_type}
                  {/* 옛 LDAP 그룹 규칙 -- 서버가 평가하지 않는다(DenyDialog.SUBJECT_TYPES 주석). 해제만 남긴다. */}
                  {e.subject_type === "group" && (
                    <span className="block text-xs text-muted">평가 안 됨 — 이전 버전의 LDAP 그룹 규칙(해제하세요)</span>
                  )}
                </td>
                <td>{e.subject}</td>
                <td className="text-muted">{e.reason ?? "—"}</td>
                <td className="py-2"><ReleaseButton e={e} /></td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      </Card>
    </section>
  );
}
