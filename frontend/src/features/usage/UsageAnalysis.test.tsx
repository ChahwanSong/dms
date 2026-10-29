import { cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { expect, test, vi } from "vitest";
import { hotRatio, pointEpoch, stackLayout, staleClass } from "./UsageAnalysis";
import type { UsagePoint } from "../../lib/types";
import { downloadCsv } from "../../lib/csvExport";
import { USAGE_API_UNKNOWN, USAGE_FILTER_MISMATCH } from "./useUsage";
import {
  FS_ROWS, NFS, NFS_ROWS, OUTSIDE, TARGETS, buckets, chooseStorage, closeSheet, echoOf, emptyTotals, filterGroup, installServer,
  inv, isoAgo, loc, m, navType, nrow, optionTexts, panelOf, pathname, point, pvSetup, select, server, setup, sheetOf, st,
  storageSelect, strip, sum, totalsOf, unreg, vol, type Setup,
} from "./usageTestKit";

// 사용량 분석 「경로별」(/admin/usage) -- 스토리지 select·목록·검색·정렬·CSV·합계 줄·필터. 경로 상세 시트는 TargetSheet.test,
// 스토리지별 탭은 StoragesPage.test(공용 픽스처는 usageTestKit).

// 다운로드(Blob·a.click)는 jsdom 밖 동작이라 가로챈다 -- 직렬화(toCsv)는 진짜를 쓴다.
vi.mock("../../lib/csvExport", async (orig) => ({
  ...(await orig<typeof import("../../lib/csvExport")>()), downloadCsv: vi.fn() }));

installServer();

// ---- 순수 헬퍼 ----


test("hotRatio: ≤180일(HOT_AGE_MAX_DAYS) 버킷 비중 / 전체 0·bytes 결측은 null(모름 ≠ 0)", () => {
  // 기준 180일(2026-08-24 사용자 결정, 7일에서 상향): [91,180] 까지 hot(30+30),
  // [181,365] 는 cold(40) -- 60/100. 경계값 180 자체가 hot 에 포함됨을 고정한다.
  const b = [{ bucket: "a", min_age_days: 0, max_age_days: 7, bytes: 30 },
             { bucket: "b", min_age_days: 91, max_age_days: 180, bytes: 30 },
             { bucket: "c", min_age_days: 181, max_age_days: 365, bytes: 40 }];
  expect(hotRatio(b)).toBeCloseTo(0.6);
  expect(hotRatio(buckets([0, 0, 0]))).toBeNull();       // 전체 0: 비율 정의 불가
  expect(hotRatio(buckets([10, undefined]))).toBeNull(); // bytes 결측: 모름
  // bytes 가 실렸는데 나이 필드가 없으면 hot/cold 판정 근거가 없다 -- cold 로
  // 접으면 비율이 조용히 내려간다(리뷰 확인). 0 바이트 무나이 버킷은 무해.
  expect(hotRatio(buckets([10], false))).toBeNull();
  expect(hotRatio([{ bucket: "z", bytes: 0 } as never,
                   ...buckets([10])])).toBeCloseTo(1);
  expect(hotRatio([])).toBeNull();
  expect(hotRatio(undefined)).toBeNull();
});

test("stackLayout: 비중 % / 전체 0·결측은 null", () => {
  expect(stackLayout(buckets([30, 70]))!.map((s) => s.pct)).toEqual([30, 70]);
  expect(stackLayout(buckets([0, 0]))).toBeNull();
  expect(stackLayout(buckets([1, undefined]))).toBeNull();
});

test("pointEpoch: 리포트 생성 시각 우선, 결측이면 완료 시각, 둘 다 없으면 null", () => {
  const base = { job_id: "j", request_id: "r", summary: {}, time_histograms: {},
                 requester: null, total_bytes: null } as Partial<UsagePoint>;
  expect(pointEpoch({ ...base, generated_at_epoch: 5,
                      finished_at: "2026-08-23T00:00:00Z" } as UsagePoint)).toBe(5);
  expect(pointEpoch({ ...base, generated_at_epoch: null,
                      finished_at: "1970-01-01T00:01:40Z" } as UsagePoint)).toBe(100);
  expect(pointEpoch({ ...base, generated_at_epoch: null,
                      finished_at: null } as UsagePoint)).toBeNull();
});

// ---- 머리 · 스토리지 select(2026-10-29 -- 예전 스토리지 탭 줄) ----

test("처음부터 스토리지 하나: 기본 스토리지가 골라지고 목록 요청에 늘 storage 가 실린다(「전체」·스토리지 열 없음) · 머리 + 하위 탭", async () => {
  const seen: URL[] = [];
  setup({ onTargets: (u) => seen.push(u) });
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(storageSelect()).toHaveValue("s:cephfs-dms");
  expect(optionTexts(storageSelect())).toEqual(["cephfs-dms · cephfs · 대상 2개"]);
  expect(within(storageSelect()).queryByRole("option", { name: /전체/ })).toBeNull();
  expect(screen.getByRole("heading", { name: "사용량 분석", level: 1 })).toBeInTheDocument();
  // 하위 탭: 경로별(지금 -- end 매칭) · 스토리지별
  const nav = screen.getByRole("navigation", { name: "사용량 분석 하위 메뉴" });
  expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["경로별", "스토리지별"]);
  expect(within(nav).getByRole("link", { name: "경로별" })).toHaveAttribute("aria-current", "page");
  expect(within(nav).getByRole("link", { name: "스토리지별" })).toHaveAttribute("href", "/admin/usage/storages");
  expect(seen.length).toBeGreaterThan(0);
  for (const u of seen) expect(u.searchParams.get("storage")).toBe("cephfs-dms");
  // 표: 6열(경로·실 사용량·파일 수·hot 비율·스캔 횟수·최근 스캔) -- 스토리지 열은 select 가 말한다
  const heads = within(screen.getByRole("table")).getAllByRole("columnheader");
  expect(heads).toHaveLength(6);
  ["경로", "실 사용량", "파일 수", "hot 비율", "스캔 횟수", "최근 스캔"].forEach((label, i) =>
    expect(heads[i].textContent).toMatch(new RegExp(`^${label}`)));
  // 범위 줄: select 옆 메타 한 줄(마운트 · 타입 · 대상 수 · 최근 스캔) -- 이름은 select 가 말하므로 다시 쓰지 않는다
  expect(screen.getByText("/cephfs").closest("p")).toHaveTextContent(/^\/cephfscephfs대상 2개최근 스캔 3일 전$/);
  // 패널: 스토리지 이름의 구역(예전 탭 패널)
  expect(panelOf("cephfs-dms")).toContainElement(screen.getByRole("table"));
  // 기본 스토리지는 파생값이다 -- URL 에 쓰지 않는다
  expect(loc().get("storage")).toBeNull();
});

test("기본 스토리지: 기록이 있는 등록 스토리지 중 이름순 첫째(기록 0 은 건너뜀) → 없으면 기록 있는 미등록 → 첫 스토리지", async () => {
  setup({
    storages: [st("a-empty"), st("b-full"), st("c-full")],
    summary: [sum("a-empty", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null }),
              sum("b-full"), sum("c-full"), sum("old", { registered: false, source: "last_known" })],
    unregistered: [unreg("old")],
    targets: { "b-full": [{ ...TARGETS[0], storage_name: "b-full" }] },
  });
  await waitFor(() => expect(storageSelect()).toHaveValue("s:b-full"));
  // 기록 0 은 「대상 0개」(진짜 0 -- 요약이 왔다). 순서: 등록 이름순 → 미등록, 미등록은 타입 자리에 「미등록」(수는 요약이 우선)
  expect(optionTexts(storageSelect())).toEqual(
    ["a-empty · cephfs · 대상 0개", "b-full · cephfs · 대상 2개", "c-full · cephfs · 대상 2개", "old · 미등록 · 대상 2개"]);
});

test("기본 스토리지: 기록 있는 등록 스토리지가 없으면 기록 있는 미등록", async () => {
  setup({
    storages: [st("a-empty")],
    summary: [sum("a-empty", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null }),
              sum("old", { registered: false, source: "last_known" })],
    unregistered: [unreg("old")],
    targets: { old: [{ ...TARGETS[0], storage_name: "old", target: "legacy" }] },
  });
  expect(await screen.findByRole("button", { name: "legacy" })).toBeInTheDocument();
  expect(storageSelect()).toHaveValue("s:old");
});

test("스토리지 집합 = 등록 ∪ 요약 ∪ 미등록(실패 기록만 남은 이름 포함) -- 묶음은 optgroup 「등록」 | 「미등록 — 기록만 남음」", async () => {
  setup({
    storages: [st("cephfs-dms"), st("zz-new")],
    summary: [sum("cephfs-dms"), sum("zz-new", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null }),
              sum("old-gpfs", { registered: false, source: "last_known", target_count: 3 })],
    unregistered: [unreg("old-gpfs", { target_count: 3 }),
                   unreg("ghost", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })],
  });
  await waitFor(() => expect(optionTexts(storageSelect())).toContain("ghost · 미등록 · 대상 0개"));
  const groups = within(storageSelect()).getAllByRole("group");
  expect(groups.map((g) => g.getAttribute("label"))).toEqual(["등록", "미등록 — 기록만 남음"]);
  expect(optionTexts(groups[0])).toEqual(["cephfs-dms · cephfs · 대상 2개", "zz-new · cephfs · 대상 0개"]);
  expect(optionTexts(groups[1])).toEqual(["ghost · 미등록 · 대상 0개", "old-gpfs · 미등록 · 대상 3개"]);
});

test("요약 도착 전엔 대상 수를 그리지 않는다(모름 ≠ 0) -- 등록 스토리지는 먼저 옵션이 되고 값은 「고르는 중」, 패널은 자리표시", async () => {
  setup({ summary: "slow" });
  await waitFor(() => expect(optionTexts(storageSelect())).toEqual(["스토리지 고르는 중…", "cephfs-dms · cephfs"]));
  expect(storageSelect()).toHaveValue("");                       // 기본 스토리지는 기록 여부를 알아야 고른다
  expect(screen.queryByRole("table")).toBeNull();
  expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
});

test("요약을 못 받으면 등록 목록 ∪ scan 기록 이름으로 집합만 그린다(대상 수 모름) + 한 줄 알림", async () => {
  setup({ summary: "error", storages: [st("a-empty"), st("cephfs-dms")], scanStorages: ["cephfs-dms", "old-gpfs"],
          unregistered: [unreg("old-gpfs")] });
  // 대체 원천으로 집합이 그려지면 흐린 한 줄로 「건수 모름」만 -- 원문 오류(옛 API 의 SPA index.html 이면 파서 문장)는 안 보인다
  expect(await screen.findByText("스토리지별 대상 수를 불러오지 못해 건수 없이 표시합니다")).toBeInTheDocument();
  expect(screen.queryByText(/internal/)).toBeNull();
  // 기록 있는 등록 스토리지가 기본(scan 기록 이름으로 판정) · 미등록 목록은 규모를 안다
  await waitFor(() => expect(storageSelect()).toHaveValue("s:cephfs-dms"));
  expect(optionTexts(storageSelect())).toEqual(["a-empty · cephfs", "cephfs-dms · cephfs", "old-gpfs · 미등록 · 대상 1개"]);
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
});

test("옛 API(요약 경로가 SPA index.html 200)여도 대체 집합 + 흐린 한 줄 -- 파서 문장을 그대로 띄우지 않는다", async () => {
  setup({ summary: "html", storages: [st("cephfs-dms")], scanStorages: ["cephfs-dms"] });
  expect(await screen.findByText("스토리지별 대상 수를 불러오지 못해 건수 없이 표시합니다")).toBeInTheDocument();
  expect(screen.queryByText(/Unexpected token|JSON/)).toBeNull();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
});

test("요약 재조회로 대상 수가 바뀌어도 보던 기본 스토리지는 그대로(붙잡은 기본 스토리지)", async () => {
  const qc = setup({ storages: [st("a"), st("b")],
                     summary: [sum("a", { target_count: 0 }), sum("b")],
                     targets: { b: [{ ...TARGETS[0], storage_name: "b", target: "b-row" }] } });
  expect(await screen.findByRole("button", { name: "b-row" })).toBeInTheDocument();
  expect(storageSelect()).toHaveValue("s:b");
  // 이제 a 에도 기록이 생겼다 -- 다시 계산하면 이름순 첫째(a)가 기본이지만 보던 b 를 바꾸지 않는다
  server.use(http.get("/api/admin/usage/storages", () =>
    HttpResponse.json({ window_limit: 10000, storages: [sum("a", { target_count: 3 }), sum("b")] })));
  await qc.invalidateQueries({ queryKey: ["usage-storages"] });
  await waitFor(() => expect(optionTexts(storageSelect())).toContain("a · cephfs · 대상 3개"));
  expect(storageSelect()).toHaveValue("s:b");
  expect(screen.getByRole("button", { name: "b-row" })).toBeInTheDocument();
});

