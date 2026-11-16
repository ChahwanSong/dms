import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { Pager } from "./Pager";

const names = ["처음", "이전", "다음", "마지막"];

test("넘길 쪽이 없으면(pageCount ≤ 1) 아무것도 그리지 않는다", () => {
  for (const n of [1, 0]) {
    const { container, unmount } = render(<Pager page={1} pageCount={n} onPage={() => {}} label="대상 목록 쪽" />);
    expect(container).toBeEmptyDOMElement();
    unmount();
  }
});

test("nav(label) 안에 처음·이전 · 「page / pageCount쪽」 · 다음·마지막 -- 아이콘 버튼은 sr-only 이름, 한 줄", async () => {
  const onPage = vi.fn();
  render(<Pager page={3} pageCount={10} onPage={onPage} label="대상 목록 쪽" />);
  const nav = screen.getByRole("navigation", { name: "대상 목록 쪽" });
  expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(names);
  for (const n of names) {
    const b = within(nav).getByRole("button", { name: n });
    expect(b).toBeEnabled();
    expect(within(b).getByText(n)).toHaveClass("sr-only");
    expect(b.querySelector("svg")).toHaveAttribute("aria-hidden");
  }
  expect(nav).toHaveTextContent("3 / 10쪽");
  expect(nav.className).toMatch(/\bflex-nowrap\b/);
  expect(nav.className).toMatch(/\bwhitespace-nowrap\b/);
  expect(within(nav).getByText("3 / 10쪽")).toHaveClass("whitespace-nowrap");
  for (const [n, want] of [["처음", 1], ["이전", 2], ["다음", 4], ["마지막", 10]] as const) {
    await userEvent.click(within(nav).getByRole("button", { name: n }));
    expect(onPage).toHaveBeenLastCalledWith(want);
  }
  expect(onPage).toHaveBeenCalledTimes(4);
});

test("경계에서 disabled: 첫 쪽은 처음·이전, 마지막 쪽은 다음·마지막 -- 눌러도 부르지 않는다", async () => {
  const onPage = vi.fn();
  const { rerender } = render(<Pager page={1} pageCount={2} onPage={onPage} label="쪽" />);
  const state = () => names.map((n) => screen.getByRole("button", { name: n }).hasAttribute("disabled"));
  expect(state()).toEqual([true, true, false, false]);
  await userEvent.click(screen.getByRole("button", { name: "이전" }));
  expect(onPage).not.toHaveBeenCalled();
  rerender(<Pager page={2} pageCount={2} onPage={onPage} label="쪽" />);
  expect(state()).toEqual([false, false, true, true]);
  expect(screen.getByRole("navigation", { name: "쪽" })).toHaveTextContent("2 / 2쪽");
  await userEvent.click(screen.getByRole("button", { name: "마지막" }));
  expect(onPage).not.toHaveBeenCalled();
});
