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
//     정책 결정이다(예전 "빈값 = 플래그 생략 = 도구 기본" 계약의 의도된 변경).
//   - bufsize 4194304: 도구 기본(MFU_BUFFER_SIZE 4 MiB)과 **같은 값**이라 동작
//     변화는 없다. 명시만 한다.
// 사용자가 입력을 비우면 예전처럼 키가 통째로 빠져 도구 기본으로 돌아간다 — 그
// 성질이 배칭을 끄는 유일한 표현이라 폼·테스트가 함께 지킨다. 서버가 기본값을
// 박지 않는 이유도 같다(domain.py 의 「왜」 주석).
export const SYNC_INT_FIELDS = {
  batch_files: { lo: 1, hi: 10_000_000, prefill: "1000000" },
  bufsize: { lo: 4096, hi: 1_073_741_824, prefill: "4194304" },
} as const;

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
