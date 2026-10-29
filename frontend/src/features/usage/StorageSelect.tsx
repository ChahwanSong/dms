import { startTransition, useEffect, useRef, useState } from "react";
import { Clock, FolderTree, HardDrive, Layers } from "lucide-react";
import { agoText, kstStampOrDash } from "../../lib/datetime";
import { storageLabel, type StorageTab } from "./usageTabs";

// 경로별의 범위 줄(2026-10-29 사용자 결정 「권장사항대로」 -- 예전 스토리지 탭 줄을 select 하나로): 왼쪽 StorageSelect, 오른쪽
// MetaLine(마운트 · 타입 · 대상 수 · 최근 스캔). 미등록 스토리지면 메타 줄 대신 바로 아래 경고 띠(UnregisteredBanner -- 패널이
// 그린다)가 같은 것을 말한다.
//
// StorageSelect: 네이티브 <select>(Radix Select 를 들이지 않는다 -- 새 의존성·jsdom 폴리필 없이 보조기기가 선택을 말한다). 묶음은
// 예전 탭과 같은 집합·순서(usageTabs: 등록 이름순 | 미등록 이름순, 실패·취소 기록만 남은 이름 포함)를 optgroup 으로. 옵션 글자는
// 필터 줄의 「… · N개」 관용: 「k8s-pv-nfs · nfs · 대상 5개」, 미등록은 타입 자리에 「미등록」, 요약 전엔 수 없이(모름 ≠ 0).
// 바꾸면 **300ms 디바운스 뒤 push** 한다: 닫힌 select 에서 화살표로 값이 바로 바뀌는 브라우저가 있어 화살표 한 번마다 요청과 히스토리
// 항목을 쌓지 않게(예전 탭의 수동 활성과 같은 의도). select 를 떠나면(blur) 기다리지 않고 바로 반영한다(아래 commit). 기다리는
// 동안은 고른 값을 그대로 보인다. 옵션 값은 「s:이름」이다 -- 빈 이름
// (변조·구형 행)도 스토리지라 빈 값을 자리표시(아직 고르는 중)와 가르기 위해서.
const DEBOUNCE_MS = 300;
const OPT = (name: string) => `s:${name}`;
const n = (x: number) => x.toLocaleString("ko-KR");

export function storageOptionLabel(t: StorageTab): string {
  const type = t.summary?.backend_type ?? t.info?.backend_type ?? null;
  return [storageLabel(t.name), t.registered ? type : "미등록", t.targetCount !== null && `대상 ${n(t.targetCount)}개`]
    .filter((x): x is string => typeof x === "string" && x !== "").join(" · ");
}

