import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { StoragesList } from "./StoragesList";
const server = setupServer();
beforeAll(() => server.listen()); afterEach(() => server.resetHandlers()); afterAll(() => server.close());
const S = { storage_name:"cephfs", mount_path:"/cephfs", managed_root:"/cephfs/dms",
  backend_type:"ceph", enabled:1, status:"Healthy", status_detail:null };
function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><StoragesList /></QueryClientProvider>);
}
test("lists storages and shows manage actions", async () => {
  server.use(http.get("/api/admin/storages", () => HttpResponse.json([S])));
  wrap();
  expect(await screen.findByText("cephfs")).toBeInTheDocument();
  expect(screen.getByText("Healthy")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "스토리지 등록" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "삭제" })).toBeInTheDocument();
});
// 관리 디렉토리 컬럼(사용자 보고 2026-08-15): 표에 마운트만 있고 관리 디렉토리가
// 없어, 운영자가 스토리지↔경로 매핑의 기준점을 이 화면에서 얻을 수 없었다.
// (작업 등록·조회 화면의 절대경로 표시가 이 값을 뿌리로 쓴다.)
test("관리 디렉토리 컬럼: managed_root 를 마운트와 나란히 보여준다", async () => {
  server.use(http.get("/api/admin/storages", () => HttpResponse.json([S])));
  wrap();
  expect(await screen.findByText("관리 디렉토리")).toBeInTheDocument();   // 헤더
  expect(screen.getByText("/cephfs/dms")).toBeInTheDocument();
  expect(screen.getByText("/cephfs")).toBeInTheDocument();               // 마운트는 그대로
});

test("storage badges: Ready=ok, Degraded=busy, and actions td is not a flex cell", async () => {
  const ready = { ...S, storage_name: "st-ready", status: "Ready" };
  const degraded = { ...S, storage_name: "st-degraded", status: "Degraded" };
  server.use(http.get("/api/admin/storages", () => HttpResponse.json([ready, degraded])));
  wrap();
  // Ready 는 ok(녹색), Degraded 는 busy(황색 주의) -- planner 가 Degraded 에도 잡을
  // 보내므로 bad(적색)가 아니라 주의가 정직하다(storagePillVariant 계약).
  expect((await screen.findByText("Ready")).className).toContain("text-ok");
  expect(screen.getByText("Degraded").className).toContain("text-busy");
  // td 자체가 flex 면 표 레이아웃 계산에서 빠진다(9fbef86 구조 결함) -- flex 는
  // td 안 div 가 진다. jsdom 은 기하를 못 재므로 className 으로 못박는다.
  const row = screen.getByText("st-ready").closest("tr");
  const actionsTd = row?.querySelector("td:last-child");
  expect(actionsTd?.className ?? "").not.toMatch(/\bflex\b/);
  expect(actionsTd?.querySelector("div")?.className ?? "").toMatch(/\bflex\b/);
});
test("delete shows in-use error on 409", async () => {
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([S])),
    http.delete("/api/admin/storages/cephfs", () => HttpResponse.json({ detail: "storage_in_use" }, { status: 409 })));
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "삭제" }));
  await userEvent.click(await screen.findByRole("button", { name: "삭제 확인" }));
  expect(await screen.findByText("사용 중인 스토리지는 삭제할 수 없습니다 (비활성화하세요)")).toBeInTheDocument();
});
test("사용 범위 셀렉트: 관리자 전용·완전 비활성을 고르면 즉시 PUT 한다", async () => {
  // 옛 "비활성화/활성화" 토글의 자리(2026-09-30): 비활성이 두 종류다. 완전 비활성은
  // user_enabled 를 생략해(서버 = 현재 값 유지) 다시 켤 때 이전 범위가 돌아온다.
  const bodies: unknown[] = [];
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([S])),
    http.put("/api/admin/storages/:name", async ({ request }) => {
      bodies.push(await request.json());
      return HttpResponse.json(S);
    }));
  wrap();
  const sel = await screen.findByLabelText("cephfs 사용 범위") as HTMLSelectElement;
  expect(sel.value).toBe("all");
  await userEvent.selectOptions(sel, "관리자 전용");
  await userEvent.selectOptions(sel, "완전 비활성");
  const base = { mount_path: S.mount_path, managed_root: S.managed_root, backend_type: S.backend_type };
  await screen.findByLabelText("cephfs 사용 범위");
  expect(bodies).toEqual([
    { ...base, enabled: true, user_enabled: false },
    { ...base, enabled: false },
  ]);
});

test("사용 범위: 행 상태를 셀렉트로, 범례에 세 상태의 개수를 보인다", async () => {
  server.use(http.get("/api/admin/storages", () => HttpResponse.json([
    S, { ...S, storage_name: "adm", user_enabled: 0 }, { ...S, storage_name: "off", enabled: 0 },
  ])));
  wrap();
  expect(((await screen.findByLabelText("adm 사용 범위")) as HTMLSelectElement).value).toBe("admin");
  expect((screen.getByLabelText("off 사용 범위") as HTMLSelectElement).value).toBe("off");
  expect((screen.getByLabelText("cephfs 사용 범위") as HTMLSelectElement).value).toBe("all");
  const legend = screen.getByLabelText("사용 범위 안내");
  expect(legend.textContent).toMatch(/전체 사용1/);
  expect(legend.textContent).toMatch(/관리자 전용1/);
  expect(legend.textContent).toMatch(/완전 비활성1/);
  expect(legend.textContent).toMatch(/사용자에게만 비활성/);
});

test("완전 비활성 행은 DB 에 남은 이전 범위(관리자 전용)를 보인다", async () => {
  server.use(http.get("/api/admin/storages", () => HttpResponse.json([
    { ...S, storage_name: "off-adm", enabled: 0, user_enabled: 0 },
    { ...S, storage_name: "off-all", enabled: 0, user_enabled: 1 },
  ])));
  wrap();
  const offAdm = (await screen.findByText("off-adm")).closest("tr")!;
  expect(within(offAdm).getByText("이전 설정: 관리자 전용")).toBeInTheDocument();
  const offAll = screen.getByText("off-all").closest("tr")!;
  expect(within(offAll).queryByText(/이전 설정/)).toBeNull();
});
test("cancelling the delete dialog clears the stale 409 error", async () => {
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([S])),
    http.delete("/api/admin/storages/cephfs", () => HttpResponse.json({ detail: "storage_in_use" }, { status: 409 })));
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "삭제" }));
  await userEvent.click(await screen.findByRole("button", { name: "삭제 확인" }));
  const msg = "사용 중인 스토리지는 삭제할 수 없습니다 (비활성화하세요)";
  expect(await screen.findByText(msg)).toBeInTheDocument();
  // "취소"는 setOpen(false)를 직접 부른다 — Radix의 onOpenChange가 발화하지 않는 경로다.
  await userEvent.click(screen.getByRole("button", { name: "취소" }));
  await userEvent.click(await screen.findByRole("button", { name: "삭제" }));
  expect(screen.queryByText(msg)).not.toBeInTheDocument();
});
