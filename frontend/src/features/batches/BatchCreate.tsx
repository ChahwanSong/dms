import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useCreateBatch } from "./useBatches";
import type { CreateBatchBody } from "./useBatches";
import { parseItemsCsv, serializeItemsCsv } from "../../lib/csv";
import type { ScanRow, SyncRow } from "../../lib/csv";
import { useUserStorages } from "../storages/useUserStorages";
import { usePolicies } from "../policies/usePolicies";
import type { Policy } from "../../lib/types";
import { StoragePicker, field } from "../jobs/formFields";
import {
  CHMOD_RE, CHOWN_RE, SYNC_INT_FIELDS, intFieldError, syncIntFieldError,
} from "../jobs/optionRules";
import { Card } from "../../components/ui/Card";
import { Button } from "../../components/ui/Button";
import { InfoPanel } from "../../components/ui/InfoPanel";
import { Wizard } from "../../components/wizard/Wizard";
import type { WizardStep } from "../../components/wizard/Wizard";
import { ApiError } from "../../lib/api";

// 4스텝 위저드(슬라이스 32 T9): SubmitJob 관례(연산→대상→옵션→확인)를 배치에
// 그대로 얹는다 — 위저드 프레임은 배치 생성 재사용을 명시 설계(Wizard.tsx 주석).
const STEPS: WizardStep[] = [
  { id: "operation", label: "연산" },
  { id: "items", label: "대상·항목" },
  { id: "controls", label: "실행 제어" },
  { id: "confirm", label: "확인·제출" },
];

type InputTab = "table" | "paste" | "upload";

// 우선순위 순서는 PRIORITIES(domain.py:61)의 미러 — 상한 초과 판정에 쓴다.
const PRIORITY_RANK: Record<string, number> = { low: 0, mid: 1, high: 2 };

// 행은 경로만 나른다(스토리지는 배치 레벨 선택) — a = target(scan)/source(sync),
// b = destination(sync 전용). CSV 파서(ScanRow/SyncRow)와 제출 바디 조립의 중간형.
interface RowPair { a: string; b: string }

const initial = {
  op: "scan" as "scan" | "sync",
  storage: "", srcStorage: "", dstStorage: "",
  // scan 옵션(SubmitScan 미러 — dscan 1b93d54 실측 전부. top_k는 신버전에서
  // 기능 삭제라 함께 제거). scanBatchFiles는 sync의 batchFiles와 별도 상태 —
  // 같은 옵션명이지만 범위도 프리필도 다르다(scan 0..10억·0 = 배칭 끔·프리필 없음,
  // sync 1..1,000만·1,000,000 프리필).
  scanBatchFiles: "", brokenLimit: "", verbose: false, quiet: false,
  // sync 옵션(SubmitJob 미러, 정규식·범위는 optionRules 공유). 단 open_noatime 은
  // 배치만 기본 ON(사용자 승인): 배치는 통일 게이트로 항상 특권(root) 실행이라
  // O_NOATIME 권한 제약이 없고, 소스 atime 오염을 막아야 데이터 온도(hot/cold)
  // 통계가 정직해진다. dsync 도구 기본은 off(mfu_flist_copy.c:3344)라 DMS 가
  // 명시적으로 켠다. 단건 sync(SubmitJob)는 비특권 경로에서 타인 소유 파일
  // O_NOATIME open 이 EPERM 이라 기본 OFF — 배치와 다른 게 맞다(고급 옵션
  // <details> 기본 접힘이어도 바디에 실리는 진실은 이 초기값이다).
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
  priority: "", nodeCount: "", procsPerNode: "", mc: "2", note: "", name: "",
  // 실행 신원: 빈값 = 바디에서 생략 = 서버 NULL(기본: 생성자 본인). 특권 여부와
  // 무관하다 — 배치는 통일 게이트(routes_batches)로 항상 특권(root) 실행.
  ownerUsername: "",
};

