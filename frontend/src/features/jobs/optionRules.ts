import { CHOWN_NAME_ERROR } from "../../lib/syncOwnership";
// 서버 검증의 클라이언트 미러(즉답용) — 최종 심판은 서버 422 invalid_option 이다.
// SubmitJob(단건 sync)과 BatchCreate(배치 옵션 스텝)가 공유한다(슬라이스 32 T8) —
// 파일별 사본이면 미러가 발산한다(슬라이스 31 T3 formFields 이사와 같은 이유).
// domain.py 의 _CHMOD_ITEM_RE(콤마 항목별 fullmatch)·_CHOWN_RE 의 미러.
// chown 은 **숫자 uid/gid 만**(2026-10-01 사용자 결정 -- 이름은 잡 컨테이너에서 LDAP 으로 풀리지 않아 미리보기
// 실패·엉뚱한 gid·root 실행 시 uid 0 이 됐다; domain.chown_problem 주석). 빈 파트 규칙(":gid" 허용, "user:" 거부) 동일.
export const CHMOD_RE = /^[DF]?[0-7]{1,4}(,[DF]?[0-7]{1,4})*$/;
export const CHOWN_RE = /^(?:[0-9]{1,10})?(?::[0-9]{1,10})?$/;
// 이름 모양(서버 _CHOWN_NAMED_RE 미러) -- 이것에만 맞으면 "이름은 안 된다", 둘 다 아니면 형식 오류.
const CHOWN_NAMED_RE =
  /^(?:[A-Za-z_][A-Za-z0-9._-]{0,63}|[0-9]{1,10})?(?::(?:[A-Za-z_][A-Za-z0-9._-]{0,63}|[0-9]{1,10}))?$/;

/** chown 칸 오류 문구(빈 값 = 미지정 = 정상). 서버 domain.chown_problem 과 같은 분류: 숫자 = 정상, 이름 모양 =
    chown_name_not_supported 와 같은 뜻, 그 밖 = 형식 오류. SubmitJob(단건)·BatchCreate(배치)가 이 한 곳을 읽는다. */
export function chownFieldError(raw: string): string | null {
  const c = raw.trim();
  if (c === "" || CHOWN_RE.test(c)) return null;
  return CHOWN_NAMED_RE.test(c) ? CHOWN_NAME_ERROR : "chown 형식이 올바르지 않습니다 (예: 10003:10000 — 숫자 uid:gid)";
}

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

// 제출 폼(SubmitJob·BatchCreate)과 요청 상세(requestSpec)가 같은 문구를 쓴다(한 곳) -- 화면마다 사본이면 같은 옵션이
// 화면마다 다르게 설명된다. 바꾸면 SubmitJob.test·BatchCreate.test 의 정확 일치가 함께 깨져야 정상이다.
export const SYNC_OPTION_HELP = {
  delete: "원본에 없는 파일을 대상에서도 삭제해 완전히 동일하게 맞춥니다(미러 동기화).",
  contents: "크기·수정시각 대신 파일 내용을 바이트 단위로 비교합니다(더 느리지만 정확).",
} as const;
export const OPEN_NOATIME_NON_ROOT =
  "open_noatime 은 root 실행에서만 적용됩니다 — 일반 실행 신원은 남의 파일을 O_NOATIME 으로 열 수 없어(EPERM) 복사가 실패합니다.";
// 실행 권한 문구(제출 요약 「실행 권한」 ↔ 요청 상세 「요청 내용」의 「실행 권한」). root 의 어휘·색(text-bad)은 포탈 전체가 같다.
// 요청 상세는 PRIV_ROOT_HEAD·PRIV_USER 만 쓴다 -- sync 의 소유 결과는 같은 카드의 「목적지 소유」 행이 말하므로
// PRIV_ROOT_SYNC(소유 결과를 덧붙인 꼬리)는 제출 요약 전용이다(한 화면에서 같은 사실을 두 번 말하지 않는다).
export const PRIV_ROOT_HEAD = "root(특권) — 권한 검사 우회";
export const PRIV_ROOT_SYNC = `${PRIV_ROOT_HEAD}, sync 는 목적지 소유·권한을 소스에 맞춤(chown·chmod 지정 시 그 값)`;
export const PRIV_USER = "실행 신원의 uid/gid(권한 그대로 적용)";
