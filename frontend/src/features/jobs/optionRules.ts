import { CHOWN_NAME_ERROR } from "../../lib/syncOwnership";
import { REASON_MESSAGES } from "../../lib/api";
import type { Policy } from "../../lib/types";
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

// direct + bufsize 교차 검증(2026-10-27 d192, 포탈 괴리 [8]) -- 서버 domain.validate_options 의 미러(422
// direct_bufsize_misaligned). dsync 는 O_DIRECT 일 때 파일을 4 MiB 조각(MFU_CHUNK_SIZE, DMS 는 --chunksize 를 넘기지
// 않는다)으로 나눠 읽는데, 조각 길이가 버퍼 크기의 배수가 아니면 파일 끝이 아닌 조각에서 MFU_ABORT(MPI 중단)한다
// (mfu_flist_copy.c). 4 MiB 의 약수이면서 4 KiB(블록) 정렬인 값 = 4096·2^k ≤ 4194304 뿐이다. 미리보기는 복사를 안 해
// 통과하고 실행에서 큰 파일이 여러 프로세스에 나뉠 때 중단되므로, 보내기 전에 막는다.
// 빈 값(생략)은 서버 기본 4 MiB 라 정렬돼 있고, 범위 밖 정수·정수 아님은 syncIntFieldError 가 먼저 말한다(같은 칸에 두 문장을
// 겹쳐 그리지 않는다). 문구는 서버 422 와 **같은 문장**(REASON_MESSAGES) -- 화면 즉답과 서버 거부가 다르게 말하지 않게.
export const DIRECT_BUFSIZE_MIN = 4096;
export const DIRECT_BUFSIZE_MAX = 4_194_304;
export function directBufsizeError(direct: boolean, raw: string): string | null {
  if (!direct) return null;
  const v = raw.trim();
  if (v === "") return null;
  const n = Number(v);
  const { lo, hi } = SYNC_INT_FIELDS.bufsize;
  if (!Number.isInteger(n) || n < lo || n > hi) return null;          // 범위 오류는 syncIntFieldError 몫
  // n 은 1 GiB 이하라 32비트 비트 연산이 안전하다 -- 2의 거듭제곱 판정.
  if (n >= DIRECT_BUFSIZE_MIN && n <= DIRECT_BUFSIZE_MAX && (n & (n - 1)) === 0) return null;
  return REASON_MESSAGES.direct_bufsize_misaligned;
}