export function BatchCreate() {
  const nav = useNavigate();
  const create = useCreateBatch();
  const storagesQ = useUserStorages();
  const policiesQ = usePolicies();          // 정책 기본값 캡션(표시 전용 배선)
  // 폼 값은 위저드 밖 단일 useState(SubmitJob 관례) — 스텝을 오가도 값이 보존된다.
  const [f, setF] = useState(initial);
  const [step, setStep] = useState(0);
  const [rows, setRows] = useState<RowPair[]>([{ a: "", b: "" }]);
  const [tab, setTab] = useState<InputTab>("table");
  const [csvText, setCsvText] = useState("");
  const [csvErrors, setCsvErrors] = useState<string[]>([]);

  const storages = storagesQ.data ?? [];
  const loadingStorages = storagesQ.isLoading;

  const on = (k: keyof typeof initial) => (e: any) =>
    setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });

  // --- 대상·항목 스텝 검증 ---
  const storageChosen = f.op === "scan"
    ? f.storage !== ""
    : f.srcStorage !== "" && f.dstStorage !== "";
  const rowsComplete = rows.length >= 1 && rows.every(
    (r) => r.a.trim() !== "" && (f.op === "scan" || r.b.trim() !== ""));
  const itemsValid = storageChosen && rowsComplete && csvErrors.length === 0;

  // --- 실행 제어 스텝 검증(즉답 미러 — 최종 심판은 서버 422) ---
  const verboseQuietConflict = f.op === "scan" && f.verbose && f.quiet;
  const scanBatchFilesError = f.op === "scan"
    ? intFieldError("batch_files", f.scanBatchFiles, 0, 1_000_000_000) : null;
  const brokenLimitError = f.op === "scan"
    ? intFieldError("broken_limit", f.brokenLimit, 0, 10_000) : null;
  const batchFilesError = f.op === "sync"
    ? syncIntFieldError("batch_files", f.batchFiles) : null;
  const bufsizeError = f.op === "sync"
    ? syncIntFieldError("bufsize", f.bufsize) : null;
  const chmodError = f.op === "sync" && f.chmod.trim() !== "" && !CHMOD_RE.test(f.chmod.trim())
    ? "chmod 형식이 올바르지 않습니다 (예: D770,F660)" : null;
  const chownError = f.op === "sync" && f.chown.trim() !== "" && !CHOWN_RE.test(f.chown.trim())
    ? "chown 형식이 올바르지 않습니다 (예: 10003:10000 또는 cocoa.song:mig)" : null;
  // 1..64 는 서버 위생 상한(1024)의 보수적 부분집합 — 실제 캡은 정책 max_nodes.
  const nodeCountError = intFieldError("노드 수", f.nodeCount, 1, 64);
  // 노드당 프로세스 수도 같은 부분집합 — 실제 캡은 정책 procs_per_node(min).
  const procsPerNodeError = intFieldError("노드당 프로세스 수", f.procsPerNode, 1, 64);
  // 필수 필드라 빈 값도 오류다 — intFieldError(빈 값 = 미입력 허용)와 다르다.
  const mcError = f.mc.trim() === ""
    ? "동시 실행 상한은 1..64 범위의 정수여야 합니다"
    : intFieldError("동시 실행 상한", f.mc, 1, 64);
  const controlsInvalid = verboseQuietConflict || scanBatchFilesError !== null
    || brokenLimitError !== null
    || batchFilesError !== null || bufsizeError !== null
    || chmodError !== null || chownError !== null
    || nodeCountError !== null || procsPerNodeError !== null || mcError !== null;

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
      // 빈값 = 키 생략 = 도구 기본. "0"은 정상 입력(배칭 끔)이라 생략과 다르다(null≠0).
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

  // 제출 바디와 확인 스텝 요약이 **같은 함수**에서 파생 — 화면 따로 바디 따로면
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

  const canNext = STEPS[step].id === "items" ? itemsValid
    : STEPS[step].id === "controls" ? !controlsInvalid : true;

  const tabButton = (t: InputTab, label: string) => (
    <Button type="button" variant={tab === t ? "outline" : "ghost"}
            onClick={() => setTab(t)}>{label}</Button>
  );

  return (
    <Card className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-5">배치 생성</h1>
      <Wizard steps={STEPS} current={step} onNavigate={setStep}
              canNext={canNext} onCancel={() => nav("/admin/batches")}
              submitLabel="배치 생성" submitDisabled={blocked}
              onSubmit={handleSubmit}>
        {STEPS[step].id === "operation" && (
          <div className="space-y-3">
            <label className="text-sm block">연산
              <select aria-label="연산" className={field} value={f.op}
                      onChange={(e) => setF({ ...f, op: e.target.value as "scan" | "sync" })}>
                <option value="scan">scan</option>
                <option value="sync">sync</option>
              </select>
            </label>
            <p className="text-muted text-sm">
              한 배치는 하나의 스토리지만 대상으로 합니다 — 행은 경로만 입력합니다.
            </p>
          </div>
        )}

        {STEPS[step].id === "items" && (
          <div className="space-y-3">
            {storagesQ.isError && (
              <p className="text-bad text-sm">{(storagesQ.error as ApiError).message}</p>
            )}
            {f.op === "scan" ? (
              <StoragePicker label="스토리지" value={f.storage}
                onChange={(v) => setF({ ...f, storage: v })}
                storages={storages} loading={loadingStorages} />
            ) : (
              <div className="grid grid-cols-2 gap-3">
                <StoragePicker label="소스 스토리지" value={f.srcStorage}
                  onChange={(v) => setF({ ...f, srcStorage: v })}
                  storages={storages} loading={loadingStorages} />
                <StoragePicker label="목적지 스토리지" value={f.dstStorage}
                  onChange={(v) => setF({ ...f, dstStorage: v })}
                  storages={storages} loading={loadingStorages} />
              </div>
            )}

            <div className="flex gap-2">
              {tabButton("table", "테이블 편집")}
              {tabButton("paste", "CSV 붙여넣기")}
              {tabButton("upload", "파일 업로드")}
              <Button type="button" variant="ghost" onClick={downloadCsv}>CSV 다운로드</Button>
            </div>

            {tab === "table" && (
              <div className="space-y-2">
                {rows.map((r, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className="text-muted text-xs w-8 shrink-0">{i + 1}</span>
                    {/* placeholder 예시는 스토리지 기준 상대경로(선행 슬래시 없음) —
                        경로가 스토리지 루트 기준임을 힌트로 드러낸다 */}
                    <input aria-label={`${i + 1}행 ${f.op === "scan" ? "경로" : "소스"}`}
                           placeholder={f.op === "scan" ? "예: team/projects" : "예: team/dataset"}
                           className={field} value={r.a}
                           onChange={(e) => setRow(i, "a", e.target.value)} />
                    {f.op === "sync" && (
                      <input aria-label={`${i + 1}행 목적지`} placeholder="예: backup/dataset"
                             className={field} value={r.b}
                             onChange={(e) => setRow(i, "b", e.target.value)} />
                    )}
                    <Button type="button" variant="ghost" aria-label={`${i + 1}행 삭제`}
                            onClick={() => { setRows(rows.filter((_, j) => j !== i)); setCsvErrors([]); }}>
                      ✕
                    </Button>
                  </div>
                ))}
                <Button type="button" variant="outline" aria-label="행 추가"
                        onClick={() => setRows([...rows, { a: "", b: "" }])}>
                  행 추가
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
              <ul className="text-bad text-sm space-y-1">
                {csvErrors.map((err, i) => <li key={i}>{err}</li>)}
              </ul>
            )}
            <div className="text-sm text-muted">행: {rows.length}</div>
          </div>
        )}

        {STEPS[step].id === "controls" && (
          <div className="space-y-3">
            {f.op === "scan" ? (
              <>
                <label className="text-sm block">batch_files (선택 · 0..1,000,000,000 · 0 = 배칭 끔)
                  <input aria-label="batch_files" type="number" min={0} max={1000000000}
                         placeholder="기본 1000000 · 0 = 배칭 끔"
                         className={field} value={f.scanBatchFiles}
                         onChange={on("scanBatchFiles")} />
                </label>
                {scanBatchFilesError && <p className="text-bad text-sm">{scanBatchFilesError}</p>}
                <label className="text-sm block">broken_limit (선택 · 0..10,000 · 리포트에 보관할 파손 경로 수)
                  <input aria-label="broken_limit" type="number" min={0} max={10000}
                         placeholder="기본 100"
                         className={field} value={f.brokenLimit}
                         onChange={on("brokenLimit")} />
                </label>
                {brokenLimitError && <p className="text-bad text-sm">{brokenLimitError}</p>}
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="verbose" checked={f.verbose}
                         onChange={on("verbose")} /> verbose
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="quiet" checked={f.quiet}
                         onChange={on("quiet")} /> quiet
                </label>
                {verboseQuietConflict && (
                  <p className="text-bad text-sm">verbose와 quiet은 함께 쓸 수 없습니다</p>
                )}
              </>
            ) : (
              <>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="delete" checked={f.delete}
                         onChange={on("delete")} /> delete
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="contents" checked={f.contents}
                         onChange={on("contents")} /> contents
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="direct" checked={f.direct}
                         onChange={on("direct")} /> direct
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input type="checkbox" aria-label="quiet" checked={f.quiet}
                         onChange={on("quiet")} /> quiet
                </label>
                <details className="rounded-lg border border-line p-3">
                  <summary className="cursor-pointer text-sm font-medium">고급 옵션</summary>
                  <div className="mt-3 space-y-3">
                    <label className="flex items-center gap-2 text-sm">
                      <input type="checkbox" aria-label="open_noatime" checked={f.openNoatime}
                             onChange={on("openNoatime")} /> open_noatime
                    </label>
                    {/* 기본 ON 의 「왜」는 initial 의 openNoatime 주석 — 캡션은
                        사용자에게 같은 근거를 요약해 준다(끄는 건 자유). */}
                    <p className="text-muted text-xs">
                      기본 켬 — 배치는 특권(root) 실행이라 O_NOATIME 권한 제약이 없고,
                      소스 atime 오염을 막아 데이터 온도(hot/cold) 통계를 정직하게 유지합니다.
                    </p>
                    {/* 프리필 계약(단건 폼과 동일 문구): 값이 미리 채워져 있고
                        비우면 키가 빠져 도구 기본으로 돌아간다 — placeholder 는
                        "비웠을 때", 캡션은 "지금 채워진 값"을 말한다. */}
                    <label className="text-sm block">batch_files (선택 · 1..10,000,000)
                      <input aria-label="batch_files"
                             placeholder="비우면 배칭 안 함(도구 기본)"
                             className={field} value={f.batchFiles}
                             onChange={on("batchFiles")} />
                    </label>
                    <p className="text-muted text-xs">
                      미리 채운 1,000,000 = 기본 배치 사이즈 100만. 비우면 배칭 안 함(도구 기본).
                    </p>
                    {batchFilesError && <p className="text-bad text-sm">{batchFilesError}</p>}
                    <label className="text-sm block">bufsize (선택 · 바이트, 4096..1,073,741,824)
                      <input aria-label="bufsize" placeholder="비우면 4 MiB(도구 기본)"
                             className={field} value={f.bufsize}
                             onChange={on("bufsize")} />
                    </label>
                    <p className="text-muted text-xs">
                      미리 채운 4194304 = 4 MiB. 비우면 4 MiB(도구 기본).
                    </p>
                    {bufsizeError && <p className="text-bad text-sm">{bufsizeError}</p>}
                    <label className="text-sm block">chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)
                      <input aria-label="chmod" placeholder="예: D770,F660"
                             className={field} value={f.chmod} onChange={on("chmod")} />
                    </label>
                    {chmodError && <p className="text-bad text-sm">{chmodError}</p>}
                    <label className="text-sm block">chown (선택 · user:group 또는 uid:gid)
                      <input aria-label="chown" placeholder="예: 10003:10000 또는 cocoa.song:mig"
                             className={field} value={f.chown} onChange={on("chown")} />
                    </label>
                    {chownError && <p className="text-bad text-sm">{chownError}</p>}
                    {/* 배치는 통일 게이트로 전부 특권(root) 실행 — 단건(SubmitJob)의
                        비특권 함정 캡션은 여기선 거짓이라 싣지 않는다. 특권 실행의
                        "비우면" 기본은 소스 소유권 보존(_auto_chown 무개입 분기). */}
                    <p className="text-muted text-xs">
                      비우면 원래 소유권 보존(특권 실행 기준). 지정하면 자동 chown 이 꺼지고
                      목적지가 그 소유자로 기록됩니다.
                    </p>
                  </div>
                </details>
              </>
            )}

            <label className="text-sm block">우선순위
              <select aria-label="우선순위" className={field} value={f.priority} onChange={on("priority")}>
                <option value="">{priorityDefaultLabel}</option>
                <option value="low">low</option>
                <option value="mid">mid</option>
                <option value="high">high</option>
              </select>
              {priorityCapCaption !== null && (
                <p className="text-muted text-xs mt-1">{priorityCapCaption}</p>
              )}
            </label>

            {/* 통일 특권 게이트(routes_batches.create_batch): 배치는 전부 관리자
                특권(root) 실행이다 — owner 입력은 특권 스위치가 아니므로 안내문은
                입력과 무관하게 고정이다. 인가의 최종 심판은 서버 게이트
                (403 privileged_not_authorized). */}
            <p className="text-muted text-sm">이 배치는 관리자 특권(root)으로 실행됩니다.</p>
            {/* 라벨 정정(사용자 결정 2026-08-16): 이 값은 결과물의 소유자 기록이
                아니라 **잡의 실행 신원**이다(identity.resolve_job_identity —
                owner = owner_username or requester_id). 실행이 root 라는 사실은
                위 고정 안내문이 이미 말하므로 여기선 반복하지 않는다 — 캡션은
                "누구의 신원으로 파일을 다루는가"만 말한다(문구 중복 = 소음). */}
            <label className="text-sm block">실행 신원(선택)
              <input aria-label="실행 신원(선택)" placeholder="예: cocoa.song"
                     className={field}
                     value={f.ownerUsername} onChange={on("ownerUsername")} />
              <p className="text-muted text-xs mt-1">
                비우면 생성자 본인 신원으로 실행됩니다. 지정하면 그 사용자 신원으로
                파일을 다룹니다(LDAP 에 없는 계정도 지정할 수 있습니다).
              </p>
            </label>

            <label className="text-sm block">노드 수 (선택 · 1..64, 빈값 = 정책 기본)
              <input aria-label="노드 수" type="number" min={1} max={64} className={field}
                     placeholder="비우면 정책 기본"
                     value={f.nodeCount} onChange={on("nodeCount")} />
              {/* "정책 기본"의 실값 — 잡 하나가 몇 노드로 퍼지는가. "노드당
                  P프로세스"까지 병기돼 있어 아래 노드당 프로세스 수 입력과도 짝이다 */}
              <p className="text-muted text-xs mt-1">{policyCaption}</p>
              {f.op === "sync" && (
                // nsync 면당 캡(placement.py:121-122): resolve_fanout 은 nsync
                // (공존 노드 없음 폴백)에서 max_nodes·요청 node_count 를 출발·
                // 목적지 **각각**에 min-캡한다 — 입력값이 면당 상한이라 총 노드는
                // 최대 2배. dsync 는 공존 노드 단일 집합(primary)이라 해당 없음.
                // 정책 조회 상태와 무관한 배치 메커니즘 사실이라 항상 표기한다.
                <p className="text-muted text-xs mt-1">
                  nsync 폴백 시 입력한 노드 수는 출발·목적지 각각(면당)의 상한이라
                  총 노드는 최대 2배가 될 수 있습니다.
                </p>
              )}
            </label>
            {nodeCountError && <p className="text-bad text-sm">{nodeCountError}</p>}

            <label className="text-sm block">노드당 프로세스 수 (선택 · 1..64, 빈값 = 정책 기본)
              <input aria-label="노드당 프로세스 수" type="number" min={1} max={64}
                     className={field} placeholder="비우면 정책 기본"
                     value={f.procsPerNode} onChange={on("procsPerNode")} />
              {/* 실값 캡션은 위 policyCaption 이 이미 "노드당 P프로세스"를 보여준다.
                  요청은 정책을 줄일 수만 있다(planner min-캡) — 늘리려면 정책 수정 */}
            </label>
            {procsPerNodeError && <p className="text-bad text-sm">{procsPerNodeError}</p>}

            <label className="text-sm block">동시 실행 상한 (1..64)
              <input aria-label="동시 실행 상한" type="number" min={1} max={64} className={field}
                     placeholder="예: 2"
                     value={f.mc} onChange={(e) => setF({ ...f, mc: e.target.value })} />
              {/* 노드 수 캡션과 대구: 위는 잡 하나의 폭, 이것은 잡 몇 개를 나란히 */}
              <p className="text-muted text-xs mt-1">
                동시에 실행할 배치 항목(잡) 수 — 잡 하나가 쓰는 노드 수와 무관합니다.
              </p>
            </label>
            {mcError && <p className="text-bad text-sm">{mcError}</p>}

            {/* 이름은 목록·상세 헤더에 얹히는 식별자, 메모는 자유 기록 — 역할이
                달라 별도 입력이다. 둘 다 빈값 = 없음(이름은 키 생략, 메모는 null). */}
            <label className="text-sm block">배치 이름(선택)
              <input aria-label="배치 이름" placeholder="예: 8월 정기 스캔 1차"
                     maxLength={120} className={field} value={f.name} onChange={on("name")} />
            </label>

            <label className="text-sm block">메모(선택)
              <input aria-label="메모" placeholder="예: 8월 정기 스캔"
                     className={field} value={f.note} onChange={on("note")} />
            </label>
          </div>
        )}

        {STEPS[step].id === "confirm" && (() => {
          const body = buildBody();   // 요약 = 제출 바디에서 파생(화면 거짓말 금지)
          return (
            <div className="space-y-3">
              <InfoPanel>
                <dl className="space-y-1">
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">연산</dt>
                    <dd>{body.operation}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">스토리지</dt>
                    <dd>{f.op === "scan" ? f.storage : `${f.srcStorage} → ${f.dstStorage}`}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">항목 수</dt>
                    <dd>{body.items.length}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">옵션</dt>
                    <dd>{JSON.stringify(body.options)}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">우선순위</dt>
                    <dd>{body.priority ?? "(정책 기본)"}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">노드 수</dt>
                    <dd>{body.node_count ?? "(정책 기본)"}</dd>
                  </div>
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">노드당 프로세스</dt>
                    <dd>{body.procs_per_node ?? "(정책 기본)"}</dd>
                  </div>
                  {/* 특권 실행 표시는 고정 행(통일 게이트 — 입력과 무관), 실행
                      신원 행은 값이 있을 때만(빈값 = 생성자 본인) */}
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">실행 권한</dt>
                    <dd>관리자 특권(root)</dd>
                  </div>
                  {body.owner_username && (
                    <div className="flex gap-2">
                      <dt className="w-28 shrink-0 text-muted">실행 신원</dt>
                      <dd>{body.owner_username}</dd>
                    </div>
                  )}
                  <div className="flex gap-2">
                    <dt className="w-28 shrink-0 text-muted">동시 상한</dt>
                    <dd>{body.max_concurrency}</dd>
                  </div>
                  {body.name && (
                    <div className="flex gap-2">
                      <dt className="w-28 shrink-0 text-muted">이름</dt>
                      <dd>{body.name}</dd>
                    </div>
                  )}
                  {body.note && (
                    <div className="flex gap-2">
                      <dt className="w-28 shrink-0 text-muted">메모</dt>
                      <dd>{body.note}</dd>
                    </div>
                  )}
                </dl>
              </InfoPanel>
              {create.isError && (
                <p className="text-bad text-sm">{(create.error as ApiError).message}</p>
              )}
            </div>
          );
        })()}
      </Wizard>
    </Card>
  );
}
