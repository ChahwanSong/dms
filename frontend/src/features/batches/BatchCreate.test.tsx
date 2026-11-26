// 슬라이스 32: 배치 생성 — 배치 레벨 스토리지·입력 방식(표·CSV 붙여넣기·파일)·실행 제어.
// 2026-10-06: 4스텝 위저드 → 한 장짜리 시트 + 오른쪽 생성 요약. 2026-11-26: 단계별 등록(사용자 결정 「초안 1 상단 스테퍼」 --
// 작업 종류 → 스토리지 → 항목 → 옵션·실행 제어 → 이름·검토). 단계의 막힘은 「다음」(aria-disabled) + 바 이유 줄로, 최종 막힘은
// 「배치 생성」 disabled 로 단언한다. **제출 바디 단언(toEqual)은 원문 보존** -- 단계로 나눠도 바디는 바이트 단위로 같아야 한다.
// 연산·스토리지는 카드형 라디오(접근 이름은 옛 select·옵션 글자 그대로 -- 연산 이름·「이름 (백엔드)」), 칸 옆 긴 설명은 「요약 + 자세히」
// (MoreText -- 접혀도 DOM 에 있어 「보인다」는 toBeVisible), 검토는 카드 + 「수정」.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchCreate } from "./BatchCreate";
import { STORAGE_EMPTY_CSV_BAR } from "./batchGates";
import {
  BUFSIZE_HELP, DIRECT_HELP, HELP_SUMMARY, OPEN_NOATIME_HELP, QUIET_HELP, SYNC_DSYNC_SPECIAL_FILE_NOTE, SYNC_INT_FIELDS,
  SYNC_MAX_ERRORS_NOTE, SYNC_OPTION_HELP,
} from "../jobs/optionRules";
import { RM_PATH_LINK_RULE, SYNC_PATH_LINK_RULE } from "../../lib/storagePaths";
import { REASON_MESSAGES } from "../../lib/api";
import { NODE_CPU_CAP_NOTE } from "../../lib/jobTool";
import { parseCsvHandler } from "../../test/batchCsvFake";
import {
  buttonsWithoutType, chooseOp, goStep, next, nextButton, pickStorageCard, reasonLine, stepCard, stepHeading, storageChecked, toReview,
} from "../../test/wizard";

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

function mount(entry = "/admin/batches/new") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[entry]}>
        <Routes>
          <Route path="/admin/batches/new" element={<BatchCreate />} />
          <Route path="/admin/batches/:id" element={<h1>배치 b9</h1>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function renderPage(policies: object[] = POLICIES, entry?: string) {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(STORAGES)));
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(policies)));
  // CSV 해석은 서버(2026-10-26) -- 시험용 축소판(test/batchCsvFake). 테스트가 server.use 로 덮을 수 있다.
  server.use(parseCsvHandler());
  mount(entry);
}

function captureCreate() {
  const captured: { body: any; count: number } = { body: null, count: 0 };
  server.use(http.post("/api/admin/batches", async ({ request }) => {
    captured.body = await request.json();
    captured.count += 1;
    return HttpResponse.json({ batch_id: "b9", status: "Running" }, { status: 202 });
  }));
  return captured;
}

const createButton = () => screen.getByRole("button", { name: "배치 생성" });

// ---- 단계 이동 헬퍼(끝 상태는 「그 단계에 머문다」 -- 바디 시험은 그 뒤 toReview() → 「배치 생성」) ----------------------------

/** ① 연산 카드 고르기(radiogroup 「연산」 -- 라디오 이름 = 연산 이름). */
const chooseOperation = chooseOp;

// ② 스토리지 카드 고르기 -- 라디오 접근 이름 = 「이름 (백엔드)」(BatchStoragePicker storageOptionText) 라 이름 앞부분으로 찾는다.
// 목록 로드를 카드 등장으로 기다린다(findByRole).
const pickStorage = pickStorageCard;
/** ② 지금 고른 스토리지 이름(없으면 ""). */
const storageValue = (label: string) => (storageChecked(label) as HTMLInputElement | null)?.value ?? "";

const toStorage = () => next("스토리지");
const toItems = () => next("항목");
const toControls = () => next("옵션·실행 제어");
const toReviewStep = () => toReview("이름·검토");

// scan(기본 연산): ② 스토리지 → ③ 1행 경로. ③ 에 머문다.
async function fillScan() {
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toItems();
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
}

// sync: ① 연산 → ② 소스·목적지 스토리지 → ③ 1행 짝. ③ 에 머문다.
async function fillSync() {
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  await toItems();
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
}

const openAdvanced = () => userEvent.click(screen.getByText("고급 옵션"));

/** 지금 단계의 「다음」이 막혔고(aria-disabled) 바 이유 줄이 reason 이다. */
function expectNextBlocked(reason?: string | RegExp) {
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  if (reason !== undefined) expect(reasonLine()).toHaveTextContent(reason);
}
const expectNextOpen = () => expect(nextButton(), reasonLine()?.textContent ?? "").not.toHaveAttribute("aria-disabled");

/** 검토 카드의 요약 한 줄(dt + dd 래퍼 div). */
const summaryRow = (label: string) =>
  within(stepCard()).getByText(label, { selector: "dt" }).closest("div") as HTMLElement;

// 입력 칸 하나의 묶음(라벨 + 그 아래 오류) -- 같은 문장이 다른 안내에도 나오는 오류를 그 칸으로 좁혀 단언할 때 쓴다.
const fieldBox = (label: string) =>
  screen.getByLabelText(label).closest("label")!.parentElement as HTMLElement;

// ---- 단계 구조 -----------------------------------------------------------------------------------------------------------

test("스테퍼 다섯 단계 · 현재 단계 카드만 그린다 · 처음은 작업 종류이고 「이전」이 잠겨 있다", async () => {
  renderPage();
  const nav = screen.getByRole("navigation", { name: "진행 단계" });
  // 접근 이름 = 「단계명 + 값 + 상태 꼬리」(번호는 aria-hidden) -- 순서대로 다섯
  const names = within(nav).getAllByRole("button").map((b) => b.getAttribute("title"));
  expect(names).toEqual(["작업 종류 — scan", "스토리지", "항목", "옵션·실행 제어", "이름·검토"]);
  expect(stepHeading()).toHaveTextContent("작업 종류");
  expect(within(nav).getByRole("button", { name: /^작업 종류/ })).toHaveAttribute("aria-current", "step");
  // 다른 단계의 칸은 그리지 않는다(값은 화면 최상위 상태에 있다)
  for (const label of ["1행 경로", "batch_files", "우선순위", "배치 이름"]) expect(screen.queryByLabelText(label)).toBeNull();
  expect(screen.queryByRole("button", { name: "배치 생성" })).toBeNull();
  expect(screen.getByRole("button", { name: "이전" })).toHaveAttribute("aria-disabled", "true");
  // 스토리지를 고르지 않았으니 항목 이후는 잠겨 있다(눌러도 그대로)
  await userEvent.click(within(nav).getByRole("button", { name: /^항목/ }));
  expect(stepHeading()).toHaveTextContent("작업 종류");
  expect(within(nav).getByRole("button", { name: /^항목/ })).toHaveAttribute("aria-disabled", "true");
});

test("단계 바는 남은 입력을 필드 문구와 다른 문장으로 말하고, 다 채우면 다음 단계 이름을 보인다", async () => {
  renderPage();
  await toStorage();
  await screen.findByRole("radio", { name: /^s1 / });
  expectNextBlocked("스토리지를 고르세요");
  expect(nextButton()).toHaveAccessibleDescription(/스토리지를 고르세요/);
  // 막힌 「다음」을 눌러도 단계는 그대로
  await userEvent.click(nextButton());
  expect(stepHeading()).toHaveTextContent("스토리지");
  await pickStorage("스토리지", "s1");
  expect(reasonLine()).toHaveTextContent("다음: 항목");
  await toItems();
  expectNextBlocked("경로가 빈 항목 1개를 채우거나 지우세요");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  expect(reasonLine()).toHaveTextContent("다음: 옵션·실행 제어");
  // 행을 전부 지우면 0행 -- 안내가 "추가하세요" 로 바뀐다
  await userEvent.click(screen.getByRole("button", { name: "1행 삭제" }));
  expectNextBlocked("항목을 1개 이상 추가하세요");
  await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await toReviewStep();
  expect(summaryRow("항목 수")).toHaveTextContent(/^항목 수1$/);
  expect(createButton()).toBeEnabled();
  expect(screen.queryByText("스토리지를 고르세요")).toBeNull();
});

test("스토리지 단계 머리말은 연산을 따른다 — scan 은 스토리지 하나, sync 는 소스 → 목적지 한 쌍", async () => {
  renderPage();
  await toStorage();
  expect(screen.getByText("한 배치는 하나의 스토리지만 대상으로 합니다 — 행은 경로만 입력합니다.")).toBeInTheDocument();
  await goStep("작업 종류");
  await chooseOperation("sync");
  await toStorage();
  expect(screen.getByText("한 배치는 소스 → 목적지 스토리지 한 쌍만 대상으로 합니다 — 행은 경로만 입력합니다."))
    .toBeInTheDocument();
  expect(screen.queryByText(/하나의 스토리지만/)).toBeNull();
});

