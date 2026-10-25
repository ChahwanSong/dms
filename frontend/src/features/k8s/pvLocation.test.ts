import { beforeEach, expect, test } from "vitest";
import {
  SIDE_LABELS, bindingNameFor, buildGroups, effectiveGroup, emptyLoc, groupKey, normalizeSub, parsePvParam, pvAbsPath,
  pvDestParent, pvOptionText, pvSubProblem, pvSummaryText, readLastGroup, writeLastGroup,
} from "./pvLocation";
import { pvJobLink } from "./k8sTerms";
import { VOLUME_MODELS, VOLUME_PENDING } from "./k8sFixtures";
import type { VolumeRow } from "./types";

// 단일 작업 PV 고르기의 순수 규칙(dms-k8s 설계 §6·§7 미러 -- 최종 판정은 서버).

beforeEach(() => { try { localStorage.clear(); } catch { /* 없음 */ } });

test("pvSubProblem: rm 은 '', '.', './', './/.' 처럼 정규화하면 PV 루트인 경로를 막는다(pv_root_rm_forbidden 미러)", () => {
  for (const sub of ["", ".", "./", ".//.", "/"]) {
    expect(pvSubProblem(sub.startsWith("/") ? "./" : sub, "rm")).toBe("PV 전체(루트)는 삭제할 수 없습니다 — PV 안의 하위 경로를 적으세요");
  }
  expect(pvSubProblem("a", "rm")).toBeNull();
  expect(pvSubProblem("./a/", "rm")).toBeNull();
  // sync·scan 은 빈 하위경로 = PV 전체(정상값)
  for (const op of ["sync", "scan"]) {
    expect(pvSubProblem("", op)).toBeNull();
    expect(pvSubProblem(".", op)).toBeNull();
  }
  expect(pvSubProblem("/abs", "scan")).toMatch(/^"\/" 로 시작할 수 없습니다/);
  expect(pvSubProblem("a/../b", "sync")).toMatch(/^"\.\." 은 쓸 수 없습니다/);
});

test("normalizeSub·pvAbsPath·pvDestParent: 서버 normpath 처럼 접고, abs_root 를 모르면 지어내지 않는다", () => {
  expect(normalizeSub("./a//b/./c/")).toBe("a/b/c");
  expect(normalizeSub(" a ")).toBe(" a ");                         // 공백은 이름의 일부
  expect(pvAbsPath(VOLUME_MODELS, "")).toBe(VOLUME_MODELS.abs_root);
  expect(pvAbsPath(VOLUME_MODELS, "./x/")).toBe(`${VOLUME_MODELS.abs_root}/x`);
  expect(pvAbsPath(VOLUME_PENDING, "x")).toBeNull();
  expect(pvAbsPath(null, "x")).toBeNull();
  expect(pvDestParent({ abs_root: "/k8s_pv/nfs/team-a/datasets" }, "")).toBe("/k8s_pv/nfs/team-a");
  expect(pvDestParent({ abs_root: "/k8s_pv/nfs/team-a/datasets" }, "in/run")).toBe("/k8s_pv/nfs/team-a/datasets/in");
});

test("buildGroups: 「namespace (StorageClass)」 묶음, 권한 namespace 의 PV 없음은 따로, 클러스터가 여럿이면 꼬리", () => {
  const nfs: VolumeRow = { ...VOLUME_MODELS, pvc: "datasets", storage_class: "tb-nfs" };
  const one = buildGroups([VOLUME_MODELS, nfs], [
    { cluster: "dmsk8s", namespace: "team-a", access: "admin" }, { cluster: "dmsk8s", namespace: "team-z", access: "edit" }]);
  expect(one.map((g) => g.label)).toEqual(["team-a (tb-gpfs)", "team-a (tb-nfs)", "team-z — PV 없음"]);
  expect(one.map((g) => g.empty)).toEqual([false, false, true]);
  const two = buildGroups([VOLUME_MODELS, { ...VOLUME_MODELS, cluster: "dmsk8s-2" }], null);
  expect(two.map((g) => g.label)).toEqual(["team-a (tb-gpfs) · dmsk8s", "team-a (tb-gpfs) · dmsk8s-2"]);
});

