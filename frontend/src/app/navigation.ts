import { matchPath } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import {
  Ban, Boxes, Database, FilePlus, FolderCog, Hammer,
  Layers, LayoutDashboard, ListTodo, Rocket, ScrollText,
  Server, Shield, SlidersHorizontal, TrendingUp, Users,
} from "lucide-react";

// 메뉴는 **데이터가 진실**이다(슬라이스 31 T2): 항목 추가·이동은 아래 배열 한 줄이고,
// AppShell 은 이 데이터를 렌더만 한다. adminOnly 는 표시 게이트일 뿐 보안이 아니다 --
// 진짜 차단은 라우터의 RequireRole(role="admin")과 서버가 한다.

export interface NavItem { path: string; label: string; icon: LucideIcon; adminOnly?: boolean }
// 접힘: 그룹 토글은 서로 독립(사용자 결정 2026-08-19 — 아코디언은 같은 날
// 도입했다가 해제). 초기엔 현재 경로의 그룹만 열리므로(AppShell) 그룹별 접힘
// 기본 필드는 없다 -- 로그인 직후엔 홈 리다이렉트 화면의 그룹(운영자는 운영)만
// 열려 있고, 이후 사용자가 연 그룹은 경로를 옮겨도 닫히지 않는다.
export interface NavGroup { label: string; items: NavItem[]; adminOnly?: boolean }
export interface NavSection {           // 최상위: 지금은 DMS 뿐(NAS·Monitoring 추후 추가)
  label: string; icon: LucideIcon;
  groups?: NavGroup[];                  // DMS
  path?: string;                        // 미래의 단일 링크 섹션용(현재 미사용)
}

// 항목 라벨은 기존 사이드바 문구 그대로다 -- router.test 가 「사이드바 라벨 = h1」
// 짝을 단언하는 화면(릴리스 등)이 있어 문구를 바꾸면 화면까지 연쇄로 바꿔야 한다.
// 그룹 순서(사용자 조정, 2026-08-13): 홈=대시보드(운영)와 짝.
export const NAVIGATION: NavSection[] = [
  {
    label: "DMS", icon: Boxes,
    groups: [
      {
        label: "운영", adminOnly: true,
        items: [
          { path: "/admin/dashboard", label: "대시보드", icon: LayoutDashboard },
          { path: "/admin/batches", label: "배치 작업", icon: Layers },
          // 사용량 분석(2026-08-23): 디렉터리별 scan 이력(실 사용량·데이터 온도)
          // 시계열 — 전 요청자 통합이라 운영 그룹(admin 전용)이다.
          { path: "/admin/usage", label: "사용량 분석", icon: TrendingUp },
          { path: "/admin/builds", label: "빌드", icon: Hammer },
          { path: "/admin/releases", label: "릴리스", icon: Rocket },
          { path: "/admin/control", label: "컨트롤 상태", icon: SlidersHorizontal },
          // 스토리지 그룹에서 이동(사용자 결정 2026-08-18): 아티팩트 경로는 잡
          // 산출물 저장 위치라 스토리지 등록보다 운영 설정에 가깝다.
          { path: "/admin/artifact-base", label: "아티팩트 경로", icon: FolderCog },
        ],
      },
      {
        // 슬라이스 37(사용자 결정): 「작업 제출」→「단일 작업」(배치 작업과 같은
        // 성격의 단일 항목 제출). 「내 스캔 경로」·「scan 실행」 메뉴는 제거 --
        // scan 은 단일 작업 위저드(운영자 전용 연산)로 흡수됐다.
        // 순서(사용자 결정 2026-08-19): 단일 작업(제출)이 전체 작업(목록)보다 위 --
        // 운영 그룹의 배치 작업(제출 동선)과 대구. 「전체 작업」(구 「내 작업」,
        // 개명 2026-08-22): 운영자는 전 요청, 사용자는 자기 요청 -- 요청자 열·필터·
        // 무한 스크롤이 붙었다.
        label: "작업",
        items: [
          { path: "/jobs/new", label: "단일 작업", icon: FilePlus },
          { path: "/jobs", label: "전체 작업", icon: ListTodo },
        ],
      },
      {
        label: "스토리지", adminOnly: true,
        items: [
          { path: "/admin/storages", label: "스토리지", icon: Database },
          { path: "/admin/nodes", label: "노드", icon: Server },
        ],
      },
      {
        label: "관리", adminOnly: true,
        items: [
          { path: "/admin/accounts", label: "계정", icon: Users },
          { path: "/admin/policies", label: "정책", icon: Shield },
          { path: "/admin/denylist", label: "denylist", icon: Ban },
          { path: "/admin/audit", label: "감사 로그", icon: ScrollText },
        ],
      },
    ],
  },
];

