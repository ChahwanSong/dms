import { forwardRef } from "react";
type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "outline" | "ghost";
};
// 3계층(슬라이스 31 T3): primary(솔리드 파랑)=주 동작, outline(파랑 아웃라인)=보조
// 동작, ghost(회색 아웃라인)=취소·중립. 기존 사용처 2종(primary/ghost)은 의미를
// 그대로 두고 팔레트만 새로 받는다 -- 호출부 무수정 호환이 계약이다.
export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "primary", className = "", ...p }, ref) {
  const base = "inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50";
  const v = variant === "primary" ? "bg-accent text-white hover:bg-accenthover"
    : variant === "outline" ? "bg-surface text-accent border border-accent"
    : "bg-surface text-ink border border-line";
  return <button ref={ref} className={`${base} ${v} ${className}`} {...p} />;
});
