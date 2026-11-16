import { render, screen, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { HistogramBucket } from "../../lib/types";
import { HIST_TEMP_CAPTIONS, SizeHistogram, TEMP_AXES, TempHistogram, histBars, shortBytes, sizeBucketLabel,
         sizeHistogramTotal, tempAxes } from "./ScanHistograms";

// 공용 모듈의 규칙(누락 → 모름, 라벨 대체, 색 끝점, 축 목록, 총계의 null/0)을 고정한다. 배치 화면의 DOM 은 BatchDetail.test 의
// 차트 테스트가 그물이고, 여기 「배치 화면과 같은 DOM」 두 테스트가 그 계약(data-tip·색·누적 % 라벨)을 공용 모듈 쪽에서 미리 본다 --
// 배치 항목 펼침이 이 모듈로 갈아탈 때 화면이 바뀌지 않게.

const temp = (vals: (number | undefined)[], over: Partial<HistogramBucket> = {}): HistogramBucket[] =>
  vals.map((b, i) => ({ bucket: `[${i}d,${i + 1}d]`, min_age_days: i, max_age_days: i + 1,
                        ...(b === undefined ? {} : { bytes: b }), ...over }));

test("histBars: 값이 하나라도 빠지거나 숫자가 아니면(음수·비유한 포함) null(모름) -- 빈 배열·부재는 [](0건)", () => {
  expect(histBars(temp([1, 2]), "bytes", (b) => b.bucket!)).toEqual([
    { label: "[0d,1d]", value: 1 }, { label: "[1d,2d]", value: 2 }]);
  expect(histBars(temp([1, undefined]), "bytes", () => "")).toBeNull();
  for (const bad of [-1, Number.NaN, Number.POSITIVE_INFINITY, "3" as unknown as number]) {
    expect(histBars([{ bucket: "a", bytes: 1 }, { bucket: "b", bytes: bad }], "bytes", () => "")).toBeNull();
  }
  expect(histBars([], "count", () => "")).toEqual([]);
  expect(histBars(undefined, "count", () => "")).toEqual([]);
  expect(histBars(null, "count", () => "")).toEqual([]);
  expect(histBars([{ bucket: "a", bytes: 0 }], "bytes", () => "z")).toEqual([{ label: "z", value: 0 }]);   // 0 은 정상값
  // 라벨 함수는 순번을 받는다(「구간 N」 대체용)
  expect(histBars([{ count: 1 }, { count: 2 }], "count", (_b, i) => `#${i}`)?.map((d) => d.label)).toEqual(["#0", "#1"]);
});

test("온도 히스토그램: 첫 막대 빨강·끝 막대 파랑(막대 수와 무관), 값 툴팁·누적 캡션은 humanBytes", () => {
  render(<TempHistogram buckets={temp([3072, 1024, 0])} axis="mtime" />);
  const chart = screen.getByRole("img", { name: "데이터 온도(mtime) 히스토그램" });
  const fills = chart.getElementsByClassName("rounded-t");
  expect(fills).toHaveLength(3);
  expect(fills[0]).toHaveStyle({ backgroundColor: "#dc2626" });
  expect(fills[2]).toHaveStyle({ backgroundColor: "#6366f1" });
  expect(chart.querySelector('[data-tip="[0d,1d]: 3.0 KiB (75%)"]')).not.toBeNull();
  expect(screen.getByText(HIST_TEMP_CAPTIONS.mtime)).toBeInTheDocument();
  expect(screen.getByText("선 = hot쪽부터의 누적 용량 비중 · 총 4.0 KiB · 막대에 마우스를 올리면 구간별 용량"))
    .toBeInTheDocument();
});

test("온도 히스토그램: 라벨 없는 버킷은 나이 필드로 라벨을 짓고(버리지 않는다) 누적은 전체 기준", () => {
  // 서버는 경로처럼 생긴 라벨을 지우고 수치만 남긴다(routes_scan_paths._buckets) -- 옛 toBars 는 그 막대를 버려 누적 % 를
  // 부분합 기준으로 부풀렸다.
  const b: HistogramBucket[] = [{ bucket: "[0d,1d]", min_age_days: 0, max_age_days: 1, bytes: 1024 },
                                { min_age_days: 181, max_age_days: 365, bytes: 3072 }];
  render(<TempHistogram buckets={b} axis="atime" />);
  const chart = screen.getByRole("img", { name: "데이터 온도(atime) 히스토그램" });
  expect(within(chart).getByText("181일~1년")).toBeInTheDocument();
  expect(chart.querySelector('[data-tip="[0d,1d]: 1.0 KiB (25%)"]')).not.toBeNull();
  expect(within(chart).getAllByText(/%$/).map((n) => n.textContent)).toEqual(["25%", "100%"]);
});

test("온도 히스토그램: 값이 빠진 버킷이 있으면 차트 대신 「모름」 한 줄(빈 히스토그램 문구와 다르다), 캡션도 없다", () => {
  const { unmount } = render(<TempHistogram buckets={temp([10, undefined, 5])} axis="atime" />);
  expect(screen.getByText("이 축의 온도 분포를 알 수 없습니다(리포트 값 누락)")).toBeInTheDocument();
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.queryByText(/선 = /)).toBeNull();
  expect(screen.queryByText(/왼쪽\(빨강\)/)).toBeNull();
  unmount();
  // 빈 배열 = 0건(정상값) -- 호출측 문구, 축 캡션은 그대로
  render(<TempHistogram buckets={[]} axis="ctime" emptyText="이 축의 온도 분포 없음" />);
  expect(screen.getByText("이 축의 온도 분포 없음")).toBeInTheDocument();
  expect(screen.getByText(HIST_TEMP_CAPTIONS.ctime)).toBeInTheDocument();
  expect(screen.queryByText(/선 = /)).toBeNull();
});

