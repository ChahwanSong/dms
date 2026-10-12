import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StatusPill } from "./StatusPill";
import { test, expect } from "vitest";

test("renders label and ok styling, no leading dot", () => {
  const { container } = render(<StatusPill state="Succeeded" />);
  expect(screen.getByText("Succeeded")).toBeInTheDocument();
  expect(container.querySelector(".text-ok")).not.toBeNull();
  // dot 금지: 자식은 텍스트 노드만
  expect(container.querySelectorAll("span[aria-hidden]").length).toBe(0);
});

// --- 단계·상태 설명 툴팁(2026-10-12) ---

test("help 가 없으면 종전 그대로: 포커스 불가 span, 툴팁 없음, label 이 다르면 title 이 상태 원문", () => {
  const { container } = render(<StatusPill state="PreviewReady" label="확인 대기" />);
  const pill = screen.getByText("확인 대기");
  expect(pill).toHaveAttribute("title", "PreviewReady");
  expect(pill).not.toHaveAttribute("tabindex");
  expect(container.querySelectorAll("span[aria-hidden]").length).toBe(0);
});

test("help 가 있으면 포커스·마우스로 설명 툴팁(첫 줄 상태 코드), Esc·이탈로 닫힌다, 설명은 aria-describedby 로 늘 읽힌다", async () => {
  render(<StatusPill state="ConfirmPending" help="컨펌 대기 — 설명" />);
  const pill = screen.getByText("ConfirmPending");
  expect(pill).toHaveAttribute("tabindex", "0");
  expect(pill).not.toHaveAttribute("title");
  expect(pill).toHaveAccessibleDescription("컨펌 대기 — 설명");
  expect(screen.queryByRole("tooltip")).toBeNull();                     // 열기 전엔 본문이 없다(정확 일치 단언 보호)
  pill.focus();
  expect(await screen.findByRole("tooltip")).toHaveTextContent("상태 코드 ConfirmPending");
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("tooltip")).toBeNull();
  pill.blur();
  await userEvent.hover(pill);
  expect(screen.getByRole("tooltip")).toHaveTextContent("컨펌 대기 — 설명");
  await userEvent.unhover(pill);
  expect(screen.queryByRole("tooltip")).toBeNull();
  // 정확 일치 상태 텍스트는 배지 하나뿐(툴팁 첫 줄은 「상태 코드 X」 한 노드 -- e2e E6 개수 보호)
  pill.focus();
  expect(screen.getAllByText("ConfirmPending")).toHaveLength(1);
});

test("클릭(탭) 한 번이면 열린 채로 남는다 -- 진입·포커스로 연 직후의 click 이 닫던 토글 회귀(검증 지적)", async () => {
  render(<><StatusPill state="Planned" help="잡이 만들어졌다" /><button>다른 곳</button></>);
  await userEvent.click(screen.getByText("Planned"));
  expect(screen.getByRole("tooltip")).toHaveTextContent("잡이 만들어졌다");
  await userEvent.click(screen.getByText("Planned"));                  // 다시 눌러도 닫히지 않는다(열기만)
  expect(screen.getByRole("tooltip")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "다른 곳" })); // 밖을 누르면(이탈·블러) 닫힌다
  expect(screen.queryByRole("tooltip")).toBeNull();
});

test("설명 노드는 hidden -- 렌더되지 않아(복사·낭독기 읽기 모드에 안 섞임) 설명으로만 읽힌다", () => {
  render(<StatusPill state="Planned" help="잡이 만들어졌다" />);
  const desc = screen.getByText("잡이 만들어졌다");
  expect(desc).toHaveAttribute("hidden");
  expect(desc).not.toBeVisible();
  expect(screen.getByText("Planned")).toHaveAccessibleDescription("잡이 만들어졌다");
});
