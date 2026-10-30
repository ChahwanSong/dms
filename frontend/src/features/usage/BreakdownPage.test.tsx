import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { describe, expect, test } from "vitest";
import type { UsageAttrFilter, UsageGroupRow } from "../../lib/types";
import { BREAKDOWN_API_UNKNOWN } from "./useUsage";
import { NO_PV_TEXT } from "./BreakdownPage";
import { groupName, pathsHref, rowKeys, shareText } from "./GroupTable";
import {
  NFS, chooseStorage, installServer, isoAgo, loc, m, navType, pathname, pvSetup, storageSelect, unreg,
} from "./usageTestKit";

// 사용량 분석 「소속별」(/admin/usage/breakdown, 2026-10-30 2단계): 서버 /breakdown 의 순위를 그대로 그리고, 행을 누르면 그 묶음의
// 필터를 건 경로별로 간다. 픽스처는 usageTestKit 의 테스트베드 모양(k8s-pv-nfs = 두 클러스터 PV + 기록 PV + 직접 경로, cephfs-dms =
// filesystem)이고, 순위 응답은 그 행들에서 손으로 센 값이다(서버 흉내가 아니다 -- 화면은 서버 값을 다시 계산하지 않는다).

installServer();

const PAGE = "/admin/usage/breakdown";
type By = "user" | "requester" | "namespace" | "pvc" | "storage_class" | "cluster";

const grow = (key: Partial<UsageGroupRow>, filter: UsageAttrFilter | null, bytes: number | null, targets: number,
              over: Partial<UsageGroupRow> = {}): UsageGroupRow => ({
  target_count: targets, summed_target_count: targets, nested_target_count: 0,
  total_bytes: bytes === null ? m(null, 0, targets) : m(bytes, targets),
  total_files: bytes === null ? m(null, 0, targets) : m(10 * targets + 1, targets),
  hot_ratio: { value: bytes === null ? null : 1, hot_bytes: bytes, total_bytes: bytes, known: bytes === null ? 0 : targets,
               unknown: bytes === null ? targets : 0 },
  oldest_scan_at: isoAgo(5), newest_scan_at: isoAgo(1), filter, ...key, ...over });

const NFS_TOTAL = 1_005_050;
const NFS_ROWS_BY: Record<By, UsageGroupRow[]> = {
  user: [grow({ dms_user: "alice" }, { user: "alice" }, 1_005_000, 3),
         grow({ dms_user: "junwoo.song" }, { user: "junwoo.song" }, 1_004_000, 2),
         grow({ dms_user: "cocoa.song" }, { user: "cocoa.song" }, 22, 1)],
  requester: [grow({ requester_id: "mason" }, { requester: "mason" }, 1_005_020, 5),
              grow({ requester_id: "alice" }, { requester: "alice" }, 1_000_063, 2)],
  namespace: [grow({ cluster: "dmsk8s", namespace: "team-a" }, { cluster: "dmsk8s", namespace: "team-a" }, 1_005_000, 3),
              grow({ cluster: "dmsk8s2", namespace: "lab-y" }, { cluster: "dmsk8s2", namespace: "lab-y" }, 22, 1)],
  pvc: [grow({ cluster: "dmsk8s", namespace: "team-a", pvc: "datasets", storage_class: "tb-nfs" },
             { cluster: "dmsk8s", namespace: "team-a", pvc: "datasets" }, 1_004_000, 2),
        grow({ cluster: "dmsk8s", namespace: "team-b", pvc: "models", storage_class: null },
             { cluster: "dmsk8s", namespace: "team-b", pvc: "models" }, 14, 1)],
  storage_class: [grow({ storage_class: "tb-nfs" }, { storage_class: "tb-nfs" }, 1_005_000, 4)],
  cluster: [grow({ cluster: "dmsk8s" }, { cluster: "dmsk8s" }, 1_005_014, 4),
            grow({ cluster: "dmsk8s2" }, { cluster: "dmsk8s2" }, 22, 1)],
};
const OUTSIDE_ROW = grow({}, { scope: "outside" }, 14, 1);
const NONE_USER = grow({}, null, 14, 1);

