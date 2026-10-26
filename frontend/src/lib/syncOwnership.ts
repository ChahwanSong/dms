// sync 결과(목적지) 소유권 안내(2026-10-01 사용자 요청: "기본적으로 목적지는 요청자 본인 uid:gid 로
// 셋업된다"를 사용자·운영자 안내에). 서버 규칙의 미러 -- execution_manifests._auto_chown:
//   - chown 옵션을 지정했으면 그 값(자동 지정은 꺼진다)
//   - root 실행 + 목적지가 PV 주소면 그 요청의 결과 소유 규칙(payload.destination_pv.owner_rule, 2026-10-26 사용자 결정
//     「각 k8s 스토리지 항목마다 기본 uid,gid … 사용자는 항상 자동, 운영자는 옵션으로 PV 루트 소유자」):
//       storage  **목적지 스토리지의 기본 소유자**(스토리지 등록 화면의 기본 소유 uid:gid, 기본 0:0 = root:root) -- 서버가
//                제출 때 그 값을 destination_pv.owner 에 박고, 계획·컨펌 때 지금 값과 다르면 storage_owner_changed 로 멈춘다
//       pv_root  **목적지 PV 루트의 소유자·그룹**(특권 운영자 옵션 -- 점검(preflight)이 재서 잡 worker_pool.dest_pv_owner 에
//                남긴 값; 미리보기 뒤 바뀌면 실행을 멈춘다)
//       없음     이 규칙 전에 제출된 옛 행 -- 서버가 pv_root 와 같은 잰 값으로 돌린다(부재를 거부로 강화하지 않는다)
//   - root 실행이면 개입하지 않는다 → 소스의 소유자·그룹이 그대로
//   - 그 외(비 root)엔 `--chown <실행 신원 uid>:<주 그룹 gid>` → 새로 만든 목적지·복사본 전부 실행 신원 소유
// 2026-10-26 LDAP 정리 뒤로 새 작업은 전부 root 다(k8s 신원 모드) -- 비 root 갈래는 옛 행(계획 스냅숏이 비 root)의 표시용으로만
// 남는다. 실행 신원 = 실행 신원(owner_username)을 지정했으면 그 사용자, 아니면 요청자 본인. gid 는 실행 신원의 **주 그룹**이다
// (상위 디렉토리의 setgid 그룹을 물려받지 않는다).
// chown 칸은 포탈에서 관리자 전용 고급 옵션이다(SubmitJob) -- 비관리자에게 "chown 에 지정하세요" 라고 하면 할 수 없는
// 지시가 된다(2026-10-08 리뷰). chownEditable=false 면 관리자에게 요청하라는 문장으로 바꾼다.
//
// 2026-10-01 검증 워크플로 2회가 포크 소스(mpifileutils 1b93d54)와 테스트베드 실측으로 잡은 사실:
//   - 권한 비트·수정 시각은 소스 그대로다(preserve) -- uid/gid 만 바뀌므로 소스의 **그룹 권한이 이 주 그룹에**
//     적용된다. --chmod 를 주면 권한 비트는 그 값.
//   - dsync/nsync 의 **기본 비교**(UID·GID·PERM·ATIME·MTIME DIFFER, DMS 는 -o 를 넘기지 않는다)가 목적지에
//     이미 있던 같은 경로 항목의 메타데이터를 소스(또는 --chown/--chmod 값)로 다시 맞춘다 -- 최상위만이 아니다.
//     root 실행이면 그 항목들이 전부 소스 소유·권한·시각이 된다(2026-09-30 프로덕션 사고의 범위).
//   - 비 root 에서 uid 를 본인 외로 바꾸는 chown 은 EPERM: dsync 는 데이터를 복사한 뒤 작업이 실패하고, nsync 는
//     EPERM 을 무시 가능 오류로 보고 소유 변경을 건너뛴 채 성공한다(새 복사본은 실행 신원 소유). gid 는 프로세스가
//     속한 그룹(주 그룹)이면 커널이 허용하고, 그 밖의 gid 는 같은 EPERM 이다.
//   - 목적지에 이미 있던 **남의 소유** 항목은 비 root 가 소유·권한·시각을 못 바꾼다 -- 결과는 도구·항목에 따라
//     갈린다(dsync 는 실패, nsync 는 메타데이터 변경을 건너뛰되 내용이 바뀐 파일은 제자리 쓰기라 쓰기 권한이 없으면
//     실패; dsync 는 내용이 바뀐 파일을 지우고 새로 복사). 그래서 안내는 "실패하거나 일부가 안 바뀔 수 있다" 로만
//     말한다(preflight 는 최상위 소유만 본다).
//   - chown 에서 "10003"(uid 만)·":10000"(gid 만)은 빈 쪽이 소스 값으로 유지된다(dsync.c set_uid/set_gid).
//     "10003:"(끝 콜론)은 도구가 "empty group" 으로 거부하는 형식이라 포탈·서버 정규식이 받지 않는다.
//   - chown 의 **이름**은 잡 컨테이너(디렉터리 NSS 없음, /etc/passwd 에 실행 신원 한 줄 -- root 실행이면 uid 0)
//     안에서 getpwnam/getgrnam 으로 풀린다 → 사이트 계정 이름은 "unknown user/group" 으로 미리보기 실패, users 같은
//     이름은 데비안 기본 gid, root 실행에선 실행 신원 이름이 uid 0 으로 풀린다. 그래서 2026-10-01 부터 서버가
//     숫자 uid:gid 만 받는다(chown_name_not_supported -- domain.chown_problem). 폼은 이름을 오류로 막는다.
export interface OwnershipInput {
  chown: string;          // 옵션 값(빈 문자열 = 미지정)
  chmod?: string;         // 옵션 값(빈 문자열·생략 = 미지정) -- 권한 비트 문구만 바꾼다
  root: boolean;          // 이 요청이 root 로 실행되는가(포탈이 확정해 싣는 값)
  runAs: string | null;   // 실행 신원 이름(모르면 null)
  self: boolean;          // 실행 신원이 요청자 본인인가
  chownEditable?: boolean; // 이 화면에서 chown 을 직접 지정할 수 있나(기본 true -- 관리자 전용 화면들). 사용자 신청 화면은 false
  pvDestination?: boolean; // 목적지가 PV 주소인가(k8s 모드) -- root 면 아래 규칙으로 결과 소유를 맞춘다
  // PV 목적지의 결과 소유 규칙(위 주석). null·생략 = 옛 행(규칙 이전 -- 잰 PV 루트 소유자, 문구도 그때 그대로).
  pvOwnerRule?: PvOwnerRule | null;
  storageOwner?: string | null; // 목적지 스토리지 기본 소유자 "uid:gid"(storage 규칙 -- 모르면 null: PV 를 아직 안 고름)
  pvOwner?: string | null; // 점검이 잰 목적지 PV 루트 소유자 "uid:gid"(pv_root·옛 행 -- 모르면 null: 아직 점검 전)
}

