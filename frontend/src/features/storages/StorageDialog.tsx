import { useEffect, useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import { useCreateStorage, useUpdateStorage } from "./useStorages";
import type { Storage } from "../../lib/types";
import { SCOPES, flagsFor, scopeOf } from "./storageScope";
import type { StorageScope } from "./storageScope";
import { KIND_CHOICES, PV_PATH_PRESETS, storageKindOf } from "../k8s/k8sTerms";
import { useStorageClasses } from "../k8s/useK8s";
import type { StorageKind } from "../k8s/types";
// 서버 식별자 ↔ 표시명(src/dms/repositories/storages.py _BACKENDS 와 같은 셋 --
// tests/test_storage_backends.py 가 value 집합의 일치를 고정한다).
export const BACKENDS = [
  { value: "cephfs", label: "CephFS" },
  { value: "gpfs", label: "IBM GPFS (Storage Scale)" },
  { value: "wekafs", label: "WekaFS" },
  { value: "lustre", label: "DDN Lustre (EXAScaler)" },
  { value: "purestorage", label: "Pure Storage (FlashBlade)" },
  { value: "netapp", label: "NetApp (ONTAP)" },
  { value: "onefs", label: "Dell PowerScale (OneFS)" },
  { value: "nfs", label: "NFS (csi-driver-nfs)" },
];

const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

/** PV·둘 다 스토리지의 경로 규칙 안내(dms-k8s 설계 D9·D11): 백엔드 프리셋이 있으면 그 템플릿(읽기 전용), 없으면 StorageClass
 *  화면에서 템플릿을 지정해야 한다는 사실. 프리셋 표는 서버 pv_paths.PRESETS 의 표시용 미러다(해석·검증은 서버). */
function PvPresetNote({ backend }: { backend: string }) {
  const preset = PV_PATH_PRESETS[backend];
  if (backend === "") return <p className="text-xs text-ink/70">백엔드를 고르면 PV 경로 규칙(프리셋)이 보입니다.</p>;
  if (preset === undefined) {
    return (
      <p className="text-xs text-attn">
        이 백엔드는 PV 경로 프리셋이 없습니다 — StorageClass 화면에서 템플릿 지정 필요(예: {"{volume}"} · {"{subdir}"} · {"{attr:<키>}"}).
      </p>
    );
  }
  return (
    <p className="text-xs text-ink/70 [overflow-wrap:anywhere]">
      PV 경로 규칙(백엔드 프리셋, 읽기 전용): <code className="rounded bg-panel px-1 font-mono text-ink">{preset}</code>{" "}
      — 관리 루트 아래 상대경로입니다. StorageClass 화면에서 StorageClass 마다 덮어쓸 수 있습니다.
    </p>
  );
}

/** 수정 창의 「연결된 StorageClass」 -- 매핑이 있는 스토리지를 파일시스템으로 바꾸면 서버가 거부한다
 *  (storage_has_sc_mappings) -- 무엇이 걸려 있는지 창 안에서 보인다. */
function MappedClasses({ name, enabled }: { name: string; enabled: boolean }) {
  const q = useStorageClasses(enabled);
  if (!enabled || q.isLoading) return null;
  if (q.isError) return <p className="text-xs text-ink/70">연결된 StorageClass 를 불러오지 못했습니다.</p>;
  const rows = (q.data ?? []).filter((r) => r.storage_name === name);
  return (
    <p className="text-xs text-ink/70 [overflow-wrap:anywhere]">
      {rows.length === 0 ? "연결된 StorageClass: 없음"
        : `연결된 StorageClass: ${rows.map((r) => `${r.name} (${r.cluster})`).join(", ")}`}
    </p>
  );
}

/** 종류(파일시스템 / PV / 둘 다) 라디오를 늘 보이고 바디에 kind 를 싣는다(dms-k8s 설계 D11 -- 2026-10-26 LDAP 정리로 이 라인은 k8s
 *  신원 모드 전용이라 옛 ldap 모드의 「kind 없는 바디」 갈래는 지웠다). */
export function StorageDialog({ mode, storage, trigger }: {
  mode: "create" | "edit"; storage?: Storage; trigger: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(storage?.storage_name ?? "");
  const [mount, setMount] = useState(storage?.mount_path ?? "");
  const [root, setRoot] = useState(storage?.managed_root ?? "");
  const [backend, setBackend] = useState(storage?.backend_type ?? "");
  const [scope, setScope] = useState<StorageScope>(storage ? scopeOf(storage) : "all");
  const [kind, setKind] = useState<StorageKind>(storage ? storageKindOf(storage) : "filesystem");
  const create = useCreateStorage(); const update = useUpdateStorage();
  useEffect(() => {
    if (!open) { create.reset(); update.reset(); return; }
    setName(storage?.storage_name ?? ""); setMount(storage?.mount_path ?? "");
    setRoot(storage?.managed_root ?? ""); setBackend(storage?.backend_type ?? "");
    setScope(storage ? scopeOf(storage) : "all");
    setKind(storage ? storageKindOf(storage) : "filesystem");
  }, [open, storage]);
  const m = mode === "create" ? create : update;
  // 수정에서도 지금 종류를 함께 싣는다 -- 서버가 생략을 「유지」로 읽든 「파일시스템」으로 읽든 종류가 바뀌지 않게.
  const kindBody = { kind };
  const submit = () => {
    if (mode === "create")
      create.mutate({ storage_name: name, mount_path: mount, managed_root: root, backend_type: backend,
                      ...flagsFor(scope), ...kindBody },
        { onSuccess: () => setOpen(false) });
    else
      update.mutate({ name, body: { mount_path: mount, managed_root: root, backend_type: backend,
                                    ...flagsFor(scope), ...kindBody } },
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
          </span>
          <span className="block text-muted text-xs">
            onefs · nfs 도 받습니다 — Dell PowerScale 은 onefs, csi-driver-nfs 로 붙는 NFS 서버는 nfs
          </span></label>
        {/* 스토리지 종류(dms-k8s 설계 D11): PV 주소가 해석되는 곳(pv·both)과 직접 경로 피커에 보이는 곳
            (filesystem·both)을 가른다. 등록 정보 요구는 종류별 안내가 말한다 -- PV·둘 다면 경로 규칙(백엔드 프리셋)을
            읽기 전용으로 보이고, 프리셋이 없는 백엔드는 StorageClass 화면의 템플릿이 필요하다는 사실을 말한다. */}
        <fieldset className="space-y-1.5">
          <legend className="mb-1">종류</legend>
          <div className="grid gap-1.5 sm:grid-cols-3">
            {KIND_CHOICES.map((o) => (
              <label key={o.value}
                     className={`flex items-center gap-2 rounded-lg border px-3 py-2 cursor-pointer ${
                       kind === o.value ? "border-accent/40 bg-infobg" : "border-line"}`}>
                <input type="radio" name="storage-kind" value={o.value} aria-label={o.label}
                       aria-describedby="storage-kind-help" checked={kind === o.value}
                       onChange={() => setKind(o.value)} />
                <span className="font-medium">{o.label}</span>
              </label>
            ))}
          </div>
          <p id="storage-kind-help" className="text-xs text-ink/70">
            {KIND_CHOICES.find((o) => o.value === kind)?.help}
          </p>
          {kind !== "filesystem" && <PvPresetNote backend={backend} />}
          {mode === "edit" && storage !== undefined && (
            <MappedClasses name={storage.storage_name} enabled={open} />
          )}
        </fieldset>
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
