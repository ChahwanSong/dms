import { useEffect, useRef, useState } from "react";
import type { MouseEvent, SyntheticEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, ChevronRight, Info, TriangleAlert } from "lucide-react";
import { useSubmitRequest } from "./useJobs";
import type { SubmitBody } from "./useJobs";
import { useStorageBackends, useUserStorages } from "../storages/useUserStorages";
import { useUserSyncPairs } from "../policies/useSyncPairs";
import { useMe } from "../auth/useAuth";
import { InfoCard } from "../../components/ui/InfoCard";
// 2026-11-27 단계별 등록(dms-k8s 219e8870·798480ab·b1eddc3c 이식): 한 장 시트 + 오른쪽 요약(SubmitLayout·SummaryPanel·FormSection)을
// 걷고 위저드 골격(components/form/Wizard) 위에 단계 넷을 그린다. 남은 표시 부품(FieldRow·SummaryList·SummaryRow·OptionTokens·
// SubmitHints)은 그대로 쓴다.
import { FieldRow, OptionTokens, SubmitHints, SummaryList, SummaryRow } from "../../components/form/SubmitLayout";
import { useWizard, WizardActionBar, WizardShell, WizardStepCard, WizardStepper } from "../../components/form/Wizard";
import type { WizardStep } from "../../components/form/Wizard";
import { MoreText, OperationCards, OptionToggle, ReviewCard, SubSection } from "../../components/form/wizardParts";
import { submitGates } from "./submitGates";
import type { SubmitStep } from "./submitGates";
import { ApiError } from "../../lib/api";
// field·StoragePicker 는 formFields.tsx 에 산다(슬라이스 31 T3) -- 제출 화면이 통째로 갈려도
// 다른 임포터가 흔들리지 않게 결합을 끊어 둔 것.
import { StoragePicker, field } from "./formFields";
import {
  absolutePath, destinationParent, normalizedRelativePath, relativePathProblem, RM_PATH_LINK_RULE, SYNC_PATH_LINK_RULE,
} from "../../lib/storagePaths";
import { pairAllowed, syncChoices } from "../../lib/syncPairs";
import { syncOwnership } from "../../lib/syncOwnership";
import { groupCaveat, groupCaveatsFor } from "../../lib/groupCaveats";
// 옵션 미러(CHMOD_RE·chownFieldError·intFieldError, sync 숫자 범위·프리필 SYNC_INT_FIELDS)는
// optionRules.ts 로 이사(슬라이스 32 T8) -- BatchCreate 옵션과 공유한다
// (사본이면 미러가 발산한다).
import { BUFSIZE_HELP, CHMOD_RE, chownFieldError, DIRECT_HELP, directBufsizeError, HELP_SUMMARY, OPEN_NOATIME_HELP,
         OPERATION_LABEL, PRIV_ROOT_SYNC, PRIV_USER, QUIET_HELP, SCAN_INT_FIELDS, SYNC_DSYNC_SPECIAL_FILE_NOTE, SYNC_INT_FIELDS,
         SYNC_MAX_ERRORS_NOTE, SYNC_OPTION_HELP, policyCaptionAlert, scanIntFieldError, syncIntFieldError } from "./optionRules";
// 정책 기본값 캡션(슬라이스 37: 배치 생성과 같은 표시 배선 — 백엔드 무변경).
import { usePolicies } from "../policies/usePolicies";
import type { Policy } from "../../lib/types";
import { NODE_CPU_CAP_NOTE } from "../../lib/jobTool";

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
  // open_noatime 초기값 ON(사용자 결정 2026-08-22). 운영자 폼은 이 체크박스를 **root 여부와 무관하게** 그대로
  // 싣는다(2026-10-27, syncOptions): O_NOATIME 은 root(또는 파일 소유자)만 쓸 수 있지만, 포크가 EPERM(실행 신원 소유가
  // 아닌 파일)이면 그 파일만 O_NOATIME 없이 다시 연다 -- 복사(nsync db1e33c·공통 43ba468)와 dsync --contents 비교(7a3189c)
  // 모두. 그래서 2026-09-30 의 「root 실행일 때만 싣는다」(비 root 에서 부분 복사 뒤 실패하던 시절의 우회)는 걷혔다 --
  // 되살리지 마라. 사용자 폼은 옵션을 보이지 않아(isAdmin) 싣지 않는다. 배치는 BatchCreate 가 같은 기본 ON 을 유지한다.
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

// 연산 카드의 설명 한 줄(라디오의 aria-describedby) -- 이름만으로는 무엇이 일어나는지 화면이 말하지 않았다.
const OPERATION_HELP: Record<Operation, string> = {
  sync: "소스 디렉토리의 내용을 목적지로 복사·동기화합니다.",
  scan: "대상 경로의 파일 수·용량·데이터 온도 통계를 수집합니다.",
  // d194: 대상 자신이 심볼릭 링크면 링크만 지운다(drm lstat) -- 「그 아래를 모두」가 링크 너머까지로 읽히지 않게.
  rm: "대상 경로와 그 아래를 모두 삭제합니다(대상이 심볼릭 링크면 링크만 지웁니다).",
};

const ALL_OPERATIONS: Operation[] = ["sync", "scan", "rm"];