test("입력 방식 세그먼트는 눌린 상태(aria-pressed)를 말한다", async () => {
  renderPage();
  await fillScan();
  expect(screen.getByRole("button", { name: "테이블 편집" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  expect(screen.getByRole("button", { name: "CSV 붙여넣기" })).toHaveAttribute("aria-pressed", "true");
  expect(screen.getByRole("button", { name: "테이블 편집" })).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByLabelText("CSV")).toBeInTheDocument();
});

test("모든 단계의 버튼은 type=\"button\" 이다(폼 제출 버튼이 없다)", async () => {
  renderPage();
  expect(buttonsWithoutType(document.body)).toHaveLength(0);
  await fillSync();
  expect(buttonsWithoutType(document.body)).toHaveLength(0);
  await toControls();
  await openAdvanced();
  expect(buttonsWithoutType(document.body)).toHaveLength(0);
  await toReviewStep();
  expect(buttonsWithoutType(document.body)).toHaveLength(0);
  await goStep("스토리지");
  expect(buttonsWithoutType(document.body)).toHaveLength(0);
});

test("주소의 ?step= 이 아직 못 가는 단계면 처음부터 센 첫 막힘(스토리지)으로 맞춘다 — 빈 폼으로 검토를 보이지 않는다", async () => {
  renderPage(POLICIES, "/admin/batches/new?step=review");
  expect(stepHeading()).toHaveTextContent("스토리지");
});

// ---- 제출 바디 -----------------------------------------------------------------------------------------------------------

test("scan: 테이블 2행 + 스토리지 → 제출 바디 조립·미지정 키 부재·내비게이션", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
  await userEvent.type(screen.getByLabelText("2행 경로"), "b");
  await toReviewStep();
  await userEvent.click(createButton());
  expect(await screen.findByRole("heading", { name: "배치 b9" })).toBeInTheDocument();
  // 정확 일치: priority/node_count 미지정 = 키 부재(생략 계약, null≠0)
  expect(captured.body).toEqual({
    operation: "scan", max_concurrency: 32, options: { batch_files: 1000000, broken_limit: 100 }, note: null,   // scan 프리필 = 서버 기본(2026-09-17), 동시 상한 프리필 32(2026-10-01)
    items: [{ storage: "s1", target: "a" }, { storage: "s1", target: "b" }],
  });
});

test("같은 입력이면 단계를 오가도 바디가 같다 — 끝까지 「다음」 · 옵션을 바꿨다 되돌리고 ①까지 갔다 「검토로 돌아가기」", async () => {
  const expected = {
    operation: "sync", max_concurrency: 32, note: null,
    options: { ...SYNC_NUM_DEFAULTS, open_noatime: true },
    items: [{ source_storage: "s1", source: "a", destination_storage: "s2", destination: "b" }],
  };
  const captured = captureCreate();
  renderPage();
  await fillSync();
  await toControls();
  await userEvent.click(screen.getByLabelText("delete"));
  await userEvent.click(screen.getByLabelText("delete"));
  await toReviewStep();
  await goStep("작업 종류");
  expect(stepHeading()).toHaveTextContent("작업 종류");
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  expect(stepHeading()).toHaveTextContent("이름·검토");
  expect(stepHeading()).toHaveFocus();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toEqual(expected);
});

test("Enter 는 다음 단계로 갈 뿐 배치를 만들지 않는다 — 행 칸은 같은 열 다음 행으로, 마지막 행은 다음 단계로, 검토에선 아무 일도 없다", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
  await userEvent.type(screen.getByLabelText("2행 경로"), "b");
  await userEvent.click(screen.getByLabelText("1행 경로"));
  await userEvent.keyboard("{Enter}");
  expect(screen.getByLabelText("2행 경로")).toHaveFocus();
  expect(stepHeading()).toHaveTextContent("항목");
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(stepHeading()).toHaveTextContent("옵션·실행 제어"));
  // 텍스트 칸 Enter = 「다음」(유효할 때)
  await userEvent.type(screen.getByLabelText("노드 수"), "2{enter}");
  await waitFor(() => expect(stepHeading()).toHaveTextContent("이름·검토"));
  await userEvent.type(screen.getByLabelText("메모"), "x{enter}");
  await userEvent.type(screen.getByLabelText("배치 이름"), "n{enter}");
  await new Promise((r) => setTimeout(r, 150));
  expect(stepHeading()).toHaveTextContent("이름·검토");
  expect(captured.body).toBeNull();
});

test("막힌 단계의 Enter 는 단계를 옮기지 않는다 — 빨간 칸이 있으면 그리로 포커스", async () => {
  renderPage();
  await fillScan();
  await userEvent.clear(screen.getByLabelText("1행 경로"));
  await userEvent.type(screen.getByLabelText("1행 경로"), "{enter}");
  expect(stepHeading()).toHaveTextContent("항목");
  expectNextBlocked("경로가 빈 항목 1개를 채우거나 지우세요");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await toControls();
  await userEvent.type(screen.getByLabelText("노드 수"), "99");
  await userEvent.click(screen.getByLabelText("동시 실행 상한"));
  await userEvent.keyboard("{Enter}");
  expect(stepHeading()).toHaveTextContent("옵션·실행 제어");
  expect(screen.getByLabelText("노드 수")).toHaveFocus();
});

test("④ 에서 「다음」을 더블클릭해도 배치가 만들어지지 않는다 — 두 번째 클릭은 같은 자리 「배치 생성」에 떨어지지만 무시된다", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toControls();
  const btn = nextButton();
  await userEvent.dblClick(btn);
  await new Promise((r) => setTimeout(r, 100));
  expect(stepHeading()).toHaveTextContent("이름·검토");
  expect(btn).toBe(createButton());           // 두 번째 클릭이 실제로 「배치 생성」 노드에 떨어졌다(시험이 헛돌지 않게)
  expect(captured.count).toBe(0);
  await userEvent.click(createButton());      // 검토가 그려진 뒤의 새 클릭은 의도한 생성이다
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.count).toBe(1);
});

test("scan 옵션: batch_files·broken_limit·quiet·우선순위·노드 수가 바디에 실린다", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toControls();
  // top_k는 신 dscan(1b93d54)에서 기능 삭제 — 입력 자체가 없어야 한다.
  expect(screen.queryByLabelText("top_k")).toBeNull();
  await userEvent.clear(screen.getByLabelText("batch_files"));   // 프리필(1,000,000) 지우고 0 명시
  await userEvent.type(screen.getByLabelText("batch_files"), "0");
  await userEvent.clear(screen.getByLabelText("broken_limit"));
  await userEvent.type(screen.getByLabelText("broken_limit"), "500");
  await userEvent.click(screen.getByLabelText("quiet"));
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
  await userEvent.type(screen.getByLabelText("노드 수"), "4");
  await toReviewStep();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  // batch_files 0 = 배칭 끔 — 0이 정상값으로 실려야 한다(빈값 생략과 구분, null≠0).
  expect(captured.body).toMatchObject({
    options: { batch_files: 0, broken_limit: 500, quiet: true },
    priority: "high", node_count: 4,
  });
});

test("scan broken_limit 범위 밖이면 즉답 문구 + 「다음」 잠금", async () => {
  renderPage();
  await fillScan();
  await toControls();
  expectNextOpen();
  await userEvent.clear(screen.getByLabelText("broken_limit"));
  await userEvent.type(screen.getByLabelText("broken_limit"), "10001");
  expect(screen.getByText("broken_limit는 0..10000 범위의 정수여야 합니다")).toBeInTheDocument();
  expectNextBlocked("빨간 안내가 붙은 옵션·실행 제어 값을 고치세요");
});

test("sync: CSV 붙여넣기 반영 → 소스/목적지 짝 조립", async () => {
  const captured = captureCreate();
  renderPage();
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  await toItems();
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  await userEvent.type(screen.getByLabelText("CSV"), "source,destination\na,b");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  expect(await screen.findByDisplayValue("b")).toBeInTheDocument();      // 서버 해석 → 표 반영
  await toReviewStep();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.items).toEqual([{
    source_storage: "s1", source: "a", destination_storage: "s2", destination: "b",
  }]);
});

test("CSV 오류 행이 있으면 「다음」 잠금 + 행 번호 문구", async () => {
  renderPage();
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  await toItems();
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  await userEvent.type(screen.getByLabelText("CSV"), "a,b\nc");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  // 서버 해석의 줄별 사유 -- 물리 줄 번호 + 사유 코드 문구
  expect(await screen.findByText(/^2번째 줄: 열 수가 맞지 않거나/)).toBeInTheDocument();
  expect(nextButton()).toHaveAttribute("aria-disabled", "true");
  // 바 이유는 그 단계의 첫 이유(빈 1행이 먼저) -- 「CSV 오류」 문장은 검토 목록과 같은 순서로 그 뒤에 있다
  expect(reasonLine()).toHaveTextContent("경로가 빈 항목 1개를 채우거나 지우세요");
  await userEvent.type(screen.getByLabelText("CSV"), "{backspace}");
  await userEvent.click(screen.getByRole("button", { name: "테이블 편집" }));
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  expect(reasonLine()).toHaveTextContent("다음: 옵션·실행 제어");   // 수동 편집 = 사용자가 인수 -- 파스 오류는 무효
});

test("verbose+quiet 상충: 즉답 문구 + 「다음」 잠금", async () => {
  renderPage();
  await fillScan();
  await toControls();
  await userEvent.click(screen.getByLabelText("verbose"));
  await userEvent.click(screen.getByLabelText("quiet"));
  expect(screen.getByText("verbose와 quiet은 함께 쓸 수 없습니다")).toBeInTheDocument();
  expectNextBlocked("빨간 안내가 붙은 옵션·실행 제어 값을 고치세요");
});

// 라벨 정정(사용자 결정 2026-08-16): owner_username 은 아티팩트 소유자 기록이
// 아니라 **잡의 실행 신원**이다(identity.resolve_job_identity). 필드·계약은 그대로,
// 라벨·캡션만 사실에 맞춘다 — 특권 실행 고정 안내문과 모순되지 않게.
test("실행 제어: 특권 실행 고정 안내문 + 실행 신원 입력이 바디·검토에 실린다", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toControls();
  // 통일 특권 게이트(routes_batches): 배치는 전부 관리자 특권(root) 실행 — 고정 안내
  expect(screen.getByText("이 배치는 관리자 특권(root)으로 실행됩니다.")).toBeInTheDocument();
  // 2026-09-30 정정: 배치는 root 로 돌고 실행 신원은 기록용 이름이다(파일 권한 검사 없음).
  expect(screen.getByText((_, el) => el?.tagName === "P"
    && (el.textContent ?? "").startsWith("배치는 관리자 이관·정리용이라 root 로 실행됩니다")
    && (el.textContent ?? "").includes("실행 신원은 기록용"))).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("실행 신원(선택)"), "alice");
  await toReviewStep();
  // 검토 = 제출 바디 파생 + 특권 실행 표시(고정 행)
  expect(summaryRow("실행 권한")).toHaveTextContent("관리자 특권(root)");
  expect(summaryRow("실행 신원")).toHaveTextContent("alice");
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toMatchObject({ owner_username: "alice" });
});

test("실행 신원이 빈값이면 owner_username 키 부재 — 특권 실행 표시는 항상", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toReviewStep();
  expect(within(stepCard()).queryByText("실행 신원")).toBeNull();          // 빈값 = 검토 행 부재(기본: 생성자)
  expect(summaryRow("실행 권한")).toHaveTextContent("관리자 특권(root)");   // 특권 표시는 고정
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).not.toHaveProperty("owner_username");
});

test("배치 이름: ⑤ 에서 입력하면 바디에 실리고 스테퍼 값이 된다", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toReviewStep();
  expect(screen.getByLabelText("배치 이름"))
    .toHaveAttribute("placeholder", "예: 8월 정기 스캔 1차");
  await userEvent.type(screen.getByLabelText("배치 이름"), "8월 정기 스캔 1차");
  const nav = screen.getByRole("navigation", { name: "진행 단계" });
  expect(within(nav).getByRole("button", { name: /^이름·검토/ })).toHaveTextContent("8월 정기 스캔 1차");
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toMatchObject({ name: "8월 정기 스캔 1차" });
});

test("배치 이름 빈값이면 name 키 부재", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toReviewStep();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).not.toHaveProperty("name");
});

// placeholder 계약(희미한 힌트): 각 입력에 실값과 혼동되지 않는 예시가 떠야 한다.
// 경로 예시는 스토리지 기준 상대경로(선행 슬래시 없음) 형태 — 도메인 계약의 가시화.
test("placeholder 힌트(scan): 경로·CSV·실행 제어 필드", async () => {
  renderPage();
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toItems();
  expect(screen.getByLabelText("1행 경로")).toHaveAttribute("placeholder", "예: team/projects");
  await userEvent.type(screen.getByLabelText("1행 경로"), "a");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  expect(screen.getByLabelText("CSV")).toHaveAttribute("placeholder", "team\nprojects/alpha");
  await toControls();
  // 프리필 = 서버 기본(2026-09-17) — placeholder 는 비웠을 때 같은 기본이 적용됨을 말한다.
  expect(screen.getByLabelText("batch_files"))
    .toHaveAttribute("placeholder", "비우면 기본 1,000,000 적용 · 0 = 배칭 끔");
  expect(screen.getByLabelText("broken_limit")).toHaveAttribute("placeholder", "비우면 기본 100 적용");
  expect(screen.getByLabelText("실행 신원(선택)")).toHaveAttribute("placeholder", "예: cocoa.song");
  expect(screen.getByLabelText("노드 수")).toHaveAttribute("placeholder", "비우면 정책 기본");
  expect(screen.getByLabelText("노드당 프로세스 수")).toHaveAttribute("placeholder", "비우면 정책 기본");
  expect(screen.getByLabelText("동시 실행 상한")).toHaveAttribute("placeholder", "예: 32");
  await toReviewStep();
  expect(screen.getByLabelText("메모")).toHaveAttribute("placeholder", "예: 8월 정기 스캔");
});

