// sync 결과(목적지) 소유권 안내(2026-10-01 사용자 요청: "기본적으로 목적지는 요청자 본인 uid:gid 로
// 셋업된다"를 사용자·운영자 안내에). 서버 규칙의 미러 -- execution_manifests._auto_chown:
//   - chown 옵션을 지정했으면 그 값(자동 지정은 꺼진다)
//   - root 실행이면 개입하지 않는다 → 소스의 소유자·그룹이 그대로
//   - 그 외엔 `--chown <실행 신원 uid>:<주 그룹 gid>` → 새로 만든 목적지·복사본 전부 실행 신원 소유
// 실행 신원 = 운영자가 실행 신원(owner_username)을 지정했으면 그 사용자, 아니면 요청자 본인
// (identity.resolve_job_identity). gid 는 LDAP 계정의 **주 그룹**이다(러너가 보조 그룹 없이 물질화) --
// 상위 디렉토리의 setgid 그룹을 물려받지 않는다.
//
// 2026-10-01 검증 워크플로 2회가 포크 소스(mpifileutils 1b93d54)와 테스트베드 실측으로 잡은 사실:
//   - 권한 비트·수정 시각은 소스 그대로다(preserve) -- uid/gid 만 바뀌므로 소스의 **그룹 권한이 이 주 그룹에**
//     적용된다. --chmod 를 주면 권한 비트는 그 값.
//   - dsync/nsync 의 **기본 비교**(UID·GID·PERM·ATIME·MTIME DIFFER, DMS 는 -o 를 넘기지 않는다)가 목적지에
//     이미 있던 같은 경로 항목의 메타데이터를 소스(또는 --chown/--chmod 값)로 다시 맞춘다 -- 최상위만이 아니다.
//     root 실행이면 그 항목들이 전부 소스 소유·권한·시각이 된다(2026-09-30 프로덕션 사고의 범위).
//   - 비 root 에서 본인(uid·주 gid) 외의 소유로 바꾸는 chown 은 EPERM: dsync 는 데이터를 복사한 뒤 작업이
//     실패하고, nsync 는 EPERM 을 무시 가능 오류로 보고 소유 변경을 건너뛴 채 성공한다(새 복사본은 실행 신원 소유).
//   - 목적지에 이미 있던 **남의 소유** 항목은 비 root 가 소유·권한·시각을 못 바꾼다 -- 결과는 도구·항목에 따라
//     갈린다(dsync 는 실패, nsync 는 메타데이터 변경을 건너뛰되 내용이 바뀐 파일은 제자리 쓰기라 쓰기 권한이 없으면
//     실패; dsync 는 내용이 바뀐 파일을 지우고 새로 복사). 그래서 안내는 "실패하거나 일부가 안 바뀔 수 있다" 로만
//     말한다(preflight 는 최상위 소유만 본다).
//   - chown 에서 "10003"(uid 만)·":10000"(gid 만)은 빈 쪽이 소스 값으로 유지된다(dsync.c set_uid/set_gid).
//     "10003:"(끝 콜론)은 도구가 "empty group" 으로 거부하는 형식이라 포탈·서버 정규식이 받지 않는다.
//   - chown 의 **이름**은 잡 컨테이너(LDAP NSS 없음, /etc/passwd 에 실행 신원 한 줄 -- root 실행이면 uid 0)
//     안에서 getpwnam/getgrnam 으로 풀린다 → 대부분 "unknown user/group" 으로 미리보기 실패, root 실행에선
//     실행 신원 이름이 uid 0 으로 풀린다. 그래서 안내는 숫자 uid:gid 를 요구한다(서버 검증은 형식만 본다).
export interface OwnershipInput {
  chown: string;          // 옵션 값(빈 문자열 = 미지정)
  chmod?: string;         // 옵션 값(빈 문자열·생략 = 미지정) -- 권한 비트 문구만 바꾼다
  root: boolean;          // 이 요청이 root 로 실행되는가(포탈이 확정해 싣는 값)
  runAs: string | null;   // 실행 신원 이름(모르면 null)
  self: boolean;          // 실행 신원이 요청자 본인인가
}

