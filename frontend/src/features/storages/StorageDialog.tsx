import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useCreateStorage, useUpdateStorage } from "./useStorages";
import type { Storage } from "../../lib/types";
// 서버 식별자 ↔ 표시명(src/dms/repositories/storages.py _BACKENDS 와 같은 셋).
export const BACKENDS = [
  { value: "cephfs", label: "CephFS" },
  { value: "gpfs", label: "IBM GPFS (Storage Scale)" },
  { value: "wekafs", label: "WekaFS" },
];

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";
export function StorageDialog({ mode, storage, trigger }: {
  mode: "create" | "edit"; storage?: Storage; trigger: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(storage?.storage_name ?? "");
  const [mount, setMount] = useState(storage?.mount_path ?? "");
  const [root, setRoot] = useState(storage?.managed_root ?? "");
  const [backend, setBackend] = useState(storage?.backend_type ?? "");
  const [enabled, setEnabled] = useState(storage ? storage.enabled === 1 : true);
  const create = useCreateStorage(); const update = useUpdateStorage();
  useEffect(() => {
    if (!open) { create.reset(); update.reset(); return; }
    setName(storage?.storage_name ?? ""); setMount(storage?.mount_path ?? "");
    setRoot(storage?.managed_root ?? ""); setBackend(storage?.backend_type ?? "");
    setEnabled(storage ? storage.enabled === 1 : true);
  }, [open, storage]);
  const m = mode === "create" ? create : update;
  const submit = () => {
    if (mode === "create")
      create.mutate({ storage_name: name, mount_path: mount, managed_root: root, backend_type: backend },
        { onSuccess: () => setOpen(false) });
    else
      update.mutate({ name, body: { mount_path: mount, managed_root: root, backend_type: backend, enabled } },
        { onSuccess: () => setOpen(false) });
  };
  return (
    <Dialog open={open} onOpenChange={setOpen} title={mode === "create" ? "스토리지 등록" : "스토리지 수정"} trigger={trigger}>
      <form className="space-y-3 text-sm" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label className="block">스토리지 이름
          <input aria-label="스토리지 이름" className={field} value={name} disabled={mode === "edit"}
                 onChange={(e) => setName(e.target.value)} /></label>
        <label className="block">마운트 경로
          <input aria-label="마운트 경로" className={field} value={mount} onChange={(e) => setMount(e.target.value)} /></label>
        <label className="block">관리 루트
          <input aria-label="관리 루트" className={field} value={root} onChange={(e) => setRoot(e.target.value)} /></label>
        <label className="block">백엔드
          {/* 2026-09-09 사용자 보고: 자유 입력에 "IBM GPFS" 를 넣어 invalid_storage.
              서버(repositories/storages._BACKENDS)는 식별자 셋만 받는다 -- 표시명은
              사람에게, 값은 식별자로. 기존 행의 알 수 없는 값(구형 "ceph" 류)은 편집
              화면에서 잃지 않도록 그대로 한 옵션으로 둔다. */}
          <select aria-label="백엔드" className={field} value={backend}
                  onChange={(e) => setBackend(e.target.value)}>
            <option value="">선택</option>
            {BACKENDS.map((b) => <option key={b.value} value={b.value}>{b.label}</option>)}
            {backend !== "" && !BACKENDS.some((b) => b.value === backend) && (
              <option value={backend}>{`${backend} (알 수 없는 값)`}</option>
            )}
          </select>
          <span className="block text-muted text-xs mt-1">
            서버가 받는 값은 cephfs · gpfs · wekafs 세 식별자입니다 — IBM Storage Scale(GPFS)은 gpfs
          </span></label>
        {mode === "edit" && (
          <label className="flex items-center gap-2"><input type="checkbox" checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)} /> 활성</label>)}
        {m.isError && <p className="text-bad">{(m.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={m.isPending}>저장</Button>
        </div>
      </form>
    </Dialog>
  );
}
