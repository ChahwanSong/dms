import { Link, useLocation } from "react-router-dom";
import { breadcrumbFor } from "./navigation";

// 라우트 → 브레드크럼(슬라이스 31 T2). **h1 이 아니다** -- 페이지 타이틀(h1)은
// 각 화면이 소유한다(전제 재확인 #4). 여기 링크는 main 안이라 e2e 의 `aside a`
// 셀렉터(L4) 밖이다.
export function Breadcrumb() {
  const { pathname } = useLocation();
  const crumbs = breadcrumbFor(pathname);
  return (
    <nav aria-label="breadcrumb" className="mb-4 flex flex-wrap items-center gap-1.5 text-sm text-muted">
      {crumbs.map((crumb, i) => {
        const isLast = i === crumbs.length - 1;
        return (
          <span key={`${crumb.label}-${i}`} className="flex items-center gap-1.5">
            {i > 0 && <span aria-hidden>&gt;</span>}
            {i === 0 && crumb.path !== undefined ? (
              // 링크는 HOME 하나뿐이다(플랜 원문). 중간 크럼(항목 라벨)까지 링크로
              // 만들면 사이드바 NavLink 와 같은 이름의 <a> 가 중복돼 기존 테스트의
              // getByRole("link", { name }) 유일성 단언이 깨진다(실측 -- innerBoundary).
              <Link to={crumb.path} className="text-accent hover:underline">{crumb.label}</Link>
            ) : (
              <span className={isLast ? "text-ink" : undefined}>{crumb.label}</span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
