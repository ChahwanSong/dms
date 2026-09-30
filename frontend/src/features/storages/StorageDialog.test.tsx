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
  await userEvent.selectOptions(screen.getByLabelText("백엔드"), "cephfs");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);
  // 사용 범위 기본 = 전체 사용(2026-09-30) -- 두 플래그가 명시로 실린다.
  expect(body).toEqual({ storage_name: "s1", mount_path: "/s1", managed_root: "/s1/dms", backend_type: "cephfs",
                         enabled: true, user_enabled: true });
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
    user_enabled: true,
  });
});


test("백엔드는 표시명으로 고르고 서버 식별자로 보낸다 -- 'IBM GPFS' 자유 입력 사고 재발 방지", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/storages", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body, { status: 201 }); }));
  wrap(<StorageDialog mode="create" trigger={<Button>등록</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  await userEvent.type(screen.getByLabelText("스토리지 이름"), "gpu1");
  await userEvent.type(screen.getByLabelText("마운트 경로"), "/home/gpu1");
  await userEvent.type(screen.getByLabelText("관리 루트"), "/home/gpu1");
  const select = screen.getByLabelText("백엔드") as HTMLSelectElement;
  expect(select.tagName).toBe("SELECT");
  await userEvent.selectOptions(select, "IBM GPFS (Storage Scale)");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);
  expect(body).toEqual({ storage_name: "gpu1", mount_path: "/home/gpu1", managed_root: "/home/gpu1",
                         backend_type: "gpfs", enabled: true, user_enabled: true });
});


test("추가 백엔드(DDN Lustre·Pure Storage·NetApp)도 표시명으로 고르고 식별자로 보낸다", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/storages", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body, { status: 201 }); }));
  wrap(<StorageDialog mode="create" trigger={<Button>등록</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  const select = screen.getByLabelText("백엔드") as HTMLSelectElement;
  const labels = Array.from(select.options).map((o) => o.textContent);
  expect(labels).toEqual(expect.arrayContaining(
    ["DDN Lustre (EXAScaler)", "Pure Storage (FlashBlade)", "NetApp (ONTAP)"]));
  await userEvent.type(screen.getByLabelText("스토리지 이름"), "lfs1");
  await userEvent.type(screen.getByLabelText("마운트 경로"), "/lustre");
  await userEvent.type(screen.getByLabelText("관리 루트"), "/lustre/dms");
  await userEvent.selectOptions(select, "DDN Lustre (EXAScaler)");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);
  expect(body).toEqual({ storage_name: "lfs1", mount_path: "/lustre", managed_root: "/lustre/dms",
                         backend_type: "lustre", enabled: true, user_enabled: true });
});


// 사용 범위(2026-09-30 사용자 요청: 완전 비활성 / 사용자에게만 비활성) -------------------

test("등록 때 관리자 전용을 고르면 user_enabled: false 로 보낸다", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/storages", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body, { status: 201 }); }));
  wrap(<StorageDialog mode="create" trigger={<Button>등록</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  expect(screen.getByLabelText("전체 사용")).toBeChecked();          // 기본
  await userEvent.type(screen.getByLabelText("스토리지 이름"), "s2");
  await userEvent.type(screen.getByLabelText("마운트 경로"), "/s2");
  await userEvent.type(screen.getByLabelText("관리 루트"), "/s2");
  await userEvent.selectOptions(screen.getByLabelText("백엔드"), "cephfs");
  await userEvent.click(screen.getByLabelText("관리자 전용"));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);
  expect(body).toMatchObject({ storage_name: "s2", enabled: true, user_enabled: false });
});

test("수정: 관리자 전용 행은 그 라디오로 시작하고, 완전 비활성은 user_enabled 를 생략한다", async () => {
  // 생략 = 서버가 현재 값 유지 -- 다시 켤 때 관리자 전용 설정이 그대로 돌아온다.
  const S: Storage = {
    storage_name: "s3", mount_path: "/s3", managed_root: "/s3", backend_type: "cephfs",
    enabled: 1, user_enabled: 0, status: "Ready", status_detail: null,
  };
  let body: any = null;
  server.use(http.put("/api/admin/storages/:name", async ({ request }) => {
    body = await request.json(); return HttpResponse.json({ ...S, ...body }, { status: 200 }); }));
  wrap(<StorageDialog mode="edit" storage={S} trigger={<Button>수정</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "수정" }));
  expect(screen.getByLabelText("관리자 전용")).toBeChecked();
  await userEvent.click(screen.getByLabelText("완전 비활성"));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);
  expect(body).toEqual({ mount_path: "/s3", managed_root: "/s3", backend_type: "cephfs", enabled: false });
});
