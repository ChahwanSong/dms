import { describe, it, expect, beforeAll, afterEach, afterAll, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { ReleasesPage } from "./ReleasesPage";

const TARGETS = {
  registry_ok: true,
  targets: [
    { component: "dms-agent", kind: "DaemonSet", workload: "dms-agent",
      container: "agent", repository: "dms-agent",
      current_image: "pkg-01:5000/dms-agent:dev5", tags: ["dev5", "dev6"] },
    { component: "dms-api", kind: "Deployment", workload: "dms-api",
      container: "api", repository: "dms",
      current_image: "pkg-01:5000/dms:d22", tags: ["d22", "d23"] },
    { component: "dms-controller", kind: "Deployment", workload: "dms-controller",
      container: "controller", repository: "dms",
      current_image: "pkg-01:5000/dms:d22", tags: ["d22", "d23"] },
  ],
};
const HISTORY = {
  current: {
    "dms-api": { id: 1, component: "dms-api", image: "pkg-01:5000/dms:d22",
                 tag: "d22", digest: null, state: "Applied", reason_code: null,
                 actor: "ops", applied_at: "2026-08-06T00:00:00Z" },
  },
  history: [
    { id: 1, component: "dms-api", image: "pkg-01:5000/dms:d22", tag: "d22",
      digest: null, state: "Applied", reason_code: null, actor: "ops",
      applied_at: "2026-08-06T00:00:00Z" },
    { id: 2, component: "dms-agent", image: "pkg-01:5000/dms-agent:dev4",
      tag: "dev4", digest: null, state: "Failed",
      reason_code: "rollout_timeout", actor: "ops",
      applied_at: "2026-08-05T00:00:00Z" },
  ],
};

const server = setupServer(
  http.get("/api/admin/releases/targets", () => HttpResponse.json(TARGETS)),
  http.get("/api/admin/releases", () => HttpResponse.json(HISTORY)),
);
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const wrap = (ui: React.ReactNode) => {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><MemoryRouter>{ui}</MemoryRouter></QueryClientProvider>);
};

