import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient, focusManager } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AppRouter } from "./router";
import { ME_RETRY_LIMIT } from "../features/auth/useAuth";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderAt(path: string, qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><AppRouter /></MemoryRouter>
    </QueryClientProvider>,
  );
}

// /me 의 5xx·네트워크 오류는 useMe 가 ME_RETRY_LIMIT 번 더 묻고(0.5초·1초 뒤) 그래도 실패해야 오류 화면이 뜬다 -- 기본
// findBy 상한(1초)으로는 모자라 오류 화면을 기다리는 단언만 이 상한을 쓴다.
const ME_RETRY_WAIT = { timeout: 5000 };
const SESSION_CHECK_FAILED = "세션 확인에 실패했습니다 — 서버 오류이거나 네트워크 문제일 수 있습니다";

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

// 보안 이벤트(2026-11-03): 사이드바 라벨 = h1. 세 카드(상태·잠금·이벤트)의 조회를 모두 받아 화면이 끝까지 그려지는지 본다.
test("admin can open security events", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
    http.get("/api/admin/security/status", () => HttpResponse.json({
      started_at: "2026-11-03T01:00:00Z",
      login: { id_failure_limit: 10, id_failure_window_seconds: 60, id_attempt_limit: 60, id_attempt_window_seconds: 60,
               scrypt_slots: 2, scrypt_max_wait_seconds: 5, scrypt_in_use: 0, scrypt_waiting: 0,
               pacing: { enabled: false, limit: 0, window_seconds: 60, max_wait_seconds: 15, tokens: null, waiting: 0 } },
      alarms: { window_seconds: 600, failures: 30, attempts: 300, source_failures: 20, recent_failures: 0, recent_attempts: 0 },
      verification: { code_digits: 6, code_ttl_seconds: 300, per_code_attempts: 5, per_id_failure_limit: 10,
                      per_id_failure_window_seconds: 86400, global_guess_limit: 1000, global_guess_window_seconds: 3600,
                      global_guess_failures: 0, global_locked: false, global_retry_after_seconds: null },
    })),
    http.get("/api/admin/security/verification-locks", () => HttpResponse.json({ items: [], truncated: false, limit: 200 })),
    http.get("/api/admin/security/events", () => HttpResponse.json([])),
  );
  renderAt("/admin/security");
  expect(await screen.findByRole("heading", { name: "보안 이벤트", level: 1 })).toBeInTheDocument();
  expect(await screen.findByText("기록된 보안 이벤트가 없습니다")).toBeInTheDocument();
  expect(await screen.findByText("잠기거나 실패가 쌓인 아이디가 없습니다")).toBeInTheDocument();
  expect(screen.getByRole("navigation", { name: "breadcrumb" })).toHaveTextContent("HOME>DMS>관리>보안 이벤트");
});

test("user visiting /admin/security is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/security");
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
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

// k8s 연동(dms-k8s 설계 §7): 개요 h1 = 사이드바 라벨 「k8s 연동」, 하위 다섯 h1 = 탭·크럼 라벨(빌드 이력 선례).
const K8S_OVERVIEW = {
  now: "2026-10-25T00:00:00Z",
  settings: { primary_node: null, secondary_node: null, interval_seconds: 300, poll_seconds: 15 },
  nodes: [], clusters: [], totals: { clusters: 0, namespaces: 0, pvcs: 0, bindings: 0, users: 0 },
};
const k8sHandlers = () => [
  http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
  http.get("/api/admin/k8s/overview", () => HttpResponse.json(K8S_OVERVIEW)),
  http.get("/api/admin/nodes", () => HttpResponse.json([])),
  http.get("/api/admin/storages", () => HttpResponse.json([])),
  http.get("/api/admin/k8s/storage-classes", () => HttpResponse.json([])),
  http.get("/api/admin/k8s/volumes", () => HttpResponse.json({ clusters: [], truncated: false, volumes: [] })),
  http.get("/api/admin/k8s/access", () => HttpResponse.json({ clusters: [], users: [], unrecognized: [] })),
  http.get("/api/admin/k8s/runs", () => HttpResponse.json([])),
  http.get("/api/admin/k8s/clusters", () => HttpResponse.json([])),
];

