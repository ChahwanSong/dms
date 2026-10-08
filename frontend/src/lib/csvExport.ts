// 범용 CSV 내보내기(2026-10-02 사용량 분석 CSV). lib/csv 는 배치 항목 왕복(붙여넣기 파싱)용 2열 전용이라
// 따옴표 규칙이 없다 -- 임의 열·임의 문자열을 싣는 내보내기는 여기서 RFC 4180 대로 쓴다.
//
// - 따옴표: 쉼표·큰따옴표·줄바꿈이 있으면 "..." 로 감싸고 " 는 "" 로.
// - 수식 주입 방어: = + - @ (와 탭·CR)로 시작하는 **문자열** 칸은 앞에 ' 를 붙인다 -- 스프레드시트가 경로
//   "=HYPERLINK(...)" 같은 값을 수식으로 실행하지 않게(OWASP CSV injection). 숫자 칸은 그대로(음수 증감 -123 이
//   글자로 변하면 안 된다).
// - null/undefined = 빈 칸(모름). 0 은 "0"(정상값) -- null≠0.
// - 다운로드는 UTF-8 BOM 을 붙인다: 엑셀이 BOM 없는 UTF-8 을 시스템 코드페이지로 읽어 한글 경로가 깨진다.

export type CsvCell = string | number | boolean | null | undefined;

const FORMULA_START = /^[=+\-@\t\r]/;

export function csvCell(v: CsvCell): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "number") return Number.isFinite(v) ? String(v) : "";
  let s = typeof v === "boolean" ? String(v) : v;
  if (FORMULA_START.test(s)) s = `'${s}`;
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(columns: string[], rows: Record<string, CsvCell>[]): string {
  const lines = [columns.map(csvCell).join(",")];
  for (const r of rows) lines.push(columns.map((c) => csvCell(r[c])).join(","));
  return lines.join("\r\n") + "\r\n";
}

// CSV 다운로드 = BOM + 본문(엑셀 한글 깨짐 방지, 위 머리 주석).
const BOM = String.fromCharCode(0xfeff);
export function downloadCsv(filename: string, text: string): void {
  downloadText(filename, BOM + text, "text/csv;charset=utf-8");
}

// 브라우저 다운로드(Blob + 임시 a). 런타임 외부 리소스 없음(airgap). 서버 라우트 없이 화면이 이미 받은 문자열을
// 파일로 내릴 때 쓴다(요청 상세 「로그 저장」 -- 로그에는 다운로드 라우트가 없다). clipboard API 는 쓰지 않는다:
// 포탈은 http(비보안 컨텍스트)로도 뜨므로 navigator.clipboard 가 없을 수 있다.
export function downloadText(filename: string, text: string, type = "text/plain;charset=utf-8"): void {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
