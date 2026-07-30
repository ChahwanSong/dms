import { type User } from "../api";

// Shared chrome for both interfaces. The role badge makes the active interface
// obvious; everything below the bar is role-specific.
export default function TopBar({
  user,
  onLogout,
  title,
  clusterName,
  showUser = false,
  onTitleClick,
}: {
  user: User;
  onLogout: () => void;
  title: string;
  // Which cluster this portal serves. Rendered as its own chip rather than being
  // concatenated into `title` so it reads as an environment marker, not part of the
  // product name — the point is to notice at a glance which cluster you are on.
  clusterName?: string;
  // Default: the bar shows only the title + role badge, and the caller renders the
  // user/logout at the bottom-left (operator → sidebar foot, user → app foot). Pass
  // true to put them back in the bar.
  showUser?: boolean;
  // When provided, the title becomes a clickable "home" affordance (e.g. operator →
  // 종합 대시보드). Omitted → plain, non-interactive title.
  onTitleClick?: () => void;
}) {
  const roleLabel = user.role === "operator" ? "운영자" : "사용자";
  return (
    <header className="topbar">
      {onTitleClick ? (
        <button
          type="button"
          className="brand brand-link"
          onClick={onTitleClick}
          title="종합 대시보드로 이동"
        >
          {title}
        </button>
      ) : (
        <span className="brand">{title}</span>
      )}
      {clusterName && (
        <span className="cluster-chip" title={`클러스터: ${clusterName}`}>
          <i className="cluster-chip-dot" />
          {clusterName}
        </span>
      )}
      <span className={`badge badge-${user.role}`}>{roleLabel}</span>
      {showUser && (
        <>
          <span className="spacer" />
          <span className="muted">
            {user.username}
          </span>
          <button className="ghost" onClick={onLogout}>
            로그아웃
          </button>
        </>
      )}
    </header>
  );
}
