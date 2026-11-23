import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test } from "vitest";
import { MountStatusPage } from "./MountStatusPage";
import { EMPTY_NODES, LIVE, NO_STORAGES, VARIETY, WITH_PODS } from "./mountFixtures";
import type { MountStatusResponse } from "./types";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const URL = "/api/admin/mount-status";

function Probe() {
  const loc = useLocation();
  return <output data-testid="search">{loc.search}</output>;
}

function renderPage(body: MountStatusResponse | (() => Response), path = "/admin/storages/mounts") {
  server.use(http.get(URL, () => (typeof body === "function" ? body() : HttpResponse.json(body))));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}><MountStatusPage /><Probe /></MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...utils, qc };
}

const table = () => screen.getByRole("table", { name: "노드별 스토리지 마운트 현황" });

test("데이터 전에는 「–」와 「불러오는 중…」 -- 0 으로 지어내지 않는다", () => {
  renderPage(LIVE);
  expect(screen.getByRole("heading", { name: "마운트 현황", level: 1 })).toBeInTheDocument();
  expect(screen.getByText("불러오는 중…")).toBeInTheDocument();
  expect(screen.getAllByText("–").length).toBeGreaterThanOrEqual(3);
});

test("라이브 모양: 칸·열 머리 요약·타일·범례·각주", async () => {
  renderPage(LIVE);
  await screen.findByRole("table");
  const t = table();
  expect(within(t).getByLabelText("k8s-w1 · cephfs-secondary: 없음")).toBeInTheDocument();
  expect(within(t).getByLabelText("k8s-w2 · cephfs-third: 없음")).toBeInTheDocument();
  expect(within(t).getByLabelText("k8s-w1 · cephfs-dms: 정상")).toBeInTheDocument();
  expect(within(t).getByLabelText("k8s-w1 · DMS 공용: 정상")).toBeInTheDocument();
  expect(within(t).getAllByText("마운트 1/2")).toHaveLength(2);
  expect(within(t).getAllByText("마운트 2/2")).toHaveLength(4);
  expect(within(t).getByRole("columnheader", { name: /DMS 공용/ })).toHaveTextContent("/dms_shared/dms");
  // 타일: 노드 2대 · 마운트 문제 없음 · 공용 정상 2/2대
  expect(screen.getByText("2대")).toBeInTheDocument();
  expect(screen.getByText("없음", { selector: "span.text-ok" })).toBeInTheDocument();
  expect(screen.getByText("정상 2/2대")).toBeInTheDocument();
  const legend = screen.getByRole("list", { name: "범례" });
  expect(within(legend).getAllByRole("listitem")).toHaveLength(6);
  // 칸 수는 「칸」, 모름만 노드 수 「대」(위 「전체 노드 2」와 헷갈리지 않게)
  expect(legend).toHaveTextContent("정상10칸");
  expect(legend).toHaveTextContent("없음2칸");
  expect(legend).toHaveTextContent("모름0대");
  // 열 머리는 칸처럼 가운데 정렬, 면은 bg-surface(bg-panel 이면 중립 꼬리표 「마운트 2/2」의 칩 모양이 사라진다)
  expect(within(t).getByRole("columnheader", { name: /^cephfs-dms/ })).toHaveClass("text-center", "bg-surface");
  expect(within(t).getByRole("columnheader", { name: /^cephfs-dms/ })).not.toHaveClass("bg-panel");
  // 역할 꼬리표는 슬롯이 없으면 원문(공용 코어는 k8s 를 모른다)
  expect(within(t).getByText("k8s_primary")).toBeInTheDocument();
  expect(screen.getByText(/에이전트가 없는 k8s 노드 1대: k8s-cp1\(control-plane\)/)).toBeInTheDocument();
});

