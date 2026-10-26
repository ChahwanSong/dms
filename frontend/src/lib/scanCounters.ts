// dscan 리포트 카운터의 화면 판정·문구(2026-10-26 -- 포크 ae781bd 가 summary 에 total_bytes·invalid_name_entries 를 더했다).
// 배치 항목의 scan 리포트·사용량 분석(펼친 상세·스토리지 합계·CSV)이 같은 판정과 같은 말을 쓰게 한 벌로 둔다.
//
// - 실 사용량(total_bytes): 서버가 summary.total_bytes(정확값)를 우선하고 구형 리포트는 히스토그램 합으로 물러나 한 값으로 준다
//   (routes_scan_paths._report_total_bytes). 화면은 사람 표기(반올림)를 보이고 정확한 바이트는 마우스를 올리면(exactBytes).
// - 잘못된 인코딩 이름(invalid_name_entries): 이름(경로의 마지막 성분)이 올바른 UTF-8 이 아닌 항목 수. 사용자 결정 「카운터만
//   둠」 -- 파손 경로(broken_paths_total)에 더하지 않는 별도 카운터다. 응답에 없거나(구형 리포트·구형 서버) 정수가 아니면
//   모름(null) -- 0(그런 이름 없음)과 다르다(null≠0).

// 응답 값 -> 카운터(안전 정수 ≥0) | null(모름). DB 가 신뢰 경계라 서버가 걸렀어도 화면이 다시 본다.
export function scanCount(v: unknown): number | null {
  return typeof v === "number" && Number.isSafeInteger(v) && v >= 0 ? v : null;
}

export const INVALID_NAMES_LABEL = "잘못된 인코딩 이름";

export const INVALID_NAMES_HELP = "이름(경로의 마지막 성분)이 올바른 UTF-8 이 아닌 항목 수입니다 — 예: CP949(EUC-KR)로 만든 "
  + "한글 이름. 그런 디렉터리 아래의 자식은 자기 이름이 올바르면 세지 않습니다. 파손 경로에 더하지 않는 별도 카운터이고, "
  + "scan 결과·실 사용량에는 그대로 들어 있습니다.";

// 「잘못된 인코딩 이름 N개」 / 모르면 「… 기록 없음(구형 리포트)」.
export function invalidNamesText(v: unknown): string {
  const n = scanCount(v);
  return n === null ? `${INVALID_NAMES_LABEL}: 기록 없음(구형 리포트)` : `${INVALID_NAMES_LABEL} ${n.toLocaleString("ko-KR")}개`;
}

// 정확한 바이트(title 용 -- 사람 표기는 소수 한 자리 반올림이다).
export function exactBytes(n: number): string {
  return `${n.toLocaleString("ko-KR")} B`;
}

// summary 의 키 중 화면·CSV 가 따로 말하는 것 -- 요약 목록(원 키 dl)·CSV 요약 열에서 빼 같은 값이 두 번 나오지 않게 한다.
export const SUMMARY_KEYS_SHOWN_ELSEWHERE: readonly string[] = ["total_bytes", "invalid_name_entries"];
