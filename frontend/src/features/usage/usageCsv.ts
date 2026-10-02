// 사용량 분석 전체 CSV(2026-10-02 사용자 요청: "모든 정보를 csv 로, 각 항목당 한 줄, 가능한 한 모든 데이터").
// 원천은 GET /api/admin/usage/export(타깃마다 최신·직전 성공 scan 포인트) + 관리자 스토리지 목록(백엔드 종류·
// 마운트·관리 디렉토리 -> 절대경로). hot 비율·구간 키는 화면과 같은 usageStats 한 벌.
//
// 열 이름은 영문 snake_case(스크립트·피벗에서 그대로 쓰게), 시각은 KST 표기 열(_kst), 바이트는 원값(정수) +
// 사람용 크기 한 열. 모름은 빈 칸(null≠0). 히스토그램은 축(atime/mtime/ctime)×구간마다 바이트 열, 파일 크기
// 분포는 구간마다 개수(+바이트가 있으면 바이트) 열 -- 구간 집합은 전 행의 합집합(행마다 버킷이 달라도 열이 맞는다).
import type { HistogramBucket, Storage, UsageExportRow, UsagePoint } from "../../lib/types";
import { absolutePath } from "../../lib/storagePaths";
import { ageDays, kstStamp, kstStampEpoch } from "../../lib/datetime";
import type { CsvCell } from "../../lib/csvExport";
import { bucketKey, hotRatio, humanBytes } from "./usageStats";

const AXES_FIRST = ["atime", "mtime", "ctime"];
const SUMMARY_FIRST = ["total_entries", "total_files", "total_directories", "total_symlinks",
                       "total_other", "scan_errors"];

const pct1 = (r: number | null) => (r === null ? null : Math.round(r * 1000) / 10);
const kst = (iso: string | null | undefined) => (iso ? kstStamp(iso) : null);

function ordered(seen: string[], first: string[]): string[] {
  return [...first.filter((k) => seen.includes(k)), ...seen.filter((k) => !first.includes(k)).sort()];
}

// 구간 키 합집합(처음 본 순서 -- dscan 버킷은 이미 나이·크기 오름차순이라 그 순서가 곧 열 순서)
function unionKeys(lists: (HistogramBucket[] | undefined)[]): string[] {
  const out: string[] = [];
  for (const l of lists) (l ?? []).forEach((b, i) => {
    const k = bucketKey(b, i);
    if (!out.includes(k)) out.push(k);
  });
  return out;
}

