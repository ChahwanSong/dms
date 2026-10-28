import { CHOWN_NAME_ERROR } from "../../lib/syncOwnership";
import { REASON_MESSAGES } from "../../lib/api";
// 서버 검증의 클라이언트 미러(즉답용) — 최종 심판은 서버 422 invalid_option 이다.
// SubmitJob(단건 sync)과 BatchCreate(배치 옵션 스텝)가 공유한다(슬라이스 32 T8) —
// 파일별 사본이면 미러가 발산한다(슬라이스 31 T3 formFields 이사와 같은 이유).
// domain.py 의 _CHMOD_ITEM_RE(콤마 항목별 fullmatch)·_CHOWN_RE 의 미러.
// chown 은 **숫자 uid/gid 만**(2026-10-01 사용자 결정 -- 이름은 잡 컨테이너에서 디렉터리로 풀리지 않아 미리보기
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

// direct + bufsize 교차 검증(d192, 2026-10-28 dms-ssc 에서 이식 -- 포탈 괴리 [8]) -- 서버 domain.validate_options 의 미러(422
// direct_bufsize_misaligned). dsync 는 O_DIRECT 일 때 파일을 4 MiB 조각(MFU_CHUNK_SIZE, DMS 는 --chunksize 를 넘기지
// 않는다)으로 나눠 읽는데, 조각 길이가 버퍼 크기의 배수가 아니면 파일 끝이 아닌 조각에서 MFU_ABORT(MPI 중단)한다
// (mfu_flist_copy.c). 4 MiB 의 약수이면서 4 KiB(블록) 정렬인 값 = 4096·2^k ≤ 4194304 뿐이다. 미리보기는 복사를 안 해
// 통과하고 실행에서 큰 파일이 여러 프로세스에 나뉠 때 중단되므로, 보내기 전에 막는다.
// 빈 값(생략)은 서버 기본 4 MiB 라 정렬돼 있고, 범위 밖 정수·정수 아님은 syncIntFieldError 가 먼저 말한다(같은 칸에 두 문장을
// 겹쳐 그리지 않는다). 문구는 서버 422 와 **같은 문장**(REASON_MESSAGES) -- 화면 즉답과 서버 거부가 다르게 말하지 않게.
// dms-k8s 의 배치 CSV 행(BatchCreate CSV 가져오기)도 서버에서 같은 domain.validate_options 를 지난다 -- 화면 즉답은 폼 칸만.
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
// d192(포탈 괴리 [3]·[24]·[25]·[28] -- dms-ssc 판을 옮기고 「실행 신원이 읽을 수 없는」만 root 전용 어휘로 바꿨다):
//   - delete: 「완전히 동일하게 맞춥니다」는 더는 참이 아니다 -- 소스의 FIFO·소켓·장치는 두 도구 모두 복사하지 않지만(nsync·
//     dsync "Skipped N special file(s)") 그 이름의 목적지 항목을 다루는 방식이 **도구마다 다르다**: nsync 는 비교 단계에서 특수
//     파일을 빼 목적지 항목을 delete 와 무관하게 그대로 둔다. dsync 는 비교에서 종류가 다르면 delete 검사 없이 목적지 항목을
//     지울 목록에 넣고 복사 단계는 특수 파일을 만들지 않는다 -- 그 이름이 목적지에서 사라진다(rc 0, 포크 빌드 실측). 비어 있지
//     않은 디렉토리면 delete 없이는 "Type change not applied" 로 실패하고, delete 를 켜면 하위 트리째 지워진다. dsync 미리보기
//     (dry-run)는 지울 목록을 채우지 않아 이 경우를 미리 말할 수 없다 -- 그래서 도움말이 미리 말한다.
//     소스를 다 읽지 못한 실행은 delete 를 그 실행에서 끈다(nsync "--delete is off for this run", dsync "delete option
//     disabled") -- 꺼지는 것은 「소스에 없는 항목 지우기」뿐이고 타입 교체의 목적지 삭제는 그대로다. 반대로 delete 가 꺼져
//     있으면 소스에서 파일·링크로 바뀐 디렉토리를 목적지에서 비우지 못해 그 항목이 실패한다(sync_type_change_refused).
//   - contents: nsync 는 바이트 비교가 아니라 스캔 중 양쪽 모든 일반 파일의 SHA-256 을 계산한다(미리보기 포함) -- 읽을 수 없는
//     파일 하나가 미리보기를 실패시킨다. dsync 는 양쪽에 있고 크기가 같은 파일만 바이트 비교한다. dms-k8s 는 모든 잡이 root 라
//     「읽을 수 없는」 파일은 권한이 아니라 스토리지 쪽(NFS root_squash 등)에서 생긴다.
export const SYNC_OPTION_HELP = {
  delete: "원본에 없는 항목을 대상에서 지웁니다(미러 동기화). 원본에서 파일·링크로 바뀐 디렉토리가 대상에 비어 있지 않게 남아 "
    + "있으면 이 옵션을 켜야 교체되고, 끄면 그 항목은 실패로 남습니다. 원본의 FIFO·소켓·장치 파일은 복사하지 않습니다 — 노드 간 "
    + "실행(nsync)은 대상의 같은 이름 항목을 그대로 두고, 한 노드 실행(dsync)은 그 이름의 대상 항목이 다른 종류면 이 옵션과 "
    + "무관하게 지운 뒤 아무것도 만들지 않아 그 이름이 대상에서 사라집니다(비어 있지 않은 디렉토리는 이 옵션을 켜야 지워지고, "
    + "끄면 실패로 남습니다). 원본 일부를 읽지 못한 실행은 안전을 위해 그 실행에서 원본에 없는 항목을 지우지 않습니다.",
  contents: "크기·수정시각 대신 파일 내용으로 비교합니다(더 느리지만 정확). 미리보기 단계부터 파일을 읽습니다 — 한 노드 실행"
    + "(dsync)은 양쪽에 있고 크기가 같은 파일을 바이트 단위로, 노드 간 실행(nsync)은 양쪽 모든 파일의 SHA-256 을 비교하므로 "
    + "읽을 수 없는 그런 파일(예: NFS root_squash)이 하나라도 있으면 미리보기가 실패합니다.",
} as const;
// open_noatime·direct 안내(SubmitJob·BatchCreate·배치 실행 설정 공용, dms-ssc d190·d192 이식). dms-k8s 는 모든 작업이 root 라
// O_NOATIME 의 EPERM(실행 신원 소유가 아닌 파일) 제약이 없어 단건·배치 모두 기본 켬이다(사용자 결정 2026-08-22 -- 소스 atime 오염
// 방지). nsync 의 --open-noatime 은 포크 db1e33c(잡 이미지 7a3189c)부터 실제로 동작한다 -- 그 전 ae781bd 의 nsync 는 빌드에
// _GNU_SOURCE 가 없어 O_NOATIME 이 0 이라, 공존 노드가 없어 nsync 로 돈 sync 는 소스 atime 을 갱신했다.
// 차이가 나는 스토리지(d192, 포탈 괴리 [C3]): 읽기가 atime 을 갱신하는 곳에서만 차이가 난다. 리눅스 기본 relatime 은 읽을 때
// 「이전 atime 이 mtime·ctime 이하이거나 24시간보다 오래됐으면」 갱신한다 -- 오래 안 읽힌(cold) 파일은 sync 한 번에 최근 접근이
// 되므로 relatime 을 「차이 없음」 쪽에 넣지 않는다. noatime 마운트·테스트베드 CephFS(relatime 마운트인데도 읽기가 atime 을 바꾸지
// 않았다 -- dms-ssc CHANGELOG d190)에선 켜고 끄는 차이가 보이지 않는다.
// direct 는 dsync 만 O_DIRECT 로 열고 nsync 는 받기만 한다(정렬 I/O 미구현, 실행 로그에 「--direct has no effect in nsync」).
// bufsize 조건은 directBufsizeError.
export const OPEN_NOATIME_HELP =
  "기본 켬 — 원본을 읽을 때 접근 시각(atime)을 바꾸지 않아 데이터 온도(hot/cold) 통계를 정직하게 유지합니다. 모든 작업이 root 로 "
  + "실행돼 모든 파일에 적용됩니다. 읽기가 atime 을 갱신하는 스토리지에서만 차이가 납니다 — 리눅스 기본 relatime 도 마지막 접근이 "
  + "하루를 넘었거나 수정 뒤 처음 읽는 파일은 갱신하므로(끄면 sync 가 읽은 cold 파일이 hot 이 됩니다) 차이가 나고, noatime 마운트나 "
  + "일부 CephFS 처럼 읽기가 atime 을 바꾸지 않는 곳에선 차이가 없습니다.";
