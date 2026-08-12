import { Routes, Route, Navigate, useLocation } from "react-router-dom";
import { AuthProvider } from "./AuthContext";
import { AppShell } from "./AppShell";
import { ErrorBoundary } from "./ErrorBoundary";
import { RequireRole } from "./RequireRole";
import { useMe } from "../features/auth/useAuth";
import { Button } from "../components/ui/Button";
import { ApiError } from "../lib/api";
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
import { ArtifactBasePage } from "../features/control/ArtifactBasePage";
import { AccountsList } from "../features/accounts/AccountsList";
import { NodesList } from "../features/nodes/NodesList";
import { BuildsPage } from "../features/builds/BuildsPage";
import { BuildDetail } from "../features/builds/BuildDetail";
import { ReleasesPage } from "../features/releases/ReleasesPage";
import { PlaceholderPage } from "../features/placeholder/PlaceholderPage";

function Home() {
  const me = useMe();
  if (me.isLoading) return <div className="p-6 text-muted">불러오는 중…</div>;
  // §2.4-5: 일시 500/네트워크 오류를 /login 으로 흘려보내면 로그인된 관리자가 "세션
  // 만료"로 오독하고 재로그인한다 -- 리다이렉트 없이 오류 문구 + 재시도를 렌더한다.
  // 단 401(세션 없음)은 진짜 "로그인 필요"라 기존 리다이렉트에 맡긴다: 여기서
  // 오류 화면을 띄우면 문구가 거짓말이 될 뿐 아니라, dms:unauthorized -> me 무효화
  // -> 재조회 401 무한 루프가 된다(AuthContext 는 관찰자가 /login 이동으로
  // 언마운트되어야 루프가 끊긴다 -- AuthContext.tsx 주석 참고).
  if (me.isError && !(me.error instanceof ApiError && me.error.status === 401)) {
    return (
      <div className="p-6 space-y-3">
        <p className="text-bad">세션 확인에 실패했습니다 — 서버 오류이거나 네트워크 문제일 수 있습니다</p>
        <Button onClick={() => me.refetch()}>다시 시도</Button>
      </div>
    );
  }
  if (me.data?.role === "admin") return <Navigate to="/admin/dashboard" replace />;
  return <Navigate to="/jobs" replace />;
}

export function AppRouter() {
  // AppRouter는 main.tsx에서 <BrowserRouter><AppRouter /></BrowserRouter>로
  // 마운트되고(테스트는 MemoryRouter) -- useLocation은 Router 컨텍스트 안에서만
  // 쓸 수 있는데, AppRouter 자체가 그 컨텍스트의 자식이므로 여기서 바로 쓸 수 있다.
  const { pathname } = useLocation();
  return (
    <AuthProvider>
      {/* key가 없으면 location이 바뀌어도(뒤로가기, 주소창 편집) 이미 잡힌 에러
          상태가 그대로 남아 "다시 시도"를 누르기 전까지 계속 같은 폴백만 보여준다
          -- AppShell의 안쪽 경계와 같은 이유로 여기도 key={pathname}이 필요하다. */}
      <ErrorBoundary key={pathname}>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Home />} />
          <Route path="/jobs" element={<RequireRole><AppShell><JobsList /></AppShell></RequireRole>} />
          <Route path="/jobs/new" element={<RequireRole><AppShell><SubmitJob /></AppShell></RequireRole>} />
          <Route path="/jobs/:requestId" element={<RequireRole><AppShell><RequestDetail /></AppShell></RequireRole>} />
          <Route path="/scan-paths" element={<RequireRole><AppShell><ScanPaths /></AppShell></RequireRole>} />
          {/* 슬라이스 31: 사이드바 최상위 placeholder(확정값 ③). 역할 무관 접근 --
              사이드바에서 모든 역할에게 보이므로 클릭이 404 로 떨어지면 안 된다. */}
          <Route path="/nas" element={<RequireRole><AppShell><PlaceholderPage title="NAS" /></AppShell></RequireRole>} />
          <Route path="/monitoring" element={<RequireRole><AppShell><PlaceholderPage title="Monitoring" /></AppShell></RequireRole>} />
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
          <Route path="/admin/artifact-base" element={<RequireRole role="admin"><AppShell><ArtifactBasePage /></AppShell></RequireRole>} />
          <Route path="/admin/accounts" element={<RequireRole role="admin"><AppShell><AccountsList /></AppShell></RequireRole>} />
          <Route path="/admin/nodes" element={<RequireRole role="admin"><AppShell><NodesList /></AppShell></RequireRole>} />
          <Route path="/admin/builds" element={<RequireRole role="admin"><AppShell><BuildsPage /></AppShell></RequireRole>} />
          <Route path="/admin/builds/:buildId" element={<RequireRole role="admin"><AppShell><BuildDetail /></AppShell></RequireRole>} />
          <Route path="/admin/releases" element={<RequireRole role="admin"><AppShell><ReleasesPage /></AppShell></RequireRole>} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </ErrorBoundary>
    </AuthProvider>
  );
}
