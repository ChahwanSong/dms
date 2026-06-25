import type { BackupRequestInput } from "../../../api";

// Batch / request status -> Korean label + a sanity-style color class (reuses .san-*).
export const BATCH_STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: "초안", cls: "san-unknown" },
  previewing: { label: "미리보기 중", cls: "san-degraded" },
  previewed: { label: "미리보기 완료", cls: "san-ready" },
  running: { label: "실행 중", cls: "san-degraded" },
  done: { label: "완료", cls: "san-ready" },
  cancelled: { label: "취소됨", cls: "san-failed" },
};

export const REQUEST_STATE: Record<string, { label: string; cls: string }> = {
  registered: { label: "등록됨", cls: "san-unknown" },
  preview_pending: { label: "미리보기 대기", cls: "san-degraded" },
  preview_ready: { label: "미리보기 완료", cls: "san-ready" },
  preview_failed: { label: "미리보기 실패", cls: "san-failed" },
  running: { label: "실행 중", cls: "san-degraded" },
  succeeded: { label: "성공", cls: "san-ready" },
  failed: { label: "실패", cls: "san-failed" },
  cancelled: { label: "취소됨", cls: "san-failed" },
};

export function batchStatus(s?: string) {
  return BATCH_STATUS[s || ""] || { label: s || "—", cls: "san-unknown" };
}
export function requestState(s?: string) {
  return REQUEST_STATE[s || ""] || { label: s || "—", cls: "san-unknown" };
}

export function fmtBytes(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const units = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || Number.isInteger(v) ? 0 : 1)} ${units[i]}`;
}

const HEADER_TOKENS = new Set([
  "src_storage",
  "source_storage",
  "src",
  "source",
  "dst_storage",
  "destination_storage",
  "dst",
  "destination",
]);

export interface ParseResult {
  requests: BackupRequestInput[];
  errors: string[];
}

// Parse pasted CSV/TSV: 4 columns `src_storage, src_path, dst_storage, dst_path`.
// Comma or tab delimited; blank lines and `#` comments skipped; a header row
// (first field looks like a column name) is ignored.
export function parseRequestsCsv(text: string): ParseResult {
  const requests: BackupRequestInput[] = [];
  const errors: string[] = [];
  const lines = text.split(/\r?\n/);
  lines.forEach((raw, idx) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    const parts = line.split(line.includes("\t") ? "\t" : ",").map((p) => p.trim());
    if (idx === 0 && HEADER_TOKENS.has((parts[0] || "").toLowerCase())) return; // header
    if (parts.length < 4 || parts.some((p, i) => i < 4 && !p)) {
      errors.push(`${idx + 1}행: 4개 컬럼 필요 (src_storage,src_path,dst_storage,dst_path)`);
      return;
    }
    requests.push({
      src_storage: parts[0],
      src_path: parts[1],
      dst_storage: parts[2],
      dst_path: parts[3],
    });
  });
  return { requests, errors };
}
