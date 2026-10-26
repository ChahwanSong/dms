import { render, screen, waitFor, within } from "@testing-library/react";
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
  // 종류는 늘 싣는다(기본 파일시스템 -- 2026-10-26 k8s 신원 모드 전용).
  expect(body).toEqual({ storage_name: "s1", mount_path: "/s1", managed_root: "/s1/dms", backend_type: "cephfs",
                         enabled: true, user_enabled: true, kind: "filesystem" });
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
    user_enabled: true, kind: "filesystem",
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
                         backend_type: "gpfs", enabled: true, user_enabled: true, kind: "filesystem" });
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
                         backend_type: "lustre", enabled: true, user_enabled: true, kind: "filesystem" });
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
  expect(body).toEqual({ mount_path: "/s3", managed_root: "/s3", backend_type: "cephfs", enabled: false, kind: "filesystem" });
});


// ---- 스토리지 종류(dms-k8s 설계 D11) ----------------------------------------------------------------

test("백엔드 선택지에 onefs·nfs 가 있다(서버 _BACKENDS 와 같은 셋 -- 공용 줄)", async () => {
  wrap(<StorageDialog mode="create" trigger={<Button>등록</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  const select = screen.getByLabelText("백엔드") as HTMLSelectElement;
  expect(Array.from(select.options).map((o) => o.value)).toEqual(
    ["", "cephfs", "gpfs", "wekafs", "lustre", "purestorage", "netapp", "onefs", "nfs"]);
  // 종류 라디오는 늘 있다(옛 ldap 모드의 「종류 없음」 화면은 2026-10-26 에 지웠다)
  expect(screen.getByRole("group", { name: "종류" })).toBeInTheDocument();
});

test("등록: 종류 라디오(파일시스템 / PV / 둘 다), PV·둘 다면 백엔드 프리셋을 읽기 전용으로, 바디에 kind", async () => {
  let body: any = null;
  server.use(http.post("/api/admin/storages", async ({ request }) => {
    body = await request.json(); return HttpResponse.json(body, { status: 201 }); }));
  wrap(<StorageDialog mode="create" trigger={<Button>등록</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "등록" }));
  const kinds = screen.getByRole("group", { name: "종류" });
  expect(within(kinds).getByLabelText("파일시스템")).toBeChecked();
  expect(within(kinds).getByText(/직접 경로\(스토리지 \+ 관리 디렉토리 아래 상대경로\)로만 씁니다/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("스토리지 이름"), "k8s-pv-gpfs");
  await userEvent.type(screen.getByLabelText("마운트 경로"), "/k8s_pv/gpfs");
  await userEvent.type(screen.getByLabelText("관리 루트"), "/k8s_pv/gpfs");
  await userEvent.click(within(kinds).getByLabelText("둘 다"));
  expect(within(kinds).getByText("백엔드를 고르면 PV 경로 규칙(프리셋)이 보입니다.")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("백엔드"), "gpfs");
  expect(within(kinds).getByText("{volume}/{volume}-data", { selector: "code" })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("백엔드"), "onefs");
  expect(within(kinds).getByText("data/csi/{volume}", { selector: "code" })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("백엔드"), "nfs");
  expect(within(kinds).getByText("{subdir}", { selector: "code" })).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("백엔드"), "wekafs");
  expect(within(kinds).getByText(/StorageClass 화면에서 템플릿 지정 필요/)).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("백엔드"), "gpfs");
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await screen.findByText(/./);
  await waitFor(() => expect(body).toEqual({ storage_name: "k8s-pv-gpfs", mount_path: "/k8s_pv/gpfs",
    managed_root: "/k8s_pv/gpfs", backend_type: "gpfs", enabled: true, user_enabled: true, kind: "both" }));
});

test("수정: 지금 종류로 시작하고 연결된 StorageClass 를 보이며, 바꾼 종류를 싣는다", async () => {
  const S = { storage_name: "k8s-pv-nfs", mount_path: "/k8s_pv/nfs", managed_root: "/k8s_pv/nfs", backend_type: "nfs",
              enabled: 1, status: "Ready", status_detail: null, kind: "pv" } as unknown as Storage;
  let body: any = null;
  server.use(
    http.put("/api/admin/storages/:name", async ({ request }) => {
      body = await request.json(); return HttpResponse.json(S); }),
    http.get("/api/admin/k8s/storage-classes", () => HttpResponse.json([
      { cluster: "dmsk8s", name: "tb-nfs", storage_name: "k8s-pv-nfs" },
      { cluster: "dmsk8s", name: "tb-gpfs", storage_name: "k8s-pv-gpfs" }])),
  );
  wrap(<StorageDialog mode="edit" storage={S} trigger={<Button>수정</Button>} />);
  await userEvent.click(screen.getByRole("button", { name: "수정" }));
  const kinds = screen.getByRole("group", { name: "종류" });
  expect(within(kinds).getByLabelText("PV")).toBeChecked();
  expect(await within(kinds).findByText("연결된 StorageClass: tb-nfs (dmsk8s)")).toBeInTheDocument();
  await userEvent.click(within(kinds).getByLabelText("둘 다"));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  await waitFor(() => expect(body).toEqual({ mount_path: "/k8s_pv/nfs", managed_root: "/k8s_pv/nfs", backend_type: "nfs",
    enabled: true, user_enabled: true, kind: "both" }));
});
