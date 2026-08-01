// Shared loading indicator for the whole portal: a spinner + "불러오는 중…" line,
// optionally followed by shimmer skeleton cards. Use `rows` for list/table/detail
// areas so a slow fetch never looks like an empty result; omit it for tiny inline
// spots. `center` vertically centers it for full-page boots.
export default function Loading({
  label = "불러오는 중…",
  rows = 0,
  center = false,
}: {
  label?: string;
  rows?: number;
  center?: boolean;
}) {
  return (
    <div className={`loading${center ? " loading-center" : ""}`} role="status" aria-live="polite">
      <div className="loading-head"><span className="spinner" aria-hidden="true" />{label}</div>
      {rows > 0 && (
        <div className="skel-list" aria-hidden="true">
          {Array.from({ length: rows }).map((_, i) => (
            <div key={i} className="skel-card">
              <span className="skel skel-pill" />
              <div className="skel-card-main">
                <span className="skel" style={{ width: `${40 + (i % 3) * 12}%` }} />
                <span className="skel" style={{ width: `${62 + (i % 2) * 16}%` }} />
              </div>
              <span className="skel skel-end" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