// 제출 폼(SubmitJob·BatchCreate)·배치 실행 설정 변경과 요청 상세(requestSpec)가 같은 문구를 쓴다(한 곳) -- 화면마다 사본이면
// 같은 옵션이 화면마다 다르게 설명된다. 바꾸면 SubmitJob.test·BatchCreate.test 의 정확 일치가 함께 깨져야 정상이다.
// 2026-10-27 d192(포탈 괴리 [3]·[24]·[25]·[28]):
//   - delete: 「완전히 동일하게 맞춥니다」는 더는 참이 아니다 -- 소스의 FIFO·소켓·장치는 두 도구 모두 복사하지 않지만(nsync·
//     dsync "Skipped N special file(s)") 그 이름의 목적지 항목을 다루는 방식이 **도구마다 다르다**(2026-10-28 리뷰 portal-batch-26·
//     contract-39 -- 예전 문구는 두 도구 모두 남긴다고 했다): nsync 는 비교 단계에서 특수 파일을 빼 목적지 항목을 delete 와
//     무관하게 그대로 둔다(경고 꼬리 "the destination entry at that name is left as it is"). dsync 는 비교에서 종류가 다르면
//     (예: 소스 FIFO, 목적지 일반 파일) delete 검사 없이 목적지 항목을 지울 목록에 넣고(dsync.c 의 type differ 분기 →
//     dst_remove_list·dst_retype_list) 복사 단계는 특수 파일을 만들지 않는다(mfu_flist_copy.c) -- 그 이름이 목적지에서 사라진다
//     (rc 0, 포크 빌드 실측). 비어 있지 않은 디렉토리면 delete 없이는 "Type change not applied" 로 실패하고, delete 를 켜면
//     하위 트리째 지워진다. 같은 종류(FIFO↔FIFO)는 속성만 맞추고 지우지 않는다. dsync 미리보기(dry-run)는 지울 목록을 채우지
//     않아 이 경우를 미리 말할 수 없다 -- 그래서 도움말이 미리 말한다.
//     소스를 다 읽지 못한 실행은 delete 를 그 실행에서 끈다(nsync "--delete is off for this run", dsync "delete option
//     disabled") -- 꺼지는 것은 「소스에 없는 항목 지우기」뿐이고 타입 교체의 목적지 삭제는 그대로라 「삭제를 하지 않는다」가
//     아니라 「소스에 없는 항목을 지우지 않는다」로 말한다. 반대로 delete 가 꺼져 있으면 소스에서 파일·링크로 바뀐 디렉토리를
//     목적지에서 비우지 못해 그 항목이 실패한다(sync_type_change_refused) -- 끌 때의 결과도 말한다.
//   - contents: nsync 는 바이트 비교가 아니라 스캔 중 양쪽 모든 일반 파일의 SHA-256 을 계산한다(미리보기 포함) -- 읽을 수 없는
//     파일 하나가 미리보기를 실패시킨다. dsync 는 양쪽에 있고 크기가 같은 파일만 바이트 비교한다.
export const SYNC_OPTION_HELP = {
  delete: "원본에 없는 항목을 대상에서 지웁니다(미러 동기화). 원본에서 파일·링크로 바뀐 디렉토리가 대상에 비어 있지 않게 남아 "
    + "있으면 이 옵션을 켜야 교체되고, 끄면 그 항목은 실패로 남습니다. 원본의 FIFO·소켓·장치 파일은 복사하지 않습니다 — 노드 간 "
    + "실행(nsync)은 대상의 같은 이름 항목을 그대로 두고, 한 노드 실행(dsync)은 그 이름의 대상 항목이 다른 종류면 이 옵션과 "
    + "무관하게 지운 뒤 아무것도 만들지 않아 그 이름이 대상에서 사라집니다(비어 있지 않은 디렉토리는 이 옵션을 켜야 지워지고, "
    + "끄면 실패로 남습니다). 원본 일부를 읽지 못한 실행은 안전을 위해 그 실행에서 원본에 없는 항목을 지우지 않습니다.",
  contents: "크기·수정시각 대신 파일 내용으로 비교합니다(더 느리지만 정확). 미리보기 단계부터 파일을 읽습니다 — 한 노드 실행"
    + "(dsync)은 양쪽에 있고 크기가 같은 파일을 바이트 단위로, 노드 간 실행(nsync)은 양쪽 모든 파일의 SHA-256 을 비교하므로 "
    + "실행 신원이 읽을 수 없는 그런 파일이 하나라도 있으면 미리보기가 실패합니다.",
} as const;
// 단계별 등록(2026-11-26, dms-k8s ed7fe322 이식)의 토글 카드 요약 한 줄 -- 전문은 바로 위 SYNC_OPTION_HELP(.delete·.contents)이고
// 요약은 그 **첫 문장 그대로**다(전문 앞부분과 다르면 wizardParts.test 가 빨갛다). 전문은 「자세히」 또는 토글을 켜면 펼쳐진다
// (components/form/wizardParts MoreText·OptionToggle). 요약을 바꾸려면 전문의 첫 문장을 같이 바꾼다 -- 둘이 따로 낡지 않게.
const SUMMARY_DELETE = "원본에 없는 항목을 대상에서 지웁니다(미러 동기화).";
const SUMMARY_CONTENTS = "크기·수정시각 대신 파일 내용으로 비교합니다(더 느리지만 정확).";
// delete 와 무관한 dsync 삭제(단계별 등록 2026-11-26 -- 옵션 단계에 늘 보이는 한 줄 + 검토의 목적지 조건 카드). 근거 둘:
//   (1) 앞부분은 SYNC_OPTION_HELP.delete 의 절(「한 노드 실행(dsync)은 그 이름의 대상 항목이 다른 종류면 이 옵션과 무관하게 지운 뒤 …
//       비어 있지 않은 디렉토리는 이 옵션을 켜야 지워지고, 끄면 실패로 남습니다」)을 다시 쓴 것이다 -- 범위는 원본의 FIFO·소켓·장치와
//       이름이 같은 경우뿐이고, 비어 있지 않은 디렉토리는 delete 없이는 실패다.
//   (2) 꼬리 「dsync 미리보기에는 나오지 않습니다」는 위 SYNC_OPTION_HELP 주석의 「dsync 미리보기(dry-run)는 지울 목록을 채우지 않아
//       이 경우를 미리 말할 수 없다」의 사용자 문장이다. nsync 는 이 항목을 지우지 않으니(비교 단계에서 특수 파일을 뺀다) 「dsync
//       미리보기」로 좁혔다.
// delete 토글을 접어도(요약만 보여도) 이 사실이 사라지지 않게 따로 둔다 -- delete 를 꺼 두면 「아무것도 안 지운다」로 읽히기 쉽다.
export const SYNC_DSYNC_SPECIAL_FILE_NOTE =
  "delete 를 꺼도 한 노드 실행(dsync)은 원본의 FIFO·소켓·장치 파일과 이름이 같은 대상 항목이 다른 종류면 지웁니다(비어 있지 않은 "
  + "디렉토리는 실패로 남습니다) — dsync 미리보기에는 나오지 않습니다.";
