import { Routes, Route, Navigate } from "react-router-dom";
import { AuthProvider } from "./AuthContext";
import { AppShell } from "./AppShell";
import { ErrorBoundary } from "./ErrorBoundary";
import { RequireRole } from "./RequireRole";
import { useMe } from "../features/auth/useAuth";
import { Login } from "../features/auth/Login";
import { JobsList } from "../features/jobs/JobsList";
import { SubmitJob } from "../features/jobs/SubmitJob";
import { SubmitScan } from "../features/jobs/SubmitScan";
import { RequestDetail } from "../features/jobs/RequestDetail";
import { StoragesList } from "../features/storages/StoragesList";
import { ScanPaths } from "../features/scanpaths/ScanPaths";
import { Dashboard } from "../features/dashboard/Dashboard";
import { BatchesList } from "../features/batches/BatchesList";
import { BatchCreate } from "../features/batches/BatchCreate";
import { BatchDetail } from "../features/batches/BatchDetail";
import { AuditLog } from "../features/audit/AuditLog";
import { PoliciesList } from "../features/policies/PoliciesList";
import { DenylistList } from "../features/denylist/DenylistList";
import { ControlStatePage } from "../features/control/ControlStatePage";
import { AccountsList } from "../features/accounts/AccountsList";
import { NodesList } from "../features/nodes/NodesList";
import { BuildsPage } from "../features/builds/BuildsPage";
import { BuildDetail } from "../features/builds/BuildDetail";

function Home() {
  const me = useMe();
  if (me.isLoading) return <div className="p-6 text-muted">불러오는 중…</div>;
  if (me.data?.role === "admin") return <Navigate to="/admin/dashboard" replace />;
  return <Navigate to="/jobs" replace />;
}

export function AppRouter() {
  return (
    <AuthProvider>
      <ErrorBoundary>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Home />} />
          <Route path="/jobs" element={<RequireRole><AppShell><JobsList /></AppShell></RequireRole>} />
          <Route path="/jobs/new" element={<RequireRole><AppShell><SubmitJob /></AppShell></RequireRole>} />
          <Route path="/jobs/:requestId" element={<RequireRole><AppShell><RequestDetail /></AppShell></RequireRole>} />
          <Route path="/scan-paths" element={<RequireRole><AppShell><ScanPaths /></AppShell></RequireRole>} />
          <Route path="/admin/scan" element={<RequireRole role="admin"><AppShell><SubmitScan /></AppShell></RequireRole>} />
          <Route path="/admin/storages" element={<RequireRole role="admin"><AppShell><StoragesList /></AppShell></RequireRole>} />
          <Route path="/admin/dashboard" element={<RequireRole role="admin"><AppShell><Dashboard /></AppShell></RequireRole>} />
          <Route path="/admin/batches" element={<RequireRole role="admin"><AppShell><BatchesList /></AppShell></RequireRole>} />
          <Route path="/admin/batches/new" element={<RequireRole role="admin"><AppShell><BatchCreate /></AppShell></RequireRole>} />
          <Route path="/admin/batches/:batchId" element={<RequireRole role="admin"><AppShell><BatchDetail /></AppShell></RequireRole>} />
          <Route path="/admin/audit" element={<RequireRole role="admin"><AppShell><AuditLog /></AppShell></RequireRole>} />
          <Route path="/admin/policies" element={<RequireRole role="admin"><AppShell><PoliciesList /></AppShell></RequireRole>} />
          <Route path="/admin/denylist" element={<RequireRole role="admin"><AppShell><DenylistList /></AppShell></RequireRole>} />
          <Route path="/admin/control" element={<RequireRole role="admin"><AppShell><ControlStatePage /></AppShell></RequireRole>} />
          <Route path="/admin/accounts" element={<RequireRole role="admin"><AppShell><AccountsList /></AppShell></RequireRole>} />
          <Route path="/admin/nodes" element={<RequireRole role="admin"><AppShell><NodesList /></AppShell></RequireRole>} />
          <Route path="/admin/builds" element={<RequireRole role="admin"><AppShell><BuildsPage /></AppShell></RequireRole>} />
          <Route path="/admin/builds/:buildId" element={<RequireRole role="admin"><AppShell><BuildDetail /></AppShell></RequireRole>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ErrorBoundary>
    </AuthProvider>
  );
}