test("pvOptionText: 「PV 이름 — 용량 · 상태」, 고를 수 없으면 이유, 모르면 모른다고", () => {
  expect(pvOptionText(VOLUME_MODELS)).toBe("models — 1Gi · Bound");
  expect(pvOptionText(VOLUME_PENDING)).toBe("pending-claim — 용량 모름 · Pending (Bound 아님)");
  expect(pvOptionText({ ...VOLUME_MODELS, selectable: false, reason: "pv_storageclass_unmapped" }))
    .toBe("models — 1Gi · Bound (StorageClass 매핑 없음)");
  expect(pvOptionText({ ...VOLUME_MODELS, selectable: false, reason: "pv_path_unresolved", phase: null }))
    .toBe("models — 1Gi · 상태 모름 (경로를 알 수 없음)");
});

test("effectiveGroup: 고른 값 → 고른 PV 의 묶음 → 하나뿐이면 그것 → 최근 고른 묶음(목록에 있을 때만)", () => {
  const nfs: VolumeRow = { ...VOLUME_MODELS, pvc: "datasets", storage_class: "tb-nfs" };
  const groups = buildGroups([VOLUME_MODELS, nfs], null);
  const gpfsKey = groupKey("dmsk8s", "team-a", "tb-gpfs");
  const nfsKey = groupKey("dmsk8s", "team-a", "tb-nfs");
  expect(effectiveGroup({ ...emptyLoc(), group: nfsKey }, groups, null, null)).toBe(nfsKey);
  expect(effectiveGroup(emptyLoc(), groups, VOLUME_MODELS, null)).toBe(gpfsKey);
  expect(effectiveGroup(emptyLoc(), groups, null, null)).toBe("");
  expect(effectiveGroup(emptyLoc(), groups, null, nfsKey)).toBe(nfsKey);
  expect(effectiveGroup(emptyLoc(), groups, null, "[\"x\"]")).toBe("");
  expect(effectiveGroup(emptyLoc(), buildGroups([VOLUME_MODELS], null), null, null)).toBe(gpfsKey);
});

test("최근 고른 묶음 기억은 쪽마다, 저장소가 막혀도 던지지 않는다", () => {
  writeLastGroup("source", "k1");
  expect(readLastGroup("source")).toBe("k1");
  expect(readLastGroup("target")).toBeNull();
  writeLastGroup("source", "");
  expect(readLastGroup("source")).toBeNull();
  const saved = globalThis.localStorage;
  Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => { throw new Error("blocked"); } });
  try {
    expect(readLastGroup("source")).toBeNull();
    expect(() => writeLastGroup("source", "k2")).not.toThrow();
  } finally {
    Object.defineProperty(globalThis, "localStorage", { configurable: true, writable: true, value: saved });
  }
});

test("parsePvParam 은 pvJobLink 의 짝 -- 모양이 틀리면 null", () => {
  const link = pvJobLink("dmsk8s", "team-a", "kraken-kv-cache");
  expect(parsePvParam(new URL(`http://x${link}`).searchParams.get("pv"))).toEqual(
    { cluster: "dmsk8s", namespace: "team-a", pvc: "kraken-kv-cache" });
  expect(parsePvParam(null)).toBeNull();
  expect(parsePvParam("a/b")).toBeNull();
  expect(parsePvParam("a//c")).toBeNull();
  expect(parsePvParam("a/b/%E0%A4%A")).toBeNull();
});

test("bindingNameFor·pvSummaryText·라벨: 서로 부분 문자열이 아닌 쪽별 라벨", () => {
  expect(bindingNameFor("junwoo.song")).toBe("junwoo-song-clusterrole-edit|admin");
  expect(pvSummaryText({ cluster: "dmsk8s", namespace: "team-a", pvc: "models" }, VOLUME_MODELS, "./a/"))
    .toBe("models — team-a (tb-gpfs) / a");
  expect(pvSummaryText({ cluster: "dmsk8s", namespace: "team-a", pvc: "models" }, null, ""))
    .toBe("models — team-a (?) / (PV 전체)");
  // 한 화면(sync = 소스·목적지, scan·rm = 대상)의 입력 라벨끼리 부분 문자열이 되지 않는다(e2e getByLabel 부분 일치).
  const screens = [["source", "destination"], ["target"]] as const;
  for (const sides of screens) {
    const labels = sides.flatMap((s) => {
      const L = SIDE_LABELS[s];
      return [L.group, L.pvc, L.sub, L.search, L.storage, L.path, L.mode];
    });
    labels.push("권한 기준 사용자", "연산", "우선순위");
    for (const a of labels) for (const b of labels) if (a !== b) expect(b.includes(a)).toBe(false);
  }
});
