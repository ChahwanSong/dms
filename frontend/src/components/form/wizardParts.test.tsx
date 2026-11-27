import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { expect, test, vi } from "vitest";
import { MoreText, OperationCards, OptionToggle, ReviewCard, SubSection } from "./wizardParts";
import {
  HELP_SUMMARY, OPERATION_LABEL, OPERATION_LABEL_BATCH, SYNC_DSYNC_SPECIAL_FILE_NOTE, SYNC_OPTION_HELP,
} from "../../features/jobs/optionRules";

// 제출 위저드 단계 본문 부품(2026-11-26 단계별 등록)의 계약. 접힌 전문도 DOM 에 남으므로 「보인다」는 toBeVisible 로 단언한다.

test("MoreText — 접힘: 요약은 보이고 전문은 숨은 <p>, 「자세히」(type=button)로 펼치고 접는다", async () => {
  render(<MoreText summary="요약 한 줄." full="전문 문단입니다." />);
  expect(screen.getByText("요약 한 줄.")).toBeVisible();
  const full = screen.getByText("전문 문단입니다.");
  expect(full.tagName).toBe("P");
  expect(full).not.toBeVisible();
  const btn = screen.getByRole("button", { name: "자세히" });
  expect(btn).toHaveAttribute("type", "button");
  expect(btn).toHaveAttribute("aria-expanded", "false");
  expect(btn).toHaveAttribute("aria-controls", full.id);
  await userEvent.click(btn);
  expect(full).toBeVisible();
  expect(btn).toHaveAttribute("aria-expanded", "true");
  expect(btn).toHaveTextContent("접기");
  await userEvent.click(btn);
  expect(full).not.toBeVisible();
});

test("MoreText — forceOpen 이면 전문을 펼친 채 고정하고 버튼은 없다(요약은 그대로 보인다)", () => {
  render(<MoreText summary="요약 한 줄." full="전문 문단입니다." forceOpen />);
  expect(screen.getByText("전문 문단입니다.")).toBeVisible();
  expect(screen.getByText("요약 한 줄.")).toBeVisible();
  expect(screen.queryByRole("button")).toBeNull();
});

test("MoreText — 전문이 여러 문단이면 문단마다 <p>, 전문이 없으면 버튼도 없다", async () => {
  const { unmount } = render(<MoreText summary="요약." full={["첫 문단.", "둘째 문단."]} />);
  const paras = [screen.getByText("첫 문단."), screen.getByText("둘째 문단.")];
  for (const p of paras) { expect(p.tagName).toBe("P"); expect(p).not.toBeVisible(); }
  await userEvent.click(screen.getByRole("button", { name: "자세히" }));
  for (const p of paras) expect(p).toBeVisible();
  unmount();
  render(<MoreText summary="요약만." />);
  expect(screen.getByText("요약만.")).toBeVisible();
  expect(screen.queryByRole("button")).toBeNull();
});

function ToggleHarness({ error }: { error?: string }) {
  const [on, setOn] = useState(false);
  return <OptionToggle label="delete" checked={on} onChange={(e) => setOn(e.target.checked)}
                       summary={HELP_SUMMARY.delete} full={SYNC_OPTION_HELP.delete} error={error} />;
}

test("OptionToggle — 체크박스 이름은 그대로, 켜면 전문이 펼쳐지고 끄면 다시 접힌다", async () => {
  render(<ToggleHarness />);
  const box = screen.getByRole("checkbox", { name: "delete" });
  expect(box).not.toBeChecked();
  expect(screen.getByText(HELP_SUMMARY.delete)).toBeVisible();
  expect(screen.getByText(SYNC_OPTION_HELP.delete)).not.toBeVisible();
  await userEvent.click(box);
  expect(box).toBeChecked();
  expect(screen.getByText(SYNC_OPTION_HELP.delete)).toBeVisible();
  expect(screen.queryByRole("button", { name: "자세히" })).toBeNull();
  await userEvent.click(box);
  expect(screen.getByText(SYNC_OPTION_HELP.delete)).not.toBeVisible();
  expect(screen.getByRole("button", { name: "자세히" })).toHaveAttribute("type", "button");
});

