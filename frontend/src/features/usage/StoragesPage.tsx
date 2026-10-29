import { Skeleton } from "../../components/ui/Skeleton";
import { useStorageTabs } from "./useActiveStorage";
import { readPathsMemory } from "./usageMemory";
import type { StorageTab } from "./usageTabs";
import { UsageHeader } from "./UsageHeader";
import { CompareTable } from "./UsageOverview";
import { NoStorages, sourceNotices } from "./UsageParts";

// 사용량 분석 「스토리지별」(2026-10-29 사용자 결정 「권장사항대로」 -- 예전 경로별 머리말 아래 접힌 「스토리지별 비교」를 탭으로).
// 스토리지마다 얼마나 쓰고, 파일시스템이 얼마나 찼고, 언제 쟀나 · 정리할 미등록 기록이 있나. 구성: 머리(h1 = 탭·크럼 라벨) → 알림
// 자리(원천 실패만 -- 정리는 경로별의 경고 띠에서 한다) → 사실 한 줄 → 비교 표(UsageOverview.CompareTable -- 열 정렬, 이름 → 그
// 스토리지의 경로별) → 각주. 필터가 없다(보이지 않는 필터가 숫자를 바꾸는 일이 없게 -- 필터는 경로별에만). 쿼리도 없다(열 정렬은
// 페이지 상태). 경로별에서 마지막으로 본 스토리지 행이 현재로 칠해진다(usageMemory).
//
// 비용: 열 때마다 스토리지 수만큼 합계를 부른다(예전 비교를 펼칠 때와 같다 -- staleTime 30s 라 경로별과 오가도 다시 부르지 않는다).
// 그래서 사이드바로 들어오는 화면은 경로별이다(결정 3).

const n = (x: number) => x.toLocaleString("ko-KR");

// 사실 한 줄: 「등록 6개 · scan 대상 11개 · 성공 scan 13건」 + 미등록이 있으면 「· 미등록 1개」. 수를 모르는 조각(요약 전·실패)은
// 빼고 말한다(모름 ≠ 0).
export function factsLine(tabs: StorageTab[]): string {
  const reg = tabs.filter((t) => t.registered).length;
  const unreg = tabs.length - reg;
  const counts = tabs.map((t) => t.targetCount);
  const scans = tabs.map((t) => t.summary?.succeeded_scan_count ?? t.unregistered?.succeeded_scan_count ?? null);
  const total = (xs: (number | null)[]) => (xs.every((x): x is number => x !== null) ? xs.reduce((a, b) => a + b, 0) : null);
  const targets = total(counts), succeeded = total(scans);
  return [
    `등록 ${n(reg)}개`,
    targets !== null && `scan 대상 ${n(targets)}개`,
    succeeded !== null && `성공 scan ${n(succeeded)}건`,
    unreg > 0 && `미등록 ${n(unreg)}개`,
  ].filter(Boolean).join(" · ");
}

export function StoragesPage() {
  const src = useStorageTabs();
  const current = readPathsMemory()?.storage ?? null;
  const notices = sourceNotices(src);
  const now = Date.now();
  return (
    <section className="space-y-4">
      <UsageHeader title="스토리지별"
                   description="스토리지마다 최상위 scan 대상의 실 사용량과 파일시스템 사용률 — 이름을 누르면 그 스토리지의 경로로." />
      {notices.length > 0 && <div className="space-y-2">{notices}</div>}
      {src.sourcesPending || (!src.ready && src.tabs.length === 0) ? (
        <div className="space-y-2.5">
          <span className="sr-only">스토리지 목록 불러오는 중</span>
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-6" />)}
        </div>
      ) : src.noStorages ? <NoStorages /> : (<>
        <p className="text-sm text-muted break-keep">{factsLine(src.tabs)}</p>
        <CompareTable tabs={src.tabs} current={current} now={now} windowLimit={src.windowLimit} />
      </>)}
    </section>
  );
}
