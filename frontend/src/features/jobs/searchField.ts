import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, CompositionEvent } from "react";

/** 타이핑이 멈춘 뒤 검색어를 적용하기까지(2026-10-25 -- 사용량 분석 경로 검색과 같은 300ms). */
export const SEARCH_DEBOUNCE_MS = 300;

/** 「전체 작업」 검색칸 하나(2026-10-25). 보이는 값(bind.value)과 **적용된 값**(applied -- 앞뒤 공백을 뗀 값)을 나눈다.
 *
 *  - 키 입력마다 적용하지 않는다: 적용값이 곧 목록 쿼리 키라, 예전 요청자 칸은 「bob」을 치는 동안 b·bo·bob 세 번 서버를
 *    불렀고(부분 일치는 인덱스를 못 타 드문 검색어면 범위 전체를 훑는다) 키마다 표가 비어 「불러오는 중…」이 깜빡였다.
 *    멈춘 뒤 SEARCH_DEBOUNCE_MS 에 한 번 적용한다.
 *  - 한글 조합 중(compositionstart~end)에는 **끝에 남은 미완성 자모만 떼고** 적용한다: 음절 중간에 멈추면 「프로젝ㅌ」 같은
 *    미완성 글자로 물어 0건 문구가 잠깐 떴다(→ 「프로젝」). 조합 중이라고 아예 적용하지 않으면 안 된다(2026-10-25 리뷰, 실
 *    Chrome 재현): 한국어 IME 는 마지막 음절(「이관」의 「관」)을 Space·Enter·Tab·blur 전까지 조합 상태로 둬, 「이관」을 치고
 *    멈춰도 목록이 그대로였다. 조합이 끝나면(compositionend) 그 값 전체로 다시 디바운스를 건다.
 *  - 앞뒤 공백만 바뀐 입력(끝에 띄어쓰기)은 적용값이 같아 다시 묻지 않는다. */
// 호환 자모(ㄱ~ㆎ) -- 조합 중 끝에 홀로 남은 자음·모음. 완성 음절(가~힣)은 건드리지 않는다.
const TRAILING_JAMO = /[ㄱ-ㆎ]+$/u;
export function useSearchField() {
  const [input, setInput] = useState("");
  const [applied, setApplied] = useState("");
  const composing = useRef(false);
  // 조합이 끝날 때마다 올린다 -- 조합 중 마지막 onChange 와 끝 값이 같으면 input 이 안 바뀌어 디바운스가 다시 걸리지 않는다
  // (조합 중에 적용한 값은 자모를 뗀 값이라, 끝난 뒤 전체 값으로 다시 맞춰야 한다).
  const [settled, setSettled] = useState(0);
  useEffect(() => {
    const next = (composing.current ? input.replace(TRAILING_JAMO, "") : input).trim();
    const t = setTimeout(() => setApplied(next), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [input, settled]);
  const bind = {
    value: input,
    onChange: (e: ChangeEvent<HTMLInputElement>) => setInput(e.target.value),
    onCompositionStart: () => { composing.current = true; },
    onCompositionEnd: (e: CompositionEvent<HTMLInputElement>) => {
      composing.current = false;
      setInput(e.currentTarget.value);
      setSettled((n) => n + 1);
    },
  };
  return { applied, bind };
}