export const DIRECT_HELP =
  "O_DIRECT(페이지 캐시 우회)로 읽고 씁니다 — 두 스토리지를 함께 마운트한 노드에서 dsync 로 실행될 때만 적용되고, 공존 노드가 없어 "
  + "nsync 로 실행되면 무시됩니다. 켜면 bufsize 는 4 KiB~4 MiB 의 2의 거듭제곱(4096·8192·…·4194304)이어야 합니다(dsync 는 4 MiB "
  + "단위로 나눠 읽어 그 약수가 아니면 큰 파일에서 중단됩니다).";
// bufsize 칸 설명(단건·배치 생성 공용). 프리필 계약(2026-09-17)에 direct 조건을 덧붙인다.
export const BUFSIZE_HELP =
  "미리 채운 4194304 = 4 MiB(서버 기본). 비워도 같은 값이 적용됩니다. direct 를 켜면 4096·8192·…·4194304(4 KiB~4 MiB 의 "
  + "2의 거듭제곱)만 쓸 수 있습니다.";
// quiet 안내(d192, 포탈 괴리 [9]·[13]·[18]·[29]): -q 는 도구 로그 수준을 「없음」으로 둔다(nsync·dsync·drm 모두 MFU_LOG_NONE)
// -- 줄이는 게 아니라 오류 줄·실패 항목 목록·요약까지 전부 사라진다. 그래서 결과 개수는 「—」이고, 러너가 실패 원인
// (DMS_EXEC_REASON)을 가를 재료도 없어 실패하면 일반 사유(execution_failed)로만 남는다. 미리보기는 대상 수를 세야 해서 quiet 를
// 빼고 돈다(execution_manifests). 단건·배치 생성·배치 실행 설정이 같은 문구를 쓴다.
export const QUIET_HELP = {
  sync: "도구 출력을 모두 끕니다 — 실행 결과의 개수(반영·삭제한 항목)가 「—」가 되고, 실패한 항목 목록과 오류 줄도 남지 않아 "
    + "실패하면 원인이 일반 사유(「실행에 실패했습니다」)로만 보입니다. 미리보기(dry-run)에는 적용하지 않습니다(대상 수를 세는 "
    + "줄이 사라지므로).",
  rm: "도구 출력을 모두 끕니다 — 실행 결과의 개수(삭제한 항목)가 「—」가 되고, 지우지 못한 항목 목록과 오류 줄도 남지 않아 "
    + "실패하면 원인이 일반 사유(「실행에 실패했습니다」)로만 보입니다. 미리보기(dry-run)에는 적용하지 않습니다(지울 대상 수를 "
    + "세는 줄이 사라지므로).",
} as const;
// nsync 실패 한도 안내(d192, 포탈 괴리 [21]): 포크 nsync 는 --max-errors 기본 65,000(NSYNC_DEFAULT_MAX_ERRORS)에 닿으면 배치
// 사이에서 멈추고(배치 안에서는 목적지 rank 마다 남은 몫에서) 남은 액션을 시도하지 않는다 -- DMS 는 이 값을 넘기지도 바꾸게
// 하지도 않는다(domain._OPTION_SPECS 에 없음). 멈춘 실행은 sync_stopped_max_errors 로 남는다. sync 옵션 구획 캡션(단건·배치 생성)이
// 말한다. 문구는 그 사유 메시지와 같은 어휘.
export const SYNC_MAX_ERRORS_NOTE =
  "노드 간 실행(nsync)은 실패한 항목이 허용 한도(기본 65,000건)에 닿으면 남은 항목을 시도하지 않고 멈춥니다 — 원인을 고친 뒤 "
  + "다시 실행하면 이미 맞춘 항목은 건너뛰고 이어서 맞춥니다.";