const isBy = (x: string | null): x is By => x !== null && x in NFS_ROWS_BY;
// k8s-pv-nfs 응답: by 파라미터 그대로 적용(없으면 user). PV 축은 끝에 「PV 밖」, user 축은 「권한 사용자 없음」도.
function nfsBreakdown(u: URL, over: Record<string, unknown> = {}) {
  const asked = u.searchParams.get("by");
  const by: By = isBy(asked) ? asked : "user";
  return {
    storage_name: NFS, computed_at: isoAgo(0), by, requested_by: asked, window_limit: 10000, window_truncated: false,
    target_count: 6, scope: { inventory: 4, recorded: 1, outside: 1, unknown: 0 }, cluster_count: 2,
    storage_total: { total_bytes: m(NFS_TOTAL, 4), summed_target_count: 4 },
    rows: NFS_ROWS_BY[by], rows_truncated: false,
    extras: { outside: by === "requester" ? null : OUTSIDE_ROW, unknown: null, none: by === "user" ? NONE_USER : null },
    pending_count: 0, unreadable_count: 0, progressed: false, ...over };
}
// cephfs-dms(filesystem -- PV 안 대상 없음): 서버는 무엇을 요청하든 requester 로 적용한다.
function fsBreakdown(u: URL, over: Record<string, unknown> = {}) {
  return {
    storage_name: "cephfs-dms", computed_at: isoAgo(0), by: "requester", requested_by: u.searchParams.get("by"),
    window_limit: 10000, window_truncated: false, target_count: 2, scope: { inventory: 0, recorded: 0, outside: 2, unknown: 0 },
    cluster_count: 0, storage_total: { total_bytes: m(17786075, 2), summed_target_count: 2 },
    rows: [grow({ requester_id: "mason" }, { requester: "mason" }, 17786075, 2),
           grow({ requester_id: "alice" }, { requester: "alice" }, 1024, 1)],
    rows_truncated: false, extras: { outside: null, unknown: null, none: null },
    pending_count: 0, unreadable_count: 0, progressed: false, ...over };
}
const byStorage = (u: URL) => (u.searchParams.get("storage") === NFS ? nfsBreakdown(u) : fsBreakdown(u));

const table = () => screen.findByRole("table", { name: /실 사용량 순위$/ });
const bySelect = () => screen.getByRole("combobox", { name: "기준" });
const rowOf = (name: string) => within(screen.getByRole("table")).getByText(name).closest('[role="row"]') as HTMLElement;
// 이름 칸의 첫 글자 요소(링크 또는 덧붙임 라벨) -- 부속·설명 단추는 빼고.
const rankNames = () => within(screen.getByRole("table")).getAllByRole("row").slice(1)
  .map((r) => within(r).getAllByRole("cell")[0].firstElementChild?.textContent);
const nameCell = (name: string) => within(rowOf(name)).getAllByRole("cell")[0];