test("받아 둔 요약이 있으면 재조회 실패에도 패널을 그대로 두고 그 사실만 말한다", async () => {
  const qc = setup();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  server.use(http.get("/api/admin/usage/storages", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  await qc.invalidateQueries({ queryKey: ["usage-storages"] });
  expect(await screen.findByText(/스토리지별 요약을 새로 고치지 못했습니다/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(storageSelect()).toHaveValue("s:cephfs-dms");
  expect(optionTexts(storageSelect())).toEqual(["cephfs-dms · cephfs · 대상 2개"]);
});

test("빈 이름(변조·구형 행): 「(이름 없음)」으로 보이고, 목록 요청은 storage= (빈 이름)로 나간다", async () => {
  const seen: (string | null)[] = [];
  setup({ storages: [st("cephfs-dms")],
          summary: [sum("cephfs-dms"), sum("", { registered: false, source: null, backend_type: null, mount_path: null,
                                                 target_count: 1 })],
          targets: { "cephfs-dms": TARGETS, "": [{ ...TARGETS[0], storage_name: "", target: "blank-row" }] },
          onTargets: (u) => seen.push(u.searchParams.get("storage")) });
  await waitFor(() => expect(optionTexts(storageSelect())).toContain("(이름 없음) · 미등록 · 대상 1개"));
  await chooseStorage("");
  expect(await screen.findByRole("button", { name: "blank-row" })).toBeInTheDocument();
  expect(seen).toContain("");
  expect(panelOf("(이름 없음)")).toBeInTheDocument();
});

test("?storage 딥링크는 그 스토리지를 고르고 목록을 바로 그 스토리지로 부른다", async () => {
  const seen: (string | null)[] = [];
  setup({ path: "/admin/usage?storage=cephfs-b",
          storages: [st("cephfs-a"), st("cephfs-b")], summary: [sum("cephfs-a"), sum("cephfs-b")],
          targets: { "cephfs-b": [{ ...TARGETS[0], storage_name: "cephfs-b", target: "b-only" }] },
          onTargets: (u) => seen.push(u.searchParams.get("storage")) });
  expect(await screen.findByRole("button", { name: "b-only" })).toBeInTheDocument();
  expect(storageSelect()).toHaveValue("s:cephfs-b");
  expect(seen).not.toContain("cephfs-a");
});

test("스토리지 바꾸기: 300ms 디바운스 뒤 push -- target 은 버리고 필터·q·sort·order 는 남긴다 · 같은 값은 아무것도 바꾸지 않는다", async () => {
  const seen: URL[] = [];
  const rows = (name: string) => TARGETS.map((r) => ({ ...r, storage_name: name, _requesters: ["mason"] }));
  setup({ path: "/admin/usage?storage=cephfs-dms&q=art&sort=bytes&order=asc&requester=mason",
          storages: [st("cephfs-dms"), st("other")], summary: [sum("cephfs-dms"), sum("other")],
          targets: { "cephfs-dms": rows("cephfs-dms"), other: rows("other") }, onTargets: (u) => seen.push(u) });
  await screen.findByRole("button", { name: "artifacts" });
  await userEvent.selectOptions(storageSelect(), "s:cephfs-dms");                  // 지금 값 그대로
  await new Promise((r) => setTimeout(r, 400));
  expect(navType()).toBe("POP");                                                    // 아무 이동도 없었다
  await userEvent.selectOptions(storageSelect(), "s:other");
  expect(storageSelect()).toHaveValue("s:other");                                   // 고른 값은 바로 보이고
  expect(loc().get("storage")).toBe("cephfs-dms");                                  // URL 은 디바운스 뒤에
  await waitFor(() => expect(loc().get("storage")).toBe("other"));
  expect(navType()).toBe("PUSH");
  expect([loc().get("q"), loc().get("sort"), loc().get("order"), loc().get("requester"), loc().get("target")])
    .toEqual(["art", "bytes", "asc", "mason", null]);
  await waitFor(() => expect(seen[seen.length - 1].searchParams.get("storage")).toBe("other"));
  const last = seen[seen.length - 1];
  expect([last.searchParams.get("q"), last.searchParams.get("sort"), last.searchParams.get("order"),
          last.searchParams.get("requester")]).toEqual(["art", "bytes", "asc", "mason"]);
  expect(screen.getByLabelText("경로·PV 이름 검색")).toHaveValue("art");
  // 같은 경로 이름이 다른 스토리지에 있어도 시트는 열리지 않는다
  expect(await screen.findByRole("button", { name: "artifacts" })).toHaveAttribute("aria-haspopup", "dialog");
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("디바운스: 300ms 안에 연달아 바꾸면 마지막 하나만 push 한다(그 사이 스토리지의 목록 요청도 없다)", async () => {
  const seen: (string | null)[] = [];
  setup({ storages: [st("a"), st("b"), st("c")], summary: [sum("a"), sum("b"), sum("c")],
          onTargets: (u) => seen.push(u.searchParams.get("storage")) });
  await waitFor(() => expect(storageSelect()).toHaveValue("s:a"));
  await userEvent.selectOptions(storageSelect(), "s:b");
  await userEvent.selectOptions(storageSelect(), "s:c");
  expect(storageSelect()).toHaveValue("s:c");
  await waitFor(() => expect(loc().get("storage")).toBe("c"));
  await waitFor(() => expect(seen).toContain("c"));
  expect(seen).not.toContain("b");
  expect(navType()).toBe("PUSH");
});

test("디바운스 중에 아직 남은 옛 스토리지의 행을 누르면: select 를 떠나는 순간 스토리지가 바뀌고, 옛 스토리지 시트는 열리지 않는다", async () => {
  // 리뷰(2026-10-29): 예전엔 옛 스토리지(cephfs-dms)로 시트가 열렸다가 300ms 뒤 스토리지 push 에 저절로 닫히고, 뒤로 가기가 그 옛
  // 시트를 다시 열었다(히스토리 [열기 전, 옛 시트, 새 스토리지]).
  pvSetup();
  const old = await screen.findByRole("button", { name: "artifacts" });
  expect(storageSelect()).toHaveValue("s:cephfs-dms");
  await userEvent.selectOptions(storageSelect(), `s:${NFS}`);
  expect(loc().get("storage")).toBeNull();                                          // 아직 디바운스 중
  await userEvent.click(old);                                                       // 300ms 안 -- select 를 떠난다
  expect(loc().get("storage")).toBe(NFS);                                           // 기다리지 않고 바로 push
  expect(navType()).toBe("PUSH");
  expect(loc().get("target")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(await screen.findByRole("button", { name: "team-a/datasets/dms-proof" })).toBeInTheDocument();
  await new Promise((r) => setTimeout(r, 400));                                     // 남은 타이머가 다시 push 하지 않는다
  expect([loc().get("storage"), loc().get("target"), navType()]).toEqual([NFS, null, "PUSH"]);
  expect(screen.queryByRole("dialog")).toBeNull();
  // 뒤로 가기는 열기 전(기본 스토리지)으로 -- 옛 스토리지의 시트 항목이 없다
  fireEvent.click(screen.getByTestId("back"));
  await waitFor(() => expect(storageSelect()).toHaveValue("s:cephfs-dms"));
  expect([loc().get("storage"), loc().get("target")]).toEqual([null, null]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("반영되는 순간에도 select 가 옛 스토리지로 되돌아가지 않는다 -- 고른 값은 라우터의 위치 전환과 같은 커밋에서 거둔다(디바운스·blur)", async () => {
  // 2차 리뷰(2026-10-29): react-router 7 은 위치를 startTransition 으로 바꾸는데 기다리던 값을 긴급 갱신으로 지워, 그 사이 한 번의
  // 커밋이 옛 스토리지를 그렸다(고른 값이 취소된 것처럼 되돌아갔다가 표 교체가 끝나야 새 값이 됐다).
  setup({ storages: [st("a-first"), st("cephfs-dms")], summary: [sum("a-first", { target_count: 0 }), sum("cephfs-dms")] });
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(storageSelect()).toHaveValue("s:cephfs-dms");                              // 기본 = 기록 있는 첫 등록
  // React 가 스토리지 select 에 커밋한 선택을 모두 적는다(제어 select 는 커밋마다 고른 option 의 selected 를 쓴다)
  const writes: string[] = [];
  const desc = Object.getOwnPropertyDescriptor(HTMLOptionElement.prototype, "selected")!;
  Object.defineProperty(HTMLOptionElement.prototype, "selected", {
    configurable: true, get: desc.get,
    set(this: HTMLOptionElement, v: boolean) {
      if (v && this.closest("select")?.getAttribute("aria-label") === "스토리지") writes.push(this.value);
      desc.set!.call(this, v);
    },
  });
  try {
    await userEvent.selectOptions(storageSelect(), "s:a-first");                    // 300ms 디바운스 뒤 반영
    await waitFor(() => expect(loc().get("storage")).toBe("a-first"));
    expect(await screen.findByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeInTheDocument();
    expect(writes).toContain("s:a-first");
    expect(writes).not.toContain("s:cephfs-dms");
    writes.length = 0;
    await userEvent.selectOptions(storageSelect(), "s:cephfs-dms");                 // select 를 떠나면 바로 반영
    fireEvent.blur(storageSelect());
    await waitFor(() => expect(loc().get("storage")).toBe("cephfs-dms"));
    expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
    expect(writes).toContain("s:cephfs-dms");
    expect(writes).not.toContain("s:a-first");
  } finally {
    Object.defineProperty(HTMLOptionElement.prototype, "selected", desc);
  }
  expect(storageSelect()).toHaveValue("s:cephfs-dms");
});

test("URL 의 storage 가 집합에 없으면(데이터 도착 후) 기본 스토리지 + 흐린 안내 한 줄(알림 자리), URL 은 replace 로 정리", async () => {
  setup({ path: "/admin/usage?storage=typo-name&target=x", storages: [st("cephfs-dms"), st("other")],
          summary: [sum("cephfs-dms"), sum("other")] });
  expect(await screen.findByText("‘typo-name’ 스토리지의 사용량 기록이 없습니다 — 기본 스토리지를 보여 줍니다"))
    .toBeInTheDocument();
  await waitFor(() => expect(storageSelect()).toHaveValue("s:cephfs-dms"));
  await waitFor(() => expect(loc().get("storage")).toBeNull());
  expect(navType()).toBe("REPLACE");
  expect(loc().get("target")).toBeNull();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  // 다른 스토리지를 고르면 안내를 거둔다
  await chooseStorage("other");
  expect(screen.queryByText(/typo-name/)).toBeNull();
});

test("없는 storage 안내는 뒤로·앞으로 가기로 집합 안의 스토리지 항목에 돌아오면 거둔다 -- 기본이 아닌 스토리지를 보이며 「기본을 보여 줍니다」라 하지 않는다", async () => {
  // 2차 리뷰(2026-10-29): 예전엔 select 만 안내를 지워, 앞으로 가기로 z-other 를 보이는 동안에도 「‘gone’ … 기본 스토리지를 보여
  // 줍니다」가 남았다.
  setup({ entries: ["/admin/usage?storage=gone", "/admin/usage?storage=z-other"],
          storages: [st("cephfs-dms"), st("z-other")], summary: [sum("cephfs-dms"), sum("z-other")] });
  await waitFor(() => expect(storageSelect()).toHaveValue("s:z-other"));
  fireEvent.click(screen.getByTestId("back"));                                      // 삭제된 스토리지의 옛 링크
  expect(await screen.findByText("‘gone’ 스토리지의 사용량 기록이 없습니다 — 기본 스토리지를 보여 줍니다"))
    .toBeInTheDocument();
  await waitFor(() => expect([loc().get("storage"), navType()]).toEqual([null, "REPLACE"]));
  expect(storageSelect()).toHaveValue("s:cephfs-dms");
  fireEvent.click(screen.getByTestId("forward"));
  await waitFor(() => expect(loc().get("storage")).toBe("z-other"));
  await waitFor(() => expect(storageSelect()).toHaveValue("s:z-other"));
  expect(screen.queryByText(/‘gone’/)).toBeNull();
});

test("스토리지를 바꾸는 동안 이전 스토리지의 행을 새 스토리지 아래 보이지 않는다(자리표시는 같은 스토리지 안에서만)", async () => {
  setup({ storages: [st("cephfs-dms"), st("slow")], summary: [sum("cephfs-dms"), sum("slow")], slowTargets: "slow" });
  await screen.findByRole("button", { name: "artifacts" });
  await chooseStorage("slow");
  expect(screen.queryByRole("button", { name: "artifacts" })).toBeNull();
  expect(within(panelOf("slow")).getByText("불러오는 중…")).toBeInTheDocument();
});

test("기록 0개 등록 스토리지: 빈 상태 안내", async () => {
  setup({ path: "/admin/usage?storage=fresh", storages: [st("cephfs-dms"), st("fresh")],
          summary: [sum("cephfs-dms"), sum("fresh", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })] });
  expect(await screen.findByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeInTheDocument();
  expect(screen.getByText("대상 0개")).toBeInTheDocument();                         // 메타 줄(진짜 0)
  expect(screen.queryByRole("table")).toBeNull();
  // 기록이 없으면 검색·CSV 는 할 일이 없다 -- 빈 상태 안내만
  expect(screen.queryByLabelText("경로·PV 이름 검색")).toBeNull();
  expect(screen.queryByRole("button", { name: "CSV 내보내기" })).toBeNull();
});

test("스토리지도 기록도 없으면 등록 안내", async () => {
  setup({ storages: [], summary: [] });
  expect(await screen.findByText("등록된 스토리지가 없습니다")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "스토리지 화면으로" })).toHaveAttribute("href", "/admin/storages");
  expect(screen.queryByRole("combobox", { name: "스토리지" })).toBeNull();
});

// ---- 미등록 스토리지 ----

test("미등록 스토리지: 패널 맨 위 경고 띠(마지막 설정·규모·「기록 정리」) + 같은 표, 메타 줄 대신", async () => {
  setup({ path: "/admin/usage?storage=old-gpfs",
          summary: [sum("cephfs-dms"), sum("old-gpfs", { registered: false, source: "last_known", target_count: 3 })],
          unregistered: [unreg("old-gpfs", { target_count: 3, succeeded_scan_count: 5, single_count: 4, request_count: 6,
                                            batches: [{ batch_id: "b".repeat(32), request_count: 2 }] })],
          targets: { "old-gpfs": [{ ...TARGETS[0], storage_name: "old-gpfs", target: "proj" }] } });
  const note = await screen.findByRole("note", { name: "미등록 스토리지" });
  expect(note).toHaveTextContent("스토리지 목록에서 삭제된 스토리지의 기록입니다");
  expect(note).toHaveTextContent("마지막 설정 gpfs · /old");
  expect(note).toHaveTextContent("대상 3개 · 성공 scan 5개 · 정리할 작업 6개(배치 1개 포함) · 최근 스캔 200일 전");
  expect(within(note).getByRole("button", { name: "old-gpfs 기록 정리" })).toBeEnabled();
  expect(await screen.findByRole("button", { name: "proj" })).toBeInTheDocument();
  expect(storageSelect()).toHaveValue("s:old-gpfs");
  // 띠가 메타 줄 대신이다(마운트 경로 줄이 따로 없다) · 행마다 붙던 「미등록」 배지는 없다
  expect(screen.queryByTitle("마운트 경로")).toBeNull();
  expect(within(screen.getByRole("table")).queryByText("미등록")).toBeNull();
});

test("미등록 스토리지: 실패·취소 기록만 남은 이름은 띠 + 「성공한 scan 기록이 없습니다」", async () => {
  setup({ path: "/admin/usage?storage=ghost",
          unregistered: [unreg("ghost", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null,
                                          request_count: 2, single_count: 2 })] });
  expect(await screen.findByText("성공한 scan 기록이 없습니다")).toBeInTheDocument();
  expect(screen.getByRole("note", { name: "미등록 스토리지" })).toHaveTextContent("정리할 작업 2개");
});

// ---- URL 의 검색·정렬(2026-10-29 -- 예전엔 페이지 상태) · 탭 왕복 ----

test("q·sort·order 딥링크: 요청에 실리고 검색칸·정렬 머리가 그 값 -- 기본 정렬(last_scan·desc)은 URL 에서 생략", async () => {
  const seen: URL[] = [];
  setup({ path: "/admin/usage?storage=cephfs-dms&q=art&sort=hot&order=asc", onTargets: (u) => seen.push(u), filterQ: true });
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(screen.getByLabelText("경로·PV 이름 검색")).toHaveValue("art");
  expect(screen.getByRole("button", { name: "hot 비율 정렬: 낮은 순" }).closest("th")).toHaveAttribute("aria-sort", "ascending");
  for (const u of seen) {
    expect([u.searchParams.get("q"), u.searchParams.get("sort"), u.searchParams.get("order")]).toEqual(["art", "hot", "asc"]);
  }
  // 정렬은 replace -- 다른 열은 내림차순부터(기본 order 는 생략), 최근 스캔 내림차순(기본)이면 둘 다 생략
  await userEvent.click(screen.getByRole("button", { name: "실 사용량 정렬" }));
  expect(navType()).toBe("REPLACE");
  expect([loc().get("sort"), loc().get("order")]).toEqual(["bytes", null]);
  await userEvent.click(screen.getByRole("button", { name: "최근 스캔 정렬" }));
  expect([loc().get("sort"), loc().get("order")]).toEqual([null, null]);
  await userEvent.click(screen.getByRole("button", { name: "최근 스캔 정렬: 최신순" }));
  expect([loc().get("sort"), loc().get("order")]).toEqual([null, "asc"]);
  // 모르는 값은 기본으로 읽는다(서버가 422 로 거절할 값을 싣지 않는다)
  cleanup();
  seen.length = 0;
  setup({ path: "/admin/usage?storage=cephfs-dms&sort=nope&order=up", onTargets: (u) => seen.push(u) });
  expect(await screen.findByRole("button", { name: "최근 스캔 정렬: 최신순" })).toBeInTheDocument();
  expect([seen[0].searchParams.get("sort"), seen[0].searchParams.get("order")]).toEqual(["last_scan", "desc"]);
});

test("검색은 300ms 뒤 URL q(replace) -- 탭 왕복은 마지막 쿼리를 복원하고, 뒤로 가기로 바뀐 q 를 검색칸이 따라간다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms", filterQ: true,
          storages: [st("cephfs-dms"), st("mirror")], summary: [sum("cephfs-dms"), sum("mirror")],
          targets: { "cephfs-dms": TARGETS, mirror: TARGETS.map((r) => ({ ...r, storage_name: "mirror" })) } });
  await screen.findByRole("button", { name: "artifacts" });
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "tea");
  expect(loc().get("q")).toBeNull();                                                // 디바운스 전
  await waitFor(() => expect(loc().get("q")).toBe("tea"));
  expect(navType()).toBe("REPLACE");
  expect(await screen.findByRole("button", { name: "team" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "artifacts" })).toBeNull();
  // 앞뒤 공백은 싣지 않는다
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "m ");
  await waitFor(() => expect(loc().get("q")).toBe("team"));
  // 탭 왕복: 스토리지별로 갔다가 「경로별」 링크로 돌아오면 마지막 쿼리(스토리지·검색)가 그대로다
  await userEvent.click(screen.getByRole("link", { name: "스토리지별" }));
  await waitFor(() => expect(pathname()).toBe("/admin/usage/storages"));
  await userEvent.click(screen.getByRole("link", { name: "경로별" }));
  await waitFor(() => expect(pathname()).toBe("/admin/usage"));
  expect([loc().get("storage"), loc().get("q")]).toEqual(["cephfs-dms", "team"]);
  expect(screen.getByLabelText("경로·PV 이름 검색")).toHaveValue("team");
  // 뒤로 가기(같은 화면 안): 다른 스토리지(push)에서 검색을 지운 뒤 뒤로 가면 그 전 항목의 q 로 검색칸이 돌아간다
  await chooseStorage("mirror");
  await userEvent.clear(screen.getByLabelText("경로·PV 이름 검색"));
  await waitFor(() => expect(loc().get("q")).toBeNull());
  fireEvent.click(screen.getByTestId("back"));
  await waitFor(() => expect([loc().get("storage"), loc().get("q")]).toEqual(["cephfs-dms", "team"]));
  await waitFor(() => expect(screen.getByLabelText("경로·PV 이름 검색")).toHaveValue("team"));
  expect(await screen.findByRole("button", { name: "team" })).toBeInTheDocument();
});

test("기본 스토리지를 보다가 탭을 오가도 같은 스토리지로 돌아온다 -- 그 사이 요약이 바뀌어 기본이 달라져도(스토리지별의 「현재」 행과 같은 것)", async () => {
  // 2차 리뷰(2026-10-29): 붙잡은 기본 스토리지는 경로별 페이지 상태라 탭 왕복으로 사라졌고, 그 사이 a-first 가 첫 scan 을 얻으면
  // 다시 파생한 기본(a-first)이 열렸다 -- 방금 스토리지별 표가 「현재」로 칠한 cephfs-dms 가 아니라.
  const qc = setup({ storages: [st("a-first"), st("cephfs-dms")],
                     summary: [sum("a-first", { target_count: 0 }), sum("cephfs-dms")] });
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(storageSelect()).toHaveValue("s:cephfs-dms");
  expect(loc().get("storage")).toBeNull();                                          // 기본 스토리지는 URL 에 없다
  await userEvent.click(screen.getByRole("link", { name: "스토리지별" }));
  expect(await screen.findByRole("link", { name: "cephfs-dms" })).toHaveAttribute("aria-current", "true");
  server.use(http.get("/api/admin/usage/storages", () =>
    HttpResponse.json({ window_limit: 10000, storages: [sum("a-first", { target_count: 1 }), sum("cephfs-dms")] })));
  await qc.invalidateQueries({ queryKey: ["usage-storages"] });
  await userEvent.click(screen.getByRole("link", { name: "경로별" }));
  await waitFor(() => expect(pathname()).toBe("/admin/usage"));
  expect(loc().get("storage")).toBe("cephfs-dms");                                  // 링크가 기억한 스토리지를 적어 돌아온다
  await waitFor(() => expect(storageSelect()).toHaveValue("s:cephfs-dms"));
  expect(optionTexts(storageSelect())).toContain("a-first · cephfs · 대상 1개");    // 바뀐 요약은 받았다
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(screen.queryByText(/사용량 기록이 없습니다/)).toBeNull();
});

// ---- 목록 ----

const LIST = [
  { storage_name: "cephfs-dms", target: "fresh", scan_count: 2, last_scan_at: isoAgo(2),
    latest: point({ total_bytes: 3 * 1024 * 1024, summary: { total_files: 12345 },
                    time_histograms: { atime: [
                      { bucket: "[0d,1d]", min_age_days: 0, max_age_days: 1, bytes: 30 },
                      { bucket: "[181d,365d]", min_age_days: 181, max_age_days: 365, bytes: 70 }] } }) },
  { storage_name: "cephfs-dms", target: "stale", scan_count: 1, last_scan_at: isoAgo(45), latest: null },
  { storage_name: "cephfs-dms", target: "ancient", scan_count: 1, last_scan_at: isoAgo(100),
    latest: point({ total_bytes: null, summary: {}, time_histograms: {}, report_readable: false }) },
];
const list = (rows: unknown[], o: Setup = {}) => setup({ targets: { "cephfs-dms": rows }, ...o });

test("검색: storage 와 q 가 함께 나가고(디바운스 후 -- 옛 path 는 보내지 않는다), 없으면 「검색 지우기」", async () => {
  let last: URL | null = null;
  setup({ onTargets: (u) => { last = u; }, filterQ: true });
  await screen.findByRole("button", { name: "artifacts" });
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "zzz");
  await waitFor(() => expect(last!.searchParams.get("q")).toBe("zzz"));
  expect([last!.searchParams.get("storage"), last!.searchParams.get("path")]).toEqual(["cephfs-dms", null]);
  expect(await screen.findByText("‘zzz’ 검색 결과가 없습니다")).toBeInTheDocument();
  // 검색 중의 빈 결과는 검색창을 남긴다(기록이 없는 스토리지의 빈 상태와 다르다)
  expect(screen.getByLabelText("경로·PV 이름 검색")).toHaveValue("zzz");
  expect(loc().get("q")).toBe("zzz");
  await userEvent.click(screen.getByRole("button", { name: "검색 지우기" }));
  expect(screen.getByLabelText("경로·PV 이름 검색")).toHaveValue("");
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(loc().get("q")).toBeNull();
});

test("검색·정렬은 스토리지를 바꿔도 남고(URL), 새 스토리지에서도 그 스토리지 안에서만 걸린다", async () => {
  let last: URL | null = null;
  setup({ storages: [st("cephfs-dms"), st("mirror")], summary: [sum("cephfs-dms"), sum("mirror")],
          targets: { "cephfs-dms": LIST, mirror: LIST.map((r) => ({ ...r, storage_name: "mirror" })) },
          onTargets: (u) => { last = u; } });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "proj");
  await userEvent.click(screen.getByRole("button", { name: "실 사용량 정렬" }));
  await waitFor(() => expect(last!.searchParams.get("q")).toBe("proj"));
  // 검색 디바운스(300ms) 안에 정렬을 눌러도 둘 다 URL 에 남는다(디바운스가 그 사이 바뀐 쿼리를 되돌리지 않는다)
  await waitFor(() => expect([loc().get("q"), loc().get("sort")]).toEqual(["proj", "bytes"]));
  await chooseStorage("mirror");
  await waitFor(() => expect(last!.searchParams.get("storage")).toBe("mirror"));
  expect(last!.searchParams.get("q")).toBe("proj");
  expect(last!.searchParams.get("sort")).toBe("bytes");
  expect(screen.getByLabelText("경로·PV 이름 검색")).toHaveValue("proj");
});

test("건수 문구: 「N개」(봉투의 matched_count) · 검색 중 「필터에 맞는 N개」", async () => {
  list(LIST);
  await screen.findByRole("button", { name: "fresh" });
  expect(within(panelOf("cephfs-dms")).getByText("3개")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "fr");
  expect(await screen.findByText("필터에 맞는 3개")).toBeInTheDocument();
});

test("목록 컬럼: 실 사용량·파일 수·hot 비율(최신 scan) -- 모름은 —", async () => {
  list(LIST);
  const row = (await screen.findByRole("button", { name: "fresh" })).closest("tr")!;
  expect(row).toHaveTextContent("3.0 MiB");
  expect(row).toHaveTextContent("12,345");
  expect(row).toHaveTextContent("30%");                         // atime ≤180일 30/(30+70)
  for (const t of ["stale", "ancient"]) {
    const r = screen.getByRole("button", { name: t }).closest("tr")!;
    expect(within(r).getAllByText("—")).toHaveLength(3);         // 용량·파일 수·hot 모두 모름(0 아님)
  }
  // 머리글은 정렬 버튼이다(2026-10-19). hot 비율의 정의는 옆 설명(HelpTip).
  expect(screen.getByRole("button", { name: "hot 비율 정렬" }).closest("th")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "hot 비율 설명" })).toBeInTheDocument();
});

