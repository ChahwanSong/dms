// 슬라이스 32: 스토리지가 배치 레벨 선택으로 올라가면서 CSV 는 경로만 나른다.
// scan = 행당 1열(target), sync = 행당 2열(source,destination — 콤마 구분).
// 헤더 유연: 첫 줄 셀이 전부 알려진 헤더 토큰이면(대소문자 무시) 스킵하고,
// 아니면 데이터로 취급한다 — 내보내기(serialize)는 헤더를 포함하므로 왕복이 성립.
// 열 수 불일치·빈 셀은 "N행: ..." 오류로 수집한다(조용한 드랍 금지 — 기존 계약).

export interface ScanRow {
  target: string;
}
export interface SyncRow {
  source: string;
  destination: string;
}

// 헤더 판정 토큰(소문자 비교). scan 은 target/path 어느 쪽이든 1셀,
// sync 는 (source, destination) 정확히 2셀.
const SCAN_HEADER_TOKENS = new Set(["target", "path"]);
const SYNC_HEADER = ["source", "destination"];

function isHeaderLine(operation: "scan" | "sync", cells: string[]): boolean {
  const lower = cells.map((c) => c.toLowerCase());
  if (operation === "scan") {
    return lower.length === 1 && SCAN_HEADER_TOKENS.has(lower[0]);
  }
  return lower.length === 2 && lower[0] === SYNC_HEADER[0] && lower[1] === SYNC_HEADER[1];
}

export function parseItemsCsv(
  operation: "scan" | "sync",
  text: string,
): { rows: (ScanRow | SyncRow)[]; errors: string[] } {
  const errors: string[] = [];
  const lines = text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l.length > 0);
  if (lines.length === 0) return { rows: [], errors: ["빈 CSV"] };
  let body = lines;
  if (isHeaderLine(operation, lines[0].split(",").map((c) => c.trim()))) {
    body = lines.slice(1);
  }
  const rows: (ScanRow | SyncRow)[] = [];
  body.forEach((line, i) => {
    const cells = line.split(",").map((c) => c.trim());
    if (operation === "scan") {
      // 1열 계약: 콤마가 들어 있으면 경로 오타인지 다열 CSV 인지 알 수 없다 —
      // 침묵 분할하지 않고 오류로 보인다.
      if (cells.length !== 1 || cells[0] === "") {
        errors.push(`${i + 1}행: 경로 1개(콤마 없이)여야 합니다`);
        return;
      }
      rows.push({ target: cells[0] });
      return;
    }
    if (cells.length !== 2 || cells.some((c) => c === "")) {
      errors.push(`${i + 1}행: source,destination 2열이어야 합니다`);
      return;
    }
    rows.push({ source: cells[0], destination: cells[1] });
  });
  return { rows, errors };
}

export function serializeItemsCsv(
  operation: "scan" | "sync",
  rows: (ScanRow | SyncRow)[],
): string {
  // 헤더 포함 내보내기 — 재붙여넣기(파싱) 왕복이 성립해야 한다.
  if (operation === "scan") {
    return ["target", ...rows.map((r) => (r as ScanRow).target)].join("\n");
  }
  return [
    "source,destination",
    ...rows.map((r) => `${(r as SyncRow).source},${(r as SyncRow).destination}`),
  ].join("\n");
}
