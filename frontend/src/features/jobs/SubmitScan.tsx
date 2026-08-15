import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSubmitRequest } from "./useJobs";
import type { SubmitBody } from "./useJobs";
import { StoragePicker, field } from "./formFields";
import { intFieldError } from "./optionRules";
import { useUserStorages } from "../storages/useUserStorages";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";

// dscan(1b93d54) 실측 옵션 미러: batch_files 0..10억(0 = 배칭 끔), broken_limit
// 0..10,000 — domain.py _OPTION_SPECS[SCAN]의 미러(발산 금지). 빈값 = 플래그
// 생략 = 도구 기본(batch_files 1,000,000 / broken_limit 100).
const initial = {
  storage: "", target: "",
  batchFiles: "", brokenLimit: "", verbose: false, quiet: false,
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
  // 즉답 미러(최종 심판은 서버 422 invalid_option) — 빈 문자열은 "미입력"이라 오류가 아니다.
  const batchFilesError = intFieldError("batch_files", f.batchFiles, 0, 1_000_000_000);
  const brokenLimitError = intFieldError("broken_limit", f.brokenLimit, 0, 10_000);
  const blocked = submit.isPending || verboseQuietConflict || storagesQ.isError
    || batchFilesError !== null || brokenLimitError !== null;

  const on = (k: keyof typeof initial) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (blocked) return;
    const options: Record<string, boolean | number> = {};
    if (f.verbose) options.verbose = true;
    if (f.quiet) options.quiet = true;
    if (f.batchFiles.trim() !== "") options.batch_files = Number(f.batchFiles.trim());
    if (f.brokenLimit.trim() !== "") options.broken_limit = Number(f.brokenLimit.trim());

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
      <h1 className="text-2xl font-bold mb-5">scan 실행</h1>
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

        {/* scan 은 프리필하지 않는다(sync 와 다름): dscan 도구 기본이 이미
            batch_files 1,000,000 이라 빈값 = 도구 기본이 곧 원하는 동작이고,
            placeholder 가 그 값을 말한다. sync 쪽 프리필의 「왜」는 optionRules. */}
        <label className="text-sm block">batch_files (선택 · 0..1,000,000,000 · 0 = 배칭 끔)
          <input aria-label="batch_files" type="number" min={0} max={1000000000}
                 placeholder="기본 1000000 · 0 = 배칭 끔"
                 className={field} value={f.batchFiles} onChange={on("batchFiles")} />
        </label>
        {batchFilesError && <p className="text-bad text-sm">{batchFilesError}</p>}

        <label className="text-sm block">broken_limit (선택 · 0..10,000 · 리포트에 보관할 파손 경로 수)
          <input aria-label="broken_limit" type="number" min={0} max={10000}
                 placeholder="기본 100"
                 className={field} value={f.brokenLimit} onChange={on("brokenLimit")} />
        </label>
        {brokenLimitError && <p className="text-bad text-sm">{brokenLimitError}</p>}

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

        {/* 라벨 정정(사용자 결정 2026-08-16): owner_username 은 소유자 기록이 아니라
            **잡의 실행 신원**이다(identity.resolve_job_identity: owner =
            owner_username or requester_id). 캡션 문구는 SubmitJob 과 한 글자도
            다르지 않게 유지한다 — 같은 필드가 화면마다 다른 말을 하면 그게 거짓말. */}
        <label className="text-sm block">실행 신원(선택)
          <input aria-label="실행 신원(선택)" className={field}
                 placeholder="예: cocoa.song"
                 value={f.ownerUsername} onChange={on("ownerUsername")} />
          <p className="text-muted text-xs mt-1">
            비우면 요청자 본인으로 실행됩니다. 다른 사용자를 지정하려면 특권
            요청자여야 하며, 그때 잡은 root 로 실행되고 지정한 사용자 신원으로
            파일을 다룹니다(LDAP 에 없는 계정도 지정할 수 있습니다).
          </p>
        </label>

        {submit.isError && <p className="text-bad text-sm">{(submit.error as ApiError).message}</p>}
        <Button type="submit" disabled={blocked}>제출</Button>
      </form>
    </Card>
  );
}