test("최근 스캔 경과: N일 전 + 30일 주의색(attn) · 90일 빨강", async () => {
  list(LIST);
  await screen.findByRole("button", { name: "fresh" });
  const ago = (t: string) =>
    within(screen.getByRole("button", { name: t }).closest("tr")!).getByLabelText("최근 스캔 경과");
  expect(ago("fresh")).toHaveTextContent("2일 전");
  expect(ago("fresh").className).not.toMatch(/text-attn|text-bad/);
  expect(ago("stale")).toHaveTextContent("45일 전");
  expect(ago("stale").className).toMatch(/text-attn/);
  expect(ago("ancient")).toHaveTextContent("100일 전");
  expect(ago("ancient").className).toMatch(/text-bad/);
  expect(staleClass(29.9)).not.toMatch(/attn/);
  expect(staleClass(30)).toMatch(/text-attn/);
  expect(staleClass(90)).toMatch(/text-bad/);
  expect(staleClass(null)).toBe("text-muted");
});

test("최근 스캔 정렬 토글: 서버 order 파라미터(desc ↔ asc) + aria-sort", async () => {
  const orders: (string | null)[] = [];
  list(LIST, { onTargets: (u) => orders.push(u.searchParams.get("order")) });
  const sortBtn = await screen.findByRole("button", { name: "최근 스캔 정렬: 최신순" });
  expect(sortBtn.closest("th")).toHaveAttribute("aria-sort", "descending");
  await userEvent.click(sortBtn);
  await waitFor(() => expect(orders[orders.length - 1]).toBe("asc"));
  expect(screen.getByRole("button", { name: "최근 스캔 정렬: 오래된순" }).closest("th"))
    .toHaveAttribute("aria-sort", "ascending");
  expect(orders[0]).toBe("desc");
});

test("열 정렬(2026-10-19): 머리글 -- 서버 sort·order(storage 함께), 같은 열 재클릭 = 방향 전환, 지표 정렬이면 모름 안내", async () => {
  const seen: { storage: string | null; sort: string | null; order: string | null; limit: string | null }[] = [];
  list(LIST, { onTargets: (u) => seen.push({ storage: u.searchParams.get("storage"), sort: u.searchParams.get("sort"),
                                             order: u.searchParams.get("order"), limit: u.searchParams.get("limit") }) });
  await screen.findByRole("button", { name: "최근 스캔 정렬: 최신순" });
  expect(seen[0]).toEqual({ storage: "cephfs-dms", sort: "last_scan", order: "desc", limit: "100" });
  expect(screen.queryByText("값 모름(—)은 맨 뒤")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "실 사용량 정렬" }));
  await waitFor(() => expect(seen[seen.length - 1]).toMatchObject({ storage: "cephfs-dms", sort: "bytes", order: "desc" }));
  const bytesBtn = screen.getByRole("button", { name: "실 사용량 정렬: 큰 순" });
  expect(bytesBtn.closest("th")).toHaveAttribute("aria-sort", "descending");
  expect(screen.getByRole("button", { name: "최근 스캔 정렬" }).closest("th")).not.toHaveAttribute("aria-sort");
  expect(screen.getByText("값 모름(—)은 맨 뒤")).toBeInTheDocument();
  // 대상 수(2)가 창(10,000)을 넘지 않으면 창 안내는 없다
  expect(screen.queryByText(/개 안에서 정렬/)).toBeNull();
  await userEvent.click(bytesBtn);
  await waitFor(() => expect(seen[seen.length - 1]).toMatchObject({ sort: "bytes", order: "asc" }));
  expect(screen.getByRole("button", { name: "실 사용량 정렬: 작은 순" }).closest("th"))
    .toHaveAttribute("aria-sort", "ascending");
  for (const [label, sort, word] of [["파일 수", "files", "많은 순"], ["hot 비율", "hot", "높은 순"],
                                     ["스캔 횟수", "scans", "많은 순"]] as const) {
    await userEvent.click(screen.getByRole("button", { name: `${label} 정렬` }));
    await waitFor(() => expect(seen[seen.length - 1]).toMatchObject({ sort, order: "desc" }));
    expect(screen.getByRole("button", { name: `${label} 정렬: ${word}` })).toBeInTheDocument();
  }
  await userEvent.click(screen.getByRole("button", { name: "최근 스캔 정렬" }));
  expect(await screen.findByRole("button", { name: "최근 스캔 정렬: 최신순" })).toBeInTheDocument();
  expect(screen.queryByText("값 모름(—)은 맨 뒤")).toBeNull();
});

