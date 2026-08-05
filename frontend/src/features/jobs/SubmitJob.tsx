import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSubmitRequest } from "./useJobs";
import type { SubmitBody } from "./useJobs";
import { useUserStorages } from "../storages/useUserStorages";
import { useMe } from "../auth/useAuth";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
import type { UserStorage } from "../../lib/types";

export const field = "mt-1 w-full rounded-lg border border-black/10 px-3 py-2";

export function StoragePicker({ label, value, onChange, storages, loading }: {
  label: string; value: string; onChange: (v: string) => void;
  storages: UserStorage[]; loading: boolean;
}) {
  return (
    <label className="text-sm">{label}
      <select aria-label={label} className={field} value={value} disabled={loading}
              onChange={(e) => onChange(e.target.value)}>
        <option value="">{loading ? "불러오는 중…" : "선택하세요"}</option>
        {storages.map((s) => (
          <option key={s.storage_name} value={s.storage_name}>
            {s.status === "Ready" || s.status === "Degraded"
              ? `${s.storage_name} (${s.status})`
              : `${s.storage_name} (${s.status} — 준비 안 됨)`}
          </option>
        ))}
      </select>
    </label>
  );
}

type Operation = "sync" | "rm";

const initial = {
  operation: "sync" as Operation,
  sourceStorage: "", sourcePath: "",
  destStorage: "", destPath: "",
  storage: "", target: "",
  delete: false, contents: false, direct: false,
  recursive: true, stat: false, lite: false, quiet: false,
  priority: "mid",
  ownerUsername: "",
};

function checkedOptions(opts: Record<string, boolean>): Record<string, boolean> {
  return Object.fromEntries(Object.entries(opts).filter(([, v]) => v));
}

export function SubmitJob() {
  const nav = useNavigate();
  const submit = useSubmitRequest();
  const storagesQ = useUserStorages();
  const me = useMe();
  const [f, setF] = useState(initial);

  const storages = storagesQ.data ?? [];
  const loadingStorages = storagesQ.isLoading;
  const isAdmin = me.data?.role === "admin";

  const recursiveMissing = f.operation === "rm" && !f.recursive;
  const statLiteConflict = f.operation === "rm" && f.stat && f.lite;
  const blocked = submit.isPending || recursiveMissing || statLiteConflict || storagesQ.isError;

  const on = (k: keyof typeof initial) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (blocked) return;
    const body: SubmitBody = f.operation === "sync"
      ? {
          operation: "sync",
          source_storage: f.sourceStorage, source: f.sourcePath,
          destination_storage: f.destStorage, destination: f.destPath,
          options: checkedOptions({ delete: f.delete, contents: f.contents, direct: f.direct, quiet: f.quiet }),
          priority: f.priority,
        }
      : {
          operation: "rm",
          storage: f.storage, target: f.target,
          options: checkedOptions({ recursive: f.recursive, stat: f.stat, lite: f.lite, quiet: f.quiet }),
          priority: f.priority,
        };
    if (isAdmin && f.ownerUsername.trim()) body.owner_username = f.ownerUsername.trim();
    submit.mutate(body, { onSuccess: (r) => nav(`/jobs/${r.request_id}`) });
  }

  return (
    <Card className="max-w-xl">
      <h1 className="text-lg font-semibold mb-4">작업 제출</h1>
      <form className="space-y-3" onSubmit={handleSubmit}>
        <label className="text-sm block">연산
          <select aria-label="연산" className={field} value={f.operation}
                  onChange={(e) => setF({ ...f, operation: e.target.value as Operation })}>
            <option value="sync">sync</option>
            <option value="rm">rm</option>
          </select>
        </label>

        {f.operation === "rm" && (
          <p className="rounded-lg border border-bad/40 bg-bad/5 p-3 text-bad text-sm">
            삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.
          </p>
        )}

        {storagesQ.isError && (
          <p className="text-bad text-sm">{(storagesQ.error as ApiError).message}</p>
        )}

        {f.operation === "sync" ? (
          <>
            <div className="grid grid-cols-2 gap-3">
              <StoragePicker label="소스 스토리지" value={f.sourceStorage}
                onChange={(v) => setF({ ...f, sourceStorage: v })} storages={storages} loading={loadingStorages} />
              <label className="text-sm">소스 경로
                <input aria-label="소스 경로" className={field} value={f.sourcePath} onChange={on("sourcePath")} />
              </label>
              <StoragePicker label="목적지 스토리지" value={f.destStorage}
                onChange={(v) => setF({ ...f, destStorage: v })} storages={storages} loading={loadingStorages} />
              <label className="text-sm">목적지 경로
                <input aria-label="목적지 경로" className={field} value={f.destPath} onChange={on("destPath")} />
              </label>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" aria-label="delete" checked={f.delete} onChange={on("delete")} /> delete
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" aria-label="contents" checked={f.contents} onChange={on("contents")} /> contents
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" aria-label="direct" checked={f.direct} onChange={on("direct")} /> direct
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
            </label>
          </>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-3">
              <StoragePicker label="스토리지" value={f.storage}
                onChange={(v) => setF({ ...f, storage: v })} storages={storages} loading={loadingStorages} />
              <label className="text-sm">대상 경로
                <input aria-label="대상 경로" className={field} value={f.target} onChange={on("target")} />
              </label>
            </div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" aria-label="재귀 삭제(필수)" checked={f.recursive} onChange={on("recursive")} /> 재귀 삭제(필수)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" aria-label="stat" checked={f.stat} onChange={on("stat")} /> stat
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" aria-label="lite" checked={f.lite} onChange={on("lite")} /> lite
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
            </label>
            {recursiveMissing && <p className="text-bad text-sm">재귀 옵션이 필요합니다</p>}
            {statLiteConflict && <p className="text-bad text-sm">stat과 lite는 함께 쓸 수 없습니다</p>}
          </>
        )}

        <label className="text-sm block">우선순위
          <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
            <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
          </select>
        </label>

        {isAdmin && (
          <label className="text-sm block">관리자 특권 실행(root)
            <input aria-label="관리자 특권 실행(root)" className={field} value={f.ownerUsername} onChange={on("ownerUsername")} />
            <p className="text-muted text-xs mt-1">root로 실행되며, 입력한 사용자는 소유자로 기록됩니다</p>
            {f.operation === "rm" && (
              <p className="text-bad text-xs">삭제가 root 권한으로 수행됩니다</p>
            )}
          </label>
        )}

        {submit.isError && <p className="text-bad text-sm">{(submit.error as ApiError).message}</p>}
        <Button type="submit" disabled={blocked}>제출</Button>
      </form>
    </Card>
  );
}
