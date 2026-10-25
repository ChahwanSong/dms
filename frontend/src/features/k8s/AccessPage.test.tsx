import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { AccessPage, pivotByNamespace } from "./AccessPage";
import { ACCESS } from "./k8sFixtures";
import type { AccessResponse } from "./types";

// 사용자 권한(설계 §7·D5): 사용자별·namespace별, 근거 바인딩 원문, subject 불일치·계정 없음 경고, 계정 만들기 링크,
// 인식하지 않은 바인딩 접힘, ?q= 로 시작·주소에 남김, 빈 상태(동기화 전 = 모름)·오류 상태.

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function Where() {
  const loc = useLocation();
  return <output data-testid="where">{`${loc.pathname}${loc.search}`}</output>;
}

function wrap(data: AccessResponse | null = ACCESS, at = "/admin/k8s/access") {
  const asked: (string | null)[] = [];
  if (data !== null) {
    server.use(http.get("/api/admin/k8s/access", ({ request }) => {
      asked.push(new URL(request.url).searchParams.get("q"));
      return HttpResponse.json(data);
    }));
  }
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}>
    <MemoryRouter initialEntries={[at]}><AccessPage /><Where /></MemoryRouter>
  </QueryClientProvider>);
  return asked;
}

const userRow = (id: string) => screen.getByText(id, { selector: "span.font-semibold" }).closest("tr") as HTMLElement;

test("사용자별: 근거 바인딩 원문·권한 수준·PV 개수 링크", async () => {
  wrap();
  await screen.findByText("junwoo.song", { selector: "span.font-semibold" });
  const jw = userRow("junwoo.song");
  expect(within(jw).getByText("junwoo-song-clusterrole-admin")).toBeInTheDocument();
  expect(within(jw).getByText("→ ClusterRole/kubeflow-admin · subject User junwoo.song@example.com")).toBeInTheDocument();
  expect(within(jw).getByText("admin")).toBeInTheDocument();
  expect(within(jw).getByText("계정 있음")).toBeInTheDocument();
  expect(within(jw).getByRole("link", { name: "3개" })).toHaveAttribute("href", "/admin/k8s/volumes?user=junwoo.song");
  // alice 는 두 namespace -- 두 줄(사용자 칸은 rowSpan)
  expect(userRow("alice").querySelector("td")).toHaveAttribute("rowspan", "2");
  expect(screen.getByText("alice-clusterrole-admin")).toBeInTheDocument();
});

test("경고: 계정 없음 + 계정 만들기 링크, subject 불일치 문장", async () => {
  wrap();
  await screen.findByText("user.yo.hong.example.com", { selector: "span.font-semibold" });
  const yo = userRow("user.yo.hong.example.com");
  expect(within(yo).getByText("계정 없음")).toBeInTheDocument();
  expect(within(yo).getByRole("link", { name: "계정 만들기" }))
    .toHaveAttribute("href", "/admin/accounts?create=user.yo.hong.example.com");
  expect(within(yo).getByText(/subject 불일치 — 바인딩 subject 의 이름·이메일이/)).toBeInTheDocument();
  // 요약 줄에 개수
  expect(screen.getByText("계정 없음 1")).toBeInTheDocument();
  expect(screen.getByText("subject 불일치 1")).toBeInTheDocument();
});

test("namespace별 보기: 뒤집어서 namespace 마다 사용자", async () => {
  const groups = pivotByNamespace(ACCESS.users);
  expect(groups.map((g) => `${g.namespace}:${g.entries.map((e) => e.user.dms_user).join(",")}`))
    .toEqual(["team-a:alice,junwoo.song,user.yo.hong.example.com", "team-b:alice"]);
  wrap();
  await screen.findByText("junwoo.song", { selector: "span.font-semibold" });
  await userEvent.click(screen.getByRole("button", { name: "namespace별" }));
  expect(screen.getByText("사용자 3명")).toBeInTheDocument();
  expect(screen.getByText("사용자 1명")).toBeInTheDocument();
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("view=namespace"));
});

test("인식하지 않은 바인딩은 접혀 있고 펼치면 이유를 보인다", async () => {
  wrap();
  await screen.findByText("junwoo.song", { selector: "span.font-semibold" });
  const summary = screen.getByText("인식하지 않은 바인딩 2건");
  const details = summary.closest("details")!;
  expect(details).not.toHaveAttribute("open");
  await userEvent.click(summary);
  expect(details).toHaveAttribute("open");
  expect(within(details).getByText("namespaceAdmin")).toBeInTheDocument();
  expect(within(details).getByText(/역할은 맞지만 이름이 <ID>-clusterrole-admin\|edit 꼴이 아님/)).toBeInTheDocument();
  expect(within(details).getByText(/이름은 맞지만 역할이 ClusterRole kubeflow-admin·kubeflow-edit 가 아님/)).toBeInTheDocument();
});

test("?q= 로 시작하면 첫 조회부터 그 검색어, 새 검색어는 디바운스 뒤 서버와 주소에", async () => {
  const asked = wrap(ACCESS, "/admin/k8s/access?q=alice");
  await screen.findByText("alice", { selector: "span.font-semibold" });
  expect(asked[0]).toBe("alice");
  const input = screen.getByLabelText("권한 검색");
  expect(input).toHaveValue("alice");
  await userEvent.clear(input);
  await userEvent.type(input, "team-b");
  await waitFor(() => expect(asked).toContain("team-b"));
  await waitFor(() => expect(screen.getByTestId("where")).toHaveTextContent("/admin/k8s/access?q=team-b"));
});

test("빈 상태: 동기화 전이면 「모름」, 검색 0건은 다른 문구", async () => {
  wrap({ clusters: [{ name: "dmsk8s", synced_at: null, stale: false, status: "never" }], users: [], unrecognized: [] });
  expect(await screen.findByText(/권한은 모름입니다\(권한이 없다는 뜻이 아닙니다\)/)).toBeInTheDocument();
});

test("빈 상태: 검색어에 맞는 권한 없음", async () => {
  wrap({ ...ACCESS, users: [], unrecognized: [] }, "/admin/k8s/access?q=nobody");
  expect(await screen.findByText("검색어에 맞는 권한이 없습니다")).toBeInTheDocument();
});

test("오류 상태: 조회 실패는 role=alert 하나", async () => {
  server.use(http.get("/api/admin/k8s/access", () => HttpResponse.json({ detail: "x" }, { status: 500 })));
  wrap(null);
  expect(await screen.findByRole("alert")).toHaveTextContent("권한 목록을 불러오지 못했습니다");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
});
