import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { NodeMetricsSection, timeFractions } from "./NodeMetricsSection";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const METRICS = {
  window_hours: 24, start: "2026-08-09T00:00:00Z", end: "2026-08-10T00:00:00Z",
  nodes: [{
    node_name: "w1", reported_at: "2026-08-10T00:00:00Z", fresh: true,
    points: [
      // cpu_used_pct·cpu_iowait_pct(2026-10-16): 에이전트가 계산한 사용률. 첫 점은 재시작 직후라 null(모름).
      { at: "2026-08-09T23:58:00Z", cpu_used_pct: null, cpu_iowait_pct: null,
        load1: 0.5, load5: 0.4, load15: 0.3,
        mem_used_pct: 50, net_rx_bps: null, net_tx_bps: null,
        disks: [{ storage_name: "s1", used_pct: 40 }] },
      { at: "2026-08-09T23:59:00Z", cpu_used_pct: 60, cpu_iowait_pct: 2,
        load1: 0.7, load5: 0.4, load15: 0.3,
        mem_used_pct: 55, net_rx_bps: 100, net_tx_bps: 10,
        disks: [{ storage_name: "s1", used_pct: 41 }] },
      // 마지막 점이 최대가 아니게(0.6 < 0.7, 52 < 55, 42 < 60) -- "현재 · 최대" 병기가
      // 같은 값의 반복이 아니라 실제로 둘을 구분함을 단언할 수 있는 형상.
      { at: "2026-08-09T23:59:30Z", cpu_used_pct: 42, cpu_iowait_pct: 3,
        load1: 0.6, load5: 0.4, load15: 0.3,
        mem_used_pct: 52, net_rx_bps: 2048, net_tx_bps: 20,
        disks: [{ storage_name: "s1", used_pct: 41 }] },
    ],
  }],
};

const NODES = [{
  node_name: "w1", reported_at: "2026-08-10T00:00:00Z", fresh: true,
  report: {
    mounts: [{ storage_name: "s1", mount_path: "/s1", status: "Ready" }],
    tools: [{ name: "dsync", status: "Ready" }, { name: "drm", status: "Missing" }],
    identities: [],
    os: { cpu_count: 2 },
  },
}];

