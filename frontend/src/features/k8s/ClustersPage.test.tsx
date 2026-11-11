import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { ClustersPage } from "./ClustersPage";
import { CLUSTER_FAILING, CLUSTER_OK, overview } from "./k8sFixtures";
import type { K8sOverview as Overview } from "./types";

// 「클러스터」 탭(2026-11-11 개요에서 분리): 클러스터 카드(상태·n분 전·개수·마지막 run·경고·같은 클러스터 경고·지금 동기화)·
// 머리 동작(모두 지금 동기화·클러스터 등록)·빈 상태(개요 탭으로 보내는 순서)·「호출 노드 없음」 안내·오류 상태.
// null(모름)·0·실패를 다른 문구로 보이는지까지 고정한다(개요에서 옮겨 온 단언 포함).

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function wrap(data: Overview | null = overview(), at = "/admin/k8s/clusters") {
  if (data !== null) server.use(http.get("/api/admin/k8s/overview", () => HttpResponse.json(data)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}><ClustersPage /></MemoryRouter>
    </QueryClientProvider>);
}

// 데이터가 온 뒤(h1·탭은 로딩 중에도 그려진다) -- 요약 줄이 앵커. 카드 이름은 h2(h1 「클러스터」 바로 아래 단계).
const ready = () => screen.findByText(/^클러스터 \d+개$/);
const card = (name: string) => screen.getByRole("heading", { name, level: 2 }).closest("div.flex-col") as HTMLElement;
const EMPTY = { clusters: [], settings: { primary_node: null, secondary_node: null, interval_seconds: 300, poll_seconds: 15 },
                totals: { clusters: 0, namespaces: null, pvcs: null, bindings: null, users: null } };

test("h1·탭(클러스터가 켜짐)·머리 동작·요약 줄·클러스터 카드를 그린다", async () => {
  wrap();
  expect(await screen.findByRole("heading", { name: "클러스터", level: 1 })).toBeInTheDocument();
  await ready();
  expect(screen.getByRole("link", { name: "클러스터" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "개요" })).not.toHaveAttribute("aria-current");
  expect(screen.getByRole("button", { name: "모두 지금 동기화" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "클러스터 등록" })).toBeInTheDocument();
  // 머리 설명은 호출 노드·주기가 개요 탭의 일임을 링크로 알린다
  expect(screen.getByRole("link", { name: "개요 탭" })).toHaveAttribute("href", "/admin/k8s");
  // 요약 줄: 상태별 개수(실패는 빨간 글자) + 주기·폴링
  expect(screen.getByText("정상 1")).not.toHaveClass("text-bad");
  expect(screen.getByText("실패 1")).toHaveClass("text-bad");
  expect(screen.getByText(/초마다 새로 읽습니다/)).toHaveTextContent("5분마다 동기화 · 화면은 10초마다 새로 읽습니다");
  // 클러스터 카드 둘 -- 제목 단계를 건너뛰지 않는다(h3 없음)
  expect(screen.queryAllByRole("heading", { level: 3 })).toHaveLength(0);
  const ok = card("dmsk8s");
  expect(within(ok).getByText("정상")).toBeInTheDocument();
  expect(within(ok).getByText("https://10.10.10.20:6443")).toBeInTheDocument();
  expect(within(ok).getByText("3분 전")).toBeInTheDocument();
  expect(within(ok).getByRole("link", { name: "이력" })).toHaveAttribute("href", "/admin/k8s/history?cluster=dmsk8s");
  expect(within(ok).getByRole("button", { name: "편집" })).toBeInTheDocument();
  expect(within(ok).getByRole("button", { name: "삭제" })).toBeInTheDocument();
  const bad = card("dmsk8s-dns");
  expect(within(bad).getAllByText("실패").length).toBeGreaterThanOrEqual(1);   // 배지 + 마지막 실행
});

test("null(모름)·0·실패를 다른 문구로 -- 개수 0 은 「0」, 받은 목록 없음은 「모름」, 실패는 사유 문장", async () => {
  wrap();
  await ready();
  const ok = card("dmsk8s");
  // users: 0 -- 「0」으로 보인다(—로 뭉개지 않는다)
  const usersTile = within(ok).getByText("권한 사용자").parentElement!;
  expect(usersTile).toHaveTextContent("0");
  expect(usersTile).not.toHaveTextContent("—");
  const bad = card("dmsk8s-dns");
  expect(within(bad).getByText(/아직 받은 목록이 없습니다 — 개수는 모름/)).toBeInTheDocument();
  expect(within(bad).getByText("아직 없음")).toBeInTheDocument();          // 마지막 성공 없음
  expect(within(bad).getByText("2회")).toBeInTheDocument();
  expect(within(bad).getByText(/certificate verify failed/)).toBeInTheDocument();
  expect(within(bad).getByText(/한 번도 성공하지 않아/)).toBeInTheDocument();
  expect(within(bad).getByText(/같은 클러스터로 보입니다\(kube-system uid 일치\): dmsk8s/)).toBeInTheDocument();
});

