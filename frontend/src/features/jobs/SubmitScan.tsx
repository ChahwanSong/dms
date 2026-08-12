import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSubmitRequest } from "./useJobs";
import type { SubmitBody } from "./useJobs";
import { StoragePicker, field } from "./formFields";
import { useUserStorages } from "../storages/useUserStorages";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

const initial = {
  storage: "", target: "",
  topK: "", verbose: false, quiet: false,
  priority: "mid",
  ownerUsername: "",
};

export function SubmitScan() {
  const nav = useNavigate();
  const submit = useSubmitRequest();
  const storagesQ = useUserStorages();
  const [f, setF] = useState(initial);

  const storages = storagesQ.data ?? [];
  const loadingStorages = storagesQ.isLoading;

  const verboseQuietConflict = f.verbose && f.quiet;
  const blocked = submit.isPending || verboseQuietConflict || storagesQ.isError;

  const on = (k: keyof typeof initial) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (blocked) return;
    const options: Record<string, boolean | number> = {};
    if (f.verbose) options.verbose = true;
    if (f.quiet) options.quiet = true;
    if (f.topK.trim() !== "") options.top_k = Number(f.topK);

    const body: SubmitBody = {
      operation: "scan",
      storage: f.storage, target: f.target,
      options,
      priority: f.priority,
    };
    if (f.ownerUsername.trim()) body.owner_username = f.ownerUsername.trim();
    submit.mutate(body, { onSuccess: (r) => nav(`/jobs/${r.request_id}`) });
  }

  return (
    <Card className="max-w-xl">
      <h1 className="text-lg font-semibold mb-4">scan 실행</h1>
      <p className="text-sm text-muted mb-3">
        scan은 미리보기 확인 단계가 없습니다 — 제출하면 바로 실행됩니다.
      </p>
      <form className="space-y-3" onSubmit={handleSubmit}>
        {storagesQ.isError && (
          <p className="text-bad text-sm">{(storagesQ.error as ApiError).message}</p>
        )}
        <div className="grid grid-cols-2 gap-3">
          <StoragePicker label="스토리지" value={f.storage}
            onChange={(v) => setF({ ...f, storage: v })} storages={storages} loading={loadingStorages} />
          <label className="text-sm">대상 경로
            <input aria-label="대상 경로" className={field} value={f.target} onChange={on("target")} />
          </label>
        </div>

        <label className="text-sm block">top_k
          <input aria-label="top_k" type="number" min={1} max={1000000} className={field}
                 value={f.topK} onChange={on("topK")} />
        </label>

        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" aria-label="verbose" checked={f.verbose} onChange={on("verbose")} /> verbose
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
        </label>
        {verboseQuietConflict && (
          <p className="text-bad text-sm">verbose와 quiet은 함께 쓸 수 없습니다</p>
        )}

        <label className="text-sm block">우선순위
          <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
            <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
          </select>
        </label>

        <label className="text-sm block">관리자 특권 실행(root)
          <input aria-label="관리자 특권 실행(root)" className={field} value={f.ownerUsername} onChange={on("ownerUsername")} />
          <p className="text-muted text-xs mt-1">root로 실행되며, 입력한 사용자는 소유자로 기록됩니다</p>
        </label>

        {submit.isError && <p className="text-bad text-sm">{(submit.error as ApiError).message}</p>}
        <Button type="submit" disabled={blocked}>제출</Button>
      </form>
    </Card>
  );
}
