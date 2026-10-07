import type { WorkerPoolIdentity } from "./types";

// 보조 그룹 인정의 화면 문구(2026-10-07 D15). 포탈은 "보조 그룹이 인정된다" 를 무조건으로 말하지 않는다:
//   - 무엇이 실렸나는 계획 시점에 확정된다(planner 가 worker_pool.identity 에 얼린 4키 -- gidNumber 가 있는
//     posixGroup 만, 중첩 그룹 제외). 잡 상세 '보조 그룹(gid)' 행이 그 스냅숏을 그대로 읽는다.
//   - 실려도 실제 인정은 **스토리지 서버**가 정한다 -- NFS(AUTH_SYS)는 그룹을 16개까지만 싣고, 서버가 그룹을
//     자체 조회(manage-gids 류)하면 클라이언트가 보낸 목록을 버린다. Lustre 도 MDS 의 identity upcall 설정에 따라
//     서버가 그룹을 다시 판정한다. 그래서 등록된 backend_type 별 주의문을 함께 보인다.
// backend_type 은 **등록 라벨**일 뿐이다(런타임은 mount_path 만 본다 -- CLAUDE.md) -- 문구가 "등록 종류 기준" 이라고
// 스스로 말하는 이유다. 라벨이 실제와 다르거나 모르는 값이면 일반 주의문으로 떨어진다(거짓 단정 금지).

const NFS_CAVEAT =
  "NFS 스토리지(등록 종류 기준): 보조 그룹은 최대 16개까지만 전달되거나, 서버가 그룹을 자체 조회하면 인정되지 않을 수 있습니다.";
const LUSTRE_CAVEAT =
  "Lustre(등록 종류 기준): 서버(MDS) 설정에 따라 서버가 그룹을 다시 판정하므로 보조 그룹이 인정되지 않을 수 있습니다.";
const GENERIC_CAVEAT =
  "실제 인정 여부는 스토리지 설정에 따라 다를 수 있습니다(등록된 스토리지 종류 기준 안내).";

// identity.MAX_SUPPLEMENTARY_GROUPS 미러(D4: 넘으면 통째로 미적용). 서버 상수가 하드라(설정 키 아님) 사본이 발산할
// 경로는 그 상수를 고치는 커밋뿐이다.
export const MAX_SUPPLEMENTARY_GROUPS = 256;

/** 스토리지 종류(backend_type 등록 라벨)별 보조 그룹 주의문 한 줄. 모르는 라벨·null 은 일반 주의문. */
export function groupCaveat(backendType: string | null | undefined): string {
  switch (backendType) {
    case "purestorage":
    case "netapp":
      return NFS_CAVEAT;
    case "lustre":
      return LUSTRE_CAVEAT;
    default:
      return GENERIC_CAVEAT;
  }
}

/** 스토리지 이름들(sync 면 소스·목적지, 아니면 대상)의 주의문을 **문구 기준 중복 없이** 순서대로.
    빈 이름(아직 안 고름·잡 컬럼 null)은 건너뛴다. 이름 → backend_type 맵에 없는 이름(비관리자 응답엔 관리자 전용
    스토리지가 없다 -- routes_storages.list_user_storages, 조회 실패·로딩 중)은 일반 주의문이다. */
export function groupCaveatsFor(names: (string | null | undefined)[],
                                backends: Record<string, string>): string[] {
  const out: string[] = [];
  for (const n of names) {
    if (typeof n !== "string" || n === "") continue;
    const line = groupCaveat(Object.prototype.hasOwnProperty.call(backends, n) ? backends[n] : undefined);
    if (!out.includes(line)) out.push(line);
  }
  return out;
}

const gidList = (v: unknown): number[] =>
  Array.isArray(v) ? v.filter((g): g is number => typeof g === "number" && Number.isInteger(g)) : [];

/** 잡 상세 '보조 그룹(gid)' 행의 값. null = 행을 그리지 않는다:
    상태 키가 없는 잡(기능 배포 전에 계획됨 -- 모름이지 "없음" 이 아니다)·identity 없음·모르는 상태 값,
    그리고 applied 인데 목록이 비어 있는 모순 모양(stepper 의 identity_problem 이 실행 전에 끊는다 -- 화면이
    거짓 목록을 지어내지 않는다). */
export function supplementaryGroupsText(ident: WorkerPoolIdentity | null | undefined): string | null {
  if (ident == null) return null;
  const status = ident.supplementary_gids_status;
  if (status == null) return null;
  let head: string;
  switch (status) {
    case "applied": {
      const gids = gidList(ident.supplementary_gids);
      if (gids.length === 0) return null;
      head = gids.join(", ");
      break;
    }
    case "none":
      head = "없음";
      break;
    case "over_limit": {
      // found 는 0 도 정상값이다(is None 비교 규약) -- 여기선 over_limit 이라 0 일 수 없지만 모름(null)은 숫자 없이.
      const found = ident.supplementary_gids_found;
      head = typeof found === "number"
        ? `적용 안 됨 — 그룹 ${found}개가 상한 ${MAX_SUPPLEMENTARY_GROUPS}개를 넘음`
        : `적용 안 됨 — 그룹 수가 상한 ${MAX_SUPPLEMENTARY_GROUPS}개를 넘음`;
      break;
    }
    case "disabled":
      head = "적용 안 됨 — 운영자가 기능을 꺼 둠(계획 시점)";
      break;
    case "privileged":
      head = "root 실행 — 해당 없음";
      break;
    default:
      return null;
  }
  const excluded = gidList(ident.supplementary_gids_excluded);
  return excluded.length > 0 ? `${head} (제외: ${excluded.join(", ")} — 유효하지 않은 gid)` : head;
}
