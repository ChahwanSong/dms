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
    // 서버 모양(routes_k8s._sample_view): reason = 사유 코드, problem = 영문 진단.
    sample: { ...SC_ROWS[0].sample!, abs_root: null, reason: "pv_path_unresolved",
              problem: "PV claimRef is team-b/other" } };
  expect(needsAttention(broken)).toBe(true);
  wrap([...SC_ROWS, broken]);
  await screen.findByText("tb-gpfs");
  // 예시 칸의 사유는 관리자 시점의 조치다(2026-11-11) -- 제출 화면용 「관리자에게 … 요청하세요」를 그 일을 할 관리자에게 보이지
  // 않는다(예전 단언은 reasonText 재사용을 고정했다)
  expect(within(rowOf("tb-x")).getByText(
    "이 PV 의 실제 경로를 계산할 수 없습니다(경로 템플릿·PV 정보 부족) — 경로 템플릿을 확인하세요")).toBeInTheDocument();
  expect(within(rowOf("tb-x")).queryByText(/관리자에게/)).toBeNull();
  expect(within(rowOf("tb-x")).getByText("PV claimRef is team-b/other")).toBeInTheDocument();
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

test("매핑 창: 스토리지 목록을 못 받았으면 「PV 를 담을 수 없는 종류」·「스토리지가 없다」고 단정하지 않는다(2026-11-11)", async () => {
  server.use(http.get("/api/admin/k8s/storage-classes", () => HttpResponse.json(SC_ROWS)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  server.use(http.get("/api/admin/storages", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  render(<QueryClientProvider client={qc}>
    <MemoryRouter initialEntries={["/admin/k8s/storageclasses"]}><StorageClassesPage /></MemoryRouter>
  </QueryClientProvider>);
  await screen.findByText("tb-gpfs");
  await screen.findByText(/스토리지 목록을 불러오지 못해 매핑 선택지가 비어 있습니다/);
  // 표의 종류 배지는 서버 storage_kind 그대로
  expect(within(rowOf("tb-gpfs")).getByText("PV·파일시스템")).toBeInTheDocument();
  await userEvent.click(within(rowOf("tb-gpfs")).getByRole("button", { name: "편집" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/스토리지 목록을 불러오지 못해 고를 수 있는 스토리지를 보일 수 없습니다/)).toBeInTheDocument();
  expect(within(dialog).queryByText(/스토리지가 아직 없습니다/)).toBeNull();
  // 지금 매핑은 한 옵션으로 남는다(값을 잃지 않게) -- 종류는 단정하지 않는다
  const select = within(dialog).getByLabelText("대상 스토리지");
  expect(within(select).getByRole("option", { name: "k8s-pv-gpfs (지금 매핑)" })).toBeInTheDocument();
  expect(within(dialog).queryByText(/PV 를 담을 수 없는 종류/)).toBeNull();
  expect(select).toHaveValue("k8s-pv-gpfs");
});

test("매핑 창: 매핑된 스토리지가 지워졌으면(storage_kind null) 「목록에 없음」, 종류가 바뀌었으면 「PV 를 담을 수 없는 종류」", async () => {
  const gone: StorageClassRow = { ...SC_ROWS[0], name: "tb-gone", storage_name: "ghost", storage_kind: null };
  const fs: StorageClassRow = { ...SC_ROWS[0], name: "tb-fs", storage_name: "cephfs-dms", storage_kind: "filesystem" };
  wrap([gone, fs]);
  await screen.findByText("tb-gone");
  await userEvent.click(within(rowOf("tb-gone")).getByRole("button", { name: "편집" }));
  let dialog = await screen.findByRole("dialog");
  expect(within(within(dialog).getByLabelText("대상 스토리지"))
    .getByRole("option", { name: "ghost (지금 매핑 — 스토리지 목록에 없음(지워졌을 수 있음))" })).toBeInTheDocument();
  await userEvent.click(within(dialog).getByRole("button", { name: "취소" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await userEvent.click(within(rowOf("tb-fs")).getByRole("button", { name: "편집" }));
  dialog = await screen.findByRole("dialog");
  expect(within(within(dialog).getByLabelText("대상 스토리지"))
    .getByRole("option", { name: "cephfs-dms (지금 매핑 — PV 를 담을 수 없는 종류)" })).toBeInTheDocument();
});

test("어느 클러스터에도 보이지 않는 매핑 행(cluster null): 클러스터 칸·창에 이유를 적고 「손볼 것」에 넣는다(2026-11-11)", async () => {
  const orphan: StorageClassRow = { ...SC_ROWS[0], cluster: null, name: "old-sc", provisioner: null, parameters: {},
                                    pvc_count: 0, sample: null, duplicate_in: [] };
  expect(needsAttention(orphan)).toBe(true);
  wrap([SC_ROWS[0], orphan]);
  await screen.findByText("old-sc");
  expect(within(rowOf("old-sc")).getByText("어느 클러스터에도 보이지 않음 — 매핑 해제 가능")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "손볼 것 1" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "정상 1" })).toBeInTheDocument();
  await userEvent.click(within(rowOf("old-sc")).getByRole("button", { name: "편집" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("어느 클러스터에도 보이지 않음 — 매핑 해제 가능")).toBeInTheDocument();
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
