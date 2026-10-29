import { act, cleanup, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test } from "vitest";
import {
  HISTORY, NFS, TARGETS, chooseStorage, closeSheet, installServer, isoAgo, loc, navType, pad10, panelOf, point, pvSetup,
  setup, sheetOf, sizeHist, st, storageSelect, sum,
} from "./usageTestKit";
import { ANNOUNCE_MS } from "./TargetSheet";

// 경로 상세 시트(2026-10-29 사용자 결정 「경로 상세는 표 행 펼침 대신 오른쪽 시트」 -- ?target=, TargetSheet + TargetDetail).
// URL·포커스 규칙(열기 push · ↑↓ replace · 닫기 -1 또는 replace · 시트 안 필터 = 한 번의 replace)과 본문(소속·최신 scan·분포·추이·
// 온도·이력). 경로별 화면 위에 덮이므로 경로별 페이지를 그대로 그린다(usageTestKit.setup).

installServer();


// ---- 경로 상세 시트(2026-10-29 -- 행 아래 펼침 대신 오른쪽 모달 시트) ----

test("행 → 시트: 머리(경로·위치) + 한 줄 수치·추이 차트·미상 고지·접힌 스캔 이력, 연 행은 bg-infobg, 포커스는 제목", async () => {
  setup();
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const sheet = await sheetOf("artifacts");
  // 머리: h2 = 경로(등폭·한 번에 전체 선택), 아래 흐린 「스토리지 · 목록 i/n번째」, 포커스는 제목(tabIndex -1)
  const title = within(sheet).getByRole("heading", { name: "artifacts", level: 2 });
  expect(title).toHaveClass("font-mono", "select-all");
  await waitFor(() => expect(title).toHaveFocus());
  expect(sheet).toHaveTextContent("cephfs-dms · 목록 1/2번째");
  // 수치 줄: 최신 3.0 KiB, 직전(1.0 KiB) 대비 +2.0 KiB, hot 100%(atime 첫 버킷 전부). 값은 dt 의 다음 형제 dd.
  const stat = async (label: string) => (await within(sheet).findByText(label, { selector: "dt" })).nextElementSibling;
  expect(await stat("최신 실 사용량")).toHaveTextContent("3.0 KiB");
  expect(await stat("직전 스캔 대비")).toHaveTextContent("+2.0 KiB");
  expect(await stat("hot 비율(180일)")).toHaveTextContent("100%");
  expect(await stat("스캔 이력")).toHaveTextContent("3건");
  // 차트: 용량 아는 2점만(j2 는 미상), 고지 문구가 사실을 말한다
  expect(within(sheet).getByRole("group", { name: "실 사용량 추이" })).toBeInTheDocument();
  expect(within(sheet).getByText(/용량 미상 1건은 차트에서 제외/)).toBeInTheDocument();
  expect(within(sheet).getByText(/리포트를 읽지 못한 스캔 1건 제외/)).toBeInTheDocument();
  // 스캔 이력 표는 <details> 기본 닫힘 -- 요약 「스캔 이력 3건」, 열면 최신이 위(reverse)·요청 상세 링크
  const summary = within(sheet).getByText("스캔 이력 3건");
  const details = summary.closest("details")!;
  expect(details.open).toBe(false);
  await userEvent.click(summary);
  expect(details.open).toBe(true);
  expect(within(details).getByRole("link", { name: "r3" })).toHaveAttribute("href", "/jobs/r3");
  expect(within(details).getAllByText("alice")).toHaveLength(HISTORY.points.length);
  // 시트가 연 행은 bg-infobg(뒤 화면은 모달 동안 aria-hidden -- hidden 으로 찾는다). 표는 펼침 행 없이 그대로 2행.
  const row = screen.getByRole("button", { name: "artifacts", hidden: true }).closest("tr")!;
  expect(row).toHaveClass("bg-infobg");
  expect(screen.getAllByRole("row", { hidden: true }).filter((r) => r.closest("tbody") !== null
    && r.closest("[role=dialog]") === null)).toHaveLength(2);
});

