import { expect, test, type Page } from "@playwright/test";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane, assertMainColumn, assertTableOverflows } from "./helpers/layout";
import { singleLayoutFlow } from "./helpers/layoutSingle";
import { batchLayoutFlow } from "./helpers/layoutBatch";
import { STORAGE_NAME } from "./harness/env";

// E3 — 화면 순회 × 2 뷰포트. 이 슬라이스의 회귀 방어 본체다: 9fbef86·6bc2ecb 두
// 결함은 jsdom 이 기하를 계산하지 않는 탓에 단위 테스트 228건을 통과했고, 그래서
// "실제로 배포되는 것"을 실 브라우저로 재는 이 파일이 유일한 그물이다.
//
// 진입 후 안정화는 **heading 가시성**으로 한다. networkidle 은 이 앱에서 영원히
// 오지 않는다 -- 요청 목록 3s·대시보드 5s 폴링이 상시 돌기 때문이다(설계 §1-11).
async function visit(page: Page, path: string, heading: string): Promise<void> {
  await page.goto(path);
  await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
}

// 사용량 분석 경로별(2026-10-20 스토리지별 → 2026-10-29 하위 탭 + 스토리지 select): select 와 고른 스토리지의 패널(스토리지 이름의
// 구역 -- 표·빈 상태 어느 쪽이든)이 그려진 뒤에 잰다 -- select 는 등록 목록이 오면 바로 그려지지만 패널은 요약(기본 스토리지
// 판정)을 기다린다. 옵션 값은 「s:이름」(StorageSelect).
const usagePanel = (page: Page) => page.getByRole("region", { name: STORAGE_NAME, exact: true });
async function visitUsage(page: Page): Promise<void> {
  await visit(page, "/admin/usage", "사용량 분석");
  await expect(page.getByRole("combobox", { name: "스토리지", exact: true })).toHaveValue(`s:${STORAGE_NAME}`);
  const panel = usagePanel(page);
  await expect(panel.locator("table").first().or(panel.getByText("이 스토리지에는 아직 성공한 scan 이 없습니다"))).toBeVisible();
}

// 사용량 분석 스토리지별(2026-10-29): 비교 표(th 8 + 시드 스토리지 행 td 8)가 그려지고 합계 칸 자리표시가 걷힌 뒤에 잰다 -- 새로
// 로드라 합계 캐시가 없고, 자리표시(td 바로 아래 Skeleton = aria-hidden div)도 td 수가 같아 셀 하한(L2)을 채운다. 그 상태에서 재면
// 실제 값 칸(df compact 막대·「이상」·긴 바이트)의 375 넘침이 초록으로 지나간다(2026-10-29 리뷰). 값이든 「불러오지 못함」이든 실제
// 칸이 들어선 뒤에 잰다.
const USAGE_TOTALS_PLACEHOLDER = "table td > div[aria-hidden]";
async function visitUsageStorages(page: Page): Promise<void> {
  await visit(page, "/admin/usage/storages", "스토리지별");
  await expect(page.getByRole("table").getByRole("link", { name: STORAGE_NAME, exact: true })).toBeVisible();
  await expect(page.locator(USAGE_TOTALS_PLACEHOLDER)).toHaveCount(0);
}

// 사용량 필터 줄(2026-10-29): 켜진 필터는 선택지와 무관하게 늘 그 select 를 보인다(이 스토리지에 없는 값은 「(이 스토리지에 없음)」
// 옵션). 하네스엔 k8s 클러스터가 없어 PV 탭을 만들 수 없으므로, PV 축 다섯 + 구분 + 요청자를 모두 켠 딥링크가 가장 넓은 필터 줄
// (select 7칸 + 설명 + 지우기, 긴 값)을 연다 -- 375 에서 두 칸씩 접혀 문서가 넘치지 않아야 한다(L1). 결과는 0건이라 빈 상태 + 툴바.
const USAGE_FILTERED = `/admin/usage?storage=${STORAGE_NAME}&cluster=e2e-cluster-with-a-rather-long-name`
  + "&namespace=team-namespace-with-long-name&pvc=datasets-very-long-persistent-volume-claim-0001"
  + "&storage_class=nfs-client-storage-class-long&user=someone.with.a.long.account.id&scope=pv&requester=admin";
