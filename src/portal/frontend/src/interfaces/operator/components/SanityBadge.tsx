// Maps a DMS sanity_status / readiness value to a colored pill.
// Status precedence (per DMS): any error -> Failed; no fresh reports -> Unknown;
// any warning -> Degraded; else Ready.

const STATUS_CLASS: Record<string, string> = {
  Ready: "san-ready",
  Degraded: "san-degraded",
  Unknown: "san-unknown",
  Failed: "san-failed",
  // readiness axis values
  Missing: "san-failed",
};

export function SanityBadge({ status }: { status?: string }) {
  const s = status || "Unknown";
  return <span className={`san ${STATUS_CLASS[s] || "san-unknown"}`}>{s}</span>;
}

// Compact readiness dots: the DM / INV pair for filesystem mappings. k8s CSI
// mappings are agentless (no DM agent evidence), so they render the INV dot only
// — pass showRoles={false} for those.
export function ReadinessDots({
  readiness,
  showRoles = true,
}: {
  readiness?: {
    data_management?: string;
    inventory?: string;
  };
  showRoles?: boolean;
}) {
  const roleAxes: [string, string | undefined][] = showRoles
    ? [["DM", readiness?.data_management]]
    : [];
  const axes: [string, string | undefined][] = [
    ...roleAxes,
    ["INV", readiness?.inventory],
  ];
  return (
    <span className="readiness-dots">
      {axes.map(([label, value]) => (
        <span
          key={label}
          className={`rdot ${STATUS_CLASS[value || "Unknown"] || "san-unknown"}`}
          title={`${label}: ${value || "Unknown"}`}
        >
          {label}
        </span>
      ))}
    </span>
  );
}
