import { useEffect, useRef, useState } from "react";
import { type NodeMetricPoint } from "../../../api";

const H = 96, PL = 34, PR = 8, PT = 10, PB = 16;

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

// A compact area+line chart with a dynamic y axis. Optional 2nd series (series2) for
// e.g. network rx/tx on one chart; optional fmt formats current + y-axis (e.g. bytes/s).
export default function MetricChart({
  series, label, unit, color, sec, max100, digits = 0, hint,
  series2, color2, fmt,
}: {
  series: NodeMetricPoint[];
  label: string;
  unit: string;
  color: string;
  sec: number;
  max100?: boolean;
  digits?: number;
  hint?: string;
  series2?: NodeMetricPoint[];
  color2?: string;
  fmt?: (v: number) => string;
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

  const hasData = series.some((p) => typeof p.v === "number") || (series2?.some((p) => typeof p.v === "number") ?? false);
  const v = fill(series);
  const v2 = series2 ? fill(series2) : [];
  const cur = v.length ? v[v.length - 1] : null;
  const cur2 = v2.length ? v2[v2.length - 1] : null;
  const mx = max100 ? 100 : Math.max(1, ...v, ...v2) * 1.2;
  const fmtVal = (x: number) => (fmt ? fmt(x) : `${x.toFixed(digits)}${unit}`);
  const yTickText = (val: number) => (fmt ? fmt(val) : `${max100 || mx >= 10 ? Math.round(val) : val.toFixed(1)}${unit}`);
  const xAt = (i: number, n: number) => (n <= 1 ? PL : PL + (i * (w - PL - PR)) / (n - 1));
  const yAt = (val: number) => PT + (1 - val / mx) * (H - PT - PB);
  const linePath = (a: number[]) => a.map((x, i) => `${i ? "L" : "M"}${xAt(i, a.length).toFixed(1)} ${yAt(x).toFixed(1)}`).join(" ");
  const areaPath = (a: number[]) => (a.length ? `${linePath(a)} L${xAt(a.length - 1, a.length).toFixed(1)} ${H - PB} L${PL} ${H - PB} Z` : "");
  const xl = xLabels(sec), step = (w - PL - PR) / (xl.length - 1);

  const Line = ({ a, c }: { a: number[]; c: string }) => (a.length ? (
    <>
      <path d={areaPath(a)} fill={c} opacity="0.11" />
      <path d={linePath(a)} fill="none" stroke={c} strokeWidth="1.8" strokeLinejoin="round" />
      <circle cx={xAt(a.length - 1, a.length)} cy={yAt(a[a.length - 1])} r="3" fill={c} stroke="var(--bg)" strokeWidth="1.5" />
    </>
  ) : null);

  return (
    <div className="nmc">
      <div className="nmc-hd">
        <span className="nmc-label" title={hint}>{label}</span>
        <span className="nmc-cur">
          {series2 ? (
            hasData ? (
              <>
                <span style={{ color }}>↓{cur != null ? fmtVal(cur) : "—"}</span>{" "}
                <span style={{ color: color2 }}>↑{cur2 != null ? fmtVal(cur2) : "—"}</span>
              </>
            ) : "—"
          ) : (
            <span style={{ color }}>{hasData && cur != null ? fmtVal(cur) : "—"}</span>
          )}
        </span>
      </div>
      <div ref={ref} className="nmc-wrap">
        {hasData ? (
          <svg className="nmc-svg" viewBox={`0 0 ${w} ${H}`} role="img" aria-label={`${label} 추이`}>
            {[0, 1, 2].map((i) => {
              const y = PT + (i * (H - PT - PB)) / 2;
              return (
                <g key={i}>
                  <line className="ts-gridline" x1={PL} y1={y} x2={w - PR} y2={y} />
                  <text className="ts-axis" x={PL - 5} y={y + 3} textAnchor="end">{yTickText(mx * (1 - i / 2))}</text>
                </g>
              );
            })}
            {xl.map((t, j) => (
              <text key={j} className="ts-axis" x={PL + j * step} y={H - 5} textAnchor="middle">{t}</text>
            ))}
            <Line a={v} c={color} />
            {series2 && <Line a={v2} c={color2 || "var(--teal)"} />}
          </svg>
        ) : (
          <div className="nmc-empty">수집 중…</div>
        )}
      </div>
    </div>
  );
}
