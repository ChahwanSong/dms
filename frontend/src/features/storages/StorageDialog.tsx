import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useCreateStorage, useUpdateStorage } from "./useStorages";
import type { Storage } from "../../lib/types";
import { SCOPES, flagsFor, scopeOf } from "./storageScope";
import type { StorageScope } from "./storageScope";
// 서버 식별자 ↔ 표시명(src/dms/repositories/storages.py _BACKENDS 와 같은 셋 --
// tests/test_storage_backends.py 가 value 집합의 일치를 고정한다).
export const BACKENDS = [
  { value: "cephfs", label: "CephFS" },
  { value: "gpfs", label: "IBM GPFS (Storage Scale)" },
  { value: "wekafs", label: "WekaFS" },
  { value: "lustre", label: "DDN Lustre (EXAScaler)" },
  { value: "purestorage", label: "Pure Storage (FlashBlade)" },
  { value: "netapp", label: "NetApp (ONTAP)" },
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
  const [scope, setScope] = useState<StorageScope>(storage ? scopeOf(storage) : "all");
  const create = useCreateStorage(); const update = useUpdateStorage();
  useEffect(() => {
    if (!open) { create.reset(); update.reset(); return; }
    setName(storage?.storage_name ?? ""); setMount(storage?.mount_path ?? "");
    setRoot(storage?.managed_root ?? ""); setBackend(storage?.backend_type ?? "");
    setScope(storage ? scopeOf(storage) : "all");
  }, [open, storage]);
  const m = mode === "create" ? create : update;
  const submit = () => {
    if (mode === "create")
      create.mutate({ storage_name: name, mount_path: mount, managed_root: root, backend_type: backend,
                      ...flagsFor(scope) },
        { onSuccess: () => setOpen(false) });
    else
      update.mutate({ name, body: { mount_path: mount, managed_root: root, backend_type: backend,
                                    ...flagsFor(scope) } },
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
            서버가 받는 값은 cephfs · gpfs · wekafs · lustre · purestorage · netapp 식별자입니다 — IBM Storage Scale(GPFS)은 gpfs, DDN EXAScaler 는 lustre
          </span></label>
        {/* 사용 범위(2026-09-30): 옛 "활성" 체크박스의 자리 -- 비활성이 두 종류(완전 /
            사용자에게만)라 세 상태 라디오다. 등록 때도 고른다(관리자 전용으로 먼저 열어
            점검한 뒤 사용자에게 여는 흐름). */}
        <fieldset className="space-y-1.5">
          <legend className="mb-1">사용 범위</legend>
          {SCOPES.map((o) => (
            <label key={o.value}
                   className={`flex items-start gap-2 rounded-lg border px-3 py-2 cursor-pointer ${
                     scope === o.value ? o.tone : "border-line"}`}>
              <input type="radio" name="storage-scope" className="mt-0.5" value={o.value}
                     aria-label={o.label} checked={scope === o.value}
                     onChange={() => setScope(o.value)} />
              <span>
                <span className="font-medium">{o.label}</span>
                <span className="block text-xs text-muted">{o.help}</span>
              </span>
            </label>
          ))}
        </fieldset>
        {m.isError && <p className="text-bad">{(m.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2 pt-2">
          <Button variant="ghost" type="button" onClick={() => setOpen(false)}>취소</Button>
          <Button type="submit" disabled={m.isPending}>저장</Button>
        </div>
      </form>
    </Dialog>
  );
}
