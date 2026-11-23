import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { K8sMountStatusPage, K8sStorageMeta, PvMountExplainer } from "./k8sMountParts";
import type { K8sColumn } from "./k8sMountParts";
import { LIVE, column } from "./mountFixtures";
import type { MountStatusResponse } from "./types";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const k8sCol = (name: string, kind: K8sColumn["kind"], pv: K8sColumn["pv"]): K8sColumn =>
  ({ ...column(name, `/x/${name}`), kind, pv });

function meta(col: K8sColumn | ReturnType<typeof column>) {
  // render 의 쿼리는 document.body 에 묶여 앞 렌더가 남는다 -- 이 렌더의 컨테이너만 본다.
  return render(<div><K8sStorageMeta col={col} /></div>).container.firstElementChild as HTMLElement;
}

test("종류 꼬리표: kind 없음/null 은 파일시스템, PV 류는 PV·PV·파일시스템", () => {
  expect(meta(k8sCol("a", null, null))).toHaveTextContent(/^파일시스템$/);
  expect(meta(k8sCol("b", "filesystem", null))).toHaveTextContent(/^파일시스템$/);
  expect(meta(k8sCol("c", "pv", { count: 5, clusters: ["dmsk8s", "dmsk8s2"], storage_classes: ["c2-nfs", "tb-nfs"] })))
    .toHaveTextContent("PVPV 5개 · 클러스터 2");
  expect(meta(k8sCol("d", "both", { count: 2, clusters: ["dmsk8s"], storage_classes: ["tb-gpfs"] })))
    .toHaveTextContent(/^PV·파일시스템PV 2개$/);
});

test("PV 수 모름(null)은 「PV 모름」, 매핑이 없으면 「StorageClass 매핑 없음」(기존 화면 용어 + 설명)", () => {
  expect(meta(k8sCol("c", "pv", { count: null, clusters: [], storage_classes: ["tb-nfs"] }))).toHaveTextContent("PV 모름");
  const none = meta(k8sCol("e", "pv", { count: 0, clusters: [], storage_classes: [] }));
  expect(none).toHaveTextContent("PV 0개");
  expect(within(none).getByText("StorageClass 매핑 없음")).toHaveAttribute("title", expect.stringContaining("PV 를 고를 수 없습니다"));
  expect(within(none).getByText("PV 0개")).toHaveAttribute("title", expect.stringContaining("선택 가능한 PV 0개"));
});

test("kind 키가 없는 응답(dms-ssc 모양)이면 아무것도 그리지 않는다", () => {
  expect(meta(column("ceph", "/cephfs")).textContent).toBe("");
});

test("PV 안내는 PV 열이 있을 때만", () => {
  const withPv: MountStatusResponse = { ...LIVE, storages: [k8sCol("nfs", "pv", { count: 1, clusters: [], storage_classes: [] })] };
  const { container, unmount } = render(<PvMountExplainer data={withPv} />);
  expect(container).toHaveTextContent(/PVC 를 붙이지 않고/);
  unmount();
  const fsOnly: MountStatusResponse = { ...LIVE, storages: [k8sCol("ceph", "filesystem", null)] };
  expect(render(<PvMountExplainer data={fsOnly} />).container.textContent).toBe("");
  expect(render(<PvMountExplainer data={LIVE} />).container.textContent).toBe("");
});

test("K8sMountStatusPage: 열 머리에 종류·PV 수, 호출 노드는 「k8s 호출 주·보조」", async () => {
  const body: MountStatusResponse = {
    ...LIVE,
    storages: LIVE.storages.map((c) => c.storage_name === "k8s-pv-nfs"
      ? { ...c, kind: "pv", pv: { count: 5, clusters: ["dmsk8s", "dmsk8s2"], storage_classes: ["c2-nfs", "tb-nfs"] } }
      : { ...c, kind: c.storage_name.startsWith("k8s-pv-gpfs") ? "both" : "filesystem", pv: null }) as MountStatusResponse["storages"],
  };
  server.use(http.get("/api/admin/mount-status", () => HttpResponse.json(body)));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><K8sMountStatusPage /></MemoryRouter>
    </QueryClientProvider>,
  );
  const table = await screen.findByRole("table");
  expect(within(table).getByRole("columnheader", { name: /^k8s-pv-nfs/ })).toHaveTextContent("PV 5개 · 클러스터 2");
  expect(within(table).getByRole("columnheader", { name: /^cephfs-dms/ })).toHaveTextContent("파일시스템");
  expect(within(table).getByText("k8s 호출 주")).toBeInTheDocument();
  expect(within(table).getByText("k8s 호출 보조")).toBeInTheDocument();
  expect(screen.getByText(/PV 스토리지도 노드 마운트가 전제입니다/)).toHaveTextContent("PV 스토리지의 마운트 경로를 hostPath 로");
});
