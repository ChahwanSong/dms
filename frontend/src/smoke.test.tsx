import { render, screen } from "@testing-library/react";
import { test, expect } from "vitest";

test("renders a heading", () => {
  render(<h1>DMS Portal</h1>);
  expect(screen.getByRole("heading", { name: "DMS Portal" })).toBeInTheDocument();
});