export function buildUsageCsv(rows: UsageExportRow[], storages: Storage[] | undefined, nowMs: number):
    { columns: string[]; records: Record<string, CsvCell>[] } {
  const byName = new Map((storages ?? []).map((s) => [s.storage_name, s]));
  const latest = rows.map((r) => r.latest).filter((p): p is UsagePoint => p !== null && p !== undefined);
  const summaryKeys = ordered([...new Set(latest.flatMap((p) => Object.keys(p.summary ?? {})))], SUMMARY_FIRST);
  const axes = ordered([...new Set(latest.flatMap((p) => Object.keys(p.time_histograms ?? {})))], AXES_FIRST);
  const axisKeys = new Map(axes.map((a) => [a, unionKeys(latest.map((p) => p.time_histograms?.[a]))]));
  const sizeKeys = unionKeys(latest.map((p) => p.file_size_histogram));
  const sizeHasBytes = latest.some((p) => (p.file_size_histogram ?? []).some((b) => typeof b.bytes === "number"));

  const base = [
    "storage_name", "backend_type", "mount_path", "managed_root", "abs_path", "target",
    "scan_count", "first_scan_at_kst", "last_scan_at_kst", "days_since_last_scan",
    "latest_request_id", "latest_job_id", "latest_requester", "latest_report_generated_at_kst",
    "latest_report_readable", "total_bytes", "total_size",
    "previous_scan_at_kst", "previous_total_bytes", "delta_bytes_vs_previous", "delta_pct_vs_previous",
    ...AXES_FIRST.map((a) => `hot_ratio_${a}_180d_pct`),
  ];
  const summaryCols = summaryKeys.map((k) => (base.includes(k) ? `summary_${k}` : k));
  const histCols = axes.flatMap((a) => axisKeys.get(a)!.map((k) => `${a}_bytes_${k}`));
  const sizeCols = sizeKeys.flatMap((k) => (sizeHasBytes
    ? [`size_count_${k}`, `size_bytes_${k}`] : [`size_count_${k}`]));
  const columns = [...base, ...summaryCols, "broken_paths_total", "broken_paths_limit", ...histCols, ...sizeCols];

  const records = rows.map((r) => {
    const st = byName.get(r.storage_name);
    const p = r.latest ?? null, prev = r.previous ?? null;
    const total = p?.total_bytes ?? null, prevTotal = prev?.total_bytes ?? null;
    const delta = total !== null && prevTotal !== null ? total - prevTotal : null;
    const age = ageDays(r.last_scan_at, nowMs);
    const rec: Record<string, CsvCell> = {
      storage_name: r.storage_name, backend_type: st?.backend_type ?? null,
      mount_path: st?.mount_path ?? null, managed_root: st?.managed_root ?? null,
      abs_path: absolutePath(st?.managed_root, r.target), target: r.target,
      scan_count: r.scan_count, first_scan_at_kst: kst(r.first_scan_at),
      last_scan_at_kst: kst(r.last_scan_at), days_since_last_scan: age === null ? null : Math.floor(age),
      latest_request_id: p?.request_id ?? null, latest_job_id: p?.job_id ?? null,
      latest_requester: p?.requester ?? null,
      latest_report_generated_at_kst: typeof p?.generated_at_epoch === "number"
        ? kstStampEpoch(p.generated_at_epoch) : null,
      latest_report_readable: p === null ? null : p.report_readable ?? null,
      total_bytes: total, total_size: total === null ? null : humanBytes(total),
      previous_scan_at_kst: kst(prev?.finished_at), previous_total_bytes: prevTotal,
      delta_bytes_vs_previous: delta,
      delta_pct_vs_previous: delta !== null && prevTotal !== null && prevTotal > 0
        ? Math.round((delta / prevTotal) * 1000) / 10 : null,
      broken_paths_total: p?.broken_paths_total ?? null, broken_paths_limit: p?.broken_paths_limit ?? null,
    };
    for (const a of AXES_FIRST) rec[`hot_ratio_${a}_180d_pct`] = pct1(hotRatio(p?.time_histograms?.[a]));
    summaryKeys.forEach((k, i) => { rec[summaryCols[i]] = p?.summary?.[k] ?? null; });
    for (const a of axes) {
      const byKey = new Map((p?.time_histograms?.[a] ?? []).map((b, i) => [bucketKey(b, i), b]));
      for (const k of axisKeys.get(a)!) rec[`${a}_bytes_${k}`] = byKey.get(k)?.bytes ?? null;
    }
    const sizeByKey = new Map((p?.file_size_histogram ?? []).map((b, i) => [bucketKey(b, i), b]));
    for (const k of sizeKeys) {
      rec[`size_count_${k}`] = sizeByKey.get(k)?.count ?? null;
      if (sizeHasBytes) rec[`size_bytes_${k}`] = sizeByKey.get(k)?.bytes ?? null;
    }
    return rec;
  });
  return { columns, records };
}

// 파일 이름: dms-usage-YYYYMMDD-HHMM.csv(KST)
export function usageCsvFilename(nowMs: number): string {
  const s = kstStampEpoch(Math.floor(nowMs / 1000));          // "YYYY-MM-DD HH:MM:SS KST"
  return `dms-usage-${s.slice(0, 10).replace(/-/g, "")}-${s.slice(11, 16).replace(":", "")}.csv`;
}
