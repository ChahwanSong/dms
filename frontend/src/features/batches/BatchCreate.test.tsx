// 슬라이스 32: 배치 생성 위저드(4스텝) — 배치 레벨 스토리지·입력 4방식·실행 제어.
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchCreate } from "./BatchCreate";
import { SYNC_INT_FIELDS } from "../jobs/optionRules";

// sync 고급 숫자 옵션의 프리필 기본값(사용자 조정 2026-08-16) — 단건 폼과 **같은**
// 단일 출처(optionRules)를 읽는다. 프리필이라 sync 배치 바디엔 항상 실린다.
const SYNC_NUM_DEFAULTS = {
  batch_files: Number(SYNC_INT_FIELDS.batch_files.prefill),
  bufsize: Number(SYNC_INT_FIELDS.bufsize.prefill),
};

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const STORAGES = [
  { storage_name: "s1", backend_type: "cephfs", status: "Ready" },
  { storage_name: "s2", backend_type: "gpfs", status: "Ready" },
];

// 도구별로 값을 다르게 — 캡션이 "그 정책의 실값"을 읽는지 구분하기 위해.
const policyRow = (tool: string, max_nodes: number, procs_per_node: number,
                   default_priority: string, max_priority: string) => ({
  tool, max_nodes, procs_per_node, queue: "dms-data",
  default_priority, max_priority,
  preview_timeout_seconds: null, execution_timeout_seconds: 3600,
  enabled: 1, updated_at: "2026-08-05T00:00:00Z", updated_by: "admin",
});
const POLICIES = [
  policyRow("scan", 4, 8, "low", "mid"),
  policyRow("dsync", 6, 4, "high", "high"),
  policyRow("nsync", 2, 2, "mid", "mid"),
];

function renderPage(policies: object[] = POLICIES) {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(STORAGES)));
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(policies)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/admin/batches/new"]}>
        <Routes>
          <Route path="/admin/batches/new" element={<BatchCreate />} />
          <Route path="/admin/batches/:id" element={<h1>배치 b9</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function captureCreate() {
  const captured: { body: any } = { body: null };
  server.use(http.post("/api/admin/batches", async ({ request }) => {
    captured.body = await request.json();
    return HttpResponse.json({ batch_id: "b9", status: "Running" }, { status: 202 });
  }));
  return captured;
}

const next = () => screen.getByRole("button", { name: "다음" });

test("scan: 테이블 2행 + 스토리지 → 제출 바디 조립·미지정 키 부재·내비게이션", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());                              // 연산(scan 기본) → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
  await userEvent.type(screen.getByLabelText("2행 경로"), "b");
  await userEvent.click(next());                              // → 실행 제어
  await userEvent.click(next());                              // → 확인·제출
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  expect(await screen.findByRole("heading", { name: "배치 b9" })).toBeInTheDocument();
  // 정확 일치: priority/node_count 미지정 = 키 부재(생략 계약, null≠0)
  expect(captured.body).toEqual({
    operation: "scan", max_concurrency: 2, options: {}, note: null,
    items: [{ storage: "s1", target: "a" }, { storage: "s1", target: "b" }],
  });
});

test("scan 옵션: batch_files·broken_limit·quiet·우선순위·노드 수가 바디에 실린다", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());
  // top_k는 신 dscan(1b93d54)에서 기능 삭제 — 입력 자체가 없어야 한다.
  expect(screen.queryByLabelText("top_k")).toBeNull();
  await userEvent.type(screen.getByLabelText("batch_files"), "0");
  await userEvent.type(screen.getByLabelText("broken_limit"), "500");
  await userEvent.click(screen.getByLabelText("quiet"));
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
  await userEvent.type(screen.getByLabelText("노드 수"), "4");
  await userEvent.click(next());
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  // batch_files 0 = 배칭 끔 — 0이 정상값으로 실려야 한다(빈값 생략과 구분, null≠0).
  expect(captured.body).toMatchObject({
    options: { batch_files: 0, broken_limit: 500, quiet: true },
    priority: "high", node_count: 4,
  });
});