describe("ReleasesPage", () => {
  it("세 컴포넌트와 현재 이미지를 렌더한다", async () => {
    wrap(<ReleasesPage />);
    expect(await screen.findByRole("heading", { name: "릴리스" })).toBeInTheDocument();
    expect(screen.getByText("pkg-01:5000/dms-agent:dev5")).toBeInTheDocument();
    expect(screen.getByLabelText("dms-controller")).toBeInTheDocument();
  });

  it("로드된 화면에 컨트롤러 재시작 경고가 있다", async () => {
    // findByText만 쓰면 targets 로딩 중 렌더에 걸려 통과해 버린다 -- 그 상태에서는
    // Card 안의 경고가 아직 존재하지도 않는다. 표가 그려진 뒤에 단언해야
    // "Card에서 경고를 지웠다"는 회귀가 잡힌다(설계 §8: 조건 없이 항상 보인다).
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    expect(screen.getByLabelText("dms-controller")).toBeInTheDocument();
    expect(screen.getByText(/컨트롤러가 재시작되어 롤아웃 추적이 잠시 끊깁니다/))
      .toBeInTheDocument();
  });

  it("targets를 기다리는 로딩 화면에도 경고가 있다", async () => {
    // targets는 최악 30초다(컴포넌트 3종 × 10초 타임아웃). 그 동안 경고가 없으면
    // 운영자는 고르기 전에 읽어야 할 문구를 못 읽는다.
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    server.use(http.get("/api/admin/releases/targets", async () => {
      await gate;
      return HttpResponse.json(TARGETS);
    }));
    wrap(<ReleasesPage />);
    expect(await screen.findByText("불러오는 중…")).toBeInTheDocument();
    expect(screen.getByText(/컨트롤러가 재시작되어 롤아웃 추적이 잠시 끊깁니다/))
      .toBeInTheDocument();
    release();
    await screen.findByRole("heading", { name: "릴리스" });
  });

  it("선택이 없으면 제출이 비활성이고, 선택한 것만 한 배치로 보낸다", async () => {
    let posted: unknown = null;
    server.use(http.post("/api/admin/releases", async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ items: [] }, { status: 202 });
    }));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    expect(screen.getByRole("button", { name: "롤아웃 시작" })).toBeDisabled();
    await userEvent.selectOptions(screen.getByLabelText("dms-agent"), "dev6");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    await waitFor(() => expect(posted).toEqual({
      items: [{ component: "dms-agent", tag: "dev6" }] }));
  });

  it("서버 거절을 한국어로 보여준다", async () => {
    server.use(http.post("/api/admin/releases", () =>
      HttpResponse.json({ detail: "same_tag" }, { status: 422 })));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    await userEvent.selectOptions(screen.getByLabelText("dms-agent"), "dev5");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    expect(await screen.findByText(/현재 태그와 같습니다/)).toBeInTheDocument();
  });

  it("이력의 사유 코드를 번역한다", async () => {
    wrap(<ReleasesPage />);
    expect(await screen.findByText("롤아웃이 제한 시간을 넘겨 실패했습니다"))
      .toBeInTheDocument();
  });

  it("진행 중이면 배지를 보여준다", async () => {
    server.use(http.get("/api/admin/releases", () => HttpResponse.json({
      current: {},
      history: [{ id: 3, component: "dms-agent", image: "i", tag: "t",
                  digest: null, state: "Applying", reason_code: null,
                  actor: "ops", applied_at: "2026-08-06T01:00:00Z" }] })));
    wrap(<ReleasesPage />);
    expect(await screen.findByText("진행 중")).toBeInTheDocument();
  });

  it("이력이 전부 종단이면 배지가 없다", async () => {
    // 반대 증거가 없으면 active를 상수 true로 바꿔도 아무것도 빨간불이 되지 않는다.
    // 기본 HISTORY는 Applied + Failed 두 행뿐이다.
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    await screen.findByText("롤아웃이 제한 시간을 넘겨 실패했습니다");   // 이력이 그려졌다
    expect(screen.queryByText("진행 중")).toBeNull();
  });

  it("진행 중이면 폴링하고, 종단이 되면 멈추고 targets를 한 번 다시 읽는다", async () => {
    // 설계 §8의 세 요구를 한 시나리오로 고정한다: (1) 진행 중 폴링 시작,
    // (2) 종단 전이에서 targets 재조회(useRefreshTargetsOnSettle), (3) 폴링 정지.
    // targets는 비싼 엔드포인트라 (2)가 없으면 롤아웃이 끝나도 "현재 이미지"가 옛
    // 값으로 남아 운영자가 적용 여부를 화면에서 확인할 수 없다.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let releasesCalls = 0;
      let targetsCalls = 0;
      let state = "Applying";
      server.use(
        http.get("/api/admin/releases/targets", () => {
          targetsCalls += 1;
          return HttpResponse.json(TARGETS);
        }),
        http.get("/api/admin/releases", () => {
          releasesCalls += 1;
          return HttpResponse.json({ current: {}, history: [
            { id: 3, component: "dms-agent", image: "i", tag: "t", digest: null,
              state, reason_code: null, actor: "ops",
              applied_at: "2026-08-06T01:00:00Z" }] });
        }),
      );
      wrap(<ReleasesPage />);
      await screen.findByText("진행 중");
      expect(targetsCalls).toBe(1);
      const afterFirstLoad = releasesCalls;

      // (1) 진행 중이면 5초마다 다시 읽는다
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      await waitFor(() => expect(releasesCalls).toBeGreaterThan(afterFirstLoad));

      // (2) 종단으로 바뀌는 순간 targets를 다시 읽는다
      state = "Applied";
      await act(async () => { await vi.advanceTimersByTimeAsync(5100); });
      await waitFor(() => expect(screen.queryByText("진행 중")).toBeNull());
      await waitFor(() => expect(targetsCalls).toBe(2));

      // (3) 그 뒤로는 폴링이 멈춘다
      const afterSettle = releasesCalls;
      await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
      expect(releasesCalls).toBe(afterSettle);
      expect(targetsCalls).toBe(2);        // 재조회는 전이 순간 1회뿐이다
    } finally {
      vi.useRealTimers();
    }
  });

  it("레지스트리가 죽어도 화면이 산다", async () => {
    server.use(http.get("/api/admin/releases/targets", () =>
      HttpResponse.json({ registry_ok: false,
                          targets: TARGETS.targets.map(t => ({ ...t, tags: [] })) })));
    wrap(<ReleasesPage />);
    expect(await screen.findByText(/레지스트리에 연결할 수 없어/)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "릴리스" })).toBeInTheDocument();
  });

  it("응답이 배열이 아니어도 흰 화면이 되지 않는다", async () => {
    server.use(
      http.get("/api/admin/releases/targets", () => HttpResponse.json({ oops: 1 })),
      http.get("/api/admin/releases", () => HttpResponse.json(null)),
    );
    wrap(<ReleasesPage />);
    expect(await screen.findByRole("heading", { name: "릴리스" })).toBeInTheDocument();
  });

  it("미검증 제출(tag_verified=false)은 경고 배너를 띄운다", async () => {
    // 레지스트리 전면 다운이면 드롭다운이 비어 UI 제출 자체가 불가하다 --
    // 이 배너가 잡는 실 창은 "목록 로드 후 제출 전 장애"(TOCTOU)와 리포별
    // 부분 침묵이다. msw 로 그 결과(202 + tag_verified:false)만 재현한다.
    server.use(http.post("/api/admin/releases", () =>
      HttpResponse.json({ items: [], tag_verified: false }, { status: 202 })));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    await userEvent.selectOptions(screen.getByLabelText("dms-api"), "d23");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    expect(await screen.findByText(/태그 존재를 확인하지 못한 채/)).toBeInTheDocument();
  });

  it("검증된 제출(tag_verified=true)에는 경고 배너가 없다", async () => {
    server.use(http.post("/api/admin/releases", () =>
      HttpResponse.json({ items: [], tag_verified: true }, { status: 202 })));
    wrap(<ReleasesPage />);
    await screen.findByRole("heading", { name: "릴리스" });
    await userEvent.selectOptions(screen.getByLabelText("dms-api"), "d23");
    await userEvent.click(screen.getByRole("button", { name: "롤아웃 시작" }));
    // 성공 시 선택이 비워지는 기존 거동을 settle 신호로 쓴다.
    await waitFor(() => expect(screen.getByLabelText("dms-api")).toHaveValue(""));
    expect(screen.queryByText(/태그 존재를 확인하지 못한 채/)).toBeNull();
  });
});
