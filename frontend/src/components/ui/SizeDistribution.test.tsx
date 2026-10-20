import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import { SizeDistribution } from "./SizeDistribution";
import type { HistogramBucket } from "../../lib/types";

const LIMITS = [4096, 65536, 1048576, 16777216, 268435456, 1073741824, 17179869184, 274877906944, 4398046511104];
const dscan = (counts: (number | undefined)[], bytes?: number[]): HistogramBucket[] =>
  counts.map((c, i) => {
    const lo = i === 0 ? 0 : LIMITS[i - 1] + 1;
    const hi = LIMITS[i];
    return { bucket: hi === undefined ? `[${lo},INF]` : `[${lo},${hi}]`, lower_inclusive: lo,
             ...(hi === undefined ? {} : { upper_inclusive: hi }), ...(c === undefined ? {} : { count: c }),
             ...(bytes ? { bytes: bytes[i] } : {}) };
  });

function renderDist(buckets: HistogramBucket[] | undefined) {
  render(<><h3 id="h">파일 크기 분포</h3><SizeDistribution buckets={buckets} labelledBy="h" /></>);
}

test("줄마다 낭독 한 문장(라벨 개수 비중), 보이는 칸은 aria-hidden, 10줄 순서 그대로 · 0 줄도 그린다", () => {
  renderDist(dscan([22, 0, 0, 4, 0, 0, 0, 0, 0, 0]));
  const list = screen.getByRole("list", { name: "파일 크기 분포" });
  const items = within(list).getAllByRole("listitem");
  expect(items).toHaveLength(10);
  expect(screen.getByText("4 KiB 이하 22개 85%")).toHaveClass("sr-only");
  expect(screen.getByText("4–64 KiB 0개 0%")).toBeInTheDocument();
  expect(screen.getByText("1–16 MiB 4개 15%")).toBeInTheDocument();
  expect(screen.getByText("4 TiB 초과 0개 0%")).toBeInTheDocument();
  // 보이는 라벨은 장식(aria-hidden) -- 정확한 경계는 줄의 title
  expect(within(items[1]).getByText("4–64 KiB", { selector: "[aria-hidden]" })).toBeInTheDocument();
  expect(items[1]).toHaveAttribute("title", "4,097 B ~ 65,536 B");
  expect(items[0]).toHaveAttribute("title", "0 B ~ 4,096 B(빈 파일 포함)");
});

test("막대 = 최다 구간 대비(최대 100%, 0 은 0, 극소값은 최소 폭)", () => {
  renderDist(dscan([1000, 1, 0, 500, 0, 0, 0, 0, 0, 0]));
  const widths = screen.getAllByRole("listitem").map((li) =>
    (li.querySelector("span.bg-accent") as HTMLElement).style.width);
  expect(widths.slice(0, 4)).toEqual(["100%", "2.5%", "0%", "50%"]);
});

test("부재 → 「기록 없음」, 합 0 → 「파일이 없습니다(0개)」 -- 0 막대 10줄로 그리지 않는다", () => {
  const { unmount } = render(<SizeDistribution buckets={undefined} />);
  expect(screen.getByText("크기 분포 기록이 없습니다(구형 리포트)")).toBeInTheDocument();
  expect(screen.queryByRole("list")).toBeNull();
  unmount();
  render(<SizeDistribution buckets={dscan(Array(10).fill(0))} />);
  expect(screen.getByText("파일이 없습니다(0개)")).toBeInTheDocument();
  expect(screen.queryByRole("list")).toBeNull();
});

test("일부 구간 개수 모름 → 그 줄 —, 모든 비중 — + 안내 한 줄", () => {
  renderDist(dscan([5, undefined, 0, 0, 0, 0, 0, 0, 0, 0]));
  expect(screen.getByText("4 KiB 이하 5개 —")).toBeInTheDocument();
  expect(screen.getByText("4–64 KiB — —")).toBeInTheDocument();
  expect(screen.getByText("일부 구간의 개수를 몰라 비중은 표시하지 않습니다")).toBeInTheDocument();
});

test("모든 구간에 용량이 실리면 용량 열이 붙는다(낭독 문장에도)", () => {
  renderDist(dscan([2, 0, 0, 0, 0, 0, 0, 0, 0, 0], [3072, 0, 0, 0, 0, 0, 0, 0, 0, 0]));
  expect(screen.getByText("4 KiB 이하 2개 3.0 KiB 100%")).toBeInTheDocument();
});
