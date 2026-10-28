import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, beforeEach, expect, test } from "vitest";
import { reasonText } from "../../lib/api";
import { K8sOverview, customIntervalSeconds } from "./K8sOverview";
import { CLUSTER_FAILING, CLUSTER_OK, NOW, overview } from "./k8sFixtures";
import type { CallerClusterLink, CallerNode, ClusterSummary, K8sOverview as Overview } from "./types";

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

// --- 호출 노드 카드(2026-10-28 사고 분석) -------------------------------------------------------------
// k8s-w1(primary): 하루 전 dmsk8s2 수동 동기화 1건이 연결 거부(실험 클러스터 재시작 2분) — 그 뒤 쭉 성공.
// k8s-w2(secondary): 그 직후 대체 재시도 1건도 실패, 이후 한 번도 안 불림 — dmsk8s2 에는 성공한 적이 없다.
const ago = (m: number) => new Date(Date.parse(NOW) - m * 60_000).toISOString();
const DAY = 1440;
const CLUSTER_2: ClusterSummary = { ...CLUSTER_OK, name: "dmsk8s2", api_server: "https://10.10.10.30:6443",
                                    fingerprint: "uid-2", last_success_at: ago(2), last_attempt_at: ago(2) };
const link = (cluster: string, status: CallerClusterLink["last_status"], over: Partial<CallerClusterLink> = {}) =>
  ({ cluster, last_ok_at: null, last_fail_at: null, last_reason: null, last_trigger: null, last_status: status, ...over });

function incident(secondary: Partial<CallerNode> = {}): Overview {
  const base = overview({ clusters: [CLUSTER_OK, CLUSTER_2] });
  const [w1, w2] = base.nodes;
  return { ...base, nodes: [
    { ...w1, last_claim_at: ago(2), last_ok_at: ago(2), last_fail_at: ago(DAY + 3), last_reason: "k8s_sync_unreachable",
      last_fail_cluster: "dmsk8s2", last_fail_trigger: "manual", last_fail_cluster_ok_at: ago(2),
      serving: ["dmsk8s", "dmsk8s2"], check_pending: [],
      clusters: [link("dmsk8s", "ok", { last_ok_at: ago(2), last_trigger: "schedule" }),
                 link("dmsk8s2", "ok", { last_ok_at: ago(2), last_fail_at: ago(DAY + 3),
                                         last_reason: "k8s_sync_unreachable", last_trigger: "schedule" })] },
    { ...w2, last_claim_at: ago(DAY + 2), last_ok_at: ago(3 * DAY), last_fail_at: ago(DAY + 2),
      last_reason: "k8s_sync_unreachable", last_fail_cluster: "dmsk8s2", last_fail_trigger: "failover",
      last_fail_cluster_ok_at: null, check_pending: [],
      clusters: [link("dmsk8s", "ok", { last_ok_at: ago(3 * DAY), last_trigger: "failover" }),
                 link("dmsk8s2", "failed", { last_fail_at: ago(DAY + 2), last_reason: "k8s_sync_unreachable",
                                             last_trigger: "failover" })],
      ...secondary },
  ] };
}

const slot = (title: string) => screen.getByText(title).closest("div.rounded-card") as HTMLElement;
const PRIMARY = "주 호출 노드 · primary";
const SECONDARY = "보조 호출 노드 · secondary";

test("이미 해결된 실패는 흐리게·짧게(조치 문구 없이), 클러스터별 연결은 상태를 글자로", async () => {
  wrap(incident());
  await ready();
  const p = slot(PRIMARY);
  expect(within(p).getByText("사용 중")).toBeInTheDocument();
  const fail = within(p).getByText(/이후 성공해 해결됨/);
  expect(fail).toHaveClass("text-ink/70");
  expect(fail).toHaveTextContent("1일 전 · dmsk8s2 수동 · API 서버 연결 실패 · 이후 성공해 해결됨");
  expect(fail.textContent).not.toContain("방화벽");
  expect(p.querySelector(".text-bad")).toBeNull();                          // 빨간 글씨가 하나도 없다
  const rows = within(p).getAllByRole("listitem");
  expect(rows.map((r) => r.textContent)).toEqual(["dmsk8s 성공 · 2분 전", "dmsk8s2 성공 · 2분 전"]);
  // 호출 노드 카드 밖(클러스터 카드)은 그대로
  expect(within(card("dmsk8s2")).getByText("정상")).toBeInTheDocument();
});

