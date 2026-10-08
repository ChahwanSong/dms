import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse, delay } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { DeleteRequestsDialog, isSucceededScan, opCountLine } from "./DeleteRequestsDialog";
import { useDeleteRequests } from "./useJobs";
import type { RequestRow } from "../../lib/types";

const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());

const hex = (n: number) => `${String(n).padStart(4, "0")}${"cd".repeat(14)}`;
function req(n: number, over: Partial<RequestRow> = {}): RequestRow {
  return {
    request_id: hex(n), operation: "sync", requester_id: "alice", resource_key: "k", priority: "mid",
    state: "Succeeded", created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:01:00Z",
    payload: { source_storage: "s1", source: `a${n}`, destination_storage: "s2", destination: `b${n}` },
    commit_order: n, batch_id: null, ...over,
  };
}
const scan = (n: number, state = "Succeeded") =>
  req(n, { operation: "scan", state, payload: { storage: "s1", target: `t${n}` } });

function Harness({ rows, onDeleted = () => {}, focusAfterDelete }: {
  rows: RequestRow[]; onDeleted?: () => void; focusAfterDelete?: () => void;
}) {
  const del = useDeleteRequests();
  return <DeleteRequestsDialog rows={rows} disabled={rows.length === 0} del={del} onDeleted={onDeleted}
                               focusAfterDelete={focusAfterDelete} />;
}
function renderDialog(rows: RequestRow[], onDeleted?: () => void, focusAfterDelete?: () => void) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (r: RequestRow[]) => (
    <QueryClientProvider client={qc}>
      <Harness rows={r} onDeleted={onDeleted} focusAfterDelete={focusAfterDelete} />
    </QueryClientProvider>);
  const view = render(ui(rows));
  return { ...view, rerenderRows: (r: RequestRow[]) => view.rerender(ui(r)) };
}
async function open() {
  await userEvent.click(screen.getByRole("button", { name: "선택 삭제" }));
  return screen.findByRole("dialog");
}
const ACK = "되돌릴 수 없음을 확인했습니다";

test("순수 함수: 성공 scan 판정·연산별 개수 줄", () => {
  expect(isSucceededScan(scan(1))).toBe(true);
  expect(isSucceededScan(scan(2, "Failed"))).toBe(false);
  expect(isSucceededScan(req(3))).toBe(false);
  // 서버의 has_succeeded_scan(잡 상태 기준)이 있으면 그것이 판정한다 -- 취소 경합의 「Cancelled 인데 잡은 성공」.
  expect(isSucceededScan({ ...scan(4, "Cancelled"), has_succeeded_scan: true })).toBe(true);
  expect(isSucceededScan({ ...scan(5), has_succeeded_scan: false })).toBe(false);
  expect(opCountLine([scan(1), req(2), req(3, { operation: "rm" }), scan(4), req(5, { operation: "weird" })]))
    .toBe("sync 1 · scan 2 · rm 1 · weird 1");
});

test("내용: 개수·연산별 개수·앞 10건 + 「외 K개」·함께 지워지는 것·스토리지 무접촉 문구", async () => {
  const rows = [...[1, 2, 3, 4, 5].map((n) => req(n)), ...[6, 7, 8, 9].map((n) => scan(n, "Failed")),
                ...[10, 11, 12].map((n) => req(n, { operation: "rm", payload: { storage: "s1", target: `r${n}` } }))];
  renderDialog(rows);
  const dlg = await open();
  expect(within(dlg).getByText("선택한 작업 12개를 영구 삭제합니다. 되돌릴 수 없습니다.")).toBeInTheDocument();
  expect(within(dlg).getByText("연산별: sync 5 · scan 4 · rm 3")).toBeInTheDocument();
  const list = within(dlg).getByRole("list", { name: "삭제할 작업" });
  const items = within(list).getAllByRole("listitem");
  expect(items).toHaveLength(11);                       // 앞 10건 + 「외 2개」
  expect(items[0]).toHaveTextContent(hex(1).slice(0, 12));
  expect(items[0]).toHaveTextContent("s1:a1 → s2:b1");   // pathSummary
  expect(items[0]).toHaveTextContent("Succeeded");      // StatusPill
  expect(items[0]).not.toHaveTextContent(hex(1));       // id 는 앞 12자만
  expect(items[10]).toHaveTextContent("외 2개");
  expect(within(dlg).getByText(/함께 지워지는 것: 요청·잡 기록/)).toBeInTheDocument();
  expect(within(dlg).getByText(
    "스토리지의 실제 데이터(복사·삭제된 파일)는 건드리지 않습니다 — 작업 기록 삭제는 실행 취소가 아닙니다."))
    .toBeInTheDocument();
  expect(within(dlg).getByText(/감사 로그에는 누가·언제·무엇을 지웠는지 남습니다/)).toBeInTheDocument();
});

