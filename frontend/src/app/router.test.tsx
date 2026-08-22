import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AppRouter } from "./router";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("unauthenticated shows login", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ detail: "x" }, { status: 401 })));
  renderAt("/jobs");
  expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
});

test("user visiting admin route is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/dashboard");
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
});

test("admin can open batches list", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/batches", () => HttpResponse.json([])),
  );
  renderAt("/admin/batches");
  expect(await screen.findByRole("heading", { name: "배치 작업" })).toBeInTheDocument();
});

test("admin can open audit log", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/audit-log", () => HttpResponse.json([])),
  );
  renderAt("/admin/audit");
  expect(await screen.findByRole("heading", { name: "감사 로그" })).toBeInTheDocument();
});

test("admin can open policies list", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/policies", () => HttpResponse.json([])),
  );
  renderAt("/admin/policies");
  expect(await screen.findByRole("heading", { name: "정책" })).toBeInTheDocument();
});

test("admin can open denylist", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
  );
  renderAt("/admin/denylist");
  expect(await screen.findByRole("heading", { name: "denylist" })).toBeInTheDocument();
});

test("admin can open control state", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null, changed_by: null, changed_at: null })),
  );
  renderAt("/admin/control");
  expect(await screen.findByRole("heading", { name: "컨트롤 상태" })).toBeInTheDocument();
});

// 슬라이스 37: /admin/scan·/scan-paths 라우트 제거(scan 은 단일 작업으로 흡수) --
// 미지 경로가 됐으므로 홈 리다이렉트로 귀결돼야 한다(catch-all 계약).
test("removed routes (/admin/scan, /scan-paths) fall through to home redirect", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/metrics/jobs", () => HttpResponse.json({ by_state: [] })),
    http.get("/api/admin/metrics/infra", () => HttpResponse.json({ components: [], job_image: { live: null, manifest: null } })),
    http.get("/api/admin/metrics/queue", () => HttpResponse.json({ queue: null, podgroups: null })),
    http.get("/api/admin/metrics/nodes", () => HttpResponse.json({ nodes: [] })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/scan");
  expect(await screen.findByRole("heading", { name: "대시보드" })).toBeInTheDocument();
});

test("admin can open accounts list", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/accounts", () => HttpResponse.json([])),
  );
  renderAt("/admin/accounts");
  expect(await screen.findByRole("heading", { name: "계정" })).toBeInTheDocument();
});

test("admin can open nodes dashboard", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/nodes", () => HttpResponse.json([])),
  );
  renderAt("/admin/nodes");
  expect(await screen.findByRole("heading", { name: "노드" })).toBeInTheDocument();
});

test("admin can open builds screen", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/control-state", () =>
      HttpResponse.json({ maintenance: 0, drain: 0, reason: null, build_node_name: "dms-w1",
                          changed_by: null, changed_at: null })),
    http.get("/api/admin/builds", () => HttpResponse.json([])),
  );
  renderAt("/admin/builds");
  expect(await screen.findByRole("heading", { name: "빌드" })).toBeInTheDocument();
});

// 빌드 하위 페이지: /admin/builds 는 빌드하기(기본), /admin/builds/history 는 이력.
// 라우트 순서가 뒤집히면 "history" 가 :buildId 로 먹혀 상세 화면이 열린다
// (/admin/batches/new vs :batchId 와 같은 함정) -- 그래서 부재 단언을 함께 건다.
test("admin can open build history subpage", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/builds", () => HttpResponse.json([])),
  );
  renderAt("/admin/builds/history");
  expect(await screen.findByRole("heading", { name: "빌드 이력" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "빌드 history" })).not.toBeInTheDocument();
});

test("admin can open build detail", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/builds/b1", () =>
      HttpResponse.json({ build_id: "b1", repo_url: "u", git_ref: "main", commit_sha: "deadbeef",
                          images: ["dms"], node_name: "dms-w1", state: "Succeeded", reason_code: null,
                          tag: "b01234567", created_at: "2026-08-06T00:00:00Z",
                          finished_at: "2026-08-06T00:10:00Z" })),
    http.get("/api/admin/builds/b1/log", () => HttpResponse.json({ build_id: "b1", log: "ok\n" })),
  );
  renderAt("/admin/builds/b1");
  expect(await screen.findByRole("heading", { name: "빌드 b1" })).toBeInTheDocument();
});