test("scan broken_limit 범위 밖이면 즉답 문구 + 다음 비활성", async () => {
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());
  await userEvent.type(screen.getByLabelText("broken_limit"), "10001");
  expect(screen.getByText("broken_limit는 0..10000 범위의 정수여야 합니다")).toBeInTheDocument();
  expect(next()).toBeDisabled();
});

test("sync: CSV 붙여넣기 반영 → 소스/목적지 짝 조립", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  await userEvent.type(screen.getByLabelText("CSV"), "source,destination\na,b");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  await userEvent.click(next());
  await userEvent.click(next());
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.items).toEqual([{
    source_storage: "s1", source: "a", destination_storage: "s2", destination: "b",
  }]);
});

test("CSV 오류 행이 있으면 다음 비활성 + 행 번호 문구", async () => {
  renderPage();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  await userEvent.type(screen.getByLabelText("CSV"), "a,b\nc");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  expect(await screen.findByText(/2행:/)).toBeInTheDocument();
  expect(next()).toBeDisabled();
});

test("verbose+quiet 상충: 즉답 문구 + 다음 비활성", async () => {
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());
  await userEvent.click(screen.getByLabelText("verbose"));
  await userEvent.click(screen.getByLabelText("quiet"));
  expect(screen.getByText("verbose와 quiet은 함께 쓸 수 없습니다")).toBeInTheDocument();
  expect(next()).toBeDisabled();
});

// 라벨 정정(사용자 결정 2026-08-16): owner_username 은 아티팩트 소유자 기록이
// 아니라 **잡의 실행 신원**이다(identity.resolve_job_identity). 필드·계약은 그대로,
// 라벨·캡션만 사실에 맞춘다 — 특권 실행 고정 안내문과 모순되지 않게.
test("실행 제어 스텝: 특권 실행 고정 안내문 + 실행 신원 입력이 바디·요약에 실린다", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());                              // → 실행 제어
  // 통일 특권 게이트(routes_batches): 배치는 전부 관리자 특권(root) 실행 — 고정 안내
  expect(screen.getByText("이 배치는 관리자 특권(root)으로 실행됩니다.")).toBeInTheDocument();
  expect(screen.getByText(
    "비우면 생성자 본인 신원으로 실행됩니다. 지정하면 그 사용자 신원으로 파일을 "
    + "다룹니다(LDAP 에 없는 계정도 지정할 수 있습니다).")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("실행 신원(선택)"), "alice");
  await userEvent.click(next());                              // → 확인·제출
  // 확인 스텝 요약 = 제출 바디 파생 + 특권 실행 표시(고정 행)
  expect(screen.getByText("실행 권한")).toBeInTheDocument();
  expect(screen.getByText("관리자 특권(root)")).toBeInTheDocument();
  expect(screen.getByText("실행 신원")).toBeInTheDocument();
  expect(screen.getByText("alice")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toMatchObject({ owner_username: "alice" });
});

test("실행 신원이 빈값이면 owner_username 키 부재 — 특권 실행 표시는 항상", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());
  await userEvent.click(next());
  expect(screen.queryByText("실행 신원")).toBeNull();         // 빈값 = 요약 행 부재(기본: 생성자)
  expect(screen.getByText("관리자 특권(root)")).toBeInTheDocument();  // 특권 표시는 고정
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).not.toHaveProperty("owner_username");
});

test("배치 이름: 입력이 바디·확인 요약에 실린다", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());                              // → 실행 제어
  expect(screen.getByLabelText("배치 이름"))
    .toHaveAttribute("placeholder", "예: 8월 정기 스캔 1차");
  await userEvent.type(screen.getByLabelText("배치 이름"), "8월 정기 스캔 1차");
  await userEvent.click(next());                              // → 확인·제출
  expect(screen.getByText("이름")).toBeInTheDocument();
  expect(screen.getByText("8월 정기 스캔 1차")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toMatchObject({ name: "8월 정기 스캔 1차" });
});

