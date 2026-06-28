import { useState } from "react";
import AttentionPanel from "./AttentionPanel";

// "조치 필요" — sub-page under 종합 대시보드. Hosts the action-required panel.
// Refresh re-mounts the panel (it fetches once on mount), so a key bump forces a
// reload. onNavigate lets per-issue shortcuts jump to the relevant operator section.
export default function DashboardAttention({
  onNavigate,
}: {
  onNavigate?: (section: string) => void;
}) {
  const [reloadKey, setReloadKey] = useState(0);
  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>조치 필요</h2>
        <div className="inv-actions">
          <button className="ghost" onClick={() => setReloadKey((k) => k + 1)}>
            새로고침
          </button>
        </div>
      </div>
      <AttentionPanel key={reloadKey} onNavigate={onNavigate} />
    </div>
  );
}
