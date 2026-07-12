import type { AtimeBucket } from "../api";

// 접근 경과별 데이터 액티비티 시각화 (스캔 상세). 좌우 2컬럼(히스토그램 | 누적 CDF)을
// 같은 크기로 두고, 숫자 요약(전체 용량 + 최근 1달/6달/1년/3년 누적)은 아래 full-width
// 스트립으로 분리해 레이아웃 균형을 맞춘다. 운영자·사용자 스캔 상세가 공유(다크 전용).

const HOT = "#ef4444"; // 최근 접근 (히스토그램과 동일 팔레트 양 끝)
const COLD = "#6366f1"; // 오래 미접근

function fmtBytes(n?: number | null): string {
  if (n == null) return "—";
  if (n < 1024) return `${n} B`;
  const u = ["KB", "MB", "GB", "TB", "PB"];
  let v = n / 1024;
  let i = 0;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(v >= 100 || Number.isInteger(v) ? 0 : 1)} ${u[i]}`;
}

const DAY_LABEL: Record<number, string> = {
  1: "1일",
  7: "1주",
  30: "1달",
  90: "3달",
  180: "6달",
  365: "1년",
  1095: "3년",
  3650: "10년",
};
function shortLabel(b: AtimeBucket): string {
  if (b.max_age_days == null) return "10년+";
  return DAY_LABEL[b.max_age_days] ?? `${b.max_age_days}일`;
}

const totalBytes = (h: AtimeBucket[]) => h.reduce((s, b) => s + (b.bytes ?? 0), 0);

// 누적 CDF 차트 (SVG). y = 전체 대비 누적 %, x = 접근 경과(hot→cold), 모든 버킷 경계에 tick.
export function ActivityCumulativeChart({ hist }: { hist?: AtimeBucket[] | null }) {
  if (!hist || hist.length === 0) return null;
  const total = totalBytes(hist);
  if (!total) return null;

  const n = hist.length;
  let run = 0;
  const cum = hist.map((b) => {
    run += b.bytes ?? 0;
    return run;
  });

  const W = 340;
  const H = 150;
  const L = 26;
  const R = 10;
  const T = 10;
  const Bt = 26;
  const pw = W - L - R;
  const ph = H - T - Bt;
  const xAt = (slot: number) => L + (slot / n) * pw;
  const yAt = (pct: number) => T + (1 - pct) * ph;

  const pts = [{ x: xAt(0), y: yAt(0) }].concat(
    hist.map((_, i) => ({ x: xAt(i + 1), y: yAt(cum[i] / total) })),
  );
  const line = pts.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const area = `${line} L${xAt(n).toFixed(1)},${yAt(0).toFixed(1)} L${xAt(0).toFixed(1)},${yAt(0).toFixed(1)} Z`;
  const pctOf = (v: number) => Math.round((v / total) * 100);

  return (
    <svg
      className="act-chart"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="xMidYMid meet"
      role="img"
      aria-label="접근 경과별 누적 용량 분포"
    >
      <defs>
        {/* warm(hot/최근) → cool(cold/오래) wash; 단일 2-stop, 데이터 마크는 라인 */}
        <linearGradient id="act-hotcold" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stopColor={HOT} stopOpacity="0.24" />
          <stop offset="100%" stopColor={COLD} stopOpacity="0.32" />
        </linearGradient>
      </defs>

      {[0, 0.25, 0.5, 0.75, 1].map((g) => (
        <g key={g}>
          <line x1={L} x2={W - R} y1={yAt(g)} y2={yAt(g)} className="act-grid" />
          <text x={L - 4} y={yAt(g) + 3} textAnchor="end" className="act-axis">
            {g * 100}
          </text>
        </g>
      ))}

      <path d={area} fill="url(#act-hotcold)" stroke="none" />
      <path d={line} className="act-line" fill="none" />

      {hist.map((b, i) => (
        <circle key={i} cx={pts[i + 1].x} cy={pts[i + 1].y} r={2.4} className="act-dot">
          <title>{`≤${shortLabel(b)} 접근 누적: ${pctOf(cum[i])}% · ${fmtBytes(cum[i])}`}</title>
        </circle>
      ))}

      {hist.map((_, i) => (
        <g key={i}>
          <line
            x1={xAt(i + 1)}
            x2={xAt(i + 1)}
            y1={yAt(0)}
            y2={yAt(0) + 3}
            className="act-grid"
          />
          <text x={xAt(i + 1)} y={H - 7} textAnchor="middle" className="act-axis act-xlab">
            {shortLabel(hist[i])}
          </text>
        </g>
      ))}
    </svg>
  );
}

// 숫자 요약 스트립 (full-width): 전체 용량 + 최근 1달/6달/1년/3년 누적 %·용량.
export function ActivityStatsStrip({ hist }: { hist?: AtimeBucket[] | null }) {
  if (!hist || hist.length === 0) return null;
  const total = totalBytes(hist);
  if (!total) return null;

  const cumUpTo = (days: number) =>
    hist.reduce(
      (s, b) => s + (b.max_age_days != null && b.max_age_days <= days ? b.bytes ?? 0 : 0),
      0,
    );
  const pctOf = (v: number) => Math.round((v / total) * 100);
  const stats = [
    { label: "최근 1달", bytes: cumUpTo(30) },
    { label: "최근 6달", bytes: cumUpTo(180) },
    { label: "최근 1년", bytes: cumUpTo(365) },
    { label: "최근 3년", bytes: cumUpTo(1095) },
  ];

  return (
    <div className="act-strip">
      <div className="act-si act-si-total">
        <span className="act-si-l">전체 용량</span>
        <span className="act-si-v">
          <b>{fmtBytes(total)}</b>
        </span>
      </div>
      {stats.map((s) => {
        const pct = pctOf(s.bytes);
        return (
          <div className="act-si" key={s.label}>
            <span className="act-si-l">{s.label} 접근</span>
            <span className="act-si-v">
              <b>{pct}%</b>
              <span className="muted small"> · {fmtBytes(s.bytes)}</span>
            </span>
            <span className="act-si-bar" aria-hidden>
              <span className="act-si-fill" style={{ width: `${pct}%` }} />
            </span>
          </div>
        );
      })}
    </div>
  );
}