test("배치 이름 빈값이면 name 키 부재 — 요약 행도 없다", async () => {
  const captured = captureCreate();
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());
  await userEvent.click(next());
  expect(screen.queryByText("이름")).toBeNull();              // 빈값 = 요약 행 부재
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).not.toHaveProperty("name");
});

// placeholder 계약(희미한 힌트): 각 입력에 실값과 혼동되지 않는 예시가 떠야 한다.
// 경로 예시는 스토리지 기준 상대경로(선행 슬래시 없음) 형태 — 도메인 계약의 가시화.
test("placeholder 힌트(scan): 경로·CSV·실행 제어 필드", async () => {
  renderPage();
  await userEvent.click(next());                              // → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  expect(screen.getByLabelText("1행 경로")).toHaveAttribute("placeholder", "예: team/projects");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  expect(screen.getByLabelText("CSV")).toHaveAttribute("placeholder", "team\nprojects/alpha");
  await userEvent.click(next());                              // → 실행 제어
  // 빈값 = 플래그 생략 = 도구 기본 — placeholder가 그 기본값을 그대로 말한다.
  expect(screen.getByLabelText("batch_files"))
    .toHaveAttribute("placeholder", "기본 1000000 · 0 = 배칭 끔");
  expect(screen.getByLabelText("broken_limit")).toHaveAttribute("placeholder", "기본 100");
  expect(screen.getByLabelText("실행 신원(선택)")).toHaveAttribute("placeholder", "예: cocoa.song");
  expect(screen.getByLabelText("노드 수")).toHaveAttribute("placeholder", "비우면 정책 기본");
  expect(screen.getByLabelText("노드당 프로세스 수")).toHaveAttribute("placeholder", "비우면 정책 기본");
  expect(screen.getByLabelText("동시 실행 상한")).toHaveAttribute("placeholder", "예: 2");
  expect(screen.getByLabelText("메모")).toHaveAttribute("placeholder", "예: 8월 정기 스캔");
});

test("placeholder 힌트(sync): 소스·목적지·CSV(2열 멀티라인)·고급 옵션", async () => {
  renderPage();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());                              // → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  expect(screen.getByLabelText("1행 소스")).toHaveAttribute("placeholder", "예: team/dataset");
  expect(screen.getByLabelText("1행 목적지")).toHaveAttribute("placeholder", "예: backup/dataset");
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  expect(screen.getByLabelText("CSV")).toHaveAttribute(
    "placeholder", "team/dataset,backup/dataset\nprojects/alpha,backup/alpha");
  await userEvent.click(next());                              // → 실행 제어
  await userEvent.click(screen.getByText("고급 옵션"));
  // 프리필이 생긴 뒤 placeholder 의 일은 "예시"가 아니라 **비웠을 때 무슨 일이
  // 나는가"다(사용자 지시 2026-08-16) — 빈값의 의미를 그 자리에서 말한다.
  expect(screen.getByLabelText("batch_files"))
    .toHaveAttribute("placeholder", "비우면 배칭 안 함(도구 기본)");
  expect(screen.getByLabelText("bufsize"))
    .toHaveAttribute("placeholder", "비우면 4 MiB(도구 기본)");
  expect(screen.getByLabelText("chmod")).toHaveAttribute("placeholder", "예: D770,F660");
  expect(screen.getByLabelText("chown")).toHaveAttribute(
    "placeholder", "예: 10003:10000 또는 cocoa.song:mig");
});

// 정책 기본값 실값 캡션(실행 제어): 도구→정책 키는 placement.py TOOL_TO_POLICY 의
// 미러 — scan→"scan", sync→공존 노드면 "dsync", 없으면 "nsync" 폴백. 실값이 떠야
// "빈값 = 정책 기본"이 값 있는 말이 된다. 미조회·비활성은 숨기지 않는다(null≠0).
async function toScanControls() {
  await userEvent.click(next());                              // 연산(scan) → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(next());                              // → 실행 제어
}