test("시트 닫기는 storage 를 남긴다(기본 스토리지로 튀지 않게) -- 기본 스토리지에서 열면 storage 가 URL 에 실리고, 닫으면(뒤로) 보던 스토리지 그대로", async () => {
  setup({ storages: [st("a-first"), st("cephfs-dms")],
          summary: [sum("a-first"), sum("cephfs-dms")],
          targets: { "a-first": [{ ...TARGETS[0], storage_name: "a-first", target: "x" }], "cephfs-dms": TARGETS } });
  await screen.findByRole("button", { name: "x" });
  await chooseStorage("cephfs-dms");
  await userEvent.click(await screen.findByRole("button", { name: "team" }));
  await sheetOf("team");
  expect([loc().get("storage"), loc().get("target")]).toEqual(["cephfs-dms", "team"]);
  await closeSheet();
  expect([loc().get("storage"), loc().get("target")]).toEqual(["cephfs-dms", null]);
  expect(storageSelect()).toHaveValue("s:cephfs-dms");
  cleanup();
  // 기본 스토리지(URL 에 storage 없음)에서 열어도 storage 를 적는다 -- 닫기(뒤로)는 열기 전 URL 로, 기본 스토리지는 붙잡혀 그대로다
  setup({ storages: [st("cephfs-dms"), st("z-other")], summary: [sum("cephfs-dms"), sum("z-other")] });
  await userEvent.click(await screen.findByRole("button", { name: "team" }));
  await sheetOf("team");
  expect([loc().get("storage"), loc().get("target")]).toEqual(["cephfs-dms", "team"]);
  await closeSheet();
  expect(navType()).toBe("POP");
  expect([loc().get("storage"), loc().get("target")]).toEqual([null, null]);
  expect(storageSelect()).toHaveValue("s:cephfs-dms");
});

test("시트 URL·포커스: 열기 push → ✕·Esc 는 뒤로(-1) 닫고 포커스는 그 행 경로 버튼으로 · 행 아무 곳이나 눌러도 연다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms" });
  const artifacts = await screen.findByRole("button", { name: "artifacts" });
  artifacts.focus();
  await userEvent.keyboard("{Enter}");                                              // 키보드로 연다(경로 버튼)
  await sheetOf("artifacts");
  expect(navType()).toBe("PUSH");
  expect(loc().get("target")).toBe("artifacts");
  await closeSheet();
  expect(navType()).toBe("POP");                                                    // 뒤로 -- 히스토리에 죽은 칸이 없다
  expect([loc().get("storage"), loc().get("target")]).toEqual(["cephfs-dms", null]);
  await waitFor(() => expect(screen.getByRole("button", { name: "artifacts" })).toHaveFocus());
  // 행의 다른 칸을 눌러도 같다(마우스) -- Esc 로 닫는다
  const teamRow = screen.getByRole("button", { name: "team" }).closest("tr")!;
  await userEvent.click(within(teamRow).getAllByRole("cell")[4]);
  await sheetOf("team");
  expect(navType()).toBe("PUSH");
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(loc().get("target")).toBeNull();
  await waitFor(() => expect(screen.getByRole("button", { name: "team" })).toHaveFocus());
});

test("검색 디바운스 중에 시트를 열어도 그 검색은 지금 항목에 실린다 -- 닫고(뒤로) 돌아와도 검색칸·URL 이 그대로", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms", filterPath: true });
  await screen.findByRole("button", { name: "artifacts" });
  await userEvent.type(screen.getByLabelText("경로 검색"), "tea");
  await userEvent.click(screen.getByRole("button", { name: "team" }));             // 300ms 안
  await sheetOf("team");
  expect(navType()).toBe("PUSH");
  expect([loc().get("q"), loc().get("target")]).toEqual(["tea", "team"]);
  await new Promise((r) => setTimeout(r, 400));                                     // 디바운스가 시트 항목을 건드리지 않는다
  expect(navType()).toBe("PUSH");
  await closeSheet();
  expect(navType()).toBe("POP");
  expect([loc().get("q"), loc().get("target")]).toEqual(["tea", null]);
  expect(screen.getByLabelText("경로 검색")).toHaveValue("tea");
});

// 끝에 닿은 이전·다음은 aria-disabled(포커스 가능 -- disabled 면 실 브라우저가 포커스를 <body> 로 떨어뜨린다, TargetSheet 머리 주석).
const dimmed = (b: HTMLElement) => { expect(b).toHaveAttribute("aria-disabled", "true"); expect(b).not.toHaveAttribute("disabled"); };
const usable = (b: HTMLElement) => expect(b).not.toHaveAttribute("aria-disabled");
const sheetStatus = (sheet: HTMLElement) => within(sheet).getAllByRole("status").find((el) => el.classList.contains("sr-only"))!;