test("지표 정렬 창 안내: 맞는 대상 수(봉투 matched_count)가 서버 window_limit 을 넘을 때만(숫자는 서버 값)", async () => {
  list(LIST, { summary: [sum("cephfs-dms", { target_count: 12345 })], windowLimit: 10000, matched: 12345 });
  await screen.findByRole("button", { name: "fresh" });
  expect(screen.queryByText(/개 안에서 정렬/)).toBeNull();          // 최근 스캔 정렬(SQL)엔 창이 없다
  await userEvent.click(screen.getByRole("button", { name: "hot 비율 정렬" }));
  expect(await screen.findByText("최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
});

test("지표 정렬 창 안내 + 검색(filesystem -- SQL): 맞는 수(정확)가 창 이하면 안내 없음, 넘으면 「필터에 맞는 대상 중」 -- 조건부 추정은 없다", async () => {
  const many = Array.from({ length: 100 }, (_, i) => ({ ...(LIST[0] as object), target: `t${i}` }));
  // 서버 흉내: 검색 없음·「t」는 1만 넘게 맞고, 「solo」는 하나
  list([...many, { ...(LIST[0] as object), target: "solo" }],
       { summary: [sum("cephfs-dms", { target_count: 12345 })], windowLimit: 10000, filterQ: true,
         matched: (u, rows) => (u.searchParams.get("q") === "solo" ? rows.length : 12345) });
  await userEvent.click(await screen.findByRole("button", { name: "hot 비율 정렬" }));
  expect(await screen.findByText("최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "solo");
  expect(await screen.findByRole("button", { name: "solo" })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/개 안에서 정렬/)).toBeNull());
  await userEvent.clear(screen.getByLabelText("경로·PV 이름 검색"));
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "t");
  expect(await screen.findByText("필터에 맞는 대상 중 최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
  expect(screen.getByText("필터에 맞는 12,345개 · 상위 100개 표시")).toBeInTheDocument();
  expect(screen.queryByText(/넘으면/)).toBeNull();
});

test("목록이 상한(100)을 채우면 「상위 100개 표시 · 전체 N개」(전체 = 봉투의 matched_count -- select 옵션의 대상 수가 아니다)", async () => {
  const many = Array.from({ length: 100 }, (_, i) => ({ ...(LIST[0] as object), target: `t${i}` }));
  list(many, { summary: [sum("cephfs-dms", { target_count: 99999 })], matched: 12345 });
  expect(await screen.findByText("상위 100개 표시 · 전체 12,345개")).toBeInTheDocument();
});

test("최신 리포트를 못 읽은 행: 용량은 DB 기록값임을 표시, 파일 수·hot 은 —", async () => {
  list([{ storage_name: "cephfs-dms", target: "dbonly", scan_count: 1, last_scan_at: isoAgo(1),
          latest: point({ total_bytes: 2048, summary: {}, time_histograms: {}, report_readable: false }) }]);
  const row = (await screen.findByRole("button", { name: "dbonly" })).closest("tr")!;
  expect(row).toHaveTextContent("2.0 KiB(DB)");
  expect(within(row).getAllByText("—")).toHaveLength(2);
});

// ---- CSV ----

test("CSV 내보내기: 지금 스토리지·정렬로 export 를 받아 항목당 한 줄, 파일 이름에 스토리지", async () => {
  vi.mocked(downloadCsv).mockClear();
  let exportUrl: URL | null = null;
  const exportBody = {
    generated_at: "2026-10-02T00:00:00Z", count: 1, truncated: false,
    rows: [{ ...LIST[0], first_scan_at: isoAgo(10),
             previous: point({ job_id: "j0", total_bytes: 2 * 1024 * 1024 }) }],
  };
  list(LIST, { onExport: (u) => { exportUrl = u; return exportBody; } });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.click(screen.getByRole("button", { name: /최근 스캔 정렬/ }));       // asc 로 바꾼 뒤 내보내기
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  await waitFor(() => expect(downloadCsv).toHaveBeenCalledTimes(1));
  expect(exportUrl!.searchParams.get("storage")).toBe("cephfs-dms");
  expect(exportUrl!.searchParams.get("order")).toBe("asc");
  const [filename, text] = vi.mocked(downloadCsv).mock.calls[0];
  expect(filename).toMatch(/^dms-usage-cephfs-dms-\d{8}-\d{4}\.csv$/);
  const [header, line] = (text as string).split("\r\n");
  const cols = header.split(",");
  const val = (c: string) => line.split(",")[cols.indexOf(c)];
  expect(val("abs_path")).toBe("/cephfs/dms/fresh");
  expect(val("total_bytes")).toBe(String(3 * 1024 * 1024));
  expect(val("delta_bytes_vs_previous")).toBe(String(1024 * 1024));
  expect(val("hot_ratio_atime_180d_pct")).toBe("30");
  expect(val("total_files")).toBe("12345");
  expect(val("atime_bytes_181d-365d")).toBe("70");
  expect(await screen.findByRole("status")).toHaveTextContent("1개 항목을 내보냈습니다");
});

test("CSV 내보내기: 서버 상한을 넘으면 잘렸다고 말한다(검색·필터로 좁히라고)", async () => {
  list(LIST, { onExport: () => ({ generated_at: "", count: 10000, truncated: true, matched_count: 12345, rows: [LIST[0]] }) });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "상한 10,000개까지만 내보냈습니다 — 검색·필터로 범위를 좁히세요");
});

test("CSV 내보내기는 지금 열 정렬(sort·order)과 검색(q)을 그대로 싣는다", async () => {
  const exportUrls: URL[] = [];
  list(LIST, { onExport: (u) => { exportUrls.push(u); return undefined; } });
  await screen.findByRole("button", { name: "fresh" });
  await userEvent.click(screen.getByRole("button", { name: "hot 비율 정렬" }));
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "fr");
  await screen.findByText("필터에 맞는 3개");
  await userEvent.click(screen.getByRole("button", { name: /CSV 내보내기/ }));
  await waitFor(() => expect(exportUrls).toHaveLength(1));
  expect(exportUrls[0].searchParams.get("storage")).toBe("cephfs-dms");
  expect([exportUrls[0].searchParams.get("q"), exportUrls[0].searchParams.get("path")]).toEqual(["fr", null]);
  expect(exportUrls[0].searchParams.get("sort")).toBe("hot");
  expect(exportUrls[0].searchParams.get("order")).toBe("desc");
});

// ---- 이 스토리지 합계(2026-10-21 「스토리지별 사용량 및 정보들의 총합」) ----

// 2026-10-29 합계 한 줄 + [자세히]: 구역(이름 「이 스토리지 합계」·「필터 합계」)의 첫 줄이 합계 줄(totalsLine), [자세히] 안이
// 예전 블록과 같은 StorageTotals(totalsBox -- 열어서 돌려준다. 값이 와야 [자세히] 가 생긴다).
const totalsRegion = (name = "이 스토리지 합계") => screen.findByRole("region", { name });
const totalsLine = async (name?: string) => (await totalsRegion(name)).firstElementChild as HTMLElement;
async function totalsBox(name?: string): Promise<HTMLElement> {
  const region = await totalsRegion(name);
  const details = await waitFor(() => {
    const d = region.querySelector("details");
    if (d === null) throw new Error("합계가 아직 오지 않았다");
    return d;
  });
  if (!details.open) await userEvent.click(within(details).getByText("자세히"));
  return details;
}
const valueOf = (box: HTMLElement, label: string) => within(box).getByText(label, { selector: "dt" }).nextElementSibling;

test("이 스토리지 합계: 실 사용량·파일·디렉터리·hot·df + 크기 분포 + 각주(최상위 기준 · 하위 제외 · scan 시점 범위)", async () => {
  const seen: URL[] = [];
  setup({ onTotals: (u) => seen.push(u) });
  const box = await totalsBox();
  expect(await within(box).findByText("17.0 MiB")).toBeInTheDocument();
  expect(valueOf(box, "실 사용량")).toHaveTextContent(/^17\.0 MiB$/);
  expect(valueOf(box, "실 사용량")).toHaveAttribute("title", "17,786,075 B");
  expect(valueOf(box, "파일")).toHaveTextContent(/^662개$/);
  expect(valueOf(box, "디렉터리")).toHaveTextContent(/^243개$/);
  expect(valueOf(box, "hot 비율(180일)")).toHaveTextContent(/^100%$/);
  expect(valueOf(box, "파일시스템 사용(df)")).toHaveTextContent("36.0 MiB/ 22.8 GiB0.2%");
  const dist = within(box).getByRole("list", { name: "파일 크기 분포" });
  expect(within(dist).getByText("4 KiB 이하 590개 89%")).toBeInTheDocument();
  expect(within(dist).getByText("4–64 KiB 66개 10.0%")).toBeInTheDocument();
  expect(box).toHaveTextContent("최상위 대상 4개 기준 · 하위 대상 5개 제외 · scan 시점 65일 전 ~ 18일 전");
  expect(within(box).getByText("65일 전")).toHaveClass("text-attn");            // 오래된 쪽은 표의 경과 색(30일↑ 주의)
  // 표·검색은 합계 아래에서 그대로
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  // 요청은 storage 하나만(경로 검색·정렬과 무관)
  expect(seen.map((u) => u.search)).toEqual(["?storage=cephfs-dms"]);
});

test("합계 null ≠ 0: 모르는 값은 —, 0 은 「0개」, 모름을 뺀 합은 값 뒤 「이상」(hot 은 비율이라 붙이지 않는다) · df 모름이면 칸 없음", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", {
    total_bytes: m(null, 0, 4), total_files: m(0, 4), total_directories: m(243, 3, 1),
    hot_ratio: { value: 0.5, hot_bytes: 1, total_bytes: 2, known: 3, unknown: 1 }, filesystem: null }) } });
  const box = await totalsBox();
  await within(box).findByText("243개");
  expect(valueOf(box, "실 사용량")).toHaveTextContent(/^—$/);
  expect(valueOf(box, "파일")).toHaveTextContent(/^0개$/);
  expect(valueOf(box, "디렉터리")).toHaveTextContent(/^243개 이상$/);
  expect(valueOf(box, "hot 비율(180일)")).toHaveTextContent(/^50%$/);
  expect(within(box).queryByText("파일시스템 사용(df)")).toBeNull();
  expect(box).toHaveTextContent("값 모름 4개");
});

test("각주 조각은 해당할 때만: 형식 오류·값 모름·창(1만) -- 하위 0 이면 없음, 같은 날 scan 시점은 하나", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", {
    nested_target_count: 0, invalid_target_count: 2, truncated: true, total_files: m(600, 1, 3),
    oldest_scan_at: isoAgo(5), newest_scan_at: isoAgo(5) }) } });
  const box = await totalsBox();
  expect(await within(box).findByText(/형식이 잘못된 대상 2개 제외/)).toBeInTheDocument();
  expect(box).toHaveTextContent(
    "최상위 대상 4개 기준 · 형식이 잘못된 대상 2개 제외 · 값 모름 3개 · 최근 스캔된 10,000개 대상 기준 · scan 시점 5일 전");
  expect(box).not.toHaveTextContent("하위 대상");
  expect(box).not.toHaveTextContent("요약 읽는 중");
  expect(box).not.toHaveTextContent("~");
});

test("요약 읽는 중(pending > 0 · progressed): 1초 뒤 다시 불러 이어 읽고, 다 읽으면 멈춘다", async () => {
  const calls: number[] = [];
  setup({ totals: (s, call) => {
    calls.push(call);
    return call === 1 ? totalsOf(s, { pending_count: 2, progressed: true, total_files: m(600, 2, 2) }) : totalsOf(s);
  } });
  const box = await totalsBox();
  expect(await within(box).findByText(/요약 읽는 중 2개/)).toBeInTheDocument();
  expect(box).not.toHaveTextContent("값 모름");                                // 읽는 중인 것은 따로 말한다
  expect(valueOf(box, "파일")).toHaveTextContent(/^600개 이상$/);
  await waitFor(() => expect(valueOf(box, "파일")).toHaveTextContent(/^662개$/), { timeout: 3000 });
  expect(box).not.toHaveTextContent("요약 읽는 중");
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls).toEqual([1, 2]);
});

test("진전 없는 pending(progressed false)은 다시 부르지 않고 「값 모름」으로 말한다", async () => {
  const calls: number[] = [];
  setup({ totals: (s, call) => {
    calls.push(call);
    return totalsOf(s, { pending_count: 2, progressed: false, total_files: m(600, 2, 2) });
  } });
  const box = await totalsBox();
  expect(await within(box).findByText(/값 모름 2개/)).toBeInTheDocument();
  expect(box).not.toHaveTextContent("요약 읽는 중");
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls).toEqual([1]);
});

test("재조회 실패로 이어 읽기가 멈추면 「읽는 중」이라 말하지 않는다 -- 남은 대상은 값 모름, 「다시 시도」로 이어 읽는다", async () => {
  // 리뷰: 오류 중엔 폴링이 멈추는데(useStorageTotals) 캐시에 남은 응답이 pending·progressed 라 「요약 읽는 중 240개」와
  // 「요약을 읽는 중입니다…」가 아무것도 읽지 않는 동안 남았고, 값 모름은 pending 만큼 적게 셌다.
  const calls: number[] = [];
  let failing = true;
  setup({ totals: (s, call) => {
    calls.push(call);
    if (call === 1) {
      return totalsOf(s, { target_count: 300, summed_target_count: 300, pending_count: 240, progressed: true,
                           total_files: m(null, 0, 300), file_size_histogram: { buckets: [], known: 0, unknown: 300 } });
    }
    return failing ? "error" : totalsOf(s);
  } });
  const box = await totalsBox();
  const line = await totalsLine();
  expect(await within(box).findByText(/요약 읽는 중 240개/)).toBeInTheDocument();
  expect(line).toHaveTextContent("요약 읽는 중 240개");                          // 줄도 말한다
  expect(box).toHaveTextContent("값 모름 60개");
  expect(within(box).getByText("요약을 읽는 중입니다…")).toBeInTheDocument();
  // 실패와 [다시 시도] 는 줄이 말한다([자세히] 안에 두 벌을 두지 않는다)
  expect(await within(line).findByText(/합계를 새로 고치지 못했습니다/, undefined, { timeout: 3000 })).toBeInTheDocument();
  expect(box).not.toHaveTextContent("새로 고치지 못했습니다");
  expect(line).not.toHaveTextContent("요약 읽는 중");
  expect(box).not.toHaveTextContent("요약 읽는 중");
  expect(box).not.toHaveTextContent("요약을 읽는 중입니다");
  expect(within(box).getByText("크기 분포를 아는 대상이 없습니다")).toBeInTheDocument();
  expect(box).toHaveTextContent("값 모름 300개");
  await new Promise((r) => setTimeout(r, 1300));
  expect(calls).toEqual([1, 2]);                                       // 오류 중엔 다시 부르지 않는다
  failing = false;
  await userEvent.click(within(line).getByRole("button", { name: "다시 시도" }));
  await waitFor(() => expect(valueOf(box, "파일")).toHaveTextContent(/^662개$/));
  expect(line).not.toHaveTextContent("새로 고치지 못했습니다");
  expect(calls).toEqual([1, 2, 3]);
});

test("합계 개수 값은 title 에 정확한 값(칸이 좁아 말줄임돼도 읽힌다)", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", { total_files: m(1234567890, 3, 1) }) } });
  const box = await totalsBox();
  await within(box).findByText("1,234,567,890개");
  expect(valueOf(box, "파일")).toHaveAttribute("title", "1,234,567,890개");
  expect(valueOf(box, "파일")).toHaveTextContent(/^1,234,567,890개 이상$/);
  expect(valueOf(box, "디렉터리")).toHaveAttribute("title", "243개");
});

test("합계 실패여도 표는 그려진다 -- 서버 사유는 덧붙이고, 옛 API(SPA index.html 200)의 파서 문장은 띄우지 않는다 · 줄 안 [다시 시도]", async () => {
  let failing = true;
  setup({ totals: (s) => (failing ? "error" : totalsOf(s)) });
  expect(await screen.findByText("합계를 불러오지 못했습니다 — internal")).toBeInTheDocument();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  const line = await totalsLine();
  expect(within(line).queryByText("자세히")).toBeNull();                         // 값이 없으면 펼칠 것도 없다
  failing = false;
  await userEvent.click(within(line).getByRole("button", { name: "다시 시도" }));
  expect(await within(await totalsLine()).findByText("17.0 MiB")).toBeInTheDocument();
  cleanup();
  setup({ totals: { "cephfs-dms": "html" } });
  expect(await screen.findByText("합계를 불러오지 못했습니다")).toBeInTheDocument();
  expect(screen.queryByText(/Unexpected token|JSON/)).toBeNull();
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
});

test("[다시 시도]가 성공해 단추가 사라지면 포커스는 <body> 가 아니라 합계 줄(구역)로 -- 다시 실패하면 단추에 남는다 · 필터 중 「이 스토리지 합계 다시 시도」도", async () => {
  // 2차 리뷰(2026-10-29): 오류 문구째 단추가 사라져 포커스가 <body> 로 떨어지고 화면 낭독기가 자리를 잃었다.
  let calls = 0;
  setup({ onTotals: () => { calls += 1; }, totals: (s, call) => (call <= 2 ? "error" : totalsOf(s)) });
  const retry = await screen.findByRole("button", { name: "다시 시도" });
  retry.focus();
  await userEvent.keyboard("{Enter}");                                              // 다시 실패 -- 단추가 남고 포커스도 그대로
  await waitFor(() => expect(calls).toBe(2));
  await new Promise((r) => setTimeout(r, 50));
  expect(screen.getByRole("button", { name: "다시 시도" })).toHaveFocus();
  await userEvent.keyboard("{Enter}");
  const region = await totalsRegion();
  await waitFor(() => expect(within(region).queryByRole("button", { name: "다시 시도" })).toBeNull());
  expect(region).toHaveTextContent("17.0 MiB");
  expect(region).toHaveFocus();
  cleanup();
  let isFiltered = false;
  let baseCalls = 0;
  setup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", targets: { "cephfs-dms": FS_ROWS },
          onTotals: (u) => { isFiltered = u.searchParams.has("requester"); },
          totals: (s) => (isFiltered ? totalsOf(s, { total_bytes: m(1024, 2) })
            : (baseCalls += 1) === 1 ? "error" : totalsOf(s)) });
  const line = await totalsLine("필터 합계");
  const baseRetry = await within(line).findByRole("button", { name: "이 스토리지 합계 다시 시도" });
  baseRetry.focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(valueOf(line, "이 스토리지")).toHaveTextContent(/^17\.0 MiB$/));
  expect(await totalsRegion("필터 합계")).toHaveFocus();
});

test("기록 0개 스토리지(등록 빈 스토리지·실패 기록만 남은 미등록)엔 합계 줄도 요청도 없다", async () => {
  const seen: string[] = [];
  setup({ storages: [st("a-empty")],
          summary: [sum("a-empty", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })],
          unregistered: [unreg("ghost", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })],
          onTotals: (u) => seen.push(u.searchParams.get("storage") ?? "") });
  expect(await screen.findByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeInTheDocument();
  await chooseStorage("ghost");
  expect(await screen.findByText("성공한 scan 기록이 없습니다")).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "이 스토리지 합계" })).toBeNull();
  expect(seen).toEqual([]);
});