async function toSyncControls() {
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());                              // 연산(sync) → 대상·항목
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  await userEvent.click(next());                              // → 실행 제어
}

test("정책 캡션(scan): scan 정책 실값이 노드 수 아래에 뜬다", async () => {
  renderPage();
  await toScanControls();
  expect(await screen.findByText(
    "정책 기본: 최대 4노드 · 노드당 8프로세스")).toBeInTheDocument();
});

test("정책 캡션(sync): dsync 실값 + nsync 폴백 실값 병기", async () => {
  renderPage();
  await userEvent.selectOptions(screen.getByLabelText("연산"), "sync");
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("소스 스토리지"), "s1");
  await userEvent.selectOptions(screen.getByLabelText("목적지 스토리지"), "s2");
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  await userEvent.click(next());                              // → 실행 제어
  expect(await screen.findByText(
    "정책 기본(dsync): 최대 6노드 · 노드당 4프로세스 — 공존 노드가 없으면 "
    + "nsync 정책(최대 2노드 · 노드당 2프로세스)이 적용됩니다")).toBeInTheDocument();
});

test("정책 행이 없으면 생략·'—' 대신 정직한 미조회 문구", async () => {
  renderPage([]);
  await toScanControls();
  expect(await screen.findByText(
    "정책 미조회 — scan 정책 행이 없습니다")).toBeInTheDocument();
});

test("정책 disabled 는 캡션에 그 사실이 표기된다", async () => {
  renderPage([{ ...POLICIES[0], enabled: 0 }]);
  await toScanControls();
  expect(await screen.findByText(
    "정책 기본: 최대 4노드 · 노드당 8프로세스 · 비활성(잡 배치 거부)")).toBeInTheDocument();
});

// 우선순위 정책 실값(슬라이스 32+): 기본 옵션 라벨은 resolve_priority(domain.py)
// 미러 — scan→"scan" 정책, sync→dsync 대표(제출 시점 도구 미정). 상한 캡션은
// _clamp_priority(placement.py) 미러 — 초과 선택을 조용히 누르는 침묵을 미리 말한다.
test("우선순위 기본 옵션(scan): scan 정책 default_priority 실값이 라벨에 뜬다", async () => {
  renderPage();
  await toScanControls();
  expect(await screen.findByRole("option", { name: "(정책 기본: low)" })).toBeInTheDocument();
});

test("우선순위 기본 옵션(sync): dsync 정책 대표의 default_priority 실값", async () => {
  renderPage();
  await toSyncControls();
  // 라벨은 **실제 적용값**만 — nsync 기본값은 요청 경로 어디에서도 적용되지
  // 않으므로(resolve_priority 는 dsync 단독) 라벨에 섞지 않는다. 캡션 몫.
  expect(await screen.findByRole("option", { name: "(정책 기본: high)" })).toBeInTheDocument();
});

test("우선순위 상한 캡션(scan): max_priority 실값 + 조정 예고", async () => {
  renderPage();
  await toScanControls();
  expect(await screen.findByText(
    "정책 상한: mid — 상한을 넘는 선택은 배치 시 상한으로 조정됩니다")).toBeInTheDocument();
});

// sync 캡션은 도구별 묶음(기본+상한) — nsync 기본값도 **표기**하되, 기본값
// **적용**은 dsync 정책 단독(resolve_priority — 제출 시점 도구 미정이라 dsync
// 대표. nsync 폴백 실행이어도 요청에 박힌 기본값은 dsync 것)이고 nsync 는
// 상한(clamp)만 실행 도구 기준으로 실제 적용됨을 그대로 말한다(화면 거짓말 금지).
test("우선순위 캡션(sync): dsync·nsync 기본·상한 도구별 묶음 + 적용 기준 명시", async () => {
  renderPage();
  await toSyncControls();
  expect(await screen.findByText(
    "정책 — dsync: 기본 high · 상한 high / nsync: 기본 mid · 상한 mid. "
    + "기본값 적용은 dsync 기준(제출 시점 도구 미정), 상한은 실행 도구 기준으로 조정됩니다"))
    .toBeInTheDocument();
});

