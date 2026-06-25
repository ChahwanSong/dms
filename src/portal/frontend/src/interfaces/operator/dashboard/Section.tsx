import { type ReactNode, useState } from "react";

// Collapsible dashboard section. Default collapsed (기본 접어두기): the title row
// — with an optional count badge — stays visible so key info shows at a glance,
// and the body (filters + table/list) folds away. Click the header to toggle.
export default function Section({
  title,
  badge,
  defaultOpen = false,
  children,
}: {
  title: string;
  badge?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="dash-section">
      <button
        type="button"
        className="dash-section-head"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        <span className="dash-caret">{open ? "▾" : "▸"}</span>
        <h3>{title}</h3>
        {badge}
      </button>
      {open && <div className="dash-section-body">{children}</div>}
    </div>
  );
}