test.each([
  ["/admin/k8s", "k8s 연동"],
  ["/admin/k8s/clusters", "클러스터"],
  ["/admin/k8s/storageclasses", "StorageClass"],
  ["/admin/k8s/volumes", "PV 탐색"],
  ["/admin/k8s/access", "사용자 권한"],
  ["/admin/k8s/history", "동기화 이력"],
])("admin can open %s (h1 %s)", async (path, heading) => {
  server.use(...k8sHandlers());
  renderAt(path);
  expect(await screen.findByRole("heading", { name: heading, level: 1 })).toBeInTheDocument();
  // 하위 내비는 여섯 탭(2026-11-11 클러스터 분리) -- 지금 화면이 aria-current 로 켜진다. 하위 화면의 h1 = 탭 라벨 = 크럼 끝 라벨.
  const nav = screen.getByRole("navigation", { name: "k8s 연동 하위 메뉴" });
  expect(nav.querySelectorAll("a")).toHaveLength(6);
  expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent(path === "/admin/k8s" ? "개요" : heading);
  if (path !== "/admin/k8s") {
    const crumb = screen.getByRole("navigation", { name: "breadcrumb" });
    expect(within(crumb).getByText(heading)).toBeInTheDocument();
  }
});

// 사용량 분석(2026-10-29 하위 메뉴): 경로별 h1 = 사이드바 라벨 「사용량 분석」, 스토리지별 h1 = 탭·크럼 라벨(k8s 연동 선례).
const usageHandlers = () => [
  http.get("/api/auth/me", () => HttpResponse.json({ actor: "admin", role: "admin" })),
  http.get("/api/admin/storages", () => HttpResponse.json([])),
  http.get("/api/admin/usage/storages", () => HttpResponse.json({ window_limit: 10000, storages: [] })),
  http.get("/api/admin/usage/unregistered-storages", () => HttpResponse.json([])),
];

test.each([
  ["/admin/usage", "사용량 분석", "경로별"],
  ["/admin/usage/storages", "스토리지별", "스토리지별"],
  ["/admin/usage/breakdown", "소속별", "소속별"],
])("admin can open %s (h1 %s)", async (path, heading, tab) => {
  server.use(...usageHandlers());
  renderAt(path);
  expect(await screen.findByRole("heading", { name: heading, level: 1 })).toBeInTheDocument();
  // 하위 내비는 세 탭(2026-10-30 소속별) -- 지금 화면이 aria-current 로 켜진다. 사이드바는 셋 다 「사용량 분석」. 하위 화면의
  // h1 = 탭 라벨 = 크럼 끝 라벨(소속별·스토리지별).
  const nav = screen.getByRole("navigation", { name: "사용량 분석 하위 메뉴" });
  expect(nav.querySelectorAll("a")).toHaveLength(3);
  expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent(tab);
  if (path !== "/admin/usage") {
    const crumb = screen.getByRole("navigation", { name: "breadcrumb" });
    expect(within(crumb).getByText(heading)).toBeInTheDocument();
  }
});

test("user visiting /admin/usage/breakdown is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/usage/breakdown");
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
});

test("user visiting /admin/usage/storages is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/usage/storages");
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
});

test("user visiting /admin/k8s is redirected to /jobs", async () => {
  server.use(
    http.get("/api/auth/me", () => HttpResponse.json({ actor: "alice", role: "user" })),
    http.get("/api/user/requests", () => HttpResponse.json([])),
  );
  renderAt("/admin/k8s/access");
  expect(await screen.findByRole("heading", { name: "전체 작업" })).toBeInTheDocument();
});

test("me 500 이면 / 는 오류 문구 + 재시도를 렌더하고 어디로도 리다이렉트하지 않는다", async () => {
  // §2.4-5: /api/auth/me 의 일시 500/네트워크 오류를 /login 으로 보내면 로그인된
  // 관리자가 "세션 만료"로 오독하고 재로그인한다 -- 오류 화면 + 재시도가 정직하다.
  server.use(http.get("/api/auth/me", () =>
    HttpResponse.json({ detail: "http_500" }, { status: 500 })));
  renderAt("/");
  expect(await screen.findByText(SESSION_CHECK_FAILED, {}, ME_RETRY_WAIT)).toBeInTheDocument();
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

// 2026-11-04 기능 점검 #2: 보호 화면(RequireRole)도 Home 과 같은 규칙 -- /login 은 401 뿐이다. 라이브에서 DB 전환 ~35초 동안
// /me 가 500 이었고 그 사이 열린 딥링크(/jobs/<id>·/admin/*)가 전부 안내 없는 로그인 폼으로 갔다(세션은 살아 있었다).
test("me 500 이면 보호 화면은 /login 대신 제자리에서 오류 + 다시 시도, 다시 시도가 성공하면 그 화면을 그린다", async () => {
  let meCalls = 0;
  let down = true;
  server.use(
    http.get("/api/auth/me", () => {
      meCalls += 1;
      return down
        ? HttpResponse.json({ detail: "http_500" }, { status: 500 })
        : HttpResponse.json({ actor: "admin", role: "admin" });
    }),
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
  );
  renderAt("/admin/denylist");
  expect(await screen.findByRole("alert", {}, ME_RETRY_WAIT)).toHaveTextContent(SESSION_CHECK_FAILED);
  // 짧은 재시도(5xx 한정)를 다 쓴 뒤에야 오류 화면이다 -- 첫 요청 + ME_RETRY_LIMIT 회.
  expect(meCalls).toBe(1 + ME_RETRY_LIMIT);
  // 리다이렉트 없음: 로그인 폼도, 역할 리다이렉트 목적지(/jobs)도 아니다.
  expect(screen.queryByRole("button", { name: "로그인" })).toBeNull();
  expect(screen.queryByRole("heading", { name: "전체 작업" })).toBeNull();

  down = false;
  await userEvent.click(screen.getByRole("button", { name: "다시 시도" }));
  // URL 이 그대로였으므로 다시 시도 한 번으로 원래 화면이 열린다.
  expect(await screen.findByRole("heading", { name: "denylist" })).toBeInTheDocument();
  expect(screen.queryByText(SESSION_CHECK_FAILED)).toBeNull();
});

test("me 네트워크 오류(연결 끊김)도 보호 화면에서 오류 + 다시 시도다(/login 아님)", async () => {
  server.use(http.get("/api/auth/me", () => HttpResponse.error()));
  renderAt("/jobs");
  expect(await screen.findByRole("alert", {}, ME_RETRY_WAIT)).toHaveTextContent(SESSION_CHECK_FAILED);
  expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "로그인" })).toBeNull();
});

