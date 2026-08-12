import { describe, expect, test } from "vitest";
import { NAVIGATION, breadcrumbFor } from "./navigation";
import type { NavItem } from "./navigation";

// 메뉴는 데이터가 진실이다(슬라이스 31 T2) -- 이 파일은 그 데이터가 "기존 사이드바
// 전수"와 어긋나지 않음을 못 박는다. 항목 한 줄이 사라지면 여기가 문다(뮤테이션
// 증명 대상). 플랜 본문의 "15링크"는 오기다: 구 AppShell 실측은 작업4+스토리지3+
// 운영5+관리4 = 16링크다.

/** DMS 섹션의 모든 항목을 (그룹 라벨과 함께) 평탄화한다. */
function dmsItems(): { group: string; groupAdminOnly: boolean; item: NavItem }[] {
  const dms = NAVIGATION.find((s) => s.groups !== undefined);
  expect(dms, "그룹을 가진 DMS 섹션이 있어야 한다").toBeDefined();
  return (dms!.groups ?? []).flatMap((g) =>
    g.items.map((item) => ({ group: g.label, groupAdminOnly: g.adminOnly === true, item })));
}

test("기존 사이드바 16링크가 전부 데이터에 있다(전수 -- 초과도 누락도 없다)", () => {
  const paths = dmsItems().map((x) => x.item.path).sort();
  expect(paths).toEqual([
    "/admin/accounts", "/admin/artifact-base", "/admin/audit", "/admin/batches",
    "/admin/builds", "/admin/control", "/admin/dashboard", "/admin/denylist",
    "/admin/nodes", "/admin/policies", "/admin/releases", "/admin/scan",
    "/admin/storages", "/jobs", "/jobs/new", "/scan-paths",
  ].sort());
});

test("adminOnly 표시가 기존 isAdmin 게이트와 일치한다", () => {
  // 구 AppShell: /jobs·/jobs/new·/scan-paths 만 비관리자에게 보였다 -- 그룹 또는
  // 항목의 adminOnly 로 같은 게이트가 재현돼야 한다(항목이 새 통로로 새면 안 된다).
  const visibleToUser = dmsItems()
    .filter((x) => !x.groupAdminOnly && x.item.adminOnly !== true)
    .map((x) => x.item.path)
    .sort();
  expect(visibleToUser).toEqual(["/jobs", "/jobs/new", "/scan-paths"]);
});

test("placeholder 최상위 링크 /nas·/monitoring 이 데이터에 있다", () => {
  const placeholderPaths = NAVIGATION.filter((s) => s.path !== undefined).map((s) => s.path);
  expect(placeholderPaths).toEqual(["/nas", "/monitoring"]);
});

describe("breadcrumbFor", () => {
  test("상세 라우트: /jobs/abc = HOME>DMS>작업>내 작업>요청 상세", () => {
    expect(breadcrumbFor("/jobs/abc").map((c) => c.label))
      .toEqual(["HOME", "DMS", "작업", "내 작업", "요청 상세"]);
  });

  test("사이드바 항목: /admin/storages = HOME>DMS>스토리지>스토리지", () => {
    expect(breadcrumbFor("/admin/storages").map((c) => c.label))
      .toEqual(["HOME", "DMS", "스토리지", "스토리지"]);
  });

  test("/jobs/new 는 상세 패턴(:requestId)이 아니라 항목이 이긴다", () => {
    // matchPath("/jobs/:requestId", "/jobs/new") 도 참이라 -- 항목 스캔이 먼저가
    // 아니면 "작업 제출" 화면이 "요청 상세" 크럼을 달게 된다.
    expect(breadcrumbFor("/jobs/new").map((c) => c.label))
      .toEqual(["HOME", "DMS", "작업", "작업 제출"]);
  });

  test("HOME 은 / 링크, 마지막 크럼은 현재 화면(경로 유무 무관)", () => {
    const crumbs = breadcrumbFor("/jobs/abc");
    expect(crumbs[0]).toEqual({ label: "HOME", path: "/" });
    // 부모 항목("내 작업")은 되돌아갈 링크 경로를 가진다.
    expect(crumbs[3]).toEqual({ label: "내 작업", path: "/jobs" });
  });

  test("미지 경로는 HOME 만", () => {
    expect(breadcrumbFor("/no-such-route")).toEqual([{ label: "HOME", path: "/" }]);
  });

  test("placeholder: /nas = HOME>NAS", () => {
    expect(breadcrumbFor("/nas").map((c) => c.label)).toEqual(["HOME", "NAS"]);
  });
});