test("placeholder 힌트(sync): 소스·목적지·CSV(2열 멀티라인)·고급 옵션", async () => {
  renderPage();
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  await toItems();
  expect(screen.getByLabelText("1행 소스")).toHaveAttribute("placeholder", "예: team/dataset");
  expect(screen.getByLabelText("1행 목적지")).toHaveAttribute("placeholder", "예: backup/dataset");
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  expect(screen.getByLabelText("CSV")).toHaveAttribute(
    "placeholder", "team/dataset,backup/dataset\nprojects/alpha,backup/alpha");
  await toControls();
  await openAdvanced();
  // 프리필이 생긴 뒤 placeholder 의 일은 "예시"가 아니라 **비웠을 때 무슨 일이
  // 나는가"다(사용자 지시 2026-08-16) — 빈값의 의미를 그 자리에서 말한다.
  expect(screen.getByLabelText("batch_files"))
    .toHaveAttribute("placeholder", "비우면 기본 1,000,000 적용 · 0 = 배칭 끔");
  expect(screen.getByLabelText("bufsize"))
    .toHaveAttribute("placeholder", "비우면 기본 4 MiB 적용");
  expect(screen.getByLabelText("chmod")).toHaveAttribute("placeholder", "예: D770,F660");
  expect(screen.getByLabelText("chown")).toHaveAttribute(
    "placeholder", "예: 10003:10000");
});

// 정책 기본값 실값 캡션(실행 제어): 도구→정책 키는 placement.py TOOL_TO_POLICY 의
// 미러 — scan→"scan", sync→공존 노드면 "dsync", 없으면 "nsync" 폴백. 실값이 떠야
// "빈값 = 정책 기본"이 값 있는 말이 된다. 미조회·비활성은 숨기지 않는다(null≠0).
test("정책 캡션(scan): scan 정책 실값이 노드 수 아래에 뜬다", async () => {
  renderPage();
  await fillScan();
  await toControls();
  expect(await screen.findByText(
    "정책 기본: 최대 4노드 · 노드당 8프로세스")).toBeInTheDocument();
  // 노드 CPU 상한(2026-10-16): 정책·입력값은 노드가 담을 수 있는 만큼까지만 -- 정책 캡션 옆 한 줄(SubmitJob 과 같은 문장)
  expect(screen.getByText(NODE_CPU_CAP_NOTE)).toBeInTheDocument();
});

test("정책 캡션(sync): dsync 실값 + nsync 폴백 실값 병기", async () => {
  renderPage();
  await fillSync();
  await toControls();
  expect(await screen.findByText(
    "정책 기본(dsync): 최대 6노드 · 노드당 4프로세스 — 공존 노드가 없으면 "
    + "nsync 정책(최대 2노드 · 노드당 2프로세스)이 적용됩니다")).toBeInTheDocument();
});

test("정책 행이 없으면 생략·'—' 대신 정직한 미조회 문구", async () => {
  renderPage([]);
  await fillScan();
  await toControls();
  expect(await screen.findByText(
    "정책 미조회 — scan 정책 행이 없습니다")).toBeInTheDocument();
});

test("정책 disabled 는 캡션에 그 사실이 표기된다", async () => {
  renderPage([{ ...POLICIES[0], enabled: 0 }]);
  await fillScan();
  await toControls();
  expect(await screen.findByText(
    "정책 기본: 최대 4노드 · 노드당 8프로세스 · 비활성(잡 배치 거부)")).toBeInTheDocument();
});

// 우선순위 정책 실값(슬라이스 32+): 기본 옵션 라벨은 resolve_priority(domain.py)
// 미러 — scan→"scan" 정책, sync→dsync 대표(제출 시점 도구 미정). 상한 캡션은
// _clamp_priority(placement.py) 미러 — 초과 선택을 조용히 누르는 침묵을 미리 말한다.
test("우선순위 기본 옵션(scan): scan 정책 default_priority 실값이 라벨에 뜬다", async () => {
  renderPage();
  await fillScan();
  await toControls();
  expect(await screen.findByRole("option", { name: "(정책 기본: low)" })).toBeInTheDocument();
});

test("우선순위 기본 옵션(sync): dsync 정책 대표의 default_priority 실값", async () => {
  renderPage();
  await fillSync();
  await toControls();
  // 라벨은 **실제 적용값**만 — nsync 기본값은 요청 경로 어디에서도 적용되지
  // 않으므로(resolve_priority 는 dsync 단독) 라벨에 섞지 않는다. 캡션 몫.
  expect(await screen.findByRole("option", { name: "(정책 기본: high)" })).toBeInTheDocument();
});

test("우선순위 상한 캡션(scan): max_priority 실값 + 조정 예고", async () => {
  renderPage();
  await fillScan();
  await toControls();
  expect(await screen.findByText(
    "정책 상한: mid — 상한을 넘는 선택은 배치 시 상한으로 조정됩니다")).toBeInTheDocument();
});

// sync 캡션은 도구별 묶음 — 기본값 **적용**은 dsync 정책 단독(resolve_priority —
// 제출 시점 도구 미정이라 dsync 대표. nsync 폴백 실행이어도 요청에 박힌 기본값은
// dsync 것)이고 nsync 는 상한(clamp)만 실행 도구 기준으로 실제 적용됨을 그대로
// 말한다(화면 거짓말 금지). 그래서 nsync 행은 상한만 — 저장된 nsync 기본값은
// 쓰이지 않고 정책 화면도 「dsync 따름」으로 잠근다(2026-11-11 B59 후속).
test("우선순위 캡션(sync): dsync 기본·상한 + nsync 상한만 + 적용 기준 명시", async () => {
  renderPage();
  await fillSync();
  await toControls();
  expect(await screen.findByText(
    "정책 — dsync: 기본 high · 상한 high / nsync: 상한 mid. "
    + "기본값 적용은 dsync 기준(제출 시점 도구 미정), 상한은 실행 도구 기준으로 조정됩니다"))
    .toBeInTheDocument();
  expect(screen.queryByText(/nsync: 기본/)).toBeNull();
});

test("우선순위 캡션(sync): 쓰이지 않는 nsync 기본값이 달라도 상한이 같으면 한 묶음 축약(기본은 dsync 값)", async () => {
  renderPage([
    policyRow("scan", 4, 8, "low", "mid"),
    policyRow("dsync", 6, 4, "mid", "high"),
    policyRow("nsync", 2, 2, "low", "high"),
  ]);
  await fillSync();
  await toControls();
  expect(await screen.findByText(
    "정책 — dsync·nsync: 기본 mid · 상한 high. "
    + "기본값 적용은 dsync 기준(제출 시점 도구 미정), 상한은 실행 도구 기준으로 조정됩니다"))
    .toBeInTheDocument();
  expect(screen.queryByText(/기본 low/)).toBeNull();
});

test("정직성 계약(sync): '기본값 적용은 dsync 기준' 문구는 형식 개편에도 남는다", async () => {
  renderPage();
  await fillSync();
  await toControls();
  expect(await screen.findByText(/기본값 적용은 dsync 기준\(제출 시점 도구 미정\)/))
    .toBeInTheDocument();
});

test("우선순위 캡션(sync): 기본·상한이 전부 같으면 dsync·nsync 한 묶음 축약", async () => {
  renderPage([
    policyRow("scan", 4, 8, "low", "mid"),
    policyRow("dsync", 6, 4, "mid", "high"),
    policyRow("nsync", 2, 2, "mid", "high"),
  ]);
  await fillSync();
  await toControls();
  expect(await screen.findByText(
    "정책 — dsync·nsync: 기본 mid · 상한 high. "
    + "기본값 적용은 dsync 기준(제출 시점 도구 미정), 상한은 실행 도구 기준으로 조정됩니다"))
    .toBeInTheDocument();
});

test("정책 부재 시 우선순위: 실값 없는 '(정책 기본)' + 상한 캡션 생략(거짓값 금지)", async () => {
  renderPage([]);
  await fillScan();
  await toControls();
  expect(screen.getByRole("option", { name: "(정책 기본)" })).toBeInTheDocument();
  expect(screen.queryByText(/정책 상한/)).toBeNull();
});

test("상한 초과 선택 즉답: 같은 캡션 자리에 조정될 실값이 뜬다", async () => {
  renderPage();
  await fillScan();
  await toControls();
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
  // scan max_priority=mid — 오류가 아니라 조정 예고이므로 톤은 기존 캡션 그대로.
  expect(await screen.findByText(
    "정책 상한: mid — 선택한 high는 상한 mid로 조정됩니다")).toBeInTheDocument();
});

test("상한 초과 선택 즉답(sync): 넘는 도구만 실값으로 — 도구별 묶음 형식 유지", async () => {
  renderPage();
  await fillSync();
  await toControls();
  await userEvent.selectOptions(screen.getByLabelText("우선순위"), "high");
  // dsync 상한 high 는 통과, nsync 상한 mid 만 초과 — 조정은 실제 배치 도구의
  // 정책으로만 일어나므로 넘는 도구만 구체화한다(a4c0e3e 즉답 회귀 방지).
  expect(await screen.findByText(
    "정책 — dsync: 기본 high · 상한 high / nsync: 상한 mid. "
    + "기본값 적용은 dsync 기준(제출 시점 도구 미정), "
    + "선택한 high는 배치 시 nsync 상한 mid로 조정됩니다")).toBeInTheDocument();
});

// 노드당 프로세스 수 override: 노드 수 입력과 같은 관례의 미러(빈값 = 정책 기본).
test("노드당 프로세스 수: 바디 조립·검토 행", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toControls();
  await userEvent.type(screen.getByLabelText("노드당 프로세스 수"), "4");
  await toReviewStep();
  // 검토 = 제출 바디 파생(화면 거짓말 금지).
  expect(summaryRow("노드당 프로세스")).toHaveTextContent(/^노드당 프로세스4$/);
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).toMatchObject({ procs_per_node: 4 });
});

test("노드당 프로세스 수 즉답 검증: 범위 밖이면 문구 + 「다음」 잠금", async () => {
  renderPage();
  await fillScan();
  await toControls();
  await userEvent.type(screen.getByLabelText("노드당 프로세스 수"), "65");
  expect(screen.getByText(
    "노드당 프로세스 수는 1..64 범위의 정수여야 합니다")).toBeInTheDocument();
  expectNextBlocked();
});

test("동시 실행 상한 캡션: 배치 항목(잡) 수 의미 — 노드 수와 무관", async () => {
  renderPage();
  await fillScan();
  await toControls();
  expect(screen.getByText(
    "동시에 실행할 배치 항목(잡) 수 — 잡 하나가 쓰는 노드 수와 무관합니다.")).toBeInTheDocument();
});

