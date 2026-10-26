import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from "vitest";
import { reasonText } from "../../lib/api";
import { K8sOverview, customIntervalSeconds } from "./K8sOverview";
import { CLUSTER_FAILING, CLUSTER_OK, overview } from "./k8sFixtures";
import type { K8sOverview as Overview } from "./types";

// 개요(설계 §7): 호출 노드 카드·노드 지정·주기 변경(1·5·15·60분 + 직접 입력)·클러스터 카드(상태·n분 전·개수·마지막 run·
// 지금 동기화)·빈 상태·오류 상태. null(모름)·0·실패를 다른 문구로 보이는지까지 고정한다.

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const NODES = [
  { node_name: "k8s-w1", reported_at: "2026-10-25T03:00:00Z", fresh: true, report: { k8s_sync: { v: 1 } } },
  { node_name: "k8s-w2", reported_at: "2026-10-25T03:00:00Z", fresh: true, report: { k8s_sync: { v: 1 } } },
  { node_name: "k8s-w3", reported_at: "2026-10-25T03:00:00Z", fresh: true, report: {} },
];

beforeEach(() => {
  server.use(http.get("/api/admin/nodes", () => HttpResponse.json(NODES)));
});

function wrap(data: Overview | null = overview()) {
  if (data !== null) server.use(http.get("/api/admin/k8s/overview", () => HttpResponse.json(data)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/k8s"]}><K8sOverview /></MemoryRouter>
    </QueryClientProvider>);
}

// 데이터가 온 뒤(h1·탭은 로딩 중에도 그려진다) -- 호출 노드 카드 제목이 앵커.
const ready = () => screen.findByRole("heading", { name: "호출 노드와 주기", level: 2 });
const card = (name: string) => screen.getByRole("heading", { name, level: 3 }).closest("div.flex-col") as HTMLElement;

test("h1·탭·합계·호출 노드(IP·방화벽 안내·맡은 클러스터)·클러스터 카드를 그린다", async () => {
  wrap();
  expect(await screen.findByRole("heading", { name: "k8s 연동", level: 1 })).toBeInTheDocument();
  await ready();
  expect(screen.getByRole("link", { name: "개요" })).toHaveAttribute("aria-current", "page");
  // 노드 슬롯: 주 노드는 IP + 방화벽 안내 + 맡은 클러스터
  expect(screen.getByText("10.10.10.21")).toBeInTheDocument();
  expect(screen.getAllByText("대상 API 서버 방화벽에 이 IP 를 허용하세요")).toHaveLength(2);
  expect(screen.getByText("사용 중")).toBeInTheDocument();
  expect(screen.getByText("대기")).toBeInTheDocument();
  // 클러스터 카드 둘
  const ok = card("dmsk8s");
  expect(within(ok).getByText("정상")).toBeInTheDocument();
  expect(within(ok).getByText("https://10.10.10.20:6443")).toBeInTheDocument();
  expect(within(ok).getByText("3분 전")).toBeInTheDocument();
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
});

test("주기 변경: 15분을 고르고 저장하면 PUT settings 에 interval_seconds 900(노드는 그대로)", async () => {
  let body: unknown = null;
  server.use(http.put("/api/admin/k8s/settings", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({ ...(body as object), poll_seconds: 15 });
  }));
  wrap();
  await ready();
  const save = screen.getByRole("button", { name: "설정 저장" });
  expect(save).toBeDisabled();                                   // 바꾼 것이 없으면 잠김
  const group = screen.getByRole("group", { name: "동기화 주기" });
  expect(within(group).getByRole("button", { name: "5분" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(within(group).getByRole("button", { name: "15분" }));
  await userEvent.click(save);
  await waitFor(() => expect(body).toEqual({ primary_node: "k8s-w1", secondary_node: "k8s-w2", interval_seconds: 900 }));
});

test("직접 입력(분): 1.5분 = 90초, 1분 미만은 저장 불가", async () => {
  expect(customIntervalSeconds("1.5")).toBe(90);
  expect(customIntervalSeconds("0.5")).toBeNull();
  expect(customIntervalSeconds("abc")).toBeNull();
  let body: { interval_seconds?: number } | null = null;
  server.use(http.put("/api/admin/k8s/settings", async ({ request }) => {
    body = await request.json() as { interval_seconds: number };
    return HttpResponse.json({});
  }));
  wrap();
  await ready();
  await userEvent.click(screen.getByRole("button", { name: "직접 입력" }));
  const input = screen.getByLabelText("직접 입력 주기(분)");
  await userEvent.clear(input);
  await userEvent.type(input, "0.5");
  expect(screen.getByText(/1분 이상 1440분 이하/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "설정 저장" })).toBeDisabled();
  await userEvent.clear(input);
  await userEvent.type(input, "1.5");
  await userEvent.click(screen.getByRole("button", { name: "설정 저장" }));
  await waitFor(() => expect(body?.interval_seconds).toBe(90));
});

test("노드 지정: 보고된 노드 목록에서 고르고, 주·보조 같은 노드는 막는다", async () => {
  let body: unknown = null;
  server.use(http.put("/api/admin/k8s/settings", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({});
  }));
  wrap();
  await ready();
  const primary = screen.getByLabelText("주(primary) 호출 노드");
  // 노드 화면의 보고 노드(k8s-w3, 능력 미선언)도 선택지에 있고 미지원이라고 적힌다
  await waitFor(() => expect(within(primary).getByRole("option", { name: /k8s-w3 — k8s 동기화 미지원 에이전트/ }))
    .toBeInTheDocument());
  await userEvent.selectOptions(primary, "k8s-w2");
  expect(screen.getByText(/주·보조에 같은 노드를 지정할 수 없습니다/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "설정 저장" })).toBeDisabled();
  await userEvent.selectOptions(primary, "k8s-w3");
  await userEvent.selectOptions(screen.getByLabelText("보조(secondary) 호출 노드"), "");
  await userEvent.click(screen.getByRole("button", { name: "설정 저장" }));
  await waitFor(() => expect(body).toEqual({ primary_node: "k8s-w3", secondary_node: null, interval_seconds: 300 }));
});