// 실행 권한 문구(제출 요약 「실행 권한」 ↔ 요청 상세 「요청 내용」의 「실행 권한」). root 의 어휘·색(text-bad)은 포탈 전체가 같다.
// 요청 상세는 PRIV_ROOT_HEAD·PRIV_USER(옛 비 root 행 표시) 만 쓴다 -- sync 의 소유 결과는 같은 카드의 「목적지 소유」 행이 말하므로
// PRIV_ROOT_SYNC(소유 결과를 덧붙인 꼬리)는 제출 요약 전용이다(한 화면에서 같은 사실을 두 번 말하지 않는다).
export const PRIV_ROOT_HEAD = "root(특권) — 권한 검사 우회";
export const PRIV_ROOT_SYNC = `${PRIV_ROOT_HEAD}, sync 는 목적지 소유·권한을 소스에 맞춤(chown·chmod 지정 시 그 값)`;
// PV 목적지 root sync(2026-10-26 사용자 결정): 결과 소유는 목적지 스토리지 기본 소유자(사용자는 늘 이것), 운영자가 옵션으로
// 「PV 루트 소유자」를 고르면 잰 PV 루트 소유자. 권한 비트는 둘 다 소스(서버 _auto_chown).
export const PRIV_ROOT_SYNC_PV = `${PRIV_ROOT_HEAD}, sync 결과 소유는 목적지 스토리지 기본 소유자로·권한은 소스대로(chown·chmod 지정 시 그 값)`;
export const PRIV_ROOT_SYNC_PV_ROOT = `${PRIV_ROOT_HEAD}, sync 결과 소유는 목적지 PV 루트 소유자로(운영자 지정)·권한은 소스대로(chown·chmod 지정 시 그 값)`;
// 직접 경로 목적지가 PV·파일시스템 겸용(both) 스토리지(2026-10-26 사용자 결정 (4)): 경로가 그 스토리지의 PV 루트 아래면 스토리지 기본
// 소유자, 아니면 소스 소유 그대로(서버 k8s_access.direct_dest_owner). pv 종류 스토리지의 직접 경로는 PRIV_ROOT_SYNC_PV 와 같은 말이다.
export const PRIV_ROOT_SYNC_DIRECT_BOTH = `${PRIV_ROOT_HEAD}, sync 결과 소유는 목적지가 PV 루트 아래면 목적지 스토리지 기본 소유자로, 아니면 소스에 맞춤·권한은 소스대로(chown·chmod 지정 시 그 값)`;
export const PRIV_USER = "실행 신원의 uid/gid(권한 그대로 적용)";
