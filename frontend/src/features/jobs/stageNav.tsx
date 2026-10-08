import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

// 요청 상세의 출력 선택 상태(2026-10-08 재설계). 잡마다, **단계 구획마다 하나씩** 열린 출력을 기억한다 -- 미리보기
// stdout 과 실행 stdout 을 나란히 펼쳐 비교할 수 있어야 해서(구획 분리의 핵심) 잡 전체에 선택 하나가 아니다.
// 배너(OutcomeCard)의 「실패 지점 로그 보기」·「진행 중인 로그 보기」·「미리보기 결과 보기」·「컨펌하러 가기」가 잡 카드
// 안의 선택·포커스를 움직여야 해서 상태를 페이지(Provider)로 올렸다. Provider 가 없으면(JobStages 단독 테스트) 컴포넌트 지역 상태로
// 떨어진다.
//
// touched: 사용자가 칩을 고르거나 「출력 닫기」를 한 번이라도 누르면 참 -- 그 뒤로는 자동 열림(실패 단계 로그)이
// 사용자의 선택을 덮어쓰지 않는다(폴링으로 잡이 실패로 바뀌어도).
// auto: 자동 열림이 마지막으로 연 것(구획·키). 손대지 않은 동안 모델이 자동 열림을 거두거나 바꾸면 이것만 닫거나
// 바꾼다(syncAuto, 리뷰 3차).
export type NavSlot = "pre" | "exec" | "other";
// confirm = 그 잡 관문 줄의 「작업 컨펌」 버튼(배너 「컨펌하러 가기」가 스크롤·포커스한다 -- 컨펌 창은 관문 줄에만 있다).
export type RevealTarget = NavSlot | "previewResult" | "confirm";
const isSlot = (t: RevealTarget): t is NavSlot => t === "pre" || t === "exec" || t === "other";
export interface AutoPick { slot: NavSlot; key: string }
export interface NavState {
  selected: Record<NavSlot, string | null>;
  touched: boolean;
  auto: AutoPick | null;
  reveal: { token: number; target: RevealTarget } | null;
}
const EMPTY: NavState = { selected: { pre: null, exec: null, other: null }, touched: false, auto: null, reveal: null };

interface Ctx {
  states: Record<string, NavState>;
  update: (jobId: string, fn: (s: NavState) => NavState) => void;
}
const StageNavContext = createContext<Ctx | null>(null);

export function StageNavProvider({ children }: { children: ReactNode }) {
  const [states, setStates] = useState<Record<string, NavState>>({});
  const update = useCallback((jobId: string, fn: (s: NavState) => NavState) => {
    setStates((m) => {
      const prev = m[jobId] ?? EMPTY;
      const next = fn(prev);
      return next === prev ? m : { ...m, [jobId]: next };
    });
  }, []);
  const value = useMemo(() => ({ states, update }), [states, update]);
  return <StageNavContext.Provider value={value}>{children}</StageNavContext.Provider>;
}

// 자동 열림을 모델의 판정(want)에 맞춘다. 손댔으면 아무것도 바꾸지 않는다. 같은 입력이면 같은 상태 객체를 돌려줘
// (렌더마다 불러도) 다시 그리지 않는다.
export function syncAutoState(s: NavState, want: AutoPick | null): NavState {
  if (s.touched) return s;
  const prev = s.auto;
  if (prev !== null && want !== null && prev.slot === want.slot && prev.key === want.key) return s;
  let selected = s.selected;
  // 지난번 자동으로 연 것이 아직 열려 있으면 닫는다(손대지 않았으니 열린 것은 자동 열림뿐이다).
  if (prev !== null && selected[prev.slot] === prev.key) selected = { ...selected, [prev.slot]: null };
  let auto: AutoPick | null = null;
  if (want !== null && selected[want.slot] === null) {
    selected = { ...selected, [want.slot]: want.key };
    auto = want;
  }
  if (selected === s.selected && auto === prev) return s;
  return { ...s, selected, auto };
}

export interface StageNav extends NavState {
  select: (slot: NavSlot, key: string) => void;          // 사용자 선택(touched)
  close: (slot: NavSlot) => void;                         // 「출력 닫기」(touched)
  syncAuto: (want: AutoPick | null) => void;              // 자동 열림 -- 손대지 않았을 때만, 모델을 따라 열고·바꾸고·닫는다
  reveal: NavState["reveal"];
  requestReveal: (target: RevealTarget, key?: string) => void;   // 배너 CTA: 선택 + 스크롤·포커스 요청
}

export function useStageNav(jobId: string): StageNav {
  const ctx = useContext(StageNavContext);
  const [local, setLocal] = useState<NavState>(EMPTY);
  const state = ctx ? ctx.states[jobId] ?? EMPTY : local;
  const update = useCallback((fn: (s: NavState) => NavState) => {
    if (ctx) ctx.update(jobId, fn);
    else setLocal(fn);
  }, [ctx, jobId]);
  const syncAuto = useCallback((want: AutoPick | null) => update((s) => syncAutoState(s, want)), [update]);
  return useMemo(() => ({
    ...state,
    select: (slot, key) => update((s) => ({ ...s, touched: true, selected: { ...s.selected, [slot]: key } })),
    close: (slot) => update((s) => ({ ...s, touched: true, selected: { ...s.selected, [slot]: null } })),
    syncAuto,
    requestReveal: (target, key) => update((s) => ({
      ...s,
      touched: key !== undefined ? true : s.touched,
      selected: isSlot(target) && key !== undefined ? { ...s.selected, [target]: key } : s.selected,
      reveal: { token: (s.reveal?.token ?? 0) + 1, target },
    })),
  }), [state, update, syncAuto]);
}