// 경로 입력 바로 아래의 즉답: 서버가 거부할 경로면 그 이유(relativePathProblem = validate_relative_path
// 미러), 아니면 입력한 상대경로를 관리 디렉토리에 붙인 절대경로(관리 디렉토리를 알 때만 -- 모르면 지어내지
// 않는다). 절대경로는 **보내는 값 그대로**로 짓는다 -- 다듬은 값으로 지으면 "/a" 나 "x " 같은 입력에
// 서버가 실제로 쓰지 않을(또는 거부할) 경로를 그럴듯하게 보여 준다(리뷰 2026-10-06).
// 라벨은 「지정 경로」(2026-10-27 d192, 괴리 [C2]): 글자 그대로 붙인 경로라 링크를 풀지 않는다 -- 「실제 경로」라 부르면 링크를
// 거치는 경로로 거부된 뒤 사용자가 화면이 「실제」라 한 바로 그 경로를 다시 낸다(lib/storagePaths absolutePath 주석).
// 「서버가 저장할 모양」으로 짓는다(2026-10-28 d194): 서버는 경로를 normpath 로 정리해 저장한다(「//」·「.」·끝의 「/」 제거 --
// 공백은 그대로). 입력과 다르면 저장되는 경로를 말한다. rm 은 끝의 「/」 가 뜻을 바꿀 것처럼 보인다(셸의 「link/」 는 링크 너머) --
// 서버에선 사라져 그 이름이 링크면 링크만 지워진다는 것을 입력 자리에서 말한다(lib/storagePaths normalizedRelativePath).
function PathFeedback({ root, rel, op }: { root: string | null | undefined; rel: string; op: Operation }) {
  if (rel.trim() === "") return null;
  const problem = relativePathProblem(rel);
  if (problem !== null) return <p className="mt-1 text-bad text-sm">{problem}</p>;
  const stored = normalizedRelativePath(rel);
  const abs = stored === null ? null : absolutePath(root, stored);
  const changed = stored !== null && stored !== rel;
  return (
    <>
      {abs !== null && (
        <p className="mt-1 text-xs text-muted [overflow-wrap:anywhere]">{`지정 경로: ${abs}`}</p>
      )}
      {changed && (
        <p className="mt-1 text-xs text-muted [overflow-wrap:anywhere]" role="note" aria-label="저장되는 경로">
          {op === "rm" && rel.endsWith("/")
            ? `끝의 / 는 무시됩니다 — 이 이름이 링크면 링크만 지워집니다(저장되는 경로: ${stored})`
            : `서버는 이 경로를 정리해 저장합니다(저장되는 경로: ${stored})`}
        </p>
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
  // 남지만 아래 검증·바디가 f.operation 으로 걸러 싣지 않는다. 단계 본문이 언마운트돼도(단계 이동) 값은 여기 남는다.
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
  // 연산 선택지(사용자 연산 allowlist 2026-08-20, 사용자 결정): 비운영자는 sync 만. scan·rm 은 admin 전용 -- 표시 게이트일 뿐
  // 진짜 차단은 서버(routes_requests operation_admin_only 403).
  const operations: Operation[] = isAdmin ? ALL_OPERATIONS : ["sync"];
  // 보조 그룹 주의문(2026-10-07 D15): 고른 소스·목적지의 등록 종류(backend_type)별 한 줄. 일반 주의문은 아래
  // 권한 안내 문장이 이미 말하므로 빼고, 종류별로 더 할 말이 있는 스토리지(NFS·Lustre)만 덧붙인다.
  const backends = useStorageBackends();
  const syncGroupCaveats = groupCaveatsFor([f.sourceStorage, f.destStorage], backends)
    .filter((c) => c !== groupCaveat(undefined));
  // sync 목적지의 상위 디렉토리(쓰기 권한이 필요한 곳) -- 관리 디렉토리를 알면 절대경로로.
  const destRoot = rootOf(f.destStorage);
  const destParentAbs = f.destPath.trim() === "" ? null : destinationParent(destRoot, f.destPath.trim());
  // 결과(목적지) 소유권 안내 -- 바디와 같은 값(chown 옵션·rootEffective·실행 신원)에서 파생한다
  // (lib/syncOwnership = 서버 _auto_chown 미러). 사용자는 언제나 "요청자 본인 uid:gid". chown 칸은 관리자 전용
  // 고급 옵션이라 비관리자에겐 "chown 에 지정하세요" 대신 관리자에게 요청하라고 말한다(chownEditable).
  const ownership = syncOwnership({
    chown: f.chown, chmod: f.chmod, root: rootEffective,
    runAs: otherOwner ? ownerTrim : (me.data?.actor ?? null), self: !otherOwner, chownEditable: isAdmin });
  // 사용자 sync 허용 쌍(2026-09-30 사용자 결정: 기본 전부 불가 + 관리자가 허용한 소스 → 목적지 쌍).
  // 관리자는 제한이 없어 조회하지 않는다. 선택지 필터는 표시일 뿐 -- 제출·계획·컨펌이 서버에서 다시
  // 본다(sync_pair_not_allowed). 신원·허용 목록을 알기 전에는 sync 선택지를 비운다: 거르기 전 목록이
  // 잠깐 보였다 사라지면 그 사이 고른 값이 허용 밖일 수 있다(그동안 대상 단계는 targetMissing 으로 막힌다 -- submitGates 머리 주석).
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
  // 범위 오류가 먼저, 범위 안이면 direct 정렬 조건(서버 422 direct_bufsize_misaligned 미러 -- optionRules 주석). direct 칸은
  // 관리자에게만 보이지만 f.direct 는 비관리자에겐 늘 false 라 판정이 그대로 맞다.
  const bufsizeError = f.operation === "sync"
    ? syncIntFieldError("bufsize", f.bufsize) ?? directBufsizeError(f.direct, f.bufsize) : null;
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
  const optionsInvalid = recursiveMissing || statLiteConflict || verboseQuietConflict
    || advancedError !== null || scanBatchFilesError !== null || brokenLimitError !== null;
  // 제출 게이트·이유 목록·단계 게이트는 순수 함수 하나가 같은 잎에서 만든다(submitGates -- 식·문장·순서는 옛 한 장 시트의 blocked·
  // hints 그대로이고 단계 태그만 붙인다; 합집합 불변식은 잎 조합 전수 시험). 잎 계산은 위 그대로 이 화면에 있다.
  const gates = submitGates({
    isPending: submit.isPending, storagesError: storagesQ.isError, op: f.operation,
    pairsError, noPairs: allowedPairs !== null && allowedPairs.length === 0, pairBlocked,
    targetMissing, pathInvalid, optionsInvalid,
  });
  const blocked = gates.blocked;
  // 제출이 잠긴 이유(검토 단계 바, 제출 버튼 위). 필드 옆 빨간 문구와 **다른 문장**으로 짧게 -- 같은 문장을 두 번 그리지 않는다
  // (SubmitHints 주석). 대상 미입력 안내만은 원문이 여기 한 곳에 산다. 각 단계 바에는 그 단계 태그의 첫 줄(gates.barReason).
  const hints = gates.hints;

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
  // 캡션이 거부 경고(조회 실패·비활성·행 부재)를 담으면 「자세히」에 접지 않고 펼친 채 둔다(optionRules.policyCaptionAlert -- 정책을
  // 조회하는 관리자 화면에서만 그린다: 꺼진 조회는 「조회 실패」로 읽힌다).
  const policyAlert = policyCaptionAlert(byTool, f.operation, policiesQ.isLoading);
  // 우선순위 "" = (정책 기본): resolve_priority 미러 — sync 는 dsync 정책 대표.
  const defaultPolicy = byTool?.get(f.operation === "sync" ? "dsync" : f.operation);
  const priorityDefaultLabel = defaultPolicy === undefined
    ? "(정책 기본)" : `(정책 기본: ${defaultPolicy.default_priority})`;

  const on = (k: keyof typeof initial) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  function syncOptions(): SubmitBody["options"] {
    const options: SubmitBody["options"] = checkedOptions({
      delete: f.delete, contents: f.contents, direct: f.direct, quiet: f.quiet,
      // open_noatime 은 운영자 폼의 체크박스 그대로(root 여부 무관, 2026-10-27): 포크가 O_NOATIME 의
      // EPERM(실행 신원 소유가 아닌 파일)을 그 파일만 O_NOATIME 없이 다시 열어 복사하므로, 비 root 실행에서
      // 부분 복사 뒤 Failed 가 되던 2026-09-30 의 이유가 없어졌다. 사용자 폼은 이 옵션을 보이지 않는다
      // (2026-08-22 사용자 결정 「사용자 요청은 기본 OFF」 유지 -- isAdmin).
      open_noatime: isAdmin && f.openNoatime,
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

  const targetTitle = f.operation === "sync" ? "소스와 목적지"
    : f.operation === "scan" ? "스캔 대상" : "삭제 대상";

  // ---- 단계별 등록(2026-11-27, dms-k8s 219e8870·798480ab 이식 -- 그 라인의 사용자 결정 「초안 1 상단 스테퍼」) ------------------
  // 값·검증·바디는 위 그대로다(handleSubmit 의 바디 조립 무변경 -- 시험의 바디 toEqual 이 바이트 단위 그물). 바뀐 것은 그리는 방식뿐:
  // 단계 넷(작업 종류 → 대상 → 옵션 → 검토·제출)이고 단계 게이트를 모두 합치면 옛 제출 게이트에서 진행 중 상태를 뺀 것과 같다
  // (submitGates). 단계 본문은 이 파일의 지역 render 함수다(수십 개의 파생값을 읽는다 -- 따로 떼면 props 가 폭발하고 「로직 그대로」를
  // 확인하기 어렵다). 단계 상태·URL(?step=)·Enter·포커스·도달 규칙은 useWizard 가 쥔다(규칙 전문은 components/form/Wizard 머리 주석).
  // 실행 신원·root 실행은 ① 에 둔다(dms-k8s 의 「권한 기준 사용자」 자리): ② 목적지 조건·③ chown 도움말·④ 「실행 권한」이 모두 이
  // 값(rootEffective)에 기댄다 -- 뒤 단계에서 정하면 앞 단계의 안내가 바뀐 값을 모른 채 읽힌다.
  const [params] = useSearchParams();
  // 비관리자는 연산이 sync 하나뿐이다 -- 고를 것이 없는 ①을 완료로 두고 ②에서 시작한다(dms-k8s design-wizard §1-6). me 가 처음
  // 렌더부터 있으면(셸이 이미 받아 둔 캐시) 처음 상태로, 늦게 오면 아래 효과가 replace 로 옮긴다 -- 로드 전 대체값도 ["sync"] 라 me
  // 가 오기 전에는 판정하지 않는다(그러면 관리자도 ①을 건너뛴다).
  const singleOp = me.data !== undefined && operations.length === 1;
  // 스테퍼 아래 값(방문했거나 지난 단계만, 넓은 폭에서 보인다). ① 은 연산 + (관리자) root·다른 실행 신원, ② 는 검토와 같은 loc 문법,
  // ③ 은 켠 불리언 옵션 이름(늘 켜진 기본 open_noatime·recursive 는 빼고, 관리자가 open_noatime 을 끄면 그 사실을) + 관리자 우선순위.
  const kindValue = [f.operation, ...(rootEffective ? ["root"] : []), ...(isAdmin && otherOwner ? [`실행 신원 ${ownerTrim}`] : [])]
    .join(" · ");
  const sideValue = (storage: string, path: string) => { const t = loc(storage, path); return t === "—" ? null : t; };
  const targetValue = f.operation !== "sync" ? sideValue(f.storage, f.target) ?? "미선택"
    : sideValue(f.sourceStorage, f.sourcePath) === null && sideValue(f.destStorage, f.destPath) === null ? "미선택"
    : `${sideValue(f.sourceStorage, f.sourcePath) ?? "미선택"} → ${sideValue(f.destStorage, f.destPath) ?? "미선택"}`;
  const optionsValue = (() => {
    const onKeys = Object.entries(currentOptions() ?? {})
      .filter(([k, v]) => v === true && k !== "open_noatime" && k !== "recursive").map(([k]) => k);
    if (f.operation === "sync" && isAdmin && !f.openNoatime) onKeys.push("open_noatime 끔");
    if (isAdmin && f.priority !== "") onKeys.push(`우선순위 ${f.priority}`);
    return onKeys.length === 0 ? "기본값" : onKeys.join(" · ");
  })();
  const optionsTitle = isAdmin ? "옵션·실행 설정" : "옵션";
  const steps: WizardStep<SubmitStep>[] = [
    { key: "kind", title: "작업 종류", blocked: gates.gate.kind, reason: gates.barReason("kind"), value: kindValue },
    { key: "target", title: targetTitle, blocked: gates.gate.target, reason: gates.barReason("target"), value: targetValue },
    { key: "options", title: optionsTitle, blocked: gates.gate.options, reason: gates.barReason("options"), value: optionsValue },
    // 마지막 단계의 blocked 는 「다음」이 아니라 최종 제출 게이트(= blocked)다.
    { key: "review", title: "검토·제출", blocked, reason: gates.barReason("review"), value: "제출 전 확인" },
  ];
  const wizard = useWizard<SubmitStep>({ steps, initial: singleOp ? "target" : "kind" });
  const autoStartChecked = useRef(me.data !== undefined);
  useEffect(() => {
    if (autoStartChecked.current || me.data === undefined) return;
    autoStartChecked.current = true;
    // 아직 사용자가 단계를 옮기지 않았을 때만(현재 ①이고 URL 에 step 이 없다). ①은 스테퍼로 다시 열 수 있다.
    if (singleOp && wizard.current === "kind" && !params.has("step")) wizard.jump("target");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me.data]);

  // 제출 방어 세 겹(dms-k8s design-wizard §1-6 「제출 방어」): ① 더블클릭 -- 「다음」과 「제출」은 바의 같은 자리·같은 노드라 ③에서
  // 「다음」을 두 번 누르면 두 번째 클릭이 막 그려진 「제출」에 떨어진다. 같은 연속 클릭의 두 번째 이후(e.detail > 1)를 무시한다(바도
  // 같은 줄을 둔다 -- 둘 다 있어도 무해). ② 단계 가드 -- 검토를 안 보고 새어 나온 submit 이벤트(암묵 제출·회귀)는 current !== "review"
  // 로 막는다. ③ 모든 버튼 type="button"(form 안 type 없는 버튼은 submit 버튼이다). 그래도 새는 submit 은 blocked 가드가 한 번 더
  // 막는다(관리자 기본 root·rm 을 검토도 안 보고 내는 회귀 방지).
  function handleSubmit(e?: SyntheticEvent) {
    e?.preventDefault();
    if (blocked || wizard.current !== "review") return;
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
  const handleSubmitClick = (e: MouseEvent<HTMLButtonElement>) => {
    if (e.detail > 1) return;
    handleSubmit();
  };

  // ---- ① 작업 종류 ----------------------------------------------------------------------------------------------------
  // 연산 카드형 라디오(접근 이름 「연산」·라디오 이름 sync·scan·rm 은 옛 select 와 같다). 카드 설명은 OPERATION_HELP 원문, 부제는
  // OPERATION_LABEL(optionRules). 관리자에겐 실행 신원·root 실행이 이어진다.
  // 라벨 정정(사용자 결정 2026-08-16): 실행 신원(owner_username)은 결과물의 소유자 기록이 아니라 **잡의 실행 신원**이다 — identity.py
  // resolve_job_identity 가 `owner = owner_username or requester_id` 로 잡의 신원을 정한다. 2026-09-30(프로덕션 사고): 예전 캡션
  // ("지정하면 root 로 실행되고 지정한 사용자 신원으로 파일을 다룹니다")과 달리 실제로는 특권 요청자면 **무조건 root** 였고, root 로
  // 돈 sync 가 남의 700 목적지를 소스 소유로 덮어썼다. 이제 root 는 「root 권한으로 실행」(rootEffective)이 정한다: 자격 있는 관리자
  // 기본 root, 다른 실행 신원을 적으면 기본이 그 사용자 uid/gid -- 도움말은 그 사실을 그대로 말한다(요약 한 줄 HELP_SUMMARY.runAsSingle
  // 은 늘 보이고 전문은 「자세히」). 서버 게이트: 다른 신원·root 둘 다 특권 인가(403 privileged_not_authorized).
  const ownerHelp = (<>
    {canRoot ? "비우면 root 로 실행됩니다(관리자 기본 — 아래에서 끌 수 있음). "
      : "비우면 요청자 본인의 LDAP 계정(uid/gid)으로 실행됩니다. "}
    다른 사용자를 지정하면(특권 요청자만) 기본은{" "}
    <strong>그 사용자의 uid/gid</strong> 로 실행되어 그 사용자의 파일 권한이
    그대로 적용됩니다 — 그 사용자가 쓸 수 없는 목적지는 실패합니다.
  </>);
  // root 토글의 전문. 켜져 있으면(관리자 기본) 펼친 채 고정된다(OptionToggle expandOnOn) -- 소유 덮어쓰기가 늘 보인다.
  const rootHelp = (<>
    켜면 잡이 root 로 실행되어 파일 권한 검사를 우회하고, <strong>sync 는
    목적지(이미 있는 디렉토리 포함)의 소유자·권한을 소스와 같게 바꿉니다</strong>(chown·chmod 를
    지정하면 그 값).{" "}
    실행 신원에 다른 사용자를 적으면 기본으로 꺼지고(그 사용자 권한으로 실행),
    끄면 실행 신원의 권한으로만 동작합니다.
  </>);
  const kindStep = () => (
    <>
      <div className="space-y-2">
        <p aria-hidden className="text-sm font-semibold text-ink">연산</p>
        <OperationCards name="submit-job-operation" value={f.operation}
                        onChange={(v) => setF({ ...f, operation: v })}
                        options={operations.map((o) => ({
                          value: o, subtitle: OPERATION_LABEL[o], description: OPERATION_HELP[o], danger: o === "rm" }))} />
      </div>
      {isAdmin && (
        <SubSection title="실행 신원·권한" className="border-t border-line pt-5">
          <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            <label className="min-w-0 text-sm block">실행 신원(선택)
              <input aria-label="실행 신원(선택)" className={field}
                     placeholder="예: cocoa.song"
                     value={f.ownerUsername} onChange={on("ownerUsername")} />
            </label>
            <MoreText className="sm:pt-0.5" summary={HELP_SUMMARY.runAsSingle} full={ownerHelp} />
          </div>
          {canRoot ? (
            <>
              <OptionToggle label="root 권한으로 실행" title="root 권한으로 실행(관리자 기본)" danger
                            checked={rootEffective} onChange={on("runAsRoot")}
                            summary={HELP_SUMMARY.runAsRoot} full={rootHelp} />
              {rootEffective && f.operation === "rm" && (
                <p className="text-sm text-bad">삭제가 root 권한으로 수행됩니다</p>
              )}
            </>
          ) : (
            <p className="text-muted text-xs">
              이 계정은 root 실행 자격이 없습니다 — 실행 신원의 권한으로 실행됩니다. admin 역할만으로는 생기지 않으며,
              root 자격이 있는 관리자가 「관리 → 계정」의 root 실행에서 줄 수 있습니다(세션 로그인 필요).
            </p>
          )}
        </SubSection>
      )}
    </>
  );

  // ---- ② 대상 ---------------------------------------------------------------------------------------------------------
  // "경로는 관리 디렉토리 아래 상대경로" 는 스토리지 선택 아래 캡션(StoragePicker)이 이미 말하고, 입력 아래 "지정 경로" 가 그 결과를
  // 보여 준다 -- 구획 캡션으로 세 번째 반복하지 않는다. sync 의 두 쪽은 테두리 상자로 묶고(어느 칸이 어느 쪽인지 한눈에), 두 쪽 상자는
  // lg 부터 나란히(768 은 사이드바를 빼면 본문이 약 488px 라 위아래로) 둔다. 경로 칸: 서버가 거부할 값이면 aria-invalid(막힌 「다음」이
  // 그리로 포커스), 스토리지는 골랐는데 경로가 비었으면 data-wizard-nudge(빨갛게 칠하지 않는 「아직 비어 있는 필수 칸」 표지 -- dms-k8s
  // b1eddc3c 의 rm 빈 PV 경로 자리) -- 스토리지를 아직 안 골랐으면 막힘은 스토리지라 달지 않는다.
  const SIDE_BOX = "min-w-0 rounded-xl border border-line p-4";
  const pathInput = (label: string, value: string, storage: string, placeholder: string, key: "sourcePath" | "destPath" | "target") => (
    <label className="text-sm block">{label}
      <input aria-label={label} className={field} value={value} placeholder={placeholder}
             aria-invalid={relativePathProblem(value) !== null || undefined}
             data-wizard-nudge={(storage !== "" && value.trim() === "") || undefined}
             onChange={on(key)} />
    </label>
  );
  // sync 목적지 한 줄(② -- 「목적지 조건」 요약 행 문자열 + 「목적지 소유」 short). 전문(목적지 조건과 소유권 카드)은 ④ 검토에 접지
  // 않고 둔다(2026-09-30 「분명히 보이게」). root 실행이면 권한 검사가 우회되지만 상위 디렉토리 존재는 여전히 필요하다(preflight test -w).
  const destCondition = rootEffective
    ? `상위 디렉토리 ${destParentAbs ?? "(목적지 경로의 상위)"} 가 있어야 함(root 실행 — 권한 검사 우회). 목적지가 없으면 새로 만듦`
    : `상위 디렉토리 ${destParentAbs ?? "(목적지 경로의 상위)"} 가 있어야 하고 ${isAdmin ? "실행 신원" : "요청자 본인"}의 쓰기 권한 필요. 목적지가 없으면 새로 만들고, 이미 있으면 ${isAdmin ? "실행 신원" : "요청자 본인"} 소유·쓰기 가능해야 함`;
  const targetStep = () => (
    <>
      {/* 목록 로드 실패 문구는 스토리지를 고르는 이 단계에 -- 잠금은 단계 게이트·제출 게이트가 맡는다 */}
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
          <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
            <div className={`${SIDE_BOX} flex flex-col gap-3`}>
              <StoragePicker label="소스 스토리지" value={f.sourceStorage}
                onChange={(v) => setF({ ...f, sourceStorage: v })} storages={sourceChoices}
                loading={loadingStorages || pairsPending} />
              <div>
                {pathInput("소스 경로", f.sourcePath, f.sourceStorage, "예: team/dataset", "sourcePath")}
                <PathFeedback root={rootOf(f.sourceStorage)} rel={f.sourcePath} op="sync" />
              </div>
            </div>
            <ArrowRight aria-hidden
                        className="h-5 w-5 rotate-90 self-center justify-self-center text-muted lg:rotate-0" />
            <div className={`${SIDE_BOX} flex flex-col gap-3`}>
              <StoragePicker label="목적지 스토리지" value={f.destStorage}
                onChange={(v) => setF({ ...f, destStorage: v })} storages={destChoices}
                loading={loadingStorages || pairsPending} />
              <div>
                {pathInput("목적지 경로", f.destPath, f.destStorage, "예: backup/dataset", "destPath")}
                <PathFeedback root={destRoot} rel={f.destPath} op="sync" />
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
          {/* 허용 목록은 화면에 있는 동안에도 다시 읽힌다(포커스·staleTime) -- 관리자가 쌍을 해제하면 대상 단계·제출이 잠기고,
              그 이유를 고르는 자리에서 경보로 말한다(페이지의 유일한 role=alert). 다른 단계에 있으면 그 단계의 빨간 점과 검토 바의
              「2단계에서 고치기」가 말한다(위저드 도달 규칙 -- 사용자를 끌어내리지 않는다). */}
          {pairBlocked && (
            <p className="text-bad text-sm" role="alert">
              허용되지 않은 소스 → 목적지 조합입니다 — 관리자가 허용한 조합만 sync 할 수 있습니다(허용이
              해제됐을 수 있습니다).
            </p>
          )}
          <div className="flex items-start gap-2.5 rounded-xl bg-infobg px-4 py-3">
            <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-accent" />
            <div className="min-w-0 flex-1">
              <SummaryList>
                <SummaryRow label="목적지 조건">{destCondition}</SummaryRow>
                <SummaryRow label="목적지 소유">{ownership.short}</SummaryRow>
              </SummaryList>
              <p className="mt-1.5 text-xs text-muted">전문은 ④ 검토·제출에서 봅니다.</p>
            </div>
          </div>
        </>
      ) : (<>
        {/* scan·rm 공용: 스토리지 하나 + 대상 경로(상대). */}
        <div className={`${SIDE_BOX} grid gap-3 sm:grid-cols-2`}>
          <StoragePicker label="스토리지" value={f.storage}
            onChange={(v) => setF({ ...f, storage: v })} storages={storages} loading={loadingStorages} />
          <div>
            {pathInput("대상 경로", f.target, f.storage, "예: team/projects", "target")}
            <PathFeedback root={rootOf(f.storage)} rel={f.target} op={f.operation} />
          </div>
        </div>
        {/* rm 대상의 링크 규칙(괴리 [14]·[27], d194~ 중간 단계 링크는 거부·대상 자신이 링크면 링크만 삭제): 거부된 뒤
            「링크가 가리키는 곳」을 다시 내지 않게, 그리고 링크를 넣은 사람이 데이터까지 지워진다고 믿지 않게 고르는
            자리에서 먼저 말한다. */}
        {f.operation === "rm" && (
          <p className="text-xs text-muted" role="note" aria-label="삭제 경로 조건">{RM_PATH_LINK_RULE}</p>
        )}
      </>)}
    </>
  );

  // ---- ③ 옵션(·실행 설정) ----------------------------------------------------------------------------------------------
  // 불리언 옵션은 토글 카드(겉모양만 스위치 -- 역할·접근 이름은 옛 체크박스와 같다): 요약 한 줄이 늘 보이고 전문은 「자세히」 또는
  // 켜면 펼쳐진다(d192 「direct·quiet 설명은 툴팁이 아니라 칸 옆에」 -- 요약이 핵심 사실을 담는다). delete 와 무관한 dsync 삭제는
  // delete 를 접어도 사라지지 않게 늘 보이는 한 줄(SYNC_DSYNC_SPECIAL_FILE_NOTE). 사용자에겐 숨은 기본 옵션을 토큰으로 보인다 --
  // syncOptions() 에서 delete·contents 를 뺀 값(바디와 같은 함수라 「보이는 것 = 보내는 것」).
  const optionsStep = () => (
    <>
      <SubSection title="실행 옵션"
                  caption={f.operation === "sync" ? <MoreText summary={HELP_SUMMARY.maxErrors} full={SYNC_MAX_ERRORS_NOTE} /> : undefined}>
        {f.operation === "sync" ? (
          <>
            {/* 옵션 설명(2026-08-20, 사용자 요청): dsync --delete / --contents. direct·quiet·고급옵션·우선순위는 운영자 전용
                (2026-08-20, 사용자 결정): 사용자 sync 폼은 delete·contents 만 남긴다. 숨겨도 제출 payload 는 동일하다 --
                checkedOptions 가 기본값(false)을 이미 생략하고, 고급 프리필(batch_files·bufsize)은 도구 기본값이라 운영자가 고급을
                안 펼친 것과 결과가 같다. direct·quiet 의 요약은 칸 옆에 늘 보인다(2026-10-27 d192 -- quiet 가 실패 원인까지 지운다는
                것(괴리 [9]·[13]·[18]·[29])과 direct 의 bufsize 조건([8])). */}
            <div className="grid items-start gap-3 lg:grid-cols-2">
              <OptionToggle label="delete" checked={f.delete} onChange={on("delete")}
                            summary={HELP_SUMMARY.delete} full={SYNC_OPTION_HELP.delete} />
              <OptionToggle label="contents" checked={f.contents} onChange={on("contents")}
                            summary={HELP_SUMMARY.contents} full={SYNC_OPTION_HELP.contents} />
              {isAdmin && (<>
                <OptionToggle label="direct" checked={f.direct} onChange={on("direct")}
                              summary={HELP_SUMMARY.direct} full={DIRECT_HELP} />
                <OptionToggle label="quiet" checked={f.quiet} onChange={on("quiet")}
                              summary={HELP_SUMMARY.quietSync} full={QUIET_HELP.sync} />
              </>)}
            </div>
            <p className="flex items-start gap-1.5 text-xs text-attn">
              <TriangleAlert aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{SYNC_DSYNC_SPECIAL_FILE_NOTE}</span>
            </p>
            {!isAdmin && (
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
                <span>기본으로 함께 실립니다:</span>
                <OptionTokens options={Object.fromEntries(Object.entries(syncOptions() ?? {})
                  .filter(([k]) => k !== "delete" && k !== "contents"))} />
              </div>
            )}
            {/* 기본 접힘 — 단순 sync 제출 동선을 바꾸지 않기 위해 <details> 로 숨긴다 */}
            {isAdmin && (
              <details className="rounded-lg border border-line"
                       open={advancedOpen || advancedError !== null}
                       onToggle={(e) => {
                         const el = e.currentTarget;
                         // 오류가 있는 동안은 접히지 않는다: 접으면 다음 단계로 못 가는 이유(빨간 안내)가 숨는다.
                         // open prop 은 true→true 라 React 가 DOM 을 다시 열어 주지 않는다 -- 직접 되돌린다.
                         if (!el.open && advancedError !== null) { el.open = true; return; }
                         setAdvancedOpen(el.open);
                       }}>
                <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium">고급 옵션</summary>
                <div className="space-y-4 border-t border-line px-4 py-4">
                  <FieldRow align="check"
                    control={<label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" aria-label="open_noatime"
                             checked={f.openNoatime}
                             onChange={on("openNoatime")} /> open_noatime
                    </label>}
                    help={OPEN_NOATIME_HELP} />
                  {/* 프리필 계약(2026-09-17): 값이 미리 채워져 있고(placeholder 가
                      아니다) 그 값이 곧 서버 기본이라 비워도 같은 값이 적용된다 —
                      배칭을 끄는 유일한 표현은 0 명시. placeholder 는 "비웠을 때
                      무슨 일이 나는가"를, 캡션은 "지금 채워진 값"을 말한다. */}
                  <FieldRow
                    control={<>
                      <label className="text-sm block">batch_files (선택 · 0..10,000,000)
                        <input aria-label="batch_files" className={field} value={f.batchFiles}
                               aria-invalid={batchFilesError !== null || undefined}
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
                               aria-invalid={bufsizeError !== null || undefined}
                               placeholder="비우면 기본 4 MiB 적용"
                               onChange={on("bufsize")} />
                      </label>
                      {bufsizeError && <p className="mt-1 text-bad text-sm">{bufsizeError}</p>}
                    </>}
                    help={BUFSIZE_HELP} />
                  <FieldRow
                    control={<>
                      <label className="text-sm block">chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)
                        <input aria-label="chmod" className={field} value={f.chmod}
                               aria-invalid={chmodError !== null || undefined}
                               onChange={on("chmod")} />
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
                               aria-invalid={chownError !== null || undefined}
                               placeholder="예: 10003:10000"
                               onChange={on("chown")} />
                      </label>
                      {chownError && <p className="mt-1 text-bad text-sm">{chownError}</p>}
                    </>}
                    help={<>
                      {rootEffective
                        ? "비우면 원래(소스) 소유권을 보존합니다(지금 root 실행). chown 을 지정하면 목적지와 복사본이 그 값으로 셋업됩니다."
                        : <>비우면 실행 신원의 uid:주 그룹 gid 소유로 자동 chown 됩니다(지금 root 아님) — 프로젝트(보조)
                            그룹 소유로 남기려면 uid:&lt;그룹 gid&gt; 를 지정하세요. gid 가 실행 신원의 주 그룹 또는 이 작업에
                            적용된 보조 그룹이 아니면(보조 그룹이 적용되지 않으면 주 그룹만) 계획 단계에서
                            거부되고(chown_group_not_member), uid 가 본인이 아니면 도구에 권한이 없어{" "}
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
            {/* 상충 문구는 quiet 토글의 오류 줄(체크박스 aria-invalid -- 막힌 「다음」이 그리로 포커스, BatchCreate 와 같은 자리). */}
            <div className="grid items-start gap-3 lg:grid-cols-2">
              <OptionToggle label="verbose" checked={f.verbose} onChange={on("verbose")} />
              <OptionToggle label="quiet" checked={f.quiet} onChange={on("quiet")}
                            error={verboseQuietConflict ? "verbose와 quiet는 함께 쓸 수 없습니다" : null} />
            </div>
            {/* 프리필 = 서버 기본(domain._OPTION_DEFAULTS, 2026-09-17). 비우면 키가
                빠지고 서버가 같은 값을 박는다 -- placeholder 는 그 사실을 말한다. */}
            <FieldRow
              control={<>
                <label className="text-sm block">batch_files (선택 · 0..1,000,000,000)
                  <input aria-label="batch_files" className={field} value={f.scanBatchFiles}
                         aria-invalid={scanBatchFilesError !== null || undefined}
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
                         aria-invalid={brokenLimitError !== null || undefined}
                         placeholder="비우면 기본 100 적용 · 파손 경로 표본 보관 상한"
                         onChange={on("brokenLimit")} />
                </label>
                {brokenLimitError && <p className="mt-1 text-bad text-sm">{brokenLimitError}</p>}
              </>}
              help="미리 채운 100 = 서버 기본(리포트에 보관할 파손 경로 수). 비워도 같은 값이 적용됩니다." />
          </>
        ) : (
          <>
            {/* quiet 는 지우지 못한 항목 목록까지 없앤다 -- 요약이 그 사실을 늘 말하고 전문은 자세히·켜면(sync 와 같은 이유). */}
            <div className="grid items-start gap-3 lg:grid-cols-2">
              <OptionToggle label="재귀 삭제(필수)" checked={f.recursive} onChange={on("recursive")}
                            error={recursiveMissing ? "재귀 옵션이 필요합니다" : null} />
              <OptionToggle label="stat" checked={f.stat} onChange={on("stat")} />
              {/* stat+lite 상충 문구는 lite 토글의 오류 줄(aria-invalid -- 막힌 「다음」의 포커스 자리, BatchCreate 와 같은 자리). */}
              <OptionToggle label="lite" checked={f.lite} onChange={on("lite")}
                            error={statLiteConflict ? "stat과 lite는 함께 쓸 수 없습니다" : null} />
              <OptionToggle label="quiet" checked={f.quiet} onChange={on("quiet")}
                            summary={HELP_SUMMARY.quietRm} full={QUIET_HELP.rm} />
            </div>
          </>
        )}
      </SubSection>
      {/* 실행 설정은 운영자 전용(2026-08-20, 사용자 결정): 사용자 폼에선 우선순위를 정책 기본에 맡긴다(생략 = resolve_priority
          가 정책값으로 해석). 노드 수·프로세스 수도 정책이 정한다 -- 정책 캡션·CPU 상한은 자세히(요약 한 줄은 늘 보인다). 캡션이
          거부 경고(정책 비활성·조회 실패·행 부재)면 펼친 채 고정한다(policyAlert). 실행 신원·root 실행은 ① 에 있다. */}
      {isAdmin && (
        <SubSection title="실행 설정" className="border-t border-line pt-5"
                    caption={<MoreText summary={HELP_SUMMARY.policy} full={[policyCaption, NODE_CPU_CAP_NOTE]}
                                       forceOpen={policyAlert} />}>
          <FieldRow
            control={<label className="text-sm block">우선순위
              <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
                <option value="">{priorityDefaultLabel}</option>
                <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
              </select>
            </label>} />
        </SubSection>
      )}
    </>
  );

  // ---- ④ 검토·제출 ------------------------------------------------------------------------------------------------------
  // sync 목적지 조건과 소유권 안내(2026-09-30 "상위 디렉토리 쓰기 권한 조건을 분명히", 2026-10-01 "목적지가 없는 경우" + "기본적으로
  // 목적지는 요청자 본인 uid:gid" 추가). 근거: preflight _DEST_TYPE_CHECK·_DEST_CHECK -- 목적지가 없으면 상위 디렉토리 쓰기만 보고
  // sync 가 목적지를 한 단계 만든다(상위가 없으면 test -w 가 실패 -- root 도 마찬가지), 목적지가 있으면 디렉토리여야 하고 쓰기·진입
  // (비 root 는 소유) + 상위 쓰기. 소유권은 lib/syncOwnership(_auto_chown 미러). 권한이 필요한 상위 디렉토리는 실제 절대경로로. 사용자가
  // "분명히" 보이게 해 달라고 한 안내라 접지 않는다 -- 단계별 등록부터 ④ 검토의 오른쪽 카드다(② 에는 요점 한 줄). 머리 옆 「수정」은
  // ② 대상으로(이름은 sr-only 내용 -- aria-label 이면 getByLabel 부분 일치가 겹친다), 끝에 delete 와 무관한 dsync 삭제 한 줄.
  const syncConditions = (onEdit: () => void) => (
    <InfoCard role="note" aria-label="목적지 조건과 소유권">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="font-medium">
          {`목적지 조건과 소유권 — ${isAdmin ? "실행 신원" : "요청자 본인 계정"}(uid/gid) 기준`}
        </p>
        <button type="button" onClick={onEdit}
                className="text-xs font-semibold text-accent hover:text-accenthover hover:underline hover:underline-offset-2">
          <span aria-hidden>수정</span><span className="sr-only">수정: 목적지 조건과 소유권</span>
        </button>
      </div>
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
                합니다(sync 가 최상위의 권한·시각을 소스에 맞추기 때문) — 그룹 쓰기 권한만으로는 부족합니다.
                상위 디렉토리 쓰기 권한도 마찬가지로 필요합니다.</>}
        </li>
        <li><strong>소유권</strong>: {ownership.long}</li>
        {/* 보조 그룹 인정(2026-10-07 D15 -- 조건부 문구): preflight·도구가 실행 uid + LDAP 주 gid + 계획
            시점에 얼린 LDAP 보조 gid(supplementalGroups·워커 /etc/group)로 돈다. "인정된다" 를 무조건으로
            말하지 않는다 -- 계획 시점 확정·gidNumber 그룹만·중첩 제외·스토리지 서버가 최종 판정(D10·D1). */}
        <li>
          {canRoot ? "root 가 아닌 실행에서 권한은" : "권한은"} {isAdmin ? "실행 신원" : "본인 계정"}의 uid·LDAP 주
          그룹·LDAP 보조 그룹(적용된 경우 — 운영자가 기능을 껐거나 그룹이 256개를 넘으면 주 그룹만) 기준으로
          판정됩니다. 보조 그룹은 신청이 계획될 때 한 번 확정되고(gidNumber 가 있는
          그룹만, 중첩 그룹 제외), 그 뒤 그룹에서 빠지면 실행 전에 중단되며 새로 들어간 그룹은 다시 신청해야
          반영됩니다. 실제 인정 여부는 스토리지 설정에 따라 다를 수 있습니다.
          {syncGroupCaveats.map((c) => <span key={c} className="block">{c}</span>)}
        </li>
        {/* 링크 경로 규칙(2026-10-27 d192, 괴리 [14]) -- 링크로 된 목적지가 위 「디렉토리여야 합니다」를 만족한다고
            보고 낸 요청이 preflight 에서 거부됐다(destination_path_redirected). */}
        <li>{SYNC_PATH_LINK_RULE}</li>
        <li>조건이 맞지 않으면 미리보기 전에 거부되고 사유가 표시됩니다 — 아무것도 복사되지 않습니다.</li>
        {canRoot && (
          <li>root 권한으로 실행하면 권한·소유 검사는 우회됩니다(① 작업 종류에서 선택) — 상위 디렉토리는
            그래도 이미 있어야 합니다.</li>
        )}
      </ul>
      <p className="mt-2 text-xs text-attn">{SYNC_DSYNC_SPECIAL_FILE_NOTE}</p>
    </InfoCard>
  );

  // 옛 오른쪽 요약의 행 전부(라벨 그대로)를 카드 셋(작업 · 소스 → 목적지/대상 · 옵션)으로 나누고, 「수정」은 그 단계로 간다(go -- 뒤로는
  // 늘 갈 수 있다). 오른쪽: sync 는 목적지 조건과 소유권 전문(접지 않음), rm 은 삭제 규칙 -- 경로는 왼쪽 「대상」 한 곳에만(같은 카드 안
  // getByText(경로) 다중 일치 방지), 「되돌릴 수 없음」은 하단 바 strip 한 곳이라 여기 되풀이하지 않는다.
  const reviewStep = () => (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:items-start">
      <div className="min-w-0 space-y-4">
        <ReviewCard title="작업" onEdit={() => wizard.go("kind")}>
          <SummaryList>
            <SummaryRow label="연산">
              <span className="font-semibold">{f.operation}</span>
              <span className="text-muted">{` — ${OPERATION_LABEL[f.operation]}`}</span>
            </SummaryRow>
            {isAdmin && ownerTrim !== "" && (
              <SummaryRow label="실행 신원">{ownerTrim}</SummaryRow>
            )}
            {isAdmin && (
              <SummaryRow label="실행 권한" className={rootEffective ? "text-bad" : ""}>
                {rootEffective ? PRIV_ROOT_SYNC : PRIV_USER}
              </SummaryRow>
            )}
          </SummaryList>
        </ReviewCard>
        <ReviewCard title={f.operation === "sync" ? "소스 → 목적지" : "대상"} onEdit={() => wizard.go("target")}>
          <SummaryList>
            {f.operation === "sync" ? (
              <>
                <SummaryRow label="소스">{loc(f.sourceStorage, f.sourcePath)}</SummaryRow>
                <SummaryRow label="목적지">{loc(f.destStorage, f.destPath)}</SummaryRow>
                <SummaryRow label="목적지 조건">{destCondition}</SummaryRow>
                <SummaryRow label="목적지 소유">{ownership.short}</SummaryRow>
              </>
            ) : (
              <SummaryRow label="대상">{loc(f.storage, f.target)}</SummaryRow>
            )}
          </SummaryList>
        </ReviewCard>
        <ReviewCard title={optionsTitle} onEdit={() => wizard.go("options")}>
          <SummaryList>
            <SummaryRow label="옵션"><OptionTokens options={currentOptions()} /></SummaryRow>
            <SummaryRow label="우선순위">{f.priority === "" ? priorityDefaultLabel : f.priority}</SummaryRow>
          </SummaryList>
        </ReviewCard>
      </div>
      <div className="min-w-0 space-y-4">
        {f.operation === "sync" && syncConditions(() => wizard.go("target"))}
        {f.operation === "rm" && (
          <ReviewCard title="삭제 규칙" tone="bad" onEdit={() => wizard.go("target")}>
            <p className="text-xs">{RM_PATH_LINK_RULE}</p>
          </ReviewCard>
        )}
      </div>
    </div>
  );

  // 단계 카드 머리 한 줄.
  const STEP_LEDE: Record<SubmitStep, string> = {
    kind: isAdmin ? "무엇을 할지와 누구의 권한으로 실행할지 정하세요. 다음 단계의 입력이 연산에 맞춰 바뀝니다."
      : "무엇을 할지 고르세요. 다음 단계의 입력이 연산에 맞춰 바뀝니다.",
    target: f.operation === "sync" ? "어디에서 어디로 복사할지 고르세요."
      : f.operation === "scan" ? "통계를 수집할 경로를 고르세요." : "삭제할 경로를 고르세요.",
    options: "기본값 그대로도 충분합니다. 켠 옵션은 자세한 안내가 저절로 펼쳐집니다.",
    review: "제출 전에 한 화면에서 확인하세요. 「수정」을 누르면 그 단계로 돌아갑니다.",
  };

  // 대상 단계의 지름길(dms-k8s design-wizard §1-6 「바로가기」): 옵션 단계를 거치기 전엔 「옵션 기본값으로 검토」, 거친 뒤엔 「검토로
  // 건너뛰기」. 대상 게이트를 통과했을 때만 보이고, 검토로 갈 수 없으면(옵션이 막힘) 잠긴다(aria-disabled -- 눌러도 동작 없음, 이유는
  // title). 좁은 폭(<sm)에서만 숨긴다(b1eddc3c -- 768 의 상태 줄 꺾임은 바 쪽 flex-wrap 이 푼다). 검토를 이미 봤고 갈 수 있으면 바가
  // 「검토로 돌아가기」를 대신 그린다.
  const reviewReachable = wizard.reachable("review");
  const skipToReview = wizard.current === "target" && !gates.gate.target && (
    <button type="button" aria-disabled={!reviewReachable || undefined}
            title={reviewReachable ? undefined : (gates.barReason("options") ?? undefined)}
            onClick={() => { if (reviewReachable) wizard.go("review"); }}
            className="inline-flex h-10 items-center gap-0.5 whitespace-nowrap rounded-lg px-2 text-sm font-medium text-ink/80
                       hover:text-ink aria-disabled:cursor-not-allowed aria-disabled:opacity-50 max-sm:hidden">
      {wizard.visited.has("options") ? "검토로 건너뛰기" : "옵션 기본값으로 검토"}
      <ChevronRight aria-hidden className="h-4 w-4" />
    </button>
  );

  return (
    <section className="space-y-4">
      {/* 개명(사용자 결정 2026-08-18): 「작업 제출」→「단일 작업」 — 배치 작업과
          같은 성격의 단일 항목 제출임을 이름이 말한다. */}
      <div>
        <h1 className="text-2xl font-bold">단일 작업</h1>
        {me.data !== undefined && (
          <p className="mt-1 text-sm text-muted">
            {isAdmin ? "경로 하나에 sync·scan·rm 을 실행합니다 — 여러 경로의 sync·scan·rm 은 배치 작업으로 묶어 요청하세요."
              : "스토리지 사이에서 디렉토리 하나를 sync 합니다."}
          </p>
        )}
      </div>
      {/* form 은 남긴다(dms-k8s design-wizard §1-3): 걷어 내면 암묵 제출 부류가 사라지는 대신 「강제 submit 도 막힌다」 시험이 관찰할
          입구도 사라져 handleSubmit 가드가 죽은 코드가 된다. 대신 모든 버튼 type="button" + 셸이 처리하는 모든 Enter 에 preventDefault
          (Enter 는 다음 단계일 뿐 절대 제출하지 않는다 -- 관리자 기본 root·rm 을 검토도 안 보고 내는 회귀 방지) + handleSubmit 의 단계·
          blocked 가드로 막는다. */}
      <form onSubmit={handleSubmit}>
        <WizardShell {...wizard.shellProps}>
          <WizardStepper wizard={wizard} />
          <WizardStepCard wizard={wizard} lede={STEP_LEDE[wizard.current]}
                          chips={wizard.current === "target" && f.operation === "rm" && (
                            <span className="whitespace-nowrap rounded-full bg-badbg px-2.5 py-0.5 text-xs font-semibold text-bad">
                              삭제 — 되돌릴 수 없음
                            </span>
                          )}>
            {wizard.current === "kind" ? kindStep()
              : wizard.current === "target" ? targetStep()
              : wizard.current === "options" ? optionsStep()
              : reviewStep()}
          </WizardStepCard>
          {/* rm 경고는 한 곳 -- 검토 단계 하단 바, 제출 버튼 바로 위(「누르기 직전」). 위험=빨강 의미 체계 유지. */}
          <WizardActionBar
            wizard={wizard}
            doneHint="제출하면 요청 상세로 이동합니다."
            onCancel={() => nav("/jobs")}
            secondary={skipToReview}
            submit={{ label: "제출", onClick: handleSubmitClick, disabled: blocked,
                      // rm 검토의 제출은 빨간 솔리드(주 동작 자체가 파괴적일 때만 destructive)
                      variant: f.operation === "rm" ? "destructive" : "primary",
                      describedBy: hints.length > 0 ? "submit-job-hints" : undefined }}
            strip={wizard.current === "review" && f.operation === "rm"
              ? "삭제는 되돌릴 수 없습니다. 미리보기에서 대상을 확인한 뒤 확인해야 실행됩니다." : undefined}
            details={wizard.current === "review" && (hints.length > 0 || submit.isError) ? (<>
              <SubmitHints id="submit-job-hints" items={hints} />
              {submit.isError && <p className="text-bad text-sm">{(submit.error as ApiError).message}</p>}
            </>) : undefined} />
        </WizardShell>
      </form>
    </section>
  );
}
