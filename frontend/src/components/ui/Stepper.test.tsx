import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { Stepper } from "./Stepper";

// 스테퍼의 「상태 → 표시」 계약(슬라이스 31 T3): 활성=aria-current, 완료=체크,
// 미래=순번 숫자. 색 값은 단언하지 않는다 -- 팔레트는 theme.test 가 지킨다.

const steps = [
  { id: "basics", label: "기본 정보" },
  { id: "options", label: "옵션" },
  { id: "review", label: "확인" },
];

test("활성 스텝만 aria-current=step, 완료는 체크, 미래는 순번 숫자", () => {
  const { container } = render(<Stepper steps={steps} current={1} />);
  const done = screen.getByRole("button", { name: "기본 정보" });
  const active = screen.getByRole("button", { name: "옵션" });
  const todo = screen.getByRole("button", { name: "확인" });
  expect(active).toHaveAttribute("aria-current", "step");
  expect(done).not.toHaveAttribute("aria-current");
  expect(todo).not.toHaveAttribute("aria-current");
  // 완료 스텝은 숫자 대신 체크 아이콘(svg) -- "1" 이 보이면 완료 표시가 아니다.
  expect(done.querySelector("svg")).not.toBeNull();
  expect(done.textContent).not.toContain("1");
  // 활성·미래 스텝은 자기 순번(1-기준).
  expect(active.textContent).toContain("2");
  expect(todo.textContent).toContain("3");
  // 규율 통일(플랜 T3): li/button 렌더만 -- h1 은 화면 소유, a 는 라우트 소유.
  expect(container.querySelector("h1")).toBeNull();
  expect(container.querySelector("a")).toBeNull();
  expect(container.querySelectorAll("li").length).toBe(3);
});

test("스텝 클릭이 onNavigate(index) 를 호출한다", async () => {
  const onNavigate = vi.fn();
  render(<Stepper steps={steps} current={2} onNavigate={onNavigate} />);
  await userEvent.click(screen.getByRole("button", { name: "기본 정보" }));
  expect(onNavigate).toHaveBeenCalledWith(0);
});

test("onNavigate 미지정이면 클릭이 조용히 무시된다(단독 표시 용도)", async () => {
  render(<Stepper steps={steps} current={0} />);
  // throw 없이 지나가면 통과 -- 콜백 부재가 크래시가 되면 안 된다.
  await userEvent.click(screen.getByRole("button", { name: "확인" }));
  expect(screen.getByRole("button", { name: "확인" })).toBeInTheDocument();
});