test("합계는 검색과 무관하고, 스토리지를 오가도 스토리지마다 한 번(같은 키 캐시)", async () => {
  const seen: string[] = [];
  setup({ storages: [st("cephfs-dms"), st("mirror")], summary: [sum("cephfs-dms"), sum("mirror")],
          targets: { "cephfs-dms": TARGETS, mirror: TARGETS.map((r) => ({ ...r, storage_name: "mirror" })) },
          totals: { "cephfs-dms": totalsOf("cephfs-dms"), mirror: totalsOf("mirror", { total_bytes: m(2048, 4) }) },
          onTotals: (u) => seen.push(u.search) });
  await within(await totalsBox()).findByText("17.0 MiB");
  expect(within(await totalsLine()).queryByText("검색은 합계에 들어가지 않습니다")).toBeNull();
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "team");
  // 검색어가 걸리면 줄이 「합계에는 들어가지 않는다」고 말한다(합계 요청은 그대로 -- 아래 seen)
  expect(await within(await totalsLine()).findByText("검색은 합계에 들어가지 않습니다")).toBeInTheDocument();
  await chooseStorage("mirror");
  expect(await within(await totalsBox()).findByText("2.0 KiB")).toBeInTheDocument();
  await chooseStorage("cephfs-dms");
  expect(await within(await totalsBox()).findByText("17.0 MiB")).toBeInTheDocument();
  expect(seen).toEqual(["?storage=cephfs-dms", "?storage=mirror"]);
});

test("빈 이름 스토리지의 합계는 storage= (빈 이름)로 부른다", async () => {
  const seen: (string | null)[] = [];
  setup({ storages: [st("cephfs-dms")],
          summary: [sum("cephfs-dms"), sum("", { registered: false, source: null, backend_type: null, mount_path: null,
                                                 target_count: 1 })],
          targets: { "cephfs-dms": TARGETS, "": [{ ...TARGETS[0], storage_name: "", target: "blank-row" }] },
          totals: { "cephfs-dms": totalsOf("cephfs-dms"), "": totalsOf("", { total_bytes: m(4096, 4) }) },
          onTotals: (u) => seen.push(u.searchParams.get("storage")) });
  await waitFor(() => expect(optionTexts(storageSelect())).toContain("(이름 없음) · 미등록 · 대상 1개"));
  await chooseStorage("");
  expect(await within(await totalsBox()).findByText("4.0 KiB")).toBeInTheDocument();
  expect(seen).toContain("");
});

test("이 스토리지 합계: 잘못된 인코딩 이름 합은 1개 이상일 때만 각주 한 조각(주의색) -- 모르는 대상이 있으면 「이상」", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", { invalid_name_entries: m(15, 3, 1) }) } });
  const box = await totalsBox();
  const part = await within(box).findByText("잘못된 인코딩 이름 15개 이상");
  expect(part).toHaveClass("text-attn");
  expect(box).not.toHaveTextContent("파손");                                       // 파손 경로에 더하지 않는다
  // 합계 줄 끝에도 주의색 한 조각(접힌 [자세히] 를 열지 않아도 보인다)
  expect(within(await totalsLine()).getByText("잘못된 인코딩 이름 15개 이상")).toHaveClass("text-attn");
  cleanup();
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", { invalid_name_entries: m(0, 4) }) } });
  const zero = await totalsBox();
  await within(zero).findByText("17.0 MiB");
  expect(zero).not.toHaveTextContent("잘못된 인코딩 이름");
  expect(await totalsLine()).not.toHaveTextContent("잘못된 인코딩 이름");
  cleanup();
  setup();                                                                           // 구형 서버(키 없음)
  const old = await totalsBox();
  await within(old).findByText("17.0 MiB");
  expect(old).not.toHaveTextContent("잘못된 인코딩 이름");
  expect(await totalsLine()).not.toHaveTextContent("잘못된 인코딩 이름");
});

// ---- 합계 줄(2026-10-29 「합계 두 벌 → 한 줄 + [자세히]」) ----

test("합계 줄: 한 줄 dl(실 사용량·파일·디렉터리·hot·df %(사용 / 전체)) + [자세히] 기본 닫힘 -- 안은 예전 블록 그대로, 요청은 한 번", async () => {
  const seen: string[] = [];
  setup({ onTotals: (u) => seen.push(u.search) });
  const region = await totalsRegion();
  const line = await totalsLine();
  await within(line).findByText("17.0 MiB");
  expect(valueOf(line, "실 사용량")).toHaveTextContent(/^17\.0 MiB$/);
  expect(valueOf(line, "실 사용량")).toHaveAttribute("title", "17,786,075 B");
  expect(valueOf(line, "파일")).toHaveTextContent(/^662개$/);
  expect(valueOf(line, "디렉터리")).toHaveTextContent(/^243개$/);
  expect(valueOf(line, "hot 비율")).toHaveTextContent(/^100%$/);
  expect(valueOf(line, "df")).toHaveTextContent(/^0\.2% \(36\.0 MiB \/ 22\.8 GiB\)$/);
  expect(valueOf(line, "df")).not.toHaveClass("text-attn");
  expect(within(region).getByRole("button", { name: "이 스토리지 합계 설명" })).toHaveAccessibleDescription(
    /각 최상위 대상의 최근 성공 scan 값을 더합니다/);
  // [자세히] 는 기본 닫힘 -- 안은 KPI·df 미터·분포·각주(예전 블록과 같은 StorageTotals). 제목·설명은 줄에 한 번만.
  const details = region.querySelector("details")!;
  expect(details.open).toBe(false);
  expect(within(details).getByText("자세히")).toBeInTheDocument();
  await userEvent.click(within(details).getByText("자세히"));
  expect(details.open).toBe(true);
  expect(within(details).getByRole("list", { name: "파일 크기 분포" })).toBeInTheDocument();
  expect(within(details).getByText("파일시스템 사용(df)")).toBeInTheDocument();
  expect(within(details).queryByRole("button", { name: "이 스토리지 합계 설명" })).toBeNull();
  expect(seen).toEqual(["?storage=cephfs-dms"]);                                    // 줄과 [자세히] 가 한 쿼리
  // 카드 안의 카드 없음: 패널을 감싸던 카드가 없고, 줄·표가 각자 테두리다
  expect(panelOf("cephfs-dms").querySelector(".shadow-soft")).toBeNull();
});

test("합계 줄: df 90% 이상은 주의색 · 창을 넘어 잘린 합은 「이상」(부분 합) · 로딩은 줄 높이 Skeleton", async () => {
  setup({ totals: { "cephfs-dms": totalsOf("cephfs-dms", {
    truncated: true, filesystem: { total_bytes: 1000, used_bytes: 950, node_name: "w1", reported_at: isoAgo(0), fresh: true,
                                   node_count: 1, consistent: true } }) } });
  const line = await totalsLine();
  await within(line).findByText("17.0 MiB");
  expect(valueOf(line, "df")).toHaveClass("text-attn");
  expect(valueOf(line, "df")).toHaveTextContent(/^95%/);
  expect(valueOf(line, "실 사용량")).toHaveTextContent(/^17\.0 MiB 이상$/);
  expect(valueOf(line, "hot 비율")).toHaveTextContent(/^100%$/);                    // 비율은 하한이 아니다
  const box = await totalsBox();
  expect(valueOf(box, "실 사용량")).toHaveTextContent(/^17\.0 MiB 이상$/);           // [자세히] 도 같은 규칙
  expect(box).toHaveTextContent("최근 스캔된 10,000개 대상 기준");
  cleanup();
  setup();
  // 합계 응답이 오지 않는 동안(패널은 요약이 온 뒤 그려지므로 그 전에 갈아 끼운다)
  server.use(http.get("/api/admin/usage/storage-totals", async () => { await delay("infinite"); return HttpResponse.json({}); }));
  const loading = await totalsLine();
  expect(within(loading).getByText("합계 불러오는 중…")).toHaveClass("sr-only");
  expect(within(loading).queryByText("자세히")).toBeNull();
});

test("합계 줄(필터 0건): 줄은 남아 「대상 0개」와 스토리지 전체 숫자를 말한다 -- 예전 스토리지 합계 블록의 숫자를 잃지 않는다", async () => {
  let filtered = false;
  setup({ path: "/admin/usage?storage=cephfs-dms&requester=nobody",
          onTotals: (u) => { filtered = u.searchParams.has("requester"); },
          totals: (s) => (filtered ? emptyTotals(s) : totalsOf(s)) });
  expect(await screen.findByText("필터에 맞는 대상이 없습니다")).toBeInTheDocument();
  const line = await totalsLine("필터 합계");
  await waitFor(() => expect(valueOf(line, "이 스토리지")).toHaveTextContent(/^17\.0 MiB$/));
  expect(line).toHaveTextContent("대상 0개");
  expect(within(line).queryByText("실 사용량", { selector: "dt" })).toBeNull();      // 0 개의 합을 「—」로 늘어놓지 않는다
  const box = await totalsBox("필터 합계");
  expect(within(box).getByText(/^이 스토리지 전체: /)).toHaveTextContent("이 스토리지 전체: 17.0 MiB · 파일 662개");
  expect(within(box).queryByRole("list", { name: "파일 크기 분포" })).toBeNull();
});

// 필터를 건 동안의 스토리지 전체(2026-10-29 리뷰 -- 예전엔 스토리지 합계 블록이 위에 남아 제 실패·df 경고를 말했다): 그 쿼리의 실패와
// 따로 부르는 [다시 시도], df 90% 이상 주의는 줄이, 노드 불일치·인코딩 경고·읽는 중은 [자세히] 의 전체 한 줄이 말한다.
test("합계 줄(필터): 스토리지 합계가 실패하면 줄이 말하고 따로 [다시 시도] -- 필터 합계와 별개(둘 다 실패해도 단추 이름이 갈린다)", async () => {
  let isFiltered = false;
  let failing: "both" | "base" | null = "both";
  setup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", targets: { "cephfs-dms": FS_ROWS },
          onTotals: (u) => { isFiltered = u.searchParams.has("requester"); },
          totals: (s) => (failing === "both" || (failing === "base" && !isFiltered) ? "error"
            : isFiltered ? totalsOf(s, { total_bytes: m(1024, 2) }) : totalsOf(s)) });
  const line = await totalsLine("필터 합계");
  expect(await within(line).findByText("이 스토리지 합계를 불러오지 못했습니다 — internal")).toHaveClass("text-bad");
  expect(within(line).getByText("합계를 불러오지 못했습니다 — internal")).toBeInTheDocument();
  expect(within(line).getAllByRole("button", { name: /다시 시도$/ }).map((b) => b.getAttribute("aria-label")))
    .toEqual([null, "이 스토리지 합계 다시 시도"]);
  // 필터 합계만 다시 부르면 필터 숫자가 오고, 스토리지 쪽 실패는 그대로 남는다
  failing = "base";
  await userEvent.click(within(line).getByRole("button", { name: "다시 시도" }));
  expect(await within(line).findByText("1.0 KiB")).toBeInTheDocument();
  expect(line).toHaveTextContent("이 스토리지 합계를 불러오지 못했습니다 — internal");
  expect(within(line).queryByText("이 스토리지", { selector: "dt" })).toBeNull();
  failing = null;
  await userEvent.click(within(line).getByRole("button", { name: "이 스토리지 합계 다시 시도" }));
  await waitFor(() => expect(valueOf(line, "이 스토리지")).toHaveTextContent(/^17\.0 MiB$/));
  expect(line).not.toHaveTextContent("불러오지 못했습니다");
  expect(within(await totalsBox("필터 합계")).getByText(/^이 스토리지 전체: /)).toHaveTextContent("17.0 MiB");
});

test("합계 줄(필터): 스토리지 전체의 df 90% 이상은 줄에 「이 스토리지 df」(주의색), 노드 불일치·인코딩 경고·읽는 중·재조회 실패도 잃지 않는다", async () => {
  let isFiltered = false;
  let baseCalls = 0;
  const fs = { total_bytes: 1000, used_bytes: 950, node_name: "w1", reported_at: isoAgo(0), fresh: true, node_count: 2,
               consistent: false };
  setup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", targets: { "cephfs-dms": FS_ROWS },
          onTotals: (u) => { isFiltered = u.searchParams.has("requester"); if (!isFiltered) baseCalls += 1; },
          totals: (s) => (isFiltered ? totalsOf(s, { total_bytes: m(1024, 2) })
            : baseCalls <= 2 ? totalsOf(s, { filesystem: fs, invalid_name_entries: m(3, 4), pending_count: 2, progressed: true,
                                               total_bytes: m(17786075, 2, 2) })
            : "error") });
  const line = await totalsLine("필터 합계");
  await waitFor(() => expect(valueOf(line, "이 스토리지 df")).toHaveTextContent(/^95%\*\(노드마다 다름\)$/));
  expect(valueOf(line, "이 스토리지 df")).toHaveClass("text-attn");
  expect(valueOf(line, "이 스토리지")).toHaveTextContent(/^17\.0 MiB 이상$/);       // 읽는 중인 대상은 값 모름 -- 부분 합
  const box = await totalsBox("필터 합계");
  const all = within(box).getByText(/^이 스토리지 전체: /);
  expect(all).toHaveTextContent("df 950 B / 1000 B (95%) — 노드마다 다름");
  expect(within(all).getByText("(95%)")).toHaveClass("text-attn");
  expect(within(all).getByText("잘못된 인코딩 이름 3개")).toHaveClass("text-attn");
  expect(all).toHaveTextContent("요약 읽는 중 2개");
  // 이어 읽기(1초마다 -- 셋째 부름)가 실패하면: 줄이 「새로 고치지 못했습니다」 + [다시 시도], 전체 한 줄은 「읽는 중」이라 말하지 않는다
  expect(await within(line).findByText("이 스토리지 합계를 새로 고치지 못했습니다", undefined, { timeout: 4000 }))
    .toBeInTheDocument();
  expect(within(line).getByRole("button", { name: "이 스토리지 합계 다시 시도" })).toBeInTheDocument();
  expect(all).not.toHaveTextContent("요약 읽는 중");
  expect(valueOf(line, "이 스토리지 df")).toHaveTextContent(/^95%/);                  // 받아 둔 값은 남는다
  // df 가 90% 미만이면 줄엔 「이 스토리지 X」만(필터 줄을 늘이지 않는다)
  cleanup();
  setup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", targets: { "cephfs-dms": FS_ROWS } });
  const quiet = await totalsLine("필터 합계");
  await waitFor(() => expect(valueOf(quiet, "이 스토리지")).toHaveTextContent(/^17\.0 MiB$/));
  expect(within(quiet).queryByText("이 스토리지 df", { selector: "dt" })).toBeNull();
});

// ---- 필터(2026-10-29 「사용자·PV 이름·namespace 등으로 필터」 -- 픽스처는 usageTestKit 의 NFS_ROWS·FS_ROWS) ----

