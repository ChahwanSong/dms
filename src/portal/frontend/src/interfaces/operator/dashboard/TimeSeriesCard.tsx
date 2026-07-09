import { useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type NodeMetricPoint, type TimeSeriesResp } from "../../../api";

// Request / in-progress-job trend. Data is BFF-sampled (DMS has no history endpoint),
// so history accrues from deploy time forward — the wide windows fill in over time.
const WINDOWS: { key: string; label: string; sec: number }[] = [
  { key: "1h", label: "1시간", sec: 3600 },
  { key: "6h", label: "6시간", sec: 21600 },
  { key: "24h", label: "24시간", sec: 86400 },
  { key: "7d", label: "7일", sec: 604800 },
  { key: "30d", label: "30일", sec: 2592000 },
];

const H = 230, PL = 34, PR = 12, PT = 14, PB = 24;

function xLabels(sec: number): string[] {
  if (sec <= 3600) return ["-60분", "-45분", "-30분", "-15분", "now"];
  if (sec <= 21600) return ["-6h", "-4h", "-2h", "now"];
  if (sec <= 86400) return ["-24h", "-18h", "-12h", "-6h", "now"];
  if (sec <= 604800) return ["-7일", "-5일", "-3일", "-1일", "now"];
  return ["-30일", "-22일", "-15일", "-8일", "now"];
}

// forward-fill nulls (a gauge carries its last value across a missed sample; leading
// gaps read as 0) so the line/area stay contiguous.
function fill(series: NodeMetricPoint[]): number[] {
  let last = 0;
  return series.map((p) => {
    if (typeof p.v === "number") { last = p.v; return p.v; }
    return last;
  });
}

// The viewBox width tracks the measured render width (w), so the SVG maps 1:1 to CSS
// pixels — no horizontal stretch, so axis text stays crisp and endpoint dots stay round.
function Chart({ reqV, jobV, sec, w }: { reqV: number[]; jobV: number[]; sec: number; w: number }) {
  const mx = Math.max(1, ...reqV, ...jobV) * 1.15;
  const xAt = (i: number, n: number) => (n <= 1 ? PL : PL + (i * (w - PL - PR)) / (n - 1));
  const yAt = (v: number) => PT + (1 - v / mx) * (H - PT - PB);
  const line = (v: number[]) => v.map((x, i) => `${i ? "L" : "M"}${xAt(i, v.length).toFixed(1)} ${yAt(x).toFixed(1)}`).join(" ");
  const area = (v: number[]) => (v.length ? `${line(v)} L${xAt(v.length - 1, v.length).toFixed(1)} ${H - PB} L${PL} ${H - PB} Z` : "");
  const xl = xLabels(sec), step = (w - PL - PR) / (xl.length - 1);
  const series = (v: number[], color: string, sw: number) =>
    v.length ? (
      <g>
        <path d={area(v)} fill={color} opacity="0.13" />
        <path d={line(v)} fill="none" stroke={color} strokeWidth={sw} strokeLinejoin="round" />
        <circle cx={xAt(v.length - 1, v.length)} cy={yAt(v[v.length - 1])} r="3.5" fill={color} stroke="var(--bg)" strokeWidth="2" />
      </g>
    ) : null;
  return (
    <svg className="ts-svg" viewBox={`0 0 ${w} ${H}`} role="img" aria-label="요청 및 진행중 작업 시계열">
      {[0, 1, 2, 3, 4].map((i) => {
        const y = PT + (i * (H - PT - PB)) / 4;
        return (
          <g key={`g${i}`}>
            <line className="ts-gridline" x1={PL} y1={y} x2={w - PR} y2={y} />
            <text className="ts-axis" x={PL - 6} y={y + 3} textAnchor="end">{Math.round(mx * (1 - i / 4))}</text>
          </g>
        );
      })}
      {xl.map((t, j) => (
        <text key={`x${j}`} className="ts-axis" x={PL + j * step} y={H - 6} textAnchor="middle">{t}</text>
      ))}
      {series(jobV, "var(--teal)", 2)}
      {series(reqV, "var(--accent)", 2.2)}
    </svg>
  );
}

export default function TimeSeriesCard() {
  const [sec, setSec] = useState(86400);
  const [data, setData] = useState<TimeSeriesResp | null>(null);
  const [err, setErr] = useState(false);
  const [w, setW] = useState(760);
  const timer = useRef<number | undefined>(undefined);
  const wrapRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async (s: number) => {
    try {
      setData(await operatorApi.dashboard.timeseries(s));
      setErr(false);
    } catch {
      setErr(true);
    }
  }, []);

  useEffect(() => {
    load(sec);
    timer.current = window.setInterval(() => load(sec), 30000);
    return () => window.clearInterval(timer.current);
  }, [sec, load]);

  // track the rendered chart width so the SVG viewBox matches CSS pixels 1:1.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0].contentRect.width;
      if (cw > 0) setW(Math.round(cw));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const reqV = data ? fill(data.requests) : [];
  const jobV = data ? fill(data.jobs) : [];
  const hasData = (data?.points ?? 0) > 0;
  const reqNow = reqV.length ? reqV[reqV.length - 1] : 0;
  const jobNow = jobV.length ? jobV[jobV.length - 1] : 0;

  return (
    <section className="ui-card">
      <div className="ui-card-hd ts-hd">
        <div className="ts-legend">
          <h3 style={{ marginRight: "0.4rem" }}>요청 · 진행중 작업 추이</h3>
          <span className="it"><span className="sw" style={{ background: "var(--accent)" }} />요청(활성) <span className="v">{reqNow}</span></span>
          <span className="it"><span className="sw" style={{ background: "var(--teal)" }} />진행중 작업 <span className="v">{jobNow}</span></span>
        </div>
        <div className="ts-pills">
          {WINDOWS.map((win) => (
            <button key={win.key} className={sec === win.sec ? "on" : ""} onClick={() => setSec(win.sec)}>
              {win.label}
            </button>
          ))}
        </div>
      </div>
      <div className="ui-card-bd">
        <div ref={wrapRef}>
          {hasData ? (
            <Chart reqV={reqV} jobV={jobV} sec={sec} w={w} />
          ) : (
            <div className="ts-empty">
              {err
                ? "추이 데이터를 불러오지 못했습니다."
                : "추이 데이터 수집 중입니다 — 배포 시점부터 축적됩니다. 잠시 후 다시 확인하세요."}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