test("정직성 계약(sync): '기본값 적용은 dsync 기준' 문구는 형식 개편에도 남는다", async () => {
  renderPage();
  await toSyncControls();
  expect(await screen.findByText(/기본값 적용은 dsync 기준\(제출 시점 도구 미정\)/))
    .toBeInTheDocument();
});

test("우선순위 캡션(sync): 기본·상한이 전부 같으면 dsync·nsync 한 묶음 축약", async () => {
  renderPage([
    policyRow("scan", 4, 8, "low", "mid"),
    policyRow("dsync", 6, 4, "mid", "high"),
    policyRow("nsync", 2, 2, "mid", "high"),
  ]);
  await toSyncControls();
  expect(await screen.findByText(
    "정책 — dsync·nsync: 기본 mid · 상한 high. "
    + "기본값 적용은 dsync 기준(제출 시점 도구 미정), 상한은 실행 도구 기준으로 조정됩니다"))
    .toBeInTheDocument();
});

test("정책 부재 시 우선순위: 실값 없는 '(정책 기본)' + 상한 캡션 생략(거짓값 금지)", async () => {
  renderPage([]);
  await toScanControls();
  expect(screen.getByRole("option", { name: "(정책 기본)" })).toBeInTheDocument();
  expect(screen.queryByText(/정책 상한/)).toBeNull();
});

test("상한 초과 선택 즉답: 같은 캡션 자리에 조정될 실값이 뜬다", async () => {
  renderPage();
  await toScanControls();
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
  // scan max_priority=mid — 오류가 아니라 조정 예고이므로 톤은 기존 캡션 그대로.
  expect(await screen.findByText(
    "정책 상한: mid — 선택한 high는 상한 mid로 조정됩니다")).toBeInTheDocument();
});

test("상한 초과 선택 즉답(sync): 넘는 도구만 실값으로 — 도구별 묶음 형식 유지", async () => {
  renderPage();
  await toSyncControls();
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
  // dsync 상한 high 는 통과, nsync 상한 mid 만 초과 — 조정은 실제 배치 도구의
  // 정책으로만 일어나므로 넘는 도구만 구체화한다(a4c0e3e 즉답 회귀 방지).
  expect(await screen.findByText(
    "정책 — dsync: 기본 high · 상한 high / nsync: 기본 mid · 상한 mid. "
    + "기본값 적용은 dsync 기준(제출 시점 도구 미정), "
    + "선택한 high는 배치 시 nsync 상한 mid로 조정됩니다")).toBeInTheDocument();
});

// 노드당 프로세스 수 override: 노드 수 입력과 같은 관례의 미러(빈값 = 정책 기본).
test("노드당 프로세스 수: 바디 조립·확인 요약", async () => {
  const captured = captureCreate();
  renderPage();
  await toScanControls();
  await userEvent.type(screen.getByLabelText("노드당 프로세스 수"), "4");
  await userEvent.click(next());                              // → 확인·제출
  // 확인 스텝 요약 = 제출 바디 파생(화면 거짓말 금지). "4"는 위저드 스텝
  // 배지(4스텝)에도 있어 행(dt+dd) 스코프로 단언한다.
  expect(screen.getByText("노드당 프로세스").closest("div")).toHaveTextContent("4");
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toMatchObject({ procs_per_node: 4 });
});

test("노드당 프로세스 수 즉답 검증: 범위 밖이면 문구 + 다음 비활성", async () => {
  renderPage();
  await toScanControls();
  await userEvent.type(screen.getByLabelText("노드당 프로세스 수"), "65");
  expect(screen.getByText(
    "노드당 프로세스 수는 1..64 범위의 정수여야 합니다")).toBeInTheDocument();
  expect(next()).toBeDisabled();
});

