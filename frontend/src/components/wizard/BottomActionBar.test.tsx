import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { BottomActionBar } from "./BottomActionBar";
import { Button } from "../ui/Button";

// 액션바는 위저드 비종속 슬롯 컴포넌트(슬라이스 31 T3): cancel(좌)/help(중앙)/
// actions(우) 를 그대로 렌더한다. 슬롯이 ReactNode 그대로라 disabled 같은 버튼
// 속성이 중간에서 소실되지 않는 것까지 계약이다.

test("cancel/help/actions 3 슬롯이 렌더되고 disabled 가 그대로 전달된다", () => {
  const { container } = render(
    <BottomActionBar
      cancel={<Button variant="ghost">취소</Button>}
      help={<span>문의: dms-admin</span>}
      actions={
        <>
          <Button variant="outline">이전</Button>
          <Button disabled>다음</Button>
        </>
      }
    />,
  );
  expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
  expect(screen.getByText("문의: dms-admin")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "이전" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  // 상단 구분선 계약(border-t border-line) -- DS 의 보더 구획.
  const bar = container.firstElementChild!;
  expect(bar.className).toContain("border-t");
  expect(bar.className).toContain("border-line");
});

test("help 슬롯은 선택적이다 -- 없어도 좌/우 슬롯이 그대로 렌더된다", () => {
  render(
    <BottomActionBar cancel={<button>취소</button>} actions={<button>제출</button>} />,
  );
  expect(screen.getByRole("button", { name: "취소" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "제출" })).toBeInTheDocument();
});
