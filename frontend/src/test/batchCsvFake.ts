import { http, HttpResponse } from "msw";
import type { CsvParseError, CsvParseResult } from "../lib/csv";
import { normalizedRmTarget } from "../lib/rmTargets";
import { relativePathProblem } from "../lib/storagePaths";

// 서버 CSV 해석(POST /api/admin/batches:parse-csv — src/dms/batch_csv.py)의 **시험용 축소판**. 화면 테스트가 서버 없이
// 「붙여넣기 → 검사 → 반영·교체」 동선을 돌릴 수 있게 응답 모양·사유 코드·줄 번호 규칙만 서버와 맞춘다: BOM, 빈 줄 건너뛰기, 첫 줄
// 헤더(경로 열 이름이 모두 있으면 — target 별칭 path), 위치 읽기의 열 수(scan·rm 1, sync 2), 경로 형식(unsafe_path ·
// rm_root_forbidden), rm 겹침(batch_rm_targets_overlap + related_line). 따옴표·스토리지 열·내보내기 정보 열은 모른다 — 진짜
// 규칙은 tests/test_batch_csv.py 가 서버에서 고정한다. 운영 번들에는 들어가지 않는다(테스트만 import).
export interface FakeParseBody {
  operation: string; csv: string; storage?: string; source_storage?: string; destination_storage?: string;
}

export function fakeParseCsv(body: FakeParseBody): CsvParseResult {
  const op = body.operation;
  const cols = op === "sync" ? ["source", "destination"] : ["target"];
  const skeys = op === "sync" ? ["source_storage", "destination_storage"] : ["storage"];
  const storages: Record<string, string | null> = {};
  for (const k of skeys) {
    const v = (body as unknown as Record<string, unknown>)[k];
    storages[k] = typeof v === "string" && v !== "" ? v : null;
  }
  const lines = body.csv.replace(/^﻿/, "").split(/\r\n|\n|\r/);
  const errors: CsvParseError[] = [];
  const items: Record<string, string>[] = [];
  const kept: { line: number; row: number; target: string }[] = [];
  let header: Record<string, number> | null = null;
  let seenFirst = false;
  let row = 0;
  const err = (line: number | null, r: number | null, code: string, related: number | null = null) =>
    errors.push({ line, row: r, reason_code: code, message: "", related_line: related });
  lines.forEach((text, i) => {
    const line = i + 1;
    if (text.trim() === "") return;
    const cells = text.split(",").map((c) => c.trim());
    if (!seenFirst) {
      seenFirst = true;
      const names = cells.map((c) => (c.toLowerCase() === "path" ? "target" : c.toLowerCase()));
      if (cols.every((c) => names.includes(c))) {
        header = Object.fromEntries(cols.map((c) => [c, names.indexOf(c)]));
        return;
      }
    }
    row += 1;
    const h = header as Record<string, number> | null;
    const vals = h === null ? cells : cols.map((c) => cells[h[c]] ?? "");
    if ((h === null && vals.length !== cols.length) || vals.some((v) => v === "")) {
      err(line, row, "batch_csv_bad_row");
      return;
    }
    const paths = Object.fromEntries(cols.map((c, k) => [c, vals[k]]));
    items.push({ ...Object.fromEntries(skeys.flatMap((k) => (storages[k] ? [[k, storages[k] as string]] : []))), ...paths });
    for (const c of cols) {
      const p = paths[c];
      if (op === "rm" && (p === "." || p === "./")) { err(line, row, "rm_root_forbidden"); return; }
      if (relativePathProblem(p) !== null) { err(line, row, "unsafe_path"); return; }
    }
    if (op === "rm") kept.push({ line, row, target: paths.target });
  });
  if (row === 0) err(null, null, "empty_batch");
  const firstAt = new Map<string, number>();
  kept.forEach((k, i) => { const n = normalizedRmTarget(k.target); if (n !== null && !firstAt.has(n)) firstAt.set(n, i); });
  kept.forEach((k, i) => {
    const n = normalizedRmTarget(k.target);
    if (n === null) return;
    const seen = firstAt.get(n) as number;
    if (seen !== i) { err(k.line, k.row, "batch_rm_targets_overlap", kept[seen].line); return; }
    const parts = n.split("/");
    for (let d = 1; d < parts.length; d += 1) {
      const owner = firstAt.get(parts.slice(0, d).join("/"));
      if (owner !== undefined) { err(k.line, k.row, "batch_rm_targets_overlap", kept[owner].line); return; }
    }
  });
  errors.sort((a, b) => (a.line ?? Infinity) - (b.line ?? Infinity));
  return { items: row === 0 ? [] : items, errors, error_count: errors.length, rows: row,
           header: header === null ? null : cols, storages };
}

/** msw 핸들러 — 받은 본문을 spy 로 넘기고(선택) 축소판 해석을 돌려준다. */
export const parseCsvHandler = (spy?: (b: FakeParseBody) => void) =>
  http.post("/api/admin/batches:parse-csv", async ({ request }) => {
    const b = await request.json() as FakeParseBody;
    spy?.(b);
    return HttpResponse.json(fakeParseCsv(b));
  });