async function visitUsageFiltered(page: Page): Promise<void> {
  await visit(page, USAGE_FILTERED, "사용량 분석");
  const panel = usagePanel(page);
  const filters = panel.getByRole("group", { name: "사용량 필터" });
  await expect(filters.getByRole("combobox")).toHaveCount(7);
  await expect(panel.getByText("필터에 맞는 대상이 없습니다").or(panel.locator("table").first())).toBeVisible();
}

// k8s 연동(dms-k8s 설계 §7) 개요 + 하위 다섯(클러스터는 2026-11-11 개요에서 분리). e2e 하네스엔 클러스터가 없어 여섯 화면 모두
// 빈 상태(안내·빈 표)로 그려진다 -- 표 하한은 0, 머리의 동작 버튼 줄·탭 줄(가로 스크롤 -- 여섯 탭)·필터 줄이 L1 위험이다. 로딩
// 문구가 사라진 뒤에 잰다(개요는 「호출 노드와 주기」 h2, 클러스터는 빈 상태 h2 -- 카드가 있으면 카드 이름 h2).
const K8S_PAGES: Array<[string, string]> = [
  ["/admin/k8s", "k8s 연동"],
  ["/admin/k8s/clusters", "클러스터"],
  ["/admin/k8s/storageclasses", "StorageClass"],
  ["/admin/k8s/volumes", "PV 탐색"],
  ["/admin/k8s/access", "사용자 권한"],
  ["/admin/k8s/history", "동기화 이력"],
];

// 보안 이벤트(2026-11-03): 세 카드(상태 dl·인증번호 잠금·이벤트)가 다 그려진 뒤에 잰다 -- 이 파일 시점엔 잠금·이벤트가 보통 없어
// (빈 상태 문구) 표 하한은 0이다(이벤트 표가 있는 375 는 E17 이 잰다). 상태 dl 은 375 에서 1열로 접히고, 필터 줄(긴 종류 select·
// 검색칸)은 줄바꿈으로 접혀 문서를 넘치지 않아야 한다(L1).
async function visitSecurity(page: Page): Promise<void> {
  await visit(page, "/admin/security", "보안 이벤트");
  await expect(page.getByText("불러오는 중…", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/^카운터는 dms-api 프로세스 메모리에 있습니다/)).toBeVisible();
}

// 마운트 현황(2026-11-23): 노드 × 스토리지 표(고정 머리·첫 열, 래퍼 안 가로·세로 스크롤). 노드 버튼(행 머리)이 그려진 뒤에 잰다.
// 셀 하한은 소스 실측: 머리 th 3(모서리 노드·DMS 공용·e2e-store) + e2e-node 1행(행 머리 th·공용·e2e-store) 3 = 6(2026-11-25 「보고」
// 열을 행 머리의 <time> 으로 합쳐 8 → 6).
// view 를 URL 로 고정한다 -- 없으면 640 미만에서 노드별 카드가 기본이다(그 보기는 375 에서 따로 잰다).
async function visitMounts(page: Page, view: "table" | "cards"): Promise<void> {
  await visit(page, `/admin/storages/mounts?view=${view}`, "마운트 현황");
  const scope = view === "table" ? page.getByRole("table") : page.getByRole("list", { name: "노드별 마운트 현황" });
  await expect(scope.getByRole("button", { name: "e2e-node", exact: true })).toBeVisible();
}

async function visitK8s(page: Page, path: string, heading: string): Promise<void> {
  await visit(page, path, heading);
  await expect(page.getByText("불러오는 중…", { exact: true })).toHaveCount(0);
  if (path === "/admin/k8s") await expect(page.getByRole("heading", { name: "호출 노드와 주기", level: 2 })).toBeVisible();
  if (path === "/admin/k8s/clusters") {
    await expect(page.getByRole("heading", { name: "등록된 클러스터가 없습니다", level: 2 })
      .or(page.getByRole("button", { name: "지금 동기화", exact: true }).first())).toBeVisible();
  }
  // 켜진 탭은 탭 줄 안에 보여야 한다 -- 여섯 탭이 375·390·768 에서 넘쳐 「사용자 권한」·「동기화 이력」이 켜진 채 화면 밖에 있었다
  // (2026-11-11 리뷰; 문서 가로 넘침 L1 은 탭 줄 안의 스크롤이라 못 잡는다). K8sTabs 가 켜진 탭으로 탭 줄을 옮긴다.
  const nav = page.getByRole("navigation", { name: "k8s 연동 하위 메뉴" });
  await expect.poll(async () => nav.evaluate((el) => {
    const a = el.querySelector<HTMLElement>('[aria-current="page"]');
    if (!a) return "켜진 탭 없음";
    const n = el.getBoundingClientRect();
    const t = a.getBoundingClientRect();
    return t.left >= n.left - 1 && t.right <= n.right + 1 ? "보임" : `밖: 탭 [${t.left},${t.right}] nav [${n.left},${n.right}]`;
  }), { message: `${path} 켜진 탭이 탭 줄 밖에 있다` }).toBe("보임");
}

