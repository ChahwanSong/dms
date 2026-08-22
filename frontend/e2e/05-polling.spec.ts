import { expect, test, type Page } from "@playwright/test";
import { STORAGE_NAME } from "./harness/env";
import { apiLogin } from "./helpers/session";

// E5/E6 — 폴링. 단위 테스트가 구조적으로 못 보는 것을 여기서만 본다: **시간**이다.
// jsdom 에는 실 타이머 위에서 도는 react-query 인터벌도, 그 인터벌이 낳는 실 HTTP
// 요청도 없다. 이 파일은 두 방향을 각각 잡는다.
//   E5: 폴링이 **돈다**(목록 3s 상시 — useJobs.ts:8) — 리로드 없이 새 행이 도착한다.
//   E6: 폴링이 **멈춘다**(상세 잡 2s + 종단 중지 콜백 — useJobs.ts:17-20) —
//       종단 뒤엔 요청이 한 건도 나가지 않는다.
//
// 계측 대상 주의: 종단 중지 콜백이 달린 쪽은 **상세 잡**(`/api/user/requests/{id}/jobs`)
// 이지 요청 **목록**(`/api/user/requests`, 상시 3s)이 아니다. 둘을 섞으면 E6 은
// 영영 초록이 못 된다 -- 카운터는 pathname 완전 일치로 좁힌다.

// 제출 헬퍼. target 은 시나리오마다 달라야 한다: 같은
// requester+storage+target+options 는 resource_key 가 같아 활성 요청이 있으면
// Conflict 로 떨어진다(E4 는 e4-scan, 여기는 e5-scan/e6-scan).
async function submitScan(page: Page, target: string): Promise<string> {
  const response = await page.request.post("/api/user/requests", {
    data: {
      operation: "scan", storage: STORAGE_NAME, target,
      options: {}, priority: "mid",
    },
  });
  expect(response.status(), await response.text()).toBe(202);
  const body = (await response.json()) as { request_id?: string };
  const rid = body.request_id;
  // 빈 문자열/undefined 를 그대로 안고 가면 아래 필터가 **모든 행**에 걸리거나
  // 아무 행에도 안 걸려, 폴링과 무관한 이유로 초록/빨강이 난다.
  expect(typeof rid, `202 응답에 request_id 가 문자열로 없다: ${JSON.stringify(body)}`)
    .toBe("string");
  expect(rid!.length).toBeGreaterThan(0);
  return rid!;
}

test.describe("E5 폴링 수렴", () => {
  test("목록을 연 채로 제출한 요청이 리로드 없이 3s 폴링을 타고 도착한다", async ({ page }) => {
    await apiLogin(page);
    await page.goto("/jobs");

    // **제출 전에** 최초 fetch 가 끝났음을 못박는다. 표는 isLoading 이 false 일
    // 때만 렌더되므로(JobsList.tsx) 표가 보인다 = 마운트 fetch 는 이미 응답을
    // 받았다는 뜻이다. 이게 없으면 "마운트 fetch 가 우연히 제출 뒤에 도착"한
    // 경우까지 초록이 되어, 폴링이 죽어 있어도 통과하는 공허한 단언이 된다.
    await expect(page.getByRole("heading", { name: "전체 작업" })).toBeVisible();
    await expect(page.getByRole("table")).toBeVisible();

    // 「새로고침 없이」의 증거. window 는 문서 내비게이션에서 통째로 교체되므로
    // 이 표식의 생존이 곧 하드 리로드 0 의 증명이다(E4 와 같은 규율).
    await page.evaluate(() => {
      (window as unknown as Record<string, unknown>).__dmsE2eNoReload = true;
    });

    // page.request 는 브라우저 컨텍스트와 쿠키를 공유한다 -- 같은 세션의 제출이다.
    // UI 폼을 거치지 않는 이유: 이 시나리오의 관심사는 제출 경로가 아니라(그건 E4)
    // **화면을 떠나지 않은 채** 서버 상태가 바뀌었을 때의 수렴이다.
    const rid5 = await submitScan(page, "e5-scan");

    // 이 행은 제출 **후**에 생겼다 -- 최초 fetch 에는 존재할 수 없었다. 따라서
    // 리로드 없이 이 행이 뜨는 유일한 경로는 3s 목록 폴링뿐이다(상한 10s = 폴링
    // 2~3회분).
    await expect(
      page.getByRole("row").filter({ hasText: rid5 }),
      `제출한 요청 ${rid5} 의 행이 10s 안에 목록에 도착하지 않았다 -- ` +
        `useRequests 의 3s 목록 폴링(useJobs.ts:8)이 끊겼다`,
    ).toHaveCount(1, { timeout: 10_000 });

    expect(
      await page.evaluate(
        () => (window as unknown as Record<string, unknown>).__dmsE2eNoReload === true),
      "문서가 다시 로드됐다 -- 새 행을 폴링이 아니라 새로고침이 날랐다면 " +
        "이 시나리오는 아무것도 증명하지 못한다",
    ).toBe(true);
  });
});