describe("머리·기준·순위 표", () => {
  test("h1 = 탭 라벨, 기본 기준은 by 없이 부른다 · 구분 줄(조각마다 경로별 구분 링크) · 서버 순서 그대로의 표", async () => {
    const seen: URL[] = [];
    pvSetup({ path: `${PAGE}?storage=${NFS}`, breakdown: byStorage, onBreakdown: (u) => seen.push(u) });
    expect(await screen.findByRole("heading", { name: "소속별", level: 1 })).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "사용량 분석 하위 메뉴" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["경로별", "스토리지별", "소속별"]);
    expect(within(nav).getByRole("link", { name: "소속별" })).toHaveAttribute("aria-current", "page");
    const t = await table();
    expect(storageSelect()).toHaveValue(`s:${NFS}`);
    expect(t).toHaveAccessibleName("권한 사용자별 실 사용량 순위");
    expect(seen.map((u) => u.searchParams.get("by"))).toEqual([null]);              // 기본(권한 사용자)은 생략
    expect(bySelect()).toHaveValue("user");
    expect(within(bySelect()).getAllByRole("option").map((o) => o.textContent))
      .toEqual(["권한 사용자", "요청자", "namespace", "PV 이름", "StorageClass", "클러스터"]);
    // 구분 줄: PV 안(인벤토리 + 기록) · PV 밖 · 모름 -- 0 인 조각은 글자만
    const line = screen.getByText(/^PV 안 5/).closest("p")!;
    expect(line).toHaveTextContent("PV 안 5 · PV 밖(직접 경로) 1 · 모름 0");
    expect(within(line).getByRole("link", { name: "PV 안 5" })).toHaveAttribute("href", `/admin/usage?storage=${NFS}&scope=pv`);
    expect(within(line).getByRole("link", { name: "PV 밖(직접 경로) 1" }))
      .toHaveAttribute("href", `/admin/usage?storage=${NFS}&scope=outside`);
    expect(within(line).queryByRole("link", { name: "모름 0" })).toBeNull();
    expect(within(t).getAllByRole("columnheader").map((h) => h.textContent))
      .toEqual(["권한 사용자", "대상", "실 사용량", "파일", "hot 비율"]);
    // 서버 순서 그대로 + 끝에 덧붙임(PV 밖 · 권한 사용자 없음)
    expect(rankNames()).toEqual(["alice", "junwoo.song", "cocoa.song", "PV 밖(직접 경로)", "권한 사용자 없음"]);
    const alice = within(rowOf("alice")).getAllByRole("cell").map((c) => c.textContent);
    expect(alice).toEqual(["alice", "대상 3개", "981.4 KiB", "≈100%", "파일 31개", "hot 100%"]);
    expect(within(rowOf("cocoa.song")).getAllByRole("cell")[3]).toHaveTextContent("<0.1%");
    expect(within(rowOf("alice")).getAllByRole("cell")[3]).toHaveAttribute("title", "이 스토리지 합계 981.5 KiB 대비");
    // 각주: 행끼리 더하지 않음 · 정의 · 용어 · 막대의 분모(창이 넘치지 않았으면 창 문구 없음)
    const note = screen.getByText(/^행끼리 더하지 않습니다/);
    expect(note).toHaveTextContent("행끼리 더하지 않습니다 — 한 경로가 여러 사용자·요청자에 속합니다 · 묶음 합계 = 그 묶음에 맞는 "
      + "경로 중 최상위 합(경로별 필터 합계와 같은 정의) · 권한 사용자 = RoleBinding(관리자·특권 운영자의 전체 접근 제외) · "
      + "요청자 = 그 경로를 한 번이라도 scan 한 계정 · 막대 = 이 스토리지 합계 981.5 KiB 대비");
    expect(note).not.toHaveTextContent("최근 스캔된");
  });

  test("기준 바꾸기 = ?by= replace(권한 사용자는 URL 에서 지운다) · 응답 전엔 고른 값, 다른 기준의 행은 보이지 않는다 · 스토리지를 바꿔도 by 는 남는다", async () => {
    const seen: (string | null)[] = [];
    let release = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    pvSetup({ path: `${PAGE}?storage=${NFS}`, onBreakdown: (u) => seen.push(u.searchParams.get("by")),
              breakdown: async (u) => { if (u.searchParams.get("by") === "requester") await gate; return byStorage(u); } });
    await table();
    await userEvent.selectOptions(bySelect(), "requester");
    await waitFor(() => expect(loc().get("by")).toBe("requester"));
    expect(navType()).toBe("REPLACE");
    // 응답 전: 고른 값을 보이고, 이전 기준(권한 사용자)의 행은 그리지 않는다 -- 스토리지 단위인 구분 줄은 남는다
    expect(bySelect()).toHaveValue("requester");
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getByText(/^PV 안 5/)).toBeInTheDocument();
    release();
    await waitFor(() => expect(rankNames()).toEqual(["mason", "alice"]));
    expect(screen.getByRole("table")).toHaveAccessibleName("요청자별 실 사용량 순위");
    expect(bySelect()).toHaveValue("requester");
    // PV 이름: 부속 = 경로별 「PV 이름」 칸의 둘째 줄 표기(namespace (StorageClass)) + 클러스터가 둘 이상이면 클러스터
    await userEvent.selectOptions(bySelect(), "pvc");
    await waitFor(() => expect(rankNames()[0]).toBe("datasets"));
    expect(nameCell("datasets")).toHaveTextContent(/^datasetsteam-a \(tb-nfs\) · dmsk8s$/);
    expect(nameCell("models")).toHaveTextContent(/^modelsteam-b \(StorageClass 없음\) · dmsk8s$/);
    expect(screen.getByRole("table")).toHaveAccessibleName("PV 이름별 실 사용량 순위");
    await userEvent.selectOptions(bySelect(), "namespace");
    await waitFor(() => expect(rankNames()[0]).toBe("team-a"));
    expect(nameCell("team-a")).toHaveTextContent(/^team-admsk8s$/);
    // 권한 사용자 = 기본 -- URL 에서 by 를 지운다(replace)
    await userEvent.selectOptions(bySelect(), "user");
    await waitFor(() => expect(loc().has("by")).toBe(false));
    expect(navType()).toBe("REPLACE");
    await waitFor(() => expect(rankNames()[0]).toBe("alice"));
    expect(seen).toEqual([null, "requester", "pvc", "namespace"]);                  // 돌아온 user 는 캐시(같은 키 = by 없음)
    // 스토리지를 바꿔도 by 는 남는다
    await userEvent.selectOptions(bySelect(), "requester");
    await waitFor(() => expect(loc().get("by")).toBe("requester"));
    await chooseStorage("cephfs-dms");
    expect(loc().get("by")).toBe("requester");
    await waitFor(() => expect(rankNames()).toEqual(["mason", "alice"]));
  });

  test("PV 안 대상이 없는 스토리지: PV 축 다섯은 disabled + 문장, 서버가 적용한 요청자를 보인다(URL 의 PV 축은 그대로)", async () => {
    const seen: (string | null)[] = [];
    pvSetup({ path: `${PAGE}?storage=cephfs-dms&by=namespace`, breakdown: byStorage,
              onBreakdown: (u) => seen.push(u.searchParams.get("by")) });
    await table();
    expect(seen).toEqual(["namespace"]);
    expect(bySelect()).toHaveValue("requester");
    const opts = within(bySelect()).getAllByRole("option") as HTMLOptionElement[];
    expect(opts.filter((o) => o.disabled).map((o) => o.value)).toEqual(["user", "namespace", "pvc", "storage_class", "cluster"]);
    expect(screen.getByText(NO_PV_TEXT)).toBeInTheDocument();
    // 흐린 옵션의 이유를 낭독기도 듣는다(select 의 설명 = 그 문장)
    expect(bySelect()).toHaveAccessibleDescription(NO_PV_TEXT);
    expect(screen.queryByText(/^PV 안 /)).toBeNull();
    expect(loc().get("by")).toBe("namespace");                                      // URL 은 건드리지 않는다
    expect(rankNames()).toEqual(["mason", "alice"]);
    // PV 스토리지로 바꾸면 그 기준이 다시 걸린다
    await chooseStorage(NFS);
    await waitFor(() => expect(bySelect()).toHaveValue("namespace"));
    expect(screen.queryByText(NO_PV_TEXT)).toBeNull();
    expect(bySelect()).not.toHaveAttribute("aria-describedby");
    expect((within(bySelect()).getAllByRole("option") as HTMLOptionElement[]).some((o) => o.disabled)).toBe(false);
  });
});

