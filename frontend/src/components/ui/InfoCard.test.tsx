import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";
import { InfoCard } from "./InfoCard";

// smoke: 연파랑 안내 서피스. role 없음 -- 장식 서피스이지 alert/region 이 아니다.
test("InfoCard 는 bg-infobg 서피스로 children 을 감싸고 role 이 없다", () => {
  const { container } = render(<InfoCard>안내 카드</InfoCard>);
  expect(screen.getByText("안내 카드")).toBeInTheDocument();
  const el = container.firstElementChild!;
  expect(el.className).toContain("bg-infobg");
  expect(el.getAttribute("role")).toBeNull();
});
