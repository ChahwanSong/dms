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
  // (summary.total_bytes 는 2026-10-26 부터 고정 total_bytes 열이 싣는 같은 값이라 요약 열에서 빠진다 -- 아래 테스트)
  const { columns } = buildUsageCsv([row({ latest: pt({ summary: { scan_count: 5 } }) })], ST, NOW);
  expect(columns).toContain("summary_scan_count");
});

// 2026-10-26 dscan 포크 ae781bd: summary.total_bytes(정확 실 사용량 -- 서버가 고정 total_bytes 로 싣는다)·invalid_name_entries.
test("buildUsageCsv: invalid_name_entries 는 파손 열 옆 고정 열(빈 칸 = 모름) · summary 의 같은 값 열은 만들지 않는다", () => {
  const { columns, records } = buildUsageCsv([
    row({ latest: pt({ total_bytes: 1074750158, invalid_name_entries: 15,
                       summary: { total_files: 4, total_bytes: 1074750158, invalid_name_entries: 15 } }) }),
    row({ target: "zero", latest: pt({ invalid_name_entries: 0 }) }),
    row({ target: "old", latest: pt({ invalid_name_entries: null }) }),
    row({ target: "older-server", latest: pt({}) }),
    row({ target: "unreadable", latest: null }),
  ], ST, NOW);
  expect(columns.indexOf("invalid_name_entries")).toBe(columns.indexOf("broken_paths_limit") + 1);
  expect(columns.filter((c) => c.includes("total_bytes"))).toEqual(["total_bytes", "previous_total_bytes"]);
  expect(columns.filter((c) => c.includes("invalid_name_entries"))).toEqual(["invalid_name_entries"]);
  expect(records.map((r) => r.invalid_name_entries)).toEqual([15, 0, null, null, null]);
  expect(records[0].total_bytes).toBe(1074750158);
  expect(records[0].broken_paths_total).toBe(0);                         // 더하지 않는다
});

// 2026-10-12 사용자 보고: 「사용량 csv 로 추출하니까 두번째 행부터는 backend type, mount path 등 몇몇 column 들이 누락」 --
// 첫 행만 등록 스토리지, 다음 행들은 삭제된(미등록) 스토리지였다(최근 스캔 순). 서버가 행마다 싣는 storage 를 쓴다.
test("buildUsageCsv: 미등록 스토리지 행도 서버의 마지막 설정으로 채우고 출처·등록 여부를 말한다", () => {
  const { columns, records } = buildUsageCsv([
    row({ storage: { registered: true, source: "registered", backend_type: "gpfs", mount_path: "/mnt/s1",
                     managed_root: "/mnt/s1/managed" } }),
    row({ storage_name: "old-gpfs", target: "proj", storage: { registered: false, source: "last_known",
          backend_type: "wekafs", mount_path: "/old", managed_root: "/old/managed" } }),
    row({ storage_name: "ghost", target: "x", storage: { registered: false, source: null, backend_type: null,
          mount_path: null, managed_root: null } }),
  ], ST, NOW);
  expect(columns.slice(0, 8)).toEqual(["storage_name", "storage_registered", "backend_type", "mount_path",
                                       "managed_root", "storage_info_source", "abs_path", "target"]);
  expect(records[1]).toMatchObject({ storage_registered: false, backend_type: "wekafs", mount_path: "/old",
                                     managed_root: "/old/managed", storage_info_source: "last_known",
                                     abs_path: "/old/managed/proj" });
  expect(records[0]).toMatchObject({ storage_registered: true, storage_info_source: "registered" });
  // 모름은 빈 칸 -- 등록 여부는 안다(false)
  expect(records[2]).toMatchObject({ storage_registered: false, backend_type: null, storage_info_source: null,
                                     abs_path: null });
});

test("buildUsageCsv: 서버가 storage 를 싣지 않으면(구형) 등록 목록으로 대신한다 -- 목록도 모르면 등록 여부도 모름", () => {
  const known = buildUsageCsv([row({}), row({ storage_name: "gone" })], ST, NOW).records;
  expect(known[0]).toMatchObject({ storage_registered: true, storage_info_source: "registered", backend_type: "gpfs" });
  expect(known[1]).toMatchObject({ storage_registered: false, storage_info_source: null, backend_type: null });
  const unknown = buildUsageCsv([row({})], undefined, NOW).records;
  expect(unknown[0]).toMatchObject({ storage_registered: null, backend_type: null, abs_path: null });
});

test("buildUsageCsv: 요약 키가 파손·히스토그램 열 이름과 겹쳐도 열이 둘 생기지 않는다", () => {
  const { columns, records } = buildUsageCsv([row({ latest: pt({
    summary: { broken_paths_total: 9, "atime_bytes_0d-1d": 1 }, broken_paths_total: 3 }) })], ST, NOW);
  expect(columns.filter((c) => c === "broken_paths_total")).toHaveLength(1);
  expect(columns.filter((c) => c === "atime_bytes_0d-1d")).toHaveLength(1);
  expect(records[0].broken_paths_total).toBe(3);
  expect(records[0].summary_broken_paths_total).toBe(9);
  expect(records[0]["atime_bytes_0d-1d"]).toBe(50);
  expect(records[0]["summary_atime_bytes_0d-1d"]).toBe(1);
});

test("usageCsvFilename: 스토리지 이름 + KST 날짜·시각 -- 파일 이름에 못 쓰는 글자는 _, 길이 64자까지", () => {
  const at = Date.parse("2026-10-02T03:04:00Z");
  expect(usageCsvFilename(at, "cephfs-dms")).toBe("dms-usage-cephfs-dms-20261002-1204.csv");
  // 미등록 옛 이름은 등록 규칙([a-z0-9-])을 지킨다는 보장이 없다
  expect(usageCsvFilename(at, "Old GPFS/../x.v2")).toBe("dms-usage-Old_GPFS_.._x.v2-20261002-1204.csv");
  expect(usageCsvFilename(at, "한글")).toBe("dms-usage-__-20261002-1204.csv");
  expect(usageCsvFilename(at, "")).toBe("dms-usage-_-20261002-1204.csv");          // 빈 이름(변조·구형 행) 탭
  expect(usageCsvFilename(at, "a".repeat(100))).toBe(`dms-usage-${"a".repeat(64)}-20261002-1204.csv`);
});