// open_noatime·direct 안내(SubmitJob·BatchCreate·배치 실행 설정 공용). 포크(nsync db1e33c·공통 43ba468, 2026-10-27
// 7a3189c 에서 dsync --contents 비교 경로까지)부터 nsync·dsync 모두 O_NOATIME 이 EPERM(실행 신원 소유가 아닌 파일)이면 그
// 파일만 O_NOATIME 없이 다시 연다 -- 복사도 contents 비교도. 그래서 root 실행 전용으로 막지 않는다. 읽기가 atime 을 갱신하지
// 않는 스토리지(noatime 마운트, 테스트베드 CephFS 실측 -- CHANGELOG d190)에선 켜고 끄는 차이가 보이지 않는다(괴리 [C3]).
// relatime 은 그 목록에 넣지 않는다(2026-10-28 리뷰 portal-batch-27): 리눅스 기본 relatime 은 읽을 때 「이전 atime 이 mtime·
// ctime 이하이거나 24시간보다 오래됐으면」 갱신한다 -- 오래 안 읽힌(cold) 파일은 sync 한 번에 최근 접근이 되고, 반복 읽기만
// 하루 단위로 접힌다. 테스트베드 CephFS 가 갱신하지 않은 것은 relatime 마운트인데도 그랬던 CephFS 의 성질이다(같은 노드 ext4
// 는 갱신). 예전 문구는 relatime 을 「차이 없음」에 넣어, relatime 스토리지에서 이 옵션을 꺼도 된다고 읽혔다.
// direct 는 dsync 만 O_DIRECT 로 열고 nsync 는 받기만 한다(정렬 I/O 미구현, 포크 db1e33c). bufsize 조건은 directBufsizeError.
export const OPEN_NOATIME_HELP =
  "원본을 읽을 때 접근 시각(atime)을 바꾸지 않습니다. 읽기가 atime 을 갱신하는 스토리지에서만 차이가 납니다 — 리눅스 기본 "
  + "relatime 도 마지막 접근이 하루를 넘었거나 수정 뒤 처음 읽는 파일은 갱신하므로 차이가 나고, noatime 마운트나 일부 CephFS "
  + "처럼 읽기가 atime 을 바꾸지 않는 곳에선 차이가 없습니다. 실행 신원 소유가 아닌 파일은 커널이 허용하지 않아 그 파일만 접근 "
  + "시각 보존 없이 그대로 읽습니다(복사·contents 비교 모두, root 실행은 전부 보존).";
