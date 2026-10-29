import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import {
  TARGETS, installServer, isoAgo, loc, m, navType, pathname, setup, st, storageSelect, sum, totalsOf, unreg,
} from "./usageTestKit";
import { PATHS_MEMORY_KEY } from "./usageMemory";

// 사용량 분석 「스토리지별」(/admin/usage/storages, 2026-10-29 -- 예전 경로별 머리말 아래 접힌 「스토리지별 비교」). 스토리지마다
// 합계(경로별 합계 줄과 같은 쿼리 키)를 표 하나로 비교하고, 이름을 누르면 그 스토리지의 경로별로 간다.

installServer();

const PAGE = "/admin/usage/storages";
const table = () => screen.findByRole("table");
const rowOf = (name: string) => within(screen.getByRole("table")).getByRole("link", { name }).closest("tr")!;
const cellsOf = (name: string) => within(rowOf(name)).getAllByRole("cell");
const linkNames = () => within(screen.getByRole("table")).getAllByRole("link").map((a) => a.textContent);

test("머리(h1 = 탭 라벨) + 사실 한 줄 + 비교 표(8열): 스토리지마다 같은 합계 쿼리, 기록 없음·실패·미등록 행, 총계 행 없음, 등록 | 미등록 구분선", async () => {
  const seen: string[] = [];
  setup({ path: PAGE,
          storages: [st("cephfs-dms"), st("empty"), st("zz-broken")],
          summary: [sum("cephfs-dms"), sum("empty", { target_count: 0, succeeded_scan_count: 0, last_scan_at: null }),
                    sum("zz-broken"), sum("old", { registered: false, source: "last_known", backend_type: "gpfs" })],
          unregistered: [unreg("old")],
          totals: { "cephfs-dms": totalsOf("cephfs-dms"), "zz-broken": "error",
                    old: totalsOf("old", { total_bytes: m(734003200, 4), filesystem: null }) },
          onTotals: (u) => seen.push(u.searchParams.get("storage") ?? "") });
  expect(await screen.findByRole("heading", { name: "스토리지별", level: 1 })).toBeInTheDocument();
  const nav = screen.getByRole("navigation", { name: "사용량 분석 하위 메뉴" });
  expect(within(nav).getByRole("link", { name: "스토리지별" })).toHaveAttribute("aria-current", "page");
  expect(within(nav).getByRole("link", { name: "경로별" })).not.toHaveAttribute("aria-current");
  // 필터·스토리지 select 는 없다(보이지 않는 필터가 숫자를 바꾸지 않게 -- 필터는 경로별에만)
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(screen.queryByRole("group", { name: "사용량 필터" })).toBeNull();
  // 사실 한 줄: 등록 · scan 대상(요약의 수) · 성공 scan · 미등록
  // 375 에서 접혀도 어절 안(「1」/「개」)에서 끊지 않는다(break-keep -- 주변 한국어 문단 관용)
  expect(await screen.findByText("등록 3개 · scan 대상 6개 · 성공 scan 12건 · 미등록 1개")).toHaveClass("break-keep");
  const t = await table();
  expect(within(t).getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["스토리지", "실 사용량", "파일", "디렉터리", "hot 비율", "파일시스템(df)", "대상", "최근 스캔"]);
  await waitFor(() => expect(rowOf("old")).toHaveTextContent("700.0 MiB"));
  expect(rowOf("cephfs-dms")).toHaveTextContent("17.0 MiB");
  expect(rowOf("cephfs-dms")).toHaveTextContent("36.0 MiB / 22.8 GiB");
  expect(rowOf("cephfs-dms")).toHaveTextContent("662");
  expect(within(rowOf("cephfs-dms")).getByText("cephfs")).toHaveClass("text-muted");     // 흐린 타입
  expect(rowOf("old")).toHaveTextContent("미등록");
  expect(within(rowOf("old")).getByText("gpfs")).toBeInTheDocument();                    // 미등록은 마지막 설정의 타입
  await waitFor(() => expect(rowOf("zz-broken")).toHaveTextContent("불러오지 못함"));
  // 기록 없음 ≠ 0 B: 다섯 칸 + 최근 스캔이 「—」, 대상 0 은 흐리게, 실 사용량 칸은 이유를 title 로
  await waitFor(() => expect(within(rowOf("empty")).getAllByText("—")).toHaveLength(6));
  expect(cellsOf("empty")[1]).toHaveAttribute("title", "아직 성공한 scan 없음");
  expect(cellsOf("empty")[6]).toHaveTextContent(/^0$/);
  expect(cellsOf("empty")[6]).toHaveClass("text-muted");
  // 최근 스캔 칸: 요약의 last_scan_at(경과 + title 에 KST)
  expect(cellsOf("cephfs-dms")[7]).toHaveTextContent("3일 전");
  // 행 = 스토리지(순서: 등록 이름순 → 미등록), 스토리지끼리 더한 총계 행은 없다 -- 구분선 줄은 보조기기에 숨김
  expect(within(t).getAllByRole("row")).toHaveLength(1 + 4);
  expect(linkNames()).toEqual(["cephfs-dms", "empty", "zz-broken", "old"]);
  const sep = t.querySelector("tr[aria-hidden]")!;
  expect(sep).toHaveTextContent("미등록 — 기록만 남음");
  expect(sep.nextElementSibling).toBe(rowOf("old"));
  expect(screen.getByText(/스토리지끼리는 더하지 않습니다/)).toBeInTheDocument();
  // 스토리지마다 한 번씩(경로별 합계 줄과 같은 쿼리 -- 무필터 ?storage=X)
  expect([...seen].sort()).toEqual(["cephfs-dms", "empty", "old", "zz-broken"]);
});