test("시트 ↑↓: 지금 목록의 이웃으로 replace(끝에서 비활성) -- 닫으면 뒤로 한 번, 포커스는 마지막 경로의 행", async () => {
  const rows = ["a1", "a2", "a3"].map((t, i) => ({ ...TARGETS[0], target: t, last_scan_at: isoAgo(i + 1) }));
  const seen: (string | null)[] = [];
  setup({ path: "/admin/usage?storage=cephfs-dms", targets: { "cephfs-dms": rows },
          onHistory: (u) => seen.push(u.searchParams.get("target")) });
  await userEvent.click(await screen.findByRole("button", { name: "a1" }));
  let sheet = await sheetOf("a1");
  expect(sheet).toHaveTextContent("cephfs-dms · 목록 1/3번째");
  dimmed(within(sheet).getByRole("button", { name: "이전 경로" }));
  expect(sheetStatus(sheet)).toHaveTextContent(/^$/);                              // 열 때는 제목 포커스가 말한다
  await userEvent.click(within(sheet).getByRole("button", { name: "다음 경로" }));
  sheet = await sheetOf("a2");
  expect(navType()).toBe("REPLACE");
  expect(loc().get("target")).toBe("a2");
  expect(sheet).toHaveTextContent("cephfs-dms · 목록 2/3번째");
  // 포커스는 누른 버튼에 남고(연달아 누를 수 있게), 옮긴 결과는 상태 줄이 알린다
  expect(within(sheet).getByRole("button", { name: "다음 경로" })).toHaveFocus();
  expect(sheetStatus(sheet)).toHaveTextContent(/^a2 · 목록 2\/3번째$/);
  await userEvent.click(within(sheet).getByRole("button", { name: "다음 경로" }));
  sheet = await sheetOf("a3");
  const down = within(sheet).getByRole("button", { name: "다음 경로" });
  dimmed(down);
  expect(down).toHaveFocus();                                                       // 끝에 닿아도 포커스가 시트 안에 남는다
  usable(within(sheet).getByRole("button", { name: "이전 경로" }));
  expect(sheetStatus(sheet)).toHaveTextContent(/^a3 · 목록 3\/3번째$/);
  await userEvent.click(down);                                                      // 끝에서 누르면 아무 일 없다
  expect([loc().get("target"), navType()]).toEqual(["a3", "REPLACE"]);
  await waitFor(() => expect(seen).toEqual(expect.arrayContaining(["a1", "a2", "a3"])));
  // 이동이 히스토리를 쌓지 않았고 push 표지(location.state)를 지켰다 -- 닫기는 replace 가 아니라 뒤로 한 번(POP)이고 열기 전 URL 이다
  await closeSheet();
  expect(navType()).toBe("POP");
  expect([loc().get("storage"), loc().get("target")]).toEqual(["cephfs-dms", null]);
  await waitFor(() => expect(screen.getByRole("button", { name: "a3" })).toHaveFocus());
  // 다시 열면 상태 줄은 비어 있다(옛 이동 문장을 싣고 나오지 않는다)
  await userEvent.click(screen.getByRole("button", { name: "a3" }));
  expect(sheetStatus(await sheetOf("a3"))).toHaveTextContent(/^$/);
});

test("이동 알림은 잠시 뒤 비운다(읽기 모드에서 제목·부제와 같은 경로·위치를 두 번 읽지 않게) -- 빈 뒤에 다시 옮기면 다시 알린다 · 닫기는 이전·다음 묶음과 떨어져 있다", async () => {
  // 2차 리뷰(2026-10-29): 상태 줄이 시트를 닫을 때까지 「경로 · 목록 i/n번째」를 들고 있어, 가상 커서로 읽으면 바로 위 h2·부제와
  // 같은 내용이 한 번 더 읽혔다. 또 375 에서 36px 버튼이 4px 간격이라 [다음]을 누르려다 [닫기]를 눌렀다.
  const rows = ["a1", "a2", "a3"].map((t, i) => ({ ...TARGETS[0], target: t, last_scan_at: isoAgo(i + 1) }));
  setup({ path: "/admin/usage?storage=cephfs-dms", targets: { "cephfs-dms": rows } });
  await userEvent.click(await screen.findByRole("button", { name: "a1" }));
  let sheet = await sheetOf("a1");
  await userEvent.click(within(sheet).getByRole("button", { name: "다음 경로" }));
  sheet = await sheetOf("a2");
  expect(sheetStatus(sheet)).toHaveTextContent(/^a2 · 목록 2\/3번째$/);
  await waitFor(() => expect(sheetStatus(sheet)).toHaveTextContent(/^$/), { timeout: ANNOUNCE_MS + 1500 });
  expect(sheet).toHaveTextContent("cephfs-dms · 목록 2/3번째");                    // 부제(보이는 위치)는 그대로
  await userEvent.click(within(sheet).getByRole("button", { name: "이전 경로" }));
  sheet = await sheetOf("a1");
  expect(sheetStatus(sheet)).toHaveTextContent(/^a1 · 목록 1\/3번째$/);
  const up = within(sheet).getByRole("button", { name: "이전 경로" });
  const down = within(sheet).getByRole("button", { name: "다음 경로" });
  const close = within(sheet).getByRole("button", { name: "닫기" });
  expect(down.parentElement).toBe(up.parentElement);                               // 이전·다음은 한 묶음(붙여 둔다)
  expect(close.parentElement).not.toBe(down.parentElement);
  expect(close.parentElement).toContainElement(down.parentElement);
  expect(close.parentElement).toHaveClass("gap-3");                                // 닫기와 묶음 사이 12px
});

