export function MetricTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-surface rounded-card shadow-soft p-4">
      <div className="text-muted text-xs">{label}</div>
      <div className="text-2xl font-semibold mt-1">{value}</div>
    </div>
  );
}
