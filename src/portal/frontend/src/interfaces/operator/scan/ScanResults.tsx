import type { ScanRequest } from "../../../api";
import { fmtBytes } from "./helpers";

// Aggregate scan totals (batch.result_totals): the summed counts across succeeded
// requests. All fields optional/nullable until at least one request has succeeded.
type Totals = {
  file_count?: number | null;
  directory_count?: number | null;
  total_bytes?: number | null;
  error_count?: number | null;
};

const nf = (n?: number | null) => (n == null ? "—" : n.toLocaleString());

// Visualize a scan batch's results in a way that fits the data (a directory-tree
// census): a stat band with the aggregate counts PLUS derived metrics that
// characterize the data shape (avg file size, files-per-directory), and a
// per-path comparison chart (file count + size, normalized to the batch max) so
// the operator sees at a glance which path dominates.
export default function ScanResults({
  totals,
  jobs,
}: {
  totals: Totals;
  jobs: ScanRequest[];
}) {
  const files = totals.file_count ?? 0;
  const dirs = totals.directory_count ?? 0;
  const bytes = totals.total_bytes ?? 0;
  const avg = files > 0 ? bytes / files : null; // 평균 파일 크기 (작은 파일 多 vs 큰 파일 少)
  const fpd = dirs > 0 ? files / dirs : null; // 디렉터리당 파일 (트리 밀도)

  const stats = [
    { l: "파일", v: nf(totals.file_count) },
    { l: "디렉터리", v: nf(totals.directory_count) },
    { l: "총 용량", v: fmtBytes(totals.total_bytes) },
    { l: "평균 파일 크기", v: avg == null ? "—" : fmtBytes(Math.round(avg)) },
    {
      l: "디렉터리당 파일",
      v: fpd == null ? "—" : fpd.toLocaleString(undefined, { maximumFractionDigits: 1 }),
    },
    { l: "오류", v: nf(totals.error_count), danger: Boolean(totals.error_count) },
  ];

  // per-path comparison — only succeeded requests carry a result.
  const rows = jobs
    .filter((j) => j.state === "succeeded" && j.result)
    .sort((a, b) => (b.result!.total_bytes ?? 0) - (a.result!.total_bytes ?? 0));
  const maxBytes = Math.max(1, ...rows.map((r) => r.result!.total_bytes ?? 0));
  const maxFiles = Math.max(1, ...rows.map((r) => r.result!.file_count ?? 0));
  const CAP = 12;
  const shown = rows.slice(0, CAP);

  return (
    <div className="scan-results">
      <div className="scan-stats">
        {stats.map((s) => (
          <div className="scan-stat" key={s.l}>
            <span className={`scan-stat-n${s.danger ? " err-num" : ""}`}>{s.v}</span>
            <span className="scan-stat-l">{s.l}</span>
          </div>
        ))}
      </div>

      {rows.length >= 2 && (
        <div className="scan-bars">
          <div className="scan-bars-head">
            <span>경로별 분포</span>
            <span className="scan-bars-legend">
              <i className="sbdot files" aria-hidden="true" /> 파일 수
              <i className="sbdot bytes" aria-hidden="true" /> 용량
            </span>
          </div>
          {shown.map((j) => {
            const r = j.result!;
            const fp = ((r.file_count ?? 0) / maxFiles) * 100;
            const bp = ((r.total_bytes ?? 0) / maxBytes) * 100;
            return (
              <div className="scan-bar-row" key={j.id}>
                <code className="scan-bar-path" title={j.path}>
                  {j.path}
                </code>
                <div className="scan-bar-metrics">
                  <div className="scan-bar">
                    <span className="sbtrack">
                      <span className="sbfill files" style={{ width: `${fp}%` }} />
                    </span>
                    <span className="scan-bar-v">{nf(r.file_count)} 파일</span>
                  </div>
                  <div className="scan-bar">
                    <span className="sbtrack">
                      <span className="sbfill bytes" style={{ width: `${bp}%` }} />
                    </span>
                    <span className="scan-bar-v">{fmtBytes(r.total_bytes)}</span>
                  </div>
                </div>
              </div>
            );
          })}
          {rows.length > CAP && (
            <div className="scan-bars-more muted small">
              +{rows.length - CAP}개 경로 더 — 아래 표에서 확인
            </div>
          )}
        </div>
      )}
    </div>
  );
}
