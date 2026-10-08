import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

// 요청 상세의 출력 선택 상태(2026-10-08 재설계). 잡마다, **단계 구획마다 하나씩** 열린 출력을 기억한다 -- 미리보기
// stdout 과 실행 stdout 을 나란히 펼쳐 비교할 수 있어야 해서(구획 분리의 핵심) 잡 전체에 선택 하나가 아니다.
// 배너(OutcomeCard)의 「실패 지점 로그 보기」·「진행 중인 로그 보기」·「미리보기 결과 보기」가 잡 카드 안의 선택을
// 움직여야 해서 상태를 페이지(Provider)로 올렸다. Provider 가 없으면(JobStages 단독 테스트) 컴포넌트 지역 상태로
// 떨어진다.
//
// touched: 사용자가 칩을 고르거나 「출력 닫기」를 한 번이라도 누르면 참 -- 그 뒤로는 자동 열림(실패 단계 로그)이
// 사용자의 선택을 덮어쓰지 않는다(폴링으로 잡이 실패로 바뀌어도).
export type NavSlot = "pre" | "exec" | "other";
export type RevealTarget = NavSlot | "previewResult";
export interface NavState {
  selected: Record<NavSlot, string | null>;
  touched: boolean;
  reveal: { token: number; target: RevealTarget } | null;
}
const EMPTY: NavState = { selected: { pre: null, exec: null, other: null }, touched: false, reveal: null };

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

export interface StageNav extends NavState {
  select: (slot: NavSlot, key: string) => void;          // 사용자 선택(touched)
  close: (slot: NavSlot) => void;                         // 「출력 닫기」(touched)
  autoSelect: (slot: NavSlot, key: string) => void;       // 자동 열림 -- 손대지 않았고 그 구획이 비었을 때만
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
  return useMemo(() => ({
    ...state,
    select: (slot, key) => update((s) => ({ ...s, touched: true, selected: { ...s.selected, [slot]: key } })),
    close: (slot) => update((s) => ({ ...s, touched: true, selected: { ...s.selected, [slot]: null } })),
    autoSelect: (slot, key) => update((s) => (s.touched || s.selected[slot] !== null
      ? s : { ...s, selected: { ...s.selected, [slot]: key } })),
    requestReveal: (target, key) => update((s) => ({
      ...s,
      touched: key !== undefined ? true : s.touched,
      selected: target !== "previewResult" && key !== undefined ? { ...s.selected, [target]: key } : s.selected,
      reveal: { token: (s.reveal?.token ?? 0) + 1, target },
    })),
  }), [state, update]);
}