describe("행 링크·덧붙임", () => {
  test("행 = 그 묶음의 필터를 건 경로별(push, 다른 쿼리 없이) -- 이름 링크도 행 아무 곳도 같다, 목록이 그 필터로 걸린다", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}&by=pvc`, breakdown: byStorage });
    await table();
    await waitFor(() => expect(rankNames()[0]).toBe("datasets"));
    // 링크 이름 = 「이름 — 부속」(같은 PV 이름이 다른 namespace·클러스터에 있어도 보조기기 링크 목록에서 가린다), 부속 글자는 보조기기에서 숨긴다
    const link = within(rowOf("datasets")).getByRole("link", { name: "datasets — team-a (tb-nfs) · dmsk8s" });
    expect(link).toHaveAttribute("href", `/admin/usage?storage=${NFS}&cluster=dmsk8s&namespace=team-a&pvc=datasets`);
    expect(link).toHaveAttribute("title", "datasets — team-a (tb-nfs) · dmsk8s");
    expect(within(nameCell("datasets")).getByText("team-a (tb-nfs) · dmsk8s")).toHaveAttribute("aria-hidden", "true");
    expect(within(rowOf("PV 밖(직접 경로)")).getByRole("link", { name: "PV 밖(직접 경로)" }))
      .toHaveAttribute("href", `/admin/usage?storage=${NFS}&scope=outside`);
    // 행 아무 곳(마우스) -- 경로별로 push, 필터만(by·q·sort 없이)
    await userEvent.click(within(rowOf("datasets")).getAllByRole("cell")[2]);
    await waitFor(() => expect(pathname()).toBe("/admin/usage"));
    expect(navType()).toBe("PUSH");
    expect(loc().toString()).toBe(`storage=${NFS}&cluster=dmsk8s&namespace=team-a&pvc=datasets`);
    expect(await screen.findByRole("button", { name: "team-a/datasets/dms-proof" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "team-a/datasets/proof-scan" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "team-b/models" })).toBeNull();
  });

  test("이름 링크(키보드 자리) → 권한 사용자 필터의 경로별 · 「권한 사용자 없음」은 링크가 아니고 설명이 붙는다(경로별에 그 필터가 없다)", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}`, breakdown: byStorage });
    await table();
    const none = rowOf("권한 사용자 없음");
    expect(within(none).queryByRole("link")).toBeNull();
    expect(within(none).getByRole("button", { name: "권한 사용자 없음 설명" })).toBeInTheDocument();
    expect(within(none).getByText("권한 사용자 없음")).toHaveClass("italic");
    await userEvent.click(within(rowOf("junwoo.song")).getByRole("link", { name: "junwoo.song" }));
    await waitFor(() => expect(pathname()).toBe("/admin/usage"));
    expect(loc().toString()).toBe(`storage=${NFS}&user=junwoo.song`);
    expect(await screen.findByRole("button", { name: "team-a/datasets/dms-proof" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "old/gone" })).toBeNull();
    expect(navType()).toBe("PUSH");
  });

  test("경로별 필터로 표현할 수 없는 키의 순위 행(서버 filter null)은 순위에 서되 링크가 아니다 -- 행을 눌러도 가지 않는다", async () => {
    const odd = grow({ requester_id: "bad\x01q" }, null, 1_004_500, 1);
    pvSetup({ path: `${PAGE}?storage=${NFS}&by=requester`,
              breakdown: (u) => nfsBreakdown(u, { rows: [NFS_ROWS_BY.requester[0], odd, NFS_ROWS_BY.requester[1]] }) });
    await table();
    await waitFor(() => expect(rankNames()).toEqual(["mason", "bad\x01q", "alice"]));
    const row = rowOf("bad\x01q");
    expect(within(row).queryByRole("link")).toBeNull();
    await userEvent.click(row);
    expect(pathname()).toBe(PAGE);
    expect(within(rowOf("alice")).getByRole("link", { name: "alice" })).toBeInTheDocument();
  });

  test("요청자 축: 「요청자 모름」(요청 기록 없는 scan 만) 덧붙임 · PV 밖·모름 행은 없다", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}&by=requester`,
              breakdown: (u) => nfsBreakdown(u, { extras: { outside: null, unknown: null, none: NONE_USER } }) });
    await table();
    await waitFor(() => expect(rankNames()).toEqual(["mason", "alice", "요청자 모름"]));
    expect(within(rowOf("요청자 모름")).queryByRole("link")).toBeNull();
  });

  test("StorageClass 축: 「모름」 덧붙임 = scope=unknown 링크 · 「StorageClass 없음」은 링크가 아니고 설명이 붙는다", async () => {
    const UNKNOWN_ROW = grow({}, { scope: "unknown" }, 7, 1);
    pvSetup({ path: `${PAGE}?storage=${NFS}&by=storage_class`,
              breakdown: (u) => nfsBreakdown(u, { extras: { outside: OUTSIDE_ROW, unknown: UNKNOWN_ROW, none: NONE_USER } }) });
    await table();
    await waitFor(() => expect(rankNames()).toEqual(["tb-nfs", "PV 밖(직접 경로)", "모름", "StorageClass 없음"]));
    expect(screen.getByRole("table")).toHaveAccessibleName("StorageClass별 실 사용량 순위");
    expect(within(rowOf("tb-nfs")).getByRole("link", { name: "tb-nfs" }))
      .toHaveAttribute("href", `/admin/usage?storage=${NFS}&storage_class=tb-nfs`);
    const unknown = within(rowOf("모름")).getByRole("link", { name: "모름" });
    expect(unknown).toHaveAttribute("href", `/admin/usage?storage=${NFS}&scope=unknown`);
    expect(unknown).toHaveClass("italic");
    const none = rowOf("StorageClass 없음");
    expect(within(none).queryByRole("link")).toBeNull();
    expect(within(none).getByRole("button", { name: "StorageClass 없음 설명" })).toBeInTheDocument();
    // 「모름」 행 = 경로별 「모름」 구분(push)
    await userEvent.click(within(rowOf("모름")).getAllByRole("cell")[2]);
    await waitFor(() => expect(pathname()).toBe("/admin/usage"));
    expect(loc().toString()).toBe(`storage=${NFS}&scope=unknown`);
    expect(navType()).toBe("PUSH");
  });

  test("이어 읽기로 순위가 바뀌어도 포커스는 같은 묶음의 링크에 남는다(행 key = 묶음 키 -- 다른 묶음의 링크로 바뀌지 않는다)", async () => {
    const rows = [grow({ dms_user: "alice" }, { user: "alice" }, 3000, 3), grow({ dms_user: "junwoo.song" }, { user: "junwoo.song" }, 2000, 2),
                  grow({ dms_user: "cocoa.song" }, { user: "cocoa.song" }, 1000, 1)];
    // 첫 응답은 읽는 중(1초 뒤 다시 부른다) -- 요약을 더 읽자 순서가 바뀌었다: junwoo.song 이 1위로
    pvSetup({ path: `${PAGE}?storage=${NFS}`, breakdown: (u, call) => (call === 1
      ? nfsBreakdown(u, { rows, pending_count: 1, progressed: true })
      : nfsBreakdown(u, { rows: [{ ...rows[1], total_bytes: m(9000, 2) }, rows[0], rows[2]] })) });
    await table();
    within(rowOf("junwoo.song")).getByRole("link", { name: "junwoo.song" }).focus();
    await waitFor(() => expect(rankNames()).toEqual(["junwoo.song", "alice", "cocoa.song", "PV 밖(직접 경로)", "권한 사용자 없음"]),
                  { timeout: 3000 });
    expect(document.activeElement).toHaveTextContent(/^junwoo\.song$/);
    expect(document.activeElement).toHaveAttribute("href", `/admin/usage?storage=${NFS}&user=junwoo.song`);
  });
});

describe("값 표기·상태", () => {
  test("판정 창이 넘치면 값은 부분 합(「이상」)·대상 수는 하한(「+」) · 각주에 「최근 스캔된 N개 기준」 · 값 모름은 「—」, 분모 모르면 막대 없음", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}`, windowLimit: 10000,
              breakdown: (u) => nfsBreakdown(u, {
                window_truncated: true, storage_total: null,
                rows: [grow({ dms_user: "alice" }, { user: "alice" }, 1_005_000, 3),
                       grow({ dms_user: "ghost" }, { user: "ghost" }, null, 1)] }) });
    await table();
    const alice = within(rowOf("alice")).getAllByRole("cell");
    expect(alice[1]).toHaveTextContent(/^대상 3\+개$/);                            // CompareTable TargetsCell 관용
    expect(alice[1]).toHaveAttribute("title", "합계는 최근 스캔된 10,000개 대상 기준 부분 합 · 최상위 3개 합계");
    expect(alice[2]).toHaveTextContent(/^981\.4 KiB이상$/);
    expect(alice[3]).toHaveTextContent(/^$/);                                       // 분모 모름 -- 막대 없음
    expect(alice[4]).toHaveTextContent(/^파일 31개이상$/);
    // 「이상」은 말줄임(수) 밖이라 좁은 폭에서도 남는다 · title 에도 「이상」(수가 잘리면 title 이 전체 값)
    expect(alice[4]).toHaveAttribute("title", "31개 이상");
    const clip = alice[4].querySelector(".truncate")!;
    expect(clip).toHaveTextContent(/^파일 31개$/);
    expect(within(alice[4]).getByText("이상").parentElement).toBe(alice[4]);
    const ghost = within(rowOf("ghost")).getAllByRole("cell").map((c) => c.textContent);
    expect([ghost[1], ghost[2], ghost[4], ghost[5]]).toEqual(["대상 1+개", "—", "파일 —", "hot —"]);
    const note = screen.getByText(/^행끼리 더하지 않습니다/);
    expect(note).toHaveTextContent("· 최근 스캔된 10,000개 기준");
    expect(note).toHaveTextContent("「이상」 = 값을 모르는 대상(요약 읽는 중 포함)을 뺀 합");
    expect(note).not.toHaveTextContent("막대 =");
  });

  test("막대의 분모(스토리지 합계)가 부분 합이면 각주·막대 title 이 「X 이상 대비」(근사)라고 말한다", async () => {
    // 값 모름 3(창은 안 넘침) -- 분모가 하한이라 % 는 과대일 수 있다
    pvSetup({ path: `${PAGE}?storage=${NFS}`,
              breakdown: (u) => nfsBreakdown(u, { storage_total: { total_bytes: m(NFS_TOTAL, 4, 3), summed_target_count: 7 } }) });
    await table();
    const note = screen.getByText(/^행끼리 더하지 않습니다/);
    expect(note).toHaveTextContent("· 막대 = 이 스토리지 합계 981.5 KiB 이상 대비(근사)");
    expect(within(rowOf("alice")).getAllByRole("cell")[3])
      .toHaveAttribute("title", "이 스토리지 합계 981.5 KiB 이상 대비(부분 합끼리라 근사)");
  });

  test("창이 넘쳤다는데 창 크기가 없는 부분 응답도 화면이 죽지 않는다 -- 「최근 스캔된 것 기준」(수 없이)", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}`, breakdown: (u) => {
      const { window_limit: _drop, ...b } = nfsBreakdown(u, { window_truncated: true });   // eslint-disable-line @typescript-eslint/no-unused-vars
      return b;
    } });
    await table();
    const note = screen.getByText(/^행끼리 더하지 않습니다/);
    expect(note).toHaveTextContent("· 최근 스캔된 것 기준");
    expect(note).not.toHaveTextContent("undefined");
    expect(within(rowOf("alice")).getAllByRole("cell")[1]).toHaveAttribute("title", "합계는 최근 스캔된 대상 기준 부분 합 · 최상위 3개 합계");
  });

  test("요약 읽는 중(pending > 0 · progressed): 1초 뒤 다시 불러 이어 읽고, 다 읽으면 멈춘다", async () => {
    const calls: number[] = [];
    pvSetup({ path: `${PAGE}?storage=${NFS}`, breakdown: (u, call) => {
      calls.push(call);
      return call === 1
        ? nfsBreakdown(u, { pending_count: 2, progressed: true,
                            rows: [grow({ dms_user: "alice" }, { user: "alice" }, 1_000_000, 3, { total_bytes: m(1_000_000, 1, 2) })] })
        : nfsBreakdown(u);
    } });
    await table();
    expect(await screen.findByText("요약 읽는 중 2개")).toBeInTheDocument();
    expect(within(rowOf("alice")).getAllByRole("cell")[2]).toHaveTextContent(/이상$/);
    await waitFor(() => expect(screen.queryByText(/요약 읽는 중/)).toBeNull(), { timeout: 3000 });
    expect(within(rowOf("alice")).getAllByRole("cell")[2]).toHaveTextContent(/^981\.4 KiB$/);
    await new Promise((r) => setTimeout(r, 1300));
    expect(calls).toEqual([1, 2]);
  });

  test("처음 100행만 그리고 「모두 보기 (n개)」 -- 누르면 전부, 포커스는 새로 보인 첫 행 · 서버 상한에서 잘렸으면 그 사실을 말한다", async () => {
    const many = Array.from({ length: 150 }, (_, i) =>
      grow({ requester_id: `user${String(i).padStart(3, "0")}` }, { requester: `user${i}` }, 1000 - i, 1));
    pvSetup({ path: `${PAGE}?storage=${NFS}&by=requester`,
              breakdown: (u) => (u.searchParams.get("by") === "requester"
                ? nfsBreakdown(u, { rows: many, rows_truncated: true }) : nfsBreakdown(u)) });
    const t = await table();
    expect(within(t).getAllByRole("row")).toHaveLength(1 + 100);
    expect(screen.getByText("묶음이 많아 실 사용량 상위 150개만 보입니다")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "모두 보기 (150개)" }));
    expect(within(t).getAllByRole("row")).toHaveLength(1 + 150);
    expect(screen.queryByRole("button", { name: /모두 보기/ })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(within(rowOf("user100")).getByRole("link")));
    // 기준을 바꿨다 돌아오면 다시 100행부터(펼침은 그 기준 동안만)
    await userEvent.selectOptions(bySelect(), "user");
    await waitFor(() => expect(rankNames()[0]).toBe("alice"));
    await userEvent.selectOptions(bySelect(), "requester");
    await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(1 + 100));
    expect(screen.getByRole("button", { name: "모두 보기 (150개)" })).toBeInTheDocument();
  });

  test("「모두 보기」 뒤 새로 보인 첫 행에 링크가 없으면(필터 없는 행) 포커스는 표 자체로 -- <body> 로 떨어지지 않는다", async () => {
    const many = Array.from({ length: 101 }, (_, i) =>
      grow({ requester_id: `user${String(i).padStart(3, "0")}` }, i === 100 ? null : { requester: `user${i}` }, 1000 - i, 1));
    pvSetup({ path: `${PAGE}?storage=${NFS}&by=requester`, breakdown: (u) => nfsBreakdown(u, { rows: many }) });
    const t = await table();
    await userEvent.click(screen.getByRole("button", { name: "모두 보기 (101개)" }));
    expect(within(rowOf("user100")).queryByRole("link")).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(t));
  });

  test("롤링 배포 중 옛 API(404)면 안내 한 줄 -- 오류처럼 다시 시도를 권하지 않는다", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}` });
    expect(await screen.findByText(BREAKDOWN_API_UNKNOWN)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("button", { name: "다시 시도" })).toBeNull();
    expect(bySelect()).toBeInTheDocument();
  });

  test("서버 오류는 사유 + [다시 시도] · 다른 스토리지·기준의 응답(메아리 불일치)은 보이지 않는다", async () => {
    let fail = true;
    pvSetup({ path: `${PAGE}?storage=${NFS}`, breakdown: (u) => (fail
      ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : nfsBreakdown(u)) });
    expect(await screen.findByText(/^소속별 순위를 불러오지 못했습니다/)).toBeInTheDocument();
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await table();
    expect(rankNames()[0]).toBe("alice");
    // 단추가 오류 문구째 사라졌다 -- 포커스는 <body> 가 아니라 이 스토리지의 구역(TotalsLine refocus 관용)
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("region", { name: NFS })));
  });

  test("재조회 실패의 [다시 시도]도 성공 뒤 포커스를 구역으로 옮긴다(순위는 그동안 남는다)", async () => {
    let fail = false;
    const qc = pvSetup({ path: `${PAGE}?storage=${NFS}`, breakdown: (u) => (fail
      ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : nfsBreakdown(u)) });
    await table();
    fail = true;
    await qc.refetchQueries({ queryKey: ["usage-storages", "breakdown"] });
    expect(await screen.findByText(/^순위를 새로 고치지 못했습니다/)).toBeInTheDocument();
    expect(rankNames()[0]).toBe("alice");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
    await waitFor(() => expect(screen.queryByText(/^순위를 새로 고치지 못했습니다/)).toBeNull());
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("region", { name: NFS })));
  });

  test("메아리가 다르면(다른 기준을 적용한 척) filter_mismatch 오류 -- 순위를 그리지 않는다", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}&by=requester`, breakdown: (u) => ({ ...nfsBreakdown(u), requested_by: null }) });
    expect(await screen.findByText(/서버가 적용한 필터가 화면과 다릅니다/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  test("다른 스토리지의 응답(storage_name 메아리 불일치)도 filter_mismatch -- 그 순위를 그리지 않는다", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}`, breakdown: (u) => ({ ...nfsBreakdown(u), storage_name: "cephfs-dms" }) });
    expect(await screen.findByText(/서버가 적용한 필터가 화면과 다릅니다/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  test.each(["user", "bogus"])("URL ?by=%s 는 생략과 같다 -- by 없이 부르고(캐시 한 벌) URL 은 건드리지 않는다", async (v) => {
    const seen: (string | null)[] = [];
    pvSetup({ path: `${PAGE}?storage=${NFS}&by=${v}`, breakdown: byStorage, onBreakdown: (u) => seen.push(u.searchParams.get("by")) });
    await table();
    expect(seen).toEqual([null]);
    expect(bySelect()).toHaveValue("user");
    expect(rankNames()[0]).toBe("alice");
    expect(loc().get("by")).toBe(v);
  });

  test("미등록 스토리지: 흐린 한 줄로 무엇의 기록인지와 정리할 곳(경로별)을 말한다", async () => {
    pvSetup({ path: `${PAGE}?storage=old`, unregistered: [unreg("old", { target_count: 2 })],
              breakdown: (u) => (u.searchParams.get("storage") === "old"
                ? { ...fsBreakdown(u), storage_name: "old" } : byStorage(u)) });
    await table();
    const line = screen.getByText(/^미등록 스토리지 — 스토리지 목록에서 삭제된 스토리지의 기록입니다/);
    expect(line).toHaveTextContent("미등록 스토리지 — 스토리지 목록에서 삭제된 스토리지의 기록입니다(기록 정리는 경로별에서)");
    expect(within(line).getByRole("link", { name: "경로별" })).toHaveAttribute("href", "/admin/usage?storage=old");
    // 등록 스토리지로 바꾸면 안내가 없다
    await chooseStorage(NFS);
    await waitFor(() => expect(rankNames()[0]).toBe("alice"));
    expect(screen.queryByText(/^미등록 스토리지/)).toBeNull();
  });

  test("scan 기록이 없는 스토리지는 빈 상태(기준 줄 없음) · 스토리지가 없으면 등록 안내", async () => {
    pvSetup({ path: `${PAGE}?storage=${NFS}`,
              breakdown: (u) => nfsBreakdown(u, { target_count: 0, scope: { inventory: 0, recorded: 0, outside: 0, unknown: 0 },
                                                  rows: [], extras: { outside: null, unknown: null, none: null },
                                                  storage_total: null }) });
    expect(await screen.findByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "기준" })).toBeNull();
  });

  test("등록된 스토리지도 기록도 없으면 등록 안내", async () => {
    pvSetup({ path: PAGE, storages: [], summary: [], breakdown: byStorage });
    expect(await screen.findByText("등록된 스토리지가 없습니다")).toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "기준" })).toBeNull();
  });

  test("없는 storage 딥링크는 기본 스토리지로 정리(replace)하고 안내 -- by 는 남는다", async () => {
    pvSetup({ path: `${PAGE}?storage=nope&by=requester`, breakdown: byStorage });
    expect(await screen.findByText("‘nope’ 스토리지의 사용량 기록이 없습니다 — 기본 스토리지를 보여 줍니다")).toBeInTheDocument();
    await waitFor(() => expect(loc().has("storage")).toBe(false));
    expect(loc().get("by")).toBe("requester");
    await table();
    expect(storageSelect()).toHaveValue("s:cephfs-dms");
  });
});

