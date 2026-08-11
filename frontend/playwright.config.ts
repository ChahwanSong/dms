import { defineConfig } from "@playwright/test";

// e2e 러너 설정. globalSetup/Teardown(풀스택 부팅·시드)은 다음 태스크가 배선한다.
export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  fullyParallel: false, // 단일 sqlite 상태를 공유하므로 병렬 금지
  retries: 0, // 플레이크를 재시도로 숨기지 않는다
  // CI 가 없어 `npm run test:e2e` 수기 실행이 유일한 게이트다 --
  // 남겨진 test.only 하나가 스위트를 1건으로 줄이고도 초록이 되는 걸 막는다.
  forbidOnly: true,
  timeout: 60_000,
  reporter: [["list"]],
  outputDir: "./test-results",
  use: {
    baseURL: "http://127.0.0.1:8093",
    // 시스템 크롬을 쓴다 -- 브라우저 다운로드 0 이 이 슬라이스의 계약이다.
    channel: process.env.DMS_E2E_BROWSER_CHANNEL ?? "chrome",
    headless: true, // 이 개발 환경은 헤드리스다
    viewport: { width: 1280, height: 800 },
    trace: "retain-on-failure",
  },
});
