import { useState, type ReactNode } from "react";
import { type User } from "../../api";
import TopBar from "../../components/TopBar";
import UserSyncTab from "./sync/UserSyncTab";
import UserScanTab from "./scan/UserScanTab";

// End-user interface. Talks only to /api/user/*. A simple left-nav shell (mirrors
// the operator console) with two self-service menus: 데이터 Sync · 데이터 스캔.
type Section = "sync" | "scan";

interface NavItem {
  key: Section;
  label: string;
}

const NAV: NavItem[] = [
  { key: "sync", label: "데이터 Sync" },
  { key: "scan", label: "데이터 스캔" },
];

const ICONS: Record<Section, ReactNode> = {
  sync: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 2l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 22l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3" /></svg>),
  scan: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>),
};

export default function UserApp({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const [section, setSection] = useState<Section>("sync");

  return (
    <div className="app app-fixed">
      <TopBar user={user} onLogout={onLogout} title="DMS Portal · 사용자" />
      <div className="layout">
        <nav className="sidebar">
          {NAV.map((item) => (
            <button
              key={item.key}
              className={"nav-item" + (section === item.key ? " active" : "")}
              onClick={() => setSection(item.key)}
            >
              <span className="nav-ic">{ICONS[item.key]}</span>
              {item.label}
            </button>
          ))}
          <div className="sidebar-foot">
            <span className="sidebar-user">
              <span className="badge badge-user">사용자</span>
              <span className="muted">
                {user.username}
                {user.dummy ? " · 더미" : ""}
              </span>
            </span>
            <button className="ghost sidebar-logout" onClick={onLogout}>
              로그아웃
            </button>
          </div>
        </nav>
        <main className="content content-wide">
          {section === "sync" && <UserSyncTab />}
          {section === "scan" && <UserScanTab />}
        </main>
      </div>
    </div>
  );
}
