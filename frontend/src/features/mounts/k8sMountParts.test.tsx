import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { K8sMountStatusPage, K8sStorageMeta, K8sStorageTip, PvMountExplainer } from "./k8sMountParts";
import type { K8sColumn } from "./k8sMountParts";
import { LIVE, column } from "./mountFixtures";
import type { MountStatusResponse } from "./types";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const k8sCol = (name: string, kind: K8sColumn["kind"], pv: K8sColumn["pv"]): K8sColumn =>
  ({ ...column(name, `/x/${name}`), kind, pv });

// render 의 쿼리는 document.body 에 묶여 앞 렌더가 남는다 -- 이 렌더의 컨테이너만 본다.
function meta(col: K8sColumn | ReturnType<typeof column>) {
  return render(<div><K8sStorageMeta col={col} /></div>).container.firstElementChild as HTMLElement;
}
/** sr-only 를 뺀 보이는 글자(jsdom 은 CSS 를 모른다 -- 낭독 전용 조각을 직접 뺀다). */
function visibleText(el: Element): string {
  const clone = el.cloneNode(true) as Element;
  clone.querySelectorAll(".sr-only").forEach((n) => n.remove());
  return clone.textContent ?? "";
}
function tip(col: K8sColumn | ReturnType<typeof column>) {
  return render(<div><K8sStorageTip col={col} /></div>).container.firstElementChild as HTMLElement;
}

// 2026-11-25 압축: 열 머리는 고정 폭(6.5rem)이라 k8s 꼬리표는 한 줄 하나(종류)만 보인다 -- PV 수·클러스터는 꼬리표 안 sr-only 로
// 낭독하고(hover 전용이 아니게 -- 리뷰), 문장은 머리 툴팁·노드 시트.
test("머리 꼬리표는 종류 하나: kind 없음/null 은 파일시스템, PV 류는 PV·PV·파일시스템 + 낭독용 PV 수·클러스터(sr-only)", () => {
  expect(meta(k8sCol("a", null, null))).toHaveTextContent(/^파일시스템$/);
  expect(meta(k8sCol("b", "filesystem", null))).toHaveTextContent(/^파일시스템$/);
  const pv = meta(k8sCol("c", "pv", { count: 5, clusters: ["dmsk8s", "dmsk8s2"], storage_classes: ["c2-nfs", "tb-nfs"] }));
  const tag = pv.firstElementChild as HTMLElement;
  expect(visibleText(pv)).toBe("PV");
  expect(tag).toHaveClass("bg-infobg");
  expect(pv).toHaveTextContent(/^PV — 선택 가능한 PV 5개 · 클러스터 2$/);
  expect(within(tag).getByText("— 선택 가능한 PV 5개 · 클러스터 2")).toHaveClass("sr-only");
  expect(tag.querySelector("svg")).toBeNull();                          // 수를 아는 정상 PV 열에는 표지가 없다
  const both = meta(k8sCol("d", "both", { count: 2, clusters: ["dmsk8s"], storage_classes: ["tb-gpfs"] }));
  expect(visibleText(both)).toBe("PV·파일시스템");
  expect(both).toHaveTextContent("선택 가능한 PV 2개 · 클러스터 1");
  // 내장 title 툴팁은 달지 않는다(머리 툴팁과 겹친다)
  expect(pv.querySelector("[title]")).toBeNull();
});

test("PV 수 모름(null ≠ 0): 보이는 표지(범례 「모름」과 같은 물음표 아이콘) + 낭독 「선택 가능한 PV 모름」, 0개는 표지 없이 「0개」", () => {
  const unknown = meta(k8sCol("u", "pv", { count: null, clusters: ["dmsk8s"], storage_classes: ["tb-nfs"] }));
  const tag = unknown.firstElementChild as HTMLElement;
  expect(visibleText(unknown)).toBe("PV");
  expect(tag.querySelectorAll("svg")).toHaveLength(1);
  expect(tag).toHaveClass("bg-infobg");
  expect(unknown).toHaveTextContent(/^PV — 선택 가능한 PV 모름 · 클러스터 1$/);
  const zero = meta(k8sCol("z", "pv", { count: 0, clusters: [], storage_classes: ["tb-nfs"] }));
  expect((zero.firstElementChild as HTMLElement).querySelector("svg")).toBeNull();
  expect(zero).toHaveTextContent(/^PV — 선택 가능한 PV 0개$/);
  // 매핑 없음과 모름이 겹치면 ⚠ 하나만(PV 를 고를 수 없으니 수는 부차) -- 낭독은 둘 다
  const both = meta(k8sCol("w", "both", { count: null, clusters: [], storage_classes: [] }));
  expect((both.firstElementChild as HTMLElement).querySelectorAll("svg")).toHaveLength(1);
  expect(both).toHaveTextContent(/^PV·파일시스템 — StorageClass 매핑 없음 · 선택 가능한 PV 모름$/);
});

