import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight } from "lucide-react";
import { useSubmitRequest } from "./useJobs";
import type { SubmitBody } from "./useJobs";
import { useUserStorages } from "../storages/useUserStorages";
import { useUserSyncPairs } from "../policies/useSyncPairs";
import { useMe } from "../auth/useAuth";
import { Button } from "../../components/ui/Button";
import { InfoCard } from "../../components/ui/InfoCard";
import {
  FieldRow, FormSection, OptionTokens, SubmitHints, SubmitLayout, SummaryList, SummaryPanel, SummaryRow,
} from "../../components/form/SubmitLayout";
import { ApiError } from "../../lib/api";
// field·StoragePicker 는 formFields.tsx 에 산다(슬라이스 31 T3) -- 제출 화면이 통째로 갈려도
// 다른 임포터가 흔들리지 않게 결합을 끊어 둔 것.
import { StoragePicker, field } from "./formFields";
import { absolutePath, destinationParent, relativePathProblem } from "../../lib/storagePaths";
import { pairAllowed, syncChoices } from "../../lib/syncPairs";
import { syncOwnership } from "../../lib/syncOwnership";
// 옵션 미러(CHMOD_RE·chownFieldError·intFieldError, sync 숫자 범위·프리필 SYNC_INT_FIELDS)는
// optionRules.ts 로 이사(슬라이스 32 T8) -- BatchCreate 옵션과 공유한다
// (사본이면 미러가 발산한다).
import { CHMOD_RE, chownFieldError, SCAN_INT_FIELDS, SYNC_INT_FIELDS,
         scanIntFieldError, syncIntFieldError } from "./optionRules";
// 정책 기본값 캡션(슬라이스 37: 배치 생성과 같은 표시 배선 — 백엔드 무변경).
import { usePolicies } from "../policies/usePolicies";
import type { Policy } from "../../lib/types";

// 슬라이스 37: 단일 작업(구 「작업 제출」) — 배치 작업과 같은 성격의 단일 항목
// 제출이다. 운영자는 scan 까지 세 연산 전부(서버 게이트 미러: scan 제출은 admin
// 전용 403), 비관리자는 sync 만.
type Operation = "sync" | "scan" | "rm";

const initial = {
  operation: "sync" as Operation,
  sourceStorage: "", sourcePath: "",
  destStorage: "", destPath: "",
  storage: "", target: "",
  delete: false, contents: false, direct: false,
  recursive: true, stat: false, lite: false, quiet: false,
  // scan 옵션(구 SubmitScan 미러 — dscan 1b93d54 실측): batch_files 0..10억
  // (0 = 배칭 끔), broken_limit 0..10,000. 2026-09-17 부터 프리필(SCAN_INT_FIELDS
  // .prefill) = 서버 기본(domain._OPTION_DEFAULTS) — 값이 dscan 기본과 같아 동작은
  // 종전과 같고, 요청 상세에 어떤 값으로 돌았는지 명시적으로 남는다.
  // sync 의 batchFiles 와 별도 상태인 이유: 같은 옵션명이지만 범위가 다르다.
  scanBatchFiles: SCAN_INT_FIELDS.batch_files.prefill,
  brokenLimit: SCAN_INT_FIELDS.broken_limit.prefill, verbose: false,
  // 고급 sync 옵션 — 숫자도 문자열로 들고, 빈 문자열("")일 때만 "미입력"으로 생략한다.
  // truthy 검사 금지: "0"은 미입력이 아니라 정상 입력(배칭 끔, 2026-09-17 하한 0)이다.
  // batchFiles·bufsize 는 프리필(SYNC_INT_FIELDS.prefill — 「왜」는 그 주석):
  // 값이 실려 있으니 손대지 않으면 바디에 그대로 나간다. 지우면 키가 빠지고 서버가
  // 같은 기본값을 박는다(domain._OPTION_DEFAULTS).
  // 초기값 ON = **root 실행의 기본**(사용자 결정 2026-08-22 → 2026-09-30 재조정):
  // O_NOATIME 은 root(또는 파일 소유자)만 쓸 수 있어 비 root 실행에선 타인 소유
  // 파일 open 이 EPERM 이다(mpifileutils 는 폴백 없이 실패). 2026-09-30 부터 운영자
  // 단건도 root 가 아닐 수 있어(실행 신원 지정·체크 해제), 이 값은 root 실행일 때만
  // 실린다(syncOptions). 사용자 폼은 옵션이 숨겨져 어차피 못 바꾼다. 배치는
  // (자격 있으면) root 라 BatchCreate 가 별도로 기본 ON 을 유지한다.
  openNoatime: true,
  batchFiles: SYNC_INT_FIELDS.batch_files.prefill,
  bufsize: SYNC_INT_FIELDS.bufsize.prefill,
  chmod: "", chown: "",
  // "" = (정책 기본) = 바디에서 생략 — resolve_priority 가 정책 default_priority 로
  // 해석한다(BatchCreate 와 같은 계약, null≠0).
  priority: "",
  ownerUsername: "",
  // root 실행 체크박스(2026-09-30). null = 손대지 않음 → 기본값을 따른다(rootEffective):
  // 자격 있는 관리자는 기본 root(사용자 결정), 단 실행 신원에 **다른 사용자**를 적으면 기본이
  // 그 사용자 권한이다 -- 관리자가 실행 신원=일반 사용자로 낸 sync 가 root 로 돌아 남의 700
  // 목적지 소유를 덮어쓴 사고 경로를 기본값으로 되살리지 않는다. 한 번 누르면 명시값(boolean).
  runAsRoot: null as boolean | null,
};