test("칸 글자: 모든 갈래 + 공용 디렉터리 + 행 꼬리표", async () => {
  renderPage(VARIETY);
  await screen.findByRole("table");
  const t = table();
  const label = (s: string) => within(t).getByLabelText(s);
  expect(label("n-build · ceph: 정상")).toHaveTextContent("사용 41%");
  expect(label("n-build · sec: 없음")).toBeInTheDocument();
  expect(label("n-broken · ceph: 마운트 빠짐")).toBeInTheDocument();
  expect(label("n-broken · sec: 전파 안 됨")).toBeInTheDocument();
  expect(label("n-ro · ceph: 읽기 전용")).toBeInTheDocument();
  expect(label("n-ro · sec: 점검 불가")).toBeInTheDocument();
  expect(label("n-pend · sec: 확인 대기")).toHaveTextContent("경로 바뀜");
  expect(label("n-stale · ceph: 모름")).toHaveTextContent("지연");
  expect(label("n-broken · DMS 공용: 로컬 디렉터리")).toBeInTheDocument();
  expect(label("n-ro · DMS 공용: 다른 노드와 다름")).toHaveTextContent("xfs");
  expect(label("n-pend · DMS 공용: 있음")).toHaveTextContent("공유 여부 모름");
  expect(label("n-stale · DMS 공용: 모름")).toBeInTheDocument();
  // 행 꼬리표
  const rowOf = (name: string) => within(t).getByRole("button", { name: name }).closest("tr")!;
  expect(within(rowOf("n-build")).getByText("빌드 노드")).toBeInTheDocument();
  expect(within(rowOf("n-build")).getByText("control-plane")).toBeInTheDocument();
  expect(within(rowOf("n-broken")).getByText("배치 제외")).toBeInTheDocument();
  expect(within(rowOf("n-broken")).getByText("cordon")).toBeInTheDocument();
  expect(within(rowOf("n-pend")).getByText("클러스터에 없음")).toBeInTheDocument();
  expect(within(rowOf("n-stale")).getByText(/^리포트 지연 · 12분 전$/)).toBeInTheDocument();
  // 문제 꼬리표: 고정 첫 열(행 머리)에 수 -- 가로로 민 표·필터 없이도 문제 노드가 보인다. 지연은 「지연」(수 모름)
  const head = (name: string) => within(t).getByRole("rowheader", { name: new RegExp(`^${name}`) });
  expect(within(head("n-broken")).getByText("문제 3")).toBeInTheDocument();
  expect(within(head("n-ro")).getByText("문제 3")).toBeInTheDocument();
  expect(within(head("n-stale")).getByText("지연")).toBeInTheDocument();
  expect(within(head("n-build")).queryByText(/^문제 \d+$/)).toBeNull();
  expect(within(head("n-pend")).queryByText(/^문제 \d+$/)).toBeNull();
  // 지연 행도 노드 이름은 흐리지 않는다(text-ink/50 은 2.8:1)
  expect(head("n-stale").className).not.toMatch(/text-ink\/\d+/);
  // 열 머리 요약: 문제·읽기 전용 수
  expect(within(t).getByRole("columnheader", { name: /^ceph/ })).toHaveTextContent("문제 1");
  expect(within(t).getByRole("columnheader", { name: /^ceph/ })).toHaveTextContent("읽기 전용 1");
  // 타일: 마운트 문제 3칸 · 공용 문제 2대 · 리포트 지연 1대
  expect(screen.getByText("3칸", { selector: "span.text-bad" })).toBeInTheDocument();          // 범례에도 「3칸」(마운트 문제)이 있다
  expect(screen.getByText("문제 2대")).toBeInTheDocument();
  expect(screen.getByText("리포트 지연 1대")).toBeInTheDocument();
  // 각주: 비활성·노드 목록 모름·마운트 지점 모름
  expect(screen.getByText(/비활성 스토리지 1개\(old-nas\)/)).toBeInTheDocument();
  expect(screen.getByText(/k8s 노드 목록을 읽지 못해/)).toBeInTheDocument();
  expect(screen.getByText(/공용 디렉터리의 마운트 지점을 보고하지 않습니다/)).toBeInTheDocument();
});

test("「문제 있는 노드」는 없음·확인 대기만 있는 행을 숨기고 URL 에 남긴다", async () => {
  renderPage(VARIETY);
  await screen.findByRole("table");
  await userEvent.click(screen.getByRole("button", { name: "문제 있는 노드 3" }));
  expect(screen.getByTestId("search")).toHaveTextContent("only=problems");
  const t = table();
  expect(within(t).queryByRole("button", { name: "n-build" })).toBeNull();
  expect(within(t).queryByRole("button", { name: "n-pend" })).toBeNull();
  expect(within(t).getByRole("button", { name: "n-broken" })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "전체 노드 5" }));
  expect(screen.getByTestId("search")).not.toHaveTextContent("only=problems");
});

test("문제 필터 0행: 리포트 지연이 없으면 「문제 있는 노드가 없습니다」", async () => {
  const { unmount } = renderPage(LIVE, "/admin/storages/mounts?only=problems");
  expect(await screen.findByText(/문제 있는 노드가 없습니다/)).toBeInTheDocument();
  expect(screen.queryByText(/읽기 전용은/)).toBeNull();
  expect(screen.queryByRole("table")).toBeNull();
  unmount();
  // 모든 노드에 ro 로 붙인 스토리지(읽기 전용 칸은 있지만 문제 노드 0)면 왜 0 인지 말한다
  renderPage({ ...LIVE, totals: { ...LIVE.totals, read_only: 2 } }, "/admin/storages/mounts?only=problems");
  expect(await screen.findByText(/같은 스토리지를 쓰기 가능으로 붙인 노드가 있을 때만 문제로 셉니다/)).toBeInTheDocument();
});