test("동시 실행 상한 캡션: 배치 항목(잡) 수 의미 — 노드 수와 무관", async () => {
  renderPage();
  await toScanControls();
  expect(screen.getByText(
    "동시에 실행할 배치 항목(잡) 수 — 잡 하나가 쓰는 노드 수와 무관합니다.")).toBeInTheDocument();
});

// 배치 sync open_noatime 기본 ON(사용자 승인): 배치는 통일 게이트로 항상 특권
// (root) 실행이라 O_NOATIME 권한 제약이 없고, 소스 atime 오염을 막아야 데이터
// 온도(hot/cold) 통계가 정직해진다. dsync 도구 기본은 off(mfu_flist_copy.c:3344)
// 라 DMS 가 명시적으로 켠다. 단건 sync(SubmitJob)는 비특권 경로에서 타인 소유
// 파일 O_NOATIME open 이 EPERM 이라 기본 OFF 유지 — 스코프 밖.
test("sync 기본 제출: open_noatime 기본 ON — 바디에 true 명시, 확인 요약에도 보인다", async () => {
  const captured = captureCreate();
  renderPage();
  await toSyncControls();
  // 고급 옵션 <details> 기본 접힘 그대로 — 상태 초기값이 진실(펼치지 않아도 실린다).
  expect(screen.getByLabelText("open_noatime")).toBeChecked();
  await userEvent.click(next());                              // → 확인·제출
  expect(screen.getByText("옵션").closest("div")).toHaveTextContent("open_noatime");
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  // 계약 변경(사용자 조정 2026-08-16): batch_files·bufsize 프리필이 함께 실린다.
  expect(captured.body.options).toEqual({ ...SYNC_NUM_DEFAULTS, open_noatime: true });
});

test("sync open_noatime 체크 해제: 키 생략(기존 bool 옵션 직렬화 관례)", async () => {
  const captured = captureCreate();
  renderPage();
  await toSyncControls();
  await userEvent.click(screen.getByLabelText("open_noatime"));
  await userEvent.click(next());
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.options).toEqual(SYNC_NUM_DEFAULTS);
});

// 프리필 계약(사용자 조정 2026-08-16): 단건 폼과 동일 — 실제 값이 미리 채워지고,
// 지우면 키가 빠져 도구 기본(배칭 안 함 / 4 MiB)으로 돌아간다.
test("sync 고급 숫자 옵션 프리필 — 값·placeholder·캡션, 지우면 키가 빠진다", async () => {
  const captured = captureCreate();
  renderPage();
  await toSyncControls();
  await userEvent.click(screen.getByText("고급 옵션"));
  expect(screen.getByLabelText("batch_files")).toHaveValue(
    SYNC_INT_FIELDS.batch_files.prefill);
  expect(screen.getByLabelText("bufsize")).toHaveValue(
    SYNC_INT_FIELDS.bufsize.prefill);
  expect(screen.getByText(
    "미리 채운 1,000,000 = 기본 배치 사이즈 100만. 비우면 배칭 안 함(도구 기본).",
  )).toBeInTheDocument();
  expect(screen.getByText(
    "미리 채운 4194304 = 4 MiB. 비우면 4 MiB(도구 기본).")).toBeInTheDocument();

  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.click(next());
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.options).toEqual({ open_noatime: true });
});

test("sync batch_files 상한 1,000만 — 넘으면 즉답 문구 + 다음 비활성", async () => {
  renderPage();
  await toSyncControls();
  await userEvent.click(screen.getByText("고급 옵션"));
  await userEvent.type(screen.getByLabelText("batch_files"), "0");   // 1000000 → 10000000
  expect(screen.queryByText(/batch_files는/)).toBeNull();
  await userEvent.type(screen.getByLabelText("batch_files"), "0");   // → 1,000만 초과
  expect(screen.getByText("batch_files는 1..10000000 범위의 정수여야 합니다"))
    .toBeInTheDocument();
  expect(next()).toBeDisabled();
});

