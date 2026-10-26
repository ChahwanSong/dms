// 배치 항목 CSV — 2026-10-26 부터 **해석은 서버**다(POST /api/admin/batches:parse-csv, src/dms/batch_csv.py). 예전엔 여기서
// 줄을 콤마로 쪼갰는데 (1) 따옴표·BOM 을 몰라 엑셀이 저장한 CSV 가 깨졌고 (2) 경로 규칙(unsafe_path·rm 겹침 등)은 「배치
// 생성」 뒤 서버 422 한 문장으로만 나와 수백 행에서 어느 줄인지 몰랐다. 이제 서버가 생성과 같은 검증으로 줄마다 사유를 단다 —
// 이 파일은 얇은 클라이언트다: 응답 모양·오류 문구·직렬화(표 → CSV 다운로드·「현재 항목 CSV」)·다운로드 주소만.
//
// 열(서버 batch_csv docstring): scan·rm = target(별칭 path) [+ storage], sync = source,destination [+ source_storage,
// destination_storage]. 헤더가 없으면 위치로(scan·rm 1열, sync 2열). 「CSV 내보내기」 파일(정보 열 포함)도 그대로 다시 올린다.
import { reasonText } from "./api";
import { csvCell } from "./csvExport";

export type ItemsCsvOp = "scan" | "sync" | "rm";

export interface ScanRow {
  target: string;
}
export interface SyncRow {
  source: string;
  destination: string;
}

/** 줄 하나의 문제. line = 파일의 물리 줄(1부터, 헤더·빈 줄 포함 — 편집기에서 찾는 번호), row = 데이터 행 번호(해석이 전부
 *  통과하면 표의 행 번호와 같다). 파일 단위 사유(빈 CSV·깨진 따옴표)는 null 일 수 있다. related_line = 겹친 상대 줄(rm). */
export interface CsvParseError {
  line: number | null;
  row: number | null;
  reason_code: string;
  message: string;
  related_line: number | null;
}
/** 서버 해석 결과. **errors 가 비어 있지 않으면 부분 결과**다 — 화면은 반영하지 않는다(부분 반영 금지, 예전 계약 그대로).
 *  items 는 생성 바디의 items 와 같은 모양(스토리지는 알 때만), storages = 배치 스토리지(요청 값 → CSV 의 첫 값 → null). */
export interface CsvParseResult {
  items: Record<string, string>[];
  errors: CsvParseError[];
  error_count: number;
  rows: number;
  header: string[] | null;
  storages: Record<string, string | null>;
}

/** 「3번째 줄: <사유 문구>(2번째 줄과 겹침)」 — 사유 문구는 사유 코드 계약(reasonText) 하나다. */
export function csvErrorText(e: CsvParseError): string {
  const head = typeof e.line === "number" ? `${e.line}번째 줄: ` : "";
  const tail = typeof e.related_line === "number" ? ` (${e.related_line}번째 줄과 겹침)` : "";
  return `${head}${reasonText(e.reason_code)}${tail}`;
}

/** 화면에 늘어놓을 오류 줄들 — 서버가 상한(200)까지만 실으면 「외 N개」를 덧붙인다(조용한 절단 금지). */
export function csvErrorLines(r: Pick<CsvParseResult, "errors" | "error_count">): string[] {
  const lines = r.errors.map(csvErrorText);
  const more = r.error_count - r.errors.length;
  return more > 0 ? [...lines, `외 ${more}개 오류`] : lines;
}

/** 해석 결과의 항목 → 표의 경로 짝(a = target·source, b = destination). */
export function itemsToRows(op: ItemsCsvOp, items: Record<string, string>[]): (ScanRow | SyncRow)[] {
  return items.map((it) => op === "sync"
    ? { source: String(it.source ?? ""), destination: String(it.destination ?? "") }
    : { target: String(it.target ?? "") });
}

export function serializeItemsCsv(
  operation: ItemsCsvOp,
  rows: (ScanRow | SyncRow)[],
): string {
  // 헤더 포함 내보내기 — 재붙여넣기(서버 해석) 왕복이 성립해야 한다. 칸은 csvExport.csvCell(따옴표·수식 주입 방어 ') —
  // 서버 해석이 같은 집합의 ' 를 걷는다. 줄바꿈은 \n(텍스트 상자에 그대로 보이는 모양).
  if (operation !== "sync") {
    return ["target", ...rows.map((r) => csvCell((r as ScanRow).target))].join("\n");
  }
  return [
    "source,destination",
    ...rows.map((r) => `${csvCell((r as SyncRow).source)},${csvCell((r as SyncRow).destination)}`),
  ].join("\n");
}

/** 템플릿(경로 열 헤더 + 예시 한 줄) — <a href download> 로 부른다(세션 쿠키, 로그 다운로드와 같은 길). */
export const batchCsvTemplateUrl = (op: ItemsCsvOp) =>
  `/api/admin/batches/csv-template?operation=${encodeURIComponent(op)}`;
/** 배치 항목 내보내기(경로·스토리지 + 상태 열) — 고쳐서 「CSV로 전체 교체」·배치 생성에 그대로 다시 올릴 수 있다. */
export const batchItemsCsvUrl = (batchId: string) => `/api/admin/batches/${encodeURIComponent(batchId)}/items.csv`;
