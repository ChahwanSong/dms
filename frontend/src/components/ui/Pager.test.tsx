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

// --- 쪽 수 모름(pageCount null -- 사용량 PV 경로 창 넘침의 pageable_count: null). dms-k8s 확장. ---

const nullNames = ["처음", "이전", "다음"];
const nullState = () => nullNames.map((n) => screen.getByRole("button", { name: n }).hasAttribute("disabled"));

test("쪽 수 모름: 1쪽인데 다음도 없으면(hasMore false·부재) 그리지 않는다 -- 0쪽·1쪽 표시로 접지 않는다", () => {
  for (const hasMore of [false, undefined]) {
    const { container, unmount } = render(
      <Pager page={1} pageCount={null} hasMore={hasMore} onPage={() => {}} label="쪽" />);
    expect(container).toBeEmptyDOMElement();
    unmount();
  }
});

test("쪽 수 모름: 「n쪽」 + 처음·이전·다음만(마지막 없음), 다음은 hasMore 일 때만 산다", async () => {
  const onPage = vi.fn();
  const { rerender } = render(<Pager page={1} pageCount={null} hasMore onPage={onPage} label="대상 목록 쪽" />);
  const nav = screen.getByRole("navigation", { name: "대상 목록 쪽" });
  expect(within(nav).getAllByRole("button").map((b) => b.textContent)).toEqual(nullNames);
  expect(within(nav).queryByRole("button", { name: "마지막" })).toBeNull();
  expect(within(nav).getByText("1쪽")).toHaveClass("whitespace-nowrap");
  expect(nav).not.toHaveTextContent("/");
  expect(nullState()).toEqual([true, true, false]);
  await userEvent.click(within(nav).getByRole("button", { name: "다음" }));
  expect(onPage).toHaveBeenLastCalledWith(2);

  // 가운데 쪽: 셋 다 산다
  rerender(<Pager page={3} pageCount={null} hasMore onPage={onPage} label="대상 목록 쪽" />);
  expect(screen.getByRole("navigation", { name: "대상 목록 쪽" })).toHaveTextContent("3쪽");
  expect(nullState()).toEqual([false, false, false]);
  for (const [n, want] of [["처음", 1], ["이전", 2], ["다음", 4]] as const) {
    await userEvent.click(screen.getByRole("button", { name: n }));
    expect(onPage).toHaveBeenLastCalledWith(want);
  }

  // 다음이 없는 쪽(범위 밖 offset 으로 빈 쪽 포함): 되돌아갈 버튼은 남고 다음은 닫힌다 -- 부재(hasMore 생략)도 「없음」
  onPage.mockClear();
  for (const hasMore of [false, undefined]) {
    rerender(<Pager page={4} pageCount={null} hasMore={hasMore} onPage={onPage} label="대상 목록 쪽" />);
    expect(nullState()).toEqual([false, false, true]);
    await userEvent.click(screen.getByRole("button", { name: "다음" }));
  }
  expect(onPage).not.toHaveBeenCalled();
});

test("pageCount 가 숫자면 hasMore 를 보지 않는다 -- 쪽 수가 경계(지표 정렬 창 밖의 대상은 쪽으로 닿지 않는다)", async () => {
  const { container, unmount } = render(<Pager page={1} pageCount={1} hasMore onPage={() => {}} label="쪽" />);
  expect(container).toBeEmptyDOMElement();
  unmount();
  const onPage = vi.fn();
  render(<Pager page={5} pageCount={5} hasMore onPage={onPage} label="쪽" />);
  expect(names.map((n) => screen.getByRole("button", { name: n }).hasAttribute("disabled")))
    .toEqual([false, false, true, true]);
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  expect(onPage).not.toHaveBeenCalled();
  expect(screen.getByRole("navigation", { name: "쪽" })).toHaveTextContent("5 / 5쪽");
});
