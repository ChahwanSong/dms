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
  approved: { label: "승인됨", cls: "san-degraded" },
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

// --- batch-wide DMS sync options --------------------------------------------
// Curated subset of DMS DATA_SYNC_OPTION_TYPES surfaced in the UI. `delete` is
// intentionally NOT here — it is driven by the dedicated --delete checkbox
// (delete_enabled). The authoritative validation runs in the DMS preview; the
// checks below are a light client-side mirror for early feedback.

export type SyncOptionKind = "text" | "bool" | "int";

export interface SyncOptionField {
  key: string;
  label: string;
  kind: SyncOptionKind;
  placeholder?: string;
  hint?: string;
  warn?: boolean; // render hint as a warning (e.g. chown overrides ownership)
  advanced?: boolean;
}

export const SYNC_OPTION_FIELDS: SyncOptionField[] = [
  { key: "chmod", label: "chmod", kind: "text", placeholder: "0750 또는 D0750,F0640", hint: "대상 권한 강제" },
  {
    key: "chown",
    label: "chown",
    kind: "text",
    placeholder: "user:group",
    hint: "대상 소유자 강제 — 백업 기본인 원본 소유권 보존을 덮어씁니다",
    warn: true,
  },
  { key: "contents", label: "contents", kind: "bool", hint: "내용(체크섬) 기반 비교" },
  { key: "batch_files", label: "batch_files", kind: "int", hint: "배치당 파일 수", advanced: true },
  { key: "bufsize", label: "bufsize", kind: "int", hint: "전송 버퍼 크기(byte)", advanced: true },
  { key: "direct", label: "direct", kind: "bool", hint: "O_DIRECT I/O", advanced: true },
  { key: "open_noatime", label: "open_noatime", kind: "bool", hint: "atime 미갱신", advanced: true },
  { key: "quiet", label: "quiet", kind: "bool", hint: "로그 최소화", advanced: true },
];

const CHMOD_TOKEN = /^[DF]?[0-7]{1,4}$/;
const CHOWN_PART = /^[A-Za-z0-9._-]+$/;

function validateChown(spec: string): string | null {
  if (/\s/.test(spec)) return "chown에 공백을 넣을 수 없습니다";
  if (spec.includes(":")) {
    const idx = spec.indexOf(":");
    if (spec.indexOf(":", idx + 1) !== -1) return "chown은 ':'를 하나만 허용합니다";
    const user = spec.slice(0, idx);
    const group = spec.slice(idx + 1);
    if (!group) return "chown 그룹이 비었습니다 (':GROUP' 또는 'USER:GROUP')";
    if (user && !CHOWN_PART.test(user)) return `chown 사용자 형식 오류: '${user}'`;
    if (!CHOWN_PART.test(group)) return `chown 그룹 형식 오류: '${group}'`;
  } else if (!CHOWN_PART.test(spec)) {
    return `chown 사용자 형식 오류: '${spec}'`;
  }
  return null;
}

// Light mirror of DMS chmod/chown validation. Returns human-readable errors.
export function validateSyncOptions(opts: Record<string, unknown>): string[] {
  const errors: string[] = [];
  const chmod = opts.chmod;
  if (typeof chmod === "string" && chmod.trim()) {
    const toks = chmod.split(",").map((t) => t.trim());
    if (!toks.every((t) => CHMOD_TOKEN.test(t))) {
      errors.push("chmod 형식 오류 (예: 0750 또는 D0750,F0640)");
    }
  }
  const chown = opts.chown;
  if (typeof chown === "string" && chown.trim()) {
    const e = validateChown(chown.trim());
    if (e) errors.push(e);
  }
  return errors;
}

// Drop the `delete` key (driven by the --delete checkbox) from an options object
// when seeding the options editor, so it is never double-managed.
export function optionsWithoutDelete(opts?: Record<string, unknown> | null): Record<string, unknown> {
  const out: Record<string, unknown> = { ...(opts || {}) };
  delete out.delete;
  return out;
}
