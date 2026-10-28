import { describe, expect, test } from "vitest";
import {
  BINDING_NAME_RE, TRIGGER_LABEL, bindingParts, callerNodeState, changeNames, clusterStatusMeta, countText, durationText,
  failureResolved, intervalText, isoAfter, nsLabel, pathUnverifiedSince, phaseVariant, pvJobLink, runStatusMeta,
  shortReasonText,
  storageKindOf, storageOwnerOf, unrecognizedWhy, untilText, warningText,
} from "./k8sTerms";
import { pvRootDestNote } from "./LocationField";
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
  // namespaceAdmin(소유자, 2026-10-26 (a)) -- 서버가 인식하지 않았다면 도메인·subject 조건 탓
  expect(unrecognizedWhy({ name: "namespaceAdmin", role_kind: "ClusterRole", role_name: "kubeflow-admin" }))
    .toContain("이메일 도메인이 DMS 계정 메일 도메인과 다르거나 User subject 가 하나가 아님");
  expect(unrecognizedWhy({ name: "namespaceAdmin", role_kind: "ClusterRole", role_name: "kubeflow-edit" }))
    .toContain("역할이 ClusterRole kubeflow-admin 이 아님");
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
  // 2026-10-28 사고: secondary 는 대체 때만 불려 실패 하나가 「최근 실패」로 오래 남았다 -- 그 클러스터가 이후 다른 노드로
  // 회복했으면 지금 실패 중이 아니라 「이 노드 경로를 다시 확인할 일」이다.
  test("실패한 클러스터가 이후 회복했으면 재확인 필요(action), 아니면·모르면 최근 실패(bad)", () => {
    const failed = { ...base, role: "secondary" as const, last_ok_at: "2026-10-25T00:00:00Z",
                     last_fail_at: "2026-10-26T03:27:00Z", last_fail_cluster: "dmsk8s2", last_fail_trigger: "failover" };
    const later = [{ name: "dmsk8s2", last_success_at: "2026-10-26T03:29:06Z" }];
    expect(callerNodeState(failed, later)).toEqual({ label: "재확인 필요", variant: "action" });
    // 클러스터 마지막 성공이 실패보다 앞이면(아직 회복 안 됨) 최근 실패
    expect(callerNodeState(failed, [{ name: "dmsk8s2", last_success_at: "2026-10-26T03:00:00Z" }]))
      .toEqual({ label: "최근 실패", variant: "bad" });
    // 클러스터 정보가 없거나(인자 생략·지운 클러스터) 실패 클러스터를 모르면(옛 응답) 종전대로
    expect(callerNodeState(failed).label).toBe("최근 실패");
    expect(callerNodeState(failed, [{ name: "other", last_success_at: "2026-10-27T00:00:00Z" }]).label).toBe("최근 실패");
    expect(callerNodeState({ ...failed, last_fail_cluster: undefined }, later).label).toBe("최근 실패");
    expect(callerNodeState(failed, [{ name: "dmsk8s2", last_success_at: null }]).label).toBe("최근 실패");
    // 그 뒤 이 노드가 성공했으면 실패 판정 자체가 없다
    expect(callerNodeState({ ...failed, last_ok_at: "2026-10-28T00:00:00Z", serving: ["dmsk8s"] }, later).label)
      .toBe("사용 중");
  });
  // 2026-10-28 리뷰: 「재확인 필요」는 secondary 의 대체·정규 실패에만 — primary 는 매 주기 스스로 다시 부르니 마지막 성공보다
  // 나중인 실패는 지금 실패다(대체가 매번 메워 클러스터가 회복해 보여도). 확인 run 의 실패는 방금 확인한 결과다.
  test("primary 의 실패·secondary 의 보조 연결 확인 실패는 클러스터가 회복해도 최근 실패", () => {
    const later = [{ name: "dmsk8s2", last_success_at: "2026-10-26T03:29:06Z" }];
    const primary = { ...base, role: "primary" as const, last_ok_at: "2026-10-25T00:00:00Z",
                      last_fail_at: "2026-10-26T03:27:00Z", last_fail_cluster: "dmsk8s2", last_fail_trigger: "schedule" };
    expect(callerNodeState(primary, later)).toEqual({ label: "최근 실패", variant: "bad" });
    const checked = { ...primary, role: "secondary" as const, last_fail_trigger: "check" };
    expect(callerNodeState(checked, later)).toEqual({ label: "최근 실패", variant: "bad" });
    expect(pathUnverifiedSince(checked, later)).toBe(false);
    expect(pathUnverifiedSince({ ...checked, last_fail_trigger: "schedule" }, later)).toBe(true);
    expect(pathUnverifiedSince({ ...checked, role: null, last_fail_trigger: "failover" }, later)).toBe(false);
  });
  // 2026-10-28 2차 리뷰: 배지도 「마지막 실패」 칸과 같은 판정(failureResolved — 그 클러스터에 대한 이 노드의 그 뒤 성공)을
  // 쓴다. 노드 단위 마지막 성공(다른 클러스터)으로 실패를 덮으면 칸은 빨간데 배지는 「대기」·「사용 중」이었다.
  test("다른 클러스터의 나중 성공은 실패를 덮지 않는다 — 확인 묶음 앞 실패·뒤 성공이어도 결과는 순서와 무관", () => {
    const at = (s: number) => new Date(Date.parse("2026-10-28T01:00:00Z") - s * 1000).toISOString();
    // 같은 확인 묶음: dmsk8s(이름순 앞) 실패 40초 전 → dmsk8s2 성공 20초 전
    const aFailBOk = { ...base, role: "secondary" as const, last_ok_at: at(20), last_fail_at: at(40),
                       last_fail_cluster: "dmsk8s", last_fail_trigger: "check", last_fail_cluster_ok_at: at(3 * 86400) };
    const clusters = [{ name: "dmsk8s", last_success_at: at(5) }, { name: "dmsk8s2", last_success_at: at(20) }];
    expect(failureResolved(aFailBOk)).toBe(false);
    expect(callerNodeState(aFailBOk, clusters)).toEqual({ label: "최근 실패", variant: "bad" });
    // 순서만 반대(앞 성공 → 뒤 실패)여도 같은 배지
    const aOkBFail = { ...aFailBOk, last_ok_at: at(40), last_fail_at: at(20), last_fail_cluster: "dmsk8s2",
                       last_fail_cluster_ok_at: null };
    expect(callerNodeState(aOkBFail, clusters)).toEqual({ label: "최근 실패", variant: "bad" });
    // 대체 실패(클러스터는 이후 primary 로 회복) 뒤 다른 클러스터를 대체로 맡아 성공 — 「사용 중」이 아니라 「재확인 필요」
    const failover = { ...aFailBOk, last_fail_trigger: "failover", serving: ["dmsk8s2"] };
    expect(callerNodeState(failover, clusters)).toEqual({ label: "재확인 필요", variant: "action" });
    // 그 클러스터에 그 뒤 성공했으면 해결 — 칸 없음(옛 응답)은 노드의 마지막 성공으로
    expect(callerNodeState({ ...failover, last_fail_cluster_ok_at: at(10) }, clusters))
      .toEqual({ label: "사용 중", variant: "ok" });
    expect(failureResolved({ last_fail_at: at(40), last_ok_at: at(20) })).toBe(true);
    expect(failureResolved({ last_fail_at: at(40), last_ok_at: at(20), last_fail_cluster_ok_at: null })).toBe(false);
  });
});

