import { Fragment, useState } from "react";
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

// Operator/admin interface. A simple left-nav shell; each section is a separate
// feature module. 종합 대시보드 has two sub-sections (조치 필요 · 액티비티) shown
// indented beneath it.
type Section =
  | "dashboard"
  | "dashboard-attention"
  | "dashboard-activity"
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
    ],
  },
  { key: "storage", label: "스토리지 인벤토리" },
  { key: "backup", label: "데이터 백업" },
  { key: "sync", label: "데이터 Sync" },
  { key: "rm", label: "데이터 삭제" },
  { key: "scan", label: "데이터 스캔" },
];

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
    <div className="app">
      <TopBar user={user} onLogout={onLogout} title="DMS Portal · 운영자 콘솔" />
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
        </nav>
        <main className="content content-wide">
          {section === "dashboard" && <Dashboard onNavigate={go} />}
          {section === "dashboard-attention" && (
            <DashboardAttention onNavigate={go} />
          )}
          {section === "dashboard-activity" && (
            <DashboardActivity focus={focus} onNavigate={go} />
          )}
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
