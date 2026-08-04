import { render } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { test, expect } from "vitest";
import { AuthProvider } from "./AuthContext";

test("AuthProvider invalidates only the me query when a global 401 is dispatched", () => {
  const qc = new QueryClient();
  qc.setQueryData(["auth", "me"], { actor: "alice", role: "user" });
  qc.setQueryData(["requests"], [{}]);

  render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <div />
      </AuthProvider>
    </QueryClientProvider>,
  );

  window.dispatchEvent(new CustomEvent("dms:unauthorized"));

  // me is invalidated (marked stale), not cleared/removed from cache.
  expect(qc.getQueryData(["auth", "me"])).toEqual({ actor: "alice", role: "user" });
  expect(qc.getQueryState(["auth", "me"])?.isInvalidated).toBe(true);
  // other cache entries (e.g. requests) are left untouched — full clear() is not called.
  expect(qc.getQueryData(["requests"])).toEqual([{}]);
});
