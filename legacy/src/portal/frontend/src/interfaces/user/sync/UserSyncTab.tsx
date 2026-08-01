import { useState } from "react";
import UserSyncForm from "./UserSyncForm";
import UserSyncList from "./UserSyncList";

// 사용자 데이터 Sync 메뉴. 상단 = 요청 작성(단일 복사), 하단 = 내 작업 목록.
// 파일시스템↔파일시스템 또는 PVC↔PVC 만 가능하며, 옵션은 정책값으로 고정된다.
export default function UserSyncTab() {
  const [reloadKey, setReloadKey] = useState(0);
  return (
    <div className="inventory">
      <div className="inv-head">
        <h2>데이터 Sync</h2>
      </div>
      <section className="ui-card">
        <div className="ui-card-hd">
          <h3>
            새 Sync 요청{" "}
            <span className="muted small">
              단일 복사 (data.sync) · 파일시스템↔파일시스템 또는 PVC↔PVC 만 가능
            </span>
          </h3>
        </div>
        <div className="ui-card-bd">
          <div className="ui-card-div" />
          <UserSyncForm onCreated={() => setReloadKey((k) => k + 1)} />
        </div>
      </section>
      <UserSyncList reloadKey={reloadKey} />
    </div>
  );
}