test("온도 히스토그램: caption=\"none\" 이면 축 캡션은 빼고(호출측 설명에 둔다) 누적 캡션은 남긴다", () => {
  render(<TempHistogram buckets={temp([1, 1])} axis="atime" caption="none" />);
  expect(screen.queryByText(/왼쪽\(빨강\)/)).toBeNull();
  expect(screen.getByText(/^선 = hot쪽부터의 누적 용량 비중 · 총 2 B/)).toBeInTheDocument();
});

test("온도 히스토그램: 총합 0 이면 막대(0 은 정상값)는 그리되 누적선·누적 캡션은 없다(0/0 비중은 정의 불가)", () => {
  render(<TempHistogram buckets={temp([0, 0])} axis="mtime" />);
  const chart = screen.getByRole("img", { name: "데이터 온도(mtime) 히스토그램" });
  expect(chart.getElementsByClassName("rounded-t")).toHaveLength(2);
  expect(chart.querySelector("polyline")).toBeNull();
  expect(chart.querySelector('[data-tip="[0d,1d]: 0 B"]')).not.toBeNull();   // 비중 없음(전체 0)
  expect(screen.queryByText(/선 = /)).toBeNull();
  expect(screen.getByText(HIST_TEMP_CAPTIONS.mtime)).toBeInTheDocument();
});

const SIZE: HistogramBucket[] = [
  { bucket: "[0,4096]", lower_inclusive: 0, upper_inclusive: 4096, count: 3 },
  { bucket: "[4097,65536]", lower_inclusive: 4097, upper_inclusive: 65536, count: 1 },
  { bucket: "[4398046511105,INF]", lower_inclusive: 4398046511105, count: 0 },
];

test("크기 히스토그램: 단색 accent(온도색 없음)·축약 라벨·누적 개수 · 총계는 제목용 도우미", () => {
  render(<SizeHistogram buckets={SIZE} />);
  const chart = screen.getByRole("img", { name: "파일 크기 분포" });
  expect(chart.getElementsByClassName("bg-accent")).toHaveLength(3);
  expect(chart.querySelector('[style*="background-color"]')).toBeNull();
  for (const l of ["0~4K", "4K~64K", "4T~"]) expect(within(chart).getByText(l)).toBeInTheDocument();
  expect(within(chart).getAllByText("100%")[0].getAttribute("data-tip")).toBe("누적 4개 (100%)");
  expect(screen.getByText(/^가로축 = 파일 크기 구간/)).toBeInTheDocument();
  expect(screen.getByText(/^선 = 작은 파일부터의 누적 개수 비중/)).toBeInTheDocument();
  expect(sizeHistogramTotal(SIZE)).toBe(4);
});

test("크기 히스토그램: 개수가 빠진 구간 → 「모름」 · 총계 null(부분합을 총계라 하지 않는다), 부재·빈 배열 총계도 null, 0 은 0", () => {
  const broken = [SIZE[0], { bucket: "[4097,65536]", lower_inclusive: 4097, upper_inclusive: 65536 }];
  render(<SizeHistogram buckets={broken} />);
  expect(screen.getByText("크기 분포를 알 수 없습니다(리포트 값 누락)")).toBeInTheDocument();
  expect(screen.queryByRole("img")).toBeNull();
  expect(sizeHistogramTotal(broken)).toBeNull();
  expect(sizeHistogramTotal(undefined)).toBeNull();
  expect(sizeHistogramTotal([])).toBeNull();
  expect(sizeHistogramTotal(SIZE.map((b) => ({ ...b, count: 0 })))).toBe(0);
});

