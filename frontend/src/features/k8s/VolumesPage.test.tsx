import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
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
  render(<QueryClientProvider client={qc}>
    <MemoryRouter initialEntries={[at]}><VolumesPage /><LocationProbe /></MemoryRouter>
  </QueryClientProvider>);
  return asked;
}

// 지금 주소의 쿼리(화면과 주소가 맞는지 -- 2026-11-11)
function LocationProbe() {
  return <output data-testid="location-search">{useLocation().search}</output>;
}
const locationSearch = () => screen.getByTestId("location-search").textContent;

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

test("관리자 화면의 사유는 관리자 시점 -- 「관리자에게 요청하세요」 대신 StorageClass 탭 링크(2026-11-11)", async () => {
  const unmapped = { ...volumes().volumes[0], pvc: "dms-pg-1", storage_class: "local-path", storage_name: null, root: null,
                     abs_root: null, selectable: false, reason: "pv_storageclass_unmapped" };
  const unresolved = { ...volumes().volumes[0], pvc: "odd", root: null, abs_root: null, selectable: false,
                       reason: "pv_path_unresolved" };
  wrap(volumes({ volumes: [unmapped, unresolved] }));
  await screen.findByText("dms-pg-1", { selector: "span.font-medium" });
  const row = rowOf("dms-pg-1");
  expect(within(row).queryByText(/관리자에게/)).toBeNull();
  const cell = within(row).getByRole("link", { name: "StorageClass 탭" });
  expect(cell).toHaveAttribute("href", "/admin/k8s/storageclasses");
  expect(cell.closest("span")).toHaveTextContent(
    "이 PV 의 StorageClass 가 DMS 스토리지에 매핑돼 있지 않습니다 — StorageClass 탭에서 이 StorageClass 를 DMS 스토리지에 매핑하세요");
  expect(within(rowOf("odd")).getByRole("link", { name: "StorageClass 탭" }).closest("span"))
    .toHaveTextContent("이 PV 의 실제 경로를 계산할 수 없습니다(경로 템플릿·PV 정보 부족) — StorageClass 탭에서 이 StorageClass 의 경로 템플릿을 확인하세요");
  // 상세 창(실제 경로 칸·아래 사유)도 같은 문구
  await userEvent.click(within(row).getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).queryByText(/관리자에게/)).toBeNull();
  expect(within(dialog).getAllByRole("link", { name: "StorageClass 탭" })).toHaveLength(2);
});

test("스냅숏 기준 줄: 경고 꼬리까지 한 줄로 묶지 않는다 -- 좁은 폭에서 본문을 넘지 않게(2026-11-11)", async () => {
  wrap(volumes({ clusters: [{ name: "gpu-train-prod-a", synced_at: "2026-10-24T00:00:00Z", stale: true, status: "delayed" }] }));
  const name = await screen.findByText("gpu-train-prod-a", { selector: "span.font-medium" });
  const item = name.parentElement as HTMLElement;
  expect(item).toHaveTextContent(/gpu-train-prod-a .* 성공 · 오래됨 · 이후 변경은 반영 안 됐을 수 있음 · 지연/);
  expect(item).not.toHaveClass("whitespace-nowrap");
  expect(item).toHaveClass("min-w-0");
  expect(item.className).toContain("[overflow-wrap:anywhere]");
  // 「n분 전 성공」만 한 덩어리
  expect(within(item).getByText(/성공$/)).toHaveClass("whitespace-nowrap");
});

test("주소가 상태다: 필터를 바꾸면 주소에 남고, ?user= 로 들어온 뒤 탭 「PV 탐색」을 누르면 조건 없이 다시 시작한다(2026-11-11)", async () => {
  const asked = wrap(volumes(), "/admin/k8s/volumes?user=alice");
  await screen.findByText("models", { selector: "span.font-medium" });
  expect(locationSearch()).toBe("?user=alice");
  await userEvent.selectOptions(screen.getByLabelText("클러스터 필터"), "dmsk8s");
  await waitFor(() => expect(locationSearch()).toBe("?user=alice&cluster=dmsk8s"));
  // 탭을 누르면(같은 경로 -- 라우터가 다시 마운트하지 않는다) 주소와 화면이 같이 빈 조건이 된다
  await userEvent.click(screen.getByRole("link", { name: "PV 탐색" }));
  await waitFor(() => expect(screen.getByLabelText("권한 사용자 필터")).toHaveValue(""));
  expect(screen.getByLabelText("클러스터 필터")).toHaveValue("");
  expect(locationSearch()).toBe("");
  await waitFor(() => {
    const last = asked[asked.length - 1];
    expect(last.get("user")).toBeNull();
    expect(last.get("cluster")).toBeNull();
  });
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

const meAs = (actor: string, unrestricted: boolean) =>
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor, role: "admin", identity_mode: "k8s", unrestricted })));

async function openModels() {
  await screen.findByText("models", { selector: "span.font-medium" });
  await userEvent.click(within(rowOf("models")).getByRole("button", { name: "상세" }));
  return screen.findByRole("dialog", { name: "PV 이름 models" });
}

test("상세: 전체 PV 권한이 없는 관리자는 자기 맵핑이 없는 PV 에서 단일 작업이 잠기고 이유를 보인다(2026-10-26)", async () => {
  meAs("ops2", false);
  wrap();
  const dialog = await openModels();
  await waitFor(() => expect(within(dialog).getByRole("button", { name: "이 PV로 단일 작업" })).toBeDisabled());
  expect(within(dialog).getByText(/이 PV 에 대한 권한이 없습니다 — team-a 에 ops2-clusterrole-edit\|admin 또는 namespaceAdmin/))
    .toBeInTheDocument();
});

test("상세: 그 namespace 에 맵핑이 있는 관리자는 단일 작업 링크가 열린다", async () => {
  meAs("junwoo.song", false);
  wrap();
  const dialog = await openModels();
  expect(await within(dialog).findByRole("link", { name: "이 PV로 단일 작업" })).toBeInTheDocument();
});

test("상세: 전체 PV 권한 관리자는 맵핑과 무관하게 단일 작업 링크가 열린다", async () => {
  meAs("root", true);
  wrap();
  const dialog = await openModels();
  expect(await within(dialog).findByRole("link", { name: "이 PV로 단일 작업" })).toBeInTheDocument();
});

test("상한에 걸리면(truncated) 좁히라고 안내", async () => {
  wrap(volumes({ truncated: true }));
  expect(await screen.findByText(/결과가 많아 처음 200건만 보입니다/)).toBeInTheDocument();
});

test("빈 상태 셋: 클러스터 없음 / 동기화 전(모름) / 조건 0건을 다른 문구로", async () => {
  wrap(volumes({ clusters: [], volumes: [] }));
  // 표의 빈 상태는 클러스터 탭 링크(2026-11-11 개요에서 분리) 한 줄뿐 -- 스냅숏 기준 줄의 위치 글(단일 작업 화면용)은 끈다(같은
  // 지시가 한 화면에 두 문구로 겹치지 않게, 2026-11-11 리뷰)
  const link = await screen.findByRole("link", { name: "클러스터 탭" });
  expect(link).toHaveAttribute("href", "/admin/k8s/clusters");
  expect(link.closest("p")).toHaveTextContent("등록된 클러스터가 없습니다 — 클러스터 탭에서 클러스터를 등록하세요.");
  expect(screen.getAllByText(/등록된 클러스터가 없습니다/)).toHaveLength(1);
  expect(screen.queryByText(/「k8s 연동 → 클러스터」/)).toBeNull();
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
