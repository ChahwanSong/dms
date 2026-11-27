import { forwardRef, useEffect, useId, useRef, useState } from "react";
import type {
  FocusEventHandler, HTMLAttributes, KeyboardEventHandler, MouseEvent, ReactNode, RefObject,
} from "react";
import { useLocation, useNavigationType, useSearchParams } from "react-router-dom";
import { Check, ChevronLeft, ChevronRight, CircleCheck, Lock, TriangleAlert } from "lucide-react";
import { Button } from "../ui/Button";

// 제출 위저드 공용 골격(2026-11-26 단계별 등록 -- 사용자 결정 「초안 1 상단 스테퍼」). 단일 작업(SubmitJob, 4단계)·배치 생성
// (BatchCreate, 5단계)이 같이 쓴다. 화면과 무관한 것만 여기 있다: 단계 상태·도달 규칙·URL 거울·Enter·포커스(useWizard)와 상태 없는 표시
// 부품 넷(WizardStepper·WizardStepCard·WizardActionBar·WizardShell). 값·검증·제출 바디는 화면이 쥔다(값은 모두 화면 최상위 useState --
// 단계 본문이 언마운트돼도 남는다). dms-ssc 는 dms-k8s ed7fe322(+ b1eddc3c·d7a228f2 의 위저드 몫)를 이식했다(2026-11-27) --
// 이 파일은 화면과 무관해 그 라인과 같은 규칙이고, 그 라인의 설계 근거(design-wizard §1-1·§1-6·§4·§5)를 따른다.
//
// 규칙(바꾸면 시험·두 화면이 함께 깨져야 정상이다):
// - **게이트 합집합 불변식**: 단계 게이트(steps[i].blocked, 마지막 제외)를 모두 합치면 지금의 제출 게이트에서 진행 중 상태를 뺀 것과
//   같다 -- 새 판정은 없다. 식은 화면 쪽 순수 함수(features/jobs/submitGates·features/batches/batchGates)가 잎 조합 전수로 시험한다.
//   마지막 단계(검토)의 blocked 는 그 단계의 「다음」이 아니라 **최종 제출 게이트**(= 화면의 blocked)다.
// - **도달 규칙**: firstBlockedFrom(i) = i 이상에서 처음 막힌 단계(마지막 단계는 셈에서 뺀다, 없으면 마지막). reachable(k) =
//   index(k) <= 현재 **또는** index(k) <= firstBlockedFrom(현재). 즉 잠김은 「현재보다 뒤이면서, 현재부터 센 첫 막힘보다 뒤」에만
//   붙는다 -- 현재 단계 **앞**의 막힘은 잠금을 만들지 않는다. 「다음」·「이전」은 현재 단계 게이트만 본다. 그래서 비동기 재조회
//   실패(react-query 는 창 포커스 때 다시 조회하고, 데이터가 있어도 재조회 실패면 isError)로 앞 단계가 막혀도 사용자를 끌어내리지
//   않는다: 그 단계에 빨간 점, 최종 제출은 합집합 게이트가 막고, 검토 바에 「n단계에서 고치기」. 스테퍼·스테퍼 클릭·「검토로
//   돌아가기」·뒤로/앞으로가 이 규칙 하나를 쓴다.
// - **Enter**(셸 onKeyDown -- 위에서부터 처음 맞는 것 하나만): ① Enter 가 아니면 무시 ② 한글 조합 확정(isComposing·keyCode 229)은
//   preventDefault 도 하지 않는다 ③ DOM 으로 셸 밖(포털 -- React 합성 이벤트는 포털을 지나 버블링한다: Radix 대화상자)이면 무시
//   ④ TEXTAREA·SELECT·BUTTON·A·SUMMARY, INPUT file·button·submit·reset·image 는 기본 동작(버튼 Enter = 그 버튼 click)
//   ⑤ checkbox·radio 는 preventDefault 만(단일 화면의 <form> 암묵 제출 차단 -- 브라우저는 체크박스 Enter 로도 제출한다)
//   ⑥ list 속성(datalist 제안)·data-wizard-enter="stay"(검색 칸) 는 preventDefault 만 -- 단계·포커스·이유 줄 모두 그대로(제안 고르기·
//   검색이 제 뜻이다; 넘어가면 고른 값이 안 보이거나 옛 값을 든 채 진행한다) ⑦ 그 밖의 텍스트형 INPUT 은 **늘** preventDefault
//   (마지막 단계 포함) 뒤: 배치 항목 표의 행 칸(data-row = 행 번호, data-col = 열 이름)이고 다음 행의 같은 열 칸이 있으면 그리로,
//   마지막 단계면 아무것도 안 한다(**Enter 는 절대 제출하지 않는다** -- 「경로 칸 Enter 로 제출 안 됨」 계약), 그 밖은 next().
// - **「다음」은 aria-disabled, 제출은 disabled**: 「다음」은 탭 순서에 남아 aria-describedby(이유 줄)로 이유를 읽히고, 막힌 채 누르면
//   blockedNudge()(이유 줄 강조 + 카드 안 첫 [aria-invalid="true"] 또는 [data-wizard-nudge] 칸(문서 순서)이 있으면 그리로 포커스·스크롤,
//   없으면 포커스 그대로 -- data-wizard-nudge 는 빨갛게 칠하지 않을 「아직 비어 있는 필수 칸」 표지다: 예) 아직 비운 필수 경로 칸,
//   배치 rm 자동 실행의 「되돌릴 수 없음」 동의 체크). 막는 필수 칸이 화면 밖일 수 있으면 둘 중 하나를 꼭 단다 -- 없으면 사람이 직접 스크롤해야 한다.
//   제출은 지금 계약대로 disabled 다(마지막 단계라 「누르면 이유」 길이 필요 없다).
// - **제출 더블클릭 가드**: 「다음」과 제출은 바의 **같은 자리·같은 <Button>(key 고정)** 이라 React 가 DOM 노드를 다시 쓴다 -- 그래서
//   마지막 앞 단계에서 「다음」을 더블클릭하면 두 번째 클릭이 막 그려진 제출에 떨어진다(그때 current 는 검토·blocked 는 false 라 단계
//   가드로는 못 막는다). 바가 제출 onClick 을 감싸 `e.detail > 1`(같은 연속 클릭의 두 번째 이후)을 무시한다 -- detail 은 브라우저의
//   클릭 횟수(위치·시간 기준, 노드와 무관)라 노드가 바뀌어도 맞고, 터치 연속 탭도 탭 횟수가 들어오며, 키보드 누르기는 0 이라 통과한다.
//   바가 쥐는 이유: 위저드를 쓰는 화면이 이 가드를 빠뜨려도 막힌다(화면이 같은 첫 줄을 또 둬도 무해하다). 검토 화면이 그려진 뒤 다시
//   한 번 누르는 것(새 클릭, detail 1)은 의도한 제출이다. 같은 노드를 유지하는 것은 시험이 「두 번째 클릭이 실제로 제출 노드에
//   떨어졌다」를 노드 동일성으로 단언하게 하려는 것이다(요소 종류가 바뀌면 두 번째 클릭이 떨어져 나간 옛 노드로 가 시험이 헛돈다).
//   시간 잠금은 쓰지 않는다(빠른 시험 클릭을 다 막고 사람에겐 「눌렀는데 안 됨」).
// - **모든 버튼 type="button"**: Button 에는 type 기본값이 없고 단일 화면은 <form> 안이다 -- type 없는 버튼은 submit 버튼이라 검토
//   「수정」이 onClick(go) 뒤 form 제출을 부르고 Enter 암묵 제출의 기본 버튼이 된다. 이 파일의 부품은 type 을 스스로 정하고(호출측
//   props 로 덮지 못한다), 화면이 넘기는 버튼도 명시한다(시험: test/wizard.ts buttonsWithoutType).
// - **URL**: ?step=<키> 는 거울이고 원천은 상태다(RR7 은 위치 갱신을 전환으로 늦게 커밋한다 -- 원천이면 한 커밋 동안 옛 단계가
//   그려진다). 사용자 이동(go·next·prev·스테퍼·수정·바로가기)은 push, 맞춤(처음 렌더 보정·jump·POP 보정)은 replace, 다른 쿼리 키는
//   보존(dms-k8s 의 ?pv= 딥링크 같은 것 -- dms-ssc 제출 화면엔 지금 없다). URL 변화에는 **useNavigationType() === "POP"**
//   (뒤로·앞으로)일 때만 반응한다 -- 자기 push·replace 를 뒤로 가기로 오인하면 빠른 연속 「다음」 중에 앞 단계로 되돌았다 오는 왕복이 생긴다. POP: 앞 단계면 그리로(뒤로는 늘), 뒤 단계면 도달 가능할
//   때만, 아니면 firstBlockedFrom(현재) 로 맞추고 replace. 처음 렌더: step 이 있으면 min(URL 단계, firstInvalid) 로 맞추고(새로고침·
//   다시 마운트 -- 값이 사라진 빈 폼으로 검토 화면을 보이지 않는다) 다르면 replace, 없으면 initial(첫 단계가 아니면 replace 로 step 을
//   더한다 -- 딥링크의 뒤로 가기 기준점), 모르는 값이면 첫 단계 + replace. 비동기 게이트 변화는 URL·단계를 건드리지 않는다.
// - **포커스**: 단계가 바뀌면(jump 제외) 새 카드 h2(tabIndex -1)로 옮긴다 -- 요청 ref + 단계 키 의존 effect(상태 커밋 직후). 스테퍼가
//   화면 위로 벗어났으면 셸을 scrollIntoView(start, 움직임 줄이기면 auto). 처음 마운트는 옮기지 않는다. 셸 onFocusCapture 는 하단 바에
//   가리는 포커스 요소를 위로 올리는데, 바와 그 요소의 실측 높이가 **둘 다 > 0** 일 때만 잰다(jsdom 은 크기 0 -- 가드가 없으면
//   0 > 0 − 12 가 참이 되어 jsdom 의 미구현 scrollBy 가 모든 단계 이동 시험에서 불린다).
// - **단계 카드에 aria-label·aria-labelledby 를 달지 않는다**(옛 FormSection 과 같은 이유 -- 배치 단계 제목 「스토리지」가 이름이 되면
//   Playwright getByLabel("스토리지") 부분 일치가 단계까지 잡는다). 카드 표지는 data-wizard-card(Radix Dialog 제목도 h2 라 「h2 는
//   하나뿐」에 기대지 않는다).
// - **하단 바에 z-index 를 주지 않는다**(usage/TargetTable 교훈 -- z 를 주면 z 없는 Radix 덮개(Dialog) 위로 올라온다). sticky 는 조상에
//   overflow 가 없어야 산다(AppShell 체인은 없다).

