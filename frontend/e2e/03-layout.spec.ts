import { expect, test, type Page } from "@playwright/test";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane, assertTableOverflows } from "./helpers/layout";
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

// k8s 연동(dms-k8s 설계 §7) 개요 + 하위 넷. e2e 하네스엔 클러스터가 없어 다섯 화면 모두 빈 상태(안내·빈 표)로 그려진다 -- 표
// 하한은 0, 머리의 동작 버튼 줄·탭 줄(가로 스크롤)·필터 줄이 L1 위험이다. 로딩 문구가 사라진 뒤에 잰다(개요는 「클러스터」 h2).
const K8S_PAGES: Array<[string, string]> = [
  ["/admin/k8s", "k8s 연동"],
  ["/admin/k8s/storageclasses", "StorageClass"],
  ["/admin/k8s/volumes", "PV 탐색"],
  ["/admin/k8s/access", "사용자 권한"],
  ["/admin/k8s/history", "동기화 이력"],
];

async function visitK8s(page: Page, path: string, heading: string): Promise<void> {
  await visit(page, path, heading);
  await expect(page.getByText("불러오는 중…", { exact: true })).toHaveCount(0);
  if (path === "/admin/k8s") await expect(page.getByRole("heading", { name: "클러스터", level: 2 })).toBeVisible();
}

// 단일 작업 화면에 200자 무공백 경로를 넣는다(제출하지 않음 -- resource_key·검증과 무관).
// 긴 토큰이 1fr 열을 밀어내던 유형(6bc2ecb)의 재현 재료다: minmax(0,1fr) 이 빠지면 문서가 넘치고(L1),
// 요약 dd·"지정 경로" 줄(2026-10-27 d192 에 「실제 경로」에서 개명 -- 괴리 [C2])의 overflow-wrap 이 빠지면 그 칸만
// 넘친다 -- xl 의 요약 본문은 자기 스크롤 상자라 문서 L1 엔 안 보이므로 아래 assertTextContained 가 칸 단위로 잰다.
async function fillLongScanTarget(page: Page): Promise<void> {
  await page.getByLabel("연산", { exact: true }).selectOption("scan");
  // 면제자(하네스 admin)의 대상은 기본이 「PV」라 「직접 경로」로 바꾼다(2026-10-26 -- k8s 신원 모드 전용 화면).
  await page.getByRole("group", { name: "대상 지정 방식" }).getByRole("button", { name: "직접 경로", exact: true }).click();
  const storage = page.getByLabel("스토리지", { exact: true });
  await expect(storage.locator(`option[value="${STORAGE_NAME}"]`)).toHaveCount(1);
  await storage.selectOption(STORAGE_NAME);
  await page.getByLabel("대상 경로", { exact: true }).fill("x".repeat(200));
}

