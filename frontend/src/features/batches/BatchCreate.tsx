import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ArrowRight, Download, Plus, X } from "lucide-react";
import { useCreateBatch } from "./useBatches";
import type { CreateBatchBody } from "./useBatches";
import { parseItemsCsv, serializeItemsCsv } from "../../lib/csv";
import type { ScanRow, SyncRow } from "../../lib/csv";
import { useUserStorages } from "../storages/useUserStorages";
import { usePolicies } from "../policies/usePolicies";
import type { Policy } from "../../lib/types";
import { StoragePicker, field } from "../jobs/formFields";
import {
  CHMOD_RE, chownFieldError, SCAN_INT_FIELDS, SYNC_INT_FIELDS, intFieldError,
  scanIntFieldError, syncIntFieldError,
} from "../jobs/optionRules";
import { Button } from "../../components/ui/Button";
import {
  FieldRow, FormSection, OptionTokens, SubmitHints, SubmitLayout, SummaryList, SummaryPanel, SummaryRow,
} from "../../components/form/SubmitLayout";
import { ApiError } from "../../lib/api";
import { syncOwnership } from "../../lib/syncOwnership";

// 배치 생성(슬라이스 32): 단일 작업과 같은 한 장짜리 시트 + 오른쪽 제출 요약(2026-10-06 — 4스텝
// 위저드를 걷었다. 「왜」는 components/form/SubmitLayout 주석). 구획 순서: 작업 종류 → 대상 스토리지와
// 항목 → 실행 옵션 → 실행 제어 → 이름·메모.

type InputTab = "table" | "paste" | "upload";

// 우선순위 순서는 PRIORITIES(domain.py:61)의 미러 — 상한 초과 판정에 쓴다.
const PRIORITY_RANK: Record<string, number> = { low: 0, mid: 1, high: 2 };

// 동시 실행 상한 프리필(사용자 결정 2026-10-01: 2 → 32). 서버엔 기본값이 없다(필수 필드,
// domain.validate_batch 1..64) -- 기본은 이 화면이 정해 바디에 늘 싣는다. 배치 하나가 동시에
// 미리보기·실행하는 항목(잡) 수이고, 자원이 모자라면 잡은 Volcano 큐에서 기다린다.
export const DEFAULT_MAX_CONCURRENCY = 32;

// 행은 경로만 나른다(스토리지는 배치 레벨 선택) — a = target(scan)/source(sync),
// b = destination(sync 전용). CSV 파서(ScanRow/SyncRow)와 제출 바디 조립의 중간형.
interface RowPair { a: string; b: string }

// 항목 표의 입력 칸(촘촘한 변형 -- field 의 mt-1 이 행 정렬을 흔든다).
const cell = "w-full rounded-lg border border-line px-3 py-1.5 text-sm";

const OPERATION_HELP: Record<"scan" | "sync", string> = {
  scan: "한 스토리지의 여러 경로를 scan 합니다. 행마다 잡 하나가 만들어집니다.",
  sync: "소스 스토리지 → 목적지 스토리지 한 쌍 안에서 여러 경로를 sync 합니다. 행마다 잡 하나가 만들어집니다.",
};