export interface WizardStep<K extends string> {
  key: K;                 // URL 값이자 안정 키 -- 역할·연산이 바뀌어도 그대로
  title: string;          // 스테퍼 이름 = 단계 카드 h2
  value?: string | null;  // 스테퍼 아래 짧은 값(방문했거나 지난 단계만, lg 이상에서 보인다)
  blocked: boolean;       // 이 단계의 「다음」 게이트(마지막 단계는 최종 제출 게이트)
  reason: string | null;  // blocked 일 때 바에 보일 첫 이유(게이트 순수 함수의 bar 이유)
}

/** 검토 단계 primary 자리의 제출 버튼 props(요소가 아니라 props -- 바가 같은 <Button> 으로 그려 노드를 유지한다, 머리 주석). */
export interface WizardSubmit {
  label: string;
  onClick: (e: MouseEvent<HTMLButtonElement>) => void;   // 바가 e.detail > 1(더블클릭의 두 번째)을 걸러서 부른다(머리 주석)
  disabled: boolean;
  variant?: "primary" | "destructive";                    // rm 이면 destructive(빨강 솔리드)
  describedBy?: string;
}

export interface Wizard<K extends string> {
  steps: readonly WizardStep<K>[];
  current: K;
  index: number;
  visited: ReadonlySet<K>;
  /** 처음부터 세어 처음 막힌 단계(마지막 제외, 없으면 마지막) -- 「n단계에서 고치기」. */
  firstInvalid: number;
  reachable: (k: K) => boolean;
  /** reachable 일 때만: push + 방문 표시 + 제목 포커스. */
  go: (k: K) => void;
  /** 시작 단계 자동 설정 전용(연산이 하나뿐인 사용자 등): replace + 앞 단계 방문 표시, 포커스 이동 없음. */
  jump: (k: K) => void;
  /** 현재 단계 게이트만 본다 -- 막혔으면 이동 없이 blockedNudge(). */
  next: () => void;
  prev: () => void;
  blockedNudge: () => void;
  /** 이유 줄 강조 횟수(단계가 바뀌면 0) -- 바가 애니메이션을 다시 걸 때 쓴다. */
  nudge: number;
  headingRef: RefObject<HTMLHeadingElement>;
  reasonRef: RefObject<HTMLParagraphElement>;
  reasonId: string;
  shellProps: {
    onKeyDown: KeyboardEventHandler<HTMLElement>;
    onFocusCapture: FocusEventHandler<HTMLElement>;
    "data-wizard-shell": "";
  };
}

