import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { PoliciesList } from "./PoliciesList";

// 허용 쌍 편집기(SyncPairsPanel, 2026-09-30)가 같은 화면에 있다 -- 이 파일은 도구 정책이 관심사라
// 빈 목록만 준다(편집기 자체는 SyncPairsPanel.test 가 고정).
const server = setupServer(
  http.get("/api/admin/storages", () => HttpResponse.json([])),
  http.get("/api/admin/sync-pairs", () => HttpResponse.json([])),
);
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const POLICIES = [
  { tool: "scan", max_nodes: 4, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: null, execution_timeout_seconds: 3600,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" },
  { tool: "dsync", max_nodes: 8, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: 3600, execution_timeout_seconds: 259200,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" },
  { tool: "nsync", max_nodes: 8, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: 3600, execution_timeout_seconds: 259200,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" },
  { tool: "rm", max_nodes: 4, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: 1800, execution_timeout_seconds: 3600,
    enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin" },
];

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><PoliciesList /></QueryClientProvider>);
}

test("lists the four tool policies with human-readable timeouts", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  wrap();
  expect(await screen.findByText("scan")).toBeInTheDocument();
  expect(screen.getByText("dsync")).toBeInTheDocument();
  expect(screen.getAllByText("3600s (1h)").length).toBeGreaterThan(0);
  expect(screen.getAllByText("259200s (3d)").length).toBeGreaterThan(0);
});

test("editing a policy sends the correct PUT body, including null preview timeout when cleared", async () => {
  let capturedBody: unknown;
  server.use(
    http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)),
    http.put("/api/admin/policies/:tool", async ({ request }) => {
      capturedBody = await request.json();
      return HttpResponse.json({ ...POLICIES[1], max_nodes: 16, preview_timeout_seconds: null });
    }));
  wrap();
  const row = (await screen.findByText("dsync")).closest("article")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));

  const maxNodes = await screen.findByRole("spinbutton", { name: "최대 노드" });
  await userEvent.clear(maxNodes);
  await userEvent.type(maxNodes, "16");

  const previewTimeout = screen.getByRole("spinbutton", { name: "미리보기 타임아웃(초)" });
  await userEvent.clear(previewTimeout);

  await userEvent.click(screen.getByRole("button", { name: "저장" }));

  expect(capturedBody).toEqual({
    max_nodes: 16, procs_per_node: 8, queue: "dms-data",
    default_priority: "mid", max_priority: "high",
    preview_timeout_seconds: null, execution_timeout_seconds: 259200,
    enabled: true,
  });
});

test("숫자 필드를 지우면 빈 칸(0 이 아님) + 인라인 오류 + 저장 비활성", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  wrap();
  const row = (await screen.findByText("scan")).closest("article")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));
  const maxNodes = await screen.findByRole("spinbutton", { name: "최대 노드" });
  await userEvent.clear(maxNodes);
  // 결함 회귀 그물: number 상태 시절엔 Number("")=0 이 "0"으로 그려져 필드를
  // 비우는 것 자체가 불가능했다.
  expect(maxNodes).toHaveValue(null);
  expect(screen.getByText("최대 노드: 값을 입력하세요")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeDisabled();
  // 다시 치면 친 그대로 보인다("08" 잔류 없음 — 문자열 상태라 표시 = 상태)
  await userEvent.type(maxNodes, "8");
  expect(maxNodes).toHaveValue(8);
  expect(screen.queryByText("최대 노드: 값을 입력하세요")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "저장" })).toBeEnabled();
});

test("0·음수는 서버까지 가기 전에 인라인 오류로 막는다(pydantic ge=1 미러)", async () => {
  const calls: string[] = [];
  server.use(
    http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)),
    http.put("/api/admin/policies/:tool", () => { calls.push("put"); return HttpResponse.json(POLICIES[0]); }));
  wrap();
  const row = (await screen.findByText("scan")).closest("article")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));
  const et = await screen.findByRole("spinbutton", { name: "실행 타임아웃(초)" });
  await userEvent.clear(et);
  await userEvent.type(et, "0");
  expect(screen.getByText("실행 타임아웃: 1 이상의 정수여야 합니다")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(calls).toEqual([]);   // 저장이 비활성이라 PUT 이 나가지 않았다
});

test("shows an inline message when the PUT returns 422 invalid_priority", async () => {
  server.use(
    http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)),
    http.put("/api/admin/policies/:tool", () => HttpResponse.json({ detail: "invalid_priority" }, { status: 422 })));
  wrap();
  const row = (await screen.findByText("scan")).closest("article")!;
  await userEvent.click(within(row).getByRole("button", { name: "수정" }));
  await userEvent.click(screen.getByRole("button", { name: "저장" }));
  expect(await screen.findByText("우선순위 값이 올바르지 않습니다")).toBeInTheDocument();
});

