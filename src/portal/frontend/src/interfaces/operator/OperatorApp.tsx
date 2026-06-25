import { useState } from "react";
import { type User } from "../../api";
import TopBar from "../../components/TopBar";
import StorageInventory from "./storage/StorageInventory";
import BackupBatches from "./backup/BackupBatches";

// Operator/admin interface. A simple left-nav shell; each section is a separate
// feature module.
type Section = "storage" | "backup";

const NAV: { key: Section; label: string; enabled: boolean }[] = [
  { key: "storage", label: "스토리지 인벤토리", enabled: true },
  { key: "backup", label: "데이터 백업", enabled: true },
];

export default function OperatorApp({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const [section, setSection] = useState<Section>("storage");

  return (
    <div className="app">
      <TopBar user={user} onLogout={onLogout} title="DMS Portal · 운영자 콘솔" />
      <div className="layout">
        <nav className="sidebar">
          {NAV.map((item) => (
            <button
              key={item.key}
              className={
                "nav-item" +
                (section === item.key ? " active" : "") +
                (item.enabled ? "" : " disabled")
              }
              disabled={!item.enabled}
              onClick={() => item.enabled && setSection(item.key)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <main className="content content-wide">
          {section === "storage" && <StorageInventory />}
          {section === "backup" && <BackupBatches />}
        </main>
      </div>
    </div>
  );
}
