import { useEffect, useState } from "react";
import { useStorages, useUpdateStorage, useDeleteStorage } from "./useStorages";
import { StorageDialog } from "./StorageDialog";
import { Table } from "../../components/ui/Table";
import { Card } from "../../components/ui/Card";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError } from "../../lib/api";
import { storagePillVariant } from "../../lib/jobState";
import type { Storage } from "../../lib/types";
import { SCOPES, flagsFor, scopeMeta, scopeOf } from "./storageScope";
import type { StorageScope } from "./storageScope";

function DeleteButton({ s }: { s: Storage }) {
  const [open, setOpen] = useState(false);
  const del = useDeleteStorage();
  // 닫힐 때마다 에러를 비운다. onOpenChange만으로는 부족하다 — "취소"는 setOpen(false)를
  // 직접 부르고 Radix는 그 경우 onOpenChange를 발화하지 않아 낡은 409가 재오픈 시 남는다.
  useEffect(() => { if (!open) del.reset(); }, [open]);
  return (
    <Dialog open={open} onOpenChange={setOpen} title="스토리지 삭제"
            trigger={<Button variant="ghost">삭제</Button>}>
      <p className="text-sm text-muted mb-3">{s.storage_name} 을(를) 삭제할까요?</p>
      {del.isError && <p className="text-bad text-sm mb-2">{(del.error as ApiError).message}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="ghost" onClick={() => setOpen(false)}>취소</Button>
        <Button onClick={() => del.mutate(s.storage_name, { onSuccess: () => setOpen(false) })}
                disabled={del.isPending}>삭제 확인</Button>
      </div>
    </Dialog>
  );
}

/** 행 안의 사용 범위 선택(2026-09-30 사용자 요청). 셀렉트 테두리·글자색이 지금 상태를 말하고
    바꾸면 즉시 저장한다 -- 예전 "비활성화/활성화" 토글 버튼의 자리다(완전 비활성은 진행 중
    잡의 비상 차단 경로라 확인 창을 두지 않는다 -- 서버 PUT 도 enabled 토글엔 가드가 없다). */
function ScopeSelect({ s, onChange, pending }: {
  s: Storage; onChange: (scope: StorageScope) => void; pending: boolean;
}) {
  const scope = scopeOf(s);
  return (
    <div>
      <select aria-label={`${s.storage_name} 사용 범위`} value={scope} disabled={pending}
              onChange={(e) => onChange(e.target.value as StorageScope)}
              className={`rounded-lg border px-2 py-1 text-xs font-semibold ${scopeMeta(scope).tone}`}>
        {SCOPES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      {/* 비활성 동안에도 DB 에 남아 있는 이전 범위 -- 다시 켤 때 모르고 사용자에게 여는 실수 방지 */}
      {scope === "off" && s.user_enabled === 0 && (
        <span className="mt-0.5 block text-xs text-muted">이전 설정: 관리자 전용</span>
      )}
    </div>
  );
}

export function StoragesList() {
  const q = useStorages(); const update = useUpdateStorage();
  const setScope = (s: Storage, scope: StorageScope) => update.mutate({ name: s.storage_name,
    body: { mount_path: s.mount_path, managed_root: s.managed_root, backend_type: s.backend_type,
            ...flagsFor(scope) } });
  const rows = q.data ?? [];
  const count = (scope: StorageScope) => rows.filter((s) => scopeOf(s) === scope).length;
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">스토리지</h1>
        <StorageDialog mode="create" trigger={<Button>스토리지 등록</Button>} />
      </div>
      {/* 사용 범위 범례 = 요약: 세 상태의 뜻과 지금 개수를 한눈에(표 안 셀렉트와 같은 색). */}
      <div className="grid gap-3 sm:grid-cols-3" role="region" aria-label="사용 범위 안내">
        {SCOPES.map((o) => (
          <div key={o.value} className="rounded-card border border-line bg-surface px-4 py-3">
            <div className="flex items-baseline justify-between">
              <span className={`flex items-center gap-2 text-sm font-semibold ${o.text}`}>
                <span className={`h-2 w-2 rounded-full ${o.dot}`} aria-hidden />{o.label}
              </span>
              <span className={`text-lg font-bold tabular-nums ${o.text}`}>{q.isLoading ? "–" : count(o.value)}</span>
            </div>
            <p className="mt-1 text-xs text-muted">{o.help}</p>
          </div>
        ))}
      </div>
      {/* Card 구획(2026-08-19): 대시보드·릴리스 등과 같은 서피스 — 목록 화면 일관화 */}
      <Card>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          {/* 관리 디렉토리(managed_root)는 마운트 옆에 둔다: 잡이 실제로 도는 뿌리는
              마운트가 아니라 이쪽이고(입력 경로는 이 아래 상대경로다), 운영자가
              스토리지↔경로 매핑을 이 표 한 줄에서 읽는 기준점이 된다. */}
          <thead><tr className="text-muted"><th className="py-2">이름</th><th>백엔드</th><th>마운트</th><th>관리 디렉토리</th><th>상태</th><th>사용 범위</th><th>작업</th></tr></thead>
          <tbody>
            {rows.map((s) => (
              <tr key={s.storage_name} className={`border-t border-line ${scopeOf(s) === "off" ? "opacity-60" : ""}`}>
                <td className="py-2 font-medium">{s.storage_name}</td><td>{s.backend_type}</td>
                {/* 스토리지 전용 storagePillVariant: Ready=ok, Degraded=busy(주의 --
                    planner 가 Degraded 에도 잡을 보낸다), Unknown 등=neutral. */}
                <td className="text-muted">{s.mount_path}</td>
                <td className="text-muted break-all">{s.managed_root}</td>
                <td><StatusPill state={s.status} variant={storagePillVariant(s.status)} /></td>
                <td>
                  <ScopeSelect s={s} pending={update.isPending}
                               onChange={(scope) => setScope(s, scope)} />
                </td>
                {/* td 를 flex 컨테이너로 만들지 않는다: td 가 flex 면 표 레이아웃 계산에서
                    빠져나와 다른 열과 폭을 못 나눠 갖는다(9fbef86 이 계정 표에서 걷어낸
                    구조 -- e2e L2 가 감시). flex 는 td 안 div 가 진다. */}
                <td className="py-2">
                  <div className="flex items-center gap-2 whitespace-nowrap">
                    <StorageDialog mode="edit" storage={s} trigger={<Button variant="ghost">수정</Button>} />
                    <DeleteButton s={s} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {update.isError && <p className="text-bad text-sm mt-2">{(update.error as ApiError).message}</p>}
      </Card>
    </section>
  );
}