// 제출 화면 둘(단일 작업·배치 생성)의 흐름은 helpers/layoutSingle·layoutBatch 에 있다(2026-11-26 단계별 등록 준비 -- 단일·배치 위저드
// 작업이 이 spec 을 같이 고치지 않게 동작 그대로 옮겼다). 이 파일은 폭마다 singleLayoutFlow·batchLayoutFlow 를 부르기만 한다.

// 표 칸 글자가 한 줄인가(2026-11-04 기능 점검 #3). L4 는 `table button` 만 재고 td 는 행 높이를 따라 늘어 높이로는 못 잰다 --
// 칸 내용의 줄 상자(Range.getClientRects)의 서로 다른 top 개수로 잰다. 칸이 하나도 안 잡히면 잰 게 없으니 실패다.
async function assertCellsOneLine(page: Page, selector: string, label: string): Promise<void> {
  const { count, wrapped } = await page.locator(selector).evaluateAll((tds) => ({
    count: tds.length,
    wrapped: tds.filter((td) => {
      const range = document.createRange();
      range.selectNodeContents(td);
      return new Set(Array.from(range.getClientRects()).map((r) => Math.round(r.top))).size > 1;
    }).map((td) => td.textContent),
  }));
  expect(count, `${label}: 칸(${selector})을 하나도 못 찾았다 -- 셀렉터가 화면과 어긋났다`).toBeGreaterThan(0);
  expect(wrapped, `${label}이 두 줄 이상으로 쪼개졌다(whitespace-nowrap 회귀)`).toEqual([]);
}