describe("순수 규칙", () => {
  test("shareText: 반올림 100% 인데 같지 않으면 ≈100%, 0.1% 미만 <0.1%, 분모 모름·0 이면 null", () => {
    expect(shareText(10, 10)).toBe("100%");
    expect(shareText(9999, 10000)).toBe("≈100%");
    expect(shareText(1, 10000)).toBe("<0.1%");
    expect(shareText(0, 10)).toBe("0%");
    expect(shareText(5, 10)).toBe("50%");
    expect(shareText(12, 10)).toBe("120%");                                         // scan 시점이 달라 클 수 있다 -- 숨기지 않는다
    expect(shareText(1, null)).toBeNull();
    expect(shareText(1, 0)).toBeNull();
    expect(shareText(null, 10)).toBeNull();
  });

  test("pathsHref: 경로별 URL 키 순서 · 걸 것이 없거나 모르는 키가 있으면 null(덜 걸린 목록으로 보내지 않는다)", () => {
    expect(pathsHref("s", { pvc: "p", namespace: "n", cluster: "c" })).toBe("/admin/usage?storage=s&cluster=c&namespace=n&pvc=p");
    expect(pathsHref("", { requester: "a b" })).toBe("/admin/usage?storage=&requester=a+b");
    expect(pathsHref("s", { scope: "outside" })).toBe("/admin/usage?storage=s&scope=outside");
    expect(pathsHref("s", null)).toBeNull();
    expect(pathsHref("s", {})).toBeNull();
    expect(pathsHref("s", { user: "" })).toBeNull();
    expect(pathsHref("s", { user: "a", owner: "x" } as UsageAttrFilter)).toBeNull();
    expect(pathsHref("s", { scope: "bogus" } as unknown as UsageAttrFilter)).toBeNull();
  });

  test("groupName: 축의 키 필드 + 부속(PV 이름 = namespace (StorageClass) [· 클러스터], namespace = 클러스터 여럿일 때만)", () => {
    const r = grow({ cluster: "c1", namespace: "ns", pvc: "p", storage_class: "sc" }, null, 1, 1);
    expect(groupName("pvc", r, false)).toEqual({ name: "p", sub: "ns (sc)" });
    expect(groupName("pvc", r, true)).toEqual({ name: "p", sub: "ns (sc) · c1" });
    expect(groupName("namespace", r, false)).toEqual({ name: "ns", sub: null });
    expect(groupName("namespace", r, true)).toEqual({ name: "ns", sub: "c1" });
    expect(groupName("cluster", r, true)).toEqual({ name: "c1", sub: null });
    expect(groupName("user", grow({ dms_user: "u" }, null, 1, 1), true)).toEqual({ name: "u", sub: null });
    expect(groupName("requester", grow({}, null, 1, 1), true)).toEqual({ name: null, sub: null });
  });

  test("rowKeys: 축의 묶음 키(값만 바뀌어도 같은 key) · 키 필드가 빠져 겹치면 차례를 붙인다", () => {
    const a = grow({ cluster: "c1", namespace: "ns", pvc: "p", storage_class: "sc" }, null, 1, 1);
    const b = grow({ cluster: "c2", namespace: "ns", pvc: "p", storage_class: "sc" }, null, 1, 1);
    expect(rowKeys("pvc", [a, b])).toEqual(['["c1","ns","p"]', '["c2","ns","p"]']);
    expect(rowKeys("pvc", [{ ...a, total_bytes: m(99, 1) }])).toEqual(rowKeys("pvc", [a]));
    expect(rowKeys("namespace", [a, b])).toEqual(['["c1","ns"]', '["c2","ns"]']);
    expect(rowKeys("requester", [grow({}, null, 1, 1), grow({}, null, 2, 1)])).toEqual(["[null]", "[null]#1"]);
  });
});
