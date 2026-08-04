import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useCreateStorage, useUpdateStorage } from "./useStorages";
import type { Storage } from "../../lib/types";
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
  useEffect(() => {
    if (!open) return;
    setName(storage?.storage_name ?? ""); setMount(storage?.mount_path ?? "");
    setRoot(storage?.managed_root ?? ""); setBackend(storage?.backend_type ?? "");
    setEnabled(storage ? storage.enabled === 1 : true);
  }, [open, storage]);
  const create = useCreateStorage(); const update = useUpdateStorage();
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
          <input aria-label="백엔드" className={field} value={backend} onChange={(e) => setBackend(e.target.value)} /></label>
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
