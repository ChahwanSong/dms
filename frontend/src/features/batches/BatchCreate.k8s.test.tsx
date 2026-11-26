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
import { buttonsWithoutType, chooseOp, next, pickStorageCard, stepHeading, storageChecked } from "../../test/wizard";

// 배치 생성의 「PV 에서 경로 찾기」(2026-10-26 사용자 결정 (e)): PV 를 검색해 그 PV 의 직접 경로(관리 디렉토리 기준 root + 하위경로)를
// 표의 칸에 넣는다. 배치 바디는 그대로 직접 경로(스토리지 + 경로)다. 단계별 등록(2026-11-26)부터 오른쪽 **시트**(모달)이고 ② 스토리지
// 「PV 에서 찾아 정하기」·③ 항목 툴바 「PV 에서 경로 찾기」에서 연다. 넣어도 시트를 닫지 않고(여러 PV 를 이어 넣는다) 상태 줄이 넣은
// 행을 말한다. 시트는 모달이라 열린 동안 표 칸 포커스가 안 바뀐다 -- 넣은 직후 「마지막 포커스 칸」을 비워 두 번째 넣기가 같은 행을
// 덮지 않는다(첫 빈 칸 → 새 행).

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

const storageValue = (label: string) => (storageChecked(label) as HTMLInputElement | null)?.value ?? "";
const sheet = () => screen.findByRole("dialog", { name: "PV 에서 경로 찾기" });

/** ② 스토리지 단계의 「PV 에서 찾아 정하기」로 시트를 연다(스토리지가 빈 채로). */
async function openFromStorage(op?: "sync" | "rm") {
  if (op !== undefined) await chooseOp(op);
  await next("스토리지");
  await userEvent.click(screen.getByRole("button", { name: "PV 에서 찾아 정하기" }));
  return sheet();
}

async function pickPv(dlg: HTMLElement, pvc: string) {
  const list = await within(dlg).findByRole("radiogroup", { name: "PV 검색 결과 목록" });
  const label = within(list).getByText(pvc, { selector: "span.font-medium" }).closest("label")!;
  await userEvent.click(within(label).getByRole("radio"));
}

