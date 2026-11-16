import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { BatchesList } from "./BatchesList";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

// updated_at 은 서버가 늘 보낸다(batches.updated_at NOT NULL + SELECT *) — fixture 도
// 그렇게 둔다. 「최근 갱신」이 비는 경우는 테스트가 명시적으로 null 을 줄 때뿐이라,
// 다른 테스트의 "—" 단언(이름 없음)이 이 컬럼과 뒤섞이지 않는다. 대상 스토리지(2026-11-16)도 같은 이유로 늘 싣는다(서버는
// 항목이 있는 배치면 첫 항목에서 뽑아 준다) — 「—」는 그 칸을 시험하는 테스트가 명시적으로 뺄 때만 생긴다.
const row = (over: object = {}) => ({ batch_id: "b1234567890abcdef", operation: "scan",
  status: "Running", max_concurrency: 2, item_count: 3, succeeded_count: 1,
  failed_count: 0, note: null, created_at: "", updated_at: "2026-08-15T00:00:00Z", storage: "s1", ...over });

function renderList(rows: object[] | (() => object[])) {
  server.use(http.get("/api/admin/batches",
    () => HttpResponse.json(typeof rows === "function" ? rows() : rows)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const r = render(
    <QueryClientProvider client={qc}>
      <MemoryRouter><BatchesList /></MemoryRouter>
    </QueryClientProvider>);
  return { ...r, qc };
}

test("상태 pill 은 batchPillVariant 색 — 상세 헤더와 동일 계약(Completed=ok·Running=busy)", async () => {
  renderList([row({ status: "Completed" }),
              row({ batch_id: "b2222222222222", status: "Running" })]);
  expect((await screen.findByText("Completed")).className).toContain("text-ok");
  expect(screen.getByText("Running").className).toContain("text-busy");
});

test("이름 컬럼: 있으면 이름, 없으면 — (빈칸으로 뭉개지 않는다)", async () => {
  renderList([row({ name: "8월 정기 스캔 1차" }),
              row({ batch_id: "b2222222222222", name: null })]);
  expect(await screen.findByText("8월 정기 스캔 1차")).toBeInTheDocument();
  expect(screen.getByText("이름")).toBeInTheDocument();       // 헤더
  expect(screen.getByText("—")).toBeInTheDocument();          // 이름 없음
});

// slice(0,12) 가 서로 달라야 체크박스 aria-label 이 구분된다.
const ID1 = "b111111111111aaa";
const ID2 = "b222222222222bbb";
const ID3 = "b333333333333ccc";

// --- T2 「최근 갱신」 컬럼 ---------------------------------------------------
test("최근 갱신 컬럼: updated_at 을 KST 로 표기, 값 없으면 —", async () => {
  renderList([row({ batch_id: ID1, name: "n1", updated_at: "2026-08-15T03:04:05Z" }),
              row({ batch_id: ID2, name: "n2", updated_at: null })]);
  expect(await screen.findByText("최근 갱신")).toBeInTheDocument();   // 헤더
  expect(screen.getByText("2026-08-15 12:04:05 KST")).toBeInTheDocument();  // +9h
  expect(screen.getByText("—")).toBeInTheDocument();                  // 갱신 시각 없음
});

// --- T1 다중 선택 삭제 -------------------------------------------------------
const box = (id: string) => screen.getByLabelText(`배치 ${id.slice(0, 12)} 선택`);
/** Completed·Cancelled(종단 2건) + Running(활성 1건) — 선택 가능/불가가 섞인 목록. */
const mixed = () => [row({ batch_id: ID1, status: "Completed" }),
                     row({ batch_id: ID2, status: "Cancelled" }),
                     row({ batch_id: ID3, status: "Running" })];
async function selectBoth() {
  await userEvent.click(await screen.findByLabelText(`배치 ${ID1.slice(0, 12)} 선택`));
  await userEvent.click(box(ID2));
}

test("행 체크박스: 종단 배치만 선택 가능 — 활성 배치는 disabled + 사유 title", async () => {
  renderList(mixed());
  expect(await screen.findByLabelText(`배치 ${ID1.slice(0, 12)} 선택`)).toBeEnabled();
  expect(box(ID2)).toBeEnabled();
  const active = box(ID3);
  expect(active).toBeDisabled();
  expect(active.getAttribute("title")).toMatch(/취소/);
});

test("전체 선택은 선택 가능 행만 토글 — 일부만 선택되면 indeterminate", async () => {
  renderList(mixed());
  const all = await screen.findByLabelText("전체 선택") as HTMLInputElement;
  expect(all.indeterminate).toBe(false);
  await userEvent.click(box(ID1));
  expect(all.indeterminate).toBe(true);          // 2건 중 1건 — 부분 선택
  await userEvent.click(all);
  expect(box(ID1)).toBeChecked();
  expect(box(ID2)).toBeChecked();
  expect(box(ID3)).not.toBeChecked();            // 활성 행은 전체 선택 대상이 아니다
  expect(all.indeterminate).toBe(false);
  expect(screen.getByText("2개 선택됨")).toBeInTheDocument();
  await userEvent.click(all);                    // 다시 누르면 전부 해제
  expect(screen.queryByText(/개 선택됨/)).toBeNull();
});

test("액션 바 내용: N개 선택됨 · 선택 삭제 · 선택 해제 — 해제하면 안내 문구로 되돌아온다", async () => {
  renderList(mixed());
  await screen.findByLabelText(`배치 ${ID1.slice(0, 12)} 선택`);
  expect(screen.queryByText(/개 선택됨/)).toBeNull();
  await selectBoth();
  expect(screen.getByText("2개 선택됨")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeEnabled();
  await userEvent.click(screen.getByRole("button", { name: "선택 해제" }));
  expect(screen.queryByText(/개 선택됨/)).toBeNull();
  expect(box(ID1)).not.toBeChecked();
});

// --- T3 체크박스 표적 확대 --------------------------------------------------
test("체크박스는 확대 표적(h-5 w-5 + cursor-pointer) — 헤더 전체선택·행이 같은 크기", async () => {
  renderList(mixed());
  const rowBox = await screen.findByLabelText(`배치 ${ID1.slice(0, 12)} 선택`);
  const all = screen.getByLabelText("전체 선택");
  for (const el of [all, rowBox]) {
    expect(el.className).toMatch(/\bh-5\b/);
    expect(el.className).toMatch(/\bw-5\b/);
    expect(el.className).toMatch(/\bcursor-pointer\b/);
  }
});

// --- T4 레이아웃 점프 0(액션 바 자리 예약) ----------------------------------
const bar = () => screen.getByRole("toolbar", { name: "배치 일괄 작업" });

test("액션 바는 미선택에도 자리를 지킨다 — 체크해도 표가 밀리지 않는다", async () => {
  renderList(mixed());
  await screen.findByLabelText(`배치 ${ID1.slice(0, 12)} 선택`);
  const before = bar();
  expect(before).toHaveTextContent("배치를 선택해 삭제할 수 있습니다");
  // 버튼도 늘 렌더된다 — 바 높이가 버튼 높이로 **구조상** 고정되어 magic min-h 가
  // 필요 없다. 미선택 시엔 disabled(누를 게 없다는 사실을 자리와 함께 남긴다).
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "선택 해제" })).toBeDisabled();
  await userEvent.click(box(ID1));
  // **같은 DOM 노드**가 그대로다 = 마운트/언마운트로 인한 재배치가 없다(점프 0).
  expect(bar()).toBe(before);
  expect(before).toHaveTextContent("1개 선택됨");
  expect(screen.getByRole("button", { name: "선택 삭제" })).toBeEnabled();
});

