import { forwardRef } from "react";
export type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "outline" | "ghost" | "danger";
};
// 3계층(슬라이스 31 T3): primary(솔리드 파랑)=주 동작, outline(파랑 아웃라인)=보조
// 동작, ghost(회색 아웃라인)=취소·중립. 기존 사용처 2종(primary/ghost)은 의미를
// 그대로 두고 팔레트만 새로 받는다 -- 호출부 무수정 호환이 계약이다.
// danger(빨강 아웃라인, 2026-10-12 사용자 요청 「취소버튼을 조금 눈에띄게」)=실행 중인 일을 멈추는 취소. 솔리드로 칠하지
// 않는다 -- 주 동작(primary)보다 앞서 보이면 「컨펌」 자리에서 오클릭을 부른다. 대화상자 닫기·편집 취소는 ghost 그대로다.
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "primary", className = "", ...p }, ref) {
  const base = "inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50";
  const v = variant === "primary" ? "bg-accent text-white hover:bg-accenthover"
    : variant === "outline" ? "bg-surface text-accent border border-accent"
    : variant === "danger" ? "bg-surface text-bad border border-bad hover:bg-badbg"
    : "bg-surface text-ink border border-line";
  return <button ref={ref} className={`${base} ${v} ${className}`} {...p} />;
});