test("크기 히스토그램(스토리지 합계 쓰임): 분포 부재·빈 배열은 호출측 문구 한 줄(0 막대·캡션 없음), 전부 0 은 0 막대", () => {
  // 합계는 경로마다 최신 scan 의 분포를 더한 버킷이다(StorageTotals file_size_histogram.buckets -- 모양이 같다). 읽는 중·아는 대상
  // 없음은 화면이 다르게 말하므로 emptyText 로 받는다. 부재를 0 막대 10개로 그리면 「파일이 없다」는 거짓이다.
  for (const buckets of [undefined, null, []]) {
    const { unmount } = render(<SizeHistogram buckets={buckets} emptyText="크기 분포를 아는 대상이 없습니다" />);
    expect(screen.getByText("크기 분포를 아는 대상이 없습니다")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByText(/^가로축 = /)).toBeNull();
    expect(screen.queryByText(/^선 = /)).toBeNull();
    unmount();
  }
  // 전부 0(빈 트리 -- 정상값): 막대 자리는 남기고(0 막대), 누적선·누적 캡션은 없다(0/0)
  render(<SizeHistogram buckets={SIZE.map((b) => ({ ...b, count: 0 }))} />);
  const chart = screen.getByRole("img", { name: "파일 크기 분포" });
  expect(chart.getElementsByClassName("bg-accent")).toHaveLength(3);
  expect(chart.querySelector("polyline")).toBeNull();
  expect(screen.getByText(/^가로축 = /)).toBeInTheDocument();
  expect(screen.queryByText(/^선 = /)).toBeNull();
});

// --- 배치 항목 펼침과 같은 DOM(BatchDetail.test 의 라이브 실측 fixture 그대로 -- d63 dscan: 크기 10버킷, 마지막은 상한 없음) ---

const LIVE_SIZE: [number, number | null, number][] = [
  [0, 4096, 3], [4097, 65536, 1], [65537, 1048576, 1],
  [1048577, 16777216, 1], [16777217, 268435456, 1],
  [268435457, 1073741824, 0], [1073741825, 17179869184, 0],
  [17179869185, 274877906944, 0], [274877906945, 4398046511104, 0],
  [4398046511105, null, 0]];
const LIVE_LABELS = ["0~4K", "4K~64K", "64K~1M", "1M~16M", "16M~256M",
                     "256M~1G", "1G~16G", "16G~256G", "256G~4T", "4T~"];
const liveSize = (): HistogramBucket[] => LIVE_SIZE.map(([lo, hi, count]) => ({
  bucket: hi === null ? `[${lo},)` : `[${lo},${hi}]`, lower_inclusive: lo, count,
  ...(hi === null ? {} : { upper_inclusive: hi }) }));

test("배치 화면과 같은 DOM -- 크기 10버킷: 축약 라벨 전부(솎지 않음·「.0」 없음), 개수 툴팁·누적 %·누적 개수, 총 7개", () => {
  render(<SizeHistogram buckets={liveSize()} />);
  const el = screen.getByRole("img", { name: "파일 크기 분포" });
  const chart = within(el);
  for (const label of LIVE_LABELS) expect(chart.getByText(label)).toBeInTheDocument();
  expect(screen.queryByText("[0,4096]")).toBeNull();
  expect(chart.queryByText(/\.0[KMGT]/)).toBeNull();
  expect(el.getElementsByClassName("bg-accent")).toHaveLength(10);
  expect(chart.queryByText("3")).toBeNull();                                   // 막대 위 개수 글자 없음 -- 툴팁
  expect(el.querySelector('[data-tip="0~4K: 3 (43%)"]')).not.toBeNull();
  expect(el.querySelector('[data-tip="4K~64K: 1 (14%)"]')).not.toBeNull();
  expect(el.querySelector('[data-tip="4T~: 0 (0%)"]')).not.toBeNull();
  expect(el.querySelector("polyline")).not.toBeNull();
  expect(chart.getAllByText(/%$/).map((n) => n.textContent))
    .toEqual(["43%", "57%", "71%", "86%", "100%", "100%", "100%", "100%", "100%", "100%"]);
  expect(chart.getAllByText("100%")[0].getAttribute("data-tip")).toBe("누적 7개 (100%)");
  expect(screen.getByText(/K=KiB·M=MiB·G=GiB/)).toBeInTheDocument();
  expect(screen.getByText("선 = 작은 파일부터의 누적 개수 비중 · 막대에 마우스를 올리면 구간별 개수")).toBeInTheDocument();
  expect(sizeHistogramTotal(liveSize())).toBe(7);
});

