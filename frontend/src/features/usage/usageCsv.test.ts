import { expect, test } from "vitest";
import { buildUsageCsv, usageCsvFilename } from "./usageCsv";
import type { Storage, UsageExportRow, UsagePoint } from "../../lib/types";

const NOW = Date.parse("2026-10-02T00:00:00Z");
const ST: Storage[] = [{ storage_name: "s1", mount_path: "/mnt/s1", managed_root: "/mnt/s1/managed",
                         backend_type: "gpfs", enabled: 1, status: "Ready", status_detail: null }];

const pt = (over: Partial<UsagePoint>): UsagePoint => ({
  job_id: "j", request_id: "r", finished_at: "2026-09-30T00:00:00Z", generated_at_epoch: 1790000000,
  total_bytes: 200, summary: { total_files: 4, total_directories: 2, custom_metric: 9 },
  time_histograms: {
    atime: [{ bucket: "[0d,1d]", min_age_days: 0, max_age_days: 1, bytes: 50 },
            { bucket: "[181d,365d]", min_age_days: 181, max_age_days: 365, bytes: 150 }],
    mtime: [{ bucket: "[0d,1d]", min_age_days: 0, max_age_days: 1, bytes: 200 }],
  },
  file_size_histogram: [{ bucket: "[0,4096]", count: 3 }, { bucket: "[4097,65536]", count: 1 }],
  broken_paths_total: 0, broken_paths_limit: 100, requester: "alice", report_readable: true, ...over });

const row = (over: Partial<UsageExportRow>): UsageExportRow => ({
  storage_name: "s1", target: "team/a", scan_count: 3, first_scan_at: "2026-08-01T00:00:00Z",
  last_scan_at: "2026-09-30T00:00:00Z", latest: pt({}), previous: pt({ total_bytes: 100,
  finished_at: "2026-09-20T00:00:00Z" }), ...over });

test("buildUsageCsv: 항목당 한 줄 -- 스토리지 정보·절대경로·최신/직전 증감·hot 비율·요약·히스토그램", () => {
  const { columns, records } = buildUsageCsv([row({})], ST, NOW);
  const r = records[0];
  expect(r.backend_type).toBe("gpfs");
  expect(r.abs_path).toBe("/mnt/s1/managed/team/a");
  expect(r.days_since_last_scan).toBe(2);
  expect(r.first_scan_at_kst).toBe("2026-08-01 09:00:00 KST");
  expect(r.total_bytes).toBe(200);
  expect(r.total_size).toBe("200 B");
  expect(r.previous_total_bytes).toBe(100);
  expect(r.delta_bytes_vs_previous).toBe(100);
  expect(r.delta_pct_vs_previous).toBe(100);
  expect(r.hot_ratio_atime_180d_pct).toBe(25);
  expect(r.hot_ratio_mtime_180d_pct).toBe(100);
  expect(r.hot_ratio_ctime_180d_pct).toBeNull();               // 축 없음 = 모름(0 아님)
  expect(r.total_files).toBe(4);
  expect(r.custom_metric).toBe(9);                            // 모르는 요약 키도 싣는다(가능한 모든 데이터)
  expect(r["atime_bytes_181d-365d"]).toBe(150);
  expect(r["size_count_4097-65536"]).toBe(1);
  // 열 순서: 기본 → 요약(알려진 키 먼저) → 파손 → 축별 히스토그램 → 크기 분포
  expect(columns.indexOf("total_files")).toBeLessThan(columns.indexOf("custom_metric"));
  expect(columns.indexOf("broken_paths_total")).toBeLessThan(columns.indexOf("atime_bytes_0d-1d"));
  expect(columns.indexOf("mtime_bytes_0d-1d")).toBeLessThan(columns.indexOf("size_count_0-4096"));
  expect(columns).not.toContain("size_bytes_0-4096");          // 크기 분포에 바이트가 없으면 열도 없다
});

test("buildUsageCsv: 구간 열은 전 행의 합집합 -- 버킷이 다른 행도 열이 맞고, 없는 칸은 빈 칸", () => {
  const other = row({ target: "b", latest: pt({ time_histograms: {
    atime: [{ bucket: "[3651d,INF]", min_age_days: 3651, bytes: 7 }] } }) });
  const { columns, records } = buildUsageCsv([row({}), other], ST, NOW);
  expect(columns).toContain("atime_bytes_3651d-INF");
  expect(records[0]["atime_bytes_3651d-INF"]).toBeNull();
  expect(records[1]["atime_bytes_3651d-INF"]).toBe(7);
  expect(records[1]["atime_bytes_0d-1d"]).toBeNull();
});

test("buildUsageCsv: 최신 리포트를 못 읽음·직전 없음·스토리지 모름 -- 지어내지 않는다", () => {
  const { records } = buildUsageCsv([row({
    storage_name: "gone", previous: null,
    latest: pt({ report_readable: false, summary: {}, time_histograms: {}, file_size_histogram: [],
                 total_bytes: 123 }) })], ST, NOW);
  const r = records[0];
  expect(r.backend_type).toBeNull();
  expect(r.abs_path).toBeNull();
  expect(r.latest_report_readable).toBe(false);
  expect(r.total_bytes).toBe(123);                            // DB(bytes_count) 대체값
  expect(r.delta_bytes_vs_previous).toBeNull();
  expect(r.hot_ratio_atime_180d_pct).toBeNull();
});

test("buildUsageCsv: 요약 키가 기본 열 이름과 겹치면 summary_ 접두", () => {
  const { columns } = buildUsageCsv([row({ latest: pt({ summary: { total_bytes: 5 } }) })], ST, NOW);
  expect(columns).toContain("summary_total_bytes");
});

test("usageCsvFilename: KST 날짜·시각", () => {
  expect(usageCsvFilename(Date.parse("2026-10-02T03:04:00Z"))).toBe("dms-usage-20261002-1204.csv");
});
