import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { StoragesList } from "./StoragesList";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
const S = { storage_name:"cephfs", mount_path:"/cephfs", managed_root:"/cephfs/dms",
  backend_type:"ceph", enabled:1, status:"Healthy", status_detail:null };
function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><StoragesList /></QueryClientProvider>);
}
test("lists storages and shows manage actions", async () => {
  server.use(http.get("/api/admin/storages", () => HttpResponse.json([S])));
  wrap();
  expect(await screen.findByText("cephfs")).toBeInTheDocument();
  expect(screen.getByText("Healthy")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "스토리지 등록" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "삭제" })).toBeInTheDocument();
});
test("delete shows in-use error on 409", async () => {
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([S])),
    http.delete("/api/admin/storages/cephfs", () => HttpResponse.json({ detail: "storage_in_use" }, { status: 409 })));
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "삭제" }));
  await userEvent.click(await screen.findByRole("button", { name: "삭제 확인" }));
  expect(await screen.findByText("사용 중인 스토리지는 삭제할 수 없습니다 (비활성화하세요)")).toBeInTheDocument();
});
test("toggling a storage sends the correct PUT body", async () => {
  let capturedBody: unknown;
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([S])),
    http.put("/api/admin/storages/:name", async ({ request }) => {
      capturedBody = await request.json();
      return HttpResponse.json({ ...S, enabled: 0 });
    }));
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "비활성화" }));
  await screen.findByRole("button", { name: "비활성화" });
  expect(capturedBody).toEqual({
    mount_path: S.mount_path, managed_root: S.managed_root, backend_type: S.backend_type, enabled: false,
  });
});