export const DIRECT_HELP =
  "O_DIRECT(페이지 캐시 우회)로 읽고 씁니다 — 한 노드에서 dsync 로 실행될 때만 적용되고, 스토리지가 노드별로 나뉘어 nsync 로 "
  + "실행되면 무시됩니다. 켜면 bufsize 는 4 KiB~4 MiB 의 2의 거듭제곱(4096·8192·…·4194304)이어야 합니다(dsync 는 4 MiB 단위로 "
  + "나눠 읽어 그 약수가 아니면 큰 파일에서 중단됩니다).";
// 요약 — 전문은 바로 위 DIRECT_HELP. 핵심 사실 둘(nsync 에선 무시·켜면 bufsize 조건)을 요약에 담는다(d192 「설명은 칸 옆에」 결정:
// 접힌 채로도 무엇이 걸리는지 보인다).
const SUMMARY_DIRECT = "O_DIRECT(페이지 캐시 우회)로 읽고 씁니다 — dsync 로 실행될 때만 적용되고 켜면 bufsize 조건이 붙습니다.";
// bufsize 칸 설명(단건·배치 생성 공용). 프리필 계약(2026-09-17)에 direct 조건을 덧붙인다.
export const BUFSIZE_HELP =
  "미리 채운 4194304 = 4 MiB(서버 기본). 비워도 같은 값이 적용됩니다. direct 를 켜면 4096·8192·…·4194304(4 KiB~4 MiB 의 "
  + "2의 거듭제곱)만 쓸 수 있습니다.";
// quiet 안내(2026-10-27 d192, 포탈 괴리 [9]·[13]·[18]·[29]): -q 는 도구 로그 수준을 「없음」으로 둔다(nsync·dsync·drm 모두
// MFU_LOG_NONE) -- 줄이는 게 아니라 오류 줄·실패 항목 목록·요약까지 전부 사라진다. 그래서 결과 개수는 「—」이고, 러너가
// 실패 원인(DMS_EXEC_REASON)을 가를 재료도 없어 실패하면 일반 사유(execution_failed)로만 남는다. 미리보기는 대상 수를 세야
// 해서 quiet 를 빼고 돈다(execution_manifests). 단건·배치 생성·배치 실행 설정이 같은 문구를 쓴다.
export const QUIET_HELP = {
  sync: "도구 출력을 모두 끕니다 — 실행 결과의 개수(반영·삭제한 항목)가 「—」가 되고, 실패한 항목 목록과 오류 줄도 남지 않아 "
    + "실패하면 원인이 일반 사유(「실행에 실패했습니다」)로만 보입니다. 미리보기(dry-run)에는 적용하지 않습니다(대상 수를 세는 "
    + "줄이 사라지므로).",
  rm: "도구 출력을 모두 끕니다 — 실행 결과의 개수(삭제한 항목)가 「—」가 되고, 지우지 못한 항목 목록과 오류 줄도 남지 않아 "
    + "실패하면 원인이 일반 사유(「실행에 실패했습니다」)로만 보입니다. 미리보기(dry-run)에는 적용하지 않습니다(지울 대상 수를 "
    + "세는 줄이 사라지므로).",
} as const;
// 요약 — 전문은 바로 위 QUIET_HELP(.sync·.rm). 「실패 원인까지 지운다」가 핵심이라 요약에 남긴다(괴리 [9]·[13]·[18]·[29]).
const SUMMARY_QUIET = "도구 출력을 모두 끕니다 — 실패하면 원인이 일반 사유로만 보입니다.";
// nsync 실패 한도 안내(2026-10-27 d192, 포탈 괴리 [21]): 포크 nsync 는 --max-errors 기본 65,000(NSYNC_DEFAULT_MAX_ERRORS)에
// 닿으면 배치 사이에서 멈추고(배치 안에서는 목적지 rank 마다 남은 몫에서) 남은 액션을 시도하지 않는다 -- DMS 는 이 값을 넘기지도
// 바꾸게 하지도 않는다(domain._OPTION_SPECS 에 없음). 멈춘 실행은 sync_stopped_max_errors 로 남는다. 한도가 화면 어디에도 없어
// 「왜 더 나아가지 않는가」를 알 수 없었다 -- sync 옵션 구획 캡션(단건·배치 생성)이 말한다. 문구는 그 사유 메시지와 같은 어휘.
export const SYNC_MAX_ERRORS_NOTE =
  "노드 간 실행(nsync)은 실패한 항목이 허용 한도(기본 65,000건)에 닿으면 남은 항목을 시도하지 않고 멈춥니다 — 원인을 고친 뒤 "
  + "다시 실행하면 이미 맞춘 항목은 건너뛰고 이어서 맞춥니다.";