test("동시 실행 상한을 지우면 빈 칸(0 아님) + 오류 + 「다음」 잠금, 스테퍼 값은 0 이 아니라 '?'", async () => {
  renderPage();
  await fillScan();
  await toControls();
  const mc = screen.getByLabelText("동시 실행 상한");
  await userEvent.clear(mc);
  // 결함 회귀 그물(정책 다이얼로그와 같은 유형): number 상태 시절엔 지우는
  // 순간 Number("")=0 이 "0"으로 그려졌다. 칸은 2026-11-11 부터 text + inputMode 라 값은 문자열이다(B14).
  expect(mc).toHaveValue("");
  expect(screen.getByText("동시 실행 상한은 1..64 범위의 정수여야 합니다")).toBeInTheDocument();
  expectNextBlocked("빨간 안내가 붙은 옵션·실행 제어 값을 고치세요");
  expect(within(screen.getByRole("navigation", { name: "진행 단계" })).getByRole("button", { name: /^옵션·실행 제어/ }))
    .toHaveAttribute("title", "옵션·실행 제어 — 동시 ?");
  await userEvent.type(mc, "8");
  expect(mc).toHaveValue("8");
  expectNextOpen();
});

test("숫자 칸(노드 수·노드당 프로세스·batch_files·broken_limit)의 잘못된 글자는 빈값으로 조용히 빠지지 않고 즉답으로 막힌다", async () => {
  // 2026-11-11 (B14): type=number 시절엔 「8e」·(Firefox)「2,000,000」의 value 가 "" 라, 칸엔 친 글자가 남은 채 키가 빠져
  // 정책·서버 기본으로 조용히 생성됐다. 이제 원문을 intFieldError 가 거른다.
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toControls();
  for (const label of ["노드 수", "노드당 프로세스 수", "batch_files", "broken_limit", "동시 실행 상한"])
    expect(screen.getByLabelText(label)).not.toHaveAttribute("type", "number");
  await userEvent.type(screen.getByLabelText("노드 수"), "8e");
  expect(screen.getByLabelText("노드 수")).toHaveValue("8e");
  expect(screen.getByText("노드 수는 1..64 범위의 정수여야 합니다")).toBeInTheDocument();
  expectNextBlocked();
  await userEvent.clear(screen.getByLabelText("노드 수"));
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.type(screen.getByLabelText("batch_files"), "2,000,000");
  expect(screen.getByText("batch_files는 0..1000000000 범위의 정수여야 합니다")).toBeInTheDocument();
  expectNextBlocked();
  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.type(screen.getByLabelText("batch_files"), "2000000");
  await toReviewStep();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.options.batch_files).toBe(2000000);
  expect(captured.body).not.toHaveProperty("node_count");
});

test("메모는 trim 해서 싣고 공백뿐이면 null", async () => {
  // 2026-11-11 (B81): 예전엔 "   " 을 그대로 보내(서버도 그대로 저장) 요약에 빈 메모 행이 보였다.
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toReviewStep();
  await userEvent.type(screen.getByLabelText("메모"), "   ");
  expect(within(stepCard()).queryByText("메모", { selector: "dt" })).toBeNull();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.note).toBeNull();
});

test("메모 앞뒤 공백은 떼고 싣는다", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toReviewStep();
  await userEvent.type(screen.getByLabelText("메모"), "  8월  ");
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.note).toBe("8월");
});

// 배치 sync open_noatime 기본 ON(사용자 승인): 배치는 통일 게이트로 항상 특권
// (root) 실행이라 O_NOATIME 권한 제약이 없고, 소스 atime 오염을 막아야 데이터
// 온도(hot/cold) 통계가 정직해진다. dsync 도구 기본은 off(mfu_flist_copy.c:3344)
// 라 DMS 가 명시적으로 켠다. dms-k8s 는 단건 sync 도 전부 root 라 같은 기본·같은
// 안내 문구(optionRules OPEN_NOATIME_HELP)다.
test("sync 기본 제출: open_noatime 기본 ON — 바디에 true 명시, 검토에도 보인다", async () => {
  const captured = captureCreate();
  renderPage();
  await fillSync();
  await toControls();
  // 고급 옵션 <details> 기본 접힘 그대로 — 상태 초기값이 진실(펼치지 않아도 실린다).
  expect(screen.getByText("고급 옵션").closest("details")!.open).toBe(false);
  expect(screen.getByLabelText("open_noatime")).toBeChecked();
  expect(screen.getByText(OPEN_NOATIME_HELP)).toBeInTheDocument();   // 단건 폼과 같은 문구(한 곳)
  await toReviewStep();
  expect(summaryRow("옵션")).toHaveTextContent("open_noatime");
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  // 계약 변경(사용자 조정 2026-08-16): batch_files·bufsize 프리필이 함께 실린다.
  expect(captured.body.options).toEqual({ ...SYNC_NUM_DEFAULTS, open_noatime: true });
});

test("sync 배치 옵션 설명: delete·contents·direct·quiet 은 요약이 늘 보이고 켜거나 「자세히」면 전문 -- 경로 링크 규칙을 말한다(d190·d192 이식)", async () => {
  // d192: 예전 sync quiet 는 설명이 아예 없었고(괴리 [9]·[13]) direct 는 title 툴팁(+ sr-only)뿐, open_noatime 은 「통계를
  // 정직하게」를 조건 없이 약속했다([C3]). delete 는 「완전히 동일하게」([3]·[25]·[28]), contents 는 「바이트 단위」([24]).
  // 단계별 등록(2026-11-26): 칸 옆 설명은 「요약 한 줄 + 자세히」(MoreText) -- 요약이 핵심 사실(quiet = 실패 원인까지 지움, direct =
  // nsync 에선 무시 + bufsize 조건)을 담고, 전문은 접혀 있다가 「자세히」 한 번 또는 켜면 보인다. 접혀도 전문은 DOM 에 있어
  // getByText 는 찾으므로 「보인다」는 toBeVisible 로 단언한다.
  renderPage();
  await fillSync();
  await toControls();
  for (const summary of [HELP_SUMMARY.delete, HELP_SUMMARY.contents, HELP_SUMMARY.direct, HELP_SUMMARY.quietSync])
    expect(screen.getAllByText(summary)[0]).toBeVisible();
  for (const help of [SYNC_OPTION_HELP.delete, SYNC_OPTION_HELP.contents, DIRECT_HELP, QUIET_HELP.sync])
    expect(screen.getByText(help)).not.toBeVisible();
  expect(HELP_SUMMARY.direct).toContain("dsync 로 실행될 때만 적용되고 켜면 bufsize 조건");
  // 켜면 그 전문이 펼쳐진다(켠 옵션은 설명을 숨기지 않는다)
  await userEvent.click(screen.getByLabelText("direct"));
  expect(screen.getByText(DIRECT_HELP)).toBeVisible();
  // 「자세히」는 버튼(type="button")이고 aria-expanded 로 펼침을 말한다
  const quietCard = screen.getByLabelText("quiet").closest("div.rounded-xl") as HTMLElement;
  const more = within(quietCard).getByRole("button", { name: "자세히" });
  expect(more).toHaveAttribute("aria-expanded", "false");
  await userEvent.click(more);
  expect(screen.getByText(QUIET_HELP.sync)).toBeVisible();
  expect(screen.getByLabelText("direct").closest("label")).not.toHaveAttribute("title");
  // delete 를 꺼도 지우는 dsync 의 경우는 늘 보이는 한 줄(전문을 펼치지 않아도) -- 미리보기에 안 나오는 사실까지
  expect(screen.getByText(SYNC_DSYNC_SPECIAL_FILE_NOTE)).toBeVisible();
  expect(DIRECT_HELP).toMatch(/dsync 로 실행될 때만 적용되고, .*nsync 로 실행되면 무시됩니다/);
  expect(SYNC_OPTION_HELP.delete).not.toContain("완전히 동일하게");
  expect(SYNC_OPTION_HELP.delete).toContain("끄면 그 항목은 실패로 남습니다");
  // 특수 파일의 같은 이름 목적지 항목은 도구마다 다르다(리뷰 portal-batch-26·contract-39 -- nsync 는 그대로, dsync 는 종류가
  // 다른 목적지 항목을 delete 와 무관하게 지우고 FIFO 를 만들지 않아 그 이름이 사라진다, 포크 빌드 실측).
  expect(SYNC_OPTION_HELP.delete).toContain("원본의 FIFO·소켓·장치 파일은 복사하지 않습니다 — 노드 간 실행(nsync)은 대상의 "
    + "같은 이름 항목을 그대로 두고, 한 노드 실행(dsync)은 그 이름의 대상 항목이 다른 종류면 이 옵션과 무관하게 지운 뒤 아무것도 "
    + "만들지 않아 그 이름이 대상에서 사라집니다(비어 있지 않은 디렉토리는 이 옵션을 켜야 지워지고, 끄면 실패로 남습니다).");
  // 읽기 오류로 꺼지는 것은 「원본에 없는 항목 지우기」뿐이다(타입 교체의 목적지 삭제는 그대로)
  expect(SYNC_OPTION_HELP.delete).toContain("원본 일부를 읽지 못한 실행은 안전을 위해 그 실행에서 원본에 없는 항목을 지우지 않습니다");
  expect(SYNC_OPTION_HELP.contents).toContain("SHA-256");
  expect(SYNC_OPTION_HELP.contents).toContain("미리보기가 실패합니다");
  // dms-k8s: 배치·단건 모두 root 라 open_noatime 문구는 하나(OPEN_NOATIME_HELP -- dms-ssc 의 BATCH_OPEN_NOATIME_HELP 를 두지 않는다)
  await openAdvanced();
  expect(screen.getByText(OPEN_NOATIME_HELP)).toBeVisible();
  expect(OPEN_NOATIME_HELP).toContain("읽기가 atime 을 바꾸지 않는 곳에선 차이가 없습니다");
  expect(OPEN_NOATIME_HELP).toContain("(끄면 sync 가 읽은 cold 파일이 hot 이 됩니다)");
  // nsync 실패 한도(괴리 [21])는 sync 실행 옵션 caption -- 요약은 늘, 전문은 「자세히」
  expect(screen.getByText(HELP_SUMMARY.maxErrors)).toBeVisible();
  expect(screen.getByText(SYNC_MAX_ERRORS_NOTE)).not.toBeVisible();
  // d191·통일 봉쇄의 링크 규칙(괴리 [14]) -- ⑤ 검토의 목적지 조건 안내에 들어 있다
  await toReviewStep();
  expect(screen.getByRole("note", { name: "목적지 조건과 소유권" })).toHaveTextContent(SYNC_PATH_LINK_RULE);
});

test("실행 제어의 긴 캡션(실행 신원·정책·CPU 상한·nsync 면당)은 요약이 늘 보이고 「자세히」면 전문", async () => {
  renderPage();
  await fillSync();
  await toControls();
  expect(screen.getByText(HELP_SUMMARY.runAs)).toBeVisible();
  expect(screen.getByText(HELP_SUMMARY.controlsPolicy)).toBeVisible();
  const policy = await screen.findByText(/^정책 기본\(dsync\): 최대 6노드/);
  expect(policy).not.toBeVisible();
  expect(screen.getByText(NODE_CPU_CAP_NOTE)).not.toBeVisible();
  const box = screen.getByText(HELP_SUMMARY.controlsPolicy).closest("div") as HTMLElement;
  await userEvent.click(within(box).getByRole("button", { name: "자세히" }));
  expect(policy).toBeVisible();
  expect(screen.getByText(NODE_CPU_CAP_NOTE)).toBeVisible();
  expect(screen.getByText(/총 노드는 최대 2배가 될 수 있습니다/)).toBeVisible();
});