test("다른 클러스터의 성공으로 이 클러스터의 실패를 「해결됨」이라 하지 않는다 — 칸과 배지가 같은 판정", async () => {
  const ov = incident();
  const [w1] = ov.nodes;
  ov.nodes[0] = { ...w1, last_fail_cluster_ok_at: null, clusters: [w1.clusters![0], link("dmsk8s2", "failed", {
    last_fail_at: ago(DAY + 3), last_reason: "k8s_sync_unreachable", last_trigger: "manual" })] };
  wrap(ov);
  await ready();
  const p = slot(PRIMARY);
  expect(within(p).queryByText(/이후 성공해 해결됨/)).toBeNull();
  expect(within(p).getByText(/dmsk8s2 수동 · /)).toHaveClass("text-bad");
  // 배지도 노드 단위 마지막 성공(dmsk8s, 2분 전)으로 덮지 않는다(2026-10-28 2차 리뷰 -- 칸은 빨간데 배지는 「사용 중」이었다)
  expect(within(p).getByText("최근 실패")).toBeInTheDocument();
  expect(within(p).queryByText("사용 중")).toBeNull();
});

test("해결 안 된 실패: 빨간 전체 사유 + 클러스터 이름, 그 클러스터가 회복했으면 배지는 「재확인 필요」", async () => {
  const view = wrap(incident());
  await ready();
  const s = slot(SECONDARY);
  expect(within(s).getByText("재확인 필요")).toBeInTheDocument();
  expect(within(s).queryByText("최근 실패")).toBeNull();
  const red = within(s).getByText(/dmsk8s2 대체 재시도 · /);
  expect(red).toHaveClass("text-bad");
  expect(red).toHaveTextContent(`1일 전 · dmsk8s2 대체 재시도 · ${reasonText("k8s_sync_unreachable")}`);
  expect(within(s).getByText(/클러스터 dmsk8s2 는 이후 다른 노드로 회복했습니다/)).toBeInTheDocument();
  const [a, b] = within(s).getAllByRole("listitem");
  expect(a).toHaveTextContent("dmsk8s 성공 · 3일 전");
  expect(b).toHaveTextContent("dmsk8s2 실패 · 1일 전 · API 서버 연결 실패 — 클러스터는 이후 회복");
  // 클러스터가 아직 회복하지 않았으면 「최근 실패」 그대로, 회복 꼬리표도 없다
  view.unmount();
  const ov = incident();
  wrap({ ...ov, clusters: [CLUSTER_OK, { ...CLUSTER_2, last_success_at: ago(3 * DAY), status: "failing" }] });
  await ready();
  const s2 = slot(SECONDARY);
  expect(within(s2).getByText("최근 실패")).toBeInTheDocument();
  expect(s2).not.toHaveTextContent("이후 회복");
  expect(s2).not.toHaveTextContent("이후 다른 노드로 회복");
});

test("호출 기록이 없는 클러스터는 「호출 기록 없음」(모름 — 보존 정리로 지워졌을 수도) — secondary 면 장애 넘김 경로 미확인", async () => {
  const ov = incident({ last_fail_at: null, last_reason: null, last_fail_cluster: null, last_fail_trigger: null,
                        clusters: [link("dmsk8s", "ok", { last_ok_at: ago(3 * DAY) }), link("dmsk8s2", null)] });
  ov.nodes[0] = { ...ov.nodes[0], clusters: [link("dmsk8s", "running", { last_trigger: "schedule" }), link("dmsk8s2", null)] };
  wrap(ov);
  await ready();
  const [, never] = within(slot(SECONDARY)).getAllByRole("listitem");
  expect(never).toHaveTextContent("dmsk8s2 호출 기록 없음 — 장애 넘김 경로 미확인");
  expect(never.querySelector(".text-bad")).toBeNull();                      // 모름은 실패색이 아니다
  const [running, pNever] = within(slot(PRIMARY)).getAllByRole("listitem");
  expect(running).toHaveTextContent("dmsk8s 진행 중(정규)");
  expect(pNever).toHaveTextContent("dmsk8s2 호출 기록 없음");
  expect(pNever).not.toHaveTextContent("장애 넘김");                          // primary 엔 대체 경로 힌트가 없다
});

