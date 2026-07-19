import { Fragment, useState, type ReactNode } from "react";
import { type User, type FocusTarget } from "../../api";
import TopBar from "../../components/TopBar";
import StorageInventory from "./storage/StorageInventory";
import BackupBatches from "./backup/BackupBatches";
import ScanBatches from "./scan/ScanBatches";
import SyncTab from "./sync/SyncTab";
import RmTab from "./rm/RmTab";
import Dashboard from "./dashboard/Dashboard";
import DashboardAttention from "./dashboard/DashboardAttention";
import DashboardActivity from "./dashboard/DashboardActivity";
import WorkerNodes from "./dashboard/WorkerNodes";

// Operator/admin interface. A simple left-nav shell; each section is a separate
// feature module. 종합 대시보드 has two sub-sections (조치 필요 · 액티비티) shown
// indented beneath it.
type Section =
  | "dashboard"
  | "dashboard-attention"
  | "dashboard-activity"
  | "dashboard-nodes"
  | "storage"
  | "backup"
  | "scan"
  | "sync"
  | "rm";

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
      { key: "dashboard-nodes", label: "워커 노드" },
    ],
  },
  { key: "storage", label: "스토리지 인벤토리" },
  { key: "backup", label: "데이터 백업 (배치)" },
  { key: "scan", label: "데이터 스캔 (배치)" },
  { key: "sync", label: "데이터 Sync (단일)" },
  { key: "rm", label: "데이터 삭제 (단일)" },
];

// Left-nav icons (inline SVG, stroke=currentColor so the .nav-ic tone applies).
const ICONS: Partial<Record<Section, ReactNode>> = {
  dashboard: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="9" rx="1" /><rect x="14" y="3" width="7" height="5" rx="1" /><rect x="14" y="12" width="7" height="9" rx="1" /><rect x="3" y="16" width="7" height="5" rx="1" /></svg>),
  storage: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M3 5v14a9 3 0 0 0 18 0V5M3 12a9 3 0 0 0 18 0" /></svg>),
  backup: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3" /></svg>),
  scan: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></svg>),
  sync: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M17 2l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 22l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3" /></svg>),
  rm: (<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" /></svg>),
};

export default function OperatorApp({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const [section, setSection] = useState<Section>("dashboard");
  // Parent nav items with children start EXPANDED (sub-items 조치 필요 · 액티비티
  // visible by default); clicking the parent navigates to it AND toggles the
  // sub-items open/closed, so it can still be collapsed.
  const [expanded, setExpanded] = useState<Set<Section>>(
    () => new Set(NAV.filter((n) => n.children).map((n) => n.key)),
  );
  // deep-link target: which specific item the destination view should focus/open.
  const [focus, setFocus] = useState<FocusTarget | null>(null);

  // navigate to a section, optionally asking it to focus a specific item (조치 필요
  // "상세" → open the storage mapping / highlight the request, etc.). The focus
  // persists until the next navigation (nav clicks reset it), so the target view
  // keeps its highlight/open-detail while the operator stays there.
  function go(target: string, f?: FocusTarget) {
    setSection(target as Section);
    setFocus(f ?? null);
  }

  function selectParent(item: NavItem) {
    setSection(item.key);
    setFocus(null);
    if (item.children) {
      setExpanded((prev) => {
        const next = new Set(prev);
        next.has(item.key) ? next.delete(item.key) : next.add(item.key);
        return next;
      });
    }
  }

  return (
    <div className="app app-fixed">
      <TopBar
        user={user}
        onLogout={onLogout}
        title="DMS Portal · 운영자 콘솔"
        onTitleClick={() => go("dashboard")}
      />
      <div className="layout">
        <nav className="sidebar">
          {NAV.map((item) => {
            // keep the subtree open while a child is the active view, even if the
            // user never explicitly expanded it (defensive — children are normally
            // only reachable after expanding).
            const childActive = item.children?.some((c) => c.key === section) ?? false;
            const open = expanded.has(item.key) || childActive;
            return (
              <Fragment key={item.key}>
                <button
                  className={"nav-item" + (section === item.key ? " active" : "")}
                  onClick={() => selectParent(item)}
                  aria-expanded={item.children ? open : undefined}
                >
                  {item.children && (
                    <span className="nav-caret" aria-hidden>{open ? "▾" : "▸"}</span>
                  )}
                  {ICONS[item.key] && <span className="nav-ic">{ICONS[item.key]}</span>}
                  {item.label}
                </button>
                {item.children && open &&
                  item.children.map((child) => (
                    <button
                      key={child.key}
                      className={"nav-item nav-subitem" + (section === child.key ? " active" : "")}
                      onClick={() => { setSection(child.key); setFocus(null); }}
                    >
                      {child.label}
                    </button>
                  ))}
              </Fragment>
            );
          })}
          {/* logged-in operator + logout, pinned to the bottom of the sidebar */}
          <div className="sidebar-foot">
            <span className="sidebar-user">
              <span className="badge badge-operator">운영자</span>
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
          {section === "dashboard" && <Dashboard onNavigate={go} />}
          {section === "dashboard-attention" && (
            <DashboardAttention onNavigate={go} />
          )}
          {section === "dashboard-activity" && (
            <DashboardActivity focus={focus} onNavigate={go} />
          )}
          {section === "dashboard-nodes" && <WorkerNodes />}
          {section === "storage" && <StorageInventory focus={focus} />}
          {section === "backup" && <BackupBatches />}
          {section === "sync" && <SyncTab />}
          {section === "rm" && <RmTab />}
          {section === "scan" && <ScanBatches />}
        </main>
      </div>
    </div>
  );
}
