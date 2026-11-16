import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, test, vi } from "vitest";
import { Pager } from "./Pager";

test("한 쪽뿐이면(0·1) 아무것도 그리지 않는다", () => {
  const { container, rerender } = render(<Pager page={1} pageCount={1} onPage={() => {}} label="항목 쪽" />);
  expect(container).toBeEmptyDOMElement();
  rerender(<Pager page={1} pageCount={0} onPage={() => {}} label="항목 쪽" />);
  expect(container).toBeEmptyDOMElement();
});

test("nav(label) 안에 처음·이전·「N / M쪽」·다음·마지막 — 버튼 이름은 sr-only 라벨", () => {
  render(<Pager page={2} pageCount={5} onPage={() => {}} label="항목 쪽" />);
  const nav = screen.getByRole("navigation", { name: "항목 쪽" });
  expect(nav).toHaveTextContent("2 / 5쪽");
  for (const name of ["처음", "이전", "다음", "마지막"]) {
    expect(screen.getByRole("button", { name })).toBeEnabled();
  }
  // 375 에서 한 줄(접히지 않음)
  expect(nav.className).toContain("whitespace-nowrap");
});

test("경계에서 disabled: 첫 쪽은 처음·이전, 마지막 쪽은 다음·마지막", () => {
  const { rerender } = render(<Pager page={1} pageCount={3} onPage={() => {}} label="항목 쪽" />);
  expect(screen.getByRole("button", { name: "처음" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "이전" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "다음" })).toBeEnabled();
  rerender(<Pager page={3} pageCount={3} onPage={() => {}} label="항목 쪽" />);
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "마지막" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "이전" })).toBeEnabled();
});

test("누르면 onPage(1부터) — 처음=1·이전=page-1·다음=page+1·마지막=pageCount", async () => {
  const onPage = vi.fn();
  render(<Pager page={3} pageCount={7} onPage={onPage} label="항목 쪽" />);
  await userEvent.click(screen.getByRole("button", { name: "처음" }));
  await userEvent.click(screen.getByRole("button", { name: "이전" }));
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.click(screen.getByRole("button", { name: "마지막" }));
  expect(onPage.mock.calls.map((c) => c[0])).toEqual([1, 2, 4, 7]);
});
