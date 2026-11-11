import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { StoragesList } from "./StoragesList";
// 삭제 확인 창이 함께 지워질 StorageClass 매핑·sync 허용 쌍을 묻는다(2026-11-11) -- 기본은 빈 목록.
const server = setupServer(
  http.get("/api/admin/k8s/storage-classes", () => HttpResponse.json([])),
  http.get("/api/admin/sync-pairs", () => HttpResponse.json([])),
);
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
  // 지금 종류도 함께 싣는다(종류 키가 없는 행 = 파일시스템).
  const base = { mount_path: S.mount_path, managed_root: S.managed_root, backend_type: S.backend_type, kind: "filesystem" };
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


// ---- 스토리지 종류 배지(dms-k8s 설계 D11) -----------------------------------------------------------
test("종류 열에 글자 배지(점 없음), 범위를 바꿔도 지금 종류를 함께 싣는다", async () => {
  const bodies: unknown[] = [];
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([
      S, { ...S, storage_name: "k8s-pv-nfs", kind: "pv" }, { ...S, storage_name: "k8s-pv-gpfs", kind: "both" }])),
    http.put("/api/admin/storages/:name", async ({ request }) => {
      bodies.push(await request.json()); return HttpResponse.json(S); }),
  );
  wrap();
  expect(await screen.findByRole("columnheader", { name: "종류" })).toBeInTheDocument();
  const badge = within(screen.getByText("k8s-pv-nfs").closest("tr")!).getByText("PV");
  expect(badge.tagName).toBe("SPAN");
  expect(badge.querySelector("span")).toBeNull();                       // 점(dot) 없음
  expect(badge.className).not.toMatch(/rounded-full/);                  // 상태 pill 아님
  expect(within(screen.getByText("k8s-pv-gpfs").closest("tr")!).getByText("PV·파일시스템")).toBeInTheDocument();
  expect(within(screen.getByText("cephfs").closest("tr")!).getByText("파일시스템")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("k8s-pv-nfs 사용 범위"), "관리자 전용");
  await waitFor(() => expect(bodies).toEqual([{ mount_path: S.mount_path, managed_root: S.managed_root,
    backend_type: S.backend_type, enabled: true, user_enabled: false, kind: "pv" }]));
});


// ---- 2026-11-11 전수조사 수정 -----------------------------------------------------------------------------------------
test("목록 첫 조회가 실패하면 오류를 말하고 범례는 「–」(0 이 아님), 빈 목록이면 안내", async () => {
  // 예전엔 둘 다 범례 0·0·0 + 머리줄만 있는 빈 표라 스토리지가 모두 사라진 것처럼 보였다(「모름」을 0 으로 뭉갬).
  server.use(http.get("/api/admin/storages", () => HttpResponse.json({ detail: "http_500" }, { status: 500 })));
  const first = wrap();
  expect(await screen.findByText("서버 오류가 발생했습니다")).toHaveClass("text-bad");
  const legend = screen.getByLabelText("사용 범위 안내");
  expect(legend.textContent).toMatch(/전체 사용–/);
  expect(legend.textContent).not.toMatch(/0/);
  expect(screen.queryByRole("columnheader", { name: "이름" })).toBeNull();
  first.unmount();

  server.use(http.get("/api/admin/storages", () => HttpResponse.json([])));
  wrap();
  expect(await screen.findByText(/등록된 스토리지가 없습니다/)).toBeInTheDocument();
  expect(screen.getByLabelText("사용 범위 안내").textContent).toMatch(/전체 사용0/);   // 받은 빈 목록의 0 은 정상값
});

test("삭제 확인 창은 함께 지워질 StorageClass 매핑·sync 허용 쌍과 재등록해도 돌아오지 않음을 말한다", async () => {
  // 서버는 같은 트랜잭션에서 둘 다 지운다(StoragesRepository.delete) -- 말없이 지우면 PV 가 모두 「매핑 없음」이 된다.
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([{ ...S, storage_name: "k8s-pv-nfs", kind: "pv" }])),
    http.get("/api/admin/k8s/storage-classes", () => HttpResponse.json([
      { cluster: "tb", name: "tb-nfs", storage_name: "k8s-pv-nfs" },
      { cluster: "c2", name: "c2-nfs", storage_name: "k8s-pv-nfs" },
      // 매핑만 남고 어느 클러스터에도 없는 이름(서버가 cluster: null 로 싣는다) -- 「(null)」이 아니라 사실을 말한다.
      { cluster: null, name: "gone-nfs", storage_name: "k8s-pv-nfs" },
      { cluster: "tb", name: "other", storage_name: "cephfs" }])),
    http.get("/api/admin/sync-pairs", () => HttpResponse.json([
      { source_storage: "cephfs", destination_storage: "k8s-pv-nfs" },
      { source_storage: "cephfs", destination_storage: "cephfs" }])));
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "삭제" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(/함께 삭제됩니다 — 같은 이름으로 다시 등록해도 돌아오지 않습니다/)).toBeInTheDocument();
  expect(await within(dialog).findByText("StorageClass 매핑: tb-nfs (tb), c2-nfs (c2), gone-nfs (어느 클러스터에도 없음)"))
    .toBeInTheDocument();
  expect(within(dialog).queryByText(/\(null\)/)).toBeNull();
  expect(await within(dialog).findByText("sync 허용 쌍 1개: cephfs → k8s-pv-nfs")).toBeInTheDocument();
});

