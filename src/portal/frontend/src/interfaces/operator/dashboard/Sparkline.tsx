import { type NodeMetricPoint } from "../../../api";

// Inline SVG area-sparkline for a 0–100% metric series (CPU/mem over time). Nulls
// break the line into gaps. Tints amber/red as the current value climbs. No deps.
export default function Sparkline({
  series,
  current,
  width = 96,
  height = 30,
}: {
  series: NodeMetricPoint[];
  current?: number | null;
  width?: number;
  height?: number;
}) {
  const clamp = (v: number) => Math.max(0, Math.min(100, v));
  const vals = (series || []).map((p) => (p.v == null ? null : clamp(p.v)));
  const n = vals.length;
  if (n === 0 || vals.every((v) => v == null)) return <span className="muted small">—</span>;

  const pad = 2;
  const x = (i: number) => (n <= 1 ? width / 2 : pad + (i * (width - 2 * pad)) / (n - 1));
  const y = (v: number) => pad + (1 - v / 100) * (height - 2 * pad);

  // split into contiguous (non-null) segments → gaps where the probe was missing
  const segments: [number, number][][] = [];
  let cur: [number, number][] = [];
  vals.forEach((v, i) => {
    if (v == null) {
      if (cur.length) segments.push(cur);
      cur = [];
    } else {
      cur.push([x(i), y(v)]);
    }
  });
  if (cur.length) segments.push(cur);

  const last = current ?? [...vals].reverse().find((v) => v != null) ?? null;
  const tone =
    last == null ? "ok" : last >= 90 ? "bad" : last >= 75 ? "warn" : "ok";
  const base = (height - pad).toFixed(1);

  return (
    <svg
      className={`spark spark-${tone}`}
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={last == null ? "데이터 없음" : `현재 ${last}%`}
    >
      {segments.map((pts, si) => {
        const line = pts
          .map((p, k) => `${k ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`)
          .join(" ");
        const area =
          pts.length > 1
            ? `M${pts[0][0].toFixed(1)},${base} ` +
              pts.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" ") +
              ` L${pts[pts.length - 1][0].toFixed(1)},${base} Z`
            : "";
        return (
          <g key={si}>
            {area && <path className="spark-area" d={area} />}
            <path className="spark-line" d={line} fill="none" />
            {pts.length === 1 && <circle className="spark-dot" cx={pts[0][0]} cy={pts[0][1]} r={1.6} />}
          </g>
        );
      })}
    </svg>
  );
}