/** PV 목적지 sync 의 결과 소유 규칙(서버 payload.destination_pv.owner_rule 의 값 집합). */
export type PvOwnerRule = "storage" | "pv_root";

// uid·gid 상한(서버 execution_manifests valid_owner·스토리지 기본 소유 검증과 같은 범위 -- 4294967295 = (uid_t)-1 은
// 「바꾸지 않음」 표식이라 값이 아니다).
export const OWNER_ID_MAX = 4294967294;

/** "uid:gid" 꼴(숫자 둘, 각 0..OWNER_ID_MAX)만 값으로 받는다 -- 그 밖(문자열 아님·이름·범위 밖)은 null(모름). DB·응답은 신뢰
    경계 밖이라 모양이 틀린 값을 그럴듯하게 보이지 않는다(0 은 정상값 -- root:root). */
export function validOwner(v: unknown): string | null {
  if (typeof v !== "string" || !/^[0-9]{1,10}:[0-9]{1,10}$/.test(v)) return null;
  return v.split(":").every((p) => Number(p) <= OWNER_ID_MAX) ? v : null;
}

/** 소유 표기: "uid:gid" 그대로, root:root(0:0)만 「0:0(root)」 -- 0 은 「없음」이 아니라 root 라는 실제 값이다. */
export function ownerText(owner: string): string {
  return owner === "0:0" ? "0:0(root)" : owner;
}

/** chown 값에 이름(숫자가 아닌 파트)이 있나 -- 잡 컨테이너에서 해석되지 않아 서버가 거부한다(위 주석,
    chown_name_not_supported). 새 입력은 폼이 막고, 이 판정은 그 문구와 이름이 든 기존 배치의 안내에 쓴다. */
