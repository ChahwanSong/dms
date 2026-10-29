import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { USAGE_TABS, UsageTabs } from "./UsageHeader";
import { PATHS_MEMORY_KEY, readPathsMemory, rememberPaths } from "./usageMemory";
import { DETAIL_ROUTES } from "../../app/navigation";

// 사용량 분석 하위 탭(2026-10-29 -- K8sTabs 관용) + 경로별 마지막 쿼리 기억(usageMemory).
const at = (path: string) => render(<MemoryRouter initialEntries={[path]}><UsageTabs /></MemoryRouter>);
const pathsLink = () => screen.getByRole("link", { name: "경로별" });

// 차단 환경(사생활 모드·저장소 차단)은 접근마다 던지는 sessionStorage 로 흉내 낸다.
const blocked = () => {
  const deny = () => { throw new DOMException("blocked", "SecurityError"); };
  vi.stubGlobal("sessionStorage", { getItem: deny, setItem: deny, removeItem: deny, clear: deny, key: deny, length: 0 });
};
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear(); });

describe("UsageTabs", () => {
  it("두 하위 화면을 잇고, nav 라벨은 「사용량 분석 하위 메뉴」 하나", () => {
    at("/admin/usage");
    expect(screen.getByRole("navigation", { name: "사용량 분석 하위 메뉴" })).toBeInTheDocument();
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["경로별", "스토리지별"]);
    expect(pathsLink()).toHaveAttribute("href", "/admin/usage");
    expect(screen.getByRole("link", { name: "스토리지별" })).toHaveAttribute("href", "/admin/usage/storages");
  });

  it("하위 화면에서 「경로별」이 함께 켜지지 않는다(end 매칭) -- 현재 위치는 aria-current", () => {
    at("/admin/usage/storages");
    expect(screen.getByRole("link", { name: "스토리지별" })).toHaveAttribute("aria-current", "page");
    expect(pathsLink()).not.toHaveAttribute("aria-current");
  });

  it("탭 라벨 = 브레드크럼 라벨(navigation DETAIL_ROUTES) -- h1·크럼·탭이 같은 문자열", () => {
    for (const t of USAGE_TABS.slice(1)) {
      const d = DETAIL_ROUTES.find((r) => r.pattern === t.to);
      expect(d?.label, t.to).toBe(t.label);
      expect(d?.parent).toBe("/admin/usage");
    }
  });

  it("탭 라벨끼리 부분 문자열이 아니다(Playwright getByLabel·getByText 부분 일치 사고 예방)", () => {
    const labels = USAGE_TABS.map((t) => t.label);
    for (const a of labels) for (const b of labels) if (a !== b) expect(b.includes(a), `${a} ⊂ ${b}`).toBe(false);
  });

  it("「경로별」 링크: 경로별 안에선 지금 쿼리(target 제외), 다른 탭에선 sessionStorage 의 마지막 쿼리를 복원한다", () => {
    const { unmount } = at("/admin/usage?storage=k8s-pv-nfs&requester=alice&target=a%2Fb&q=proj");
    expect(pathsLink()).toHaveAttribute("href", "/admin/usage?storage=k8s-pv-nfs&requester=alice&q=proj");
    unmount();
    rememberPaths("?storage=k8s-pv-nfs&cluster=dmsk8s&sort=bytes&target=x", "k8s-pv-nfs");
    at("/admin/usage/storages");
    expect(pathsLink()).toHaveAttribute("href", "/admin/usage?storage=k8s-pv-nfs&cluster=dmsk8s&sort=bytes");
  });

  it("기본 스토리지를 보고 있었으면(기억한 쿼리에 storage 없음) 기억한 스토리지를 적어 돌아간다 -- 붙잡은 기본은 탭 왕복으로 사라진다", () => {
    // 2차 리뷰(2026-10-29): 그 사이 요약이 바뀌면 다시 파생한 기본이 보던 것과 다른 스토리지였다.
    rememberPaths("?q=team&sort=bytes", "cephfs-dms");
    const { unmount } = at("/admin/usage/storages");
    expect(pathsLink()).toHaveAttribute("href", "/admin/usage?storage=cephfs-dms&q=team&sort=bytes");
    unmount();
    rememberPaths("", "");                                         // 빈 이름도 이름이다
    const second = at("/admin/usage/storages");
    expect(pathsLink()).toHaveAttribute("href", "/admin/usage?storage=");
    second.unmount();
    sessionStorage.setItem(PATHS_MEMORY_KEY, JSON.stringify({ search: "q=x" }));   // 스토리지를 모르면 쿼리만
    at("/admin/usage/storages");
    expect(pathsLink()).toHaveAttribute("href", "/admin/usage?q=x");
  });

  it("기억을 못 읽으면(차단 환경·깨진 값) 쿼리 없는 링크 -- 화면은 깨지지 않는다", () => {
    sessionStorage.setItem(PATHS_MEMORY_KEY, "[1,2]");
    const { unmount } = at("/admin/usage/storages");
    expect(pathsLink()).toHaveAttribute("href", "/admin/usage");
    unmount();
    rememberPaths("?storage=a", "a");
    blocked();
    at("/admin/usage/storages");
    expect(pathsLink()).toHaveAttribute("href", "/admin/usage");
  });
});

describe("usageMemory", () => {
  it("스토리지를 모르면(null) 전에 적은 스토리지를 남긴다 · target 은 적지 않는다", () => {
    rememberPaths("?storage=a&target=x", "a");
    expect(readPathsMemory()).toEqual({ search: "storage=a", storage: "a" });
    rememberPaths("?q=team", null);                                // 기본 스토리지가 아직 정해지지 않았다
    expect(readPathsMemory()).toEqual({ search: "q=team", storage: "a" });
    rememberPaths("", "");                                         // 빈 이름도 이름이다
    expect(readPathsMemory()).toEqual({ search: "", storage: "" });
  });

  it("쓰기가 막혀도 던지지 않는다", () => {
    blocked();
    expect(() => rememberPaths("?storage=a", "a")).not.toThrow();
    expect(readPathsMemory()).toBeNull();
  });
});