const closeSheet = async (dlg: HTMLElement) => {
  await userEvent.click(within(dlg).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
};

test("pvBatchPath: root + 정규화한 하위경로, root 를 모르면 null", () => {
  expect(pvBatchPath("team-a/datasets", "")).toBe("team-a/datasets");
  expect(pvBatchPath("team-a/datasets/", "./x//y/")).toBe("team-a/datasets/x/y");
  expect(pvBatchPath(null, "x")).toBeNull();
});

test("scan: ② 에서 연 시트로 넣으면 스토리지가 그 PV 의 스토리지로 채워지고 1행이 PV 의 직접 경로 — 시트는 열린 채 상태 줄, 닫으면 연 버튼으로", async () => {
  renderPage();
  const dlg = await openFromStorage();
  await pickPv(dlg, "models");
  await userEvent.type(within(dlg).getByLabelText("PV 안 경로"), "sample");
  expect(within(dlg).getByText(`${VOLUME_MODELS.root}/sample`)).toBeInTheDocument();
  expect(within(dlg).getByText(`${VOLUME_MODELS.abs_root}/sample`)).toBeInTheDocument();
  await userEvent.click(within(dlg).getByRole("button", { name: "경로 칸에 넣기" }));
  expect(within(dlg).getByRole("status")).toHaveTextContent("1행 경로 칸에 넣었습니다");
  // 두 번째 넣기는 다음 빈 칸이 없으니 새 행으로(시트는 열려 있다)
  await userEvent.click(within(dlg).getByRole("button", { name: "경로 칸에 넣기" }));
  expect(within(dlg).getByRole("status")).toHaveTextContent("2행 경로 칸에 넣었습니다");
  expect(screen.getByRole("dialog", { name: "PV 에서 경로 찾기" })).toBeInTheDocument();
  await closeSheet(dlg);
  expect(screen.getByRole("button", { name: "PV 에서 찾아 정하기" })).toHaveFocus();
  expect(storageValue("스토리지")).toBe("k8s-pv-gpfs");
  expect(stepHeading()).toHaveTextContent("스토리지");          // 자동으로 넘어가지 않는다
  await next("항목");
  expect(screen.getByLabelText("1행 경로")).toHaveValue(`${VOLUME_MODELS.root}/sample`);
  expect(screen.getByLabelText("2행 경로")).toHaveValue(`${VOLUME_MODELS.root}/sample`);
});

test("③ 에서 1행에 포커스한 뒤 시트를 열어 두 번 넣으면 1행·2행이 각각 채워진다(앞서 넣은 값이 덮이지 않는다)", async () => {
  renderPage();
  await next("스토리지");
  await pickStorageCard("스토리지", "k8s-pv-gpfs");
  await next("항목");
  await userEvent.click(screen.getByLabelText("1행 경로"));         // 마지막 포커스 칸 = 1행
  await userEvent.click(screen.getByRole("button", { name: "PV 에서 경로 찾기" }));
  const dlg = await sheet();
  await pickPv(dlg, "models");
  await userEvent.type(within(dlg).getByLabelText("PV 안 경로"), "one");
  await userEvent.click(within(dlg).getByRole("button", { name: "경로 칸에 넣기" }));
  expect(within(dlg).getByRole("status")).toHaveTextContent("1행 경로 칸에 넣었습니다");
  await userEvent.clear(within(dlg).getByLabelText("PV 안 경로"));
  await userEvent.type(within(dlg).getByLabelText("PV 안 경로"), "two");
  await userEvent.click(within(dlg).getByRole("button", { name: "경로 칸에 넣기" }));
  expect(within(dlg).getByRole("status")).toHaveTextContent("2행 경로 칸에 넣었습니다");
  await closeSheet(dlg);
  expect(screen.getByRole("button", { name: "PV 에서 경로 찾기" })).toHaveFocus();
  expect(screen.getByLabelText("1행 경로")).toHaveValue(`${VOLUME_MODELS.root}/one`);
  expect(screen.getByLabelText("2행 경로")).toHaveValue(`${VOLUME_MODELS.root}/two`);
});

test("③ 을 떠났다가 ② 에서 시트로 넣으면 ③ 에서 누른 칸을 덮지 않는다(첫 빈 칸 → 새 행)", async () => {
  renderPage();
  await next("스토리지");
  await pickStorageCard("스토리지", "k8s-pv-gpfs");
  await next("항목");
  await userEvent.type(screen.getByLabelText("1행 경로"), "keep");
  await userEvent.click(screen.getByRole("button", { name: "수정: 스토리지" }));
  const dlg = await (async () => {
    await userEvent.click(screen.getByRole("button", { name: "PV 에서 찾아 정하기" }));
    return sheet();
  })();
  await pickPv(dlg, "models");
  await userEvent.click(within(dlg).getByRole("button", { name: "경로 칸에 넣기" }));
  expect(within(dlg).getByRole("status")).toHaveTextContent("2행 경로 칸에 넣었습니다");
  await closeSheet(dlg);
  await next("항목");
  expect(screen.getByLabelText("1행 경로")).toHaveValue("keep");
  expect(screen.getByLabelText("2행 경로")).toHaveValue(VOLUME_MODELS.root);
});

test("배치 스토리지와 다른 스토리지의 PV 는 넣기가 잠기고 이유를 보인다", async () => {
  renderPage();
  const dlg = await openFromStorage();
  await pickPv(dlg, "models");
  await userEvent.click(within(dlg).getByRole("button", { name: "경로 칸에 넣기" }));
  await pickPv(dlg, "datasets");
  expect(within(dlg).getByRole("button", { name: "경로 칸에 넣기" })).toBeDisabled();
  expect(within(dlg).getByText("배치 스토리지(k8s-pv-gpfs)와 다른 스토리지(k8s-pv-nfs)의 PV 입니다")).toBeInTheDocument();
});

test("sync: 소스·목적지 칸에 각각 넣는다(쌍의 스토리지가 각각 채워진다)", async () => {
  renderPage();
  const dlg = await openFromStorage("sync");
  await pickPv(dlg, "datasets");
  await userEvent.click(within(dlg).getByRole("button", { name: "소스 칸에 넣기" }));
  expect(within(dlg).getByRole("status")).toHaveTextContent("1행 소스 칸에 넣었습니다");
  await pickPv(dlg, "models");
  await userEvent.type(within(dlg).getByLabelText("PV 안 경로"), "copy");
  await userEvent.click(within(dlg).getByRole("button", { name: "목적지 칸에 넣기" }));
  expect(within(dlg).getByRole("status")).toHaveTextContent("1행 목적지 칸에 넣었습니다");
  await closeSheet(dlg);
  expect(storageValue("소스 스토리지")).toBe("k8s-pv-nfs");
  expect(storageValue("목적지 스토리지")).toBe("k8s-pv-gpfs");
  await next("항목");
  expect(screen.getByLabelText("1행 소스")).toHaveValue("team-a/datasets");
  expect(screen.getByLabelText("1행 목적지")).toHaveValue(`${VOLUME_MODELS.root}/copy`);
});

test("rm: PV 전체(루트)는 넣지 않는다(PV 안 하위경로가 필요)", async () => {
  renderPage();
  const dlg = await openFromStorage("rm");
  await pickPv(dlg, "models");
  expect(within(dlg).getByRole("button", { name: "경로 칸에 넣기" })).toBeDisabled();
  expect(within(dlg).getByText(/PV 전체\(루트\)는 삭제할 수 없습니다/)).toBeInTheDocument();
  await userEvent.type(within(dlg).getByLabelText("PV 안 경로"), "old");
  expect(within(dlg).getByRole("button", { name: "경로 칸에 넣기" })).toBeEnabled();
});

test("②·③ 에 PV 찾기 진입점이 늘 있다(옛 ldap 모드 분기 삭제 -- 배치 생성은 me 를 읽지 않는다) · 시트의 검색 Enter 는 단계를 옮기지 않는다 · 시트 버튼도 type=button", async () => {
  renderPage();
  await next("스토리지");
  expect(screen.getByRole("button", { name: "PV 에서 찾아 정하기" })).toBeInTheDocument();
  await pickStorageCard("스토리지", "cephfs-dms");
  await next("항목");
  await userEvent.click(screen.getByRole("button", { name: "PV 에서 경로 찾기" }));
  const dlg = await sheet();
  await userEvent.type(within(dlg).getByLabelText("PV 검색"), "models{enter}");
  expect(within(dlg).getByLabelText("PV 검색")).toHaveFocus();
  expect(screen.getByRole("dialog", { name: "PV 에서 경로 찾기" })).toBeInTheDocument();
  expect(buttonsWithoutType(document.body)).toHaveLength(0);
  await closeSheet(dlg);
  expect(stepHeading()).toHaveTextContent("항목");
});