// 요약 — 전문은 바로 위 SYNC_MAX_ERRORS_NOTE(옵션 단계 「실행 옵션」 머리의 자세히).
const SUMMARY_MAX_ERRORS = "실패 한도: nsync 는 실패 65,000건에 닿으면 멈춥니다.";
// 배치 생성의 open_noatime 칸 설명 -- 배치는 특권(root) 실행이라 모든 파일을 보존으로 읽는다. 「통계를 정직하게 유지」는
// 읽기가 atime 을 갱신하는 스토리지에서만 참이라 그 조건을 함께 말한다(괴리 [C3]). relatime 은 「차이 없음」 쪽이 아니다
// (2026-10-28 리뷰 portal-batch-27 -- OPEN_NOATIME_HELP 주석): relatime 스토리지에서 이 옵션을 끄면 sync 가 읽은 cold 파일이
// 다음 scan 에서 hot 으로 뒤집혀 온도 통계가 흐려진다 -- 그 경우를 명시한다.
export const BATCH_OPEN_NOATIME_HELP =
  "기본 켬 — 배치는 특권(root) 실행이라 모든 원본 파일을 접근 시각(atime)을 바꾸지 않고 읽습니다. 읽기가 atime 을 갱신하는 "
  + "스토리지에서 데이터 온도(hot/cold) 통계가 sync 때문에 흐려지지 않게 합니다 — 리눅스 기본 relatime 도 마지막 접근이 하루를 "
  + "넘은 파일은 읽을 때 갱신하므로(끄면 sync 가 읽은 cold 파일이 hot 이 됩니다) 의미가 있고, noatime 마운트나 일부 CephFS 처럼 "
  + "읽기가 atime 을 바꾸지 않는 곳에선 차이가 없습니다.";
// 실행 권한 문구(제출 요약 「실행 권한」 ↔ 요청 상세 「요청 내용」의 「실행 권한」). root 의 어휘·색(text-bad)은 포탈 전체가 같다.
// 요청 상세는 PRIV_ROOT_HEAD·PRIV_USER 만 쓴다 -- sync 의 소유 결과는 같은 카드의 「목적지 소유」 행이 말하므로
// PRIV_ROOT_SYNC(소유 결과를 덧붙인 꼬리)는 제출 요약 전용이다(한 화면에서 같은 사실을 두 번 말하지 않는다).
export const PRIV_ROOT_HEAD = "root(특권) — 권한 검사 우회";
export const PRIV_ROOT_SYNC = `${PRIV_ROOT_HEAD}, sync 는 목적지 소유·권한을 소스에 맞춤(chown·chmod 지정 시 그 값)`;
export const PRIV_USER = "실행 신원의 uid/gid(권한 그대로 적용)";

