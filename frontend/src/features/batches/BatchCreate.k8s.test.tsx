import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchCreate } from "./BatchCreate";
import { VOLUME_MODELS, VOLUME_PENDING, volumes } from "../k8s/k8sFixtures";
import { pvBatchPath } from "../k8s/PvPathPicker";

// 배치 생성의 「PV 에서 경로 찾기」(2026-10-26 사용자 결정 (e)): k8s 모드에서만 사이드바에 보이고, PV 를 검색해 그 PV 의
// 직접 경로(관리 디렉토리 기준 root + 하위경로)를 표의 칸에 넣는다. 배치 바디는 그대로 직접 경로(스토리지 + 경로)다.

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const STORAGES = [
  { storage_name: "cephfs-dms", backend_type: "cephfs", status: "Ready", kind: "filesystem" },
  { storage_name: "k8s-pv-gpfs", backend_type: "gpfs", status: "Ready", kind: "both" },
  { storage_name: "k8s-pv-nfs", backend_type: "nfs", status: "Ready", kind: "pv" },
];
const NFS_ROW = { ...VOLUME_MODELS, storage_class: "tb-nfs", pvc: "datasets", volume: "pvc-nfs-1",
                  storage_name: "k8s-pv-nfs", root: "team-a/datasets", abs_root: "/k8s_pv/nfs/team-a/datasets" };

function renderPage(identityMode: "k8s" | "ldap" = "k8s") {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(STORAGES)));
  server.use(http.get("/api/admin/policies", () => HttpResponse.json([])));
  server.use(http.get("/api/auth/me", () => HttpResponse.json({
    actor: "root", role: "admin", can_run_as_root: true, identity_mode: identityMode, unrestricted: true })));
  server.use(http.get("/api/admin/k8s/volumes", () =>
    HttpResponse.json(volumes({ volumes: [VOLUME_MODELS, NFS_ROW, VOLUME_PENDING] }))));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/batches/new"]}>
        <Routes><Route path="/admin/batches/new" element={<BatchCreate />} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

async function pickPv(pvc: string) {
  const panel = await screen.findByRole("region", { name: "PV 에서 경로 찾기" });
  const list = await within(panel).findByRole("radiogroup", { name: "PV 검색 결과 목록" });
  const label = within(list).getByText(pvc, { selector: "span.font-medium" }).closest("label")!;
  await userEvent.click(within(label).getByRole("radio"));
  return panel;
}

test("pvBatchPath: root + 정규화한 하위경로, root 를 모르면 null", () => {
  expect(pvBatchPath("team-a/datasets", "")).toBe("team-a/datasets");
  expect(pvBatchPath("team-a/datasets/", "./x//y/")).toBe("team-a/datasets/x/y");
  expect(pvBatchPath(null, "x")).toBeNull();
});

test("scan: PV 를 골라 넣으면 스토리지가 그 PV 의 스토리지로 채워지고 1행 경로가 PV 의 직접 경로가 된다", async () => {
  renderPage();
  const panel = await pickPv("models");
  await userEvent.type(within(panel).getByLabelText("PV 안 경로"), "sample");
  expect(within(panel).getByText(`${VOLUME_MODELS.root}/sample`)).toBeInTheDocument();
  expect(within(panel).getByText(`${VOLUME_MODELS.abs_root}/sample`)).toBeInTheDocument();
  await userEvent.click(within(panel).getByRole("button", { name: "경로 칸에 넣기" }));
  await waitFor(() => expect(screen.getByLabelText("1행 경로")).toHaveValue(`${VOLUME_MODELS.root}/sample`));
  expect(screen.getByLabelText("스토리지")).toHaveValue("k8s-pv-gpfs");
  // 두 번째 넣기는 다음 빈 칸이 없으니 새 행으로
  await userEvent.click(within(panel).getByRole("button", { name: "경로 칸에 넣기" }));
  await waitFor(() => expect(screen.getByLabelText("2행 경로")).toHaveValue(`${VOLUME_MODELS.root}/sample`));
});

test("배치 스토리지와 다른 스토리지의 PV 는 넣기가 잠기고 이유를 보인다", async () => {
  renderPage();
  const panel = await pickPv("models");
  await userEvent.click(within(panel).getByRole("button", { name: "경로 칸에 넣기" }));
  await waitFor(() => expect(screen.getByLabelText("스토리지")).toHaveValue("k8s-pv-gpfs"));
  await pickPv("datasets");
  expect(within(panel).getByRole("button", { name: "경로 칸에 넣기" })).toBeDisabled();
  expect(within(panel).getByText("배치 스토리지(k8s-pv-gpfs)와 다른 스토리지(k8s-pv-nfs)의 PV 입니다")).toBeInTheDocument();
});

test("sync: 소스·목적지 칸에 각각 넣는다(쌍의 스토리지가 각각 채워진다)", async () => {
  renderPage();
  await userEvent.selectOptions(await screen.findByLabelText("연산"), "sync");
  const panel = await pickPv("datasets");
  await userEvent.click(within(panel).getByRole("button", { name: "소스 칸에 넣기" }));
  await pickPv("models");
  await userEvent.type(within(panel).getByLabelText("PV 안 경로"), "copy");
  await userEvent.click(within(panel).getByRole("button", { name: "목적지 칸에 넣기" }));
  await waitFor(() => expect(screen.getByLabelText("1행 목적지")).toHaveValue(`${VOLUME_MODELS.root}/copy`));
  expect(screen.getByLabelText("1행 소스")).toHaveValue("team-a/datasets");
  expect(screen.getByLabelText("소스 스토리지")).toHaveValue("k8s-pv-nfs");
  expect(screen.getByLabelText("목적지 스토리지")).toHaveValue("k8s-pv-gpfs");
});

test("rm: PV 전체(루트)는 넣지 않는다(PV 안 하위경로가 필요)", async () => {
  renderPage();
  await userEvent.selectOptions(await screen.findByLabelText("연산"), "rm");
  const panel = await pickPv("models");
  expect(within(panel).getByRole("button", { name: "경로 칸에 넣기" })).toBeDisabled();
  expect(within(panel).getByText(/PV 전체\(루트\)는 삭제할 수 없습니다/)).toBeInTheDocument();
  await userEvent.type(within(panel).getByLabelText("PV 안 경로"), "old");
  expect(within(panel).getByRole("button", { name: "경로 칸에 넣기" })).toBeEnabled();
});

test("ldap 모드에는 패널이 없다", async () => {
  renderPage("ldap");
  await screen.findByLabelText("스토리지");
  expect(screen.queryByRole("region", { name: "PV 에서 경로 찾기" })).toBeNull();
});
