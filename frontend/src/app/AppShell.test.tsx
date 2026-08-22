import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { AppShell } from "./AppShell";

// 셸의 「데이터 → 렌더」 계약(슬라이스 31 T2, 사용자 조정 반영): 사이드바는
// navigation.ts 의 함수이고 adminOnly 필터·접힘 상태가 여기서 고정된다.
// 접힘 규칙(사용자 결정 2026-08-19 재조정): 그룹 토글은 **서로 독립** --
// 아코디언(하나만 열림)은 같은 날 도입했다가 해제됐다. 초기엔 현재 경로의
// 그룹만 열리고(로그인 직후 운영자는 운영), 경로 이동은 그 그룹을 열기만 하며
// 사용자가 연 다른 그룹을 닫지 않는다. 자동 펼침 덕에 e2e 04 의 "사이드바
// 링크 클릭"이 접힘에서도 링크를 찾는다.

const server = setupServer();
beforeAll(() => server.listen());
// sessionStorage: 접힘 상태가 셸 리마운트를 넘도록 저장된다 -- 테스트 간에도
// 넘어가 버리므로 매 테스트 후 비운다(안 비우면 앞 테스트의 접힘이 샌다).
afterEach(() => { server.resetHandlers(); sessionStorage.clear(); });
afterAll(() => server.close());

function renderShell(role: "user" | "admin", at = "/jobs") {
  const actor = role === "admin" ? "admin" : "alice";
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor, role })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}>
        <AppShell>
          <div>본문</div>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // unmount 를 함께 돌려준다 -- 리마운트 유지 테스트가 "경로 이동 = 셸 재마운트"를 모사한다.
  return { actor, ...view };
}

// 구 AppShell 실측 16링크(작업4+스토리지3+운영5+관리4). 라벨은 기존 문구 그대로.
const ADMIN_ONLY_LABELS = [
  "스토리지", "노드", "아티팩트 경로", "대시보드", "배치 작업",
  "빌드", "릴리스", "컨트롤 상태", "계정", "정책", "denylist", "감사 로그",
];
const USER_LABELS = ["내 작업", "단일 작업"];

test("user 는 작업 그룹 2링크만 보이고 admin 전용 그룹은 없다", async () => {
  renderShell("user");
  // me 도착을 먼저 기다린다 -- 기다리지 않으면 "adminOnly 부재" 단언이 로딩 중
  // 화면을 보고 공허하게 통과한다(데이터가 오기 전엔 누구든 user 로 보인다).
  await screen.findByText("alice");   // me 로드 앵커(UserPanel 아이디)
  for (const label of USER_LABELS)
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  for (const label of ADMIN_ONLY_LABELS)
    expect(screen.queryByRole("link", { name: label })).toBeNull();
  // admin 전용 그룹은 헤더(버튼)째로 없어야 한다 -- 빈 그룹 껍데기 금지.
  for (const group of ["스토리지", "운영", "관리"])
    expect(screen.queryByRole("button", { name: group })).toBeNull();
});

test("대시보드 마운트: 운영 5링크만 보이고 접힘 그룹(작업·스토리지·관리)은 숨는다", async () => {
  renderShell("admin", "/admin/dashboard");
  // findByRole: admin 전용 링크는 me 도착 후에만 그려진다.
  expect(await screen.findByRole("link", { name: "대시보드" })).toBeInTheDocument();
  for (const label of ["배치 작업", "빌드", "릴리스", "컨트롤 상태"])
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  // 접힘 기본 그룹의 링크는 렌더되지 않는다 -- 헤더(버튼)만 남는다.
  for (const label of ["내 작업", "스토리지", "계정"])
    expect(screen.queryByRole("link", { name: label })).toBeNull();
  for (const group of ["작업", "스토리지", "관리"])
    expect(screen.getByRole("button", { name: group })).toBeInTheDocument();
});

test("독립 토글: 대시보드에서 스토리지를 열어도 운영은 열린 채다", async () => {
  renderShell("admin", "/admin/dashboard");
  await screen.findByRole("link", { name: "대시보드" });
  expect(screen.queryByRole("link", { name: "노드" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "스토리지" }));
  expect(screen.getByRole("link", { name: "노드" })).toBeInTheDocument();
  // 아코디언 아님(사용자 결정) -- 다른 그룹을 열어도 기존 열림은 유지된다.
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
});

test("최상위 섹션은 DMS 뿐 -- NAS·Monitoring 링크는 없다(추후 추가)", async () => {
  renderShell("user");
  await screen.findByText("alice");   // me 로드 앵커(UserPanel 아이디)
  expect(screen.queryByRole("link", { name: "NAS" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Monitoring" })).toBeNull();
});

test("로그아웃 버튼 접근성 이름은 '로그아웃'이다(e2e 01·router.test 와 삼중 계약)", async () => {
  renderShell("user");
  expect(await screen.findByRole("button", { name: "로그아웃" })).toBeInTheDocument();
});

test("초기 상태: /jobs 마운트면 작업 그룹만 열려 있다", async () => {
  // e2e 04 의 "사이드바 링크 클릭"이 기대는 성질 -- 지금 보고 있는 화면의
  // 그룹은 항상 열려 있어 자기 위치를 잃지 않는다. 나머지는 초기 접힘.
  renderShell("admin", "/jobs");
  expect(await screen.findByRole("link", { name: "내 작업" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "단일 작업" })).toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "계정" })).toBeNull();
  expect(screen.queryByRole("link", { name: "대시보드" })).toBeNull();
});

test("열어둔 그룹은 셸 리마운트(경로 이동)에도 유지된다", async () => {
  // AppRouter 의 <ErrorBoundary key={pathname}> 가 경로마다 셸을 리마운트한다 --
  // sessionStorage 유지가 없으면 이동할 때마다 접힘이 초기화된다(사용자 보고:
  // "클릭하면 해당 메뉴 빼고 나머지가 자동으로 접힌다").
  const first = renderShell("admin", "/admin/dashboard");
  await screen.findByRole("button", { name: "관리" });
  await userEvent.click(screen.getByRole("button", { name: "관리" }));
  expect(screen.getByRole("link", { name: "계정" })).toBeInTheDocument();
  first.unmount();                    // 경로 이동 = 리마운트 모사
  renderShell("admin", "/jobs");
  // 이전에 열어둔 관리가 그대로 열려 있다(+ 새 활성 그룹 작업도 열림).
  // findByRole: 관리 그룹은 admin 전용이라 me 도착 후에야 그려진다.
  expect(await screen.findByRole("link", { name: "계정" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "단일 작업" })).toBeInTheDocument();
});

test("열린 그룹 헤더 재클릭은 닫고, 다시 클릭이 복원한다 -- 다른 그룹은 무영향", async () => {
  renderShell("admin", "/jobs");
  // 운영 헤더는 admin 전용이라 me 도착 후에야 그려진다 -- 공용 링크(단일 작업)만
  // 기다리면 이 버튼이 아직 없다.
  await screen.findByRole("button", { name: "운영" });
  // 다른 그룹(운영)을 먼저 열어 둔다 -- 작업 토글이 이것을 건드리면 안 된다.
  await userEvent.click(screen.getByRole("button", { name: "운영" }));
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "작업" }));
  expect(screen.queryByRole("link", { name: "단일 작업" })).toBeNull();
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "작업" }));
  expect(screen.getByRole("link", { name: "단일 작업" })).toBeInTheDocument();
});
