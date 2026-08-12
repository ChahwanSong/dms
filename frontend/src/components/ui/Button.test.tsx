import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { Button } from "./Button";

// variant 3계층의 클래스 분기 계약(슬라이스 31 T3). 클래스명 존재만 단언한다 --
// 색 hex 값은 tailwind 토큰(theme.test)의 몫이라 여기서 다시 재지 않는다.
test("variant 3종이 서로 다른 계층 클래스로 분기된다", () => {
  const { rerender } = render(<Button>확인</Button>);
  const btn = screen.getByRole("button", { name: "확인" });
  // 기본값 primary = 솔리드 파랑(기존 사용처 호환).
  expect(btn.className).toContain("bg-accent");
  expect(btn.className).toContain("text-white");

  // outline = 파랑 아웃라인(신설) -- 솔리드 배경이 아니다.
  rerender(<Button variant="outline">확인</Button>);
  expect(btn.className).toContain("border-accent");
  expect(btn.className).not.toContain("bg-accent");

  // ghost = 회색 아웃라인(취소 용도, 기존 호환) -- 파랑 계열이 전혀 없다.
  rerender(<Button variant="ghost">확인</Button>);
  expect(btn.className).toContain("border-line");
  expect(btn.className).not.toContain("accent");
});