test("성공 scan 이 있을 때만 사용량 분석 경고 — 개수는 성공 scan 만", async () => {
  renderDialog([scan(1), scan(2), scan(3, "Failed"), req(4)]);
  const dlg = await open();
  const note = within(dlg).getByRole("note");
  expect(note).toHaveTextContent("성공한 scan 2개 포함 — 사용량 분석(대상별 최신 사용량·이력 차트·CSV)에서 이 결과가 사라집니다.");
  expect(note).toHaveTextContent("같은 대상에 이전 scan 이 있으면 그 값이 최신으로 보이고, 없으면 대상이 목록에서 빠집니다.");
});

test("실패·취소 scan 만이면 사용량 경고가 없다", async () => {
  renderDialog([scan(1, "Failed"), scan(2, "Cancelled")]);
  const dlg = await open();
  expect(within(dlg).queryByRole("note")).toBeNull();
  expect(within(dlg).queryByText(/사용량 분석/)).toBeNull();
});

test("확인 체크 전에는 주 버튼이 잠겨 있고, 체크하면 풀린다", async () => {
  renderDialog([req(1), req(2)]);
  const dlg = await open();
  const go = within(dlg).getByRole("button", { name: "2개 영구 삭제" });
  expect(go).toBeDisabled();
  await userEvent.click(within(dlg).getByLabelText(ACK));
  expect(go).toBeEnabled();
});

test("POST 본문 = 연 순간의 스냅숏 — 열린 뒤 선택(폴링)이 바뀌어도 확인한 목록 그대로 보낸다", async () => {
  let body: unknown = null;
  server.use(http.post("/api/admin/requests:delete", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({ deleted: [], skipped: [], purge_pending: 0 });
  }));
  const onDeleted = vi.fn();
  const { rerenderRows } = renderDialog([req(1), req(2)], onDeleted);
  const dlg = await open();
  rerenderRows([req(2), req(3), req(4)]);              // 열린 창 뒤에서 선택이 바뀌었다
  expect(within(dlg).getByText("선택한 작업 2개를 영구 삭제합니다. 되돌릴 수 없습니다.")).toBeInTheDocument();
  await userEvent.click(within(dlg).getByLabelText(ACK));
  await userEvent.click(within(dlg).getByRole("button", { name: "2개 영구 삭제" }));
  await waitFor(() => expect(body).toEqual({ request_ids: [hex(1), hex(2)] }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());   // 성공이면 닫힌다
});