export function chownHasName(chown: string): boolean {
  return chown.trim().split(":").some((p) => p !== "" && !/^[0-9]+$/.test(p));
}

/** uid·gid 중 한쪽만 지정했나("10003" uid 만, ":10000" gid 만) -- 빈 쪽은 소스 값이 유지된다.
    "10003:"(끝 콜론)은 형식 오류(도구가 거부, 정규식도 거부)라 여기서 '한쪽만' 으로 치지 않는다. */
export function chownIsPartial(chown: string): boolean {
  const c = chown.trim();
  if (c === "") return false;
  if (!c.includes(":")) return true;
  const [u, g] = c.split(":", 2);
  return u === "" && g !== "";
}

// 이름은 잡 컨테이너(디렉터리 NSS 없음)에서 제대로 풀리지 않는다 -- 못 찾으면 미리보기 실패, users 같은 이름은
// 엉뚱한 기본 gid, root 실행이면 실행 신원 이름이 uid 0 으로 풀린다. 그래서 서버가 거부한다(2026-10-01).
export const CHOWN_NAME_ERROR =
  "chown 은 숫자 uid:gid 만 지정할 수 있습니다(예: 10003:10000) — 이름은 작업 컨테이너에서 해석되지 않습니다.";

// 비 root 에서 남의 소유로 못 바꿀 때의 결과(도구별) -- 같은 노드 sync 는 dsync, 노드 간은 nsync(planner).
// 비 root 에서 남의 소유로 chown 할 때(새 복사본 기준): dsync 는 실패, nsync 는 소유 변경을 건너뛴다.
const NON_ROOT_CHOWN_EPERM =
  "dsync(같은 노드) 는 데이터를 복사한 뒤 작업이 실패하고, nsync(노드 간) 는 소유 변경이 적용되지 않아 실행 신원 소유로 남습니다";

