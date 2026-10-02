import { describe, expect, test } from "vitest";
import { NAVIGATION, activeNavPath, breadcrumbFor } from "./navigation";
import type { NavItem } from "./navigation";

// 메뉴는 데이터가 진실이다(슬라이스 31 T2) -- 이 파일은 그 데이터가 "현행 사이드바
// 전수"와 어긋나지 않음을 못 박는다. 항목 한 줄이 사라지면 여기가 문다(뮤테이션
// 증명 대상). 슬라이스 37: 내 스캔 경로·scan 실행 제거(scan 은 단일 작업으로 흡수),
// 아티팩트 경로는 운영으로 이동. 2026-08-23: 운영에 사용량 분석(/admin/usage)
// 추가 -- 작업2+스토리지2+운영7+관리4 = 15링크.

/** DMS 섹션의 모든 항목을 (그룹 라벨과 함께) 평탄화한다. */
function dmsItems(): { group: string; groupAdminOnly: boolean; item: NavItem }[] {
  const dms = NAVIGATION.find((s) => s.groups !== undefined);
  expect(dms, "그룹을 가진 DMS 섹션이 있어야 한다").toBeDefined();
  return (dms!.groups ?? []).flatMap((g) =>
    g.items.map((item) => ({ group: g.label, groupAdminOnly: g.adminOnly === true, item })));
}

test("현행 사이드바 17링크가 전부 데이터에 있다(전수 -- 초과도 누락도 없다)", () => {
  const paths = dmsItems().map((x) => x.item.path).sort();
  expect(paths).toEqual([
    "/admin/accounts", "/admin/artifact-base", "/admin/audit", "/admin/batches",
    "/admin/builds", "/admin/control", "/admin/dashboard", "/admin/denylist", "/admin/mail",
    "/admin/nodes", "/admin/policies", "/admin/portal", "/admin/releases",
    "/admin/storages", "/admin/usage", "/jobs", "/jobs/new",
  ].sort());
});

test("adminOnly 표시가 기존 isAdmin 게이트와 일치한다", () => {
  // 비관리자에게는 /jobs·/jobs/new 만 보인다 -- 그룹 또는 항목의 adminOnly 로
  // 같은 게이트가 재현돼야 한다(항목이 새 통로로 새면 안 된다).
  const visibleToUser = dmsItems()
    .filter((x) => !x.groupAdminOnly && x.item.adminOnly !== true)
    .map((x) => x.item.path)
    .sort();
  expect(visibleToUser).toEqual(["/jobs", "/jobs/new"]);
});

test("최상위 섹션은 DMS 하나뿐이다(NAS·Monitoring 은 추후 추가 — 데이터에서 제거됨)", () => {
  expect(NAVIGATION.map((s) => s.label)).toEqual(["DMS"]);
});

test("그룹 순서는 운영·작업·스토리지·관리다(접힘은 AppShell 아코디언이 정한다)", () => {
  // 홈=대시보드(운영)와 짝: 로그인 직후엔 활성 그룹(운영)만 열린다 -- 열림
  // 접힘 상태는 데이터가 아니라 AppShell(lib/navState -- 사용자 토글 유지)의 몫이라 여기선 순서만.
  const groups = NAVIGATION[0].groups ?? [];
  expect(groups.map((g) => g.label)).toEqual(["운영", "작업", "스토리지", "관리"]);
});

test("작업 그룹은 단일 작업(제출)이 내 작업(목록)보다 위다(사용자 결정 2026-08-19)", () => {
  const jobs = (NAVIGATION[0].groups ?? []).find((g) => g.label === "작업")!;
  expect(jobs.items.map((i) => i.label)).toEqual(["단일 작업", "배치 작업", "전체 작업"]);
});