test("sync 배치: direct 를 켜면 bufsize 는 4096·2^k ≤ 4 MiB 만 — 서버와 같은 문장으로 즉답·잠금, 고친 값은 그대로 실린다", async () => {
  // d192 이식(괴리 [8]): 단건(SubmitJob)과 같은 규칙(optionRules.directBufsizeError).
  const captured = captureCreate();
  renderPage();
  await fillSync();
  await toControls();
  await openAdvanced();
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "1000000");
  expectNextOpen();                                                        // direct 없이는 범위 안이면 된다
  await userEvent.click(screen.getByLabelText("direct"));
  expect(within(fieldBox("bufsize")).getByText(REASON_MESSAGES.direct_bufsize_misaligned)).toBeInTheDocument();
  expectNextBlocked();
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await userEvent.type(screen.getByLabelText("bufsize"), "1048576");
  expect(screen.queryByText(REASON_MESSAGES.direct_bufsize_misaligned)).toBeNull();
  await toReviewStep();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.options).toEqual({ batch_files: SYNC_NUM_DEFAULTS.batch_files, bufsize: 1048576,
                                          direct: true, open_noatime: true });
});

test("sync open_noatime 체크 해제: 키 생략(기존 bool 옵션 직렬화 관례)", async () => {
  const captured = captureCreate();
  renderPage();
  await fillSync();
  await toControls();
  await userEvent.click(screen.getByLabelText("open_noatime"));
  await toReviewStep();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.options).toEqual(SYNC_NUM_DEFAULTS);
});

// 프리필 계약(사용자 조정 2026-08-16): 단건 폼과 동일 — 실제 값이 미리 채워지고,
// 지우면 키가 빠지고 서버가 같은 기본값(1,000,000 / 4 MiB)을 박는다(2026-09-17).
test("sync 고급 숫자 옵션 프리필 — 값·placeholder·캡션, 지우면 키가 빠진다", async () => {
  const captured = captureCreate();
  renderPage();
  await fillSync();
  await toControls();
  await openAdvanced();
  expect(screen.getByLabelText("batch_files")).toHaveValue(
    SYNC_INT_FIELDS.batch_files.prefill);
  expect(screen.getByLabelText("bufsize")).toHaveValue(
    SYNC_INT_FIELDS.bufsize.prefill);
  expect(screen.getByText(
    "미리 채운 1,000,000 = 서버 기본 배치 사이즈. 비워도 같은 값이 적용되며, 배칭을 끄려면 0 을 입력하세요.",
  )).toBeInTheDocument();
  // d192 이식: bufsize 설명에 direct 조건이 붙는다(단건과 같은 BUFSIZE_HELP -- 괴리 [8]).
  expect(screen.getByText(BUFSIZE_HELP)).toBeInTheDocument();
  expect(BUFSIZE_HELP).toMatch(/^미리 채운 4194304 = 4 MiB\(서버 기본\)\. 비워도 같은 값이 적용됩니다\. direct 를 켜면/);

  await userEvent.clear(screen.getByLabelText("batch_files"));
  await userEvent.clear(screen.getByLabelText("bufsize"));
  await toReviewStep();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.options).toEqual({ open_noatime: true });
});

test("sync batch_files 상한 1,000만 — 넘으면 즉답 문구 + 「다음」 잠금", async () => {
  renderPage();
  await fillSync();
  await toControls();
  await openAdvanced();
  await userEvent.type(screen.getByLabelText("batch_files"), "0");   // 1000000 → 10000000
  expect(screen.queryByText(/batch_files는/)).toBeNull();
  await userEvent.type(screen.getByLabelText("batch_files"), "0");   // → 1,000만 초과
  expect(screen.getByText("batch_files는 0..10000000 범위의 정수여야 합니다"))
    .toBeInTheDocument();
  expectNextBlocked();
});

// 선택 필드 표기 통일(사용자 지시 2026-08-16): 비워도 되는 입력은 라벨에 (선택).
test("선택 입력들이 라벨에 (선택) 을 단다(scan)", async () => {
  renderPage();
  await fillScan();
  await toControls();
  expect(screen.getByText("batch_files (선택 · 0..1,000,000,000 · 0 = 배칭 끔)"))
    .toBeInTheDocument();
  expect(screen.getByText(
    "broken_limit (선택 · 0..10,000 · 리포트에 보관할 파손 경로 수)")).toBeInTheDocument();
  expect(screen.getByText("노드 수 (선택 · 1..64, 빈값 = 정책 기본)")).toBeInTheDocument();
  expect(screen.getByText("노드당 프로세스 수 (선택 · 1..64, 빈값 = 정책 기본)"))
    .toBeInTheDocument();
  // 동시 실행 상한은 항상 바디에 실리는 값이라 (선택) 이 아니다 — 표기 남발 금지.
  expect(screen.getByText("동시 실행 상한 (1..64)")).toBeInTheDocument();
  await toReviewStep();
  expect(screen.getByText("배치 이름(선택)")).toBeInTheDocument();
  expect(screen.getByText("메모(선택)")).toBeInTheDocument();
});

test("선택 입력들이 라벨에 (선택) 을 단다(sync 고급)", async () => {
  renderPage();
  await fillSync();
  await toControls();
  await openAdvanced();
  expect(screen.getByText("batch_files (선택 · 0..10,000,000)")).toBeInTheDocument();
  expect(screen.getByText("bufsize (선택 · 바이트, 4096..1,073,741,824)")).toBeInTheDocument();
  expect(screen.getByText(
    "chmod (선택 · 예: D770,F660 — 콤마 구분, D=디렉터리 F=파일)")).toBeInTheDocument();
  expect(screen.getByText("chown (선택 · 숫자 uid:gid)")).toBeInTheDocument();
});

test("scan 배치엔 open_noatime 무관 — options 에 키 부재(sync 전용)", async () => {
  const captured = captureCreate();
  renderPage();
  await fillScan();
  await toReviewStep();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.options).toEqual({ batch_files: 1000000, broken_limit: 100 });   // scan 프리필 = 서버 기본(2026-09-17)
});

// nsync 면당 캡션(정직화): resolve_fanout 은 nsync(공존 노드 없음 폴백)에서
// max_nodes·요청 node_count 를 출발·목적지 **각각**에 캡한다(placement.py:121-122
// 면당 캡) — 총 노드는 최대 2배. dsync 는 공존 노드 단일 집합(primary)이라 무관.
test("노드 수 면당 캡션(sync): nsync 폴백 시 면당 상한·총 최대 2배 문구", async () => {
  renderPage();
  await fillSync();
  await toControls();
  expect(screen.getByText(
    "nsync 폴백 시 입력한 노드 수는 출발·목적지 각각(면당)의 상한이라 "
    + "총 노드는 최대 2배가 될 수 있습니다.")).toBeInTheDocument();
});

test("노드 수 면당 캡션은 scan 에 없다(sync 전용)", async () => {
  renderPage();
  await fillScan();
  await toControls();
  expect(screen.queryByText(/면당/)).toBeNull();
});

test("파일 업로드: 로컬 FileReader 로 파싱해 테이블에 반영", async () => {
  renderPage();
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toItems();
  await userEvent.click(screen.getByRole("button", { name: "파일 업로드" }));
  const file = new File(["target\nx/y"], "items.csv", { type: "text/csv" });
  await userEvent.upload(screen.getByLabelText("CSV 파일"), file);
  // 파싱 성공 → 테이블 편집으로 복귀, 행이 반영돼 있다
  expect(await screen.findByDisplayValue("x/y")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "테이블 편집" })).toHaveAttribute("aria-pressed", "true");
});

// ---- sync 목적지 조건과 소유권(2026-10-01: "목적지가 없는 경우" + 기본 소유 안내) -------------

test("sync 배치: ⑤ 검토가 목적지가 없는 경우·있는 경우와 root 소유권(소스 그대로)을 안내하고 소유 행을 보인다 — ② 는 한 줄", async () => {
  renderPage();
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  // ② 목적지 한 줄(전문은 ⑤)
  expect(screen.getByText(/각 목적지의 상위 디렉토리는 이미 있어야 합니다 · 결과 소유: 소스의 소유자·그룹 그대로\(root 실행\) · 전문은 ⑤ 검토에서/))
    .toBeInTheDocument();
  expect(screen.queryByRole("note", { name: "목적지 조건과 소유권" })).toBeNull();
  await toItems();
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  await toReviewStep();
  const note = screen.getByRole("note", { name: "목적지 조건과 소유권" });
  expect(note).toHaveTextContent("목적지가 없는 경우: sync 가 목적지 디렉토리를 새로 만듭니다");
  expect(note).toHaveTextContent("상위 디렉토리는 이미 있어야 합니다");
  expect(note).toHaveTextContent("목적지가 이미 있는 경우: 디렉토리여야 합니다");
  // 배치는 root(생성 게이트 + IF_ELIGIBLE) -- 요청자 uid:gid 가 아니라 소스 소유 그대로
  expect(note).toHaveTextContent("root 실행이라 목적지와 복사된 파일·디렉토리는 소스의 소유자·그룹을 그대로 유지");
  // 방향어는 단계 이름이다(단계로 나뉜 뒤 「아래」는 거짓)
  expect(note).toHaveTextContent("④ 옵션·실행 제어의 고급 옵션 chown 에 숫자 uid:gid 를 지정하세요");
  expect(note).not.toHaveTextContent("아래 실행 옵션");
  // 자격이 계획 시점에 빠진 자식은 비 root 로 내리지 않고 거부된다(2026-10-26 LDAP 정리 -- 비 root 실행 경로가 없다)
  expect(note).toHaveTextContent("계획 시점에 만든 관리자의 특권 자격이 빠져 있으면 항목은 root 로 돌지 않고 거부됩니다");
  expect(note).not.toHaveTextContent("LDAP");
  expect(note).not.toHaveTextContent("보조 그룹");
  expect(note).not.toHaveTextContent("결과는 실행 신원의 uid:gid(주 그룹) 소유");
  expect(summaryRow("목적지 소유")).toHaveTextContent("소스의 소유자·그룹 그대로(root 실행)");
});

test("sync 배치: chown 을 지정하면 소유 안내와 검토 행이 그 값이 된다", async () => {
  renderPage();
  await fillSync();
  await toControls();
  await openAdvanced();
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  await toReviewStep();
  expect(summaryRow("목적지 소유")).toHaveTextContent("chown 지정값 10003:10000");
  // 같은 화면의 노트도 chown 기준으로 바뀐다(같은 카드 안 모순 금지)
  const note = screen.getByRole("note", { name: "목적지 조건과 소유권" });
  expect(note).toHaveTextContent("chown 옵션으로 지정한 10003:10000 소유로 셋업됩니다");
  // 배치 자식은 root 라 비 root chown 경고(옛 보조 그룹 멤버십 검증)가 없다
  expect(note).not.toHaveTextContent("chown_group_not_member");
  expect(note).not.toHaveTextContent("보조 그룹");
});