test("배치 화면과 같은 DOM -- 온도 2버킷(atime): 값 툴팁 humanBytes+비중, 빨강→파랑 끝점, 누적 100%·100%, 총 2.0 KiB", () => {
  render(<TempHistogram buckets={[{ bucket: "[0d,1d]", bytes: 2048 }, { bucket: "[1d,7d]", bytes: 0 }]} axis="atime" />);
  const chart = screen.getByRole("img", { name: "데이터 온도(atime) 히스토그램" });
  expect(within(chart).queryByText("2.0 KiB")).toBeNull();
  expect(chart.querySelector('[data-tip="[0d,1d]: 2.0 KiB (100%)"]')).not.toBeNull();
  expect(chart.querySelector('[data-tip="[1d,7d]: 0 B (0%)"]')).not.toBeNull();
  const fills = chart.getElementsByClassName("rounded-t");
  expect(fills[0]).toHaveStyle({ backgroundColor: "#dc2626" });
  expect(fills[1]).toHaveStyle({ backgroundColor: "#6366f1" });
  const labels = within(chart).getAllByText("100%");
  expect(labels).toHaveLength(2);
  expect(labels[0].getAttribute("data-tip")).toBe("누적 2.0 KiB (100%)");
  const caption = screen.getByText(/왼쪽\(빨강\)=hot·최근 접근, 오른쪽\(파랑\)=cold/);
  expect(caption).toHaveTextContent("읽기가 atime 에 반영되는지는 마운트·스토리지에 따라 달라 근사치입니다");
  expect(screen.getByText("선 = hot쪽부터의 누적 용량 비중 · 총 2.0 KiB · 막대에 마우스를 올리면 구간별 용량"))
    .toBeInTheDocument();
});

test("sizeBucketLabel: 숫자 필드 > 라벨 파싱(INF·빈 상한 = 열린 구간) > 원 라벨 > 「구간 N」", () => {
  expect(sizeBucketLabel({ lower_inclusive: 4097, upper_inclusive: 65536 })).toBe("4K~64K");
  expect(sizeBucketLabel({ bucket: "[1048577,16777216]" })).toBe("1M~16M");
  expect(sizeBucketLabel({ bucket: "[1073741825,)" })).toBe("1G~");
  expect(sizeBucketLabel({ bucket: "[4398046511105,INF]" })).toBe("4T~");
  expect(sizeBucketLabel({ bucket: "[1536,2048]" })).toBe("1.5K~2K");
  expect(sizeBucketLabel({ bucket: "small" })).toBe("small");
  expect(sizeBucketLabel({}, 2)).toBe("구간 3");
  expect(sizeBucketLabel({ bucket: "" }, 0)).toBe("구간 1");
  // 숫자 필드도 신뢰 경계: 음수는 없는 것(라벨로 물러선다), 뒤집힌 경계는 경계 모름(「64K~4K」를 만들지 않는다)
  expect(sizeBucketLabel({ bucket: "[0,4096]", lower_inclusive: -5, upper_inclusive: 4096 })).toBe("0~4K");
  expect(sizeBucketLabel({ bucket: "x", lower_inclusive: -5 })).toBe("x");
  expect(sizeBucketLabel({ lower_inclusive: 65536, upper_inclusive: 4096 }, 0)).toBe("구간 1");
  expect(sizeBucketLabel({ bucket: "[65536,4096]" }, 4)).toBe("[65536,4096]");
});

test("shortBytes: 1024 단위 초단축, 「X.0」은 정수로 접고 진짜 중간값은 한 자리", () => {
  expect(shortBytes(0)).toBe("0");
  expect(shortBytes(1023)).toBe("1023");
  expect(shortBytes(1024)).toBe("1K");
  expect(shortBytes(4097)).toBe("4K");
  expect(shortBytes(1536)).toBe("1.5K");
  expect(shortBytes(1024 ** 3 + 1)).toBe("1G");
  expect(shortBytes(1024 ** 4 * 2.5)).toBe("2.5T");
});

test("tempAxes: 기본은 키가 있으면(빈 배열 포함), nonEmpty 면 버킷이 있는 축만 -- 순서는 atime·mtime·ctime", () => {
  const h = { ctime: [], mtime: temp([1]), atime: temp([2]), weird: temp([3]) };
  expect(tempAxes(h)).toEqual(["atime", "mtime", "ctime"]);
  expect(tempAxes(h, { nonEmpty: true })).toEqual(["atime", "mtime"]);
  expect(tempAxes({})).toEqual([]);
  expect(tempAxes(undefined)).toEqual([]);
  expect(tempAxes(null)).toEqual([]);
  expect([...TEMP_AXES]).toEqual(["atime", "mtime", "ctime"]);
});