test("이 화면의 삭제는 배치 기록만 지운다는 것을 늘 있는 줄이 말한다(작업까지 지우는 길은 전체 작업)", async () => {
  // 2026-10-10 검증 지적: 전체 작업에 「배치 단위 삭제 = 작업까지 삭제」가 생긴 뒤, 같은 「선택 삭제」가 여기서는 기록만
  // 지운다는 안내가 없었다. 무장 상태에만 붙이면 바가 늘어 확인 버튼이 밀리므로 늘 있는 줄이다.
  renderList(mixed());
  await screen.findByLabelText(`배치 ${ID1.slice(0, 12)} 선택`);
  const hint = within(bar()).getByText(
    "여기서는 배치 기록만 삭제됩니다 — 작업은 전체 작업에 남습니다(작업까지 지우려면 전체 작업에서 배치 단위로 삭제)");
  expect(hint.className).toContain("text-ink/70");
  await userEvent.click(box(ID1));
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  expect(screen.getByRole("button", { name: "1개 삭제 확인" })).toBeInTheDocument();
  expect(within(bar()).getByText(/^여기서는 배치 기록만 삭제됩니다/)).toBe(hint);       // 같은 줄 그대로(높이 무변)
});

test("삭제 결과는 예약된 바 **안에서** 교체된다 — 결과가 떠도 추가 점프가 없다", async () => {
  server.use(http.delete("/api/admin/batches/:id",
    () => new HttpResponse(null, { status: 204 })));
  renderList(mixed());
  await selectBoth();
  const before = bar();
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "2개 삭제 확인" }));
  await waitFor(() => expect(within(before).getByText("2개 삭제됨")).toBeInTheDocument());
  expect(bar()).toBe(before);                  // 바는 사라지지도 새로 생기지도 않았다
});