test("시트 안 도움말(?)이 열린 채 Esc 는 툴팁만 닫는다 -- 시트·URL 은 그대로, 다음 Esc 가 시트를 닫는다(WCAG 1.4.13)", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms" });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const sheet = await sheetOf("artifacts");
  const help = await within(sheet).findByRole("button", { name: "파일 크기 분포 설명" });
  act(() => help.focus());                                                          // 포커스로 열린다(HelpTip)
  expect(await screen.findByRole("tooltip")).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("tooltip")).toBeNull();
  expect(screen.getByRole("dialog", { name: "artifacts" })).toBeInTheDocument();
  expect([loc().get("target"), navType()]).toEqual(["artifacts", "PUSH"]);
  expect(help).toHaveFocus();
  await userEvent.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect([loc().get("target"), navType()]).toEqual([null, "POP"]);
});

test("시트 ↑↓ 는 상세를 새로 그린다 -- 표시 창(60건) 선택이 이웃 경로에 묻어 가지 않는다", async () => {
  const limits: string[] = [];
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts",
          onHistory: (u) => limits.push(`${u.searchParams.get("target")}:${u.searchParams.get("limit")}`) });
  const sheet = await sheetOf("artifacts");
  await within(sheet).findByText("최신 실 사용량");
  await userEvent.click(within(sheet).getByRole("button", { name: "최근 60건" }));
  await waitFor(() => expect(limits).toContain("artifacts:60"));
  await userEvent.click(within(sheet).getByRole("button", { name: "다음 경로" }));
  const team = await sheetOf("team");
  await waitFor(() => expect(limits).toContain("team:30"));
  expect(within(team).getByRole("button", { name: "최근 30건" })).toHaveAttribute("aria-pressed", "true");
});

test("옛 딥링크 ?storage=&target= 은 시트가 열린 채로 -- 닫기는 target 만 지우는 replace(뒤로 가지 않는다)", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=team&requester=mason",
          targets: { "cephfs-dms": TARGETS.map((r) => ({ ...r, _requesters: ["mason"] })) } });
  const sheet = await sheetOf("team");
  expect(await within(sheet).findByText("최신 실 사용량")).toBeInTheDocument();
  await waitFor(() => expect(sheet).toHaveTextContent("cephfs-dms · 목록 2/2번째"));
  await closeSheet();
  expect(navType()).toBe("REPLACE");
  expect([loc().get("storage"), loc().get("target"), loc().get("requester")]).toEqual(["cephfs-dms", null, "mason"]);
  await waitFor(() => expect(screen.getByRole("button", { name: "team" })).toHaveFocus());
});

test("리포트를 못 읽은 스캔만 있으면 「scan 이 없다」가 아니라 읽지 못했다고 말한다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts",
          history: { ...HISTORY, points: [], skipped_unreadable: 2 } });
  expect(await screen.findByText("성공 scan 2건의 리포트를 읽지 못해 표시할 이력이 없습니다")).toBeInTheDocument();
});

test("목록 밖 target(옛 딥링크·검색으로 빠짐): 시트가 target 만으로 열리고 「지금 목록 밖」 한 줄 + 이전·다음 없음 -- 표 아래 폴백 칸은 없다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts", targets: { "cephfs-dms": [TARGETS[1]] } });
  const sheet = await sheetOf("artifacts");
  expect(await within(sheet).findByText("지금 목록(검색·필터·상위 100개) 밖의 경로입니다 — 이전/다음 없음")).toBeInTheDocument();
  dimmed(within(sheet).getByRole("button", { name: "이전 경로" }));
  dimmed(within(sheet).getByRole("button", { name: "다음 경로" }));
  expect(sheet).not.toHaveTextContent("목록 1/");
  expect(await within(sheet).findByText("최신 실 사용량")).toBeInTheDocument();
  expect(screen.queryByText(/에 없어 아래에 표시합니다/)).toBeNull();
  expect(within(screen.getByRole("region", { name: "cephfs-dms", hidden: true }))
    .queryByRole("region", { name: /상세/, hidden: true })).toBeNull();
  // 닫으면 그 행이 표에 없다 -- 포커스는 <body> 가 아니라 툴바(필터 줄이 없는 filesystem 스토리지)로
  await closeSheet();
  await waitFor(() => expect(document.activeElement).not.toBe(document.body));
  expect(panelOf("cephfs-dms").contains(document.activeElement)).toBe(true);
});

