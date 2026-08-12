import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { InfoPanel } from "./InfoPanel";

// smoke: 회색 안내 서피스. role 없음 -- 장식 서피스이지 alert/region 이 아니다.
test("InfoPanel 은 bg-panel 서피스로 children 을 감싸고 role 이 없다", () => {
  const { container } = render(<InfoPanel>안내 문구</InfoPanel>);
  expect(screen.getByText("안내 문구")).toBeInTheDocument();
  const el = container.firstElementChild!;
  expect(el.className).toContain("bg-panel");
  expect(el.getAttribute("role")).toBeNull();
});