test("필터 줄: PV 스토리지는 7개 select(라벨·순서·옵션 = 선택지와 대상 수), filesystem 스토리지는 요청자만", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}` });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  const group = await filterGroup();
  await waitFor(() => expect(within(group).getAllByRole("combobox")).toHaveLength(7));
  expect(within(group).getAllByRole("combobox").map((s) => s.getAttribute("aria-label"))).toEqual(
    ["클러스터 필터", "namespace 필터", "PV 이름 필터", "StorageClass 필터", "PV 구분 필터", "권한 사용자 필터", "요청자 필터"]);
  expect(optionTexts(select("클러스터 필터"))).toEqual(["클러스터 전체", "dmsk8s · 4개", "dmsk8s2 · 1개"]);
  // 클러스터가 둘이라 namespace 옵션에 클러스터를 붙인다(namespace 는 클러스터마다만 유일)
  expect(optionTexts(select("namespace 필터"))).toEqual(
    ["namespace 전체", "team-a · dmsk8s · 3개", "team-b · dmsk8s · 1개", "lab-y · dmsk8s2 · 1개"]);
  expect(optionTexts(select("PV 이름 필터"))).toEqual(["PV 이름 전체", "datasets — team-a (tb-nfs) · dmsk8s · 2개",
    "models — team-b (tb-nfs) · dmsk8s · 1개", "weights — lab-y (c2-nfs) · dmsk8s2 · 1개", "gone — team-a (tb-nfs) · dmsk8s · 1개"]);
  expect(optionTexts(select("StorageClass 필터"))).toEqual(["StorageClass 전체", "tb-nfs · 4개", "c2-nfs · 1개"]);
  expect(optionTexts(select("PV 구분 필터"))).toEqual(["구분 전체", "PV 안 · 5개", "PV 밖(직접 경로) · 1개"]);  // 0 범주는 없다
  expect(optionTexts(select("권한 사용자 필터"))).toEqual(
    ["권한 사용자 전체", "alice · 3개", "junwoo.song · 2개", "cocoa.song · 1개"]);
  expect(optionTexts(select("요청자 필터"))).toEqual(["요청자 전체", "alice · 2개", "mason · 5개"]);
  expect(within(group).getByRole("button", { name: "필터 설명" })).toHaveAccessibleDescription(
    /PV 필터는 각 경로가 지금 어느 PV\(=PVC\) 아래에 있는지로 거릅니다/);
  expect(within(group).queryByRole("button", { name: "필터 지우기" })).toBeNull();     // 켜진 필터가 없다
  // filesystem 스토리지: PV 안 대상이 0 이라 PV 축·구분(범주 하나)은 숨고 요청자만
  await chooseStorage("cephfs-dms");
  await screen.findByRole("button", { name: "artifacts" });
  await waitFor(() => expect(within(screen.getByRole("group", { name: "사용량 필터" })).getAllByRole("combobox")
    .map((s) => s.getAttribute("aria-label"))).toEqual(["요청자 필터"]));
});

test("딥링크 ?storage=k8s-pv-nfs&cluster=dmsk8s2&namespace=lab-y: 요청에 실리고 select 가 그 값, 목록·건수·필터 합계가 그 결과", async () => {
  const seen: URL[] = [], totals: string[] = [];
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s2&namespace=lab-y`, onTargets: (u) => seen.push(u),
            totals: { [NFS]: totalsOf(NFS) }, onTotals: (u) => totals.push(u.search) });
  expect(await screen.findByRole("button", { name: "c2/lab-y/weights" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "team-a/datasets/dms-proof" })).toBeNull();
  for (const u of seen) {
    expect(u.pathname).toBe("/api/admin/usage/targets");
    expect([u.searchParams.get("cluster"), u.searchParams.get("namespace"), u.searchParams.get("pvc")])
      .toEqual(["dmsk8s2", "lab-y", null]);
  }
  await filterGroup();
  await waitFor(() => expect(select("클러스터 필터")).toHaveValue("dmsk8s2"));
  expect(select("namespace 필터")).toHaveDisplayValue("lab-y · dmsk8s2 · 1개");
  expect(screen.getByText("필터에 맞는 1개")).toBeInTheDocument();
  expect(within(screen.getByRole("group", { name: "사용량 필터" })).getByRole("button", { name: "필터 지우기" }))
    .toBeInTheDocument();
  // 필터 합계(2026-10-29 한 줄): 합계 줄 하나가 「필터 합계」가 된다 -- 대상 수 · 숫자 · df 없음 · 끝에 흐린 「이 스토리지 X」.
  // [자세히] 안: 적용 필터 한 줄 + 필터 KPI·분포 + 「이 스토리지 전체」 한 줄. 요청은 필터 합계 + 스토리지 합계(경로 검색 없음).
  const line = await totalsLine("필터 합계");
  expect(screen.queryByRole("region", { name: "이 스토리지 합계" })).toBeNull();      // 두 벌이 아니다
  await waitFor(() => expect(valueOf(line, "이 스토리지")).toHaveTextContent(/^17\.0 MiB$/));
  expect(valueOf(line, "이 스토리지")).toHaveClass("text-muted");
  expect(line).toHaveTextContent("대상 9개");
  expect(within(line).queryByText("df")).toBeNull();
  const box = await totalsBox("필터 합계");
  expect(box).toHaveTextContent("적용 필터: 클러스터 dmsk8s2 · namespace lab-y");
  expect(within(box).queryByText("파일시스템 사용(df)")).toBeNull();
  expect(within(box).getByText(/^이 스토리지 전체: /)).toHaveTextContent(
    "이 스토리지 전체: 17.0 MiB · 파일 662개 · 디렉터리 243개 · hot 100% · df 36.0 MiB / 22.8 GiB (0.2%)");
  await waitFor(() => expect([...totals].sort()).toEqual(
    [`?storage=${NFS}`, `?storage=${NFS}&cluster=dmsk8s2&namespace=lab-y`]));
});

test("위계: namespace 를 고르면 클러스터가 함께 실리고, 클러스터를 바꾸면 namespace·PV 이름이 풀린다 · 「PV 밖」은 PV 축을, PV 축은 「PV 밖」을 푼다", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}` });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await filterGroup();
  await waitFor(() => expect(select("PV 이름 필터")).toBeInTheDocument());
  await userEvent.selectOptions(select("PV 이름 필터"), "datasets — team-a (tb-nfs) · dmsk8s · 2개");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace"), loc().get("pvc")])
    .toEqual(["dmsk8s", "team-a", "datasets"]));
  expect(await screen.findByRole("button", { name: "team-a/datasets/proof-scan" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "old/gone" })).toBeNull();          // 같은 namespace 의 다른 PV 이름
  // PV 이름 선택지는 위 단계(클러스터·namespace)로 좁혀진다
  expect(optionTexts(select("PV 이름 필터"))).toEqual(
    ["PV 이름 전체", "datasets — team-a (tb-nfs) · dmsk8s · 2개", "gone — team-a (tb-nfs) · dmsk8s · 1개"]);
  await userEvent.selectOptions(select("클러스터 필터"), "dmsk8s2 · 1개");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace"), loc().get("pvc")])
    .toEqual(["dmsk8s2", null, null]));
  expect(await screen.findByRole("button", { name: "c2/lab-y/weights" })).toBeInTheDocument();
  // 구분 「PV 밖」 → PV 축이 풀린다(함께 걸면 늘 0건)
  await userEvent.selectOptions(select("PV 구분 필터"), "PV 밖(직접 경로) · 1개");
  await waitFor(() => expect(loc().get("scope")).toBe("outside"));
  expect(loc().get("cluster")).toBeNull();
  expect(await screen.findByRole("button", { name: "proof-d9/alice/latest" })).toBeInTheDocument();
  // PV 축(권한 사용자)을 고르면 「PV 밖」이 풀린다
  await userEvent.selectOptions(select("권한 사용자 필터"), "cocoa.song · 1개");
  await waitFor(() => expect(loc().get("user")).toBe("cocoa.song"));
  expect(loc().get("scope")).toBeNull();
  expect(await screen.findByRole("button", { name: "c2/lab-y/weights" })).toBeInTheDocument();
  // namespace 를 지우면 PV 이름도 풀린다(클러스터는 남는다)
  await userEvent.selectOptions(select("namespace 필터"), "team-a · dmsk8s · 3개");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace")]).toEqual(["dmsk8s", "team-a"]));
  await userEvent.selectOptions(select("PV 이름 필터"), "gone — team-a (tb-nfs) · dmsk8s · 1개");
  await waitFor(() => expect(loc().get("pvc")).toBe("gone"));
  await userEvent.selectOptions(select("namespace 필터"), "namespace 전체");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace"), loc().get("pvc")]).toEqual(["dmsk8s", null, null]));
});

test("URL 보존: 필터는 스토리지 전환·시트 열기/닫기에 남는다 · 잘못된 storage 정리(replace)도 필터를 남긴다", async () => {
  let last: URL | null = null;
  pvSetup({ path: `/admin/usage?storage=${NFS}&requester=mason`, onTargets: (u) => { last = u; } });
  await userEvent.click(await screen.findByRole("button", { name: "team-b/models" }));
  await sheetOf("team-b/models");
  expect([loc().get("target"), loc().get("requester")]).toEqual(["team-b/models", "mason"]);
  await closeSheet();
  expect([loc().get("storage"), loc().get("target"), loc().get("requester")]).toEqual([NFS, null, "mason"]);
  await chooseStorage("cephfs-dms");
  expect([loc().get("storage"), loc().get("requester")]).toEqual(["cephfs-dms", "mason"]);
  await waitFor(() => expect(last!.searchParams.get("storage")).toBe("cephfs-dms"));
  expect(last!.searchParams.get("requester")).toBe("mason");
  expect(await screen.findByRole("button", { name: "team" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "artifacts" })).not.toBeNull();          // artifacts 도 mason 이 scan 했다
  await waitFor(() => expect(select("요청자 필터")).toHaveValue("mason"));
  cleanup();
  pvSetup({ path: "/admin/usage?storage=typo-name&target=x&requester=alice" });
  expect(await screen.findByText("‘typo-name’ 스토리지의 사용량 기록이 없습니다 — 기본 스토리지를 보여 줍니다")).toBeInTheDocument();
  await waitFor(() => expect(loc().get("storage")).toBeNull());
  expect([loc().get("target"), loc().get("requester")]).toEqual([null, "alice"]);
});

test("그 스토리지에 없는 값은 「(이 스토리지에 없음)」 옵션 · 필터 0건이면 툴바·필터 줄이 남고 「필터에 맞는 대상이 없습니다」 → 지우기", async () => {
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&cluster=dmsk8s" });
  expect(await screen.findByText("필터에 맞는 대상이 없습니다")).toBeInTheDocument();
  expect(screen.queryByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeNull();
  expect(screen.getByLabelText("경로·PV 이름 검색")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeInTheDocument();
  await filterGroup();
  // filesystem 스토리지라도 켜진 PV 축은 보인다(풀 수 있게) -- 값은 이 스토리지에 없다고
  await waitFor(() => expect(select("클러스터 필터")).toHaveDisplayValue("dmsk8s (이 스토리지에 없음)"));
  expect(screen.getByText("필터에 맞는 0개")).toBeInTheDocument();
  const clears = screen.getAllByRole("button", { name: "필터 지우기" });
  expect(clears).toHaveLength(2);                                                       // 필터 줄 + 빈 상태
  await userEvent.click(clears[1]);
  await waitFor(() => expect(loc().get("cluster")).toBeNull());
  expect(await screen.findByRole("button", { name: "artifacts" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "필터 지우기" })).toBeNull();
  // 빈 상태의 버튼도 사라진다 -- 포커스는 필터 줄로(<body> 가 아니다)
  expect(document.activeElement).toBe(screen.getByRole("group", { name: "사용량 필터" }));
});

test("건수·각주: 「필터에 맞는 N개 · 상위 100개 표시」 · matched 모름(PV 판정 창 넘침)이면 그 사실을 한 문단 + 지표 정렬 각주", async () => {
  const many = Array.from({ length: 100 }, (_, i) => ({ ...TARGETS[0], target: `t${i}`, pv: OUTSIDE, _requesters: ["mason"] }));
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", targets: { "cephfs-dms": many, [NFS]: NFS_ROWS },
            matched: 12345 });
  expect(await screen.findByText("필터에 맞는 12,345개 · 상위 100개 표시")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "hot 비율 정렬" }));
  expect(await screen.findByText("필터에 맞는 대상 중 최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`, matched: null, windowTruncated: true });
  // PV 필터만이면 늘 보이는 검색칸을 권한다(2026-10-29 리뷰): 서버는 검색어가 모두 경로에 든 대상끼리의 최근 창에서 PV 필터를
  // 판정한다(routes_usage._with_path_hits -- 예전 「경로 검색 + PV 필터」와 같은 창). 요청자 필터는 칸이 없을 수 있어 권하지 않는다.
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에서 4개 맞음 — 대상이 10,000개를 넘는 스토리지라 PV 필터는 "
    + "이 범위에서만 거릅니다. 검색으로 범위를 좁히세요")).toBeInTheDocument();
  expect(screen.queryByText(/필터에 맞는 \d/)).toBeNull();                              // 모르는 수를 지어내지 않는다
  await userEvent.click(screen.getByRole("button", { name: "파일 수 정렬" }));
  // PV 경로는 창 → 필터 순서다(2026-10-29 리뷰: 「필터에 맞는 대상 중 최근 N개」는 거꾸로라 위 문단과 모순됐다)
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에서 PV 필터에 맞는 것끼리 정렬")).toBeInTheDocument();
  expect(screen.queryByText("필터에 맞는 대상 중 최근 스캔된 10,000개 안에서 정렬")).toBeNull();
});

test("PV 판정 창은 정렬과 무관하게 최근 스캔 순(서버 규칙) -- 오래된 순·스캔 횟수에서도 같은 범위, 요청자(SQL)가 먼저 좁힌 것을 밝히고 PV 스토리지의 검색은 창 안이라 말한다, 0건은 「그 범위 안에는」", async () => {
  // 2026-10-29 리뷰: 서버가 요청한 정렬로 창을 자르던 때 이 문단은 오래된 순에서 거짓이었다 -- 이제 서버 창이 늘 최근 스캔 순이라
  // 문단은 정렬로 바뀌지 않는다. 대신 요청자(SQL)가 창보다 먼저 걸린다는 사실은 말한다. 2026-10-29 검색(경로·PV 이름): PV 이름이
  // 생길 수 있는 스토리지에선 PV 이름 일치가 창 **안**에서 걸리고, 검색어가 모두 경로에 든 대상은 그것끼리의 최근 창에서 PV 필터를
  // 판정한다(서버 _with_path_hits) -- 「검색에 맞는 대상 중」이라 말하지 않고 두 범위를 나눠 말한다.
  const seen: URL[] = [];
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s&requester=mason`, matched: null, windowTruncated: true,
            onTargets: (u) => seen.push(u) });
  const note = "요청자에 맞는 대상 중 최근 스캔된 10,000개 안에서 3개 맞음 — 요청자에 맞는 대상이 10,000개를 넘어 PV 필터는 "
    + "이 범위에서만 거릅니다. 검색으로 범위를 좁히세요";
  expect(await screen.findByText(note)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^최근 스캔 정렬/ }));
  await waitFor(() => expect(seen[seen.length - 1].searchParams.get("order")).toBe("asc"));
  await userEvent.click(screen.getByRole("button", { name: "스캔 횟수 정렬" }));
  await waitFor(() => expect(seen[seen.length - 1].searchParams.get("sort")).toBe("scans"));
  expect(await screen.findByText(note)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "team");
  expect(await screen.findByText(new RegExp("^\\d+개 맞음 — 요청자에 맞는 대상이 10,000개를 넘어 PV 필터와 PV 이름 검색은 "
    + "요청자에 맞는 대상 중 최근 스캔된 10,000개 안에서만 판정합니다\\. 검색어가 모두 경로에 든 대상은 그것끼리 최근 스캔된 "
    + "10,000개 안에서 PV 필터로 거릅니다$"))).toBeInTheDocument();
  expect(screen.queryByText(/검색·요청자에 맞는 대상 중/)).toBeNull();
  cleanup();
  // 판정 창 안에 맞는 대상이 0개 -- 「없다」를 단정하지 않는다
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=nowhere`, matched: null, windowTruncated: true });
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에는 필터에 맞는 대상이 없습니다")).toBeInTheDocument();
  expect(screen.queryByText("필터에 맞는 대상이 없습니다")).toBeNull();
});