// ---- 2026-09-30 정책 UI 개선: 도구별 카드 + 값의 뜻 ------------------------------------

test("도구별 카드: 용도 설명·병렬도(노드×프로세스=최대)·타임아웃 없음을 사람이 읽게 보인다", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  wrap();
  const scan = await screen.findByRole("article", { name: "scan 정책" });
  // 제목은 정책 키(사용자 결정 2026-10-01: 한국어 이름 대신 dsync·nsync 등) -- 도구명이 다른 scan 만 옆에 dscan
  expect(within(scan).getByText("scan")).toBeInTheDocument();
  expect(within(scan).getByText("dscan")).toBeInTheDocument();
  expect(within(scan).queryByText("스캔")).not.toBeInTheDocument();
  expect(within(scan).getByText(/파일 수·용량·데이터 온도/)).toBeInTheDocument();
  expect(within(scan).getByText("4노드 × 8프로세스")).toBeInTheDocument();
  expect(within(scan).getByText("최대 32개 프로세스")).toBeInTheDocument();
  expect(within(scan).getByText("없음")).toBeInTheDocument();          // 미리보기 타임아웃 null
  // dsync 와 nsync 의 차이가 화면에 있다(같은 노드 공존 vs 노드 간).
  const nsync = screen.getByRole("article", { name: "nsync 정책" });
  expect(within(nsync).getByText(/함께 마운트한 노드가 없을 때/)).toBeInTheDocument();
  // nsync 의 최대 노드는 면당(소스·목적지 각각) -- 합계는 2배(placement.resolve_fanout).
  expect(within(nsync).getByText("병렬 실행(소스·목적지 각각)")).toBeInTheDocument();
  expect(within(nsync).getByText("양쪽 합계 최대 16노드 · 128개 프로세스")).toBeInTheDocument();
});

test("비활성 정책은 카드에 거부 결과를 경고한다", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(
    [{ ...POLICIES[3], enabled: 0 }])));
  wrap();
  const rm = await screen.findByRole("article", { name: "rm 정책" });
  expect(within(rm).getByText("비활성")).toBeInTheDocument();
  expect(within(rm).getByText(/계획 단계에서 거부됩니다\(policy_disabled\)/)).toBeInTheDocument();
});

test("수정 다이얼로그는 병렬 자원·스케줄링·타임아웃·상태로 묶이고, 초를 사람 단위로 보조 표기한다", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  wrap();
  const card = (await screen.findByText("dsync")).closest("article")!;
  await userEvent.click(within(card).getByRole("button", { name: "수정" }));
  for (const g of ["병렬 자원", "스케줄링", "타임아웃", "상태"])
    expect(await screen.findByRole("group", { name: g })).toBeInTheDocument();
  expect(screen.getByText("최대 64개 프로세스로 실행됩니다")).toBeInTheDocument();
  expect(screen.getByText("= 3일")).toBeInTheDocument();               // 259200s
  expect(screen.getByText("= 1시간")).toBeInTheDocument();             // 3600s
});

test("카드는 한 줄에 하나씩, 제목은 정책 키(dsync·nsync 등) -- 한국어 이름 없음(사용자 결정 2026-10-01)", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  wrap();
  const cards = await screen.findAllByRole("article");
  expect(cards).toHaveLength(4);
  // 한 열: 카드들의 부모가 격자(2열)가 아니라 세로 스택
  const list = cards[0].parentElement!;
  expect(list.className).toContain("space-y-4");
  expect(list.className).not.toMatch(/grid-cols/);
  for (const ko of ["동기화", "노드 간 동기화", "삭제", "스캔"])
    expect(screen.queryByText(ko)).not.toBeInTheDocument();
  const dsync = screen.getByRole("article", { name: "dsync 정책" });
  expect(within(dsync).getByText("dsync")).toBeInTheDocument();
  expect(within(dsync).queryByText(/^도구/)).not.toBeInTheDocument();    // 키 = 도구명이면 덧붙이지 않는다
  const rm = screen.getByRole("article", { name: "rm 정책" });
  expect(within(rm).getByText("drm")).toBeInTheDocument();
});

test("지표 다섯 칸(병렬·우선순위·큐·타임아웃 둘)은 한 격자 -- 넓으면 한 줄, 좁으면 자연 줄바꿈(2026-10-02)", async () => {
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  wrap();
  const dsync = await screen.findByRole("article", { name: "dsync 정책" });
  const grid = within(dsync).getByLabelText("정책 지표");
  expect(grid.className).toContain("grid-cols-[repeat(auto-fit,minmax(11rem,1fr))]");
  const labels = Array.from(grid.children).map((c) => c.firstElementChild?.textContent);
  expect(labels).toEqual(["병렬 실행", "우선순위(기본 / 최대)", "큐", "미리보기 타임아웃", "실행 타임아웃"]);
});
