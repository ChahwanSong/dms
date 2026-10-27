import { FileText, Info, TriangleAlert } from "lucide-react";
import type { DataJob } from "../../lib/types";
import { reasonText } from "../../lib/api";
import {
  ACTION_LABELS, errnoText, failureCounts, hasFailureItems, kindsText, opLabel, previewWarnings, readSummary, scanSubject,
  sumKinds, summaryTool, withObj, type ActionKind, type ReadSummary,
} from "../../lib/toolSummary";
import { bytesText, countText, humanBytes, isPlainObject } from "./format";
import { SMALL_BTN } from "./ui";

// 단계 구획 맨 위의 결과 타일. 한 값은 **한 곳에만** 보인다 -- 미리보기 summary 는 ①, 실행 summary 는 ②.
// 미리보기가 실패하면 stepper(_surface_failed_artifact)가 미리보기 summary 를 result_summary 에 싣는다 -- 그 값을
// ②의 「실행 결과」로 그리면 "실행했다" 는 거짓이 되므로 ①의 「미리보기 결과 (실패 시점)」로만 보인다(stageModel
// resultStage).
//
// 2026-10-27 d192(summary v2, 포탈 괴리 [1]·[2]·[4]·[5]·[6]·[17]): 결과를 원 키(files·bytes) 대신 **도구별 뜻**으로 그린다.
// 같은 files 가 nsync 실행은 「실제로 반영한 수(종류 합)」, dsync 실행은 「복사한 항목」, drm 은 「지운 수」, 미리보기는
// 각각 「변경 예정」·「소스 전체」·「지울 대상」이다 -- 원 키 하나로 그리면 nsync 의 계획 합이 실패한 실행에서도 「복사한
// 파일」처럼 읽혔다. 판독은 lib/toolSummary 하나(같은 summary 를 컨펌 창·배너·배치 화면이 같은 뜻으로 읽는다). 옛 잡
// (3키만)은 상세가 없어 핵심 수만 중립 라벨로 그린다. scan 결과는 도구마다 키가 다르고 원 키가 운영자의 검색어라 예전
// 원 키 타일 그대로다.

const TILE = "min-w-0 rounded-lg border border-line px-3 py-2";
const GRID = "grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4";
const H5 = "mb-2 text-xs font-semibold text-ink/70";

export interface ResultTile { key: string; label: string; value: string; sub?: string | null; bad?: boolean }

function Tiles({ tiles }: { tiles: ResultTile[] }) {
  return (
    <dl className={GRID}>
      {tiles.map((t) => (
        <div key={t.key} className={TILE}>
          {/* 라벨은 그 값의 유일한 이름이라 장식이 아니다 -- ink/70(AA). text-muted 는 3.54:1 이었다(리뷰 3차). */}
          <dt className="text-xs text-ink/70 break-keep">{t.label}</dt>
          <dd className={`text-sm font-semibold tabular-nums [overflow-wrap:anywhere] ${t.bad === true ? "text-bad" : ""}`}>{t.value}</dd>
          {t.sub ? <dd className="text-xs text-ink/70 tabular-nums break-keep">{t.sub}</dd> : null}
        </div>
      ))}
    </dl>
  );
}

export interface ResultNote { tone: "warn" | "info"; text: string }