test("이름 링크(또는 행 아무 곳) = 그 스토리지의 경로별(/admin/usage?storage=X, push) -- 마지막으로 본 스토리지 행은 현재(aria-current · bg-infobg)", async () => {
  setup({ path: "/admin/usage?storage=other&requester=mason",
          storages: [st("cephfs-dms"), st("other")], summary: [sum("cephfs-dms"), sum("other")],
          targets: { "cephfs-dms": TARGETS, other: TARGETS.map((r) => ({ ...r, storage_name: "other", _requesters: ["mason"] })) } });
  await screen.findByRole("button", { name: "artifacts" });
  await userEvent.click(screen.getByRole("link", { name: "스토리지별" }));
  await waitFor(() => expect(pathname()).toBe(PAGE));
  await table();
  expect(within(rowOf("other")).getByRole("link")).toHaveAttribute("aria-current", "true");
  expect(rowOf("other")).toHaveClass("bg-infobg");
  expect(within(rowOf("cephfs-dms")).getByRole("link")).not.toHaveAttribute("aria-current");
  expect(within(rowOf("cephfs-dms")).getByRole("link")).toHaveAttribute("href", "/admin/usage?storage=cephfs-dms");
  // 「경로별」 탭 링크는 마지막 쿼리를 복원한다(target 제외)
  expect(screen.getByRole("link", { name: "경로별" })).toHaveAttribute("href", "/admin/usage?storage=other&requester=mason");
  // 이름 링크: 그 스토리지만 실은 경로별(필터는 싣지 않는다)
  await userEvent.click(within(rowOf("cephfs-dms")).getByRole("link"));
  await waitFor(() => expect(pathname()).toBe("/admin/usage"));
  expect(navType()).toBe("PUSH");
  expect([loc().get("storage"), loc().get("requester")]).toEqual(["cephfs-dms", null]);
  await waitFor(() => expect(storageSelect()).toHaveValue("s:cephfs-dms"));
  // 행 아무 곳(마우스)도 같다
  await userEvent.click(screen.getByRole("link", { name: "스토리지별" }));
  await table();
  await userEvent.click(cellsOf("other")[6]);
  await waitFor(() => expect(pathname()).toBe("/admin/usage"));
  expect(loc().get("storage")).toBe("other");
});

