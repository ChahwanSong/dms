import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { StorageDialog } from "./StorageDialog";
import { Button } from "../../components/ui/Button";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}
test("create posts the four fields", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/storages", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body, { status: 201 }); }));
  wrap(<StorageDialog mode="create" trigger={<Button>등록</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  await userEvent.type(screen.getByLabelText("스토리지 이름"), "s1");
  await userEvent.type(screen.getByLabelText("마운트 경로"), "/s1");
  await userEvent.type(screen.getByLabelText("관리 루트"), "/s1/dms");
  await userEvent.type(screen.getByLabelText("백엔드"), "cephfs");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);
  expect(body).toEqual({ storage_name: "s1", mount_path: "/s1", managed_root: "/s1/dms", backend_type: "cephfs" });
});
