type Props = React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" };

export function Button({ variant = "primary", className = "", ...p }: Props) {
  const base = "inline-flex items-center justify-center rounded-lg px-3 py-2 text-sm font-medium disabled:opacity-50";
  const v = variant === "primary" ? "bg-accent text-white" : "bg-surface text-ink border border-black/10";
  return <button className={`${base} ${v} ${className}`} {...p} />;
}
