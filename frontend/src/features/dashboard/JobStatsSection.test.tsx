import { render, screen, waitFor, within } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { JobStatsSection, successRate, rateTone, humanSeconds } from "./JobStatsSection";
import type { StateCount } from "../../lib/types";

const server = setupServer();
beforeAll(() => server.listen());
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const STATS = {
  window_hours: 24, bucket: "hour",
  by_state: [
    { state: "Succeeded", count: 20 }, { state: "Failed", count: 8 },
    { state: "TimedOut", count: 2 }, { state: "Rejected", count: 9 },
    { state: "Cancelled", count: 3 }, { state: "PreviewExpired", count: 1 },
    { state: "Pending", count: 2 },
  ],
  by_tool: [{ tool: "dscan", count: 23, succeeded: 20, failed: 3 }],
  by_storage: [{ storage: "cephfs-a", count: 30, succeeded: 18, failed: 12 }],
  by_requester: [{ requester_id: "alice", count: 30, succeeded: 20, failed: 10 }],
  failure_reasons: [{ reason_code: "execution_failed", count: 2 }],
  throughput: [{ bucket: "2026-08-09T01", count: 2 },
               { bucket: "2026-08-09T02", count: 1 }],
  duration_histogram: [
    { bucket: "<1m", count: 1 }, { bucket: "1-10m", count: 0 },
    { bucket: "10-60m", count: 1 }, { bucket: "1-6h", count: 0 },
    { bucket: "6-24h", count: 0 }, { bucket: ">24h", count: 0 }],
  submit_wait_histogram: [
    { bucket: "<10s", count: 2 }, { bucket: "10-30s", count: 1 },
    { bucket: "30-60s", count: 0 }, { bucket: "1-5m", count: 0 },
    { bucket: "5-30m", count: 0 }, { bucket: ">30m", count: 0 }],
  submit_wait_counted: 3, submit_wait_excluded: 1,
  sched_wait_histogram: [
    { bucket: "<10s", count: 1 }, { bucket: "10-30s", count: 1 },
    { bucket: "30-60s", count: 0 }, { bucket: "1-5m", count: 0 },
    { bucket: "5-30m", count: 0 }, { bucket: ">30m", count: 0 }],
  sched_wait_counted: 2, sched_wait_excluded: 4,
  exec_runtime_histogram: [
    { bucket: "<1m", count: 0 }, { bucket: "1-10m", count: 0 },
    { bucket: "10-60m", count: 1 }, { bucket: "1-6h", count: 0 },
    { bucket: "6-24h", count: 0 }, { bucket: ">24h", count: 0 }],
  exec_runtime_counted: 1, exec_runtime_excluded: 2,
  duration_summary: { mean_seconds: 800, p50_seconds: 42, p95_seconds: 1600 },
  exec_runtime_summary: { mean_seconds: 940, p50_seconds: 940, p95_seconds: 940 },
  files_total: null, bytes_total: null,
};

function renderSection(stats: unknown = STATS) {
  server.use(http.get("/api/admin/metrics/jobs", () => HttpResponse.json(stats ?? STATS)));
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}><JobStatsSection /></QueryClientProvider>);
}

test("성공률·처리량·분해 표·실패 사유를 그린다", async () => {
  renderSection();
  // 종단 43건 중 성공 20 = 47%
  expect(await screen.findByText(/47%/)).toBeInTheDocument();
  const chart = screen.getByRole("img", { name: "처리량" });
  expect(chart.querySelectorAll("[title]")).toHaveLength(2);
  expect(screen.getByText("dscan")).toBeInTheDocument();
  expect(screen.getByText("cephfs-a")).toBeInTheDocument();
  expect(screen.getByText("alice")).toBeInTheDocument();
  // 사유 코드는 reasonText로 한글화된다(설계 §4.3)
  expect(screen.getByText("실행에 실패했습니다")).toBeInTheDocument();
});

test("files/bytes가 NULL이면 — 로 우아하게 생략한다", async () => {
  renderSection();
  const row = await screen.findByText("처리 항목/바이트");
  expect(row.parentElement).toHaveTextContent("— / —");
});

test("응답이 비배열이어도 죽지 않는다", async () => {
  renderSection({ by_state: null });
  expect(await screen.findByText("잡 통계")).toBeInTheDocument();
});