test("포커스: 「필터 지우기」(필터 줄·빈 상태)와 값 때문에만 보이던 칸이 사라져도 포커스가 <body> 로 떨어지지 않고 필터 줄로 간다", async () => {
  // 2026-10-29 리뷰(JobsList 와 같은 결함 유형): 키보드로 지우면 버튼이 언마운트돼 화면 낭독기가 자리를 잃었다.
  pvSetup({ path: `/admin/usage?storage=${NFS}&requester=mason` });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  const group = await filterGroup();
  const clear = within(group).getByRole("button", { name: "필터 지우기" });
  clear.focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(loc().get("requester")).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("group", { name: "사용량 필터" })));
  cleanup();
  // 단일 클러스터 스토리지: namespace 를 고르면 나타나는 클러스터 select -- 「클러스터 전체」로 바꾸면 그 칸이 사라진다
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s&namespace=team-a`,
            targets: { "cephfs-dms": FS_ROWS, [NFS]: NFS_ROWS.filter((r) => r.target !== "c2/lab-y/weights") } });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await filterGroup();
  const cluster = await waitFor(() => select("클러스터 필터"));
  cluster.focus();
  await userEvent.selectOptions(cluster, "클러스터 전체");
  await waitFor(() => expect([loc().get("cluster"), loc().get("namespace")]).toEqual([null, null]));
  await waitFor(() => expect(within(screen.getByRole("group", { name: "사용량 필터" }))
    .queryByRole("combobox", { name: "클러스터 필터" })).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("group", { name: "사용량 필터" })));
  // 살아 있는 칸에서 고르면 포커스를 건드리지 않는다
  const ns = select("namespace 필터");
  ns.focus();
  await userEvent.selectOptions(ns, "team-b · 1개");
  await waitFor(() => expect(loc().get("namespace")).toBe("team-b"));
  expect(document.activeElement).toBe(select("namespace 필터"));
});

test("포커스: 기록 0 스토리지에서 필터를 지우면 필터 줄·툴바가 함께 사라진다 -- 포커스는 <body> 가 아니라 패널에 남는다", async () => {
  // 2026-10-29 2차 리뷰: 필터를 지우면 필터 줄이 사라지고(선택지 없음) 새 목록이 오면 「기록 없음」이라 툴바도 사라진다 -- 툴바로
  // 옮긴 포커스가 다시 <body> 로 떨어졌다. 빈 상태의 「필터 지우기」와 필터 줄의 것 둘 다.
  const empty = {
    storages: [st("cephfs-dms"), st("empty-store")],
    summary: [sum("cephfs-dms"), sum("empty-store", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null })],
    targets: { "cephfs-dms": TARGETS, "empty-store": [] },
    path: "/admin/usage?storage=empty-store&requester=mason",
  };
  for (const which of ["empty", "group"] as const) {
    setup(empty);
    expect(await screen.findByText("필터에 맞는 대상이 없습니다")).toBeInTheDocument();
    const group = await filterGroup();
    const clears = screen.getAllByRole("button", { name: "필터 지우기" });
    expect(clears).toHaveLength(2);
    const btn = which === "group" ? within(group).getByRole("button", { name: "필터 지우기" }) : clears[1];
    btn.focus();
    await userEvent.keyboard("{Enter}");
    await waitFor(() => expect(loc().get("requester")).toBeNull());
    expect(await screen.findByText("이 스토리지에는 아직 성공한 scan 이 없습니다")).toBeInTheDocument();
    expect(screen.queryByLabelText("경로·PV 이름 검색")).toBeNull();                      // 툴바도 사라졌다
    await waitFor(() => expect(document.activeElement).not.toBe(document.body));
    expect(panelOf("empty-store").contains(document.activeElement)).toBe(true);
    cleanup();
  }
});

test("자리표시(검색 응답 대기) 동안 PV 창 문단·지표 정렬 각주를 말하지 않는다 -- 옛 결과의 수를 새 검색의 범위로 이름 붙이지 않는다", async () => {
  // 2026-10-29 2차 리뷰: 건수 자리는 「검색 중…」인데 문단은 이전 결과의 「4개 맞음」을 지금 입력의 범위(「경로 검색에 맞는 대상 중」)로
  // 말했다(아무도 확인하지 않은 수와 이유). 건수와 같은 규칙 -- 자리표시 동안 말하지 않는다.
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`, matched: null, windowTruncated: true, filterQ: true,
            slowWhen: (u) => u.searchParams.get("q") !== null });
  expect(await screen.findByText(/^최근 스캔된 10,000개 대상 안에서 4개 맞음 — /)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "파일 수 정렬" }));
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에서 PV 필터에 맞는 것끼리 정렬")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "zzz");
  expect(await screen.findByText("검색 중…")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "team-a/datasets/dms-proof" })).toBeInTheDocument();   // 옛 행은 자리표시로 남는다
  expect(screen.queryByText(/맞음 — /)).toBeNull();
  expect(screen.queryByText(/PV 필터에 맞는 것끼리 정렬/)).toBeNull();
  cleanup();
  // 빈 결과의 자리표시: 제목·보조 줄은 그 결과를 낸 검색(봉투 메아리)으로 말한다 -- 지금 입력이 아니다
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=nowhere`, matched: null, windowTruncated: true, filterQ: true,
            slowWhen: (u) => u.searchParams.get("q") !== null });
  const title = "최근 스캔된 10,000개 대상 안에는 필터에 맞는 대상이 없습니다";
  expect(await screen.findByText(title)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "zzz");
  expect(await screen.findByText("검색 중…")).toBeInTheDocument();
  expect(screen.getByText(title)).toBeInTheDocument();
  expect(screen.queryByText(/검색에 맞는 대상 중/)).toBeNull();
  expect(screen.queryByText(/검색 ‘zzz’ 도 함께/)).toBeNull();
});

test("배포 틈: /targets 가 봉투가 아니면(배열·HTML·404) 「새 사용량 API 를 모릅니다」, 메아리가 다르면 「필터가 화면과 다릅니다」 -- 둘 다 CSV 를 막는다", async () => {
  const cases: (() => Response)[] = [
    () => HttpResponse.json(TARGETS),                                                  // 옛 scan-targets 모양(배열)
    () => new HttpResponse("<!doctype html><html></html>", { status: 200, headers: { "content-type": "text/html" } }),
    () => HttpResponse.json({ detail: "Not Found" }, { status: 404 }),
  ];
  for (const raw of cases) {
    setup({ targetsRaw: raw });
    expect(await screen.findByText(USAGE_API_UNKNOWN)).toBeInTheDocument();
    expect(screen.queryByText(/Unexpected token|JSON/)).toBeNull();
    expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeDisabled();
    cleanup();
  }
  // 옛 서버는 모르는 파라미터를 조용히 무시한다 -- 메아리에 요청자가 없으면 거른 척한 전체 목록이다
  pvSetup({ path: `/admin/usage?storage=${NFS}&requester=alice`,
            echo: (u) => ({ ...echoOf(u), requester: null }) });
  expect(await screen.findByText(USAGE_FILTER_MISMATCH)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "team-b/models" })).toBeNull();
  expect(screen.getByRole("button", { name: "CSV 내보내기" })).toBeDisabled();
});

test("자리표시: 필터가 바뀌면 이전 필터의 행을 남기지 않는다(같은 필터 안의 정렬·검색만 남긴다)", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}`, slowWhen: (u) => u.searchParams.get("requester") === "alice" });
  await screen.findByRole("button", { name: "team-b/models" });
  await filterGroup();
  await waitFor(() => expect(select("요청자 필터")).toBeInTheDocument());
  await userEvent.selectOptions(select("요청자 필터"), "alice · 2개");
  await waitFor(() => expect(screen.queryByRole("button", { name: "team-b/models" })).toBeNull());
  expect(within(panelOf(NFS)).getByText("불러오는 중…")).toBeInTheDocument();
});

// 행의 칸들(경로 버튼으로 찾는다 -- 버튼 이름 = 경로).
const cellsOf = (t: string) => within(screen.getByRole("button", { name: t }).closest("tr")!).getAllByRole("cell");
const marksIn = (el: HTMLElement) => [...el.querySelectorAll("mark")].map((x) => x.textContent);

test("표: PV 스토리지는 「경로」 다음 「PV 이름」 열(7열·정렬 없음) -- PV 이름 + 작게 「namespace (SC) · 클러스터」 · 외 n개 · 기록 · PV 밖 · 모름, 경로 칸 둘째 줄은 없다 · filesystem 스토리지는 6열", async () => {
  // 2026-10-29 사용자 요청 「테이블에 경로 이름뿐 아니라 pv 이름도 표시해줘」 -- 예전 경로 칸 둘째 줄을 열로 옮겼다(둘 다 두지 않는다).
  const both = nrow("shared/root", inv([vol("dmsk8s", "team-a", "a1", "pvc-x", "tb-nfs"), vol("dmsk8s2", "team-a", "a1", "pvc-y", "c2-nfs")],
                                        ["alice"], "shared/root"), ["mason"]);
  const unknown = nrow("weird/x", { source: "unknown", root: null, volumes: [], users: [] }, ["mason"]);
  pvSetup({ path: `/admin/usage?storage=${NFS}`, targets: { "cephfs-dms": FS_ROWS, [NFS]: [...NFS_ROWS, both, unknown] } });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("columnheader")).toHaveLength(7));
  const heads = within(screen.getByRole("table")).getAllByRole("columnheader");
  ["경로", "PV 이름", "실 사용량", "파일 수", "hot 비율", "스캔 횟수", "최근 스캔"].forEach((label, i) =>
    expect(heads[i].textContent).toMatch(new RegExp(`^${label}`)));
  expect(within(heads[1]).queryByRole("button")).toBeNull();                       // 정렬 열이 아니다
  expect(heads[1]).not.toHaveAttribute("aria-sort");
  // PV 안: 윗줄 PV 이름(본문 글자), 아랫줄 작게 namespace (SC) · 클러스터(두 클러스터 탭). title 은 정체 전부 + PV 루트.
  const proof = cellsOf("team-a/datasets/dms-proof");
  expect(proof).toHaveLength(7);
  expect(proof[0]).toHaveTextContent(/^team-a\/datasets\/dms-proof$/);              // 경로 칸 둘째 줄은 없다
  expect(within(proof[1]).getByText("datasets")).toHaveClass("text-ink");
  expect(within(proof[1]).getByText("team-a (tb-nfs) · dmsk8s")).toHaveClass("text-xs", "text-muted");
  expect(proof[1].firstElementChild).toHaveAttribute("title",
    "datasets — team-a (tb-nfs) · dmsk8s · 볼륨 pvc-aaa\nPV 루트 team-a/datasets");
  // 루트 충돌(정체 둘): 첫째 + 「외 1개」, title 은 둘 다
  const shared = cellsOf("shared/root")[1];
  expect(shared).toHaveTextContent(/^a1외 1개team-a \(tb-nfs\) · dmsk8s$/);
  expect(shared.firstElementChild!.getAttribute("title")).toContain("a1 — team-a (c2-nfs) · dmsk8s2 · 볼륨 pvc-y");
  // 기록(지금 인벤토리에 없는 PV) · PV 밖(흐림) · 모름(기울임)
  expect(within(cellsOf("old/gone")[1]).getByText("기록")).toHaveAttribute("title",
    "지금 인벤토리에 없는 PV — 이 경로를 PV 주소로 scan 한 기록 기준");
  expect(within(cellsOf("old/gone")[1]).getByText("gone")).toBeInTheDocument();
  const outside = within(cellsOf("proof-d9/alice/latest")[1]).getByText("PV 밖");
  expect(outside).toHaveClass("text-muted");
  expect(outside).toHaveAttribute("title", "지금 어떤 PV 루트에도 속하지 않는 경로(직접 경로 scan·PV 상위 디렉터리)");
  expect(within(cellsOf("weird/x")[1]).getByText("모름")).toHaveClass("italic");
  // 버튼 이름은 경로 그대로
  expect(screen.getByRole("button", { name: "old/gone" })).toBeInTheDocument();
  // filesystem 스토리지(PV 안 대상 없음): 열 자체가 없다(6열)
  await chooseStorage("cephfs-dms");
  await screen.findByRole("button", { name: "artifacts" });
  await waitFor(() => expect(within(screen.getByRole("table")).getAllByRole("columnheader")).toHaveLength(6));
  expect(within(screen.getByRole("table")).queryByText("PV 밖")).toBeNull();
  expect(within(screen.getByRole("table")).queryByRole("columnheader", { name: "PV 이름" })).toBeNull();
  expect(cellsOf("artifacts")).toHaveLength(6);
});

test("PV 이름 열은 선택지(facets)가 PV 안 0 이어도 받은 행에 PV 안이 있으면 보인다 -- 선택지 실패에도 행으로 판단", async () => {
  // 선택지는 최근 스캔 순 창의 집계라, 창 밖의 오래된 PV 대상이 목록(오래된 순)에 올 수 있다. 선택지를 못 받아도 행이 말한다.
  pvSetup({ path: `/admin/usage?storage=${NFS}`, facets: "error" });
  await screen.findByRole("button", { name: "team-b/models" });
  expect(within(screen.getByRole("table")).getAllByRole("columnheader")).toHaveLength(7);
  expect(within(cellsOf("team-b/models")[1]).getByText("models")).toBeInTheDocument();
});

test("검색(q): 경로·PV 이름 어디든 · 여러 단어 AND · 대소문자 무시 -- path 가 아니라 q 로 보내고, 맞은 글자를 경로·PV 이름 양쪽에서 강조", async () => {
  // 2026-10-29 사용자 요청 「경로로 검색할 뿐 아니라, pv 이름도 검색할 수 있게 하고 일부 단어만 포함해도 뜨도록」(작업 목록 검색과 같은
  // 뜻 -- 서버 search_tokens·search_matches). Checkpoints 는 PV 이름에만, run 은 경로에만 있다.
  const ckpt = nrow("projects/run-01", inv([vol("dmsk8s", "team-c", "Checkpoints", "pvc-eee", "tb-nfs")], [], "projects"), ["mason"]);
  const twin = nrow("shared/two", inv([vol("dmsk8s", "team-a", "alpha", "pvc-f1", "tb-nfs"), vol("dmsk8s2", "lab-y", "beta", "pvc-f2", "c2-nfs")],
                                      [], "shared/two"), ["mason"]);
  const seen: URL[] = [];
  pvSetup({ path: `/admin/usage?storage=${NFS}`, filterQ: true, onTargets: (u) => seen.push(u),
            targets: { "cephfs-dms": FS_ROWS, [NFS]: [...NFS_ROWS, ckpt, twin] } });
  await screen.findByRole("button", { name: "team-b/models" });
  // 칸 이름·자리표시·규칙 설명(낭독기는 칸에서, 눈은 ? 에서)
  const box = screen.getByLabelText("경로·PV 이름 검색");
  expect(box).toHaveAttribute("placeholder", "경로·PV 이름 검색…");
  expect(box).toHaveAccessibleDescription("여러 단어는 모두 들어간 것만 · 대소문자 무시 · 경로나 PV 이름 어디든");
  expect(screen.getByRole("button", { name: "경로·PV 이름 검색 설명" }))
    .toHaveAccessibleDescription("여러 단어는 모두 들어간 것만 · 대소문자 무시 · 경로나 PV 이름 어디든");
  expect(within(screen.getByRole("table")).queryAllByRole("mark")).toHaveLength(0);   // 검색 없으면 강조 없음
  // 두 토큰 AND: CHECK(PV 이름, 대소문자 무시) + run(경로)
  await userEvent.type(box, "CHECK run");
  await waitFor(() => expect(loc().get("q")).toBe("CHECK run"));
  await waitFor(() => expect(screen.queryByRole("button", { name: "team-b/models" })).toBeNull());
  const last = seen[seen.length - 1];
  expect([last.searchParams.get("q"), last.searchParams.get("path")]).toEqual(["CHECK run", null]);
  expect(screen.getAllByRole("button", { name: /^projects\/run-01$/ })).toHaveLength(1);
  expect(await screen.findByText("필터에 맞는 1개")).toBeInTheDocument();
  const [pathCell, pvCell] = cellsOf("projects/run-01");
  expect(marksIn(pathCell)).toEqual(["run"]);
  expect(marksIn(pvCell)).toEqual(["Check"]);                                          // 원문 글자 그대로 강조
  expect(pvCell).toHaveTextContent(/^Checkpointsteam-c \(tb-nfs\) · dmsk8s$/);
  // 한 토큰이 경로와 PV 이름 양쪽에 맞으면 양쪽 강조
  await userEvent.clear(box);
  await userEvent.type(box, "DATA");
  await waitFor(() => expect(screen.queryByRole("button", { name: "team-b/models" })).toBeNull());
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  const [p2, v2] = cellsOf("team-a/datasets/dms-proof");
  await waitFor(() => expect(marksIn(p2)).toEqual(["data"]));
  expect(marksIn(v2)).toEqual(["data"]);
  // 루트 충돌 행: 둘째 정체의 PV 이름으로 걸리면 그 정체를 칸에 세운다(강조가 보이게) -- 「외 1개」는 그대로
  await userEvent.clear(box);
  await userEvent.type(box, "beta");
  await screen.findByRole("button", { name: "shared/two" });
  await waitFor(() => expect(marksIn(cellsOf("shared/two")[1])).toEqual(["beta"]));
  expect(cellsOf("shared/two")[1]).toHaveTextContent(/^beta외 1개lab-y \(c2-nfs\) · dmsk8s2$/);
});

