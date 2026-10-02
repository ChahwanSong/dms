import { useState } from "react";
import { Dialog } from "../../components/ui/Dialog";
import { Button } from "../../components/ui/Button";
import { field } from "../jobs/formFields";
import {
  CHMOD_RE, chownFieldError, intFieldError, scanIntFieldError, syncIntFieldError,
  SCAN_INT_FIELDS, SYNC_INT_FIELDS,
} from "../jobs/optionRules";
import { ApiError } from "../../lib/api";
import type { Batch } from "../../lib/types";
import { useUpdateBatchExecution, type BatchExecutionBody } from "./useBatches";

// 종단(완료·취소) 배치의 실행 설정 변경(사용자 요청 2026-10-02: 배치를 취소한 뒤 재실행할 때 노드·프로세스 수·
// 옵션 등을 바꿀 수 있어야 한다). 서버 PATCH /execution — 바뀐 값은 **다음 재실행**의 자식부터 적용되고 이미 끝난
// 항목의 기록은 그대로다(orchestrator 가 자식을 만들 때 배치 행을 읽는다). 실행 신원·연산·항목은 여기 없다.
//
// 입력 규칙은 배치 생성 폼(BatchCreate)과 같은 출처(optionRules)다 — 노드 수·노드당 프로세스 1..64, 동시 실행 1..64
// 필수, 옵션 범위·chmod·chown(숫자 uid:gid). 최종 심판은 서버 422.
// options 는 통째 교체 계약이다. 아래 키 집합은 서버 옵션 스펙(domain._OPTION_SPECS)과 **같다**(계약 테스트
// test_batch_execution_dialog_contract 가 고정) — 그래서 이 화면이 모르는 키는 서버가 unknown_option 으로 거부하는
// 옛 옵션(예: scan top_k)뿐이고, 남겨 보내면 저장이 늘 422 다. 모르는 키는 "저장하면 빠집니다"로 보여 주고 뺀다
// (이 화면이 그런 옛 배치를 다시 돌릴 수 있게 고치는 길이다).

const BOOLS = { scan: ["verbose", "quiet"], sync: ["delete", "contents", "direct", "quiet", "open_noatime"] } as const;
const INTS = { scan: ["batch_files", "broken_limit"], sync: ["batch_files", "bufsize"] } as const;
const STRS = { scan: [], sync: ["chmod", "chown"] } as const;
type Op = "scan" | "sync";
const opOf = (b: Batch): Op => (b.operation === "sync" ? "sync" : "scan");
const knownKeys = (op: Op): string[] => [...BOOLS[op], ...INTS[op], ...STRS[op]];

interface Form {
  mc: string; priority: string; nodeCount: string; procsPerNode: string;
  bools: Record<string, boolean>; ints: Record<string, string>; strs: Record<string, string>;
}

// 폼 초기값 = 지금 배치 행. 키가 없는 정수 옵션은 "" (= 서버 기본) — 생성 폼처럼 기본값을 프리필하지 않는다: 그
// 배치가 실제로 무엇을 저장했는지(키 없음)를 그대로 보여 주는 편이 정직하다. null 실행 제어도 "" (= 정책 기본).
function initialForm(b: Batch): Form {
  const op = opOf(b);
  const o = b.options ?? {};
  const str = (v: unknown) => (v === undefined || v === null ? "" : String(v));
  return {
    mc: String(b.max_concurrency), priority: b.priority ?? "",
    nodeCount: str(b.node_count), procsPerNode: str(b.procs_per_node),
    bools: Object.fromEntries(BOOLS[op].map((k) => [k, o[k] === true])),
    ints: Object.fromEntries(INTS[op].map((k) => [k, str(o[k])])),
    strs: Object.fromEntries(STRS[op].map((k) => [k, str(o[k])])),
  };
}

// 서버가 거부하는 옛 옵션 키(저장하면 빠진다)
const droppedKeys = (b: Batch): string[] =>
  Object.keys(b.options ?? {}).filter((k) => !knownKeys(opOf(b)).includes(k));

function buildBody(b: Batch, f: Form): BatchExecutionBody {
  const op = opOf(b);
  const options: Record<string, unknown> = {};
  for (const k of BOOLS[op]) if (f.bools[k]) options[k] = true;
  // 빈 정수 = 키 생략 = 서버 기본. "0" 은 정상 입력(배칭 끔)이라 생략과 다르다(null≠0).
  for (const k of INTS[op]) if (f.ints[k].trim() !== "") options[k] = Number(f.ints[k].trim());
  for (const k of STRS[op]) if (f.strs[k].trim() !== "") options[k] = f.strs[k].trim();
  const intOrNull = (s: string) => (s.trim() === "" ? null : Number(s.trim()));
  return {
    max_concurrency: Number(f.mc.trim()), priority: f.priority === "" ? null : f.priority,
    node_count: intOrNull(f.nodeCount), procs_per_node: intOrNull(f.procsPerNode), options,
  };
}

