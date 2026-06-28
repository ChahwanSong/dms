import { Fragment, useState } from "react";
import { type User } from "../../api";
import TopBar from "../../components/TopBar";
import StorageInventory from "./storage/StorageInventory";
import BackupBatches from "./backup/BackupBatches";
import Dashboard from "./dashboard/Dashboard";
import DashboardAttention from "./dashboard/DashboardAttention";
import DashboardActivity from "./dashboard/DashboardActivity";

// Operator/admin interface. A simple left-nav shell; each section is a separate
// feature module. 종합 대시보드 has two sub-sections (조치 필요 · 액티비티) shown
// indented beneath it.
type Section =
  | "dashboard"
  | "dashboard-attention"
  | "dashboard-activity"
  | "storage"
  | "backup";

interface NavItem {
  key: Section;
  label: string;
  children?: { key: Section; label: string }[];
}

const NAV: NavItem[] = [
  {
    key: "dashboard",
    label: "종합 대시보드",
    children: [
      { key: "dashboard-attention", label: "조치 필요" },
      { key: "dashboard-activity", label: "액티비티" },
    ],
  },
  { key: "storage", label: "스토리지 인벤토리" },
  { key: "backup", label: "데이터 백업" },
];

export default function OperatorApp({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const [section, setSection] = useState<Section>("dashboard");

  return (
    <div className="app">
      <TopBar user={user} onLogout={onLogout} title="DMS Portal · 운영자 콘솔" />
      <div className="layout">
        <nav className="sidebar">
          {NAV.map((item) => (
            <Fragment key={item.key}>
              <button
                className={"nav-item" + (section === item.key ? " active" : "")}
                onClick={() => setSection(item.key)}
              >
                {item.label}
              </button>
              {item.children?.map((child) => (
                <button
                  key={child.key}
                  className={"nav-item nav-subitem" + (section === child.key ? " active" : "")}
                  onClick={() => setSection(child.key)}
                >
                  {child.label}
                </button>
              ))}
            </Fragment>
          ))}
        </nav>
        <main className="content content-wide">
          {section === "dashboard" && <Dashboard />}
          {section === "dashboard-attention" && (
            <DashboardAttention onNavigate={(s) => setSection(s as Section)} />
          )}
          {section === "dashboard-activity" && <DashboardActivity />}
          {section === "storage" && <StorageInventory />}
          {section === "backup" && <BackupBatches />}
        </main>
      </div>
    </div>
  );
}
