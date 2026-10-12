import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { CancelButton } from "./CancelButton";

// 2026-10-12 사용자 요청 「취소버튼을 조금 눈에띄게」: 빨강 아웃라인 + 아이콘. 아이콘이 접근 이름에 끼면 「취소」 정확 일치
// 단언·e2e 가 전부 깨진다 -- 이름이 글자 그대로인지가 이 컴포넌트의 계약이다.
test("빨강 아웃라인 + 장식 아이콘, 접근 이름은 글자 그대로", async () => {
  const onClick = vi.fn();
  render(<CancelButton onClick={onClick}>작업 취소</CancelButton>);
  const btn = screen.getByRole("button", { name: "작업 취소" });
  expect(btn).toHaveAccessibleName("작업 취소");
  expect(btn.className).toContain("text-bad");
  expect(btn.className).toContain("border-bad");
  const icon = btn.querySelector("svg");
  expect(icon).not.toBeNull();
  expect(icon!.getAttribute("aria-hidden")).toBe("true");
  await userEvent.click(btn);
  expect(onClick).toHaveBeenCalledTimes(1);
});

test("disabled·className 을 그대로 넘긴다", () => {
  render(<CancelButton disabled className="w-full">취소</CancelButton>);
  const btn = screen.getByRole("button", { name: "취소" });
  expect(btn).toBeDisabled();
  expect(btn.className).toContain("w-full");
  expect(btn.className).toContain("gap-1.5");
});
