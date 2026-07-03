import { useState } from "react";
import SyncForm from "./SyncForm";
import SyncList from "./SyncList";

// 데이터 Sync 탭. 상단 = 요청 작성(단발성 복사), 하단 = 요청된 작업 리스트(무한 스크롤).
// 백업과 달리 배치·재실행이 없다 — 잡 하나가 최상위 단위.
export default function SyncTab() {
  const [reloadKey, setReloadKey] = useState(0);
  const bump = () => setReloadKey((k) => k + 1);
  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>데이터 Sync</h2>
        <div className="inv-actions">
          <button className="ghost" onClick={bump}>새로고침</button>
        </div>
      </div>
      <div className="sync-form-section">
        <div className="wrun-h">
          Sync 요청 작성{" "}
          <span className="muted small">단발성 복사 (data.sync) · 배치·재실행 없음</span>
        </div>
        <SyncForm onCreated={bump} />
      </div>
      <SyncList reloadKey={reloadKey} />
    </div>
  );
}