test("me 401 이면 보호 화면 딥링크는 여전히 /login 이다(오류 화면이 아니다)", async () => {
  server.use(http.get("/api/auth/me", () =>
    HttpResponse.json({ detail: "not_authenticated" }, { status: 401 })));
  renderAt("/admin/denylist");
  expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
  expect(screen.queryByText(SESSION_CHECK_FAILED)).toBeNull();
  expect(screen.queryByRole("button", { name: "다시 시도" })).toBeNull();
});

test("확인된 세션이 있으면 me 재조회 500 은 보던 화면을 걷지 않는다(재조회 401 만 /login)", async () => {
  // 화면 이동·창 포커스의 백그라운드 재조회가 일시 500 이면 마지막으로 확인된 세션으로 화면을 그대로 둔다 -- 걷으면 작성 중이던
  // 입력이 사라진다. 인가는 서버가 요청마다 다시 판정한다.
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let meCalls = 0;
  let status = 200;
  server.use(
    http.get("/api/auth/me", async () => {
      meCalls += 1;
      // 401 응답을 조금 늦춘다 -- 무효화 루프가 회귀하면(아래 사례) 테스트가 멈추지 않고 findBy 상한에서 실패하게.
      if (status === 401) await delay(5);
      return status === 200
        ? HttpResponse.json({ actor: "admin", role: "admin" })
        : HttpResponse.json({ detail: status === 401 ? "not_authenticated" : "http_500" }, { status });
    }),
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
  );
  renderAt("/admin/denylist", qc);
  await screen.findByRole("heading", { name: "denylist" });
  // 화면 셸의 마운트 재조회가 끝난 뒤에 센다(진행 중 조회가 섞이면 호출 수가 흔들린다).
  await waitFor(() => expect(qc.isFetching({ queryKey: ["auth", "me"] })).toBe(0));

  status = 500;
  const before = meCalls;
  await qc.invalidateQueries({ queryKey: ["auth", "me"] });
  await waitFor(() => expect(qc.getQueryState(["auth", "me"])?.status).toBe("error"), ME_RETRY_WAIT);
  expect(meCalls).toBe(before + 1 + ME_RETRY_LIMIT);
  expect(screen.getByRole("heading", { name: "denylist" })).toBeInTheDocument();
  expect(screen.queryByText(SESSION_CHECK_FAILED)).toBeNull();
  expect(screen.queryByRole("button", { name: "로그인" })).toBeNull();

  // 세션이 정말 끝났으면(401) 데이터가 있어도 /login 이다.
  status = 401;
  await qc.invalidateQueries({ queryKey: ["auth", "me"] });
  expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
});