/** 정책 캡션(단일 ③ 「실행 설정」·배치 ④ 노드 수 아래)을 「자세히」에 접지 않고 늘 펼칠 때인가 -- 캡션이 「이 요청은 잡 배치에서
 *  거부될 수 있다」는 경고를 담을 때다(dms-k8s b1eddc3c 이식 -- 2026-11-26 단계별 등록 뒤 지적: 옛 한 장 시트는 캡션이라 늘 보였는데
 *  요약 + 자세히로 접히자 관리자가 비활성 정책을 모른 채 제출할 수 있었다). 경고 = 정책 목록 조회 실패 · 걸릴 수 있는 정책 행이
 *  비활성(resolve_fanout policy_disabled) · 걸릴 정책 행이 하나도 없음(missing_policy). 조회 중은 경고가 아니다(모름 ≠ 실패). sync 는
 *  dsync·nsync 중 어느 쪽이 걸릴지 런타임(공존 노드)에야 갈려서, 있는 행 중 하나라도 비활성이면 경고이고 행 부재는 둘 다 없을 때만
 *  경고다(한쪽만 있으면 캡션이 「미조회」로 말하는 것으로 둔다 -- 공존 노드가 있으면 dsync 하나로 돈다). 캡션 문장은 화면의
 *  policyCaption 그대로다(SubmitJob·BatchCreate 의 policyCaption 과 같은 분기: 조회 중 · 조회 실패 · 행 부재 · 비활성 꼬리).
 *  dms-ssc 단일 작업은 정책을 관리자에게만 조회한다(usePolicies(isAdmin)) -- 꺼진 조회는 isLoading 이 거짓이고 data 가 없어 여기서
 *  「조회 실패」(true)로 읽히므로, 캡션을 그리는 관리자 화면에서만 부른다. */
export function policyCaptionAlert(byTool: Map<string, Pick<Policy, "enabled">> | undefined, op: string,
                                   loading: boolean): boolean {
  if (loading) return false;
  if (byTool === undefined) return true;
  const rows = (op === "sync" ? ["dsync", "nsync"] : [op]).flatMap((t) => {
    const p = byTool.get(t);
    return p === undefined ? [] : [p];
  });
  return rows.length === 0 || rows.some((p) => p.enabled !== 1);
}

