import { useEffect, useState } from "react";
import { operatorApi, type AttentionItem } from "../../../api";

export default function AttentionPanel() {
  const [rows, setRows] = useState<AttentionItem[]>([]);
  useEffect(() => {
    operatorApi.dashboard.attention().then(setRows).catch(() => setRows([]));
  }, []);
  return (
    <div className="dash-section">
      <h3>조치 필요 {rows.length > 0 && <span className="err-num">({rows.length})</span>}</h3>
      {rows.length === 0 ? <p className="muted">없음</p> : (
        <ul className="dash-attention">
          {rows.map((r, i) => (
            <li key={i}><span className="san san-failed">{r.issue_type}</span>
              <span className="mono small"> {JSON.stringify(
                Object.fromEntries(Object.entries(r).filter(([k]) => k !== "issue_type")),
              ).slice(0, 160)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
