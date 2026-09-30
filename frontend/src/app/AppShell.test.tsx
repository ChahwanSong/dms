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
// 접힘 규칙(사용자 결정 2026-09-30 "처음 로그인했을 때 전부 펼치고, 그 후 메뉴를 오갈 때는
// 접힘 상태 유지"): 그룹 토글은 **서로 독립** -- 아코디언(하나만 열림)은 해제됐다. 로그인 직후
// (저장분 없음)엔 **모든 그룹이 펼쳐져** 있고, 사용자가 접은 그룹은 경로 이동(셸 리마운트)에도
// 접힌 채다 -- 그 그룹의 화면으로 가도 자동으로 열지 않고 헤더에 "현재 화면" 을 표시한다.
// 로그인 시 리셋은 useAuth.test(resetNavCollapsed)가 고정한다.

const server = setupServer();
beforeAll(() => server.listen());
// localStorage(lib/navState): 접힘 상태가 셸 리마운트를 넘도록 저장된다 -- 테스트 간에도
// 넘어가 버리므로 매 테스트 후 비운다(안 비우면 앞 테스트의 접힘이 샌다).
afterEach(() => { server.resetHandlers(); localStorage.clear(); sessionStorage.clear(); });
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
const USER_LABELS = ["전체 작업", "단일 작업"];

test("user 는 작업 그룹 2링크만 보이고 admin 전용 그룹은 없다(작업 그룹의 배치 작업도 숨김)", async () => {
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

test("기본은 전 그룹 펼침: 대시보드 마운트에도 작업·스토리지·관리 링크가 모두 보인다", async () => {
  renderShell("admin", "/admin/dashboard");
  // findByRole: admin 전용 링크는 me 도착 후에만 그려진다.
  expect(await screen.findByRole("link", { name: "대시보드" })).toBeInTheDocument();
  for (const label of ADMIN_ONLY_LABELS.concat(USER_LABELS))
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  for (const group of ["운영", "작업", "스토리지", "관리"])
    expect(screen.getByRole("button", { name: group })).toHaveAttribute("aria-expanded", "true");
});

test("독립 토글: 스토리지를 접어도 운영은 열린 채다", async () => {
  renderShell("admin", "/admin/dashboard");
  await screen.findByRole("link", { name: "노드" });
  await userEvent.click(screen.getByRole("button", { name: "스토리지" }));
  expect(screen.queryByRole("link", { name: "노드" })).toBeNull();
  // 아코디언 아님(사용자 결정) -- 한 그룹을 접어도 다른 그룹은 그대로다.
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "스토리지" }));
  expect(screen.getByRole("link", { name: "노드" })).toBeInTheDocument();
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

test("초기 상태: /jobs 마운트여도 모든 그룹이 펼쳐져 있다", async () => {
  renderShell("admin", "/jobs");
  expect(await screen.findByRole("link", { name: "계정" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "단일 작업" })).toBeInTheDocument();
});

test("옛 규칙이 저장한 접힘 맵(v1·v2 sessionStorage 키)은 무시된다 -- 전부 펼침에서 시작", async () => {
  sessionStorage.setItem("dms.nav.collapsed", JSON.stringify({ "DMS:관리": true, "DMS:운영": true }));
  sessionStorage.setItem("dms.nav.collapsed.v2", JSON.stringify({ "DMS:관리": true }));
  renderShell("admin", "/jobs");
  expect(await screen.findByRole("link", { name: "계정" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
});

test("접어 둔 그룹은 경로를 오가도(셸 리마운트) 접힌 채 -- 그 그룹 화면으로 가도 자동으로 열지 않는다", async () => {
  // 사용자 결정(2026-09-30): "그 후에 메뉴를 왔다갔다할 때는 접힘 상태는 유지". AppRouter 가
  // 라우트마다 셸을 리마운트하므로 상태는 localStorage(lib/navState)로 넘어간다.
  const first = renderShell("admin", "/admin/dashboard");
  await screen.findByRole("link", { name: "계정" });
  await userEvent.click(screen.getByRole("button", { name: "관리" }));
  expect(screen.queryByRole("link", { name: "계정" })).toBeNull();
  first.unmount();                    // 경로 이동 = 리마운트 모사
  const second = renderShell("admin", "/jobs");
  // findByRole: admin 전용 그룹은 me 도착 후에야 그려진다.
  await screen.findByRole("link", { name: "대시보드" });
  expect(screen.queryByRole("link", { name: "계정" })).toBeNull();   // 접은 채 유지
  second.unmount();
  renderShell("admin", "/admin/accounts");                          // 관리 그룹의 화면으로 이동
  const header = await screen.findByRole("button", { name: "관리" });
  expect(header).toHaveAttribute("aria-expanded", "false");        // 여전히 접힘(자동 펼침 없음)
  expect(screen.queryByRole("link", { name: "계정" })).toBeNull();
  // 대신 헤더가 "현재 화면" 을 알린다(이름은 그룹 라벨 그대로, 표식은 설명으로).
  expect(header).toHaveAccessibleDescription("현재 화면");
  expect(header.className).toContain("text-accent");
  // 다른(펼친) 그룹 헤더엔 표식이 없다.
  expect(screen.getByRole("button", { name: "작업" })).not.toHaveAccessibleDescription("현재 화면");
  // 그룹 헤더는 라벨 속성으로 이름을 달지 않는다 -- getByLabel 류가 폼 필드와 함께 주워, "스토리지"
  // 그룹이 단일 작업 폼의 "스토리지" 셀렉트와 겹쳤다(e2e E4 strict 위반 회귀).
  expect(header).not.toHaveAttribute("aria-label");
  expect(screen.queryAllByLabelText("스토리지")).toHaveLength(0);
});

test("열린 그룹 헤더 재클릭은 닫고, 다시 클릭이 복원한다 -- 다른 그룹은 무영향", async () => {
  renderShell("admin", "/jobs");
  // 운영 헤더는 admin 전용이라 me 도착 후에야 그려진다 -- 공용 링크(단일 작업)만
  // 기다리면 이 버튼이 아직 없다. 운영은 기본 펼침 -- 작업 토글이 이것을 건드리면 안 된다.
  await screen.findByRole("button", { name: "운영" });
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "작업" }));
  expect(screen.queryByRole("link", { name: "단일 작업" })).toBeNull();
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "작업" }));
  expect(screen.getByRole("link", { name: "단일 작업" })).toBeInTheDocument();
});

test("배치 작업은 작업 그룹 안에 있다 -- 운영을 접어도 보이고, 작업을 접으면 함께 숨는다", async () => {
  // 사용자 결정(2026-10-01): 배치 작업 메뉴를 운영 → 작업 그룹으로.
  renderShell("admin", "/admin/dashboard");
  await screen.findByRole("link", { name: "배치 작업" });
  await userEvent.click(screen.getByRole("button", { name: "운영" }));
  expect(screen.getByRole("link", { name: "배치 작업" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "작업" }));
  expect(screen.queryByRole("link", { name: "배치 작업" })).toBeNull();
});
