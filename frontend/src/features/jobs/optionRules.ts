// 서버 검증의 클라이언트 미러(즉답용) — 최종 심판은 서버 422 invalid_option 이다.
// SubmitJob(단건 sync)과 BatchCreate(배치 옵션 스텝)가 공유한다(슬라이스 32 T8) —
// 파일별 사본이면 미러가 발산한다(슬라이스 31 T3 formFields 이사와 같은 이유).
// domain.py:112-113(_CHMOD_ITEM_RE 콤마 항목별 fullmatch·_CHOWN_RE)의 미러.
export const CHMOD_RE = /^[DF]?[0-7]{1,4}(,[DF]?[0-7]{1,4})*$/;
export const CHOWN_RE = /^([A-Za-z_][A-Za-z0-9._-]{0,63})?(:[A-Za-z_][A-Za-z0-9._-]{0,63})?$/;

// 정수 범위 미러(domain.py:127-128 — batch_files 1..1,000,000 / bufsize 4096..1GiB).
// 빈 문자열은 "미입력"(생략 대상)이라 오류가 아니다.
export function intFieldError(label: string, raw: string, lo: number, hi: number): string | null {
  const v = raw.trim();
  if (v === "") return null;
  const n = Number(v);
  if (!Number.isInteger(n) || n < lo || n > hi)
    return `${label}는 ${lo}..${hi} 범위의 정수여야 합니다`;
  return null;
}