export function syncOwnership({ chown, chmod = "", root, runAs, self, chownEditable = true, pvDestination = false,
                                pvOwnerRule = null, storageOwner = null, pvOwner = null }: OwnershipInput):
                                { short: string; long: string } {
  const c = chown.trim();
  const m = chmod.trim();
  const modeText = m !== "" ? `권한 비트는 chmod 지정값(${m}), 수정 시각은 소스 그대로입니다.`
    : "권한 비트·수정 시각은 소스 그대로입니다.";
  if (c !== "") {
    if (chownHasName(c))   // 입력 중인 폼(서버가 거부할 값)·규칙 전에 만든 배치의 표시 -- 배치 문맥 안내는 BatchDetail 이
      return { short: `chown ${c} — 이름은 지원하지 않음(숫자 uid:gid 로 지정)`,
               long: `chown 에 이름(${c})을 지정했습니다 — ${CHOWN_NAME_ERROR}` };
    const partial = chownIsPartial(c) ? " uid·gid 중 비워 둔 쪽은 소스 값이 유지됩니다." : "";
    const lead = `chown 옵션으로 지정한 ${c} 소유로 셋업됩니다${root ? "" : "(자동 소유 지정은 꺼집니다)"} — `
      + "목적지에 이미 있던 같은 경로의 항목도 이 소유로 바뀝니다.";
    const nonRoot = root ? "" : ` root 실행이 아니면 uid 는 실행 신원 본인, gid 는 실행 신원의 주 그룹이어야 하고, 아니면 바꿀 권한이 없어 ${NON_ROOT_CHOWN_EPERM}.`;
    return {
      short: `chown 지정값 ${c}${root ? "" : " — 비 root: uid 는 본인, gid 는 주 그룹만(아니면 실패)"}`,
      long: `${lead}${partial}${nonRoot} ${modeText}`,
    };
  }
  // 이미 있던 같은 경로 항목·setuid 비트(PV 목적지 두 규칙 공통 -- 서버가 둘 다 --chown 을 넘기고, 포크는 --chown 이 있으면
  // 일반 파일의 setuid·setgid 를 지운다. 0:0 이어도 --chown 0:0 이 넘어가므로 같다).
  const pvTail = "목적지에 이미 있던 같은 경로의 항목도 이 소유로 다시 맞춰지고, 일반 파일의 setuid·setgid 비트는 지워집니다"
    + `(소유자를 바꾸는 chown 과 같은 규칙). ${modeText}`;
  if (root && pvDestination && pvOwnerRule === "storage") {
    const owner = validOwner(storageOwner);
    const v = owner !== null ? ownerText(owner) : null;
    return { short: v !== null ? `목적지 스토리지 기본 소유자 ${v}` : "목적지 스토리지 기본 소유자(uid:gid)",
             long: (v !== null ? `결과는 목적지 스토리지 기본 소유자 ${v} 로 맞춰집니다` : "결과는 목적지 스토리지의 기본 소유자(uid:gid)로 맞춰집니다")
                 + "(목적지와 복사된 파일·디렉토리 전부) — 소스 소유자와 관계없이 관리자가 그 스토리지에 등록한 기본 소유자입니다"
                 + "(등록하지 않았으면 0:0 = root). "
                 + (v !== null ? "" : "실제 값은 목적지 PV 를 고르면 보입니다. ")
                 + `컨펌 전에 관리자가 그 값을 바꾸면 실행하지 않고 멈춥니다(이미 컨펌한 작업은 확인 창에 보인 값으로 실행됩니다). ${pvTail}` };
  }
  if (root && pvDestination) {
    // pv_root(운영자 옵션) 와 옛 행(규칙 없음)은 같은 동작(잰 PV 루트 소유자) -- 운영자 옵션이면 그 사실만 덧붙인다.
    const owner = validOwner(pvOwner);
    const chosen = pvOwnerRule === "pv_root";
    return { short: owner !== null ? `목적지 PV 소유자 ${owner}(PV 루트 기준${chosen ? " — 운영자 지정" : ""})`
                      : `목적지 PV 루트의 소유자·그룹(점검 뒤 확정${chosen ? " — 운영자 지정" : ""})`,
             long: (chosen ? "결과 소유를 PV 루트 소유자로 골랐습니다(특권 운영자 옵션 — 기본은 목적지 스토리지 기본 소유자). " : "")
                 + "결과(목적지와 복사된 파일·디렉토리)는 목적지 PV 루트의 소유자·그룹(uid:gid)으로 맞춰집니다 — 소스 소유자와 "
                 + "관계없이 그 PV 의 소유자 기준입니다(PV 루트가 root 소유면 결과도 root 소유). "
                 + (owner !== null ? `점검에서 확인한 값은 ${owner} 입니다. ` : "실제 값은 점검(preflight)에서 확인돼 확인 창에 보입니다. ")
                 + `미리보기 뒤 그 소유자가 바뀌면 실행하지 않고 멈춥니다. ${pvTail}` };
  }
  if (root)
    return { short: "소스의 소유자·그룹 그대로(root 실행)",
             long: "root 실행이라 목적지와 복사된 파일·디렉토리는 소스의 소유자·그룹을 그대로 유지합니다 — "
                 + "목적지에 이미 있던 같은 경로의 항목(최상위 디렉토리 포함)도 소유자·그룹·권한·시각이 소스 것으로 "
                 + `바뀝니다${m !== "" ? `(권한 비트는 chmod 지정값 ${m})` : ""}.` };
  const who = self ? `요청자 본인${runAs ? `(${runAs})` : ""}` : `실행 신원 ${runAs ?? ""}`.trim();
  return { short: `${who}의 uid:gid(주 그룹)`,
           long: `기본적으로 목적지(새로 만드는 경우 포함)와 복사된 파일·디렉토리는 ${who}의 uid:gid`
               + "(주 그룹)로 셋업됩니다 — 소스 소유자와 관계없습니다. "
               + (m !== "" ? modeText : "권한 비트·수정 시각은 소스 그대로라 소스의 그룹 권한이 이 주 그룹에 적용됩니다.")
               + " 목적지에 이미 있던 같은 경로의 항목도 이 소유로 다시 맞춰지므로, 그 안에 다른 사용자 소유 항목이 "
               + "있으면 작업이 실패하거나 일부 항목의 소유·권한이 바뀌지 않을 수 있습니다."
               + (chownEditable
                 ? " 다른 소유로 남기려면 chown 에 숫자 uid:gid 를 지정하세요(자동 지정은 주 그룹)."
                 : " 자동 지정은 주 그룹입니다 — 다른 소유가 필요하면 관리자에게 chown 지정을 요청하세요.") };
}