test("다시 열면 확인 체크가 풀려 있다(이전 확인을 새 선택에 넘기지 않는다)", async () => {
  renderDialog([req(1)]);
  let dlg = await open();
  await userEvent.click(within(dlg).getByLabelText(ACK));
  await userEvent.click(within(dlg).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  dlg = await open();
  expect(within(dlg).getByLabelText(ACK)).not.toBeChecked();
  expect(within(dlg).getByRole("button", { name: "1개 영구 삭제" })).toBeDisabled();
});

test("전체 실패(403 admin_session_required)는 창 안에 보이고, 닫았다 열면 지워져 있다", async () => {
  server.use(http.post("/api/admin/requests:delete",
    () => HttpResponse.json({ detail: "admin_session_required" }, { status: 403 })));
  const onDeleted = vi.fn();
  renderDialog([req(1)], onDeleted);
  let dlg = await open();
  await userEvent.click(within(dlg).getByLabelText(ACK));
  await userEvent.click(within(dlg).getByRole("button", { name: "1개 영구 삭제" }));
  expect(await within(dlg).findByRole("alert")).toHaveTextContent(/작업 삭제는 포탈에 로그인한 관리자만/);
  expect(screen.getByRole("dialog")).toBeInTheDocument();   // 실패면 창이 남는다
  expect(onDeleted).not.toHaveBeenCalled();
  await userEvent.click(within(dlg).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  dlg = await open();
  expect(within(dlg).queryByRole("alert")).toBeNull();
});

test("취소된 scan 이라도 서버가 성공 scan 잡을 알리면(has_succeeded_scan) 사용량 경고를 띄운다", async () => {
  renderDialog([{ ...scan(1, "Cancelled"), has_succeeded_scan: true }, { ...scan(2), has_succeeded_scan: false }]);
  const dlg = await open();
  expect(within(dlg).getByRole("note")).toHaveTextContent("성공한 scan 1개 포함 — 사용량 분석");
});

test("같은 틱에 두 번 눌러도 POST 는 한 번이다(튀는 스위치·dblclick)", async () => {
  let posts = 0;
  server.use(http.post("/api/admin/requests:delete", async () => {
    posts += 1;
    return HttpResponse.json({ deleted: [{ request_id: hex(1), job_ids: [] }], skipped: [], purge_pending: 1 });
  }));
  const onDeleted = vi.fn();
  renderDialog([req(1)], onDeleted);
  const dlg = await open();
  await userEvent.click(within(dlg).getByLabelText(ACK));
  const go = within(dlg).getByRole("button", { name: "1개 영구 삭제" });
  fireEvent.click(go);                                   // isPending 이 화면에 반영되기 전 같은 태스크의 두 번째 클릭
  fireEvent.click(go);
  await waitFor(() => expect(onDeleted).toHaveBeenCalledTimes(1));
  expect(posts).toBe(1);
});

test("진행 중에도 포커스는 창 안 주 버튼에 남고(aria-disabled), 성공해 닫히면 지정한 자리로 간다", async () => {
  server.use(http.post("/api/admin/requests:delete", async () => {
    await delay(150);
    return HttpResponse.json({ deleted: [{ request_id: hex(1), job_ids: [] }], skipped: [], purge_pending: 1 });
  }));
  const focusAfterDelete = vi.fn();
  renderDialog([req(1)], undefined, focusAfterDelete);
  const dlg = await open();
  await userEvent.click(within(dlg).getByLabelText(ACK));
  const go = within(dlg).getByRole("button", { name: "1개 영구 삭제" });
  go.focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() => expect(go).toHaveAttribute("aria-disabled", "true"));
  expect(go).not.toBeDisabled();
  expect(go).toHaveFocus();                              // disabled 였으면 <body> 로 떨어졌다
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(focusAfterDelete).toHaveBeenCalledTimes(1);
});

test("진행 중엔 「삭제 중…」(버튼·status)과 창 aria-busy — 반응 없는 창으로 보이지 않는다", async () => {
  // 2026-10-09 검증 지적: 수 초 걸릴 수 있는 일괄 삭제 동안 opacity 만 바뀌고 보조기기엔 아무것도 전달되지 않았다.
  let release!: () => void;
  const gate = new Promise<void>((r) => { release = r; });
  server.use(http.post("/api/admin/requests:delete", async () => {
    await gate;
    return HttpResponse.json({ deleted: [], skipped: [{ request_id: hex(1), reason: "request_not_found" }],
                               purge_pending: 0 });
  }));
  renderDialog([req(1)]);
  const dlg = await open();
  const status = within(dlg).getByRole("status");          // 늘 있는 라이브 리전(내용이 바뀌기 전부터 DOM 에)
  expect(status).toHaveTextContent("");
  expect(dlg).not.toHaveAttribute("aria-busy");
  await userEvent.click(within(dlg).getByLabelText(ACK));
  await userEvent.click(within(dlg).getByRole("button", { name: "1개 영구 삭제" }));
  await waitFor(() => expect(status).toHaveTextContent("삭제 중…"));
  expect(dlg).toHaveAttribute("aria-busy", "true");
  expect(within(dlg).getByRole("button", { name: "삭제 중…" })).toHaveAttribute("aria-disabled", "true");
  release();
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

test("함께 지워지는 것 문구에 내부 은어(「박제」)가 없다", async () => {
  renderDialog([req(1)]);
  const dlg = await open();
  expect(within(dlg).getByText(/실패 시 보관한 로그/)).toBeInTheDocument();
  expect(dlg).not.toHaveTextContent("박제");
});

test("닫기로 닫으면(트리거가 살아 있으면) 지정한 자리가 아니라 트리거로 돌아간다", async () => {
  const focusAfterDelete = vi.fn();
  renderDialog([req(1)], undefined, focusAfterDelete);
  const dlg = await open();
  await userEvent.click(within(dlg).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(focusAfterDelete).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "선택 삭제" })).toHaveFocus();
});

test("열린 창 뒤에서 선택이 비어 트리거가 잠기면 닫기·Esc 로 닫아도 지정한 자리로 간다(<body> 로 떨어지지 않는다)", async () => {
  // 2026-10-09 검증 지적: 창이 열린 동안 폴링이 선택한 행을 모두 목록에서 지우면(다른 관리자가 삭제·새 제출에 밀림)
  // 「선택 삭제」가 잠긴다 -- Radix 의 기본 복귀가 잠긴 트리거로 가 포커스가 <body> 로 떨어졌다.
  const target = document.createElement("span");
  target.tabIndex = -1;
  document.body.appendChild(target);
  try {
    for (const close of ["button", "escape"] as const) {
      const view = renderDialog([req(1)], undefined, () => target.focus());
      await open();
      view.rerenderRows([]);                                 // 선택이 비었다 -- 트리거 disabled
      // 모달이 열린 동안 바깥은 aria-hidden 이다(hidden: true 로 찾는다).
      expect(screen.getByRole("button", { name: "선택 삭제", hidden: true })).toBeDisabled();
      if (close === "button") await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "닫기" }));
      else await userEvent.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      await waitFor(() => expect(target).toHaveFocus());
      expect(document.activeElement).not.toBe(document.body);
      view.unmount();
    }
  } finally {
    target.remove();
  }
});