// 주의·안내 줄. 색만으로 구분하지 않는다 -- 아이콘 + 화면 낭독용 단어(「주의」·「안내」).
function Notes({ notes, label }: { notes: ResultNote[]; label: string }) {
  if (!notes.length) return null;
  return (
    <ul aria-label={label} className="mt-2 space-y-1 text-sm">
      {notes.map((n, i) => (
        <li key={i} className="flex items-start gap-1.5">
          {n.tone === "warn"
            ? <TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-attn" />
            : <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-ink/70" />}
          <span className="sr-only">{n.tone === "warn" ? "주의" : "안내"}</span>
          <span className="min-w-0 break-keep [overflow-wrap:anywhere]">{n.text}</span>
        </li>
      ))}
    </ul>
  );
}

// 실패 경로 표시: 제어 문자(dsync·drm 은 이름을 날것으로 찍는다 -- 줄바꿈이 든 이름이 있을 수 있다)를 \xHH 로 보이게
// 한다. 화면에서 경로 한 줄이 두 줄로 갈라져 다른 항목처럼 읽히지 않게(nsync 는 도구가 이미 이렇게 찍는다).
export function visiblePath(p: string): string {
  // eslint-disable-next-line no-control-regex
  return p.replace(/[\u0000-\u001f\u007f]/g, (c) => `\\x${c.charCodeAt(0).toString(16).padStart(2, "0")}`);
}

// 「실패 항목」 -- 총계 + 연산별 칩 + 앞 10개 예시(연산 · 경로 · 이유) + 전체 목록이 있는 곳. 배너·사유 문구가 이 이름
// (「실패 항목」)과 stdout.log 를 가리킨다(api.ts REASON_MESSAGES 의 sync_items_failed 등).
function FailureBlock({ s, tool, phaseWord, onOpenOutput }: {
  s: ReadSummary; tool: string | null; phaseWord: string; onOpenOutput?: (() => void) | null;
}) {
  const { total, byOp } = failureCounts(s, tool);
  const samples = s.failureSamples;
  const more = total !== null && total > samples.length;
  return (
    <section aria-label="실패 항목" className="mt-3 rounded-lg border border-bad/30 bg-badbg/30 px-3 py-2">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h6 className="text-sm font-semibold text-bad">{total !== null ? `실패 항목 ${countText(total)}` : "실패 항목"}</h6>
        {byOp.length > 0 && (
          <ul aria-label="연산별 실패 수" className="flex flex-wrap gap-1.5">
            {byOp.map(([label, n]) => (
              <li key={label} className="rounded-full border border-bad/30 bg-surface px-2 py-0.5 text-xs text-bad tabular-nums">
                {`${label} ${n.toLocaleString("ko-KR")}`}
              </li>
            ))}
          </ul>
        )}
      </div>
      {samples.length > 0 && (
        <ol aria-label="실패 항목 예시" className="mt-2 space-y-1.5">
          {samples.map((x, i) => (
            <li key={i} className="min-w-0 text-sm">
              <span className="font-medium">{opLabel(x.op)}</span>
              <span className="text-ink/70">{` · ${errnoText(x.errno)}${x.errno !== null ? ` (errno ${x.errno})` : ""}`}</span>
              <code className="mt-0.5 block font-mono text-xs text-ink break-all">{visiblePath(x.path)}</code>
            </li>
          ))}
        </ol>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink/70">
        <p className="min-w-0 flex-1 break-keep">
          {more ? `앞의 ${samples.length.toLocaleString("ko-KR")}개만 보입니다 — ` : ""}
          {`전체 목록과 원문은 ${phaseWord} 출력(stdout.log)에 있습니다`}
        </p>
        {onOpenOutput && (
          <button type="button" className={SMALL_BTN} onClick={onOpenOutput}>
            <FileText aria-hidden className="h-3.5 w-3.5" />stdout.log 열기
          </button>
        )}
      </div>
    </section>
  );
}

const STOP_CAUSE: Record<string, string> = {
  max_errors: "실패가 허용 한도에 닿아 ",
  destination_full: "목적지 공간·쿼터가 가득 차 ",
  destination_read_only: "목적지가 읽기 전용이라 ",
};

// 비정상 종료(MPI 중단) 안내 -- 실행 결과·scan 원 키 타일이 같은 문장을 쓴다. 중단된 도구는 마지막 요약 블록을 찍지 못해
// 반영 수를 보고하지 않는다(러너도 그 수를 null 로 둔다 -- dsync 의 「Items:」는 복사 전 계획 줄에도 있어 그 수를 반영으로
// 읽으면 거짓이었다, d192 리뷰 portal-jobs-12). 그래서 「중단 전까지 보고된 값」이라 하지 않는다. ABORT 줄은 stderr 에만 있다.
const ABORTED_NOTE = "도구가 비정상 종료했습니다(MPI 중단) — 끝까지 실행되지 않아 무엇이 반영됐는지 도구가 보고하지 않았습니다. "
  + "원인은 실행 로그의 stderr 발췌나 stderr.log 의 ABORT 줄에 있습니다";

/** 실행 결과의 주의·안내 줄(순수 -- 테스트가 문장을 고정한다). quiet = summary 의 quiet 또는 잡 옵션의 quiet.
 *  deleteOn = 잡 옵션의 삭제(sync --delete) -- 「삭제를 켜고 다시 실행하면 바뀝니다」 조언은 꺼져 있었을 때만 맞다. */
export function resultNotes(s: ReadSummary, tool: string | null, quiet: boolean, deleteOn = false): ResultNote[] {
  const out: ResultNote[] = [];
  const n = (x: number) => x.toLocaleString("ko-KR");
  const failureBlock = hasFailureItems(s, tool);
  if (quiet) {
    out.push({ tone: "warn", text: "quiet 로 실행돼 도구가 세부 결과를 남기지 않았습니다 — 개수·실패 항목을 알 수 없습니다(quiet 를 끄고 다시 실행하면 기록됩니다)" });
  }
  if (s.aborted) out.push({ tone: "warn", text: ABORTED_NOTE });
  if (s.stoppedEarly || (s.notAttempted !== null && s.notAttempted > 0)) {
    const cause = s.stopCause !== null ? STOP_CAUSE[s.stopCause] ?? "" : "";
    const rest = s.notAttempted !== null && s.notAttempted > 0 ? `남은 작업 ${n(s.notAttempted)}개는 시도하지 않았습니다`
      : "뒤 배치는 실행하지 않았습니다";
    out.push({ tone: "warn", text: `${cause}중간에 멈췄습니다 — ${rest}(위 수는 멈추기 전까지)` });
  }
  // 타입 교체 거부. 「삭제를 켜면 바뀐다」는 조언은 러너가 그 경우로 판정했고(sync_type_change_refused -- 비어 있지 않은
  // 디렉토리 + 삭제 꺼짐뿐) 잡도 삭제가 꺼져 있었을 때만 한다(d192 리뷰 portal-jobs-14). 권한 오류(EACCES)·이미 삭제를 켠
  // 실행에 그 조언을 하면 되돌릴 수 없는 삭제를 켜게 하고도 교체는 여전히 실패한다 -- 그때는 지우지 못했다는 사실과 이유가
  // 있는 곳만 말한다.
  const refused = s.typeChangeRefused ?? s.failuresByOp?.["replace-dir"] ?? null;
  if (refused !== null && refused > 0) {
    out.push({ tone: "warn", text: s.reason === "sync_type_change_refused" && !deleteOn
      ? `목적지의 비어 있지 않은 디렉토리 ${n(refused)}개는 파일·링크로 바꾸지 못해 그대로 뒀습니다 — 「삭제」를 켜고 다시 실행하면 바뀝니다`
      : `형식이 바뀐 목적지 항목 ${n(refused)}개를 지우지 못해 그대로 뒀습니다 — 이유는 ${failureBlock ? "위 「실패 항목」" : "실행 출력(stdout.log)"}에 있습니다` });
  }
  // nsync 의 「Type changes」 줄은 계획 단계 수다(완료 수가 아니다 -- 거부된 교체도 든다, 리뷰 portal-jobs-22). 실행 결과에서
  // 「바뀐 항목」이라 하면 바로 위 거부 줄과 모순이라 계획임을 밝힌다.
  if ((s.typeToDir !== null && s.typeToDir > 0) || (s.typeFromDir !== null && s.typeFromDir > 0)) {
    out.push({ tone: "info", text: `형식이 바뀌는 항목(계획) — 디렉토리로 ${n(s.typeToDir ?? 0)}개, 디렉토리에서 파일·링크로 ${n(s.typeFromDir ?? 0)}개` });
  }
  if (s.skippedSpecial !== null && s.skippedSpecial > 0) {
    out.push({ tone: "info", text: `FIFO·소켓·장치 파일 ${n(s.skippedSpecial)}개는 복사하지 않았습니다`
      + (tool === "nsync" ? " — 목적지의 같은 이름 항목은 그대로 뒀습니다" : "") });
  }
  // 읽기 오류: 주어는 scanSubject(nsync 는 소스·목적지 합이라 소스 쪽 수와 같을 때만 「소스」, dsync 는 수를 찍지 않아
  // 「소스 일부」 -- 수를 지어내지 않는다, 리뷰 portal-jobs-18). 읽지 못한 경로가 있는 곳(리뷰 portal-jobs-17): nsync 는 결과의
  // 「실패 항목」 스캔(읽기) 줄, dsync 는 walk 오류 줄(작은따옴표 형식이라 「실패 항목」 예시가 되지 않는다)이 있는 출력.
  const unread = scanSubject(s, tool);
  if (unread !== null) {
    const where = tool === "nsync"
      ? (failureBlock ? ". 읽지 못한 경로는 위 「실패 항목」의 스캔(읽기) 줄에 있습니다" : "")
      : tool === "dsync" ? ". 읽지 못한 경로는 실행 출력(stdout.log)의 'Failed to open directory'·'Failed to stat' 줄에 있습니다" : "";
    out.push({ tone: "warn", text: `${withObj(unread)} 읽지 못해 그 부분은 반영되지 않았습니다(권한 등)`
      + (s.deleteDisabled ? " — 안전을 위해 이번 실행에서는 삭제를 하지 않았습니다" : "") + where });
  } else if (s.deleteDisabled) {
    out.push({ tone: "warn", text: "소스를 읽는 중 오류가 있어 안전을 위해 이번 실행에서는 삭제를 하지 않았습니다" });
  }
  if (s.metadataFailed !== null && s.metadataFailed > 0) {
    out.push({ tone: "warn", text: `속성(소유자·권한·시각)을 적용하지 못한 항목 ${n(s.metadataFailed)}개` });
  }
  if (s.returncode === 0 && s.toolErrors !== null && s.toolErrors > 0) {
    out.push({ tone: "warn", text: `성공으로 끝났지만 도구가 오류 줄 ${n(s.toolErrors)}개를 남겼습니다(예: 속성 적용 실패) — 실행 출력(stdout.log)의 ERROR 줄을 확인하세요` });
  }
  return out;
}

const KINDS: ActionKind[] = ["copy", "mkdir", "symlink_update", "meta_update", "remove"];
const num = (n: number | null | undefined): string => (typeof n === "number" ? countText(n) : "모름");

/** 실행 결과 타일(도구별 뜻). 옛 잡(상세 없음)은 핵심 수만 중립 라벨로. */
export function executionTiles(s: ReadSummary, tool: string | null, op: string | null): ResultTile[] {
  if (!s.detailed) {
    const tiles: ResultTile[] = [{ key: "files", label: op === "rm" ? "삭제한 항목" : "항목 수", value: s.files === null ? "—" : countText(s.files) }];
    if (op !== "rm" || s.bytes !== null) tiles.push({ key: "bytes", label: "크기", value: bytesText(s.bytes) });
    if (s.returncode !== null) tiles.push({ key: "rc", label: "종료 코드", value: String(s.returncode), bad: s.returncode !== 0 });
    return tiles;
  }
  if (tool === "nsync") {
    const tiles: ResultTile[] = [];
    if (s.done !== null) {
      for (const k of KINDS) {
        const d = s.done[k];
        const p = s.planned?.[k];
        if (k !== "copy" && !(typeof d === "number" && d > 0) && !(typeof p === "number" && p > 0)) continue;
        tiles.push({ key: k, label: ACTION_LABELS[k], value: num(d),
          sub: typeof p === "number" && p !== d ? `계획 ${countText(p)}` : null });
      }
    } else {
      const p = sumKinds(s.planned);
      tiles.push({ key: "done", label: "반영한 항목", value: "모름", sub: p !== null ? `계획 ${countText(p)}` : null });
    }
    tiles.push({ key: "bytes", label: "복사한 크기", value: bytesText(s.copiedBytes ?? s.bytes) });
    return tiles;
  }
  if (tool === "drm" || op === "rm") {
    const tiles: ResultTile[] = [{ key: "removed", label: "삭제한 항목", value: num(s.removed ?? s.files) }];
    if (s.removeFailed !== null) {
      tiles.push({ key: "remove_failed", label: "지우지 못한 항목", value: countText(s.removeFailed), bad: s.removeFailed > 0 });
    }
    return tiles;
  }
  // dsync(그리고 v2 인데 도구를 모르는 sync -- dsync 와 같은 키만 읽는다). 비정상 종료면 복사 수·크기는 「모름」 -- 마지막
  // 요약 블록이 없어 남은 「Items:」는 복사 전 계획 줄이다(리뷰 portal-jobs-12, 러너도 null 로 둔다 -- 값이 와도 믿지 않는다).
  const tiles: ResultTile[] = [
    { key: "copied", label: "복사한 항목", value: s.aborted ? "모름" : num(s.copiedItems ?? s.files) },
    { key: "bytes", label: "복사한 크기", value: s.aborted ? "모름" : bytesText(s.copiedBytes ?? s.bytes) },
  ];
  if (s.removed !== null) tiles.push({ key: "removed", label: "삭제한 항목", value: countText(s.removed) });
  if (s.removeFailed !== null && s.removeFailed > 0) {
    tiles.push({ key: "remove_failed", label: "지우지 못한 항목", value: countText(s.removeFailed), bad: true });
  }
  return tiles;
}

function rawValueText(k: string, v: unknown): string {
  // String(null) 은 "null" -- 모르는 값은 대시보드와 같은 "—" 규약.
  if (v === null || v === undefined) return "—";
  if (k === "bytes" && typeof v === "number") return bytesText(v);
  if (typeof v === "object") {
    try { return JSON.stringify(v); } catch { return String(v); }
  }
  return String(v);
}

// scan 결과: 원 키 타일 그대로(dt = 원 키, dd = 값). dscan 은 files·bytes 외에도 싣고 원 키가 운영자의 검색어다.
// 단 summary v2 의 머리 키(러너가 모든 도구에 싣는 형식 표식 -- detail_version·tool·dryrun·quiet)는 결과 값이 아니라 타일에서
// 뺀다(d192 리뷰 portal-jobs-15: 새 scan 결과마다 「detail_version 2」·「tool dscan」 칸이 붙었다). aborted·reason 은 값 칸이
// 아니라 주의 줄로(rawNotes), summary_unavailable 은 위 안내 문장이 이미 말한다.
const RAW_HIDDEN_KEYS: ReadonlySet<string> = new Set([
  "detail_version", "tool", "dryrun", "quiet", "aborted", "reason", "summary_unavailable",
]);

/** scan 원 키 결과의 주의 줄(순수): 비정상 종료와 러너가 정한 사유(비정상 종료 사유는 첫 줄이 이미 말한다). */
export function rawNotes(s: ReadSummary): ResultNote[] {
  const out: ResultNote[] = [];
  if (s.aborted) out.push({ tone: "warn", text: ABORTED_NOTE });
  if (s.reason !== null && !(s.aborted && s.reason === "tool_aborted")) {
    out.push({ tone: "warn", text: `도구 출력으로 정한 사유: ${reasonText(s.reason)}` });
  }
  return out;
}

function RawTiles({ summary }: { summary: Record<string, unknown> }) {
  return (
    <dl className={GRID}>
      {Object.entries(summary).filter(([k]) => !RAW_HIDDEN_KEYS.has(k)).map(([k, v]) => (
        <div key={k} className={TILE}>
          <dt className="font-mono text-xs text-ink/70 [overflow-wrap:anywhere]">{k}</dt>
          <dd className="text-sm font-semibold tabular-nums break-all">{rawValueText(k, v)}</dd>
        </div>
      ))}
    </dl>
  );
}

type JobLike = Pick<DataJob, "operation"> & Partial<Pick<DataJob, "tool" | "options">>;

export function ExecutionResult({ summary, job, headingId, onOpenOutput }: {
  summary: unknown; job?: JobLike; headingId?: string; onOpenOutput?: (() => void) | null;
}) {
  if (summary == null) return null;
  const heading = <h5 id={headingId} className={H5}>실행 결과</h5>;
  if (!isPlainObject(summary)) {
    return <div>{heading}<p className="text-sm">{String(summary)}</p></div>;
  }
  const s = readSummary(summary);
  const op = job?.operation ?? null;
  const tool = summaryTool(s, job?.tool);
  const unavailableNote = s.summaryUnavailable && (
    <p className="mb-2 text-sm text-attn break-keep">결과 요약을 읽지 못했습니다 — 진단 이벤트의 summary_unreadable 을 보세요</p>
  );
  if (op === "scan" || tool === "dscan") {
    if (!Object.keys(summary).length) return null;
    return (
      <div>
        {heading}{unavailableNote}<RawTiles summary={summary} />
        <Notes notes={rawNotes(s)} label="실행 결과 주의 사항" />
      </div>
    );
  }
  // 실행 성공인데 summary.json 을 못 읽었다(stepper 표식만) -- 개수 칸을 「—」로 늘어놓지 않고 그 사실만 말한다.
  if (s.summaryUnavailable && s.returncode === null && s.files === null && !s.detailed) {
    return <div>{heading}{unavailableNote}</div>;
  }
  // 도구가 돌지 않은 실행(러너가 워커 준비 실패·rm 대상 봉쇄에서 3키를 null 로 쓴다 -- returncode 모름).
  if (!s.detailed && s.returncode === null && s.files === null && s.bytes === null) {
    return (
      <div>
        {heading}
        <p className="text-sm text-ink/70 break-keep">처리 결과가 없습니다 — 도구가 시작되기 전에 멈춘 실행입니다(위 사유 참고).</p>
      </div>
    );
  }
  const quiet = s.quiet || job?.options?.quiet === true;
  const tiles = executionTiles(s, tool, op);
  const notes = s.detailed ? resultNotes(s, tool, quiet, job?.options?.delete === true) : [];
  const planNote = s.detailed && tool === "nsync" && tiles.some((t) => t.sub);
  return (
    <div>
      {heading}
      {unavailableNote}
      {planNote && (
        <p className="mb-2 text-xs text-ink/70 break-keep">실제로 반영한 수입니다 — 계획(미리보기)과 다르면 칸 아래에 계획 수가 있습니다.</p>
      )}
      <Tiles tiles={tiles} />
      {hasFailureItems(s, tool) && (
        <FailureBlock s={s} tool={tool} phaseWord="실행" onOpenOutput={onOpenOutput} />
      )}
      <Notes notes={notes} label="실행 결과 주의 사항" />
      {!s.detailed && (
        <p className="mt-2 text-xs text-ink/70 break-keep">
          {quiet ? "quiet 로 실행돼 도구가 세부 결과를 남기지 않았습니다." : "이전 형식의 결과 요약이라 종류별 내역과 실패 항목이 없습니다 — 실행 출력(stdout.log)에서 확인하세요."}
        </p>
      )}
    </div>
  );
}

/** 미리보기 타일(도구별 뜻). nsync = 변경 예정(이미 같은 항목은 빠진 수), dsync = 소스 전체(변경 수가 아니다 -- 실행 후 확정),
 *  drm = 지울 대상(대상 경로 자신 포함). 도구를 모르는 옛 잡은 중립 라벨. */
export function previewTiles(s: ReadSummary, tool: string | null, op: string): ResultTile[] {
  const rm = op === "rm";
  const tiles: ResultTile[] = [];
  if (rm) {
    const n = s.targetItems ?? s.files;
    tiles.push({ key: "files", label: "삭제 대상", value: n === null ? "모름" : countText(n) });
    // rm 은 도구가 바이트를 보고하지 않아 null 이 정상(「—」).
    tiles.push({ key: "bytes", label: "크기", value: s.bytes === null ? "—" : humanBytes(s.bytes) });
  } else if (tool === "nsync") {
    const total = s.files ?? sumKinds(s.planned);
    tiles.push({ key: "files", label: "변경 예정", value: total === null ? "모름" : countText(total),
      sub: s.planned !== null ? kindsText(s.planned) : null });
    const b = s.plannedBytes ?? s.bytes;
    tiles.push({ key: "bytes", label: "복사할 크기", value: b === null ? "모름" : humanBytes(b) });
  } else if (tool === "dsync") {
    const n = s.sourceItems ?? s.files;
    tiles.push({ key: "files", label: "소스 전체", value: n === null ? "모름" : countText(n), sub: "변경 수는 실행 후 확정" });
    if (s.bytes !== null) tiles.push({ key: "bytes", label: "크기", value: humanBytes(s.bytes) });
  } else {
    tiles.push({ key: "files", label: "미리보기 항목", value: s.files === null ? "모름" : countText(s.files) });
    tiles.push({ key: "bytes", label: "크기", value: s.bytes === null ? "모름" : humanBytes(s.bytes) });
  }
  // 정상 미리보기는 종료 코드 0 으로만 컨펌 대기에 온다 -- 0 이 아닌 값은 실패 시점 결과·옛 데이터·스텁에서만 보인다(그때는 신호다).
  if (s.returncode !== null && s.returncode !== 0) {
    tiles.push({ key: "rc", label: "미리보기 종료 코드", value: String(s.returncode), bad: true });
  }
  return tiles;
}

// 미리보기 결과 타일(한국어 라벨). null(모름)과 0(정상값)을 뭉개지 않는다 -- 러너는 카운트를 못 읽으면 null 을 준다.
// ConfirmDialog.previewSummaryText 와 같은 판정이다(같은 previewTiles·previewWarnings 를 읽는다).
export function PreviewResult({ job, summary, failedAt, headingId, onOpenOutput }: {
  job: JobLike; summary: unknown; failedAt: boolean; headingId: string; onOpenOutput?: (() => void) | null;
}) {
  const s = readSummary(isPlainObject(summary) ? summary : {});
  const rm = job.operation === "rm";
  const tool = summaryTool(s, job.tool);
  const tiles = previewTiles(s, tool, job.operation);
  // 컨펌 전 주의는 성공한 미리보기에만(d192 리뷰 portal-jobs-21): 문장이 「…삭제합니다」·「이번 실행에서는」처럼 앞으로의
  // 실행을 말하는데, 실패한 미리보기의 요청은 실행되지 않는다(배너는 「데이터는 바뀌지 않았습니다」).
  const warnings = failedAt ? [] : previewWarnings(s, { op: job.operation, tool, deleteOn: job.options?.delete === true })
    .map((text): ResultNote => ({ tone: "warn", text }));
  const files = rm ? s.targetItems ?? s.files : s.files;
  return (
    <div>
      {/* 「미리보기 결과 보기」(배너)의 포커스 대상이라 tabIndex=-1 */}
      <h5 id={headingId} tabIndex={-1}
          className={`${H5} scroll-mt-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent`}>
        {failedAt ? "미리보기 결과 (실패 시점)" : "미리보기 결과"}
      </h5>
      <Tiles tiles={tiles} />
      {/* 수의 뜻은 타일 바로 아래(주의 목록보다 먼저 -- 무엇을 센 수인지 알아야 주의가 읽힌다). */}
      {!rm && !failedAt && tool === "nsync" && (
        <p className="mt-1 text-xs text-ink/70 break-keep">이미 같은 항목은 빠진, 실제로 바뀔 항목의 수입니다(삭제·속성 갱신 포함).</p>
      )}
      {failedAt && hasFailureItems(s, tool) && (
        <FailureBlock s={s} tool={tool} phaseWord="미리보기" onOpenOutput={onOpenOutput} />
      )}
      <Notes notes={warnings} label="미리보기 주의 사항" />
      {/* rm 의 수가 무엇을 센 것인지(2026-10-12 -- 예전엔 늘 「모름」이라 말할 필요가 없었다): drm walk 항목 수 = 대상
          경로 자신 + 그 아래 파일·디렉터리·링크. 실행의 「삭제한 항목」과 같은 기준이다. 모르면(옛 잡) 목록이 있는 곳. */}
      {/* 실패 시점 결과(failedAt)에는 붙이지 않는다 -- 도구가 아예 돌지 않았을 수 있어(workers_unreachable 등 stdout.log
          없음) 개수·목록 안내가 거짓이 된다(2026-10-12 리뷰). 0 은 「빈 대상」이 아니라 대상 경로를 찾지 못한 것(walk 는
          대상 자신을 센다 -- 빈 디렉터리도 1). 모르면 개수가 있는 곳(walk 요약 줄 -- 목록은 앞·뒤 10개만). */}
      {rm && !failedAt && (
        <p className="mt-1 text-xs text-ink/70 break-keep">
          {files === null
            ? "개수를 읽지 못한 미리보기입니다 — 아래 「미리보기」 단계 출력(stdout.log)의 'Walked N items' 줄이 지울 항목 수입니다(목록은 앞·뒤 10개만 찍힙니다)."
            : files === 0
              ? "대상 경로를 찾지 못했습니다(오타·이미 삭제·권한) — 지울 것이 없습니다. 아래 「미리보기」 단계 출력의 'Failed to stat' 을 확인하세요."
              : "대상 경로 자신과 그 아래 파일·디렉터리·링크의 합계입니다(실행 뒤 「삭제한 항목」과 같은 기준)."}
        </p>
      )}
    </div>
  );
}
