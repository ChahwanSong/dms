import { describe, expect, test } from "vitest";
import {
  BINDING_NAME_RE, bindingParts, callerNodeState, changeNames, clusterStatusMeta, countText, durationText, intervalText,
  nsLabel, phaseVariant, pvJobLink, runStatusMeta, storageKindOf, unrecognizedWhy, untilText, warningText,
} from "./k8sTerms";
import type { CallerNode } from "./types";

// 용어·표기 단일 출처(설계 §1). 화면 라벨이 이 파일 밖에서 지어지면 「PV 이름」과 「볼륨」이 화면마다 뒤바뀐다.

test("nsLabel = 「namespace (storageclass)」, SC 없음은 그 사실을 적는다", () => {
  expect(nsLabel("team-a", "tb-nfs")).toBe("team-a (tb-nfs)");
  expect(nsLabel("team-a", null)).toBe("team-a (StorageClass 없음)");
});

test("countText: null(모름)은 「—」, 0 은 「0」 -- 둘을 뭉개지 않는다", () => {
  expect(countText(null)).toBe("—");
  expect(countText(undefined)).toBe("—");
  expect(countText(0)).toBe("0");
  expect(countText(1234)).toBe("1,234");
});

test("클러스터 상태 배지: variant 를 명시하고 모르는 상태는 원문 중립", () => {
  expect(clusterStatusMeta("ok")).toMatchObject({ label: "정상", variant: "ok" });
  expect(clusterStatusMeta("failing")).toMatchObject({ label: "실패", variant: "bad" });
  expect(clusterStatusMeta("never")).toMatchObject({ label: "동기화 전", variant: "neutral" });
  expect(clusterStatusMeta("delayed").variant).toBe("action");
  expect(clusterStatusMeta("weird")).toMatchObject({ label: "weird", variant: "neutral" });
  expect(runStatusMeta("applied")).toMatchObject({ label: "적용됨", variant: "ok" });
  expect(runStatusMeta("superseded").variant).toBe("neutral");
});

test("intervalText·durationText·untilText", () => {
  expect(intervalText(300)).toBe("5분");
  expect(intervalText(3600)).toBe("1시간");
  expect(intervalText(90)).toBe("90초");
  expect(durationText(null)).toBe("—");
  expect(durationText(1234)).toBe("1.2초");
  expect(durationText(250)).toBe("250ms");
  const now = Date.parse("2026-10-25T00:00:00Z");
  expect(untilText("2026-10-25T00:05:30Z", now)).toBe("5분 뒤");
  expect(untilText("2026-10-24T23:00:00Z", now)).toBe("곧");
  expect(untilText(null, now)).toBe("—");
});

test("pvJobLink = /jobs/new?pv=<cluster>/<ns>/<pvc>", () => {
  expect(pvJobLink("dmsk8s", "team-a", "models")).toBe("/jobs/new?pv=dmsk8s/team-a/models");
  expect(new URL(`http://x${pvJobLink("a b", "ns", "p")}`).searchParams.get("pv")).toBe("a b/ns/p");
});

test("인식 규칙 표시: 역할·이름 중 무엇이 어긋났는지 한 줄로", () => {
  expect(BINDING_NAME_RE.test("junwoo-song-clusterrole-admin")).toBe(true);
  expect(unrecognizedWhy({ name: "bob-clusterrole-edit", role_kind: "ClusterRole", role_name: "edit" }))
    .toContain("역할이 ClusterRole kubeflow-admin·kubeflow-edit 가 아님");
  expect(unrecognizedWhy({ name: "namespaceAdmin", role_kind: "ClusterRole", role_name: "kubeflow-admin" }))
    .toContain("이름이 <ID>-clusterrole-admin|edit 꼴이 아님");
  expect(unrecognizedWhy({ name: "bob-clusterrole-view", role_kind: "ClusterRole", role_name: "kubeflow-view" }))
    .toContain("이름도");
});

test("bindingParts: 이름만 와도, 원문 객체가 와도 같은 모양", () => {
  expect(bindingParts("alice-clusterrole-edit")).toEqual({ name: "alice-clusterrole-edit", role: null, subjects: [] });
  expect(bindingParts({ name: "x", role_kind: "ClusterRole", role_name: "kubeflow-edit",
                        subjects: [{ kind: "User", name: "alice@example.com" },
                                   { kind: "ServiceAccount", name: "sa", namespace: "ns" }] }))
    .toEqual({ name: "x", role: "ClusterRole/kubeflow-edit", subjects: ["User alice@example.com", "ServiceAccount ns/sa"] });
});

describe("callerNodeState", () => {
  const base: CallerNode = { node_name: "k8s-w1", role: "primary", fresh: true, capable: true, reported_at: null,
    internal_ip: null, last_claim_at: null, last_ok_at: null, last_fail_at: null, last_reason: null, serving: [] };
  test("목록에 없음 → 보고 없음, 지연 → 보고 지연, 미선언 → 미지원", () => {
    expect(callerNodeState(undefined).label).toBe("보고 없음");
    expect(callerNodeState({ ...base, fresh: false }).label).toBe("보고 지연");
    expect(callerNodeState({ ...base, capable: false })).toEqual({ label: "미지원 에이전트", variant: "action" });
  });
  test("최근 실패가 마지막 성공보다 나중이면 실패, 맡은 클러스터가 있으면 사용 중, 아니면 대기", () => {
    expect(callerNodeState({ ...base, last_ok_at: "2026-10-25T00:00:00Z", last_fail_at: "2026-10-25T00:01:00Z" }).label)
      .toBe("최근 실패");
    expect(callerNodeState({ ...base, last_ok_at: "2026-10-25T00:02:00Z", last_fail_at: "2026-10-25T00:01:00Z",
                             serving: ["dmsk8s"] })).toEqual({ label: "사용 중", variant: "ok" });
    expect(callerNodeState(base)).toEqual({ label: "대기", variant: "neutral" });
  });
});

test("warningText·changeNames·phaseVariant·storageKindOf", () => {
  expect(warningText("k8s_sync_forbidden")).not.toBe("");
  expect(warningText({ list: "pvs", reason: "zz_unknown" })).toBe("pvs: zz_unknown");
  expect(changeNames({ added: 2, removed: 0, names: ["a", "b"] })).toEqual({ added: [], removed: [], other: ["a", "b"] });
  expect(changeNames({ added: 1, removed: 1, names: { added: ["a"], removed: ["b"] } }))
    .toEqual({ added: ["a"], removed: ["b"], other: [] });
  expect(phaseVariant("Bound")).toBe("ok");
  expect(phaseVariant("Pending")).toBe("action");
  expect(phaseVariant(null)).toBe("neutral");
  expect(storageKindOf({})).toBe("filesystem");
  expect(storageKindOf({ kind: null })).toBe("filesystem");
  expect(storageKindOf({ kind: "both" })).toBe("both");
});
