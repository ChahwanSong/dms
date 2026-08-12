import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSubmitRequest } from "./useJobs";
import type { SubmitBody } from "./useJobs";
import { useUserStorages } from "../storages/useUserStorages";
import { useMe } from "../auth/useAuth";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { ApiError } from "../../lib/api";
// field·StoragePicker 는 formFields.tsx 로 이사(슬라이스 31 T3) -- T4 위저드화 때
// 이 파일이 통째로 갈려도 SubmitScan·ScanPaths 가 흔들리지 않게 결합을 끊었다.
import { StoragePicker, field } from "./formFields";

type Operation = "sync" | "rm";

const initial = {
  operation: "sync" as Operation,
  sourceStorage: "", sourcePath: "",
  destStorage: "", destPath: "",
  storage: "", target: "",
  delete: false, contents: false, direct: false,
  recursive: true, stat: false, lite: false, quiet: false,
  // 고급 sync 옵션 — 숫자도 문자열로 들고, 빈 문자열("")일 때만 "미입력"으로 생략한다.
  // truthy 검사 금지: "0"은 미입력이 아니라 범위 밖 클라이언트 검증 오류다.
  openNoatime: false,
  batchFiles: "", bufsize: "", chmod: "", chown: "",
  priority: "mid",
  ownerUsername: "",
};

// 서버 검증의 클라이언트 미러(즉답용) — 최종 심판은 서버 422 invalid_option 이다.
// domain.py:112-113(_CHMOD_ITEM_RE 콤마 항목별 fullmatch·_CHOWN_RE)의 미러.
const CHMOD_RE = /^[DF]?[0-7]{1,4}(,[DF]?[0-7]{1,4})*$/;
const CHOWN_RE = /^([A-Za-z_][A-Za-z0-9._-]{0,63})?(:[A-Za-z_][A-Za-z0-9._-]{0,63})?$/;

// 정수 범위 미러(domain.py:127-128 — batch_files 1..1,000,000 / bufsize 4096..1GiB).
// 빈 문자열은 "미입력"(생략 대상)이라 오류가 아니다.
function intFieldError(label: string, raw: string, lo: number, hi: number): string | null {
  const v = raw.trim();
  if (v === "") return null;
  const n = Number(v);
  if (!Number.isInteger(n) || n < lo || n > hi)
    return `${label}는 ${lo}..${hi} 범위의 정수여야 합니다`;
  return null;
}

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
  // 고급 옵션은 sync 전용이라 rm 으로 바꾸면(전송도 안 되므로) 차단 사유에서 빠진다.
  const batchFilesError = f.operation === "sync"
    ? intFieldError("batch_files", f.batchFiles, 1, 1_000_000) : null;
  const bufsizeError = f.operation === "sync"
    ? intFieldError("bufsize", f.bufsize, 4096, 1_073_741_824) : null;
  const chmodError = f.operation === "sync" && f.chmod.trim() !== "" && !CHMOD_RE.test(f.chmod.trim())
    ? "chmod 형식이 올바르지 않습니다 (예: D770,F660)" : null;
  const chownError = f.operation === "sync" && f.chown.trim() !== "" && !CHOWN_RE.test(f.chown.trim())
    ? "chown 형식이 올바르지 않습니다 (예: user:group)" : null;
  const advancedError = batchFilesError ?? bufsizeError ?? chmodError ?? chownError;
  const blocked = submit.isPending || recursiveMissing || statLiteConflict || storagesQ.isError
    || advancedError !== null;

  const on = (k: keyof typeof initial) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  function syncOptions(): SubmitBody["options"] {
    const options: SubmitBody["options"] = checkedOptions({
      delete: f.delete, contents: f.contents, direct: f.direct, quiet: f.quiet,
      open_noatime: f.openNoatime,
    });
    // 빈 문자열일 때만 생략 — 빈 chmod/chown 을 그대로 실으면 서버 fullmatch 가
    // 422 invalid_option 으로 거부한다(빈 값은 "옵션 없음"이지 "빈 값 지정"이 아니다).
    if (f.batchFiles.trim() !== "") options.batch_files = Number(f.batchFiles.trim());
    if (f.bufsize.trim() !== "") options.bufsize = Number(f.bufsize.trim());
    if (f.chmod.trim() !== "") options.chmod = f.chmod.trim();
    if (f.chown.trim() !== "") options.chown = f.chown.trim();
    return options;
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (blocked) return;
    const body: SubmitBody = f.operation === "sync"
      ? {
          operation: "sync",
          source_storage: f.sourceStorage, source: f.sourcePath,
          destination_storage: f.destStorage, destination: f.destPath,
          options: syncOptions(),
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
            {/* 기본 접힘 — 기존 동선(단순 sync 제출)을 바꾸지 않기 위해 <details> 로 숨긴다 */}
            <details className="rounded-lg border border-black/10 p-3">
              <summary className="cursor-pointer text-sm font-medium">고급 옵션</summary>
              <div className="mt-3 space-y-3">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="open_noatime" checked={f.openNoatime}
                         onChange={on("openNoatime")} /> open_noatime
                </label>
                <label className="text-sm block">batch_files (1..1,000,000)
                  <input aria-label="batch_files" className={field} value={f.batchFiles}
                         onChange={on("batchFiles")} />
                </label>
                {batchFilesError && <p className="text-bad text-sm">{batchFilesError}</p>}
                <label className="text-sm block">bufsize (바이트, 4096..1,073,741,824)
                  <input aria-label="bufsize" className={field} value={f.bufsize}
                         onChange={on("bufsize")} />
                </label>
                {bufsizeError && <p className="text-bad text-sm">{bufsizeError}</p>}
                <label className="text-sm block">chmod (예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)
                  <input aria-label="chmod" className={field} value={f.chmod} onChange={on("chmod")} />
                </label>
                {chmodError && <p className="text-bad text-sm">{chmodError}</p>}
                <label className="text-sm block">chown (user:group)
                  <input aria-label="chown" className={field} value={f.chown} onChange={on("chown")} />
                </label>
                {chownError && <p className="text-bad text-sm">{chownError}</p>}
                {/* 함정 캡션(설계 §2.5): chown 명시 시 auto-chown 억제는
                    execution_manifests.py:75-77("chown" in spec.options) — 실패는
                    서버가 아니라 도구 실행 단계에서 나므로 여기서 미리 경고한다 */}
                <p className="text-muted text-xs">
                  chown 을 지정하면 자동 chown 이 꺼집니다. 비특권 사용자가 타인 소유를
                  지정하면 도구가 chown 권한이 없어 <strong>데이터는 복사되고 잡은 Failed 로
                  끝납니다</strong>.
                </p>
              </div>
            </details>
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