test("열 정렬(페이지 상태): 실 사용량·df 사용률·대상·최근 스캔 -- 값 모름은 방향과 무관하게 맨 뒤, 정렬 중엔 구분선 없음, 「스토리지」 머리가 기본 순서로", async () => {
  const fs = (pct: number) => ({ total_bytes: 1000, used_bytes: pct * 10, node_name: "w1", reported_at: isoAgo(0), fresh: true,
                                node_count: 1, consistent: true });
  setup({ path: PAGE,
          storages: [st("a"), st("b"), st("c")],
          summary: [sum("a", { target_count: 5, last_scan_at: isoAgo(10) }), sum("b", { target_count: 1, last_scan_at: isoAgo(1) }),
                    sum("c", { target_count: 3, last_scan_at: null }),
                    sum("old", { registered: false, source: "last_known", target_count: 9, last_scan_at: isoAgo(100) })],
          unregistered: [unreg("old", { target_count: 9 })],
          totals: { a: totalsOf("a", { total_bytes: m(100, 4), filesystem: fs(50) }),
                    b: totalsOf("b", { total_bytes: m(300, 4), filesystem: fs(95) }),
                    c: "error",
                    old: totalsOf("old", { total_bytes: m(200, 4), filesystem: null }) } });
  await table();
  await waitFor(() => expect(rowOf("b")).toHaveTextContent("300 B"));
  await waitFor(() => expect(rowOf("c")).toHaveTextContent("불러오지 못함"));
  expect(linkNames()).toEqual(["a", "b", "c", "old"]);
  const t = screen.getByRole("table");
  expect(t.querySelector("tr[aria-hidden]")).not.toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "실 사용량 정렬" }));
  expect(linkNames()).toEqual(["b", "old", "a", "c"]);                                   // c(값 모름)는 맨 뒤
  expect(screen.getByRole("button", { name: "실 사용량 정렬: 큰 순" }).closest("th")).toHaveAttribute("aria-sort", "descending");
  expect(t.querySelector("tr[aria-hidden]")).toBeNull();                                  // 정렬 중엔 구분선 없음
  expect(rowOf("old")).toHaveTextContent("미등록");                                      // 배지로만 구별
  await userEvent.click(screen.getByRole("button", { name: "실 사용량 정렬: 큰 순" }));
  expect(linkNames()).toEqual(["a", "old", "b", "c"]);                                   // 오름차순에서도 모름은 맨 뒤
  expect(screen.getByRole("button", { name: "실 사용량 정렬: 작은 순" }).closest("th")).toHaveAttribute("aria-sort", "ascending");
  await userEvent.click(screen.getByRole("button", { name: "파일시스템(df) 정렬" }));
  expect(linkNames()).toEqual(["b", "a", "c", "old"]);                                   // df 없는 c·old 는 기본 순서로 뒤
  expect(screen.getByRole("button", { name: "실 사용량 정렬" }).closest("th")).not.toHaveAttribute("aria-sort");
  await userEvent.click(screen.getByRole("button", { name: "대상 정렬" }));
  expect(linkNames()).toEqual(["old", "a", "c", "b"]);
  await userEvent.click(screen.getByRole("button", { name: "최근 스캔 정렬" }));
  expect(linkNames()).toEqual(["b", "a", "old", "c"]);                                   // 기록 없는 c 는 맨 뒤
  await userEvent.click(screen.getByRole("button", { name: "최근 스캔 정렬: 최신순" }));
  expect(linkNames()).toEqual(["old", "a", "b", "c"]);
  // 「스토리지」 머리 = 기본 순서(등록 이름순 → 구분선 → 미등록)
  await userEvent.click(screen.getByRole("button", { name: "스토리지 정렬" }));
  expect(linkNames()).toEqual(["a", "b", "c", "old"]);
  expect(t.querySelector("tr[aria-hidden]")).not.toBeNull();
  expect(screen.getByRole("button", { name: "스토리지 정렬: 이름순(등록 → 미등록)" })).toBeInTheDocument();
});

test("「최근 스캔」 경과 색은 각주가 범례로 말한다(30일↑ 주의색 · 90일↑ 빨강) · 「스토리지」 머리도 아이콘으로 -- 기본 순서면 지금 기준 ↑, 다른 열 정렬 중이면 흐린 ↕", async () => {
  // 2차 리뷰(2026-10-29): 색이 뜻하는 기준을 색으로만 말했고(경로별 각주엔 있던 범례가 없었다), 「스토리지」 머리는 글자색만 달라
  // 정렬이 안 되는 머리(파일·디렉터리)와 구별되지 않았다.
  setup({ path: PAGE, storages: [st("a"), st("b")],
          summary: [sum("a", { last_scan_at: isoAgo(3) }), sum("b", { last_scan_at: isoAgo(45) }),
                    sum("old", { registered: false, source: "last_known", last_scan_at: isoAgo(200) })],
          unregistered: [unreg("old")],
          totals: { a: totalsOf("a"), b: totalsOf("b"), old: totalsOf("old") } });
  await table();
  expect(within(cellsOf("a")[7]).getByText("3일 전")).not.toHaveClass("text-attn");
  expect(within(cellsOf("b")[7]).getByText("45일 전")).toHaveClass("text-attn");
  expect(within(cellsOf("old")[7]).getByText("200일 전")).toHaveClass("text-bad");
  const note = screen.getByText(/스토리지끼리는 더하지 않습니다/);
  expect(note).toHaveTextContent(/최근 스캔 30일↑ · 90일↑$/);
  expect(within(note).getByText("30일↑")).toHaveClass("text-attn");
  expect(within(note).getByText("90일↑")).toHaveClass("text-bad");
  const head = () => within(screen.getByRole("table")).getByRole("button", { name: /^스토리지 정렬/ });
  expect(head()).toHaveAccessibleName("스토리지 정렬: 이름순(등록 → 미등록)");
  expect(head().querySelector("svg.lucide-arrow-up")).not.toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "대상 정렬" }));
  expect(head()).toHaveAccessibleName("스토리지 정렬");
  const icon = head().querySelector("svg");
  expect(icon).toHaveClass("lucide-arrow-up-down");
  expect(icon).toHaveClass("text-muted/50");
  await userEvent.click(head());
  expect(head().querySelector("svg.lucide-arrow-up")).not.toBeNull();
});