// 경로 줄 접두는 화면 라벨 그대로(SubmitJob PathFeedback). 2026-10-27 d192 에 「실제 경로」가 「지정 경로」로 바뀌었는데 이
// 셀렉터는 옛 라벨을 찾아 아무것도 못 잡고 조용히 통과했다(2026-10-28 리뷰 contract-36) -- 그래서 경로 줄을 하나 이상 찾았는지도
// 함께 단언한다(라벨이 또 바뀌면 여기서 빨간불).
const PATH_LINE_PREFIX = "지정 경로: ";
async function assertTextContained(page: Page): Promise<void> {
  const { leaks, pathLines } = await page.evaluate((prefix) => {
    const lines = Array.from(document.querySelectorAll("main p")).filter(
      (p) => (p.textContent ?? "").startsWith(prefix));
    const els = [
      ...Array.from(document.querySelectorAll("main dd")),
      ...Array.from(document.querySelectorAll("main dl")).map((dl) => dl.parentElement!),
      ...lines,
    ];
    return {
      pathLines: lines.length,
      leaks: els.filter((el) => el.scrollWidth > el.clientWidth + 1)
        .map((el) => `${el.tagName} ${el.scrollWidth}>${el.clientWidth}: ${(el.textContent ?? "").slice(0, 40)}`),
    };
  }, PATH_LINE_PREFIX);
  expect(pathLines, `「${PATH_LINE_PREFIX}」 줄을 못 찾았다 -- 라벨이 바뀌었거나 스토리지 뿌리를 몰라 줄이 없다(검사한 게 없다)`)
    .toBeGreaterThan(0);
  expect(leaks, "긴 무공백 경로가 요약 칸·지정 경로 줄을 넘쳤다(overflow-wrap/minmax(0,1fr) 회귀)").toEqual([]);
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

    // 빌드는 하위 페이지 둘이다(빌드하기·빌드 이력). 폼 화면에는 표가 없으므로
    // 하한을 0 으로 두되 **순회에서 빼지는 않는다** -- L1(가로 오버플로)·L3·L4 는
    // 여기서도 진다. 8셀 하한은 표를 실제로 가진 이력 화면이 이어받는다.
    await visit(page, "/admin/builds", "빌드");
    await assertLayoutSane(page, { minTableCells: 0 });

    await visit(page, "/admin/builds/history", "빌드 이력");
    await assertLayoutSane(page, { minTableCells: 8 });

    // 제출 화면 둘(2026-10-06 단일 페이지화): 시트 + 오른쪽 sticky 요약 2열은 xl(1280)부터다.
    // 표가 없으므로 하한 0 -- L1(가로 넘침)·L3(사이드바 240, 요약이 <aside> 가 아님)·L4 가 진다.
    await visit(page, "/jobs/new", "단일 작업");
    await assertLayoutSane(page, { minTableCells: 0 });
    await fillLongScanTarget(page);
    await assertLayoutSane(page, { minTableCells: 0 });
    await assertTextContained(page);

    await visit(page, "/admin/batches/new", "배치 생성");
    await assertLayoutSane(page, { minTableCells: 0 });
    // rm(2026-10-21): 「삭제 범위」 안내·rm 옵션 줄·요약 위 경고가 넘치지 않는다.
    await page.getByLabel("연산").selectOption("rm");
    await assertLayoutSane(page, { minTableCells: 0 });
    // rm 자동 실행(2026-10-21): 켜면 빨간 경고 + 동의 체크가 실행 제어 안에 붙는다 — 그 상자도 넘치지 않는다.
    await page.getByLabel("미리보기 후 자동 실행").check();
    await expect(page.getByRole("note", { name: "자동 삭제 경고" })).toBeVisible();
    await assertLayoutSane(page, { minTableCells: 0 });

    await visitUsage(page);
    await assertLayoutSane(page, { minTableCells: 0 });
    await visitUsageStorages(page);
    await assertLayoutSane(page, { minTableCells: 16 });

    for (const [path, heading] of K8S_PAGES) {
      await visitK8s(page, path, heading);
      await assertLayoutSane(page, { minTableCells: 0 });
    }
  });

  test("375x667 모바일 spot check", async ({ page }) => {
    await apiLogin(page);
    // md: 분기 **아래**라 사이드바는 전폭 블록이다 -> L3 제외, L1/L2/L4 만(설계 §3).
    await page.setViewportSize({ width: 375, height: 667 });

    await visit(page, "/admin/accounts", "계정");
    await assertTableOverflows(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 24 });

    await visit(page, "/jobs", "전체 작업");
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 4 });

    // 제출 화면은 1열(시트 → 요약)로 접힌다 -- sync 경로 줄·항목 표가 375 에서 넘치지 않아야 한다.
    await visit(page, "/jobs/new", "단일 작업");
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    await fillLongScanTarget(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    await assertTextContained(page);

    await visit(page, "/admin/batches/new", "배치 생성");
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    await page.getByLabel("연산").selectOption("rm");
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    await page.getByLabel("미리보기 후 자동 실행").check();
    await expect(page.getByRole("note", { name: "자동 삭제 경고" })).toBeVisible();
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    await page.getByLabel("연산").selectOption("scan");
    // 입력 방식 세그먼트는 좁은 폭에서도 라벨이 음절 중간에서 접히지 않는다(통째로 다음 줄로).
    // L4 는 aside a·table button 만 보므로 여기서 직접 잰다(한 줄 = 높이 < 줄 높이 2배).
    const wrapped = await page.locator("button[aria-pressed]").evaluateAll((bs) => bs
      .filter((b) => b.getBoundingClientRect().height
        >= 2 * parseFloat(getComputedStyle(b).lineHeight) + 1)
      .map((b) => b.textContent));
    expect(wrapped, "입력 방식 세그먼트 라벨이 두 줄로 접혔다").toEqual([]);

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

    // k8s 연동: 머리 동작 버튼(클러스터 등록 등)은 375 에서 제목 아래로 접히고, 하위 메뉴 탭 줄은 자기 안에서 가로 스크롤한다.
    for (const [path, heading] of K8S_PAGES) {
      await visitK8s(page, path, heading);
      await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
    }
  });
});
