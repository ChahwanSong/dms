import { render, screen } from "@testing-library/react";
import { StatusPill } from "./StatusPill";
import { test, expect } from "vitest";

test("renders label and ok styling, no leading dot", () => {
  const { container } = render(<StatusPill state="Succeeded" />);
  expect(screen.getByText("Succeeded")).toBeInTheDocument();
  expect(container.querySelector(".text-ok")).not.toBeNull();
  // dot 금지: 자식은 텍스트 노드만
  expect(container.querySelectorAll("span[aria-hidden]").length).toBe(0);
});
