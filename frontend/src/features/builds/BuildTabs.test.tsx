import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { BuildTabs } from "./BuildTabs";

const at = (path: string) =>
  render(<MemoryRouter initialEntries={[path]}><BuildTabs /></MemoryRouter>);

describe("BuildTabs", () => {
  it("빌드하기·빌드 이력 두 하위 페이지를 잇는다", () => {
    at("/admin/builds");
    expect(screen.getByRole("link", { name: "빌드하기" }))
      .toHaveAttribute("href", "/admin/builds");
    expect(screen.getByRole("link", { name: "빌드 이력" }))
      .toHaveAttribute("href", "/admin/builds/history");
  });

  it("현재 위치를 aria-current 로 알린다(색만으로 말하지 않는다)", () => {
    at("/admin/builds");
    expect(screen.getByRole("link", { name: "빌드하기" }))
      .toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "빌드 이력" }))
      .not.toHaveAttribute("aria-current");
  });

  it("이력에서 「빌드하기」가 함께 켜지지 않는다(end 매칭)", () => {
    // /admin/builds 는 /admin/builds/history 의 접두라 end 가 없으면 둘 다 활성이
    // 된다 -- 그러면 탭이 "지금 어디"를 말하지 못한다.
    at("/admin/builds/history");
    expect(screen.getByRole("link", { name: "빌드 이력" }))
      .toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "빌드하기" }))
      .not.toHaveAttribute("aria-current");
  });
});