test("데이터 없이 실패하면 오류 문구, 받은 뒤 재조회가 실패하면 표를 유지하고 띠를 띄운다", async () => {
  const { unmount } = renderPage(() => HttpResponse.json({}, { status: 500 }));
  expect(await screen.findByText("서버 오류가 발생했습니다")).toBeInTheDocument();
  unmount();
  const { qc } = renderPage(LIVE);
  await screen.findByRole("table");
  server.use(http.get(URL, () => HttpResponse.json({}, { status: 500 })));
  await qc.refetchQueries({ queryKey: ["mount-status"] }).catch(() => undefined);
  expect(await screen.findByText(/최근 갱신 실패/)).toBeInTheDocument();
  expect(screen.getByRole("table")).toBeInTheDocument();
});

test("칸에 올리면 즉시 툴팁(경로·설명·KST·사유 코드), 벗어나면 닫힌다", async () => {
  renderPage(VARIETY);
  await screen.findByRole("table");
  const target = within(table()).getByLabelText("n-broken · ceph: 마운트 빠짐");
  fireEvent.mouseEnter(target);
  const tip = await screen.findByRole("tooltip");
  expect(tip).toHaveTextContent("n-broken · ceph");
  expect(tip).toHaveTextContent("/cephfs");
  expect(tip).toHaveTextContent(/빈 로컬 디렉터리만 남았습니다/);
  expect(tip).toHaveTextContent("KST");
  expect(tip).toHaveTextContent("사유 코드 not_a_mountpoint");
  fireEvent.mouseLeave(target);
  await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
  // 공용 디렉터리 칸
  fireEvent.mouseEnter(within(table()).getByLabelText("n-ro · DMS 공용: 다른 노드와 다름"));
  expect(await screen.findByRole("tooltip")).toHaveTextContent("/dms_shared nfs4");
});

test("노드 버튼 → 상세 시트(사유 문장 + 원문 코드 + 경로), 닫으면 그 버튼으로 포커스", async () => {
  renderPage(VARIETY);
  await screen.findByRole("table");
  const button = screen.getByRole("button", { name: "n-ro" });
  await userEvent.click(button);
  const sheet = await screen.findByRole("dialog");
  expect(within(sheet).getByRole("heading", { name: "n-ro" })).toBeInTheDocument();
  expect(sheet).toHaveTextContent(/호스트 루트\(\/host\/root\)가 없어/);
  expect(within(sheet).getByText("host_root_missing")).toBeInTheDocument();
  expect(sheet).toHaveTextContent("/sec");
  expect(sheet).toHaveTextContent("호스트 마운트 표");
  expect(sheet).toHaveTextContent("다른 노드와 다름");
  await userEvent.click(within(sheet).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(button).toHaveFocus());
});

test("?view=cards: 표 없이 노드별 카드 -- 문제 칸이 먼저, 정상은 접는다", async () => {
  renderPage(VARIETY, "/admin/storages/mounts?view=cards");
  const list = await screen.findByRole("list", { name: "노드별 마운트 현황" });
  expect(screen.queryByRole("table")).toBeNull();
  const cards = within(list).getAllByRole("listitem");
  expect(cards).toHaveLength(5);
  const build = cards[0];
  expect(within(build).getByLabelText("n-build · sec: 없음")).toBeInTheDocument();
  expect(build).toHaveTextContent("정상 1 · 없음 1");
  expect(within(build).getByText("정상 1개 보기")).toBeInTheDocument();
  const brokenCard = cards[1];
  expect(within(brokenCard).getByText("문제 3")).toBeInTheDocument();
  // 지연 카드는 같은 머리에 「리포트 지연」 칩이 있어 「지연」 꼬리표를 겹쳐 달지 않는다
  expect(within(cards[4]).queryByTitle(/지금 마운트 상태를 모릅니다/)).toBeNull();
  expect(within(cards[4]).getByText(/^리포트 지연 · /)).toBeInTheDocument();
  const labels = within(brokenCard).getAllByLabelText(/^n-broken · /).map((el) => el.getAttribute("aria-label"));
  expect(labels).toEqual(["n-broken · DMS 공용: 로컬 디렉터리", "n-broken · ceph: 마운트 빠짐", "n-broken · sec: 전파 안 됨"]);
  await userEvent.click(screen.getByRole("button", { name: "표" }));
  expect(await screen.findByRole("table")).toBeInTheDocument();
  expect(screen.getByTestId("search")).toHaveTextContent("view=table");
});

test("빈 상태: 보고한 노드 0 · 활성 스토리지 0(공용 열은 그대로)", async () => {
  const { unmount } = renderPage(EMPTY_NODES);
  expect(await screen.findByText(/보고한 노드가 없습니다/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "노드 화면" })).toHaveAttribute("href", "/admin/nodes");
  unmount();
  renderPage(NO_STORAGES);
  expect(await screen.findByText(/점검할\(활성\) 스토리지가 없습니다/)).toBeInTheDocument();
  expect(within(table()).getByLabelText("k8s-w1 · DMS 공용: 정상")).toBeInTheDocument();
});

