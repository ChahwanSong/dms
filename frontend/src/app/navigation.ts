import { matchPath } from "react-router-dom";
import type { LucideIcon } from "lucide-react";
import {
  Ban, Boxes, Database, FilePlus, FolderCog, FolderSearch, Hammer,
  Layers, LayoutDashboard, ListTodo, Play, Rocket, ScrollText,
  Server, Shield, SlidersHorizontal, Users,
} from "lucide-react";

// 메뉴는 **데이터가 진실**이다(슬라이스 31 T2): 항목 추가·이동은 아래 배열 한 줄이고,
// AppShell 은 이 데이터를 렌더만 한다. adminOnly 는 표시 게이트일 뿐 보안이 아니다 --
// 진짜 차단은 라우터의 RequireRole(role="admin")과 서버가 한다.

export interface NavItem { path: string; label: string; icon: LucideIcon; adminOnly?: boolean }
export interface NavGroup {
  label: string; items: NavItem[]; adminOnly?: boolean;
  // 접힘 기본(사용자 조정): 운영만 펼치고 나머지는 접는다. 단 AppShell 의 「활성
  // 그룹 자동 펼침」(groupLabelFor)이 우선이라, 지금 보고 있는 화면의 그룹은 접힘
  // 기본이어도 항상 열려 있다 -- 접힘이 "링크를 못 찾는" 사고가 되지 않는 이유다.
  defaultCollapsed?: boolean;
}
export interface NavSection {           // 최상위: 지금은 DMS 뿐(NAS·Monitoring 추후 추가)
  label: string; icon: LucideIcon;
  groups?: NavGroup[];                  // DMS
  path?: string;                        // 미래의 단일 링크 섹션용(현재 미사용)
}

// 항목 라벨은 기존 사이드바 문구 그대로다 -- router.test 가 「사이드바 라벨 = h1」
// 짝을 단언하는 화면(릴리스 등)이 있어 문구를 바꾸면 화면까지 연쇄로 바꿔야 한다.
// 그룹 순서·접힘(사용자 조정, 2026-08-13): 홈=대시보드(운영)와 짝 -- 로그인 직후
// 화면의 그룹(운영)만 열려 있고 작업·스토리지·관리는 접힌다.
export const NAVIGATION: NavSection[] = [
  {
    label: "DMS", icon: Boxes,
    groups: [
      {
        label: "운영", adminOnly: true,
        items: [
          { path: "/admin/dashboard", label: "대시보드", icon: LayoutDashboard },
          { path: "/admin/batches", label: "배치 작업", icon: Layers },
          { path: "/admin/builds", label: "빌드", icon: Hammer },
          { path: "/admin/releases", label: "릴리스", icon: Rocket },
          { path: "/admin/control", label: "컨트롤 상태", icon: SlidersHorizontal },
        ],
      },
      {
        label: "작업", defaultCollapsed: true,
        items: [
          { path: "/jobs", label: "내 작업", icon: ListTodo },
          { path: "/jobs/new", label: "작업 제출", icon: FilePlus },
          { path: "/scan-paths", label: "내 스캔 경로", icon: FolderSearch },
          { path: "/admin/scan", label: "scan 실행", icon: Play, adminOnly: true },
        ],
      },
      {
        label: "스토리지", adminOnly: true, defaultCollapsed: true,
        items: [
          { path: "/admin/storages", label: "스토리지", icon: Database },
          { path: "/admin/nodes", label: "노드", icon: Server },
          { path: "/admin/artifact-base", label: "아티팩트 경로", icon: FolderCog },
        ],
      },
      {
        label: "관리", adminOnly: true, defaultCollapsed: true,
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
