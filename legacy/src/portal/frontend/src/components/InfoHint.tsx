import { useEffect, useRef, useState, type ReactNode } from "react";

// Small "i" affordance that reveals a help popover on hover, keyboard focus, or
// tap (mobile). Closes on click-outside / Escape. Use for inline explanations
// next to a heading or label.
export default function InfoHint({
  children,
  label = "설명 보기",
  align = "start",
}: {
  children: ReactNode;
  label?: string;
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <span
      className="info-hint"
      ref={ref}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        className="info-hint-btn"
        aria-label={label}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
      >
        i
      </button>
      {open && (
        <span className={`info-hint-pop ${align === "end" ? "end" : "start"}`} role="tooltip">
          {children}
        </span>
      )}
    </span>
  );
}