test("sync 배치: chown 에 이름을 쓰면 오류로 막는다(숫자 uid:gid 만 -- 서버 chown_name_not_supported)", async () => {
  renderPage();
  await fillSync();
  await toControls();
  await openAdvanced();
  expectNextOpen();
  await userEvent.type(screen.getByLabelText("chown"), "cocoa.song:mig");
  expect(within(fieldBox("chown")).getByText(/chown 은 숫자 uid:gid 만 지정할 수 있습니다/)).toBeInTheDocument();
  expectNextBlocked();
  // 막힌 「다음」을 누르면 빨간 칸(aria-invalid)으로 포커스가 간다
  await userEvent.click(nextButton());
  expect(screen.getByLabelText("chown")).toHaveFocus();
  await userEvent.clear(screen.getByLabelText("chown"));
  await userEvent.type(screen.getByLabelText("chown"), "10003:10000");
  expect(screen.queryByText(/chown 은 숫자 uid:gid 만/)).not.toBeInTheDocument();
  expectNextOpen();
});

test("sync 배치: 고급 옵션에 오류가 있으면 펼쳐진 채 접히지 않는다", async () => {
  renderPage();
  await fillSync();
  await toControls();
  await openAdvanced();
  await userEvent.type(screen.getByLabelText("chmod"), "999x");
  const details = screen.getByText("고급 옵션").closest("details")!;
  await userEvent.click(screen.getByText("고급 옵션"));                     // 접으려 한다
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });  // toggle 이벤트는 비동기
  expect(details.open).toBe(true);
  expect(screen.getByText("chmod 형식이 올바르지 않습니다 (예: D770,F660)")).toBeVisible();
});

test("scan 배치 검토엔 목적지 소유 행이 없다", async () => {
  renderPage();
  await fillScan();
  await toReviewStep();
  expect(createButton()).toBeEnabled();
  expect(screen.queryByText("목적지 소유")).not.toBeInTheDocument();
});

test("sync 를 고르면 만든 뒤 「배치 확인」을 눌러야 실행된다고 미리 말한다", async () => {
  renderPage();
  await chooseOperation("sync");
  expect(screen.getByText(/항목별 미리보기가 끝나면 배치 상세에서 「배치 확인」을 눌러야 실행됩니다\./)).toBeInTheDocument();
});

// ---- 자동 확인(sync 2026-10-11, rm 2026-10-21 — 옵션, 기본 꺼짐) ----------------------------------------------

test("자동 실행: scan 엔 없고 sync 는 기본 꺼짐 — 끈 채로 만들면 auto_confirm 키 부재, 검토는 「직접」", async () => {
  renderPage();
  await fillScan();
  await toControls();
  expect(screen.queryByLabelText("미리보기 후 자동 실행")).toBeNull();          // scan 에는 없다
  const captured = captureCreate();
  await goStep("작업 종류");
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  await toItems();
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");          // 1행 소스는 scan 에서 친 「a」가 그대로 있다
  await toControls();
  expect(screen.getByLabelText("미리보기 후 자동 실행")).not.toBeChecked();
  await toReviewStep();
  expect(summaryRow("실행 확인")).toHaveTextContent("직접 — 미리보기 후 「배치 확인」");
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).not.toHaveProperty("auto_confirm");
});

test("자동 실행을 켜면 바디에 auto_confirm: true, 검토는 「자동」, 연산 안내도 「확인 없이 바로 실행」", async () => {
  renderPage();
  const captured = captureCreate();
  await fillSync();
  await toControls();
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));
  expect(screen.getByText(/확인하는 단계를 건너뜁니다/)).toBeInTheDocument();
  await toReviewStep();
  expect(summaryRow("실행 확인")).toHaveTextContent("자동 — 미리보기가 끝나면 바로 실행");
  await goStep("작업 종류");
  expect(screen.getByText(/「배치 확인」 없이 바로 실행됩니다\(자동 실행 켜짐\)/)).toBeInTheDocument();
  expect(screen.queryByText(/「배치 확인」을 눌러야 실행됩니다/)).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.auto_confirm).toBe(true);
});

test("자동 실행을 켠 뒤 scan 으로 바꾸면 싣지 않는다(서버 422 auto_confirm_sync_only 를 미리 피한다)", async () => {
  renderPage();
  const captured = captureCreate();
  await fillSync();
  await toControls();
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));
  await goStep("작업 종류");
  await chooseOperation("scan");
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toReviewStep();
  expect(within(stepCard()).queryByText("실행 확인")).toBeNull();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body).not.toHaveProperty("auto_confirm");
  expect(captured.body.items).toEqual([{ storage: "s1", target: "a" }]);   // 연산을 바꿔도 행(경로)은 유지
});


// ---- rm 배치(2026-10-21 「배치작업에 rm도」) -------------------------------------------------------
// 항목 모양은 scan(스토리지 하나 + 행당 경로), 확인 흐름은 sync(미리보기 → 「배치 확인」, 자동 실행은 동의와 함께 켤 때만).
const RM_POLICIES = [...POLICIES, policyRow("rm", 3, 2, "mid", "high")];

// rm: ① 연산 → ② 스토리지 → ③ 행. ③ 에 머문다.
async function fillRm(paths: string[] = ["old/a"]) {
  await chooseOperation("rm");
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toItems();
  for (let i = 0; i < paths.length; i += 1) {
    if (i > 0) await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
    await userEvent.type(screen.getByLabelText(`${i + 1}행 경로`), paths[i]);
  }
}

test("rm: 스토리지 하나 + 행당 경로, 바디는 {storage,target} 행 + options {recursive:true} 명시 — 소스·목적지 없음, 자동 실행은 기본 꺼짐", async () => {
  const captured = captureCreate();
  renderPage(RM_POLICIES);
  await fillRm(["old/a", "old/b"]);
  expect(screen.queryByLabelText("1행 목적지")).toBeNull();
  expect(screen.getByText("삭제할 경로")).toBeInTheDocument();
  await goStep("스토리지");
  expect(screen.queryByLabelText("소스 스토리지")).toBeNull();
  await goStep("옵션·실행 제어");
  expect(screen.getByLabelText("재귀 삭제(필수)")).toBeChecked();
  expect(screen.getByLabelText("미리보기 후 자동 실행")).not.toBeChecked();
  await toReviewStep();
  expect(createButton()).toBeEnabled();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.operation).toBe("rm");
  expect(captured.body.items).toEqual([{ storage: "s1", target: "old/a" }, { storage: "s1", target: "old/b" }]);
  expect(captured.body.options).toEqual({ recursive: true });
  expect("auto_confirm" in captured.body).toBe(false);
  expect("acknowledge_irreversible" in captured.body).toBe(false);
});

test("rm: sync 에서 켠 자동 실행은 물려받지 않는다 — rm 으로 바꾸면 꺼진 채 시작, 바디에 auto_confirm 없음, 검토 「직접」", async () => {
  const captured = captureCreate();
  renderPage(RM_POLICIES);
  await fillSync();
  await toControls();
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));
  await goStep("작업 종류");
  await chooseOperation("rm");
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toItems();
  await toControls();
  expect(screen.getByLabelText("미리보기 후 자동 실행")).not.toBeChecked();
  expect(screen.queryByRole("note", { name: "자동 삭제 경고" })).toBeNull();
  await toReviewStep();
  expect(summaryRow("실행 확인")).toHaveTextContent(/직접 — 미리보기 후 「배치 확인」$/);
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect("auto_confirm" in captured.body).toBe(false);
  expect(captured.body.items).toEqual([{ storage: "s1", target: "a" }]);   // 연산을 바꿔도 행(경로)은 유지
});

test("rm: 자동 실행을 켜면 빨간 경고 + 필수 동의 체크 — 체크 전엔 「다음」이 잠기고, 체크하면 auto_confirm·acknowledge_irreversible 을 싣는다", async () => {
  const captured = captureCreate();
  renderPage(RM_POLICIES);
  await fillRm();
  await toControls();
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));
  const warn = screen.getByRole("note", { name: "자동 삭제 경고" });
  expect(warn).toHaveTextContent("미리보기가 끝나면 사람 확인 없이 root 권한으로 삭제됩니다 — 되돌릴 수 없습니다");
  expect(warn).toHaveClass("text-bad");
  const ack = screen.getByLabelText(/되돌릴 수 없다는 것을 이해했습니다/);
  expect(ack).not.toBeChecked();
  expectNextBlocked("자동 실행으로 삭제하려면 경고를 읽고 동의를 체크하세요");
  await userEvent.click(ack);
  await toReviewStep();
  // 켜진 동안 화면의 다른 문장도 「확인 없이 삭제」로 바뀐다 — 「배치 확인」을 눌러야 삭제된다는 문장은 거짓이 된다.
  const row = summaryRow("실행 확인");
  expect(row).toHaveTextContent("자동 — 미리보기가 끝나면 확인 없이 삭제");
  expect(row.querySelector("dd")).toHaveClass("text-bad");
  expect(screen.getByRole("note", { name: "삭제 범위" })).toHaveTextContent("자동 실행이 켜져 있습니다");
  expect(screen.getByText(/자동 실행이 켜져 있어 항목별 미리보기가 끝나는 즉시 「배치 확인」 없이 삭제됩니다/)).toBeInTheDocument();
  expect(screen.queryByText(/「배치 확인」을 눌러야 삭제됩니다/)).toBeNull();
  await goStep("작업 종류");
  expect(screen.getByText(/「배치 확인」 없이 바로 삭제됩니다\(자동 실행 켜짐\)/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  expect(createButton()).toBeEnabled();
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.auto_confirm).toBe(true);
  expect(captured.body.acknowledge_irreversible).toBe(true);
  expect(captured.body.operation).toBe("rm");
});

test("rm: 동의는 ④ 를 떠났다 와도 남는다", async () => {
  renderPage(RM_POLICIES);
  await fillRm();
  await toControls();
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));
  await userEvent.click(screen.getByLabelText(/되돌릴 수 없다는 것을 이해했습니다/));
  await toReviewStep();
  await goStep("옵션·실행 제어");
  expect(screen.getByLabelText(/되돌릴 수 없다는 것을 이해했습니다/)).toBeChecked();
  expectNextOpen();
});

test("rm: 자동 실행을 껐다 켜거나 연산을 바꿨다 오면 동의를 다시 받는다(지난 동의가 이어지지 않는다)", async () => {
  renderPage(RM_POLICIES);
  await fillRm();
  await toControls();
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));
  await userEvent.click(screen.getByLabelText(/되돌릴 수 없다는 것을 이해했습니다/));
  expectNextOpen();
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));      // 끄면 동의 칸도 사라진다
  expect(screen.queryByLabelText(/되돌릴 수 없다는 것을 이해했습니다/)).toBeNull();
  expectNextOpen();                                                         // 끈 rm 은 동의가 필요 없다
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));
  expect(screen.getByLabelText(/되돌릴 수 없다는 것을 이해했습니다/)).not.toBeChecked();
  expectNextBlocked();
  await userEvent.click(screen.getByLabelText(/되돌릴 수 없다는 것을 이해했습니다/));
  await goStep("작업 종류");
  await chooseOperation("scan");
  await chooseOperation("rm");
  await goStep("옵션·실행 제어");
  expect(screen.getByLabelText("미리보기 후 자동 실행")).not.toBeChecked();   // rm 으로 들어오면 꺼진 채
  await userEvent.click(screen.getByLabelText("미리보기 후 자동 실행"));
  expect(screen.getByLabelText(/되돌릴 수 없다는 것을 이해했습니다/)).not.toBeChecked();
  expectNextBlocked();
});