// 선택 필드 표기 통일(사용자 지시 2026-08-16): 비워도 되는 입력은 라벨에 (선택).
test("실행 제어의 선택 입력들이 라벨에 (선택) 을 단다(scan)", async () => {
  renderPage();
  await toScanControls();
  expect(screen.getByText("batch_files (선택 · 0..1,000,000,000 · 0 = 배칭 끔)"))
    .toBeInTheDocument();
  expect(screen.getByText(
    "broken_limit (선택 · 0..10,000 · 리포트에 보관할 파손 경로 수)")).toBeInTheDocument();
  expect(screen.getByText("노드 수 (선택 · 1..64, 빈값 = 정책 기본)")).toBeInTheDocument();
  expect(screen.getByText("노드당 프로세스 수 (선택 · 1..64, 빈값 = 정책 기본)"))
    .toBeInTheDocument();
  expect(screen.getByText("배치 이름(선택)")).toBeInTheDocument();
  expect(screen.getByText("메모(선택)")).toBeInTheDocument();
  // 동시 실행 상한은 항상 바디에 실리는 값이라 (선택) 이 아니다 — 표기 남발 금지.
  expect(screen.getByText("동시 실행 상한 (1..64)")).toBeInTheDocument();
});

test("실행 제어의 선택 입력들이 라벨에 (선택) 을 단다(sync 고급)", async () => {
  renderPage();
  await toSyncControls();
  await userEvent.click(screen.getByText("고급 옵션"));
  expect(screen.getByText("batch_files (선택 · 1..10,000,000)")).toBeInTheDocument();
  expect(screen.getByText("bufsize (선택 · 바이트, 4096..1,073,741,824)")).toBeInTheDocument();
  expect(screen.getByText(
    "chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)")).toBeInTheDocument();
  expect(screen.getByText("chown (선택 · user:group 또는 uid:gid)")).toBeInTheDocument();
});

test("scan 배치엔 open_noatime 무관 — options 에 키 부재(sync 전용)", async () => {
  const captured = captureCreate();
  renderPage();
  await toScanControls();
  await userEvent.click(next());
  await userEvent.click(screen.getByRole("button", { name: "배치 생성" }));
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.options).toEqual({});
});

// nsync 면당 캡션(정직화): resolve_fanout 은 nsync(공존 노드 없음 폴백)에서
// max_nodes·요청 node_count 를 출발·목적지 **각각**에 캡한다(placement.py:121-122
// 면당 캡) — 총 노드는 최대 2배. dsync 는 공존 노드 단일 집합(primary)이라 무관.
test("노드 수 면당 캡션(sync): nsync 폴백 시 면당 상한·총 최대 2배 문구", async () => {
  renderPage();
  await toSyncControls();
  expect(screen.getByText(
    "nsync 폴백 시 입력한 노드 수는 출발·목적지 각각(면당)의 상한이라 "
    + "총 노드는 최대 2배가 될 수 있습니다.")).toBeInTheDocument();
});

test("노드 수 면당 캡션은 scan 에 없다(sync 전용)", async () => {
  renderPage();
  await toScanControls();
  expect(screen.queryByText(/면당/)).toBeNull();
});

test("파일 업로드: 로컬 FileReader 로 파싱해 테이블에 반영", async () => {
  renderPage();
  await userEvent.click(next());
  await userEvent.selectOptions(await screen.findByLabelText("스토리지"), "s1");
  await userEvent.click(screen.getByRole("button", { name: "파일 업로드" }));
  const file = new File(["target\nx/y"], "items.csv", { type: "text/csv" });
  await userEvent.upload(screen.getByLabelText("CSV 파일"), file);
  // 파싱 성공 → 테이블 편집 탭으로 복귀, 행이 반영돼 있다
  expect(await screen.findByDisplayValue("x/y")).toBeInTheDocument();
});
