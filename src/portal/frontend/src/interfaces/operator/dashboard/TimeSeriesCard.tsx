import { useCallback, useEffect, useRef, useState } from "react";
import { operatorApi, type NodeMetricPoint, type TimeSeriesResp } from "../../../api";
import { fmtAgo } from "../../../lib/format";

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

// Compact tooltip timestamp: within a day show HH:mm, wider windows prepend M/D.
function tipTime(iso: string | undefined, sec: number): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const hm = d.toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit" });
  if (sec <= 86400) return hm;
  return `${d.toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" })} ${hm}`;
}

// The viewBox width tracks the measured render width (w), so the SVG maps 1:1 to CSS
// pixels — no horizontal stretch, so axis text stays crisp and endpoint dots stay round.
function Chart({
  reqV, jobV, times, sec, w,
}: { reqV: number[]; jobV: number[]; times: (string | undefined)[]; sec: number; w: number }) {
  const [hi, setHi] = useState<number | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const n = Math.max(reqV.length, jobV.length);
  const mx = Math.max(1, ...reqV, ...jobV) * 1.15;
  const xAt = (i: number, len: number) => (len <= 1 ? PL : PL + (i * (w - PL - PR)) / (len - 1));
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

  // Crosshair snaps to the nearest sample index under the pointer (viewBox units,
  // scaled from the measured render width so it stays exact if the two differ).
  const idxFromClientX = (clientX: number): number => {
    const svg = svgRef.current;
    if (!svg || n <= 1) return 0;
    const r = svg.getBoundingClientRect();
    const vx = (clientX - r.left) * (w / (r.width || w));
    const i = Math.round(((vx - PL) / (w - PL - PR)) * (n - 1));
    return Math.max(0, Math.min(n - 1, i));
  };
  const onMove = (e: React.PointerEvent) => setHi(idxFromClientX(e.clientX));
  const onLeave = () => setHi(null);
  // Keyboard: same readout on focus as hover — arrows walk samples, Home/End jump,
  // Esc clears. Focusing an un-hovered chart lands on the latest sample.
  const onKey = (e: React.KeyboardEvent) => {
    if (n <= 1) return;
    if (e.key === "Escape") { setHi(null); return; }
    let cur = hi ?? n - 1;
    if (e.key === "ArrowLeft") cur = Math.max(0, cur - 1);
    else if (e.key === "ArrowRight") cur = Math.min(n - 1, cur + 1);
    else if (e.key === "Home") cur = 0;
    else if (e.key === "End") cur = n - 1;
    else return;
    e.preventDefault();
    setHi(cur);
  };

  const active = hi != null && hi < n ? hi : null;
  const hx = active != null ? xAt(active, n) : 0;
  // Flip the tooltip so it never spills past the plot edges.
  const align = active == null ? "c" : active < n * 0.28 ? "l" : active > n * 0.72 ? "r" : "c";

  return (
    <div className="ts-plot">
      <svg
        ref={svgRef}
        className="ts-svg"
        viewBox={`0 0 ${w} ${H}`}
        role="img"
        tabIndex={0}
        onKeyDown={onKey}
        onFocus={() => { if (hi == null && n > 1) setHi(n - 1); }}
        onBlur={onLeave}
        aria-label="요청 및 진행중 작업 시계열 — 좌우 방향키로 시점 이동"
      >
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
        {active != null && (
          <g className="ts-cursor" aria-hidden>
            <line className="ts-crosshair" x1={hx} y1={PT} x2={hx} y2={H - PB} />
            {jobV.length > active && <circle className="ts-dot" cx={hx} cy={yAt(jobV[active])} r="4" fill="var(--teal)" />}
            {reqV.length > active && <circle className="ts-dot" cx={hx} cy={yAt(reqV[active])} r="4" fill="var(--accent)" />}
          </g>
        )}
        {/* transparent hit layer on top so the whole plot area drives the crosshair */}
        <rect
          className="ts-hit"
          x={PL}
          y={PT}
          width={Math.max(0, w - PL - PR)}
          height={H - PT - PB}
          fill="transparent"
          onPointerMove={onMove}
          onPointerDown={onMove}
          onPointerLeave={onLeave}
        />
      </svg>
      {active != null && (
        <div className={`ts-tip ts-tip-${align}`} style={{ left: `${hx}px` }} role="status" aria-live="polite">
          <div className="ts-tip-t">
            {tipTime(times[active], sec)}<span className="ts-tip-ago">{fmtAgo(times[active])}</span>
          </div>
          <div className="ts-tip-row">
            <span className="ts-tip-key" style={{ background: "var(--accent)" }} />
            <span className="ts-tip-lbl">요청(활성)</span>
            <span className="ts-tip-val">{reqV[active] ?? 0}</span>
          </div>
          <div className="ts-tip-row">
            <span className="ts-tip-key" style={{ background: "var(--teal)" }} />
            <span className="ts-tip-lbl">진행중 작업</span>
            <span className="ts-tip-val">{jobV[active] ?? 0}</span>
          </div>
        </div>
      )}
    </div>
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
  const times = data ? data.requests.map((p) => p.t) : [];
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
            <Chart reqV={reqV} jobV={jobV} times={times} sec={sec} w={w} />
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