function checkedOptions(opts: Record<string, boolean>): Record<string, boolean> {
  return Object.fromEntries(Object.entries(opts).filter(([, v]) => v));
}

// 연산 고르기 옆의 한 줄 설명 -- 드롭다운 하나만으로는 무엇이 일어나는지 화면이 말하지 않았다.
const OPERATION_HELP: Record<Operation, string> = {
  sync: "소스 디렉토리의 내용을 목적지로 복사·동기화합니다.",
  scan: "대상 경로의 파일 수·용량·데이터 온도 통계를 수집합니다.",
  rm: "대상 경로와 그 아래를 모두 삭제합니다.",
};

// 경로 입력 바로 아래의 즉답: 서버가 거부할 경로면 그 이유(relativePathProblem = validate_relative_path
// 미러), 아니면 입력한 상대경로가 실제로 가리키는 절대경로(관리 디렉토리를 알 때만 -- 모르면 지어내지
// 않는다). 절대경로는 **보내는 값 그대로**로 짓는다 -- 다듬은 값으로 지으면 "/a" 나 "x " 같은 입력에
// 서버가 실제로 쓰지 않을(또는 거부할) 경로를 그럴듯하게 보여 준다(리뷰 2026-10-06).
function PathFeedback({ root, rel }: { root: string | null | undefined; rel: string }) {
  if (rel.trim() === "") return null;
  const problem = relativePathProblem(rel);
  if (problem !== null) return <p className="mt-1 text-bad text-sm">{problem}</p>;
  const abs = absolutePath(root, rel);
  return (
    <>
      {abs !== null && (
        <p className="mt-1 text-xs text-muted [overflow-wrap:anywhere]">{`실제 경로: ${abs}`}</p>
      )}
      {rel !== rel.trim() && (
        <p className="mt-1 text-xs text-muted">앞뒤 공백도 경로 이름에 그대로 들어갑니다.</p>
      )}
    </>
  );
}

// 요약의 storage:path. 둘 다 비면 ":" 대신 "—" -- 값이 차면 정확히 "cephfs:a/b"(한 텍스트 노드).
const loc = (storage: string, path: string) =>
  storage === "" && path.trim() === "" ? "—" : `${storage}:${path}`;

