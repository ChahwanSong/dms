// 서버 검증의 클라이언트 미러(즉답용) — 최종 심판은 서버 422 invalid_option 이다.
// SubmitJob(단건 sync)과 BatchCreate(배치 옵션 스텝)가 공유한다(슬라이스 32 T8) —
// 파일별 사본이면 미러가 발산한다(슬라이스 31 T3 formFields 이사와 같은 이유).
// domain.py:112(_CHMOD_ITEM_RE 콤마 항목별 fullmatch)·119-120(_CHOWN_PART/_CHOWN_RE)의 미러.
// chown 파트는 「이름 또는 숫자 uid/gid」(dsync --chown 이 숫자를 받는다 —
// auto_chown 의 uid:gid 숫자 주입이 증명). 빈 파트 규칙(":gid" 허용, "user:" 거부) 동일.
export const CHMOD_RE = /^[DF]?[0-7]{1,4}(,[DF]?[0-7]{1,4})*$/;
export const CHOWN_RE = /^(?:[A-Za-z_][A-Za-z0-9._-]{0,63}|[0-9]{1,10})?(?::(?:[A-Za-z_][A-Za-z0-9._-]{0,63}|[0-9]{1,10}))?$/;

// sync 숫자 옵션의 범위 + 폼 프리필 값. domain.py `_OPTION_SPECS[SYNC]` 의 미러이고,
// SubmitJob(단건)·BatchCreate(배치)가 **이 한 곳**을 읽는다 — 파일별 리터럴이면
// 상한을 올릴 때 한쪽만 올라 미러가 발산한다(CHMOD_RE 를 여기로 모은 것과 같은 이유).
//
// prefill 은 폼 상태가 문자열이라 문자열이다. 「왜 프리필인가」(사용자 조정 2026-08-16):
//   - batch_files 1,000,000: dsync·nsync 도구 기본은 **0 = 배칭 안 함**
//     (mfu_flist_copy.c:3361). 대규모 sync 의 메모리 안정성을 위해 DMS 는 배칭을
//     기본으로 켠다 — 즉 이 프리필은 도구 기본과 **다른 동작**을 명시 전송하는
//     정책 결정이다.
//   - bufsize 4194304: 도구 기본(MFU_BUFFER_SIZE 4 MiB)과 **같은 값**이라 동작
//     변화는 없다. 명시만 한다.
// 2026-09-17(사용자 결정): 같은 값이 **서버 기본값**이기도 하다(domain.py
// _OPTION_DEFAULTS) — 입력을 비워 키가 빠지면 서버가 이 값을 박는다(API 직접 제출도
// 동일). 그래서 배칭을 끄는 표현은 "비우기"가 아니라 **0 명시**뿐이고 하한이 0 이다.
// prefill 과 서버 기본은 같은 숫자여야 한다(test_domain_option_defaults 가 고정).
export const SYNC_INT_FIELDS = {
  batch_files: { lo: 0, hi: 10_000_000, prefill: "1000000" },
  bufsize: { lo: 4096, hi: 1_073_741_824, prefill: "4194304" },
} as const;

// scan 숫자 옵션(dscan 1b93d54 실측: batch_files 0..10억, 0 = 배칭 끔; broken_limit
// 0..10,000). 2026-09-17 부터 프리필 + 서버 기본(domain.py _OPTION_DEFAULTS) —
// 둘 다 dscan 자체 기본(100만 / 100)과 같은 값이라 동작은 종전과 같고, 요청 상세에
// 어떤 값으로 돌았는지 명시적으로 남는다. 비우면 서버 기본으로 돌아간다.
export const SCAN_INT_FIELDS = {
  batch_files: { lo: 0, hi: 1_000_000_000, prefill: "1000000" },
  broken_limit: { lo: 0, hi: 10_000, prefill: "100" },
} as const;

export function scanIntFieldError(
  key: keyof typeof SCAN_INT_FIELDS, raw: string,
): string | null {
  const { lo, hi } = SCAN_INT_FIELDS[key];
  return intFieldError(key, raw, lo, hi);
}

// 라벨은 서버 오류 문구와 같은 키 이름을 쓴다(batch_files/bufsize) — 화면 문구와
// 서버 422 detail 이 같은 단어를 가리켜야 사용자가 둘을 잇는다.
export function syncIntFieldError(
  key: keyof typeof SYNC_INT_FIELDS, raw: string,
): string | null {
  const { lo, hi } = SYNC_INT_FIELDS[key];
  return intFieldError(key, raw, lo, hi);
}

// 정수 범위 미러(domain.py `_OPTION_SPECS` — sync 는 위 SYNC_INT_FIELDS, scan 은
// batch_files 0..10억 / broken_limit 0..10,000). 노드 수·노드당 프로세스 수처럼
// 도메인 스펙 밖 위생 상한도 이 함수를 쓴다.
// 빈 문자열은 "미입력"(생략 대상)이라 오류가 아니다.
export function intFieldError(label: string, raw: string, lo: number, hi: number): string | null {
  const v = raw.trim();
  if (v === "") return null;
  const n = Number(v);
  if (!Number.isInteger(n) || n < lo || n > hi)
    return `${label}는 ${lo}..${hi} 범위의 정수여야 합니다`;
  return null;
}
