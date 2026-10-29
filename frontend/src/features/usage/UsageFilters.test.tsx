import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { UsageAttrFilter, UsageFacets } from "../../lib/types";
import { UsageFilters } from "./UsageFilters";

// 필터 줄의 보조 라벨(2026-10-29 리뷰): 딥링크·탭 이동으로 온 값이 선택지에 없을 때 무엇을 말하는가. 화면 흐름(URL·요청)은
// UsageAnalysis.test 가 본다 -- 여기는 선택지 → 라벨 규칙만.

const FACETS: UsageFacets = {
  storage_name: "k8s-pv-nfs", computed_at: "", window_limit: 10000, window_truncated: false, target_count: 6,
  scope: { inventory: 4, recorded: 1, outside: 1, unknown: 0 },
  clusters: [{ cluster: "dmsk8s", target_count: 4 }, { cluster: "dmsk8s2", target_count: 1 }],
  namespaces: [{ cluster: "dmsk8s", namespace: "team-a", target_count: 3 },
               { cluster: "dmsk8s", namespace: "team-b", target_count: 1 },
               { cluster: "dmsk8s2", namespace: "lab-y", target_count: 1 }],
  pvcs: [{ cluster: "dmsk8s", namespace: "team-a", pvc: "datasets", storage_class: "tb-nfs", target_count: 2 },
         { cluster: "dmsk8s", namespace: "team-b", pvc: "models", storage_class: "tb-nfs", target_count: 1 },
         { cluster: "dmsk8s2", namespace: "lab-y", pvc: "weights", storage_class: "c2-nfs", target_count: 1 }],
  storage_classes: [{ storage_class: "tb-nfs", target_count: 4 }, { storage_class: "c2-nfs", target_count: 1 }],
  users: [{ dms_user: "alice", target_count: 3 }, { dms_user: "cocoa.song", target_count: 1 }],
  requesters: [{ requester_id: "mason", target_count: 5 }, { requester_id: "alice", target_count: 2 }],
  lists_truncated: {},
};

function shown(attr: UsageAttrFilter, facets: UsageFacets = FACETS) {
  const { unmount } = render(<UsageFilters attr={attr} facets={facets} onChange={() => {}} />);
  const group = screen.getByRole("group", { name: "사용량 필터" });
  const value = (label: string) => {
    const el = within(group).queryByRole("combobox", { name: label }) as HTMLSelectElement | null;
    return el === null ? null : el.options[el.selectedIndex]?.textContent ?? null;
  };
  const out = { cluster: value("클러스터 필터"), namespace: value("namespace 필터"), pvc: value("PV 이름 필터"),
                sc: value("StorageClass 필터"), user: value("권한 사용자 필터"), requester: value("요청자 필터"),
                text: group.textContent ?? "" };
  unmount();
  return out;
}

test("짝이 덜 찬 값은 비어 있는 축을 말한다(「모든 클러스터」·「모든 namespace」·「모든 클러스터·namespace」)", () => {
  // namespace 는 있고 클러스터가 빈 PV 이름 -- 풀린 것은 클러스터다(예전엔 늘 「모든 namespace」라 같은 줄의 두 칸이 모순됐다)
  let s = shown({ namespace: "team-a", pvc: "datasets" });
  expect([s.namespace, s.pvc]).toEqual(["team-a (모든 클러스터)", "datasets (모든 클러스터)"]);
  s = shown({ cluster: "dmsk8s", pvc: "datasets" });
  expect(s.pvc).toBe("datasets (모든 namespace)");
  s = shown({ pvc: "models" });
  expect(s.pvc).toBe("models (모든 클러스터·namespace)");
  // 짝이 다 찬 값은 선택지 그대로
  s = shown({ cluster: "dmsk8s", namespace: "team-a", pvc: "datasets" });
  expect([s.namespace, s.pvc]).toEqual(["team-a · dmsk8s · 3개", "datasets — team-a (tb-nfs) · dmsk8s · 2개"]);
});

test("이름은 이 스토리지에 있는데 고른 클러스터·namespace 아래에만 없으면 그렇게 말한다 -- 「이 스토리지에 없음」은 정말 없을 때만", () => {
  let s = shown({ cluster: "dmsk8s2", namespace: "team-a" });               // team-a 는 dmsk8s 에 있다
  expect(s.namespace).toBe("team-a (이 클러스터에 없음)");
  s = shown({ cluster: "dmsk8s", namespace: "team-a", pvc: "models" });     // models 는 dmsk8s 의 team-b 에 있다
  expect(s.pvc).toBe("models (이 namespace 에 없음)");
  s = shown({ cluster: "dmsk8s", namespace: "team-a", pvc: "weights" });    // weights 는 dmsk8s2 에만
  expect(s.pvc).toBe("weights (이 클러스터에 없음)");
  s = shown({ namespace: "team-b", pvc: "datasets" });                      // 클러스터 없이 -- 다른 namespace 에만
  expect(s.pvc).toBe("datasets (이 namespace 에 없음)");
  s = shown({ cluster: "dmsk8s2", pvc: "models" });                         // namespace 없이 -- 다른 클러스터에만
  expect(s.pvc).toBe("models (이 클러스터에 없음)");
  s = shown({ cluster: "dmsk8s3", namespace: "zz", pvc: "nope", storage_class: "x-sc", user: "bob", requester: "eve" });
  expect([s.cluster, s.namespace, s.pvc, s.sc, s.user, s.requester]).toEqual([
    "dmsk8s3 (이 스토리지에 없음)", "zz (이 스토리지에 없음)", "nope (이 스토리지에 없음)", "x-sc (이 스토리지에 없음)",
    "bob (이 스토리지에 없음)", "eve (이 스토리지에 없음)"]);
});

test("판정 창이 넘친 스토리지: PV 선택지·수는 최근 스캔된 창 기준이라고 말하고, 없는 PV 값은 「최근 N개 안에 없음」(요청자는 전체 기준)", () => {
  const windowed = { ...FACETS, window_truncated: true };
  let s = shown({ cluster: "dmsk8s3", pvc: "nope", storage_class: "x-sc", user: "bob", requester: "eve" }, windowed);
  expect([s.cluster, s.pvc, s.sc, s.user, s.requester]).toEqual([
    "dmsk8s3 (최근 10,000개 안에 없음)", "nope (최근 10,000개 안에 없음)", "x-sc (최근 10,000개 안에 없음)",
    "bob (최근 10,000개 안에 없음)", "eve (이 스토리지에 없음)"]);
  expect(s.text).toContain("대상이 10,000개를 넘는 스토리지라 PV 선택지와 수는 최근 스캔된 10,000개 기준입니다(요청자는 전체 기준)");
  // 창이 넘치지 않았으면 그 줄이 없다
  expect(shown({}).text).not.toContain("최근 스캔된");
  // 요청자만 남은 탭(PV 안 대상 0 -- PV 칸·구분이 숨는다)에는 해당이 없다
  const fsOnly = { ...windowed, scope: { inventory: 0, recorded: 0, outside: 6, unknown: 0 }, clusters: [], namespaces: [],
                   pvcs: [], storage_classes: [], users: [] };
  s = shown({}, fsOnly);
  expect([s.cluster, s.requester]).toEqual([null, "요청자 전체"]);
  expect(s.text).not.toContain("최근 스캔된");
});
