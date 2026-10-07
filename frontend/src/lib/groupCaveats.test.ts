import { expect, test } from "vitest";
import { groupCaveat, groupCaveatsFor, supplementaryGroupsText } from "./groupCaveats";

// 보조 그룹 화면 문구(2026-10-07 D15): 스토리지 종류별 주의문 + 잡 상세 '보조 그룹(gid)' 행 값.

const NFS = "NFS 스토리지(등록 종류 기준): 보조 그룹은 최대 16개까지만 전달되거나, 서버가 그룹을 자체 조회하면 인정되지 않을 수 있습니다.";
const LUSTRE = "Lustre(등록 종류 기준): 서버(MDS) 설정에 따라 서버가 그룹을 다시 판정하므로 보조 그룹이 인정되지 않을 수 있습니다.";
const GENERIC = "실제 인정 여부는 스토리지 설정에 따라 다를 수 있습니다(등록된 스토리지 종류 기준 안내).";

test("groupCaveat: 등록 라벨 6종 + 모르는 라벨·null -- NFS 둘·Lustre 만 고유 문구, 나머지는 일반 주의문", () => {
  expect(groupCaveat("purestorage")).toBe(NFS);
  expect(groupCaveat("netapp")).toBe(NFS);
  expect(groupCaveat("lustre")).toBe(LUSTRE);
  for (const t of ["cephfs", "gpfs", "wekafs"]) expect(groupCaveat(t)).toBe(GENERIC);
  expect(groupCaveat("beegfs")).toBe(GENERIC);       // 모르는 라벨 -- 단정하지 않는다
  expect(groupCaveat("NetApp")).toBe(GENERIC);       // 서버 어휘는 소문자 식별자(대소문자 변형은 모르는 값)
  expect(groupCaveat("")).toBe(GENERIC);
  expect(groupCaveat(null)).toBe(GENERIC);
  expect(groupCaveat(undefined)).toBe(GENERIC);
});

test("groupCaveatsFor: 문구 기준 중복 없이 순서대로, 빈 이름은 건너뛰고 맵에 없는 이름은 일반 주의문", () => {
  const backends = { a: "netapp", b: "purestorage", c: "lustre", d: "cephfs", e: "gpfs" };
  expect(groupCaveatsFor(["a", "b"], backends)).toEqual([NFS]);              // 둘 다 NFS -- 한 줄
  expect(groupCaveatsFor(["c", "a"], backends)).toEqual([LUSTRE, NFS]);      // 순서 유지
  expect(groupCaveatsFor(["d", "e"], backends)).toEqual([GENERIC]);          // cephfs·gpfs 는 같은 일반 문구
  expect(groupCaveatsFor([null, "a", undefined, ""], backends)).toEqual([NFS]);
  expect(groupCaveatsFor([null, null], backends)).toEqual([]);
  // 비관리자 응답엔 관리자 전용 스토리지가 없다 -- 그 이름은 모름 → 일반 주의문(거짓 단정 금지)
  expect(groupCaveatsFor(["adm-only"], backends)).toEqual([GENERIC]);
  // Object.prototype 의 키가 라벨로 새지 않는다
  expect(groupCaveatsFor(["constructor", "toString"], {})).toEqual([GENERIC]);
});

test("supplementaryGroupsText: 다섯 상태", () => {
  expect(supplementaryGroupsText({ supplementary_gids: [10010, 20001], supplementary_gids_status: "applied",
    supplementary_gids_excluded: [], supplementary_gids_found: 2 })).toBe("10010, 20001");
  expect(supplementaryGroupsText({ supplementary_gids: [], supplementary_gids_status: "none",
    supplementary_gids_excluded: [], supplementary_gids_found: 0 })).toBe("없음");
  expect(supplementaryGroupsText({ supplementary_gids: [], supplementary_gids_status: "over_limit",
    supplementary_gids_excluded: [], supplementary_gids_found: 300 }))
    .toBe("적용 안 됨 — 그룹 300개가 상한 256개를 넘음");
  expect(supplementaryGroupsText({ supplementary_gids: [], supplementary_gids_status: "disabled",
    supplementary_gids_excluded: [], supplementary_gids_found: null }))
    .toBe("적용 안 됨 — 운영자가 기능을 꺼 둠(계획 시점)");
  expect(supplementaryGroupsText({ supplementary_gids: [], supplementary_gids_status: "privileged",
    supplementary_gids_excluded: [], supplementary_gids_found: null })).toBe("root 실행 — 해당 없음");
});

test("supplementaryGroupsText: 제외된 gid 가 있으면 끝에 꼬리 -- 조용한 권한 축소가 되지 않게", () => {
  expect(supplementaryGroupsText({ supplementary_gids: [10010], supplementary_gids_status: "applied",
    supplementary_gids_excluded: [-5, 4294967296], supplementary_gids_found: 1 }))
    .toBe("10010 (제외: -5, 4294967296 — 유효하지 않은 gid)");
  expect(supplementaryGroupsText({ supplementary_gids: [], supplementary_gids_status: "none",
    supplementary_gids_excluded: [-1], supplementary_gids_found: 0 }))
    .toBe("없음 (제외: -1 — 유효하지 않은 gid)");
  // 0·65534 는 D3 로 인정되는 정상값이다(제외 아님) -- 화면이 걸러 내지 않는다
  expect(supplementaryGroupsText({ supplementary_gids: [0, 65534], supplementary_gids_status: "applied",
    supplementary_gids_excluded: [], supplementary_gids_found: 2 })).toBe("0, 65534");
});

test("supplementaryGroupsText: 키 부재(배포 전 잡)·identity 없음·모르는 상태·모순 모양은 null(행 숨김)", () => {
  expect(supplementaryGroupsText(undefined)).toBeNull();
  expect(supplementaryGroupsText(null)).toBeNull();
  expect(supplementaryGroupsText({ username: "alice", uid: 10003, gid: 10000, privileged: false })).toBeNull();
  expect(supplementaryGroupsText({ supplementary_gids_status: null })).toBeNull();
  expect(supplementaryGroupsText({ supplementary_gids_status: "weird" })).toBeNull();
  // applied 인데 목록이 비었다(stepper 가 끊는 변조 모양) -- 거짓 목록을 지어내지 않는다
  expect(supplementaryGroupsText({ supplementary_gids: [], supplementary_gids_status: "applied" })).toBeNull();
  expect(supplementaryGroupsText({ supplementary_gids: null, supplementary_gids_status: "applied" })).toBeNull();
  // over_limit 의 found 를 모르면 숫자 없이
  expect(supplementaryGroupsText({ supplementary_gids_status: "over_limit" }))
    .toBe("적용 안 됨 — 그룹 수가 상한 256개를 넘음");
});
