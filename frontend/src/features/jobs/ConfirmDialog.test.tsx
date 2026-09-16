import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ConfirmDialog, previewSummaryText } from "./ConfirmDialog";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// 2026-09-17: 컨펌 창은 실행 결과(result_summary, 컨펌 시점엔 항상 null)가 아니라
// 미리보기 dry-run 의 summary.json 사본(preview_summary)을 보여준다.
const job = { job_id: "j1", request_id: "r1", operation: "sync", state: "ConfirmPending",
  reason_code: null, preview_fingerprint: "abc123", preview_expires_at: "2099-01-01T00:00:00Z",
  result_summary: null, preview_summary: { returncode: 0, files: 3, bytes: 12582912 },
  transitions: [] };

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

test("버튼·제목은 '작업 컨펌'이고 미리보기 요약(개수·크기)과 지문을 보여준 뒤 지문을 POST 한다", async () => {
  let body: any = null;
  server.use(http.post("/api/user/jobs/j1:confirm", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({ state: "Executing" });
  }));
  wrap(<ConfirmDialog job={job as any} />);
  expect(screen.queryByRole("button", { name: "미리보기 확인" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText("sync 작업 컨펌")).toBeInTheDocument();
  expect(screen.getByText(/abc123/)).toBeInTheDocument();
  expect(screen.getByText("복사 대상 3개 · 12.0 MiB")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "컨펌" }));
  await screen.findByText(/./); // flush
  expect(body).toEqual({ fingerprint: "abc123" });
});

test("요약 문구: null(모름)과 0 을 구분하고, 요약이 없는 옛 잡은 그 사실을 말한다", () => {
  expect(previewSummaryText({ operation: "sync", preview_summary: { returncode: 0, files: 0, bytes: 0 } }))
    .toBe("복사 대상 0개 · 0 B");
  expect(previewSummaryText({ operation: "sync", preview_summary: { returncode: 0, files: null, bytes: null } }))
    .toBe("복사 대상 모름 · 크기 모름");
  expect(previewSummaryText({ operation: "rm", preview_summary: { returncode: 0, files: 12, bytes: null } }))
    .toBe("삭제 대상 12개");
  expect(previewSummaryText({ operation: "sync", preview_summary: { returncode: 2, files: 1, bytes: 10 } }))
    .toBe("복사 대상 1개 · 10 B · dry-run 종료코드 2");
  expect(previewSummaryText({ operation: "sync", preview_summary: null }))
    .toBe("(요약 없음 — 이 잡은 미리보기 요약이 저장되지 않았습니다)");
  expect(previewSummaryText({ operation: "sync" }))
    .toBe("(요약 없음 — 이 잡은 미리보기 요약이 저장되지 않았습니다)");
});

test("shows error message on fingerprint mismatch", async () => {
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  await userEvent.click(screen.getByRole("button", { name: "컨펌" }));
  expect(await screen.findByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeInTheDocument();
});

test("닫았다 다시 열면 이전 확인 오류가 남지 않는다", async () => {
  // Radix 는 "닫기" 버튼의 setOpen(false) 직접 호출에 onOpenChange 를 태우지 않는다
  // -- reset 없이는 지문 만료/변경 409 오류가 재오픈에 그대로 남아 새 시도의 결과와
  // 혼동된다(StoragesList DeleteButton 의 useEffect reset 선례와 같은 처방).
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  await userEvent.click(screen.getByRole("button", { name: "컨펌" }));
  expect(await screen.findByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "닫기" }));
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText(/abc123/)).toBeInTheDocument(); // 재오픈이 실제로 됐다
  expect(screen.queryByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeNull();
});

test("rm job shows rm-specific dialog title", async () => {
  const rmJob = { ...job, operation: "rm" };
  wrap(<ConfirmDialog job={rmJob as any} />);
  await userEvent.click(screen.getByRole("button", { name: "작업 컨펌" }));
  expect(await screen.findByText("rm 작업 컨펌")).toBeInTheDocument();
});