test("OptionToggle — 오류가 있으면 오류 줄 + 체크박스 aria-invalid(막힌 「다음」이 그리로 포커스한다)", () => {
  render(<ToggleHarness error="재귀 옵션이 필요합니다" />);
  expect(screen.getByText("재귀 옵션이 필요합니다")).toBeVisible();
  expect(screen.getByRole("checkbox", { name: "delete" })).toHaveAttribute("aria-invalid", "true");
});

test("OperationCards — radiogroup 「연산」, 라디오 이름 = 연산 이름, 설명은 aria-describedby, 카드를 누르면 고른다", async () => {
  const onChange = vi.fn();
  render(<OperationCards name="op" value="sync" onChange={onChange} options={[
    { value: "sync", subtitle: OPERATION_LABEL.sync, description: "소스를 목적지로 복사합니다." },
    { value: "rm", subtitle: OPERATION_LABEL.rm, description: "대상을 삭제합니다.", danger: true },
  ]} />);
  const group = screen.getByRole("radiogroup", { name: "연산" });
  const sync = within(group).getByRole("radio", { name: "sync" });
  const rm = within(group).getByRole("radio", { name: "rm" });
  expect(sync).toBeChecked();
  expect(rm).not.toBeChecked();
  expect(sync).toHaveAccessibleDescription("소스를 목적지로 복사합니다.");
  expect(within(group).getByText("복사·동기화")).toBeVisible();
  // rm 카드는 「되돌릴 수 없음」 칩
  expect(within(rm.closest("label")!).getByText("되돌릴 수 없음")).toBeVisible();
  await userEvent.click(within(group).getByText("대상을 삭제합니다."));
  expect(onChange).toHaveBeenCalledWith("rm");
});

test("ReviewCard — h3 제목, 「수정: 제목」(type=button) 이름은 내용에서, 카드에 aria-label 없음", async () => {
  const onEdit = vi.fn();
  const { container } = render(<ReviewCard title="소스 → 목적지" onEdit={onEdit}><p>본문</p></ReviewCard>);
  expect(screen.getByRole("heading", { level: 3, name: "소스 → 목적지" })).toBeInTheDocument();
  const edit = screen.getByRole("button", { name: "수정: 소스 → 목적지" });
  expect(edit).toHaveAttribute("type", "button");
  expect(edit).not.toHaveAttribute("aria-label");
  await userEvent.click(edit);
  expect(onEdit).toHaveBeenCalledTimes(1);
  expect(container.querySelector("[aria-label]")).toBeNull();
});

test("SubSection — h3 소제목 + caption(문단을 담을 수 있는 div)", () => {
  render(<SubSection title="실행 옵션" caption={<MoreText summary="요약." full="전문." />}><input aria-label="칸" /></SubSection>);
  expect(screen.getByRole("heading", { level: 3, name: "실행 옵션" })).toBeInTheDocument();
  expect(screen.getByText("요약.")).toBeVisible();
  expect(screen.getByLabelText("칸")).toBeInTheDocument();
});

test("요약 상수 — 첫 문장 요약은 전문 앞부분 그대로, dsync 한 줄은 미리보기 꼬리까지, 연산 부제는 세 연산", () => {
  // 요약과 전문이 따로 낡지 않게(optionRules SUMMARY_* 주석)
  expect(SYNC_OPTION_HELP.delete.startsWith(HELP_SUMMARY.delete)).toBe(true);
  expect(SYNC_OPTION_HELP.contents.startsWith(HELP_SUMMARY.contents)).toBe(true);
  expect(SYNC_DSYNC_SPECIAL_FILE_NOTE.endsWith("— dsync 미리보기에는 나오지 않습니다.")).toBe(true);
  // runAsSingle·runAsRoot 는 dms-ssc 몫(단일 작업은 실행 신원이 uid/gid 를 정하고 root 는 관리자 선택 -- optionRules 주석)
  expect(Object.keys(HELP_SUMMARY).sort()).toEqual(
    ["contents", "controlsPolicy", "csvApply", "delete", "direct", "maxErrors", "policy", "quietRm", "quietSync", "runAs",
     "runAsRoot", "runAsSingle"]);
  for (const labels of [OPERATION_LABEL, OPERATION_LABEL_BATCH]) expect(Object.keys(labels).sort()).toEqual(["rm", "scan", "sync"]);
});