test("2단 확인: 1단 클릭은 안 쏘고, 2단 「N개 삭제 확인」이 선택 수만큼 DELETE", async () => {
  const deleted: string[] = [];
  server.use(http.delete("/api/admin/batches/:id", ({ params }) => {
    deleted.push(String(params.id));
    return new HttpResponse(null, { status: 204 });
  }));
  renderList(mixed());
  await selectBoth();
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  expect(deleted).toEqual([]);                   // 1단은 무장만
  await userEvent.click(screen.getByRole("button", { name: "2개 삭제 확인" }));
  await waitFor(() => expect([...deleted].sort()).toEqual([ID1, ID2].sort()));
  expect(deleted).toHaveLength(2);               // 활성 행(ID3)은 안 나갔다
});

test("일괄 삭제 완료 후 선택 초기화 — 선택 문구가 결과로 바뀐다", async () => {
  server.use(http.delete("/api/admin/batches/:id",
    () => new HttpResponse(null, { status: 204 })));
  renderList(mixed());
  await selectBoth();
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "2개 삭제 확인" }));
  expect(await screen.findByText("2개 삭제됨")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByText(/개 선택됨/)).toBeNull());
});

test("부분 실패는 정직하게: 성공 n·실패 m 과 사유(409 batch_not_deletable)", async () => {
  server.use(http.delete("/api/admin/batches/:id", ({ params }) =>
    params.id === ID2 ? HttpResponse.json({ detail: "batch_not_deletable" }, { status: 409 })
                      : new HttpResponse(null, { status: 204 })));
  renderList(mixed());
  await selectBoth();
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "2개 삭제 확인" }));
  expect(await screen.findByText("1개 삭제됨 · 1개 실패")).toBeInTheDocument();
  // 사유는 reasonText 매핑(api.ts REASON_MESSAGES) — 어느 배치가 실패했는지도 함께.
  expect(screen.getByText(new RegExp(`${ID2.slice(0, 12)}.*진행 중인 배치는 삭제할 수 없습니다`)))
    .toBeInTheDocument();
});

// 삭제 후 화면 갱신(사용자 보고 2026-08-15): 무효화 키는 맞았고 어긋난 건 **시점**
// 이었다 — onSettled 가 invalidate 의 프라미스를 안 돌려줘 재조회가 끝나기 전에
// mutation 이 완료를 선언했다. 결과 문구("N개 삭제됨")가 뜬 뒤에도 지운 행이 남아
// 있었고 화면엔 아무 표시도 없었다. 재조회를 느리게 해 그 창을 열어 놓고 못 박는다.
test("일괄 삭제: 결과 문구가 뜨는 시점엔 이미 행이 사라져 있다", async () => {
  // 이름이 있는 행이라야 "행이 화면에서 사라졌다"를 이름으로 단언할 수 있다
  const state = { rows: [row({ batch_id: ID1, status: "Completed", name: "n1" }),
                         row({ batch_id: ID2, status: "Cancelled", name: "n2" }),
                         row({ batch_id: ID3, status: "Running", name: "n3" })] as
                        { batch_id: string }[] };
  let gets = 0;
  server.use(
    http.get("/api/admin/batches", async () => { gets += 1;
      if (gets > 1) await delay(200);              // 삭제 후 재조회만 느리게
      return HttpResponse.json(state.rows); }),
    http.delete("/api/admin/batches/:id", ({ params }) => {
      state.rows = state.rows.filter((r) => r.batch_id !== String(params.id));
      return new HttpResponse(null, { status: 204 }); }));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><BatchesList /></MemoryRouter></QueryClientProvider>);
  await screen.findByText("n1");
  await selectBoth();
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  await userEvent.click(screen.getByRole("button", { name: "2개 삭제 확인" }));
  expect(await screen.findByText("2개 삭제됨")).toBeInTheDocument();
  expect(screen.queryByText("n1")).toBeNull();
  expect(screen.queryByText("n2")).toBeNull();
});

