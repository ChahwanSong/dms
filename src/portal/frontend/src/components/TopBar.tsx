import { type User } from "../api";

// Shared chrome for both interfaces. The role badge makes the active interface
// obvious; everything below the bar is role-specific.
export default function TopBar({
  user,
  onLogout,
  title,
  showUser = false,
  onTitleClick,
}: {
  user: User;
  onLogout: () => void;
  title: string;
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