test("primary 의 실패·보조 연결 확인의 실패는 클러스터가 회복해도 「재확인 필요」로 내리지 않는다", async () => {
  // primary: 경로가 매 주기 끊기고 대체가 메워 클러스터는 회복 — 지금 실패 중이다(2026-10-28 리뷰)
  const ov = incident();
  const [w1] = ov.nodes;
  ov.nodes[0] = { ...w1, last_ok_at: ago(3 * DAY), last_fail_at: ago(5), last_fail_trigger: "schedule",
                  last_fail_cluster_ok_at: ago(3 * DAY),
                  clusters: [w1.clusters![0], link("dmsk8s2", "failed", { last_fail_at: ago(5),
                                                                          last_reason: "k8s_sync_unreachable" })] };
  const view = wrap(ov);
  await ready();
  const p = slot(PRIMARY);
  expect(within(p).getByText("최근 실패")).toBeInTheDocument();
  expect(within(p).queryByText("재확인 필요")).toBeNull();
  expect(p).not.toHaveTextContent("이 노드의 경로는 그 뒤 확인되지 않았습니다");
  view.unmount();
  // secondary: 마지막 실패가 방금 누른 「연결 확인」 자체 — 다시 확인하라고 하지 않는다
  wrap(incident({ last_fail_at: ago(10), last_fail_trigger: "check",
                  clusters: [link("dmsk8s", "ok", { last_ok_at: ago(11), last_trigger: "check" }),
                             link("dmsk8s2", "failed", { last_fail_at: ago(10), last_reason: "k8s_sync_unreachable",
                                                         last_trigger: "check" })] }));
  await ready();
  const s = slot(SECONDARY);
  expect(within(s).getByText("최근 실패")).toBeInTheDocument();
  expect(within(s).getByText(/dmsk8s2 보조 연결 확인 · /)).toHaveClass("text-bad");
  expect(s).not.toHaveTextContent("이 노드의 경로는 그 뒤 확인되지 않았습니다");
  // 「클러스터별 연결」도 확인 실패에 「클러스터는 이후 회복」을 붙이지 않는다 -- 클러스터는 실패한 적이 없다(카드는 「정상」,
  // primary 가 2분 전 성공; 2026-10-28 2차 리뷰)
  const [, row] = within(s).getAllByRole("listitem");
  expect(row).toHaveTextContent("dmsk8s2 실패 · 10분 전 · API 서버 연결 실패");
  expect(row).not.toHaveTextContent("회복");
  expect(within(card("dmsk8s2")).getByText("정상")).toBeInTheDocument();
});

test("옛 서버 응답(clusters 칸 없음)이면 「클러스터별 연결」 줄을 지어내지 않는다", async () => {
  wrap();
  await ready();
  expect(screen.queryByText("클러스터별 연결")).toBeNull();
  // 연결 확인 버튼은 secondary 슬롯에만
  expect(screen.getAllByRole("button", { name: "연결 확인" })).toHaveLength(1);
  expect(within(slot(SECONDARY)).getByRole("button", { name: "연결 확인" })).toBeEnabled();
});

test("연결 확인: POST secondary:check, 요청 중(check_pending)이면 잠그고 폴링 안내", async () => {
  let current = incident();
  const hits: string[] = [];
  server.use(
    http.get("/api/admin/k8s/overview", () => HttpResponse.json(current)),
    http.post(/\/api\/admin\/k8s\/secondary:check$/, ({ request }) => {
      hits.push(new URL(request.url).pathname);
      current = incident({ check_pending: ["dmsk8s", "dmsk8s2"] });
      return HttpResponse.json({ requested: ["dmsk8s", "dmsk8s2"] });
    }),
  );
  wrap(null);
  await ready();
  const s = slot(SECONDARY);
  expect(within(s).getByText(/모든 클러스터 API 에 닿는지 지금 확인합니다/)).toBeInTheDocument();
  await userEvent.click(within(s).getByRole("button", { name: "연결 확인" }));
  await waitFor(() => expect(hits).toEqual(["/api/admin/k8s/secondary:check"]));
  expect(await within(slot(SECONDARY)).findByText(/확인 요청됨 — 보조 노드가 다음 폴링\(15초\) 안에 가져갑니다/))
    .toHaveTextContent("dmsk8s, dmsk8s2");
  expect(within(slot(SECONDARY)).getByRole("button", { name: "연결 확인" })).toBeDisabled();
});

