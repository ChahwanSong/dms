const HEADERS = {
  scan: ["storage", "target"],
  sync: ["source_storage", "source", "destination_storage", "destination"],
} as const;

export function parseBatchCsv(operation: "scan" | "sync", text: string) {
  const errors: string[] = [];
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l.length > 0);
  if (lines.length === 0) return { rows: [], errors: ["빈 CSV"] };
  const want = HEADERS[operation];
  const header = lines[0].split(",").map((c) => c.trim());
  if (header.length !== want.length || want.some((w, i) => header[i] !== w)) {
    return { rows: [], errors: [`헤더가 ${want.join(",")} 이어야 합니다`] };
  }
  const rows: Record<string, string>[] = [];
  lines.slice(1).forEach((line, i) => {
    const cells = line.split(",").map((c) => c.trim());
    if (cells.length !== want.length || cells.some((c) => c === "")) {
      errors.push(`${i + 2}행: 열 수/빈 값 오류`);
      return;
    }
    rows.push(Object.fromEntries(want.map((w, j) => [w, cells[j]])));
  });
  return { rows, errors };
}
