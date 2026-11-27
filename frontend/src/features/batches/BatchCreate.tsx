import { useEffect, useRef, useState } from "react";
import type { MouseEvent } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Download, Plus, X } from "lucide-react";
import { autoConfirmable, useCreateBatch } from "./useBatches";
import type { BatchOp, CreateBatchBody } from "./useBatches";
import { parseItemsCsv, serializeItemsCsv } from "../../lib/csv";
import type { ScanRow, SyncRow } from "../../lib/csv";
import { BatchStorageCards } from "./BatchStoragePicker";
import { useUserStorages } from "../storages/useUserStorages";
import { usePolicies } from "../policies/usePolicies";
import type { Policy } from "../../lib/types";
import { NODE_CPU_CAP_NOTE } from "../../lib/jobTool";
import { field } from "../jobs/formFields";
import {
  BATCH_OPEN_NOATIME_HELP, BUFSIZE_HELP, CHMOD_RE, chownFieldError, DIRECT_HELP, directBufsizeError, HELP_SUMMARY,
  OPERATION_LABEL_BATCH, QUIET_HELP, SCAN_INT_FIELDS, SYNC_DSYNC_SPECIAL_FILE_NOTE, SYNC_INT_FIELDS, SYNC_MAX_ERRORS_NOTE,
  SYNC_OPTION_HELP, intFieldError, policyCaptionAlert, scanIntFieldError, syncIntFieldError,
} from "../jobs/optionRules";
import { Button } from "../../components/ui/Button";
import { FieldRow, OptionTokens, SubmitHints, SummaryList, SummaryRow } from "../../components/form/SubmitLayout";
import { useWizard, WizardActionBar, WizardShell, WizardStepCard, WizardStepper } from "../../components/form/Wizard";
import type { WizardStep } from "../../components/form/Wizard";
import { MoreText, OperationCards, OptionToggle, ReviewCard, SubSection } from "../../components/form/wizardParts";
import { batchGates } from "./batchGates";
import type { BatchStep } from "./batchGates";
import { ApiError } from "../../lib/api";
import { syncOwnership } from "../../lib/syncOwnership";
import { relativePathProblem, RM_PATH_LINK_RULE, SYNC_PATH_LINK_RULE } from "../../lib/storagePaths";
import { rmTargetOverlapDetails } from "../../lib/rmTargets";

// 배치 생성(슬라이스 32). 2026-11-27 단계별 등록(dms-k8s 46caffe3·7bfa2be3·b1eddc3c·d7a228f2 이식 -- 그 라인 사용자 결정 「초안 1 상단
// 스테퍼」, 단일 작업과 같은 위저드 골격 components/form/Wizard): 작업 종류 → 스토리지 → 항목 → 옵션·실행 제어 → 이름·검토의 다섯 단계.
// 2026-10-06 에 4스텝 위저드를 걷고 한 장 시트 + 오른쪽 요약으로 갔던 이유(좁은 카드·드롭다운 하나뿐인 단계·값이 안 보임)는 본문 열 전체
// 폭·단계마다 의미 있는 묶음(연산 카드·스토리지 카드)·스테퍼 값·사람이 읽는 검토로 피한다. **값·검증·바디는 그대로다**: 모든 값은 이 화면
// 최상위 useState(단계 본문이 언마운트돼도 남는다), 바디는 buildBody() 하나(시험의 바디 toEqual 이 바이트 단위 그물), 생성 게이트·이유
// 목록은 batchGates 순수 함수가 옛 식·문장·순서 그대로 만들고 단계 태그만 붙인다(단계 게이트의 합 = 옛 생성 게이트에서 진행 중 상태를
// 뺀 것). dms-k8s 전용(PV 에서 경로 찾기 시트·서버 CSV 해석과 「스토리지를 CSV 로 정하기」·템플릿 받기·스토리지 종류 묶음·PV 목적지
// 결과 소유)은 옮기지 않았다 -- dms-ssc 의 CSV 는 클라이언트 parseItemsCsv(경로 열만)이고 PV 개념이 없다.

type InputTab = "table" | "paste" | "upload";

// 우선순위 순서는 PRIORITIES(domain.py:61)의 미러 — 상한 초과 판정에 쓴다.
const PRIORITY_RANK: Record<string, number> = { low: 0, mid: 1, high: 2 };

// 동시 실행 상한 프리필(사용자 결정 2026-10-01: 2 → 32). 서버엔 기본값이 없다(필수 필드,
// domain.validate_batch 1..64) -- 기본은 이 화면이 정해 바디에 늘 싣는다. 배치 하나가 동시에
// 미리보기·실행하는 항목(잡) 수이고, 자원이 모자라면 잡은 Volcano 큐에서 기다린다.
export const DEFAULT_MAX_CONCURRENCY = 32;

// 행은 경로만 나른다(스토리지는 배치 레벨 선택) — a = target(scan·rm)/source(sync),
// b = destination(sync 전용). CSV 파서(ScanRow/SyncRow)와 제출 바디 조립의 중간형.
interface RowPair { a: string; b: string }

// 항목 표의 입력 칸(촘촘한 변형 -- field 의 mt-1 이 행 정렬을 흔든다).
const cell = "w-full rounded-lg border border-line px-3 py-1.5 text-sm";

const OPERATION_HELP: Record<BatchOp, string> = {
  scan: "한 스토리지의 여러 경로를 scan 합니다. 행마다 잡 하나가 만들어집니다.",
  // 확인 단계(2026-10-07): sync 배치는 만들자마자 실행되지 않는다 -- 생성 화면 어디에도 이 사실이 없었다.
  sync: "소스 스토리지 → 목적지 스토리지 한 쌍 안에서 여러 경로를 sync 합니다. 행마다 잡 하나가 만들어지고, "
    + "항목별 미리보기가 끝나면 배치 상세에서 「배치 확인」을 눌러야 실행됩니다.",
  // rm(2026-10-21): sync 와 같은 확인 단계. 자동 실행은 켤 때만(아래 AUTO_HELP — 켜려면 「되돌릴 수 없음」 동의가 따로 필요).
  // 「그 아래 전부」의 예외(2026-10-28 d194): 경로 자신이 심볼릭 링크면 drm 이 링크만 지운다 -- 링크 너머까지로 읽히지 않게.
  rm: "한 스토리지의 여러 경로를 삭제합니다(경로와 그 아래 전부 — 경로 자신이 심볼릭 링크면 링크만). 행마다 잡 하나가 "
    + "만들어지고, 항목별 미리보기(지울 항목 수)가 끝나면 배치 상세에서 「배치 확인」을 눌러야 삭제됩니다.",
};
// 자동 실행(sync 2026-10-11, rm 2026-10-21)을 켜면 위 「눌러야 실행·삭제」가 거짓이 된다 -- 켠 동안은 이 문장으로 바꿔 말한다.
const AUTO_HELP: Partial<Record<BatchOp, string>> = {
  sync: "소스 스토리지 → 목적지 스토리지 한 쌍 안에서 여러 경로를 sync 합니다. 행마다 잡 하나가 "
    + "만들어지고, 항목별 미리보기가 끝나면 「배치 확인」 없이 바로 실행됩니다(자동 실행 켜짐).",
  rm: "한 스토리지의 여러 경로를 삭제합니다(경로와 그 아래 전부 — 경로 자신이 심볼릭 링크면 링크만). 행마다 잡 하나가 "
    + "만들어지고, 항목별 미리보기(지울 항목 수)가 끝나면 「배치 확인」 없이 바로 삭제됩니다(자동 실행 켜짐).",
};

const initial = {
  op: "scan" as BatchOp,
  storage: "", srcStorage: "", dstStorage: "",
  // rm 옵션(SubmitJob 미러). recursive 는 동의 게이트라 서버가 기본으로 박지 않는다(domain._OPTION_DEFAULTS rm 주석) —
  // 화면이 켠 채 시작해 명시로 싣고, 끄면 즉답이 제출을 막는다(rm_recursive_required). quiet 는 scan·sync 와 같은 칸.
  recursive: true, stat: false, lite: false,
  // scan 옵션(SubmitScan 미러 — dscan 1b93d54 실측 전부. top_k는 신버전에서
  // 기능 삭제라 함께 제거). scanBatchFiles는 sync의 batchFiles와 별도 상태 —
  // 같은 옵션명이지만 범위가 다르다(scan 0..10억, sync 0..1,000만). 2026-09-17 부터
  // 둘 다 프리필 = 서버 기본(SCAN_INT_FIELDS/SYNC_INT_FIELDS ↔ domain._OPTION_DEFAULTS).
  scanBatchFiles: SCAN_INT_FIELDS.batch_files.prefill,
  brokenLimit: SCAN_INT_FIELDS.broken_limit.prefill, verbose: false, quiet: false,
  // sync 옵션(SubmitJob 미러, 정규식·범위는 optionRules 공유). open_noatime 은
  // 단건·배치 모두 기본 ON 으로 통일했다(사용자 결정 2026-08-22 — 소스 atime
  // 오염 방지). 배치는 통일 게이트로 항상 특권(root) 실행이라 O_NOATIME 권한
  // 제약이 없다. dsync 도구 기본은 off(mfu_flist_copy.c:3344)라 DMS 가 명시적으로
  // 켠다. (비 root 실행의 타인 소유 파일 O_NOATIME EPERM 은 2026-10-27 포크부터 그 파일만 O_NOATIME 없이
  // 다시 열어 복사한다 -- optionRules OPEN_NOATIME_HELP.)
  // batchFiles·bufsize 프리필은 단건 폼과 같은 단일 출처(SYNC_INT_FIELDS) —
  // 「왜 도구 기본과 다른 값을 명시 전송하는가」는 그 주석에 있다.
  delete: false, contents: false, direct: false,
  openNoatime: true,
  batchFiles: SYNC_INT_FIELDS.batch_files.prefill,
  bufsize: SYNC_INT_FIELDS.bufsize.prefill,
  chmod: "", chown: "",
  // 실행 제어: priority "" = "(정책 기본)" = 바디에서 생략(null≠0).
  // nodeCount/procsPerNode "" = 생략 = 정책값. mc 상한 64 는 서버 위생 상한의 미러.
  // mc 도 문자열 상태다: number 상태 + Number(e.target.value) 는 지우면 0 이
  // 그려지고 이어 친 숫자가 "08"로 남는다(type=number 숫자 동등 비교) — 정책
  // 다이얼로그에서 잡은 결함과 같은 유형. 변환은 제출 시점 한 곳.
  priority: "", nodeCount: "", procsPerNode: "", mc: String(DEFAULT_MAX_CONCURRENCY), note: "", name: "",
  // 실행 신원: 빈값 = 바디에서 생략 = 서버 NULL(기본: 생성자 본인). 특권 여부와
  // 무관하다 — 배치는 통일 게이트(routes_batches)로 항상 특권(root) 실행.
  ownerUsername: "",
  // 자동 확인(sync 2026-10-11, rm 2026-10-21 사용자 결정 — 옵션, 기본 꺼짐): 켜면 미리보기가 끝나는 순간 「배치 확인」 없이
  // 실행된다. sync·rm 에서 켰을 때만 바디에 true 를 싣는다(서버는 명시 true 만 켠다 — 생략 = 사람이 확인).
  autoConfirm: false,
  // rm 자동 실행의 「되돌릴 수 없음」 동의 체크(필수): 켜야 제출이 풀리고 acknowledge_irreversible: true 로 실린다(서버도 본다 —
  // 422 batch_rm_auto_confirm_ack_required). 자동 실행 체크나 연산을 바꾸면 풀린다 — 지난 동의가 다른 설정으로 이어지지 않게.
  autoConfirmAck: false,
};

