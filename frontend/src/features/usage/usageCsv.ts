// 사용량 분석 전체 CSV(2026-10-02 사용자 요청: "모든 정보를 csv 로, 각 항목당 한 줄, 가능한 한 모든 데이터").
// 원천은 GET /api/admin/usage/export(타깃마다 최신·직전 성공 scan 포인트 + 행의 스토리지 정보). hot 비율·구간 키는
// 화면과 같은 usageStats 한 벌.
//
// 스토리지 열(backend_type·mount_path·managed_root → abs_path, 2026-10-12 사용자 보고: 「두번째 행부터는 backend type,
// mount path 등이 누락」): 예전엔 **지금 등록된** 스토리지 목록에서 이름으로만 찾아, 삭제된(미등록) 스토리지의 행 --
// 화면의 「(미등록)」 -- 은 그 네 열이 비었다(최근 스캔 순 정렬이라 첫 행만 등록 스토리지였다). 이제 서버가 행마다 싣는
// storage(등록이면 지금 값, 아니면 감사 기록의 마지막 설정)를 먼저 쓰고, 구형 서버 응답(storage 없음)만 등록 목록으로
// 대신한다. storage_registered(등록 여부)·storage_info_source(registered | last_known | 빈 칸=모름)가 그 값의 출처를 말한다.
//
// 열 이름은 영문 snake_case(스크립트·피벗에서 그대로 쓰게), 시각은 KST 표기 열(_kst), 바이트는 원값(정수) +
// 사람용 크기 한 열. 모름은 빈 칸(null≠0). 히스토그램은 축(atime/mtime/ctime)×구간마다 바이트 열, 파일 크기
// 분포는 구간마다 개수(+바이트가 있으면 바이트) 열 -- 구간 집합은 전 행의 합집합(행마다 버킷이 달라도 열이 맞는다).
import type { HistogramBucket, Storage, UsageExportRow, UsagePoint, UsageStorageInfo } from "../../lib/types";
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

// 행의 스토리지 정보: 서버 값이 있으면 그것, 없으면(구형 서버) 등록 목록에서. 등록 목록도 모르면(undefined) 등록 여부도 모름.
function storageInfoOf(r: UsageExportRow, byName: Map<string, Storage>, listKnown: boolean): UsageStorageInfo | null {
  if (r.storage !== undefined) return r.storage;
  const st = byName.get(r.storage_name);
  if (st !== undefined) {
    return { registered: true, source: "registered", backend_type: st.backend_type, mount_path: st.mount_path,
             managed_root: st.managed_root };
  }
  return listKnown ? { registered: false, source: null, backend_type: null, mount_path: null, managed_root: null }
    : null;
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
    "storage_name", "storage_registered", "backend_type", "mount_path", "managed_root", "storage_info_source",
    "abs_path", "target",
    "scan_count", "first_scan_at_kst", "last_scan_at_kst", "days_since_last_scan",
    "latest_request_id", "latest_job_id", "latest_requester", "latest_report_generated_at_kst",
    "latest_report_readable", "total_bytes", "total_size",
    "previous_scan_at_kst", "previous_total_bytes", "delta_bytes_vs_previous", "delta_pct_vs_previous",
    ...AXES_FIRST.map((a) => `hot_ratio_${a}_180d_pct`),
  ];
  const broken = ["broken_paths_total", "broken_paths_limit"];
  const histCols = axes.flatMap((a) => axisKeys.get(a)!.map((k) => `${a}_bytes_${k}`));
  const sizeCols = sizeKeys.flatMap((k) => (sizeHasBytes
    ? [`size_count_${k}`, `size_bytes_${k}`] : [`size_count_${k}`]));
  // 요약 키가 다른 고정 열 이름과 겹치면 summary_ 접두 -- 겹친 채 두면 머리줄에 같은 열이 둘 생기고 레코드 값이 서로
  // 덮인다(broken_paths_*·히스토그램 열도 고정 열이다).
  const fixed = new Set([...base, ...broken, ...histCols, ...sizeCols]);
  const summaryCols = summaryKeys.map((k) => (fixed.has(k) ? `summary_${k}` : k));
  const columns = [...base, ...summaryCols, ...broken, ...histCols, ...sizeCols];

  const listKnown = storages !== undefined;
  const records = rows.map((r) => {
    const st = storageInfoOf(r, byName, listKnown);
    const p = r.latest ?? null, prev = r.previous ?? null;
    const total = p?.total_bytes ?? null, prevTotal = prev?.total_bytes ?? null;
    const delta = total !== null && prevTotal !== null ? total - prevTotal : null;
    const age = ageDays(r.last_scan_at, nowMs);
    const rec: Record<string, CsvCell> = {
      storage_name: r.storage_name, storage_registered: st?.registered ?? null,
      backend_type: st?.backend_type ?? null, mount_path: st?.mount_path ?? null,
      managed_root: st?.managed_root ?? null, storage_info_source: st?.source ?? null,
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

// 파일 이름: dms-usage-<storage>-YYYYMMDD-HHMM.csv(KST). 2026-10-20 부터 CSV 는 탭(스토리지) 하나씩이라 이름에 스토리지를
// 넣는다 -- 여러 스토리지 파일을 한 폴더에 받아도 구별된다(파일 안의 storage_name 열은 그대로 -- 합칠 때 쓴다. 단 요약 키·
// 구간 열은 파일마다의 합집합이라 이어 붙이면 열이 어긋날 수 있다). 등록 이름은 [a-z0-9-] 이지만 미등록 옛 이름은 그 규칙을
// 지킨다는 보장이 없다 -- [A-Za-z0-9._-] 밖의 글자는 _ 로, 길이는 64자까지.
export function usageCsvFilename(nowMs: number, storage: string): string {
  const s = kstStampEpoch(Math.floor(nowMs / 1000));          // "YYYY-MM-DD HH:MM:SS KST"
  const name = storage.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 64);
  return `dms-usage-${name}-${s.slice(0, 10).replace(/-/g, "")}-${s.slice(11, 16).replace(":", "")}.csv`;
}
