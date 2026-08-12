import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { ConfirmDialog } from "./ConfirmDialog";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const job = { job_id: "j1", request_id: "r1", operation: "sync", state: "ConfirmPending",
  reason_code: null, preview_fingerprint: "abc123", preview_expires_at: "2099-01-01T00:00:00Z",
  result_summary: "3 files, 12 MiB", transitions: [] };

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

test("shows fingerprint and posts it on confirm", async () => {
  let body: any = null;
  server.use(http.post("/api/user/jobs/j1:confirm", async ({ request }) => {
    body = await request.json();
    return HttpResponse.json({ state: "Executing" });
  }));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "미리보기 확인" }));
  expect(await screen.findByText(/abc123/)).toBeInTheDocument();
  expect(screen.getByText(/3 files/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "확인" }));
  await screen.findByText(/./); // flush
  expect(body).toEqual({ fingerprint: "abc123" });
});

test("shows error message on fingerprint mismatch", async () => {
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "미리보기 확인" }));
  await userEvent.click(screen.getByRole("button", { name: "확인" }));
  expect(await screen.findByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeInTheDocument();
});

test("닫았다 다시 열면 이전 확인 오류가 남지 않는다", async () => {
  // Radix 는 "닫기" 버튼의 setOpen(false) 직접 호출에 onOpenChange 를 태우지 않는다
  // -- reset 없이는 지문 만료/변경 409 오류가 재오픈에 그대로 남아 새 시도의 결과와
  // 혼동된다(StoragesList DeleteButton 의 useEffect reset 선례와 같은 처방).
  server.use(http.post("/api/user/jobs/j1:confirm",
    () => HttpResponse.json({ detail: "fingerprint_mismatch" }, { status: 409 })));
  wrap(<ConfirmDialog job={job as any} />);
  await userEvent.click(screen.getByRole("button", { name: "미리보기 확인" }));
  await userEvent.click(screen.getByRole("button", { name: "확인" }));
  expect(await screen.findByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "닫기" }));
  await userEvent.click(screen.getByRole("button", { name: "미리보기 확인" }));
  expect(await screen.findByText(/abc123/)).toBeInTheDocument(); // 재오픈이 실제로 됐다
  expect(screen.queryByText("미리보기가 변경되었습니다. 다시 확인해 주세요")).toBeNull();
});

test("rm job shows rm-specific dialog title", async () => {
  const rmJob = { ...job, operation: "rm" };
  wrap(<ConfirmDialog job={rmJob as any} />);
  await userEvent.click(screen.getByRole("button", { name: "미리보기 확인" }));
  expect(await screen.findByText("rm 미리보기 확인")).toBeInTheDocument();
});
