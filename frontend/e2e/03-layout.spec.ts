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

// 사용량 분석(2026-10-20 스토리지 탭): 탭 줄과 고른 탭의 패널(표·빈 상태 어느 쪽이든)이 그려진 뒤에 잰다 -- 탭은 등록
// 목록이 오면 바로 그려지지만 패널은 요약(기본 탭 판정)을 기다린다.
async function visitUsage(page: Page): Promise<void> {
  await visit(page, "/admin/usage", "사용량 분석");
  await expect(page.getByRole("tab", { name: new RegExp(`^${STORAGE_NAME}`) })).toHaveAttribute("aria-selected", "true");
  const panel = page.getByRole("tabpanel");
  await expect(panel.locator("table").first().or(panel.getByText("이 스토리지에는 아직 성공한 scan 이 없습니다"))).toBeVisible();
}

// 제출 화면 둘(단일 작업·배치 생성)의 흐름은 helpers/layoutSingle·layoutBatch 에 있다(dms-k8s fe87a767 이식 -- 2026-11-27 단계별
// 등록으로 흐름이 단계마다 길어져 이 spec 에서 뺐다). 이 파일은 폭마다 singleLayoutFlow·batchLayoutFlow 를 부르기만 한다.

// 배치 목록(2026-11-16 「대상 스토리지」 열 추가): 표(배치가 있을 때)나 빈 상태 문구 어느 쪽이든 그려진 뒤에 잰다 -- 목록은
// heading 보다 늦게 온다.
async function visitBatches(page: Page): Promise<void> {
  await visit(page, "/admin/batches", "배치 작업");
  await expect(page.locator("main table").first().or(page.getByText("배치가 없습니다"))).toBeVisible();
}

// 배치 스케줄러(2026-11-16): 표(스케줄이 있을 때)나 빈 상태 문구 어느 쪽이든 그려진 뒤에 잰다 -- 목록은 heading 보다 늦게 온다.
async function visitSchedules(page: Page): Promise<void> {
  await visit(page, "/admin/batch-schedules", "배치 스케줄러");
  await expect(page.locator("main table").first().or(page.getByText(/^스케줄이 없습니다/))).toBeVisible();
}