const initial = {
  op: "scan" as "scan" | "sync",
  storage: "", srcStorage: "", dstStorage: "",
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
  // 켠다. (단건 sync 는 비특권 실행이라 타인 소유 파일 O_NOATIME 이 EPERM 일 수
  // 있다는 캐비엇이 SubmitJob 쪽 초기값 주석에 있다.)
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
  const storageChosen = f.op === "scan"
    ? f.storage !== ""
    : f.srcStorage !== "" && f.dstStorage !== "";
  const incompleteRows = rows.filter(
    (r) => r.a.trim() === "" || (f.op === "sync" && r.b.trim() === "")).length;
  const rowsComplete = rows.length >= 1 && incompleteRows === 0;
  const itemsValid = storageChosen && rowsComplete && csvErrors.length === 0;

  // --- 옵션·실행 제어 검증(즉답 미러 — 최종 심판은 서버 422) ---
  const verboseQuietConflict = f.op === "scan" && f.verbose && f.quiet;
  const scanBatchFilesError = f.op === "scan"
    ? scanIntFieldError("batch_files", f.scanBatchFiles) : null;
  const brokenLimitError = f.op === "scan"
    ? scanIntFieldError("broken_limit", f.brokenLimit) : null;
  const batchFilesError = f.op === "sync"
    ? syncIntFieldError("batch_files", f.batchFiles) : null;
  const bufsizeError = f.op === "sync"
    ? syncIntFieldError("bufsize", f.bufsize) : null;
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
  const controlsInvalid = verboseQuietConflict || scanBatchFilesError !== null
    || brokenLimitError !== null || advancedError !== null
    || nodeCountError !== null || procsPerNodeError !== null || mcError !== null;

  // 제출 게이트는 이 하나다(위저드 시절 스텝별 '다음' 게이트의 합집합 + 조회 실패·진행 중).
  const blocked = create.isPending || !itemsValid || controlsInvalid || storagesQ.isError;

  // --- 정책 기본값 캡션(백엔드 무변경 — 표시 배선만) ---
  // 도구→정책 키는 placement.py TOOL_TO_POLICY 의 미러: scan→"scan", sync→공존
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
    if (f.op === "scan") {
      const p = byTool.get("scan");
      return p === undefined ? "정책 미조회 — scan 정책 행이 없습니다"
        : `정책 기본: ${fmtPolicy(p)}`;
    }
    const dsync = byTool.get("dsync");
    const nsync = byTool.get("nsync");
    if (dsync === undefined && nsync === undefined)
      return "정책 미조회 — dsync/nsync 정책 행이 없습니다";
    return `정책 기본(dsync): ${fmtPolicy(dsync)} — 공존 노드가 없으면 `
      + `nsync 정책(${fmtPolicy(nsync)})이 적용됩니다`;
  })();

  // --- 우선순위: 정책 기본값 실값 + 상한 캡션 ---
  // 기본 라벨은 resolve_priority(domain.py) 미러 — scan→"scan" 정책, sync 는
  // 제출 시점에 도구(dsync/nsync)가 정해지지 않으므로 dsync 정책을 대표로 읽는다.
  // 미조회·행 부재면 실값 없이 "(정책 기본)" — 거짓값 금지(null≠0).
  const defaultPolicy = byTool?.get(f.op === "scan" ? "scan" : "dsync");
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
    const tools = f.op === "scan" ? ["scan"] : ["dsync", "nsync"];
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
    if (f.op === "scan")
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

  // 제출 바디와 오른쪽 요약이 **같은 함수**에서 파생 — 화면 따로 바디 따로면
  // "요약과 다른 것이 제출되는" 화면 거짓말이 생긴다(SubmitJob 관례).
  function buildBody(): CreateBatchBody {
    const items = f.op === "scan"
      ? rows.map((r) => ({ storage: f.storage, target: r.a.trim() }))
      : rows.map((r) => ({ source_storage: f.srcStorage, source: r.a.trim(),
                           destination_storage: f.dstStorage, destination: r.b.trim() }));
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
    };
  }

  // <form> 이 없다: 경로·메모 입력에서 Enter 가 배치를 바로 만들지 않게(제출은 버튼 onClick 하나).
  function handleSubmit() {
    if (blocked) return;
    create.mutate(buildBody(), { onSuccess: (r) => nav(`/admin/batches/${r.batch_id}`) });
  }

  function applyParsed(text: string) {
    const { rows: parsed, errors } = parseItemsCsv(f.op, text);
    setCsvErrors(errors);
    if (errors.length > 0) return;   // 오류가 있으면 rows 를 덮지 않는다(부분 반영 금지)
    setRows(parsed.map((r) => f.op === "scan"
      ? { a: (r as ScanRow).target, b: "" }
      : { a: (r as SyncRow).source, b: (r as SyncRow).destination }));
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
    const text = serializeItemsCsv(f.op, f.op === "scan"
      ? rows.map((r) => ({ target: r.a.trim() }))
      : rows.map((r) => ({ source: r.a.trim(), destination: r.b.trim() })));
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

  // 입력 방식 전환(세그먼트). 역할은 버튼 그대로(aria-pressed) -- 세 방식은 같은 rows 를 고치는
  // 도구라 탭 패널 구조가 아니고, CSV 반영 성공이 표로 돌아오는 동선(applyParsed)이 있다.
  const segment = (t: InputTab, label: string) => (
    <button type="button" aria-pressed={tab === t} onClick={() => setTab(t)}
            className={`whitespace-nowrap rounded-md px-2.5 py-1.5 text-sm sm:px-3 ${tab === t
              ? "bg-surface font-medium text-ink shadow-soft" : "text-muted hover:text-ink"}`}>
      {label}
    </button>
  );

  const rowCols = f.op === "scan"
    ? "grid-cols-[2rem_minmax(0,1fr)_2rem]"
    : "grid-cols-[2rem_minmax(0,1fr)_minmax(0,1fr)_2rem]";

  // 제출이 잠긴 이유(요약 패널, 버튼 위) -- 필드 옆 빨간 문구와 다른 문장으로 짧게(SubmitHints 주석).
  const hints: string[] = [];
  if (create.isPending) hints.push("생성 중…");
  if (storagesQ.isError) hints.push("스토리지 목록 조회 실패 — 새로고침 후 다시 시도하세요");
  if (!storageChosen) hints.push(f.op === "scan" ? "스토리지를 고르세요" : "소스·목적지 스토리지를 고르세요");
  if (rows.length === 0) hints.push("항목을 1개 이상 추가하세요");
  else if (incompleteRows > 0) hints.push(`경로가 빈 항목 ${incompleteRows}개를 채우거나 지우세요`);
  if (csvErrors.length > 0) hints.push("CSV 오류를 고친 뒤 다시 반영하세요");
  if (controlsInvalid) hints.push("빨간 안내가 붙은 옵션·실행 제어 값을 고치세요");

  const body = buildBody();   // 요약 = 제출 바디에서 파생(화면 거짓말 금지)

  const sheet = (
    <>
      <FormSection step={1} title="작업 종류">
        <div className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
          <label className="text-sm block">연산
            <select aria-label="연산" className={field} value={f.op}
                    onChange={(e) => setF({ ...f, op: e.target.value as "scan" | "sync" })}>
              <option value="scan">scan</option>
              <option value="sync">sync</option>
            </select>
          </label>
          <p className="text-sm text-muted sm:pt-8">{OPERATION_HELP[f.op]}</p>
        </div>
      </FormSection>

      <FormSection step={2} title="대상 스토리지와 항목"
                   caption={f.op === "scan"
                     ? "한 배치는 하나의 스토리지만 대상으로 합니다 — 행은 경로만 입력합니다."
                     : "한 배치는 소스 → 목적지 스토리지 한 쌍만 대상으로 합니다 — 행은 경로만 입력합니다."}>
        {storagesQ.isError && (
          <p className="text-bad text-sm">{(storagesQ.error as ApiError).message}</p>
        )}
        {f.op === "scan" ? (
          <div className="grid gap-3 sm:grid-cols-2">
            <StoragePicker label="스토리지" value={f.storage}
              onChange={(v) => setF({ ...f, storage: v })}
              storages={storages} loading={loadingStorages} />
          </div>
        ) : (
          <>
            <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
              <StoragePicker label="소스 스토리지" value={f.srcStorage}
                onChange={(v) => setF({ ...f, srcStorage: v })}
                storages={storages} loading={loadingStorages} />
              <ArrowRight aria-hidden
                          className="h-5 w-5 rotate-90 justify-self-center text-muted md:mt-8 md:rotate-0" />
              <StoragePicker label="목적지 스토리지" value={f.dstStorage}
                onChange={(v) => setF({ ...f, dstStorage: v })}
                storages={storages} loading={loadingStorages} />
            </div>
            {/* 목적지 조건과 소유권(2026-09-30 상위 디렉토리 조건, 2026-10-01 "목적지가 없는 경우" +
                소유권 추가 -- SubmitJob 과 같은 규칙: preflight _DEST_TYPE_CHECK·_DEST_CHECK, 소유권은
                lib/syncOwnership). 배치는 생성 게이트(routes_batches: 특권 목록 + 세션)를 통과한
                관리자만 만들고 자식은 자격이 있으면 root(identity.PRIVILEGE_IF_ELIGIBLE) -- 그래서
                소유권 기본은 "소스 그대로"다. 자격이 계획 시점에 빠진 드문 경우만 비 root 로 돈다.
                BatchDetail SyncDestHint 가 "상세 규칙은 배치 생성 화면의 안내" 로 이 노트를 가리킨다. */}
            <div className="rounded-card bg-infobg p-3 text-xs" role="note"
                 aria-label="목적지 조건과 소유권">
              <p className="font-medium text-sm">목적지 조건과 소유권</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5">
                <li><strong>목적지가 없는 경우</strong>: sync 가 목적지 디렉토리를 새로 만듭니다 — 각 목적지의
                  상위 디렉토리는 이미 있어야 합니다(중간 디렉토리는 만들지 않습니다).</li>
                <li><strong>목적지가 이미 있는 경우</strong>: 디렉토리여야 합니다(파일이면 그 항목은 거부).</li>
                <li><strong>소유권</strong>: {syncOwnership({ chown: f.chown, chmod: f.chmod, root: true, runAs: null, self: true }).long}
                  {f.chown.trim() === "" && " 특정 사용자 소유로 맞추려면 아래 실행 옵션의 고급 옵션 chown 에 숫자 uid:gid 를 지정하세요."}</li>
                {/* 자격이 계획 시점에 빠진 드문 비 root 폴백 -- 결과 소유는 chown 유무로 갈린다
                    (_auto_chown: chown 이 있으면 자동 주입 없음). 권한은 uid·주 gid 만(보조 그룹 미적용). */}
                <li>관리자 특권 자격이 빠져 비 root 로 실행되면 단일 작업과 같은 권한 조건(상위 디렉토리 쓰기,
                  이미 있는 목적지는 실행 신원 소유 — 보조 그룹 권한은 인정되지 않음)이 적용되고,{" "}
                  {f.chown.trim() === ""
                    ? "결과는 실행 신원의 uid:gid(주 그룹) 소유가 됩니다."
                    : "chown 값이 실행 신원 본인의 uid·주 그룹이 아니면 적용되지 않습니다(dsync 는 그 항목 실패, nsync 는 소유 변경이 적용되지 않음)."}</li>
                <li>조건이 맞지 않는 항목은 미리보기 전에 거부됩니다 — 그 항목은 아무것도 복사되지 않습니다.</li>
              </ul>
            </div>
          </>
        )}

        {/* 항목 편집기: 입력 방식 세그먼트 + (보조 동작) CSV 다운로드 / 본문 / 행 수 */}
        <div className="rounded-lg border border-line">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-3 py-2">
            {/* 좁은 화면에선 세그먼트가 음절 중간에서 접히지 않고 통째로 다음 줄로 넘어간다. */}
            <div className="inline-flex flex-wrap gap-0.5 rounded-lg bg-panel p-0.5">
              {segment("table", "테이블 편집")}
              {segment("paste", "CSV 붙여넣기")}
              {segment("upload", "파일 업로드")}
            </div>
            <Button type="button" variant="ghost" className="gap-1.5"
                    onClick={downloadCsv}>
              <Download className="h-4 w-4" aria-hidden />CSV 다운로드
            </Button>
          </div>
          <div className="p-3">
            {tab === "table" && (
              <div className="space-y-2">
                <div className={`grid ${rowCols} gap-2 text-xs text-muted`} aria-hidden>
                  <span className="text-right">#</span>
                  <span>{f.op === "scan" ? "경로" : "소스 경로"}</span>
                  {f.op === "sync" && <span>목적지 경로</span>}
                  <span />
                </div>
                {/* 수백 행(CSV)이 들어와도 실행 제어가 화면 아래로 묻히지 않게 목록만 스크롤한다. */}
                <div className="max-h-[28rem] space-y-2 overflow-y-auto">
                  {rows.map((r, i) => (
                    <div key={i} className={`grid ${rowCols} items-center gap-2`}>
                      <span className="text-right text-xs tabular-nums text-muted">{i + 1}</span>
                      {/* placeholder 예시는 스토리지 기준 상대경로(선행 슬래시 없음) —
                          경로가 스토리지 루트 기준임을 힌트로 드러낸다 */}
                      <input aria-label={`${i + 1}행 ${f.op === "scan" ? "경로" : "소스"}`}
                             placeholder={f.op === "scan" ? "예: team/projects" : "예: team/dataset"}
                             className={cell} value={r.a}
                             onChange={(e) => setRow(i, "a", e.target.value)} />
                      {f.op === "sync" && (
                        <input aria-label={`${i + 1}행 목적지`} placeholder="예: backup/dataset"
                               className={cell} value={r.b}
                               onChange={(e) => setRow(i, "b", e.target.value)} />
                      )}
                      <button type="button" aria-label={`${i + 1}행 삭제`}
                              className="grid h-8 w-8 place-items-center rounded-lg text-muted hover:bg-panel hover:text-bad"
                              onClick={() => { setRows(rows.filter((_, j) => j !== i)); setCsvErrors([]); }}>
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
                  CSV ({f.op === "scan" ? "행당 경로 1개" : "행당 source,destination"})
                  <textarea aria-label="CSV" className={`${field} h-40 font-mono`}
                            placeholder={f.op === "scan"
                              ? "team\nprojects/alpha"
                              : "team/dataset,backup/dataset\nprojects/alpha,backup/alpha"}
                            value={csvText} onChange={(e) => setCsvText(e.target.value)} />
                </label>
                <p className="text-xs text-muted">반영하면 표의 행을 통째로 바꿉니다(이어 붙이지 않음).</p>
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
          </div>
          <div className="border-t border-line px-3 py-2 text-sm text-muted">행: {rows.length}</div>
        </div>
      </FormSection>

      <FormSection step={3} title="실행 옵션">
        {f.op === "scan" ? (
          <>
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="verbose" checked={f.verbose}
                       onChange={on("verbose")} /> verbose
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="quiet" checked={f.quiet}
                       onChange={on("quiet")} /> quiet
              </label>
            </div>
            {verboseQuietConflict && (
              <p className="text-bad text-sm">verbose와 quiet은 함께 쓸 수 없습니다</p>
            )}
            {/* 프리필 = 서버 기본(domain._OPTION_DEFAULTS, 2026-09-17). 비우면 키가
                빠지고 서버가 같은 값을 박는다 -- placeholder 는 그 사실을 말한다. */}
            <FieldRow
              control={<>
                <label className="text-sm block">batch_files (선택 · 0..1,000,000,000 · 0 = 배칭 끔)
                  <input aria-label="batch_files" type="number" min={0} max={1000000000}
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
                         placeholder="비우면 기본 100 적용"
                         className={field} value={f.brokenLimit}
                         onChange={on("brokenLimit")} />
                </label>
                {brokenLimitError && <p className="mt-1 text-bad text-sm">{brokenLimitError}</p>}
              </>}
              help="미리 채운 100 = 서버 기본. 비워도 같은 값이 적용됩니다." />
          </>
        ) : (
          <>
            {/* 설명은 단일 작업과 같은 문구(같은 dsync 옵션). */}
            <FieldRow align="check"
              control={<label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="delete" checked={f.delete}
                       onChange={on("delete")} /> delete
              </label>}
              help="원본에 없는 파일을 대상에서도 삭제해 완전히 동일하게 맞춥니다(미러 동기화)." />
            <FieldRow align="check"
              control={<label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="contents" checked={f.contents}
                       onChange={on("contents")} /> contents
              </label>}
              help="크기·수정시각 대신 파일 내용을 바이트 단위로 비교합니다(더 느리지만 정확)." />
            <div className="flex flex-wrap gap-x-6 gap-y-2">
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="direct" checked={f.direct}
                       onChange={on("direct")} /> direct
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label="quiet" checked={f.quiet}
                       onChange={on("quiet")} /> quiet
              </label>
            </div>
            {/* 접혀 있어도 내용은 마운트돼 있다 -- open_noatime 기본 ON 이 펼치지 않아도 실린다. */}
            <details className="rounded-lg border border-line"
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
                  help="기본 켬 — 배치는 특권(root) 실행이라 O_NOATIME 권한 제약이 없고, 소스 atime 오염을 막아 데이터 온도(hot/cold) 통계를 정직하게 유지합니다." />
                {/* 프리필 계약(단건 폼과 동일 문구, 2026-09-17): 값이 미리 채워져
                    있고 그 값이 곧 서버 기본이라 비워도 같은 값이 적용된다 — 배칭을
                    끄는 유일한 표현은 0 명시. placeholder 는 "비웠을 때", 캡션은
                    "지금 채워진 값"을 말한다. */}
                <FieldRow
                  control={<>
                    <label className="text-sm block">batch_files (선택 · 0..10,000,000)
                      <input aria-label="batch_files"
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
                      <input aria-label="bufsize" placeholder="비우면 기본 4 MiB 적용"
                             className={field} value={f.bufsize}
                             onChange={on("bufsize")} />
                    </label>
                    {bufsizeError && <p className="mt-1 text-bad text-sm">{bufsizeError}</p>}
                  </>}
                  help="미리 채운 4194304 = 4 MiB(서버 기본). 비워도 같은 값이 적용됩니다." />
                <FieldRow
                  control={<>
                    <label className="text-sm block">chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)
                      <input aria-label="chmod" placeholder="예: D770,F660"
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
                      <input aria-label="chown" placeholder="예: 10003:10000"
                             className={field} value={f.chown} onChange={on("chown")} />
                    </label>
                    {chownError && <p className="mt-1 text-bad text-sm">{chownError}</p>}
                  </>}
                  help="비우면 원래(소스) 소유권을 보존합니다(root 실행). 지정하면 목적지와 복사본이 그 소유로 셋업됩니다 — 숫자 uid:gid 로 지정하세요." />
              </div>
            </details>
          </>
        )}
      </FormSection>

      {/* 통일 특권 게이트(routes_batches.create_batch): 배치는 전부 관리자
          특권(root) 실행이다 — owner 입력은 특권 스위치가 아니므로 안내문은
          입력과 무관하게 고정이다. 인가의 최종 심판은 서버 게이트
          (403 privileged_not_authorized). */}
      <FormSection step={4} title="실행 제어" caption="이 배치는 관리자 특권(root)으로 실행됩니다.">
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
            소유·권한을 소스에 맞춘다). */}
        <FieldRow
          control={<label className="text-sm block">실행 신원(선택)
            <input aria-label="실행 신원(선택)" placeholder="예: cocoa.song"
                   className={field}
                   value={f.ownerUsername} onChange={on("ownerUsername")} />
          </label>}
          help="배치는 관리자 이관·정리용이라 root 로 실행됩니다 — 실행 신원은 기록용 이름이며 파일 권한 검사는 적용되지 않고, sync 는 목적지(이미 있는 디렉토리 포함)의 소유자·권한을 소스와 같게 바꿉니다(chown·chmod 지정 시 그 값). 사용자 권한 그대로 실행하려면 단일 작업에서 실행 신원을 지정하세요." />
        {/* 잡 하나의 폭(노드 수 × 노드당 프로세스) -- 정책 실값 캡션은 두 칸을 함께 설명한다.
            라벨 길이가 달라도 입력 상자가 한 줄에 서도록 아래 정렬(items-end), 오류는 그 아래. */}
        <div className="space-y-1">
          <div className="grid gap-x-6 gap-y-3 sm:grid-cols-2 sm:items-end">
            <label className="text-sm block">노드 수 (선택 · 1..64, 빈값 = 정책 기본)
              <input aria-label="노드 수" type="number" min={1} max={64} className={field}
                     placeholder="비우면 정책 기본"
                     value={f.nodeCount} onChange={on("nodeCount")} />
            </label>
            {/* 요청은 정책을 줄일 수만 있다(planner min-캡) — 늘리려면 정책 수정 */}
            <label className="text-sm block">노드당 프로세스 수 (선택 · 1..64, 빈값 = 정책 기본)
              <input aria-label="노드당 프로세스 수" type="number" min={1} max={64}
                     className={field} placeholder="비우면 정책 기본"
                     value={f.procsPerNode} onChange={on("procsPerNode")} />
            </label>
          </div>
          {nodeCountError && <p className="text-bad text-sm">{nodeCountError}</p>}
          {procsPerNodeError && <p className="text-bad text-sm">{procsPerNodeError}</p>}
          <p className="text-muted text-xs">{policyCaption}</p>
          {f.op === "sync" && (
            // nsync 면당 캡(placement.py:121-122): resolve_fanout 은 nsync
            // (공존 노드 없음 폴백)에서 max_nodes·요청 node_count 를 출발·
            // 목적지 **각각**에 min-캡한다 — 입력값이 면당 상한이라 총 노드는
            // 최대 2배. dsync 는 공존 노드 단일 집합(primary)이라 해당 없음.
            // 정책 조회 상태와 무관한 배치 메커니즘 사실이라 항상 표기한다.
            <p className="text-muted text-xs">
              nsync 폴백 시 입력한 노드 수는 출발·목적지 각각(면당)의 상한이라
              총 노드는 최대 2배가 될 수 있습니다.
            </p>
          )}
        </div>
        {/* 노드 수와 대구: 위는 잡 하나의 폭, 이것은 잡 몇 개를 나란히 */}
        <FieldRow
          control={<>
            <label className="text-sm block">동시 실행 상한 (1..64)
              <input aria-label="동시 실행 상한" type="number" min={1} max={64} className={field}
                     placeholder={`예: ${DEFAULT_MAX_CONCURRENCY}`}
                     value={f.mc} onChange={(e) => setF({ ...f, mc: e.target.value })} />
            </label>
            {mcError && <p className="mt-1 text-bad text-sm">{mcError}</p>}
          </>}
          help="동시에 실행할 배치 항목(잡) 수 — 잡 하나가 쓰는 노드 수와 무관합니다." />
      </FormSection>

      {/* 이름은 목록·상세 헤더에 얹히는 식별자, 메모는 자유 기록 — 역할이
          달라 별도 입력이다. 둘 다 빈값 = 없음(이름은 키 생략, 메모는 null). */}
      <FormSection step={5} title="이름·메모" caption="둘 다 비워도 됩니다 — 이름은 배치 목록·상세의 제목이 됩니다.">
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
      </FormSection>
    </>
  );

  const storageText = f.op === "scan" ? (f.storage === "" ? "—" : f.storage)
    : `${f.srcStorage === "" ? "—" : f.srcStorage} → ${f.dstStorage === "" ? "—" : f.dstStorage}`;

  const summary = (
    <SummaryPanel title="생성 요약" footer={<>
      <SubmitHints id="batch-create-hints" items={hints} />
      {create.isError && (
        <p className="text-bad text-sm">{(create.error as ApiError).message}</p>
      )}
      <div className="flex gap-2">
        <Button type="button" variant="ghost" onClick={() => nav("/admin/batches")}>취소</Button>
        <Button type="button" className="flex-1" disabled={blocked} onClick={handleSubmit}
                aria-describedby={hints.length > 0 ? "batch-create-hints" : undefined}>
          배치 생성
        </Button>
      </div>
    </>}>
      <SummaryList>
        <SummaryRow label="연산">{body.operation}</SummaryRow>
        <SummaryRow label="스토리지">{storageText}</SummaryRow>
        <SummaryRow label="항목 수">
          {incompleteRows > 0 ? `${body.items.length} (경로 빈 항목 ${incompleteRows})` : body.items.length}
        </SummaryRow>
        <SummaryRow label="옵션"><OptionTokens options={body.options} /></SummaryRow>
        <SummaryRow label="우선순위">{body.priority ?? "(정책 기본)"}</SummaryRow>
        <SummaryRow label="노드 수">{body.node_count ?? "(정책 기본)"}</SummaryRow>
        <SummaryRow label="노드당 프로세스">{body.procs_per_node ?? "(정책 기본)"}</SummaryRow>
        {/* 특권 실행 표시는 고정 행(통일 게이트 — 입력과 무관), 실행 신원 행은 값이 있을 때만
            (빈값 = 생성자 본인) */}
        <SummaryRow label="실행 권한">관리자 특권(root)</SummaryRow>
        {/* sync 결과 소유(2026-10-01): 배치는 root 라 chown 미지정이면 소스 그대로. */}
        {body.operation === "sync" && (
          <SummaryRow label="목적지 소유">
            {syncOwnership({ chown: String(body.options?.chown ?? ""),
                             chmod: String(body.options?.chmod ?? ""), root: true,
                             runAs: null, self: true }).short}
          </SummaryRow>
        )}
        {body.owner_username && <SummaryRow label="실행 신원">{body.owner_username}</SummaryRow>}
        {/* 비운 동시 상한은 Number("")=0 이 아니라 "—"(제출은 그 상태로 막힌다) */}
        <SummaryRow label="동시 상한">{f.mc.trim() === "" ? "—" : body.max_concurrency}</SummaryRow>
        {body.name && <SummaryRow label="이름">{body.name}</SummaryRow>}
        {body.note && <SummaryRow label="메모">{body.note}</SummaryRow>}
      </SummaryList>
    </SummaryPanel>
  );

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold">배치 생성</h1>
        <p className="mt-1 text-sm text-muted">여러 경로를 한 번에 scan·sync 합니다.</p>
      </div>
      <SubmitLayout sheet={sheet} summary={summary} />
    </section>
  );
}
