import { expect, test } from "vitest";
import { SECURITY_EVENT_GROUPS, SECURITY_EVENT_LABELS, eventLabel, ownValue } from "./eventLabels";

// 라벨 키 집합 == 서버 SECURITY_EVENT_TYPES 는 백엔드 테스트(test_api_security)가 이 파일을 읽어 본다. 여기서는 화면 안의 두
// 목록(라벨·필터 묶음)이 서로 어긋나지 않는지만 본다 -- 묶음에 없는 종류는 필터로 고를 수 없고, 라벨 없는 종류는 원문으로 보인다.
test("필터 묶음의 모든 종류에 라벨이 있고, 모든 라벨 키가 정확히 한 묶음에 든다", () => {
  const grouped = SECURITY_EVENT_GROUPS.flatMap((g) => g.types);
  expect(new Set(grouped).size).toBe(grouped.length);
  expect([...grouped].sort()).toEqual(Object.keys(SECURITY_EVENT_LABELS).sort());
  expect(SECURITY_EVENT_GROUPS.map((g) => g.label)).toEqual(["로그인", "인증번호", "메일", "관리자 조치"]);
});

test("로그인 묶음은 login_* 전부, 21종이다", () => {
  const login = SECURITY_EVENT_GROUPS.find((g) => g.label === "로그인")!;
  expect(login.types.every((t) => t.startsWith("login_"))).toBe(true);
  expect(Object.keys(SECURITY_EVENT_LABELS).filter((t) => t.startsWith("login_")).sort()).toEqual([...login.types].sort());
  expect(Object.keys(SECURITY_EVENT_LABELS)).toHaveLength(21);
});

test("인증 메일 「발송 불확실」(릴레이 무응답)도 「메일」 묶음으로 고를 수 있다(2026-11-11 -- 빠져 있어 화면 어디에도 안 보였다)", () => {
  const mail = SECURITY_EVENT_GROUPS.find((g) => g.label === "메일")!;
  expect(mail.types).toContain("verification_email_uncertain");
  expect(eventLabel("verification_email_uncertain")).toBe("인증 메일 발송 불확실(릴레이 무응답)");
});

test("eventLabel: 아는 종류는 라벨, 모르는 종류는 원문", () => {
  expect(eventLabel("login_busy")).toBe("로그인 과부하(503)");
  expect(eventLabel("something_new")).toBe("something_new");
  // Object.prototype 의 이름은 라벨이 아니다(DB 에서 온 값 -- 함수 원문이 화면에 찍히면 안 된다)
  expect(eventLabel("constructor")).toBe("constructor");
  expect(eventLabel("toString")).toBe("toString");
  expect(ownValue({ a: 1 }, "hasOwnProperty")).toBeUndefined();
});