// 각 화면의 minTableCells 는 **소스 실측치**다(완화가 아니라 실측으로 정한다):
//   /admin/accounts  th 6 + (admin 1 + e2ewide 3)행 × td 6 = 30 >= 24
//   /jobs            th 10(관리자 선택 열 1 + 관리자 배치 열 1 + 요청·요청자·작업·대상·우선순위·상태·생성·갱신 8)
//                    + 0행(이 파일 시점엔 요청이 없다) = 10. 하한은 4 로 둔다 -- 선택·배치 열은 me 도착 뒤에 생겨(그
//                    전엔 8) 바닥만 건다. 체크·배치 열이 있는 표의 셀 불변식은 E7(06-request-delete)이 행과 함께 잰다.
//   /admin/storages  th 6 + e2e-store 1행 × td 6 = 12
//   /admin/builds    빌드하기(폼 전용) -- 표가 없다 -> 하한 0. L1/L3/L4 가 진다.
//   /admin/builds/history
//                    th 8(시각·ref·이미지·상태·사유·경과·태그·작업) + 0행 = 8
//                    commit·노드는 상세로 밀었다(DS Cloud 재설계) -- 열을 더 줄이면
//                    이 하한이 문다. 밀도를 낮추더라도 8열이 바닥이라는 뜻이다.
//                    빌드 표는 하위 페이지 분리로 여기로 옮겨 왔다(d72) -- 표 불변식을
//                    "빌드 화면"이 아니라 **표가 실제로 있는 화면**에 건다.
//   /admin/dashboard 표는 잡 통계·큐 데이터가 있을 때만 그려진다 -> 하한 불가(0).
//                    이 화면은 L1/L3/L4 가 진다.
//   /admin/usage     표는 고른 스토리지에 성공 scan 이 있을 때만 그려진다(이 파일 시점엔 보통 없다) -> 하한 0.
//                    하위 탭 줄(2026-10-29 -- 가로 스크롤 띠는 이것 하나)·범위 줄(스토리지 select 전체 폭 + 메타 줄 접힘)이
//                    L1 위험이라 순회에 넣는다(예전 스토리지 탭 줄의 sr-only 노드가 문서를 넓히던 유형 StatusPill 619/375).
//                    행·상세 시트의 375 불변식은 E10(09)이 기록과 함께 잰다. 필터 줄(2026-10-29)은 375 에서 모든 필터를 켠
//                    딥링크로 따로 잰다(결과 0건 -- 하한 0). 실데이터 요청자 필터의 375 는 E15(15)가 잰다.
//                    「PV 이름」 열(2026-10-29, 7열)은 PV 안 대상이 있는 스토리지에만 생긴다 -- 하네스엔 k8s 클러스터가 없어
//                    시드 스토리지는 늘 6열이고(셀 하한을 바꾸지 않는다), 7열 표의 375 는 실 브라우저 그물 밖이다(칸 상한·말줄임은
//                    PvName 의 max-w·truncate, 고정 경로 칸은 그대로).
//   /admin/usage/storages
//                    th 8(스토리지·실 사용량·파일·디렉터리·hot·df·대상·최근 스캔) + 시드 스토리지 행 td 8 = 16. 이름 칸 고정 +
//                    래퍼 안 가로 스크롤(375)이 문서를 넓히지 않아야 한다(L1).
test.describe("E3 레이아웃 불변식", () => {
  test("1280x800 주요 화면 순회", async ({ page }) => {
    await apiLogin(page);

    await visit(page, "/admin/dashboard", "대시보드");
    // 노드 칸(2026-10-16 CPU 사용률 추가로 노드당 5칸)이 그려진 뒤에 잰다 -- 시계열은 heading 보다 늦게 온다.
    await expect(page.getByRole("group", { name: / CPU 사용률$/ }).first()).toBeVisible();
    await assertLayoutSane(page, { minTableCells: 0 });

    // 계정 화면이 두 결함이 실제로 터졌던 자리다. 전제 단언을 **먼저** 건다 --
    // 표가 넘치지 않으면 아래 불변식들은 아무것도 증명하지 못한다.
    await visit(page, "/admin/accounts", "계정");
    await assertTableOverflows(page);
    await assertLayoutSane(page, { minTableCells: 24 });

    await visit(page, "/jobs", "전체 작업");
    await assertLayoutSane(page, { minTableCells: 4 });

    // StoragesList 작업 셀의 flex td 결함(슬라이스 23 이 knownNonTableCells: 1 로
    // 기록)은 슬라이스 26 이 수리했다 -- td 안 div 로 flex 이동(9fbef86 형태).
    await visit(page, "/admin/storages", "스토리지");
    await assertLayoutSane(page, { minTableCells: 12 });

    await visitMounts(page, "table");
    await assertLayoutSane(page, { minTableCells: 6 });

    // 빌드는 하위 페이지 둘이다(빌드하기·빌드 이력). 폼 화면에는 표가 없으므로
    // 하한을 0 으로 두되 **순회에서 빼지는 않는다** -- L1(가로 오버플로)·L3·L4 는
    // 여기서도 진다. 8셀 하한은 표를 실제로 가진 이력 화면이 이어받는다.
    await visit(page, "/admin/builds", "빌드");
    await assertLayoutSane(page, { minTableCells: 0 });

    await visit(page, "/admin/builds/history", "빌드 이력");
    await assertLayoutSane(page, { minTableCells: 8 });

    // 제출 화면 둘 -- 표가 없으므로 하한 0, L1·L3·L4 가 진다(흐름은 helpers/layoutSingle·layoutBatch).
    await singleLayoutFlow(page, 1280);
    await batchLayoutFlow(page, 1280);

    await visitUsage(page);
    await assertLayoutSane(page, { minTableCells: 0 });
    await visitUsageStorages(page);
    await assertLayoutSane(page, { minTableCells: 16 });

    for (const [path, heading] of K8S_PAGES) {
      await visitK8s(page, path, heading);
      await assertLayoutSane(page, { minTableCells: 0 });
    }

    await visitSecurity(page);
    await assertLayoutSane(page, { minTableCells: 0 });
  });

  test("375x667 모바일 spot check", async ({ page }) => {
    await apiLogin(page);
    // md: 분기 **아래**라 사이드바는 전폭 블록이다 -> L3 제외, L1/L2/L4 만(설계 §3).
    await page.setViewportSize({ width: 375, height: 667 });

    await visit(page, "/admin/accounts", "계정");
    await assertTableOverflows(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 24 });

    // 좁은 폭 메뉴(2026-11-11, BACKLOG 「모바일(375) 사이드바 접기」 해소): 메뉴·사용자 패널은 기본 닫힘이라 첫 화면이 본문이다
    // (예전엔 관리자 사이드바 약 1,050px 블록 뒤 y≈1,200 에서 본문이 시작했다). 닫힌 채로는 nav 링크가 display:none 이라 위 L4 가
    // 높이 0 으로 지나가므로, 한 번 열어 링크 한 줄·문서 넘침을 재고 다시 닫는다.
    const h1Top = await page.getByRole("heading", { name: "계정", level: 1 })
      .evaluate((el) => el.getBoundingClientRect().top);
    expect(h1Top, "375 첫 화면 안에 본문 h1 이 없다 -- 사이드바가 본문 위를 차지했다").toBeLessThan(667);
    const menu = page.getByRole("button", { name: "메뉴", exact: true });
    await expect(menu).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("aside").getByRole("link", { name: "전체 작업" })).toBeHidden();
    await menu.click();
    await expect(page.locator("aside").getByRole("link", { name: "전체 작업" })).toBeVisible();
    await expect(page.getByRole("button", { name: "로그아웃" })).toBeVisible();
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 24 });
    await menu.click();
    await expect(page.locator("aside").getByRole("link", { name: "전체 작업" })).toBeHidden();

    await visit(page, "/jobs", "전체 작업");
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 4 });

    // 제출 화면은 좁은 폭으로 접힌다 -- 경로 줄·항목 표·입력 방식 세그먼트가 375 에서 넘치거나 접히지 않아야 한다(흐름은 helpers).
    await singleLayoutFlow(page, 375);
    await batchLayoutFlow(page, 375);

    // 사용량 분석: 하위 탭 줄은 375 에서 줄바꿈 없이 가로 스크롤하고(줄 자체만 넘친다), 스토리지 select 는 전체 폭 -- 문서는
    // 넘치지 않아야 한다(L1). 스토리지별 표는 래퍼 안에서만 가로로 넘친다.
    await visitUsage(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    await visitUsageStorages(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 16 });
    // 필터 줄(2026-10-29)이 가장 넓게 열린 채로(위 visitUsageFiltered) -- select 가 두 칸씩 접히고 긴 값이 칸을 밀지 않는다.
    await visitUsageFiltered(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });

    // 대시보드 노드 칸(2026-10-16 CPU 사용률 추가로 5칸): md 아래는 2열로 쌓여 375 에서 넘치지 않아야 한다.
    await visit(page, "/admin/dashboard", "대시보드");
    await expect(page.getByRole("group", { name: / CPU 사용률$/ }).first()).toBeVisible();
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });

    // k8s 연동: 머리 동작 버튼(클러스터 탭의 클러스터 등록 등)은 375 에서 제목 아래로 접히고, 하위 메뉴 탭 줄(여섯)은 자기 안에서
    // 가로 스크롤한다.
    for (const [path, heading] of K8S_PAGES) {
      await visitK8s(page, path, heading);
      await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    }

    // 보안 이벤트: 상태 dl 1열·필터 줄 접힘(문서 L1)
    await visitSecurity(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });

    // 표 안 버튼·시각 칸 nowrap(2026-11-04 기능 점검 #3): 390·375 에서 노드 표 「배치 제외」(4줄)·「상세」·마지막 리포트 시각(4줄), 감사 로그 「펼치기」
    // (3줄)·시각 칸(4줄)이 음절마다 세로로 쪼개졌다 -- 공용 Button 엔 nowrap 이 없어 표 안 버튼마다 붙여야 한다(9fbef86 규약).
    // L4 는 `table button` 을 재므로 **버튼이 그려진 뒤에** 잰다(없으면 잰 게 없는 초록). 셀 하한은 소스 실측:
    //   /admin/nodes     th 9 + e2e-node 1행 × td 9 = 18
    //   /admin/audit     th 6 + 시드가 남긴 기록(스토리지 등록 등) 1행 이상 × td 6 >= 12
    //   /admin/denylist  th 4 + 아래에서 심는 규칙 1행 × td 4 = 8(같은 부류 「해제」 버튼)
    await visit(page, "/admin/nodes", "노드");
    await expect(page.getByRole("button", { name: "e2e-node 배치 제외", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "상세", exact: true })).toBeVisible();
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 18 });
    await assertCellsOneLine(page, "main table tbody tr td:first-child", "노드 표 노드명 칸");
    // 맨 끝 「마지막 리포트」 칸은 의도된 두 블록(경과 + 시각)이라 칸 전체가 아니라 시각 줄 하나만 잰다(LastReport).
    await assertCellsOneLine(page, "main table tbody tr td:last-child > div > div:last-child", "노드 표 마지막 리포트 시각");

    // 마운트 현황(2026-11-23): 표는 래퍼 안에서만 가로로 넘치고(L1), 노드 이름 버튼(L4 table button)·보고 시각(행 머리의 <time> --
    // 2026-11-25 「보고」 열을 합쳤다)은 한 줄이다. 노드별 카드 보기(640 미만 기본)도 문서를 넘치지 않는다 -- 표가 없어 하한 0.
    await visitMounts(page, "table");
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 6 });
    await assertCellsOneLine(page, "main table tbody tr > th button", "마운트 현황 노드 이름");
    await assertCellsOneLine(page, "main table tbody tr > th time", "마운트 현황 보고 시각");
    await visitMounts(page, "cards");
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });

    // denylist 는 이 파일 시점엔 비어 있다 -- 아무 시나리오도 쓰지 않는 이름으로 하나 심고 끝에 지운다(감사 기록도 하나 는다).
    const denyPath = "/api/admin/identity-denylist/requester/e2e-layout-nobody";
    const denied = await page.request.put(denyPath, { data: { reason: "e2e 375 레이아웃" } });
    expect(denied.status(), await denied.text()).toBe(201);
    try {
      await visit(page, "/admin/denylist", "denylist");
      await expect(page.getByRole("button", { name: "해제", exact: true })).toBeVisible();
      await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 8 });
    } finally {
      const allowed = await page.request.delete(denyPath);
      expect(allowed.status(), await allowed.text()).toBe(200);
    }

    await visit(page, "/admin/audit", "감사 로그");
    await expect(page.getByRole("button", { name: "펼치기", exact: true }).first()).toBeVisible();
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 12 });
    await assertCellsOneLine(page, "main table tbody tr td:nth-child(2)", "감사 로그 시각 칸");
  });

  // 배치 목록 「대상 스토리지」 열 + 상세 찾기 줄(검색칸·상태 칩)·쪽 넘김(2026-11-16, 설계 #67). 목록 열은 일반 텍스트 td(L2 --
  // computed display=table-cell)이고 줄바꿈 없이(한 줄) 표의 가로 스크롤 래퍼 안에서 넘친다(문서 L1 아님). 상세의 찾기 줄은
  // 375 에서 검색칸이 자기 행·칩이 줄바꿈으로 접혀 문서를 넘치지 않아야 한다. 항목 101개라야 쪽 넘김(Pager)이 생긴다 -- 실제로
  // 돌지 않게 만들자마자 취소한다(취소는 항목 장벽 + 자식 정리 -- 하네스 1초 틱이 그 사이 자식 1~2개를 만들 수 있다).
  // 셀 하한(소스 실측): /admin/batches th 8(선택·배치·이름·작업·대상 스토리지·상태·성공/실패/전체·최근 갱신) + 이 배치 1행 td 8 = 16.
  test("배치 목록 「대상 스토리지」 열 · 상세 찾기 줄과 쪽 넘김(1280·375)", async ({ page }) => {
    test.setTimeout(60_000);
    await apiLogin(page);
    const tag = `lay${Date.now().toString(36)}`;
    const created = await page.request.post("/api/admin/batches", {
      data: { operation: "scan", max_concurrency: 1, options: {}, name: `${tag} 레이아웃`,
              items: Array.from({ length: 101 }, (_, i) => ({ storage: STORAGE_NAME, target: `${tag}/item-${i}` })) },
    });
    expect(created.status(), await created.text()).toBe(202);
    const bid = ((await created.json()) as { batch_id: string }).batch_id;
    const cancel = await page.request.post(`/api/admin/batches/${bid}:cancel`);
    expect(cancel.status(), await cancel.text()).toBe(200);

    for (const [width, height, sidebarFixed] of [[1280, 800, true], [375, 667, false]] as const) {
      await page.setViewportSize({ width, height });
      await visit(page, "/admin/batches", "배치 작업");
      const head = page.getByRole("columnheader", { name: "대상 스토리지", exact: true });
      await expect(head).toBeVisible();
      const heads = await page.getByRole("columnheader").allTextContents();
      expect(heads.indexOf("대상 스토리지"), "「대상 스토리지」는 「작업」 바로 오른쪽").toBe(heads.indexOf("작업") + 1);
      const col = heads.indexOf("대상 스토리지") + 1;
      await expect(page.locator(`main table tbody tr td:nth-child(${col})`).first()).toHaveText(STORAGE_NAME);
      await assertLayoutSane(page, { sidebarFixed, minTableCells: 16 });
      await assertCellsOneLine(page, `main table tbody tr td:nth-child(${col})`, "배치 목록 대상 스토리지 칸");

      await visit(page, `/admin/batches/${bid}`, `${tag} 레이아웃`);
      await expect(page.getByPlaceholder("경로·PV 이름 검색…")).toBeVisible();
      await expect(page.getByRole("group", { name: "항목 상태" })).toBeVisible();
      const pager = page.getByRole("navigation", { name: "항목 쪽" });
      await expect(pager).toContainText("1 / 2쪽");
      await assertLayoutSane(page, { sidebarFixed, minTableCells: 0 });
      // Pager 는 한 줄(375 에서도 -- flex-nowrap·whitespace-nowrap)
      const [ph, lh] = await pager.evaluate((el) => {
        const span = el.querySelector("span:not(.sr-only)") as HTMLElement;
        return [el.getBoundingClientRect().height, span.getBoundingClientRect().height * 2.5];
      });
      expect(ph, "항목 쪽 넘김이 두 줄 이상으로 접혔다").toBeLessThan(lh);
      // 검색은 서버로 묻는다 -- 한 항목으로 좁혀지고 쪽 넘김은 사라진다
      await page.getByPlaceholder("경로·PV 이름 검색…").fill(`${tag} item-100`);
      await expect(page.getByRole("button", { name: "항목 100 상세", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "항목 0 상세", exact: true })).toHaveCount(0);
      await expect(pager).toHaveCount(0);
      await assertLayoutSane(page, { sidebarFixed, minTableCells: 0 });
    }
  });

  // 배치 스케줄러(2026-11-16, 설계 #68): 스케줄 목록 표(텍스트·링크 td -- L2, 관리 칸만 안쪽 div 가 flex)와 배치 상세 머리의 스케줄
  // 배지(긴 문구 링크 -- 375 에서 줄바꿈으로 접혀 문서를 넘치지 않아야 한다, L1). 주기는 가장 긴 요약(매주 일곱 요일)이고 시각은
  // 지금 KST + 12시간이라 테스트 동안 회차가 돌지 않는다. 배치는 만들자마자 취소한다(실행하지 않게). 끝에 스케줄을 지운다.
  // 셀 하한(소스 실측): /admin/batch-schedules th 9(배치·작업·대상 스토리지·주기·다음 실행(KST)·마지막 결과·사용·켠 사람·관리) +
  // 이 스케줄 1행 td 9 = 18(다른 시나리오가 남긴 스케줄이 있으면 더 많다 -- 하한이다).
  test("배치 스케줄러 목록 · 배치 상세의 스케줄 배지(1280·375)", async ({ page }) => {
    test.setTimeout(60_000);
    await apiLogin(page);
    const tag = `sch${Date.now().toString(36)}`;
    const name = `${tag} 스케줄 레이아웃`;
    const created = await page.request.post("/api/admin/batches", {
      data: { operation: "scan", max_concurrency: 1, options: {}, name,
              items: [{ storage: STORAGE_NAME, target: `${tag}/a` }] },
    });
    expect(created.status(), await created.text()).toBe(202);
    const bid = ((await created.json()) as { batch_id: string }).batch_id;
    const cancel = await page.request.post(`/api/admin/batches/${bid}:cancel`);
    expect(cancel.status(), await cancel.text()).toBe(200);
    const hour = (new Date(Date.now() + 9 * 3600_000).getUTCHours() + 12) % 24;
    const made = await page.request.post("/api/admin/batch-schedules", {
      data: { batch_id: bid, kind: "weekly", spec: { weekdays: [1, 2, 3, 4, 5, 6, 7], hour, minute: 0 } },
    });
    expect(made.status(), await made.text()).toBe(201);
    const sid = ((await made.json()) as { schedule_id: string }).schedule_id;
    const rule = `매주 월·화·수·목·금·토·일 ${String(hour).padStart(2, "0")}:00 (KST)`;
    try {
      for (const [width, height, sidebarFixed] of [[1280, 800, true], [375, 667, false]] as const) {
        await page.setViewportSize({ width, height });
        await visit(page, "/admin/batch-schedules", "배치 스케줄러");
        const row = page.getByRole("row").filter({ has: page.getByRole("link", { name, exact: true }) });
        await expect(row.getByText(rule)).toBeVisible();
        await expect(row.getByRole("checkbox", { name: `${name} 사용` })).toBeChecked();
        await assertLayoutSane(page, { sidebarFixed, minTableCells: 18 });
        await assertCellsOneLine(page, "main table tbody tr td:nth-child(2)", "스케줄 표 작업 칸");

        await visit(page, `/admin/batches/${bid}`, name);
        const badge = page.getByRole("link", { name: new RegExp(`^스케줄 ${rule.replace(/[()]/g, "\\$&")} · 다음 `) });
        await expect(badge).toBeVisible();
        await expect(badge).toHaveAttribute("href", `/admin/batch-schedules?batch=${bid}`);
        await assertLayoutSane(page, { sidebarFixed, minTableCells: 0 });
      }
    } finally {
      const removed = await page.request.delete(`/api/admin/batch-schedules/${sid}`);
      expect(removed.status(), await removed.text()).toBe(200);
    }
  });

  // 본문 열(2026-11-26 사용자 결정 「A안」 -- AppShell main 의 mx-auto max-w-[90rem]): 넓은 창(1920)에서는 최대 1440 열이 사이드바
  // 오른쪽 영역 가운데에 서고, 1680 이하 창(1280)에서는 예전처럼 여백 없이 영역을 꽉 채운다(L5 -- helpers/layout assertMainColumn).
  // 네 화면의 h1 왼쪽이 같아야 한다: 기준선은 본문 열의 왼쪽 하나다 -- 한 화면이 스스로 mx-auto 하면(2026-08-16 사용자 지적 유형)
  // 그 화면 제목만 옆으로 튄다. 화면은 넓은 표(마운트 현황)·목록(전체 작업)·제출 시트(단일 작업)·카드(대시보드) 한 벌씩이다.
  test("본문 열: 1920 에서 최대 1440 가운데 · 1280 에서 여백 없음 · 화면 제목 기준선 하나", async ({ page }) => {
    await apiLogin(page);
    const screens: Array<[string, () => Promise<void>, number]> = [
      ["대시보드", () => visit(page, "/admin/dashboard", "대시보드"), 0],
      ["전체 작업", () => visit(page, "/jobs", "전체 작업"), 4],
      ["단일 작업", () => visit(page, "/jobs/new", "단일 작업"), 0],
      ["마운트 현황", () => visitMounts(page, "table"), 6],
    ];
    for (const [width, height] of [[1920, 1080], [1280, 900]] as const) {
      await page.setViewportSize({ width, height });
      const h1Lefts: Array<[string, number | null]> = [];
      for (const [name, go, minTableCells] of screens) {
        await go();
        await assertLayoutSane(page, { minTableCells });
        const col = await assertMainColumn(page);
        if (width === 1920) {
          // 1920 − 240 = 1680 > 1440 -- 열이 실제로 묶이고 좌우에 여백이 생겨야 한다(여백 0 이면 가운데 단언이 공허하다).
          expect(col.width, `${name} 1920: 본문 열 폭`).toBeLessThanOrEqual(1440.5);
          expect(col.left - 240, `${name} 1920: 왼쪽 여백이 없다 -- 열이 가운데로 가지 않았다`).toBeGreaterThan(10);
        } else {
          expect(Math.abs(col.left - 240), `${name} 1280: main.left ${col.left} -- 좁은 창은 사이드바에 붙어야 한다`).toBeLessThanOrEqual(0.5);
          expect(Math.abs(col.right - col.clientWidth),
                 `${name} 1280: main.right ${col.right} ≠ clientWidth ${col.clientWidth} -- 좁은 창에 오른쪽 여백이 생겼다`).toBeLessThanOrEqual(0.5);
        }
        h1Lefts.push([name, col.h1Left]);
      }
      for (const [name, left] of h1Lefts) expect(left, `${width}: ${name} 화면 제목(h1)이 main 안에 없다`).not.toBeNull();
      const [firstName, firstLeft] = h1Lefts[0];
      for (const [name, left] of h1Lefts.slice(1)) {
        expect(Math.abs((left as number) - (firstLeft as number)),
               `${width}: ${name} 제목 왼쪽 ${left} ≠ ${firstName} ${firstLeft} -- 화면이 본문 열의 왼쪽 기준선을 벗어났다(혼자 mx-auto?)`)
          .toBeLessThanOrEqual(0.5);
      }
    }
  });
});
