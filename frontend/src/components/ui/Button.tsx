import { forwardRef } from "react";
type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" };
export const Button = forwardRef<HTMLButtonElement, Props>(function Button(
  { variant = "primary", className = "", ...p }, ref) {
  const base = "inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50";
  const v = variant === "primary" ? "bg-accent text-white" : "bg-surface text-ink border border-black/10";
  return <button ref={ref} className={`${base} ${v} ${className}`} {...p} />;
});
