// 경로별의 마지막 쿼리(2026-10-29 사용량 분석 하위 메뉴 -- 사용자 결정 「권장사항대로」). 경로별(/admin/usage)을 떠나 스토리지별로
// 갔다가 탭으로 돌아오면 보던 스토리지·필터·검색·정렬이 그대로여야 한다(심사 비평 15: 「탭 왕복에 필터가 사라진다」). URL 이
// 진실이므로 경로별이 지금 쿼리(target 제외 -- 시트는 다시 열지 않는다)와 지금 보는 스토리지(기본 스토리지는 URL 에 없으므로 따로)를
// 탭 단위 sessionStorage 에 적어 두고, 「경로별」 탭 링크가 그 쿼리를 복원하며 스토리지별 표가 그 스토리지 행을 현재로 칠한다.
//
// 저장소는 꾸밈(per-viewer convenience)일 뿐이다: 막힌 환경(사생활 모드·차단)·깨진 값이면 조용히 「기억 없음」 -- 링크는 쿼리 없는
// /admin/usage, 표에는 현재 행이 없다(navState 와 같은 try/catch 관용).
export const PATHS_MEMORY_KEY = "dms.usage.paths.v1";

export interface PathsMemory {
  search: string;              // 「?」 없는 쿼리 문자열(target 제외) -- 빈 문자열이면 쿼리 없음
  storage: string | null;      // 마지막으로 본 스토리지(빈 이름도 이름이다). 모르면 null
}

/** 쿼리 문자열에서 시트(target)를 뺀다 -- 「?」 없이. */
export function withoutTarget(search: string): string {
  const p = new URLSearchParams(search);
  p.delete("target");
  return p.toString();
}

export function readPathsMemory(): PathsMemory | null {
  try {
    const x: unknown = JSON.parse(sessionStorage.getItem(PATHS_MEMORY_KEY) ?? "");
    if (x === null || typeof x !== "object" || Array.isArray(x)) return null;
    const { search, storage } = x as Record<string, unknown>;
    if (typeof search !== "string") return null;
    return { search: withoutTarget(search), storage: typeof storage === "string" ? storage : null };
  } catch {
    return null;    // 저장분 없음·파싱 불가·스토리지 차단
  }
}

/** 「경로별」 탭 링크가 복원할 쿼리(「?」 없이 -- 기억이 없으면 빈 문자열). 기억한 쿼리에 storage 가 없으면(기본 스토리지를 보고
 *  있었다) 기억한 스토리지를 맨 앞에 적는다(2026-10-29 2차 리뷰): 붙잡은 기본 스토리지는 경로별 페이지의 상태라 탭을 오가면
 *  사라지고, 그 사이 요약이 바뀌면(다른 스토리지의 첫 scan) 다시 파생한 기본이 보던 것과 다른 스토리지가 됐다 -- 방금 스토리지별
 *  표가 「현재」로 칠한 행과도 어긋났다. */
export function pathsReturnSearch(): string {
  const mem = readPathsMemory();
  if (mem === null) return "";
  const p = new URLSearchParams(mem.search);
  if (p.has("storage") || mem.storage === null) return mem.search;
  return new URLSearchParams([["storage", mem.storage], ...p]).toString();
}

/** 경로별의 지금 쿼리를 적는다. storage 가 null(아직 모름)이면 전에 적은 스토리지를 남긴다. */
export function rememberPaths(search: string, storage: string | null): void {
  const value: PathsMemory = { search: withoutTarget(search), storage: storage ?? readPathsMemory()?.storage ?? null };
  try { sessionStorage.setItem(PATHS_MEMORY_KEY, JSON.stringify(value)); }
  catch { /* 차단 환경 -- 기억 없이 동작 */ }
}
