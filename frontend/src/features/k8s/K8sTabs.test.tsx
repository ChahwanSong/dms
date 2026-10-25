import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { K8S_TABS, K8sTabs } from "./K8sTabs";
import { DETAIL_ROUTES } from "../../app/navigation";

const at = (path: string) => render(<MemoryRouter initialEntries={[path]}><K8sTabs /></MemoryRouter>);

describe("K8sTabs", () => {
  it("다섯 하위 화면을 잇고, nav 라벨은 「k8s 연동 하위 메뉴」 하나", () => {
    at("/admin/k8s");
    const nav = screen.getByRole("navigation", { name: "k8s 연동 하위 메뉴" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "개요" })).toHaveAttribute("href", "/admin/k8s");
    expect(screen.getByRole("link", { name: "StorageClass" })).toHaveAttribute("href", "/admin/k8s/storageclasses");
    expect(screen.getByRole("link", { name: "PV 탐색" })).toHaveAttribute("href", "/admin/k8s/volumes");
    expect(screen.getByRole("link", { name: "사용자 권한" })).toHaveAttribute("href", "/admin/k8s/access");
    expect(screen.getByRole("link", { name: "동기화 이력" })).toHaveAttribute("href", "/admin/k8s/history");
  });

  it("하위 화면에서 「개요」가 함께 켜지지 않는다(end 매칭) -- 현재 위치는 aria-current", () => {
    at("/admin/k8s/volumes");
    expect(screen.getByRole("link", { name: "PV 탐색" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "개요" })).not.toHaveAttribute("aria-current");
  });

  it("탭 라벨 = 브레드크럼 라벨(navigation DETAIL_ROUTES) -- h1·크럼·탭이 같은 문자열", () => {
    for (const t of K8S_TABS.slice(1)) {
      const d = DETAIL_ROUTES.find((r) => r.pattern === t.to);
      expect(d?.label, t.to).toBe(t.label);
      expect(d?.parent).toBe("/admin/k8s");
    }
  });

  it("탭 라벨끼리 부분 문자열이 아니다(Playwright getByLabel·getByText 부분 일치 사고 예방)", () => {
    const labels = K8S_TABS.map((t) => t.label);
    for (const a of labels) for (const b of labels) if (a !== b) expect(b.includes(a), `${a} ⊂ ${b}`).toBe(false);
  });
});