export function SubmitJob() {
  const nav = useNavigate();
  const submit = useSubmitRequest();
  const storagesQ = useUserStorages();
  const me = useMe();
  // 폼 값은 단일 useState -- 연산을 바꿔도 필드를 초기화하지 않는다(현행 정책). 숨은 연산의 값은
  // 남지만 아래 검증·바디가 f.operation 으로 걸러 싣지 않는다.
  const [f, setF] = useState(initial);
  // 고급 옵션 펼침 상태. 오류가 있으면 항상 펼친다(아래 details open/onToggle).
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const storages = storagesQ.data ?? [];
  const loadingStorages = storagesQ.isLoading;
  const isAdmin = me.data?.role === "admin";
  // root 자격은 서버가 알려 준다(me.can_run_as_root = 제출 게이트와 같은 판정) -- 특권 목록 밖
  // 관리자에게 기본 root 를 켜 두면 제출이 403 이 된다. 표시일 뿐 서버가 다시 본다.
  const canRoot = isAdmin && me.data?.can_run_as_root === true;
  const ownerTrim = f.ownerUsername.trim();
  const otherOwner = ownerTrim !== "" && ownerTrim !== me.data?.actor;
  // "관리자는 기본 root"(사용자 결정 2026-09-30)의 **유일한 구현 지점**: 서버는 명시 true 만
  // root 로 받고 생략은 비 root 라(routes_requests.submit), 기본값 규칙(다른 실행 신원이 없으면
  // root)은 여기서 정해 확정값을 바디에 명시로 싣는다 -- 화면의 "실행 권한" 과 서버가 어긋날 수 없다.
  const rootEffective = canRoot && (f.runAsRoot ?? !otherOwner);
  const rootOf = (name: string) => storages.find((s) => s.storage_name === name)?.managed_root;
  // sync 목적지의 상위 디렉토리(쓰기 권한이 필요한 곳) -- 관리 디렉토리를 알면 절대경로로.
  const destRoot = rootOf(f.destStorage);
  const destParentAbs = f.destPath.trim() === "" ? null : destinationParent(destRoot, f.destPath.trim());
  // 결과(목적지) 소유권 안내 -- 바디와 같은 값(chown 옵션·rootEffective·실행 신원)에서 파생한다
  // (lib/syncOwnership = 서버 _auto_chown 미러). 사용자는 언제나 "요청자 본인 uid:gid".
  const ownership = syncOwnership({
    chown: f.chown, chmod: f.chmod, root: rootEffective,
    runAs: otherOwner ? ownerTrim : (me.data?.actor ?? null), self: !otherOwner });
  // 사용자 sync 허용 쌍(2026-09-30 사용자 결정: 기본 전부 불가 + 관리자가 허용한 소스 → 목적지 쌍).
  // 관리자는 제한이 없어 조회하지 않는다. 선택지 필터는 표시일 뿐 -- 제출·계획·컨펌이 서버에서 다시
  // 본다(sync_pair_not_allowed). 신원·허용 목록을 알기 전에는 sync 선택지를 비운다: 거르기 전 목록이
  // 잠깐 보였다 사라지면 그 사이 고른 값이 허용 밖일 수 있다.
  const needPairs = me.data !== undefined && !isAdmin;
  const pairsQ = useUserSyncPairs(needPairs);
  const pairsError = needPairs && pairsQ.data === undefined && pairsQ.isError;
  const pairsPending = me.data === undefined || (needPairs && pairsQ.data === undefined && !pairsQ.isError);
  // null = 제한 없음(관리자, 또는 서버가 restricted=false 라고 한 경우 -- 서버 판정이 진실).
  const allowedPairs = needPairs && pairsQ.data?.restricted ? pairsQ.data.pairs : null;
  const { sources: sourceChoices, destinations: destChoices } = pairsPending || pairsError
    ? { sources: [], destinations: [] }
    : allowedPairs === null ? { sources: storages, destinations: storages }
    : syncChoices(storages, allowedPairs, f.sourceStorage, f.destStorage);
  const pairBlocked = allowedPairs !== null && f.sourceStorage !== "" && f.destStorage !== ""
    && !pairAllowed(allowedPairs, f.sourceStorage, f.destStorage);

  const recursiveMissing = f.operation === "rm" && !f.recursive;
  const statLiteConflict = f.operation === "rm" && f.stat && f.lite;
  // scan 국소 검증(BatchCreate 옵션 미러).
  const verboseQuietConflict = f.operation === "scan" && f.verbose && f.quiet;
  const scanBatchFilesError = f.operation === "scan"
    ? scanIntFieldError("batch_files", f.scanBatchFiles) : null;
  const brokenLimitError = f.operation === "scan"
    ? scanIntFieldError("broken_limit", f.brokenLimit) : null;
  // 고급 옵션은 sync 전용이라 rm 으로 바꾸면(전송도 안 되므로) 차단 사유에서 빠진다.
  const batchFilesError = f.operation === "sync"
    ? syncIntFieldError("batch_files", f.batchFiles) : null;
  const bufsizeError = f.operation === "sync"
    ? syncIntFieldError("bufsize", f.bufsize) : null;
  const chmodError = f.operation === "sync" && f.chmod.trim() !== "" && !CHMOD_RE.test(f.chmod.trim())
    ? "chmod 형식이 올바르지 않습니다 (예: D770,F660)" : null;
  const chownError = f.operation === "sync" ? chownFieldError(f.chown) : null;
  // sync 고급 옵션(접힘 패널) 안의 오류 -- 있으면 패널을 강제로 펼친다.
  const advancedError = batchFilesError ?? bufsizeError ?? chmodError ?? chownError;
  // 대상 sanity(슬라이스 39, 사용자 결정): 스토리지 미선택·경로 공백이면 제출할 수 없다.
  // sync 는 소스·목적지 4필드, scan/rm 은 스토리지+대상. trim() 로 공백만 있는 입력도 미입력으로 본다.
  const targetMissing = f.operation === "sync"
    ? (f.sourceStorage === "" || f.sourcePath.trim() === ""
       || f.destStorage === "" || f.destPath.trim() === "")
    : (f.storage === "" || f.target.trim() === "");
  // 서버가 422 unsafe_path 로 거부할 경로(relativePathProblem 미러) -- 보내기 전에 잠근다.
  const pathInvalid = f.operation === "sync"
    ? relativePathProblem(f.sourcePath) !== null || relativePathProblem(f.destPath) !== null
    : relativePathProblem(f.target) !== null;
  const targetInvalid = targetMissing || pathInvalid || (f.operation === "sync" && pairBlocked);
  const optionsInvalid = recursiveMissing || statLiteConflict || verboseQuietConflict
    || advancedError !== null || scanBatchFilesError !== null || brokenLimitError !== null;
  // 제출 게이트는 이 하나다(위저드 시절 스텝별 '다음' 게이트의 합집합 + 조회 실패·진행 중).
  const blocked = submit.isPending || storagesQ.isError
    || (f.operation === "sync" && pairsError)
    || optionsInvalid || targetInvalid;

  // --- 정책 기본값 캡션(슬라이스 37: BatchCreate 미러 — 표시 배선만) ---
  // 정책은 관리자 전용 API -- 사용자에겐 캡션·우선순위가 없으니 조회하지 않는다(요약은 "(정책 기본)").
  const policiesQ = usePolicies(isAdmin);
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
      // open_noatime 은 **root 실행에만** 실린다(2026-09-30): 비 root 실행에서
      // 타인 소유 파일 O_NOATIME 은 EPERM 이라 부분 복사 뒤 Failed 가 된다. 예전엔
      // 운영자 단건 = 항상 root 라 isAdmin 만으로 충분했지만, 이제 root 는
      // 체크박스(rootEffective)가 정한다. 체크박스 값은 root 를 다시 켤 때를 위해 보존한다.
      open_noatime: isAdmin && rootEffective && f.openNoatime,
    });
    // 빈 문자열일 때만 생략 — 빈 chmod/chown 을 그대로 실으면 서버 fullmatch 가
    // 422 invalid_option 으로 거부한다(빈 값은 "옵션 없음"이지 "빈 값 지정"이 아니다).
    if (f.batchFiles.trim() !== "") options.batch_files = Number(f.batchFiles.trim());
    if (f.bufsize.trim() !== "") options.bufsize = Number(f.bufsize.trim());
    if (f.chmod.trim() !== "") options.chmod = f.chmod.trim();
    if (f.chown.trim() !== "") options.chown = f.chown.trim();
    return options;
  }

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

  // 요약과 제출 바디가 **같은 함수**에서 나온다 -- 화면 따로 바디 따로면 "요약과 다른 것이
  // 제출되는" 화면 거짓말이 생긴다.
  const currentOptions = () =>
    f.operation === "sync" ? syncOptions() : f.operation === "scan" ? scanOptions() : rmOptions();

  // 제출 버튼은 type="button" + onClick 이다(아래 form 주석) -- e 는 옵션으로 받고, form 경유
  // (강제 submit 등) 때만 기본 동작을 막는다.
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
    // 관리자는 확정값을 **항상 명시**로 싣는다(true/false) -- 화면의 "실행 권한" 과 서버 결정이
    // 같다. 자격이 없으면 rootEffective 는 false 이고 서버는 명시 false 를 403 없이 받는다(me
    // 판정이 제출 시점과 어긋나도 화면에 없던 root 가 되지 않는다).
    if (isAdmin) body.run_as_root = rootEffective;
    submit.mutate(body, { onSuccess: (r) => nav(`/jobs/${r.request_id}`) });
  }

  // 제출이 잠긴 이유(요약 패널, 버튼 위). 필드 옆 빨간 문구와 **다른 문장**으로 짧게 -- 같은 문장을
  // 두 번 그리지 않는다(SubmitHints 주석). 대상 미입력 안내만은 원문이 여기 한 곳에 산다.
  const hints: string[] = [];
  if (submit.isPending) hints.push("제출 중…");
  if (storagesQ.isError) hints.push("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  if (f.operation === "sync" && pairsError) hints.push("허용 조합 조회 실패 — 새로고침 후 다시 시도하세요");
  if (f.operation === "sync" && allowedPairs !== null && allowedPairs.length === 0)
    hints.push("허용된 sync 조합이 없어 지금은 제출할 수 없습니다");
  else if (f.operation === "sync" && pairBlocked) hints.push("허용된 조합으로 소스·목적지를 다시 고르세요");
  else if (targetMissing)
    hints.push(f.operation === "sync" ? "소스·목적지 스토리지와 경로를 모두 입력하세요"
      : "스토리지와 대상 경로를 입력하세요");
  if (pathInvalid) hints.push("경로를 관리 디렉토리 아래 상대경로로 고치세요");
  if (optionsInvalid) hints.push("빨간 안내가 붙은 옵션 값을 고치세요");

  const targetTitle = f.operation === "sync" ? "소스와 목적지"
    : f.operation === "scan" ? "스캔 대상" : "삭제 대상";

  const sheet = (
    <>
      <FormSection step={1} title="작업 종류">
        <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
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
          <p className="text-sm text-muted sm:pt-8">{OPERATION_HELP[f.operation]}</p>
        </div>
      </FormSection>

      {/* "경로는 관리 디렉토리 아래 상대경로" 는 스토리지 선택 아래 캡션(StoragePicker)이 이미 말하고,
          입력 아래 "실제 경로" 가 그 결과를 보여 준다 -- 구획 캡션으로 세 번째 반복하지 않는다. */}
      <FormSection step={2} title={targetTitle}>
        {/* 목록 로드 실패 문구는 스토리지를 고르는 이 구획에 -- 제출 차단은 blocked 가 맡는다 */}
        {storagesQ.isError && (
          <p className="text-bad text-sm">{(storagesQ.error as ApiError).message}</p>
        )}
        {f.operation === "sync" && pairsError && (
          <p className="text-bad text-sm">
            {`허용된 스토리지 조합을 불러오지 못했습니다: ${(pairsQ.error as ApiError).message}`}
          </p>
        )}
        {f.operation === "sync" ? (
          <>
            {/* 경로 한 줄: 소스 → 목적지. 넓은 화면에선 좌우로 놓아 복사 방향이 그대로 읽힌다. */}
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
              <div className="flex min-w-0 flex-col gap-3">
                <StoragePicker label="소스 스토리지" value={f.sourceStorage}
                  onChange={(v) => setF({ ...f, sourceStorage: v })} storages={sourceChoices}
                  loading={loadingStorages || pairsPending} />
                <div>
                  <label className="text-sm block">소스 경로
                    <input aria-label="소스 경로" className={field} value={f.sourcePath}
                           placeholder="예: team/dataset" onChange={on("sourcePath")} />
                  </label>
                  <PathFeedback root={rootOf(f.sourceStorage)} rel={f.sourcePath} />
                </div>
              </div>
              <ArrowRight aria-hidden
                          className="h-5 w-5 rotate-90 justify-self-center text-muted md:mt-8 md:rotate-0" />
              <div className="flex min-w-0 flex-col gap-3">
                <StoragePicker label="목적지 스토리지" value={f.destStorage}
                  onChange={(v) => setF({ ...f, destStorage: v })} storages={destChoices}
                  loading={loadingStorages || pairsPending} />
                <div>
                  <label className="text-sm block">목적지 경로
                    <input aria-label="목적지 경로" className={field} value={f.destPath}
                           placeholder="예: backup/dataset" onChange={on("destPath")} />
                  </label>
                  <PathFeedback root={destRoot} rel={f.destPath} />
                </div>
              </div>
            </div>
            {/* 허용 조합 안내(사용자에게만): 선택지가 왜 줄었는지와 다른 조합을 보는 법. */}
            {allowedPairs !== null && (allowedPairs.length === 0 ? (
              <p className="text-sm text-bad" role="note" aria-label="허용된 스토리지 조합">
                관리자가 허용한 sync 스토리지 조합이 없어 지금은 sync 를 요청할 수 없습니다 — 관리자에게
                필요한 소스 → 목적지 조합의 허용을 요청하세요.
              </p>
            ) : (
              <p className="text-xs text-muted" role="note" aria-label="허용된 스토리지 조합">
                관리자가 허용한 소스 → 목적지 조합({allowedPairs.length}개)만 선택지에 보입니다 — 한쪽을
                고르면 다른 쪽은 그와 짝이 되는 스토리지만 남습니다. 다른 조합을 보려면 한쪽을
                「선택하세요」로 되돌리세요.
              </p>
            ))}
            {/* 허용 목록은 화면에 있는 동안에도 다시 읽힌다(포커스·staleTime) -- 관리자가 쌍을 해제하면
                제출이 잠기고, 그 이유를 고르는 자리에서 경보로 말한다(페이지의 유일한 role=alert). */}
            {pairBlocked && (
              <p className="text-bad text-sm" role="alert">
                허용되지 않은 소스 → 목적지 조합입니다 — 관리자가 허용한 조합만 sync 할 수 있습니다(허용이
                해제됐을 수 있습니다).
              </p>
            )}
            {/* 목적지 조건과 소유권(2026-09-30 "상위 디렉토리 쓰기 권한 조건을 분명히", 2026-10-01
                "목적지가 없는 경우" + "기본적으로 목적지는 요청자 본인 uid:gid" 추가). 근거:
                preflight _DEST_TYPE_CHECK·_DEST_CHECK -- 목적지가 없으면 상위 디렉토리 쓰기만 보고
                sync 가 목적지를 한 단계 만든다(상위가 없으면 test -w 가 실패 -- root 도 마찬가지),
                목적지가 있으면 디렉토리여야 하고 쓰기·진입(비 root 는 소유) + 상위 쓰기. 소유권은
                lib/syncOwnership(_auto_chown 미러). 권한이 필요한 상위 디렉토리는 실제 절대경로로.
                사용자가 "분명히" 보이게 해 달라고 한 안내라 접지 않는다. */}
            <InfoCard role="note" aria-label="목적지 조건과 소유권">
              <p className="font-medium">
                {`목적지 조건과 소유권 — ${isAdmin ? "실행 신원" : "요청자 본인 계정"}(uid/gid) 기준`}
              </p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
                <li>
                  <strong>목적지가 없는 경우</strong>: sync 가 목적지 디렉토리를 새로 만듭니다. 목적지의{" "}
                  {rootEffective
                    ? <><strong>상위 디렉토리는 이미 있어야</strong> 합니다(root 실행 — 쓰기 권한 검사 우회)</>
                    : <><strong>상위 디렉토리는 이미 있고 그 디렉토리에 쓰기 권한</strong>이 있어야 합니다</>}
                  {" "}— 중간 디렉토리는 만들지 않습니다.
                  {destParentAbs !== null && (rootEffective ? (
                    <> 이 요청에서는 <code className="rounded bg-surface px-1 break-all">{destParentAbs}</code> 가
                      이미 있어야 합니다.</>
                  ) : (
                    <> 이 요청에서는 <code className="rounded bg-surface px-1 break-all">{destParentAbs}</code> 에
                      쓰기 권한이 필요합니다.</>
                  ))}
                </li>
                <li>
                  <strong>목적지가 이미 있는 경우</strong>: 디렉토리여야 합니다(파일이면 거부).{" "}
                  {rootEffective
                    ? "root 실행이라 쓰기·소유 검사는 우회되고, 그 안의 같은 경로 항목은 소스의 소유·권한·시각으로 다시 맞춰집니다(chown·chmod 를 지정하면 그 값)."
                    : <>쓰기·진입할 수 있어야 하며,{" "}
                        {canRoot ? "root 실행이 아니면 실행 신원 소유여야" : isAdmin ? "실행 신원 소유여야" : "요청자 본인 소유여야"}{" "}
                        합니다(sync 가 최상위의 권한·시각을 소스에 맞추기 때문). 상위 디렉토리 쓰기 권한도 마찬가지로
                        필요합니다.</>}
                </li>
                <li><strong>소유권</strong>: {ownership.long}</li>
                {/* 보조 그룹 미적용(검증 워크플로 2026-10-01): preflight·도구는 실행 uid + LDAP 주 gid
                    만으로 돈다(supplementalGroups 없음, 잡 컨테이너에 LDAP NSS 없음). */}
                <li>
                  {canRoot ? "root 가 아닌 실행에서 권한은" : "권한은"} {isAdmin ? "실행 신원" : "본인 계정"}의 uid·LDAP 주 그룹(그리고 기타
                  사용자 권한) 기준으로만 판정됩니다 — <strong>보조 그룹으로 받은 권한은 인정되지 않습니다</strong>(소스
                  읽기도 마찬가지).
                </li>
                <li>조건이 맞지 않으면 미리보기 전에 거부되고 사유가 표시됩니다 — 아무것도 복사되지 않습니다.</li>
                {canRoot && (
                  <li>root 권한으로 실행하면 권한·소유 검사는 우회됩니다(아래 실행 설정에서 선택) — 상위 디렉토리는
                    그래도 이미 있어야 합니다.</li>
                )}
              </ul>
            </InfoCard>
          </>
        ) : (
          /* scan·rm 공용: 스토리지 하나 + 대상 경로(상대). */
          <div className="grid gap-3 sm:grid-cols-2">
            <StoragePicker label="스토리지" value={f.storage}
              onChange={(v) => setF({ ...f, storage: v })} storages={storages} loading={loadingStorages} />
            <div>
              <label className="text-sm block">대상 경로
                <input aria-label="대상 경로" className={field} value={f.target}
                       placeholder="예: team/projects" onChange={on("target")} />
              </label>
              <PathFeedback root={rootOf(f.storage)} rel={f.target} />
            </div>
          </div>
        )}
      </FormSection>

      <FormSection step={3} title="실행 옵션">
        {f.operation === "sync" ? (
          <>
            {/* 옵션 설명(2026-08-20, 사용자 요청): dsync --delete / --contents. */}
            <FieldRow align="check"
              control={<label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="delete" checked={f.delete} onChange={on("delete")} /> delete
              </label>}
              help="원본에 없는 파일을 대상에서도 삭제해 완전히 동일하게 맞춥니다(미러 동기화)." />
            <FieldRow align="check"
              control={<label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="contents" checked={f.contents} onChange={on("contents")} /> contents
              </label>}
              help="크기·수정시각 대신 파일 내용을 바이트 단위로 비교합니다(더 느리지만 정확)." />
            {/* direct·quiet·고급옵션·우선순위는 운영자 전용(2026-08-20, 사용자
                결정): 사용자 sync 폼은 delete·contents 만 남긴다. 숨겨도 제출
                payload 는 동일하다 -- checkedOptions 가 기본값(false)을 이미
                생략하고, 고급 프리필(batch_files·bufsize)은 도구 기본값이라
                운영자가 고급을 안 펼친 것과 결과가 같다. */}
            {isAdmin && (
              <div className="flex flex-wrap gap-x-6 gap-y-2">
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="direct" checked={f.direct} onChange={on("direct")} /> direct
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
                </label>
              </div>
            )}
            {/* 기본 접힘 — 단순 sync 제출 동선을 바꾸지 않기 위해 <details> 로 숨긴다 */}
            {isAdmin && (
              <details className="rounded-lg border border-line"
                       open={advancedOpen || advancedError !== null}
                       onToggle={(e) => {
                         const el = e.currentTarget;
                         // 오류가 있는 동안은 접히지 않는다: 접으면 제출이 잠긴 이유(빨간 안내)가 숨는다.
                         // open prop 은 true→true 라 React 가 DOM 을 다시 열어 주지 않는다 -- 직접 되돌린다
                         // (위저드 시절엔 스텝 재마운트가 이 함정을 가렸다).
                         if (!el.open && advancedError !== null) { el.open = true; return; }
                         setAdvancedOpen(el.open);
                       }}>
                <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium">고급 옵션</summary>
                <div className="space-y-4 border-t border-line px-4 py-4">
                  <FieldRow align="check"
                    control={<label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" aria-label="open_noatime"
                             checked={rootEffective && f.openNoatime} disabled={!rootEffective}
                             onChange={on("openNoatime")} /> open_noatime
                    </label>}
                    help={!rootEffective && <>
                      open_noatime 은 root 실행에서만 적용됩니다 — 일반 실행 신원은
                      남의 파일을 O_NOATIME 으로 열 수 없어(EPERM) 복사가 실패합니다.
                    </>} />
                  {/* 프리필 계약(2026-09-17): 값이 미리 채워져 있고(placeholder 가
                      아니다) 그 값이 곧 서버 기본이라 비워도 같은 값이 적용된다 —
                      배칭을 끄는 유일한 표현은 0 명시. placeholder 는 "비웠을 때
                      무슨 일이 나는가"를, 캡션은 "지금 채워진 값"을 말한다. */}
                  <FieldRow
                    control={<>
                      <label className="text-sm block">batch_files (선택 · 0..10,000,000)
                        <input aria-label="batch_files" className={field} value={f.batchFiles}
                               placeholder="비우면 기본 1,000,000 적용 · 0 = 배칭 끔"
                               onChange={on("batchFiles")} />
                      </label>
                      {batchFilesError && <p className="mt-1 text-bad text-sm">{batchFilesError}</p>}
                    </>}
                    help="미리 채운 1,000,000 = 서버 기본 배치 사이즈. 비워도 같은 값이 적용되며, 배칭을 끄려면 0 을 입력하세요." />
                  <FieldRow
                    control={<>
                      <label className="text-sm block">bufsize (선택 · 바이트, 4096..1,073,741,824)
                        <input aria-label="bufsize" className={field} value={f.bufsize}
                               placeholder="비우면 기본 4 MiB 적용"
                               onChange={on("bufsize")} />
                      </label>
                      {bufsizeError && <p className="mt-1 text-bad text-sm">{bufsizeError}</p>}
                    </>}
                    help="미리 채운 4194304 = 4 MiB(서버 기본). 비워도 같은 값이 적용됩니다." />
                  <FieldRow
                    control={<>
                      <label className="text-sm block">chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)
                        <input aria-label="chmod" className={field} value={f.chmod} onChange={on("chmod")} />
                      </label>
                      {chmodError && <p className="mt-1 text-bad text-sm">{chmodError}</p>}
                    </>} />
                  {/* 함정 캡션(설계 §2.5): chown 명시 시 auto-chown 억제는
                      execution_manifests.py("chown" in spec.options) — 실패는
                      서버가 아니라 도구 실행 단계에서 나므로 여기서 미리 경고한다.
                      "비우면" 기본도 root 여부로 갈린다(_auto_chown): root 는 소스
                      소유권 보존, 아니면 **실행 신원**(요청자가 아니다 — 운영자가 실행
                      신원을 지정하면 그 사용자) 소유 자동 chown — 정직하게 병기 */}
                  <FieldRow
                    control={<>
                      <label className="text-sm block">chown (선택 · 숫자 uid:gid)
                        <input aria-label="chown" className={field} value={f.chown}
                               placeholder="예: 10003:10000"
                               onChange={on("chown")} />
                      </label>
                      {chownError && <p className="mt-1 text-bad text-sm">{chownError}</p>}
                    </>}
                    help={<>
                      {rootEffective
                        ? "비우면 원래(소스) 소유권을 보존합니다(지금 root 실행). chown 을 지정하면 목적지와 복사본이 그 값으로 셋업됩니다."
                        : <>비우면 실행 신원의 uid:gid 소유로 자동 chown 됩니다(지금 root 아님). chown 을 지정하면 자동
                            chown 이 꺼지는데, 본인 uid·주 gid 가 아닌 값이면 도구에 권한이 없어{" "}
                            <strong>dsync 는 데이터를 복사한 뒤 Failed 로 끝나고, nsync 는 소유 변경이 적용되지 않습니다</strong>.</>}
                      {" "}uid·gid 는 둘 다 숫자로 적으세요 — 한쪽을 비우면 그쪽은 소스 값이 유지됩니다.
                    </>} />
                </div>
              </details>
            )}
          </>
        ) : f.operation === "scan" ? (
          /* scan 옵션(구 SubmitScan 미러): 생략 = 도구 기본. */
          <>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="verbose" checked={f.verbose} onChange={on("verbose")} /> verbose
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
              </label>
            </div>
            {verboseQuietConflict && (
              <p className="text-bad text-sm">verbose와 quiet는 함께 쓸 수 없습니다</p>
            )}
            {/* 프리필 = 서버 기본(domain._OPTION_DEFAULTS, 2026-09-17). 비우면 키가
                빠지고 서버가 같은 값을 박는다 -- placeholder 는 그 사실을 말한다. */}
            <FieldRow
              control={<>
                <label className="text-sm block">batch_files (선택 · 0..1,000,000,000)
                  <input aria-label="batch_files" className={field} value={f.scanBatchFiles}
                         placeholder="비우면 기본 1,000,000 적용 · 0 = 배칭 안 함"
                         onChange={on("scanBatchFiles")} />
                </label>
                {scanBatchFilesError && <p className="mt-1 text-bad text-sm">{scanBatchFilesError}</p>}
              </>}
              help="미리 채운 1,000,000 = 서버 기본. 비워도 같은 값이 적용됩니다." />
            <FieldRow
              control={<>
                <label className="text-sm block">broken_limit (선택 · 0..10,000)
                  <input aria-label="broken_limit" className={field} value={f.brokenLimit}
                         placeholder="비우면 기본 100 적용 · 파손 경로 표본 보관 상한"
                         onChange={on("brokenLimit")} />
                </label>
                {brokenLimitError && <p className="mt-1 text-bad text-sm">{brokenLimitError}</p>}
              </>}
              help="미리 채운 100 = 서버 기본(리포트에 보관할 파손 경로 수). 비워도 같은 값이 적용됩니다." />
          </>
        ) : (
          <>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
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
            </div>
            {recursiveMissing && <p className="text-bad text-sm">재귀 옵션이 필요합니다</p>}
            {statLiteConflict && <p className="text-bad text-sm">stat과 lite는 함께 쓸 수 없습니다</p>}
          </>
        )}
      </FormSection>

      {/* 실행 설정은 운영자 전용(2026-08-20, 사용자 결정): 사용자 폼에선 우선순위를 정책 기본에
          맡긴다(생략 = resolve_priority 가 정책값으로 해석). 노드 수·프로세스 수도 정책이 정한다. */}
      {isAdmin && (
        <FormSection step={4} title="실행 설정" caption={policyCaption}>
          <FieldRow
            control={<label className="text-sm block">우선순위
              <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
                <option value="">{priorityDefaultLabel}</option>
                <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
              </select>
            </label>} />
          {/* 라벨 정정(사용자 결정 2026-08-16): 이 값(owner_username)은 결과물의
              소유자 기록이 아니라 **잡의 실행 신원**이다 — identity.py
              resolve_job_identity 가 `owner = owner_username or requester_id`
              로 잡의 신원을 정한다. 2026-09-30(프로덕션 사고): 예전 캡션("지정하면
              root 로 실행되고 지정한 사용자 신원으로 파일을 다룹니다")과 달리 실제로는
              특권 요청자면 **무조건 root** 였고, root 로 돈 sync 가 남의 700 목적지를
              소스 소유로 덮어썼다. 이제 root 는 아래 체크박스(rootEffective)가 정한다:
              자격 있는 관리자 기본 root, 다른 실행 신원을 적으면 기본이 그 사용자
              uid/gid -- 캡션은 그 사실을 그대로 말한다. 서버 게이트: 다른 신원·root
              둘 다 특권 인가(403 privileged_not_authorized). */}
          <FieldRow
            control={<label className="text-sm block">실행 신원(선택)
              <input aria-label="실행 신원(선택)" className={field}
                     placeholder="예: cocoa.song"
                     value={f.ownerUsername} onChange={on("ownerUsername")} />
            </label>}
            help={<>
              {canRoot ? "비우면 root 로 실행됩니다(관리자 기본 — 아래에서 끌 수 있음). "
                : "비우면 요청자 본인의 LDAP 계정(uid/gid)으로 실행됩니다. "}
              다른 사용자를 지정하면(특권 요청자만) 기본은{" "}
              <strong>그 사용자의 uid/gid</strong> 로 실행되어 그 사용자의 파일 권한이
              그대로 적용됩니다 — 그 사용자가 쓸 수 없는 목적지는 실패합니다.
            </>} />
          {canRoot && (
            <FieldRow align="check"
              control={<label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="root 권한으로 실행"
                       checked={rootEffective} onChange={on("runAsRoot")} />
                root 권한으로 실행(관리자 기본)
              </label>}
              help={<>
                켜면 잡이 root 로 실행되어 파일 권한 검사를 우회하고, <strong>sync 는
                목적지(이미 있는 디렉토리 포함)의 소유자·권한을 소스와 같게 바꿉니다</strong>(chown·chmod 를
                지정하면 그 값).{" "}
                실행 신원에 다른 사용자를 적으면 기본으로 꺼지고(그 사용자 권한으로 실행),
                끄면 실행 신원의 권한으로만 동작합니다.
                {rootEffective && f.operation === "rm" && (
                  <span className="mt-1 block text-bad">삭제가 root 권한으로 수행됩니다</span>
                )}
              </>} />
          )}
          {!canRoot && (
            <p className="text-muted text-xs">
              이 계정은 root 실행 자격이 없습니다(특권 요청자 목록 밖이거나 세션 로그인이 아님) —
              실행 신원의 권한으로 실행됩니다.
            </p>
          )}
        </FormSection>
      )}
    </>
  );

  const summary = (
    <SummaryPanel title="제출 요약" footer={<>
      {/* rm 경고는 한 곳 -- 제출 버튼 바로 위라 "고른 직후"와 "누르기 직전"을 함께 맡는다.
          위험=빨강 의미 체계 유지. */}
      {f.operation === "rm" && (
        <p className="rounded-lg border border-bad/30 bg-badbg px-3 py-2 text-sm text-bad">
          삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다.
        </p>
      )}
      <SubmitHints id="submit-job-hints" items={hints} />
      {submit.isError && <p className="text-bad text-sm">{(submit.error as ApiError).message}</p>}
      <div className="flex gap-2">
        <Button type="button" variant="ghost" onClick={() => nav("/jobs")}>취소</Button>
        <Button type="button" className="flex-1" disabled={blocked} onClick={handleSubmit}
                aria-describedby={hints.length > 0 ? "submit-job-hints" : undefined}>
          제출
        </Button>
      </div>
    </>}>
      <SummaryList>
        <SummaryRow label="연산">{f.operation}</SummaryRow>
        {f.operation === "sync" ? (
          <>
            <SummaryRow label="소스">{loc(f.sourceStorage, f.sourcePath)}</SummaryRow>
            <SummaryRow label="목적지">{loc(f.destStorage, f.destPath)}</SummaryRow>
            {/* 목적지 조건과 소유권 카드의 요점. root 실행이면 권한 검사가 우회되지만 상위 디렉토리
                존재는 여전히 필요하다(preflight test -w). */}
            <SummaryRow label="목적지 조건">{rootEffective
              ? `상위 디렉토리 ${destParentAbs ?? "(목적지 경로의 상위)"} 가 있어야 함(root 실행 — 권한 검사 우회). 목적지가 없으면 새로 만듦`
              : `상위 디렉토리 ${destParentAbs ?? "(목적지 경로의 상위)"} 가 있어야 하고 ${isAdmin ? "실행 신원" : "요청자 본인"}의 쓰기 권한 필요. 목적지가 없으면 새로 만들고, 이미 있으면 ${isAdmin ? "실행 신원" : "요청자 본인"} 소유·쓰기 가능해야 함`}
            </SummaryRow>
            <SummaryRow label="목적지 소유">{ownership.short}</SummaryRow>
          </>
        ) : (
          <SummaryRow label="대상">{loc(f.storage, f.target)}</SummaryRow>
        )}
        <SummaryRow label="옵션"><OptionTokens options={currentOptions()} /></SummaryRow>
        <SummaryRow label="우선순위">{f.priority === "" ? priorityDefaultLabel : f.priority}</SummaryRow>
        {isAdmin && f.ownerUsername.trim() !== "" && (
          <SummaryRow label="실행 신원">{f.ownerUsername.trim()}</SummaryRow>
        )}
        {isAdmin && (
          <SummaryRow label="실행 권한" className={rootEffective ? "text-bad" : ""}>
            {rootEffective ? "root(특권) — 권한 검사 우회, sync 는 목적지 소유·권한을 소스에 맞춤(chown·chmod 지정 시 그 값)"
              : "실행 신원의 uid/gid(권한 그대로 적용)"}
          </SummaryRow>
        )}
      </SummaryList>
    </SummaryPanel>
  );

  return (
    <section className="space-y-4">
      {/* 개명(사용자 결정 2026-08-18): 「작업 제출」→「단일 작업」 — 배치 작업과
          같은 성격의 단일 항목 제출임을 이름이 말한다. */}
      <div>
        <h1 className="text-2xl font-bold">단일 작업</h1>
        {me.data !== undefined && (
          <p className="mt-1 text-sm text-muted">
            {isAdmin ? "경로 하나에 sync·scan·rm 을 실행합니다 — 여러 경로의 sync·scan 은 배치 작업으로 묶어 요청하세요."
              : "스토리지 사이에서 디렉토리 하나를 sync 합니다."}
          </p>
        )}
      </div>
      {/* form 하나가 시트와 요약을 감싼다. 제출 버튼은 type="button" + onClick 이라 경로·실행 신원
          입력에서 Enter 를 눌러도 제출되지 않는다(관리자 기본 root·rm 을 요약도 안 보고 내는 회귀 방지).
          새어 나온 submit 이벤트(회귀)는 onSubmit 의 blocked 가드가 이중으로 막는다. */}
      <form onSubmit={handleSubmit}>
        <SubmitLayout sheet={sheet} summary={summary} />
      </form>
    </section>
  );
}
