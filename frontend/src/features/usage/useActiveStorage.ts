import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useStorages } from "../storages/useStorages";
import { useScanStorages, useUnregisteredStorages, useUsageStorages } from "./useUsage";
import { buildStorageTabs, defaultStorage, type StorageTab } from "./usageTabs";

// 사용량 분석의 스토리지 집합과 「지금 보는 스토리지」(2026-10-29 -- 예전 UsageAnalysis 본문에서 뽑았다: 경로별의 스토리지 select 와
// 스토리지별의 비교 표가 같은 집합·순서를, 2단계의 소속별이 같은 선택 규칙을 쓴다).
//
// useStorageTabs: 원천 셋(usageTabs 머리 주석 -- 등록 목록 · 요약 · 미등록 목록). 요약을 한 번도 못 받았으면 scan 기록 이름으로
// 집합만 그린다(대상 수 모름). 받아 둔 요약이 있으면 재조회가 실패해도 그것을 쓴다 -- isError 만 보면 백그라운드 재조회 한 번의
// 실패에 화면이 자리표시로 돌아갔다(리뷰).
export function useStorageTabs() {
  const storagesQ = useStorages();
  const summaryQ = useUsageStorages();
  const unregQ = useUnregisteredStorages();
  const summaryMissing = summaryQ.isError && summaryQ.data === undefined;
  const scanQ = useScanStorages(summaryMissing);
  const tabs = buildStorageTabs({
    registered: storagesQ.data, summary: summaryQ.data?.storages, unregistered: unregQ.data,
    scanNames: summaryMissing ? scanQ.data : undefined,
  });
  // 기본 스토리지는 「기록이 있는가」를 알아야 고를 수 있다 -- 요약(또는 그 대체 원천)이 올 때까지 패널은 자리표시다.
  const ready = summaryQ.data !== undefined || (summaryMissing && !scanQ.isPending && !storagesQ.isPending);
  return {
    tabs, ready, summaryMissing, storagesQ, summaryQ, unregQ, scanQ,
    windowLimit: summaryQ.data?.window_limit ?? null,
    sourcesPending: storagesQ.isPending && summaryQ.isPending,
    noStorages: tabs.length === 0 && ready && !unregQ.isPending && !storagesQ.isPending,
  };
}

// useActiveStorage: URL 이 진실이다(?storage=). storage 가 없으면 기본 스토리지(usageTabs.defaultStorage)를 **파생**한다 -- URL 에
// 쓰지 않아 effect 도 히스토리 항목도 없다. 한 번 고른 기본 스토리지는 그것이 집합에 남아 있는 동안 붙잡는다(리뷰: 요약 재조회로
// 대상 수가 바뀌면 다시 계산된 기본값이 보던 스토리지를 바꿔 버렸다 -- 탭을 오가 페이지가 다시 마운트되면 「경로별」 탭 링크가
// 기억한 스토리지를 URL 에 적어 돌아온다, usageMemory.pathsReturnSearch). URL 의 storage 가 집합에 없으면(오타·정리됨·삭제됨) 기본
// 스토리지로 돌리고 안내 한 줄(notice)을 낸다(URL 은 storage·target 만 지우는 replace -- 필터·검색·정렬은 남는다; URL 이 다시 집합
// 안의 스토리지를 가리키면 거둔다). 방금 정리해
// 사라진 이름(purged -- 정리 mutation 은 무효화 재조회가 끝나야 성공이 되므로 진행 중도 넣는다)이면 알리지 않는다: 결과 줄이
// 이미 말한다. select(name) = 스토리지 바꾸기(push) -- target 은 버리고(다른 스토리지로 시트가 따라가지 않게) 필터·q·sort·order 는
// 남긴다(같은 사용자·namespace·경로를 스토리지마다 비교 -- 없는 값은 필터 줄이 「(이 스토리지에 없음)」으로 보여 풀 수 있다).
export function useActiveStorage({ purged }: { purged: string | null }) {
  const src = useStorageTabs();
  const { tabs, ready, unregQ } = src;
  const [params, setParams] = useSearchParams();
  const storageParam = params.get("storage");
  // URL 의 storage 가 집합에 없다는 판정은 미등록 목록까지 온 뒤에만(실패 기록만 남은 미등록 이름은 거기에만 있다) -- 로딩 중의
  // 빈 목록으로 딥링크에서 튕겨 나가지 않게.
  const judgeable = ready && !unregQ.isPending;
  const inTabs = storageParam !== null && tabs.some((t) => t.name === storageParam);
  // 붙잡은 기본 스토리지: 집합에 남아 있으면 그대로, 사라졌으면(정리·삭제) 다시 고른다. 렌더 중 상태 조정이다 -- effect 로 하면
  // 다른 스토리지가 한 번 그려진 뒤 바뀐다.
  const [heldDefault, setHeldDefault] = useState<string | null>(null);
  const fallback = !ready ? null
    : heldDefault !== null && tabs.some((t) => t.name === heldDefault) ? heldDefault
    : defaultStorage(tabs);
  if (fallback !== null && fallback !== heldDefault) setHeldDefault(fallback);
  const active = storageParam !== null && (inTabs || !judgeable) ? storageParam : fallback;
  const activeTab: StorageTab | null = active === null ? null
    : tabs.find((t) => t.name === active)
      // 딥링크가 원천보다 먼저 왔다 -- 목록 요청은 바로 병렬로 보낸다(메타 줄·띠는 원천이 오면 채워진다).
      ?? { name: active, registered: true, targetCount: null, hasTargets: null, summary: null, unregistered: null,
           info: null };

  const [notice, setNotice] = useState<string | null>(null);
  useEffect(() => {
    // URL 이 집합 안의 스토리지를 가리키면 안내를 거둔다(2026-10-29 2차 리뷰): 뒤로·앞으로 가기로 명시 스토리지 항목에 돌아오면
    // 기본이 아닌 스토리지를 보이면서 「기본 스토리지를 보여 줍니다」라고 말했다(예전엔 select 만 지웠다). replace 직후 storage 가
    // 없는 동안(기본 스토리지를 보인다)은 남긴다.
    if (storageParam !== null && inTabs) { setNotice(null); return; }
    if (storageParam === null || !judgeable) return;
    setNotice(purged === storageParam ? null : storageParam);
    const next = new URLSearchParams(params);
    next.delete("storage"); next.delete("target");
    setParams(next, { replace: true });
  }, [storageParam, judgeable, inTabs]);  // eslint-disable-line react-hooks/exhaustive-deps

  const select = (name: string) => {
    if (name === active && storageParam !== null) return;
    setNotice(null);
    const next = new URLSearchParams(params);
    next.set("storage", name); next.delete("target");
    setParams(next);
  };
  return { ...src, storageParam, active, activeTab, notice, select };
}