test("지금 동기화: POST /api/admin/k8s/clusters/<이름>:sync 를 부르고 결과를 알린다", async () => {
  const hits: string[] = [];
  server.use(http.post(/\/api\/admin\/k8s\/clusters\/[^/]+:sync$/, ({ request }) => {
    hits.push(new URL(request.url).pathname);
    return HttpResponse.json({ ok: true });
  }));
  wrap();
  await ready();
  await userEvent.click(within(card("dmsk8s")).getByRole("button", { name: "지금 동기화" }));
  await waitFor(() => expect(hits).toEqual(["/api/admin/k8s/clusters/dmsk8s:sync"]));
  expect(await within(card("dmsk8s")).findByText(/요청했습니다/)).toBeInTheDocument();
});

test("토큰 없는·꺼진 클러스터는 지금 동기화가 잠긴다(이유는 title)", async () => {
  wrap(overview({ clusters: [{ ...CLUSTER_OK, token: "missing", status: "token_missing" },
                             { ...CLUSTER_FAILING, enabled: false, status: "disabled" }] }));
  await ready();
  const a = within(card("dmsk8s")).getByRole("button", { name: "지금 동기화" });
  expect(a).toBeDisabled();
  expect(a).toHaveAttribute("title", "토큰이 있어야 동기화합니다");
  expect(within(card("dmsk8s-dns")).getByRole("button", { name: "지금 동기화" })).toBeDisabled();
  expect(within(card("dmsk8s")).getByText("토큰 필요")).toBeInTheDocument();
  // 동기화할 수 있는 클러스터가 하나도 없으면 「모두 지금 동기화」도 잠기고 이유를 title 로
  const all = screen.getByRole("button", { name: "모두 지금 동기화" });
  expect(all).toBeDisabled();
  expect(all.getAttribute("title")).toMatch(/지금 동기화할 수 있는.*클러스터가 없습니다/);
  // 조치가 필요한 상태(토큰 없음)는 요약 줄에서 주의색
  expect(screen.getByText("토큰 없음 1")).toHaveClass("text-attn");
});

test("모두 지금 동기화: 동기화 가능한(켜짐·토큰 있음) 클러스터만 요청", async () => {
  const hits: string[] = [];
  server.use(http.post(/\/api\/admin\/k8s\/clusters\/[^/]+:sync$/, ({ request }) => {
    hits.push(new URL(request.url).pathname);
    return HttpResponse.json({});
  }));
  wrap(overview({ clusters: [CLUSTER_OK, { ...CLUSTER_FAILING, token: "missing", status: "token_missing" }] }));
  await ready();
  await userEvent.click(screen.getByRole("button", { name: "모두 지금 동기화" }));
  expect(await screen.findByText("1개 클러스터 동기화를 요청했습니다")).toBeInTheDocument();
  expect(hits).toEqual(["/api/admin/k8s/clusters/dmsk8s:sync"]);
});

test("빈 상태: 등록 순서(호출 노드는 개요 탭 링크 + 지금 지정 상태)와 등록 버튼, 모두 동기화는 없다", async () => {
  const view = wrap(overview(EMPTY));
  expect(await screen.findByRole("heading", { name: "등록된 클러스터가 없습니다", level: 2 })).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "클러스터 등록" })).toHaveLength(2);      // 머리 + 빈 상태
  expect(screen.queryByRole("button", { name: "모두 지금 동기화" })).toBeNull();
  const steps = screen.getByRole("list");
  expect(within(steps).getByRole("link", { name: "개요 탭" })).toHaveAttribute("href", "/admin/k8s");
  expect(within(steps).getByRole("link", { name: "StorageClass 탭" })).toHaveAttribute("href", "/admin/k8s/storageclasses");
  expect(within(steps).getByText("아직 지정되지 않았습니다.", { exact: false })).toHaveClass("text-attn");
  view.unmount();
  // 호출 노드가 지정돼 있으면 그 상태를 적는다(경고색 아님)
  wrap(overview({ ...EMPTY, settings: { ...EMPTY.settings, primary_node: "k8s-w1" } }));
  expect(await screen.findByText(/지금: 주 k8s-w1 · 보조 없음/)).not.toHaveClass("text-attn");
});

