// 사용량 분석의 스토리지 탭 집합(2026-10-20 사용자 요청: 「각 스토리지 별로 처음부터 나뉘어져서 보이게」). 화면은 「전체」
// 탭 없이 처음부터 스토리지 하나를 본다 -- 목록·정렬·CSV 요청이 늘 그 스토리지로 좁혀진다(서버의 지표 정렬 창도 스토리지별).
//
// 탭 = 등록 스토리지 ∪ 성공 scan 이 있는 이름(요약 GET /api/admin/usage/storages) ∪ 미등록 이름(실패·취소 기록만 남은 이름도
// -- GET /api/admin/usage/unregistered-storages, 거기서만 정리할 수 있다). 순서는 등록(이름순) → 미등록(이름순): 스토리지 화면과
// 같은 순서이고, 최근 스캔 순이면 scan 이 끝날 때마다 탭이 움직인다. 기록이 0개인 등록 스토리지도 탭이다(첫 scan 이 끝나는
// 순간 탭이 갑자기 생기지 않게 -- 빈 상태가 안내한다).
//
// 원천이 셋이라 도착 순서가 제각각이다: 등록 목록이 먼저 오면 등록 탭부터 그리고, 요약이 오면 대상 수와 미등록 탭이 뒤에
// 붙는다(등록 탭 자리는 밀리지 않는다). 요약을 못 받으면 scan 기록 이름(scan-storages)으로 탭만 그린다 -- 대상 수는 모름.
import type { Storage, UnregisteredStorage, UsageStorageSummary } from "../../lib/types";

export interface StorageTab {
  name: string;
  // 등록 여부 근거: 요약 > 미등록 목록 > 등록 목록. 셋 다 모르면(scan 기록 이름만) 등록으로 둔다 -- 증거 없이 「미등록」이라
  // 말하지 않는다(옛 목록 행 배지와 같은 규칙).
  registered: boolean;
  // 성공 scan 이 있는 경로 수. null = 모름(요약 도착 전·실패) -- 「0」으로 그리면 「기록 없음」이라는 거짓이 된다.
  targetCount: number | null;
  // 기록이 있는가(기본 탭 판정). 대상 수를 모르면 scan 기록 이름 목록으로, 그것도 모르면 null.
  hasTargets: boolean | null;
  summary: UsageStorageSummary | null;
  unregistered: UnregisteredStorage | null;
  info: Storage | null;
}

export interface TabSources {
  registered?: Storage[];
  summary?: UsageStorageSummary[];
  unregistered?: UnregisteredStorage[];
  scanNames?: string[];
}

// 서버 ORDER BY storage_name 과 같은 코드포인트 순(localeCompare 는 하이픈·대소문자를 다르게 줄 세운다).
const byName = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

export function buildStorageTabs(src: TabSources): StorageTab[] {
  const summary = new Map((src.summary ?? []).map((s) => [s.storage_name, s]));
  const unreg = new Map((src.unregistered ?? []).map((u) => [u.storage_name, u]));
  const info = new Map((src.registered ?? []).map((s) => [s.storage_name, s]));
  const scan = src.scanNames === undefined ? null : new Set(src.scanNames);
  const names = new Set([...info.keys(), ...summary.keys(), ...unreg.keys(), ...(src.scanNames ?? [])]);
  const tabs = [...names].map((name): StorageTab => {
    const s = summary.get(name) ?? null;
    const u = unreg.get(name) ?? null;
    const i = info.get(name) ?? null;
    const registered = s !== null ? s.registered
      : u !== null ? false
      : i !== null || src.registered === undefined;
    const targetCount = s !== null ? s.target_count : u !== null ? u.target_count : null;
    const hasTargets = targetCount !== null ? targetCount > 0 : scan === null ? null : scan.has(name);
    return { name, registered, targetCount, hasTargets, summary: s, unregistered: u, info: i };
  });
  const reg = tabs.filter((t) => t.registered).sort((a, b) => byName(a.name, b.name));
  const rest = tabs.filter((t) => !t.registered).sort((a, b) => byName(a.name, b.name));
  return [...reg, ...rest];
}

// 기본 탭(URL 에 storage 가 없을 때): 기록이 있는 등록 스토리지 중 이름순 첫째 → 기록이 있는 미등록 → 첫 탭. 파생값이다 --
// URL 에 쓰지 않으므로 effect 도 히스토리 항목도 생기지 않는다. 한 번 고른 기본 탭은 그 탭이 남아 있는 동안 페이지가 붙잡는다
// (UsageAnalysis -- 요약 재조회로 대상 수가 바뀌어도 보던 스토리지가 바뀌지 않게).
export function defaultStorage(tabs: StorageTab[]): string | null {
  return (tabs.find((t) => t.registered && t.hasTargets === true)
    ?? tabs.find((t) => !t.registered && t.hasTargets === true)
    ?? tabs[0])?.name ?? null;
}

// 화면에 보일 이름. 빈 이름(변조·구형 행 -- 서버는 그것도 이름으로 센다)은 글자가 없어 탭이 빈 칸이 된다 -- 자리 표시로
// 그린다. 요청·URL 에는 원래 이름(빈 문자열)이 실린다.
export const storageLabel = (name: string) => (name === "" ? "(이름 없음)" : name);
