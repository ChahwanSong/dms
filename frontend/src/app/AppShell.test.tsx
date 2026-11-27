import { render, screen, waitFor } from "@testing-library/react";
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

// 관리자 전용 링크 표본(전수·그룹 소속은 navigation.test 가 본다 -- 2026-11-25 빌드·릴리스·컨트롤 상태·아티팩트 경로는 운영 → 관리).
// 라벨은 기존 문구 그대로.
// 배치 스케줄러(2026-11-16)는 작업 그룹 안의 관리자 전용 항목 -- 배치 작업과 같은 숨김 규칙. 마운트 현황(2026-11-23)은 스토리지 그룹.
const ADMIN_ONLY_LABELS = [
  "스토리지", "마운트 현황", "노드", "아티팩트 경로", "대시보드", "배치 작업", "배치 스케줄러",
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

test("aria-current 는 최장 일치 항목 하나에만 -- /jobs/new 에서 「단일 작업」만이고 접두 부모(전체 작업)는 아니다", async () => {
  // dms-k8s d1b806e2 이식: NavLink 가 음영(activeNavPath)과 별개로 접두 일치마다 aria-current="page"·active 클래스를 붙여
  // /jobs/new 에서 「전체 작업」(/jobs)까지 현재 페이지로 읽혔다. 상세 경로(요청 상세·빌드 이력)는 음영과 같은 판정으로
  // 부모 항목 하나가 현재다 -- Link 로 바꾸며 이 부모 표시를 잃지 않는지도 함께 본다.
  for (const [at, label] of [
    ["/jobs/new", "단일 작업"], ["/jobs", "전체 작업"], ["/jobs/req-1", "전체 작업"], ["/admin/builds/history", "빌드"],
  ] as const) {
    const { unmount, container } = renderShell("admin", at);
    expect(await screen.findByRole("link", { name: "대시보드" })).toBeInTheDocument();
    const current = [...container.querySelectorAll('aside [aria-current="page"]')].map((a) => a.textContent);
    expect(current, at).toEqual([label]);
    expect(container.querySelector("aside a.active")).toBeNull();
    unmount();
  }
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


test("빌드·릴리스·컨트롤 상태·아티팩트 경로는 관리 그룹 안에 있다 -- 운영을 접어도 보이고, 관리를 접으면 함께 숨는다", async () => {
  // 사용자 결정(2026-11-25): 넷을 운영 → 관리 그룹으로(경로·화면 그대로).
  const moved = ["빌드", "릴리스", "컨트롤 상태", "아티팩트 경로"];
  renderShell("admin", "/admin/dashboard");
  await screen.findByRole("link", { name: "빌드" });
  await userEvent.click(screen.getByRole("button", { name: "운영" }));
  expect(screen.queryByRole("link", { name: "대시보드" })).toBeNull();
  for (const label of moved) expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "관리" }));
  for (const label of moved) expect(screen.queryByRole("link", { name: label })).toBeNull();
});

// 본문 열(dms-k8s 08cba773 이식 -- 2026-11-26 사용자 결정 「A안」): main 이 최대 90rem(1440, 패딩 포함)으로 묶여 사이드바 오른쪽
// 영역 가운데에 선다. jsdom 은 기하를 계산하지 않으므로 여기선 클래스만 고정한다 -- 실제 폭·가운데·좁은 창 무변화는 e2e 03
// 「본문 열」 몫이다. 화면(section)은 스스로 mx-auto 하지 않는다(BuildForm·BuildHistory test) -- 가운데 배치는 이 한 곳이다.
test("본문 열: main 은 mx-auto·max-w-[90rem] 이고 폭 고정·overflow 가 없다(사이드바는 그대로 왼쪽 240)", async () => {
  const { container } = renderShell("user");
  await screen.findByText("alice");
  const main = container.querySelector("main")!;
  expect(main).toHaveClass("mx-auto", "w-full", "max-w-[90rem]", "p-5");
  // 고정 폭은 좁은 창에서 줄지 않는다. overflow 는 제출 위저드 하단 액션 바의 sticky(bottom)를 죽인다(main·조상 모두).
  expect(main.className).not.toMatch(/(^|\s)w-\[/);
  for (let el: Element | null = main; el !== null && el !== container; el = el.parentElement)
    expect(el.className).not.toMatch(/overflow-/);
  // 본문 열의 부모는 그대로 flex-1 min-w-0(L1), 사이드바는 md:w-60 md:shrink-0(L3) -- 가운데 배치가 aside 를 건드리지 않는다.
  expect(main.parentElement).toHaveClass("flex-1", "min-w-0");
  expect(container.querySelector("aside")).toHaveClass("md:w-60", "md:shrink-0");
  expect(container.querySelector("aside")!.className).not.toMatch(/mx-auto/);
});

// 포탈 서브네임(2026-10-02): 사이드바 이름 아래 한 줄 + 브라우저 탭 제목 "메인 - 서브". 없으면 메인 이름만.
test("서브네임이 있으면 사이드바 이름 아래 줄과 탭 제목에 쓰인다", async () => {
  server.use(http.get("/api/portal-info", () => HttpResponse.json({ subtitle: "DAI-CAE" })));
  renderShell("admin");
  expect(await screen.findByLabelText("포탈 서브네임")).toHaveTextContent("DAI-CAE");
  expect(screen.getByText("AI Storage Portal")).toBeInTheDocument();
  await waitFor(() => expect(document.title).toBe("AI Storage Portal - DAI-CAE"));
});

test("서브네임이 없거나 조회가 실패하면 메인 이름만", async () => {
  server.use(http.get("/api/portal-info", () => HttpResponse.json({ detail: "x" }, { status: 500 })));
  renderShell("user");
  expect(await screen.findByText("AI Storage Portal")).toBeInTheDocument();
  await waitFor(() => expect(document.title).toBe("AI Storage Portal"));
  expect(screen.queryByLabelText("포탈 서브네임")).not.toBeInTheDocument();
});