test("창을 넘어 잘린 합은 「이상」(부분 합) · 대상 칸은 요약(select 옵션)과 같은 수 · 각주가 두 뜻을 말한다", async () => {
  // 리뷰: 1만 넘는 스토리지의 행이 최근 스캔된 1만 개만 더한 값을 표식 없이 보였고, 대상 칸은 잘린 10,000 이라 탭(12,345)과 달랐다.
  setup({ path: PAGE, storages: [st("cephfs-dms"), st("weka")], summary: [sum("cephfs-dms"), sum("weka", { target_count: 12345 })],
          windowLimit: 10000,
          totals: { "cephfs-dms": totalsOf("cephfs-dms"),
                    weka: totalsOf("weka", { truncated: true, target_count: 10000, summed_target_count: 9876,
                                             total_bytes: m(1024 ** 4, 9876), total_files: m(3551020, 9876),
                                             total_directories: m(98113, 9876),
                                             hot_ratio: { value: 0.91, hot_bytes: 91, total_bytes: 100, known: 9876,
                                                          unknown: 0 } }) } });
  await table();
  await waitFor(() => expect(rowOf("weka")).toHaveTextContent("1.0 TiB이상"));
  const weka = cellsOf("weka").map((c) => c.textContent);
  expect([weka[1], weka[2], weka[3], weka[4], weka[6]]).toEqual(["1.0 TiB이상", "3,551,020이상", "98,113이상", "91%", "12,345"]);
  expect(cellsOf("weka")[6]).toHaveAttribute("title", "합계는 최근 스캔된 10,000개 대상 기준 부분 합 · 최상위 9,876개 합계");
  // 잘리지 않고 모름도 없으면 표식 없음 -- 대상 칸은 요약과 같은 수
  await waitFor(() => expect(rowOf("cephfs-dms")).toHaveTextContent("17.0 MiB"));
  expect(rowOf("cephfs-dms")).not.toHaveTextContent("이상");
  expect(cellsOf("cephfs-dms")[6]).toHaveTextContent(/^2$/);
  expect(cellsOf("cephfs-dms")[6]).toHaveAttribute("title", "최상위 4개 합계");
  expect(screen.getByText(/스토리지끼리는 더하지 않습니다/)).toHaveTextContent(
    "「이상」 = 값을 모르는 대상(요약 읽는 중 포함)을 뺀 합이거나 대상이 많아 최근 스캔된 10,000개 대상만 더한 부분 합");
});

test("요약이 없으면 대상 칸은 합계의 수 -- 잘렸으면 「+」, 각주는 창 크기를 지어내지 않는다 · 알림 자리에 「건수 없이」", async () => {
  setup({ path: PAGE, storages: [st("cephfs-dms"), st("weka")], summary: "error", scanStorages: ["cephfs-dms", "weka"],
          totals: { "cephfs-dms": totalsOf("cephfs-dms"),
                    weka: totalsOf("weka", { truncated: true, target_count: 10000, summed_target_count: 9876 }) } });
  expect(await screen.findByText("스토리지별 대상 수를 불러오지 못해 건수 없이 표시합니다")).toBeInTheDocument();
  await table();
  await waitFor(() => expect(cellsOf("weka")[6]).toHaveTextContent(/^10,000\+$/));
  expect(cellsOf("cephfs-dms")[6]).toHaveTextContent(/^9$/);
  expect(screen.getByText(/스토리지끼리는 더하지 않습니다/)).toHaveTextContent("최근 스캔된 창 안의 대상만 더한 부분 합");
  // 수를 모르는 조각은 사실 줄에서 뺀다(모름 ≠ 0)
  expect(screen.getByText("등록 2개")).toBeInTheDocument();
});

test("스토리지가 하나여도 표가 있다 · 경로별을 본 적이 없으면 현재 행이 없다 · 기억을 못 읽어도(차단 환경) 표는 그대로", async () => {
  sessionStorage.setItem(PATHS_MEMORY_KEY, "{broken");
  setup({ path: PAGE });
  await table();
  await waitFor(() => expect(rowOf("cephfs-dms")).toHaveTextContent("17.0 MiB"));
  expect(within(rowOf("cephfs-dms")).getByRole("link")).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("link", { name: "경로별" })).toHaveAttribute("href", "/admin/usage");
});

test("등록된 스토리지도 기록도 없으면 등록 안내", async () => {
  setup({ path: PAGE, storages: [], summary: [] });
  expect(await screen.findByText("등록된 스토리지가 없습니다")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "스토리지 화면으로" })).toHaveAttribute("href", "/admin/storages");
  expect(screen.queryByRole("table")).toBeNull();
});