test("확인된 세션에서 401 이 오면 me 재조회 한 번으로 /login 에 정착한다(무효화 루프 없음)", async () => {
  // dms:unauthorized 는 me 조회 자신의 401 안에서도 쏜다. AuthContext 의 무효화가 진행 중 me 조회를 취소하면(cancelRefetch 기본값)
  // 그 401 이 또 무효화 → 또 취소 … me 가 error 로 정착하지 못해 /login 이동 없이 /me 만 무한히 두드렸다(데이터가 있을 때만 --
  // 첫 진입 401 은 취소 대신 합류라 안 보였다). 응답을 늦춰 회귀가 멈춤이 아니라 실패로 드러나게 한다.
  let meCalls = 0;
  let alive = true;
  server.use(
    http.get("/api/auth/me", async () => {
      meCalls += 1;
      await delay(5);
      return alive
        ? HttpResponse.json({ actor: "admin", role: "admin" })
        : HttpResponse.json({ detail: "not_authenticated" }, { status: 401 });
    }),
    http.get("/api/admin/identity-denylist", () => HttpResponse.json([])),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  renderAt("/admin/denylist", qc);
  await screen.findByRole("heading", { name: "denylist" });
  // 화면 셸의 두 번째 me 관찰자(마운트 재조회)가 끝난 뒤에 센다 -- 진행 중 조회가 있으면 아래 401 이 거기 합류해 0회가 된다.
  await waitFor(() => expect(qc.isFetching({ queryKey: ["auth", "me"] })).toBe(0));
  const before = meCalls;

  // 다른 API 의 401 과 같은 신호(api.ts request() 가 401 마다 쏜다).
  alive = false;
  window.dispatchEvent(new CustomEvent("dms:unauthorized"));
  expect(await screen.findByRole("button", { name: "로그인" })).toBeInTheDocument();
  // 정착 창을 두고도 더 묻지 않는다 -- /login 엔 me 관찰자가 없다.
  await new Promise((resolve) => setTimeout(resolve, 200));
  expect(meCalls - before).toBe(1);
});

test("보호 화면 「다시 시도」 중에도 오류 패널·버튼 노드가 남아 키보드 포커스를 잃지 않는다", async () => {
  // 데이터 없는 me 의 재조회는 status 를 pending 으로 되돌린다 -- 예전엔 「불러오는 중…」으로 바뀌며 버튼이 언마운트돼 포커스가
  // body 로 떨어졌다(리뷰 1차). 누른 재시도가 도는 동안은 같은 버튼(aria-disabled)을 두고, 다시 실패하면 그 버튼으로 돌아온다.
  let meCalls = 0;
  server.use(http.get("/api/auth/me", () => {
    meCalls += 1;
    return HttpResponse.json({ detail: "http_500" }, { status: 500 });
  }));
  renderAt("/admin/denylist");
  const btn = await screen.findByRole("button", { name: "다시 시도" }, ME_RETRY_WAIT);
  btn.focus();
  const before = meCalls;
  await userEvent.keyboard("{Enter}");
  expect(screen.queryByText("불러오는 중…")).toBeNull();
  expect(screen.getByRole("button", { name: "다시 확인하는 중…" })).toBe(btn);
  expect(btn).toHaveAttribute("aria-disabled", "true");
  expect(document.activeElement).toBe(btn);
  // 재시도 중 다시 눌러도 조회를 겹쳐 시작하지 않는다.
  await userEvent.keyboard("{Enter}");
  const again = await screen.findByRole("button", { name: "다시 시도" }, ME_RETRY_WAIT);
  expect(again).toBe(btn);
  expect(again).not.toHaveAttribute("aria-disabled");
  expect(document.activeElement).toBe(again);
  expect(meCalls - before).toBe(1 + ME_RETRY_LIMIT);
});

test("숨은 탭에서 me 5xx 재시도가 일시정지돼도 / 는 기다렸다가 관리자를 /admin/dashboard 로 보낸다(/jobs 로 새지 않는다)", async () => {
  // 재시도는 탭이 숨으면 일시정지한다(fetchStatus paused) -- 그때 isLoading 은 거짓이라 Home 이 데이터 없이 /jobs 로 빠졌다(리뷰 1차).
  let lastPath = "";
  function PathSpy() { lastPath = useLocation().pathname; return null; }
  let meCalls = 0;
  server.use(
    http.get("/api/auth/me", () => {
      meCalls += 1;
      return meCalls === 1
        ? HttpResponse.json({ detail: "http_500" }, { status: 500 })
        : HttpResponse.json({ actor: "admin", role: "admin" });
    }),
    // 착지한 대시보드의 조회들 -- 이 테스트는 경로만 본다.
    http.get("/api/*", () => HttpResponse.json({ detail: "http_503" }, { status: 503 })),
  );
  focusManager.setFocused(false);
  try {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/"]}><AppRouter /><PathSpy /></MemoryRouter>
      </QueryClientProvider>,
    );
    // 첫 실패 + 재시도 대기(0.5초)가 지나 일시정지된 동안 / 에 머문다.
    await new Promise((resolve) => setTimeout(resolve, 900));
    expect(meCalls).toBe(1);
    expect(lastPath).toBe("/");
    focusManager.setFocused(true);
    await waitFor(() => expect(lastPath).toBe("/admin/dashboard"), ME_RETRY_WAIT);
  } finally {
    focusManager.setFocused(undefined);
  }
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