test("rm: 재귀 삭제(필수) 해제·stat+lite 는 즉답과 함께 「다음」이 잠긴다(SubmitJob 과 같은 문구)", async () => {
  renderPage(RM_POLICIES);
  await fillRm();
  await toControls();
  expectNextOpen();
  await userEvent.click(screen.getByLabelText("재귀 삭제(필수)"));
  expect(screen.getByText("재귀 옵션이 필요합니다")).toBeInTheDocument();
  expectNextBlocked();
  await userEvent.click(screen.getByLabelText("재귀 삭제(필수)"));
  await userEvent.click(screen.getByLabelText("stat"));
  await userEvent.click(screen.getByLabelText("lite"));
  expect(screen.getByText("stat과 lite는 함께 쓸 수 없습니다")).toBeInTheDocument();
  expectNextBlocked();
  await userEvent.click(screen.getByLabelText("lite"));
  expectNextOpen();
  await toReviewStep();
  expect(summaryRow("옵션")).toHaveTextContent(/recursive.*stat/);
});

test("rm: 관리 디렉토리 자신(.)·「..」·「/」 시작 행은 행 번호와 이유로 말하고 「다음」이 잠긴다", async () => {
  renderPage(RM_POLICIES);
  await fillRm(["old/a", ".", "/etc", "a/../b"]);
  const list = screen.getByRole("list", { name: "삭제할 수 없는 경로" });
  expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "2행: 관리 디렉토리 자신(.)은 지정할 수 없습니다 — 그 아래 경로를 적으세요",
    "3행: \"/\" 로 시작할 수 없습니다 — 관리 디렉토리 아래 상대경로로 적으세요",
    "4행: \"..\" 은 쓸 수 없습니다 — 관리 디렉토리 밖을 가리킬 수 없습니다",
  ]);
  expectNextBlocked("삭제할 수 없는 경로 3개를 고치세요");
});

test("rm: 한 배치 안 겹침(같은 경로·조상과 자손)은 어느 행끼리인지 말하고 「다음」이 잠긴다 — 형제·접두 이름은 허용", async () => {
  renderPage(RM_POLICIES);
  await fillRm(["team/old", "team/old/sub", "x", "x/", "team/older", "y/a", "y/b"]);
  const list = screen.getByRole("list", { name: "겹치는 삭제 경로" });
  expect(within(list).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
    "2행: 1행의 경로(team/old) 아래입니다 — 상위 경로를 지우면 함께 지워지니 하나만 남기세요",
    "4행: 같은 경로(x)가 3행에도 있습니다",
  ]);
  expectNextBlocked("겹치는 삭제 경로 2개를 정리하세요");
  await userEvent.click(screen.getByRole("button", { name: "4행 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "2행 삭제" }));
  expect(screen.queryByRole("list", { name: "겹치는 삭제 경로" })).toBeNull();
  expectNextOpen();
});

test("rm: ② 삭제 범위 요약 · ⑤ 삭제 범위 전문 · 생성 버튼 위 빨간 경고 · 실행 권한 빨강 — sync 의 목적지·소유권 안내와 sync 옵션은 없다", async () => {
  renderPage(RM_POLICIES);
  await chooseOperation("rm");
  await toStorage();
  const brief = screen.getByRole("note", { name: "삭제 범위 요약" });
  expect(brief).toHaveTextContent("되돌릴 수 없습니다");
  expect(brief).toHaveTextContent("전문은 ⑤ 검토에서");
  expect(brief).toHaveTextContent("만들기만 해서는 아무것도 지워지지 않습니다");
  await pickStorage("스토리지", "s1");
  await toItems();
  await userEvent.type(screen.getByLabelText("1행 경로"), "old/a");
  await toControls();
  expect(screen.getByText(QUIET_HELP.rm)).toBeInTheDocument();
  expect(screen.getByText("이 배치는 관리자 특권(root)으로 삭제합니다 — 파일 권한·소유와 관계없이 지워집니다."))
    .toBeInTheDocument();
  for (const label of ["open_noatime", "chown", "delete", "batch_files", "verbose"])
    expect(screen.queryByLabelText(label)).toBeNull();
  await toReviewStep();
  const scope = screen.getByRole("note", { name: "삭제 범위" });
  expect(scope).toHaveTextContent("되돌릴 수 없습니다");
  expect(scope).toHaveTextContent("관리자 특권(root)으로 실행돼");
  // 링크 규칙(d192 이식 -- dms-k8s 는 중간 단계만 거부, 끝 링크는 링크만 지운다)
  expect(scope).toHaveTextContent(RM_PATH_LINK_RULE);
  expect(RM_PATH_LINK_RULE).toContain("대상 자신이 심볼릭 링크면 링크만 지우고");
  expect(screen.getByText(/삭제는 되돌릴 수 없습니다\. 항목별 미리보기에서/)).toBeInTheDocument();
  expect(summaryRow("실행 권한").querySelector("dd")).toHaveClass("text-bad");
  expect(screen.queryByRole("note", { name: "목적지 조건과 소유권" })).toBeNull();
  expect(screen.queryByText("목적지 소유")).toBeNull();
});

test("rm: 정책 캡션·우선순위 기본·상한이 rm 정책 실값이다(dsync/nsync 병기·nsync 면당 캡션 없음)", async () => {
  renderPage(RM_POLICIES);
  await fillRm();
  await toControls();
  expect(screen.getByText("정책 기본: 최대 3노드 · 노드당 2프로세스")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "(정책 기본: mid)" })).toBeInTheDocument();
  expect(screen.getByText(/^정책 상한: high/)).toBeInTheDocument();
  expect(screen.queryByText(/dsync/)).toBeNull();
  expect(screen.queryByText(/nsync 폴백/)).toBeNull();
});

test("rm: rm 정책 행이 없으면 그 사실을 말한다(지어내지 않는다)", async () => {
  renderPage(POLICIES);
  await fillRm();
  await toControls();
  expect(screen.getByText("정책 미조회 — rm 정책 행이 없습니다")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "(정책 기본)" })).toBeInTheDocument();
});

test("rm: CSV 붙여넣기는 1열(target 헤더 스킵) — 2열은 오류, 반영 결과는 1열 표", async () => {
  renderPage(RM_POLICIES);
  await chooseOperation("rm");
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toItems();
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  expect(screen.getByText(/행당 경로 1개/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("CSV"), "a,b");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  expect(await screen.findByText(/^1번째 줄: 열 수가 맞지 않거나/)).toBeInTheDocument();
  await userEvent.clear(screen.getByLabelText("CSV"));
  await userEvent.type(screen.getByLabelText("CSV"), "target{enter}old/a{enter}old/b");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  expect(await screen.findByDisplayValue("old/a")).toBe(screen.getByLabelText("1행 경로"));
  expect(screen.getByLabelText("2행 경로")).toHaveValue("old/b");
  expectNextOpen();
});

// ---- 서버 CSV 해석·템플릿·스토리지 드롭다운(2026-10-26 BACKLOG C) -----------------------------------------------

test("CSV 반영은 서버 해석을 부른다 -- 고른 배치 스토리지를 함께 보내고, rm 겹침은 줄 번호로 말하며 표를 덮지 않는다", async () => {
  const bodies: any[] = [];
  renderPage(RM_POLICIES);
  server.use(parseCsvHandler((b) => bodies.push(b)));
  await chooseOperation("rm");
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toItems();
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  await userEvent.type(screen.getByLabelText("CSV"), "target{enter}old/a{enter}old/a/sub");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  const errs = await screen.findByRole("list", { name: "반영할 수 없는 줄" });
  expect(errs).toHaveTextContent("3번째 줄: 한 rm 배치에 같은 경로가 두 번 있거나");
  expect(errs).toHaveTextContent("(2번째 줄과 겹침)");
  expect(bodies).toEqual([{ operation: "rm", csv: "target\nold/a\nold/a/sub", storage: "s1" }]);
  // 부분 반영 금지 -- 표는 그대로(빈 1행), 「다음」은 잠긴다
  await userEvent.click(screen.getByRole("button", { name: "테이블 편집" }));
  expect(screen.getByLabelText("1행 경로")).toHaveValue("");
  expect(screen.queryByLabelText("2행 경로")).toBeNull();
  expectNextBlocked();
});

test("「스토리지를 CSV 로 정하기」: 스토리지를 비운 채 ③ 으로 가 CSV 의 스토리지 열(서버가 돌려준 storages)로 채운다", async () => {
  const captured = captureCreate();
  renderPage();
  server.use(http.post("/api/admin/batches:parse-csv", () => HttpResponse.json({
    items: [{ storage: "s2", target: "x" }], errors: [], error_count: 0, rows: 1,
    header: ["storage", "target"], storages: { storage: "s2" } })));
  await toStorage();
  await screen.findByRole("radio", { name: /^s2 / });
  expectNextBlocked("스토리지를 고르세요");
  await userEvent.click(screen.getByRole("button", { name: "스토리지를 CSV 로 정하기" }));
  expect(screen.getByText("비어 있는 스토리지는 CSV 의 storage 열로 채웁니다")).toBeInTheDocument();
  expect(reasonLine()).toHaveTextContent("다음: 항목");
  await toItems();
  // 붙여넣기가 열려 있고, 스토리지가 비어 있다는 바 전용 이유가 먼저 보인다
  expect(screen.getByRole("button", { name: "CSV 붙여넣기" })).toHaveAttribute("aria-pressed", "true");
  expectNextBlocked(STORAGE_EMPTY_CSV_BAR);
  await userEvent.type(screen.getByLabelText("CSV"), "storage,target{enter}s2,x");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  expect(await screen.findByDisplayValue("x")).toBe(screen.getByLabelText("1행 경로"));
  await goStep("스토리지");
  expect(storageValue("스토리지")).toBe("s2");
  await toReviewStep();
  expect(summaryRow("스토리지")).toHaveTextContent("s2");
  await userEvent.click(createButton());
  await screen.findByRole("heading", { name: "배치 b9" });
  expect(captured.body.items).toEqual([{ storage: "s2", target: "x" }]);
});

test("「스토리지를 CSV 로 정하기」는 이미 고른 스토리지를 비우고(CSV 가 이기게), 스토리지를 다시 고르면 꺼진다", async () => {
  renderPage();
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  await userEvent.click(screen.getByRole("button", { name: "스토리지를 CSV 로 정하기" }));
  expect(storageValue("소스 스토리지")).toBe("");
  expect(storageValue("목적지 스토리지")).toBe("");
  expect(screen.getByText("비어 있는 스토리지는 CSV 의 source_storage·destination_storage 열로 채웁니다")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "CSV 로 정하기 취소" }));
  expectNextBlocked("소스·목적지 스토리지를 고르세요");
  await userEvent.click(screen.getByRole("button", { name: "스토리지를 CSV 로 정하기" }));
  await pickStorage("소스 스토리지", "s1");
  expect(screen.getByRole("button", { name: "스토리지를 CSV 로 정하기" })).toBeInTheDocument();   // 꺼졌다
  expectNextBlocked("소스·목적지 스토리지를 고르세요");
});