test.describe("E6 폴링 중지", () => {
  test("종단된 요청 상세는 잡 폴링을 한 건도 더 내지 않는다", async ({ page }) => {
    // 종단 대기 30s + 정착 1s + 관찰 8s + 부팅/렌더 여유. 기본 60s 는 종단이
    // 느린 날 관찰 창을 잡아먹는다 -- 관찰 창을 줄이는 대신 예산을 늘린다.
    test.setTimeout(90_000);

    await apiLogin(page);
    const rid6 = await submitScan(page, "e6-scan");

    // **종단 후 진입**이 이 시나리오의 결정적 형태다. useRequestJobs 의 콜백은
    // 잡 배열이 **비어 있어도** 폴링을 멈춘다(`[].some(...)` === false). 종단 전에
    // 상세로 들어가면 플래너 틱보다 먼저 도착한 순간 잡이 영영 안 뜨고, 그때의
    // "요청 0건" 은 종단 중지가 아니라 빈 배열 함정이다 -- 관측하려는 성질과
    // 다른 이유로 초록이 난다. API 로 먼저 종단을 확인하고 들어간다.
    await expect.poll(async () => {
      const response = await page.request.get(`/api/user/requests/${rid6}`);
      if (response.status() !== 200) return `HTTP ${response.status()}`;
      return ((await response.json()) as { state: string }).state;
    }, {
      timeout: 30_000,
      intervals: [500],
      message: `요청 ${rid6} 가 30s 안에 종단하지 않았다 -- 컨트롤러/플래너/스테퍼 ` +
        `배선을 먼저 보라(폴링 이전의 문제다)`,
    }).toBe("Succeeded");

    // 잡이 실제로 있고 전부 종단인지도 못박는다. 빈 배열이면 아래의 "요청 0건" 은
    // 종단 중지가 아니라 빈 배열 함정의 결과가 되므로, 두 원인을 여기서 가른다.
    const jobsResponse = await page.request.get(`/api/user/requests/${rid6}/jobs`);
    expect(jobsResponse.status(), await jobsResponse.text()).toBe(200);
    const jobs = (await jobsResponse.json()) as { job_id: string; state: string }[];
    expect(jobs.length, `요청 ${rid6} 가 Succeeded 인데 잡이 0건이다 -- ` +
      `종단 중지를 관측할 대상 자체가 없다`).toBeGreaterThanOrEqual(1);
    for (const job of jobs) {
      expect(job.state, `잡 ${job.job_id} 가 종단이 아니다`).toBe("Succeeded");
    }

    // 카운터는 **goto 전에** 설치한다(마운트 최초 fetch 를 놓치면 phase A 가
    // 성립하지 않는다). pathname 완전 일치 -- 목록 `/api/user/requests` 의 상시
    // 3s 폴링과 절대 섞이지 않아야 한다.
    const jobsPath = `/api/user/requests/${rid6}/jobs`;
    let jobsFetches = 0;
    page.on("request", (r) => {
      let pathname: string;
      try {
        pathname = new URL(r.url()).pathname;
      } catch {
        return; // data:/blob: 등 파싱 불가 URL 은 관심사가 아니다
      }
      if (pathname === jobsPath) jobsFetches += 1;
    });

    await page.goto(`/jobs/${rid6}`);

    // phase A — 필터 생존 증명. 마운트 시 최초 fetch 는 **반드시** 이 필터에
    // 잡힌다. 0 은 정상값이므로, 이 증명 없이는 아래 `toBe(0)` 이 "필터 오타로
    // 아무것도 못 셌다" 와 구별되지 않는다.
    await expect.poll(() => jobsFetches, {
      timeout: 10_000,
      message: `${jobsPath} 로 나가는 요청을 한 건도 못 셌다 -- 카운터 필터가 ` +
        `죽었다면 아래의 "0건" 단언은 아무 의미가 없다`,
    }).toBeGreaterThanOrEqual(1);

    // 잡 쿼리의 데이터가 실제로 화면에 도착했는지 본다. job_id 를 그대로 찍는
    // 곳은 잡 카드뿐이므로(RequestDetail.tsx:179), 이 텍스트의 존재가 곧
    // "잡 배열이 비어 있지 않다" 의 UI 측 증거다 -- 즉 이 뒤의 중지는 빈 배열
    // 함정이 아니라 종단 중지다.
    await expect(
      page.getByText(jobs[0].job_id, { exact: true }),
      `잡 ${jobs[0].job_id} 카드가 상세에 렌더되지 않았다`,
    ).toBeVisible();
    // 상태 배지는 상태 문자열을 그대로 렌더한다(StatusPill) -- 문구 번역이 아니라
    // 상태 값이다. 요청 1개 + 잡 N개 = 배지 N+1 개가 전부 Succeeded 여야 한다
    // (전이 이력의 "X → Succeeded" 는 exact 매칭에 걸리지 않는다).
    await expect(
      page.getByText("Succeeded", { exact: true }),
      `요청+잡 ${jobs.length + 1}개 모두 Succeeded 배지여야 한다`,
    ).toHaveCount(jobs.length + 1);

    // 종단 데이터를 나른 fetch 직후, 그 직전 interval 이 이미 띄워 둔 요청이
    // 비행 중일 수 있다. 그 마지막 한 건을 소진시킬 유예다 -- 이 유예가 없으면
    // 정상 동작에서도 카운터가 1을 셀 수 있어 플레이키해진다.
    await page.waitForTimeout(1000);

    // 관찰 창: 2s 인터벌이 살아 있다면 8s 에 3~4건이 잡힌다. 종단 중지 콜백이
    // 제 몫을 하면 정확히 0 이다.
    jobsFetches = 0;
    await page.waitForTimeout(8000);
    expect(
      jobsFetches,
      `종단 뒤 8s 동안 ${jobsPath} 요청이 ${jobsFetches}건 나갔다 -- ` +
        `useRequestJobs 의 종단 중지 콜백(useJobs.ts:17-20)이 죽었다`,
    ).toBe(0);
  });
});