function firstBlockedFrom<K extends string>(steps: readonly WizardStep<K>[], i: number): number {
  const last = steps.length - 1;
  for (let j = Math.max(0, i); j < last; j++) if (steps[j].blocked) return j;
  return last;
}

function canReach<K extends string>(steps: readonly WizardStep<K>[], from: number, to: number): boolean {
  return to <= from || to <= firstBlockedFrom(steps, from);
}

function scrollBehavior(): ScrollBehavior {
  const reduce = typeof window !== "undefined"
    && window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
  return reduce ? "auto" : "smooth";
}

// Enter 의 기본 동작을 그대로 두는 INPUT type(규칙 ④).
const PASS_INPUT_TYPES = new Set(["file", "button", "submit", "reset", "image"]);

export function useWizard<K extends string>({ steps, initial, param = "step" }: {
  steps: readonly WizardStep<K>[];   // 순서 = 화면 순서, 개수 고정(단일 4·배치 5), 마지막 = 검토
  initial: K;                        // URL 에 step 이 없을 때의 시작 단계(보통 첫 단계 -- 연산이 하나뿐이면 화면이 jump)
  param?: string;                    // 기본 "step"
}): Wizard<K> {
  const keys = steps.map((s) => s.key);
  const indexOf = (k: string | null) => (k === null ? -1 : keys.indexOf(k as K));
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navType = useNavigationType();
  const reasonId = `${useId()}-reason`;

  // 처음 렌더 보정(머리 주석 「URL」). 다르면 마운트 effect 가 replace 한다.
  const initFix = useRef<K | null>(null);
  const [state, setState] = useState(() => {
    const raw = params.get(param);
    const u = indexOf(raw);
    let start: number;
    let fix: boolean;
    if (raw === null) {
      start = Math.max(0, indexOf(initial));
      fix = start > 0;
    } else if (u < 0) {
      start = 0;
      fix = true;
    } else {
      start = Math.min(u, firstBlockedFrom(steps, 0));
      fix = start !== u;
    }
    initFix.current = fix ? keys[start] : null;
    return { current: keys[start], visited: new Set<K>(keys.slice(0, start + 1)) as ReadonlySet<K> };
  });

  // 최신 값 ref -- 연속 이벤트·effect 가 커밋 전 옛 클로저를 보지 않게.
  const stateRef = useRef(state);
  stateRef.current = state;
  const stepsRef = useRef(steps);
  stepsRef.current = steps;
  const setParamsRef = useRef(setParams);
  setParamsRef.current = setParams;

  const headingRef = useRef<HTMLHeadingElement>(null);
  const reasonRef = useRef<HTMLParagraphElement>(null);
  const focusRequested = useRef(false);
  const [nudge, setNudge] = useState(0);

  const writeUrl = (k: K, replace: boolean) =>
    setParamsRef.current((p) => { p.set(param, k); return p; }, { replace });

  const moveTo = (k: K, alsoVisit: readonly K[], focus: boolean) => {
    const visited = new Set(stateRef.current.visited);
    for (const v of alsoVisit) visited.add(v);
    visited.add(k);
    const nextState = { current: k, visited: visited as ReadonlySet<K> };
    stateRef.current = nextState;
    focusRequested.current = focus;
    setNudge(0);
    setState(nextState);
  };

  const blockedNudge = () => {
    setNudge((n) => n + 1);
    const card = headingRef.current?.closest("[data-wizard-card]");
    // 문서 순서의 첫 칸(선택자 목록은 문서 순서로 고른다) -- 빨간 오류 칸과 「비어 있는 필수 칸」 표지(data-wizard-nudge) 중 위에 있는 것.
    const bad = card?.querySelector<HTMLElement>('[aria-invalid="true"]:not(:disabled), [data-wizard-nudge]:not(:disabled)');
    if (bad) {
      bad.focus({ preventScroll: true });
      bad.scrollIntoView?.({ block: "center", behavior: scrollBehavior() });
    }
  };

  const go = (k: K) => {
    const from = indexOf(stateRef.current.current);
    const to = indexOf(k);
    if (to < 0 || to === from || !canReach(stepsRef.current, from, to)) return;
    moveTo(k, [], true);
    writeUrl(k, false);
  };

  const jump = (k: K) => {
    const to = indexOf(k);
    if (to < 0) return;
    moveTo(k, keys.slice(0, to + 1), false);
    writeUrl(k, true);
  };

  const next = () => {
    const i = indexOf(stateRef.current.current);
    if (i < 0 || i >= keys.length - 1) return;
    if (stepsRef.current[i].blocked) { blockedNudge(); return; }
    go(keys[i + 1]);
  };

  const prev = () => {
    const i = indexOf(stateRef.current.current);
    if (i > 0) go(keys[i - 1]);
  };

  // 처음 렌더 보정의 replace(StrictMode 의 두 번 실행은 같은 replace 두 번이라 무해하다).
  useEffect(() => {
    if (initFix.current !== null) writeUrl(initFix.current, true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // 브라우저 뒤로·앞으로(POP)에만 반응한다. 이미 본 위치 키는 건너뛴다 -- 마운트 첫 실행(StrictMode 의 재실행 포함)은 처음 렌더 보정이
  // 맡았고, 자기 push·replace 는 navType 이 PUSH·REPLACE 라 키만 기록하고 지나간다.
  const seenKey = useRef(location.key);
  useEffect(() => {
    if (location.key === seenKey.current) return;
    seenKey.current = location.key;
    if (navType !== "POP") return;
    const s = stepsRef.current;
    const from = indexOf(stateRef.current.current);
    const raw = new URLSearchParams(location.search).get(param);
    let to = raw === null ? 0 : indexOf(raw);
    let fix = false;
    if (to < 0) { to = 0; fix = true; }
    if (to > from && !canReach(s, from, to)) { to = firstBlockedFrom(s, from); fix = true; }
    if (to !== from) moveTo(keys[to], [], true);
    if (fix) writeUrl(keys[to], true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.key]);

  // 단계가 바뀐 직후(커밋 뒤) 제목으로 포커스. jump·처음 마운트는 요청하지 않는다.
  useEffect(() => {
    if (!focusRequested.current) return;
    focusRequested.current = false;
    const h = headingRef.current;
    if (h === null) return;
    h.focus({ preventScroll: true });
    const shell = h.closest<HTMLElement>("[data-wizard-shell]");
    if (shell !== null && shell.getBoundingClientRect().top < 0) {
      shell.scrollIntoView?.({ block: "start", behavior: scrollBehavior() });
    }
  }, [state.current]);

  const onKeyDown: KeyboardEventHandler<HTMLElement> = (e) => {
    if (e.key !== "Enter") return;                                              // ①
    if (e.nativeEvent.isComposing || e.keyCode === 229) return;                 // ②
    const root = e.currentTarget;
    const t = e.target as HTMLElement;
    if (!root.contains(t)) return;                                              // ③
    if (t.tagName !== "INPUT") return;                                          // ④ (TEXTAREA·SELECT·BUTTON·A·SUMMARY 등)
    const input = t as HTMLInputElement;
    if (PASS_INPUT_TYPES.has(input.type)) return;                               // ④
    e.preventDefault();
    if (input.type === "checkbox" || input.type === "radio") return;            // ⑤
    if (input.hasAttribute("list") || input.dataset.wizardEnter === "stay") return;  // ⑥
    // ⑦ 배치 항목 표: 다음 행의 같은 열 칸으로(마지막 행이면 아래로 떨어져 다음 단계).
    const row = input.dataset.row;
    const col = input.dataset.col;
    if (row !== undefined && col !== undefined && Number.isFinite(Number(row))) {
      const sel = `[data-row="${Number(row) + 1}"][data-col="${col.replace(/["\\]/g, "\\$&")}"]`;
      const below = root.querySelector<HTMLElement>(sel);
      if (below !== null) { below.focus(); return; }
    }
    if (indexOf(stateRef.current.current) === keys.length - 1) return;        // 제출 금지
    next();
  };

  const onFocusCapture: FocusEventHandler<HTMLElement> = (e) => {
    const root = e.currentTarget;
    const t = e.target as HTMLElement;
    if (!root.contains(t)) return;
    const bar = root.querySelector<HTMLElement>("[data-wizard-bar]");
    if (bar === null || bar.contains(t)) return;
    const b = bar.getBoundingClientRect();
    const r = t.getBoundingClientRect();
    if (!(b.height > 0 && r.height > 0)) return;
    const over = r.bottom - (b.top - 12);
    if (over > 0) window.scrollBy({ top: over, behavior: "auto" });
  };

  const index = indexOf(state.current);
  return {
    steps,
    current: state.current,
    index,
    visited: state.visited,
    firstInvalid: firstBlockedFrom(steps, 0),
    reachable: (k: K) => canReach(steps, index, indexOf(k)),
    go, jump, next, prev, blockedNudge, nudge,
    headingRef, reasonRef, reasonId,
    shellProps: { onKeyDown, onFocusCapture, "data-wizard-shell": "" },
  };
}

// ---- 표시 부품(상태 없음 -- 모두 wizard·props 로 받는다) ------------------------------------------------------------

/**
 * 진행 단계 스테퍼. 단계마다 <li><button type="button"> 하나: 번호(완료면 ✓ -- 둘 다 aria-hidden) + 이름 + 값 + 보조기기용 꼬리.
 * 상태 우선순위: 현재 > 잠김 > 오류 > 완료 > 남음. 현재 단계에는 aria-current="step" 만 달고 aria-disabled 를 달지 않는다(비동기 막힘으로
 * 도달 규칙이 거짓이어도 현재 단계는 잠김이 아니다). 잠김 = !reachable → aria-disabled(탭 순서엔 남아 꼬리를 읽힌다, 눌러도 동작 없음).
 * 오류 = 방문 + blocked → 빨간 점. 마지막 단계(검토)는 오류·완료를 붙이지 않는다 -- 그 게이트는 제출 게이트라 막힘의 원인은 앞 단계가
 * 빨간 점으로 말하고, 「완료」는 제출 전이라 거짓이다. aria-pressed 는 쓰지 않는다(e2e 03 의 375 `button[aria-pressed]` 한 줄 검사는
 * 입력 방식 세그먼트를 재는 그물이다).
 * 폭: ol 은 줄바꿈 없는 flex, li·버튼은 min-w-0, 이름은 truncate(+ title 에 전문). li 는 내용 폭 기준으로 줄어(flex-auto) 긴 이름이 더
 * 줄고, 연결선이 남는 폭을 채운다. lg(1024) 미만은 현재 단계만 이름이 보이고 나머지는 원만 -- 768 은 사이드바를 빼면 본문이 약 488px
 * 라 배치 다섯 이름을 다 보이면 「작.」·「스」처럼 거의 다 잘렸다(실 Chrome). 숨김은 sr-only(lg:not-sr-only)라 접근 이름에는 남는다
 * (hidden 이면 e2e wizardGo·보조기기가 단계 이름으로 못 찾는다). not-sr-only 는 white-space:normal·overflow:visible 을 넣어 truncate 를
 * 무르므로 lg:truncate 를 함께 둔다(빼면 이름이 한 글자씩 꺾였다). 값은 xl(1280)부터(max 22ch 말줄임) -- 1024 에서 값까지 보이면
 * 다시 이름이 잘린다.
 */
export function WizardStepper<K extends string>({ wizard }: { wizard: Wizard<K> }) {
  const { steps, index, visited } = wizard;
  const n = steps.length;
  return (
    <nav aria-label="진행 단계" className="rounded-card border border-line bg-surface px-2 py-2 shadow-soft sm:px-4 sm:py-2.5">
      <ol className="flex min-w-0 items-center">
        {steps.map((s, i) => {
          const isLast = i === n - 1;
          const isCurrent = i === index;
          const locked = !isCurrent && !wizard.reachable(s.key);
          const isError = !isCurrent && !locked && !isLast && visited.has(s.key) && s.blocked;
          const isDone = !isCurrent && !locked && !isLast && !isError && visited.has(s.key);
          const value = (visited.has(s.key) || i < index) && s.value != null && s.value !== "" ? s.value : null;
          const tail = isCurrent ? "(현재 단계)" : locked ? "(앞 단계를 먼저 마치세요)"
            : isError ? "(고칠 것이 있음)" : isDone ? "(완료)" : null;
          const dot = isCurrent ? "border-accent bg-accent text-white ring-4 ring-infobg"
            : isDone ? "border-accent bg-infobg text-accent"
            : `border-line bg-surface text-muted ${locked ? "opacity-50" : ""}`;
          return (
            <li key={s.key} className={`flex min-w-0 items-center ${isLast ? "flex-initial" : "flex-auto"}`}>
              <button type="button" title={value === null ? s.title : `${s.title} — ${value}`}
                      aria-current={isCurrent ? "step" : undefined} aria-disabled={locked || undefined}
                      onClick={() => { if (!locked && !isCurrent) wizard.go(s.key); }}
                      className={`relative flex min-w-0 max-w-full items-center gap-2 rounded-lg p-1 text-left
                                  focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent md:gap-2.5 md:p-1.5 md:pr-2
                                  ${locked ? "cursor-not-allowed" : "hover:bg-panel"}`}>
                <span aria-hidden
                      className={`relative grid h-7 w-7 shrink-0 place-items-center rounded-full border-2 text-xs font-bold md:h-8 md:w-8 ${dot}`}>
                  {isDone ? <Check className="h-4 w-4" strokeWidth={3} /> : i + 1}
                  {isError && <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border-2 border-surface bg-bad" />}
                </span>
                <span className="flex min-w-0 flex-col">
                  <span className={`truncate text-sm font-semibold ${locked ? "text-muted" : "text-ink"}
                                    ${isCurrent ? "" : "sr-only lg:not-sr-only lg:truncate"}`}>
                    {s.title}
                  </span>
                  {value !== null && <>{" "}
                    <span className={`hidden max-w-[22ch] truncate text-xs xl:block ${isError ? "text-bad" : "text-muted"}`}>
                      {value}
                    </span>
                  </>}
                </span>
                {tail !== null && <span className="sr-only"> {tail}</span>}
              </button>
              {!isLast && (
                <span aria-hidden
                      className={`mx-0.5 h-0.5 min-w-2 flex-1 rounded-full md:mx-2 ${isDone ? "bg-accent" : "bg-line"}`} />
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

/**
 * 단계 카드: 머리(「n단계 / N」 · h2 제목(tabIndex -1, 포커스 대상) · lede 한 줄 · 오른쪽 칩) + 본문. aria-label·aria-labelledby 를
 * 달지 않는다(머리 주석). 본문의 입력·선택·버튼·summary 에 scroll-margin-bottom 을 줘 Tab 이동 때 브라우저가 하단 바 위까지 스크롤한다.
 */
export function WizardStepCard<K extends string>({ wizard, lede, chips, children }: {
  wizard: Wizard<K>; lede?: ReactNode; chips?: ReactNode; children: ReactNode;
}) {
  const step = wizard.steps[wizard.index];
  return (
    <section data-wizard-card
             className="min-w-0 rounded-card border border-line bg-surface px-4 py-5 shadow-soft sm:px-7 sm:py-6
                        [&_:is(input,select,textarea,button,summary)]:scroll-mb-36">
      <header className="mb-5 flex flex-wrap items-start justify-between gap-x-5 gap-y-3">
        <div className="min-w-0">
          <p className="text-xs font-bold text-accent">{`${wizard.index + 1}단계 / ${wizard.steps.length}`}</p>
          <h2 ref={wizard.headingRef} tabIndex={-1} className="mt-0.5 text-lg font-bold leading-snug text-ink focus:outline-none">
            {step.title}
          </h2>
          {lede !== undefined && lede !== null && <p className="mt-1 text-sm text-muted">{lede}</p>}
        </div>
        {chips !== undefined && chips !== null && <div className="flex flex-wrap items-center gap-1.5">{chips}</div>}
      </header>
      <div className="min-w-0 space-y-5">{children}</div>
    </section>
  );
}

// 바 버튼은 높이를 고정한다(h-10) -- ghost(테두리 1px)와 primary(테두리 없음)가 나란히 서면 2px 어긋났다.
const BAR_BUTTON = "h-10 whitespace-nowrap max-sm:flex-1";

/**
 * 하단 고정 바(sticky bottom, z-index 없음 -- 머리 주석). 위: strip(검토 단계 rm 경고 -- 화면이 검토에서만 넘긴다). 안: 왼쪽 「n/N」 +
 * 상태 줄(<p data-wizard-reason> -- 막혔으면 자물쇠 + 이유, 아니면 「다음: <다음 단계>」 + Enter, 검토는 이유 + 「n단계에서 고치기」 또는
 * doneHint), 오른쪽 끝(ml-auto) 버튼 무리 = 지름길 · 「취소」 · 「이전」 · primary. 왼쪽 끝 지름길이 단계마다 생기고 사라져도 취소·
 * 이전·primary 자리는 그대로다(지름길을 연달아 누른 두 번째 클릭이 「취소」에 떨어지지 않게). 지름길: 검토를 방문했고 지금 도달할
 * 수 있으면 바가 「검토로 돌아가기」를 그리고(그때 secondary 는 숨는다 -- 둘 다 검토로 가는 지름길이라 한 자리), 아니면 화면의
 * secondary(단일 「옵션 기본값으로 검토」). primary 는 마지막 단계가 아니면 「다음」(aria-disabled + aria-describedby=이유 줄, 막힌 채
 * 누르면 blockedNudge), 마지막이면 submit props 의 제출(e.detail > 1 은 바가 거른다) -- 같은 <Button>·같은 key 라 노드가 유지된다
 * (머리 주석 「더블클릭 가드」).
 * details: 검토 단계 바 안의 SubmitHints·제출 오류(화면이 검토에서만 넘긴다) -- 박스 안 첫 줄, 버튼 줄 **위**(design-wizard §1-5
 * 순서 strip · 이유 목록 · 오류 · 제출). 조건부 칸이라 버튼 줄의 자리(자식 순번)는 그대로다 -- primary 노드 유지(더블클릭 가드)와
 * 무관하고, 바가 아래에 붙어 있을 때(sticky) 박스는 위로 자라 제출 버튼의 화면 위치도 그대로다.
 * 폭: 좁은 폭(<sm)은 이유 줄 → 버튼 줄로 쌓고 버튼은 flex-1, 취소는 숨긴다(화면의 secondary 도 좁은 폭에서 숨길지는 화면이 정한다).
 * sm 이상은 한 줄이되 줄바꿈을 허용한다(flex-wrap): 상태 칸은 기준 폭 12rem(sm:basis-48)이라 그만큼이 버튼 무리 곁에 남지 않으면
 * 버튼 무리가 통째로 다음 줄(오른쪽 끝)로 내려간다 -- 768(본문 약 488px)에서 지름길까지 서면 상태 줄이 한 글자씩 꺾였다. 기준 폭이
 * 고정이라 줄바꿈 여부는 상태 문장 길이가 아니라 버튼 무리 폭만으로 갈린다(같은 버튼 무리면 단계가 바뀌어도 버튼 자리가 같다).
 */
export function WizardActionBar<K extends string>({ wizard, submit, doneHint, onCancel, secondary, strip, details }: {
  wizard: Wizard<K>;
  submit: WizardSubmit;
  doneHint: string;
  onCancel?: () => void;
  secondary?: ReactNode;
  strip?: ReactNode;
  details?: ReactNode;
}) {
  const { steps, index, firstInvalid } = wizard;
  const n = steps.length;
  const last = index === n - 1;
  const step = steps[index];
  const lastKey = steps[n - 1].key;
  const blocked = step.blocked;
  const backToReview = !last && wizard.visited.has(lastKey) && wizard.reachable(lastKey);
  const fixAt = last && blocked && firstInvalid < n - 1 ? firstInvalid : null;
  const reason = step.reason ?? "이 단계를 마쳐야 다음으로 갈 수 있습니다";
  return (
    <div data-wizard-bar className="sticky bottom-0 bg-gradient-to-t from-canvas from-70% to-transparent pb-4 pt-2.5">
      {strip !== undefined && strip !== null && strip !== false && (
        <div className="mb-2 flex items-start gap-2 rounded-xl border border-bad/30 bg-badbg px-3.5 py-2.5 text-sm font-semibold text-bad">
          <TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
          <span className="min-w-0">{strip}</span>
        </div>
      )}
      <div className="space-y-2 rounded-xl border border-line bg-surface p-3 shadow-station sm:px-4">
        {details !== undefined && details !== null && details !== false && (
          <div className="border-b border-line pb-2">{details}</div>
        )}
        <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:gap-x-4">
          <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 sm:basis-48">
            <span aria-hidden className="text-xs text-muted">{`${index + 1}/${n}`}</span>
            <p id={wizard.reasonId} ref={wizard.reasonRef} tabIndex={-1} data-wizard-reason
               className={`flex min-w-0 items-start gap-1.5 text-sm focus:outline-none ${blocked ? "text-attn" : "text-ok"}
                           ${wizard.nudge > 0 ? "font-semibold" : ""}`}>
              {blocked ? (<>
                <Lock aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                <span key={wizard.nudge} className={`inline-block min-w-0 ${wizard.nudge > 0 ? "motion-safe:animate-nudge" : ""}`}>
                  {reason}
                </span>
              </>) : (<>
                <CircleCheck aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
                {last ? <span className="min-w-0">{doneHint}</span> : (
                  <span className="min-w-0">
                    다음: <b className="font-semibold">{steps[index + 1].title}</b>
                    <kbd aria-hidden className="ml-2 rounded border border-b-2 border-line px-1.5 font-mono text-[11px] text-muted max-md:hidden">
                      Enter
                    </kbd>
                  </span>
                )}
              </>)}
            </p>
            {fixAt !== null && (
              <button type="button" onClick={() => wizard.go(steps[fixAt].key)}
                      className="inline-flex items-center gap-0.5 text-xs font-semibold text-accent underline underline-offset-2
                                 hover:text-accenthover">
                {`${fixAt + 1}단계에서 고치기`}<ChevronRight aria-hidden className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:ml-auto sm:flex-nowrap">
            {backToReview ? (
              <Button variant="outline" className={`${BAR_BUTTON} max-sm:basis-full`} onClick={() => wizard.go(lastKey)} type="button">
                검토로 돌아가기
              </Button>
            ) : secondary}
            {onCancel !== undefined && (
              <Button variant="ghost" className="h-10 whitespace-nowrap max-sm:hidden" onClick={onCancel} type="button">취소</Button>
            )}
            <Button variant="ghost" aria-disabled={index === 0 || undefined} onClick={() => wizard.prev()} type="button"
                    className={`${BAR_BUTTON} gap-1 aria-disabled:cursor-not-allowed aria-disabled:opacity-50`}>
              <ChevronLeft aria-hidden className="h-4 w-4" />이전
            </Button>
            {last ? (
              <Button key="wizard-primary" variant={submit.variant ?? "primary"} disabled={submit.disabled}
                      aria-describedby={submit.describedBy} type="button"
                      onClick={(e) => { if (e.detail > 1) return; submit.onClick(e); }}
                      className={`${BAR_BUTTON} min-w-24`}>
                {submit.label}
              </Button>
            ) : (
              <Button key="wizard-primary" variant="primary" aria-disabled={blocked || undefined} aria-describedby={wizard.reasonId}
                      onClick={() => wizard.next()} type="button"
                      className={`${BAR_BUTTON} min-w-24 gap-1 aria-disabled:cursor-not-allowed aria-disabled:opacity-50`}>
                다음<ChevronRight aria-hidden className="h-4 w-4" />
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** 위 셋을 감싸는 div -- `<WizardShell {...wizard.shellProps}>`(Enter 규칙·바에 가리는 포커스 보정·data-wizard-shell 표지). */
export const WizardShell = forwardRef<HTMLDivElement, HTMLAttributes<HTMLDivElement> & { "data-wizard-shell"?: string }>(
  function WizardShell({ className = "", ...p }, ref) {
    return <div ref={ref} {...p} className={`min-w-0 space-y-4 ${className}`} />;
  });
