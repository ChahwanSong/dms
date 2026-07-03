import { useEffect, useState } from "react";
import { userApi, type Overview, type User } from "../../api";
import TopBar from "../../components/TopBar";

// End-user interface. Talks only to /api/user/*. Placeholder content for now;
// self-service views over DMS get added here later.
export default function UserApp({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => void;
}) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    userApi
      .overview()
      .then(setOverview)
      .catch((e) => setError(e instanceof Error ? e.message : "조회 실패"));
  }, []);

  return (
    <div className="app">
      <TopBar user={user} onLogout={onLogout} title="DMS Portal" />
      <main className="content">
        <div className="card">
          <h2>환영합니다, {user.username} 님</h2>
          <p className="muted">
            사용자 인터페이스입니다. 본인 스토리지·요청에 대한 셀프서비스
            기능이 이후 단계에서 추가됩니다.
          </p>
          {error && <p className="error">{error}</p>}
          {overview && (
            <ul className="sections">
              {overview.sections.map((s) => (
                <li key={s.key}>
                  <span>{s.title}</span>
                  <span className="tag">{s.status}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </main>
      {/* logged-in user + logout, pinned to the bottom-left (matches the operator foot) */}
      <div className="app-foot">
        <span className="badge badge-user">사용자</span>
        <span className="muted">
          {user.username}
          {user.dummy ? " · 더미" : ""}
        </span>
        <button className="ghost" onClick={onLogout}>
          로그아웃
        </button>
      </div>
    </div>
  );
}
