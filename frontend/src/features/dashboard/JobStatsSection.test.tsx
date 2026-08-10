import { render, screen } from "@testing-library/react";
import { QueryClientProvider, QueryClient } from "@tanstack/react-query";
import { setupServer } from "msw/node";
import { http, HttpResponse } from "msw";
import { beforeAll, afterAll, afterEach, test, expect } from "vitest";
import { JobStatsSection } from "./JobStatsSection";

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
  expect(chart.querySelectorAll("rect")).toHaveLength(2);
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

test("제출 대기 분포와 집계/제외 건수를 보여준다", async () => {
  renderSection();
  const chart = await screen.findByRole("img", { name: "제출 대기 분포" });
  expect(chart.querySelectorAll("rect")).toHaveLength(6);
  // 제외 건수를 숨기지 않는다(설계 §3) + 수행시간과의 포함 관계 명시(설계 §2.4)
  expect(screen.getByText(/집계 3건 · 제외\(기록 없음\) 1건/)).toBeInTheDocument();
  expect(screen.getByText(/수행시간 분포는 이 대기를 포함/)).toBeInTheDocument();
});

test("스케줄 대기(Volcano) 분포가 제출 대기와 구분돼 나온다", async () => {
  renderSection();
  const chart = await screen.findByRole("img", { name: "스케줄 대기(Volcano) 분포" });
  expect(chart.querySelectorAll("rect")).toHaveLength(6);
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