test("보조 연결 확인 라벨·짧은 사유: 짧은 라벨이 있으면 그것, 없으면 사유 문장의 「 — 」 앞부분", () => {
  expect(TRIGGER_LABEL.check).toBe("보조 연결 확인");
  expect(shortReasonText("k8s_sync_unreachable")).toBe("API 서버 연결 실패");
  expect(shortReasonText("k8s_sync_unreachable")).not.toContain("방화벽");
  expect(shortReasonText("k8s_token_required")).toBe("API 토큰을 입력하세요");
  expect(shortReasonText("zz_unknown")).toBe("zz_unknown");
  expect(shortReasonText(null)).toBe("");
  expect(isoAfter("2026-10-26T03:29:06Z", "2026-10-26T03:27:00Z")).toBe(true);
  expect(isoAfter(null, "2026-10-26T03:27:00Z")).toBe(false);          // 모름을 「나중」으로 지어내지 않는다
  expect(isoAfter("2026-10-26T03:27:00Z", undefined)).toBe(false);
});

test("warningText·changeNames·phaseVariant·storageKindOf", () => {
  expect(warningText("k8s_sync_forbidden")).not.toBe("");
  expect(warningText({ list: "pvs", reason: "zz_unknown" })).toBe("pvs: zz_unknown");
  // 서버 와이어 모양(routes_agent._warnings): {phase, reason, http_status} — phase 는 목록 라벨로
  expect(warningText({ phase: "persistentvolumes", reason: "zz_unknown", http_status: 403 })).toBe("볼륨(PV) 목록: zz_unknown");
  expect(warningText({ reason: "zz_unknown", http_status: null })).toBe("zz_unknown");
  expect(changeNames({ added: 2, removed: 0, names: ["a", "b"] })).toEqual({ added: [], removed: [], other: ["a", "b"] });
  expect(changeNames({ added: 1, removed: 1, names: { added: ["a"], removed: ["b"] } }))
    .toEqual({ added: ["a"], removed: ["b"], other: [] });
  expect(changeNames({ added: 0, removed: 0, changed: 1, names: { added: [], removed: [], changed: ["c"] } }))
    .toEqual({ added: [], removed: [], other: ["c"] });
  expect(changeNames(null)).toEqual({ added: [], removed: [], other: [] });
  expect(phaseVariant("Bound")).toBe("ok");
  expect(phaseVariant("Pending")).toBe("action");
  expect(phaseVariant(null)).toBe("neutral");
  expect(storageKindOf({})).toBe("filesystem");
  expect(storageKindOf({ kind: null })).toBe("filesystem");
  expect(storageKindOf({ kind: "both" })).toBe("both");
});

