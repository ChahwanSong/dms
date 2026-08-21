import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useSubmitRequest } from "./useJobs";
import type { SubmitBody } from "./useJobs";
import { useUserStorages } from "../storages/useUserStorages";
import { useMe } from "../auth/useAuth";
import { Card } from "../../components/ui/Card";
import { InfoCard } from "../../components/ui/InfoCard";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { Wizard } from "../../components/wizard/Wizard";
import type { WizardStep } from "../../components/wizard/Wizard";
import { ApiError } from "../../lib/api";
// field·StoragePicker 는 formFields.tsx 로 이사(슬라이스 31 T3) -- T4 위저드화 때
// 이 파일이 통째로 갈려도 SubmitScan·ScanPaths 가 흔들리지 않게 결합을 끊었다.
import { StoragePicker, field } from "./formFields";
// 옵션 미러(CHMOD_RE·CHOWN_RE·intFieldError, sync 숫자 범위·프리필 SYNC_INT_FIELDS)는
// optionRules.ts 로 이사(슬라이스 32 T8) -- BatchCreate 옵션 스텝과 공유한다
// (사본이면 미러가 발산한다).
import { CHMOD_RE, CHOWN_RE, SYNC_INT_FIELDS, intFieldError,
         syncIntFieldError } from "./optionRules";
// 정책 기본값 캡션(슬라이스 37: 배치 생성과 같은 표시 배선 — 백엔드 무변경).
import { usePolicies } from "../policies/usePolicies";
import type { Policy } from "../../lib/types";

// 슬라이스 37: 단일 작업(구 「작업 제출」) — 배치 작업과 같은 성격의 단일 항목
// 제출이다. 운영자는 scan 까지 세 연산 전부(서버 게이트 미러: scan 제출은 admin
// 전용 403), 비관리자는 기존대로 sync·rm.
type Operation = "sync" | "scan" | "rm";

const initial = {
  operation: "sync" as Operation,
  sourceStorage: "", sourcePath: "",
  destStorage: "", destPath: "",
  storage: "", target: "",
  delete: false, contents: false, direct: false,
  recursive: true, stat: false, lite: false, quiet: false,
  // scan 옵션(구 SubmitScan 미러 — dscan 1b93d54 실측): batch_files 0..10억
  // (0 = 배칭 끔), broken_limit 0..10,000. 프리필하지 않는다 — 도구 기본
  // (batch_files 100만/broken_limit 100)이 이미 원하는 값이라 생략 = 도구 기본.
  // sync 의 batchFiles 와 별도 상태인 이유: 같은 옵션명이지만 범위·프리필이 다르다.
  scanBatchFiles: "", brokenLimit: "", verbose: false,
  // 고급 sync 옵션 — 숫자도 문자열로 들고, 빈 문자열("")일 때만 "미입력"으로 생략한다.
  // truthy 검사 금지: "0"은 미입력이 아니라 범위 밖 클라이언트 검증 오류다.
  // batchFiles·bufsize 는 프리필(SYNC_INT_FIELDS.prefill — 「왜」는 그 주석):
  // 값이 실려 있으니 손대지 않으면 바디에 그대로 나간다. 지우면 옛 계약대로 생략.
  openNoatime: false,
  batchFiles: SYNC_INT_FIELDS.batch_files.prefill,
  bufsize: SYNC_INT_FIELDS.bufsize.prefill,
  chmod: "", chown: "",
  // "" = (정책 기본) = 바디에서 생략 — resolve_priority 가 정책 default_priority 로
  // 해석한다(BatchCreate 와 같은 계약, null≠0).
  priority: "",
  ownerUsername: "",
};

function checkedOptions(opts: Record<string, boolean>): Record<string, boolean> {
  return Object.fromEntries(Object.entries(opts).filter(([, v]) => v));
}

// 4스텝 위저드(슬라이스 31 T4): 연산 → 대상 → 옵션 → 확인·제출.
const STEPS: WizardStep[] = [
  { id: "operation", label: "연산" },
  { id: "target", label: "대상" },
  { id: "options", label: "옵션" },
  { id: "confirm", label: "확인·제출" },
];