test("StorageClass 매핑이 없는 PV 열: 같은 꼬리표가 주의색 + 아이콘 + 낭독용 「StorageClass 매핑 없음」(색만으로 구분하지 않는다)", () => {
  const none = meta(k8sCol("e", "pv", { count: 0, clusters: [], storage_classes: [] }));
  expect(none).toHaveTextContent(/^PV — StorageClass 매핑 없음 · 선택 가능한 PV 0개$/);
  const tag = none.firstElementChild as HTMLElement;
  expect(tag).toHaveClass("bg-attnbg");
  expect(tag.querySelector("svg")).not.toBeNull();
  expect(within(tag).getByText(/StorageClass 매핑 없음/)).toHaveClass("sr-only");
});

test("kind 키가 없는 응답(dms-ssc 모양)이면 꼬리표·툴팁 덧붙임 모두 그리지 않는다", () => {
  expect(meta(column("ceph", "/cephfs")).textContent).toBe("");
  expect(tip(column("ceph", "/cephfs")).textContent).toBe("");
});

test("머리 툴팁 덧붙임: 종류·선택 가능한 PV(모름은 null ≠ 0)·클러스터·StorageClass(매핑 없음 설명)", () => {
  const pv = tip(k8sCol("c", "pv", { count: 5, clusters: ["dmsk8s", "dmsk8s2"], storage_classes: ["c2-nfs", "tb-nfs"] }));
  expect(pv).toHaveTextContent("종류PV");
  expect(pv).toHaveTextContent("선택 가능한 PV5개(Bound·StorageClass 매핑)");
  expect(pv).toHaveTextContent("클러스터dmsk8s, dmsk8s2");
  expect(pv).toHaveTextContent("StorageClassc2-nfs, tb-nfs");
  expect(tip(k8sCol("c", "pv", { count: null, clusters: [], storage_classes: ["tb-nfs"] })))
    .toHaveTextContent("선택 가능한 PV모름 — 한 번도 동기화되지 않은 클러스터가 있습니다");
  const none = tip(k8sCol("e", "pv", { count: 0, clusters: [], storage_classes: [] }));
  expect(none).toHaveTextContent("0개(Bound·StorageClass 매핑)");
  expect(within(none).getByText("매핑 없음")).toHaveClass("text-attn");
  expect(none).toHaveTextContent("PV 를 고를 수 없습니다");
  // 파일시스템 열은 종류만
  const fs = tip(k8sCol("f", "filesystem", null));
  expect(fs).toHaveTextContent(/^종류파일시스템$/);
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

test("K8sMountStatusPage: 열 머리에 종류 꼬리표(PV 수는 낭독만), PV 수·클러스터는 머리 툴팁과 노드 시트, 호출 노드는 「k8s 호출 주·보조」", async () => {
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
  const nfs = within(table).getByRole("columnheader", { name: /^k8s-pv-nfs/ });
  const kindTag = within(nfs).getByText("— 선택 가능한 PV 5개 · 클러스터 2").parentElement!;
  expect(within(nfs).getByText("— 선택 가능한 PV 5개 · 클러스터 2")).toHaveClass("sr-only");
  expect(visibleText(kindTag)).toBe("PV");                              // 보이는 머리에는 종류만(고정 폭)
  expect(within(table).getByRole("columnheader", { name: /^cephfs-dms/ })).toHaveTextContent("파일시스템");
  expect(within(table).getByText("k8s 호출 주")).toBeInTheDocument();
  expect(within(table).getByText("k8s 호출 보조")).toBeInTheDocument();
  expect(screen.getByText(/PV 스토리지도 노드 마운트가 전제입니다/)).toHaveTextContent("PV 스토리지의 마운트 경로를 hostPath 로");
  fireEvent.mouseEnter(nfs);
  const t = await screen.findByRole("tooltip");
  expect(t).toHaveTextContent("k8s-pv-nfs");
  expect(t).toHaveTextContent("5개(Bound·StorageClass 매핑)");
  expect(t).toHaveTextContent("dmsk8s, dmsk8s2");
  fireEvent.mouseLeave(nfs);
  // 키보드·터치 경로: 노드 버튼 → 시트의 스토리지 줄에 같은 덧붙임(열 머리 툴팁은 마우스 전용)
  await userEvent.click(within(table).getByRole("button", { name: "k8s-w1" }));
  const sheet = await screen.findByRole("dialog");
  const items = within(within(sheet).getByRole("list", { name: "k8s-w1 스토리지별 마운트" })).getAllByRole("listitem");
  const nfsItem = items.find((li) => li.textContent?.startsWith("k8s-pv-nfs"))!;
  expect(nfsItem).toHaveTextContent("선택 가능한 PV5개(Bound·StorageClass 매핑)");
  expect(nfsItem).toHaveTextContent("클러스터dmsk8s, dmsk8s2");
  expect(items.find((li) => li.textContent?.startsWith("cephfs-dms"))).toHaveTextContent("종류파일시스템");
});
