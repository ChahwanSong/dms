import { useState } from "react";
import { type FocusTarget } from "../../../api";
import RunsTable from "./RunsTable";
import RequestsTable from "./RequestsTable";

// "액티비티" — sub-page under 종합 대시보드. Hosts the scheduler-activity (runs) and
// requests panels that used to sit on the main dashboard. Refresh re-mounts the
// panels (each fetches on mount) via a key bump.
export default function DashboardActivity({ focus }: {
  focus?: FocusTarget | null;
} = {}) {
  const [reloadKey, setReloadKey] = useState(0);
  const focusRequestId = focus?.kind === "request" ? focus.value : undefined;
  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>액티비티</h2>
        <div className="inv-actions">
          <button className="ghost" onClick={() => setReloadKey((k) => k + 1)}>
            새로고침
          </button>
        </div>
      </div>
      <RunsTable key={`runs-${reloadKey}`} defaultOpen />
      <RequestsTable key={`reqs-${reloadKey}`} defaultOpen focusRequestId={focusRequestId} />
    </div>
  );
}