// h1과 사이드바 라벨과 이 이름이 어긋나면 운영자가 링크와 화면을 짝지을 수 없다
// -- 세 곳이 같은 문자열이라는 것을 여기서 고정한다.
test("admin can open releases screen", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/releases/targets", () =>
      HttpResponse.json({ registry_ok: true, targets: [] })),
    http.get("/api/admin/releases", () => HttpResponse.json({ current: {}, history: [] })),
  );
  renderAt("/admin/releases");
  expect(await screen.findByRole("heading", { name: "릴리스" })).toBeInTheDocument();
});

test("me 500 이면 / 는 오류 문구 + 재시도를 렌더하고 어디로도 리다이렉트하지 않는다", async () => {
  // §2.4-5: /api/auth/me 의 일시 500/네트워크 오류를 /login 으로 보내면 로그인된
  // 관리자가 "세션 만료"로 오독하고 재로그인한다 -- 오류 화면 + 재시도가 정직하다.
  server.use(http.get("/api/auth/me", () =>
    HttpResponse.json({ detail: "http_500" }, { status: 500 })));
  renderAt("/");
  expect(await screen.findByText(
    "세션 확인에 실패했습니다 — 서버 오류이거나 네트워크 문제일 수 있습니다",
  )).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  // 리다이렉트 없음: /login 의 로그인 버튼도 /jobs 의 목록 헤딩도 렌더되지 않는다.
  expect(screen.queryByRole("button", { name: "로그인" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "전체 작업" })).toBeNull();
});

test("me 401 이면 / 는 여전히 로그인으로 보낸다(오류 화면이 아니다)", async () => {
  // 401 은 "서버 오류"가 아니라 "세션 없음"이다 -- 여기서 오류 화면을 띄우면 문구가
  // 거짓말이 되고, dms:unauthorized -> me 무효화 -> 재조회 401 무한 루프까지 생긴다
  // (AuthContext 는 관찰자가 /login 이동으로 언마운트되어야 루프가 끊긴다).
  server.use(http.get("/api/auth/me", () =>
    HttpResponse.json({ detail: "not_authenticated" }, { status: 401 })));
  renderAt("/");
  expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(screen.queryByText(
    "세션 확인에 실패했습니다 — 서버 오류이거나 네트워크 문제일 수 있습니다",
  )).toBeNull();
});

test("user visiting /admin/policies is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/policies");
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
});

// 이 두 테스트는 router.innerBoundary.test.tsx로 옮겼다 -- RequestDetail의
// `transitions[transitions.length - 1]` 무방어 접근을 크래시 재료로 썼는데, M5에서
// 그 자리에 방어 코드를 넣으면서 이 재료가 사라졌다. 경계 배선 자체를 검증하는
// 테스트는 이제 RequestDetail을 vi.mock으로 "던지는 컴포넌트"로 바꿔치기한
// 전용 파일에서 돈다(이 파일과 같이 두면 vi.mock이 파일 전체에 적용돼 다른
// 라우팅 테스트가 실제 RequestDetail을 못 쓰게 된다).

test("로그아웃 클릭은 즉시 로그인 화면으로 이동한다 -- me 재조회 루프 없이", async () => {
  // 슬라이스 29(§2.2 🔴): qc.clear() 는 관찰자 타이머까지 죽여 "자동 재조회로 401 을
  // 보게 되는" 통로가 없다 -- 명시 nav 가 유일한 전환 수단이다. me 호출 횟수 불변
  // 단언이 루프 부재 증명이다: /login 은 쿼리 관찰자가 0(Login 은 mutation 뿐)이라
  // dms:unauthorized -> me 무효화 -> 재조회 401 루프(슬라이스 26 계열)가 발화할
  // 재료 자체가 없어야 한다.
  let meCalls = 0;
  let alive = true;
  server.use(
    http.get("/api/auth/me", () => {
      meCalls += 1;
      return alive
        ? HttpResponse.json({ actor: "admin", role: "admin" })
        : HttpResponse.json({ detail: "not_authenticated" }, { status: 401 });
    }),
    http.post("/api/auth/logout", () => {
      alive = false;
      return new HttpResponse(null, { status: 204 });
    }),
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
  );
  renderAt("/admin/denylist");
  await screen.findByRole("heading", { name: "denylist" });
  const callsBeforeLogout = meCalls;

  await userEvent.click(screen.getByRole("button", { name: "로그아웃" }));

  // 클릭 즉시(하드 내비게이션 없이) 로그인 화면 -- 이것이 이번 수정의 계약이다.
  expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
  // 루프 부재: 정착 창을 두고도 me 재조회가 한 번도 안 일어난다.
  await new Promise((r) => setTimeout(r, 150));
  expect(meCalls).toBe(callsBeforeLogout);
});
