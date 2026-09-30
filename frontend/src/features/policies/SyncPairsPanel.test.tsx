import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { SyncPairsPanel } from "./SyncPairsPanel";
import type { Storage, SyncPair } from "../../lib/types";

// 사용자 sync 허용 쌍 편집기(2026-09-30): 행 = 소스, 열 = 목적지, 체크 = 즉시 저장.

const st = (storage_name: string, extra: Partial<Storage> = {}): Storage => ({
  storage_name, mount_path: `/mnt/${storage_name}`, managed_root: `/mnt/${storage_name}/dms`,
  backend_type: "cephfs", enabled: 1, user_enabled: 1, status: "Ready", status_detail: null, ...extra });

const STORAGES = [st("ceph-b"), st("ceph-a"), st("adm", { user_enabled: 0 }), st("off", { enabled: 0 })];

let pairs: SyncPair[] = [];
const server = setupServer(
  http.get("/api/admin/storages", () => HttpResponse.json(STORAGES)),
  http.get("/api/admin/sync-pairs", () => HttpResponse.json(pairs)),
);
beforeAll(() => server.listen());
afterEach(() => { server.resetHandlers(); pairs = []; });
afterAll(() => server.close());

function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><SyncPairsPanel /></QueryClientProvider>);
}

const cell = (src: string, dst: string) => screen.findByRole("checkbox", { name: `${src} → ${dst} 허용` });

test("행 = 소스, 열 = 목적지 매트릭스 -- 허용 쌍만 체크, 방향이 다른 칸은 별개다", async () => {
  pairs = [{ source_storage: "ceph-a", destination_storage: "ceph-b" }];
  wrap();
  expect(await cell("ceph-a", "ceph-b")).toBeChecked();
  expect(await cell("ceph-b", "ceph-a")).not.toBeChecked();
  expect(await cell("ceph-a", "ceph-a")).not.toBeChecked();   // 대각선도 하나의 쌍
  // 4 × 4 = 16 칸(관리자 전용·비활성 포함)
  expect(screen.getAllByRole("checkbox")).toHaveLength(16);
  expect(screen.getByText("허용된 쌍")).toBeInTheDocument();
  const list = screen.getByRole("list", { name: "허용된 쌍 목록" });
  expect(within(list).getByText("ceph-a → ceph-b")).toBeInTheDocument();
  // 행·열 머리에 사용 범위가 붙는다
  expect(screen.getAllByText("관리자 전용").length).toBeGreaterThan(0);
  expect(screen.getAllByText("완전 비활성").length).toBeGreaterThan(0);
});

test("체크하면 그 방향의 쌍을 POST 하고, 재조회 뒤 체크된 채로 남는다", async () => {
  let body: unknown = null;
  server.use(http.post("/api/admin/sync-pairs", async ({ request }) => {
    body = await request.json();
    pairs = [{ source_storage: "ceph-b", destination_storage: "ceph-a" }];
    return HttpResponse.json(pairs[0], { status: 201 });
  }));
  wrap();
  await userEvent.click(await cell("ceph-b", "ceph-a"));
  await waitFor(() => expect(body).toEqual({ source_storage: "ceph-b", destination_storage: "ceph-a" }));
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "ceph-b → ceph-a 허용" })).toBeEnabled());
  expect(screen.getByRole("checkbox", { name: "ceph-b → ceph-a 허용" })).toBeChecked();
  expect(screen.getByRole("checkbox", { name: "ceph-a → ceph-b 허용" })).not.toBeChecked();
});

test("체크를 풀면 그 쌍을 DELETE 한다", async () => {
  pairs = [{ source_storage: "ceph-a", destination_storage: "ceph-b" }];
  let deleted: string | null = null;
  server.use(http.delete("/api/admin/sync-pairs/:src/:dst", ({ params }) => {
    deleted = `${params.src}->${params.dst}`;
    pairs = [];
    return HttpResponse.json({ source_storage: params.src, destination_storage: params.dst, deleted: true });
  }));
  wrap();
  await userEvent.click(await cell("ceph-a", "ceph-b"));
  await waitFor(() => expect(deleted).toBe("ceph-a->ceph-b"));
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "ceph-a → ceph-b 허용" })).not.toBeChecked());
  expect(screen.getByRole("checkbox", { name: "ceph-a → ceph-b 허용" })).toBeEnabled();
});

test("저장 실패는 사유를 알리고 칸을 서버 값으로 되돌린다", async () => {
  server.use(http.post("/api/admin/sync-pairs", () =>
    HttpResponse.json({ detail: "storage_missing" }, { status: 422 })));
  wrap();
  await userEvent.click(await cell("ceph-a", "ceph-b"));
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("ceph-a → ceph-b 허용 실패");
  await waitFor(() => expect(screen.getByRole("checkbox", { name: "ceph-a → ceph-b 허용" })).toBeEnabled());
  expect(screen.getByRole("checkbox", { name: "ceph-a → ceph-b 허용" })).not.toBeChecked();
});

test("관리자 전용·비활성이 낀 칸은 사용자에게 적용되지 않음을 설명하고, 적용 쌍 0개면 경고한다", async () => {
  pairs = [{ source_storage: "ceph-a", destination_storage: "adm" }];
  wrap();
  const inert = await cell("ceph-a", "adm");
  expect(inert).toBeChecked();
  expect(inert).toHaveAccessibleDescription(/사용자에게는 적용되지 않습니다/);
  expect(await cell("ceph-a", "ceph-b")).not.toHaveAccessibleDescription();
  expect(screen.getByText(/사용자에게 적용 중 0개/)).toBeInTheDocument();
  expect(screen.getByText(/지금은 사용자가 sync 를 제출할 수 없습니다/)).toBeInTheDocument();
  expect(screen.getByText("ceph-a → adm (사용자 미적용)")).toBeInTheDocument();
});

test("토글 뒤 재조회가 실패해도 매트릭스는 직전 값으로 남고 경고만 얹는다", async () => {
  pairs = [{ source_storage: "ceph-a", destination_storage: "ceph-b" }];
  let fail = false;
  server.use(
    http.get("/api/admin/sync-pairs", () => fail
      ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json(pairs)),
    http.post("/api/admin/sync-pairs", () => {
      fail = true;
      return HttpResponse.json({ source_storage: "ceph-b", destination_storage: "ceph-b" }, { status: 201 });
    }));
  wrap();
  await userEvent.click(await cell("ceph-b", "ceph-b"));
  expect(await screen.findByText(/최신 목록을 다시 읽지 못했습니다/)).toBeInTheDocument();
  // 매트릭스는 그대로(직전 값): 기존 허용 칸은 체크된 채
  expect(screen.getByRole("checkbox", { name: "ceph-a → ceph-b 허용" })).toBeChecked();
  expect(screen.getAllByRole("checkbox")).toHaveLength(16);
});
