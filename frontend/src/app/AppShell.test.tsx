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
// 접힘 규칙: defaultCollapsed 그룹(작업·스토리지·관리)은 접힘 기본, 운영은 펼침 --
// 단 **현재 경로가 속한 그룹은 항상 펼쳐진다**(자동 펼침). 이 성질 덕에 e2e 04·
// router.test 의 "사이드바 링크 클릭"이 접힘 기본에서도 링크를 찾는다(잡 화면에
// 있으면 작업 그룹이 열려 있다).

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

function renderShell(role: "user" | "admin", at = "/jobs") {
  const actor = role === "admin" ? "admin" : "alice";
  server.use(http.get("/api/auth/me", () => HttpResponse.json({ actor, role })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[at]}>
        <AppShell>
          <div>본문</div>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { actor };
}

// 구 AppShell 실측 16링크(작업4+스토리지3+운영5+관리4). 라벨은 기존 문구 그대로.
const ADMIN_ONLY_LABELS = [
  "scan 실행", "스토리지", "노드", "아티팩트 경로", "대시보드", "배치 작업",
  "빌드", "릴리스", "컨트롤 상태", "계정", "정책", "denylist", "감사 로그",
];
const USER_LABELS = ["내 작업", "작업 제출", "내 스캔 경로"];

test("user 는 작업 그룹 3링크만 보이고 admin 전용 그룹은 없다", async () => {
  renderShell("user");
  // me 도착을 먼저 기다린다 -- 기다리지 않으면 "adminOnly 부재" 단언이 로딩 중
  // 화면을 보고 공허하게 통과한다(데이터가 오기 전엔 누구든 user 로 보인다).
  await screen.findByText("alice · user");
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

test("접힘 그룹은 헤더 클릭으로 펼쳐진다(대시보드에서 스토리지 열기)", async () => {
  renderShell("admin", "/admin/dashboard");
  await screen.findByRole("link", { name: "대시보드" });
  expect(screen.queryByRole("link", { name: "노드" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "스토리지" }));
  expect(screen.getByRole("link", { name: "노드" })).toBeInTheDocument();
});

test("최상위 섹션은 DMS 뿐 -- NAS·Monitoring 링크는 없다(추후 추가)", async () => {
  renderShell("user");
  await screen.findByText("alice · user");
  expect(screen.queryByRole("link", { name: "NAS" })).toBeNull();
  expect(screen.queryByRole("link", { name: "Monitoring" })).toBeNull();
});

test("로그아웃 버튼 접근성 이름은 '로그아웃'이다(e2e 01·router.test 와 삼중 계약)", async () => {
  renderShell("user");
  expect(await screen.findByRole("button", { name: "로그아웃" })).toBeInTheDocument();
});

test("활성 그룹 자동 펼침: /jobs 마운트면 접힘 기본인 작업 그룹이 열려 있다", async () => {
  // e2e 04·router.test 의 "사이드바 링크 클릭"이 기대는 성질 -- 접힘 기본이어도
  // 지금 보고 있는 화면의 그룹은 항상 열려 있어 자기 위치를 잃지 않는다.
  renderShell("admin", "/jobs");
  expect(await screen.findByRole("link", { name: "내 작업" })).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "작업 제출" })).toBeInTheDocument();
  // 비활성 접힘 그룹은 여전히 닫혀 있다.
  expect(screen.queryByRole("link", { name: "계정" })).toBeNull();
});

test("그룹 접기 토글: 클릭 시 그룹 링크가 사라지고 재클릭이 복원한다", async () => {
  renderShell("admin", "/jobs");
  await screen.findByRole("link", { name: "작업 제출" });
  await userEvent.click(screen.getByRole("button", { name: "작업" }));
  expect(screen.queryByRole("link", { name: "작업 제출" })).toBeNull();
  // 다른 그룹은 접히지 않는다 -- 상태가 그룹 단위임을 함께 못 박는다(운영은 기본 펼침).
  expect(screen.getByRole("link", { name: "대시보드" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "작업" }));
  expect(screen.getByRole("link", { name: "작업 제출" })).toBeInTheDocument();
});
