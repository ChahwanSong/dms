import { type ReactNode, useState } from "react";

// Collapsible dashboard section. Default collapsed (기본 접어두기): the title row
// — with an optional count badge — stays visible so key info shows at a glance,
// and the body (filters + table/list) folds away. Click the header to toggle.
export default function Section({
  title,
  badge,
  defaultOpen = false,
  onOpenChange,
  children,
}: {
  title: string;
  badge?: ReactNode;
  defaultOpen?: boolean;
  // fired with the new open state on toggle — lets a section lazy-load its body
  // (e.g. fetch the first page of a list) only when the operator actually expands it.
  onOpenChange?: (open: boolean) => void;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  // WAI-ARIA disclosure pattern: the heading wraps the toggle button, and the
  // badge is a SIBLING of the button (not inside it). This keeps interactive
  // badges — e.g. an <InfoHint> "i", itself a <button> — out of the toggle
  // button, which would otherwise be invalid <button>-in-<button> DOM and make
  // the "i" click also toggle the section.
  return (
    <div className="dash-section">
      <div className="dash-section-head">
        <h3 className="dash-section-title">
          <button
            type="button"
            className="dash-section-toggle"
            aria-expanded={open}
            onClick={() => {
              const next = !open;
              setOpen(next);
              onOpenChange?.(next);
            }}
          >
            <span className="dash-caret">{open ? "▾" : "▸"}</span>
            {title}
          </button>
        </h3>
        {badge}
      </div>
      {open && <div className="dash-section-body">{children}</div>}
    </div>
  );
}
