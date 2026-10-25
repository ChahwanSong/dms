import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { reasonText } from "../../lib/api";
import { VolumesPage } from "./VolumesPage";
import { volumes } from "./k8sFixtures";
import type { VolumesResponse } from "./types";

// PV 탐색(설계 §7): 표(PV 이름·볼륨·「namespace (sc)」·상태·실제 경로·권한 사용자), 검색(디바운스 → ?q=)·필터(→ 쿼리),
// 주소 쿼리로 시작(?user=), 상세(실제 경로·권한 사용자·「이 PV로 단일 작업」 링크), 상한 안내, 빈 상태 셋(클러스터 없음·
// 동기화 전 = 모름·조건 0건)·오류 상태.

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrap(data: VolumesResponse | null = volumes(), at = "/admin/k8s/volumes") {
  const asked: URLSearchParams[] = [];
  if (data !== null) {
    server.use(http.get("/api/admin/k8s/volumes", ({ request }) => {
      asked.push(new URL(request.url).searchParams);
      return HttpResponse.json(data);
    }));
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter initialEntries={[at]}><VolumesPage /></MemoryRouter></QueryClientProvider>);
  return asked;
}

const rowOf = (pvc: string) => screen.getByText(pvc, { selector: "span.font-medium" }).closest("tr") as HTMLElement;

test("표: PV 이름 + 볼륨, namespace (StorageClass), 상태, 실제 경로, 권한 사용자 / 고를 수 없는 PV 는 사유", async () => {
  wrap();
  await screen.findByText("models", { selector: "span.font-medium" });
  const models = rowOf("models");
  expect(within(models).getByText("pvc-5c25f53f-aaaa")).toBeInTheDocument();
  expect(within(models).getByText("team-a (tb-gpfs)")).toBeInTheDocument();
  expect(within(models).getByText("Bound")).toBeInTheDocument();
  expect(within(models).getByText("/k8s_pv/gpfs/pvc-5c25f53f-aaaa/pvc-5c25f53f-aaaa-data")).toBeInTheDocument();
  expect(within(models).getByText("alice")).toBeInTheDocument();
  const pending = rowOf("pending-claim");
  expect(within(pending).getByText("Pending")).toBeInTheDocument();
  expect(within(pending).getByText(reasonText("pv_not_bound"))).toBeInTheDocument();
  expect(within(pending).getByText("없음")).toBeInTheDocument();                 // 권한 사용자 0명 = 「없음」
  expect(screen.getByText("스냅숏 기준")).toBeInTheDocument();
});

test("검색은 디바운스 뒤 ?q=, 필터는 쿼리로, 상한은 limit=200", async () => {
  const asked = wrap();
  await screen.findByText("models", { selector: "span.font-medium" });
  expect(asked[0].get("limit")).toBe("200");
  expect(asked[0].get("q")).toBeNull();
  await userEvent.type(screen.getByLabelText("PV 검색"), "models");
  await waitFor(() => expect(asked.some((p) => p.get("q") === "models")).toBe(true));
  await userEvent.selectOptions(screen.getByLabelText("클러스터 필터"), "dmsk8s");
  await waitFor(() => expect(asked.some((p) => p.get("cluster") === "dmsk8s" && p.get("q") === "models")).toBe(true));
  // namespace 선택지는 받은 행에서 모인다
  await userEvent.selectOptions(screen.getByLabelText("namespace 필터"), "team-c");
  await waitFor(() => expect(asked.some((p) => p.get("namespace") === "team-c")).toBe(true));
});

test("주소의 ?user= 로 시작하면 첫 조회부터 그 사용자로 거른다", async () => {
  const asked = wrap(volumes(), "/admin/k8s/volumes?user=alice");
  await screen.findByText("models", { selector: "span.font-medium" });
  expect(asked[0].get("user")).toBe("alice");
  expect(screen.getByLabelText("권한 사용자 필터")).toHaveValue("alice");
});

test("상세: 실제 경로·권한 사용자(권한 화면 링크)·「이 PV로 단일 작업」 링크", async () => {
  wrap();
  await screen.findByText("models", { selector: "span.font-medium" });
  await userEvent.click(within(rowOf("models")).getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog", { name: "PV 이름 models" });
  expect(within(dialog).getByText("/k8s_pv/gpfs/pvc-5c25f53f-aaaa/pvc-5c25f53f-aaaa-data")).toBeInTheDocument();
  expect(within(dialog).getByText("pvc-5c25f53f-aaaa/pvc-5c25f53f-aaaa-data")).toBeInTheDocument();
  expect(within(dialog).getByRole("link", { name: "junwoo.song" })).toHaveAttribute("href", "/admin/k8s/access?q=junwoo.song");
  expect(within(dialog).getByRole("link", { name: "이 PV로 단일 작업" }))
    .toHaveAttribute("href", "/jobs/new?pv=dmsk8s/team-a/models");
});

test("상세: 고를 수 없는 PV 는 단일 작업 버튼이 잠기고 사유를 보인다", async () => {
  wrap();
  await screen.findByText("pending-claim", { selector: "span.font-medium" });
  await userEvent.click(within(rowOf("pending-claim")).getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByRole("button", { name: "이 PV로 단일 작업" })).toBeDisabled();
  expect(within(dialog).queryByRole("link", { name: "이 PV로 단일 작업" })).not.toBeInTheDocument();
  expect(within(dialog).getAllByText(reasonText("pv_not_bound")).length).toBeGreaterThanOrEqual(1);
  expect(within(dialog).getByText("아직 없음(바인딩 전)")).toBeInTheDocument();
});

test("상한에 걸리면(truncated) 좁히라고 안내", async () => {
  wrap(volumes({ truncated: true }));
  expect(await screen.findByText(/결과가 많아 처음 200건만 보입니다/)).toBeInTheDocument();
});

test("빈 상태 셋: 클러스터 없음 / 동기화 전(모름) / 조건 0건을 다른 문구로", async () => {
  wrap(volumes({ clusters: [], volumes: [] }));
  expect(await screen.findByText(/등록된 클러스터가 없습니다 — 「개요」에서 클러스터를 등록하세요\./, { selector: "p.pt-3" }))
    .toBeInTheDocument();
});

test("빈 상태: 한 번도 동기화하지 않았으면 「모름」", async () => {
  wrap(volumes({ clusters: [{ name: "dmsk8s", synced_at: null, stale: false, status: "never" }], volumes: [] }));
  expect(await screen.findByText(/PV 목록은 모름입니다\(없다는 뜻이 아닙니다\)/)).toBeInTheDocument();
  expect(screen.getByText(/아직 동기화 안 됨\(모름\)/)).toBeInTheDocument();
});

test("빈 상태: 검색 결과 0건", async () => {
  server.use(http.get("/api/admin/k8s/volumes", ({ request }) =>
    HttpResponse.json(volumes(new URL(request.url).searchParams.get("q") ? { volumes: [] } : {}))));
  wrap(null);
  await screen.findByText("models", { selector: "span.font-medium" });
  await userEvent.type(screen.getByLabelText("PV 검색"), "nothing");
  expect(await screen.findByText("조건에 맞는 PV 가 없습니다")).toBeInTheDocument();
});

test("오류 상태: 조회 실패는 role=alert", async () => {
  server.use(http.get("/api/admin/k8s/volumes", () => HttpResponse.json({ detail: "x" }, { status: 500 })));
  wrap(null);
  expect(await screen.findByRole("alert")).toHaveTextContent("PV 목록을 불러오지 못했습니다");
});
