import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test, vi } from "vitest";
import { Tabs, type TabItem } from "./Tabs";
import { Segmented } from "./Segmented";

const ITEMS: TabItem[] = [
  { id: "a", content: "Alpha" }, { id: "b", content: "Beta" }, { id: "c", content: "Gamma", separatorBefore: true },
];

function Harness({ initial = "a", onSelect }: { initial?: string | null; onSelect?: (id: string) => void }) {
  const [v, setV] = useState<string | null>(initial);
  return (
    <Tabs label="보기" items={ITEMS} value={v} idPrefix="t"
          onSelect={(id) => { onSelect?.(id); setV(id); }}>
      <p>{`패널 ${v ?? "없음"}`}</p>
    </Tabs>
  );
}

test("ARIA: tablist·tab·tabpanel 연결 -- 패널은 선택 탭으로 이름 붙고, 구분선은 보조기기에서 숨긴다", () => {
  render(<Harness />);
  const list = screen.getByRole("tablist", { name: "보기" });
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
  for (const t of tabs) expect(t).toHaveAttribute("aria-controls", "t-panel");
  expect(screen.getByRole("tabpanel", { name: "Alpha" })).toHaveTextContent("패널 a");
  const sep = list.querySelector("span[aria-hidden]")!;
  expect(sep.nextElementSibling).toBe(tabs[2]);
});

test("수동 활성: 화살표·Home/End 는 포커스만(양 끝 순환), Enter·Space·클릭이 고른다 -- tabIndex 는 roving", async () => {
  const onSelect = vi.fn();
  render(<Harness onSelect={onSelect} />);
  const tabs = screen.getAllByRole("tab");
  expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1, -1]);
  await userEvent.tab();
  expect(tabs[0]).toHaveFocus();
  await userEvent.keyboard("{ArrowLeft}");
  expect(tabs[2]).toHaveFocus();
  await userEvent.keyboard("{Home}");
  expect(tabs[0]).toHaveFocus();
  await userEvent.keyboard("{End}{ArrowRight}{ArrowRight}");
  expect(tabs[1]).toHaveFocus();
  expect(onSelect).not.toHaveBeenCalled();
  await userEvent.keyboard("{Enter}");
  expect(onSelect).toHaveBeenLastCalledWith("b");
  expect(tabs.map((t) => t.tabIndex)).toEqual([-1, 0, -1]);
  await userEvent.keyboard("{ArrowRight} ");
  expect(onSelect).toHaveBeenLastCalledWith("c");
  await userEvent.click(tabs[0]);
  expect(onSelect).toHaveBeenLastCalledWith("a");
  expect(screen.getByRole("tabpanel")).toHaveTextContent("패널 a");
});

test("값이 아직 목록에 없으면 첫 탭이 Tab 키로 닿고, 패널은 목록 이름으로 불린다", () => {
  render(<Harness initial={null} />);
  expect(screen.getAllByRole("tab").map((t) => t.tabIndex)).toEqual([0, -1, -1]);
  expect(screen.getAllByRole("tab").every((t) => t.getAttribute("aria-selected") === "false")).toBe(true);
  expect(screen.getByRole("tabpanel", { name: "보기" })).toBeInTheDocument();
});

test("Segmented: aria-pressed 그룹 -- 누르면 onChange, 선택은 하나", async () => {
  const onChange = vi.fn();
  render(<Segmented label="창" options={[{ value: 30, label: "30건" }, { value: 60, label: "60건" }]}
                    value={30} onChange={onChange} />);
  const group = screen.getByRole("group", { name: "창" });
  expect(group).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "30건" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "60건" })).toHaveAttribute("aria-pressed", "false");
  await userEvent.click(screen.getByRole("button", { name: "60건" }));
  expect(onChange).toHaveBeenCalledWith(60);
});