describe("breadcrumbFor", () => {
  test("상세 라우트: /jobs/abc = HOME>DMS>작업>내 작업>요청 상세", () => {
    expect(breadcrumbFor("/jobs/abc").map((c) => c.label))
      .toEqual(["HOME", "DMS", "작업", "전체 작업", "요청 상세"]);
  });

  test("사이드바 항목: /admin/storages = HOME>DMS>스토리지>스토리지", () => {
    expect(breadcrumbFor("/admin/storages").map((c) => c.label))
      .toEqual(["HOME", "DMS", "스토리지", "스토리지"]);
  });

  test("/jobs/new 는 상세 패턴(:requestId)이 아니라 항목이 이긴다", () => {
    // matchPath("/jobs/:requestId", "/jobs/new") 도 참이라 -- 항목 스캔이 먼저가
    // 아니면 "단일 작업" 화면이 "요청 상세" 크럼을 달게 된다.
    expect(breadcrumbFor("/jobs/new").map((c) => c.label))
      .toEqual(["HOME", "DMS", "작업", "단일 작업"]);
  });

  test("HOME 은 / 링크, 마지막 크럼은 현재 화면(경로 유무 무관)", () => {
    const crumbs = breadcrumbFor("/jobs/abc");
    expect(crumbs[0]).toEqual({ label: "HOME", path: "/" });
    // 부모 항목("내 작업")은 되돌아갈 링크 경로를 가진다.
    expect(crumbs[3]).toEqual({ label: "전체 작업", path: "/jobs" });
  });

  test("미지 경로는 HOME 만", () => {
    expect(breadcrumbFor("/no-such-route")).toEqual([{ label: "HOME", path: "/" }]);
  });

  test("미지 경로는 HOME 만(placeholder 제거 후 /nas 도 미지다)", () => {
    expect(breadcrumbFor("/nas").map((c) => c.label)).toEqual(["HOME"]);
  });

  // 빌드는 사이드바 항목 하나(「빌드」) 아래 하위 페이지 둘(빌드하기·빌드 이력)이다 --
  // 이력은 사이드바에 없으므로 DETAIL_ROUTES 를 타야 크럼과 활성 항목(「빌드」)을 얻는다.
  test("빌드 이력은 「빌드」 항목 아래 크럼을 단다", () => {
    expect(breadcrumbFor("/admin/builds/history").map((c) => c.label))
      .toEqual(["HOME", "DMS", "운영", "빌드", "빌드 이력"]);
    expect(activeNavPath("/admin/builds/history")).toBe("/admin/builds");
  });

  test("「history」가 :buildId 로 먹히지 않는다(DETAIL_ROUTES 순서)", () => {
    // matchPath("/admin/builds/:buildId", "/admin/builds/history") 도 참이라 --
    // 구체적인 쪽(history)이 먼저 물지 않으면 이력 화면이 "빌드 상세" 크럼을 단다
    // (/admin/batches/new vs :batchId 와 같은 함정).
    const last = (path: string) => {
      const crumbs = breadcrumbFor(path);
      return crumbs[crumbs.length - 1].label;
    };
    expect(last("/admin/builds/history")).toBe("빌드 이력");
    expect(last("/admin/builds/abc123")).toBe("빌드 상세");
  });
});

describe("activeNavPath — 사이드바 활성은 최장 일치 하나", () => {
  test("접두 형제(/jobs vs /jobs/new)에서 구체적인 쪽만 켠다(사용자 보고 결함)", () => {
    expect(activeNavPath("/jobs/new")).toBe("/jobs/new");   // 내 작업이 함께 켜지면 안 됨
    expect(activeNavPath("/jobs")).toBe("/jobs");
  });

  test("상세 경로에서는 부모 항목이 켜진다(end 방식이 잃는 성질)", () => {
    expect(activeNavPath("/jobs/abc123")).toBe("/jobs");
    expect(activeNavPath("/admin/batches/xyz")).toBe("/admin/batches");
    expect(activeNavPath("/admin/builds/history")).toBe("/admin/builds");
    expect(activeNavPath("/admin/builds/images")).toBe("/admin/builds");
  });

  test("경계 '/' 없는 유사 접두는 물지 않고, 무매칭은 null", () => {
    expect(activeNavPath("/jobs-archive")).toBeNull();
    expect(activeNavPath("/nowhere")).toBeNull();
  });
});

test("배치 작업은 작업 그룹의 관리자 전용 항목이다(사용자 결정 2026-10-01: 운영 → 작업)", () => {
  const groups = NAVIGATION[0].groups ?? [];
  const jobs = groups.find((g) => g.label === "작업")!;
  const ops = groups.find((g) => g.label === "운영")!;
  expect(jobs.adminOnly).toBeUndefined();              // 그룹은 모두에게 보인다
  expect(jobs.items.find((i) => i.path === "/admin/batches")?.adminOnly).toBe(true);
  expect(ops.items.map((i) => i.path)).not.toContain("/admin/batches");
  // 크럼도 새 자리를 따른다(상세 라우트는 부모 항목으로 귀속)
  expect(breadcrumbFor("/admin/batches").map((c) => c.label)).toEqual(["HOME", "DMS", "작업", "배치 작업"]);
  expect(breadcrumbFor("/admin/batches/abc").map((c) => c.label))
    .toEqual(["HOME", "DMS", "작업", "배치 작업", "배치 상세"]);
});
