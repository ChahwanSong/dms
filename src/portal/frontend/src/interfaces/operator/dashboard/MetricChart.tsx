import { useEffect, useRef, useState } from "react";
import { type NodeMetricPoint } from "../../../api";

const H = 96, PL = 30, PR = 8, PT = 10, PB = 16;

// x-axis labels for a window (shared with the request/job chart's cadence).
export function xLabels(sec: number): string[] {
  if (sec <= 3600) return ["-60분", "-30분", "now"];
  if (sec <= 21600) return ["-6h", "-3h", "now"];
  if (sec <= 86400) return ["-24h", "-12h", "now"];
  if (sec <= 604800) return ["-7일", "-3일", "now"];
  return ["-30일", "-15일", "now"];
}

// forward-fill nulls so the line stays contiguous across missed samples.
function fill(series: NodeMetricPoint[]): number[] {
  let last = 0;
  return series.map((p) => {
    if (typeof p.v === "number") { last = p.v; return p.v; }
    return last;
  });
}

// A compact single-series area+line chart (dynamic y, faint grid, emphasized endpoint).
// max100 pins the axis to 0–100 for percentages; otherwise it auto-scales (load).
export default function MetricChart({ series, label, unit, color, sec, max100, digits = 0 }: {
  series: NodeMetricPoint[];
  label: string;
  unit: string;
  color: string;
  sec: number;
  max100?: boolean;
  digits?: number;
}) {
  const [w, setW] = useState(280);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((e) => { const cw = e[0].contentRect.width; if (cw > 0) setW(Math.round(cw)); });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const hasData = series.some((p) => typeof p.v === "number");
  const v = fill(series);
  const cur = v.length ? v[v.length - 1] : null;
  const mx = max100 ? 100 : Math.max(1, ...v) * 1.2;
  const xAt = (i: number) => (v.length <= 1 ? PL : PL + (i * (w - PL - PR)) / (v.length - 1));
  const yAt = (val: number) => PT + (1 - val / mx) * (H - PT - PB);
  const line = v.map((x, i) => `${i ? "L" : "M"}${xAt(i).toFixed(1)} ${yAt(x).toFixed(1)}`).join(" ");
  const area = v.length ? `${line} L${xAt(v.length - 1).toFixed(1)} ${H - PB} L${PL} ${H - PB} Z` : "";
  const xl = xLabels(sec), step = (w - PL - PR) / (xl.length - 1);

  return (
    <div className="nmc">
      <div className="nmc-hd">
        <span className="nmc-label">{label}</span>
        <span className="nmc-cur" style={{ color }}>{hasData && cur != null ? `${cur.toFixed(digits)}${unit}` : "—"}</span>
      </div>
      <div ref={ref} className="nmc-wrap">
        {hasData ? (
          <svg className="nmc-svg" viewBox={`0 0 ${w} ${H}`} role="img" aria-label={`${label} 추이`}>
            {[0, 1, 2].map((i) => {
              const y = PT + (i * (H - PT - PB)) / 2;
              return (
                <g key={i}>
                  <line className="ts-gridline" x1={PL} y1={y} x2={w - PR} y2={y} />
                  <text className="ts-axis" x={PL - 5} y={y + 3} textAnchor="end">{Math.round(mx * (1 - i / 2))}</text>
                </g>
              );
            })}
            {xl.map((t, j) => (
              <text key={j} className="ts-axis" x={PL + j * step} y={H - 5} textAnchor="middle">{t}</text>
            ))}
            <path d={area} fill={color} opacity="0.13" />
            <path d={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" />
            {cur != null && <circle cx={xAt(v.length - 1)} cy={yAt(cur)} r="3" fill={color} stroke="var(--bg)" strokeWidth="1.5" />}
          </svg>
        ) : (
          <div className="nmc-empty">수집 중…</div>
        )}
      </div>
    </div>
  );
}
