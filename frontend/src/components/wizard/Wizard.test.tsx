import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi } from "vitest";
import { Wizard, type WizardStep } from "./Wizard";

// 위저드 프레임(슬라이스 31 T4) 계약: 프레임은 도메인을 모르고 스텝 전이만
// 소유한다. SubmitJob 이 아닌 하네스로 검증해야 "배치 생성 등이 나중에 그대로
// 얹는다"는 성공 조건(프레임 단독 재사용성)이 테스트로 못박힌다.

const steps: WizardStep[] = [
  { id: "one", label: "하나" },
  { id: "two", label: "둘" },
  { id: "three", label: "셋" },
];

function Harness(props: {
  canNext?: boolean;
  submitDisabled?: boolean;
  onSubmit?: () => void;
  onCancel?: () => void;
}) {
  // current 는 프레임 밖 소유(제어 컴포넌트) — SubmitJob 과 같은 사용 형태.
  const [cur, setCur] = useState(0);
  return (
    <Wizard
      steps={steps}
      current={cur}
      onNavigate={setCur}
      canNext={props.canNext}
      onCancel={props.onCancel ?? (() => {})}
      submitLabel="제출"
      submitDisabled={props.submitDisabled}
      onSubmit={props.onSubmit ?? (() => {})}
    >
      <p>스텝 {cur + 1} 콘텐츠</p>
    </Wizard>
  );
}

test("다음/이전 버튼으로 스텝이 전이된다", async () => {
  render(<Harness />);
  expect(screen.getByText("스텝 1 콘텐츠")).toBeInTheDocument();
  // 첫 스텝엔 "이전"이 없다 — 뒤가 없는데 버튼만 있으면 죽은 UI 다.
  expect(screen.queryByRole("button", { name: "이전" })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  expect(screen.getByText("스텝 2 콘텐츠")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "이전" }));
  expect(screen.getByText("스텝 1 콘텐츠")).toBeInTheDocument();
});

test("canNext=false 면 다음 버튼이 비활성이다", () => {
  render(<Harness canNext={false} />);
  expect(screen.getByRole("button", { name: "다음" })).toBeDisabled();
});

test("제출 버튼은 마지막 스텝에서만 나타나고 그 전엔 다음 버튼이다", async () => {
  render(<Harness />);
  expect(screen.queryByRole("button", { name: "제출" })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  expect(screen.queryByRole("button", { name: "제출" })).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  // 마지막 스텝: 제출만 있고 "다음"은 사라진다.
  expect(screen.getByRole("button", { name: "제출" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "다음" })).not.toBeInTheDocument();
});

test("submitDisabled 가 제출 버튼에 전달되고, 활성일 때만 onSubmit 이 불린다", async () => {
  const onSubmit = vi.fn();
  const { unmount } = render(<Harness submitDisabled onSubmit={onSubmit} />);
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  expect(screen.getByRole("button", { name: "제출" })).toBeDisabled();
  unmount();

  render(<Harness onSubmit={onSubmit} />);
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.click(screen.getByRole("button", { name: "제출" }));
  expect(onSubmit).toHaveBeenCalledTimes(1);
});

test("취소 버튼이 모든 스텝에서 onCancel 을 호출한다", async () => {
  const onCancel = vi.fn();
  render(<Harness onCancel={onCancel} />);
  await userEvent.click(screen.getByRole("button", { name: "취소" }));
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.click(screen.getByRole("button", { name: "취소" }));
  expect(onCancel).toHaveBeenCalledTimes(2);
});

test("프레임 버튼은 전부 type=button 이다 — 초반 스텝 Enter 가 제출로 새지 않는다", () => {
  // form 소유는 호출자 쪽(플랜 T4): 프레임 버튼 하나라도 type=submit 이면
  // 호출자 form 안에서 Enter 가 조기 제출로 샌다.
  render(<Harness />);
  for (const b of screen.getAllByRole("button"))
    expect(b).toHaveAttribute("type", "button");
});

test("스테퍼 클릭은 뒤로만 이동한다 — 앞 스텝 점프는 canNext 게이트를 우회할 수 없다", async () => {
  render(<Harness />);
  // 앞으로 점프 시도(스텝 3 라벨 클릭) → 무시.
  await userEvent.click(screen.getByRole("button", { name: "셋" }));
  expect(screen.getByText("스텝 1 콘텐츠")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  await userEvent.click(screen.getByRole("button", { name: "다음" }));
  // 뒤로 점프(스텝 1 라벨 클릭) → 허용.
  await userEvent.click(screen.getByRole("button", { name: "하나" }));
  expect(screen.getByText("스텝 1 콘텐츠")).toBeInTheDocument();
});