// 키 순서와 무관한 비교(서버 JSON 왕복이 키 순서를 보장하지 않는다) — "바뀐 것 없음" 판정용. 저장된 명시 false
// (API 로 만든 배치의 open_noatime:false 등)는 키 없음과 같은 뜻이라 접는다 — 화면은 true 만 싣기 때문에, 접지 않으면
// 손대지 않은 폼이 "바뀜"으로 보여 아무 의미 없는 저장·감사 행이 생긴다.
const stable = (v: Record<string, unknown>) =>
  JSON.stringify(Object.keys(v).filter((k) => v[k] !== false).sort().map((k) => [k, v[k]]));

export function BatchExecutionSettingsDialog({ b }: { b: Batch }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<Form>(() => initialForm(b));
  const save = useUpdateBatchExecution(b.batch_id);
  const op = opOf(b);
  // 열 때마다 지금 배치 값으로 다시 채운다 — 닫았다 다시 열면 지난 미저장 입력이 아니라 서버 상태가 보여야 한다.
  const onOpenChange = (o: boolean) => {
    if (o) { setF(initialForm(b)); save.reset(); }
    setOpen(o);
  };

  const mcError = f.mc.trim() === ""
    ? "동시 실행 상한은 1..64 범위의 정수여야 합니다"
    : intFieldError("동시 실행 상한", f.mc, 1, 64);
  // 1..64 는 생성 폼과 같은 화면 상한(서버 위생 상한은 1024). 저장된 값을 그대로 두면 검사하지 않는다 — API 로 만든
  // 배치의 100 같은 유효값이 다른 설정(예: chown 고치기)까지 막지 않게.
  const keptOr = (raw: string, stored: number | null | undefined, label: string) =>
    raw.trim() === String(stored ?? "") ? null : intFieldError(label, raw, 1, 64);
  const nodeCountError = keptOr(f.nodeCount, b.node_count, "노드 수");
  const procsPerNodeError = keptOr(f.procsPerNode, b.procs_per_node, "노드당 프로세스 수");
  const intErrors = INTS[op].map((k) => (op === "scan"
    ? scanIntFieldError(k as keyof typeof SCAN_INT_FIELDS, f.ints[k])
    : syncIntFieldError(k as keyof typeof SYNC_INT_FIELDS, f.ints[k])));
  const chmodError = op === "sync" && f.strs.chmod.trim() !== "" && !CHMOD_RE.test(f.strs.chmod.trim())
    ? "chmod 형식이 올바르지 않습니다 (예: D770,F660)" : null;
  const chownError = op === "sync" ? chownFieldError(f.strs.chown) : null;
  const verboseQuiet = op === "scan" && f.bools.verbose && f.bools.quiet
    ? "verbose와 quiet은 함께 쓸 수 없습니다" : null;
  const errors = [mcError, nodeCountError, procsPerNodeError, ...intErrors, chmodError, chownError, verboseQuiet];
  const invalid = errors.some((e) => e !== null);

  // 바뀐 것이 없으면 저장을 잠근다(서버도 같은 값이면 쓰지 않지만, 누를 이유가 없는 버튼은 헛클릭이다).
  const body = invalid ? null : buildBody(b, f);
  const unchanged = body !== null
    && body.max_concurrency === b.max_concurrency && body.priority === (b.priority ?? null)
    && body.node_count === (b.node_count ?? null) && body.procs_per_node === (b.procs_per_node ?? null)
    && stable(body.options) === stable(b.options ?? {});

  const setBool = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, bools: { ...f.bools, [k]: e.target.checked } });
  const setInt = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, ints: { ...f.ints, [k]: e.target.value } });
  const setStr = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, strs: { ...f.strs, [k]: e.target.value } });
  const err = (e: string | null) => e && <p className="text-bad text-sm">{e}</p>;
  const extras = droppedKeys(b);

  return (
    <Dialog open={open} onOpenChange={onOpenChange} title="실행 설정 변경" size="lg"
            trigger={<Button variant="ghost">실행 설정 변경</Button>}>
      <div className="space-y-3">
        <p className="text-muted text-sm">
          바꾼 값은 다음 재실행(전체·실패분·선택 재실행, 항목 추가)부터 적용됩니다. 이미 끝난 항목의 기록은 그대로
          남고, 실행 신원·연산·항목은 여기서 바꾸지 않습니다.
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-sm block">동시 실행 상한 (1..64)
            {/* 숫자 칸은 text + inputMode: type=number 는 "8e" 같은 잘못된 글자를 value "" 로 돌려줘, 노드 수가 빈칸
                (= 정책 기본)으로 조용히 저장됐다(리뷰 2026-10-02). 원문을 받아 intFieldError 가 거른다. */}
            <input aria-label="동시 실행 상한" inputMode="numeric" className={field}
                   value={f.mc} onChange={(e) => setF({ ...f, mc: e.target.value })} />
            {err(mcError)}
          </label>
          <label className="text-sm block">우선순위
            <select aria-label="우선순위" className={field} value={f.priority}
                    onChange={(e) => setF({ ...f, priority: e.target.value })}>
              <option value="">정책 기본</option>
              <option value="low">low</option>
              <option value="mid">mid</option>
              <option value="high">high</option>
            </select>
          </label>
          <label className="text-sm block">노드 수 (1..64, 빈값 = 정책 기본)
            <input aria-label="노드 수" inputMode="numeric" className={field}
                   placeholder="비우면 정책 기본"
                   value={f.nodeCount} onChange={(e) => setF({ ...f, nodeCount: e.target.value })} />
            {err(nodeCountError)}
          </label>
          <label className="text-sm block">노드당 프로세스 수 (1..64, 빈값 = 정책 기본)
            <input aria-label="노드당 프로세스 수" inputMode="numeric" className={field}
                   placeholder="비우면 정책 기본"
                   value={f.procsPerNode} onChange={(e) => setF({ ...f, procsPerNode: e.target.value })} />
            {err(procsPerNodeError)}
          </label>
        </div>
        <p className="text-muted text-xs">요청 값은 정책 상한을 넘지 못합니다(정책보다 줄이기만 가능).</p>

        <fieldset className="rounded-lg border border-line p-3 space-y-2">
          <legend className="px-1 text-sm font-medium">{`${op} 옵션`}</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            {BOOLS[op].map((k) => (
              <label key={k} className="flex items-center gap-2 text-sm">
                <input type="checkbox" aria-label={k} checked={f.bools[k]} onChange={setBool(k)} /> {k}
              </label>
            ))}
          </div>
          {err(verboseQuiet)}
          {INTS[op].map((k, i) => (
            <label key={k} className="text-sm block">
              {op === "scan"
                ? `${k} (${SCAN_INT_FIELDS[k as keyof typeof SCAN_INT_FIELDS].lo.toLocaleString("ko-KR")}..${
                    SCAN_INT_FIELDS[k as keyof typeof SCAN_INT_FIELDS].hi.toLocaleString("ko-KR")})`
                : `${k} (${SYNC_INT_FIELDS[k as keyof typeof SYNC_INT_FIELDS].lo.toLocaleString("ko-KR")}..${
                    SYNC_INT_FIELDS[k as keyof typeof SYNC_INT_FIELDS].hi.toLocaleString("ko-KR")})`}
              <input aria-label={k} className={field} placeholder="비우면 서버 기본"
                     value={f.ints[k]} onChange={setInt(k)} />
              {err(intErrors[i])}
            </label>
          ))}
          {op === "sync" && (<>
            <label className="text-sm block">chmod (예: D770,F660)
              <input aria-label="chmod" className={field} placeholder="비우면 지정 안 함"
                     value={f.strs.chmod} onChange={setStr("chmod")} />
              {err(chmodError)}
            </label>
            <label className="text-sm block">chown (숫자 uid:gid)
              <input aria-label="chown" className={field} placeholder="비우면 소스 소유권 보존"
                     value={f.strs.chown} onChange={setStr("chown")} />
              {err(chownError)}
            </label>
          </>)}
          {extras.length > 0 && (
            <p role="note" aria-label="지원하지 않는 옵션" className="rounded-lg border border-line px-3 py-2 text-xs">
              {`지원하지 않는 옛 옵션 — 저장하면 빠집니다(남아 있으면 재실행이 거부됩니다): ${
                extras.map((k) => `${k}=${JSON.stringify((b.options ?? {})[k])}`).join(" · ")}`}
            </p>
          )}
        </fieldset>

        {save.isError && <p role="alert" className="text-bad text-sm">{(save.error as ApiError).message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>닫기</Button>
          <Button disabled={invalid || unchanged || save.isPending}
                  onClick={() => body && save.mutate(body, { onSuccess: () => setOpen(false) })}>
            {unchanged ? "변경 없음" : "저장"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