test("by_state 가 비배열 truthy(\"oops\")여도 죽지 않는다 -- asArray 의 구분 사례", async () => {
  renderSection({ by_state: "oops" });
  // 헤더 "잡 통계"는 로딩 첫 렌더에도 그려져 즉시 단언은 데이터 착지 전에
  // 초록으로 끝난다(뮤테이션 실측: `?? []` 구현도 통과) -- 로딩 문구 소거를
  // 기다려 관찰 창을 착지 뒤로 민다. 크래시하면 트리째 언마운트라 헤더도 사라진다.
  await waitFor(() => expect(screen.queryByText("불러오는 중…")).toBeNull());
  expect(screen.getByText("잡 통계")).toBeInTheDocument();
});

test("성공률은 큰 % 숫자와 성공/비성공 스택 바로 그린다", async () => {
  renderSection();
  const pct = await screen.findByText("47%");
  // 47% 는 절반 미만 -- bad 톤(임계: ≥80 ok, ≥50 busy, 미만 bad)
  expect(pct.className).toContain("text-bad");
  const bar = screen.getByRole("img", { name: "성공률" });
  const segs = bar.querySelectorAll("[title]");
  expect(segs).toHaveLength(2);
  // 세그먼트 폭 = 실제 비율(20/43 = 46.51%) -- 반올림된 47% 표기와 별개로
  // 바 기하는 원비율을 유지한다
  expect((segs[0] as HTMLElement).style.width).toBe("46.51%");
  expect(segs[0].getAttribute("title")).toBe("성공 20");
  expect(segs[1].getAttribute("title")).toBe("비성공 23");
  expect(screen.getByText(/성공 20 \/ 종단 43/)).toBeInTheDocument();
});

test("종단 잡이 없으면 0%/100% 로 뭉개지 않고 명시한다", async () => {
  renderSection({ ...STATS, by_state: [{ state: "Pending", count: 2 }] });
  expect(await screen.findByText(/종단 잡 없음/)).toBeInTheDocument();
});

test("successRate 문자열 계약 유지(기존 소비자 호환)", () => {
  const byState = [
    { state: "Succeeded", count: 1 }, { state: "Failed", count: 1 },
  ] as StateCount[];
  expect(successRate(byState)).toBe("50% (1/2)");
  expect(successRate([])).toBe("—");
});

test("rateTone 임계: 80 이상 ok, 50 이상 busy, 미만 bad", () => {
  expect(rateTone(80)).toBe("text-ok");
  expect(rateTone(79)).toBe("text-busy");
  expect(rateTone(50)).toBe("text-busy");
  expect(rateTone(49)).toBe("text-bad");
});

test("제출 대기 분포와 집계/제외 건수를 보여준다", async () => {
  renderSection();
  const chart = await screen.findByRole("img", { name: "제출 대기 분포" });
  expect(chart.querySelectorAll("[title]")).toHaveLength(6);
  // 제외 건수를 숨기지 않는다(설계 §3) + 전체 수명과의 포함 관계 명시(설계 §2.4)
  expect(screen.getByText(/집계 3건 · 제외\(기록 없음\) 1건/)).toBeInTheDocument();
  expect(screen.getByText(/전체 수명 분포는 이 대기를 포함/)).toBeInTheDocument();
});

test("「수행시간」이 「전체 수명」으로 개명되고 실행시간 분포가 나란히 나온다", async () => {
  renderSection();
  // 라벨 정직화(슬라이스 31): created_at→updated_at 는 실행이 아니라 수명이다.
  // 옛 라벨 「수행시간」이 화면 어디에도 남으면 안 된다 -- 두 이름이 공존하면
  // 어느 쪽이 "진짜 실행"인지 화면이 거짓말한다.
  const life = await screen.findByRole("img", { name: "전체 수명 분포" });
  expect(life.querySelectorAll("[title]")).toHaveLength(6);
  expect(screen.queryByText(/수행시간/)).toBeNull();
  // 포함 관계 캡션: 전체 수명 ⊇ (제출·확인·스케줄 대기 + 실행)
  expect(screen.getByText(/제출·확인\(사람\)·스케줄 대기를 모두 포함/)).toBeInTheDocument();
  expect(screen.getByText(/실행시간 분포가 순수 실행 구간/)).toBeInTheDocument();
  // 실행시간 분포(파생 계산): 집계/제외 캡션 + 근사 오차 명시 -- sched_wait
  // 캡션 관례 그대로(제외 건수가 보여야 앵커 없는 과거 잡의 공백이 숨지 않는다).
  const chart = screen.getByRole("img", { name: "실행시간 분포" });
  expect(chart.querySelectorAll("[title]")).toHaveLength(6);
  expect(screen.getByText(
    /집계 1건 · 제외\(앵커 없음·한 틱 완료·실행 미도달\) 2건/)).toBeInTheDocument();
  expect(screen.getByText(/첫 RUNNING 관측→종단의 근사/)).toBeInTheDocument();
});

