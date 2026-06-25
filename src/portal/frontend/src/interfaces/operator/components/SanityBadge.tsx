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

// Compact readiness dots. For filesystem mappings this is the RM / DM / INV triple.
// For k8s CSI namespace-quota mappings (agentless) RM/DM/INV agent evidence is not
// meaningful — instead a single QUOTA dot reflects the ResourceQuota mutation
// transport probe (readiness.kubernetes_mutation). Pass `quota` to render that axis;
// `quota.title` overrides the dot tooltip with the transport detail.
export function ReadinessDots({
  readiness,
  showRoles = true,
  quota,
}: {
  readiness?: {
    resource_management?: string;
    data_management?: string;
    inventory?: string;
    kubernetes_mutation?: string;
  };
  showRoles?: boolean;
  quota?: { title?: string };
}) {
  if (quota) {
    const value = readiness?.kubernetes_mutation || "Unknown";
    return (
      <span className="readiness-dots">
        <span
          className={`rdot ${STATUS_CLASS[value] || "san-unknown"}`}
          title={quota.title || `QUOTA: ${value}`}
        >
          QUOTA
        </span>
      </span>
    );
  }
  const roleAxes: [string, string | undefined][] = showRoles
    ? [
        ["RM", readiness?.resource_management],
        ["DM", readiness?.data_management],
      ]
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