// 단계별 등록(2026-11-26, dms-k8s ed7fe322 이식)의 「한 줄 요약 + 자세히」 요약 문장 모음 -- 단일 작업(SubmitJob)·배치 생성
// (BatchCreate)이 같이 읽는다(components/form/wizardParts MoreText·OptionToggle 의 summary). 전문이 이 파일에 있는 것은 그 상수 바로
// 옆에서 정의했고(위 SUMMARY_*), 전문이 화면·다른 모듈에 있는 것은 여기서 정의하며 전문의 자리를 적는다. 요약은 전문과 **다른
// 문장**이거나(정규식 단언이 둘에 겹치지 않게) 전문의 첫 문장 그대로다(정확 일치 단언만 영향 없음) -- dms-k8s design-wizard §3·R12.
// 실행 신원 문장은 두 화면이 다르다(dms-k8s 와의 차이 -- 거기는 모든 잡이 root 라 「늘 root」가 참이다): 배치 자식은 만든 관리자에게
// 계획 시점 root 자격이 있으면 root(identity.PRIVILEGE_IF_ELIGIBLE -- planner 가 privileged_names 로 다시 본다)라 실행 신원은 보통
// 기록용이고, 자격이 빠진 드문 경우만 그 신원 권한으로 돈다(runAs -- BatchCreate ⑤ 「목적지 조건과 소유권」의 비 root 폴백 줄과 같은
// 말). 단일 작업은 실행 신원이 실제로 잡의 uid/gid 를 정하고 root 는 자격 있는 관리자의 선택이다(runAsSingle·runAsRoot -- CLAUDE.md
// 「잡의 root 여부는 제출 시점에 확정」: 다른 실행 신원을 적으면 기본은 그 사용자 권한). 단일 작업 화면에 runAs(「배치는 root」)를
// 쓰지 마라 -- 사고 경로를 다시 연다.
export const HELP_SUMMARY = {
  delete: SUMMARY_DELETE,
  contents: SUMMARY_CONTENTS,
  direct: SUMMARY_DIRECT,
  quietSync: SUMMARY_QUIET,
  quietRm: SUMMARY_QUIET,
  maxErrors: SUMMARY_MAX_ERRORS,
  // 요약 — 전문은 단일 작업 「실행 설정」의 정책 캡션 + NODE_CPU_CAP_NOTE(lib/jobTool).
  policy: "노드 수·프로세스 수는 정책이 정합니다.",
  // 요약 — 전문은 배치 생성 「실행 신원」 칸 도움말(BatchCreate, rm·그 밖 두 갈래 -- 「배치는 관리자 이관·정리용이라 root 로
  // 실행됩니다 …」). **배치 전용**. 「늘」을 붙이지 않는다(위 주석 -- ssc 의 배치 자식은 자격이 있을 때만 root).
  runAs: "기록용 이름입니다 — 배치는 root 로 실행됩니다(만든 관리자의 root 자격이 빠진 드문 경우만 이 신원 권한).",
  // 요약 — 전문은 단일 작업 「실행 신원(선택)」 칸 도움말(SubmitJob -- 관리자 전용 칸, root 자격 유무 두 갈래). 다른 실행 신원의
  // 기본 = 그 사용자의 LDAP uid/gid(rootEffective)를 접힌 채로도 말한다. 전문(「비우면 …」으로 시작)과 다른 문장이다.
  runAsSingle: "잡이 파일을 다룰 계정입니다 — 다른 사용자를 적으면 기본은 그 사용자 권한(root 아님)입니다.",
  // 요약 — 전문은 단일 작업 「root 권한으로 실행」 체크 도움말(SubmitJob -- root 자격 있는 관리자에게만 보인다). 전문(「켜면 잡이
  // root 로 …」으로 시작)과 다른 문장이다. 소유 덮어쓰기가 핵심이라 요약에 남긴다(2026-09-30 프로덕션 사고).
  runAsRoot: "root 는 파일 권한 검사를 우회합니다 — sync 는 목적지 소유·권한을 소스에 맞춥니다.",
  // 요약 — 전문은 배치 생성 노드 수·노드당 프로세스 칸 아래 정책 캡션·NODE_CPU_CAP_NOTE·nsync 면당 문장(BatchCreate). 칸 오류는
  // 요약과 무관하게 늘 보인다.
  controlsPolicy: "비운 칸은 정책 기본이 적용됩니다.",
  // 요약 — 전문은 배치 생성 「CSV 붙여넣기」 설명(BatchCreate)이고 이것은 그 첫 문장 그대로다.
  csvApply: "반영하면 표의 행을 통째로 바꿉니다(이어 붙이지 않음).",
} as const;

// 연산 카드의 짧은 부제(단계별 등록 2026-11-26 -- components/form/wizardParts OperationCards). 카드의 접근 이름은 연산 이름
// (sync·scan·rm) 그대로이고(테스트·e2e 접점) 이 부제는 보이는 보조 글자다. 설명 한 줄은 화면의 OPERATION_HELP(단일)·
// OPERATION_HELP/AUTO_HELP(배치)가 맡는다. 배치는 「여러 경로 …」 변형(dms-k8s 목업 v1-top-stepper 그대로). dms-ssc 비관리자는 sync
// 하나뿐이라(연산 카드 없이 ② 에서 시작) 이 부제는 관리자 화면에만 보인다.
type WizardOperation = "sync" | "scan" | "rm";
export const OPERATION_LABEL: Record<WizardOperation, string> = { sync: "복사·동기화", scan: "통계 수집", rm: "삭제" };
export const OPERATION_LABEL_BATCH: Record<WizardOperation, string> = {
  sync: "여러 경로 복사", scan: "여러 경로 통계", rm: "여러 경로 삭제",
};
