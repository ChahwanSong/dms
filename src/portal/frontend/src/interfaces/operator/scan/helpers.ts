// Batch / request status -> Korean label + a sanity-style color class (reuses .san-*).
// Scan is READ-ONLY: a batch goes draft -> scanning -> done (or cancelled), with
// no preview/approve step.
export const BATCH_STATUS: Record<string, { label: string; cls: string }> = {
  draft: { label: "초안", cls: "san-unknown" },
  scanning: { label: "스캔 중", cls: "san-degraded" },
  done: { label: "완료", cls: "san-ready" },
  cancelled: { label: "취소됨", cls: "san-failed" },
};

export const REQUEST_STATE: Record<string, { label: string; cls: string }> = {
  registered: { label: "등록됨", cls: "san-unknown" },
  held: { label: "보류 (이번 스캔 제외)", cls: "san-unknown" },
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

// DMS preflight/scan failure reason code -> Korean explanation. Scan is read-only,
// so the relevant reasons are source-side access failures and node shortages; map
// the known codes and pass unknown ones through.
const ERROR_REASONS: Record<string, string> = {
  source_not_found: "스캔 경로가 존재하지 않음",
  source_not_readable: "스캔 경로 읽기 권한 없음",
  source_not_traversable: "스캔 디렉터리 접근(x) 권한 없음",
  target_not_found: "대상 경로가 존재하지 않음",
  target_not_directory: "대상이 디렉터리가 아님",
  target_not_readable: "대상 읽기 권한 없음",
  target_not_traversable: "대상 디렉터리 접근(x) 권한 없음",
  parent_not_traversable: "상위 디렉터리 접근(x) 권한 없음",
  posix_permission_denied: "권한/경로 검사 실패",
  no_ready_dm_candidate: "실행 가능한 DM 노드 없음",
  insufficient_eligible_nodes: "적격 노드 수 부족",
  insufficient_source_eligible_nodes: "출발측 적격 노드 수 부족",
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
export interface ScanRow {
  path: string;
}

export interface ParsedPaths {
  rows: ScanRow[];
  storage?: string;
  errors: string[];
}

const HEADER_TOKENS = new Set([
  "path",
  "scan_path",
  "source_path",
  "src_path",
  "storage",
  "source_storage",
  "src_storage",
  "src",
  "source",
]);

// Parse pasted/uploaded CSV/TSV. Primary format is a single column: `path`
// (storage is batch-level). A 2-column file (`storage, path`) is also accepted —
// the storage is lifted into `storage` when the file uses a single one (mixed
// storages are reported as an error). Comma or tab delimited; blank lines and `#`
// comments skipped; a header row is ignored.
export function parsePathsCsv(text: string): ParsedPaths {
  const rows: ScanRow[] = [];
  const errors: string[] = [];
  const storageSet = new Set<string>();
  let firstContent = true;
  text.split(/\r?\n/).forEach((raw, idx) => {
    const line = raw.trim();
    if (!line || line.startsWith("#")) return;
    const parts = line.split(line.includes("\t") ? "\t" : ",").map((p) => p.trim());
    // A header row (path / storage,path) is ignored when it is the first non-blank,
    // non-comment line — not only the literal first line, so a leading # comment
    // doesn't push it into the data.
    if (firstContent) {
      firstContent = false;
      if (HEADER_TOKENS.has((parts[0] || "").toLowerCase())) return; // header
    }
    if (parts.length >= 2 && parts[0] && parts[1]) {
      // storage, path
      storageSet.add(parts[0]);
      rows.push({ path: parts[1] });
    } else if (parts[0]) {
      // single path column
      rows.push({ path: parts[0] });
    } else {
      errors.push(`${idx + 1}행: 경로가 비어 있습니다`);
    }
  });
  if (storageSet.size > 1) {
    errors.push("여러 스토리지가 섞여 있습니다 — 배치는 단일 스토리지만 지원합니다");
  }
  return {
    rows,
    storage: storageSet.size === 1 ? [...storageSet][0] : undefined,
    errors,
  };
}

// Shared copy/paste-popup text: an example template and the format hint. Kept
// here so the batch-detail toolbar and the create/edit form stay in sync.
export const CSV_TEMPLATE_TEXT = "path\nexample/dir1\nexample/dir2\n";
export const CSV_FORMAT_HINT =
  '한 줄에 스캔 경로 하나(또는 "스토리지,경로" 2열). 헤더·빈 줄·# 주석은 무시됩니다.';

// Serialize rows to a single-column CSV (with header) for copy/paste.
export function pathsToCsv(rows: ScanRow[]): string {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
  return "path\n" + rows.map((r) => esc(r.path)).join("\n") + "\n";
}

// --- batch-wide DMS scan options --------------------------------------------
// The full set of DMS DATA_SCAN_OPTION_TYPES — exactly these four. Scan has no
// destructive/ownership options (it is read-only). The authoritative validation
// runs in DMS; the check below is a light client-side mirror.

export type ScanOptionKind = "bool" | "int";

export interface ScanOptionField {
  key: string;
  label: string;
  kind: ScanOptionKind;
  placeholder?: string;
  hint?: string;
}

export const SCAN_OPTION_FIELDS: ScanOptionField[] = [
  {
    key: "summary_only",
    label: "summary_only",
    kind: "bool",
    hint: "개별 파일 목록 없이 집계(파일·디렉터리·용량)만 — 더 빠름",
  },
  {
    key: "max_depth",
    label: "max_depth",
    kind: "int",
    placeholder: "예: 2 (0 = 루트만)",
    hint: "탐색 최대 깊이 (0 이상). 비우면 무제한",
  },
  {
    key: "follow_symlinks",
    label: "follow_symlinks",
    kind: "bool",
    hint: "심볼릭 링크를 따라 탐색",
  },
  {
    key: "one_file_system",
    label: "one_file_system",
    kind: "bool",
    hint: "마운트 경계를 넘지 않음 (동일 파일시스템만)",
  },
];

// Light mirror of DMS scan-option validation. Returns human-readable errors.
export function validateScanOptions(opts: Record<string, unknown>): string[] {
  const errors: string[] = [];
  const md = opts.max_depth;
  if (md != null) {
    if (typeof md !== "number" || !Number.isInteger(md) || md < 0) {
      errors.push("max_depth는 0 이상의 정수여야 합니다");
    }
  }
  return errors;
}
