import { render } from "@testing-library/react";
import { expect, test } from "vitest";
import { Highlight, MAX_SEARCH_TOKENS, containsToken, matchRanges, searchTokens } from "./Highlight";

// 검색어 강조(2026-10-29 -- 사용량 분석 경로·PV 이름). 토큰은 서버 text_search.search_tokens 의 거울, 비교는 대소문자 무시 부분 일치.

const marks = (el: HTMLElement) => [...el.querySelectorAll("mark")].map((m) => m.textContent);
const draw = (text: string, tokens: readonly string[]) =>
  render(<p><Highlight text={text} tokens={tokens} /></p>).container.querySelector("p")!;

test("searchTokens: 공백으로 나눔 · 순서 유지·중복 제거(대소문자 구분) · NUL·화살표는 구분자 · 빈 값·공백뿐은 [] · 최대 8개", () => {
  expect(searchTokens("  team   a ")).toEqual(["team", "a"]);
  expect(searchTokens("a b a B")).toEqual(["a", "b", "B"]);
  expect(searchTokens("s1:a → s2:b")).toEqual(["s1:a", "s2:b"]);
  expect(searchTokens("x\u0000y\ttab\nnl")).toEqual(["x", "y", "tab", "nl"]);
  expect(searchTokens("a\u3000b\u00a0c")).toEqual(["a", "b", "c"]);              // 유니코드 공백(전각·NBSP)
  expect(searchTokens("")).toEqual([]);
  expect(searchTokens("   ")).toEqual([]);
  expect(searchTokens(null)).toEqual([]);
  expect(searchTokens(undefined)).toEqual([]);
  const many = Array.from({ length: 12 }, (_, i) => `t${i}`);
  expect(searchTokens(many.join(" "))).toEqual(many.slice(0, MAX_SEARCH_TOKENS));
  expect(MAX_SEARCH_TOKENS).toBe(8);
});

test("matchRanges: 대소문자 무시 · 여러 토큰 · 겹침·맞닿음은 합친다 · 같은 토큰의 여러 자리", () => {
  expect(matchRanges("Team-A/Datasets", ["team", "DATA"])).toEqual([[0, 4], [7, 11]]);
  expect(matchRanges("team-a", ["team", "te"])).toEqual([[0, 4]]);                // 포함된 토큰
  expect(matchRanges("abcdef", ["abc", "cde"])).toEqual([[0, 5]]);                // 겹침
  expect(matchRanges("abcdef", ["ab", "cd"])).toEqual([[0, 4]]);                  // 맞닿음
  expect(matchRanges("aaa", ["aa"])).toEqual([[0, 3]]);                           // 같은 토큰의 겹친 자리
  expect(matchRanges("a/b/a", ["A"])).toEqual([[0, 1], [4, 5]]);
  expect(matchRanges("team", ["zzz"])).toEqual([]);
  expect(matchRanges("", ["a"])).toEqual([]);
  expect(matchRanges("abc", [])).toEqual([]);
  expect(matchRanges("abc", [""])).toEqual([]);                                   // 빈 토큰은 전체를 칠하지 않는다
});

test("matchRanges: 특수문자는 글자 그대로(정규식·LIKE 와일드카드 아님) · 낮추면 길이가 바뀌는 글자·서로게이트 쌍·끝 시그마", () => {
  expect(matchRanges("100%_done.(x)", ["%_"])).toEqual([[3, 5]]);
  expect(matchRanges("a.b", ["."])).toEqual([[1, 2]]);
  expect(matchRanges("a.b", ["a*"])).toEqual([]);
  expect(matchRanges("x[1]+y", ["[1]+"])).toEqual([[1, 5]]);
  expect(matchRanges("C:\\data", ["\\D"])).toEqual([[2, 4]]);
  // 「İ」.toLowerCase() 는 두 글자(i + 결합 점) -- 원문 위치로 되돌려 그 한 글자를 칠한다
  expect(matchRanges("xİy", ["i̇"])).toEqual([[1, 2]]);
  expect(matchRanges("xİy", ["İY"])).toEqual([[1, 3]]);
  // 서로게이트 쌍(이모지)은 가르지 않는다
  expect(matchRanges("a😀b", ["😀B"])).toEqual([[1, 4]]);
  // 끝 시그마: 서버(파이썬 lower())는 낱말 끝 Σ 를 ς 로 낮춘다 -- σ·ς 어느 쪽으로 쳐도 칠한다
  expect(matchRanges("ΟΔΟΣ", ["ος"])).toEqual([[2, 4]]);
  expect(matchRanges("ΟΔΟΣ", ["οσ"])).toEqual([[2, 4]]);
  expect(matchRanges("Ärger", ["äR"])).toEqual([[0, 2]]);
});

test("containsToken: 한 토큰의 대소문자 무시 부분 일치", () => {
  expect(containsToken("Checkpoints", "CHECK")).toBe(true);
  expect(containsToken("Checkpoints", "run")).toBe(false);
  expect(containsToken("Checkpoints", "")).toBe(false);
});

test("Highlight: 맞은 글자만 <mark>(원문 글자 그대로), 글자 전체는 바뀌지 않는다 · 토큰 없음·안 맞음은 mark 없이 글자 그대로", () => {
  let el = draw("Team-A/Datasets/run", ["team", "DATA", "RUN"]);
  expect(el.textContent).toBe("Team-A/Datasets/run");
  expect(marks(el)).toEqual(["Team", "Data", "run"]);
  expect(el.querySelector("mark")).toHaveClass("bg-attnbg", "text-inherit");
  el = draw("abcdef", ["abc", "cde", "zz"]);
  expect(marks(el)).toEqual(["abcde"]);                                           // 겹침은 한 덩어리 -- 중첩 mark 없음
  expect(el.textContent).toBe("abcdef");
  expect(el.querySelector("mark mark")).toBeNull();
  el = draw("plain", []);
  expect(el.innerHTML).toBe("plain");
  el = draw("plain", ["zzz", ""]);
  expect(el.innerHTML).toBe("plain");
});

test("Highlight: HTML 처럼 보이는 글자도 글자다(마크업으로 해석하지 않는다)", () => {
  const el = draw("<b>x</b>&amp;", ["<b>", "&"]);
  expect(el.textContent).toBe("<b>x</b>&amp;");
  expect(el.querySelector("b")).toBeNull();
  expect(marks(el)).toEqual(["<b>", "&"]);
});
