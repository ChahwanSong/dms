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
import { goStep, next } from "../../test/wizard";

// 배치 생성의 「PV 에서 경로 찾기」(2026-10-26 사용자 결정 (e)): PV 를 검색해 그 PV 의 직접 경로(관리 디렉토리 기준 root + 하위경로)를
// 표의 칸에 넣는다. 배치 바디는 그대로 직접 경로(스토리지 + 경로)다. 단계별 등록(2026-11-26)부터 ③ 항목 단계에 있다 -- 스토리지가 빈
// 채 ③ 에 가려면 ② 의 「스토리지를 CSV 로 정하기」를 켠다(넣기가 비어 있는 배치 스토리지를 그 PV 의 스토리지로 채운다).

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const STORAGES = [
  { storage_name: "cephfs-dms", backend_type: "cephfs", status: "Ready", kind: "filesystem" },
  { storage_name: "k8s-pv-gpfs", backend_type: "gpfs", status: "Ready", kind: "both" },
  { storage_name: "k8s-pv-nfs", backend_type: "nfs", status: "Ready", kind: "pv" },
];
const NFS_ROW = { ...VOLUME_MODELS, storage_class: "tb-nfs", pvc: "datasets", volume: "pvc-nfs-1",
                  storage_name: "k8s-pv-nfs", root: "team-a/datasets", abs_root: "/k8s_pv/nfs/team-a/datasets" };

function renderPage() {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(STORAGES)));
  server.use(http.get("/api/admin/policies", () => HttpResponse.json([])));
  server.use(http.get("/api/auth/me", () => HttpResponse.json({
    actor: "root", role: "admin", can_run_as_root: true, identity_mode: "k8s", unrestricted: true })));
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

/** ① 연산(기본 scan) → ② 「스토리지를 CSV 로 정하기」(스토리지를 비운 채) → ③ 항목. */
async function toItemsEmptyStorage(op?: "sync" | "rm") {
  if (op !== undefined) await userEvent.selectOptions(await screen.findByLabelText("연산"), op);
  await next("스토리지");
  await userEvent.click(screen.getByRole("button", { name: "스토리지를 CSV 로 정하기" }));
  await next("항목");
}
const storageValue = (label: string) => (screen.getByLabelText(label) as HTMLSelectElement).value;

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
  await toItemsEmptyStorage();
  const panel = await pickPv("models");
  await userEvent.type(within(panel).getByLabelText("PV 안 경로"), "sample");
  expect(within(panel).getByText(`${VOLUME_MODELS.root}/sample`)).toBeInTheDocument();
  expect(within(panel).getByText(`${VOLUME_MODELS.abs_root}/sample`)).toBeInTheDocument();
  await userEvent.click(within(panel).getByRole("button", { name: "경로 칸에 넣기" }));
  await waitFor(() => expect(screen.getByLabelText("1행 경로")).toHaveValue(`${VOLUME_MODELS.root}/sample`));
  // 두 번째 넣기는 다음 빈 칸이 없으니 새 행으로
  await userEvent.click(within(panel).getByRole("button", { name: "경로 칸에 넣기" }));
  await waitFor(() => expect(screen.getByLabelText("2행 경로")).toHaveValue(`${VOLUME_MODELS.root}/sample`));
  await goStep("스토리지");
  expect(storageValue("스토리지")).toBe("k8s-pv-gpfs");
});

test("배치 스토리지와 다른 스토리지의 PV 는 넣기가 잠기고 이유를 보인다", async () => {
  renderPage();
  await toItemsEmptyStorage();
  const panel = await pickPv("models");
  await userEvent.click(within(panel).getByRole("button", { name: "경로 칸에 넣기" }));
  await waitFor(() => expect(screen.getByLabelText("1행 경로")).toHaveValue(VOLUME_MODELS.root));
  await pickPv("datasets");
  expect(within(panel).getByRole("button", { name: "경로 칸에 넣기" })).toBeDisabled();
  expect(within(panel).getByText("배치 스토리지(k8s-pv-gpfs)와 다른 스토리지(k8s-pv-nfs)의 PV 입니다")).toBeInTheDocument();
});

test("sync: 소스·목적지 칸에 각각 넣는다(쌍의 스토리지가 각각 채워진다)", async () => {
  renderPage();
  await toItemsEmptyStorage("sync");
  const panel = await pickPv("datasets");
  await userEvent.click(within(panel).getByRole("button", { name: "소스 칸에 넣기" }));
  await pickPv("models");
  await userEvent.type(within(panel).getByLabelText("PV 안 경로"), "copy");
  await userEvent.click(within(panel).getByRole("button", { name: "목적지 칸에 넣기" }));
  await waitFor(() => expect(screen.getByLabelText("1행 목적지")).toHaveValue(`${VOLUME_MODELS.root}/copy`));
  expect(screen.getByLabelText("1행 소스")).toHaveValue("team-a/datasets");
  await goStep("스토리지");
  expect(storageValue("소스 스토리지")).toBe("k8s-pv-nfs");
  expect(storageValue("목적지 스토리지")).toBe("k8s-pv-gpfs");
});

test("rm: PV 전체(루트)는 넣지 않는다(PV 안 하위경로가 필요)", async () => {
  renderPage();
  await toItemsEmptyStorage("rm");
  const panel = await pickPv("models");
  expect(within(panel).getByRole("button", { name: "경로 칸에 넣기" })).toBeDisabled();
  expect(within(panel).getByText(/PV 전체\(루트\)는 삭제할 수 없습니다/)).toBeInTheDocument();
  await userEvent.type(within(panel).getByLabelText("PV 안 경로"), "old");
  expect(within(panel).getByRole("button", { name: "경로 칸에 넣기" })).toBeEnabled();
});

test("③ 항목에 패널이 늘 있다(옛 ldap 모드 분기 삭제 -- 배치 생성은 me 를 읽지 않는다) · 검색 칸 Enter 는 단계를 옮기지 않는다", async () => {
  renderPage();
  await toItemsEmptyStorage();
  const panel = screen.getByRole("region", { name: "PV 에서 경로 찾기" });
  await userEvent.type(within(panel).getByLabelText("PV 검색"), "models{enter}");
  expect(within(panel).getByLabelText("PV 검색")).toHaveFocus();
  expect(screen.getByRole("heading", { level: 2, name: "항목" })).toBeInTheDocument();
});
