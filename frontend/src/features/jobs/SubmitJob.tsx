import { useEffect, useRef, useState } from "react";
import type { MouseEvent, SyntheticEvent } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { ArrowRight, ChevronRight } from "lucide-react";
import { useSubmitRequest } from "./useJobs";
import type { SubmitBody } from "./useJobs";
import { useUserStorages } from "../storages/useUserStorages";
import { useMe } from "../auth/useAuth";
import { Button } from "../../components/ui/Button";
import { InfoCard } from "../../components/ui/InfoCard";
// 2026-11-26 단계별 등록: 한 장 시트 + 오른쪽 요약(SubmitLayout·SummaryPanel·FormSection)을 걷고 위저드 골격(components/form/Wizard)
// 위에 단계 넷을 그린다. 남은 표시 부품(FieldRow·SummaryList·SummaryRow·OptionTokens·SubmitHints)은 그대로 쓴다.
import { FieldRow, OptionTokens, SubmitHints, SummaryList, SummaryRow } from "../../components/form/SubmitLayout";
import { useWizard, WizardActionBar, WizardShell, WizardStepCard, WizardStepper } from "../../components/form/Wizard";
import type { WizardStep } from "../../components/form/Wizard";
import { SubSection } from "../../components/form/wizardParts";
import { submitGates } from "./submitGates";
import type { SubmitStep } from "./submitGates";
import { ApiError, REASON_MESSAGES } from "../../lib/api";
// field·StoragePicker 는 formFields.tsx 에 산다(슬라이스 31 T3) -- 제출 화면이 통째로 갈려도
// 다른 임포터가 흔들리지 않게 결합을 끊어 둔 것.
import { StoragePicker, field } from "./formFields";
import {
  absolutePath, destinationParent, relativePathProblem, RM_PATH_LINK_RULE, SYNC_PATH_LINK_RULE,
} from "../../lib/storagePaths";
import { ownerText, syncOwnership, validOwner } from "../../lib/syncOwnership";
import type { DirectDestOwner, PvOwnerRule } from "../../lib/syncOwnership";
// 옵션 미러(CHMOD_RE·chownFieldError·intFieldError, sync 숫자 범위·프리필 SYNC_INT_FIELDS)는
// optionRules.ts 로 이사(슬라이스 32 T8) -- BatchCreate 옵션과 공유한다
// (사본이면 미러가 발산한다).
import { BUFSIZE_HELP, CHMOD_RE, chownFieldError, DIRECT_HELP, directBufsizeError, OPEN_NOATIME_HELP, PRIV_ROOT_HEAD, PRIV_ROOT_SYNC,
         PRIV_ROOT_SYNC_DIRECT_BOTH, PRIV_ROOT_SYNC_PV, PRIV_ROOT_SYNC_PV_ROOT, QUIET_HELP, SCAN_INT_FIELDS, SYNC_INT_FIELDS,
         SYNC_MAX_ERRORS_NOTE, SYNC_OPTION_HELP, priorityCapText, priorityClampNote, scanIntFieldError, syncIntFieldError }
  from "./optionRules";
// 정책 기본값 캡션(슬라이스 37: 배치 생성과 같은 표시 배선 — 백엔드 무변경).
import { usePolicies } from "../policies/usePolicies";
import type { Policy } from "../../lib/types";
import { NODE_CPU_CAP_NOTE } from "../../lib/jobTool";
// dms-k8s(설계 §7, 2026-10-25): PV 주소 고르기. 2026-10-26 LDAP 정리로 이 라인은 k8s 신원 모드 전용이다 -- 옛 ldap 모드
// 화면(스토리지 직접 고르기·실행 신원·root 체크박스·sync 허용 쌍 필터)은 지웠다.
import { CatalogNotice, LocationField } from "../k8s/LocationField";
import type { DestOwner, PvSearchField } from "../k8s/LocationField";
import { Segmented } from "../../components/ui/Segmented";
import { usePvCatalog, useResolvedPv } from "../k8s/usePvCatalog";
import type { Resolved } from "../k8s/usePvCatalog";
import {
  accountIdProblem, SIDE_LABELS, emptyLoc, normalizeSub, parsePvParam, pvAbsPath, pvDestParent, pvSubProblem, pvSummaryText,
} from "../k8s/pvLocation";
import type { Loc, Side } from "../k8s/pvLocation";
import { storageKindOf, storageOwnerOf } from "../k8s/k8sTerms";
import { useK8sAccess } from "../k8s/useK8s";
import { useSearchField } from "./searchField";

// 슬라이스 37: 단일 작업(구 「작업 제출」) — 배치 작업과 같은 성격의 단일 항목
// 제출이다. 운영자는 scan 까지 세 연산 전부(서버 게이트 미러: scan 제출은 admin
// 전용 403), 비관리자는 sync 만.
type Operation = "sync" | "scan" | "rm";

