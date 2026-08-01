import { lifecycleLabel, lifecycleTone } from "./statusMeta";

// Status chip for a DMS lifecycle state: Korean label + semantic tone, from the
// single statusMeta source. Reuses the existing .reqa-badge.is-* styles so the
// pill looks identical to before — only the label text becomes Korean.
export default function StatusPill({
  state,
  className,
}: {
  state?: string | null;
  className?: string;
}) {
  const s = state || "";
  return (
    <span className={`reqa-badge is-${lifecycleTone(s)}${className ? ` ${className}` : ""}`}>
      {lifecycleLabel(s) || "—"}
    </span>
  );
}