test("목록이 오기 전엔 「목록 밖」이라 말하지 않는다(깜빡임) -- 시트는 바로 열리고 이력 요청도 바로 나간다", async () => {
  const seen: string[] = [];
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts", slowTargets: "cephfs-dms",
          onHistory: (u) => seen.push(u.searchParams.get("target") ?? "") });
  const sheet = await sheetOf("artifacts");
  await waitFor(() => expect(seen).toEqual(["artifacts"]));
  expect(within(sheet).queryByText(/밖의 경로입니다/)).toBeNull();
  dimmed(within(sheet).getByRole("button", { name: "다음 경로" }));
});

test("온도 추이: atime 기본 + mtime 세그먼트(aria-pressed)", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts" });
  expect(await screen.findByRole("img", { name: "데이터 온도 추이(atime)" })).toBeInTheDocument();
  const axis = screen.getByRole("group", { name: "온도 축" });
  expect(within(axis).getByRole("button", { name: "atime" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(within(axis).getByRole("button", { name: "mtime" }));
  expect(screen.getByRole("img", { name: "데이터 온도 추이(mtime)" })).toBeInTheDocument();
  expect(within(axis).getByRole("button", { name: "mtime" })).toHaveAttribute("aria-pressed", "true");
  // 긴 캡션은 제목 옆 설명(HelpTip) -- 본문에 상시로 깔지 않는다
  expect(screen.getByRole("button", { name: "데이터 온도 추이 설명" })).toBeInTheDocument();
  // atime 축의 근사 이유는 스토리지·마운트(2026-10-27 d192, 괴리 [C3] -- 테스트베드 CephFS 는 읽어도 atime 이 그대로였다)
  await userEvent.click(within(axis).getByRole("button", { name: "atime" }));
  const help = screen.getByRole("button", { name: "데이터 온도 추이 설명" });
  expect(help).toHaveAccessibleDescription(/읽기가 atime 에 반영되는지는 마운트·스토리지에 따라 달라 근사치입니다/);
  expect(help).toHaveAccessibleDescription(/반영하지 않는 곳에선 최근에 쓴 데이터가 hot 으로 보입니다/);
  // relatime 은 읽기를 하루 단위로 반영한다 -- 「반영 안 하는」 쪽(noatime·일부 CephFS)에 넣지 않는다(2026-10-28 리뷰
  // portal-batch-27: 예전 「(relatime·noatime, CephFS 등)」은 relatime 스토리지에서 open_noatime 을 꺼도 된다고 읽혔다).
  const rule = "리눅스 기본 relatime 은 읽기를 파일마다 많아야 하루에 한 번 반영하고, noatime 마운트나 일부 CephFS 는 반영하지 않습니다";
  expect(help).toHaveAccessibleDescription(new RegExp(rule));
  const hotHelp = screen.getByRole("button", { name: "hot 비율 설명", hidden: true });   // 표 머리(시트 뒤)
  expect(hotHelp).toHaveAccessibleDescription(new RegExp(rule));
  expect(hotHelp).toHaveAccessibleDescription(
    /읽기가 atime 을 바꾸지 않는 스토리지\(noatime 마운트, 일부 CephFS 등\)에선 최근에 쓴 데이터 비중에 가까운 근사치/);
  for (const el of [help, hotHelp]) expect(el).not.toHaveAccessibleDescription(/relatime·noatime/);
});

test("온도 추이 열 호버 즉시 툴팁(시간·요청자·용량)", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts" });
  const chart = await screen.findByRole("img", { name: "데이터 온도 추이(atime)" });
  expect(screen.queryByRole("tooltip")).toBeNull();
  const col = within(chart).getAllByRole("button")[0];
  await userEvent.hover(col);
  const tip = screen.getByRole("tooltip");
  expect(tip).toHaveTextContent("2026-08-22 21:00:00 KST");     // j1 generated_at_epoch 1787400000
  expect(tip).toHaveTextContent("요청자alice");
  expect(tip).toHaveTextContent("실 사용량1.0 KiB");
  expect(tip).toHaveTextContent("파일 수7개");
  expect(tip).toHaveTextContent("hot 비율(atime 180일)100%");
  const legend = within(tip).getByLabelText("구간별 용량");
  expect(legend).toHaveTextContent("1일 이내1.0 KiB100%");
  expect(legend).toHaveTextContent("1~2일0 B0%");
  expect(tip).toHaveTextContent("빨강 → 파랑 = 최근 → 오래됨");
  // 모달 시트 밖(body)은 Radix 가 pointer-events: none 이라 userEvent 가 포인터를 치우지 못한다 -- 떠남 이벤트만 직접 낸다.
  fireEvent.mouseLeave(col);
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("온도 툴팁: mtime 축이면 그 축의 180일 이내 비중과 atime hot 비율을 따로 말한다", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts" });
  await screen.findByRole("img", { name: "데이터 온도 추이(atime)" });
  await userEvent.click(screen.getByRole("button", { name: "mtime" }));
  const chart = await screen.findByRole("img", { name: "데이터 온도 추이(mtime)" });
  await userEvent.hover(within(chart).getAllByRole("button")[0]);
  const tip = screen.getByRole("tooltip");
  expect(tip).toHaveTextContent("hot 비율(atime 180일)100%");
  expect(tip).toHaveTextContent("mtime 180일 이내100%");
});

test("atime 이 없는 이력은 첫 가용 축(mtime)으로 자동 대체 -- '분포 없음' 거짓 방지", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts",
          history: { ...HISTORY, points: HISTORY.points.map((p) => ({ ...p, time_histograms: { mtime: p.time_histograms.mtime } })) } });
  expect(await screen.findByRole("img", { name: "데이터 온도 추이(mtime)" })).toBeInTheDocument();
  expect(screen.queryByText("이 축의 온도 분포가 있는 스캔이 없습니다")).toBeNull();
});

