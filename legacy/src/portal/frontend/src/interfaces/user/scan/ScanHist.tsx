import type { AtimeBucket } from "../../../api";
import { fmtBytes } from "../sync/helpers";

// dscan atime 히스토그램(데이터 온도): 9개 고정 버킷, index 0(최근 접근/hot) …
// 8(오래 미접근/cold). 각 버킷은 해당 접근-경과 구간 파일들의 총 용량(bytes)이다.
// 운영자 ScanHist와 동일한 시각화이되 사용자 인터페이스에 독립적으로 둔다.
const HOT_COLD = [
  "#ef4444", // ≤1d   hot
  "#f97316", // ≤7d
  "#f59e0b", // ≤30d
  "#eab308", // ≤90d
  "#84cc16", // ≤180d
  "#22c55e", // ≤1y
  "#14b8a6", // ≤3y
  "#0ea5e9", // ≤10y
  "#6366f1", // 10y+  cold
];

const DAY_LABEL: Record<number, string> = {
  1: "≤1일",
  7: "≤7일",
  30: "≤30일",
  90: "≤90일",
  180: "≤180일",
  365: "≤1년",
  1095: "≤3년",
  3650: "≤10년",
};
function bucketLabel(b: AtimeBucket): string {
  if (b.max_age_days == null) return "10년+"; // open-ended oldest bucket
  return DAY_LABEL[b.max_age_days] ?? `≤${b.max_age_days}일`;
}

const totalBytes = (h: AtimeBucket[]) => h.reduce((s, b) => s + (b.bytes ?? 0), 0);

// Compact stacked hot→cold capacity bar (한 줄 요약). "—" when no histogram.
export function ScanHistBar({ hist }: { hist?: AtimeBucket[] | null }) {
  if (!hist || hist.length === 0) return <span className="muted small">—</span>;
  const sum = totalBytes(hist);
  if (!sum) return <span className="muted small">—</span>;
  return (
    <div className="ahist-bar" title="데이터 액티비티 (용량) — hot(최근 접근) → cold(오래 미접근)">
      {hist.map((b, i) =>
        b.bytes ? (
          <span
            key={i}
            className="ahist-seg"
            style={{ width: `${((b.bytes ?? 0) / sum) * 100}%`, background: HOT_COLD[i] ?? "#64748b" }}
            title={`${bucketLabel(b)}: ${fmtBytes(b.bytes)}`}
          />
        ) : null,
      )}
    </div>
  );
}

// Full labeled histogram (펼침 상세): 버킷별 한 줄 + 최대 버킷 대비 정규화 막대.
export function ScanHistFull({ hist }: { hist?: AtimeBucket[] | null }) {
  if (!hist || hist.length === 0 || !totalBytes(hist)) return null;
  const max = Math.max(1, ...hist.map((b) => b.bytes ?? 0));
  return (
    <div className="ahist-full">
      {hist.map((b, i) => (
        <div className="ahist-row" key={i}>
          <span className="ahist-label">{bucketLabel(b)}</span>
          <span className="ahist-track">
            <span
              className="ahist-fill"
              style={{ width: `${((b.bytes ?? 0) / max) * 100}%`, background: HOT_COLD[i] ?? "#64748b" }}
            />
          </span>
          <span className={`ahist-count${b.bytes ? "" : " muted"}`}>{fmtBytes(b.bytes)}</span>
        </div>
      ))}
    </div>
  );
}
