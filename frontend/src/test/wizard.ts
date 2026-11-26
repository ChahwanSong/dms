import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect } from "vitest";

// 제출 위저드(components/form/Wizard) vitest 헬퍼 -- 단일 작업(SubmitJob)·배치 생성(BatchCreate) 시험이 같이 쓴다(2026-11-26 단계별
// 등록, design-wizard §6-1). 입력 헬퍼(화면별 fillSyncTarget·pickStorage …)는 이것들로 단계를 지나고, 끝 상태는 「그 단계에 머문다」 --
// 제출 바디 시험은 그 뒤 toReview() → 제출한다.

const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

// 진행 단계 카드 -- PV 시트(Radix Dialog.Title = h2)가 열려도 흔들리지 않게 표지로 고른다(「h2 는 하나뿐」에 기대지 않는다).
export const stepCard = () => document.querySelector("[data-wizard-card]") as HTMLElement;
export const stepHeading = () => within(stepCard()).getByRole("heading", { level: 2 });
export const nextButton = () => screen.getByRole("button", { name: "다음" });
export const prevButton = () => screen.getByRole("button", { name: "이전" });
// 하단 바 상태 줄(막혔으면 이유, 아니면 「다음: <단계>」).
export const reasonLine = () => document.querySelector("[data-wizard-reason]") as HTMLElement | null;

/** 「다음」. 막힌 「다음」(aria-disabled)을 누르면 단계가 그대로라 뒤 단언이 엉뚱하게 실패한다 -- 이유를 메시지에 싣고 먼저 멈춘다. */
export async function next(expectTitle?: string) {
  expect(nextButton(), reasonLine()?.textContent ?? "").not.toHaveAttribute("aria-disabled", "true");
  await userEvent.click(nextButton());
  if (expectTitle) await waitFor(() => expect(stepHeading()).toHaveTextContent(expectTitle));
}

/** 스테퍼에서 단계로 간다(이름 앞부분 일치 -- 접근 이름은 「이름 + 값 + 상태 꼬리」). */
export async function goStep(name: string | RegExp) {
  const nav = screen.getByRole("navigation", { name: "진행 단계" });
  await userEvent.click(within(nav).getByRole("button", {
    name: typeof name === "string" ? new RegExp(`^${escapeRe(name)}`) : name }));
}

/** 연산 카드 고르기(radiogroup 「연산」의 라디오 이름 = 연산 이름). */
export async function chooseOp(op: "sync" | "scan" | "rm") {
  const g = await screen.findByRole("radiogroup", { name: "연산" });
  await userEvent.click(await within(g).findByRole("radio", { name: op }));
}

/** 검토 단계까지 「다음」을 누른다. 상한 = 배치 5 -- 끝없이 돌지 않고, 닿지 못하면 바 이유를 메시지에 싣는다. */
export async function toReview(title: "검토·제출" | "이름·검토", maxSteps = 5) {
  for (let i = 0; i < maxSteps && stepHeading().textContent !== title; i++) await next();
  expect(stepHeading(), `검토에 닿지 못함 — ${reasonLine()?.textContent ?? ""}`).toHaveTextContent(title);
}

/** 배치 스토리지 카드 고르기 -- 라디오 접근 이름은 storageOptionText(이름 + 꼬리)라 「이름 앞부분」으로 찾는다. */
export async function pickStorageCard(label: string, name: string) {
  const g = await screen.findByRole("radiogroup", { name: label });
  await userEvent.click(await within(g).findByRole("radio", { name: new RegExp(`^${escapeRe(name)}( |$)`) }));
}

// 「스토리지 값」 단언 대체 -- 카드 묶음 div 는 값이 없다. 고른 라디오(없으면 null).
export const storageChecked = (label: string) => within(screen.getByRole("radiogroup", { name: label }))
  .queryAllByRole("radio").find((r) => (r as HTMLInputElement).checked) ?? null;

// type 불변식(Wizard.tsx 머리 주석 「모든 버튼 type="button"」) -- 길이 0 이어야 한다.
export const buttonsWithoutType = (root: ParentNode) => root.querySelectorAll('button:not([type="button"])');