test("설정 저장이 403(세션 관리자 전용)이면 사유를 보인다", async () => {
  server.use(http.put("/api/admin/k8s/settings", () =>
    HttpResponse.json({ detail: "admin_session_required" }, { status: 403 })));
  wrap();
  await ready();
  await userEvent.click(screen.getByRole("button", { name: "60분" }));
  await userEvent.click(screen.getByRole("button", { name: "설정 저장" }));
  expect(await screen.findByText(reasonText("admin_session_required"))).toBeInTheDocument();
  expect(screen.queryByText("저장했습니다")).not.toBeInTheDocument();
});

test("가입 신원 경고: 인증 메일이 stub·꺼짐이면 보이고, 실제 메일이거나 모르면 없다", async () => {
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
});

test("빈 상태: 클러스터가 없으면 등록 안내와 등록 버튼", async () => {
  wrap(overview({ clusters: [], settings: { primary_node: null, secondary_node: null, interval_seconds: 300, poll_seconds: 15 },
                  totals: { clusters: 0, namespaces: null, pvcs: null, bindings: null, users: null } }));
  expect(await screen.findByText("등록된 클러스터가 없습니다")).toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "클러스터 등록" }).length).toBeGreaterThanOrEqual(2);
  expect(screen.getAllByText("지정 안 됨")).toHaveLength(2);
  expect(screen.queryByRole("button", { name: "모두 지금 동기화" })).not.toBeInTheDocument();
});

test("오류 상태: 개요 조회 실패는 role=alert 하나로 알린다(불러오는 중에 갇히지 않는다)", async () => {
  server.use(http.get("/api/admin/k8s/overview", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  wrap(null);
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("개요를 불러오지 못했습니다");
  expect(screen.getAllByRole("alert")).toHaveLength(1);
  expect(screen.queryByText("불러오는 중…")).not.toBeInTheDocument();
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