/** chown 값에 이름(숫자가 아닌 파트)이 있나 -- 잡 컨테이너에서 해석되지 않는다(위 주석). */
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

export const CHOWN_NAME_WARNING =
  "chown 의 이름은 작업 컨테이너에서 해석되지 않습니다 — 미리보기가 실패하거나 root 실행에서는 uid 0 으로 "
  + "잘못 해석됩니다. 숫자 uid:gid 로 지정하세요.";

// 비 root 에서 남의 소유로 못 바꿀 때의 결과(도구별) -- 같은 노드 sync 는 dsync, 노드 간은 nsync(planner).
// 비 root 에서 남의 소유로 chown 할 때(새 복사본 기준): dsync 는 실패, nsync 는 소유 변경을 건너뛴다.
const NON_ROOT_CHOWN_EPERM =
  "dsync(같은 노드) 는 데이터를 복사한 뒤 작업이 실패하고, nsync(노드 간) 는 소유 변경이 적용되지 않아 실행 신원 소유로 남습니다";

export function syncOwnership({ chown, chmod = "", root, runAs, self }: OwnershipInput):
    { short: string; long: string } {
  const c = chown.trim();
  const m = chmod.trim();
  const modeText = m !== "" ? `권한 비트는 chmod 지정값(${m}), 수정 시각은 소스 그대로입니다.`
    : "권한 비트·수정 시각은 소스 그대로입니다.";
  if (c !== "") {
    if (chownHasName(c))
      return { short: `chown ${c} — 이름은 해석되지 않음(숫자로 지정)`,
               long: `chown 에 이름(${c})을 지정했습니다 — ${CHOWN_NAME_WARNING}` };
    const partial = chownIsPartial(c) ? " uid·gid 중 비워 둔 쪽은 소스 값이 유지됩니다." : "";
    const lead = `chown 옵션으로 지정한 ${c} 소유로 셋업됩니다${root ? "" : "(자동 소유 지정은 꺼집니다)"} — `
      + "목적지에 이미 있던 같은 경로의 항목도 이 소유로 바뀝니다.";
    const nonRoot = root ? "" : ` root 실행이 아니면 실행 신원 본인의 uid·주 그룹 gid 외의 값으로는 바꿀 권한이 없어, ${NON_ROOT_CHOWN_EPERM}.`;
    return {
      short: `chown 지정값 ${c}${root ? "" : " — 비 root: 본인 uid:gid 가 아니면 적용 안 됨(dsync 는 실패)"}`,
      long: `${lead}${partial}${nonRoot} ${modeText}`,
    };
  }
  if (root)
    return { short: "소스의 소유자·그룹 그대로(root 실행)",
             long: "root 실행이라 목적지와 복사된 파일·디렉토리는 소스의 소유자·그룹을 그대로 유지합니다 — "
                 + "목적지에 이미 있던 같은 경로의 항목(최상위 디렉토리 포함)도 소유자·그룹·권한·시각이 소스 것으로 "
                 + `바뀝니다${m !== "" ? `(권한 비트는 chmod 지정값 ${m})` : ""}.` };
  const who = self ? `요청자 본인${runAs ? `(${runAs})` : ""}` : `실행 신원 ${runAs ?? ""}`.trim();
  return { short: `${who}의 uid:gid(주 그룹)`,
           long: `기본적으로 목적지(새로 만드는 경우 포함)와 복사된 파일·디렉토리는 ${who}의 uid:gid`
               + "(LDAP 계정의 주 그룹)로 셋업됩니다 — 소스 소유자와 관계없습니다. "
               + (m !== "" ? modeText : "권한 비트·수정 시각은 소스 그대로라 소스의 그룹 권한이 이 주 그룹에 적용됩니다.")
               + " 목적지에 이미 있던 같은 경로의 항목도 이 소유로 다시 맞춰지므로, 그 안에 다른 사용자 소유 항목이 "
               + "있으면 작업이 실패하거나 일부 항목의 소유·권한이 바뀌지 않을 수 있습니다." };
}
