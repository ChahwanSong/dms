import { expect, type Page } from "@playwright/test";
import { ADMIN } from "../harness/env";

/**
 * API 로그인. page.request 는 브라우저 컨텍스트와 쿠키 저장소를 공유하므로,
 * 이 뒤의 page.goto 는 이미 인증 상태다.
 *
 * **UI 로그인은 E1 의 단일 책임이다** -- 다른 시나리오가 폼을 다시 거치면 느리고,
 * 로그인이 깨졌을 때 스위트 전체가 같이 빨개져 신호가 뭉개진다.
 */
export async function apiLogin(page: Page): Promise<void> {
  const response = await page.request.post("/api/auth/login", { data: ADMIN });
  // 200 을 단언하지 않으면 401 을 그대로 안고 가서 "왜 /login 으로 튕기지?" 로
  // 시작하는 엉뚱한 디버깅이 된다.
  expect(response.status(), await response.text()).toBe(200);
}