test("리페치로 목록에서 사라진 배치는 선택에서 자동 제거(유령 선택 방지)", async () => {
  let rows = mixed();
  const { qc } = renderList(() => rows);
  await selectBoth();
  expect(screen.getByText("2개 선택됨")).toBeInTheDocument();
  rows = rows.filter((r) => (r as { batch_id: string }).batch_id !== ID1);
  await act(async () => { await qc.refetchQueries({ queryKey: ["batches"] }); });
  expect(await screen.findByText("1개 선택됨")).toBeInTheDocument();
});

// --- 작업 종류 탭(2026-08-23): sync/scan/rm 을 따로 모아 본다 -----------------

const opsMix = () => [
  row({ batch_id: ID1, operation: "sync", status: "Completed", name: "s-sync" }),
  row({ batch_id: ID2, operation: "scan", status: "Completed", name: "s-scan1" }),
  row({ batch_id: ID3, operation: "scan", status: "Completed", name: "s-scan2" }),
];

test("작업 종류 탭: 건수 라벨 + 기본은 전체", async () => {
  renderList(opsMix());
  await screen.findByText("s-sync");
  expect(screen.getByRole("tab", { name: "전체 (3)" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "sync (1)" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "scan (2)" })).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "rm (0)" })).toBeInTheDocument();
  expect(screen.getByText("s-scan1")).toBeInTheDocument();     // 전체 탭엔 다 보인다
});

test("scan 탭: scan 배치만 남고 sync 는 사라진다", async () => {
  renderList(opsMix());
  await screen.findByText("s-sync");
  await userEvent.click(screen.getByRole("tab", { name: "scan (2)" }));
  expect(screen.getByText("s-scan1")).toBeInTheDocument();
  expect(screen.getByText("s-scan2")).toBeInTheDocument();
  expect(screen.queryByText("s-sync")).toBeNull();
});

test("빈 탭은 빈 표 대신 문구", async () => {
  renderList(opsMix());
  await screen.findByText("s-sync");
  await userEvent.click(screen.getByRole("tab", { name: "rm (0)" }));
  expect(screen.getByText("rm 배치가 없습니다")).toBeInTheDocument();
});

test("탭 전환은 선택을 비운다 — 안 보이는 행을 지우는 사고 방지", async () => {
  renderList(opsMix());
  await screen.findByText("s-sync");
  await userEvent.click(screen.getByLabelText(`배치 ${ID2.slice(0, 12)} 선택`));
  expect(screen.getByText("1개 선택됨")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "sync (1)" }));
  expect(screen.getByText("배치를 선택해 삭제할 수 있습니다")).toBeInTheDocument();
});

test("전체 선택은 현재 탭의 행만 담는다", async () => {
  renderList(opsMix());
  await screen.findByText("s-sync");
  await userEvent.click(screen.getByRole("tab", { name: "scan (2)" }));
  await userEvent.click(screen.getByLabelText("전체 선택"));
  // sync(Completed·삭제 가능)까지 담기면 3개가 된다 — 보이는 scan 2개만이 계약
  expect(screen.getByText("2개 선택됨")).toBeInTheDocument();
});