test("서버 해석 요청이 통째로 실패하면(413) 사유 문구를 오류 줄에 보이고 「다음」이 잠긴다", async () => {
  renderPage();
  server.use(http.post("/api/admin/batches:parse-csv", () =>
    HttpResponse.json({ detail: "batch_csv_too_large" }, { status: 413 })));
  await fillScan();
  await userEvent.click(screen.getByRole("button", { name: "CSV 붙여넣기" }));
  await userEvent.type(screen.getByLabelText("CSV"), "a");
  await userEvent.click(screen.getByRole("button", { name: "테이블에 반영" }));
  expect(await screen.findByText("CSV 가 너무 큽니다(최대 2 MiB) — 나눠서 올리세요")).toBeInTheDocument();
  expectNextBlocked("CSV 오류를 고친 뒤 다시 반영하세요");
});

test("「템플릿 받기」는 지금 연산의 서버 템플릿으로 가는 다운로드 링크다", async () => {
  renderPage();
  await toStorage();
  await pickStorage("스토리지", "s1");
  await toItems();
  const link = screen.getByRole("link", { name: "템플릿 받기" });
  expect(link).toHaveAttribute("href", "/api/admin/batches/csv-template?operation=scan");
  expect(link).toHaveAttribute("download");
  await goStep("작업 종류");
  await chooseOperation("rm");
  await goStep("항목");
  expect(screen.getByRole("link", { name: "템플릿 받기" }))
    .toHaveAttribute("href", "/api/admin/batches/csv-template?operation=rm");
});

function renderWithStorages(storages: object[]) {
  server.use(http.get("/api/user/storages", () => HttpResponse.json(storages)));
  server.use(http.get("/api/admin/policies", () => HttpResponse.json(POLICIES)));
  server.use(parseCsvHandler());
  mount();
}

test("스토리지 카드: 종류별 묶음 + 「이름 (백엔드)」 라디오, 고르면 백엔드·종류·관리 디렉토리 캡션", async () => {
  renderWithStorages([
    { storage_name: "s1", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed", kind: "filesystem" },
    { storage_name: "g1", backend_type: "gpfs", status: "Ready", kind: "both", admin_only: true },
    { storage_name: "n1", backend_type: "nfs", status: "Ready", kind: "pv" },
  ]);
  await toStorage();
  const group = await screen.findByRole("radiogroup", { name: "스토리지" });
  await within(group).findByRole("radio", { name: "s1 (cephfs)" });
  const kinds = within(group).getAllByRole("group");
  expect(kinds.map((g) => g.getAttribute("aria-label"))).toEqual(["파일시스템", "PV·파일시스템", "PV"]);
  expect(within(kinds[1]).getByRole("radio", { name: "g1 (gpfs) · 관리자 전용" })).toBeInTheDocument();
  expect(within(kinds[2]).getByRole("radio", { name: "n1 (nfs)" })).toBeInTheDocument();
  expect(within(kinds[0]).getByText("/cephfs/managed")).toBeInTheDocument();      // 카드에 관리 디렉토리
  await pickStorage("스토리지", "s1");
  expect(screen.getByText("cephfs · 파일시스템 · 관리 디렉토리: /cephfs/managed — 입력 경로는 이 아래 상대경로입니다"))
    .toBeInTheDocument();
  expect(reasonLine()).toHaveTextContent("다음: 항목");
});

test("스토리지 카드 묶음은 묶음마다 다른 라디오 name 이다 — sync 에서 목적지를 골라도 소스 선택이 남는다", async () => {
  renderPage();
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  expect(storageValue("소스 스토리지")).toBe("s1");
  expect(storageValue("목적지 스토리지")).toBe("s2");
  const names = new Set(screen.getAllByRole("radio").map((r) => r.getAttribute("name")));
  expect(names).toEqual(new Set(["batch-storage-src", "batch-storage-dst"]));
});

test("스토리지 목록이 비면 스토리지 관리로 가는 길을 말한다", async () => {
  renderWithStorages([]);
  await toStorage();
  expect(await screen.findByText(/켜진 스토리지가 없습니다/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "스토리지 관리" })).toHaveAttribute("href", "/admin/storages");
  expect(within(screen.getByRole("radiogroup", { name: "스토리지" })).queryAllByRole("radio")).toHaveLength(0);
});

// 2026-10-26 사용자 결정 (4): 직접 경로 목적지의 스토리지가 PV 를 담는 종류면 그 스토리지 기본 소유자(서버가 자식 생성 때 항목마다
// 정해 얼린다 -- pv 는 늘, both 는 항목 경로가 PV 루트 아래일 때만). 목적지 스토리지 종류에 따라 안내·검토가 바뀐다 -- ② 에서 바꾸고
// ⑤ 에서 본다.
test("sync 배치 목적지 소유: pv 종류는 스토리지 기본 소유자, 겸용은 「PV 루트 아래면」, 파일시스템은 소스 그대로", async () => {
  renderWithStorages([
    { storage_name: "s1", backend_type: "cephfs", status: "Ready", managed_root: "/cephfs/managed", kind: "filesystem",
      default_owner: "0:0" },
    { storage_name: "g1", backend_type: "gpfs", status: "Ready", kind: "both", default_owner: "2500:2600" },
    { storage_name: "n1", backend_type: "nfs", status: "Ready", kind: "pv", default_owner: "1500:1500" },
  ]);
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "n1");
  await toItems();
  await userEvent.type(screen.getByLabelText("1행 소스"), "a");
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  const note = () => screen.getByRole("note", { name: "목적지 조건과 소유권" });
  await toReviewStep();
  expect(summaryRow("목적지 소유")).toHaveTextContent("목적지 스토리지 기본 소유자 1500:1500(직접 경로)");
  expect(note()).toHaveTextContent("결과는 목적지 스토리지 기본 소유자 1500:1500 로 맞춰집니다");
  await goStep("스토리지");
  await pickStorage("목적지 스토리지", "g1");
  await toReviewStep();
  expect(summaryRow("목적지 소유")).toHaveTextContent("PV 루트 아래면 목적지 스토리지 기본 소유자 2500:2600, 아니면 소스 그대로");
  await goStep("스토리지");
  await pickStorage("목적지 스토리지", "s1");
  await toReviewStep();
  expect(summaryRow("목적지 소유")).toHaveTextContent("소스의 소유자·그룹 그대로(root 실행)");
  // 명시 chown 이 이긴다
  await goStep("스토리지");
  await pickStorage("목적지 스토리지", "n1");
  await goStep("옵션·실행 제어");
  await openAdvanced();
  await userEvent.type(screen.getByLabelText("chown"), "7:7");
  await toReviewStep();
  expect(summaryRow("목적지 소유")).toHaveTextContent("chown 지정값 7:7");
});

// ---- 검토 카드·수정 점프·겉모양(B2) -------------------------------------------------------------------------------------

test("검토 「수정」은 그 단계로 가 제목에 포커스하고, 바의 「검토로 돌아가기」로 돌아온다 — note 의 「옵션·실행 제어로 가기」는 ④", async () => {
  renderPage();
  await fillSync();
  await toReviewStep();
  await userEvent.click(screen.getByRole("button", { name: "수정: 스토리지" }));
  expect(stepHeading()).toHaveTextContent("스토리지");
  expect(stepHeading()).toHaveFocus();
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  expect(stepHeading()).toHaveTextContent("이름·검토");
  const note = screen.getByRole("note", { name: "목적지 조건과 소유권" });
  await userEvent.click(within(note).getByRole("button", { name: "옵션·실행 제어로 가기" }));
  expect(stepHeading()).toHaveTextContent("옵션·실행 제어");
  await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  for (const [name, title] of [["수정: 작업", "작업 종류"], ["수정: 항목", "항목"], ["수정: 옵션·실행 제어", "옵션·실행 제어"],
                               ["수정: 목적지 조건과 소유권", "스토리지"]] as const) {
    await userEvent.click(screen.getByRole("button", { name }));
    expect(stepHeading()).toHaveTextContent(title);
    await userEvent.click(screen.getByRole("button", { name: "검토로 돌아가기" }));
  }
});

test("검토의 항목 카드는 앞 5행 미니 표 + 「외 N행」(바디의 items 에서)", async () => {
  renderPage();
  await fillScan();
  for (let i = 2; i <= 7; i += 1) {
    await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
    await userEvent.type(screen.getByLabelText(`${i}행 경로`), `p${i}`);
  }
  await toReviewStep();
  const table = within(stepCard()).getByRole("table");
  expect(within(table).getAllByRole("row")).toHaveLength(1 + 5);
  expect(within(table).getByText("p5")).toBeInTheDocument();
  expect(within(table).queryByText("p6")).toBeNull();
  expect(within(stepCard()).getByText("외 2행")).toBeInTheDocument();
  expect(summaryRow("항목 수")).toHaveTextContent(/^항목 수7$/);
});

test("rm 검토의 「배치 생성」은 빨간 솔리드(destructive), sync 는 기본 색", async () => {
  renderPage(RM_POLICIES);
  await fillRm();
  await toReviewStep();
  expect(createButton()).toHaveClass("bg-bad");
  expect(createButton()).not.toHaveClass("bg-accent");
  await goStep("작업 종류");
  await chooseOperation("sync");
  await toStorage();
  await pickStorage("소스 스토리지", "s1");
  await pickStorage("목적지 스토리지", "s2");
  await toItems();
  await userEvent.type(screen.getByLabelText("1행 목적지"), "b");
  await toReviewStep();
  expect(createButton()).toHaveClass("bg-accent");
  expect(createButton()).not.toHaveClass("bg-bad");
});

test("행을 지우면 포커스가 같은 번호 행의 삭제 버튼, 마지막 행이면 이전 행, 행이 없으면 「행 추가」로 간다", async () => {
  renderPage();
  await fillScan();
  await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
  await userEvent.click(screen.getByRole("button", { name: "행 추가" }));
  await userEvent.click(screen.getByRole("button", { name: "1행 삭제" }));
  expect(screen.getByRole("button", { name: "1행 삭제" })).toHaveFocus();      // 같은 번호(옛 2행)
  await userEvent.click(screen.getByRole("button", { name: "2행 삭제" }));
  expect(screen.getByRole("button", { name: "1행 삭제" })).toHaveFocus();      // 마지막 행 → 이전 행
  await userEvent.click(screen.getByRole("button", { name: "1행 삭제" }));
  expect(screen.getByRole("button", { name: "행 추가" })).toHaveFocus();       // 0행
});

test("연산 카드: 세 카드가 늘 보이고 rm 은 「되돌릴 수 없음」 칩, 고른 카드만 자동 실행 문장으로 바뀐다", async () => {
  renderPage();
  const group = await screen.findByRole("radiogroup", { name: "연산" });
  expect(within(group).getAllByRole("radio").map((r) => r.getAttribute("aria-label"))).toEqual(["scan", "sync", "rm"]);
  expect(within(group).getByRole("radio", { name: "scan" })).toBeChecked();
  expect(within(group).getByText("되돌릴 수 없음")).toBeInTheDocument();
  await chooseOperation("rm");
  expect(within(group).getByRole("radio", { name: "rm" })).toBeChecked();
  expect(within(group).getByRole("radio", { name: "rm" })).toHaveAccessibleDescription(/「배치 확인」을 눌러야 삭제됩니다/);
});
