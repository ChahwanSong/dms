import { afterEach, expect, test, vi } from "vitest";
import { csvCell, downloadCsv, downloadText, toCsv } from "./csvExport";

test("csvCell: 따옴표 규칙(RFC 4180) -- 쉼표·따옴표·줄바꿈만 감싼다", () => {
  expect(csvCell("plain")).toBe("plain");
  expect(csvCell("a,b")).toBe('"a,b"');
  expect(csvCell('say "hi"')).toBe('"say ""hi"""');
  expect(csvCell("line1\nline2")).toBe('"line1\nline2"');
});

test("csvCell: 수식 주입 방어는 문자열에만 -- 숫자(음수 증감 포함)는 그대로", () => {
  expect(csvCell("=HYPERLINK(\"x\")")).toBe("\"'=HYPERLINK(\"\"x\"\")\"");
  expect(csvCell("+cmd")).toBe("'+cmd");
  expect(csvCell("-rf")).toBe("'-rf");
  expect(csvCell("@sum")).toBe("'@sum");
  expect(csvCell(-123)).toBe("-123");
  expect(csvCell(0)).toBe("0");                 // 0 은 정상값
});

test("csvCell: null·undefined·비유한수 = 빈 칸(모름), 불리언은 글자", () => {
  expect(csvCell(null)).toBe("");
  expect(csvCell(undefined)).toBe("");
  expect(csvCell(Number.NaN)).toBe("");
  expect(csvCell(true)).toBe("true");
});

test("toCsv: 헤더 + 열 순서대로, CRLF 줄끝, 없는 키는 빈 칸", () => {
  expect(toCsv(["a", "b"], [{ a: 1, b: "x" }, { a: 2 }])).toBe("a,b\r\n1,x\r\n2,\r\n");
});

// --- 다운로드: jsdom 에는 URL.createObjectURL 이 없어 가로채서 Blob 을 붙잡는다 ---------------------------------
const realCreate = URL.createObjectURL;
const realRevoke = URL.revokeObjectURL;
afterEach(() => {
  URL.createObjectURL = realCreate;
  URL.revokeObjectURL = realRevoke;
  vi.restoreAllMocks();
});
function captureBlobs(): Blob[] {
  const blobs: Blob[] = [];
  URL.createObjectURL = vi.fn((b: Blob) => { blobs.push(b); return "blob:dms-test"; }) as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
  // a.click() 의 내비게이션은 jsdom 미구현이라 막는다(다운로드 자체는 브라우저 몫).
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  return blobs;
}
function bytesOf(b: Blob): Promise<Uint8Array> {
  return new Promise((resolve) => {
    const r = new FileReader();
    r.onload = () => resolve(new Uint8Array(r.result as ArrayBuffer));
    r.readAsArrayBuffer(b);
  });
}

test("downloadCsv: UTF-8 BOM 을 앞에 붙인다(엑셀 한글 깨짐 방지 -- downloadText 로 옮긴 뒤에도 그대로)", async () => {
  const blobs = captureBlobs();
  downloadCsv("x.csv", "가,b\r\n");
  expect(blobs).toHaveLength(1);
  expect(blobs[0].type).toBe("text/csv;charset=utf-8");
  const bytes = await bytesOf(blobs[0]);
  expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
});

test("downloadText: 받은 문자열 그대로(BOM 없음) + 파일 이름 + 기본 text/plain", async () => {
  const blobs = captureBlobs();
  const appended: HTMLAnchorElement[] = [];
  const realAppend = document.body.appendChild.bind(document.body);
  vi.spyOn(document.body, "appendChild").mockImplementation(<T extends Node>(n: T): T => {
    if (n instanceof HTMLAnchorElement) appended.push(n);
    return realAppend(n);
  });
  downloadText("j1-preflight.log", "===== p1 =====\nhello\n");
  expect(blobs[0].type).toBe("text/plain;charset=utf-8");
  const bytes = await bytesOf(blobs[0]);
  expect(new TextDecoder().decode(bytes)).toBe("===== p1 =====\nhello\n");
  expect(appended[0].download).toBe("j1-preflight.log");
  expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:dms-test");
});