test("숫자 요약: 전체 수명·실행시간의 평균/중앙값/p95 를 사람이 읽는 단위로", async () => {
  renderSection();
  // 헤딩 "숫자 요약"은 로딩 첫 렌더에도 그려진다(그리드는 d 없이 렌더) --
  // 즉시 findByText 로 잡으면 데이터 착지 전의 "—" 를 본다. 로딩 문구 소거를
  // 기다려 관찰 창을 착지 뒤로 민다(by_state 비배열 테스트의 선례).
  await waitFor(() => expect(screen.queryByText("불러오는 중…")).toBeNull());
  const block = screen.getByText("숫자 요약").closest("div")!;
  // 행 라벨은 분포 제목("… 분포")과 다른 정확 일치 텍스트 -- 두 구간이 구분된다.
  expect(within(block).getByText("전체 수명")).toBeInTheDocument();
  expect(within(block).getByText("실행시간")).toBeInTheDocument();
  // duration_summary: 800s→13.3m, 42s, 1600s→26.7m
  expect(within(block).getByText("13.3m")).toBeInTheDocument();
  expect(within(block).getByText("42s")).toBeInTheDocument();
  expect(within(block).getByText("26.7m")).toBeInTheDocument();
  // exec_runtime_summary: 표본 1개라 평균=중앙값=p95 = 940s→15.7m
  expect(within(block).getAllByText("15.7m")).toHaveLength(3);
});

test("요약 표본이 없으면(null) 0 으로 뭉개지 않고 — 로 표기한다", async () => {
  renderSection({ ...STATS, duration_summary: null, exec_runtime_summary: null });
  // 로딩 중 렌더도 "—" 라 즉시 단언은 헛초록이 된다 -- 착지 뒤를 관찰한다.
  await waitFor(() => expect(screen.queryByText("불러오는 중…")).toBeNull());
  const block = screen.getByText("숫자 요약").closest("div")!;
  // 2행 × 3열(평균/중앙값/p95) 전부 "—" -- null(표본 없음) ≠ 0(즉시 종료).
  expect(within(block).getAllByText("—")).toHaveLength(6);
});

test("humanSeconds: 초→사람 단위, null 은 —(0 은 정상값 0s)", () => {
  expect(humanSeconds(null)).toBe("—");
  expect(humanSeconds(0)).toBe("0s");
  expect(humanSeconds(42)).toBe("42s");
  expect(humanSeconds(90)).toBe("1.5m");
  expect(humanSeconds(3600)).toBe("1h");      // 후행 .0 은 소음 -- 떨군다
  expect(humanSeconds(4320)).toBe("1.2h");
  expect(humanSeconds(172800)).toBe("2d");
});

test("스케줄 대기(Volcano) 분포가 제출 대기와 구분돼 나온다", async () => {
  renderSection();
  const chart = await screen.findByRole("img", { name: "스케줄 대기(Volcano) 분포" });
  expect(chart.querySelectorAll("[title]")).toHaveLength(6);
  // 두 대기의 라벨이 한 화면에서 구분된다 -- getByText 는 유일 매치를 강제하므로
  // 라벨이 같은 문자열로 뭉치면 여기서 터진다(설계 §3: 「제출 대기」 옆에
  // 나란히, 서로 다른 이름으로 -- 슬라이스 17 이 queue_wait 라벨을 정정한 교훈).
  expect(screen.getByText("제출 대기 분포")).toBeInTheDocument();
  expect(screen.getByText("스케줄 대기(Volcano) 분포")).toBeInTheDocument();
  // 집계/제외 캡션(설계 §2.5: 백필이 없으므로 도입 직후 "제외 N건"이 정상 표시
  // -- 숨으면 "데이터 없음"처럼 보인다) + 근사 오차 명시(설계 §2.2).
  expect(screen.getByText(/집계 2건 · 제외\(기록 없음\) 4건/)).toBeInTheDocument();
  expect(screen.getByText(/Volcano 큐 대기의 근사/)).toBeInTheDocument();
});