const initial = {
  operation: "sync" as Operation,
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
  // 초기값 ON = **root 실행의 기본**(사용자 결정 2026-08-22): O_NOATIME 은 root(또는 파일 소유자)만 쓸 수 있다 --
  // 모든 작업이 root 라(k8s 신원 모드, 설계 D2) 늘 실린다. 사용자 폼은 옵션이 숨겨져 기본값(ON) 그대로다.
  openNoatime: true,
  batchFiles: SYNC_INT_FIELDS.batch_files.prefill,
  bufsize: SYNC_INT_FIELDS.bufsize.prefill,
  chmod: "", chown: "",
  // "" = (정책 기본) = 바디에서 생략 — resolve_priority 가 정책 default_priority 로
  // 해석한다(BatchCreate 와 같은 계약, null≠0).
  priority: "",
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
// 미러), 아니면 입력한 상대경로를 관리 디렉토리에 붙인 절대경로(관리 디렉토리를 알 때만 -- 모르면 지어내지
// 않는다). 절대경로는 **보내는 값 그대로**로 짓는다 -- 다듬은 값으로 지으면 "/a" 나 "x " 같은 입력에
// 서버가 실제로 쓰지 않을(또는 거부할) 경로를 그럴듯하게 보여 준다(리뷰 2026-10-06).
// 라벨은 「지정 경로」(d192 이식, 괴리 [C2]): 글자 그대로 붙인 경로라 링크를 풀지 않는다 -- 「실제 경로」라 부르면 링크를
// 거치는 경로로 거부된 뒤 사용자가 화면이 「실제」라 한 바로 그 경로를 다시 낸다(lib/storagePaths absolutePath 주석). PV 쪽
// (LocationField)도 서버가 준 PV 루트에 하위경로를 글자 그대로 붙이므로 같은 라벨이다.
function PathFeedback({ root, rel }: { root: string | null | undefined; rel: string }) {
  if (rel.trim() === "") return null;
  const problem = relativePathProblem(rel);
  if (problem !== null) return <p className="mt-1 text-bad text-sm">{problem}</p>;
  const abs = absolutePath(root, rel);
  return (
    <>
      {abs !== null && (
        <p className="mt-1 text-xs text-muted [overflow-wrap:anywhere]">{`지정 경로: ${abs}`}</p>
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

const ALL_OPERATIONS: Operation[] = ["sync", "scan", "rm"];

export function SubmitJob() {
  const nav = useNavigate();
  const submit = useSubmitRequest();
  const storagesQ = useUserStorages();
  const me = useMe();
  // 폼 값은 단일 useState -- 연산을 바꿔도 필드를 초기화하지 않는다(현행 정책). 숨은 연산의 값은
  // 남지만 아래 검증·바디가 f.operation 으로 걸러 싣지 않는다.
  const [f, setFState] = useState(initial);
  // 지난 제출의 거부 문구(요약의 submit.isError)는 그때 입력에 대한 답이다 -- 폼을 고치면 지운다(2026-11-11: k8s_access_denied 로
  // 거부된 뒤 다른 PV 로 바꿔도 「이 PV 의 namespace 에 권한이 없습니다」가 남아 새로 고른 PV 이야기처럼 읽혔다. K8sOverview·Login 의
  // 입력 변경 reset 과 같은 관례). 오류일 때만 -- 진행 중(pending)에 reset 하면 성공 뒤 이동(onSuccess)이 끊긴다.
  const clearSubmitError = () => { if (submit.isError) submit.reset(); };
  const setF = (next: typeof initial) => { clearSubmitError(); setFState(next); };
  // 고급 옵션 펼침 상태. 오류가 있으면 항상 펼친다(아래 details open/onToggle).
  const [advancedOpen, setAdvancedOpen] = useState(false);
  // PV 목적지 sync 의 결과 소유(2026-10-26 사용자 결정 「사용자는 항상 자동으로 셋업, 운영자는 옵션으로 PV 루트 소유자」): 기본은
  // 목적지 스토리지 기본 소유자. 「PV 루트 소유자」는 특권 운영자(면제자)만 고른다(서버 pv_owner_root_not_allowed). 연산·목적지를
  // 바꿔도 남지만(f 와 같은 정책) 바디에는 쓰일 때(아래 pvRootChosen)만 실린다.
  const [pvOwnerChoice, setPvOwnerChoiceState] = useState<PvOwnerRule>("storage");
  const setPvOwnerChoice = (v: PvOwnerRule) => { clearSubmitError(); setPvOwnerChoiceState(v); };

  // ---- k8s 신원 모드(dms-k8s 설계 D1·D2·D4·D15·§7) ----------------------------------------------------------
  // 분기 키는 me.unrestricted(맵핑 면제 = 관리자 + 전체 PV·직접 경로 권한 + 세션) 하나다. 모든 작업이 root 이고(서버 D2 -- 바디
  // run_as_root:true), 대상은 쪽마다 PV 주소(namespace (StorageClass) → PV 이름 → PV 안 경로)다.
  // 면제자만 쪽마다 「PV | 직접 경로」와 「권한 기준 사용자」를 쓴다. 쪽 위치는 연산을 바꿔도 남는다(f 와 같은 정책).
  // ?pv=<cluster>/<ns>/<pvc>(관리자 PV 탐색 「이 PV로 단일 작업」)는 소스와 대상(scan·rm) 둘 다에 미리 고른다.
  const [params] = useSearchParams();
  const [locs, setLocs] = useState<Record<Side, Loc>>(() => {
    const ref = parsePvParam(params.get("pv"));
    return { source: emptyLoc(ref), destination: emptyLoc(), target: emptyLoc(ref) };
  });
  const setLoc = (side: Side) => (next: Loc) => { clearSubmitError(); setLocs((prev) => ({ ...prev, [side]: next })); };
  const unrestricted = me.data?.unrestricted === true;
  // 「권한 기준 사용자」(면제자): 비우면 전체 PV 검색, 채우면 그 사용자의 범위(같은 고르기 흐름). 목록 조회는 디바운스된 값으로,
  // 바디·요약은 보이는 값 그대로(보이는 것 = 보내는 것).
  const scopeField = useSearchField("");
  // 면제자 PV 검색칸(쪽마다 하나 -- 훅 개수 고정). 단계를 옮기면 대상 단계 본문(LocationField·SearchPicker)이 언마운트되는데, 검색어를
  // 그 지역 상태에 두면 ②→③→② 뒤 검색칸이 비고 결과 목록에서 고른 줄이 빠졌다(고른 PV 는 locs 라 남는다) -- 값은 모두 화면
  // 최상위에 둔다(design-wizard §1-2).
  const searchFields: Record<Side, PvSearchField> = {
    source: useSearchField(""), destination: useSearchField(""), target: useSearchField(""),
  };
  const scopeInput = unrestricted ? scopeField.bind.value.trim() : "";
  // 거부 이유를 셋으로 가른다(예약어·숫자만·형식 -- 문구가 형식 하나면 「nobody」「1234」처럼 안내 형식에 맞는 입력에 무엇을 고칠지
  // 말하지 못했다, 2026-11-11).
  const scopeProblem = scopeInput === "" ? null : accountIdProblem(scopeInput);
  const scopeInvalid = scopeProblem !== null;
  const scopeUser = unrestricted && !scopeInvalid ? scopeField.applied : "";
  const accessQ = useK8sAccess("", unrestricted);
  const scopeChoices = (accessQ.data?.users ?? []).map((u) => u.dms_user);
  const { catalog, search } = usePvCatalog({ enabled: me.data !== undefined, unrestricted, scopeUser });
  // 쪽의 지정 방식: 면제자만 직접 경로를 고를 수 있다(비면제 = PV 주소만 -- 서버 raw_path_not_allowed).
  const modeOf = (side: Side) => (unrestricted ? locs[side].mode : "pv");
  const sideActive = (side: Side) => modeOf(side) === "pv"
    && (side === "target" ? f.operation !== "sync" : f.operation === "sync");
  const resolved: Record<Side, Resolved> = {
    source: useResolvedPv({ active: sideActive("source"), ref: locs.source.ref, catalog, search }),
    destination: useResolvedPv({ active: sideActive("destination"), ref: locs.destination.ref, catalog, search }),
    target: useResolvedPv({ active: sideActive("target"), ref: locs.target.ref, catalog, search }),
  };
  const rowOf = (side: Side) => {
    const r = resolved[side];
    return r.status === "found" ? r.row : null;
  };

  const storages = storagesQ.data ?? [];
  const loadingStorages = storagesQ.isLoading;
  const isAdmin = me.data?.role === "admin";
  // 특권 운영자(me.can_run_as_root = 관리자 + 특권 목록/계정 부여 + 세션 -- 서버 판정, 표시일 뿐 서버가 다시 본다).
  const canRoot = isAdmin && me.data?.can_run_as_root === true;
  // chown·chmod 지정은 특권 운영자만(2026-10-26 -- 결과 소유는 목적지 스토리지 기본 소유자로 자동, 서버 chown_not_allowed·
  // chmod_not_allowed).
  const ownerModeEditable = canRoot;
  // 연산 선택지: 서버가 준 me.user_operations(설계 D15 -- scan 은 특권 운영자만, 2026-10-26 (g)). 필드가 없는 응답(옛 서버)은
  // 관리자 셋 다·사용자 sync 만.
  const userOps = me.data?.user_operations;
  const operations: Operation[] = Array.isArray(userOps)
    ? ALL_OPERATIONS.filter((o) => o === "sync" || userOps.includes(o))
    : isAdmin ? ALL_OPERATIONS : ["sync"];
  const rootOf = (name: string) => storages.find((s) => s.storage_name === name)?.managed_root;
  // 직접 경로 선택지(면제자): 파일시스템·둘 다 종류만(PV 전용 스토리지는 PV 주소로 -- 설계 D11).
  const rawStorages = storages.filter((s) => storageKindOf(s) !== "pv");
  // sync 목적지의 상위 디렉토리: PV 목적지면 서버가 해석한 PV 실제 경로 기준(하위경로가 비면 PV 루트의 부모), 직접 경로면
  // 관리 디렉토리 기준(모르면 null -- 지어내지 않는다). 서버가 거부할 경로면 null(pvDestParent 가 pvSubProblem 을, 직접 경로는
  // relativePathProblem 을 본다 -- 입력 아래엔 오류가 뜨는데 카드·요약은 「<루트>/.. 가 이미 있어야 합니다」라고 했다, 2026-11-11).
  // 직접 경로는 **보내는 값 그대로**로 짓는다(PathFeedback 주석 -- 다듬은 값이면 「 a/b」가 「<root>/a」로 보였다).
  const destParentAbs = modeOf("destination") === "pv" ? pvDestParent(rowOf("destination"), locs.destination.sub)
    : locs.destination.path.trim() === "" || relativePathProblem(locs.destination.path) !== null ? null
    : destinationParent(rootOf(locs.destination.storage), locs.destination.path);
  // 결과(목적지) 소유권 안내 -- 바디와 같은 값(chown 옵션·목적지 종류·결과 소유 선택)에서 파생한다(lib/syncOwnership = 서버
  // _auto_chown 미러). PV 목적지 sync 면 결과는 목적지 스토리지 기본 소유자(서버가 해석한 VolumeRow.storage_owner -- 사용자는 늘
  // 이것), 운영자가 「PV 루트 소유자」를 고르면 점검이 잰 PV 루트 소유자(2026-10-26 사용자 결정) -- 소유권 문구·「목적지가 이미
  // 있는 경우」·요약 「실행 권한」·목적지 PV 루트 경고가 이 한 규칙을 쓴다. 모든 작업이 root 라 root 갈래만 쓴다(sync 허용 쌍 필터도
  // 없다 -- 설계 D14: 양쪽이 PV 주소인 sync 는 PV 권한으로 판정, 직접 경로는 면제자만).
  const pvDest = f.operation === "sync" && modeOf("destination") === "pv";
  // 바디에 chown 이 실리나(syncOptions 와 같은 조건) -- 실리면 결과 소유 선택은 쓰이지 않는다(명시 chown 이 이긴다).
  const chownSet = ownerModeEditable && f.chown.trim() !== "";
  // 「PV 루트 소유자」 선택이 실제로 쓰이나: 면제자 + PV 목적지 sync + chown 없음. 이때만 바디에 pv_owner:"pv_root" 를 싣는다 --
  // chown 과 함께 실으면 서버가 422 pv_owner_not_applicable 로 거부한다.
  const pvRootChosen = unrestricted && pvDest && !chownSet && pvOwnerChoice === "pv_root";
  const pvOwnerRule: PvOwnerRule = pvRootChosen ? "pv_root" : "storage";
  const destRow = pvDest ? rowOf("destination") : null;
  // 서버가 해석한 목적지 스토리지 기본 소유자. undefined = 옛 응답(모름), null·형식 오류 = 저장값이 올바르지 않음(서버 409
  // invalid_storage_owner -- 아래 storageOwnerBad 로 보내기 전에 잠근다).
  const storageOwnerRaw = destRow?.storage_owner;
  const storageOwner = validOwner(storageOwnerRaw);
  const storageOwnerLabel = storageOwner !== null ? ownerText(storageOwner) : null;
  // 직접 경로 목적지(면제자)의 결과 소유(2026-10-26 사용자 결정 (4) -- 서버 k8s_access.direct_dest_owner 미러): 고른 스토리지가 pv
  // 종류면 늘 그 스토리지 기본 소유자, both 면 경로가 그 스토리지의 PV 루트 아래일 때만(화면은 인벤토리의 PV 루트를 모른다 -- 조건을
  // 그대로 말하고, 제출 때 서버가 정한 값은 요청 상세에 보인다), filesystem 은 종전(소스 소유 보존). chown 을 지정하면 그 값이 이긴다.
  const rawDestRow = f.operation === "sync" && modeOf("destination") === "path"
    ? storages.find((s) => s.storage_name === locs.destination.storage) : undefined;
  const rawDestKind = rawDestRow !== undefined ? storageKindOf(rawDestRow) : null;
  const rawDestOwnerRaw = rawDestRow !== undefined ? storageOwnerOf(rawDestRow) : undefined;
  const rawDestOwner = rawDestOwnerRaw ?? null;
  const directDest: DirectDestOwner | null = chownSet || rawDestKind === null || rawDestKind === "filesystem" ? null
    : rawDestKind === "pv" ? { rule: "storage", owner: rawDestOwner }
    : { rule: "storage_if_under_pv", owner: rawDestOwner };
  const directOwnerLabel = rawDestOwner !== null ? ownerText(rawDestOwner) : null;
  // pv 종류 스토리지의 저장값이 무효면 서버가 409 invalid_storage_owner -- 보내기 전에 잠근다. both 는 경로가 PV 루트 아래일 때만
  // 거부되므로(화면은 모른다) 잠그지 않는다. 생략(undefined) = 옛 응답(모름) -- 잠그지 않는다. chown 을 지정하면(chownSet) 스토리지
  // 값은 쓰이지 않아 서버도 규칙을 기록·대조하지 않는다(routes_requests 「"chown" not in options」 -- 2026-11-11: PV 쪽만 chownSet 을
  // 안 봐, 저장값이 무효일 때 운영자의 자연스러운 우회로인 chown 명시가 화면에서 막혔다. 직접 경로 쪽 directDest 는 이미 null).
  const storageOwnerBad = (pvOwnerRule === "storage" && !chownSet && destRow !== null && destRow.selectable
    && storageOwnerRaw !== undefined && storageOwner === null)
    || (directDest?.rule === "storage" && rawDestOwnerRaw === null);
  const ownership = syncOwnership({
    chown: f.chown, chmod: f.chmod, root: true, runAs: null, self: true, chownEditable: ownerModeEditable,
    pvDestination: pvDest, pvOwnerRule, storageOwner, directDest });
  // 목적지 PV 루트 경고(LocationField)가 읽는 결과 소유 -- chown 지정 > 운영자 「PV 루트 소유자」 > 스토리지 기본 소유자.
  const destOwner: DestOwner = chownSet ? { rule: "chown", value: f.chown.trim() } : { rule: pvOwnerRule };

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
  // 대상 sanity(슬라이스 39, 사용자 결정): 고르지 않았거나 경로가 공백이면 제출할 수 없다(trim() 로 공백만 있는 입력도 미입력).
  // 쪽 판정(설계 §6 미러 -- 최종 판정은 서버): PV 쪽은 PV 를 골랐나·고를 수 있는 PV 인가·하위경로가 서버 규칙에 맞나(rm 은 PV
  // 루트 금지 -- '', '.', './'), 직접 경로 쪽은 스토리지 + 관리 디렉토리 아래 상대경로(relativePathProblem).
  const activeSides: Side[] = f.operation === "sync" ? ["source", "destination"] : ["target"];
  const sideCheck = (side: Side) => {
    const l = locs[side];
    if (modeOf(side) === "path") {
      return { raw: true, missing: l.storage === "" || l.path.trim() === "", invalid: relativePathProblem(l.path) !== null,
               pvBad: false, pvLoading: false, subBad: false, rmRoot: false };
    }
    const r = resolved[side];
    const rmRoot = f.operation === "rm" && normalizeSub(l.sub) === "";
    return { raw: false, missing: l.ref === null, invalid: false,
             // 고른 PV 를 아직 확인하지 못했으면(목록·검색 응답 전) 잠그기만 하고 「쓸 수 없다」고 말하지 않는다.
             pvLoading: l.ref !== null && r.status === "loading",
             pvBad: l.ref !== null && (r.status === "missing" || r.status === "error"
                                       || (r.status === "found" && !r.row.selectable)),
             subBad: !rmRoot && pvSubProblem(l.sub, f.operation) !== null, rmRoot: l.ref !== null && rmRoot };
  };
  const checks = activeSides.map(sideCheck);
  const anyRaw = checks.some((c) => c.raw);
  const k8sSubBad = checks.some((c) => c.subBad);
  const k8sRmRoot = checks.some((c) => c.rmRoot);
  // PV 목록은 PV 쪽에만 쓰인다 -- 그 조회 실패·미동기화는 PV 를 고르는 쪽이 있을 때만 잠그고 말한다(아래 storagesBlock 의 거울,
  // 2026-11-11: 면제자가 권한 기준 사용자를 채우고 양쪽을 직접 경로로 두면 범위 목록 5xx 하나로 PV 를 안 쓰는 제출이 잠겼다).
  const anyPv = checks.some((c) => !c.raw);
  const k8sCatalogError = catalog.status === "error" && anyPv;
  const targetMissing = checks.some((c) => c.missing);
  // 서버가 422 unsafe_path 로 거부할 경로(relativePathProblem 미러) -- 보내기 전에 잠근다.
  const pathInvalid = checks.some((c) => c.invalid);
  const optionsInvalid = recursiveMissing || statLiteConflict || verboseQuietConflict
    || advancedError !== null || scanBatchFilesError !== null || brokenLimitError !== null;
  // 스토리지 목록은 직접 경로 쪽에만 쓰인다 -- 그 조회 실패는 직접 경로를 고른 동안만 잠근다.
  const storagesBlock = storagesQ.isError && anyRaw;
  // 제출 게이트·이유 목록·단계 게이트는 순수 함수 하나가 같은 잎에서 만든다(submitGates -- 식·문장·순서는 옛 한 장 시트의 blocked·
  // hints 그대로이고 단계 태그만 붙인다; 합집합 불변식은 잎 조합 전수 시험). 잎 계산은 위 그대로 이 화면에 있다.
  const gates = submitGates({
    isPending: submit.isPending, storagesBlock, scopeProblem, k8sCatalogError,
    catalogUnsynced: catalog.status === "ready" && catalog.clusters.every((c) => c.synced_at === null) && anyPv,
    catalogNoNs: catalog.status === "ready" && catalog.namespaces !== null && catalog.namespaces.length === 0 && anyPv,
    targetMissing, op: f.operation, anyRaw,
    pvBad: checks.some((c) => c.pvBad), pvLoading: checks.some((c) => c.pvLoading),
    k8sRmRoot, k8sSubBad, storageOwnerBad, ownerModeEditable,
    // 운영자 바 문장의 「② 결과 소유를 PV 루트 소유자로」 갈래는 그 선택이 실제로 보이는 때만(결과 소유 Segmented = 면제자 + PV 목적지).
    pvDest: pvDest && unrestricted,
    pathInvalid, optionsInvalid,
  });
  const blocked = gates.blocked;
  // 제출이 잠긴 이유(검토 단계 바, 제출 버튼 위). 필드 옆 빨간 문구와 **다른 문장**으로 짧게 -- 같은 문장을 두 번 그리지 않는다
  // (SubmitHints 주석). 대상 미입력 안내만은 원문이 여기 한 곳에 산다. 각 단계 바에는 그 단계 태그의 첫 줄(gates.barReason).
  const hints = gates.hints;
  // storageOwnerBad 를 운영자가 옵션 단계에서 풀 때(chown 이 우회로 -- submitGates 머리 주석) 고급 옵션을 강제로 펼친다: 바 이유가
  // 「고급 옵션의 chown 에 …」라 그 칸이 보여야 안내가 참이다(advancedError 와 같은 경로).
  const ownerFixOpen = storageOwnerBad && ownerModeEditable;
  // 강제로 펼친 채 chown 을 적으면 ownerFixOpen 이 풀려 패널이 입력 도중 접혔다 -- 펼친 적이 있으면 사용자가 연 것으로 남긴다.
  useEffect(() => { if (ownerFixOpen) setAdvancedOpen(true); }, [ownerFixOpen]);

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
  // 정책 상한(placement._clamp_priority 미러, 배치 생성과 같은 계산 -- optionRules.priorityCapText): 상한을 넘는 선택은 배치 때
  // 조용히 상한으로 깎인다 -- 칸 아래 캡션과 요약 「우선순위」가 미리 말한다(2026-11-11: 요약은 high 인데 실제로는 mid 로 돌았다).
  const priorityCapCaption = priorityCapText(byTool, f.operation, f.priority);
  const priorityClamp = priorityClampNote(byTool, f.operation, f.priority);

  const on = (k: keyof typeof initial) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  function syncOptions(): SubmitBody["options"] {
    const options: SubmitBody["options"] = checkedOptions({
      // quiet 는 sync·scan·rm 이 함께 쓰는 상태라, 사용자가 rm 에서 켜고 sync 로 돌아오면 sync 화면엔 그 칸이 없는데(관리자 전용)
      // 바디에 실렸다(2026-11-11 -- 끌 수도 없이 결과 수·실패 항목이 사라졌다). 칸이 보이는 관리자만 싣는다(「숨겨도 payload 동일」).
      delete: f.delete, contents: f.contents, direct: f.direct, quiet: isAdmin && f.quiet,
      // open_noatime: 모든 작업이 root 라(설계 D2 -- O_NOATIME 의 EPERM 제약 없음) 사용자도 같은 기본(ON)이 실린다. 사용자
      // 폼엔 고급 옵션이 없어 기본값 그대로다. nsync 에서도 실제로 동작하는 것은 잡 이미지 7a3189c 부터(OPEN_NOATIME_HELP 주석).
      open_noatime: f.openNoatime,
    });
    // 빈 문자열일 때만 생략 — 빈 chmod/chown 을 그대로 실으면 서버 fullmatch 가
    // 422 invalid_option 으로 거부한다(빈 값은 "옵션 없음"이지 "빈 값 지정"이 아니다).
    if (f.batchFiles.trim() !== "") options.batch_files = Number(f.batchFiles.trim());
    if (f.bufsize.trim() !== "") options.bufsize = Number(f.bufsize.trim());
    if (ownerModeEditable && f.chmod.trim() !== "") options.chmod = f.chmod.trim();
    if (ownerModeEditable && f.chown.trim() !== "") options.chown = f.chown.trim();
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

  // 제출 바디(설계 §6): 쪽마다 PV 주소({cluster, namespace, pvc} + PV 안 하위경로) 또는 직접 경로(storage + 상대경로).
  // 하위경로는 입력 그대로 싣는다(정규화·경로 해석·권한 재판정은 서버). run_as_root 는 늘 명시 true(D2 -- 생략·false 는 422).
  // owner_username = 면제자의 「권한 기준 사용자」(요청 상세 「대상 사용자」 -- 실행 권한은 그래도 root).
  function k8sBody(): SubmitBody {
    const put = (side: Side, pvKey: "pv" | "source_pv" | "destination_pv",
                 storageKey: "storage" | "source_storage" | "destination_storage",
                 pathKey: "target" | "source" | "destination", body: SubmitBody) => {
      const l = locs[side];
      if (modeOf(side) === "pv" && l.ref !== null) {
        body[pvKey] = { cluster: l.ref.cluster, namespace: l.ref.namespace, pvc: l.ref.pvc };
        body[pathKey] = l.sub;
      } else {
        body[storageKey] = l.storage;
        body[pathKey] = l.path;
      }
    };
    const body: SubmitBody = { operation: f.operation, options: currentOptions() };
    if (f.operation === "sync") {
      put("source", "source_pv", "source_storage", "source", body);
      put("destination", "destination_pv", "destination_storage", "destination", body);
    } else {
      put("target", "pv", "storage", "target", body);
    }
    if (f.priority !== "") body.priority = f.priority;
    if (unrestricted && scopeInput !== "") body.owner_username = scopeInput;
    // 결과 소유: 운영자가 「PV 루트 소유자」를 고른 때만(pvRootChosen) -- 기본 바디는 이 기능 전과 같다(서버가 storage 규칙을 박는다).
    if (pvRootChosen) body.pv_owner = "pv_root";
    body.run_as_root = true;
    return body;
  }

  // 요약 한 쪽: PV = 「PV 이름 — namespace (sc) / 하위경로」(요청 상세·목록과 같은 문법), 직접 경로 = storage:path.
  const sideText = (side: Side) => {
    const l = locs[side];
    if (modeOf(side) === "path") return loc(l.storage, l.path);
    return l.ref === null ? "—" : pvSummaryText(l.ref, rowOf(side), l.sub);
  };
  const sideAbs = (side: Side) => {
    const l = locs[side];
    if (modeOf(side) === "path") return null;
    return pvSubProblem(l.sub, f.operation) !== null ? null : pvAbsPath(rowOf(side), l.sub);
  };

  const targetTitle = f.operation === "sync" ? "소스와 목적지"
    : f.operation === "scan" ? "스캔 대상" : "삭제 대상";

  // 한 쪽 입력. 직접 경로(면제자)는 StoragePicker + 상대경로 + PathFeedback 이고 라벨은 「소스 스토리지」·「목적지 경로」·
  // 「스토리지」·「대상 경로」(e2e 접점) -- 선택지는 파일시스템·둘 다 종류로 거른다.
  const locationField = (side: Side) => {
    const L = SIDE_LABELS[side];
    const l = locs[side];
    const raw = (
      <>
        <StoragePicker label={L.storage} value={l.storage} onChange={(v) => setLoc(side)({ ...l, storage: v })}
                       storages={rawStorages} loading={loadingStorages} />
        <div>
          <label className="text-sm block">{L.path}
            <input aria-label={L.path} className={field} value={l.path}
                   aria-invalid={(l.path.trim() !== "" && relativePathProblem(l.path) !== null) || undefined}
                   placeholder={side === "destination" ? "예: backup/dataset" : side === "source" ? "예: team/dataset"
                     : "예: team/projects"}
                   onChange={(e) => setLoc(side)({ ...l, path: e.target.value })} />
          </label>
          <PathFeedback root={rootOf(l.storage)} rel={l.path} />
        </div>
      </>
    );
    return (
      <LocationField side={side} operation={f.operation} loc={l} onChange={setLoc(side)} catalog={catalog}
                     search={search} resolved={resolved[side]} allowRaw={unrestricted} rawSlot={raw}
                     destOwner={side === "destination" ? destOwner : undefined} searchField={searchFields[side]} />
    );
  };

  // sync 목적지 조건과 소유권 안내 -- 모든 작업이 root 라 root 실행 기준 문구다(preflight _DEST_TYPE_CHECK·_DEST_CHECK:
  // 목적지가 없으면 상위 디렉토리가 있어야 하고(root 도 test -w 가 실패하면 거부), 있으면 디렉토리여야 한다).
  const syncConditions = (
    <InfoCard role="note" aria-label="목적지 조건과 소유권">
      <p className="font-medium">목적지 조건과 소유권 — root 실행 기준</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs">
        <li>
          <strong>목적지가 없는 경우</strong>: sync 가 목적지 디렉토리를 새로 만듭니다. 목적지의{" "}
          <strong>상위 디렉토리는 이미 있어야</strong> 합니다(root 실행 — 쓰기 권한 검사 우회)
          {" "}— 중간 디렉토리는 만들지 않습니다.
          {destParentAbs !== null && (
            <> 이 요청에서는 <code className="rounded bg-surface px-1 break-all">{destParentAbs}</code> 가
              이미 있어야 합니다.</>
          )}
        </li>
        <li>
          <strong>목적지가 이미 있는 경우</strong>: 디렉토리여야 합니다(파일이면 거부).{" "}
          {!pvDest && directDest?.rule === "storage"
            ? "root 실행이라 쓰기·소유 검사는 우회되고, 그 안의 같은 경로 항목은 목적지 스토리지 기본 소유자와 소스의 권한·시각으로 다시 맞춰집니다(chown·chmod 를 지정하면 그 값)."
            : !pvDest && directDest?.rule === "storage_if_under_pv"
            ? "root 실행이라 쓰기·소유 검사는 우회되고, 그 안의 같은 경로 항목은 소스의 권한·시각과 결과 소유(PV 루트 아래면 목적지 스토리지 기본 소유자, 아니면 소스 소유)로 다시 맞춰집니다(chown·chmod 를 지정하면 그 값)."
            : !pvDest
            ? "root 실행이라 쓰기·소유 검사는 우회되고, 그 안의 같은 경로 항목은 소스의 소유·권한·시각으로 다시 맞춰집니다(chown·chmod 를 지정하면 그 값)."
            : pvOwnerRule === "pv_root"
            ? "root 실행이라 쓰기·소유 검사는 우회되고, 그 안의 같은 경로 항목은 목적지 PV 루트의 소유자·그룹과 소스의 권한·시각으로 다시 맞춰집니다(chown·chmod 를 지정하면 그 값)."
            : "root 실행이라 쓰기·소유 검사는 우회되고, 그 안의 같은 경로 항목은 목적지 스토리지 기본 소유자와 소스의 권한·시각으로 다시 맞춰집니다(chown·chmod 를 지정하면 그 값)."}
        </li>
        <li><strong>소유권</strong>: {ownership.long}</li>
        {storageOwnerBad && (
          <li className="text-bad">
            {pvDest ? "이 PV 의 스토리지" : "목적지 스토리지"}에 저장된 기본 소유 값이 올바르지 않아 제출할 수 없습니다 — 관리자가 스토리지 화면에서 기본 소유 uid·gid 를 다시 저장해야 합니다.
          </li>
        )}
        {/* 링크 경로 규칙(d192 이식, 괴리 [14] -- dms-k8s 문구는 2026-10-28 통일 봉쇄: 관리 디렉토리·PV 아래 어느 단계든) --
            링크로 된 목적지가 위 「디렉토리여야 합니다」를 만족한다고 보고 낸 요청이 preflight 에서 거부됐다
            (destination_path_redirected). */}
        <li>{SYNC_PATH_LINK_RULE}</li>
        <li>조건이 맞지 않으면 미리보기 전에 거부되고 사유가 표시됩니다 — 아무것도 복사되지 않습니다.</li>
      </ul>
    </InfoCard>
  );

  // ---- 단계별 등록(2026-11-26 -- 사용자 결정 「초안 1 상단 스테퍼」, design-wizard §1) -------------------------------------------------
  // 값·검증·바디는 위 그대로다(k8sBody 무변경 -- 시험의 바디 toEqual 이 바이트 단위 그물). 바뀐 것은 그리는 방식뿐: 단계 넷(작업 종류 →
  // 대상 → 옵션 → 검토·제출)이고 단계 게이트를 모두 합치면 옛 제출 게이트에서 진행 중 상태를 뺀 것과 같다(submitGates). 단계 본문은
  // 이 파일의 지역 render 함수다(수십 개의 파생값을 읽는다 -- 따로 떼면 props 가 폭발하고 「로직 그대로」를 확인하기 어렵다). 단계
  // 상태·URL(?step=)·Enter·포커스·도달 규칙은 useWizard 가 쥔다(규칙 전문은 components/form/Wizard 머리 주석).
  const singleOp = me.data !== undefined && operations.length === 1;
  const steps: WizardStep<SubmitStep>[] = [
    { key: "kind", title: "작업 종류", blocked: gates.gate.kind, reason: gates.barReason("kind") },
    { key: "target", title: targetTitle, blocked: gates.gate.target, reason: gates.barReason("target") },
    { key: "options", title: isAdmin ? "옵션·실행 설정" : "옵션", blocked: gates.gate.options, reason: gates.barReason("options") },
    // 마지막 단계의 blocked 는 「다음」이 아니라 최종 제출 게이트(= blocked)다.
    { key: "review", title: "검토·제출", blocked, reason: gates.barReason("review") },
  ];
  // 시작 단계(design-wizard §1-6): ?pv= 딥링크(관리자 PV 탐색 「이 PV로 단일 작업」)는 PV 를 미리 골랐으니 ② 대상에서, 연산이 하나뿐인
  // 사용자(사이트 기본 DMS_USER_ALLOWED_OPERATIONS=sync·옛 서버)는 고를 것이 없는 ①을 완료로 두고 ②에서 시작한다. me 가 처음
  // 렌더부터 있으면(셸이 이미 받아 둔 캐시) 처음 상태로, 늦게 오면 아래 효과가 replace 로 옮긴다 -- 로드 전 대체값도 ["sync"] 라 me
  // 가 오기 전에는 판정하지 않는다(그러면 모두가 ①을 건너뛴다).
  const wizard = useWizard<SubmitStep>({
    steps, initial: parsePvParam(params.get("pv")) !== null || singleOp ? "target" : "kind" });
  const autoStartChecked = useRef(me.data !== undefined);
  useEffect(() => {
    if (autoStartChecked.current || me.data === undefined) return;
    autoStartChecked.current = true;
    // 아직 사용자가 단계를 옮기지 않았을 때만(현재 ①이고 URL 에 step 이 없다). ①은 스테퍼로 다시 열 수 있다.
    if (singleOp && wizard.current === "kind" && !params.has("step")) wizard.jump("target");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [me.data]);

  // 제출 방어 세 겹(design-wizard §1-6 「제출 방어」): ① 더블클릭 -- 「다음」과 「제출」은 바의 같은 자리·같은 노드라 ③에서 「다음」을
  // 두 번 누르면 두 번째 클릭이 막 그려진 「제출」에 떨어진다. 같은 연속 클릭의 두 번째 이후(e.detail > 1)를 무시한다(바도 같은 줄을 둔다
  // -- 둘 다 있어도 무해). ② 단계 가드 -- 검토를 안 보고 새어 나온 submit 이벤트(암묵 제출·회귀)는 current !== "review" 로 막는다.
  // ③ 모든 버튼 type="button"(form 안 type 없는 버튼은 submit 버튼이다). 그래도 새는 submit 은 blocked 가드가 한 번 더 막는다.
  function handleSubmit(e?: SyntheticEvent) {
    e?.preventDefault();
    if (blocked || wizard.current !== "review") return;
    submit.mutate(k8sBody(), { onSuccess: (r) => nav(`/jobs/${r.request_id}`) });
  }
  const handleSubmitClick = (e: MouseEvent<HTMLButtonElement>) => {
    if (e.detail > 1) return;
    handleSubmit();
  };

  // 설계 D2·D4: 모든 작업은 root -- 체크박스 대신 사실 한 줄. 면제자는 「권한 기준 사용자」로 PV 범위를 정하고(비우면 전체 PV 검색),
  // 목록 밖 관리자는 자기 맵핑 범위만이라 무엇이 더 필요한지 한 줄로 말한다(①). 연산이 하나뿐이라 ①을 건너뛰면 ② 머리로 옮긴다.
  const rootFact = me.data !== undefined && (
    <p className="text-sm text-ink/80">
      모든 작업은 root 로 실행됩니다 — {unrestricted
        ? "전체 PV·직접 경로 권한이 있어 모든 PV 와 직접 경로를 고를 수 있습니다."
        : "고를 수 있는 PV 는 내 namespace 권한(rolebinding)으로 정해집니다."}
    </p>
  );

  // ① 작업 종류
  const kindStep = () => (
    <>
      <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
        <label className="text-sm block">연산
          <select aria-label="연산" className={field} value={f.operation}
                  onChange={(e) => setF({ ...f, operation: e.target.value as Operation })}>
            {/* 서버 allowlist(me.user_operations -- 설계 D15)를 그대로 그린다. 표시 게이트일 뿐 진짜 차단은
                서버(routes_requests operation_admin_only·scan_unrestricted_only 403). */}
            {operations.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </label>
        <p className="text-sm text-muted sm:pt-8">{OPERATION_HELP[f.operation]}</p>
      </div>
      {rootFact}
      {isAdmin && !unrestricted && (
        <p className="text-xs text-ink/70">
          직접 경로·사용자 지정은 계정 화면의 「전체 PV·직접 경로 권한」이 필요합니다 — 지금은 내 namespace 권한 안의 PV 만 고릅니다.
        </p>
      )}
      {unrestricted && (
        <FieldRow
          control={<>
            <label className="text-sm block">권한 기준 사용자
              <input aria-label="권한 기준 사용자" className={field} list="k8s-scope-users"
                     aria-invalid={scopeProblem !== null || undefined}
                     placeholder="비우면 전체 PV 검색 · 예: junwoo.song" autoComplete="off" {...scopeField.bind}
                     onChange={(e) => { clearSubmitError(); scopeField.bind.onChange(e); }} />
            </label>
            <datalist id="k8s-scope-users">
              {scopeChoices.map((u) => <option key={u} value={u} />)}
            </datalist>
            {scopeProblem !== null && (
              <p className="mt-1 text-bad text-sm">{scopeProblem === "reserved" ? REASON_MESSAGES.username_reserved
                : scopeProblem === "numeric_only" ? REASON_MESSAGES.username_numeric_only
                : "계정 아이디 형식이 아닙니다(영소문자·숫자로 시작, 영소문자·숫자·. _ -)"}</p>
            )}
          </>}
          help="적으면 그 사용자의 namespace 권한 안의 PV 만 보이고, 작업에 그 사용자가 「대상 사용자」로 기록됩니다(실행은 그래도 root). 비우면 모든 클러스터의 PV 를 검색합니다." />
      )}
    </>
  );

  // sync 목적지 한 줄(② -- 원문 둘을 그대로 이어 붙인다, 새 문장 없음): 「목적지 조건」 요약 행 문자열 + 「목적지 소유」 short. 전문(목적지
  // 조건과 소유권 카드)은 ④ 검토에 접지 않고 둔다(2026-09-30 「분명히 보이게」). storageOwnerBad 면 대상 단계에서 막는 이유가 보여야
  // 하므로 카드의 빨간 줄(같은 문장)을 여기에도 늘 보인다.
  const destCondition = `상위 디렉토리 ${destParentAbs ?? "(목적지 경로의 상위)"} 가 있어야 함(root 실행 — 권한 검사 우회). 목적지가 없으면 새로 만듦`;
  const destLine = (
    <div className="rounded-xl bg-infobg px-4 py-3">
      <SummaryList>
        <SummaryRow label="목적지 조건">{destCondition}</SummaryRow>
        <SummaryRow label="목적지 소유">{ownership.short}</SummaryRow>
      </SummaryList>
      <p className="mt-1.5 text-xs text-muted">전문은 ④ 검토·제출에서 봅니다.</p>
      {storageOwnerBad && (
        <p className="mt-1 text-xs text-bad">
          {pvDest ? "이 PV 의 스토리지" : "목적지 스토리지"}에 저장된 기본 소유 값이 올바르지 않아 제출할 수 없습니다 — 관리자가 스토리지 화면에서 기본 소유 uid·gid 를 다시 저장해야 합니다.
        </p>
      )}
    </div>
  );

  // ② 대상. "경로는 관리 디렉토리 아래 상대경로" 는 스토리지 선택 아래 캡션(StoragePicker)이 이미 말하고, 입력 아래 "지정 경로" 가
  // 그 결과를 보여 준다 -- 구획 캡션으로 세 번째 반복하지 않는다.
  const targetStep = () => (
    <>
      {singleOp && rootFact}
      {/* 목록 로드 실패 문구는 스토리지를 고르는 이 단계에 -- 잠금은 단계 게이트·제출 게이트가 맡는다 */}
      {storagesBlock && (
        <p className="text-bad text-sm">{(storagesQ.error as ApiError).message}</p>
      )}
      {/* 쪽마다 LocationField -- namespace (StorageClass) → PV 이름 → PV 안 경로(면제자는 「PV | 직접 경로」·검색). 목록
          상태(조회 실패·아직 동기화 안 됨·권한 namespace 0개)는 단계 머리에 한 번만. */}
      <CatalogNotice catalog={catalog} search={search} actor={me.data?.actor ?? ""} scopeUser={scopeUser} />
      {f.operation === "sync" ? (
        <>
          <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
            {locationField("source")}
            <ArrowRight aria-hidden
                        className="h-5 w-5 rotate-90 justify-self-center text-muted md:mt-8 md:rotate-0" />
            {locationField("destination")}
          </div>
          {/* 결과 소유(2026-10-26): 특권 운영자(면제자)만, PV 목적지 sync 에만 -- 기본은 스토리지 기본 소유자, 옵션으로 PV 루트
              소유자. 사용자에겐 조작이 없다(늘 스토리지 기본 -- 목적지 한 줄·검토 카드가 그 값을 말한다). chown 을 지정하면 그
              값이 이겨 선택은 잠기고 이유를 옆에 말한다. */}
          {unrestricted && pvDest && (
            <FieldRow
              control={<div className="flex flex-wrap items-center gap-2">
                <span className="text-sm">결과 소유</span>
                <Segmented label="결과 소유" value={pvOwnerChoice} onChange={setPvOwnerChoice} disabled={chownSet}
                           options={[{ value: "storage", label: "스토리지 기본" },
                                     { value: "pv_root", label: "PV 루트 소유자" }]} />
              </div>}
              help={chownSet
                ? `chown 을 지정해 결과는 chown 지정값(${f.chown.trim()})으로 셋업됩니다 — 결과 소유 선택은 쓰이지 않습니다.`
                : `기본은 목적지 스토리지 기본 소유자${storageOwnerLabel !== null ? ` ${storageOwnerLabel}` : "(uid:gid)"}입니다(사용자 작업은 늘 이 규칙). 「PV 루트 소유자」는 점검이 잰 목적지 PV 루트의 소유자·그룹으로 맞추며 특권 운영자만 고를 수 있습니다.`} />
          )}
          {destLine}
        </>
      ) : (<>
        {locationField("target")}
        {/* rm 대상의 링크 규칙(d192 이식, 괴리 [14]·[27] -- dms-k8s 는 끝 링크 허용: 2026-10-28 사용자 결정 「rm 끝 링크 허용
            유지」). 중간 단계 링크로 거부된 뒤 「링크가 가리키는 곳」을 다시 내지 않게, 끝 링크면 링크만 지운다는 사실과 함께
            고르는 자리에서 먼저 말한다. */}
        {f.operation === "rm" && (
          <p className="text-xs text-muted" role="note" aria-label="삭제 경로 조건">{RM_PATH_LINK_RULE}</p>
        )}
      </>)}
    </>
  );

  // ③ 옵션(·실행 설정)
  const optionsStep = () => (
    <>
      <SubSection title="실행 옵션" caption={f.operation === "sync" ? SYNC_MAX_ERRORS_NOTE : undefined}>
        {f.operation === "sync" ? (
          <>
            {/* 옵션 설명(2026-08-20, 사용자 요청): dsync --delete / --contents. */}
            <FieldRow align="check"
              control={<label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="delete" checked={f.delete} onChange={on("delete")} /> delete
              </label>}
              help={SYNC_OPTION_HELP.delete} />
            <FieldRow align="check"
              control={<label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="contents" checked={f.contents} onChange={on("contents")} /> contents
              </label>}
              help={SYNC_OPTION_HELP.contents} />
            {/* direct·quiet·고급옵션·우선순위는 운영자 전용(2026-08-20, 사용자
                결정): 사용자 sync 폼은 delete·contents 만 남긴다. 숨겨도 제출
                payload 는 동일하다 -- checkedOptions 가 기본값(false)을 이미
                생략하고, 고급 프리필(batch_files·bufsize)은 도구 기본값이라
                운영자가 고급을 안 펼친 것과 결과가 같다. */}
            {/* direct·quiet 도 delete·contents 처럼 설명을 칸 옆에 그대로 보인다(d192 이식): 예전엔 title 툴팁뿐이라 quiet 가
                실패 원인까지 지운다는 것(괴리 [9]·[13]·[18]·[29])과 direct 의 bufsize 조건([8])이 보이지 않았다. */}
            {isAdmin && (<>
              <FieldRow align="check"
                control={<label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="direct" checked={f.direct} onChange={on("direct")} /> direct
                </label>}
                help={DIRECT_HELP} />
              <FieldRow align="check"
                control={<label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
                </label>}
                help={QUIET_HELP.sync} />
            </>)}
            {/* 기본 접힘 — 단순 sync 제출 동선을 바꾸지 않기 위해 <details> 로 숨긴다 */}
            {isAdmin && (
              <details className="rounded-lg border border-line"
                       open={advancedOpen || advancedError !== null || ownerFixOpen}
                       onToggle={(e) => {
                         const el = e.currentTarget;
                         // 오류가 있는 동안(운영자의 storageOwnerBad 우회 포함 -- ownerFixOpen)은 접히지 않는다: 접으면 다음 단계로
                         // 못 가는 이유(빨간 안내·바 이유가 가리키는 chown 칸)가 숨는다. open prop 은 true→true 라 React 가 DOM 을
                         // 다시 열어 주지 않는다 -- 직접 되돌린다.
                         if (!el.open && (advancedError !== null || ownerFixOpen)) { el.open = true; return; }
                         setAdvancedOpen(el.open);
                       }}>
                <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium">고급 옵션</summary>
                <div className="space-y-4 border-t border-line px-4 py-4">
                  <FieldRow align="check"
                    control={<label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" aria-label="open_noatime"
                             checked={f.openNoatime} onChange={on("openNoatime")} /> open_noatime
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
                        <input aria-label="chmod" className={field} value={ownerModeEditable ? f.chmod : ""}
                               aria-invalid={chmodError !== null || undefined}
                               disabled={!ownerModeEditable} onChange={on("chmod")} />
                      </label>
                      {chmodError && <p className="mt-1 text-bad text-sm">{chmodError}</p>}
                    </>}
                    help={!ownerModeEditable && "특권 운영자만 지정할 수 있습니다 — 결과의 권한 비트는 소스 그대로입니다."} />
                  {/* 「비우면」의 기본(_auto_chown): PV 목적지면 결과 소유 규칙(스토리지 기본 소유자 | 운영자가 고른 PV 루트
                      소유자)으로 자동 chown, 아니면 root 실행이라 소스 소유권 보존 -- 정직하게 병기한다. */}
                  <FieldRow
                    control={<>
                      <label className="text-sm block">chown (선택 · 숫자 uid:gid)
                        <input aria-label="chown" className={field} value={ownerModeEditable ? f.chown : ""}
                               aria-invalid={chownError !== null || undefined}
                               placeholder={ownerModeEditable ? "예: 10003:10000" : "특권 운영자만"}
                               disabled={!ownerModeEditable} onChange={on("chown")} />
                      </label>
                      {chownError && <p className="mt-1 text-bad text-sm">{chownError}</p>}
                    </>}
                    help={<>
                      {pvDest
                        ? `비우면 ${unrestricted && pvOwnerChoice === "pv_root"
                            ? "목적지 PV 루트의 소유자·그룹으로 자동 chown 됩니다(PV 목적지 · 결과 소유 「PV 루트 소유자」 — 실제 값은 점검 뒤 확인 창에 보입니다)"
                            : `목적지 스토리지 기본 소유자${storageOwnerLabel !== null ? ` ${storageOwnerLabel} 로` : "(uid:gid)로"} 자동 chown 됩니다(PV 목적지)`}${ownerModeEditable ? ". chown 을 지정하면 그 값으로 셋업됩니다." : " — chown 지정은 특권 운영자만 할 수 있습니다."}`
                        : rawDestKind === "pv"
                        ? `비우면 목적지 스토리지 기본 소유자${directOwnerLabel !== null ? ` ${directOwnerLabel} 로` : "(uid:gid)로"} 자동 chown 됩니다(PV 를 담는 스토리지의 직접 경로). chown 을 지정하면 그 값으로 셋업됩니다.`
                        : rawDestKind === "both"
                        ? `비우면 목적지 경로가 그 스토리지의 PV 루트 아래일 때 목적지 스토리지 기본 소유자${directOwnerLabel !== null ? ` ${directOwnerLabel} 로` : "(uid:gid)로"} 자동 chown 되고, 아니면 원래(소스) 소유권을 보존합니다(root 실행). chown 을 지정하면 그 값으로 셋업됩니다.`
                        : "비우면 원래(소스) 소유권을 보존합니다(root 실행). chown 을 지정하면 목적지와 복사본이 그 값으로 셋업됩니다."}
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
            </div>
            {recursiveMissing && <p className="text-bad text-sm">재귀 옵션이 필요합니다</p>}
            {statLiteConflict && <p className="text-bad text-sm">stat과 lite는 함께 쓸 수 없습니다</p>}
            {/* quiet 는 지우지 못한 항목 목록까지 없앤다 -- 설명을 칸 옆에 보인다(sync 와 같은 이유). */}
            <FieldRow align="check"
              control={<label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="quiet" checked={f.quiet} onChange={on("quiet")} /> quiet
              </label>}
              help={QUIET_HELP.rm} />
          </>
        )}
      </SubSection>
      {/* 실행 설정은 운영자 전용(2026-08-20, 사용자 결정): 사용자 폼에선 우선순위를 정책 기본에 맡긴다(생략 = resolve_priority
          가 정책값으로 해석). 노드 수·프로세스 수도 정책이 정한다. */}
      {isAdmin && (
        <SubSection title="실행 설정"
                    caption={<>{policyCaption}<span className="block">{NODE_CPU_CAP_NOTE}</span></>}>
          <FieldRow
            control={<label className="text-sm block">우선순위
              <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
                <option value="">{priorityDefaultLabel}</option>
                <option value="low">low</option><option value="mid">mid</option><option value="high">high</option>
              </select>
            </label>}
            help={priorityCapCaption} />
          {/* 실행 신원·root 체크박스는 없다(설계 D2·D4) -- 모든 작업이 root 이고, 사용자 지정은 ①의 「권한 기준 사용자」
              (면제자만)가 맡는다. */}
          {f.operation === "rm" && (
            <p className="text-xs text-bad">삭제가 root 권한으로 수행됩니다(모든 작업은 root)</p>
          )}
        </SubSection>
      )}
    </>
  );

  // ④ 검토·제출 -- 옛 오른쪽 요약의 행 전부(라벨 그대로) + 목적지 조건과 소유권 카드(sync, 접지 않음) / 삭제 규칙(rm).
  const reviewStep = () => (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:items-start">
      <div className="min-w-0 rounded-xl border border-line px-4 py-4 sm:px-5">
      <SummaryList>
        <SummaryRow label="연산">{f.operation}</SummaryRow>
        {f.operation === "sync" ? (
          <>
            <SummaryRow label="소스">{sideText("source")}</SummaryRow>
            {sideAbs("source") !== null && (
              <SummaryRow label="소스 지정 경로">{sideAbs("source")}</SummaryRow>
            )}
            <SummaryRow label="목적지">{sideText("destination")}</SummaryRow>
            {sideAbs("destination") !== null && (
              <SummaryRow label="목적지 지정 경로">{sideAbs("destination")}</SummaryRow>
            )}
            {/* 목적지 조건과 소유권 카드의 요점. root 실행이면 권한 검사가 우회되지만 상위 디렉토리
                존재는 여전히 필요하다(preflight test -w). */}
            <SummaryRow label="목적지 조건">
              {`상위 디렉토리 ${destParentAbs ?? "(목적지 경로의 상위)"} 가 있어야 함(root 실행 — 권한 검사 우회). 목적지가 없으면 새로 만듦`}
            </SummaryRow>
            <SummaryRow label="목적지 소유">{ownership.short}</SummaryRow>
          </>
        ) : (
          <>
            <SummaryRow label="대상">{sideText("target")}</SummaryRow>
            {sideAbs("target") !== null && (
              <SummaryRow label="지정 경로">{sideAbs("target")}</SummaryRow>
            )}
          </>
        )}
        <SummaryRow label="옵션"><OptionTokens options={currentOptions()} /></SummaryRow>
        <SummaryRow label="우선순위">
          {`${f.priority === "" ? priorityDefaultLabel : f.priority}${priorityClamp !== null ? ` — ${priorityClamp}` : ""}`}
        </SummaryRow>
        {unrestricted && scopeInput !== "" && (
          <SummaryRow label="대상 사용자">{scopeInput}</SummaryRow>
        )}
        {/* rm·scan 은 머리만(요청 상세와 같은 PRIV_ROOT_HEAD) -- sync 의 소유 결과 꼬리는 rm·scan 과 관계없다(2026-11-11). */}
        <SummaryRow label="실행 권한" className="text-bad">
          {f.operation !== "sync" ? PRIV_ROOT_HEAD
            : pvDest ? (pvOwnerRule === "pv_root" ? PRIV_ROOT_SYNC_PV_ROOT : PRIV_ROOT_SYNC_PV)
            : directDest?.rule === "storage" ? PRIV_ROOT_SYNC_PV
            : directDest?.rule === "storage_if_under_pv" ? PRIV_ROOT_SYNC_DIRECT_BOTH : PRIV_ROOT_SYNC}
        </SummaryRow>
      </SummaryList>
      </div>
      <div className="min-w-0 space-y-4">
        {f.operation === "sync" && syncConditions}
        {/* 삭제 규칙 -- 경로는 왼쪽 「대상」 한 곳에만(같은 카드 안 getByText(경로) 다중 일치 방지), 「되돌릴 수 없음」은 하단 바
            strip 한 곳이라 여기 되풀이하지 않는다. */}
        {f.operation === "rm" && (
          <p className="rounded-xl border border-bad/30 bg-badbg px-4 py-3 text-xs text-bad">{RM_PATH_LINK_RULE}</p>
        )}
      </div>
    </div>
  );

  // 대상 단계의 지름길(design-wizard §1-6 「바로가기」): 옵션 단계를 거치기 전엔 「옵션 기본값으로 검토」, 거친 뒤엔 「검토로 건너뛰기」.
  // 대상 게이트를 통과했을 때만 보이고, 검토로 갈 수 없으면(옵션이 막힘) 잠긴다(aria-disabled -- 눌러도 동작 없음, 이유는 title).
  // 좁은 폭(<sm)에서는 숨긴다(바 버튼 줄이 넘친다). 검토를 이미 봤고 갈 수 있으면 바가 「검토로 돌아가기」를 대신 그린다.
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
            {/* 배치 안내는 특권 운영자(canRoot)에게만 -- 배치 생성은 특권 목록·세션 게이트(403 privileged_not_authorized)라 목록 밖
                관리자는 못 쓰고, 그 관리자의 연산 선택지엔 scan 도 없다(2026-11-11: isAdmin 하나로 갈라 「sync·scan·rm … 배치 작업으로」를
                보였다). 목록 밖 관리자는 사용자와 같은 「권한 있는 PV」 흐름이다. 연산 이름은 선택지와 같은 operations 에서 짓는다. */}
            {canRoot ? `경로 하나에 ${operations.join("·")} 을 실행합니다 — 여러 경로의 ${operations.join("·")} 은 배치 작업으로 묶어 요청하세요.`
              : `권한 있는 PV 에서 ${operations.join("·")} 을 실행합니다 — namespace (StorageClass) → PV 이름 → PV 안 경로 순서로 고르세요.`}
          </p>
        )}
      </div>
      {/* form 은 남긴다(design-wizard §1-3): 걷어 내면 암묵 제출 부류가 사라지는 대신 「강제 submit 도 막힌다」 시험이 관찰할 입구도
          사라져 handleSubmit 가드가 죽은 코드가 된다. 대신 모든 버튼 type="button" + 셸이 처리하는 모든 Enter 에 preventDefault(Enter
          는 다음 단계일 뿐 절대 제출하지 않는다) + handleSubmit 의 단계·blocked 가드로 막는다. */}
      <form onSubmit={handleSubmit}>
        <WizardShell {...wizard.shellProps}>
          <WizardStepper wizard={wizard} />
          <WizardStepCard wizard={wizard}>
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
