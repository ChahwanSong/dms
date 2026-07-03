import { useState } from "react";
import RmForm from "./RmForm";
import RmList from "./RmList";

// 데이터 삭제 탭. 상단 = 요청 작성(단발성 data.rm), 하단 = 요청된 작업 리스트(무한 스크롤).
// Sync 탭과 동일 구조 — 잡 하나가 최상위 단위(배치·재실행 없음)이며 파괴적(원본 삭제)이라
// 실제 삭제 전 Preview(dry-run)로 삭제 대상을 확인한다.
export default function RmTab() {
  const [reloadKey, setReloadKey] = useState(0);
  const bump = () => setReloadKey((k) => k + 1);
  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>데이터 삭제</h2>
        <div className="inv-actions">
          <button className="ghost" onClick={bump}>새로고침</button>
        </div>
      </div>
      <div className="sync-form-section">
        <div className="wrun-h">
          삭제 요청 작성{" "}
          <span className="muted small">단발성 삭제 (data.rm) · 복구 불가 · 배치·재실행 없음</span>
        </div>
        <RmForm onCreated={bump} />
      </div>
      <RmList reloadKey={reloadKey} />
    </div>
  );
}
