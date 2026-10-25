import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { reasonText } from "../../lib/api";
import { StorageClassesPage, needsAttention } from "./StorageClassesPage";
import { SC_ROWS } from "./k8sFixtures";
import type { StorageClassRow } from "./types";

// StorageClass 매핑(설계 D9·D10): 표(스토리지·종류·템플릿 출처·서버 계산 예시·중복 경고), 손볼 것 필터, 매핑 Dialog(종류
// pv·both 만 고를 수 있다 — kind 없음 = filesystem), 저장 바디, 해제(null), 빈 상태·오류 상태.

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const STORAGES = [
  { storage_name: "cephfs-dms", mount_path: "/cephfs", managed_root: "/cephfs/managed", backend_type: "cephfs",
    enabled: 1, status: "Ready", status_detail: null },                                   // kind 없음 = filesystem
  { storage_name: "k8s-pv-nfs", mount_path: "/k8s_pv/nfs", managed_root: "/k8s_pv/nfs", backend_type: "nfs",
    enabled: 1, status: "Ready", status_detail: null, kind: "pv" },
  { storage_name: "k8s-pv-gpfs", mount_path: "/k8s_pv/gpfs", managed_root: "/k8s_pv/gpfs", backend_type: "gpfs",
    enabled: 1, status: "Ready", status_detail: null, kind: "both" },
];

function wrap(rows: StorageClassRow[] | null = SC_ROWS) {
  if (rows !== null) server.use(http.get("/api/admin/k8s/storage-classes", () => HttpResponse.json(rows)));
  server.use(http.get("/api/admin/storages", () => HttpResponse.json(STORAGES)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}>
    <MemoryRouter initialEntries={["/admin/k8s/storageclasses"]}><StorageClassesPage /></MemoryRouter>
  </QueryClientProvider>);
}

const rowOf = (sc: string) => screen.getByText(sc, { selector: "span.font-medium" }).closest("tr") as HTMLElement;

test("표: 매핑·종류·템플릿 출처·서버 예시 경로·중복 경고", async () => {
  wrap();
  expect(await screen.findByRole("heading", { name: "StorageClass", level: 1 })).toBeInTheDocument();
  await screen.findByText("tb-gpfs");
  const gpfs = rowOf("tb-gpfs");
  expect(within(gpfs).getByText("k8s-pv-gpfs")).toBeInTheDocument();
  expect(within(gpfs).getByText("PV·파일시스템")).toBeInTheDocument();
  expect(within(gpfs).getByText("{volume}/{volume}-data")).toBeInTheDocument();
  expect(within(gpfs).getByText("백엔드 프리셋")).toBeInTheDocument();
  expect(within(gpfs).getByText("/k8s_pv/gpfs/pvc-5c25f53f-aaaa/pvc-5c25f53f-aaaa-data")).toBeInTheDocument();
  expect(within(gpfs).getByText("예: team-a/models")).toBeInTheDocument();
  const nfs = rowOf("tb-nfs");
  expect(within(nfs).getByText("매핑 없음")).toBeInTheDocument();
  expect(within(nfs).getByText("다른 클러스터에도: dmsk8s-dns")).toBeInTheDocument();
  expect(within(nfs).getByText("매핑하면 계산됩니다")).toBeInTheDocument();
});

test("손볼 것 필터: 매핑 없음·예시 경로 문제·규칙 없음만", async () => {
  expect(needsAttention(SC_ROWS[0])).toBe(false);
  expect(needsAttention(SC_ROWS[1])).toBe(true);
  const broken: StorageClassRow = { ...SC_ROWS[0], name: "tb-x",
    sample: { ...SC_ROWS[0].sample!, abs_root: null, problem: "pv_path_unresolved" } };
  expect(needsAttention(broken)).toBe(true);
  wrap([...SC_ROWS, broken]);
  await screen.findByText("tb-gpfs");
  expect(within(rowOf("tb-x")).getByText(reasonText("pv_path_unresolved"))).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "손볼 것 2" }));
  expect(screen.queryByText("tb-gpfs")).not.toBeInTheDocument();
  expect(screen.getByText("tb-nfs")).toBeInTheDocument();
  expect(screen.getByText("tb-x")).toBeInTheDocument();
});