export function StorageSelect({ tabs, value, onSelect }: {
  tabs: StorageTab[]; value: string | null; onSelect: (name: string) => void;
}) {
  const [pending, setPending] = useState<string | null>(null);
  const pendingRef = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  // 디바운스가 끝날 때의 onSelect(그때의 URL)를 부른다 -- 고른 순간의 것을 붙잡으면 그 사이 바뀐 쿼리(검색 디바운스 등)를 되돌린다.
  const latest = useRef(onSelect);
  latest.current = onSelect;
  useEffect(() => () => clearTimeout(timer.current), []);
  // 반영: 디바운스가 끝나거나 select 를 떠날 때(blur -- 다른 곳을 누르거나 Tab, 2026-10-29 리뷰). 디바운스 창 안에 아직 남은 옛
  // 스토리지의 행·CSV·정렬을 누르면 그 동작이 select 가 보이는 것과 다른 스토리지에 걸렸다 -- 옛 스토리지로 연 시트가 300ms 뒤의
  // 스토리지 push 에 저절로 닫히고 히스토리에 옛 시트 항목이 남았다. 누르는 순간(mousedown)의 blur 가 먼저 스토리지를 바꾸므로 패널이
  // 새로 그려지고(key) 옛 행의 클릭은 닿지 않는다. 디바운스는 select 에 포커스가 있는 동안(화살표로 훑기)만의 것이다.
  // 기다리던 값은 위치 갱신과 **같은 전환**으로 거둔다(2026-10-29 2차 리뷰): react-router 7 의 BrowserRouter·MemoryRouter 는 위치를
  // startTransition 으로 바꾸므로, 긴급 갱신으로 지우면 그 사이 한 번의 커밋이 옛 스토리지(value)를 그렸다 -- 고른 값이 취소된
  // 것처럼 되돌아갔다가(표 교체가 길수록 오래) 새 값이 됐다. 같은 틱의 전환은 한 레인으로 묶여 함께 커밋된다. 라우터가 동기
  // 갱신이어도(useTransitions={false}) 이 전환이 그 뒤에 그려질 뿐 옛 값은 보이지 않는다. 부모가 받아들이지 않아도(같은 값) 늘
  // 지워져 고른 값이 남아 붙지 않는다.
  const commit = () => {
    clearTimeout(timer.current);
    const name = pendingRef.current;
    if (name === null) return;
    pendingRef.current = null;
    latest.current(name);
    startTransition(() => setPending(null));
  };
  const choose = (v: string) => {
    if (!v.startsWith("s:")) return;
    const name = v.slice(2);
    pendingRef.current = name;
    setPending(name);
    clearTimeout(timer.current);
    timer.current = setTimeout(commit, DEBOUNCE_MS);
  };
  const reg = tabs.filter((t) => t.registered);
  const unreg = tabs.filter((t) => !t.registered);
  const shown = pending ?? value;
  // 딥링크가 원천보다 먼저 왔다(그 이름이 아직 집합에 없다) -- 이름만으로 한 옵션.
  const orphan = shown !== null && !tabs.some((t) => t.name === shown) ? shown : null;
  const opt = (t: StorageTab) => <option key={t.name} value={OPT(t.name)}>{storageOptionLabel(t)}</option>;
  // 이름 툴팁(좁은 폭에서 말줄임된 이름을 마우스로)은 select 가 아니라 감싼 칸에 단다(2026-10-29 2차 리뷰): select 에 두면 접근
  // 이름(aria-label 「스토리지」)과 달라 접근 설명이 되어, 낭독기가 옵션 글자 안의 이름을 설명으로 한 번 더 읽었다.
  return (
    <span className="relative inline-flex w-full min-w-0 sm:w-auto" title={shown === null ? undefined : storageLabel(shown)}>
      <HardDrive aria-hidden className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" />
      <select aria-label="스토리지" value={shown === null ? "" : OPT(shown)} onChange={(e) => choose(e.target.value)} onBlur={commit}
              className="w-full min-w-0 rounded-lg border border-line bg-surface py-1.5 pl-8 pr-2 text-sm font-medium text-ink
                         focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/20 sm:w-auto sm:max-w-[20rem]">
        {shown === null && <option value="" disabled>스토리지 고르는 중…</option>}
        {orphan !== null && <option value={OPT(orphan)}>{storageLabel(orphan)}</option>}
        {reg.length > 0 && <optgroup label="등록">{reg.map(opt)}</optgroup>}
        {unreg.length > 0 && <optgroup label="미등록 — 기록만 남음">{unreg.map(opt)}</optgroup>}
      </select>
    </span>
  );
}

// 메타 한 줄: 마운트 · 타입 · 대상 수 · 최근 스캔. 항목마다 작은 아이콘 + 글자, 구분 기호 없이 간격만 -- 좁은 폭에서 줄이 바뀔 때
// 「· 최근 스캔」처럼 구분 기호로 시작하는 줄이 생기지 않는다. 이름은 싣지 않는다(바로 옆 select 가 이름 전체를 보인다 -- 예전
// 탭은 긴 이름을 말줄임해 여기 다시 썼다).
export function MetaLine({ tab, now }: { tab: StorageTab; now: number }) {
  const mount = tab.summary?.mount_path ?? tab.info?.mount_path ?? null;
  const type = tab.summary?.backend_type ?? tab.info?.backend_type ?? null;
  const last = tab.summary?.last_scan_at ?? null;
  const item = (key: string, icon: React.ReactNode, text: string, title?: string) => (
    <span key={key} title={title} className="inline-flex min-w-0 items-center gap-1.5">
      <span aria-hidden className="shrink-0 text-muted/80">{icon}</span>
      <span className="[overflow-wrap:anywhere]">{text}</span>
    </span>
  );
  const ico = "h-3.5 w-3.5";
  const parts = [
    mount !== null && item("m", <HardDrive className={ico} />, mount, "마운트 경로"),
    type !== null && item("t", <Layers className={ico} />, type, "스토리지 타입"),
    // 대상 수는 요약이 왔을 때만(모름 ≠ 0). 최근 스캔은 기록이 있을 때만.
    tab.targetCount !== null && item("c", <FolderTree className={ico} />, `대상 ${n(tab.targetCount)}개`),
    last !== null && item("l", <Clock className={ico} />, `최근 스캔 ${agoText(last, now)}`, kstStampOrDash(last)),
  ].filter(Boolean);
  if (parts.length === 0) return null;
  return <p className="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-1 text-sm text-ink/70">{parts}</p>;
}

/** 범위 줄 = StorageSelect + MetaLine(등록 스토리지만 -- 미등록은 패널 맨 위 경고 띠가 말한다). 375 에선 select 가 전체 폭이고
 *  메타 줄은 다음 줄로 접힌다. */
export function ScopeBar({ tabs, active, activeTab, onSelect, now }: {
  tabs: StorageTab[]; active: string | null; activeTab: StorageTab | null; onSelect: (name: string) => void; now: number;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
      <StorageSelect tabs={tabs} value={active} onSelect={onSelect} />
      {activeTab !== null && activeTab.registered && <MetaLine tab={activeTab} now={now} />}
    </div>
  );
}