// 사이드바 밖 상세 라우트 → 브레드크럼 부모 매핑(react-router matchPath 패턴).
// /admin/batches/new 가 :batchId 보다 앞인 것은 router.tsx 의 라우트 순서와 같은
// 이유다 -- 패턴상 "new" 도 :batchId 에 매칭되므로 구체적인 쪽이 먼저 물어야 한다.
export const DETAIL_ROUTES = [
  { pattern: "/jobs/:requestId", label: "요청 상세", parent: "/jobs" },
  { pattern: "/admin/batches/new", label: "배치 생성", parent: "/admin/batches" },
  { pattern: "/admin/batches/:batchId", label: "배치 상세", parent: "/admin/batches" },
  // 빌드 이력은 사이드바 항목이 아니라 「빌드」의 하위 페이지(탭)다 -- 여기 있어야
  // 크럼이 「… > 빌드 > 빌드 이력」이 되고 운영 그룹이 자동으로 펼쳐진다.
  // ":buildId" 보다 앞인 것도 batches/new 와 같은 이유다("history" 가 매칭된다).
  { pattern: "/admin/builds/history", label: "빌드 이력", parent: "/admin/builds" },
  { pattern: "/admin/builds/images", label: "이미지 관리", parent: "/admin/builds" },
  { pattern: "/admin/builds/:buildId", label: "빌드 상세", parent: "/admin/builds" },
] as const;

/** 경로가 속한 그룹 라벨(상세 라우트는 부모 항목의 그룹으로 귀속). 미지 경로는 null.
    AppShell 의 「활성 그룹 자동 펼침」이 소비한다 -- 항목 스캔이 상세 패턴보다 먼저인
    이유는 breadcrumbFor 와 같다(/jobs/new 가 :requestId 에도 매칭되므로). */
// 사이드바 활성 항목: **최장 접두 일치 하나만**. NavLink 기본 판정(접두 일치)은
// /jobs/new 에서 /jobs(내 작업)까지 함께 켠다(형제가 접두 관계인 유일한 쌍) --
// end 를 달면 이번엔 상세(/jobs/:id, /admin/builds/history …)에서 부모 음영이
// 꺼진다. "가장 구체적인 항목 하나"가 두 경우를 모두 맞춘다.
export function activeNavPath(pathname: string): string | null {
  let best: string | null = null;
  const consider = (p: string | undefined) => {
    // 경계 '/' 필수: /jobs 가 /jobs-archive 류를 잘못 물지 않게.
    if (p !== undefined && (pathname === p || pathname.startsWith(p + "/"))) {
      if (best === null || p.length > best.length) best = p;
    }
  };
  for (const section of NAVIGATION) {
    consider(section.path);   // 미래의 단일 링크 섹션(NAS 등)도 같은 규칙
    for (const group of section.groups ?? []) {
      for (const item of group.items) consider(item.path);
    }
  }
  return best;
}

export function groupLabelFor(pathname: string): string | null {
  for (const section of NAVIGATION)
    for (const group of section.groups ?? [])
      for (const item of group.items)
        if (matchPath(item.path, pathname) !== null) return group.label;
  for (const detail of DETAIL_ROUTES)
    if (matchPath(detail.pattern, pathname) !== null)
      return groupLabelFor(detail.parent);
  return null;
}

export interface Crumb { label: string; path?: string }

/** 경로 → 브레드크럼(HOME > 섹션 > 그룹 > 항목 [> 상세]). 미지 경로는 HOME 만. */
export function breadcrumbFor(pathname: string): Crumb[] {
  const home: Crumb = { label: "HOME", path: "/" };
  // 1) 사이드바 항목 직격을 먼저 스캔한다 -- /jobs/new 는 "/jobs/:requestId" 에도
  //    매칭되므로, 상세 패턴을 먼저 보면 "작업 제출"이 "요청 상세" 크럼을 단다.
  for (const section of NAVIGATION) {
    if (section.path !== undefined && matchPath(section.path, pathname) !== null)
      return [home, { label: section.label, path: section.path }];
    for (const group of section.groups ?? [])
      for (const item of group.items)
        if (matchPath(item.path, pathname) !== null)
          return [home, { label: section.label }, { label: group.label },
                  { label: item.label, path: item.path }];
  }
  // 2) 상세 라우트: 부모 항목의 크럼 사슬(부모는 링크 경로를 가진다) + 상세 라벨.
  for (const detail of DETAIL_ROUTES)
    if (matchPath(detail.pattern, pathname) !== null)
      return [...breadcrumbFor(detail.parent), { label: detail.label }];
  return [home];
}