test("조회 오류는 사유를 보이고 「배치가 없습니다」로 접지 않는다 (오류≠빈 상태)", async () => {
  server.use(http.get("/api/admin/batches",
    () => HttpResponse.json({ detail: "http_500" }, { status: 500 })));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><MemoryRouter><BatchesList /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByText("서버 오류가 발생했습니다")).toBeInTheDocument();
  expect(screen.queryByText("배치가 없습니다")).toBeNull();
  // 건수도 지어내지 않는다: 오류 중 탭 라벨에 (0) 이 붙으면 "없다"는 거짓말이다
  expect(screen.getByRole("tab", { name: "전체" })).toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "전체 (0)" })).toBeNull();
});

// --- 대상 스토리지 열(2026-11-16): 「작업」 바로 오른쪽 — 첫 항목 payload 에서 서버가 뽑은 값 ---------------------------

test("열 순서: … 이름 · 작업 · 대상 스토리지 · 상태 …", async () => {
  renderList([row()]);
  await screen.findByText("s1");
  const heads = screen.getAllByRole("columnheader").map((th) => th.textContent);
  // 첫 열은 전체 선택 체크박스(글자 없음)
  expect(heads.slice(1)).toEqual(["배치", "이름", "작업", "대상 스토리지", "상태", "진행", "최근 갱신"]);
});

test("scan·rm 은 storage, sync 는 「소스 → 목적지」(title 로 방향을 말한다)", async () => {
  renderList([
    row({ batch_id: ID1, operation: "scan", storage: "cephfs-a", name: "n1" }),
    row({ batch_id: ID2, operation: "rm", storage: "gpfs-b", name: "n2" }),
    row({ batch_id: ID3, operation: "sync", storage: null, source_storage: "s1", destination_storage: "s2", name: "n3" })]);
  expect(await screen.findByText("cephfs-a")).toBeInTheDocument();
  expect(screen.getByText("gpfs-b")).toBeInTheDocument();
  const pair = screen.getByText("s1 → s2");
  expect(pair).toHaveAttribute("title", "소스 → 목적지");
  // 셀은 같은 행의 「작업」 바로 오른쪽이다
  const cells = within(pair.closest("tr")!).getAllByRole("cell").map((td) => td.textContent);
  expect(cells.indexOf("s1 → s2")).toBe(cells.indexOf("sync") + 1);
  // 긴 자유 글자 이름도 접히고(break-all) 좁은 폭에서 짓눌리지 않는다(최소 폭)
  expect(pair.className).toContain("break-all");
  expect(pair.className).toMatch(/min-w-/);
});

test("모르면 「—」: 필드 부재(옛 서버)·문자열 아님 — sync 는 둘 다 모르면 「—」 하나, 한쪽만 알면 그쪽만", async () => {
  renderList([
    row({ batch_id: ID1, name: "n1", storage: undefined }),
    row({ batch_id: ID2, name: "n2", operation: "sync", storage: null, source_storage: 7, destination_storage: null }),
    row({ batch_id: ID3, name: "n3", operation: "sync", storage: null, source_storage: "s1", destination_storage: null })]);
  await screen.findByText("n1");
  const cellOf = (name: string) => {
    const cells = within(screen.getByText(name).closest("tr")!).getAllByRole("cell");
    return cells[4];                           // 체크 · 배치 · 이름 · 작업 · [대상 스토리지]
  };
  expect(cellOf("n1")).toHaveTextContent(/^—$/);
  expect(cellOf("n2")).toHaveTextContent(/^—$/);                   // 「— → —」가 아니다
  expect(cellOf("n2")).not.toHaveAttribute("title");               // 항목이 있는데 모른다 ≠ 항목 없음
  expect(cellOf("n3")).toHaveTextContent(/^s1 → —$/);
});

test("항목 0개 배치는 「—」 + title 「항목이 없습니다」 — 모름과 구별한다", async () => {
  renderList([row({ batch_id: ID1, name: "empty", item_count: 0, storage: null }),
              row({ batch_id: ID2, name: "empty-sync", operation: "sync", item_count: 0, storage: null,
                    source_storage: null, destination_storage: null })]);
  await screen.findByText("empty");
  for (const name of ["empty", "empty-sync"]) {
    const cell = within(screen.getByText(name).closest("tr")!).getAllByRole("cell")[4];
    expect(cell).toHaveTextContent(/^—$/);
    expect(cell).toHaveAttribute("title", "항목이 없습니다");
  }
});
