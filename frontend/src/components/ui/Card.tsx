export function Card({ className = "", ...p }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={`bg-surface rounded-card shadow-soft p-5 ${className}`} {...p} />;
}
