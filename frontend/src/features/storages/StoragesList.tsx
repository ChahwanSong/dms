import { useEffect, useState } from "react";
import { useStorages, useUpdateStorage, useDeleteStorage } from "./useStorages";
import { StorageDialog } from "./StorageDialog";
import { Table } from "../../components/ui/Table";
import { StatusPill } from "../../components/ui/StatusPill";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ApiError } from "../../lib/api";
import { storagePillVariant } from "../../lib/jobState";
import type { Storage } from "../../lib/types";

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

export function StoragesList() {
  const q = useStorages(); const update = useUpdateStorage();
  const toggle = (s: Storage) => update.mutate({ name: s.storage_name,
    body: { mount_path: s.mount_path, managed_root: s.managed_root, backend_type: s.backend_type, enabled: s.enabled !== 1 } });
  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">스토리지</h1>
        <StorageDialog mode="create" trigger={<Button>스토리지 등록</Button>} />
      </div>
      {q.isLoading ? <p className="text-muted">불러오는 중…</p> : (
        <Table>
          <thead><tr className="text-muted"><th className="py-2">이름</th><th>백엔드</th><th>마운트</th><th>상태</th><th>활성</th><th>작업</th></tr></thead>
          <tbody>
            {(q.data ?? []).map((s) => (
              <tr key={s.storage_name} className="border-t border-black/5">
                <td className="py-2">{s.storage_name}</td><td>{s.backend_type}</td>
                {/* 스토리지 전용 storagePillVariant: Ready=ok, Degraded=busy(주의 --
                    planner 가 Degraded 에도 잡을 보낸다), Unknown 등=neutral. */}
                <td className="text-muted">{s.mount_path}</td><td><StatusPill state={s.status} variant={storagePillVariant(s.status)} /></td>
                <td>{s.enabled === 1 ? "on" : "off"}</td>
                {/* td 를 flex 컨테이너로 만들지 않는다: td 가 flex 면 표 레이아웃 계산에서
                    빠져나와 다른 열과 폭을 못 나눠 갖는다(9fbef86 이 계정 표에서 걷어낸
                    구조 -- e2e L2 가 감시). flex 는 td 안 div 가 진다. */}
                <td className="py-2">
                  <div className="flex items-center gap-2 whitespace-nowrap">
                    <StorageDialog mode="edit" storage={s} trigger={<Button variant="ghost">수정</Button>} />
                    <Button variant="ghost" onClick={() => toggle(s)}>{s.enabled === 1 ? "비활성화" : "활성화"}</Button>
                    <DeleteButton s={s} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      )}
      {update.isError && <p className="text-bad text-sm mt-2">{(update.error as ApiError).message}</p>}
    </section>
  );
}