test("표시 창: 「최근 30건」이 늘 눌려 있고(상시 창 안내), window_full 이면 「이전 이력 있음」", async () => {
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts",
          history: { ...HISTORY, window_limit: 30, window_full: true } });
  expect(await screen.findByText("이전 이력 있음")).toBeInTheDocument();
  const win = screen.getByRole("group", { name: "이력 표시 창" });
  expect(within(win).getByRole("button", { name: "최근 30건" })).toHaveAttribute("aria-pressed", "true");
});

test("표시 창 선택: 최근 60건 클릭 -> limit=60 으로 재조회", async () => {
  const limits: (string | null)[] = [];
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts", history: { ...HISTORY, window_full: false },
          onHistory: (u) => limits.push(u.searchParams.get("limit")) });
  await screen.findByText("최신 실 사용량");
  expect(limits).toEqual(["30"]);
  expect(screen.queryByText("이전 이력 있음")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "최근 60건" }));
  await waitFor(() => expect(limits).toEqual(["30", "60"]));
  expect(screen.getByRole("button", { name: "최근 60건" })).toHaveAttribute("aria-pressed", "true");
});

// ---- 파일 크기 분포(2026-10-21 「최근 스캔 기준 파일의 사이즈별 정보」) ----

// 상세 = 시트(2026-10-29). 다른 경로로는 시트의 「다음 경로」로 간다(모달이라 뒤 표를 누를 수 없다).
const detailOf = (target: string) => sheetOf(target);
const nextPath = () => userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "다음 경로" }));
// 상세의 분포: 이력의 다른 포인트(이전 scan)에 다른 분포를 심어, 기준이 무엇인지 가려낸다.
const OLDER_SIZES = { ...HISTORY, points: HISTORY.points.map((p) => ({ ...p, file_size_histogram: sizeHist(pad10([1])) })) };

test("펼친 상세: 「최신 scan」 블록 = 수치 줄 | 파일 크기 분포 -- 기준은 목록 행의 최신 scan(이력의 다른 포인트가 아님)", async () => {
  setup({ targets: { "cephfs-dms": [{ ...TARGETS[0], latest: point({ file_size_histogram: sizeHist(pad10([22, 0, 0, 4])) }) },
                                    TARGETS[1]] },
          history: OLDER_SIZES });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const region = await detailOf("artifacts");
  const dist = await within(region).findByRole("list", { name: "파일 크기 분포" });
  expect(within(dist).getAllByRole("listitem")).toHaveLength(10);              // 0개 구간도 줄로 남는다
  expect(within(dist).getByText("4 KiB 이하 22개 85%")).toBeInTheDocument();
  expect(within(dist).getByText("1–16 MiB 4개 15%")).toBeInTheDocument();
  expect(within(dist).getByText("4 TiB 초과 0개 0%")).toBeInTheDocument();
  expect(within(dist).queryByText(/4 KiB 이하 1개/)).toBeNull();               // 이력(이전 scan)의 분포가 아니다
  expect(within(region).getByText("최신 scan · 파일 26개")).toBeInTheDocument();
  expect(within(region).getByRole("button", { name: "파일 크기 분포 설명" })).toBeInTheDocument();
  // 수치 줄은 그대로 같은 블록에 있다
  expect(within(region).getByRole("list", { name: "파일 크기 분포" })).toBeInTheDocument();
  expect(within(region).getByText("최신 실 사용량", { selector: "dt" })).toBeInTheDocument();
});