function renderSection(calls?: (string | null)[]) {
  server.use(
    http.get("/api/admin/metrics/nodes", ({ request }) => {
      calls?.push(new URL(request.url).searchParams.get("window"));
      return HttpResponse.json(METRICS);
    }),
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
}

test("노드별 스파크라인과 신선도를 그린다", async () => {
  const { container } = renderSection();
  expect(await screen.findByText("w1")).toBeInTheDocument();
  expect(screen.getByText(/정상/)).toBeInTheDocument();
  // 값이 있는 시리즈는 path가 실제로 그려진다
  await waitFor(() =>
    expect(container.querySelectorAll("svg path").length).toBeGreaterThan(0));
});

test("기간 버튼이 window 파라미터로 재조회한다", async () => {
  const calls: (string | null)[] = [];
  renderSection(calls);
  await screen.findByText("w1");
  expect(calls[0]).toBe("24");           // 기본 24h
  await userEvent.click(screen.getByRole("button", { name: "1h" }));
  await waitFor(() => expect(calls).toContain("1"));
});

test("노드당 한 줄: CPU 사용률·부하(load 1분)·메모리·수신·송신만, 펼침 없음(2026-10-01, CPU 칸 2026-10-16)", async () => {
  renderSection();
  await screen.findByText("w1");
  for (const title of ["CPU 사용률", "부하(load 1분)", "메모리 사용%", "수신 B/s", "송신 B/s"])
    expect(screen.getByText(title)).toBeInTheDocument();
  // CPU 사용률이 첫 칸이고, 다섯 칸이 한 격자(한 행)에 있다 -- md 이상 5열
  // (기간 선택 버튼 묶음도 group 이다 -- 이름이 노드로 시작하는 칸만)
  const groups = screen.getAllByRole("group").filter((g) => g.getAttribute("aria-label")?.startsWith("w1 "));
  expect(groups.map((g) => g.getAttribute("aria-label"))).toEqual(
    ["w1 CPU 사용률", "w1 부하(load 1분)", "w1 메모리", "w1 수신", "w1 송신"]);
  expect(groups[0].parentElement!.className).toContain("md:grid-cols-5");
  expect(groups.every((g) => g.parentElement === groups[0].parentElement)).toBe(true);
  expect(screen.queryByRole("button", { name: "w1" })).not.toBeInTheDocument();   // 노드 이름은 펼침 버튼이 아니다
  // /사용%/ 는 펼침에 있던 스토리지 사용%를 막는 그물이다 -- 칸 제목 「메모리 사용%」만 예외. CPU 칸은 「사용률」이라
  // 그물에 걸리지 않는다(의도: CPU 사용률은 펼침의 스토리지 %가 아니라 노드당 한 줄의 정식 칸이다, 2026-10-16).
  for (const gone of [/load1/, /load5/, /load15/, /사용%/, /마운트/, /도구/, /계정/]) {
    const hits = screen.queryAllByText(gone).filter((el) => el.textContent !== "메모리 사용%");
    expect(hits).toEqual([]);
  }
  expect(screen.queryByText("s1")).not.toBeInTheDocument();                       // 스토리지 사용% 없음
});

test("현재값 · 창 내 최대값을 병기한다", async () => {
  renderSection();
  await screen.findByText("w1");
  // 메모리: 마지막 점 52%, 창 최대 55% -- 현재와 최대가 구분된다
  expect(screen.getByText("52% · 최대 55%")).toBeInTheDocument();
  // 부하(load 1분): 마지막 0.6, 최대 0.7
  expect(screen.getByText("0.6 · 최대 0.7")).toBeInTheDocument();
  // CPU 사용률: 마지막 42%, 최대 60% -- 첫 점의 null 은 건너뛴다(0 으로 세지 않는다)
  const cpu = screen.getByRole("group", { name: "w1 CPU 사용률" });
  expect(within(cpu).getByText("42% · 최대 60%")).toBeInTheDocument();
  // 연속한 두 유효점(60, 42)은 선으로 이어진다(첫 점 null 은 끊김) -- 0..100 고정 스케일
  expect(within(cpu).getByRole("img", { name: "w1 CPU 사용률" }).querySelector("path")!
    .getAttribute("d")).toMatch(/^M[\d.]+,[\d.]+L[\d.]+,[\d.]+$/);
  // 네트워크는 humanBytes 표기 + /s -- 수신 2048 B/s = 2.0 KiB/s
  expect(screen.getByText("2.0 KiB/s · 최대 2.0 KiB/s")).toBeInTheDocument();
  expect(screen.getByText("20 B/s · 최대 20 B/s")).toBeInTheDocument();
});

test("상한 라벨: 메모리는 100%, 부하(load 1분) 는 리포트의 코어 수 기준선, CPU 는 I/O 대기%", async () => {
  renderSection();
  await screen.findByText("w1");
  expect(screen.getAllByText("상한 100%").length).toBeGreaterThan(0);
  expect(within(screen.getByRole("group", { name: "w1 부하(load 1분)" }))
    .getByText("코어 2 기준")).toBeInTheDocument();
  // I/O 대기는 마지막 유효값(3%)
  expect(within(screen.getByRole("group", { name: "w1 CPU 사용률" }))
    .getByText("I/O 대기 3%")).toBeInTheDocument();
});

test("부하는 사용률이 아니라는 설명이 칸 툴팁과 카드 각주에 있다(2026-10-16 사용자 보고)", async () => {
  renderSection();
  await screen.findByText("w1");
  const help = /코어 수를 넘을 수 있으며 CPU 사용률이 아닙니다/;
  expect(screen.getByText("부하(load 1분)")).toHaveAttribute("title", expect.stringMatching(help));
  expect(screen.getByText(help)).toBeInTheDocument();
});

test("구형 에이전트(cpu_used_pct 없음)는 CPU 사용률 칸이 0% 가 아니라 — 이고 I/O 캡션도 없다", async () => {
  // 서버가 키를 싣지 않던 시절의 응답 모양 그대로(키 자체 부재) + 범위 밖 오염값(120) -- 둘 다 모름.
  const old = {
    ...METRICS,
    nodes: [{
      ...METRICS.nodes[0],
      points: METRICS.nodes[0].points.map((p, i) => {
        const rest: Record<string, unknown> = { ...p };
        delete rest.cpu_used_pct;
        delete rest.cpu_iowait_pct;
        return i === 0 ? { ...rest, cpu_used_pct: 120, cpu_iowait_pct: -1 } : rest;
      }),
    }],
  };
  server.use(
    http.get("/api/admin/metrics/nodes", () => HttpResponse.json(old)),
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
  await screen.findByText("w1");
  const cpu = screen.getByRole("group", { name: "w1 CPU 사용률" });
  // 스파크라인도 값 줄도 "—" -- 0% 로 그리지 않는다
  expect(within(cpu).getAllByText("—").length).toBe(2);
  expect(within(cpu).queryByText(/0%/)).toBeNull();
  expect(within(cpu).queryByText(/I\/O 대기/)).toBeNull();
  expect(cpu.querySelector("svg")).toBeNull();
  // 다른 칸은 그대로 산다
  expect(screen.getByText("0.6 · 최대 0.7")).toBeInTheDocument();
});

test("CPU 사용률 0 은 모름이 아니라 0% 로 그린다", async () => {
  const idle = {
    ...METRICS,
    nodes: [{ ...METRICS.nodes[0],
              points: METRICS.nodes[0].points.map((p) => ({ ...p, cpu_used_pct: 0, cpu_iowait_pct: 0 })) }],
  };
  server.use(
    http.get("/api/admin/metrics/nodes", () => HttpResponse.json(idle)),
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
  await screen.findByText("w1");
  const cpu = screen.getByRole("group", { name: "w1 CPU 사용률" });
  expect(within(cpu).getByText("0% · 최대 0%")).toBeInTheDocument();
  expect(within(cpu).getByText("I/O 대기 0%")).toBeInTheDocument();
});

test("cpu_count 가 없거나 오염된 구형 리포트는 코어 라벨 없이 창 최대 폴백", async () => {
  // 스키마리스 리포트 방어: 문자열 "2" 같은 오염값을 숫자 상한으로 뭉개면
  // 거짓 기준선이 생긴다 -- null(모름)로 두고 라벨을 아예 내리지 않는다.
  server.use(
    http.get("/api/admin/metrics/nodes", () => HttpResponse.json(METRICS)),
    http.get("/api/admin/nodes", () => HttpResponse.json([
      { ...NODES[0], report: { ...NODES[0].report, os: { cpu_count: "2" } } },
    ])),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { container } = render(
    <QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
  await screen.findByText("w1");
  // 코어 기준 캡션이 없다(카드 각주의 "코어 수를 넘을 수 있으며" 설명 문장은 별개라 캡션 모양으로 좁혀 본다)
  expect(screen.queryByText(/^코어 /)).not.toBeInTheDocument();
  // 폴백에서도 부하(load 1분) 차트 자체는 그려진다(창 최대 스케일)
  await waitFor(() =>
    expect(container.querySelectorAll("svg path").length).toBeGreaterThan(0));
});

test("nodes가 비배열이어도 죽지 않는다", async () => {
  server.use(
    http.get("/api/admin/metrics/nodes",
             () => HttpResponse.json({ nodes: null })),
    http.get("/api/admin/nodes", () => HttpResponse.json(NODES)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
  expect(await screen.findByText("노드/리소스")).toBeInTheDocument();
});


// ---- 2026-11-11 전수조사 수정 ----

function renderWith(metrics: Record<string, unknown> | 500, nodes: Record<string, unknown>[] = NODES) {
  server.use(
    http.get("/api/admin/metrics/nodes", () =>
      metrics === 500 ? HttpResponse.json({ detail: "boom" }, { status: 500 }) : HttpResponse.json(metrics)),
    http.get("/api/admin/nodes", () => HttpResponse.json(nodes)),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
}

test("신선도는 폴링되는 노드 목록 값으로 그린다 -- 시계열 스냅숏이 「정상」이어도 목록이 지연이면 지연", async () => {
  // 시계열(/metrics/nodes)은 폴링하지 않아 처음 받은 값이 남는다: 정상 노드가 「정상 · 6분 전」으로 늙고, 멈춘 노드도
  // 「정상」이었다(2026-11-11). 노드 목록(5초 폴링)이 신선도의 원천이다.
  renderWith(METRICS, [{ ...NODES[0], fresh: false, reported_at: "2026-08-09T00:00:00Z" }]);
  await screen.findByText("w1");
  await waitFor(() => expect(screen.getByText(/^지연 · /)).toHaveClass("text-bad"));
  expect(screen.queryByText(/^정상 · /)).toBeNull();
});

test("나이도 노드 목록의 reported_at 이다 -- 시계열 스냅숏이 오래돼도 방금 보고했으면 「정상 · 방금」", async () => {
  const now = new Date().toISOString().replace(/\.\d+Z$/, "Z");
  renderWith({ ...METRICS, nodes: [{ ...METRICS.nodes[0], fresh: false, reported_at: "2026-08-01T00:00:00Z" }] },
             [{ ...NODES[0], fresh: true, reported_at: now }]);
  await screen.findByText("w1");
  await waitFor(() => expect(screen.getByText("정상 · 방금")).toHaveClass("text-ok"));
});

test("첫 조회 실패는 오류 한 줄이다 -- 머리줄만 남은 빈 카드가 아니다", async () => {
  renderWith(500);
  expect(await screen.findByText(/^노드 지표를 불러오지 못했습니다 — /)).toBeInTheDocument();
});

test("보고한 노드가 0대면 빈 상태 문구를 낸다", async () => {
  renderWith({ ...METRICS, nodes: [] }, []);
  expect(await screen.findByText(/^보고한 노드가 없습니다/)).toBeInTheDocument();
});

test("가로 위치는 창(start~end) 안의 시각 비율이다 -- 창 끝 2분의 점들은 오른쪽 끝에 모인다", async () => {
  // 순번 균등 배치면 몇 시간 보고 공백도 한 칸이라 사라졌다(서버가 공백에 null 표지를 끼워 선도 끊는다).
  renderWith(METRICS);
  await screen.findByText("w1");
  const load = screen.getByRole("img", { name: "w1 부하(load 1분)" }).querySelector("path")!.getAttribute("d")!;
  const xs = [...load.matchAll(/[ML]([\d.]+),/g)].map((m) => Number(m[1]));
  expect(xs).toHaveLength(3);
  expect(Math.min(...xs)).toBeGreaterThan(119);                    // 23:58~23:59:30 / 24h 창 → 120 폭의 끝
  expect(xs[2]).toBeGreaterThan(xs[1]);
  expect(screen.queryByText(/보존 기간/)).toBeNull();             // 창이 접히지 않았으면(24 = 24) 알림 없음
});

test("timeFractions: 창 경계·시각을 모르면 undefined(순번 배치로 돌아간다)", () => {
  const p = (at: string) => ({ ...METRICS.nodes[0].points[0], at });
  expect(timeFractions([p("2026-08-09T06:00:00Z"), p("2026-08-09T18:00:00Z")],
                       "2026-08-09T00:00:00Z", "2026-08-10T00:00:00Z")).toEqual([0.25, 0.75]);
  expect(timeFractions([p("2026-08-09T06:00:00Z")], "", "2026-08-10T00:00:00Z")).toBeUndefined();
  expect(timeFractions([p("broken")], "2026-08-09T00:00:00Z", "2026-08-10T00:00:00Z")).toBeUndefined();
});

test("서버가 창을 보존 기간으로 접었으면 한 줄로 알린다", async () => {
  // 기본 24h 를 골랐는데 서버가 3h 로 접었다(DMS_AGENT_REPORT_RETENTION_DAYS 를 아주 줄인 사이트의 모양).
  renderWith({ ...METRICS, window_hours: 3 });
  await screen.findByText("w1");
  expect(await screen.findByText(/최근 3시간만 보여 줍니다/)).toBeInTheDocument();
});

test("머리줄의 갱신 시각·새로고침 글자는 줄바꿈하지 않고, 묶음이 통째로 다음 줄로 내려간다(390px 「새로고/침」)", async () => {
  renderWith(METRICS);
  await screen.findByText("w1");
  const button = screen.getByRole("button", { name: "노드/리소스 새로고침" });
  expect(button.className).toContain("whitespace-nowrap");
  expect(screen.getByLabelText("마지막 갱신").className).toContain("whitespace-nowrap");
  expect(button.parentElement!.className).toContain("flex-wrap");
});

test("새로고침 버튼이 노드 메트릭(같은 기간)과 노드 목록을 다시 조회한다(2026-10-02)", async () => {
  const calls: (string | null)[] = [];
  let nodeCalls = 0;
  server.use(
    http.get("/api/admin/metrics/nodes", ({ request }) => {
      calls.push(new URL(request.url).searchParams.get("window"));
      return HttpResponse.json(METRICS);
    }),
    http.get("/api/admin/nodes", () => { nodeCalls += 1; return HttpResponse.json(NODES); }),
  );
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><NodeMetricsSection /></QueryClientProvider>);
  await screen.findByText("w1");
  expect(screen.getByLabelText("마지막 갱신")).toHaveTextContent(/^갱신 \d\d:\d\d:\d\d$/);
  const before = calls.length, nodesBefore = nodeCalls;
  await userEvent.click(screen.getByRole("button", { name: "노드/리소스 새로고침" }));
  await waitFor(() => expect(calls.length).toBe(before + 1));
  expect(calls[calls.length - 1]).toBe("24");                 // 지금 고른 기간 그대로
  await waitFor(() => expect(nodeCalls).toBeGreaterThan(nodesBefore));
});