test("「호출 노드 없음」 클러스터가 있으면 개요 탭으로 보내는 안내를 맨 위에", async () => {
  const view = wrap(overview({ clusters: [{ ...CLUSTER_OK, status: "no_node" }, CLUSTER_FAILING] }));
  await ready();
  const note = screen.getByText(/동기화가 돌지 않는 클러스터가 있습니다/);
  expect(within(note).getByRole("link", { name: "개요 탭" })).toHaveAttribute("href", "/admin/k8s");
  expect(screen.getByText("호출 노드 없음 1")).toHaveClass("text-bad");
  view.unmount();
  wrap();
  await ready();
  expect(screen.queryByText(/동기화가 돌지 않는 클러스터가 있습니다/)).toBeNull();
});

test("동기화 중이면 화면은 3초마다 새로 읽는다고 적는다(개요와 같은 폴링)", async () => {
  wrap(overview({ clusters: [{ ...CLUSTER_OK, status: "syncing" }] }));
  await ready();
  expect(screen.getByText(/초마다 새로 읽습니다/)).toHaveTextContent("화면은 3초마다 새로 읽습니다");
  expect(within(card("dmsk8s")).getByRole("button", { name: "지금 동기화" })).toHaveAttribute("title", "이미 동기화 중입니다");
});

test("보조 노드가 가져갈 수 없는 확인 요청만 걸려 있으면 빠르게 다시 읽지 않는다(10초)", async () => {
  const base = overview();
  wrap({ ...base, nodes: [base.nodes[0], { ...base.nodes[1], capable: false, check_pending: ["dmsk8s"] }] });
  await ready();
  expect(screen.getByText(/초마다 새로 읽습니다/)).toHaveTextContent("화면은 10초마다 새로 읽습니다");
});

test("가입 신원 경고: 등록 버튼이 있는 이 화면에도 -- stub·꺼짐이면 보이고, 실제 메일이거나 모르면 없다(개요와 같은 판정)", async () => {
  const view = wrap(overview({ signup_guard: "stub" }));
  const note = await screen.findByRole("note", { name: "가입 신원 경고" });
  expect(note).toHaveTextContent("stub");
  expect(within(note).getByRole("link", { name: "메일 설정" })).toHaveAttribute("href", "/admin/mail");
  view.unmount();
  for (const guard of ["mail", undefined] as const) {
    const v = wrap(overview({ signup_guard: guard }));
    await ready();
    expect(screen.queryByRole("note", { name: "가입 신원 경고" })).toBeNull();
    v.unmount();
  }
  const v = wrap(overview({ signup_guard: "open" }));
  expect(await screen.findByRole("note", { name: "가입 신원 경고" })).toHaveTextContent("가입 인증이 꺼져 있습니다");
  v.unmount();
  // 빈 상태(등록 창이 두 곳에 있는 화면)에서도
  wrap(overview({ ...EMPTY, signup_guard: "stub" }));
  expect(await screen.findByRole("heading", { name: "등록된 클러스터가 없습니다", level: 2 })).toBeInTheDocument();
  expect(screen.getByRole("note", { name: "가입 신원 경고" })).toBeInTheDocument();
});

test("주소의 #cluster-<이름>(개요의 클러스터 이름 링크)이면 그 카드를 강조하고 이름 제목에 포커스한다", async () => {
  const view = wrap(overview(), "/admin/k8s/clusters#cluster-dmsk8s-dns");
  await ready();
  const bad = card("dmsk8s-dns");
  expect(bad).toHaveAttribute("id", "cluster-dmsk8s-dns");
  expect(bad).toHaveClass("ring-2");
  expect(card("dmsk8s")).not.toHaveClass("ring-2");
  await waitFor(() => expect(screen.getByRole("heading", { name: "dmsk8s-dns", level: 2 })).toHaveFocus());
  // 대상이 아닌 카드의 제목은 포커스 대상이 아니다(탭 순서에도 없다)
  expect(screen.getByRole("heading", { name: "dmsk8s", level: 2 })).not.toHaveAttribute("tabindex");
  view.unmount();
  // 해시가 없거나 없는 클러스터면 강조 없음
  wrap(overview(), "/admin/k8s/clusters#cluster-gone");
  await ready();
  expect(card("dmsk8s")).not.toHaveClass("ring-2");
  expect(card("dmsk8s-dns")).not.toHaveClass("ring-2");
});

test("오류 상태: 조회 실패는 role=alert 하나로 알린다(불러오는 중에 갇히지 않는다)", async () => {
  server.use(http.get("/api/admin/k8s/overview", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  wrap(null);
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("클러스터 목록을 불러오지 못했습니다");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(screen.queryByText("불러오는 중…")).not.toBeInTheDocument();
  // 머리의 등록 버튼은 조회 실패와 무관하게 쓸 수 있다
  expect(screen.getByRole("button", { name: "클러스터 등록" })).toBeEnabled();
});
