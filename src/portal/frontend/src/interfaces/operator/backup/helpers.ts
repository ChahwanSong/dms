// Batch / request status -> Korean label + a sanity-style color class (reuses .san-*).
export const BATCH_STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: "초안", cls: "san-unknown" },
  previewing: { label: "Preview 중", cls: "san-degraded" },
  previewed: { label: "Preview 완료", cls: "san-ready" },
  running: { label: "실행 중", cls: "san-degraded" },
  done: { label: "완료", cls: "san-ready" },
  cancelled: { label: "취소됨", cls: "san-failed" },
};

export const REQUEST_STATE: Record<string, { label: string; cls: string }> = {
  registered: { label: "등록됨", cls: "san-unknown" },
  preview_pending: { label: "Preview 대기", cls: "san-degraded" },
  preview_ready: { label: "Preview 완료", cls: "san-ready" },
  approved: { label: "승인됨", cls: "san-degraded" },
  preview_failed: { label: "Preview 실패", cls: "san-failed" },
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

// DMS preflight/preview failure reason code -> Korean explanation. The DM worker
// now returns a precise reason (e.g. source_not_found) instead of a generic
// posix_permission_denied; map the known codes and pass unknown ones through.
const ERROR_REASONS: Record<string, string> = {
  source_not_found: "출발 경로가 존재하지 않음",
  source_not_readable: "출발 경로 읽기 권한 없음",
  source_not_traversable: "출발 디렉터리 접근(x) 권한 없음",
  destination_parent_missing: "대상 상위 디렉터리가 없음",
  destination_parent_not_writable: "대상 상위 디렉터리 쓰기 권한 없음",
  destination_parent_not_traversable: "대상 상위 디렉터리 접근(x) 권한 없음",
  destination_not_writable: "기존 대상 쓰기 권한 없음",
  target_not_found: "대상 경로가 존재하지 않음",
  target_not_directory: "대상이 디렉터리가 아님",
  target_not_readable: "대상 읽기 권한 없음",
  target_not_traversable: "대상 디렉터리 접근(x) 권한 없음",
  parent_not_writable: "상위 디렉터리 쓰기 권한 없음",
  parent_not_traversable: "상위 디렉터리 접근(x) 권한 없음",
  posix_permission_denied: "권한/경로 검사 실패",
  // common non-preflight reasons, surfaced for completeness
  no_ready_dm_candidate: "실행 가능한 DM 노드 없음",
  insufficient_eligible_nodes: "적격 노드 수 부족",
  insufficient_source_eligible_nodes: "출발측 적격 노드 수 부족",
  insufficient_destination_eligible_nodes: "대상측 적격 노드 수 부족",
  ldap_not_configured: "LDAP 미설정",
};

// Friendly Korean label for an error/reason code. Returns null for empty input
// and passes through codes/messages we don't have a mapping for.
export function friendlyError(code?: string | null): string | null {
  if (!code) return null;
  return ERROR_REASONS[code] ?? code;
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

// One request row in the inline editor (storage is batch-level, not per row).
export interface BackupRow {
  src_path: string;
  dst_path: string;
}

export interface ParsedRequests {
  rows: BackupRow[];
  srcStorage?: string;
  dstStorage?: string;
  errors: string[];
}

const HEADER_TOKENS = new Set([
  "src_path",
  "source_path",
  "dst_path",
  "destination_path",
  "src_storage",
  "source_storage",
  "src",
  "source",
  "dst_storage",
  "destination_storage",
  "dst",
  "destination",
]);

// Parse pasted/uploaded CSV/TSV. Primary format is 2 columns: `src_path, dst_path`
// (storage is batch-level). A legacy 4-column file
// (`src_storage, src_path, dst_storage, dst_path`) is also accepted — the storages
// are lifted into srcStorage/dstStorage when the file uses a single pair (mixed
// storages are reported as an error). Comma or tab delimited; blank lines and `#`
// comments skipped; a header row is ignored.
export function parseRequestsCsv(text: string): ParsedRequests {
  const rows: BackupRow[] = [];
  const errors: string[] = [];
  const srcSet = new Set<string>();
  const dstSet = new Set<string>();
  let firstContent = true;
  text.split(/\r?\n/).forEach((raw, idx) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    const parts = line.split(line.includes("\t") ? "\t" : ",").map((p) => p.trim());
    // A header row (src_path,dst_path) is ignored when it is the first non-blank,
    // non-comment line — not only the literal first line, so a leading # comment
    // doesn't push it into the data.
    if (firstContent) {
      firstContent = false;
      if (HEADER_TOKENS.has((parts[0] || "").toLowerCase())) return; // header
    }
    if (parts.length >= 4) {
      const [ss, sp, ds, dp] = parts;
      if (!sp || !dp) {
        errors.push(`${idx + 1}행: 경로가 비어 있습니다`);
        return;
      }
      if (ss) srcSet.add(ss);
      if (ds) dstSet.add(ds);
      rows.push({ src_path: sp, dst_path: dp });
    } else if (parts.length >= 2 && parts[0] && parts[1]) {
      rows.push({ src_path: parts[0], dst_path: parts[1] });
    } else {
      errors.push(`${idx + 1}행: 2개 컬럼 필요 (src_path, dst_path)`);
    }
  });
  if (srcSet.size > 1 || dstSet.size > 1) {
    errors.push("여러 스토리지가 섞여 있습니다 — 배치는 단일 출발/대상 스토리지만 지원합니다");
  }
  return {
    rows,
    srcStorage: srcSet.size === 1 ? [...srcSet][0] : undefined,
    dstStorage: dstSet.size === 1 ? [...dstSet][0] : undefined,
    errors,
  };
}

// Serialize rows to a 2-column CSV (with header) for download.
export function rowsToCsv(rows: BackupRow[]): string {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  return (
    "src_path,dst_path\n" +
    rows.map((r) => `${esc(r.src_path)},${esc(r.dst_path)}`).join("\n") +
    "\n"
  );
}

// --- batch-wide DMS sync options --------------------------------------------
// Curated subset of DMS DATA_SYNC_OPTION_TYPES surfaced in the UI. `delete` is
// intentionally NOT here — it is driven by the dedicated --delete checkbox
// (delete_enabled). The authoritative validation runs in the DMS preview; the
// checks below are a light client-side mirror for early feedback.

export type SyncOptionKind = "text" | "bool" | "int";
export type SyncOptionGroup = "sync" | "ownership";

export interface SyncOptionField {
  key: string;
  label: string;
  kind: SyncOptionKind;
  group: SyncOptionGroup;
  placeholder?: string;
  hint?: string;
  warn?: boolean; // render hint as a warning (e.g. chown overrides ownership)
}

// Two collapsible groups in the form: "sync 옵션" (transfer behavior; --delete is
// rendered alongside these but is its own field = delete_enabled) and "소유권 옵션"
// (chmod/chown). `delete` itself is NOT here — handled via the delete_enabled flag.
export const SYNC_OPTION_FIELDS: SyncOptionField[] = [
  { key: "contents", label: "contents", kind: "bool", group: "sync", hint: "내용(체크섬) 기반 비교" },
  { key: "direct", label: "direct", kind: "bool", group: "sync", hint: "O_DIRECT I/O (일부 FS 미지원)" },
  { key: "open_noatime", label: "open_noatime", kind: "bool", group: "sync", hint: "atime 미갱신" },
  { key: "quiet", label: "quiet", kind: "bool", group: "sync", hint: "로그 최소화" },
  { key: "batch_files", label: "batch_files", kind: "int", group: "sync", hint: "배치당 파일 수" },
  { key: "bufsize", label: "bufsize", kind: "int", group: "sync", hint: "전송 버퍼 크기(byte)" },
  { key: "chmod", label: "chmod", kind: "text", group: "ownership", placeholder: "0750 또는 D0750,F0640", hint: "대상 권한 강제" },
  {
    key: "chown",
    label: "chown",
    kind: "text",
    group: "ownership",
    placeholder: "user:group",
    hint: "대상 소유자 강제 — 원본 소유권 보존을 덮어씀",
    warn: true,
  },
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