// 마운트 현황(2026-11-23): 표는 노드 보고(global-setup 의 에이전트 e2e-node)가 와야 그려진다 -- heading 보다 늦게 오므로 노드 칸의
// 이름 링크가 보일 때까지 기다린다.
async function visitMounts(page: Page): Promise<void> {
  await visit(page, "/admin/mounts", "마운트 현황");
  await expect(page.getByRole("table").getByRole("link", { name: "e2e-node", exact: true })).toBeVisible();
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
//   /admin/batches   표는 배치가 있을 때만 그려진다(이 파일 시점엔 배치가 없다 -- 07 이 처음 만든다) -> 하한 0. 2026-11-16
//                    「대상 스토리지」 열(자유 글자 스토리지 이름, break-all + 최소 폭)로 8열이 돼 375 에서 표가 넘치는 자리라
//                    순회에 넣는다 -- 넘쳐도 Table 래퍼 안에서만 스크롤하고 문서는 넘치지 않아야 한다(L1).
//   /admin/batch-schedules
//                    표는 스케줄이 있을 때만 그려진다(이 파일 시점엔 없다 -- E15(14)가 처음 만들고 그 행의 표 불변식도 잰다) ->
//                    하한 0. 2026-11-16 새 화면(9열 표 + 안내 패널 + 「스케줄 추가」 머리줄)이라 L1/L3/L4 를 여기서 진다.
//   /admin/usage     표는 고른 스토리지 탭에 성공 scan 이 있을 때만 그려진다(이 파일 시점엔 보통 없다) -> 하한 0.
//                    2026-10-20 스토리지 탭 줄(가로 스크롤)이 새 L1 위험이라 순회에 넣는다 -- 탭 안 sr-only 노드가 화면
//                    밖으로 문서를 넓히던 유형(StatusPill 619/375). 행·펼침 상세의 375 불변식은 E10(09)이 기록과 함께 잰다.
//   /admin/mounts    th 3(노드·e2e-store·아티팩트 경로) + e2e-node 1행 × td 3 = 6 -- 이 파일 시점엔 스토리지 1·보고 노드 1이다.
//                    「본문 열」 테스트(가장 넓은 표 화면)와 375 spot check(2026-11-27 표 압축 -- 거르기 줄·고정 머리 래퍼)에서
//                    순회한다.
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

    // 제출 화면 둘(2026-11-27 단계별 등록) -- 표가 없으므로 하한 0, 단계마다 L1·L3(사이드바 240, 카드·바가 <aside> 가 아님)·L4 가
    // 진다(흐름은 helpers/layoutSingle·layoutBatch -- 1280 흐름은 768×900 재측정을 품고 끝에 1280×800 으로 돌린다).
    await singleLayoutFlow(page, 1280);
    await batchLayoutFlow(page, 1280);

    await visitBatches(page);
    await assertLayoutSane(page, { minTableCells: 0 });

    await visitSchedules(page);
    await assertLayoutSane(page, { minTableCells: 0 });

    await visitUsage(page);
    await assertLayoutSane(page, { minTableCells: 0 });
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

    // 제출 화면은 좁은 폭으로 접힌다 -- 경로 줄·검토 칸·항목 표·입력 방식 세그먼트가 375 에서 넘치거나 접히지 않고, 포커스한
    // 입력·동의 체크가 하단 고정 바에 가리지 않아야 한다(흐름은 helpers).
    await singleLayoutFlow(page, 375);
    await batchLayoutFlow(page, 375);

    await visitBatches(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });

    await visitSchedules(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });

    // 사용량 분석 스토리지 탭 줄은 375 에서 줄바꿈 없이 가로 스크롤한다 -- 줄 자체만 넘치고 문서는 넘치지 않아야 한다(L1).
    await visitUsage(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });

    // 마운트 현황(2026-11-27 표 압축 -- dms-k8s 7a172d1d 이식): 거르기 줄(토글 둘 + 스토리지 찾기 칸)·범례는 375 에서 접히고, 고정
    // 머리·노드 칸 표는 자기 래퍼(가로·세로 스크롤) 안에서만 넘친다 -- 문서는 넘치지 않는다(L1).
    await visitMounts(page);
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 6 });

    // 대시보드 노드 칸(2026-10-16 CPU 사용률 추가로 5칸): md 아래는 2열로 쌓여 375 에서 넘치지 않아야 한다.
    await visit(page, "/admin/dashboard", "대시보드");
    await expect(page.getByRole("group", { name: / CPU 사용률$/ }).first()).toBeVisible();
    await assertLayoutSane(page, { sidebarFixed: false, minTableCells: 0 });
  });

  // 본문 열(dms-k8s 08cba773 이식 -- 2026-11-26 사용자 결정 「A안」, AppShell main 의 mx-auto max-w-[90rem]): 넓은 창(1920)에서는
  // 최대 1440 열이 사이드바 오른쪽 영역 가운데에 서고, 1680 이하 창(1280)에서는 예전처럼 여백 없이 영역을 꽉 채운다(L5 --
  // helpers/layout assertMainColumn). 네 화면의 h1 왼쪽이 같아야 한다: 기준선은 본문 열의 왼쪽 하나다 -- 한 화면이 스스로
  // mx-auto 하면(2026-08-16 사용자 지적 유형) 그 화면 제목만 옆으로 튄다. 화면은 넓은 표(마운트 현황)·목록(전체 작업)·
  // 제출 위저드(단일 작업)·카드(대시보드) 한 벌씩이다.
  test("본문 열: 1920 에서 최대 1440 가운데 · 1280 에서 여백 없음 · 화면 제목 기준선 하나", async ({ page }) => {
    await apiLogin(page);
    const screens: Array<[string, () => Promise<void>, number]> = [
      ["대시보드", () => visit(page, "/admin/dashboard", "대시보드"), 0],
      ["전체 작업", () => visit(page, "/jobs", "전체 작업"), 4],
      ["단일 작업", () => visit(page, "/jobs/new", "단일 작업"), 0],
      ["마운트 현황", () => visitMounts(page), 6],
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
