import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { afterAll, afterEach, beforeAll, expect, test, vi } from "vitest";
import { reasonText } from "../../lib/api";
import { SyncHistoryPage, changeSummary } from "./SyncHistoryPage";
import { CLUSTER_OK, run } from "./k8sFixtures";
import type { SyncRun } from "./types";

// 동기화 이력(설계 §7): seq 커서 무한 스크롤(before=<마지막 seq>), ?cluster= 로 시작, 결과 요약(추가·삭제 개수 / 실패 사유),
// 상세 Dialog(이름 목록·개수·경고·오류 원문), 빈 상태·오류 상태.

const server = setupServer();
beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => { server.resetHandlers(); vi.unstubAllGlobals(); observers.length = 0; });
afterAll(() => server.close());

// 감시 노드를 테스트가 직접 「보이게」 만드는 IntersectionObserver(감사 로그 테스트와 같은 방식).
const observers: { cb: IntersectionObserverCallback }[] = [];
class ControlledIO {
  constructor(public cb: IntersectionObserverCallback) { observers.push(this); }
  observe() {} unobserve() {} disconnect() {} takeRecords() { return []; }
}
const scrollToBottom = () => act(() => {
  observers[observers.length - 1].cb([{ isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
});
const runs = (from: number, to: number): SyncRun[] => Array.from({ length: from - to + 1 }, (_, i) => run(from - i));

function wrap(handler: (p: URLSearchParams) => SyncRun[] | Response, at = "/admin/k8s/history") {
  const asked: string[] = [];
  server.use(
    http.get("/api/admin/k8s/runs", ({ request }) => {
      const u = new URL(request.url);
      asked.push(u.search);
      const out = handler(u.searchParams);
      return out instanceof Response ? out : HttpResponse.json(out);
    }),
    http.get("/api/admin/k8s/clusters", () => HttpResponse.json([CLUSTER_OK])),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}>
    <MemoryRouter initialEntries={[at]}><SyncHistoryPage /></MemoryRouter>
  </QueryClientProvider>);
  return asked;
}

test("changeSummary: 「PVC +2 −1 · 바인딩 +1」, 변화 없음은 「변경 없음」, 정보 없음은 null", () => {
  expect(changeSummary({ pvcs: { added: 2, removed: 1 }, bindings: { added: 1, removed: 0 } })).toBe("PVC +2 −1 · 바인딩 +1");
  expect(changeSummary({ pvcs: { added: 0, removed: 0 } })).toBe("변경 없음");
  expect(changeSummary(null)).toBeNull();
});

test("seq 커서: 마지막 seq 를 before 로 다음 쪽을 이어 붙이고, 덜 찬 쪽에서 끝난다", async () => {
  vi.stubGlobal("IntersectionObserver", ControlledIO);
  const asked = wrap((p) => (p.get("before") === null ? runs(150, 101) : runs(100, 71)));
  expect(await screen.findByText("50건 표시 — 스크롤하면 더 불러옵니다")).toBeInTheDocument();
  expect(screen.queryByText("100")).not.toBeInTheDocument();
  scrollToBottom();
  expect(await screen.findByText("마지막 기록입니다 — 전체 80건")).toBeInTheDocument();
  expect(asked).toEqual(["?limit=50", "?before=101&limit=50"]);
  scrollToBottom();
  expect(asked).toHaveLength(2);
});

test("?cluster= 로 시작하면 그 클러스터만, 필터를 바꾸면 쿼리도", async () => {
  const asked = wrap(() => runs(3, 1), "/admin/k8s/history?cluster=dmsk8s");
  await screen.findByText("마지막 기록입니다 — 전체 3건");
  expect(asked[0]).toBe("?cluster=dmsk8s&limit=50");
  expect(screen.getByLabelText("클러스터 필터")).toHaveValue("dmsk8s");
  await userEvent.selectOptions(screen.getByLabelText("호출 노드 필터"), "k8s-w1");
  await waitFor(() => expect(asked).toContain("?cluster=dmsk8s&node=k8s-w1&limit=50"));
});

test("결과 칸: 적용된 run 은 변경 요약, 실패한 run 은 사유", async () => {
  wrap(() => [
    run(12, { changes: { pvcs: { added: 2, removed: 1, names: { added: ["team-a/new1", "team-a/new2"], removed: ["team-b/old"] } },
                         bindings: { added: 1, removed: 0, names: ["team-a/alice-clusterrole-edit"] } } }),
    run(11, { status: "failed", reason: "k8s_sync_unauthorized", changes: null, http_status: 401, phase: "pvcs",
              error: "Unauthorized", applied_at: null }),
  ]);
  const ok = (await screen.findByText("PVC +2 −1 · 바인딩 +1")).closest("tr")!;
  expect(within(ok).getByText("적용됨")).toBeInTheDocument();
  const bad = screen.getByText(reasonText("k8s_sync_unauthorized")).closest("tr")!;
  expect(within(bad).getByText("실패")).toBeInTheDocument();
});

test("상세: 추가·삭제 개수와 이름, 받은 개수, 실패의 단계·HTTP·오류 원문", async () => {
  wrap(() => [
    run(12, { changes: { pvcs: { added: 2, removed: 1, names: { added: ["team-a/new1", "team-a/new2"], removed: ["team-b/old"] } } },
              warnings: ["k8s_sync_forbidden"], counts: { storage_classes: null, pvcs: 7 } }),
    run(11, { status: "failed", reason: "k8s_sync_unauthorized", changes: null, http_status: 401, phase: "pvcs",
              error: "Unauthorized" }),
  ]);
  await screen.findByText("PVC +2 −1");
  const rows = screen.getAllByRole("button", { name: "상세" });
  await userEvent.click(rows[0]);
  let dialog = await screen.findByRole("dialog", { name: "동기화 #12 — dmsk8s" });
  expect(within(dialog).getByText("추가 2 · 삭제 1")).toBeInTheDocument();
  expect(within(dialog).getByText("team-a/new2")).toBeInTheDocument();
  expect(within(dialog).getByText("team-b/old")).toBeInTheDocument();
  expect(within(dialog).getByText(`경고: ${reasonText("k8s_sync_forbidden")}`)).toBeInTheDocument();
  const scTile = within(dialog).getByText("StorageClass").parentElement!;
  expect(scTile).toHaveTextContent("—");                                     // null = 그 목록을 못 받음(모름)
  await userEvent.click(within(dialog).getByRole("button", { name: "닫기" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  await userEvent.click(screen.getAllByRole("button", { name: "상세" })[1]);
  dialog = await screen.findByRole("dialog", { name: "동기화 #11 — dmsk8s" });
  expect(within(dialog).getByText("단계 pvcs · HTTP 401")).toBeInTheDocument();
  expect(within(dialog).getByText("Unauthorized")).toBeInTheDocument();
  expect(within(dialog).getByText("변경 정보가 없습니다(적용되지 않은 run)")).toBeInTheDocument();
});

test("서버 모양 그대로(2026-10-26 리뷰): SC·PV 목록을 못 받은 run(null)도 그려지고, 내용만 바뀐 항목(changed)·경고 {phase, reason}", async () => {
  // repositories.k8s._diff_apply + routes_agent._warnings 의 와이어 모양 — 예전엔 null 항목에서 화면 전체가 죽었다.
  wrap(() => [
    run(13, {
      changes: {
        storage_classes: null, pvs: null,
        pvcs: { added: 0, removed: 0, changed: 1, names: { added: [], removed: [], changed: ["team-a/checkpoints"] } },
        bindings: { added: 0, removed: 0, changed: 0, names: { added: [], removed: [], changed: [] } },
      },
      warnings: [{ phase: "storageclasses", reason: "k8s_sync_forbidden", http_status: 403 }],
      counts: { storage_classes: null, pvs: null, pvcs: 7 },
    }),
  ]);
  await screen.findByText("PVC ~1");
  await userEvent.click(screen.getByRole("button", { name: "상세" }));
  const dialog = await screen.findByRole("dialog", { name: "동기화 #13 — dmsk8s" });
  expect(within(dialog).getByText("추가 0 · 삭제 0 · 바뀜 1")).toBeInTheDocument();
  expect(within(dialog).getByText("team-a/checkpoints")).toBeInTheDocument();
  expect(within(dialog).getAllByText("이번에 받지 못함(목록 권한 없음 등 — 이전 값 유지)")).toHaveLength(2);
  expect(within(dialog).getByText(`경고: StorageClass 목록: ${reasonText("k8s_sync_forbidden")}`)).toBeInTheDocument();
  expect(changeSummary({ pvcs: { added: 1, removed: 0, changed: 2 }, pvs: null })).toBe("PVC +1 ~2");
});

test("빈 상태와 필터 빈 상태를 다른 문구로", async () => {
  wrap(() => []);
  expect(await screen.findByText(/아직 동기화 기록이 없습니다/)).toBeInTheDocument();
});

test("오류 상태: 첫 쪽 실패는 role=alert", async () => {
  wrap(() => HttpResponse.json({ detail: "boom" }, { status: 500 }) as unknown as Response);
  expect(await screen.findByRole("alert")).toHaveTextContent("이력을 불러오지 못했습니다");
});
