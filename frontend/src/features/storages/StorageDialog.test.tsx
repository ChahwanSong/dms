import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { StorageDialog } from "./StorageDialog";
import { Button } from "../../components/ui/Button";
import type { Storage } from "../../lib/types";
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

test("edit seeds from storage, disables name, and PUTs the updated body", async () => {
  const S: Storage = {
    storage_name: "cephfs", mount_path: "/cephfs", managed_root: "/cephfs/dms",
    backend_type: "ceph", enabled: 1, status: "Healthy", status_detail: null,
  };
  let body: any = null;
  let urlName = "";
  server.use(http.put("/api/admin/storages/:name", async ({ request, params }) => {
    body = await request.json(); urlName = params.name as string;
    return HttpResponse.json({ ...S, ...body }, { status: 200 });
  }));
  wrap(<StorageDialog mode="edit" storage={S} trigger={<Button>수정</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "수정" }));

  const nameInput = screen.getByLabelText("스토리지 이름");
  expect(nameInput).toBeDisabled();
  expect(nameInput).toHaveValue(S.storage_name);
  const mountInput = screen.getByLabelText("마운트 경로");
  expect(mountInput).toHaveValue(S.mount_path);
  expect(screen.getByLabelText("관리 루트")).toHaveValue(S.managed_root);
  expect(screen.getByLabelText("백엔드")).toHaveValue(S.backend_type);

  await userEvent.clear(mountInput);
  await userEvent.type(mountInput, "/cephfs-new");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);

  expect(urlName).toBe(S.storage_name);
  expect(body).toEqual({
    mount_path: "/cephfs-new", managed_root: S.managed_root, backend_type: S.backend_type, enabled: true,
  });
});