test("연결 확인 거절(409)은 사유 문장, 미지원·미보고 노드는 잠그고 이유를 적는다", async () => {
  server.use(http.post(/\/api\/admin\/k8s\/secondary:check$/, () =>
    HttpResponse.json({ detail: "k8s_sync_nothing_to_check" }, { status: 409 })));
  const view = wrap(incident());
  await ready();
  await userEvent.click(within(slot(SECONDARY)).getByRole("button", { name: "연결 확인" }));
  expect(await within(slot(SECONDARY)).findByText(reasonText("k8s_sync_nothing_to_check"))).toHaveClass("text-bad");
  view.unmount();
  const v2 = wrap(incident({ capable: false }));
  await ready();
  const btn = within(slot(SECONDARY)).getByRole("button", { name: "연결 확인" });
  expect(btn).toBeDisabled();
  expect(within(slot(SECONDARY)).getByText(/k8s 동기화를 지원하지 않아 확인할 수 없습니다/)).toBeInTheDocument();
  v2.unmount();
  const v3 = wrap(incident({ capable: false, fresh: false, reported: false }));
  await ready();
  expect(within(slot(SECONDARY)).getByRole("button", { name: "연결 확인" })).toBeDisabled();
  expect(within(slot(SECONDARY)).getByText(/에이전트 보고가 없어 확인할 수 없습니다/)).toBeInTheDocument();
  v3.unmount();
  // 보고 지연: 가져갈 노드가 없으니 잠근다(2026-10-28 리뷰 -- 눌러도 요청이 무기한 남았다)
  const v4 = wrap(incident({ fresh: false }));
  await ready();
  expect(within(slot(SECONDARY)).getByRole("button", { name: "연결 확인" })).toBeDisabled();
  expect(within(slot(SECONDARY)).getByText(/보고가 지연돼 확인할 수 없습니다/)).toBeInTheDocument();
  v4.unmount();
  // primary 가 없으면 보조 노드가 정규 노드 — 정규 동기화가 곧 확인이다
  const noPrimary = incident();
  wrap({ ...noPrimary, settings: { ...noPrimary.settings, primary_node: null } });
  await ready();
  expect(within(slot(SECONDARY)).getByRole("button", { name: "연결 확인" })).toBeDisabled();
  expect(within(slot(SECONDARY)).getByText(/주 노드가 지정되지 않아 보조 노드가 정규 동기화를 맡고 있습니다/))
    .toBeInTheDocument();
});

test("걸린 확인 요청이 있는데 보조 노드 보고가 끊겼으면 「다음 폴링 안에」라고 하지 않는다", async () => {
  const view = wrap(incident({ fresh: false, check_pending: ["dmsk8s2"] }));
  await ready();
  const s = slot(SECONDARY);
  expect(within(s).getByText(/보조 노드가 보고하지 않아 아직 가져가지 못했습니다 · 남은 클러스터 dmsk8s2/))
    .toBeInTheDocument();
  expect(s).not.toHaveTextContent("다음 폴링");
  expect(within(s).getByRole("button", { name: "연결 확인" })).toBeDisabled();
  view.unmount();
  // 보고는 오지만 k8s 동기화를 지원하지 않는 에이전트도 가져가지 못한다 -- 5초 재조회도 하지 않는다(2026-10-28 2차 리뷰)
  wrap(incident({ capable: false, check_pending: ["dmsk8s2"] }));
  await ready();
  const s2 = slot(SECONDARY);
  expect(within(s2).getByText(/에이전트가 k8s 동기화를 지원하지 않아 가져가지 못합니다 · 남은 클러스터 dmsk8s2/))
    .toHaveClass("text-attn");
  expect(s2).not.toHaveTextContent("다음 폴링");
  expect(within(s2).getByRole("button", { name: "연결 확인" })).toBeDisabled();
  expect(screen.getByText(/초마다 새로 읽습니다/)).toHaveTextContent("화면은 10초마다 새로 읽습니다");
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
