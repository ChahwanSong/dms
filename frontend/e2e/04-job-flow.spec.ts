import { expect, test } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";
import { assertLayoutSane } from "./helpers/layout";

// E4 — 잡 종단 흐름. 이 파일에만 있는 것: **UI 제출 한 번이 풀스택 배선 전체를
// 밟는다**. 폼 -> POST /api/user/requests(202) -> 플래너가 에이전트 리포트에서
// 후보를 고름 -> 스테퍼가 stub 실행기로 전진 -> 요청 종단(Succeeded) -> 그 변화가
// **리로드 없이** 3s 목록 폴링으로 화면에 도착.
//
// 리뷰 포인트: 이 스펙에는 `page.reload()` 가 **한 줄도 없다**. "새로고침 없이
// 수렴한다"가 이 시나리오의 존재 이유이고, reload 를 한 번이라도 부르는 순간
// 폴링이 죽어 있어도 초록이 나서 시나리오가 통째로 무의미해진다.
//
// 단언 재료는 URL·개수·상태 값으로 제한한다(설계 §2.1) -- StatusPill 은 상태
// 문자열을 그대로 렌더하므로("Succeeded") 이건 문구 번역이 아니라 상태 값이다.
test.describe("E4 잡 종단 흐름", () => {
  test("UI scan 제출 -> 리로드 없이 목록이 Succeeded 로 수렴한다", async ({ page }) => {
    // admin 으로 들어간다: 특권 요청자(root/admin 기본값)라야 LDAP 없는 이 환경에서
    // placement 의 신원 검사를 건너뛰고 후보 선정까지 간다(설계 §1-8).
    await apiLogin(page);
    // 슬라이스 37: scan 은 단일 작업 위저드(연산 스텝의 운영자 전용 옵션)로 흡수됐다.
    await page.goto("/jobs/new");
    await expect(page.getByRole("heading", { name: "단일 작업" })).toBeVisible();
    await page.getByLabel("연산").selectOption("scan");
    await page.getByRole("button", { name: "다음" }).click();

    // 스토리지 목록이 폼까지 도착했는지를 먼저 못박는다. 이게 없으면 아래
    // selectOption 이 타임아웃했을 때 "폼이 깨졌나 / 시드가 안 실렸나"가 뭉개진다.
    const storage = page.getByLabel("스토리지");
    await expect(
      storage.locator(`option[value="${STORAGE_NAME}"]`),
      `시드 스토리지 ${STORAGE_NAME} 가 스토리지 선택지에 없다 -- ` +
        `하네스 시드나 /api/user/storages 를 확인하라`,
    ).toHaveCount(1);
    await storage.selectOption(STORAGE_NAME);

    // 상대경로여야 한다(scan target 은 validate_relative_path). 그리고 E5/E6 과
    // **달라야** 한다: 같은 requester+storage+target+options 는 resource_key 가
    // 같아 활성 요청이 있으면 Conflict 로 떨어진다.
    await page.getByLabel("대상 경로").fill("e4-scan");
    // 옵션 스텝(기본값 그대로) → 확인 스텝 → 제출.
    await page.getByRole("button", { name: "다음" }).click();
    await page.getByRole("button", { name: "다음" }).click();
    await page.getByRole("button", { name: "제출", exact: true }).click();

    // 202 수리와 상세 자동 이동(SubmitJob onSuccess nav)을 URL 하나로
    // 단언한다 -- 서버가 4xx 를 주면 SPA 는 이 화면에 머무르므로 여기서 빨개진다.
    await expect(page).toHaveURL(/\/jobs\/[0-9a-f-]+$/);
    const match = new URL(page.url()).pathname.match(/^\/jobs\/([0-9a-f-]+)$/);
    expect(match, `상세 URL 에서 request_id 를 못 뽑았다: ${page.url()}`).not.toBeNull();
    const rid = match![1];

    // 상세가 데이터를 받은 뒤에 기하를 잰다("불러오는 중…" 상태의 h1 은 로딩
    // 중에도 이미 보여서 안정화 신호가 못 된다).
    await expect(page.getByRole("heading", { name: "전이 이력" })).toBeVisible();
    // 설계 §3 화면 목록의 "요청 상세" 순회 몫을 여기서 갚는다 -- E3 은 파일 순서상
    // 요청이 하나도 없는 시점이라 상세를 돌 수 없다(교차 파일 상태 공유보다 이 배치가
    // 결정적이다). 상세엔 표가 없으므로 minTableCells 는 기본 0 이고 L1/L3/L4 가 진다.
    await assertLayoutSane(page);

    // 「새로고침 없이」의 증거를 심는다: window 는 문서 내비게이션에서 통째로
    // 교체되므로, 이 표식이 끝까지 살아 있다는 것은 하드 리로드가 **한 번도**
    // 없었다는 뜻이다. 스펙에 reload 를 안 쓰는 것만으로는 "실수로 들어갔다"를
    // 못 막는다 -- 표식이 그 규율을 실행 시점에 강제한다.
    await page.evaluate(() => {
      (window as unknown as Record<string, unknown>).__dmsE2eNoReload = true;
    });

    // 사이드바 클릭 = 클라이언트 내비. aside 로 스코프해 목록 화면의 동명 heading 과
    // 섞이지 않게 한다.
    await page.locator("aside").getByRole("link", { name: "전체 작업" }).click();
    await expect(page).toHaveURL(/\/jobs$/);

    const row = page.getByRole("row").filter({ hasText: rid });
    await expect(row, `요청 ${rid} 행이 목록에 정확히 1개여야 한다`).toHaveCount(1);
    // 여기가 이 시나리오의 심장이다. 30s 안에 Succeeded 가 뜨려면 (a) 에이전트
    // 리포트가 실려 플래너가 후보를 찾고 (b) 컨트롤러가 살아서 틱을 돌고
    // (c) 프론트의 3s 목록 폴링이 그 변화를 날라야 한다 -- 셋 중 하나만 끊겨도
    // 빨개진다(stub 종단은 실측 수 초, 30s 는 설계 §5 의 상한).
    await expect(row, `요청 ${rid} 가 30s 안에 Succeeded 로 수렴하지 않았다 -- ` +
      `에이전트 리포트/플래너 후보/스테퍼 전진/3s 목록 폴링 중 하나가 끊겼다`)
      .toContainText("Succeeded", { timeout: 30_000 });

    expect(
      await page.evaluate(
        () => (window as unknown as Record<string, unknown>).__dmsE2eNoReload === true),
      "문서가 다시 로드됐다 -- 상태 수렴을 폴링이 아니라 새로고침이 날랐다면 " +
        "이 시나리오는 아무것도 증명하지 못한다",
    ).toBe(true);
  });
});