test("수정 창의 경로 변경이 409 storage_in_use_path_change 로 막히면 삭제 문구가 아니라 수정 문구를 보인다", async () => {
  // 예전엔 PUT 가드가 삭제와 같은 storage_in_use 라 「삭제할 수 없습니다(비활성화하세요)」가 떴고, 비활성화해도 가드는 그대로였다.
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([S])),
    http.put("/api/admin/storages/:name", () =>
      HttpResponse.json({ detail: "storage_in_use_path_change" }, { status: 409 })));
  wrap();
  await userEvent.click(await screen.findByRole("button", { name: "수정" }));
  const dialog = await screen.findByRole("dialog");
  const root = within(dialog).getByLabelText("관리 루트");
  await userEvent.clear(root);
  await userEvent.type(root, "/cephfs/elsewhere");
  await userEvent.click(within(dialog).getByRole("button", { name: "저장" }));
  expect(await within(dialog).findByText(/진행 중인 작업이 쓰는 스토리지라 마운트 경로·관리 루트·백엔드를 바꿀 수 없습니다/))
    .toBeInTheDocument();
  expect(within(dialog).queryByText(/삭제할 수 없습니다/)).toBeNull();
});

// ---- 기본 소유(2026-10-26 -- PV 목적지 sync 결과 소유) ----------------------------------------------------------------
test("PV·둘 다 행은 종류 배지 아래 「기본 소유 uid:gid」(0:0 은 root), 파일시스템 행엔 없고, 범위 PUT 은 기본 소유를 싣지 않는다", async () => {
  const bodies: unknown[] = [];
  server.use(
    http.get("/api/admin/storages", () => HttpResponse.json([
      S,
      { ...S, storage_name: "k8s-pv-nfs", kind: "pv", default_uid: 1500, default_gid: 1500, default_owner: "1500:1500" },
      { ...S, storage_name: "k8s-pv-gpfs", kind: "both", default_uid: 0, default_gid: 0, default_owner: "0:0" },
      { ...S, storage_name: "k8s-pv-bad", kind: "pv", default_uid: -1, default_gid: 0, default_owner: null },
      { ...S, storage_name: "k8s-pv-old", kind: "pv" }])),
    http.put("/api/admin/storages/:name", async ({ request }) => {
      bodies.push(await request.json()); return HttpResponse.json(S); }),
  );
  wrap();
  const row = async (name: string) => (await screen.findByText(name)).closest("tr")!;
  expect(within(await row("k8s-pv-nfs")).getByText("기본 소유 1500:1500")).toBeInTheDocument();
  expect(within(await row("k8s-pv-gpfs")).getByText("기본 소유 0:0(root)")).toBeInTheDocument();
  expect(within(await row("k8s-pv-bad")).getByText("기본 소유 값 오류 — 수정에서 다시 저장")).toHaveClass("text-bad");
  expect(within(await row("k8s-pv-old")).getByText("기본 소유 모름")).toBeInTheDocument();     // 옛 응답(키 없음)
  expect(within(await row("cephfs")).queryByText(/기본 소유/)).toBeNull();
  // 종류 배지와 같은 칸의 둘째 줄이다(열을 늘리지 않는다 -- e2e 03 의 표 셀 수 하한)
  const kindCell = within(await row("k8s-pv-nfs")).getByText("PV").closest("td")!;
  expect(within(kindCell).getByText("기본 소유 1500:1500")).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByLabelText("k8s-pv-nfs 사용 범위"), "관리자 전용");
  await waitFor(() => expect(bodies).toEqual([{ mount_path: S.mount_path, managed_root: S.managed_root,
    backend_type: S.backend_type, enabled: true, user_enabled: false, kind: "pv" }]));
});