test("목록 행에 최신 정보가 없으면(딥링크 폴백 포함) 이력에서 시각이 가장 늦은 포인트의 분포(서버 순서가 아니다)", async () => {
  const pts = [
    point({ job_id: "j1", request_id: "r1", generated_at_epoch: 1787400000, file_size_histogram: sizeHist(pad10([1])) }),
    point({ job_id: "j3", request_id: "r3", generated_at_epoch: 1787500000, file_size_histogram: sizeHist(pad10([5, 5])) }),
    point({ job_id: "j2", request_id: "r2", generated_at_epoch: 1787450000, file_size_histogram: sizeHist(pad10([9])) }),
  ];
  setup({ path: "/admin/usage?storage=cephfs-dms&target=artifacts", history: { ...HISTORY, points: pts } });
  const region = await detailOf("artifacts");
  expect(await within(region).findByText("4 KiB 이하 5개 50%")).toBeInTheDocument();
  expect(within(region).getByText("최신 scan · 파일 10개")).toBeInTheDocument();
});

test("분포 모름: 최신 scan 리포트를 못 읽었으면 그렇게 말하고(옛 scan 분포로 대신하지 않는다), 구형 리포트면 「기록 없음」", async () => {
  setup({ targets: { "cephfs-dms": [
            { ...TARGETS[0], latest: point({ report_readable: false, summary: {}, time_histograms: {}, file_size_histogram: [] }) },
            { ...TARGETS[1], latest: point({ file_size_histogram: [] }) }] },
          history: OLDER_SIZES });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const region = await detailOf("artifacts");
  expect(await within(region).findByText("최신 scan 의 리포트를 읽지 못해 크기 분포를 알 수 없습니다")).toBeInTheDocument();
  expect(within(region).queryByRole("list", { name: "파일 크기 분포" })).toBeNull();
  expect(within(region).queryByText(/최신 scan · 파일/)).toBeNull();
  await nextPath();
  const team = await detailOf("team");
  expect(await within(team).findByText("크기 분포 기록이 없습니다(구형 리포트)")).toBeInTheDocument();
  expect(within(team).queryByRole("list", { name: "파일 크기 분포" })).toBeNull();     // 0 막대 10줄로 그리지 않는다
});

// ---- 2026-10-26 dscan 포크 ae781bd: 실 사용량 정확값(summary.total_bytes)·잘못된 인코딩 이름(카운터만) ----

test("펼친 상세: 최신 실 사용량·이력 표의 용량은 title 에 정확한 바이트, 잘못된 인코딩 이름은 1개 이상일 때만 한 줄", async () => {
  setup({ targets: { "cephfs-dms": [
            { ...TARGETS[0], latest: point({ total_bytes: 3072, invalid_name_entries: 15 }) },
            { ...TARGETS[1], latest: point({ invalid_name_entries: 0 }) }] } });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const region = await detailOf("artifacts");
  const latest = (await within(region).findByText("최신 실 사용량", { selector: "dt" })).nextElementSibling;
  expect(latest).toHaveTextContent("3.0 KiB");
  expect(latest).toHaveAttribute("title", "3,072 B");
  expect(within(region).getByText("최신 scan: 잘못된 인코딩 이름 15개")).toHaveClass("text-attn");
  expect(within(region).getByRole("button", { name: "잘못된 인코딩 이름 설명" })).toBeInTheDocument();
  const details = within(region).getByText("스캔 이력 3건").closest("details")!;
  expect(within(details).getByText("3.0 KiB").closest("td")).toHaveAttribute("title", "3,072 B");
  // 0 은 말하지 않는다(펼침마다 줄이 늘지 않게 -- 전수 값은 CSV)
  await nextPath();
  const team = await detailOf("team");
  await within(team).findByText("최신 실 사용량", { selector: "dt" });
  expect(within(team).queryByText(/잘못된 인코딩 이름/)).toBeNull();
});

test("펼친 상세: 잘못된 인코딩 이름을 모르면(구형 리포트·구형 서버) 아무 말도 하지 않는다 -- 0 으로 지어내지 않는다", async () => {
  setup({ targets: { "cephfs-dms": [{ ...TARGETS[0], latest: point({ invalid_name_entries: null }) }, TARGETS[1]] } });
  await userEvent.click(await screen.findByRole("button", { name: "artifacts" }));
  const region = await detailOf("artifacts");
  await within(region).findByText("최신 실 사용량", { selector: "dt" });
  expect(within(region).queryByText(/잘못된 인코딩 이름/)).toBeNull();
});

// ---- PV 소속(2026-10-29 필터 -- 픽스처는 usageTestKit 의 NFS_ROWS) ----