test("매핑 Dialog: PV·둘 다 스토리지만 고를 수 있고, 저장은 PUT {storage_name, path_template}", async () => {
  let sent: { sc: string; body: unknown } | null = null;
  server.use(http.put("/api/admin/k8s/storage-classes/:sc", async ({ request, params }) => {
    sent = { sc: String(params.sc), body: await request.json() };
    return HttpResponse.json({});
  }));
  wrap();
  await screen.findByText("tb-nfs");
  await userEvent.click(within(rowOf("tb-nfs")).getByRole("button", { name: "매핑" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/같은 이름의 StorageClass 가 다른 클러스터\(dmsk8s-dns\)에도/)).toBeInTheDocument();
  const select = within(dialog).getByLabelText("대상 스토리지");
  expect(within(select).getByRole("option", { name: /k8s-pv-nfs · PV · nfs/ })).toBeEnabled();
  expect(within(select).getByRole("option", { name: /k8s-pv-gpfs · PV·파일시스템/ })).toBeEnabled();
  expect(within(select).getByRole("option", { name: "cephfs-dms" })).toBeDisabled();   // kind 없음 = 파일시스템
  const save = within(dialog).getByRole("button", { name: "저장" });
  expect(save).toBeDisabled();
  await userEvent.selectOptions(select, "k8s-pv-nfs");
  await userEvent.type(within(dialog).getByLabelText("경로 템플릿(덮어쓰기)"), "{{subdir}");
  await userEvent.click(save);
  await waitFor(() => expect(sent).toEqual({ sc: "tb-nfs", body: { storage_name: "k8s-pv-nfs", path_template: "{subdir}" } }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
});

test("매핑 해제: storage_name·path_template 모두 null, 해제 영향 안내", async () => {
  let body: unknown = null;
  server.use(http.put("/api/admin/k8s/storage-classes/:sc", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({});
  }));
  wrap();
  await screen.findByText("tb-gpfs");
  await userEvent.click(within(rowOf("tb-gpfs")).getByRole("button", { name: "편집" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByLabelText("경로 템플릿(덮어쓰기)"))
    .toHaveAttribute("placeholder", "비우면 백엔드 프리셋 — 지금 {volume}/{volume}-data");
  await userEvent.selectOptions(within(dialog).getByLabelText("대상 스토리지"), "");
  expect(within(dialog).getByText(/매핑을 해제하면 이 StorageClass 의 PVC 는/)).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "저장" }));
  await waitFor(() => expect(body).toEqual({ storage_name: null, path_template: null }));
});

test("서버 거절은 창 안에 사유로(창은 열린 채)", async () => {
  server.use(http.put("/api/admin/k8s/storage-classes/:sc", () =>
    HttpResponse.json({ detail: "invalid_pv_path_template" }, { status: 422 })));
  wrap();
  await screen.findByText("tb-gpfs");
  await userEvent.click(within(rowOf("tb-gpfs")).getByRole("button", { name: "편집" }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText("경로 템플릿(덮어쓰기)"), "../x");
  await userEvent.click(within(dialog).getByRole("button", { name: "저장" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent(reasonText("invalid_pv_path_template"));
});

test("빈 상태: 아직 받은 StorageClass 가 없다", async () => {
  wrap([]);
  expect(await screen.findByText(/아직 받은 StorageClass 가 없습니다/)).toBeInTheDocument();
});

test("오류 상태: 목록 조회 실패는 role=alert", async () => {
  server.use(http.get("/api/admin/k8s/storage-classes", () => HttpResponse.json({ detail: "x" }, { status: 500 })));
  wrap(null);
  expect(await screen.findByRole("alert")).toHaveTextContent("StorageClass 목록을 불러오지 못했습니다");
});