// 기본 소유(2026-10-26): default_owner 가 있으면 그것(null = 저장값 오류), 없으면 default_uid·gid 로(NULL = 0, 서버 미러),
// 둘 다 없으면 모름(undefined). 0 은 정상값이다.
test("storageOwnerOf: 서버 default_owner 우선, 옛 응답은 uid·gid 로, 아무것도 없으면 모름", () => {
  expect(storageOwnerOf({ default_owner: "1500:1500", default_uid: 1, default_gid: 1 })).toBe("1500:1500");
  expect(storageOwnerOf({ default_owner: "0:0" })).toBe("0:0");
  expect(storageOwnerOf({ default_owner: null, default_uid: -1, default_gid: 0 })).toBeNull();
  expect(storageOwnerOf({ default_owner: "x" })).toBeNull();
  expect(storageOwnerOf({ default_uid: 1500, default_gid: 100 })).toBe("1500:100");
  expect(storageOwnerOf({ default_uid: 0, default_gid: 0 })).toBe("0:0");
  expect(storageOwnerOf({ default_uid: null, default_gid: null })).toBe("0:0");          // NULL = 0(root)
  expect(storageOwnerOf({ default_uid: 4294967295, default_gid: 0 })).toBeNull();
  expect(storageOwnerOf({ default_uid: true, default_gid: 0 })).toBeNull();
  expect(storageOwnerOf({ default_uid: "1500", default_gid: 0 })).toBeNull();
  expect(storageOwnerOf({})).toBeUndefined();
});

test("pvRootDestNote: PV 루트 목적지 경고는 결과 소유 규칙을 따른다(스토리지 기본 · 운영자 PV 루트 · chown)", () => {
  expect(pvRootDestNote({ rule: "storage" }, { storage_owner: "0:0" }))
    .toBe("목적지가 PV 전체(루트)라 PV 루트 디렉터리 자체가 스토리지 기본 소유자 0:0(root) 로 chown 되고, 권한·시각은 소스에 맞춰 바뀝니다 — 하위 디렉터리로 받으려면 경로를 적으세요.");
  expect(pvRootDestNote({ rule: "storage" }, {})).toContain("스토리지 기본 소유자(uid:gid)로 chown 되고");
  expect(pvRootDestNote({ rule: "pv_root" }, { storage_owner: "1500:1500" }))
    .toBe("목적지가 PV 전체(루트)라 PV 루트 디렉터리의 권한·시각이 소스에 맞춰 바뀝니다(소유자는 PV 루트 소유자 그대로) — 하위 디렉터리로 받으려면 경로를 적으세요.");
  expect(pvRootDestNote({ rule: "chown", value: "7:7" }, { storage_owner: "1500:1500" }))
    .toContain("PV 루트 디렉터리 자체가 chown 지정값 7:7 소유로 바뀌고");
});