test("검색 메아리: q 를 모르는 옛 서버(메아리에 q 없음)면 검색한 척한 전체 목록 대신 「필터가 화면과 다릅니다」", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&q=art",
          echo: (u) => { const e = echoOf(u); delete e.q; return e; } });
  expect(await screen.findByText(USAGE_FILTER_MISMATCH)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "artifacts" })).toBeNull();
});

test("창 안 검색: PV 스토리지에서 검색만 건 결과가 판정 창을 넘으면 「PV 이름으로 맞는 대상은 이 범위에서만, 경로는 범위 밖까지」, 0건은 「그 범위 안에는」 · PV 종류 스토리지는 소속이 안 보여도 같다 · filesystem 의 검색은 SQL(먼저 좁힌 것으로 말한다)", async () => {
  // 2026-10-29 리뷰: 서버는 창이 넘쳐도 검색어가 모두 경로에 든 대상을 창 밖까지 찾는다(routes_usage._with_path_hits) -- 행에 창 밖
  // 경로 일치가 섞이므로 「최근 N개 안에서 k개」라고 말하지 않고, 창에 묶이는 것(PV 이름 일치)만 주어로.
  pvSetup({ path: `/admin/usage?storage=${NFS}&q=team`, matched: null, windowTruncated: true, filterQ: true });
  expect(await screen.findByText(new RegExp("^\\d+개 맞음 — 대상이 10,000개를 넘는 스토리지라 PV 이름으로 맞는 대상은 최근 스캔된 "
    + "10,000개 대상 안에서만 찾습니다\\. 검색어가 모두 경로에 든 대상은 범위 밖까지 찾습니다$"))).toBeInTheDocument();
  expect(screen.queryByText(/필터에 맞는 \d/)).toBeNull();                              // 모르는 수를 지어내지 않는다
  await userEvent.click(screen.getByRole("button", { name: "실 사용량 정렬" }));
  // 서버는 창 밖 경로 일치까지 더한 뒤 그 최근 N개 안에서 정렬한다 -- SQL 경로와 같은 말
  expect(await screen.findByText("필터에 맞는 대상 중 최근 스캔된 10,000개 안에서 정렬")).toBeInTheDocument();
  expect(screen.queryByText(/맞는 것끼리 정렬/)).toBeNull();
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&q=zzz`, matched: null, windowTruncated: true, filterQ: true });
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에는 ‘zzz’ 검색 결과가 없습니다")).toBeInTheDocument();
  expect(screen.getByText("검색어가 모두 경로에 든 대상은 범위 밖까지 찾았습니다")).toBeInTheDocument();
  cleanup();
  // 종류가 PV·둘 다인 등록 스토리지는 보이는 소속이 없어도 검색이 창 안이다(서버 may_attribute_pv)
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&q=zzz", matched: null, windowTruncated: true, filterQ: true,
            storages: [{ ...st("cephfs-dms"), kind: "both" } as ReturnType<typeof st>, st(NFS)] });
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에는 ‘zzz’ 검색 결과가 없습니다")).toBeInTheDocument();
  cleanup();
  // filesystem: 검색은 SQL 로 먼저 걸린다 -- PV 필터 창의 범위 이름에 「검색에 맞는 대상 중」, 검색만이면 창 문구 없이 정확한 0건
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&cluster=x&q=art", matched: null, windowTruncated: true, filterQ: true });
  expect(await screen.findByText("검색에 맞는 대상 중 최근 스캔된 10,000개 안에는 필터에 맞는 대상이 없습니다")).toBeInTheDocument();
  expect(screen.getByText("검색 ‘art’ 도 함께 걸려 있습니다")).toBeInTheDocument();
  cleanup();
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&q=zzz", filterQ: true });
  expect(await screen.findByText("‘zzz’ 검색 결과가 없습니다")).toBeInTheDocument();
});

test("창 넘침 문단·빈 상태는 화면에 없는 요청자 필터를 권하지 않는다 -- 한 사람만 scan 한 PV 스토리지", async () => {
  // 2026-10-29 리뷰: 요청자 선택지가 하나뿐이면 요청자 select 가 그려지지 않는데(UsageFilters -- 선택지 2개 이상) 문단·빈 상태가
  // 「요청자 필터로 범위를 좁힐 수 있습니다」라고 권했다. 서버가 창 밖의 경로 일치를 찾게 된 뒤(routes_usage._with_path_hits) 검색만
  // 건 결과는 권할 것이 없고(경로는 이미 창 밖까지), PV 필터만 건 결과는 늘 보이는 검색칸을 권한다.
  const one = { "cephfs-dms": FS_ROWS, [NFS]: NFS_ROWS.map((r) => ({ ...r, _requesters: ["mason"] })) };
  pvSetup({ path: `/admin/usage?storage=${NFS}&q=team`, targets: one, matched: null, windowTruncated: true, filterQ: true });
  const note = await screen.findByText(/맞음 — /);
  expect(screen.queryByRole("combobox", { name: "요청자 필터" })).toBeNull();
  expect(note.textContent).not.toMatch(/요청자 필터/);
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&q=zzz`, targets: one, matched: null, windowTruncated: true, filterQ: true });
  expect(await screen.findByText("최근 스캔된 10,000개 대상 안에는 ‘zzz’ 검색 결과가 없습니다")).toBeInTheDocument();
  expect(screen.queryByText(/요청자 필터/)).toBeNull();
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`, targets: one, matched: null, windowTruncated: true });
  expect(await screen.findByText(/PV 필터는 이 범위에서만 거릅니다\. 검색으로 범위를 좁히세요$/)).toBeInTheDocument();
  expect(screen.queryByText(/요청자 필터로/)).toBeNull();
});

test("합계: 무필터 요청은 정확히 ?storage=X(스토리지별 비교와 한 벌) · 필터 합계는 필터만 싣고 검색은 싣지 않는다 · 메아리가 다르면 오류", async () => {
  const seen: string[] = [];
  let last: URL | null = null;
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", totals: { "cephfs-dms": totalsOf("cephfs-dms") },
            onTotals: (u) => seen.push(u.search), onTargets: (u) => { last = u; }, filterQ: true });
  const box = await totalsBox("필터 합계");
  expect(await within(box).findByText("17.0 MiB")).toBeInTheDocument();
  expect(box).toHaveTextContent("적용 필터: 요청자 mason");
  expect(within(box).queryByText("파일시스템 사용(df)")).toBeNull();
  expect(within(await totalsRegion("필터 합계")).getByRole("button", { name: "필터 합계 설명" })).toHaveAccessibleDescription(
    /검색\(경로·PV 이름\)은 반영하지 않습니다\. 필터에 맞는 경로 중 최상위만 더합니다/);
  // 스토리지 합계는 [자세히] 안 한 줄(df 포함)과 줄 끝 「이 스토리지 X」로 남는다
  await waitFor(() => expect(within(box).getByText(/^이 스토리지 전체: /)).toHaveTextContent("df 36.0 MiB / 22.8 GiB (0.2%)"));
  await userEvent.type(screen.getByLabelText("경로·PV 이름 검색"), "art");
  await waitFor(() => expect(last!.searchParams.get("q")).toBe("art"));          // 목록은 검색을 싣는다
  expect(await screen.findByText("필터에 맞는 1개")).toBeInTheDocument();
  expect(await totalsLine("필터 합계")).toHaveTextContent("검색은 합계에 들어가지 않습니다");
  expect([...new Set(seen)].sort()).toEqual(["?storage=cephfs-dms", "?storage=cephfs-dms&requester=mason"]);
  expect(seen.some((s) => s.includes("path"))).toBe(false);
  cleanup();
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", totals: { "cephfs-dms": totalsOf("cephfs-dms") },
            totalsEcho: (u) => ({ ...echoOf(u, false), requester: null }) });
  const bad = await screen.findByRole("region", { name: "필터 합계" });
  expect(await within(bad).findByText(`합계를 불러오지 못했습니다 — ${USAGE_FILTER_MISMATCH}`)).toBeInTheDocument();
});

test("CSV(필터): /targets/export 에 필터를 싣고 파일 이름은 -filtered, k8s 열 · PV 판정 창 넘침·메아리 없음을 말한다", async () => {
  vi.mocked(downloadCsv).mockClear();
  let exportUrl: URL | null = null;
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s&namespace=team-a`,
            onExport: (u) => { exportUrl = u;
              return { generated_at: "", count: 1, truncated: false, rows: [{ ...strip(NFS_ROWS[0]), latest: null, previous: null }] }; } });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  await waitFor(() => expect(downloadCsv).toHaveBeenCalledTimes(1));
  expect(exportUrl!.pathname).toBe("/api/admin/usage/targets/export");
  expect([exportUrl!.searchParams.get("cluster"), exportUrl!.searchParams.get("namespace")]).toEqual(["dmsk8s", "team-a"]);
  const [filename, text] = vi.mocked(downloadCsv).mock.calls[0];
  expect(filename).toMatch(new RegExp(`^dms-usage-${NFS}-filtered-\\d{8}-\\d{4}\\.csv$`));
  const [header, line] = (text as string).split("\r\n");
  const cols = header.split(",");
  const val = (c: string) => line.split(",")[cols.indexOf(c)];
  expect([val("k8s_source"), val("k8s_cluster"), val("k8s_namespace"), val("k8s_pvc"), val("k8s_volume"),
          val("k8s_storage_class"), val("k8s_pv_root"), val("k8s_access_users")])
    .toEqual(["inventory", "dmsk8s", "team-a", "datasets", "pvc-aaa", "tb-nfs", "team-a/datasets", "alice;junwoo.song"]);
  expect(await screen.findByRole("status")).toHaveTextContent("1개 항목을 내보냈습니다");
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`,
            onExport: () => ({ generated_at: "", count: 4, truncated: true, matched_count: null, rows: [] }) });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "대상이 10,000개를 넘는 스토리지라 PV 필터는 최근 스캔된 10,000개 대상 안에서만 걸렀습니다 — CSV 가 전부가 아닐 수 있습니다");
  cleanup();
  // 창 안 검색(PV 스토리지): 창에 묶이는 것은 PV 이름 일치뿐이다(경로 일치는 서버가 창 밖까지 -- _with_path_hits)
  pvSetup({ path: `/admin/usage?storage=${NFS}&q=team`,
            onExport: () => ({ generated_at: "", count: 4, truncated: true, matched_count: null, rows: [] }) });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("status")).toHaveTextContent("대상이 10,000개를 넘는 스토리지라 PV 이름으로 맞는 대상은 "
    + "최근 스캔된 10,000개 대상 안에서만 찾았습니다 — CSV 가 전부가 아닐 수 있습니다");
  cleanup();
  vi.mocked(downloadCsv).mockClear();
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s`,
            onExport: () => ({ generated_at: "", count: 0, truncated: false, rows: [], filters: null }) });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("alert")).toHaveTextContent(USAGE_FILTER_MISMATCH);
  expect(downloadCsv).not.toHaveBeenCalled();
});

test("CSV(창 안 검색): 창 밖 경로 일치까지 더해 상한에서 잘렸으면(capped) 「상한 N개까지만」을 창 문구와 함께 · 안 잘렸으면 행 수가 상한이어도 창 문구만", async () => {
  // 2026-10-29 2차 리뷰: PV 스토리지의 검색은 창이 넘쳐도 창 밖의 경로 일치를 더한다(routes_usage._with_path_hits) -- 그래서 내보내기가
  // 상한에서 알려진 일치를 자를 수 있다. 봉투는 창 넘침과 같은 모양(truncated, matched_count null)이라 예전엔 「PV 이름으로 맞는 대상은
  // 창 안에서만」만 말해, 같은 화면 문단이 「경로는 범위 밖까지 찾는다」고 하는데 경로 일치가 잘린 것이 안 보였다.
  const exportOf = (capped: boolean) => () =>
    ({ generated_at: "", count: 10000, truncated: true, capped, matched_count: null, rows: [] });
  pvSetup({ path: `/admin/usage?storage=${NFS}&q=team`, onExport: exportOf(true) });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("status")).toHaveTextContent("상한 10,000개까지만 내보냈습니다. 대상이 10,000개를 넘는 스토리지라 "
    + "PV 이름으로 맞는 대상은 최근 스캔된 10,000개 대상 안에서만 찾았습니다 — 검색·필터로 범위를 좁히세요");
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&cluster=dmsk8s&q=team`, onExport: exportOf(true) });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  expect(await screen.findByRole("status")).toHaveTextContent("상한 10,000개까지만 내보냈습니다. 대상이 10,000개를 넘는 스토리지라 "
    + "PV 필터와 PV 이름 검색은 최근 스캔된 10,000개 대상 안에서만 판정했습니다 — 검색·필터로 범위를 좁히세요");
  cleanup();
  // 서버가 자르지 않았으면(capped false -- 창 안 결과와 경로 일치가 꼭 상한만큼) 「상한까지만」을 지어내지 않는다
  pvSetup({ path: `/admin/usage?storage=${NFS}&q=team`, onExport: exportOf(false) });
  await screen.findByRole("button", { name: "team-a/datasets/dms-proof" });
  await userEvent.click(screen.getByRole("button", { name: "CSV 내보내기" }));
  const status = await screen.findByRole("status");
  expect(status).toHaveTextContent("대상이 10,000개를 넘는 스토리지라 PV 이름으로 맞는 대상은 최근 스캔된 10,000개 대상 안에서만 "
    + "찾았습니다 — CSV 가 전부가 아닐 수 있습니다");
  expect(status).not.toHaveTextContent("상한");
});

test("선택지(facets) 실패: 필터 줄을 그리지 않고, 필터가 켜져 있으면 「필터 지우기」만 남는다(URL 의 필터는 그대로 걸린다)", async () => {
  let last: URL | null = null;
  pvSetup({ path: "/admin/usage?storage=cephfs-dms&requester=mason", facets: "error", onTargets: (u) => { last = u; } });
  await screen.findByRole("button", { name: "team" });
  expect(last!.searchParams.get("requester")).toBe("mason");
  expect(await screen.findByRole("button", { name: "필터 지우기" })).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "사용량 필터" })).toBeNull();
  expect(screen.queryAllByRole("combobox").filter((c) => c !== storageSelect())).toHaveLength(0);
  await userEvent.click(screen.getByRole("button", { name: "필터 지우기" }));
  await waitFor(() => expect(loc().get("requester")).toBeNull());
  expect(screen.queryByRole("button", { name: "필터 지우기" })).toBeNull();
});
