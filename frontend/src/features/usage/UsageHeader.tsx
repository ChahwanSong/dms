import { NavLink, useLocation } from "react-router-dom";
import { pathsReturnSearch, readPathsMemory, withoutTarget } from "./usageMemory";

// 사용량 분석의 하위 내비게이션(2026-10-29 사용자 결정 「권장사항대로」 -- K8sTabs·BuildTabs 관용). 사이드바 항목은 「사용량 분석」
// 하나(navigation.ts)이고 화면은 여기 라우트 탭으로 가른다. 탭 이름은 「표의 행이 무엇인가」다: 경로별(한 스토리지의 scan 대상
// 경로 -- 매일 쓰는 표라 사이드바로 들어오면 여기) · 스토리지별(스토리지마다 합계 비교) · 소속별(2026-10-30 2단계 -- 한 스토리지를
// 권한 사용자·요청자·namespace·PV 이름·StorageClass·클러스터별 실 사용량 순위로, BreakdownPage). 소속별은 서버 /breakdown 과 함께
// 붙였다(결정 1 -- 기존 API 로는 「누가 많이 쓰나」 순위가 틀려서 1단계엔 없었다).
// end 는 「경로별」에만: /admin/usage 는 하위 화면의 접두라 end 가 없으면 스토리지별·소속별에서도 함께 켜진다.
// 평평한 라우트다(레이아웃 라우트를 쓰지 않는다 -- AppShell 의 ErrorBoundary key={pathname} 이 탭마다 다시 마운트하므로 정리 결과
// 같은 페이지 상태는 그 상태를 만든 경로별 페이지가 소유한다). 화면마다 UsageHeader 를 직접 그린다(K8sHeader 관용).
// nav 라벨은 이 하나뿐(고유) -- 화면의 다른 nav 에는 aria-label 을 달지 않는다(Playwright getByLabel 부분 일치 사고 예방).
// 파일 이름은 UsageHeader 다(UsageTabs 가 아니다): 같은 폴더의 usageTabs.ts(스토리지 집합)와 대소문자만 다르면 대소문자를 가리지 않는
// 파일시스템(macOS·Windows 체크아웃)에서 "./UsageTabs" 가 usageTabs.ts 로 풀린다(2026-10-29 리뷰).
export const USAGE_TABS = [
  { to: "/admin/usage", label: "경로별", end: true },
  { to: "/admin/usage/storages", label: "스토리지별", end: false },
  { to: "/admin/usage/breakdown", label: "소속별", end: false },
] as const;

const PATHS = USAGE_TABS[0].to;
const BREAKDOWN = USAGE_TABS[2].to;

/** 「소속별」 링크의 쿼리(「?」 없이): 소속별 안에선 지금 쿼리, 다른 탭에선 경로별이 보던 스토리지만(경로별이면 URL 의 storage, 기본
 *  스토리지를 보고 있었거나 스토리지별이면 경로별 기억의 storage -- 스토리지별 표가 「현재」로 칠하는 것과 같은 스토리지). 2026-10-30
 *  리뷰: 쿼리 없는 링크라 경로별에서 X 를 보다 탭을 누르면 소속별이 기본 스토리지를 새로 파생해 다른 스토리지의 순위를 보였다. 기준
 *  (by)은 싣지 않는다(소속별은 기억이 없다 -- 기본 기준으로 연다). 반대 방향(소속별 → 경로별)은 그대로 경로별의 마지막 쿼리다(필터·
 *  검색은 그 스토리지와 짝이다). */
function breakdownSearch(pathname: string, search: string): string {
  if (pathname === BREAKDOWN) return new URLSearchParams(search).toString();
  const storage = (pathname === PATHS ? new URLSearchParams(search).get("storage") : null) ?? readPathsMemory()?.storage ?? null;
  return storage === null ? "" : new URLSearchParams({ storage }).toString();
}

const tabCls = ({ isActive }: { isActive: boolean }) =>
  `-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm leading-6 ${
    isActive ? "border-accent font-medium text-accent" : "border-transparent text-ink/70 hover:text-ink"}`;

/** 현재 위치는 NavLink 의 aria-current="page" 가 알린다. 「경로별」 링크는 마지막 경로별 쿼리(target 제외)를 복원한다 --
 *  경로별에 있으면 지금 쿼리, 다른 탭이면 sessionStorage 의 기억(usageMemory.pathsReturnSearch -- 기본 스토리지를 보고 있었으면 그
 *  스토리지를 적어서, 없거나 못 읽으면 쿼리 없는 링크). 「소속별」 링크는 경로별이 보던 스토리지를 싣는다(breakdownSearch). */
export function UsageTabs() {
  const { pathname, search } = useLocation();
  const pathsSearch = pathname === PATHS ? withoutTarget(search) : pathsReturnSearch();
  const tabSearch: Record<string, string> = { [PATHS]: pathsSearch, [BREAKDOWN]: breakdownSearch(pathname, search) };
  return (
    <nav aria-label="사용량 분석 하위 메뉴" className="flex gap-1 overflow-x-auto border-b border-line">
      {USAGE_TABS.map((t) => {
        const s = tabSearch[t.to] ?? "";
        return (
          <NavLink key={t.to} end={t.end} className={tabCls} to={s !== "" ? { pathname: t.to, search: `?${s}` } : t.to}>
            {t.label}
          </NavLink>
        );
      })}
    </nav>
  );
}

/** 하위 화면 공용 머리(h1 + 한 줄 설명 + 탭). h1 은 화면에 하나 — 경로별은 사이드바 라벨(「사용량 분석」), 하위 화면은 탭·브레드크럼
 *  라벨과 같은 문자열이다(k8s 연동·빌드 이력 선례 — router.test 가 짝을 고정한다). */
export function UsageHeader({ title, description }: { title: string; description: React.ReactNode }) {
  return (
    <>
      <div className="min-w-0">
        <h1 className="text-2xl font-bold">{title}</h1>
        <p className="mt-1 max-w-3xl text-sm text-ink/70 break-keep">{description}</p>
      </div>
      <UsageTabs />
    </>
  );
}
