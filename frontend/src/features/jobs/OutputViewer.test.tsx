import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect, vi } from "vitest";
import { LINE_CAP, OutputViewer, logSaveText, splitLines } from "./OutputViewer";
import type { OutputItem } from "./jobStages";

// 출력 뷰어 고유 동작(2026-10-08 재설계): 줄바꿈 토글·JSON 정렬·2,000줄 상한·로그 저장(Blob)·파드 섹션·빈 로그.
const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); vi.restoreAllMocks(); localStorage.clear(); });
afterAll(() => server.close());

const LOG: OutputItem = { kind: "log", key: "log:preflight", phase: "preflight" };
const art = (name: string): OutputItem => ({ kind: "artifact", key: `artifact:execution/${name}`, phase: "execution", name, size: 10 });

function renderViewer(item: OutputItem, entrySize: number | null = 10) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <OutputViewer jobId="0123456789abcdef" item={item} entrySize={entrySize} live={false} viewerId="v1" onClose={() => {}} />
    </QueryClientProvider>,
  );
}
const logsReply = (entries: object[], source = "archived") =>
  http.get("/api/user/jobs/0123456789abcdef/logs", () => HttpResponse.json({ phase: "preflight", ref: "pod/p1", source, entries }));
const fileReply = (name: string, content: string, truncated = false) =>
  http.get(`/api/user/jobs/0123456789abcdef/artifacts/execution/${name}`, () =>
    HttpResponse.json({ phase: "execution", name, size: content.length, truncated, content }));

test("줄바꿈 토글: aria-pressed 와 줄 클래스가 바뀌고, 선택은 localStorage 에 남는다", async () => {
  server.use(logsReply([{ pod: "p1", log: "a long line" }]));
  renderViewer(LOG);
  const line = await screen.findByText("a long line");
  const toggle = screen.getByRole("button", { name: "줄바꿈" });
  expect(toggle).toHaveAttribute("aria-pressed", "true");          // 기본 켬
  expect(line.className).toContain("whitespace-pre-wrap");
  await userEvent.click(toggle);
  expect(toggle).toHaveAttribute("aria-pressed", "false");
  expect(screen.getByText("a long line").className).toContain("whitespace-pre");
  expect(screen.getByText("a long line").className).not.toContain("whitespace-pre-wrap");
  expect(localStorage.getItem("dms.logWrap")).toBe("0");
});

test("localStorage 가 던져도(사생활 보호 모드) 기본값으로 렌더된다", async () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
  server.use(logsReply([{ pod: "p1", log: "still here" }]));
  renderViewer(LOG);
  expect(await screen.findByText("still here")).toBeInTheDocument();
  const toggle = screen.getByRole("button", { name: "줄바꿈" });
  expect(toggle).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(toggle);                                    // 쓰기 실패도 삼킨다
  expect(toggle).toHaveAttribute("aria-pressed", "false");
});

test(".json 은 정렬해 보이고 다운로드 href 는 그대로다", async () => {
  server.use(fileReply("summary.json", '{"files":2,"bytes":10}'));
  renderViewer(art("summary.json"));
  expect(await screen.findByText('"files": 2,')).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "다운로드 (10 B)" }))
    .toHaveAttribute("href", "/api/user/jobs/0123456789abcdef/artifacts/execution/summary.json/download");
});

test("잘려서 파싱이 안 되는 JSON 은 원문 그대로", async () => {
  server.use(fileReply("summary.json", 'tail": 3}', true));
  renderViewer(art("summary.json"));
  expect(await screen.findByText('tail": 3}')).toBeInTheDocument();
  expect(screen.getByText("전체는 다운로드로 받으세요")).toBeInTheDocument();
});

test(`${LINE_CAP}줄을 넘으면 앞부분을 숨기고 「모두 표시」로 전부 보인다`, async () => {
  const text = Array.from({ length: LINE_CAP + 5 }, (_, i) => `line ${i + 1}`).join("\n");
  server.use(fileReply("stdout.log", text));
  renderViewer(art("stdout.log"));
  expect(await screen.findByText(`line ${LINE_CAP + 5}`)).toBeInTheDocument();
  expect(screen.getByText(/^앞 5줄 숨김/)).toBeInTheDocument();
  expect(screen.queryByText("line 1")).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "모두 표시" }));
  expect(screen.getByText("line 1")).toBeInTheDocument();
  expect(screen.queryByText(/^앞 5줄 숨김/)).toBeNull();
});

test("「로그 저장」= 받은 로그를 파드 머리줄과 함께 Blob 으로(clipboard 아님)", async () => {
  const blobs: Blob[] = [];
  const realCreate = URL.createObjectURL;
  const realRevoke = URL.revokeObjectURL;
  URL.createObjectURL = vi.fn((b: Blob) => { blobs.push(b); return "blob:x"; }) as typeof URL.createObjectURL;
  URL.revokeObjectURL = vi.fn();
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  try {
    server.use(logsReply([{ pod: "p1", log: "hello" }, { pod: "p2", log: null, waiting_reason: "Pending" }]));
    renderViewer(LOG);
    await screen.findByText("hello");
    await userEvent.click(screen.getByRole("button", { name: "로그 저장" }));
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    const text = await new Promise<string>((resolve) => {
      const r = new FileReader();
      r.onload = () => resolve(r.result as string);
      r.readAsText(blobs[0]);
    });
    expect(text).toContain("===== p1 =====\nhello\n");
    expect(text).toContain("===== p2 =====\n(로그 없음 — Pending)\n");
  } finally {
    URL.createObjectURL = realCreate;
    URL.revokeObjectURL = realRevoke;
  }
});

test("파드가 여럿이면 파드마다 섹션(launcher·제출 실패 원문 칩), 빈 로그엔 안내, null 로그엔 안 붙는다", async () => {
  server.use(logsReply([
    { pod: "dms-j1-launcher-0", log: "" },
    { pod: "submit:preflight", log: "422 rejected" },
    { pod: "p3", log: null },
  ]));
  const { container } = renderViewer(LOG);
  expect(await screen.findByText("422 rejected")).toBeInTheDocument();
  expect(container.querySelectorAll("[data-pod]")).toHaveLength(3);
  const launcher = container.querySelector('[data-pod="dms-j1-launcher-0"]') as HTMLElement;
  expect(within(launcher).getByText("launcher")).toBeInTheDocument();
  expect(within(launcher).getByText("빈 로그 — 이 파드는 아무것도 출력하지 않았습니다")).toBeInTheDocument();
  const submit = container.querySelector('[data-pod="submit:preflight"]') as HTMLElement;
  expect(within(submit).getByText("제출 실패 원문")).toBeInTheDocument();
  const gone = container.querySelector('[data-pod="p3"]') as HTMLElement;
  expect(within(gone).getByText("파드 로그를 더 이상 조회할 수 없습니다")).toBeInTheDocument();
  expect(within(gone).queryByText(/빈 로그/)).toBeNull();
  expect(screen.getAllByText(/^빈 로그/)).toHaveLength(1);
});

test("splitLines·logSaveText: 끝 줄바꿈은 줄이 아니고, 빈 문자열은 0줄", () => {
  expect(splitLines("")).toEqual([]);
  expect(splitLines("a\nb\n")).toEqual(["a", "b"]);
  expect(splitLines("a\r\n\nb")).toEqual(["a", "", "b"]);
  expect(logSaveText({ phase: "p", ref: null, source: "archived", entries: [{ pod: "x", log: "" }] }))
    .toBe("===== x =====\n");
});