test("k8s 덧붙임 키가 없는 응답(공용 코어 모양)에서 슬롯이 없어도 그린다", async () => {
  renderPage(LIVE);
  await screen.findByRole("table");
  expect(screen.queryByText(/PV \d+개/)).toBeNull();
  expect(screen.queryByText("파일시스템")).toBeNull();
});

test("DMS 파드 꼬리표: 공용 디렉터리가 문제인 노드·경로를 못 읽는 파드는 경고, 정상 노드는 정보 꼬리표", async () => {
  renderPage(WITH_PODS);
  await screen.findByRole("table");
  const head = (name: string) => within(table()).getByRole("rowheader", { name: new RegExp(`^${name}`) });
  // 사고 서명: 공용 디렉터리 로컬(n-broken) + 거기 뜬 dms-api -- 빨간 면·아이콘·낭독용 「경고」, 문장은 title
  const incident = within(head("n-broken")).getByText("dms-api").closest("span[title]")!;
  expect(incident).toHaveClass("bg-badbg", "text-bad");
  expect(incident).toHaveTextContent("경고: dms-api");
  expect(incident.getAttribute("title")).toMatch(/이 노드의 DMS 공용 디렉터리: 로컬 디렉터리 — 여기 뜬 dms-api 는/);
  expect(incident.getAttribute("title")).toMatch(/dms-api-n-broken: 아티팩트 경로 점검 실패 .*Ready 대기 중/);
  // 에이전트는 「있음」인데 dms-api 가 못 읽는 노드(n-pend)도 경고 + 「문제」 꼬리표(서버가 공용 열 한 칸으로 셌다)
  const pend = within(head("n-pend")).getByText("dms-api").closest("span[title]")!;
  expect(pend).toHaveClass("bg-badbg");
  expect(within(head("n-pend")).getByText("문제 1")).toBeInTheDocument();
  // 정상 노드의 컨트롤러는 정보 꼬리표(경고 아님)
  const ctl = within(head("n-build")).getByText("컨트롤러").closest("span[title]")!;
  expect(ctl).toHaveClass("bg-infobg");
  expect(ctl).not.toHaveTextContent("경고");
  expect(ctl.getAttribute("title")).toBe("컨트롤러: 아티팩트 경로 점검 정상");
  // 표 밖 노드(에이전트 없음)의 파드는 각주가 말하고, 못 읽는 파드는 빨갛게
  const note = screen.getByText(/표에 없는 노드\(에이전트 보고 없음\)에 뜬 DMS 파드/);
  expect(note).toHaveTextContent("dms-api cp1(아티팩트 경로 점검 정상)");
  expect(within(note).getByText(/^dms-api cp2\(아티팩트 경로 점검 실패/)).toHaveClass("text-bad");
  expect(within(note).getByRole("link", { name: "아티팩트 경로" })).toHaveAttribute("href", "/admin/artifact-base");
  // 「문제 있는 노드」 수는 서버 totals 그대로
  expect(screen.getByRole("button", { name: "문제 있는 노드 4" })).toBeInTheDocument();
});

test("DMS 파드 꼬리표는 카드 보기에도, 노드 시트는 같은 문장을 키보드로 읽게 싣는다", async () => {
  renderPage(WITH_PODS, "/admin/storages/mounts?view=cards");
  const list = await screen.findByRole("list", { name: "노드별 마운트 현황" });
  const brokenCard = within(list).getAllByRole("listitem")[1];
  expect(within(brokenCard).getByText("dms-api").closest("span[title]")).toHaveClass("bg-badbg");
  await userEvent.click(within(brokenCard).getByRole("button", { name: "n-broken" }));
  const sheet = await screen.findByRole("dialog");
  const pods = within(sheet).getByRole("list", { name: "n-broken DMS 파드" });
  expect(pods).toHaveTextContent(/이 노드의 DMS 공용 디렉터리: 로컬 디렉터리 — 여기 뜬 dms-api 는 다른 노드가 쓴/);
  expect(pods).toHaveTextContent(/dms-api-n-broken: 아티팩트 경로 점검 실패 — 최근 작업의 기록 디렉터리가/);
  expect(pods).toHaveTextContent("시작 점검에 막혀 Ready 대기 중(서비스 밖)");
});

test("파드 덧붙임이 없는 응답(control_pods 없음)이면 파드 꼬리표·각주를 그리지 않는다", async () => {
  renderPage(VARIETY);
  await screen.findByRole("table");
  expect(screen.queryByText("dms-api")).toBeNull();
  expect(screen.queryByText(/표에 없는 노드/)).toBeNull();
});