export function SubmitJob() {
  const nav = useNavigate();
  const submit = useSubmitRequest();
  const storagesQ = useUserStorages();
  const me = useMe();
  // 폼 값은 위저드 밖 단일 useState -- 스텝을 오가도 값이 보존되고, 연산 전환 시
  // 필드 초기화 정책(전환해도 초기화하지 않음)도 현행 그대로다.
  const [f, setF] = useState(initial);
  const [step, setStep] = useState(0);

  const storages = storagesQ.data ?? [];
  const loadingStorages = storagesQ.isLoading;
  const isAdmin = me.data?.role === "admin";

  const recursiveMissing = f.operation === "rm" && !f.recursive;
  const statLiteConflict = f.operation === "rm" && f.stat && f.lite;
  // scan 국소 검증(BatchCreate 옵션 스텝 미러).
  const verboseQuietConflict = f.operation === "scan" && f.verbose && f.quiet;
  const scanBatchFilesError = f.operation === "scan"
    ? intFieldError("batch_files", f.scanBatchFiles, 0, 1_000_000_000) : null;
  const brokenLimitError = f.operation === "scan"
    ? intFieldError("broken_limit", f.brokenLimit, 0, 10_000) : null;
  // 고급 옵션은 sync 전용이라 rm 으로 바꾸면(전송도 안 되므로) 차단 사유에서 빠진다.
  const batchFilesError = f.operation === "sync"
    ? syncIntFieldError("batch_files", f.batchFiles) : null;
  const bufsizeError = f.operation === "sync"
    ? syncIntFieldError("bufsize", f.bufsize) : null;
  const chmodError = f.operation === "sync" && f.chmod.trim() !== "" && !CHMOD_RE.test(f.chmod.trim())
    ? "chmod 형식이 올바르지 않습니다 (예: D770,F660)" : null;
  const chownError = f.operation === "sync" && f.chown.trim() !== "" && !CHOWN_RE.test(f.chown.trim())
    ? "chown 형식이 올바르지 않습니다 (예: 10003:10000 또는 cocoa.song:mig)" : null;
  const advancedError = batchFilesError ?? bufsizeError ?? chmodError ?? chownError
    ?? scanBatchFilesError ?? brokenLimitError;
  const blocked = submit.isPending || recursiveMissing || statLiteConflict || storagesQ.isError
    || verboseQuietConflict || advancedError !== null;
  // 옵션 스텝 국소 검증: 오류를 그 스텝에서 보게 하고 "다음"을 잠근다.
  // blocked 와 별도인 이유: storagesQ.isError 등은 옵션 스텝 잘못이 아니라
  // 여기서 잠그면 사용자가 원인 없는 잠김을 본다 -- 최종 차단은 제출 버튼 몫.
  const optionsInvalid = recursiveMissing || statLiteConflict || verboseQuietConflict
    || advancedError !== null;

  // --- 정책 기본값 캡션(슬라이스 37: BatchCreate 미러 — 표시 배선만) ---
  const policiesQ = usePolicies();
  const fmtPolicy = (p: Policy | undefined) => p === undefined ? "미조회"
    : `최대 ${p.max_nodes}노드 · 노드당 ${p.procs_per_node}프로세스${
        p.enabled === 1 ? "" : " · 비활성(잡 배치 거부)"}`;
  const byTool = policiesQ.data === undefined
    ? undefined : new Map(policiesQ.data.map((p) => [p.tool, p]));
  const policyCaption = (() => {
    if (policiesQ.isLoading) return "정책 조회 중…";
    if (byTool === undefined) return "정책 미조회 — 정책 목록을 불러오지 못했습니다";
    if (f.operation === "sync") {
      const dsync = byTool.get("dsync"); const nsync = byTool.get("nsync");
      if (dsync === undefined && nsync === undefined)
        return "정책 미조회 — dsync/nsync 정책 행이 없습니다";
      return `정책 기본(dsync): ${fmtPolicy(dsync)} — 공존 노드가 없으면 `
        + `nsync 정책(${fmtPolicy(nsync)})이 적용됩니다`;
    }
    const p = byTool.get(f.operation);   // scan→"scan", rm→"rm"(TOOL_TO_POLICY 미러)
    return p === undefined ? `정책 미조회 — ${f.operation} 정책 행이 없습니다`
      : `정책 기본: ${fmtPolicy(p)}`;
  })();
  // 우선순위 "" = (정책 기본): resolve_priority 미러 — sync 는 dsync 정책 대표.
  const defaultPolicy = byTool?.get(f.operation === "sync" ? "dsync" : f.operation);
  const priorityDefaultLabel = defaultPolicy === undefined
    ? "(정책 기본)" : `(정책 기본: ${defaultPolicy.default_priority})`;

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

  // 제출 버튼은 위저드 프레임의 type="button" onClick 이라 form 이벤트가 없을 수
  // 있다 -- e 는 옵션으로 받고, form 경유(Enter 유출 등) 때만 기본 동작을 막는다.
  function scanOptions(): SubmitBody["options"] {
    const options: SubmitBody["options"] = checkedOptions({
      verbose: f.verbose, quiet: f.quiet });
    if (f.scanBatchFiles.trim() !== "") options.batch_files = Number(f.scanBatchFiles.trim());
    if (f.brokenLimit.trim() !== "") options.broken_limit = Number(f.brokenLimit.trim());
    return options;
  }

  function rmOptions(): SubmitBody["options"] {
    return checkedOptions({ recursive: f.recursive, stat: f.stat, lite: f.lite, quiet: f.quiet });
  }

  function handleSubmit(e?: React.SyntheticEvent) {
    e?.preventDefault();
    if (blocked) return;
    const body: SubmitBody = f.operation === "sync"
      ? {
          operation: "sync",
          source_storage: f.sourceStorage, source: f.sourcePath,
          destination_storage: f.destStorage, destination: f.destPath,
          options: syncOptions(),
        }
      : {
          operation: f.operation,
          storage: f.storage, target: f.target,
          options: f.operation === "scan" ? scanOptions() : rmOptions(),
        };
    // "" = (정책 기본) = 생략 — resolve_priority 가 정책값으로 해석(null≠0).
    if (f.priority !== "") body.priority = f.priority;
    if (isAdmin && f.ownerUsername.trim()) body.owner_username = f.ownerUsername.trim();
    submit.mutate(body, { onSuccess: (r) => nav(`/jobs/${r.request_id}`) });
  }

  // rm 경고는 연산 스텝(선택 직후 즉답)과 확인 스텝(제출 직전 재노출) 양쪽에 쓴다.
  // text-bad 유지: InfoCard(연파랑)로 옮겨도 "위험=빨강" 의미 체계는 색으로 남긴다.
  const rmWarning = (
    <InfoCard className="text-bad">
      삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.
    </InfoCard>
  );

  return (
    <Card className="max-w-xl">
      {/* 개명(사용자 결정 2026-08-18): 「작업 제출」→「단일 작업」 — 배치 작업과
          같은 성격의 단일 항목 제출임을 이름이 말한다. */}
      <h1 className="text-2xl font-bold mb-5">단일 작업</h1>
      {/* form 소유는 화면 쪽(위저드 프레임 계약): 프레임 버튼이 전부 type="button"
          이라 Enter 는 정상 동선에서 새지 않고, 새더라도(회귀) onSubmit 의
          blocked 가드가 이중 방어한다 */}
      <form onSubmit={handleSubmit}>
        <Wizard steps={STEPS} current={step} onNavigate={setStep}
                canNext={STEPS[step].id === "options" ? !optionsInvalid : true}
                onCancel={() => nav("/jobs")}
                submitLabel="제출" submitDisabled={blocked}
                onSubmit={handleSubmit}>
          {STEPS[step].id === "operation" && (
            <div className="space-y-3">
              <label className="text-sm block">연산
                <select aria-label="연산" className={field} value={f.operation}
                        onChange={(e) => setF({ ...f, operation: e.target.value as Operation })}>
                  <option value="sync">sync</option>
                  {/* 사용자 연산 allowlist(2026-08-20, 사용자 결정): 비운영자는
                      sync 만. scan·rm 은 admin 전용 -- 표시 게이트일 뿐 진짜 차단은
                      서버(routes_requests operation_admin_only 403). */}
                  {isAdmin && <option value="scan">scan</option>}
                  {isAdmin && <option value="rm">rm</option>}
                </select>
              </label>
              {f.operation === "rm" && rmWarning}
            </div>
          )}

          {STEPS[step].id === "target" && (
            <div className="space-y-3">
              {/* 목록 로드 실패 문구는 스토리지를 고르는 이 스텝에 노출 --
                  제출 차단은 기존 blocked 산식이 그대로 맡는다 */}
              {storagesQ.isError && (
                <p className="text-bad text-sm">{(storagesQ.error as ApiError).message}</p>
              )}
              {f.operation === "sync" ? (
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
              ) : (
                /* scan·rm 공용: 스토리지 하나 + 대상 경로(상대). */
                <div className="grid grid-cols-2 gap-3">
                  <StoragePicker label="스토리지" value={f.storage}
                    onChange={(v) => setF({ ...f, storage: v })} storages={storages} loading={loadingStorages} />
                  <label className="text-sm">대상 경로
                    <input aria-label="대상 경로" className={field} value={f.target} onChange={on("target")} />
                  </label>
                </div>
              )}
            </div>
          )}

          {STEPS[step].id === "options" && (
            <div className="space-y-3">
              {f.operation === "sync" ? (
                <>
                  {/* 옵션 설명(2026-08-20, 사용자 요청): dsync --delete / --contents.
                      ml-6 은 체크박스+간격 폭이라 설명이 라벨 글자 아래로 정렬된다. */}
                  <div>
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" aria-label="delete" checked={f.delete} onChange={on("delete")} /> delete
                    </label>
                    <p className="text-muted text-xs ml-6">원본에 없는 파일을 대상에서도 삭제해 완전히 동일하게 맞춥니다(미러 동기화).</p>
                  </div>
                  <div>
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" aria-label="contents" checked={f.contents} onChange={on("contents")} /> contents
                    </label>
                    <p className="text-muted text-xs ml-6">크기·수정시각 대신 파일 내용을 바이트 단위로 비교합니다(더 느리지만 정확).</p>
                  </div>
                  {/* direct·quiet·고급옵션·우선순위는 운영자 전용(2026-08-20, 사용자
                      결정): 사용자 sync 폼은 delete·contents 만 남긴다. 숨겨도 제출
                      payload 는 동일하다 -- checkedOptions 가 기본값(false)을 이미
                      생략하고, 고급 프리필(batch_files·bufsize)은 도구 기본값이라
                      운영자가 고급을 안 펼친 것과 결과가 같다. */}
                  {isAdmin && (
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" aria-label="direct" checked={f.direct} onChange={on("direct")} /> direct
                  </label>
                  )}
                  {isAdmin && (
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
                  </label>
                  )}
                  {/* 기본 접힘 — 기존 동선(단순 sync 제출)을 바꾸지 않기 위해 <details> 로 숨긴다 */}
                  {isAdmin && (
                  <details className="rounded-lg border border-line p-3">
                    <summary className="cursor-pointer text-sm font-medium">고급 옵션</summary>
                    <div className="mt-3 space-y-3">
                      <label className="flex items-center gap-2 text-sm">
                        <input type="checkbox" aria-label="open_noatime" checked={f.openNoatime}
                               onChange={on("openNoatime")} /> open_noatime
                      </label>
                      {/* 프리필 계약: 값이 미리 채워져 있고(placeholder 가 아니다)
                          비우면 키가 빠져 도구 기본으로 돌아간다 — placeholder 는
                          "비웠을 때 무슨 일이 나는가"를, 캡션은 "지금 채워진 값이
                          무엇인가"를 말한다(둘이 다른 정보다). */}
                      <label className="text-sm block">batch_files (선택 · 1..10,000,000)
                        <input aria-label="batch_files" className={field} value={f.batchFiles}
                               placeholder="비우면 배칭 안 함(도구 기본)"
                               onChange={on("batchFiles")} />
                      </label>
                      <p className="text-muted text-xs">
                        미리 채운 1,000,000 = 기본 배치 사이즈 100만. 비우면 배칭 안 함(도구 기본).
                      </p>
                      {batchFilesError && <p className="text-bad text-sm">{batchFilesError}</p>}
                      <label className="text-sm block">bufsize (선택 · 바이트, 4096..1,073,741,824)
                        <input aria-label="bufsize" className={field} value={f.bufsize}
                               placeholder="비우면 4 MiB(도구 기본)"
                               onChange={on("bufsize")} />
                      </label>
                      <p className="text-muted text-xs">
                        미리 채운 4194304 = 4 MiB. 비우면 4 MiB(도구 기본).
                      </p>
                      {bufsizeError && <p className="text-bad text-sm">{bufsizeError}</p>}
                      <label className="text-sm block">chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)
                        <input aria-label="chmod" className={field} value={f.chmod} onChange={on("chmod")} />
                      </label>
                      {chmodError && <p className="text-bad text-sm">{chmodError}</p>}
                      <label className="text-sm block">chown (선택 · user:group 또는 uid:gid)
                        <input aria-label="chown" className={field} value={f.chown}
                               placeholder="예: 10003:10000 또는 cocoa.song:mig"
                               onChange={on("chown")} />
                      </label>
                      {chownError && <p className="text-bad text-sm">{chownError}</p>}
                      {/* 함정 캡션(설계 §2.5): chown 명시 시 auto-chown 억제는
                          execution_manifests.py("chown" in spec.options) — 실패는
                          서버가 아니라 도구 실행 단계에서 나므로 여기서 미리 경고한다.
                          "비우면" 기본도 특권 여부로 갈린다(_auto_chown): 특권은 소스
                          소유권 보존, 비특권은 요청자 소유 자동 chown — 정직하게 병기 */}
                      <p className="text-muted text-xs">
                        비우면 원래 소유권 보존(특권 실행 기준 — 비특권 실행은 요청자 소유로
                        자동 chown). chown 을 지정하면 자동 chown 이 꺼집니다. 비특권 사용자가
                        타인 소유를 지정하면 도구가 chown 권한이 없어 <strong>데이터는 복사되고
                        잡은 Failed 로 끝납니다</strong>.
                      </p>
                    </div>
                  </details>
                  )}
                </>
              ) : f.operation === "scan" ? (
                /* scan 옵션(구 SubmitScan 미러): 생략 = 도구 기본. */
                <>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" aria-label="verbose" checked={f.verbose} onChange={on("verbose")} /> verbose
                  </label>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
                  </label>
                  {verboseQuietConflict && (
                    <p className="text-bad text-sm">verbose와 quiet는 함께 쓸 수 없습니다</p>
                  )}
                  <label className="text-sm block">batch_files (선택 · 0..1,000,000,000)
                    <input aria-label="batch_files" className={field} value={f.scanBatchFiles}
                           placeholder="비우면 1,000,000(도구 기본) · 0 = 배칭 안 함"
                           onChange={on("scanBatchFiles")} />
                  </label>
                  {scanBatchFilesError && <p className="text-bad text-sm">{scanBatchFilesError}</p>}
                  <label className="text-sm block">broken_limit (선택 · 0..10,000)
                    <input aria-label="broken_limit" className={field} value={f.brokenLimit}
                           placeholder="비우면 100(도구 기본) · 파손 경로 표본 보관 상한"
                           onChange={on("brokenLimit")} />
                  </label>
                  {brokenLimitError && <p className="text-bad text-sm">{brokenLimitError}</p>}
                </>
              ) : (
                <>
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

              {/* 정책 기본값 캡션·우선순위는 운영자 전용(2026-08-20, 사용자 결정):
                  사용자 폼에선 우선순위를 정책 기본에 맡긴다(생략 = resolve_priority
                  가 정책값으로 해석). 노드 수·프로세스 수도 정책이 정한다. */}
              {isAdmin && (
                <>
                  <p className="text-muted text-xs">{policyCaption}</p>
                  <label className="text-sm block">우선순위
                    <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
                      <option value="">{priorityDefaultLabel}</option>
                      <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
                    </select>
                  </label>
                </>
              )}

              {/* 라벨 정정(사용자 결정 2026-08-16): 이 값(owner_username)은 결과물의
                  소유자 기록이 아니라 **잡의 실행 신원**이다 — identity.py
                  resolve_job_identity 가 `owner = owner_username or requester_id`
                  로 잡의 신원을 정하고, 그 신원이 runAsUser·DMS_JR_USERNAME·
                  auto-chown 을 좌우한다. 캡션은 서버 게이트 두 개를 그대로 옮긴다:
                  ① 요청자 본인과 다른 신원은 특권 인가가 있어야 한다(routes_requests
                  403 privileged_not_authorized), ② 특권 경로는 LDAP 조회를 건너뛰고
                  uid 0(root)로 실행한다 — LDAP 에 없는 계정을 신원으로 쓸 수 있는
                  유일한 경로다. */}
              {isAdmin && (
                <label className="text-sm block">실행 신원(선택)
                  <input aria-label="실행 신원(선택)" className={field}
                         placeholder="예: cocoa.song"
                         value={f.ownerUsername} onChange={on("ownerUsername")} />
                  <p className="text-muted text-xs mt-1">
                    비우면 요청자 본인으로 실행됩니다. 다른 사용자를 지정하려면 특권
                    요청자여야 하며, 그때 잡은 root 로 실행되고 지정한 사용자 신원으로
                    파일을 다룹니다(LDAP 에 없는 계정도 지정할 수 있습니다).
                  </p>
                  {f.operation === "rm" && (
                    <p className="text-bad text-xs">삭제가 root 권한으로 수행됩니다</p>
                  )}
                </label>
              )}
            </div>
          )}

          {STEPS[step].id === "confirm" && (
            <div className="space-y-3">
              {/* 요약은 제출 바디와 같은 함수(syncOptions·checkedOptions)에서 파생 --
                  화면 따로 바디 따로면 "요약과 다른 것이 제출되는" 화면 거짓말이 생긴다 */}
              <InfoPanel>
                <dl className="space-y-1">
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-muted">연산</dt>
                    <dd>{f.operation}</dd>
                  </div>
                  {f.operation === "sync" ? (
                    <>
                      <div className="flex gap-2">
                        <dt className="w-24 shrink-0 text-muted">소스</dt>
                        <dd>{f.sourceStorage}:{f.sourcePath}</dd>
                      </div>
                      <div className="flex gap-2">
                        <dt className="w-24 shrink-0 text-muted">목적지</dt>
                        <dd>{f.destStorage}:{f.destPath}</dd>
                      </div>
                    </>
                  ) : (
                    <div className="flex gap-2">
                      <dt className="w-24 shrink-0 text-muted">대상</dt>
                      <dd>{f.storage}:{f.target}</dd>
                    </div>
                  )}
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-muted">옵션</dt>
                    <dd>{JSON.stringify(f.operation === "sync" ? syncOptions()
                      : f.operation === "scan" ? scanOptions() : rmOptions())}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-24 shrink-0 text-muted">우선순위</dt>
                    <dd>{f.priority === "" ? priorityDefaultLabel : f.priority}</dd>
                  </div>
                  {isAdmin && f.ownerUsername.trim() !== "" && (
                    <div className="flex gap-2">
                      <dt className="w-24 shrink-0 text-muted">실행 신원</dt>
                      <dd>{f.ownerUsername.trim()}</dd>
                    </div>
                  )}
                </dl>
              </InfoPanel>
              {f.operation === "rm" && rmWarning}
              {submit.isError && <p className="text-bad text-sm">{(submit.error as ApiError).message}</p>}
            </div>
          )}
        </Wizard>
      </form>
    </Card>
  );
}