test("시트 머리: PV 소속(정체 전부)·권한 사용자(없으면 관리자·특권 운영자만) · 요청자 필터면 「이력은 전체」 · PV 탐색 링크", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}&target=team-a/datasets/dms-proof&requester=alice` });
  const region = await sheetOf("team-a/datasets/dms-proof");
  const dl = await within(region).findByLabelText("PV 소속");
  expect(within(dl).getByText("PV 소속", { selector: "dt" }).nextElementSibling)
    .toHaveTextContent("datasets — team-a (tb-nfs) · dmsk8s");
  expect(within(dl).getByText("권한 사용자", { selector: "dt" }).nextElementSibling).toHaveTextContent(/^alice, junwoo\.song$/);
  expect(within(region).getByText("이력은 요청자와 무관한 이 경로의 전체 이력입니다")).toBeInTheDocument();
  // 소속 글자는 필터를 거는 버튼, 옆에 PV 탐색(같은 클러스터·namespace + PV 이름 검색)
  expect(within(dl).getByRole("button", { name: "datasets — team-a (tb-nfs) · dmsk8s" })).toBeInTheDocument();
  expect(within(dl).getAllByRole("button").map((b) => b.textContent))
    .toEqual(["datasets — team-a (tb-nfs) · dmsk8s", "alice", "junwoo.song"]);
  expect(within(dl).getByRole("link", { name: /^PV 탐색에서 보기/ }))
    .toHaveAttribute("href", "/admin/k8s/volumes?cluster=dmsk8s&namespace=team-a&q=datasets");
  cleanup();
  pvSetup({ path: `/admin/usage?storage=${NFS}&target=team-b/models` });
  const r2 = await sheetOf("team-b/models");
  await waitFor(() => expect(within(r2).getByLabelText("PV 소속")).toHaveTextContent("models — team-b (tb-nfs)"));
  expect(within(within(r2).getByLabelText("PV 소속")).getByText("권한 사용자", { selector: "dt" }).nextElementSibling)
    .toHaveTextContent("없음 — 관리자·특권 운영자만 접근");
  expect(within(r2).queryByText(/이력은 요청자와 무관한/)).toBeNull();
  cleanup();
  // 기록뿐인 PV(지금 인벤토리에 없음)는 PV 탐색에 없다 -- 링크 없이 필터 버튼만
  pvSetup({ path: `/admin/usage?storage=${NFS}&target=old/gone` });
  const r3 = await sheetOf("old/gone");
  const dl3 = await within(r3).findByLabelText("PV 소속");
  await waitFor(() => expect(within(dl3).getByRole("button", { name: /^gone — team-a/ })).toBeInTheDocument());
  expect(within(dl3).queryByRole("link")).toBeNull();
});

test("시트 안 소속 글자: PV 이름 = 클러스터·namespace·PV 이름 필터, 권한 사용자 = user 필터 -- 필터 + 시트 닫기는 한 번의 replace", async () => {
  pvSetup({ path: `/admin/usage?storage=${NFS}&requester=mason` });
  await userEvent.click(await screen.findByRole("button", { name: "team-a/datasets/proof-scan" }));
  const sheet = await sheetOf("team-a/datasets/proof-scan");
  expect(navType()).toBe("PUSH");
  const pvBtn = await within(sheet).findByRole("button", { name: "datasets — team-a (tb-nfs) · dmsk8s" });
  expect(pvBtn).toHaveAttribute("title", expect.stringContaining("누르면 이 PV 로 경로 목록을 거릅니다"));
  await userEvent.click(pvBtn);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(navType()).toBe("REPLACE");
  expect([loc().get("cluster"), loc().get("namespace"), loc().get("pvc"), loc().get("requester"), loc().get("target")])
    .toEqual(["dmsk8s", "team-a", "datasets", "mason", null]);                     // 다른 필터(요청자)는 남는다
  expect(await screen.findByRole("button", { name: "team-a/datasets/dms-proof" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "team-b/models" })).toBeNull();
  // 포커스는 <body> 에 떨어지지 않는다(목록이 바뀌는 중이라 행이 없으면 필터 줄 쪽으로)
  await waitFor(() => expect(document.activeElement).not.toBe(document.body));
  cleanup();
  // 권한 사용자(딥링크로 연 시트 -- 닫기 규칙과 같이 replace): user 하나만 더 걸리고 구분 「PV 안」은 그대로
  pvSetup({ path: `/admin/usage?storage=${NFS}&scope=pv&target=c2/lab-y/weights` });
  const s2 = await sheetOf("c2/lab-y/weights");
  await userEvent.click(await within(s2).findByRole("button", { name: "cocoa.song" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(navType()).toBe("REPLACE");
  expect([loc().get("user"), loc().get("scope"), loc().get("cluster"), loc().get("target")])
    .toEqual(["cocoa.song", "pv", null, null]);
  expect(await screen.findByRole("button", { name: "c2/lab-y/weights" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "team-a/datasets/dms-proof" })).toBeNull();
});
