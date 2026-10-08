import { TriangleAlert } from "lucide-react";
import type { DataJob } from "../../lib/types";
import { countText, finiteOrNull, humanBytes, isPlainObject } from "./format";

// 단계 구획 맨 위의 결과 타일. 한 값은 **한 곳에만** 보인다 -- 미리보기 summary 는 ①, 실행 summary 는 ②.
// 미리보기가 실패하면 stepper(_surface_failed_artifact)가 미리보기 summary 를 result_summary 에 싣는다 -- 그 값을
// ②의 「실행 결과」로 그리면 "실행했다" 는 거짓이 되므로 ①의 「미리보기 결과 (실패 시점)」로만 보인다(stageModel
// resultStage).

const TILE = "min-w-0 rounded-lg border border-line px-3 py-2";
const GRID = "grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4";
const H5 = "mb-2 text-xs font-semibold text-ink/70";

function valueText(k: string, v: unknown): string {
  // String(null) 은 "null" -- rm 은 설계상 bytes 가 없어(도구 미보고) null 이 정상이다. 대시보드와 같은 "—" 규약.
  // bytes 는 사람 표기 + 원값(정밀도 손실 없이 검증 가능하게). 1 KiB 미만은 사람 표기가 곧 원값이라 괄호를 붙이지
  // 않는다(「10 B (10 B)」 중복, d164 실 화면).
  if (v === null || v === undefined) return "—";
  if (k === "bytes" && typeof v === "number") {
    const human = humanBytes(v);
    return human === `${v} B` ? human : `${human} (${v} B)`;
  }
  if (typeof v === "object") {
    try { return JSON.stringify(v); } catch { return String(v); }
  }
  return String(v);
}

// 옛 RequestDetail.ResultSummary 의 렌더 규약을 그대로 둔다(dt = 원 키, dd = 값, dt 다음 형제가 dd) -- 결과 키를
// 한국어로 바꾸지 않는 이유: 도구마다 키가 다르고(scan 은 files·bytes 외에도 싣는다) 원 키가 운영자의 검색어다.
// 한국어 요약은 배너 KPI 가 맡는다.
export function ExecutionResult({ summary, headingId }: { summary: unknown; headingId?: string }) {
  if (summary == null) return null;
  const unavailable = isPlainObject(summary) && summary.summary_unavailable === true;
  let body;
  if (isPlainObject(summary)) {
    const entries = Object.entries(summary);
    if (!entries.length) return null;
    body = (
      <dl className={GRID}>
        {entries.map(([k, v]) => (
          <div key={k} className={TILE}>
            {/* 원 키는 그 값의 유일한 라벨이라 장식이 아니다 -- ink/70(AA). text-muted 는 3.54:1 이었다(리뷰 3차). */}
            <dt className="font-mono text-xs text-ink/70 [overflow-wrap:anywhere]">{k}</dt>
            <dd className="text-sm font-semibold tabular-nums break-all">{valueText(k, v)}</dd>
          </div>
        ))}
      </dl>
    );
  } else {
    body = <p className="text-sm">{String(summary)}</p>;
  }
  return (
    <div>
      <h5 id={headingId} className={H5}>실행 결과</h5>
      {unavailable && (
        <p className="mb-2 text-sm text-attn break-keep">결과 요약을 읽지 못했습니다 — 진단 이벤트의 summary_unreadable 을 보세요</p>
      )}
      {body}
    </div>
  );
}

// 미리보기 결과 타일(한국어 라벨). null(모름)과 0(정상값)을 뭉개지 않는다 -- 러너는 카운트를 못 읽으면 null 을 준다
// (summary.json 계약 {returncode, files, bytes}). ConfirmDialog.previewSummaryText 와 같은 판정이다.
export function PreviewResult({ job, summary, failedAt, headingId }: {
  job: Pick<DataJob, "operation">; summary: unknown; failedAt: boolean; headingId: string;
}) {
  const s = isPlainObject(summary) ? summary : {};
  const files = finiteOrNull(s.files);
  const bytes = finiteOrNull(s.bytes);
  const rc = finiteOrNull(s.returncode);
  const rm = job.operation === "rm";
  return (
    <div>
      {/* 「미리보기 결과 보기」(배너)의 포커스 대상이라 tabIndex=-1 */}
      <h5 id={headingId} tabIndex={-1}
          className={`${H5} scroll-mt-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent`}>
        {failedAt ? "미리보기 결과 (실패 시점)" : "미리보기 결과"}
      </h5>
      <dl className={GRID}>
        <div className={TILE}>
          <dt className="text-xs text-ink/70">{rm ? "삭제 대상" : "복사 대상"}</dt>
          <dd className="text-sm font-semibold tabular-nums">{files === null ? "모름" : countText(files)}</dd>
        </div>
        <div className={TILE}>
          <dt className="text-xs text-ink/70">크기</dt>
          {/* rm 은 도구가 바이트를 보고하지 않아 null 이 정상(「—」), sync 의 null 은 진짜 모름(「모름」). */}
          <dd className="text-sm font-semibold tabular-nums">{bytes === null ? (rm ? "—" : "모름") : humanBytes(bytes)}</dd>
        </div>
        {!rm && (
          <div className={TILE}>
            <dt className="text-xs text-ink/70">dry-run 종료코드</dt>
            <dd className={`flex items-center gap-1 text-sm font-semibold tabular-nums ${rc !== null && rc !== 0 ? "text-bad" : ""}`}>
              {rc !== null && rc !== 0 && <TriangleAlert className="h-3.5 w-3.5 shrink-0" aria-hidden />}
              {rc === null ? "—" : String(rc)}
            </dd>
          </div>
        )}
      </dl>
    </div>
  );
}