export function BatchCreate() {
  const nav = useNavigate();
  const create = useCreateBatch();
  const storagesQ = useUserStorages();
  const policiesQ = usePolicies();          // 정책 기본값 캡션(표시 전용 배선)
  // 폼 값은 단일 useState(SubmitJob 관례). 연산을 바꿔도 행·CSV 상태는 초기화하지 않는다(현행).
  const [f, setF] = useState(initial);
  // 고급 옵션 펼침 상태. 안의 오류로 제출이 잠기면 항상 펼친다(아래 details open/onToggle).
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [rows, setRows] = useState<RowPair[]>([{ a: "", b: "" }]);
  const [tab, setTab] = useState<InputTab>("table");
  const [csvText, setCsvText] = useState("");
  const [csvErrors, setCsvErrors] = useState<string[]>([]);

  const storages = storagesQ.data ?? [];
  const loadingStorages = storagesQ.isLoading;

  const on = (k: keyof typeof initial) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  // --- 대상·항목 검증 ---
  const storageChosen = f.op === "sync"
    ? f.srcStorage !== "" && f.dstStorage !== ""
    : f.storage !== "";                                   // scan·rm: 스토리지 하나
  const incompleteRows = rows.filter(
    (r) => r.a.trim() === "" || (f.op === "sync" && r.b.trim() === "")).length;
  // rm 행 즉답(2026-10-21) — 보내는 값(trim)으로 본다. ① 경로 형식: 서버 validate_rm_target/validate_relative_path 의
  // 미러(422 rm_root_forbidden·unsafe_path) — 관리 디렉토리 자신을 root 로 지우는 행은 서버까지 가기 전에 막는다.
  // ② 한 배치 안 겹침(같은 경로·조상과 자손): 서버 422 batch_rm_targets_overlap 의 미러 — 어느 행끼리인지 말한다.
  // scan·sync 는 서버 422 로 충분해 즉답하지 않는다(루트를 지우는 위험만 비대칭이다).
  // 두 목록 모두 문장 옆에 행 번호(0 기준)를 든다 -- ③ 행 칸의 aria-invalid(막힌 「다음」이 스크롤 상자 안 첫 틀린 행으로 포커스)가 읽는다.
  const rmPathErrors = f.op === "rm"
    ? rows.flatMap((r, i) => {
        const p = relativePathProblem(r.a.trim());
        return p === null ? [] : [{ row: i, message: `${i + 1}행: ${p}` }];
      })
    : [];
  const rmOverlapErrors = f.op === "rm"
    ? rmTargetOverlapDetails(rows.map((r, i) => ({ label: `${i + 1}행`, target: r.a.trim() })))
    : [];
  // 빨간 표지(aria-invalid)를 달 행: 형식 오류 행 + 겹침에 낀 두 행(주어·상대 -- 어느 쪽을 지우거나 고쳐도 풀린다).
  const rmInvalidRows = new Set<number>([
    ...rmPathErrors.map((e) => e.row), ...rmOverlapErrors.flatMap((d) => [d.at, d.other])]);
  // (항목 유효성 itemsValid 는 아래 batchGates 가 옛 식 그대로 만든다 -- 스토리지·행·CSV·rm 즉답의 곱)

  // --- 옵션·실행 제어 검증(즉답 미러 — 최종 심판은 서버 422) ---
  const verboseQuietConflict = f.op === "scan" && f.verbose && f.quiet;
  const scanBatchFilesError = f.op === "scan"
    ? scanIntFieldError("batch_files", f.scanBatchFiles) : null;
  const brokenLimitError = f.op === "scan"
    ? scanIntFieldError("broken_limit", f.brokenLimit) : null;
  const batchFilesError = f.op === "sync"
    ? syncIntFieldError("batch_files", f.batchFiles) : null;
  // 범위 오류가 먼저, 범위 안이면 direct 정렬 조건(서버 422 direct_bufsize_misaligned 미러 -- optionRules 주석).
  const bufsizeError = f.op === "sync"
    ? syncIntFieldError("bufsize", f.bufsize) ?? directBufsizeError(f.direct, f.bufsize) : null;
  const chmodError = f.op === "sync" && f.chmod.trim() !== "" && !CHMOD_RE.test(f.chmod.trim())
    ? "chmod 형식이 올바르지 않습니다 (예: D770,F660)" : null;
  const chownError = f.op === "sync" ? chownFieldError(f.chown) : null;
  const advancedError = batchFilesError ?? bufsizeError ?? chmodError ?? chownError;
  // 1..64 는 서버 위생 상한(1024)의 보수적 부분집합 — 실제 캡은 정책 max_nodes.
  const nodeCountError = intFieldError("노드 수", f.nodeCount, 1, 64);
  // 노드당 프로세스 수도 같은 부분집합 — 실제 캡은 정책 procs_per_node(min).
  const procsPerNodeError = intFieldError("노드당 프로세스 수", f.procsPerNode, 1, 64);
  // 필수 필드라 빈 값도 오류다 — intFieldError(빈 값 = 미입력 허용)와 다르다.
  const mcError = f.mc.trim() === ""
    ? "동시 실행 상한은 1..64 범위의 정수여야 합니다"
    : intFieldError("동시 실행 상한", f.mc, 1, 64);
  // rm 옵션 즉답(SubmitJob 과 같은 문구·규칙): recursive 해제는 서버 422 rm_recursive_required, stat+lite 는 invalid_option.
  const recursiveMissing = f.op === "rm" && !f.recursive;
  const statLiteConflict = f.op === "rm" && f.stat && f.lite;
  // rm 자동 실행은 동의 체크가 있어야 만든다(서버 422 batch_rm_auto_confirm_ack_required 의 미러).
  const rmAutoAckMissing = f.op === "rm" && f.autoConfirm && !f.autoConfirmAck;
  const controlsInvalid = verboseQuietConflict || scanBatchFilesError !== null
    || brokenLimitError !== null || advancedError !== null
    || nodeCountError !== null || procsPerNodeError !== null || mcError !== null
    || recursiveMissing || statLiteConflict;

  // 생성 게이트·이유 목록·단계 게이트는 순수 함수 하나가 같은 잎에서 만든다(batchGates -- 식·문장·순서는 옛 한 장 시트의 blocked·
  // hints 그대로, 단계 태그만 붙인다). 생성 게이트(blocked)는 이 하나다 -- 「배치 생성」은 지금처럼 disabled={blocked}.
  const gates = batchGates({
    createPending: create.isPending, storagesError: storagesQ.isError, storageChosen, rowsCount: rows.length, incompleteRows,
    csvErrorCount: csvErrors.length, rmPathErrorCount: rmPathErrors.length, rmOverlapCount: rmOverlapErrors.length,
    controlsInvalid, rmAutoAckMissing, op: f.op,
  });
  const blocked = gates.blocked;

  // --- 정책 기본값 캡션(백엔드 무변경 — 표시 배선만) ---
  // 도구→정책 키는 placement.py TOOL_TO_POLICY 의 미러: scan→"scan", rm→"rm", sync→공존
  // 노드가 있으면 "dsync", 없으면 "nsync" 폴백. 어느 쪽이 걸릴지는 런타임(노드
  // 상태)에야 갈리므로 sync 는 둘 다 실값으로 병기한다. 로딩·부재·비활성은
  // "—" 로 뭉개지 않고 그대로 말한다 — null(모름)≠0≠실패. 비활성 문구의 근거:
  // resolve_fanout 이 PlacementError("policy_disabled") 로 잡 배치를 거부한다.
  const fmtPolicy = (p: Policy | undefined) => p === undefined ? "미조회"
    : `최대 ${p.max_nodes}노드 · 노드당 ${p.procs_per_node}프로세스${
        p.enabled === 1 ? "" : " · 비활성(잡 배치 거부)"}`;
  // 도구→정책 행 인덱스 — 노드 수 캡션과 우선순위 라벨·상한 캡션이 공유한다
  // (중복 조회 금지). undefined = 미조회(로딩·실패) — 거짓값 금지, null≠0.
  const byTool = policiesQ.data === undefined
    ? undefined : new Map(policiesQ.data.map((p) => [p.tool, p]));
  const policyCaption = (() => {
    if (policiesQ.isLoading) return "정책 조회 중…";
    if (byTool === undefined) return "정책 미조회 — 정책 목록을 불러오지 못했습니다";
    if (f.op !== "sync") {
      const p = byTool.get(f.op);               // scan→"scan", rm→"rm" — 도구 하나라 실값 하나
      return p === undefined ? `정책 미조회 — ${f.op} 정책 행이 없습니다`
        : `정책 기본: ${fmtPolicy(p)}`;
    }
    const dsync = byTool.get("dsync");
    const nsync = byTool.get("nsync");
    if (dsync === undefined && nsync === undefined)
      return "정책 미조회 — dsync/nsync 정책 행이 없습니다";
    return `정책 기본(dsync): ${fmtPolicy(dsync)} — 공존 노드가 없으면 `
      + `nsync 정책(${fmtPolicy(nsync)})이 적용됩니다`;
  })();
  // 캡션이 거부 경고(조회 실패·비활성·행 부재)를 담으면 「자세히」에 접지 않고 펼친 채 둔다(optionRules.policyCaptionAlert --
  // 단계별 등록 뒤 지적: 옛 한 장 시트는 캡션이라 늘 보였는데 요약 + 자세히로 접히자 비활성 정책을 모른 채 만들 수 있었다).
  const policyAlert = policyCaptionAlert(byTool, f.op, policiesQ.isLoading);

  // --- 우선순위: 정책 기본값 실값 + 상한 캡션 ---
  // 기본 라벨은 resolve_priority(domain.py _OP_POLICY) 미러 — scan→"scan"·rm→"rm" 정책, sync 는
  // 제출 시점에 도구(dsync/nsync)가 정해지지 않으므로 dsync 정책을 대표로 읽는다.
  // 미조회·행 부재면 실값 없이 "(정책 기본)" — 거짓값 금지(null≠0).
  const defaultPolicy = byTool?.get(f.op === "sync" ? "dsync" : f.op);
  const priorityDefaultLabel = defaultPolicy === undefined
    ? "(정책 기본)" : `(정책 기본: ${defaultPolicy.default_priority})`;

  // 우선순위 캡션 — 상한부는 _clamp_priority(placement.py) 미러: fanout 이 상한
  // 초과 선택을 **조용히** 상한으로 누르는 침묵을 화면이 미리 말한다. sync 는
  // 도구별 묶음(기본+상한)으로 nsync 기본값도 **표기**하되, 기본값 **적용**은
  // dsync 정책 단독(resolve_priority — 제출 시점 도구 미정이라 dsync 대표.
  // nsync 폴백 실행이어도 요청에 박힌 기본값은 dsync 것)이고 nsync 는 상한만
  // 실행 도구 기준으로 실제 적용됨을 문장으로 명시한다(화면 거짓말 금지 —
  // 제출 바디=요약 동일 함수 관례와 같은 정직성 규약). 미조회·행 부재는 거짓값
  // 대신 캡션 생략(null≠0).
  const priorityCapCaption = (() => {
    if (byTool === undefined) return null;
    const tools = f.op === "sync" ? ["dsync", "nsync"] : [f.op];
    const rows = tools.flatMap((tool) => {
      const p = byTool.get(tool);
      return p === undefined ? []
        : [{ tool, def: p.default_priority, cap: p.max_priority }];
    });
    if (rows.length === 0) return null;
    // 초과 선택 즉답 — 오류가 아니라 조정 예고이므로 톤(text-muted)은 유지하되
    // 실값으로 구체화한다. ""(정책 기본)는 rank -1 이라 절대 초과가 아니다.
    const over = rows.filter((r) =>
      (PRIORITY_RANK[f.priority] ?? -1) > (PRIORITY_RANK[r.cap] ?? Infinity));
    const overValues = [...new Set(over.map((r) => r.cap))];
    const overTail = over.length === rows.length && overValues.length === 1
      ? `선택한 ${f.priority}는 상한 ${overValues[0]}로 조정됩니다`
      // 한쪽 상한만 넘거나 두 상한이 다른 경우 — 조정은 실제 배치 도구의
      // 정책으로만 일어나므로 넘는 도구만 정직하게 말한다.
      : `선택한 ${f.priority}는 배치 시 ${
          over.map((r) => `${r.tool} 상한 ${r.cap}`).join(" · ")}로 조정됩니다`;
    if (f.op !== "sync")
      return `정책 상한: ${rows[0].cap} — ${over.length === 0
        ? "상한을 넘는 선택은 배치 시 상한으로 조정됩니다" : overTail}`;
    // sync: 기본·상한이 전부 같으면 한 묶음으로 축약(같은 값 반복은 소음).
    const allSame = rows.length > 1
      && rows.every((r) => r.def === rows[0].def && r.cap === rows[0].cap);
    const head = allSame
      ? `${rows.map((r) => r.tool).join("·")}: 기본 ${rows[0].def} · 상한 ${rows[0].cap}`
      : rows.map((r) => `${r.tool}: 기본 ${r.def} · 상한 ${r.cap}`).join(" / ");
    return `정책 — ${head}. 기본값 적용은 dsync 기준(제출 시점 도구 미정), ${
      over.length === 0 ? "상한은 실행 도구 기준으로 조정됩니다" : overTail}`;
  })();

  function buildOptions(): Record<string, unknown> {
    const options: Record<string, unknown> = {};
    if (f.op === "rm") {
      // recursive 는 늘 명시로 싣는다(서버 기본 없음 — 동의 게이트). 끄면 위 즉답이 제출을 막는다.
      if (f.recursive) options.recursive = true;
      if (f.stat) options.stat = true;
      if (f.lite) options.lite = true;
      if (f.quiet) options.quiet = true;
      return options;
    }
    if (f.op === "scan") {
      if (f.verbose) options.verbose = true;
      if (f.quiet) options.quiet = true;
      // 빈값 = 키 생략 = 서버 기본(domain._OPTION_DEFAULTS, 프리필과 같은 값). "0"은
      // 정상 입력(배칭 끔)이라 생략과 다르다(null≠0).
      if (f.scanBatchFiles.trim() !== "")
        options.batch_files = Number(f.scanBatchFiles.trim());
      if (f.brokenLimit.trim() !== "")
        options.broken_limit = Number(f.brokenLimit.trim());
      return options;
    }
    if (f.delete) options.delete = true;
    if (f.contents) options.contents = true;
    if (f.direct) options.direct = true;
    if (f.quiet) options.quiet = true;
    if (f.openNoatime) options.open_noatime = true;
    if (f.batchFiles.trim() !== "") options.batch_files = Number(f.batchFiles.trim());
    if (f.bufsize.trim() !== "") options.bufsize = Number(f.bufsize.trim());
    if (f.chmod.trim() !== "") options.chmod = f.chmod.trim();
    if (f.chown.trim() !== "") options.chown = f.chown.trim();
    return options;
  }

  // 제출 바디와 검토 카드가 **같은 함수**에서 파생 — 화면 따로 바디 따로면
  // "검토와 다른 것이 제출되는" 화면 거짓말이 생긴다(SubmitJob 관례).
  function buildBody(): CreateBatchBody {
    const items = f.op === "sync"
      ? rows.map((r) => ({ source_storage: f.srcStorage, source: r.a.trim(),
                           destination_storage: f.dstStorage, destination: r.b.trim() }))
      : rows.map((r) => ({ storage: f.storage, target: r.a.trim() }));        // scan·rm
    return {
      operation: f.op, max_concurrency: Number(f.mc), options: buildOptions(),
      note: f.note || null, items,
      // 배치 이름: 빈값은 키 생략 = 서버 NULL(이름 없음) — 아래 생략 계약의 미러.
      ...(f.name.trim() !== "" && { name: f.name.trim() }),
      // 미지정("")은 키 자체를 생략한다 — 서버 NULL(정책 기본), null≠0.
      ...(f.priority !== "" && { priority: f.priority }),
      ...(f.nodeCount.trim() !== "" && { node_count: Number(f.nodeCount.trim()) }),
      ...(f.procsPerNode.trim() !== ""
          && { procs_per_node: Number(f.procsPerNode.trim()) }),
      // 실행 신원: 빈값은 키 생략 = 서버 NULL(기본: 생성자 본인) — 특권 게이트는
      // owner 유무와 무관하게 항상 발동한다(통일 게이트).
      ...(f.ownerUsername.trim() !== "" && { owner_username: f.ownerUsername.trim() }),
      // 자동 확인은 sync·rm 만(scan 은 확인 단계가 없다 — 서버 422) — 연산을 바꾸면 체크가 남아 있어도 싣지 않는다.
      ...(autoConfirmable(f.op) && f.autoConfirm && { auto_confirm: true as const }),
      // rm 은 동의 체크까지 켰을 때만 동의를 싣는다(체크 전에는 위 rmAutoAckMissing 이 제출을 막는다).
      ...(f.op === "rm" && f.autoConfirm && f.autoConfirmAck && { acknowledge_irreversible: true as const }),
    };
  }

  function applyParsed(text: string) {
    const { rows: parsed, errors } = parseItemsCsv(f.op, text);
    setCsvErrors(errors);
    if (errors.length > 0) return;   // 오류가 있으면 rows 를 덮지 않는다(부분 반영 금지)
    setRows(parsed.map((r) => f.op === "sync"
      ? { a: (r as SyncRow).source, b: (r as SyncRow).destination }
      : { a: (r as ScanRow).target, b: "" }));
    setTab("table");                 // 성공 반영 → 결과(테이블)를 바로 보인다
  }

  function handleFile(file: File | undefined) {
    if (!file) return;
    // airgap 무관 — FileReader 는 로컬 읽기만 한다(외부 fetch 아님).
    const reader = new FileReader();
    reader.onload = () => applyParsed(String(reader.result ?? ""));
    reader.readAsText(file);
  }

  function downloadCsv() {
    // 클립보드 API 금지: 운영 포탈은 http(비 localhost) 비보안 컨텍스트라
    // 브라우저 클립보드 API 가 부재한다 — Blob 다운로드는 어디서나 동작한다.
    const text = serializeItemsCsv(f.op, f.op === "sync"
      ? rows.map((r) => ({ source: r.a.trim(), destination: r.b.trim() }))
      : rows.map((r) => ({ target: r.a.trim() })));
    const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "batch-items.csv";
    anchor.click();
    URL.revokeObjectURL(url);
  }

  const setRow = (i: number, k: keyof RowPair, v: string) => {
    setRows(rows.map((r, j) => (j === i ? { ...r, [k]: v } : r)));
    setCsvErrors([]);                // 수동 편집 = 사용자가 인수 — 파스 오류는 무효
  };

  // 행 삭제 뒤 포커스(dms-k8s design-wizard §1-6): 같은 번호 행의 삭제 버튼, 없으면 이전 행, 행이 없으면 「행 추가」 -- 예전엔 지운
  // 버튼과 함께 포커스가 <body> 로 떨어졌다.
  const deleteFocus = useRef<number | null>(null);
  const deleteRow = (i: number) => {
    deleteFocus.current = i;
    setRows(rows.filter((_, j) => j !== i));
    setCsvErrors([]);
  };
  useEffect(() => {
    const i = deleteFocus.current;
    if (i === null) return;
    deleteFocus.current = null;
    const card = document.querySelector("[data-wizard-card]");
    const label = rows.length === 0 ? "행 추가" : `${Math.min(i, rows.length - 1) + 1}행 삭제`;
    card?.querySelector<HTMLElement>(`[aria-label="${label}"]`)?.focus();
  }, [rows]);

  // 입력 방식 전환(세그먼트). 역할은 버튼 그대로(aria-pressed) -- 세 방식은 같은 rows 를 고치는
  // 도구라 탭 패널 구조가 아니고, CSV 반영 성공이 표로 돌아오는 동선(applyParsed)이 있다.
  const segment = (t: InputTab, label: string) => (
    <button type="button" aria-pressed={tab === t} onClick={() => setTab(t)}
            className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm sm:px-3 ${tab === t
              ? "bg-surface font-medium text-ink shadow-soft" : "text-muted hover:text-ink"}`}>
      {label}
    </button>
  );

  const rowCols = f.op === "sync"
    ? "grid-cols-[2rem_minmax(0,1fr)_minmax(0,1fr)_2rem]"
    : "grid-cols-[2rem_minmax(0,1fr)_2rem]";

  // 생성이 잠긴 이유(검토 단계 바, 버튼 위) -- 필드 옆 빨간 문구와 다른 문장으로 짧게(SubmitHints 주석). 옛 hints 와 같은 배열이고
  // (batchGates ③), 각 단계 바에는 그 단계 태그의 첫 줄이 보인다(gates.barReason).
  const hints = gates.hints;

  const body = buildBody();   // 검토 = 제출 바디에서 파생(화면 거짓말 금지)

  const storageText = f.op !== "sync" ? (f.storage === "" ? "—" : f.storage)
    : `${f.srcStorage === "" ? "—" : f.srcStorage} → ${f.dstStorage === "" ? "—" : f.dstStorage}`;
  // 목적지 소유(② 목적지 한 줄·⑤ 검토 note) -- 배치는 root(생성 게이트 + IF_ELIGIBLE)라 chown 미지정이면 소스 그대로.
  const ownership = syncOwnership({ chown: f.chown, chmod: f.chmod, root: true, runAs: null, self: true });

  // ---- 단계별 등록(dms-k8s design-wizard §1) ---------------------------------------------------------------------------------
  // 단계 상태·URL(?step=)·Enter·포커스·도달 규칙은 useWizard(규칙 전문은 components/form/Wizard 머리 주석). 단계 본문은 이 파일의 지역
  // render 함수다(수십 개의 파생값을 읽는다 -- 따로 떼면 props 가 폭발하고 「로직 그대로」를 확인하기 어렵다).
  // 스테퍼 아래 값(방문했거나 지난 단계만, 넓은 폭에서 보인다).
  const storageValue = f.op === "sync"
    ? (f.srcStorage === "" && f.dstStorage === "" ? "미선택" : `${f.srcStorage || "?"} → ${f.dstStorage || "?"}`)
    : f.storage || "미선택";
  const steps: WizardStep<BatchStep>[] = [
    { key: "kind", title: "작업 종류", blocked: gates.gate.kind, reason: gates.barReason("kind"), value: f.op },
    { key: "storage", title: "스토리지", blocked: gates.gate.storage, reason: gates.barReason("storage"), value: storageValue },
    { key: "items", title: "항목", blocked: gates.gate.items, reason: gates.barReason("items"), value: `${rows.length}행` },
    { key: "controls", title: "옵션·실행 제어", blocked: gates.gate.controls, reason: gates.barReason("controls"),
      value: `동시 ${f.mc.trim() || "?"}${autoConfirmable(f.op) ? ` · ${f.autoConfirm ? "자동 실행" : "직접 확인"}` : ""}` },
    // 마지막 단계의 blocked 는 「다음」이 아니라 최종 생성 게이트(= blocked)다.
    { key: "review", title: "이름·검토", blocked, reason: gates.barReason("review"), value: f.name.trim() || "이름 없음" },
  ];
  const wizard = useWizard<BatchStep>({ steps, initial: "kind" });

  // 생성 방어(design-wizard §1-6 「제출 방어」): ① 더블클릭 -- 「다음」과 「배치 생성」은 바의 같은 자리·같은 노드라 ④에서 「다음」을
  // 두 번 누르면 두 번째 클릭이 막 그려진 「배치 생성」에 떨어진다. 같은 연속 클릭의 두 번째 이후(e.detail > 1)를 무시한다(바도 같은
  // 줄을 둔다 -- 둘 다 있어도 무해). ② 단계 가드 -- 검토를 안 보고 새어 나온 호출은 current !== "review" 로 막는다. <form> 이 없다:
  // 경로·메모 입력의 Enter 는 셸이 처리하고(다음 단계일 뿐, 마지막 단계에선 아무것도 안 한다) 생성은 이 버튼 onClick 하나다.
  function handleSubmit(e: MouseEvent<HTMLButtonElement>) {
    if (e.detail > 1) return;
    if (blocked || wizard.current !== "review") return;
    create.mutate(buildBody(), { onSuccess: (r) => nav(`/admin/batches/${r.batch_id}`) });
  }

  // 연산을 바꾸면 rm 동의는 늘 풀리고, rm 으로 들어올 때는 자동 실행도 꺼진 채 시작한다 — 다른 연산에서 켠 자동 실행을 되돌릴 수
  // 없는 삭제가 조용히 물려받지 않게(sync↔scan 은 종전대로 체크를 남기고 싣지만 않는다).
  const chooseOp = (op: BatchOp) =>
    setF({ ...f, op, autoConfirmAck: false, ...(op === "rm" && f.op !== "rm" && { autoConfirm: false }) });

  // ① 작업 종류 -- 연산 카드형 라디오(접근 이름 「연산」·라디오 이름 scan·sync·rm 은 옛 select 와 같다). 설명은 OPERATION_HELP 원문,
  // 고른 카드만 자동 실행이 켜져 있으면 AUTO_HELP(「눌러야 실행·삭제」가 거짓이 된다). 부제는 OPERATION_LABEL_BATCH(optionRules).
  const kindStep = () => (
    <div className="space-y-2">
      <p aria-hidden className="text-sm font-semibold text-ink">연산</p>
      <OperationCards name="batch-operation" value={f.op} onChange={chooseOp}
                      options={(["scan", "sync", "rm"] as const).map((o) => ({
                        value: o, subtitle: OPERATION_LABEL_BATCH[o], danger: o === "rm",
                        description: o === f.op && f.autoConfirm && AUTO_HELP[o] !== undefined ? AUTO_HELP[o] : OPERATION_HELP[o],
                      }))} />
    </div>
  );

  // 삭제 범위 bullet(무엇이·누구 권한으로·언제) -- ② 요약 note 와 ⑤ 전문 note 가 같은 문장을 쓴다. 지우는 순간은 배치 상세의
  // 「배치 확인」이라 강한 마찰(체크)은 거기 두고, 여기는 정보와 즉답만 둔다. 방향어는 단계 이름이다(「아래」는 단계로 나뉜 뒤 거짓).
  const rmWhenBullet = f.autoConfirm
    ? <li>자동 실행이 켜져 있습니다 — 항목별 미리보기(지울 항목 수)가 끝나는 즉시 「배치 확인」 없이 삭제됩니다(④ 옵션·실행 제어).</li>
    : <li>만들기만 해서는 아무것도 지워지지 않습니다 — 항목별 미리보기(지울 항목 수)가 끝나면 배치 상세의 「배치 확인」에서
        검토하고 확인해야 삭제됩니다.</li>;
  const rmScopeHead = <li>각 행의 경로와 그 아래 파일·디렉토리를 모두 삭제합니다(경로 자신이 심볼릭 링크면 링크만) — 되돌릴 수
    없습니다.</li>;
  const rmScopeRoot = <li>관리자 특권(root)으로 실행돼 파일 권한·소유와 관계없이 지워집니다.</li>;
  const linkButton = "font-semibold text-accent underline underline-offset-2 hover:text-accenthover";

  // ② 스토리지 -- 라디오 카드 묶음(BatchStorageCards, 묶음마다 다른 라디오 name).
  const storageStep = () => (
    <>
      {storagesQ.isError && (
        <p className="text-bad text-sm">{(storagesQ.error as ApiError).message}</p>
      )}
      {f.op !== "sync" ? (
        <BatchStorageCards label="스토리지" name="batch-storage-single" value={f.storage}
                           onChange={(v) => setF({ ...f, storage: v })} storages={storages}
                           loading={loadingStorages} error={storagesQ.isError} />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
          <BatchStorageCards label="소스 스토리지" name="batch-storage-src" value={f.srcStorage}
                             onChange={(v) => setF({ ...f, srcStorage: v })} storages={storages}
                             loading={loadingStorages} error={storagesQ.isError} />
          <ArrowRight aria-hidden className="h-5 w-5 rotate-90 justify-self-center text-muted lg:mt-10 lg:rotate-0" />
          <BatchStorageCards label="목적지 스토리지" name="batch-storage-dst" value={f.dstStorage}
                             onChange={(v) => setF({ ...f, dstStorage: v })} storages={storages}
                             loading={loadingStorages} error={storagesQ.isError} />
        </div>
      )}
      {f.op === "rm" && (
        <div className="rounded-card border border-bad/30 bg-badbg p-3 text-xs text-bad" role="note" aria-label="삭제 범위 요약">
          <p className="text-sm font-medium">삭제 범위 <span className="font-normal">— 전문은 ⑤ 검토에서</span></p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">{rmScopeHead}{rmScopeRoot}{rmWhenBullet}</ul>
        </div>
      )}
      {/* sync 목적지 한 줄(전문 「목적지 조건과 소유권」은 ⑤ 검토 -- 원문 앞부분 + 결과 소유 short). */}
      {f.op === "sync" && f.dstStorage !== "" && (
        <p className="rounded-lg bg-infobg px-3 py-2 text-xs text-ink">
          각 목적지의 상위 디렉토리는 이미 있어야 합니다 · 결과 소유: {ownership.short} · 전문은 ⑤ 검토에서
        </p>
      )}
    </>
  );

  // ③ 항목 -- 입력 방식 세그먼트 + (보조 동작) CSV 다운로드 / 본문 / 행 수. 행 칸의 data-row·data-col 은 셸의 Enter 규칙(같은 열의
  // 다음 행 칸으로, 마지막 행이면 다음 단계)이 읽는다.
  const itemsStep = () => (
    <>
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted">
        <span className="rounded-full border border-line bg-panel px-2 py-0.5 text-ink [overflow-wrap:anywhere]">{storageText}</span>
        <span>관리 디렉토리 아래 상대경로로 적습니다.</span>
        <button type="button" className={linkButton} onClick={() => wizard.go("storage")}>
          <span aria-hidden>스토리지 수정</span><span className="sr-only">수정: 스토리지</span>
        </button>
      </p>
      <div className="min-w-0 rounded-lg border border-line">
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
          {/* 좁은 화면에선 세그먼트가 음절 중간에서 접히지 않고 통째로 다음 줄로 넘어간다. */}
          <div className="inline-flex flex-wrap gap-0.5 rounded-lg bg-panel p-0.5">
            {segment("table", "테이블 편집")}
            {segment("paste", "CSV 붙여넣기")}
            {segment("upload", "파일 업로드")}
          </div>
          <Button type="button" variant="ghost" className="gap-1.5" onClick={downloadCsv}>
            <Download className="h-4 w-4" aria-hidden />CSV 다운로드
          </Button>
        </div>
        <div className="p-3">
          {tab === "table" && (
            <div className="space-y-2">
              <div className={`grid ${rowCols} gap-2 text-xs text-muted`} aria-hidden>
                <span className="text-right">#</span>
                <span>{f.op === "sync" ? "소스 경로" : f.op === "rm" ? "삭제할 경로" : "경로"}</span>
                {f.op === "sync" && <span>목적지 경로</span>}
                <span />
              </div>
              {/* 수백 행(CSV)이 들어와도 바가 화면 아래로 묻히지 않게 목록만 스크롤한다. */}
              <div className="max-h-[28rem] space-y-2 overflow-y-auto">
                {rows.map((r, i) => (
                  <div key={i} className={`grid ${rowCols} items-center gap-2`}>
                    <span className="text-right text-xs tabular-nums text-muted">{i + 1}</span>
                    {/* placeholder 예시는 스토리지 기준 상대경로(선행 슬래시 없음) —
                        경로가 스토리지 루트 기준임을 힌트로 드러낸다 */}
                    {/* 막힌 「다음」의 포커스 자리(Wizard blockedNudge -- 목록이 스크롤 상자라 틀린 행이 화면 밖일 수 있다): rm 의 형식
                        오류·겹침 행은 aria-invalid, 그 열의 스토리지를 골랐는데 빈 칸은 data-wizard-nudge(빨갛게 칠하지 않는 「아직 비어
                        있는 필수 칸」 -- SubmitJob pathInput 과 같은 규칙). */}
                    <input aria-label={`${i + 1}행 ${f.op === "sync" ? "소스" : "경로"}`} data-row={i} data-col="a"
                           placeholder={f.op === "sync" ? "예: team/dataset"
                             : f.op === "rm" ? "예: team/old-project" : "예: team/projects"}
                           aria-invalid={rmInvalidRows.has(i) || undefined}
                           data-wizard-nudge={((f.op === "sync" ? f.srcStorage : f.storage) !== "" && r.a.trim() === "")
                             || undefined}
                           className={cell} value={r.a}
                           onChange={(e) => setRow(i, "a", e.target.value)} />
                    {f.op === "sync" && (
                      <input aria-label={`${i + 1}행 목적지`} placeholder="예: backup/dataset" data-row={i} data-col="b"
                             data-wizard-nudge={(f.dstStorage !== "" && r.b.trim() === "") || undefined}
                             className={cell} value={r.b}
                             onChange={(e) => setRow(i, "b", e.target.value)} />
                    )}
                    <button type="button" aria-label={`${i + 1}행 삭제`}
                            className="grid h-8 w-8 place-items-center rounded-lg text-muted hover:bg-panel hover:text-bad"
                            onClick={() => deleteRow(i)}>
                      <X className="h-4 w-4" aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
              <Button type="button" variant="outline" aria-label="행 추가" className="gap-1.5"
                      onClick={() => setRows([...rows, { a: "", b: "" }])}>
                <Plus className="h-4 w-4" aria-hidden />행 추가
              </Button>
            </div>
          )}

          {tab === "paste" && (
            <div className="space-y-2">
              <label className="text-sm block">
                CSV ({f.op === "sync" ? "행당 source,destination" : "행당 경로 1개"})
                <textarea aria-label="CSV" className={`${field} h-40 font-mono`}
                          placeholder={f.op === "sync"
                            ? "team/dataset,backup/dataset\nprojects/alpha,backup/alpha"
                            : "team\nprojects/alpha"}
                          value={csvText} onChange={(e) => setCsvText(e.target.value)} />
              </label>
              <p className="text-xs text-muted">{HELP_SUMMARY.csvApply}</p>
              <Button type="button" variant="outline"
                      onClick={() => applyParsed(csvText)}>테이블에 반영</Button>
            </div>
          )}

          {tab === "upload" && (
            <label className="text-sm block">CSV 파일
              <input aria-label="CSV 파일" type="file" accept=".csv,.txt" className={field}
                     onChange={(e) => handleFile(e.target.files?.[0])} />
            </label>
          )}

          {csvErrors.length > 0 && (
            <ul className="mt-3 space-y-1 text-bad text-sm">
              {csvErrors.map((err, i) => <li key={i}>{err}</li>)}
            </ul>
          )}
          {rmPathErrors.length > 0 && (
            <ul aria-label="삭제할 수 없는 경로" className="mt-3 space-y-1 text-bad text-sm">
              {rmPathErrors.map((err) => <li key={err.row}>{err.message}</li>)}
            </ul>
          )}
          {rmOverlapErrors.length > 0 && (
            <ul aria-label="겹치는 삭제 경로" className="mt-3 space-y-1 text-bad text-sm">
              {rmOverlapErrors.map((err) => <li key={err.at}>{err.message}</li>)}
            </ul>
          )}
        </div>
        <div className="border-t border-line px-3 py-2 text-sm text-muted">행: {rows.length}</div>
      </div>
    </>
  );

  // scan 숫자 칸(④). 프리필 = 서버 기본(domain._OPTION_DEFAULTS, 2026-09-17). 비우면 키가 빠지고 서버가 같은 값을 박는다 --
  // placeholder 는 그 사실을 말한다. aria-invalid 는 막힌 「다음」이 포커스를 보낼 자리(Wizard blockedNudge).
  const scanFields = (
    <>
      <FieldRow
        control={<>
          <label className="text-sm block">batch_files (선택 · 0..1,000,000,000 · 0 = 배칭 끔)
            <input aria-label="batch_files" type="number" min={0} max={1000000000}
                   aria-invalid={scanBatchFilesError !== null || undefined}
                   placeholder="비우면 기본 1,000,000 적용 · 0 = 배칭 끔"
                   className={field} value={f.scanBatchFiles}
                   onChange={on("scanBatchFiles")} />
          </label>
          {scanBatchFilesError && <p className="mt-1 text-bad text-sm">{scanBatchFilesError}</p>}
        </>}
        help="미리 채운 1,000,000 = 서버 기본. 비워도 같은 값이 적용됩니다." />
      <FieldRow
        control={<>
          <label className="text-sm block">broken_limit (선택 · 0..10,000 · 리포트에 보관할 파손 경로 수)
            <input aria-label="broken_limit" type="number" min={0} max={10000}
                   aria-invalid={brokenLimitError !== null || undefined}
                   placeholder="비우면 기본 100 적용"
                   className={field} value={f.brokenLimit}
                   onChange={on("brokenLimit")} />
          </label>
          {brokenLimitError && <p className="mt-1 text-bad text-sm">{brokenLimitError}</p>}
        </>}
        help="미리 채운 100 = 서버 기본. 비워도 같은 값이 적용됩니다." />
    </>
  );

  // sync 고급 옵션(④). 접혀 있어도 내용은 마운트돼 있다 -- open_noatime 기본 ON 이 펼치지 않아도 실린다. 오류면 강제로 펼치는
  // 로직·closest("details") 시험을 그대로 쓴다.
  const advancedPanel = (
    <details className="rounded-xl border border-line"
             open={advancedOpen || advancedError !== null}
             onToggle={(e) => {
               const el = e.currentTarget;
               // 오류가 있는 동안은 접히지 않는다(제출이 잠긴 이유가 숨지 않게) -- SubmitJob 과 같은 이유.
               if (!el.open && advancedError !== null) { el.open = true; return; }
               setAdvancedOpen(el.open);
             }}>
      <summary className="cursor-pointer select-none px-4 py-3 text-sm font-medium">고급 옵션</summary>
      <div className="space-y-4 border-t border-line px-4 py-4">
        {/* 기본 ON 의 「왜」는 initial 의 openNoatime 주석 — 캡션은
            사용자에게 같은 근거를 요약해 준다(끄는 건 자유). */}
        <FieldRow align="check"
          control={<label className="flex items-center gap-2 text-sm">
            <input type="checkbox" aria-label="open_noatime" checked={f.openNoatime}
                   onChange={on("openNoatime")} /> open_noatime
          </label>}
          help={BATCH_OPEN_NOATIME_HELP} />
        {/* 프리필 계약(단건 폼과 동일 문구, 2026-09-17): 값이 미리 채워져
            있고 그 값이 곧 서버 기본이라 비워도 같은 값이 적용된다 — 배칭을
            끄는 유일한 표현은 0 명시. placeholder 는 "비웠을 때", 캡션은
            "지금 채워진 값"을 말한다. */}
        <FieldRow
          control={<>
            <label className="text-sm block">batch_files (선택 · 0..10,000,000)
              <input aria-label="batch_files" aria-invalid={batchFilesError !== null || undefined}
                     placeholder="비우면 기본 1,000,000 적용 · 0 = 배칭 끔"
                     className={field} value={f.batchFiles}
                     onChange={on("batchFiles")} />
            </label>
            {batchFilesError && <p className="mt-1 text-bad text-sm">{batchFilesError}</p>}
          </>}
          help="미리 채운 1,000,000 = 서버 기본 배치 사이즈. 비워도 같은 값이 적용되며, 배칭을 끄려면 0 을 입력하세요." />
        <FieldRow
          control={<>
            <label className="text-sm block">bufsize (선택 · 바이트, 4096..1,073,741,824)
              <input aria-label="bufsize" placeholder="비우면 기본 4 MiB 적용" aria-invalid={bufsizeError !== null || undefined}
                     className={field} value={f.bufsize}
                     onChange={on("bufsize")} />
            </label>
            {bufsizeError && <p className="mt-1 text-bad text-sm">{bufsizeError}</p>}
          </>}
          help={BUFSIZE_HELP} />
        <FieldRow
          control={<>
            <label className="text-sm block">chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)
              <input aria-label="chmod" placeholder="예: D770,F660" aria-invalid={chmodError !== null || undefined}
                     className={field} value={f.chmod} onChange={on("chmod")} />
            </label>
            {chmodError && <p className="mt-1 text-bad text-sm">{chmodError}</p>}
          </>} />
        {/* 배치는 통일 게이트로 전부 특권(root) 실행 — 단건(SubmitJob)의
            비특권 함정 캡션은 여기선 거짓이라 싣지 않는다. 특권 실행의
            "비우면" 기본은 소스 소유권 보존(_auto_chown 무개입 분기). */}
        <FieldRow
          control={<>
            <label className="text-sm block">chown (선택 · 숫자 uid:gid)
              <input aria-label="chown" placeholder="예: 10003:10000" aria-invalid={chownError !== null || undefined}
                     className={field} value={f.chown} onChange={on("chown")} />
            </label>
            {chownError && <p className="mt-1 text-bad text-sm">{chownError}</p>}
          </>}
          help="비우면 원래(소스) 소유권을 보존합니다(root 실행). 지정하면 목적지와 복사본이 그 소유로 셋업됩니다 — 숫자 uid:gid 로 지정하세요." />
      </div>
    </details>
  );

  // 실행 제어 칸(④). 긴 캡션(실행 신원 도움말·정책·CPU 상한·nsync 면당)은 요약 + 자세히(MoreText -- 전문은 <p>, 칸 오류는 늘 보임).
  const controlFields = (
    <>
      <FieldRow
        control={<label className="text-sm block">우선순위
          <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
            <option value="">{priorityDefaultLabel}</option>
            <option value="low">low</option>
            <option value="mid">mid</option>
            <option value="high">high</option>
          </select>
        </label>}
        help={priorityCapCaption} />
      {/* 라벨 정정(사용자 결정 2026-08-16): 이 값은 결과물의 소유자 기록이
          아니라 **잡의 실행 신원**이다(identity.resolve_job_identity —
          owner = owner_username or requester_id). 2026-09-30 정정: 예전 "지정하면
          그 사용자 신원으로 파일을 다룹니다" 는 사실이 아니었다 -- 배치 자식은
          생성자가 특권 요청자면 root 로 돌고(identity.PRIVILEGE_IF_ELIGIBLE), 실행
          신원은 기록용 이름일 뿐 파일 권한 검사는 적용되지 않는다(sync 는 목적지
          소유·권한을 소스에 맞춘다). 요약(HELP_SUMMARY.runAs)은 배치 전용 문장이다 -- 단일 작업 화면에 쓰지 않는다. */}
      <div className="space-y-1">
        <label className="text-sm block">실행 신원(선택)
          <input aria-label="실행 신원(선택)" placeholder="예: cocoa.song"
                 className={field}
                 value={f.ownerUsername} onChange={on("ownerUsername")} />
        </label>
        <MoreText summary={HELP_SUMMARY.runAs} full={f.op === "rm" ? RUN_AS_HELP_RM : RUN_AS_HELP} />
      </div>
      {/* 잡 하나의 폭(노드 수 × 노드당 프로세스) -- 정책 실값 캡션은 두 칸을 함께 설명한다.
          라벨 길이가 달라도 입력 상자가 한 줄에 서도록 아래 정렬(items-end), 오류는 그 아래. */}
      <div className="space-y-1">
        <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2 sm:items-end">
          <label className="text-sm block">노드 수 (선택 · 1..64, 빈값 = 정책 기본)
            <input aria-label="노드 수" type="number" min={1} max={64} className={field}
                   aria-invalid={nodeCountError !== null || undefined}
                   placeholder="비우면 정책 기본"
                   value={f.nodeCount} onChange={on("nodeCount")} />
          </label>
          {/* 요청은 정책을 줄일 수만 있다(planner min-캡) — 늘리려면 정책 수정 */}
          <label className="text-sm block">노드당 프로세스 수 (선택 · 1..64, 빈값 = 정책 기본)
            <input aria-label="노드당 프로세스 수" type="number" min={1} max={64}
                   aria-invalid={procsPerNodeError !== null || undefined}
                   className={field} placeholder="비우면 정책 기본"
                   value={f.procsPerNode} onChange={on("procsPerNode")} />
          </label>
        </div>
        {nodeCountError && <p className="text-bad text-sm">{nodeCountError}</p>}
        {procsPerNodeError && <p className="text-bad text-sm">{procsPerNodeError}</p>}
        {/* 정책 실값 캡션 + 노드 CPU 상한(2026-10-16: 위 두 칸의 값은 노드가 담을 수 있는 만큼까지만 -- src/dms/node_cpu.py) + sync 의
            nsync 면당 문장 -- 요약 한 줄 뒤 자세히. 캡션이 거부 경고면 펼친 채 고정한다(policyAlert). */}
        <MoreText summary={HELP_SUMMARY.controlsPolicy} forceOpen={policyAlert}
                  full={f.op === "sync" ? [policyCaption, NODE_CPU_CAP_NOTE, NSYNC_PER_SIDE_NOTE] : [policyCaption, NODE_CPU_CAP_NOTE]} />
      </div>
      {/* 노드 수와 대구: 위는 잡 하나의 폭, 이것은 잡 몇 개를 나란히 */}
      <FieldRow
        control={<>
          <label className="text-sm block">동시 실행 상한 (1..64)
            <input aria-label="동시 실행 상한" type="number" min={1} max={64} className={field}
                   aria-invalid={mcError !== null || undefined}
                   placeholder={`예: ${DEFAULT_MAX_CONCURRENCY}`}
                   value={f.mc} onChange={(e) => setF({ ...f, mc: e.target.value })} />
          </label>
          {mcError && <p className="mt-1 text-bad text-sm">{mcError}</p>}
        </>}
        help="동시에 실행할 배치 항목(잡) 수 — 잡 하나가 쓰는 노드 수와 무관합니다." />
      {/* 자동 확인(sync 2026-10-11, rm 2026-10-21): 기본 꺼짐. 켜면 운영자가 미리보기를 보고 누르던 「배치 확인」을
          시스템이 대신 누른다(서버 orchestrator — 같은 확인 함수라 확인 회차·도장 규칙은 그대로). 실행은 root 라 무엇을
          생략하는지를 도움말이 그대로 말한다(켜면 전문이 펼쳐진다). 진행 중에도 배치 화면의 「자동 실행 끄기」로 되돌릴 수 있다. */}
      {autoConfirmable(f.op) && (
        <OptionToggle label="미리보기 후 자동 실행" checked={f.autoConfirm} danger={f.op === "rm"}
                      onChange={(e) => setF({ ...f, autoConfirm: e.target.checked, autoConfirmAck: false })}
                      summary={f.op === "rm" ? AUTO_RUN_SUMMARY_RM : AUTO_RUN_SUMMARY}
                      full={f.op === "rm" ? AUTO_RUN_HELP_RM : AUTO_RUN_HELP} />
      )}
      {/* rm 자동 실행(2026-10-21): 되돌릴 수 없는 root 삭제를 사람 확인 없이 하는 설정이라, 켜는 순간 경고와 **필수**
          동의 체크를 둔다 — 사람의 rm 「배치 확인」이 매번 받던 「되돌릴 수 없음」 체크를 켜는 순간 한 번 받는다(서버도
          acknowledge_irreversible 을 본다). 위험=빨강 의미 체계(삭제 범위·제출 경고와 같은 토큰). */}
      {f.op === "rm" && f.autoConfirm && (
        <div role="note" aria-label="자동 삭제 경고"
             className="rounded-card border border-bad/30 bg-badbg p-3 text-sm text-bad">
          <p className="font-medium">미리보기가 끝나면 사람 확인 없이 root 권한으로 삭제됩니다 — 되돌릴 수 없습니다</p>
          <p className="mt-1 text-xs">
            항목별 지울 대상을 검토하는 「배치 확인」 단계가 없습니다. 항목을 추가하거나 다시 실행해도 그 미리보기가 끝나는
            즉시 삭제됩니다. 결과를 보고 지우려면 자동 실행을 끄세요.
          </p>
          {/* 동의 전이면 data-wizard-nudge(빨강 없는 「아직 비어 있는 필수 칸」 표지 -- Wizard blockedNudge, dms-k8s d7a228f2): 막힌
              「다음」·Enter 가 이 체크로 포커스·스크롤한다. 경고 상자가 실행 제어 맨 아래라 넓은 창에서도 화면 밖일 수 있다(그 라인
              d49 실증 — 1440×900 에서 체크 top 989 > 창 900, 포커스가 「다음」에 남았다). 빨갛게 칠하지 않는 이유: 이미 빨간 경고
              상자 안이고, 안 적은 것이지 틀린 값이 아니다. */}
          <label className="mt-2 flex items-start gap-2 font-medium">
            <input type="checkbox" className="mt-0.5" aria-required="true" data-wizard-nudge={!f.autoConfirmAck || undefined}
                   checked={f.autoConfirmAck} onChange={on("autoConfirmAck")} />
            <span>확인 없이 root 로 삭제되며 되돌릴 수 없다는 것을 이해했습니다(필수)</span>
          </label>
        </div>
      )}
    </>
  );

  // ④ 옵션·실행 제어 -- 넓은 폭(lg)에선 [실행 옵션 | 실행 제어] 2열.
  const controlsStep = () => (
    <div className="grid gap-x-8 gap-y-6 lg:grid-cols-2 lg:items-start">
      <SubSection title="실행 옵션"
                  caption={f.op === "sync" ? <MoreText summary={HELP_SUMMARY.maxErrors} full={SYNC_MAX_ERRORS_NOTE} /> : undefined}>
        {f.op === "rm" ? (
          <>
            {/* 단일 작업(SubmitJob) rm 옵션 미러 — 같은 라벨·같은 즉답(같은 옵션 = 같은 문구). quiet 설명은 단건과 같은 문구(지우지
                못한 항목 목록까지 없앤다, 괴리 [13]) -- 요약은 늘, 전문은 자세히·켜면. */}
            <OptionToggle label="재귀 삭제(필수)" checked={f.recursive} onChange={on("recursive")}
                          error={recursiveMissing ? "재귀 옵션이 필요합니다" : null} />
            <OptionToggle label="stat" checked={f.stat} onChange={on("stat")} />
            <OptionToggle label="lite" checked={f.lite} onChange={on("lite")}
                          error={statLiteConflict ? "stat과 lite는 함께 쓸 수 없습니다" : null} />
            <OptionToggle label="quiet" checked={f.quiet} onChange={on("quiet")} summary={HELP_SUMMARY.quietRm} full={QUIET_HELP.rm} />
          </>
        ) : f.op === "scan" ? (
          <>
            <OptionToggle label="verbose" checked={f.verbose} onChange={on("verbose")} />
            <OptionToggle label="quiet" checked={f.quiet} onChange={on("quiet")}
                          error={verboseQuietConflict ? "verbose와 quiet은 함께 쓸 수 없습니다" : null} />
            {scanFields}
          </>
        ) : (
          <>
            {/* 설명은 단일 작업과 같은 문구(같은 dsync 옵션) -- 요약은 늘, 전문은 자세히·켜면(direct·quiet 도 d192 「칸 옆에」 그대로). */}
            <OptionToggle label="delete" checked={f.delete} onChange={on("delete")}
                          summary={HELP_SUMMARY.delete} full={SYNC_OPTION_HELP.delete} />
            <OptionToggle label="contents" checked={f.contents} onChange={on("contents")}
                          summary={HELP_SUMMARY.contents} full={SYNC_OPTION_HELP.contents} />
            <OptionToggle label="direct" checked={f.direct} onChange={on("direct")}
                          summary={HELP_SUMMARY.direct} full={DIRECT_HELP} />
            <OptionToggle label="quiet" checked={f.quiet} onChange={on("quiet")}
                          summary={HELP_SUMMARY.quietSync} full={QUIET_HELP.sync} />
            <p className="text-xs text-attn">{SYNC_DSYNC_SPECIAL_FILE_NOTE}</p>
            {advancedPanel}
          </>
        )}
      </SubSection>

      {/* 통일 특권 게이트(routes_batches.create_batch): 배치는 전부 관리자
          특권(root) 실행이다 — owner 입력은 특권 스위치가 아니므로 안내문은
          입력과 무관하게 고정이다. 인가의 최종 심판은 서버 게이트
          (403 privileged_not_authorized). */}
      <SubSection title="실행 제어" className="border-t border-line pt-5 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0"
                  caption={f.op === "rm"
                    ? "이 배치는 관리자 특권(root)으로 삭제합니다 — 파일 권한·소유와 관계없이 지워집니다."
                    : "이 배치는 관리자 특권(root)으로 실행됩니다."}>
        {controlFields}
      </SubSection>
    </div>
  );

  // 검토 오른쪽 note 머리: 제목 + 「④로 가기」(옵션·실행 제어 -- 소유권·자동 실행을 고치는 곳) + 「수정」(② 스토리지). role="note" 와
  // aria-label 은 옛 계약 그대로라 ReviewCard 로 감싸지 않는다. 버튼 이름은 내용(sr-only)에서 만든다(aria-label 이면 getByLabel 부분
  // 일치가 버튼까지 잡는다 -- ReviewCard 주석).
  const noteHead = (title: string, tone: "info" | "bad") => (
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <h3 className={`text-sm font-bold ${tone === "bad" ? "text-bad" : "text-ink"}`}>{title}</h3>
      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={() => wizard.go("controls")}
                className="text-xs font-semibold text-accent hover:text-accenthover hover:underline hover:underline-offset-2">
          <span aria-hidden>④로 가기</span><span className="sr-only">옵션·실행 제어로 가기</span>
        </button>
        <button type="button" onClick={() => wizard.go("storage")}
                className="text-xs font-semibold text-accent hover:text-accenthover hover:underline hover:underline-offset-2">
          <span aria-hidden>수정</span><span className="sr-only">{`수정: ${title}`}</span>
        </button>
      </div>
    </div>
  );

  // 목적지 조건과 소유권(2026-09-30 상위 디렉토리 조건, 2026-10-01 "목적지가 없는 경우" +
  // 소유권 추가 -- SubmitJob 과 같은 규칙: preflight _DEST_TYPE_CHECK·_DEST_CHECK, 소유권은
  // lib/syncOwnership). 배치는 생성 게이트(routes_batches: 특권 목록 + 세션)를 통과한
  // 관리자만 만들고 자식은 자격이 있으면 root(identity.PRIVILEGE_IF_ELIGIBLE) -- 그래서
  // 소유권 기본은 "소스 그대로"다. 자격이 계획 시점에 빠진 드문 경우만 비 root 로 돈다.
  // BatchDetail SyncDestHint 가 "상세 규칙은 배치 생성 화면의 안내" 로 이 노트를 가리킨다.
  // 단계별 등록(2026-11-27)부터 ⑤ 검토에 펼친 채 둔다(② 는 한 줄) -- chown 방향어는 단계 이름(「아래」는 거짓).
  const syncConditions = (
    <section className="min-w-0 rounded-xl bg-infobg px-4 py-4 text-xs sm:px-5" role="note" aria-label="목적지 조건과 소유권">
      {noteHead("목적지 조건과 소유권", "info")}
      <ul className="list-disc space-y-0.5 pl-5">
        <li><strong>목적지가 없는 경우</strong>: sync 가 목적지 디렉토리를 새로 만듭니다 — 각 목적지의
          상위 디렉토리는 이미 있어야 합니다(중간 디렉토리는 만들지 않습니다).</li>
        <li><strong>목적지가 이미 있는 경우</strong>: 디렉토리여야 합니다(파일이면 그 항목은 거부).</li>
        <li><strong>소유권</strong>: {ownership.long}
          {f.chown.trim() === "" && " 특정 사용자 소유로 맞추려면 ④ 옵션·실행 제어의 고급 옵션 chown 에 숫자 uid:gid 를 지정하세요."}</li>
        {/* 자격이 계획 시점에 빠진 드문 비 root 폴백 -- 결과 소유는 chown 유무로 갈린다
            (_auto_chown: chown 이 있으면 자동 주입 없음). chown 의 gid 는 계획 시점 멤버십 검증
            (chown_group_not_member)이 먼저 거른다. */}
        <li>관리자 특권 자격이 빠져 비 root 로 실행되면 단일 작업과 같은 권한 조건(상위 디렉토리 쓰기,
          이미 있는 목적지는 실행 신원 소유 — 그룹 쓰기 권한만으로는 부족, 권한은 uid·주 그룹·계획 시점
          LDAP 보조 그룹(적용된 경우 — 운영자가 기능을 껐거나 그룹이 256개를 넘으면 주 그룹만))이 적용되고,{" "}
          {f.chown.trim() === ""
            ? "결과는 실행 신원의 uid:gid(주 그룹) 소유가 됩니다."
            : "chown 의 gid 가 실행 신원의 주 그룹 또는 이 작업에 적용된 보조 그룹이 아니면(보조 그룹이 적용되지 않으면 주 그룹만) 계획 단계에서 거부되고, uid 가 본인이 아니면 적용되지 않습니다(dsync 는 그 항목 실패, nsync 는 소유 변경이 적용되지 않음)."}</li>
        <li>{SYNC_PATH_LINK_RULE}</li>
        <li>조건이 맞지 않는 항목은 미리보기 전에 거부됩니다 — 그 항목은 아무것도 복사되지 않습니다.</li>
      </ul>
      <p className="mt-2 text-xs text-attn">{SYNC_DSYNC_SPECIAL_FILE_NOTE}</p>
    </section>
  );

  // 삭제 범위(2026-10-21, ⑤ 전문): 무엇이·누구 권한으로·언제 지워지는지를 생성 화면이 먼저 말한다.
  const rmScopeNote = (
    <section className="min-w-0 rounded-xl border border-bad/30 bg-badbg px-4 py-4 text-xs text-bad sm:px-5" role="note"
             aria-label="삭제 범위">
      {noteHead("삭제 범위", "bad")}
      <ul className="list-disc space-y-0.5 pl-5">
        {rmScopeHead}
        {rmScopeRoot}
        <li>관리 디렉토리 자신(.)·「..」·「/」로 시작하는 경로는 받지 않고, 한 배치 안에서 겹치는 경로(같은 경로·
          한 경로 아래의 경로)도 받지 않습니다 — 상위 경로 하나만 남기세요.</li>
        {/* 링크 규칙(2026-10-27 d192, 괴리 [14]; d194~ 중간 단계 링크만 거부, 경로 자신이 링크면 링크만 삭제): 위 형식
            검사만 보고 중간에 링크가 낀 행을 넣으면 미리보기 전에 거부된다. */}
        <li>{RM_PATH_LINK_RULE}</li>
        {rmWhenBullet}
      </ul>
    </section>
  );

  // ⑤ 이름·검토 -- 왼쪽: 이름·메모 입력 + 옛 생성 요약의 행(라벨 그대로)을 카드 넷(작업 · 스토리지 · 항목 · 옵션·실행 제어)으로
  // 나누고 「수정」은 그 단계로 간다(go -- 뒤로는 늘 갈 수 있다). 오른쪽: sync 목적지 조건과 소유권 / rm 삭제 범위 전문(접지 않음).
  const previewRows = body.items.slice(0, 5);
  const reviewStep = () => (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] lg:items-start">
      <div className="min-w-0 space-y-4">
        {/* 이름은 목록·상세 헤더에 얹히는 식별자, 메모는 자유 기록 — 역할이
            달라 별도 입력이다. 둘 다 빈값 = 없음(이름은 키 생략, 메모는 null). */}
        <ReviewCard title="이름·메모">
          <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
            <label className="text-sm block">배치 이름(선택)
              <input aria-label="배치 이름" placeholder="예: 8월 정기 스캔 1차"
                     maxLength={120} className={field} value={f.name} onChange={on("name")} />
            </label>
            <label className="text-sm block">메모(선택)
              <input aria-label="메모" placeholder="예: 8월 정기 스캔"
                     className={field} value={f.note} onChange={on("note")} />
            </label>
          </div>
          <p className="mt-2 text-xs text-muted">둘 다 비워도 됩니다 — 이름은 배치 목록·상세의 제목이 됩니다.</p>
        </ReviewCard>
        <ReviewCard title="작업" onEdit={() => wizard.go("kind")}>
          <SummaryList>
            <SummaryRow label="연산">
              <span className="font-semibold">{body.operation}</span>
              <span className="text-muted">{` — ${OPERATION_LABEL_BATCH[f.op]}`}</span>
            </SummaryRow>
            {/* 특권 실행 표시는 고정 행(통일 게이트 — 입력과 무관) */}
            <SummaryRow label="실행 권한" className={body.operation === "rm" ? "text-bad" : ""}>관리자 특권(root)</SummaryRow>
          </SummaryList>
        </ReviewCard>
        <ReviewCard title="스토리지" onEdit={() => wizard.go("storage")}>
          <SummaryList>
            <SummaryRow label="스토리지">{storageText}</SummaryRow>
            {/* sync 결과 소유(2026-10-01): 배치는 root 라 chown 미지정이면 소스 그대로. */}
            {body.operation === "sync" && (
              <SummaryRow label="목적지 소유">
                {syncOwnership({ chown: String(body.options?.chown ?? ""),
                                 chmod: String(body.options?.chmod ?? ""), root: true,
                                 runAs: null, self: true }).short}
              </SummaryRow>
            )}
          </SummaryList>
        </ReviewCard>
        <ReviewCard title="항목" onEdit={() => wizard.go("items")}>
          <SummaryList>
            <SummaryRow label="항목 수">
              {incompleteRows > 0 ? `${body.items.length} (경로 빈 항목 ${incompleteRows})` : body.items.length}
            </SummaryRow>
          </SummaryList>
          {/* 앞 5행 미니 표(바디의 items 에서 -- 화면 거짓말 금지). 긴 무공백 경로가 카드를 밀지 않게 고정 표 + 아무 데서나 줄바꿈. */}
          {previewRows.length > 0 && (
            <table className="mt-3 w-full table-fixed text-xs">
              <thead className="text-left text-muted">
                <tr>
                  <th className="w-8 pb-1 font-normal">#</th>
                  <th className="pb-1 font-normal">{body.operation === "sync" ? "소스" : body.operation === "rm" ? "삭제할 경로" : "경로"}</th>
                  {body.operation === "sync" && <th className="pb-1 font-normal">목적지</th>}
                </tr>
              </thead>
              <tbody>
                {previewRows.map((it, i) => (
                  <tr key={i} className="border-t border-line align-top">
                    <td className="py-1 tabular-nums text-muted">{i + 1}</td>
                    <td className="py-1 font-mono [overflow-wrap:anywhere]">{"target" in it ? it.target : it.source}</td>
                    {"destination" in it && <td className="py-1 font-mono [overflow-wrap:anywhere]">{it.destination}</td>}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {body.items.length > previewRows.length && (
            <p className="mt-1 text-xs text-muted">{`외 ${body.items.length - previewRows.length}행`}</p>
          )}
        </ReviewCard>
        <ReviewCard title="옵션·실행 제어" onEdit={() => wizard.go("controls")}>
          <SummaryList>
            <SummaryRow label="옵션"><OptionTokens options={body.options} /></SummaryRow>
            <SummaryRow label="우선순위">{body.priority ?? "(정책 기본)"}</SummaryRow>
            <SummaryRow label="노드 수">{body.node_count ?? "(정책 기본)"}</SummaryRow>
            <SummaryRow label="노드당 프로세스">{body.procs_per_node ?? "(정책 기본)"}</SummaryRow>
            {/* 실행 신원 행은 값이 있을 때만(빈값 = 생성자 본인) */}
            {body.owner_username && <SummaryRow label="실행 신원">{body.owner_username}</SummaryRow>}
            {/* 실행 확인 방식(sync·rm — scan 은 확인 단계가 없다). 바디에서 파생(화면 거짓말 금지). */}
            {body.operation === "sync" && (
              <SummaryRow label="실행 확인">
                {body.auto_confirm === true ? "자동 — 미리보기가 끝나면 바로 실행" : "직접 — 미리보기 후 「배치 확인」"}
              </SummaryRow>
            )}
            {body.operation === "rm" && (
              <SummaryRow label="실행 확인" className={body.auto_confirm === true ? "text-bad" : ""}>
                {body.auto_confirm === true ? "자동 — 미리보기가 끝나면 확인 없이 삭제" : "직접 — 미리보기 후 「배치 확인」"}
              </SummaryRow>
            )}
            {/* 비운 동시 상한은 Number("")=0 이 아니라 "—"(제출은 그 상태로 막힌다) */}
            <SummaryRow label="동시 상한">{f.mc.trim() === "" ? "—" : body.max_concurrency}</SummaryRow>
          </SummaryList>
        </ReviewCard>
      </div>
      <div className="min-w-0 space-y-4">
        {f.op === "sync" && syncConditions}
        {f.op === "rm" && rmScopeNote}
      </div>
    </div>
  );

  // 단계 카드 머리 한 줄. 스토리지는 옛 구획 caption 그대로.
  const STEP_LEDE: Record<BatchStep, string> = {
    kind: "무엇을 할지 고르세요. 다음 단계의 입력이 연산에 맞춰 바뀝니다.",
    storage: f.op === "sync"
      ? "한 배치는 소스 → 목적지 스토리지 한 쌍만 대상으로 합니다 — 행은 경로만 입력합니다."
      : f.op === "rm"
        ? "한 배치는 하나의 스토리지만 대상으로 합니다 — 행마다 지울 경로를 입력합니다."
        : "한 배치는 하나의 스토리지만 대상으로 합니다 — 행은 경로만 입력합니다.",
    items: "행마다 잡 하나가 만들어집니다 — 경로는 고른 스토리지의 관리 디렉토리 아래 상대경로입니다.",
    controls: "기본값 그대로도 충분합니다. 켠 옵션은 자세한 안내가 저절로 펼쳐집니다.",
    review: "만들기 전에 한 화면에서 확인하세요. 「수정」을 누르면 그 단계로 돌아갑니다.",
  };

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">배치 생성</h1>
        <p className="mt-1 text-sm text-muted">여러 경로를 한 번에 scan·sync·rm 합니다.</p>
      </div>
      <WizardShell {...wizard.shellProps}>
        <WizardStepper wizard={wizard} />
        <WizardStepCard wizard={wizard} lede={STEP_LEDE[wizard.current]}>
          {wizard.current === "kind" ? kindStep()
            : wizard.current === "storage" ? storageStep()
            : wizard.current === "items" ? itemsStep()
            : wizard.current === "controls" ? controlsStep()
            : reviewStep()}
        </WizardStepCard>
        {/* rm 경고는 한 곳 -- 검토 단계 하단 바, 「배치 생성」 바로 위(「누르기 직전」). 위험=빨강 의미 체계 유지. */}
        <WizardActionBar
          wizard={wizard}
          doneHint="생성하면 배치 상세로 이동합니다."
          onCancel={() => nav("/admin/batches")}
          submit={{ label: "배치 생성", onClick: handleSubmit, disabled: blocked,
                    // rm 검토의 생성은 빨간 솔리드(사용자 결정 -- 주 동작 자체가 파괴적일 때만 destructive)
                    variant: body.operation === "rm" ? "destructive" : "primary",
                    describedBy: hints.length > 0 ? "batch-create-hints" : undefined }}
          strip={wizard.current === "review" && body.operation === "rm" ? (body.auto_confirm === true
            ? "삭제는 되돌릴 수 없습니다. 자동 실행이 켜져 있어 항목별 미리보기가 끝나는 즉시 「배치 확인」 없이 삭제됩니다."
            : "삭제는 되돌릴 수 없습니다. 항목별 미리보기에서 지울 대상을 본 뒤 배치 상세의 「배치 확인」에서 확인해야 삭제됩니다.")
            : undefined}
          details={wizard.current === "review" && (hints.length > 0 || create.isError) ? (<>
            <SubmitHints id="batch-create-hints" items={hints} />
            {create.isError && <p className="text-bad text-sm">{(create.error as ApiError).message}</p>}
          </>) : undefined} />
      </WizardShell>
    </section>
  );
}

// 실행 신원 도움말(④) -- rm·그 밖 두 갈래(요약은 optionRules HELP_SUMMARY.runAs -- 배치 전용). 2026-09-30 정정의 「왜」는 실행 신원 칸 주석.
const RUN_AS_HELP_RM = "배치는 관리자 이관·정리용이라 root 로 실행됩니다 — 실행 신원은 기록용 이름이며 파일 권한 검사는 적용되지 "
  + "않아, 실행 신원이 지울 수 없는 파일도 지웁니다. 사용자 권한 그대로 지우려면 단일 작업에서 실행 신원을 지정하세요.";
const RUN_AS_HELP = "배치는 관리자 이관·정리용이라 root 로 실행됩니다 — 실행 신원은 기록용 이름이며 파일 권한 검사는 적용되지 "
  + "않고, sync 는 목적지(이미 있는 디렉토리 포함)의 소유자·권한을 소스와 같게 바꿉니다(chown·chmod 지정 시 그 값). 사용자 권한 "
  + "그대로 실행하려면 단일 작업에서 실행 신원을 지정하세요.";
// nsync 면당 캡(placement.py:121-122): resolve_fanout 은 nsync(공존 노드 없음 폴백)에서 max_nodes·요청 node_count 를 출발·목적지
// **각각**에 min-캡한다 — 입력값이 면당 상한이라 총 노드는 최대 2배. dsync 는 공존 노드 단일 집합(primary)이라 해당 없음. 정책 조회
// 상태와 무관한 배치 메커니즘 사실이라 sync 에선 항상 싣는다.
const NSYNC_PER_SIDE_NOTE = "nsync 폴백 시 입력한 노드 수는 출발·목적지 각각(면당)의 상한이라 총 노드는 최대 2배가 될 수 있습니다.";
// 자동 실행 도움말(④). 켜면 무엇을 생략하는지를 그대로 말한다(실행은 root). 요약 — 전문은 바로 아래 AUTO_RUN_HELP(_RM) 의 첫 절.
const AUTO_RUN_SUMMARY_RM = "켜면 미리보기가 끝나는 즉시 「배치 확인」 없이 삭제(root)됩니다.";
const AUTO_RUN_SUMMARY = "켜면 미리보기가 끝나는 즉시 「배치 확인」 없이 실행(root)됩니다.";
const AUTO_RUN_HELP_RM = "켜면 모든 항목의 미리보기(지울 항목 수)가 끝나는 즉시 「배치 확인」 없이 삭제(root)됩니다 — 지울 대상을 "
  + "보고 확인하는 단계를 건너뜁니다. 미리보기는 그대로 돌고 항목별로 남습니다. 진행 중에도 배치 화면에서 끌 수 있습니다.";
const AUTO_RUN_HELP = "켜면 모든 항목의 미리보기가 끝나는 즉시 「배치 확인」 없이 실행(root)됩니다 — 미리보기 결과를 보고 "
  + "확인하는 단계를 건너뜁니다. 미리보기는 그대로 돌고 항목별로 남습니다. 진행 중에도 배치 화면에서 끌 수 있습니다.";
