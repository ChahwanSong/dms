import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { test, expect } from "vitest";
import { AuthProvider } from "./AuthContext";

test("AuthProvider clears the entire cache when a global 401 is dispatched", () => {
  const qc = new QueryClient();
  qc.setQueryData(["requests"], [{}]);

  render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <div />
      </AuthProvider>
    </QueryClientProvider>,
  );

  window.dispatchEvent(new CustomEvent("dms:unauthorized"));

  expect(qc.getQueryData(["requests"])).toBeUndefined();
});
