// 서버 검증의 클라이언트 미러(즉답용) — 최종 심판은 서버 422 invalid_option 이다.
// SubmitJob(단건 sync)과 BatchCreate(배치 옵션 스텝)가 공유한다(슬라이스 32 T8) —
// 파일별 사본이면 미러가 발산한다(슬라이스 31 T3 formFields 이사와 같은 이유).
// domain.py:112(_CHMOD_ITEM_RE 콤마 항목별 fullmatch)·119-120(_CHOWN_PART/_CHOWN_RE)의 미러.
// chown 파트는 「이름 또는 숫자 uid/gid」(dsync --chown 이 숫자를 받는다 —
// auto_chown 의 uid:gid 숫자 주입이 증명). 빈 파트 규칙(":gid" 허용, "user:" 거부) 동일.
export const CHMOD_RE = /^[DF]?[0-7]{1,4}(,[DF]?[0-7]{1,4})*$/;
export const CHOWN_RE = /^(?:[A-Za-z_][A-Za-z0-9._-]{0,63}|[0-9]{1,10})?(?::(?:[A-Za-z_][A-Za-z0-9._-]{0,63}|[0-9]{1,10}))?$/;

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
